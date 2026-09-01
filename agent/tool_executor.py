from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from agent.context import SessionContext
from agent.hooks import AgentLoopConfig, AfterToolCallContext, BeforeToolCallContext
from agent.message import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
)

if TYPE_CHECKING:
    # Protocol-only import — no runtime need. Annotation-only references
    # resolve through ``from __future__ import annotations`` above.
    from agent.tool.agent_tool import AgentTool  # noqa: F401


def __getattr__(name: str) -> Any:
    # Back-compat: re-export AgentTool at runtime for callers that still
    # ``from agent.tool_executor import AgentTool``. Lazy via __getattr__
    # to avoid the cycle agent.tool_executor <-> agent.tool.agent_tool.
    if name == "AgentTool":
        from agent.tool.agent_tool import AgentTool
        return AgentTool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class AgentToolResult:
    """Final or partial result produced by a tool.
    """
    content: list[TextContent | ImageContent]
    details: Any = None
    usage: Usage | None = None
    added_tool_names: list[str] | None = None
    terminate: bool = False


@dataclass(frozen=True)
class PreparedToolCall:
    kind: Literal["prepared"]
    tool_call: ToolCallContent
    tool: AgentTool
    args: Any


@dataclass(frozen=True)
class ImmediateToolCallOutcome:
    kind: Literal["immediate"]
    result: AgentToolResult
    is_error: bool

def _make_error_result(message: str, *, terminate: bool = False) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(type="text", text=message)],
        details=None,
        terminate=terminate,
    )


def _agent_tool_result_to_text(result: AgentToolResult) -> str:
    return "".join(b.text for b in result.content if isinstance(b, TextContent))

def fail_tool_calls_from_truncated_message(
    assistant_message: AssistantMessage,
) -> list[ToolResultMessage]:
    results: list[ToolResultMessage] = []
    for block in assistant_message.content:
        if not isinstance(block, ToolCallContent):
            continue
        err = (
            f'Tool call "{block.name}" was not executed: the response hit the '
            f"output token limit, so its arguments may be truncated. "
            f"Consider increasing max_tokens or breaking the task into smaller steps."
        )
        results.append(
            ToolResultMessage(
                tool_call_id=block.id,
                tool_name=block.name,
                content=[TextContent(type="text", text=err)],
                is_error=True,
                timestamp=0,
            )
        )
    return results


async def prepare_tool_call(
    current_context: SessionContext,
    assistant_message: AssistantMessage,
    tool_call: ToolCallContent,
    config: AgentLoopConfig,
    signal: Any | None,
    *,
    resolve_tool: Callable[[str], AgentTool | None],
) -> PreparedToolCall | ImmediateToolCallOutcome:
    
    tool = resolve_tool(tool_call.name)

    if tool is None:
        return ImmediateToolCallOutcome(
            kind="immediate",
            result=_make_error_result(f"Tool {tool_call.name} not found"),
            is_error=True,
        )

    try:
        args = tool_call.arguments

        if config.before_tool_call is not None:
            hook_ctx = BeforeToolCallContext(
                assistant_message=assistant_message,
                tool_call=tool_call,
                args=args,
                context=current_context,
            )
            before_raw = config.before_tool_call(hook_ctx, signal)
            before = await before_raw if asyncio.iscoroutine(before_raw) else before_raw
            if signal is not None and getattr(signal, "aborted", False):
                return ImmediateToolCallOutcome(
                    kind="immediate",
                    result=_make_error_result("Operation aborted"),
                    is_error=True,
                )
            #这里是human in loop，如果外面拒绝，则流程结束
            if before is not None and before.block:
                return ImmediateToolCallOutcome(
                    kind="immediate",
                    result=_make_error_result(before.reason or "Tool execution was blocked"),
                    is_error=True,
                )

        if signal is not None and getattr(signal, "aborted", False):
            return ImmediateToolCallOutcome(
                kind="immediate",
                result=_make_error_result("Operation aborted"),
                is_error=True,
            )
        #返回工具执行前验证阶段信息
        return PreparedToolCall(kind="prepared", tool_call=tool_call, tool=tool, args=args)
    except Exception as e:
        return ImmediateToolCallOutcome(
            kind="immediate",
            result=_make_error_result(str(e)),
            is_error=True,
        )

@dataclass(frozen=True)
class ExecutedToolCallOutcome:
    """Result of execute_tool_call (no after_tool_call yet)."""
    result: AgentToolResult
    is_error: bool


async def execute_tool_call(
    prepared: PreparedToolCall,
    signal: Any | None,
    emit: Callable[[dict[str, Any]], Awaitable[None] | None],
) -> ExecutedToolCallOutcome:
    accepting_updates = True

    def _emit_update(partial: AgentToolResult) -> None:
        if not accepting_updates:
            return
        ev = {
            "type": "tool_execution_update",
            "toolCallId": prepared.tool_call.id,
            "toolName": prepared.tool_call.name,
            "args": prepared.tool_call.arguments,
            "partialResult": partial,
        }
        result = emit(ev)
        if asyncio.iscoroutine(result):
            loop = asyncio.get_running_loop()
            loop.create_task(result)

    try:
        result = await prepared.tool.execute(
            prepared.tool_call.id,
            prepared.args,
            signal,
            _emit_update,
        )
        return ExecutedToolCallOutcome(result=result, is_error=False)
    except Exception as exc:
        return ExecutedToolCallOutcome(
            result=_make_error_result(str(exc)),
            is_error=True,
        )
    finally:
        accepting_updates = False


@dataclass(frozen=True)
class FinalizedToolCallOutcome:
    tool_call: ToolCallContent
    result: AgentToolResult
    is_error: bool


