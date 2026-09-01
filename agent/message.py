from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class TextContent:
    type: Literal["text"]
    text: str


@dataclass(frozen=True)
class ImageContent:
    type: Literal["image"]
    data: str
    mime_type: str | None = None


@dataclass(frozen=True)
class ThinkingContent:
    type: Literal["thinking"]
    text: str


@dataclass(frozen=True)
class ToolCallContent:
    type: Literal["toolCall"]
    id: str
    name: str
    arguments: dict[str, Any]


StopReason = Literal["stop", "length", "tool_use", "error", "aborted"]


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: dict[str, float] | None = None


@dataclass(frozen=True)
class UserMessage:
    role: Literal["user"]
    content: list[TextContent | ImageContent]
    timestamp: int


@dataclass(frozen=True)
class AssistantMessage:
    role: Literal["assistant"]
    content: list[TextContent | ThinkingContent | ToolCallContent]
    api: str
    provider: str
    model: str
    usage: Usage
    stop_reason: StopReason
    error_message: str | None
    timestamp: int


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    details: Any = None
    usage: Usage | None = None
    added_tool_names: list[str] | None = None
    is_error: bool = False
    timestamp: int = 0
    role: Literal["toolResult"] = "toolResult"


@dataclass(frozen=True)
class CustomMessage:
    role: Literal["custom"]
    custom_type: str
    content: str | list[TextContent | ImageContent]
    display: bool
    details: Any | None = None
    timestamp: int = 0


@dataclass(frozen=True)
class CompactionSummaryMessage:
    role: Literal["compactionSummary"]
    summary: str
    tokens_before: int
    timestamp: int = 0


@dataclass(frozen=True)
class BranchSummaryMessage:
    role: Literal["branchSummary"]
    summary: str
    from_id: str
    timestamp: int = 0
