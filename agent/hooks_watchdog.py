from typing import Any

from agent.hooks import BeforeToolCallContext, BeforeToolCallResult

def make_watchdog_hook(watchdog: Any, task_id: str):
    async def watchdog_hook(
        ctx: BeforeToolCallContext, signal: Any | None
    ) -> BeforeToolCallResult | None:
        decision, message = await watchdog.record_and_check(task_id, ctx.tool_call)
        if decision == "SAFE":
            return None
        if decision == "WARNING":
            return BeforeToolCallResult(block=True, reason=message, terminate=False)
        # CRITICAL
        return BeforeToolCallResult(block=True, reason=message, terminate=True)

    return watchdog_hook
