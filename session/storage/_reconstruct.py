"""Shared reconstruction helpers for SessionStorage backends.

Convert plain dicts (as stored in `_state.entries` / `_state.records`) back into
the user-facing frozen dataclass instances, with id/parent_id/seq/timestamp/lane
attached as attributes.

Both InMemorySessionStorage (Task 9) and JsonlSessionStorage (Task 10) reuse
these helpers; Jsonl previously kept its own copies.
"""
from copy import copy
from dataclasses import fields, is_dataclass
from typing import Any

from session.types import (
    AbortRequestedRecord,
    ActiveToolsEntry,
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    MessageEntry,
    ModelChangeEntry,
    OperationFinishedRecord,
    OperationStartedRecord,
    QueueCancelledRecord,
    QueueEnqueuedRecord,
    StepAttemptRecord,
    ThinkingLevelEntry,
    ToolStartedRecord,
    UsageRecord,
    WriteDeferredRecord,
)


ENTRY_TYPE_MAP: dict[str, type] = {
    "message": MessageEntry,
    "model_change": ModelChangeEntry,
    "thinking_level_change": ThinkingLevelEntry,
    "active_tools_change": ActiveToolsEntry,
    "compaction": CompactionEntry,
    "branch_summary": BranchSummaryEntry,
    "custom": CustomEntry,
}


RECORD_TYPE_MAP: dict[str, type] = {
    "operation_started": OperationStartedRecord,
    "operation_finished": OperationFinishedRecord,
    "abort_requested": AbortRequestedRecord,
    "step_attempt": StepAttemptRecord,
    "tool_started": ToolStartedRecord,
    "queue_enqueued": QueueEnqueuedRecord,
    "queue_cancelled": QueueCancelledRecord,
    "write_deferred": WriteDeferredRecord,
    "usage": UsageRecord,
}


def _rehydrate(value: Any, target_cls: Any) -> Any:
    """If value is a dict and target_cls is a dataclass, rehydrate.

 Handles Optional[T] (typing.Union[X, None] and PEP 604 X | None).
"""
    if value is None:
        return None
    origin = getattr(target_cls, "__origin__", None)
    if origin is not None and hasattr(target_cls, "__args__"):
        for arg in target_cls.__args__:
            if arg is type(None):
                continue
            if is_dataclass(arg) and isinstance(value, dict):
                return _rehydrate(value, arg)
        return value
    args = getattr(target_cls, "__args__", None)
    if args is not None and not isinstance(target_cls, type):
        for arg in args:
            if arg is type(None):
                continue
            if is_dataclass(arg) and isinstance(value, dict):
                return _rehydrate(value, arg)
        return value
    if is_dataclass(target_cls) and isinstance(value, dict):
        kwargs = {
            f.name: _rehydrate(value.get(f.name), f.type)
            for f in fields(target_cls)
        }
        return target_cls(**kwargs)
    return value


def _entry_dict_to_dataclass(entry: dict[str, Any]) -> Any:
    """Reconstruct a typed frozen entry dataclass from a plain dict.

 Storage assigns id/parent_id/seq/timestamp/lane onto the stored dict, so
 those keys are NOT reconstructed as fields. The typed fields are pulled
 from the dict and rehydrated (so nested dataclasses like CompactionDetails
 become typed instances).
"""
    etype = entry.get("type")
    cls = ENTRY_TYPE_MAP.get(etype)
    if cls is None:
        # Unknown type — fall back to dict (callers that care about types
        # check isinstance(fetched, ExpectedType)).
        return entry
    # Clean break: sessions stored `retained_tail`. Refuse
    # to load those — compatibility shims would corrupt downstream filtering.
    if etype == "compaction" and "retained_tail" in entry:
        raise ValueError(
            "Session file uses the legacy 'retained_tail' compaction format "
            "( migration). Recreate the session."
        )
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name == "type":
            continue
        kwargs[f.name] = _rehydrate(entry.get(f.name), f.type)
    return cls(type=etype, **kwargs)


def _record_dict_to_dataclass(record: dict[str, Any]) -> Any:
    """Reconstruct a typed frozen record dataclass from a plain dict."""
    rtype = record.get("type")
    cls = RECORD_TYPE_MAP.get(rtype)
    if cls is None:
        return record
    kwargs = {
        f.name: _rehydrate(record.get(f.name), f.type)
        for f in fields(cls)
        if f.name != "type"
    }
    return cls(type=rtype, **kwargs)


def _with_meta(
    entry: Any,
    *,
    entry_id: str,
    parent_id: str | None,
    seq: int,
    timestamp: float,
    lane: str,
) -> Any:
    """Return a copy of a frozen entry dataclass with id/parent_id/seq/timestamp/lane attached."""
    new = copy(entry)
    object.__setattr__(new, "id", entry_id)
    object.__setattr__(new, "parent_id", parent_id)
    object.__setattr__(new, "seq", seq)
    object.__setattr__(new, "timestamp", timestamp)
    object.__setattr__(new, "lane", lane)
    return new


def _record_with_meta(
    record: Any,
    *,
    record_id: str,
    timestamp: float,
    lane: str | None = None,
) -> Any:
    """Return a copy of a frozen record dataclass with id/timestamp[/lane] attached."""
    new = copy(record)
    object.__setattr__(new, "id", record_id)
    object.__setattr__(new, "timestamp", timestamp)
    if lane is not None:
        object.__setattr__(new, "lane", lane)
    return new
