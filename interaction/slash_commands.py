import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from interaction.cli import InteractiveCLI

Handler = Callable[["InteractiveCLI", str], Awaitable[None]]

_PREVIEW_LIMIT = 80


def _msg_preview(msg: Any) -> str:
    summary = getattr(msg, "summary", None)
    if isinstance(summary, str):
        text = summary
    else:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            text = content
        elif content:
            parts: list[str] = []
            for c in content:
                ctext = getattr(c, "text", None)
                if ctext:
                    parts.append(ctext)
                elif getattr(c, "name", None):
                    parts.append(f"[tool_call:{c.name}]")
            text = "".join(parts).strip()
        else:
            text = ""
    if not text:
        return "(empty)"
    if len(text) > _PREVIEW_LIMIT:
        return text[: _PREVIEW_LIMIT - 3] + "..."
    return text


async def _print_context(cli: "InteractiveCLI") -> None:
    try:
        messages = cli.agent_session.agent.state.messages
    except Exception as e:
        print(f"(context error: {e})")
        return
    if not messages:
        print("(context: empty)")
        return
    print(f"── Context ({len(messages)} messages) ──")
    for i, msg in enumerate(messages, 1):
        role = getattr(msg, "role", "?")
        print(f"  [{i}] {role}: {_msg_preview(msg)}")
    print("─" * 40)


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str
    argument_hint: Optional[str] = None
    handler: Optional[Handler] = None  # None = stub / no-op


# Module-level registry. Populated by `register(...)` calls below.
COMMANDS: dict[str, SlashCommand] = {}


def register(cmd: SlashCommand) -> None:
    """Insert (or replace) a command in the global registry."""
    COMMANDS[cmd.name] = cmd


def get(name: str) -> Optional[SlashCommand]:
    return COMMANDS.get(name)


def all_commands() -> list[SlashCommand]:
    return list(COMMANDS.values())


_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_BLUE = "\033[34m"
_ORANGE = "\033[38;5;208m"

_CAT = r"""
   /\_/\
  ( o.o )
  > ^ <
"""

_DEFAULT_MODEL = "MiniMax-M3"
_PET_INFO_GAP = 16
_HARNESS_FILENAME = "harness.yaml"


def _read_harness_model() -> Optional[str]:
    """Best-effort read of ``harness.yaml`` ``llm.model``.

    Returns ``None`` on missing file or any parse error so the welcome
    screen never aborts on broken config — the real agent loader will
    surface those errors later (it ``sys.exit(1)``s on bad YAML).
    """
    path = Path(_HARNESS_FILENAME)
    if not path.exists():
        return None
    try:
        import yaml  # local import — keep the welcome screen importable without PyYAML
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    llm = data.get("llm")
    if not isinstance(llm, dict):
        return None
    model = llm.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _resolve_model_name() -> str:
    # Mirror the precedence used by ``config/loader.py:resolve_llm`` so the
    # welcome screen always reflects what the agent will actually run against:
    #   ``harness.yaml`` ``llm.model``  →  ``$AGENT_MODEL``  →  ``_DEFAULT_MODEL``.
    yaml_model = _read_harness_model()
    if yaml_model:
        return yaml_model
    env_model = os.environ.get("AGENT_MODEL")
    if env_model and env_model.strip():
        return env_model.strip()
    return _DEFAULT_MODEL


def render_help() -> str:
    GAP = 2   # gap between pet and info, adjustable

    # Right-side information (labels right-aligned, colons aligned)
    labels = ("Model:", "Working dir:")
    label_width = max(len(label) for label in labels)
    right_lines = [
        f"{_BOLD}{_CYAN}{labels[0]:>{label_width}}{_RESET} {_resolve_model_name()}",
        f"{_BOLD}{_CYAN}{labels[1]:>{label_width}}{_RESET} {os.getcwd()}",
    ]

    # Pet (plain text, no color codes)
    pet_lines = _CAT.strip("\n").splitlines()
    pet_lines = [line.rstrip() for line in pet_lines]
    max_rows = max(len(pet_lines), len(right_lines))
    left_width = max(len(line) for line in pet_lines) if pet_lines else 0

    # Title
    title = "WELCOME TO HG"
    lines = [
        f"{_BOLD}{_ORANGE}{title}{_RESET}",
        "",
    ]

    # Assemble each row with proper alignment
    for i in range(max_rows):
        left = pet_lines[i] if i < len(pet_lines) else ""
        right = right_lines[i] if i < len(right_lines) else ""

        # Pad left part with plain spaces, then add colors (keep padding uncolored)
        padded_left = left.ljust(left_width + GAP)
        colored_padded = f"{_BOLD}{_YELLOW}{left}{_RESET}{padded_left[len(left):]}"
        lines.append(f"{colored_padded}{right}")

    lines.append("")
    lines.append(f"> Multi-line: Shift+Enter (or Ctrl+J) inserts newline. Trailing \\ + Enter also inserts.")
    lines.append(f"> Submit: Enter. During a stream: Enter=steer, Alt+Enter=queued followUp, Esc=cancel.")
    lines.append(f"> Anything else is sent to the agent.")
    lines.append(f"> /help    - Show this help message")
    lines.append(f"> /quit    - Exit the program")

    return "\n".join(lines)

