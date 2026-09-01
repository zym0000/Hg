import time
import uuid
from typing import Any

from context.compaction import (
    CompactionSettings,
    extract_file_operations,
    generate_summary_with_usage,
    prepare_compaction,
)
from session.mutations import entry_mutation
from session.state import SessionState

__all__ = ["compact_orchestrator"]

def _new_entry_id() -> str:
    return uuid.uuid4().hex

async def compact_orchestrator(
    state: SessionState,
    settings: CompactionSettings,
    estimator: Any,
    llm_client: Any,
    signal: Any | None = None,
    *,
    timeout: float = 120.0,
) -> dict[str, Any] | None:
    
    entries = state.find_entries()
    if not entries:
        return None
    if entries[-1].get("type") == "compaction":
        return None

    prep = await prepare_compaction(entries, settings, estimator)

    if not prep.messages_to_summarize and not prep.turn_prefix_messages:
        return None

    if prep.is_split_turn and prep.turn_prefix_messages:
        await _emit_branch_summary_for_split_turn(
            state=state,
            settings=settings,
            turn_prefix_messages=prep.turn_prefix_messages,
            turn_start_entry_id=prep.turn_start_entry_id,
            llm_client=llm_client,
            signal=signal,
            timeout=timeout,
        )

    summary_result = await generate_summary_with_usage(
        messages=prep.messages_to_summarize,
        previous_summary=prep.previous_summary,
        llm_client=llm_client,
        settings=settings,
        signal=signal,
        timeout=timeout,
    )

    merged_details = await extract_file_operations(
        summary_result.summary,
        previous_details=prep.file_ops,
    )

    parent_id = state.lanes.get("main")
    entry_dict: dict[str, Any] = {
        "id": _new_entry_id(),
        "parent_id": parent_id,
        "timestamp": time.time(),
        "lane": "main",
        "type": "compaction",
        "summary": summary_result.summary,
        "first_kept_entry_id": prep.first_kept_entry_id,
        "tokens_before": summary_result.tokens_before,
        "details": merged_details,
        "usage": summary_result.usage,
    }
    state.apply_mutation(entry_mutation(entry=entry_dict, lane="main"))
    return entry_dict

async def _emit_branch_summary_for_split_turn(
    *,
    state: SessionState,
    settings: CompactionSettings,
    turn_prefix_messages: list[dict[str, Any]],
    turn_start_entry_id: str | None,
    llm_client: Any,
    signal: Any | None,
    timeout: float,
) -> None:
    
    from context.compaction import TURN_PREFIX_SUMMARIZATION_PROMPT

    summary_result = await generate_summary_with_usage(
        messages=turn_prefix_messages,
        previous_summary=None,
        llm_client=llm_client,
        settings=settings,
        signal=signal,
        timeout=timeout,
        prompt_override=TURN_PREFIX_SUMMARIZATION_PROMPT,
    )

    parent_id = state.lanes.get("main")
    branch_dict: dict[str, Any] = {
        "id": _new_entry_id(),
        "parent_id": parent_id,
        "timestamp": time.time(),
        "lane": "main",
        "type": "branch_summary",
        "from_id": turn_start_entry_id,
        "summary": summary_result.summary,
        "details": summary_result.details,
        "usage": summary_result.usage,
    }
    state.apply_mutation(entry_mutation(entry=branch_dict, lane="main"))