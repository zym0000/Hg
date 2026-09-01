from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from typing import Any, Callable

from agent.tool.agent_tool import AgentTool
from agent.tool.model import AgentToolResult, TextContent
from agent.truncate import truncate_tail


_BASH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Bash command to execute",
        },
        "timeout": {
            "type": "integer",
            "description": (
                "Timeout in seconds (optional, no default). When set, the "
                "process tree is killed after this many seconds and any "
                "captured output is returned."
            ),
        },
    },
    "required": ["command"],
}

IDLE_GRACE_S: float = 0.1

# Throttle interval for streamed on_update snapshots.
UPDATE_INTERVAL_S: float = 0.1


def _windows_utf8_prefix() -> str:
    """Return a `chcp 65001` prefix for Windows cmd.exe to force UTF-8 output.

    On non-Windows, returns "". The prefix is `chcp 65001 >nul && ` — it
    silences the codepage announcement line and chains the user's command.

    Without this, Chinese (and other non-ASCII) Windows shells output GBK
    bytes; we'd then decode them as UTF-8 and end up with mojibake like
    `驱动器 E 中的卷` becoming `������ E �еľ���`.
"""
    if sys.platform != "win32":
        return ""
    return "chcp 65001 >nul && "


async def _wait_signal_aborted(signal_obj: Any, poll_s: float = 0.05) -> None:
    if signal_obj is None:
        await asyncio.Event().wait()
        return
    while not getattr(signal_obj, "aborted", False):
        await asyncio.sleep(poll_s)


async def _await_exit(proc: Any, poll_s: float = 0.05) -> int:
    while proc.returncode is None:
        await asyncio.sleep(poll_s)
    return proc.returncode

async def _drain_stream(
    stream: Any,
    on_chunk: Callable[[bytes], Any],
    proc: Any,
    idle_grace_s: float,
    poll_s: float = 0.05,
) -> None:
    if stream is None:
        return
    last_data_at = time.monotonic()
    while True:
        try:
            chunk = await asyncio.wait_for(stream.read(8192), timeout=poll_s * 5)
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                if time.monotonic() - last_data_at >= idle_grace_s:
                    return
            continue
        if not chunk:
            return
        await on_chunk(chunk)
        last_data_at = time.monotonic()


async def _drain_until_settled(
    proc: Any,
    on_chunk: Callable[[bytes], Any],
    settled: asyncio.Event,
    idle_grace_s: float = IDLE_GRACE_S,
) -> None:
    await asyncio.gather(
        _drain_stream(proc.stdout, on_chunk, proc, idle_grace_s),
        _drain_stream(proc.stderr, on_chunk, proc, idle_grace_s),
    )
    settled.set()


def _kill_process_tree(proc: Any) -> None:
    if proc.returncode is not None:
        return
    pid = proc.pid
    if pid is None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass


def _append_status(text: str, status: str) -> str:
    return f"{text}\n\n{status}" if text else status


