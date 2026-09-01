import asyncio
import uuid
from typing import Any, AsyncIterator

from agent.message import (
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    ToolResultMessage,
    UserMessage,
)
from session.session import Session
from session.types import OperationFinishedRecord, OperationStartedRecord, SessionMetadata


class AgentSession:
    def __init__(
        self,
        agent: Any,
        session: Session,
        approval_gate: Any = None,
        llm_client: Any | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.agent = agent
        self.session = session
        self.approval_gate = approval_gate
        self._llm_client = llm_client
        self._current_run_id: str | None = None
        self._subscribed = False

        self._active_lane: str = "main"

        self._last_assistant_message: AssistantMessage | None = None
        self._overflow_recovery_attempted: bool = False

        self._max_tokens: int | None = max_tokens

    def _ensure_subscribed(self) -> None:
        if self._subscribed:
            return
        self.agent.subscribe(self._on_agent_event)
        self._subscribed = True

    async def _on_agent_event(self, event: dict, signal: Any) -> None:
        t = event.get("type")
        if t == "message_end":
            msg = event.get("message")
            if msg is None:
                return
            
            if getattr(msg, "role", None) == "assistant":
                self._last_assistant_message = msg
                stop_reason = getattr(msg, "stop_reason", None)
                # Reset overflow-recovery guard on a successful assistant
                # response — only counts retriable errors / length stops.
                if stop_reason not in ("error", "length"):
                    self._overflow_recovery_attempted = False
            try:
                from session._agent_message_convert import agent_message_to_dict
                d = agent_message_to_dict(msg)
                await self.session.append_message(d, lane=self._active_lane)
            except Exception:
                pass
        elif t == "agent_end":
            if self._current_run_id is not None:
                rec = OperationFinishedRecord(
                    type="operation_finished",
                    run_id=self._current_run_id,
                    outcome="completed",
                )
                try:
                    await self.session.append_record(rec, lane=self._active_lane)
                except Exception:
                    pass
                self._current_run_id = None

    async def _load_transcript(self) -> None:
        from session.context import build_session_context

        try:
            ctx = await build_session_context(self.session, lane=self._active_lane)
        except Exception:
            self.agent.state.messages = []
            return

        new_messages: list = []
        for m in ctx.messages:
            if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage)):
                new_messages.append(m)
            elif isinstance(m, dict):
                try:
                    from session._agent_message_convert import dict_to_agent_message
                    new_messages.append(dict_to_agent_message(m))
                except Exception:
                    pass
        self.agent.state.messages = new_messages

    async def run(self, text: str) -> AsyncIterator[dict]:
        self._ensure_subscribed()

        await self._load_transcript()

        try:
            last_assistant = await self._find_last_assistant_message()
            if last_assistant is not None:
                await self._check_compaction(
                    last_assistant, skip_aborted_check=False
                )
        except Exception:
            pass

        self._current_run_id = uuid.uuid4().hex
        rec = OperationStartedRecord(
            type="operation_started",
            run_id=self._current_run_id,
            intent={"kind": "run", "original_prompt": text},
        )
        await self.session.append_record(rec, lane=self._active_lane)

        # Set up an event collector by subscribing a queue-pushing listener
        # alongside the persistent one.
        queue: asyncio.Queue = asyncio.Queue()

        async def collector(event: dict, signal: Any) -> None:
            await queue.put(event)

        unsub = self.agent.subscribe(collector)
        try:
            # Kick off agent.prompt in a background task so we can yield events
            # as they arrive.
            prompt_task = asyncio.create_task(self.agent.prompt(text))

            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if prompt_task.done():
                        while not queue.empty():
                            yield queue.get_nowait()
                        break
                    continue
                yield ev
                if ev.get("type") == "agent_end":
                    try:
                        await prompt_task
                    except Exception:
                        pass
                    break

            while await self._handle_post_agent_run():
                async for ev in self._drive_continue():
                    yield ev
        finally:
            try:
                unsub()
            except Exception:
                pass

    async def _drive_continue(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()

        async def collector(event: dict, signal: Any) -> None:
            await queue.put(event)

        unsub = self.agent.subscribe(collector)
        try:
            prompt_task = asyncio.create_task(self.agent.continue_())

            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if prompt_task.done():
                        while not queue.empty():
                            yield queue.get_nowait()
                        break
                    continue
                yield ev
                if ev.get("type") == "agent_end":
                    try:
                        await prompt_task
                    except Exception:
                        pass
                    break
        finally:
            try:
                unsub()
            except Exception:
                pass

    async def _handle_post_agent_run(self) -> bool:
        msg = self._last_assistant_message
        self._last_assistant_message = None
        if msg is None:
            return False

        try:
            triggered = await self._check_compaction(msg)
        except Exception:
            triggered = False
        if triggered:
            return True

        try:
            return bool(self.agent.has_queued_messages())
        except Exception:
            return False

    async def get_active_lane(self) -> str:
        return self._active_lane

    async def get_lanes(self) -> list:
        return await self.session.get_lanes()

    async def set_active_lane(self, lane: str) -> None:
        names = {lp.name for lp in await self.session.get_lanes()}
        if lane not in names:
            raise ValueError(f"unknown lane: {lane!r}")
        self._active_lane = lane

    async def get_current_leaf(self) -> str | None:
        for lp in await self.session.get_lanes():
            if lp.name == self._active_lane:
                return lp.leaf_id
        return None

    async def list_entries_tree(self) -> list[dict]:
        entries = await self.session.find_entries()
        # Build parent_id lookup
        by_id = {getattr(e, "id", None): e for e in entries}
        depths: dict[str, int] = {}
        for e in entries:
            eid = getattr(e, "id", None)
            depth = 0
            cur = e
            seen: set[str] = set()
            while cur is not None and getattr(cur, "parent_id", None):
                pid = cur.parent_id
                if pid in seen:
                    break  # cycle guard
                seen.add(pid)
                if pid in depths:
                    depth += depths[pid] + 1
                    break
                parent = by_id.get(pid)
                if parent is None:
                    break
                cur = parent
                depth += 1
            depths[eid] = depth

        out: list[dict] = []
        for e in entries:
            eid = getattr(e, "id", None)
            etype = getattr(e, "type", None)
            role = None
            preview = ""
            if etype == "message":
                msg = getattr(e, "message", {}) or {}
                role = msg.get("role") if isinstance(msg, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else ""
                if isinstance(content, list):
                    # Concatenate text blocks
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                    preview = " ".join(parts)
                elif isinstance(content, str):
                    preview = content
            elif etype:
                preview = etype
                role = etype
            preview = (preview or "").replace("\n", " ").strip()
            if len(preview) > 60:
                preview = preview[:57] + "..."
            out.append({
                "id": eid,
                "parent_id": getattr(e, "parent_id", None),
                "type": etype,
                "role": role,
                "preview": preview,
                "depth": depths.get(eid, 0),
                "seq": getattr(e, "seq", 0),
            })
        # Sort by seq ascending
        out.sort(key=lambda x: x.get("seq", 0))
        return out

    async def navigate_to(self, entry_id: str) -> None:
        target = await self.session.get_entry(entry_id)
        if target is None:
            raise ValueError(f"entry not found: {entry_id!r}")

        branch = await self.session.find_entries_on_branch(
            {"start": entry_id, "order": "oldestFirst"}
        )

        # Convert dict-shaped message entries back into typed AgentMessage so
        # the agent state stays consistent (mirrors _load_transcript).
        from session._agent_message_convert import dict_to_agent_message

        new_messages: list = []
        for entry in branch:
            etype = getattr(entry, "type", None)
            if etype == "message":
                msg = getattr(entry, "message", None)
                if isinstance(msg, dict):
                    try:
                        new_messages.append(dict_to_agent_message(msg))
                    except Exception:
                        continue
                else:
                    new_messages.append(msg)

        self.agent.state.messages = new_messages
        # Navigating implies a new context — the active lane pointer is left
        # at "main" per spec (the new context is the rebuilt branch on main).
        self._active_lane = "main"

    async def set_name(self, name: str | None) -> None:
        """Persist a friendly name for the current session (or clear with None)."""
        await self.session.set_name(name)

    async def get_name(self) -> str | None:
        return await self.session.get_name()

    async def get_session_metadata(self) -> SessionMetadata:
        return await self.session.get_metadata()

    async def reset_session(self, new_session: Session) -> None:
        self.session = new_session
        self._active_lane = "main"
        self.agent.state.messages = []

    async def _find_last_assistant_message(self) -> AssistantMessage | None:
        for m in reversed(self.agent.state.messages or []):
            if getattr(m, "role", None) == "assistant":
                return m
        return None

    def _latest_compaction_timestamp(self) -> float | None:
        state = self._session_state()
        if state is None:
            return None
        latest: float | None = None
        for entry in state.entries:
            if entry.get("type") != "compaction":
                continue
            if entry.get("lane", "main") != self._active_lane:
                continue
            ts = entry.get("timestamp")
            if isinstance(ts, (int, float)) and (
                latest is None or ts > latest
            ):
                latest = float(ts)
        return latest

    def _is_same_model(self, assistant_message: AssistantMessage) -> bool:
        model = getattr(self.agent.state, "model", None)
        if model is None:
            return True
        if isinstance(model, str):
            return assistant_message.model == model
        model_provider = getattr(model, "provider", None)
        model_id = getattr(model, "id", None) or getattr(model, "model", None)
        return (
            assistant_message.provider == model_provider
            and assistant_message.model == model_id
        )

    async def _check_compaction(
        self,
        assistant_message: AssistantMessage,
        skip_aborted_check: bool = True,
    ) -> bool:
        from context.compaction import (
            calculate_context_tokens,
            is_context_overflow,
            is_recoverable_length,
            should_compact,
        )

        settings = getattr(self.agent, "compaction", None)
        estimator = getattr(self.agent, "token_estimator", None)
        if (
            settings is None
            or estimator is None
            or not getattr(settings, "enabled", True)
        ):
            return False
        if self._llm_client is None:
            return False

        stop_reason = getattr(assistant_message, "stop_reason", None)
        usage = getattr(assistant_message, "usage", None)

        # Aborted skip (unless caller is pre-prompt and wants aborted handled).
        if skip_aborted_check and stop_reason == "aborted":
            return False

        # Stale guard.
        latest_compaction_ts = self._latest_compaction_timestamp()
        assistant_ts = getattr(assistant_message, "timestamp", None)
        if (
            latest_compaction_ts is not None
            and isinstance(assistant_ts, (int, float))
            and float(assistant_ts) <= float(latest_compaction_ts)
        ):
            return False

        context_window = int(getattr(self.agent, "context_window", 0) or 0)
        same_model = self._is_same_model(assistant_message)

        # Overflow path.
        overflow = same_model and is_context_overflow(assistant_message, context_window)
        recoverable = same_model and is_recoverable_length(
            assistant_message, self._max_tokens
        )

        if overflow or recoverable:
            will_retry = stop_reason != "stop"
            estimated = calculate_context_tokens(usage) if usage is not None else 0
            signal = getattr(self.agent, "signal", None)

            if not will_retry:
                triggered = await self._run_auto_compaction(
                    estimated=estimated,
                    signal=signal,
                    reason="overflow",
                    will_retry=False,
                )
                if not triggered:
                    return False

                return bool(self.agent.has_queued_messages())

            if self._overflow_recovery_attempted:
                await self._emit_session_event(
                    {
                        "type": "compaction_end",
                        "reason": "overflow",
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                        "errorMessage": (
                            "Context overflow recovery failed after one "
                            "compact-and-retry attempt."
                        ),
                    },
                    signal,
                )
                return False

            self._overflow_recovery_attempted = True
            messages = self.agent.state.messages
            if messages and getattr(messages[-1], "role", None) == "assistant":
                self.agent.state.messages = messages[:-1]

            return await self._run_auto_compaction(
                estimated=estimated,
                signal=signal,
                reason="overflow",
                will_retry=True,
            )

        direct = calculate_context_tokens(usage) if usage is not None else 0
        if stop_reason == "error" or direct == 0:
            context_tokens = estimator.estimate_message(
                self.agent.state.messages
            )
        else:
            context_tokens = direct

        if not should_compact(context_tokens, context_window, settings):
            return False

        triggered = await self._run_auto_compaction(
            estimated=context_tokens,
            signal=getattr(self.agent, "signal", None),
            reason="threshold",
            will_retry=False,
        )
        if not triggered:
            return False
        return bool(self.agent.has_queued_messages())

    async def _run_auto_compaction(
        self,
        *,
        estimated: int,
        signal: Any | None,
        reason: str = "threshold",
        will_retry: bool = False,
    ) -> bool:
        from session.orchestrator import compact_orchestrator

        state = self._session_state()
        if state is None:
            return False


        await self._emit_session_event(
            {
                "type": "compaction_start",
                "reason": reason,
                "tokens_before": estimated,
                "context_window": getattr(self.agent, "context_window", 0),
            },
            signal,
        )

        try:
            entry = await compact_orchestrator(
                state=state,
                settings=self.agent.compaction,
                estimator=self.agent.token_estimator,
                llm_client=self._llm_client,
                signal=signal,
            )
        except Exception:
            entry = None

        if entry is None:
            await self._emit_session_event(
                {
                    "type": "compaction_end",
                    "reason": reason,
                    "result": None,
                    "aborted": False,
                    "willRetry": will_retry,
                },
                signal,
            )
            return False

        await self._reload_agent_state_from_session()
        # 这里重试的原因是，如果是因为 token> windows ，触发压缩之后，删除最新的一条错误记录，然后重新构建上下文触发任务
        if will_retry:
            messages = self.agent.state.messages
            if messages:
                last = messages[-1]
                if (
                    getattr(last, "role", None) == "assistant"
                    and getattr(last, "stop_reason", None) in ("error", "length")
                ):
                    self.agent.state.messages = messages[:-1]

        await self._emit_session_event(
            {
                "type": "compaction_end",
                "reason": reason,
                "result": None,
                "aborted": False,
                "willRetry": will_retry,
            },
            signal,
        )
        return True

    def _session_state(self) -> Any:
        """Best-effort access to the SessionState for the orchestrator."""
        storage = getattr(self.session, "storage", None)
        return getattr(storage, "_state", None) if storage is not None else None

    async def _reload_agent_state_from_session(self) -> None:
        from session.context import build_session_context

        try:
            ctx = await build_session_context(self.session, lane=self._active_lane)
        except Exception:
            return

        new_messages: list = []
        for m in ctx.messages:
            if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage,
                              CompactionSummaryMessage, BranchSummaryMessage)):
                new_messages.append(m)
            elif isinstance(m, dict):
                try:
                    from session._agent_message_convert import dict_to_agent_message
                    new_messages.append(dict_to_agent_message(m))
                except Exception:
                    continue
        self.agent.state.messages = new_messages

    async def _emit_session_event(self, event: dict, signal: Any) -> None:
        """Forward an event through the Agent event bus so subscribers."""
        try:
            self.agent._emit(event, signal)
            # Drain listener dispatch tasks so tests observing events
            # synchronously don't race the loop's create_task scheduling.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        except Exception:
            pass

    async def compact(
        self, custom_instructions: str | None = None
    ) -> tuple[int, int]:

        settings = getattr(self.agent, "compaction", None)
        if (
            settings is None
            or self._llm_client is None
            or not getattr(settings, "enabled", True)
        ):
            return (0, 0)

        before = list(self.agent.state.messages)
        signal = getattr(self.agent, "signal", None)
        triggered = await self._run_auto_compaction(
            estimated=0,
            signal=signal,
            reason="manual",
            will_retry=False,
        )
        if not triggered:
            return (len(before), len(self.agent.state.messages))
        return (len(before), len(self.agent.state.messages))

    async def cancel_current_run(self) -> bool:
        active = getattr(self.agent, "_active_run", None)
        if active is None:
            return False
        try:
            self.agent.abort()
        except Exception:
            return False
        return True

    async def is_streaming(self) -> bool:
        try:
            return bool(getattr(self.agent.state, "is_streaming", False))
        except Exception:
            return False