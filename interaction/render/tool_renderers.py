from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

@dataclass
class RenderBlock:
    title: str
    body: str
    style_hint: str = "default"  # "default" | "error"

@runtime_checkable
class ToolRenderer(Protocol):
    name: str

    def render_call(self, args: dict) -> RenderBlock: ...

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock: ...

def _count_lines(text: str) -> int:
    if not text:
        return 0
    # splitlines() with keepends=False already counts the trailing line.
    return len(text.splitlines())

def _truncate_lines(text: str, max_lines: int) -> tuple[str, int]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    remaining = len(lines) - max_lines
    head = lines[:max_lines]
    marker = f"... ({remaining} more lines)"
    return ("\n".join(head) + "\n" + marker), remaining

def _patch_stats(patch: str) -> tuple[int, int]:
    adds = 0
    dels = 0
    for line in patch.splitlines():
        # Ignore unified-diff file headers
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            adds += 1
        elif line.startswith("-"):
            dels += 1
    return adds, dels

def _truncate_command(cmd: str, limit: int) -> str:
    if len(cmd) <= limit:
        return cmd
    return cmd[:limit] + "..."

class DefaultRenderer:
    name: str = "default"
    _display_name: str = "default"

    def render_call(self, args: dict) -> RenderBlock:
        return RenderBlock(
            title=self._display_name,
            body=str(args),
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        return RenderBlock(
            title=self._display_name,
            body=result,
            style_hint="error" if is_error else "default",
        )

class ReadFileRenderer:
    name = "read_file"

    def render_call(self, args: dict) -> RenderBlock:
        file_path = args.get("file_path", "?")
        start = args.get("start_line", 1)
        end = args.get("end_line", -1)
        end_label = str(end) if end and end > 0 else "end"
        return RenderBlock(
            title=f"read {file_path}",
            body=f"lines {start}..{end_label}",
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        file_path = args.get("file_path", "?")
        line_count = _count_lines(result)
        body, _ = _truncate_lines(result, max_lines=20)
        return RenderBlock(
            title=f"read {file_path} → {line_count} lines",
            body=body,
            style_hint="error" if is_error else "default",
        )


class WriteFileRenderer:
    name = "write_file"

    def render_call(self, args: dict) -> RenderBlock:
        file_path = args.get("file_path", "?")
        content = args.get("content", "") or ""
        chars = len(content)
        lines = _count_lines(content)
        return RenderBlock(
            title=f"write {file_path}",
            body=f"{chars} chars, {lines} lines",
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        file_path = args.get("file_path", "?")
        return RenderBlock(
            title=f"wrote {file_path}",
            body=result,
            style_hint="error" if is_error else "default",
        )

class ApplyPatchRenderer:
    name = "apply_patch"

    def render_call(self, args: dict) -> RenderBlock:
        file_path = args.get("file_path", "?")
        patch = args.get("patch", "") or ""
        adds, dels = _patch_stats(patch)
        return RenderBlock(
            title=f"patch {file_path}",
            body=f"+{adds} -{dels}",
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        file_path = args.get("file_path", "?")
        return RenderBlock(
            title=f"patched {file_path}",
            body=result,
            style_hint="error" if is_error else "default",
        )

class ListDirRenderer:
    name = "list_dir"

    def render_call(self, args: dict) -> RenderBlock:
        dir_path = args.get("dir_path", ".")
        recursive = args.get("recursive", False)
        pattern = args.get("pattern", "*")
        return RenderBlock(
            title=f"ls {dir_path}",
            body=f"recursive={recursive} pattern={pattern}",
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        dir_path = args.get("dir_path", ".")
        # The tool result header reads "[Directory: X] (N entries)". Extract
        # the entry count if present so the title can report it.
        entry_count = 0
        m = re.search(r"\((\d+) entries?\)", result)
        if m:
            entry_count = int(m.group(1))
        return RenderBlock(
            title=f"ls {dir_path} → {entry_count} entries",
            body=result,
            style_hint="error" if is_error else "default",
        )

class GrepRenderer:
    name = "grep"

    def render_call(self, args: dict) -> RenderBlock:
        pattern = args.get("pattern", "")
        path = args.get("path", ".")
        include = args.get("include", "*")
        case_sensitive = args.get("case_sensitive", True)
        max_results = args.get("max_results", 50)
        return RenderBlock(
            title=f"grep {pattern!r} in {path}",
            body=(
                f"include={include} "
                f"case={case_sensitive} "
                f"max={max_results}"
            ),
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        pattern = args.get("pattern", "")
        # Tool header reads "[Grep: 'X' in P] (N matches)". Extract match
        # count if present.
        match_count = 0
        m = re.search(r"\((\d+) match(?:es)?\)", result)
        if m:
            match_count = int(m.group(1))
        body, _ = _truncate_lines(result, max_lines=15)
        return RenderBlock(
            title=f"grep {pattern!r} → {match_count} matches",
            body=body,
            style_hint="error" if is_error else "default",
        )

class ShellExecRenderer:
    """Renderer for `shell_exec(command, timeout, cwd)`."""

    name = "shell_exec"

    def render_call(self, args: dict) -> RenderBlock:
        command = args.get("command", "")
        timeout = args.get("timeout", 30)
        cwd = args.get("cwd", ".")
        title_cmd = _truncate_command(command, 60)
        return RenderBlock(
            title=f"$ {title_cmd}",
            body=f"cwd={cwd} timeout={timeout}s",
        )

    def render_result(
        self, result: str, args: dict, is_error: bool
    ) -> RenderBlock:
        command = args.get("command", "")
        title_cmd = _truncate_command(command, 40)
        # Tool output ends with "[EXIT CODE: N]". Extract it.
        exit_code = "?"
        m = re.search(r"\[EXIT CODE:\s*(-?\d+)\]", result)
        if m:
            exit_code = m.group(1)
        return RenderBlock(
            title=f"$ {title_cmd} → exit {exit_code}",
            body=result,
            style_hint="error" if is_error else "default",
        )


DEFAULT_RENDERERS: dict[str, ToolRenderer] = {
    "read_file": ReadFileRenderer(),
    "write_file": WriteFileRenderer(),
    "apply_patch": ApplyPatchRenderer(),
    "list_dir": ListDirRenderer(),
    "grep": GrepRenderer(),
    "shell_exec": ShellExecRenderer(),
}

_default = DefaultRenderer()

def get_renderer(name: str) -> ToolRenderer:
    """Return the registered renderer for `name`, or the default fallback.

 For unknown tool names, returns a `DefaultRenderer` whose class
 identity (`name == "default"`) is preserved but whose displayed title
 is the requested tool name.
"""
    if name in DEFAULT_RENDERERS:
        return DEFAULT_RENDERERS[name]

    fb = DefaultRenderer()
    # `name` identifies the renderer class (always "default" for the
    # fallback). `_display_name` overrides the title shown to the user.
    fb._display_name = name
    return fb


__all__ = [
    "RenderBlock",
    "ToolRenderer",
    "DefaultRenderer",
    "ReadFileRenderer",
    "WriteFileRenderer",
    "ApplyPatchRenderer",
    "ListDirRenderer",
    "GrepRenderer",
    "ShellExecRenderer",
    "DEFAULT_RENDERERS",
    "get_renderer",
]