class _BashTool:
    def __init__(self, cwd: str) -> None:
        self.name = "bash"
        self.label = "Bash"
        self.description = (
            "Execute a shell command in the working directory. Returns combined "
            "stdout and stderr. Use timeout (seconds) to bound long-running commands."
        )
        self.parameters = _BASH_SCHEMA
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
        cmd = _windows_utf8_prefix() + args["command"]
        explicit_timeout = args.get("timeout")

        # Unix: start_new_session=True makes the child a session leader
        # so killpg covers the whole tree. Windows: taskkill /T handles it.
        start_new_session = sys.platform != "win32"

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=self._cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
                start_new_session=start_new_session,
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(type="text", text=f"Error executing command: {exc}")],
                details={"isError": True},
            )

        # Output accumulator: chunks from concurrent drain tasks, with
        # an update_event flag for throttled on_update snapshots.
        chunks: list[bytes] = []
        chunks_lock = asyncio.Lock()
        update_event = asyncio.Event()

        async def on_chunk(chunk: bytes) -> None:
            chunks.append(chunk)
            update_event.set()

        settled = asyncio.Event()
        drain_task = asyncio.create_task(
            _drain_until_settled(proc, on_chunk, settled)
        )

        # Throttled on_update streaming. No-op when on_update is None
        # (unit tests pass None).
        emit_task: asyncio.Task[None] | None = None
        if on_update is not None:
            async def _emit_loop() -> None:
                while True:
                    await asyncio.sleep(UPDATE_INTERVAL_S)
                    if not update_event.is_set():
                        continue
                    update_event.clear()
                    async with chunks_lock:
                        raw = b"".join(chunks)
                    text = raw.decode("utf-8", errors="replace")
                    truncated = truncate_tail(text)
                    on_update(AgentToolResult(
                        content=[TextContent(type="text", text=truncated.content or "")],
                        details=None,
                    ))
            emit_task = asyncio.create_task(_emit_loop())

        # Race: natural exit / abort / timeout (if set)
        tasks_to_wait: list[asyncio.Task[Any]] = []
        timeout_task: asyncio.Task[Any] | None = None
        if explicit_timeout is not None:
            timeout_task = asyncio.create_task(asyncio.sleep(explicit_timeout))
            tasks_to_wait.append(timeout_task)

        abort_task = asyncio.create_task(_wait_signal_aborted(signal))
        exit_task = asyncio.create_task(_await_exit(proc))
        tasks_to_wait.append(abort_task)
        tasks_to_wait.append(exit_task)

        aborted = False
        timed_out = False
        try:
            done, _ = await asyncio.wait(
                tasks_to_wait, return_when=asyncio.FIRST_COMPLETED
            )
            aborted = abort_task in done
            timed_out = timeout_task is not None and timeout_task in done
        finally:
            for t in tasks_to_wait:
                if not t.done():
                    t.cancel()

        if aborted or timed_out:
            _kill_process_tree(proc)
            # exit_task will see returncode set shortly; drain settles
            # via the post-exit idle window in _drain_stream.

        # Wait for drain to settle.
        try:
            await asyncio.wait_for(settled.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):
                pass

        if emit_task is not None and not emit_task.done():
            emit_task.cancel()
            try:
                await emit_task
            except (asyncio.CancelledError, Exception):
                pass

        async with chunks_lock:
            raw = b"".join(chunks)
        text = raw.decode("utf-8", errors="replace")
        truncated = truncate_tail(text)
        final_text = truncated.content or "(no output)"

        if aborted:
            return AgentToolResult(
                content=[TextContent(type="text", text=_append_status(final_text, "Command aborted"))],
                details={"isError": True, "exit_code": -1, "aborted": True},
            )
        if timed_out:
            return AgentToolResult(
                content=[TextContent(type="text", text=_append_status(final_text, f"Command timed out after {explicit_timeout}s"))],
                details={"isError": True, "exit_code": -1, "timed_out": True},
            )

        details: dict[str, Any] = {
            "exit_code": proc.returncode,
            "isError": proc.returncode != 0,
        }
        if truncated.truncated:
            details["truncation"] = {
                "truncated": truncated.truncated,
                "truncated_by": truncated.truncated_by,
                "total_lines": truncated.total_lines,
                "total_bytes": truncated.total_bytes,
                "output_lines": truncated.output_lines,
                "output_bytes": truncated.output_bytes,
                "last_line_partial": truncated.last_line_partial,
                "first_line_exceeds_limit": truncated.first_line_exceeds_limit,
                "max_lines": truncated.max_lines,
                "max_bytes": truncated.max_bytes,
            }
        return AgentToolResult(
            content=[TextContent(type="text", text=final_text)],
            details=details,
        )


def create_bash_tool(cwd: str, options: Any = None) -> AgentTool:
    return _BashTool(cwd)
