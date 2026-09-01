import dataclasses
from collections.abc import Callable, Iterator
from typing import Any

from session.errors import SessionError, SessionErrorKind
from session.mutations import SessionMutation
from session.types import LanePointer, SessionStats


def _invalid_mutation(reason: str) -> None:
    raise SessionError(SessionErrorKind.STORAGE, reason)

def _is_valid_query(q: dict | None) -> None:
    """Raise SessionError(INVALID_QUERY) on malformed query."""
    if q is None:
        return
    limit = q.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise SessionError(SessionErrorKind.INVALID_QUERY, "limit must be a positive integer")
    cursor = q.get("cursor") or {}
    after_seq = cursor.get("after_seq")
    if after_seq is not None and (not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0):
        raise SessionError(SessionErrorKind.INVALID_QUERY, "cursor.after_seq must be a non-negative integer")


def _entry_matches(entry: dict, q: dict) -> bool:
    type_filter = q.get("type")
    if type_filter is not None and entry.get("type") != type_filter:
        return False
    custom_type_filter = q.get("custom_type")
    if custom_type_filter is not None and entry.get("type") == "custom":
        if entry.get("custom_type") != custom_type_filter:
            return False
    cursor = q.get("cursor") or {}
    if "after_seq" in cursor:
        order = q.get("order", "oldestFirst")
        seq = entry.get("seq", 0)
        if order == "oldestFirst":
            if seq <= cursor["after_seq"]:
                return False
        else:  # newestFirst
            if seq >= cursor["after_seq"]:
                return False
    return True

def _record_matches(r: dict, q: dict) -> bool:
    if q.get("lane") is not None and r.get("lane") != q["lane"]:
        return False
    if q.get("type") is not None and r.get("type") != q["type"]:
        return False
    if q.get("run_id") is not None:
        rid = r.get("run_id")
        if rid != q["run_id"]:
            return False
    if q.get("operation_kind") is not None:
        if r.get("type") != "operation_started":
            return False
        intent = r.get("intent", {})
        if intent.get("kind") != q["operation_kind"]:
            return False
    if q.get("after_seq") is not None and r.get("seq", 0) <= q["after_seq"]:
        return False
    return True

