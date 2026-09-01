"""InMemorySessionStorage: test-default backend.

Owns a `_metadata: SessionMetadata` and a `_state: SessionState` (the canonical
state machine). Every mutation goes through `self._state.apply_mutation(...)`;
every query delegates to `self._state.<query>()`. An `asyncio.Lock` serializes
mutations so concurrent appends yield monotonic seq values.

Thread-safe via asyncio.Lock. Not durable across process restarts.
"""
import asyncio
import copy
import time
import uuid
from typing import Any

from session.mutations import (
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
from session.storage.base import SessionStorage
from session.types import LanePointer, SessionMetadata, SessionStats


class InMemorySessionStorage(SessionStorage):
    def __init__(self, metadata: SessionMetadata) -> None:
        self._metadata = copy.deepcopy(metadata)
        self._state = SessionState()
        self._lock = asyncio.Lock()

    # metadata ------------------------------------------------------------

    async def get_metadata(self) -> SessionMetadata:
        return SessionMetadata(
            id=self._metadata.id,
            created_at=self._metadata.created_at,
            parent_session_id=self._metadata.parent_session_id,
            name=self._state.name,
            lanes=list(self._state.lanes.keys()),
        )

    async def get_name(self) -> str | None:
        return self._state.get_name()

    async def set_name(self, name: str | None) -> None:
        async with self._lock:
            self._state.apply_mutation(
                name_fact_mutation(seq=self._state.sequence + 1, name=name)
            )


    async def get_lanes(self) -> list[LanePointer]:
        return self._state.get_lanes()

    async def create_lane(self, name: str, leaf_id: str) -> None:
        async with self._lock:
            self._state.validate_new_lane(name)
            self._state.validate_target(leaf_id)
            self._state.apply_mutation(
                lane_mutation(
                    seq=self._state.sequence + 1, lane=name, leaf_id=leaf_id,
                )
            )

    async def move_lane(self, name: str, leaf_id: str) -> None:
        async with self._lock:
            self._state.require_lane(name)  # raises INVALID_LANE if missing
            self._state.validate_target(leaf_id)
            self._state.apply_mutation(
                lane_mutation(
                    seq=self._state.sequence + 1, lane=name, leaf_id=leaf_id,
                )
            )

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
            self._state.apply_mutation(
                entry_mutation(entry=entry_dict, lane=lane)
            )
            # _state has now stamped seq onto the dict.
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
            record_id = uuid.uuid4().hex
            timestamp = time.time()
            record_dict = {
                "id": record_id,
                "timestamp": timestamp,
                **self._record_fields(record),
            }
            if lane is not None:
                # Auto-create lane on first use so callers don't need to
                # explicitly register it before appending a record.
                if lane not in self._state.lanes:
                    self._state.lanes[lane] = None
                record_dict["lane"] = lane
            from session.json_validity import assert_json_serializable
            assert_json_serializable(record_dict)
            self._state.apply_mutation(record_mutation(record=record_dict))
            return self._wrap_record(record_dict)

    async def find_records(self, query: dict[str, Any] | None = None) -> list[Any]:
        return [self._wrap_record(r) for r in self._state.find_records(query)]

    async def find_open_operations(self, lane: str | None = None) -> list[Any]:
        # Walk records: operation_started without matching operation_finished.
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
            self._state.apply_mutation(
                label_fact_mutation(
                    seq=self._state.sequence + 1,
                    target_id=entry_id,
                    label=label,
                )
            )

    async def fork(self, metadata: "SessionMetadata", options: "ForkOptions"):
        """Create a new InMemorySessionStorage by replaying fork mutations.

 SessionStorage.fork signature. Returns a new storage instance
 whose state is derived by applying compute_fork_mutations(source._state, options).
 Records are NOT copied. Labels filtered by scope. Name always copied.
"""
        from session.fork import compute_fork_mutations
        new_storage = InMemorySessionStorage(metadata=metadata)
        for mutation in compute_fork_mutations(self._state, options):
            new_storage._state.apply_mutation(mutation)
        return new_storage

    async def get_stats(self) -> SessionStats:
        # _state.stats is the typed SessionStats; lane_count was dropped in B2
        # callers needing lane count can use `len(self._state.lanes)` directly.
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
        # `type` is already a key in the dataclass dump, so it carries over.
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
