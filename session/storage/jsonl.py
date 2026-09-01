"""JsonlSessionStorage: durable session backend.

Owns a `_metadata: SessionMetadata` and a `_state: SessionState` (the canonical
state machine). Every mutation goes through `self._state.apply_mutation(...)`
and is then written to disk as a JSONL envelope line, one mutation per line.

File format (one JSON object per line):
- `{"kind": "header", "metadata": {...}}` — first line; carries the
 SessionMetadata (id, created_at, name, parent_session_id, lanes).
- `{"kind": "entry", "entry": {...}, "lane": "..."}` — entry mutation
- `{"kind": "record", "record": {...}}` — record mutation
- `{"kind": "lane", "seq": N, "lane": "...", "leaf_id": "..."}` — lane pointer
- `{"kind": "fact", "fact_kind": "name", "seq": N, "name": "..."}` — name fact
- `{"kind": "fact", "fact_kind": "label", "seq": N, "target_id": "...",
 "label": "..."}` — label fact

Replay (`_replay_on_init`): read each line, reconstruct the matching mutation
via `_dict_to_mutation`, call `_state.apply_mutation`. After replay, `_state`
is identical to a freshly-created storage that received the same mutations
in order.

Durability: synchronous append + flush per line (matches the legacy v1
contract). No fsync. A corrupt final line is tolerated on read (skipped).
"""

import asyncio
import copy
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from session.fork import ForkOptions, compute_fork_mutations
from session.mutations import (
    SessionMutation,
    entry_mutation,
    label_fact_mutation,
    lane_mutation,
    name_fact_mutation,
    record_mutation,
)
from session.state import SessionState
from session.storage._reconstruct import (
    _entry_dict_to_dataclass,
    _record_dict_to_dataclass,
    _record_with_meta,
    _with_meta,
)
from session.storage.atomic import publish_file_atomically
from session.storage.base import SessionStorage
from session.types import LanePointer, SessionMetadata, SessionStats


def _mutation_to_dict(mutation: SessionMutation) -> dict[str, Any]:
    """Serialize a SessionMutation to a JSONL line dict.

 Always emits `kind` plus the populated fields from the mutation's
 discriminated union variant. None fields are omitted to keep lines small
 and to make replay robust (the consumer only reads fields relevant to its
 branch of the union).
"""
    out: dict[str, Any] = {"kind": mutation.kind}
    if mutation.fact_kind is not None:
        out["fact_kind"] = mutation.fact_kind
    if mutation.entry is not None:
        out["entry"] = mutation.entry
    if mutation.lane is not None:
        out["lane"] = mutation.lane
    if mutation.record is not None:
        out["record"] = mutation.record
    if mutation.seq is not None:
        out["seq"] = mutation.seq
    if mutation.leaf_id is not None:
        out["leaf_id"] = mutation.leaf_id
    if mutation.name is not None:
        out["name"] = mutation.name
    if mutation.target_id is not None:
        out["target_id"] = mutation.target_id
    if mutation.label is not None:
        out["label"] = mutation.label
    return out


def _dict_to_mutation(d: dict[str, Any]) -> SessionMutation:
    """Reconstruct a SessionMutation from a JSONL line dict.

 Dispatches on `kind` (and `fact_kind` for fact mutations) to the matching
 factory function.
"""
    kind = d.get("kind")
    if kind == "entry":
        return entry_mutation(entry=d["entry"], lane=d.get("lane"))
    if kind == "record":
        return record_mutation(record=d["record"])
    if kind == "lane":
        return lane_mutation(seq=d["seq"], lane=d["lane"], leaf_id=d.get("leaf_id"))
    if kind == "fact":
        fk = d.get("fact_kind")
        if fk == "name":
            return name_fact_mutation(seq=d["seq"], name=d.get("name"))
        if fk == "label":
            return label_fact_mutation(
                seq=d["seq"], target_id=d["target_id"], label=d.get("label"),
            )
    raise ValueError(f"unknown mutation kind: {kind!r}")

def _header_to_dict(metadata: SessionMetadata) -> dict[str, Any]:
    """Serialize SessionMetadata as the header JSONL line."""
    return {
        "kind": "header",
        "metadata": {
            "id": metadata.id,
            "created_at": metadata.created_at,
            "name": metadata.name,
            "lanes": list(metadata.lanes),
            "parent_session_id": metadata.parent_session_id,
            "cwd": metadata.cwd,
        },
    }