async def cmd_help(cli: "InteractiveCLI", arg: str) -> None:
    print(render_help())


async def cmd_tools(cli: "InteractiveCLI", arg: str) -> None:
    names = (
        list(cli.agent_session.agent.state.tools)
        if hasattr(cli.agent_session.agent, "state")
        else []
    )
    print(f"({len(names)} tools registered — registry API in P9)")


async def cmd_status(cli: "InteractiveCLI", arg: str) -> None:
    storage = cli.agent_session.session.storage
    md = getattr(storage, "_metadata", None)
    if md is not None:
        sid = getattr(md, "id", "?")
    else:
        sid = getattr(storage, "_session_id", "?")
    model = getattr(cli.agent_session.agent.state, "model", None)
    model_id = getattr(model, "id", None) if model is not None else None
    if model_id is None:
        model_id = str(model) if model is not None else "?"
    try:
        active_lane = await cli.agent_session.get_active_lane()
        leaf = await cli.agent_session.get_current_leaf()
    except Exception:
        active_lane = "main"
        leaf = None
    leaf_short = (leaf or "")[:8] if leaf else "-"
    try:
        stats = await cli.agent_session.session.get_stats()
        entries = stats.message_count
    except Exception:
        entries = "?"
    print(f"Session: {sid}")
    print(f"Model:   {model_id}")
    print(f"Lane:    {active_lane}  (leaf: {leaf_short})")
    print(f"Entries: {entries}")
    await cli._refresh_footer()
    from interaction.display import print_footer
    print_footer()


async def cmd_cancel(cli: "InteractiveCLI", arg: str) -> None:
    cancelled = await cli.agent_session.cancel_current_run()
    if cancelled:
        print("(cancelled)")
    else:
        print("(no active run to cancel)")


async def cmd_compact(cli: "InteractiveCLI", arg: str) -> None:
    hint = arg.strip() or None
    settings = getattr(cli.agent_session.agent, "compaction", None)
    if settings is None or not getattr(settings, "enabled", True):
        print("(compaction not enabled)")
        return
    try:
        before, after = await cli.agent_session.compact(
            custom_instructions=hint
        )
    except Exception as e:
        print(f"(compact error: {e})")
        return
    if before == 0 and after == 0:
        print("(compaction skipped)")
        return
    print(f"Compaction complete. {before} messages → {after} messages.")


async def cmd_reload(cli: "InteractiveCLI", arg: str) -> None:
    try:
        await cli.agent_session._load_transcript()
    except Exception as e:
        print(f"(reload error: {e})")
        return
    n = len(
        getattr(cli.agent_session.agent.state, "messages", []) or []
    )
    print(f"Reloaded {n} messages from session.")


async def cmd_clear(cli: "InteractiveCLI", arg: str) -> None:
    print("\033c", end="")


async def cmd_quit(cli: "InteractiveCLI", arg: str) -> None:
    cli.alive = False


async def cmd_newsession(cli: "InteractiveCLI", arg: str) -> None:
    """Stub handler — prints a 'not yet implemented' message."""
    print("(newsession stub — not yet implemented in P8)")


async def cmd_tree(cli: "InteractiveCLI", arg: str) -> None:
    try:
        tree = await cli.agent_session.list_entries_tree()
    except Exception as e:
        print(f"(tree error: {e})")
        return

    try:
        active_leaf = await cli.agent_session.get_current_leaf()
    except Exception:
        active_leaf = None

    try:
        await cli._refresh_footer(leaf_override=active_leaf)
    except Exception:
        pass

    from interaction.render.tree_selector import (
        build_colored_tree_lines,
    )
    from rich.console import Console
    from rich.markup import render as render_markup

    import sys as _sys
    console = Console(
        soft_wrap=False, file=_sys.stdout, force_terminal=False
    )

    try:
        rendered_lines, ids = build_colored_tree_lines(
            tree, active_leaf_id=active_leaf, filter_mode=cli._tree_filter
        )
    except Exception as e:
        print(f"(tree render error: {e})")
        return

    cli._last_tree_ids = [i for i in ids if i is not None]

    for line in rendered_lines:
        try:
            console.print(render_markup(line, style="default"))
        except Exception:
            import re as _re
            plain = _re.sub(r"\[/?[^\]]+\]", "", line)
            print(plain)


