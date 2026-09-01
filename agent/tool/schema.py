"""make_tool_schema — emit OpenAI function-call schema dict from AgentTool.

``LLMClient.chat`` and ``LLMClient.chat_stream`` accept
``tool_schema: List[Dict[str, Any]]`` directly. We produce the same shape
OpenAI returns: ``{"type": "function", "function": {name, description,
parameters}}``.
"""
from typing import Any

from agent.tool.agent_tool import AgentTool


def make_tool_schema(tool: AgentTool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }