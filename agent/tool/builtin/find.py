"""agent.tool.builtin.find — Find files by glob pattern.

Uses ``fnmatch`` for pattern matching against basenames. Recurses
into subdirectories. Truncates to ``limit`` (default 500) and reports
truncation in details.
Errors are encoded in ``details={"isError": True}`` ( shape);
the executor computes ``is_error`` on the wrapping ``ToolResultMessage``.
"""
from __future__ import annotations

import fnmatch
import os
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.tool.builtin._path_utils import path_exists, resolve_to_cwd


_FIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob pattern to match (e.g. '*.py', 'test_*')",
        },
        "path": {
            "type": "string",
            "description": "Directory to search (default: working directory)",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of matches (default: 500)",
        },
    },
    "required": ["pattern"],
}


_DEFAULT_LIMIT = 500


def _walk_matches(root: str, pattern: str, limit: int) -> list[str]:
    matches: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if fnmatch.fnmatch(f, pattern):
                matches.append(os.path.relpath(os.path.join(dirpath, f), root))
                if len(matches) >= limit:
                    return matches
    return matches


class _FindTool:
    def __init__(self, cwd: str) -> None:
        self.name = "find"
        self.label = "Find"
        self.description = (
            "Find files by glob pattern under a directory. Recurses into "
            "subdirectories. Returns paths relative to the search root."
        )
        self.parameters = _FIND_SCHEMA
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
            pattern = args["pattern"]
            target = args.get("path") or "."
            limit = args.get("limit", _DEFAULT_LIMIT)
            abs_root = resolve_to_cwd(target, self._cwd)
            if not path_exists(abs_root):
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: directory not found: {abs_root}",
                    )],
                    details={"isError": True},
                )
            matches = _walk_matches(abs_root, pattern, limit)
            text = "\n".join(matches) if matches else "(no matches)"
            details: dict[str, Any] = {}
            # If we hit the limit, walk again to confirm there are more results.
            if len(matches) >= limit:
                total = len(_walk_matches(abs_root, pattern, limit + 1))
                if total > limit:
                    details["entryLimitReached"] = limit
                    details["totalEntries"] = total
            return AgentToolResult(
                content=[TextContent(type="text", text=text)],
                details=details or None,
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error finding files: {exc}",
                )],
                details={"isError": True},
            )


def create_find_tool(cwd: str, options: Any = None) -> AgentTool:
    return _FindTool(cwd)