async def cmd_filter(cli: "InteractiveCLI", arg: str) -> None:
    mode = arg.strip().lower()
    valid = ("default", "no-tools", "user-only", "labeled-only", "all")
    if not mode:
        print(
            f"Current filter: {cli._tree_filter}  "
            f"(valid: {', '.join(valid)})"
        )
        return
    if mode not in valid:
        print(
            f"(filter error: unknown mode {mode!r} — valid: "
            f"{', '.join(valid)})"
        )
        return
    cli._tree_filter = mode
    print(f"Filter set: {mode}")


async def cmd_nav(cli: "InteractiveCLI", arg: str) -> None:
    if not arg.strip():
        print("usage: /nav <N> (run /tree first to see entry numbers)")
        return
    if not cli._last_tree_ids:
        await cmd_tree(cli, "")
    try:
        n = int(arg.strip())
    except ValueError:
        print(f"(nav error: invalid index {arg!r} — expected integer)")
        return
    if n < 1 or n > len(cli._last_tree_ids):
        print(
            f"(nav error: index {n} out of range 1..{len(cli._last_tree_ids)})"
        )
        return
    target_id = cli._last_tree_ids[n - 1]
    try:
        await cli.agent_session.navigate_to(target_id)
    except Exception as e:
        print(f"(nav error: {e})")
        return
    short = (target_id or "")[:8]
    print(f"Navigated to entry {n} ({short})")
    await _print_context(cli)


async def cmd_fork(cli: "InteractiveCLI", arg: str) -> None:
    if cli.session_repo is None:
        print(
            "(fork error: no SessionRepo wired — pass via "
            "InteractiveCLI(session_repo=...))"
        )
        return
    leaf_id = await cli.agent_session.get_current_leaf()
    if leaf_id is None:
        print("(fork error: no current leaf — send a message first)")
        return
    try:
        md = await cli.agent_session.session.get_metadata()
        from session.fork import BranchForkOptions
        import uuid as _uuid
        options = BranchForkOptions(
            scope="branch",
            entry_id=leaf_id,
            position="at",
            id=f"fork-{_uuid.uuid4().hex[:12]}",
            parent_session_id=md.id,
        )
        new_session = await cli.session_repo.fork(md, options)
    except Exception as e:
        print(f"(fork error: {e})")
        return
    if new_session is None:
        print("(fork error: source session not found)")
        return
    cli.agent_session.session = new_session
    new_md = await new_session.get_metadata()
    print(
        f"Forked to session {new_md.id}"
        f"{(' (' + arg + ')') if arg else ''}"
    )


async def cmd_name(cli: "InteractiveCLI", arg: str) -> None:
    name = arg.strip()
    if not name:
        print('usage: /name <name> (or /name "" to clear)')
        return
    if name in ('""', "''"):
        name = None
    try:
        await cli.agent_session.set_name(name)
    except Exception as e:
        print(f"(name error: {e})")
        return
    print(f'Name set: "{name}"')


