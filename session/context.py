from typing import Any

from agent.context import SessionContext
from agent.message import BranchSummaryMessage, CompactionSummaryMessage

def _derive_state(path_entries: list[Any]) -> tuple[dict[str, str] | None, str, list[str] | None]:
    """Walk the FULL path entries; state is last-writer-wins per type."""
    model: dict[str, str] | None = None
    thinking_level = "off"
    active_tool_names: list[str] | None = None
    for e in path_entries:
        etype = getattr(e, "type", None)
        if etype == "model_change":
            model = {"provider": e.provider, "model_id": e.model_id}
        elif etype == "thinking_level_change":
            thinking_level = e.thinking_level
        elif etype == "active_tools_change":
            active_tool_names = list(e.active_tool_names)
    return model, thinking_level, active_tool_names


def _default_context_entry_transform(path_entries: list[Any]) -> list[Any]:

    compaction_index = -1
    #找到最近的compaction点
    for index in range(len(path_entries) - 1, -1, -1):
        if getattr(path_entries[index], "type", None) == "compaction":
            compaction_index = index
            break
    #如果没有压缩，直接返回全部消息
    if compaction_index == -1:
        return list(path_entries)
    #最新压缩内容
    compaction = path_entries[compaction_index]
    #需要保留的消息id
    first_kept_id = getattr(compaction, "first_kept_entry_id", None)
    #这里按照[compaction,first_kept_msg,rece_msg]组成新的agent context
    result: list[Any] = [compaction]
    if first_kept_id is not None:
        for i in range(compaction_index):
            if getattr(path_entries[i], "id", None) == first_kept_id:
                result.extend(path_entries[i:compaction_index])
                break

    result.extend(path_entries[compaction_index + 1 :])
    return result

def _entry_to_messages(entry: Any) -> list[Any]:
    """
    message         → [entry.message]
    compaction      → [CompactionSummaryMessage]
    branch_summary  → [BranchSummaryMessage]
    others          → []
    """
    etype = getattr(entry, "type", None)
    if etype == "message":
        msg = getattr(entry, "message", None)
        return [msg] if isinstance(msg, dict) else []
    if etype == "compaction":
        summary_msg = CompactionSummaryMessage(
            role="compactionSummary",
            summary=getattr(entry, "summary", "") or "",
            tokens_before=getattr(entry, "tokens_before", 0) or 0,
            timestamp=getattr(entry, "timestamp", 0) or 0,
        )
        return [summary_msg]
    if etype == "branch_summary":
        summary = getattr(entry, "summary", "") or ""
        if summary:
            return [
                BranchSummaryMessage(
                    role="branchSummary",
                    summary=summary,
                    from_id=getattr(entry, "from_id", "") or "",
                    timestamp=getattr(entry, "timestamp", 0) or 0,
                )
            ]
        return []
    return []


async def build_session_context(session: Any, lane: str = "main") -> SessionContext:
    """Build a SessionContext from a session, scoped to a single lane.
    """
    
    all_entries = await session.find_entries()
    path_entries = [
        e for e in all_entries
        if getattr(e, "lane", "main") == lane
    ]

    model, thinking_level, active_tool_names = _derive_state(path_entries)

    #构建上下文
    transformed = _default_context_entry_transform(path_entries)

    messages: list[Any] = []
    for entry in transformed:
        messages.extend(_entry_to_messages(entry))
        
    #返回session上下文
    return SessionContext(
        messages=messages,
        thinking_level=thinking_level,
        model=model,
        active_tool_names=active_tool_names,
    )