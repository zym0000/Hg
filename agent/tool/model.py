"""Re-export of message types consumed by AgentTool / AgentToolResult.

Avoids circular imports between agent.tool and agent.message.
"""
from agent.message import (
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
)

# Re-export AgentToolResult from the executor module so agent.tool
# consumers don't pull in agent.tool_executor transitively when they only
# need the message types. Canonical definition lives in
# (the executor owns its concrete factories).
# This import is safe because does NOT import from
# agent.tool at runtime (only TYPE_CHECKING), so the cycle is broken.
from agent.tool_executor import AgentToolResult

__all__ = [
    "AgentToolResult",
    "ImageContent",
    "TextContent",
    "ThinkingContent",
    "ToolCallContent",
    "ToolResultMessage",
    "Usage",
]