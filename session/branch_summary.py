import time
import uuid
from typing import Any

from context.compaction import CompactionSettings, generate_summary_with_usage
from context.token_estimator import TokenEstimator
from session.errors import SessionError, SessionErrorKind
from session.mutations import entry_mutation
from session.state import SessionState

__all__ = ["generate_branch_summary"]


def _new_entry_id() -> str:
    return uuid.uuid4().hex


async def generate_branch_summary(
    state: SessionState,
    leaf_entry_id: str,
    settings: CompactionSettings,
    estimator: TokenEstimator,  # part of spec API contract; reserved for future token-budget usage
    llm_client: Any,
    signal: Any | None = None,
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    # 1. Validate leaf.
    leaf = state.get_entry(leaf_entry_id)
    if leaf is None:
        raise SessionError(
            SessionErrorKind.INVALID_BRANCH_SUMMARY_TARGET,
            f"Branch summary target not found: {leaf_entry_id}",
        )
    if leaf.get("type") != "message":
        raise SessionError(
            SessionErrorKind.INVALID_BRANCH_SUMMARY_TARGET,
            f"Branch summary target is not a message entry: {leaf_entry_id}",
        )

    # 2. Walk the branch oldest-first.
    branch = state.find_entries_on_branch(
        {"start": leaf_entry_id, "order": "oldestFirst"}
    )

    # 3. Generate summary. previous_summary=None (branch is fresh).
    messages = [e["message"] for e in branch if e.get("type") == "message"]
    result = await generate_summary_with_usage(
        messages=messages,
        previous_summary=None,
        llm_client=llm_client,
        settings=settings,
        signal=signal,
        timeout=timeout,
    )

    # 4. Build the BranchSummaryEntry dict.
    lane = leaf.get("lane", "main")
    summary_entry: dict[str, Any] = {
        "id": _new_entry_id(),
        "type": "branch_summary",
        "from_id": leaf_entry_id,
        "summary": result.summary,
        "details": result.details,
        "usage": result.usage,
        "parent_id": leaf_entry_id,
        "lane": lane,
        "timestamp": time.time(),
    }

    # 5. Apply via state.apply_mutation; the dict becomes the canonical
    # stored shape.
    state.apply_mutation(entry_mutation(entry=summary_entry, lane=lane))
    return summary_entry