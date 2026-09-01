"""agent.tool — AgentTool, registry, schema, MCP adapter.

Public API:
 AgentTool — Protocol (moved from agent.tool_executor)
 AgentToolResult — re-exported from agent.tool.model
 AgentToolRegistry — name -> AgentTool store
 make_tool_schema — OpenAI function-call schema builder
 load_mcp_tools — async MCP server -> list[AgentTool]
"""
from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult
from agent.tool.registry import AgentToolRegistry
from agent.tool.schema import make_tool_schema

__all__ = ["AgentTool", "AgentToolResult", "AgentToolRegistry", "make_tool_schema"]
