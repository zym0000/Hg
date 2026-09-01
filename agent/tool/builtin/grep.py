from __future__ import annotations

import fnmatch
import os
import re
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.tool.builtin._path_utils import path_exists, resolve_to_cwd
from agent.truncate import GREP_MAX_LINE_LENGTH, truncate_line


_GREP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Pattern to search for (regex or substring)",
        },
        "path": {
            "type": "string",
            "description": "Directory to search (default: working directory)",
        },
        "include_pattern": {
            "type": "string",
            "description": "Only search files matching this glob (e.g. '*.py')",
        },
        "case_sensitive": {
            "type": "boolean",
            "description": "If true, match case-sensitively (default: false)",
        },
    },
    "required": ["pattern"],
}

def _grep_files(
    root: str,
    pattern: re.Pattern,
    include_pattern: str | None,
    limit: int = 500,
) -> list[str]:
    matches: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            if include_pattern and not fnmatch.fnmatch(fname, include_pattern):
                continue
            full = os.path.join(dirpath, fname)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, start=1):
                        if pattern.search(line):
                            truncated_line, _ = truncate_line(
                                line.rstrip("\n"),
                                max_chars=GREP_MAX_LINE_LENGTH,
                            )
                            rel = os.path.relpath(full, root)
                            matches.append(f"{rel}:{line_no}:{truncated_line}")
                            if len(matches) >= limit:
                                return matches
            except OSError:
                continue
    return matches


class _GrepTool:
    def __init__(self, cwd: str) -> None:
        self.name = "grep"
        self.label = "Grep"
        self.description = (
            "Search file contents for a regex/pattern. Case-insensitive by "
            "default. Optionally filter by file glob. Returns "
            "'path:line:matched-line' format."
        )
        self.parameters = _GREP_SCHEMA
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
            pattern_str = args["pattern"]
            case_sensitive = bool(args.get("case_sensitive", False))
            include_pattern = args.get("include_pattern")
            target = args.get("path") or "."
            abs_root = resolve_to_cwd(target, self._cwd)
            if not path_exists(abs_root):
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: directory not found: {abs_root}",
                    )],
                    details={"isError": True},
                )
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(pattern_str, flags)
            except re.error as exc:
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: invalid regex: {exc}",
                    )],
                    details={"isError": True},
                )
            matches = _grep_files(abs_root, pattern, include_pattern)
            text = "\n".join(matches) if matches else "(no matches)"
            return AgentToolResult(content=[TextContent(type="text", text=text)])
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error grepping: {exc}",
                )],
                details={"isError": True},
            )


def create_grep_tool(cwd: str, options: Any = None) -> AgentTool:
    return _GrepTool(cwd)