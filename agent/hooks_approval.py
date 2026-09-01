
import asyncio
from typing import Any, Callable

from agent.hooks import BeforeToolCallContext, BeforeToolCallResult


def make_approval_hook(
    approval_gate: Any,
    timeout: float = 300.0,
    is_dangerous: Callable[[str], bool] | None = None,
):
    if is_dangerous is None:
        is_dangerous = lambda _name: False  # noqa: E731

    async def approval_hook(
        ctx: BeforeToolCallContext, signal: Any | None
    ) -> BeforeToolCallResult | None:
        # Defensive: in production ctx.tool_call is always ToolCallContent, but
        # unit tests pass None for simplicity. Extract the tool name if available.
        tool_name = getattr(ctx.tool_call, "name", "") or ""
        if not is_dangerous(tool_name):
            return None

        await approval_gate.reset()

        # Wait for approval (cancellable, with timeout).
        try:
            approved = await asyncio.wait_for(approval_gate.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Approval timeout is treated as reject, per spec.
            return BeforeToolCallResult(block=True, reason="Approval timeout", terminate=False)
        except asyncio.CancelledError:
            raise

        if not approved:
            return BeforeToolCallResult(
                block=True, reason="User rejected or approval denied", terminate=False
            )
        return None  # approved → proceed

    return approval_hook
