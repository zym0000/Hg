from typing import Any

from agent.message import UserMessage, AssistantMessage, ToolResultMessage, CustomMessage

AgentMessageT = UserMessage | AssistantMessage | ToolResultMessage | CustomMessage


async def default_transform_context(
    messages: list[AgentMessageT],
    signal: Any | None,
) -> list[AgentMessageT]:
    return list(messages)
