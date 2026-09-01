from __future__ import annotations

import os
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.tool.builtin._path_utils import path_exists, resolve_to_cwd


_LS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory to list (default: working directory)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of entries to return (default: 500)",
        },
    },
}

_DEFAULT_LIMIT = 500

class _LsTool:
    def __init__(self, cwd: str) -> None:
        self.name = "ls"
        self.label = "Ls"
        self.description = (
            "List a directory's contents. Directories end with '/'. "
            "Sorted alphabetically."
        )
        self.parameters = _LS_SCHEMA
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
            target = args.get("path") or "."
            abs_path = resolve_to_cwd(target, self._cwd)
            if not path_exists(abs_path):
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: directory not found: {abs_path}",
                    )],
                    details={"isError": True},
                )
            if not os.path.isdir(abs_path):
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: not a directory: {abs_path}",
                    )],
                    details={"isError": True},
                )
            limit = args.get("limit", _DEFAULT_LIMIT)
            entries = sorted(os.listdir(abs_path))
            truncated_entries = entries[:limit]
            lines = []
            for name in truncated_entries:
                full = os.path.join(abs_path, name)
                if os.path.isdir(full):
                    lines.append(f"{name}/")
                else:
                    lines.append(name)
            text = "\n".join(lines) if lines else "(empty directory)"
            details: dict[str, Any] = {}
            if len(entries) > limit:
                details["entryLimitReached"] = limit
                details["totalEntries"] = len(entries)
            return AgentToolResult(
                content=[TextContent(type="text", text=text)],
                details=details or None,
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error listing {args.get('path')}: {exc}",
                )],
                details={"isError": True},
            )


def create_ls_tool(cwd: str, options: Any = None) -> AgentTool:
    return _LsTool(cwd)
