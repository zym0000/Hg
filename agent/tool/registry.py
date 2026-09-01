"""AgentToolRegistry — name -> AgentTool store, single entry point for resolution.
``agent.tool_executor.prepare_tool_call`` to translate a tool name from
the model's tool call into the concrete AgentTool that will run.

Why this exists: a misspelled tool name from the model becomes
``ImmediateToolCallOutcome("Tool X not found")`` rather than a NameError
at the call site. The registry owns the lookup; the executor owns the
error semantics.
"""
from typing import Iterable

from agent.tool.agent_tool import AgentTool


class AgentToolRegistry:
    def __init__(self, tools: Iterable[AgentTool] | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        if tools:
            self.register_many(tools)

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def register_many(self, tools: Iterable[AgentTool]) -> None:
        for t in tools:
            self.register(t)

    def resolve_tool(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def list(self) -> list[AgentTool]:
        return list(self._tools.values())