class JsonlSessionStorage(SessionStorage):
    def __init__(self, metadata: SessionMetadata, path: Path | str) -> None:
        self._metadata = copy.deepcopy(metadata)
        self._path = Path(path)
        self._state = SessionState()
        self._lock = asyncio.Lock()
        self._replay_on_init()

    @classmethod
    def create(cls, path: Path | str, header: dict[str, Any] | None = None) -> "JsonlSessionStorage":
        """Create a fresh storage backed by `path` with no mutations applied.

 Used by `JsonlSessionStorage.fork` via `publish_file_atomically`:
 the caller writes a header + a sequence of mutation lines, then
 atomically renames into place.
"""
        if header is None:
            raise ValueError("JsonlSessionStorage.create requires a header dict")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta_d = header["metadata"]
        metadata = SessionMetadata(
            id=meta_d["id"],
            created_at=meta_d.get("created_at", 0.0),
            name=meta_d.get("name"),
            lanes=meta_d.get("lanes") or ["main"],
            parent_session_id=meta_d.get("parent_session_id"),
            cwd=meta_d.get("cwd"),
        )
        instance = cls.__new__(cls)
        instance._metadata = copy.deepcopy(metadata)
        instance._path = path
        instance._state = SessionState()
        instance._lock = asyncio.Lock()
        # Empty state — caller will write lines via `_append_mutation_line` and
        # we'll replay when the file lands at its final path.
        return instance

    @classmethod
    def load(cls, path: Path | str) -> "JsonlSessionStorage":
        """Open an existing JSONL session file, replaying all mutations."""
        path = Path(path)
        # Peek at the first line to recover the metadata; then dispatch through
        # the regular constructor which runs `_replay_on_init`.
        metadata: SessionMetadata | None = None
        if path.exists():
            try:
                with path.open("r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        d = json.loads(line)
                        if d.get("kind") == "header":
                            md = d["metadata"]
                            metadata = SessionMetadata(
                                id=md["id"],
                                created_at=md.get("created_at", 0.0),
                                name=md.get("name"),
                                lanes=md.get("lanes") or ["main"],
                                parent_session_id=md.get("parent_session_id"),
                                cwd=md.get("cwd"),
                            )
                            break
            except (OSError, json.JSONDecodeError):
                metadata = None
        if metadata is None:
            raise FileNotFoundError(f"JsonlSessionStorage.load: no header line in {path}")
        return cls(metadata=metadata, path=path)

    async def get_metadata(self) -> SessionMetadata:
        return SessionMetadata(
            id=self._metadata.id,
            created_at=self._metadata.created_at,
            parent_session_id=self._metadata.parent_session_id,
            name=self._state.name,
            lanes=list(self._state.lanes.keys()),
            cwd=self._metadata.cwd,
        )

    async def get_name(self) -> str | None:
        return self._state.get_name()

    async def set_name(self, name: str | None) -> None:
        async with self._lock:
            mutation = name_fact_mutation(seq=self._state.sequence + 1, name=name)
            self._state.apply_mutation(mutation)
            await self._append_mutation_line(mutation)

    async def get_lanes(self) -> list[LanePointer]:
        return self._state.get_lanes()

    async def create_lane(self, name: str, leaf_id: str) -> None:
        async with self._lock:
            self._state.validate_new_lane(name)
            self._state.validate_target(leaf_id)
            mutation = lane_mutation(
                seq=self._state.sequence + 1, lane=name, leaf_id=leaf_id,
            )
            self._state.apply_mutation(mutation)
            await self._append_mutation_line(mutation)

    async def move_lane(self, name: str, leaf_id: str) -> None:
        async with self._lock:
            self._state.require_lane(name)  # raises INVALID_LANE if missing
            self._state.validate_target(leaf_id)
            mutation = lane_mutation(
                seq=self._state.sequence + 1, lane=name, leaf_id=leaf_id,
            )
            self._state.apply_mutation(mutation)
            await self._append_mutation_line(mutation)

    async def append_entry(self, entry: Any, lane: str) -> Any:
        async with self._lock:
            entry_id = uuid.uuid4().hex
            # Auto-create lane on first use so callers don't need to call
            # `create_lane` explicitly. Mirrors the spec where every
            # entry append registers its lane.
            if lane not in self._state.lanes:
                self._state.lanes[lane] = None
            parent_id = self._state.require_lane(lane)
            timestamp = time.time()
            entry_dict = {
                "id": entry_id,
                "parent_id": parent_id,
                "timestamp": timestamp,
                "lane": lane,
                **self._entry_fields(entry),
            }
            from session.json_validity import assert_json_serializable
            assert_json_serializable(entry_dict)
            mutation = entry_mutation(entry=entry_dict, lane=lane)
            self._state.apply_mutation(mutation)
            await self._append_mutation_line(mutation)
            return self._wrap_entry(entry_dict)

    async def find_entries(self, query: dict[str, Any] | None = None) -> list[Any]:
        return [self._wrap_entry(e) for e in self._state.find_entries(query)]

    async def find_entries_on_branch(
        self,
        query: dict[str, Any] | None = None,
        start: str | None = None,
    ) -> list[Any]:
        merged_query: dict[str, Any] = dict(query) if query else {}
        if start is not None:
            merged_query["start"] = start
        # Default to newestFirst (start → root) to match legacy behavior and
        # what most callers expect when given a `start` entry.
        merged_query.setdefault("order", "newestFirst")
        return [self._wrap_entry(e) for e in self._state.find_entries_on_branch(merged_query)]

    async def get_entry(self, entry_id: str) -> Any | None:
        e = self._state.get_entry(entry_id)
        return self._wrap_entry(e) if e is not None else None

    async def append_record(self, record: Any, lane: str | None = None) -> Any:
        async with self._lock:
            if lane is not None and lane not in self._state.lanes:
                # Auto-create lane on first use.
                self._state.lanes[lane] = None
            record_id = uuid.uuid4().hex
            timestamp = time.time()
            record_dict = {
                "id": record_id,
                "timestamp": timestamp,
                **self._record_fields(record),
            }
            if lane is not None:
                record_dict["lane"] = lane
            from session.json_validity import assert_json_serializable
            assert_json_serializable(record_dict)
            mutation = record_mutation(record=record_dict)
            self._state.apply_mutation(mutation)
            await self._append_mutation_line(mutation)
            return self._wrap_record(record_dict)

    async def find_records(self, query: dict[str, Any] | None = None) -> list[Any]:
        return [self._wrap_record(r) for r in self._state.find_records(query)]

    async def find_open_operations(self, lane: str | None = None) -> list[Any]:
        started: dict[str, dict[str, Any]] = {}
        finished: set[str] = set()
        for r in self._state.records:
            if lane is not None and r.get("lane") != lane:
                continue
            rtype = r.get("type")
            if rtype == "operation_started":
                started[r["run_id"]] = r
            elif rtype == "operation_finished":
                finished.add(r["run_id"])
        open_ids = set(started.keys()) - finished
        return [self._wrap_record(started[k]) for k in open_ids]

    async def get_label(self, entry_id: str) -> str | None:
        return self._state.get_label(entry_id)

    async def set_label(self, entry_id: str, label: str) -> None:
        async with self._lock:
            mutation = label_fact_mutation(
                seq=self._state.sequence + 1,
                target_id=entry_id,
                label=label,
            )
            self._state.apply_mutation(mutation)
            await self._append_mutation_line(mutation)

    async def get_stats(self) -> SessionStats:
        return self._state.stats

    async def get_log(self, options: dict[str, Any] | None = None) -> list[Any]:
        from session.storage._reconstruct import ENTRY_TYPE_MAP
        out: list[Any] = []
        for item in self._state.get_log(options):
            if item.get("type") in ENTRY_TYPE_MAP:
                out.append(self._wrap_entry(item))
            else:
                out.append(self._wrap_record(item))
        return out

    def _replay_on_init(self) -> None:
        """Read existing file; reconstruct mutations and feed them to state.

 The first non-blank line MUST be the header. Subsequent lines are
 mutation envelopes. A corrupt (unparseable) final line is repaired
 by atomically rewriting the file with the valid prefix + a
 terminal '\\n' (/). The header line
 is always preserved in the rewrite.
"""
        if not self._path.exists():
            # Fresh file: stamp the header on first write. Nothing to replay.
            return
        raw_text = self._path.read_text()
        # Preserve exact byte content for physical lines so the rewrite
        # round-trips without losing or reformatting previously-good bytes.
        raw_lines = raw_text.split("\n")
        last_good_idx = -1  # index in raw_lines of the last fully-parsed non-blank line
        torn_tail_detected = False
        for i, raw in enumerate(raw_lines):
            line = raw.strip()
            if not line:
                # Blank lines are not part of the valid prefix; skip but
                # do not advance last_good_idx.
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                # Unparseable line — treat as torn tail. Do not advance.
                torn_tail_detected = True
                break
            kind = d.get("kind")
            if kind == "header":
                # Metadata is authoritative from the constructor; skip.
                last_good_idx = i
                continue
            try:
                mutation = _dict_to_mutation(d)
            except (KeyError, ValueError):
                # Unknown mutation shape — treat as torn tail.
                torn_tail_detected = True
                break
            # Register the lane before applying entry/record mutations so
            # the strict lane-exists gate in _apply_entry/_apply_record
            # doesn't reject replay (entry/record lines carry the lane
            # implicitly; storage layer registers it on append).
            if mutation.kind in ("entry", "record"):
                lane_name = mutation.lane or (
                    mutation.entry.get("lane") if mutation.entry else None
                ) or (
                    mutation.record.get("lane") if mutation.record else None
                )
                if lane_name is not None and lane_name not in self._state.lanes:
                    self._state.lanes[lane_name] = None
            self._state.apply_mutation(mutation)
            last_good_idx = i

        needs_terminal_newline = not raw_text.endswith("\n")
        if torn_tail_detected or needs_terminal_newline:
            # Atomically rewrite: keep raw_lines[:last_good_idx + 1] + "\n".
            # This preserves the header (it's at index 0, before any mutation)
            # and drops the partial tail. Sibling .tmp + os.replace mirrors
            # `publish_file_atomically` for the sync __init__ context.
            valid_lines = raw_lines[: last_good_idx + 1]
            target_text = "\n".join(valid_lines) + "\n"
            self._atomic_write_text(target_text)

    def _atomic_write_text(self, target_text: str) -> None:
        """Write `target_text` to `self._path` atomically (sync, __init__-safe).

 Mirrors `publish_file_atomically` (sibling .tmp + os.replace) but
 without an event loop dependency, so it can be called from
 `_replay_on_init` during construction.
"""
        import os
        tmp = self._path.parent / f".{self._path.name}.tmp"
        try:
            tmp.write_text(target_text)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise

    async def _append_mutation_line(self, mutation: SessionMutation) -> None:
        """Serialize the mutation to JSON and append to the file. Caller holds self._lock.

 Writes the header line first if the file is empty (no mutations yet).
"""
        if not self._path.exists() or self._path.stat().st_size == 0:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            await self._append_header(self._metadata)
        line = json.dumps(_mutation_to_dict(mutation), default=str)
        await self._append_raw_line(line)

    async def _append_header(self, metadata: SessionMetadata) -> None:
        """Write the header line. Caller holds self._lock."""
        line = json.dumps(_header_to_dict(metadata), default=str)
        await self._append_raw_line(line)

    async def _append_raw_line(self, line: str) -> None:
        """Append one line to the file and flush."""
        with self._path.open("a") as f:
            f.write(line + "\n")
            f.flush()

    async def fork(
        self,
        metadata: SessionMetadata,
        options: ForkOptions,
        *,
        path: Path | None = None,
    ) -> "JsonlSessionStorage":
        """Create a new JsonlSessionStorage by writing fork mutations to a new file atomically.

 Uses publish_file_atomically to write the file (sibling .tmp + os.replace).
 The new file contains:
 - Header line with metadata
 - One mutation envelope line per fork mutation (entry/record/lane/fact)

 Records are NOT copied. Labels filtered by scope. Name always copied.
"""
        target_path = Path(path) if path is not None else self._path.parent / f"{metadata.id}.jsonl"
        mutations = compute_fork_mutations(self._state, options)
        header = {"metadata": asdict(metadata)}

        async def write_fn(tmp: Path) -> None:
            # Write the header via create(), then replay mutations through the
            # public append path (state + _append_mutation_line) so the file
            # ends up identical to one written by append_* on a fresh storage.
            target = JsonlSessionStorage.create(tmp, header=header)
            for m in mutations:
                target._state.apply_mutation(m)
                await target._append_mutation_line(m)

        await publish_file_atomically(target_path, write_fn)
        return JsonlSessionStorage.load(target_path)

    @staticmethod
    def _entry_fields(entry: Any) -> dict[str, Any]:
        """Extract user-supplied entry fields (id/parent_id/seq/timestamp/lane
 are NOT user-supplied; they're stamped by storage/state).
"""
        from dataclasses import asdict, is_dataclass
        if is_dataclass(entry):
            d = asdict(entry)
        elif isinstance(entry, dict):
            d = dict(entry)
        else:
            d = dict(vars(entry))
        return d

    @staticmethod
    def _record_fields(record: Any) -> dict[str, Any]:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(record):
            d = asdict(record)
        elif isinstance(record, dict):
            d = dict(record)
        else:
            d = dict(vars(record))
        return d

    @staticmethod
    def _wrap_entry(entry_dict: dict[str, Any]) -> Any:
        """Reconstruct a frozen entry dataclass and attach storage metadata."""
        entry = _entry_dict_to_dataclass(entry_dict)
        return _with_meta(
            entry,
            entry_id=entry_dict["id"],
            parent_id=entry_dict.get("parent_id"),
            seq=entry_dict["seq"],
            timestamp=entry_dict["timestamp"],
            lane=entry_dict.get("lane", "main"),
        )

    @staticmethod
    def _wrap_record(record_dict: dict[str, Any]) -> Any:
        record = _record_dict_to_dataclass(record_dict)
        return _record_with_meta(
            record,
            record_id=record_dict["id"],
            timestamp=record_dict["timestamp"],
            lane=record_dict.get("lane"),
        )