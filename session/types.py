from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from context.compaction import CompactionDetails

@dataclass(frozen=True)
class LanePointer:
    """Pointer to the leaf entry of a named lane."""
    name: str
    leaf_id: str

@dataclass(frozen=True)
class SessionMetadata:
    id: str
    created_at: float
    name: str | None = None
    lanes: list[str] = field(default_factory=lambda: ["main"])
    parent_session_id: str | None = None
    cwd: str | None = None

@dataclass(frozen=True)
class SessionStats:
    message_count: int = 0
    cached_tokens: int = 0
    uncached_tokens: int = 0
    total_tokens: int = 0
    cost_total: float = 0.0

@dataclass(frozen=True)
class ProvisionedEntry:
    entry_id: str
    message: dict[str, Any]


class IdGenerator(Protocol):
    def next_id(self) -> str: ...

@dataclass(frozen=True)
class MessageEntry:
    type: Literal["message"]
    message: dict[str, Any]
    terminate: bool = False

@dataclass(frozen=True)
class ModelChangeEntry:
    type: Literal["model_change"]
    provider: str
    model_id: str

@dataclass(frozen=True)
class ThinkingLevelEntry:
    type: Literal["thinking_level_change"]
    thinking_level: str

@dataclass(frozen=True)
class ActiveToolsEntry:
    type: Literal["active_tools_change"]
    active_tool_names: list[str]

@dataclass(frozen=True)
class CompactionEntry:
    type: Literal["compaction"]
    summary: str
    first_kept_entry_id: str | None
    tokens_before: int
    details: CompactionDetails | None = None
    usage: Any | None = None

@dataclass(frozen=True)
class BranchSummaryEntry:
    type: Literal["branch_summary"]
    from_id: str
    summary: str
    details: Any | None = None
    usage: Any | None = None

@dataclass(frozen=True)
class CustomEntry:
    type: Literal["custom"]
    custom_type: str
    data: Any | None = None

@dataclass(frozen=True)
class OperationStartedRecord:
    type: Literal["operation_started"]
    run_id: str
    intent: dict[str, Any]

@dataclass(frozen=True)
class OperationFinishedRecord:
    type: Literal["operation_finished"]
    run_id: str
    outcome: Literal["completed", "aborted", "failed", "declined"]
    error: dict[str, Any] | None = None

@dataclass(frozen=True)
class AbortRequestedRecord:
    type: Literal["abort_requested"]
    run_id: str

@dataclass(frozen=True)
class StepAttemptRecord:
    type: Literal["step_attempt"]
    run_id: str
    step: Literal["assistant", "branch_summary", "compaction"]
    attempt: int
    result_entry_id: str
    compaction_reason: str | None = None

@dataclass(frozen=True)
class ToolStartedRecord:
    type: Literal["tool_started"]
    run_id: str
    assistant_entry_id: str
    tool_index: int
    tool_call_id: str
    tool_name: str
    effective_args: dict[str, Any]
    result_entry_id: str
    replay: Literal["never", "safe"]

@dataclass(frozen=True)
class QueueEnqueuedRecord:
    type: Literal["queue_enqueued"]
    queue: Literal["steer", "follow_up", "next_run"]
    target: ProvisionedEntry

@dataclass(frozen=True)
class QueueCancelledRecord:
    type: Literal["queue_cancelled"]
    entry_id: str

@dataclass(frozen=True)
class WriteDeferredRecord:
    type: Literal["write_deferred"]
    target: ProvisionedEntry

@dataclass(frozen=True)
class UsageRecord:
    type: Literal["usage"]
    cause: Literal["assistant", "compaction", "branch_summary", "tool", "hook", "deferred_fetch", "adjustment"]
    usage: dict[str, Any]