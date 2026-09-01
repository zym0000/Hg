import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.agent_loop import agent_loop as _agent_loop_fn
from agent.agent_loop import agent_loop_continue as _agent_loop_continue_fn
from agent.context import SessionContext
from agent.message import (
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from agent.queue import PendingMessageQueue

AgentMessageT = (
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | CustomMessage
    | CompactionSummaryMessage
    | BranchSummaryMessage
)

class _AbortController:
    def __init__(self) -> None:
        self._aborted = False

    @property
    def signal(self) -> "_AbortSignal":
        return _AbortSignal(self)

    def abort(self) -> None:
        self._aborted = True

class _AbortSignal:
    def __init__(self, controller: "_AbortController") -> None:
        self._controller = controller

    @property
    def aborted(self) -> bool:
        return self._controller._aborted


def _to_user_message(prompt: Any) -> UserMessage:
    if isinstance(prompt, str):
        return UserMessage(
            role="user",
            content=[TextContent(type="text", text=prompt)],
            timestamp=0,
        )
    if isinstance(prompt, UserMessage):
        return prompt
    raise TypeError(f"prompt must be str or UserMessage, got {type(prompt).__name__}")

async def _empty_stream_fn(
    model: Any, context: Any, options: Any = None
) -> Any:
    async def gen():
        if False:
            yield None
    return gen()


@dataclass
class AgentState:
    system_prompt: str
    model: Any
    thinking_level: str
    is_streaming: bool = False
    streaming_message: AgentMessageT | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error_message: str | None = None
    _tools: list[Any] = field(default_factory=list)
    _messages: list[AgentMessageT] = field(default_factory=list)

    def __init__(
        self,
        system_prompt: str,
        model: Any,
        thinking_level: str,
        tools: list[Any] | None = None,
        messages: list[AgentMessageT] | None = None,
        is_streaming: bool = False,
        streaming_message: AgentMessageT | None = None,
        pending_tool_calls: set[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.model = model
        self.thinking_level = thinking_level
        self._tools = list(tools) if tools is not None else []
        self._messages = list(messages) if messages is not None else []
        self.is_streaming = is_streaming
        self.streaming_message = streaming_message
        self.pending_tool_calls = (
            set(pending_tool_calls) if pending_tool_calls is not None else set()
        )
        self.error_message = error_message

    @property
    def tools(self) -> list[Any]:
        return list(self._tools)

    @tools.setter
    def tools(self, value: list[Any]) -> None:
        self._tools = list(value)

    @property
    def messages(self) -> list[AgentMessageT]:
        return list(self._messages)

    @messages.setter
    def messages(self, value: list[AgentMessageT]) -> None:
        self._messages = list(value)


class Agent:
    def __init__(
        self,
        *,
        system_prompt: str = "",
        model: Any = None,
        thinking_level: str = "off",
        tools: list[Any] | None = None,
        messages: list[AgentMessageT] | None = None,
        session_id: str | None = None,
        steering_mode: str = "one-at-a-time",
        follow_up_mode: str = "one-at-a-time",
        llm: Any = None,
        compaction: Any | None = None,
        context_window: int = 128000,
        token_estimator: Any | None = None,
        resolve_tool: Callable[[str], Any] | None = None,
    ) -> None:
        self._state = AgentState(
            system_prompt=system_prompt,
            model=model,
            thinking_level=thinking_level,
            tools=list(tools or []),
            messages=list(messages or []),
        )
        self.steering_queue = PendingMessageQueue(mode=steering_mode)
        self.follow_up_queue = PendingMessageQueue(mode=follow_up_mode)
        self.session_id = session_id
        self._listeners: list[Any] = []
        self._active_run: dict[str, Any] | None = None
        self._llm = llm
        self.resolve_tool = resolve_tool if resolve_tool is not None else (lambda _name: None)

        if token_estimator is None and compaction is not None:
            from context.token_estimator import TokenEstimator
            token_estimator = TokenEstimator()
        self.compaction = compaction
        self.context_window = context_window
        self.token_estimator = token_estimator

        self.transform_context: Callable | None = None
        self.should_stop_after_turn: Callable | None = None
        self.prepare_next_turn: Callable | None = None
        self.tool_execution: str = "sequential"
        self.before_tool_call: Callable | None = None
        self.after_tool_call: Callable | None = None
        self.get_api_key: Callable | None = None
        self.thinking_level: str | None = None

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def signal(self) -> Any:
        return None if self._active_run is None else self._active_run.get("abort_controller").signal

    def steer(self, message: AgentMessageT) -> None:
        self.steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessageT) -> None:
        self.follow_up_queue.enqueue(message)

    def clear_steering_queue(self) -> None:
        self.steering_queue.clear()

    def clear_follow_up_queue(self) -> None:
        self.follow_up_queue.clear()

    def clear_all_queues(self) -> None:
        self.clear_steering_queue()
        self.clear_follow_up_queue()

    def has_queued_messages(self) -> bool:
        return self.steering_queue.has_items() or self.follow_up_queue.has_items()

    def subscribe(self, listener: Any) -> Any:
        """Register a listener. Returns an unsubscribe function."""
        self._listeners.append(listener)

        def _unsub() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub

    async def _dispatch(self, event: dict[str, Any], signal: Any) -> None:
        for listener in list(self._listeners):
            result = listener(event, signal)
            if asyncio.iscoroutine(result):
                await result

    def _emit(self, event: dict[str, Any], signal: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop: ignore async listeners, call sync ones directly
            for listener in list(self._listeners):
                result = listener(event, signal)
                if asyncio.iscoroutine(result):
                    # Cannot await without a loop; drop it.
                    continue
            return
        loop.create_task(self._dispatch(event, signal))

    def abort(self) -> None:
        if self._active_run is None:
            return
        ac = self._active_run.get("abort_controller")
        if ac is not None:
            ac.abort()

    async def wait_for_idle(self) -> None:
        if self._active_run is None:
            return
        promise = self._active_run.get("promise")
        if promise is None:
            return
        await promise

    def reset(self) -> None:
        if self._active_run is not None:
            raise RuntimeError("Agent is already processing. Wait for completion before resetting.")
        self._state._messages = []
        self._state.is_streaming = False
        self._state.streaming_message = None
        self._state.pending_tool_calls = set()
        self._state.error_message = None
        self.clear_all_queues()

    async def prompt(self, prompt: Any) -> list[AgentMessageT]:
        if self._active_run is not None:
            raise RuntimeError("Agent is already processing")
        user_message = _to_user_message(prompt)
        return await self._run_with_lifecycle([user_message])

    async def continue_(self) -> list[AgentMessageT]:
        if self._active_run is not None:
            raise RuntimeError("Agent is already processing")

        last_user: AgentMessageT | None = None
        last_assistant: AgentMessageT | None = None
        for m in reversed(self._state._messages):
            if last_user is None and m.role == "user":
                last_user = m
            if last_assistant is None and m.role == "assistant":
                last_assistant = m
            if last_user is not None and last_assistant is not None:
                break

        if last_assistant is not None and last_assistant is not last_user:
            drained = self.steering_queue.drain()
            if drained:
                for m in drained:
                    self._state._messages.append(m)
                return await self._run_with_lifecycle([], continuation=True)
            # Last is assistant, no steering → cannot continue.
            raise RuntimeError(
                "No pending steering or follow-up messages to continue from"
            )

        # Last is user (or there are no assistant messages): need a user message.
        if last_user is None:
            raise RuntimeError("Cannot continue: no user message in transcript")

        return await self._run_with_lifecycle([], continuation=True)

    async def _run_with_lifecycle(
        self,
        prompts: list[AgentMessageT],
        *,
        continuation: bool = False,
    ) -> list[AgentMessageT]:
        from agent.convert import convert_to_llm
        from agent.hooks import AgentLoopConfig

        stream_fn = None
        if getattr(self, "_llm", None) is not None:
            try:
                from agent.llm_stream import make_llm_stream_fn as _make_llm_stream
                stream_fn = _make_llm_stream(self._llm)
            except ImportError:
                stream_fn = None
        if stream_fn is None:
            stream_fn = _empty_stream_fn

        ac = _AbortController()
        loop = asyncio.get_running_loop()
        promise = loop.create_future()
        self._active_run = {"abort_controller": ac, "promise": promise}
        signal = ac.signal

        ctx = SessionContext(
            messages=list(self._state._messages),
            thinking_level=self._state.thinking_level,
            model=self._state.model,
            active_tool_names=[t.name for t in self._state._tools] or None,
            tools=list(self._state._tools),
            system_prompt=self._state.system_prompt,
        )

        config = AgentLoopConfig(
            model=self._state.model,
            thinking_level=self.thinking_level,
            convert_to_llm=convert_to_llm,
            transform_context=self.transform_context,
            stream_fn=stream_fn,
            get_api_key=self.get_api_key,
            should_stop_after_turn=self.should_stop_after_turn,
            prepare_next_turn=self.prepare_next_turn,
            get_steering_messages=self._steering_messages_provider,
            get_follow_up_messages=self._follow_up_messages_provider,
            tool_execution=self.tool_execution,
            before_tool_call=self.before_tool_call,
            after_tool_call=self.after_tool_call,
            resolve_tool=self.resolve_tool,
        )

        async def emit(event: dict[str, Any], _signal: Any) -> None:
            # Forward to subscribers (use Agent._emit for consistency).
            self._emit(event, _signal)

        try:
            if continuation:
                new_messages = await _agent_loop_continue_fn(
                    context=ctx,
                    config=config,
                    signal=signal,
                    stream_fn=stream_fn,
                    emit=emit,
                    resolve_tool=config.resolve_tool,
                )
            else:
                new_messages = await _agent_loop_fn(
                    prompts=prompts,
                    context=ctx,
                    config=config,
                    signal=signal,
                    stream_fn=stream_fn,
                    emit=emit,
                    resolve_tool=config.resolve_tool,
                )
            for m in new_messages:
                self._state._messages.append(m)
            promise.set_result(new_messages)
            return new_messages
        finally:
            self._active_run = None

    async def _steering_messages_provider(self) -> list[AgentMessageT]:
        return self.steering_queue.drain()

    async def _follow_up_messages_provider(self) -> list[AgentMessageT]:
        return self.follow_up_queue.drain()
