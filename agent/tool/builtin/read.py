from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.tool.builtin._path_utils import path_exists, resolve_to_cwd
from agent.truncate import truncate_head


_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to read (relative or absolute)",
        },
        "offset": {
            "type": "integer",
            "description": "Line number to start reading from (1-indexed)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of lines to read",
        },
    },
    "required": ["path"],
}


class _ReadTool:
    def __init__(self, cwd: str) -> None:
        self.name = "read"
        self.label = "Read"
        self.description = (
            "Read the contents of a file. Returns text with line numbers. "
            "Use offset and limit to read a slice. Long files are truncated."
        )
        self.parameters = _READ_SCHEMA
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
            abs_path = resolve_to_cwd(args["path"], self._cwd)
            if not path_exists(abs_path):
                return AgentToolResult(
                    content=[TextContent(type="text", text=f"Error: file not found: {abs_path}")],
                    details={"isError": True},
                )
            text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            offset = args.get("offset")
            limit = args.get("limit")

            if offset is not None or limit is not None:
                lines = text.split("\n")
                start = max(0, (offset or 1) - 1)
                end = start + limit if limit else len(lines)
                sliced = lines[start:end]
                numbered = "\n".join(
                    f"{i + start + 1:6}\t{line}" for i, line in enumerate(sliced)
                )
                return AgentToolResult(content=[TextContent(type="text", text=numbered)])

            truncated = truncate_head(text)
            details: dict[str, Any] = {
            #如果读取文件发生截断，那么记录截断信息，放入Tool工具返回具返回信息里put_bytes,
                    "last_line_partial": truncated.last_line_partial,
                    "first_line_exceeds_limit": truncated.first_line_exceeds_limit,
                    "max_lines": truncated.max_lines,
                    "max_bytes": truncated.max_bytes,
                }
            return AgentToolResult(
                content=[TextContent(type="text", text=truncated.content)],
                details=details or None,
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"Error reading {args.get('path')}: {exc}")],
                details={"isError": True},
            )


def create_read_tool(cwd: str, options: Any = None) -> AgentTool:
    return _ReadTool(cwd)