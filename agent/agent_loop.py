"""AgentLoop: per-run stateless scheduler.

Outer loop: continues when follow-up messages arrive after the agent would stop.
Inner loop: processes tool calls and steering messages within a turn.

Each invocation owns its own signal + abort lifecycle and emits its own
agent_start / agent_end events. Does NOT own transcript, queues, model, or
runtime flags — those belong to Agent.

Architecture:
- agent_loop: fresh prompts → emit agent_start, delegate to _run_loop, _run_loop emits agent_end.
- agent_loop_continue: validate last msg, drain initial steering, emit agent_start,
 delegate to _run_loop, _run_loop emits agent_end.
- _run_loop: shared inner loop. Echoes prompts via message_start/end (skipped on
 continuation), Uses an outer while loop for follow-ups, not recursion.
"""

import asyncio
from dataclasses import replace
from typing import Any, Awaitable, Callable

from agent.hooks import AgentLoopConfig, AgentEvent
from agent.llm_context import LLMContext
from agent.message import (
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from agent.context import SessionContext

AgentMessageT = (
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | CustomMessage
    | CompactionSummaryMessage
    | BranchSummaryMessage
)

EmitFn = Callable[[AgentEvent, Any], Awaitable[None] | None]


async def _stream_assistant_response(
    config: AgentLoopConfig,
    llm_context: LLMContext,
    signal: Any,
) -> AssistantMessage:
    """Run stream_fn and accumulate into a final AssistantMessage."""
    agen = config.stream_fn(config.model, llm_context, None)
    if asyncio.iscoroutine(agen):
        agen = await agen

    partial: AssistantMessage | None = None

    async for event in agen:
        etype = event.get("type")
        if etype == "start":
            partial = event["partial"]
        elif etype in ("text_start", "text_delta", "text_end", "thinking_start", "thinking_delta", "thinking_end", "toolcall_start", "toolcall_delta", "toolcall_end"):
            # In v1 we don't deeply track partials; emit a final "done" message at end.
            pass
        elif etype == "done":
            if partial is None:
                # StreamFn that finished without start: synthesize empty assistant
                partial = AssistantMessage(
                    role="assistant",
                    content=[],
                    api=getattr(config.model, "api", "stub"),
                    provider=getattr(config.model, "provider", "stub"),
                    model=getattr(config.model, "id", "stub"),
                    usage=_empty_usage(),
                    stop_reason="stop",
                    error_message=None,
                    timestamp=0,
                )
            return partial
        elif etype == "error":
            return AssistantMessage(
                role="assistant",
                content=[],
                api=getattr(config.model, "api", "stub"),
                provider=getattr(config.model, "provider", "stub"),
                model=getattr(config.model, "id", "stub"),
                usage=_empty_usage(),
                stop_reason="error",
                error_message=event.get("error_message"),
                timestamp=0,
            )
    # Stream exhausted without done/error — treat as error
    if partial is None:
        partial = AssistantMessage(
            role="assistant",
            content=[],
            api="stub",
            provider="stub",
            model="stub",
            usage=_empty_usage(),
            stop_reason="error",
            error_message="stream_fn ended without done/error",
            timestamp=0,
        )
    return partial


def _empty_usage():
    from agent.message import Usage

    return Usage()


async def _emit(emit: EmitFn, event: AgentEvent, signal: Any) -> None:
    result = emit(event, signal)
    if asyncio.iscoroutine(result):
        await result


async def _safe_call(fn, *args):
    """Call an optional hook. Returns None if not configured.

 Awaits the result if it's a coroutine.
"""
    if fn is None:
        return None
    raw = fn(*args)
    if asyncio.iscoroutine(raw):
        raw = await raw
    return raw


async def _safe_call_bool(fn, *args) -> bool:
    """Call an optional bool-returning hook. Returns False if not configured."""
    if fn is None:
        return False
    raw = fn(*args)
    if asyncio.iscoroutine(raw):
        raw = await raw
    return bool(raw)


async def _drain(provider) -> list[AgentMessageT]:
    """Drain messages from an optional provider. Returns [] if not configured."""
    if provider is None:
        return []
    raw = provider()
    if asyncio.iscoroutine(raw):
        raw = await raw
    return list(raw or [])


def _should_stop_ctx(assistant, tool_results, context, new_messages):
    from agent.hooks import ShouldStopContext

    return ShouldStopContext(
        message=assistant,
        tool_results=tool_results,
        context=context,
        new_messages=new_messages,
    )


def _prepare_next_turn_ctx(assistant, tool_results, context, new_messages):
    from agent.hooks import PrepareNextTurnContext

    return PrepareNextTurnContext(
        message=assistant,
        tool_results=tool_results,
        context=context,
        new_messages=new_messages,
    )

async def _run_loop(
    prompts: list[AgentMessageT],
    context: SessionContext,
    config: AgentLoopConfig,
    signal: Any,
    stream_fn: Any,
    emit: EmitFn,
    *,
    resolve_tool: Callable[[str], Any],
) -> list[AgentMessageT]:
    new_messages: list[AgentMessageT] = []

    for prompt in prompts:
        await _emit(emit, {"type": "message_start", "message": prompt}, signal)
        await _emit(emit, {"type": "message_end", "message": prompt}, signal)
        new_messages.append(prompt)
        context.messages.append(prompt)

    current_messages = list(context.messages)

    first_turn = True

    # drain initial steering so the user sees it BEFORE the
    # first LLM call. (The caller — agent_loop_continue — may have already
    # passed some as `prompts`; we drain whatever's still queued here.)
    initial_steer = await _drain(config.get_steering_messages)
    for m in initial_steer:
        _stash_pending(context, m)

    # Outer loop — same agent_start/agent_end envelope (no recursion).
    while True:
        has_more_tool_calls = True

        # Inner loop: process tool calls + pendingMessages (steering / follow-ups).
        while has_more_tool_calls or _has_pending_messages(context, config):
            # turn_start — skipped on first turn (caller emitted it).
            if not first_turn:
                await _emit(emit, {"type": "turn_start"}, signal)
            else:
                first_turn = False

            # Inject any pending messages (initial steering on first turn;
            # subsequent turn steering at top of next iteration).
            pending = _drain_pending(context, config)
            for m in pending:
                await _emit(emit, {"type": "message_start", "message": m}, signal)
                await _emit(emit, {"type": "message_end", "message": m}, signal)
                current_messages.append(m)
                new_messages.append(m)
                context.messages.append(m)

            # Transform context (AgentMessage-level).
            if config.transform_context is not None:
                current_messages = await config.transform_context(current_messages, signal)

            # Convert to LLM messages.
            llm_messages_raw = config.convert_to_llm(current_messages)
            if asyncio.iscoroutine(llm_messages_raw):
                llm_messages = await llm_messages_raw
            else:
                llm_messages = llm_messages_raw

            llm_context = LLMContext(
                messages=llm_messages,
                tools=list(getattr(context, "tools", None) or ()),
                system_prompt=getattr(context, "system_prompt", None),
            )

            # Stream assistant response.
            await _emit(emit, {"type": "message_start", "message": None}, signal)
            assistant = await _stream_assistant_response(config, llm_context, signal)
            new_messages.append(assistant)
            current_messages.append(assistant)
            context.messages.append(assistant)

            await _emit(emit, {"type": "message_end", "message": assistant}, signal)

            if assistant.stop_reason in ("error", "aborted"):
                await _emit(emit, {
                    "type": "turn_end", "message": assistant, "tool_results": []
                }, signal)
                await _emit(emit, {"type": "agent_end", "messages": new_messages}, signal)
                return new_messages

            tool_calls = [c for c in assistant.content if isinstance(c, ToolCallContent)]

            tool_results: list[ToolResultMessage] = []
            terminate_batch = False
            if tool_calls:
                if assistant.stop_reason == "length":
                    # Truncated tool calls — fail them all rather than executing
                    # potentially borked args.
                    from agent.tool_executor import fail_tool_calls_from_truncated_message

                    error_results = fail_tool_calls_from_truncated_message(assistant)
                    for er in error_results:
                        await _emit(emit, {
                            "type": "tool_execution_end",
                            "toolCallId": er.tool_call_id,
                            "toolName": er.tool_name,
                            "args": {},
                            "result": er.content[0].text,
                            "isError": True,
                        }, signal)
                        await _emit(emit, {"type": "message_start", "message": er}, signal)
                        await _emit(emit, {"type": "message_end", "message": er}, signal)
                        tool_results.append(er)
                else:
                    from agent.tool_executor import execute_tools

                    async def _tool_emit(event: dict[str, Any]) -> None:
                        await _emit(emit, event, signal)

                    tool_results, terminate_batch = await execute_tools(
                        current_context=context,
                        assistant_message=assistant,
                        config=config,
                        signal=signal,
                        emit=_tool_emit,
                        resolve_tool=resolve_tool,
                    )

                for tr in tool_results:
                    current_messages.append(tr)
                    new_messages.append(tr)
                    context.messages.append(tr)
                    await _emit(emit, {"type": "message_start", "message": tr}, signal)
                    await _emit(emit, {"type": "message_end", "message": tr}, signal)

            await _emit(emit, {
                "type": "turn_end",
                "message": assistant,
                "tool_results": tool_results,
                "terminate": terminate_batch,
            }, signal)

            # prepare_next_turn FIRST (so model/thinking_level
            # swaps land before should_stop_after_turn inspects the context).
            update = await _safe_call(
                config.prepare_next_turn,
                _prepare_next_turn_ctx(assistant, tool_results, context, new_messages),
            )
            if update is not None:
                if update.context is not None:
                    context = update.context
                    current_messages = list(context.messages)
                if update.model is not None:
                    config = replace(config, model=update.model)
                if update.thinking_level is not None:
                    config = replace(config, thinking_level=update.thinking_level)

            stopped = await _safe_call_bool(
                config.should_stop_after_turn,
                _should_stop_ctx(assistant, tool_results, context, new_messages),
            )
            if stopped:
                await _emit(emit, {"type": "agent_end", "messages": new_messages}, signal)
                return new_messages

            # Drain steering for next iteration (259). Stash via the same
            # mechanism as follow-ups so the inner-loop top picks them up.
            if config.get_steering_messages is not None:
                pending_steer = await _drain(config.get_steering_messages)
                for m in pending_steer:
                    _stash_pending(context, m)

            has_more_tool_calls = bool(tool_calls) and not terminate_batch

        # Agent would stop here. Drain follow-ups → set as pending → continue.
        follow_ups = await _drain(config.get_follow_up_messages)
        if follow_ups:
            for m in follow_ups:
                # Stash on context for the next inner-loop iteration.
                # We piggyback on context via a side attribute to keep this
                # single-function — the alternative (passing pending_messages
                # around explicitly) requires rewriting the inner-loop condition.
                _stash_pending(context, m)
            continue
        break

    await _emit(emit, {"type": "agent_end", "messages": new_messages}, signal)
    return new_messages


def _stash_pending(context: SessionContext, msg: AgentMessageT) -> None:
    """Stash a follow-up message on the context for the next inner iteration.

 keeps pendingMessages as a local variable across the inner/outer loop
 boundary. In Python we don't have closures across while-loops, so we
 piggyback on the context object. The follow-ups are drained by the next
 inner-loop entry via :func:`_drain_pending`.
"""
    if not hasattr(context, "_pending_follow_ups"):
        context._pending_follow_ups = []  # type: ignore[attr-defined]
    context._pending_follow_ups.append(msg)  # type: ignore[attr-defined]


def _drain_pending(context: SessionContext, config: AgentLoopConfig) -> list[AgentMessageT]:
    """Drain any stashed follow-ups. Steering is drained separately at the
 bottom of each inner iteration.
"""
    pending: list[AgentMessageT] = []
    stash = getattr(context, "_pending_follow_ups", None)
    if stash:
        pending.extend(stash)
        context._pending_follow_ups = []  # type: ignore[attr-defined]
    return pending


def _has_pending_messages(context: SessionContext, config: AgentLoopConfig) -> bool:
    """Inner-loop continuation condition: True if any follow-ups are stashed."""
    return bool(getattr(context, "_pending_follow_ups", None))


async def agent_loop(
    prompts: list[AgentMessageT],
    context: SessionContext,
    config: AgentLoopConfig,
    signal: Any,
    stream_fn: Any,
    emit: EmitFn,
    *,
    resolve_tool: Callable[[str], Any],
) -> list[AgentMessageT]:
    await _emit(emit, {"type": "agent_start"}, signal)
    await _emit(emit, {"type": "turn_start"}, signal)
    return await _run_loop(
        prompts, context, config, signal, stream_fn, emit,
        resolve_tool=resolve_tool,
    )

async def agent_loop_continue(
    context: SessionContext,
    config: AgentLoopConfig,
    signal: Any,
    stream_fn: Any,
    emit: EmitFn,
    *,
    resolve_tool: Callable[[str], Any],
) -> list[AgentMessageT]:

    if not context.messages:
        raise RuntimeError("Cannot continue: no messages in context")
    last = context.messages[-1]
    if last.role == "assistant":
        raise RuntimeError("Cannot continue from message role: assistant")

    await _emit(emit, {"type": "agent_start"}, signal)

    return await _run_loop(
        [], context, config, signal, stream_fn, emit,
        resolve_tool=resolve_tool,
    )
