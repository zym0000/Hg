import sys

from rich.box import ROUNDED
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from agent.message import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

from interaction.render.theme import DEFAULT_THEME, bg_style, style
from interaction.render.tool_renderers import (
    _truncate_lines,
    get_renderer,
)


_CALL_LINE_MAX_CHARS = 120
_RESULT_BODY_MAX_LINES = 15

_TOOL_DISPLAY_NAME: dict[str, str] = {
    "shell_exec": "Bash",
    "bash": "Bash",
    "apply_patch": "Edit",
    "edit": "Edit",
    "write_file": "Write",
    "write": "Write",
}

_HIDDEN_TOOLS: set[str] = {
    "read_file", "read",
    "list_dir", "ls",
    "grep",
    "find",
    "glob",
}

_TOOL_CALL_ARG: dict[str, callable] = {}  # populated below


def _format_call_line(name: str, args) -> str:
    display = _TOOL_DISPLAY_NAME.get(name, name)
    if not isinstance(args, dict):
        return f"{display}()"
    extractor = _TOOL_CALL_ARG.get(name)
    if extractor is not None:
        arg_str = extractor(args)
    elif args:
        arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    else:
        arg_str = ""
    if arg_str:
        return f"{display}({arg_str})"
    return f"{display}()"


def _bash_call_arg(args: dict) -> str:
    return str(args.get("command", "") or "")


def _file_call_arg(args: dict) -> str:
    return str(args.get("path") or args.get("file_path") or "?")


_TOOL_CALL_ARG["bash"] = _bash_call_arg
_TOOL_CALL_ARG["shell_exec"] = _bash_call_arg
_TOOL_CALL_ARG["edit"] = _file_call_arg
_TOOL_CALL_ARG["apply_patch"] = _file_call_arg
_TOOL_CALL_ARG["write"] = _file_call_arg
_TOOL_CALL_ARG["write_file"] = _file_call_arg


_turn_has_visible_activity: bool = False

_owned_by_us = False
_console: Console | None = None

_assistant_in_progress_id: int | None = None

_assistant_has_delta: bool = False


def _get_console() -> Console:
    """Return the shared Console, lazy-creating and rebinding to current stdout."""
    global _console, _owned_by_us
    if _console is None:
        _console = Console(soft_wrap=False, file=sys.stdout)
        _owned_by_us = True
    elif _owned_by_us and _console.file is not sys.stdout:
        # sys.stdout has been swapped (e.g., pytest's capsys for a new test).
        # Rebind the Console's file so writes land in the captured stream.
        _console.file = sys.stdout
    return _console

_footer: "StatusFooter | None" = None

def get_footer():
    """Return the shared StatusFooter, lazy-constructing on first call."""
    global _footer
    if _footer is None:
        from interaction.render.footer import StatusFooter
        _footer = StatusFooter()
    return _footer


def set_footer(footer) -> None:
    """Replace the shared footer (used by tests / `cli` startup)."""
    global _footer
    _footer = footer


def print_footer() -> None:
    if _footer is None:
        return
    _get_console().print(_footer.render())


def _content_text(msg) -> str:
    parts = []
    for c in msg.content:
        if isinstance(c, TextContent):
            parts.append(c.text)
        elif isinstance(c, ToolCallContent):
            parts.append(f"[tool_call:{c.name}({c.arguments})]")
    return "".join(parts)


def _render_user(msg: UserMessage) -> None:
    from rich.style import Style
    text_content = _content_text(msg) or "(empty)"
    padded = f" {text_content} "
    body = Text(
        padded,
        style=Style(
            color=DEFAULT_THEME.user_message_text,
            bgcolor=DEFAULT_THEME.user_message_bg,
        ),
    )
    _get_console().print(body)


def _render_assistant(msg: AssistantMessage) -> None:
    blocks = list(msg.content)
    con = _get_console()
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if isinstance(block, TextContent) and block.text.strip():
            con.print(Markdown(block.text.strip()))
            has_visible_after = any(
                (isinstance(b, TextContent) and (b.text or "").strip())
                or (isinstance(b, ThinkingContent) and (b.text or "").strip())
                for b in blocks[i + 1:]
            )
            if has_visible_after:
                con.print()
        elif isinstance(block, ThinkingContent):
            run: list[str] = []
            j = i
            while j < len(blocks) and isinstance(blocks[j], ThinkingContent):
                trimmed = (blocks[j].text or "").strip()
                if trimmed:
                    run.append(trimmed)
                j += 1
            if run:
                con.print()  # spacer before thinking if visible content preceded
                con.print(
                    Text(
                        "\n\n".join(run),
                        style=style(DEFAULT_THEME.thinking_text, italic=True),
                    )
                )
                has_visible_after = any(
                    (isinstance(b, TextContent) and (b.text or "").strip())
                    or (isinstance(b, ThinkingContent) and (b.text or "").strip())
                    for b in blocks[j:]
                )
                if has_visible_after:
                    con.print()
            i = j - 1
        i += 1

    has_tool_calls = any(isinstance(b, ToolCallContent) for b in blocks)
    if getattr(msg, "stop_reason", None) == "length":
        con.print()
        con.print(
            Text(
                "Response was truncated before completion.",
                style=style(DEFAULT_THEME.error),
            )
        )
    elif not has_tool_calls:
        stop = getattr(msg, "stop_reason", None)
        if stop == "aborted":
            err = getattr(msg, "error_message", None) or "Operation aborted"
            con.print()
            con.print(Text(err, style=style(DEFAULT_THEME.error)))
        elif stop == "error":
            err = getattr(msg, "error_message", None) or "Unknown error"
            con.print()
            con.print(Text(f"Error: {err}", style=style(DEFAULT_THEME.error)))


