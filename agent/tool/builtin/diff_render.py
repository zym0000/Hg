from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"

_BG_RED = "\x1b[48;5;52m"     # dark red bg
_BG_GREEN = "\x1b[48;5;22m"   # dark green bg
_BG_DIM = "\x1b[48;5;236m"    # dim gray bg
_FG_WHITE = "\x1b[38;5;255m"


def _supports_color() -> bool:
    """Detect whether ANSI escapes will render correctly.

 - Force off when ``NO_COLOR`` env var is set (https://no-color.org/).
 - Force off when stdout is not a TTY (piping to a file).
 - Force on for interactive terminals.
 """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


_ENABLE_COLOR: bool | None = None  # lazy


def enable_color(on: bool) -> None:
    """Override color output (e.g. for tests / piped logs)."""
    global _ENABLE_COLOR
    _ENABLE_COLOR = on


def _color_enabled() -> bool:
    """Decide whether to emit ANSI escapes.

 Priority (highest first):
    1. ``NO_COLOR`` env var — disables regardless of all other settings
       (https://no-color.org/). User intent beats programmatic enable.
    2. ``enable_color(...)`` override (used by tests / explicit CLI flag).
    3. TTY auto-detection.
 """
    if os.environ.get("NO_COLOR"):
        return False
    if _ENABLE_COLOR is None:
        return _supports_color()
    return _ENABLE_COLOR


def _wrap(text: str, prefix: str) -> str:
    """Wrap ``text`` with ``prefix`` (an ANSI code) and reset at end."""
    if not _color_enabled():
        return text
    return f"{prefix}{text}{_RESET}"

def compute_unified_diff(
    old_text: str,
    new_text: str,
    abs_path: Path,
    n_context: int = 3,
) -> list[tuple[str, str]]:
    """Return a sequence of (kind, line) tuples describing the diff.

 ``kind`` is one of:
 - ``"file_old"`` / ``"file_new"``: ``--- a/...`` / ``+++ b/...`` headers
 - ``"hunk"``:                       ``@@ -X,Y +A,B @@`` header
 - ``"context"``:                     unchanged line
 - ``"del"``:                         removed line
 - ``"add"``:                         added line

 Lines preserve the *content* only (no leading +/-/space prefix); the
 caller decides how to render them. This split lets the renderer color
 background colors without re-parsing the prefix character.
 """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    raw = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{abs_path}",
        tofile=f"b/{abs_path}",
        fromfiledate="",
        tofiledate="",
        n=n_context,
        lineterm="",
    ))

    out: list[tuple[str, str]] = []
    for line in raw:
        if line.startswith("---"):
            out.append(("file_old", line))
        elif line.startswith("+++"):
            out.append(("file_new", line))
        elif line.startswith("@@"):
            out.append(("hunk", line))
        elif line.startswith("+"):
            out.append(("add", line[1:]))
        elif line.startswith("-"):
            out.append(("del", line[1:]))
        elif line.startswith(" "):
            out.append(("context", line[1:]))
        else:
            # "\ No newline at end of file" markers etc.
            out.append(("meta", line))
    return out


def _find_change_start_1based(text: str, old: str, new: str) -> tuple[int, int]:
    offset = text.find(old)
    old_start = text.count("\n", 0, offset) + 1
    new_text = text.replace(old, new, 1)
    new_offset = new_text.find(new)
    new_start = new_text.count("\n", 0, new_offset) + 1
    return old_start, new_start

def render_unified_diff(
    abs_path: Path,
    old_text: str,
    new_text: str,
    *,
    n_context: int = 3,
) -> str:
    entries = compute_unified_diff(old_text, new_text, abs_path, n_context=n_context)

    out: list[str] = []
    old_line = 0
    new_line = 0
    # Seed cursors from the @@ hunk header.
    for kind, content in entries:
        if kind == "file_old":
            out.append(_wrap(content, _BOLD + _DIM))
            continue
        if kind == "file_new":
            out.append(_wrap(content, _BOLD + _DIM))
            continue
        if kind == "hunk":
            out.append(_wrap(content, _BOLD))
            try:
                minus = content.split(" -")[1].split(" ")[0]
                plus = content.split(" +")[1].split(" ")[0]
                old_line = int(minus.split(",")[0])
                new_line = int(plus.split(",")[0])
            except (IndexError, ValueError):
                pass
            continue

        if kind == "context":
            out.append(_render_line_dual(old_line, new_line, "  ", content, _BG_DIM))
            old_line += 1
            new_line += 1
        elif kind == "add":
            out.append(_render_line_dual(old_line, new_line, "+ ", content, _BG_GREEN))
            new_line += 1
        elif kind == "del":
            out.append(_render_line_dual(old_line, new_line, "- ", content, _BG_RED))
            old_line += 1
        elif kind == "meta":
            out.append(content)

    return "\n".join(out)


def _render_line(line_no: int, sign: str, content: str, bg: str) -> str:
    """Format one diff hunk line: ``<line_no>  <sign><content>`` with bg color."""
    text = f"{line_no:>4}  {sign}{content}"
    if not _color_enabled():
        return text
    # Use bright foreground on colored backgrounds for contrast.
    return f"{bg}{_FG_WHITE}{text}{_RESET}"


def _render_line_dual(
    old_no: int, new_no: int, sign: str, content: str, bg: str,
) -> str:
    old_disp = str(old_no) if old_no > 0 else "-"
    new_disp = str(new_no) if new_no > 0 else "-"
    text = f"{old_disp:>4}  {new_disp:>4}  {sign}{content}"
    if not _color_enabled():
        return text
    return f"{bg}{_FG_WHITE}{text}{_RESET}"

def render_edit_diff(
    abs_path: Path,
    old_text: str,
    new_text: str,
    old_block: str,
    new_block: str,
) -> str:
    entries = compute_unified_diff(old_text, new_text, abs_path, n_context=3)
    return _render_entries(entries)

def render_write_diff(abs_path: Path, new_text: str) -> str:
    old_text = ""
    if abs_path.exists():
        try:
            old_text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old_text = ""
    entries = compute_unified_diff(old_text, new_text, abs_path, n_context=3)
    return _render_entries(entries, 1)

def _render_entries(entries: list[tuple[str, str]], start_old: int = 1, start_new: int = 1) -> str:
    out: list[str] = []
    old_line = start_old
    new_line = start_new
    for kind, content in entries:
        if kind == "file_old":
            out.append(_wrap(content, _BOLD + _DIM))
            continue
        if kind == "file_new":
            out.append(_wrap(content, _BOLD + _DIM))
            continue
        if kind == "hunk":
            out.append(_wrap(content, _BOLD))
            try:
                minus = content.split(" -")[1].split(" ")[0]
                plus = content.split(" +")[1].split(" ")[0]
                old_line = int(minus.split(",")[0])
                new_line = int(plus.split(",")[0])
            except (IndexError, ValueError):
                pass
            continue
        if kind == "context":
            out.append(_render_line_dual(old_line, new_line, "  ", content, _BG_DIM))
            old_line += 1
            new_line += 1
        elif kind == "add":
            out.append(_render_line_dual(old_line, new_line, "+ ", content, _BG_GREEN))
            new_line += 1
        elif kind == "del":
            out.append(_render_line_dual(old_line, new_line, "- ", content, _BG_RED))
            old_line += 1
        elif kind == "meta":
            out.append(content)
    return "\n".join(out)