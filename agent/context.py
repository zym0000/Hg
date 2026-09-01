from dataclasses import dataclass
from typing import Any

from agent.message import (
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ToolResultMessage,
    UserMessage,
)

AgentMessageT = (
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | CustomMessage
    | CompactionSummaryMessage
    | BranchSummaryMessage
)

@dataclass
class SessionContext:
    messages: list[AgentMessageT]
    thinking_level: str = "off"
    model: dict[str, str] | None = None
    active_tool_names: list[str] | None = None
    tools: list[Any] | None = None  # populated by Agent._run_with_lifecycle
    system_prompt: str | None = None  # forwarded to the LLM as a system message