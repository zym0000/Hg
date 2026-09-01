"""load_mcp_tools — connect to an MCP server, wrap each tool as AgentTool.

The harness does not currently ship an MCP client library (the
``MCP/`` directory was removed in the legacy cutover). ``_connect``
below is a stub; the real implementation will be filled in when MCP
support is reintroduced. Tests patch ``_connect`` directly to drive
the adapter's contract.

Failure policy: connection or discovery errors return ``[]`` after
writing a warning to ``stderr``. Bootstrap remains best-effort on the
MCP side; the runtime still starts with no tools if MCP is down.
"""
import sys
from typing import Any

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent


async def _connect(server_script: str) -> Any:
    """Open a connection to the MCP server defined by ``server_script``.

 Concrete implementation depends on the harness's MCP client API at
 implementation time. The expected interface is async-context-manager
 with ``list_tools()`` and ``call_tool(name, args)`` coroutines.
"""
    raise NotImplementedError("wire to harness MCP client at implementation time")


def _wrap(mcp_tool_desc: dict[str, Any]) -> AgentTool:
    """Convert an MCP tool descriptor into a duck-typed AgentTool.

 MCP descriptor shape:
 {"name": str, "description": str, "inputSchema": dict}
"""
    name = mcp_tool_desc["name"]
    description = mcp_tool_desc.get("description", "")
    parameters = mcp_tool_desc.get("inputSchema", {"type": "object", "properties": {}})

    async def execute(tool_call_id, args, signal, on_update):
        # MCP client is closed after load_mcp_tools returns. Per-call
        # reconnect lives in the concrete adapter (filled at impl time).
        return AgentToolResult(
            content=[TextContent(
                type="text",
                text=f"MCP tool {name!r} execute() not yet wired (per-call reconnect).",
            )]
        )

    def prepare_arguments(args):
        return args

    return _AgentToolShim(
        name=name,
        label=name,
        description=description,
        parameters=parameters,
        execution_mode=None,
        prepare_arguments=prepare_arguments,
        execute=execute,
    )


class _AgentToolShim:
    """Concrete AgentTool — fills the Protocol structurally."""

    def __init__(self, name, label, description, parameters, execution_mode,
                 prepare_arguments, execute):
        self.name = name
        self.label = label
        self.description = description
        self.parameters = parameters
        self.execution_mode = execution_mode
        self._prepare_arguments = prepare_arguments
        self._execute = execute

    def prepare_arguments(self, args):
        return self._prepare_arguments(args)

    async def execute(self, tool_call_id, args, signal, on_update):
        return await self._execute(tool_call_id, args, signal, on_update)


async def load_mcp_tools(server_script: str) -> list[AgentTool]:
    try:
        client = await _connect(server_script)
    except Exception as exc:
        print(f"[load_mcp_tools] connect failed: {exc}", file=sys.stderr)
        return []

    try:
        descriptors = await client.list_tools()
    except Exception as exc:
        print(f"[load_mcp_tools] list_tools failed: {exc}", file=sys.stderr)
        return []

    return [_wrap(d) for d in descriptors]
