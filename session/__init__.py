"""Session package: tree-structured, forkable storage for agent conversations.

Modules:
- types: Entry + Record dataclasses, LanePointer, SessionMetadata, IdGenerator Protocol
- storage.base: SessionStorage Protocol
- storage.memory: InMemorySessionStorage (test default)
- storage.jsonl: JsonlSessionStorage (v1 production default)
- session: Session class façade
- repo: SessionRepo (create/open/list/delete/fork + crash recovery)
- context: build_session_context (derive SessionContext from entry log)
"""

try:
    from session.types import (
        ActiveToolsEntry,
        BranchSummaryEntry,
        CompactionEntry,
        CustomEntry,
        MessageEntry,
        ModelChangeEntry,
        ThinkingLevelEntry,
        OperationStartedRecord,
        OperationFinishedRecord,
        AbortRequestedRecord,
        StepAttemptRecord,
        ToolStartedRecord,
        QueueEnqueuedRecord,
        QueueCancelledRecord,
        WriteDeferredRecord,
        UsageRecord,
        LanePointer,
        SessionMetadata,
        ProvisionedEntry,
        IdGenerator,
    )
except ImportError:
    ActiveToolsEntry = None  # type: ignore
    BranchSummaryEntry = None  # type: ignore
    CompactionEntry = None  # type: ignore
    CustomEntry = None  # type: ignore
    MessageEntry = None  # type: ignore
    ModelChangeEntry = None  # type: ignore
    ThinkingLevelEntry = None  # type: ignore
    OperationStartedRecord = None  # type: ignore
    OperationFinishedRecord = None  # type: ignore
    AbortRequestedRecord = None  # type: ignore
    StepAttemptRecord = None  # type: ignore
    ToolStartedRecord = None  # type: ignore
    QueueEnqueuedRecord = None  # type: ignore
    QueueCancelledRecord = None  # type: ignore
    WriteDeferredRecord = None  # type: ignore
    UsageRecord = None  # type: ignore
    LanePointer = None  # type: ignore
    SessionMetadata = None  # type: ignore
    ProvisionedEntry = None  # type: ignore
    IdGenerator = None  # type: ignore


__all__ = [
    "ActiveToolsEntry",
    "BranchSummaryEntry",
    "CompactionEntry",
    "CustomEntry",
    "MessageEntry",
    "ModelChangeEntry",
    "ThinkingLevelEntry",
    "OperationStartedRecord",
    "OperationFinishedRecord",
    "AbortRequestedRecord",
    "StepAttemptRecord",
    "ToolStartedRecord",
    "QueueEnqueuedRecord",
    "QueueCancelledRecord",
    "WriteDeferredRecord",
    "UsageRecord",
    "LanePointer",
    "SessionMetadata",
    "ProvisionedEntry",
    "IdGenerator",
]