class SessionState:
    def __init__(self) -> None:
        self.sequence: int = 0
        self.used_ids: set[str] = set()
        self.entries: list[dict[str, Any]] = []
        self.entries_by_id: dict[str, dict[str, Any]] = {}
        self.records: list[dict[str, Any]] = []
        self.lanes: dict[str, str | None] = {"main": None}
        self.log: list[dict[str, Any]] = []
        self.stats: SessionStats = SessionStats()
        self.name: str | None = None
        self.labels: dict[str, str] = {}

    def apply_mutation(
        self,
        mutation: SessionMutation,
        invalid: Callable[[str], None] = _invalid_mutation,
    ) -> None:
        # Top-level seq check: applies to mutation kinds that carry an
        # explicit `seq` (lane / name / label). Entry and record mutations
        # auto-stamp a seq via _next_seq and carry seq=None, so we skip them.
        if (
            mutation.kind in ("lane", "fact")
            and mutation.seq is not None
            and mutation.seq != self.sequence + 1
        ):
            raise SessionError(
                SessionErrorKind.INVALID_ENTRY,
                f"non-consecutive seq: expected {self.sequence + 1}, got {mutation.seq}",
            )
        if mutation.kind == "entry":
            self._apply_entry(mutation, invalid)
        elif mutation.kind == "record":
            self._apply_record(mutation, invalid)
        elif mutation.kind == "lane":
            self._apply_lane(mutation, invalid)
        elif mutation.kind == "fact":
            if mutation.fact_kind == "name":
                self._apply_name_fact(mutation, invalid)
            elif mutation.fact_kind == "label":
                self._apply_label_fact(mutation, invalid)
            else:
                invalid(f"unknown fact_kind: {mutation.fact_kind}")
        else:
            invalid(f"unknown mutation kind: {mutation.kind}")

    def _next_seq(self) -> int:
        seq = self.sequence
        self.sequence += 1
        return seq

    def _apply_entry(self, m: SessionMutation, invalid: Callable[[str], None]) -> None:
        entry = m.entry
        if entry is None:
            invalid("entry_mutation missing entry")
            return
        entry_id = entry.get("id")
        if not entry_id:
            invalid("entry missing id")
            return
        # Lane must exist . Storage layer is
        # responsible for registering the lane before appending.
        lane = m.lane or entry.get("lane")
        if lane is not None and lane not in self.lanes:
            raise SessionError(
                SessionErrorKind.INVALID_LANE,
                f"unknown lane: {lane!r}",
            )
        # parent_id (if set) must reference an existing entry
        parent_id = entry.get("parent_id")
        if parent_id is not None and parent_id not in self.entries_by_id:
            raise SessionError(
                SessionErrorKind.INVALID_ENTRY,
                f"parent not found: {parent_id!r}",
            )
        if entry_id in self.used_ids:
            raise SessionError(SessionErrorKind.ALREADY_EXISTS, f"id already used: {entry_id}")
        self.used_ids.add(entry_id)
        seq = self._next_seq()
        entry["seq"] = seq
        self.entries.append(entry)
        self.entries_by_id[entry_id] = entry
        if entry.get("type") == "message":
            self.stats = dataclasses.replace(
                self.stats,
                message_count=self.stats.message_count + 1,
            )
        if lane is not None:
            self.lanes[lane] = entry_id
        self.log.append({"kind": "entry", "id": entry_id, "lane": lane, "seq": seq})

    def _apply_record(self, m: SessionMutation, invalid: Callable[[str], None]) -> None:
        record = m.record
        if record is None:
            invalid("record_mutation missing record")
            return
        # Lane (if present) must exist.
        # Storage layer is responsible for registering the lane.
        rec_lane = record.get("lane")
        if rec_lane is not None and rec_lane not in self.lanes:
            raise SessionError(
                SessionErrorKind.INVALID_LANE,
                f"unknown lane: {rec_lane!r}",
            )
        seq = self._next_seq()
        record["seq"] = seq
        self.records.append(record)
        if record.get("type") == "usage":
            usage = record.get("usage") or {}
            cache_read = int(usage.get("cacheRead", 0) or 0)
            input_t = int(usage.get("input", 0) or 0)
            cache_write = int(usage.get("cacheWrite", 0) or 0)
            total = int(usage.get("totalTokens", 0) or 0)
            cost_obj = usage.get("cost") or {}
            cost = float(cost_obj.get("total", 0.0) or 0.0)
            self.stats = dataclasses.replace(
                self.stats,
                cached_tokens=self.stats.cached_tokens + cache_read,
                uncached_tokens=self.stats.uncached_tokens + input_t + cache_write,
                total_tokens=self.stats.total_tokens + total,
                cost_total=self.stats.cost_total + cost,
            )
        self.log.append({"kind": "record", "id": record.get("id"), "seq": seq})

    def _apply_lane(self, m: SessionMutation, invalid: Callable[[str], None]) -> None:
        # leaf_id must reference an existing entry.
        if m.leaf_id is not None and m.leaf_id not in self.entries_by_id:
            raise SessionError(
                SessionErrorKind.INVALID_ENTRY,
                f"leaf not found: {m.leaf_id!r}",
            )
        self.lanes[m.lane] = m.leaf_id
        self.log.append({"kind": "lane", "lane": m.lane, "leaf_id": m.leaf_id, "seq": m.seq})

    def _apply_name_fact(self, m: SessionMutation, invalid: Callable[[str], None]) -> None:
        self.name = m.name
        self.log.append({"kind": "name", "name": m.name, "seq": m.seq})

    def _apply_label_fact(self, m: SessionMutation, invalid: Callable[[str], None]) -> None:
        # target_id must reference an existing entry
        if m.target_id not in self.entries_by_id:
            raise SessionError(
                SessionErrorKind.INVALID_ENTRY,
                f"label target not found: {m.target_id!r}",
            )
        if m.label is None:
            self.labels.pop(m.target_id, None)
        else:
            self.labels[m.target_id] = m.label
        self.log.append({"kind": "label", "target_id": m.target_id, "label": m.label, "seq": m.seq})

    def require_lane(self, lane: str) -> str | None:
        return self.lanes.get(lane)

    def validate_new_lane(self, lane: str) -> None:
        if lane in self.lanes:
            raise SessionError(SessionErrorKind.INVALID_LANE, f"lane already exists: {lane}")

    def validate_target(self, entry_id: str | None) -> None:
        if entry_id is None or entry_id not in self.entries_by_id:
            raise SessionError(
                SessionErrorKind.INVALID_TARGET,
                f"target entry not found: {entry_id}",
            )

    def validate_unused_id(self, entry_id: str) -> None:
        if entry_id in self.used_ids:
            raise SessionError(
                SessionErrorKind.ALREADY_EXISTS, f"id already used: {entry_id}",
            )

    def get_name(self) -> str | None:
        return self.name

    def get_label(self, entry_id: str) -> str | None:
        return self.labels.get(entry_id)

    def get_stats(self) -> SessionStats:
        return self.stats

    def get_lanes(self) -> list[LanePointer]:
        return [LanePointer(name=name, leaf_id=leaf) for name, leaf in self.lanes.items()]

    def get_entry(self, entry_id: str) -> dict | None:
        return self.entries_by_id.get(entry_id)

    def find_entries(self, query: dict | None = None) -> list[dict]:
        _is_valid_query(query)
        q = query or {}
        order = q.get("order", "oldestFirst")
        # Sort by seq ascending (oldest first); reverse for newestFirst.
        sorted_entries = sorted(self.entries, key=lambda e: e.get("seq", 0))
        if order == "newestFirst":
            sorted_entries = list(reversed(sorted_entries))
        out: list[dict] = []
        for e in sorted_entries:
            if not _entry_matches(e, q):
                continue
            out.append(e)
            if q.get("limit") is not None and len(out) >= q["limit"]:
                break
        return out

    def find_entries_on_branch(self, query: dict | None = None) -> list[dict]:
        if not query or "start" not in query:
            raise SessionError(SessionErrorKind.INVALID_QUERY, "find_entries_on_branch requires start entry id")
        _is_valid_query(query)
        q = query
        order = q.get("order", "oldestFirst")
        chain = list(self.walk_to_root(q["start"]))
        if order == "newestFirst":
            chain.reverse()
        stop_at_id = q.get("stop_at_id")
        stop_at_type = q.get("stop_at_type")
        out: list[dict] = []
        for e in chain:
            if not _entry_matches(e, q):
                continue
            out.append(e)
            # Branch bound: stop AFTER including the matching entry.
            if stop_at_id and e.get("id") == stop_at_id:
                break
            if stop_at_type and e.get("type") == stop_at_type:
                break
            if q.get("limit") is not None and len(out) >= q["limit"]:
                break
        return out

    def find_records(self, query: dict | None = None) -> list[dict]:
        _is_valid_query(query)
        q = query or {}
        order = q.get("order", "newestFirst")
        sorted_records = sorted(self.records, key=lambda r: r.get("seq", 0))
        if order == "newestFirst":
            sorted_records = list(reversed(sorted_records))
        out: list[dict] = []
        for r in sorted_records:
            if not _record_matches(r, q):
                continue
            out.append(r)
            if q.get("limit") is not None and len(out) >= q["limit"]:
                break
        return out

    def get_log(self, options: dict | None = None) -> list[dict]:
        opts = options or {}
        if opts.get("limit") is not None and (
            not isinstance(opts["limit"], int)
            or isinstance(opts["limit"], bool)
            or opts["limit"] <= 0
        ):
            raise SessionError(SessionErrorKind.INVALID_QUERY, "limit must be a positive integer")
        if opts.get("after_seq") is not None and (
            not isinstance(opts["after_seq"], int)
            or isinstance(opts["after_seq"], bool)
            or opts["after_seq"] < 0
        ):
            raise SessionError(SessionErrorKind.INVALID_QUERY, "afterSeq must be a non-negative integer")
        out: list[dict] = []
        for item in (*self.entries, *self.records):
            if opts.get("after_seq") is not None and item.get("seq", 0) <= opts["after_seq"]:
                continue
            out.append(item)
            if opts.get("limit") is not None and len(out) >= opts["limit"]:
                break
        return out

    def walk_to_root(self, start: str) -> "Iterator[dict]":
        seen: set[str] = set()
        # Walk from start toward root, then yield in oldest-first order.
        chain: list[dict] = []
        cur = self.entries_by_id.get(start)
        while cur is not None:
            if cur["id"] in seen:
                raise SessionError(
                    SessionErrorKind.INVALID_ENTRY,
                    f"Session branch contains a cycle at {cur['id']}",
                )
            seen.add(cur["id"])
            chain.append(cur)
            parent_id = cur.get("parent_id")
            if parent_id is None:
                break
            cur = self.entries_by_id.get(parent_id)
        # Reverse so the iterator yields oldest-first (root → start).
        for entry in reversed(chain):
            yield entry

    def create_fork_mutations(self, options) -> list:
        from session.fork import TreeForkOptions
        from session.mutations import (
            entry_mutation, lane_mutation, name_fact_mutation, label_fact_mutation,
        )
        # Lane/fact mutations need seqs that stay consecutive with
        # auto-stamped entry seqs (entries themselves don't carry a seq field).
        # Pre-count entries so the first lane/fact is offset by that many.
        entry_count: int = 0
        if isinstance(options, TreeForkOptions):
            entry_count = len(self.find_entries())
        else:
            entry_id = options.entry_id
            if entry_id is not None:
                selected_id = entry_id
            else:
                main_leaf_id = self.require_lane("main")
                selected_id = main_leaf_id
            if selected_id is not None:
                # Estimate entry count from the branch we will copy.
                entry_count = len(self.find_entries_on_branch(
                    {"start": selected_id, "order": "oldestFirst"}
                ))
        seq_counter = [entry_count]

        def next_seq() -> int:
            seq_counter[0] += 1
            return seq_counter[0]

        mutations: list = []

        if isinstance(options, TreeForkOptions):
            copied = self.find_entries()
            copied_ids = {e["id"] for e in copied}
            for e in copied:
                mutations.append(entry_mutation(entry=dict(e), lane=e.get("lane")))
            for name, leaf_id in self.lanes.items():
                mutations.append(lane_mutation(seq=next_seq(), lane=name, leaf_id=leaf_id))
            # Tree scope: copy all labels (since all entries are copied, every
            # label's target_id is in copied_ids; emit unconditionally for clarity).
            for label_target, label_value in self.labels.items():
                mutations.append(label_fact_mutation(
                    seq=next_seq(), target_id=label_target, label=label_value,
                ))
        else:
            # BranchForkOptions
            entry_id = options.entry_id
            if entry_id is not None:
                selected_id = entry_id
                position = options.position or "before"
            else:
                main_leaf_id = self.require_lane("main")
                if main_leaf_id is not None:
                    main_leaf = self.get_entry(main_leaf_id)
                    if main_leaf is None or main_leaf.get("type") != "message":
                        raise SessionError(
                            SessionErrorKind.INVALID_FORK_TARGET,
                            f"main lane leaf is not a message entry: "
                            f"{main_leaf_id}",
                        )
                selected_id = main_leaf_id
                position = options.position or "at"

            if position == "before" and selected_id is not None:
                target_entry = self.get_entry(selected_id)
                if target_entry is None:
                    raise SessionError(
                        SessionErrorKind.INVALID_FORK_TARGET,
                        f"Entry not found: {selected_id}",
                    )
                parent_id = target_entry.get("parent_id")
                target_id = parent_id
            else:
                target_id = selected_id

            if target_id is None:
                copied = []
                target_leaf = None
            else:
                target_entry = self.get_entry(target_id)
                if target_entry is None:
                    raise SessionError(
                        SessionErrorKind.INVALID_FORK_TARGET,
                        f"Entry not found: {target_id}",
                    )
                copied = self.find_entries_on_branch({"start": target_id, "order": "oldestFirst"})
                target_leaf = target_id

            copied_ids = {e["id"] for e in copied}
            for e in copied:
                mutations.append(entry_mutation(entry=dict(e), lane=e.get("lane")))
            mutations.append(lane_mutation(seq=next_seq(), lane="main", leaf_id=target_leaf))
            for label_target, label_value in self.labels.items():
                if label_target in copied_ids:
                    mutations.append(label_fact_mutation(
                        seq=next_seq(), target_id=label_target, label=label_value,
                    ))

        if self.name is not None:
            mutations.append(name_fact_mutation(seq=next_seq(), name=self.name))

        return mutations