from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.tool.builtin._path_utils import resolve_to_cwd
from agent.tool.builtin.diff_render import render_write_diff


_WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to write (relative or absolute)",
        },
        "content": {
            "type": "string",
            "description": "Content to write to the file",
        },
    },
    "required": ["path", "content"],
}


class _WriteTool:
    def __init__(self, cwd: str) -> None:
        self.name = "write"
        self.label = "Write"
        self.description = (
            "Write content to a file. Creates parent directories if needed. "
            "Overwrites existing files. Returns a unified diff showing the change."
        )
        self.parameters = _WRITE_SCHEMA
        self.execution_mode = "sequential"
        self._cwd = cwd

    def prepare_arguments(self, args: Any) -> Any:
        return args

    async def execute(
        self,
        tool_call_id: str,
        args: Any,
        signal: Any,
        on_update: Callable[[AgentToolResult], None] | None,
    ) -> AgentToolResult:
        try:
            abs_path_str = resolve_to_cwd(args["path"], self._cwd)
            abs_path = Path(abs_path_str)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            new_content = args["content"]

            diff_text = render_write_diff(abs_path, new_content)
            abs_path.write_text(new_content, encoding="utf-8")
            return AgentToolResult(
                content=[TextContent(type="text", text=diff_text)],
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error writing {args.get('path')}: {exc}",
                )],
                details={"isError": True},
            )

def create_write_tool(cwd: str, options: Any = None) -> AgentTool:
    return _WriteTool(cwd)