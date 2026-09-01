import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from llm_client import LLMClient
from session.repo import SessionRepo
from session.session import Session
from agent.tool.registry import AgentToolRegistry
from agent.tool.mcp_adapter import load_mcp_tools
from agent.tool.builtin import create_coding_tools
from agent.skill.loader import load_skills
from config.loader import resolve_llm

def _session_id_factory() -> str:
    return f"s-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"

@dataclass
class MissingSessionCwdError(Exception):
    session_cwd: str
    fallback_cwd: str
    session_id: str | None = None

    def __str__(self) -> str:
        sid = f" (session {self.session_id})" if self.session_id else ""
        return (
            f"Stored session working directory does not exist{sid}: {self.session_cwd}\n"
            f"Current working directory: {self.fallback_cwd}"
        )

@dataclass
class BootstrapResult:
    registry: AgentToolRegistry
    llm: LLMClient
    session_repo: SessionRepo
    session: "Session"
    skills: list
    workspace_path: Path


async def bootstrap(
    workspace: str | None = None,
    skills_dir: str = "./skills",
    sessions_dir: str = "./sessions",
    model: str | None = None,
    mcp_server_script: str | None = None,
    cwd_override: str | None = None,
    resume_session_id: str | None = None,
    require_cwd_exists: bool = False,
) -> BootstrapResult:
    # LLM
    resolved_llm = resolve_llm(
        default_api_key=None,
        default_base_url="https://api.minimaxi.com/v1",
        default_model=model or "MiniMax-M3",
    )
    llm = LLMClient(
        api_key=resolved_llm.api_key,
        base_url=resolved_llm.base_url,
        model=resolved_llm.model,
        timeout=120.0,
    )

    skills_path = Path(skills_dir).resolve()
    try:
        skills_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    skills = load_skills([skills_path])

    session_repo = SessionRepo(
        root_dir=sessions_dir,
        id_generator=_session_id_factory,
    )

    process_cwd = Path.cwd()

    if resume_session_id:
        session = await session_repo.open_session(resume_session_id)
        if session is None:
            from session.errors import SessionError, SessionErrorKind
            raise SessionError(
                SessionErrorKind.NOT_FOUND,
                f"Session not found: {resume_session_id}",
            )
        session_cwd = session.cwd
    else:
        pinned_cwd = str(Path(workspace).resolve()) if workspace else str(process_cwd)
        session = await session_repo.create_session(cwd=pinned_cwd)
        session_cwd = session.cwd

    # Tiered priority: override > session header > workspace hint > process cwd
    if cwd_override:
        workspace_path = Path(cwd_override).resolve()
    elif session_cwd:
        workspace_path = Path(session_cwd)
    elif workspace:
        workspace_path = Path(workspace).resolve()
    else:
        workspace_path = process_cwd

    if (
        require_cwd_exists
        and not workspace_path.exists()
        and resume_session_id is not None
    ):
        raise MissingSessionCwdError(
            session_cwd=str(workspace_path),
            fallback_cwd=str(process_cwd),
            session_id=resume_session_id,
        )


    registry = AgentToolRegistry()
    registry.register_many(create_coding_tools(str(workspace_path)))
    if mcp_server_script:
        mcp_tools = await load_mcp_tools(mcp_server_script)
        registry.register_many(mcp_tools)

    return BootstrapResult(
        registry=registry,
        llm=llm,
        session_repo=session_repo,
        session=session,
        skills=skills,
        workspace_path=workspace_path,
    )