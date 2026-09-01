"""agent.tool.builtin.edit — Surgical file edit.

Requires the ``old_text`` to match exactly once in the file; replaces
with ``new_text``. Ambiguous (multiple matches) or not-found are
returned as errors so the model can refine its edit.

On success, returns a unified-diff render of the change (matching the
``skills/coding/SKILL.md`` patch format) so the model can confirm the
edit landed as intended without re-reading the file. The diff uses
ANSI background colors for additions / deletions when stdout is a TTY.

Errors are encoded in ``details={"isError": True}`` ( shape);
the executor computes ``is_error`` on the wrapping ``ToolResultMessage``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.tool.builtin._path_utils import path_exists, resolve_to_cwd
from agent.tool.builtin.diff_render import render_edit_diff


_EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the file to edit (relative or absolute)",
        },
        "old_text": {
            "type": "string",
            "description": "Exact text to find (must match exactly once)",
        },
        "new_text": {
            "type": "string",
            "description": "Replacement text",
        },
    },
    "required": ["path", "old_text", "new_text"],
}


class _EditTool:
    def __init__(self, cwd: str) -> None:
        self.name = "edit"
        self.label = "Edit"
        self.description = (
            "Edit a file by replacing an exact unique substring with new text. "
            "Returns an error if old_text is not found or matches multiple times."
        )
        self.parameters = _EDIT_SCHEMA
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
                    content=[TextContent(
                        type="text",
                        text=f"Error: file not found: {abs_path}",
                    )],
                    details={"isError": True},
                )
            text = Path(abs_path).read_text(encoding="utf-8")
            old = args["old_text"]
            new = args["new_text"]
            count = text.count(old)
            if count == 0:
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: old_text not found in {abs_path}",
                    )],
                    details={"isError": True},
                )
            if count > 1:
                return AgentToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: old_text matches {count} times in {abs_path}; must be unique",
                    )],
                    details={"isError": True},
                )
            new_text = text.replace(old, new, 1)
            Path(abs_path).write_text(new_text, encoding="utf-8")

            diff_text = render_edit_diff(abs_path, text, new_text, old, new)
            return AgentToolResult(
                content=[TextContent(type="text", text=diff_text)],
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Error editing {args.get('path')}: {exc}",
                )],
                details={"isError": True},
            )


def create_edit_tool(cwd: str, options: Any = None) -> AgentTool:
    return _EditTool(cwd)