async def finalize_tool_call(
    current_context: SessionContext,
    assistant_message: AssistantMessage,
    prepared: PreparedToolCall,
    executed: ExecutedToolCallOutcome,
    config: AgentLoopConfig,
    signal: Any | None,
) -> FinalizedToolCallOutcome:
    result = executed.result
    is_error = executed.is_error

    if config.after_tool_call is not None:
        try:
            hook_ctx = AfterToolCallContext(
                assistant_message=assistant_message,
                tool_call=prepared.tool_call,
                args=prepared.args,
                result=result,
                is_error=is_error,
                context=current_context,
            )
            after_raw = config.after_tool_call(hook_ctx, signal)
            after = await after_raw if asyncio.iscoroutine(after_raw) else after_raw
            if after is not None:
                result = AgentToolResult(
                    content=after.content if after.content is not None else result.content,
                    details=after.details if after.details is not None else result.details,
                    usage=after.usage if after.usage is not None else result.usage,
                    added_tool_names=result.added_tool_names,
                    terminate=after.terminate if after.terminate is not None else result.terminate,
                )
                if after.is_error is not None:
                    is_error = after.is_error
        except Exception as exc:
            result = _make_error_result(str(exc))
            is_error = True

    return FinalizedToolCallOutcome(
        tool_call=prepared.tool_call,
        result=result,
        is_error=is_error,
    )


def create_tool_result_message(finalized: FinalizedToolCallOutcome) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=finalized.tool_call.id,
        tool_name=finalized.tool_call.name,
        content=list(finalized.result.content),
        details=finalized.result.details,
        usage=finalized.result.usage,
        added_tool_names=finalized.result.added_tool_names,
        is_error=finalized.is_error,
    )


def _should_terminate_batch(finalized: list[FinalizedToolCallOutcome]) -> bool:
    return len(finalized) > 0 and all(f.result.terminate for f in finalized)


async def execute_tools(
    current_context: SessionContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Callable[[dict[str, Any]], Awaitable[None] | None],
    *,
    resolve_tool: Callable[[str], AgentTool | None],
) -> tuple[list[ToolResultMessage], bool]:
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCallContent)]
    if not tool_calls:
        return [], False

    if config.tool_execution == "parallel":
        return await _execute_tools_parallel(
            current_context, assistant_message, tool_calls, config, signal, emit,
            resolve_tool=resolve_tool,
        )
    return await _execute_tools_sequential(
        current_context, assistant_message, tool_calls, config, signal, emit,
        resolve_tool=resolve_tool,
    )


async def _execute_tools_sequential(
    current_context: SessionContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCallContent],
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Callable[[dict[str, Any]], Awaitable[None] | None],
    *,
    resolve_tool: Callable[[str], AgentTool | None],
) -> tuple[list[ToolResultMessage], bool]:
    finalized: list[FinalizedToolCallOutcome] = []
    messages: list[ToolResultMessage] = []

    for tool_call in tool_calls:
        await _emit_simple(emit, {
            "type": "tool_execution_start",
            "toolCallId": tool_call.id,
            "toolName": tool_call.name,
            "args": tool_call.arguments,
        })

        outcome = await prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal,
            resolve_tool=resolve_tool,
        )
        if isinstance(outcome, ImmediateToolCallOutcome):
            f = FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=outcome.result,
                is_error=outcome.is_error,
            )
        else:
            #如果验证没有问题，执行工具
            executed = await execute_tool_call(outcome, signal, emit)
            #处理返回最终结果
            f = await finalize_tool_call(
                current_context, assistant_message, outcome, executed, config, signal,
            )

        await _emit_simple(emit, {
            "type": "tool_execution_end",
            "toolCallId": f.tool_call.id,
            "toolName": f.tool_call.name,
            "result": _agent_tool_result_to_text(f.result),
            "isError": f.is_error,
        })
        msg = create_tool_result_message(f)
        finalized.append(f)
        messages.append(msg)

        if signal is not None and getattr(signal, "aborted", False):
            break

    return messages, _should_terminate_batch(finalized)


async def _execute_tools_parallel(
    current_context: SessionContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCallContent],
    config: AgentLoopConfig,
    signal: Any | None,
    emit: Callable[[dict[str, Any]], Awaitable[None] | None],
    *,
    resolve_tool: Callable[[str], AgentTool | None],
) -> tuple[list[ToolResultMessage], bool]:
    async def _one(tool_call: ToolCallContent) -> FinalizedToolCallOutcome:
        await _emit_simple(emit, {
            "type": "tool_execution_start",
            "toolCallId": tool_call.id,
            "toolName": tool_call.name,
            "args": tool_call.arguments,
        })
        outcome = await prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal,
            resolve_tool=resolve_tool,
        )
        if isinstance(outcome, ImmediateToolCallOutcome):
            f = FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=outcome.result,
                is_error=outcome.is_error,
            )
        else:
            executed = await execute_tool_call(outcome, signal, emit)
            f = await finalize_tool_call(
                current_context, assistant_message, outcome, executed, config, signal,
            )
        await _emit_simple(emit, {
            "type": "tool_execution_end",
            "toolCallId": f.tool_call.id,
            "toolName": f.tool_call.name,
            "result": _agent_tool_result_to_text(f.result),
            "isError": f.is_error,
        })
        return f

    finalized = await asyncio.gather(*(_one(tc) for tc in tool_calls))
    messages = [create_tool_result_message(f) for f in finalized]
    return messages, _should_terminate_batch(list(finalized))


async def _emit_simple(
    emit: Callable[[dict[str, Any]], Awaitable[None] | None],
    event: dict[str, Any],
) -> None:
    result = emit(event)
    if asyncio.iscoroutine(result):
        await result
