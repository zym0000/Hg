import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from session.fork import ForkOptions
from session.session import Session
from session.storage.jsonl import JsonlSessionStorage
from session.types import SessionMetadata


@dataclass(frozen=True)
class SessionInfo:
    id: str
    cwd: str | None
    name: str | None
    created_at: float
    modified_at: float

    @staticmethod
    def try_read_header(path: Path) -> "SessionInfo | None":
        import json
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    d = json.loads(stripped)
                    if d.get("kind") != "header":
                        return None
                    md = d.get("metadata") or {}
                    stat = path.stat()
                    return SessionInfo(
                        id=md.get("id", path.stem),
                        cwd=md.get("cwd"),
                        name=md.get("name"),
                        created_at=float(md.get("created_at", 0.0)),
                        modified_at=stat.st_mtime,
                    )
        except (OSError, json.JSONDecodeError):
            return None
        return None


def format_cwd_for_display(cwd: str | None, max_width: int | None = None) -> str:
    if cwd is None:
        return ""

    home = os.path.expanduser("~")
    if home and cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    if max_width is None or len(cwd) <= max_width:
        return cwd

    # Keep the tail (last segment is usually the project name — most
    # informative for distinguishing sessions). Reserve 3 chars for
    # the leading ellipsis prefix.
    if max_width <= 3:
        return cwd[-max_width:]
    return "..." + cwd[-(max_width - 3):]

class SessionRepo:
    def __init__(
        self,
        root_dir: Path | str,
        id_generator: Callable[[], str],
        storage_factory: Callable[[str, Path], JsonlSessionStorage] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._id_generator = id_generator
        self._storage_factory = storage_factory or self._default_factory

    def _default_factory(self, session_id: str, path: Path, cwd: str | None = None) -> JsonlSessionStorage:

        from session.storage.jsonl import JsonlSessionStorage as _J
        if path.exists() and path.stat().st_size > 0:
            return _J.load(path)
        from session.types import SessionMetadata
        return _J(
            metadata=SessionMetadata(id=session_id, created_at=time.time(), cwd=cwd),
            path=path,
        )

    def _path_for(self, session_id: str) -> Path:
        return self.root_dir / f"{session_id}.jsonl"

    async def create_session(self, cwd: str | None = None) -> Session:

        session_id = self._id_generator()
        path = self._path_for(session_id)
        if path.exists():
            from session.errors import SessionError, SessionErrorKind
            raise SessionError(
                SessionErrorKind.ALREADY_EXISTS,
                f"Session already exists: {session_id}",
            )
        storage = self._storage_factory(session_id, path, cwd=cwd)
        return Session(storage=storage)

    async def open_session(self, session_id: str) -> Session | None:
        path = self._path_for(session_id)
        if not path.exists():
            return None
        storage = self._storage_factory(session_id, path, cwd=None)
        s = Session(storage=storage)
        await self._crash_recover(s)
        return s

    async def list_sessions(self) -> list[str]:
        return sorted(p.stem for p in self.root_dir.glob("*.jsonl"))

    async def list_session_infos(self) -> list[SessionInfo]:
        out: list[SessionInfo] = []
        for p in self.root_dir.glob("*.jsonl"):
            info = SessionInfo.try_read_header(p)
            if info is not None:
                out.append(info)
        out.sort(key=lambda i: i.modified_at, reverse=True)
        return out

    async def delete_session(self, session_id: str) -> bool:
        path = self._path_for(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def fork(self, source: SessionMetadata, options: ForkOptions) -> Session | None:
        """Fork `source` into a new session per `options`. Returns None if source not found."""
        src = await self.open_session(source.id)
        if src is None:
            return None
        parent_id = options.parent_session_id or source.id
        new_id = options.id or self._id_generator()
        target_path = self._path_for(new_id)
        if target_path.exists():
            from session.errors import SessionError, SessionErrorKind
            raise SessionError(
                SessionErrorKind.ALREADY_EXISTS,
                f"Session already exists: {new_id}",
            )

        inherited_cwd = src.cwd or source.cwd
        new_md = SessionMetadata(
            id=new_id,
            created_at=time.time(),
            parent_session_id=parent_id,
            cwd=inherited_cwd,
        )

        new_storage = await src.storage.fork(new_md, options)
        return Session(storage=new_storage)

    async def _crash_recover(self, s: Session) -> None:
        open_ops = await s.find_open_operations()
        for op in open_ops:
            from session.types import OperationFinishedRecord
            await s.append_record(OperationFinishedRecord(
                type="operation_finished",
                run_id=op.run_id,
                outcome="aborted",
            ))