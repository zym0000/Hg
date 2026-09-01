"""AgentTool Protocol — AgentTool<TParameters, TDetails> mirror.

Concrete tools are duck typed; this Protocol documents the surface that
``agent.tool_executor`` and ``agent.tool.registry`` rely on.

Mirrors ```` lines 386-409 (AgentTool) and
322-355 (Tool base).
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from agent.tool.model import AgentToolResult


@runtime_checkable
class AgentTool(Protocol):
    """Protocol every concrete tool must satisfy.

    Attributes:
        name:        tool identifier used in tool-call payloads.
        label:       short human-readable label for UI rendering.
        description: free-form description surfaced to the LLM.
        parameters:  JSON-schema describing the tool's argument shape.
        execution_mode: "sequential" or "parallel" — hints for the executor.

    Methods:
        prepare_arguments(args): normalize/validate the model's args dict
            before execution. Default is identity.
        execute(tool_call_id, args, signal, on_update): run the tool and
            return an ``AgentToolResult``. May be async.
    """

    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    execution_mode: str

    def prepare_arguments(self, args: Any) -> Any: ...

    async def execute(
        self,
        tool_call_id: str,
        args: Any,
        signal: Any,
        on_update: Callable[[AgentToolResult], None] | None,
    ) -> AgentToolResult: ...
