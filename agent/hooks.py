from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from agent.context import SessionContext
from agent.convert import AgentMessage
from agent.message import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
)

LLMStreamEvent = (
    dict[str, Any]  # {"type": "start", "partial": AssistantMessage}
    | dict[str, Any]  # {"type": "text_start" | "text_delta" | "text_end", ...}
    | dict[str, Any]  # {"type": "thinking_start" | "thinking_delta" | "thinking_end", ...}
    | dict[str, Any]  # {"type": "toolcall_start" | "toolcall_delta" | "toolcall_end", ...}
    | dict[str, Any]  # {"type": "done"}
    | dict[str, Any]  # {"type": "error", "error_message": str}
)

StreamFn = Callable[..., Any]

ToolExecutionMode = Literal["sequential", "parallel"]


@dataclass
class BeforeToolCallResult:
    block: bool | None = None
    reason: str | None = None
    terminate: bool | None = None  # if True and ALL tool calls in batch also set terminate, batch early-stops


@dataclass
class AfterToolCallResult:
    content: list[TextContent | ImageContent] | None = None
    details: Any = None
    usage: Usage | None = None
    is_error: bool | None = None
    terminate: bool | None = None


@dataclass
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCallContent
    args: Any
    context: SessionContext


@dataclass
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCallContent
    args: Any
    result: Any
    is_error: bool
    context: SessionContext


@dataclass
class ShouldStopContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: SessionContext
    new_messages: list[AgentMessage]


@dataclass
class PrepareNextTurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: SessionContext
    new_messages: list[AgentMessage]


@dataclass
class AgentLoopTurnUpdate:
    context: SessionContext | None = None
    model: Any | None = None
    thinking_level: str | None = None


ConvertToLlm = Callable[[list[AgentMessage]], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
TransformContext = Callable[[list[AgentMessage], Any], Awaitable[list[AgentMessage]]]
ShouldStopAfterTurn = Callable[[ShouldStopContext], Awaitable[bool] | bool]
PrepareNextTurn = Callable[[PrepareNextTurnContext], Awaitable[AgentLoopTurnUpdate | None] | AgentLoopTurnUpdate | None]
GetSteeringMessages = Callable[[], Awaitable[list[AgentMessage]]]
GetFollowUpMessages = Callable[[], Awaitable[list[AgentMessage]]]
BeforeToolCall = Callable[[BeforeToolCallContext, Any], Awaitable[BeforeToolCallResult | None] | BeforeToolCallResult | None]
AfterToolCall = Callable[[AfterToolCallContext, Any], Awaitable[AfterToolCallResult | None] | AfterToolCallResult | None]
GetApiKey = Callable[[str], Awaitable[str | None] | str | None]


@dataclass
class AgentLoopConfig:
    model: Any
    convert_to_llm: ConvertToLlm

    thinking_level: str | None = None

    transform_context: TransformContext | None = None

    should_stop_after_turn: ShouldStopAfterTurn | None = None
    prepare_next_turn: PrepareNextTurn | None = None

    get_steering_messages: GetSteeringMessages | None = None
    get_follow_up_messages: GetFollowUpMessages | None = None

    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None

    stream_fn: StreamFn = None  # type: ignore[assignment]
    get_api_key: GetApiKey | None = None
    tool_execution: ToolExecutionMode = "sequential"
    session_id: str | None = None

    resolve_tool: Any = field(
        default_factory=lambda: (lambda _name: None)
    )

AgentEvent = dict[str, Any]