async def cmd_session(cli: "InteractiveCLI", arg: str) -> None:
    try:
        md = await cli.agent_session.get_session_metadata()
        active_lane = await cli.agent_session.get_active_lane()
        leaf = await cli.agent_session.get_current_leaf()
        stats = await cli.agent_session.session.get_stats()
        lanes = await cli.agent_session.session.get_lanes()
    except Exception as e:
        print(f"(session error: {e})")
        return
    from datetime import datetime, timezone
    created = datetime.fromtimestamp(
        md.created_at, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S")
    parent = md.parent_session_id if md.parent_session_id else "none"
    leaf_short = (leaf or "")[:8] if leaf else "-"
    from collections import Counter
    lane_counts: Counter = Counter()
    try:
        all_entries = await cli.agent_session.session.find_entries()
    except Exception:
        all_entries = []
    for e in all_entries:
        ln = getattr(e, "lane", None) or "main"
        lane_counts[ln] += 1
    lane_breakdown = ", ".join(
        f"{name}={lane_counts.get(name, 0)}"
        for name in sorted(lane_counts)
    ) or "-"
    lane_names = ", ".join(lp.name for lp in lanes) or "-"
    print("Session info:")
    print(f"  id:          {md.id}")
    print(f"  name:        {md.name or '(no name)'}")
    print(f"  created_at:  {created}")
    print(f"  parent:      {parent}")
    print(f"  active_lane: {active_lane}")
    print(f"  leaf:        {leaf_short}")
    print(f"  entries:     {stats.message_count}  ({lane_breakdown})")
    print(f"  lanes:       {lane_names}")


async def cmd_new(cli: "InteractiveCLI", arg: str) -> None:
    if cli.session_repo is None:
        print(
            "(new error: no SessionRepo wired — pass via "
            "InteractiveCLI(session_repo=...))"
        )
        return
    try:
        new_session = await cli.session_repo.create_session()
    except Exception as e:
        print(f"(new error: {e})")
        return
    await cli.agent_session.reset_session(new_session)
    new_md = await new_session.get_metadata()
    print(f"Started new session: {new_md.id}")

async def cmd_resume(cli: "InteractiveCLI", arg: str) -> None:
    if cli.session_repo is None:
        print("(resume error: no SessionRepo wired)")
        return
    try:
        ids = await cli.session_repo.list_sessions()
    except Exception as e:
        print(f"(resume error: {e})")
        return
    if not ids:
        print("No sessions to resume.")
        return
    current_id = (await cli.agent_session.get_session_metadata()).id
    rows: list[tuple[str, str | None, int]] = []
    for sid in ids:
        try:
            s = await cli.session_repo.open_session(sid)
            if s is None:
                rows.append((sid, None, 0))
                continue
            md = await s.get_metadata()
            try:
                stats = await s.get_stats()
                count = stats.message_count
            except Exception:
                count = 0
            rows.append((sid, md.name, count))
        except Exception:
            rows.append((sid, None, 0))
    print(f"Sessions ({len(rows)}):")
    for i, (sid, name, count) in enumerate(rows, start=1):
        tag = "[active]" if sid == current_id else "         "
        label = f'"{name}"' if name else "(no name)"
        print(f"  {i}. {tag} {sid:<15} {label:<24} {count} entries")
    try:
        raw = await cli._ensure_prompt().prompt_async("Resume session N> ")
    except (EOFError, KeyboardInterrupt):
        print("(resume cancelled)")
        return
    raw = raw.strip()
    if not raw:
        print("(resume cancelled: no selection)")
        return
    try:
        idx = int(raw)
    except ValueError:
        print(f"(resume error: invalid selection {raw!r})")
        return
    if idx < 1 or idx > len(rows):
        print(
            f"(resume error: index {idx} out of range 1..{len(rows)})"
        )
        return
    selected_id = rows[idx - 1][0]
    try:
        opened = await cli.session_repo.open_session(selected_id)
    except Exception as e:
        print(f"(resume error: {e})")
        return
    if opened is None:
        print(f"(resume error: session {selected_id} not found)")
        return
    await cli.agent_session.reset_session(opened)
    try:
        await cli.agent_session._load_transcript()
    except Exception as e:
        print(f"(resume warning: transcript reload failed: {e})")
    print(f"Resumed session: {selected_id}")
    await _print_context(cli)


def _register_builtins() -> None:
    register(
        SlashCommand(
            name="help",
            description="Show this message",
            handler=cmd_help,
        )
    )
    register(
        SlashCommand(
            name="tools",
            description="List registered tool names",
            handler=cmd_tools,
        )
    )
    register(
        SlashCommand(
            name="status",
            description="Show session, model, lane",
            handler=cmd_status,
        )
    )
    register(
        SlashCommand(
            name="cancel",
            description="Abort current run",
            handler=cmd_cancel,
        )
    )
    register(
        SlashCommand(
            name="compact",
            description="Compact context",
            argument_hint="[hint]",
            handler=cmd_compact,
        )
    )
    register(
        SlashCommand(
            name="reload",
            description="Reload transcript from session",
            handler=cmd_reload,
        )
    )
    register(
        SlashCommand(
            name="clear",
            description="Clear screen",
            handler=cmd_clear,
        )
    )
    register(
        SlashCommand(
            name="quit",
            description="Exit",
            handler=cmd_quit,
        )
    )
    register(
        SlashCommand(
            name="tree",
            description="Show branch tree ()",
            handler=cmd_tree,
        )
    )
    register(
        SlashCommand(
            name="filter",
            description="Set tree filter",
            argument_hint="<mode>",
            handler=cmd_filter,
        )
    )
    register(
        SlashCommand(
            name="nav",
            description="Navigate to entry N",
            argument_hint="<N>",
            handler=cmd_nav,
        )
    )
    register(
        SlashCommand(
            name="fork",
            description="Fork session at current leaf",
            argument_hint="[label]",
            handler=cmd_fork,
        )
    )
    register(
        SlashCommand(
            name="name",
            description="Set session name",
            argument_hint="<name>",
            handler=cmd_name,
        )
    )
    register(
        SlashCommand(
            name="session",
            description="Show session info",
            handler=cmd_session,
        )
    )
    register(
        SlashCommand(
            name="new",
            description="Start a new session",
            handler=cmd_new,
        )
    )
    register(
        SlashCommand(
            name="resume",
            description="List and switch sessions",
            handler=cmd_resume,
        )
    )
    register(
        SlashCommand(
            name="newsession",
            description="Start a fresh session (stub — not yet implemented in P8)",
            handler=cmd_newsession,
        )
    )

_register_builtins()
