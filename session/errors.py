from enum import Enum


class CompactionErrorKind(str, Enum):
    ABORTED = "aborted"
    SUMMARIZATION_FAILED = "summarization_failed"

class CompactionError(Exception):
    def __init__(
        self,
        kind: CompactionErrorKind,
        message: str,
        run_id: str | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.run_id = run_id
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        return f"[{self.kind.value}] {super().__str__()}"

class SessionErrorKind(str, Enum):
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    INVALID_LANE = "invalid_lane"
    INVALID_TARGET = "invalid_target"
    INVALID_FORK_TARGET = "invalid_fork_target"
    INVALID_BRANCH_SUMMARY_TARGET = "invalid_branch_summary_target"
    INVALID_ENTRY = "invalid_entry"
    INVALID_QUERY = "invalid_query"
    INVALID_PAYLOAD = "invalid_payload"
    STORAGE = "storage"


class SessionError(Exception):
    """Session-layer error mirroring SessionError ()."""

    def __init__(
        self,
        code: SessionErrorKind,
        message: str,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        if cause is not None:
            self.__cause__ = cause

    def __str__(self) -> str:
        return f"[{self.code.value}] {super().__str__()}"
