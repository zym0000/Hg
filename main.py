import argparse
import asyncio
import shutil
import sys
import time

from agent.agent import Agent
from agent.agent_session import AgentSession
from agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from config.bootstrap import bootstrap
from context.compaction import CompactionSettings
from interaction.cli import InteractiveCLI
from session.repo import SessionInfo, format_cwd_for_display


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main",
        description="Hg coding-agent CLI (cwd resolution).",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Force a specific working directory for builtin tools "
        "(overrides session header and process cwd).",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__LIST__",   
        default=None,
        help="Resume an existing session. With no argument, list recent "
        "sessions and prompt for an id. With an id, open directly.",
    )
    parser.add_argument(
        "--continue-last",
        action="store_true",
        dest="continue_last",
        help="Resume the most recently modified session. (Named with a "
        "suffix because ``continue`` is a Python keyword and would "
        "shadow it.)",
    )
    parser.add_argument(
        "--no-drift-check",
        action="store_true",
        help="Skip MissingSessionCwdError when a resumed session's cwd "
        "no longer exists; fall back to process cwd silently.",
    )
    parser.add_argument(
        "--skills-dir",
        default="./skills",
        help="Directory holding skill SKILL.md files (default: ./skills).",
    )
    parser.add_argument(
        "--sessions-dir",
        default="./sessions",
        help="Directory holding session JSONL files (default: ./sessions).",
    )
    return parser.parse_args(argv)

def _format_age(modified_at: float, now: float | None = None) -> str:
    """Format a mtime as a short relative age string ('3d', '5h', '12m')."""
    now = now if now is not None else time.time()
    diff_s = max(0, now - modified_at)
    if diff_s < 60:
        return "now"
    if diff_s < 3600:
        return f"{int(diff_s // 60)}m"
    if diff_s < 86400:
        return f"{int(diff_s // 3600)}h"
    if diff_s < 86400 * 30:
        return f"{int(diff_s // 86400)}d"
    if diff_s < 86400 * 365:
        return f"{int(diff_s // (86400 * 30))}mo"
    return f"{int(diff_s // (86400 * 365))}y"

def _print_session_list(infos: list[SessionInfo], terminal_width: int) -> None:
    if not infos:
        print("(no sessions found)")
        return

    id_w = min(14, max(len(i.id) for i in infos))
    age_w = 4

    cwd_w = max(10, terminal_width - id_w - age_w - 6)

    header = f"{'ID':<4}  {'AGE':>{age_w}}  {'CWD':<{cwd_w}}"
    print(header)
    print("-" * min(len(header), terminal_width))

    for info in infos:
        age = _format_age(info.modified_at)
        cwd = format_cwd_for_display(info.cwd, cwd_w)
        # Truncate id display to id_w but show a tail if longer.
        id_disp = info.id
        if len(id_disp) > id_w:
            id_disp = id_disp[: id_w - 1] + "…"
        name_or_dash = info.name or "-"
        print(
            f"{id_disp:<{min(id_w, len(id_disp))}}  "
            f"{age:>{age_w}}  "
            f"{cwd:<{cwd_w}}  "
            f"{name_or_dash}"
        )


async def _pick_session(infos: list[SessionInfo]) -> str | None:
    print()
    try:
        choice = input("Enter session id to resume (blank to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice:
        return None
    
    matches = [i for i in infos if i.id == choice or i.id.startswith(choice)]
    if len(matches) == 1:
        return matches[0].id
    if len(matches) > 1:
        print(f"Ambiguous id '{choice}'; matches: {', '.join(m.id for m in matches[:5])}")
        return None
    print(f"No session with id starting with '{choice}'")
    return None

async def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv or [])

    # Resolve --resume target up front (before bootstrap runs) so we can
    # show a listing without paying the bootstrap cost.
    resume_id = args.resume
    if resume_id == "__LIST__":
        # Stand up just enough of the repo to list sessions — bootstrap
        # itself is heavy (loads skills, talks to LLM) so skip it here.
        from session.repo import SessionRepo

        repo_for_list = SessionRepo(
            root_dir=args.sessions_dir,
            id_generator=lambda: "unused",
        )
        infos = await repo_for_list.list_session_infos()
        if not infos:
            print(f"No sessions found in {args.sessions_dir}")
            return
        width = shutil.get_terminal_size((100, 20)).columns
        print(f"Sessions in {args.sessions_dir} (newest first):")
        print()
        _print_session_list(infos, width)
        resume_id = await _pick_session(infos)
        if resume_id is None:
            return
    elif args.continue_last:
        from session.repo import SessionRepo

        repo_for_list = SessionRepo(
            root_dir=args.sessions_dir,
            id_generator=lambda: "unused",
        )
        infos = await repo_for_list.list_session_infos()
        if not infos:
            print(f"No sessions found in {args.sessions_dir}")
            return
        resume_id = infos[0].id
        print(f"Resuming most recent session: {resume_id}")

    parts = await bootstrap(
        workspace=args.cwd,
        skills_dir=args.skills_dir,
        sessions_dir=args.sessions_dir,
        cwd_override=args.cwd,
        resume_session_id=resume_id,
        require_cwd_exists=not args.no_drift_check,
    )

    workspace = str(parts.workspace_path)

    # Compose system prompt build_system_prompt.
    tool_snapshots = {t.name: t.description for t in parts.registry.list()}
    system_prompt = build_system_prompt(
        BuildSystemPromptOptions(
            skills=parts.skills,
            tool_snippets=tool_snapshots,
            cwd=workspace,
            harness_name="Logi",
        )
    )

    # AgentTool objects go into Agent state; OpenAI schemas are derived
    # on demand when calling the LLM (see ).
    agent = Agent(
        system_prompt=system_prompt,
        model=parts.llm.model,
        llm=parts.llm,
        compaction=CompactionSettings(),
        context_window=128000,
        tools=parts.registry.list(),
        resolve_tool=parts.registry.resolve_tool,
    )

    agent_session = AgentSession(
        agent=agent,
        session=parts.session,
        llm_client=parts.llm,
    )

    cli = InteractiveCLI(
        agent_session=agent_session,
        workspace=workspace,
        session_repo=parts.session_repo,
    )
    await cli.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n(interrupted)", file=sys.stderr)