def _render_tool_pending(name: str, args: dict) -> None:
    if name in _HIDDEN_TOOLS:
        return
    line = _format_call_line(name, args)
    if len(line) > _CALL_LINE_MAX_CHARS:
        line = line[:_CALL_LINE_MAX_CHARS] + "…)"
    con = _get_console()
    con.print(Text(line, style=style(DEFAULT_THEME.tool_title, bold=True)))
    global _turn_has_visible_activity
    _turn_has_visible_activity = True


def _render_tool_end(name: str, result: str, args: dict, is_error: bool) -> None:
    if name in _HIDDEN_TOOLS:
        return
    if not result:
        return
    truncated, _ = _truncate_lines(result, _RESULT_BODY_MAX_LINES)
    body_style = (
        style(DEFAULT_THEME.error) if is_error else style(DEFAULT_THEME.tool_output)
    )
    lines = truncated.splitlines()
    if not lines:
        return
    con = _get_console()
    # First result line: 2-space indent + ⎿ + 2 spaces + content.
    con.print(Text(f"  ⎿  {lines[0]}", style=body_style))
    # Continuation lines align under the first result character.
    indent = " " * 5
    for line in lines[1:]:
        con.print(Text(indent + line, style=body_style))
    global _turn_has_visible_activity
    _turn_has_visible_activity = True


def render_pi_event(event) -> None:
    """Print a agent event to stdout using rich styling."""
    global _assistant_in_progress_id, _assistant_has_delta, _turn_has_visible_activity
    t = event.get("type")
    # Always pull whatever token/model info is in the event into the footer
    # BEFORE the per-type body runs. The footer is a passive observer — it
    # never affects what gets printed for the body of the event.
    if _footer is not None and isinstance(event, dict):
        _footer.update_from_event(event)
    if t == "message_start":
        msg = event.get("message")
        if isinstance(msg, AssistantMessage):
            _assistant_in_progress_id = id(msg)
            _assistant_has_delta = False
        elif isinstance(msg, UserMessage):
            _render_user(msg)
    elif t == "message_update":
        delta = event.get("delta")
        if isinstance(delta, str):
            if _assistant_in_progress_id is not None:
                _assistant_has_delta = True
            if delta:
                _get_console().print(delta, end="", highlight=False)
                _turn_has_visible_activity = True
    elif t == "message_end":
        msg = event.get("message")
        con = _get_console()
        if isinstance(msg, AssistantMessage):
            text = _content_text(msg)
            err = getattr(msg, "error_message", None)
            is_streamed = (
                _assistant_in_progress_id == id(msg)
                and _assistant_has_delta
            )
            _assistant_in_progress_id = None
            _assistant_has_delta = False

            if err:
                con.print()
                con.print(
                    Text(
                        f"assistant › [error] {err}",
                        style=style(DEFAULT_THEME.error),
                    )
                )
                _turn_has_visible_activity = True
            elif is_streamed:
                con.print()
            elif text:
                con.print()
                _render_assistant(msg)
                _turn_has_visible_activity = True
            elif getattr(msg, "stop_reason", None) == "error":
                con.print()
                con.print(
                    Text(
                        "assistant › [error, no message]",
                        style=style(DEFAULT_THEME.error),
                    )
                )
                _turn_has_visible_activity = True
            else:
                con.print()
                con.print(
                    Text(
                        "(assistant › <empty content>)",
                        style=style(DEFAULT_THEME.muted),
                    )
                )
                _turn_has_visible_activity = True
        elif isinstance(msg, ToolResultMessage):
            pass
        # UserMessage is rendered once on message_start — skip on message_end.
    elif t == "tool_execution_start":
        name = event.get("toolName") or event.get("tool_name") or "?"
        args = event.get("args") or event.get("toolArgs") or {}
        _render_tool_pending(name, args)
    elif t == "tool_execution_end":
        is_err = event.get("isError", False)
        result = event.get("result", "")
        name = (
            event.get("toolName")
            or event.get("tool_name")
            or ""
        )
        args = event.get("args") or event.get("toolArgs") or {}
        _render_tool_end(name, result, args, is_err)
    elif t == "turn_end":
        _turn_has_visible_activity = False
    elif t == "agent_end":
        outcome = event.get("outcome", "completed")
        con = _get_console()
        con.print()
        con.print(
            Text(
                f"── agent idle ({outcome}) ──",
                style=style(DEFAULT_THEME.divider),
            )
        )
        print_footer()
