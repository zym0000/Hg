import os
from dataclasses import dataclass, field
from typing import Any

from session.errors import CompactionError, CompactionErrorKind

@dataclass(frozen=True)
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000

    @classmethod
    def from_env(cls, prefix: str = "AGENT_COMPACTION_") -> "CompactionSettings":
        def _bool(name: str, default: bool) -> bool:
            raw = os.environ.get(prefix + name)
            if raw is None:
                return default
            lowered = raw.strip().lower()
            if lowered in ("true", "1", "yes", "on"):
                return True
            if lowered in ("false", "0", "no", "off"):
                return False
            return default

        def _int(name: str, default: int) -> int:
            raw = os.environ.get(prefix + name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return cls(
            enabled=_bool("ENABLED", True),
            reserve_tokens=_int("RESERVE_TOKENS", 16384),
            keep_recent_tokens=_int("KEEP_RECENT_TOKENS", 20000),
        )


@dataclass(frozen=True)
class CompactionDetails:
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    details: CompactionDetails
    tokens_before: int
    usage: dict[str, Any] | None = None


@dataclass(frozen=True)
class CutPointResult:
    first_kept_entry_index: int
    turn_start_index: int = -1
    is_split_turn: bool = False
    first_kept_entry_id: str | None = None


@dataclass(frozen=True)
class CompactionPreparation:
    messages_to_summarize: list[dict[str, Any]]
    turn_prefix_messages: list[dict[str, Any]]
    first_kept_entry_id: str | None
    is_split_turn: bool
    tokens_before: int
    previous_summary: str | None
    file_ops: CompactionDetails
    settings: CompactionSettings
    turn_start_entry_id: str | None = None

_CUTTABLE_ROLES = frozenset({
    "user",
    "assistant",
    "bashExecution",
    "custom",
    "branchSummary",
    "compactionSummary",
})


def should_compact(
    context_tokens: int,
    context_window: int,
    settings: "CompactionSettings",
) -> bool:
    if not settings.enabled:
        return False
    return context_tokens > context_window - settings.reserve_tokens


OVERFLOW_PATTERNS: tuple[str, ...] = (
    r"prompt is too long",
    r"request_too_large",
    r"input is too long for requested model",
    r"exceeds the context window",
    r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d]+ tokens?|\s*\([\d]+\))",
    r"input token count.*exceeds the maximum",
    r"maximum prompt length is \d+",
    r"reduce the length of the messages",
    r"maximum context length is \d+ tokens",
    r"exceeds (?:the )?maximum allowed input length of [\d]+ tokens?",
    r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)",
    r"exceeds the limit of \d+",
    r"exceeds the available context size",
    r"greater than the context length",
    r"context window exceeds limit",
    r"exceeded model token limit",
    r"too large for model with \d+ maximum context length",
    r"prompt has [\d]+ tokens?, but the configured context size is [\d]+ tokens?",
    r"model_context_window_exceeded",
    r"prompt too long; exceeded (?:max )?context length",
    r"range of input length should be",
    r"context[_ ]length[_ ]exceeded",
    r"too many tokens",
    r"token limit exceeded",
    r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)",
)

NON_OVERFLOW_PATTERNS: tuple[str, ...] = (
    r"^(Throttling error|Service unavailable):",
    r"rate limit",
    r"too many requests",
)


import re

_OVERFLOW_RE_LIST = [re.compile(p, re.IGNORECASE) for p in OVERFLOW_PATTERNS]
_NON_OVERFLOW_RE_LIST = [re.compile(p, re.IGNORECASE) for p in NON_OVERFLOW_PATTERNS]


def calculate_context_tokens(usage: Any) -> int:
    """: prefer usage.total_tokens, fall back to input+output+cache."""
    if usage is None:
        return 0
    total = getattr(usage, "total_tokens", None)
    if total:
        try:
            return int(total)
        except (TypeError, ValueError):
            pass
    return (
        int(getattr(usage, "input", 0) or 0)
        + int(getattr(usage, "output", 0) or 0)
        + int(getattr(usage, "cache_read", 0) or 0)
        + int(getattr(usage, "cache_write", 0) or 0)
    )


def is_context_overflow(message: Any, context_window: int | None = None) -> bool:
    if message is None:
        return False
    stop_reason = getattr(message, "stop_reason", None)
    error_message = getattr(message, "error_message", None)
    usage = getattr(message, "usage", None)

    # error_message regex match
    if stop_reason == "error" and error_message:
        is_non_overflow = any(
            p.search(error_message) for p in _NON_OVERFLOW_RE_LIST
        )
        if not is_non_overflow and any(
            p.search(error_message) for p in _OVERFLOW_RE_LIST
        ):
            return True

    # Tier 2 and 3 require a context window.
    if not context_window or context_window <= 0:
        return False

    input_tokens = (
        int(getattr(usage, "input", 0) or 0) if usage is not None else 0
    )
    cache_read = (
        int(getattr(usage, "cache_read", 0) or 0) if usage is not None else 0
    )

    # silent overflow
    if stop_reason == "stop":
        if input_tokens + cache_read > context_window:
            return True

    # length-stop overflow
    if stop_reason == "length" and usage is not None:
        output_tokens = int(getattr(usage, "output", 0) or 0)
        if output_tokens == 0 and input_tokens + cache_read >= context_window * 0.99:
            return True

    return False


def is_recoverable_length(
    message: Any, desired_max_output: int | None
) -> bool:
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason != "length":
        return False
    if not desired_max_output or desired_max_output <= 0:
        return False
    usage = getattr(message, "usage", None)
    if usage is None:
        return False
    output = int(getattr(usage, "output", 0) or 0)
    return output < desired_max_output


def _is_candidate_cut(entry: Any) -> bool:
    etype = entry.get("type") if isinstance(entry, dict) else getattr(entry, "type", None)
    if etype != "message":
        return False
    msg = entry.get("message") if isinstance(entry, dict) else getattr(entry, "message", None)
    if not isinstance(msg, dict):
        return False
    role = msg.get("role")
    return role in _CUTTABLE_ROLES

def _entry_type(e: Any) -> str | None:
    if isinstance(e, dict):
        v = e.get("type")
        return v if isinstance(v, str) else None
    return getattr(e, "type", None)

def _entry_message_dict(e: Any) -> dict[str, Any] | None:
    if _entry_type(e) != "message":
        return None
    if isinstance(e, dict):
        m = e.get("message")
        return m if isinstance(m, dict) else None
    m = getattr(e, "message", None)
    return m if isinstance(m, dict) else None


def _entry_id(e: Any) -> str | None:
    if isinstance(e, dict):
        v = e.get("id")
        return v if isinstance(v, str) else None
    v = getattr(e, "id", None)
    return v if isinstance(v, str) else None


async def find_cut_point(
    entries: list[Any],
    start_index: int,
    end_index: int,
    keep_recent_tokens: int,
    estimator: Any,
) -> CutPointResult:

    # 这里是获取哪些entries可以被切分的
    cut_points = [i for i in range(start_index, end_index) if _is_candidate_cut(entries[i])]

    # 如果都是不能被切，那么kept_enter_index = start_index
    if not cut_points:
        return CutPointResult(first_kept_entry_index=start_index)

    cut_index = cut_points[0]

    # 这里保留最近的keep_recent_tokens entries
    accumulated_tokens = 0
    for i in range(end_index - 1, start_index - 1, -1):
        entry = entries[i]
        msg = _entry_message_dict(entry)
        if msg is None:
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                part.get("text", "") for part in content
                if isinstance(part, dict)
            )
        msg_tokens = estimator.estimate_text(content) + 4
        accumulated_tokens += msg_tokens
        if accumulated_tokens >= keep_recent_tokens:
            for c in cut_points:
                if c >= i:
                    cut_index = c
                    break
            break

    # 这里是保证切分分割点的完整性，如果分割点是tool assistant 需要往前找message,避免数据不完整
    while cut_index > start_index:
        prev = entries[cut_index - 1]
        prev_type = _entry_type(prev)
        if prev_type in ("message", "compaction", "branch_summary"):
            break
        cut_index -= 1

    cut_entry = entries[cut_index]
    cut_msg = _entry_message_dict(cut_entry)
    is_user_message = (
        cut_msg is not None and cut_msg.get("role") == "user"
    )

    # 如果保留了LLM 完整思考的上下文，那么做摘要的时候，需要告诉LLM完整的上下文
    turn_start_index = -1
    is_split_turn = False
    if not is_user_message:
        for j in range(cut_index - 1, start_index - 1, -1):
            e = entries[j]
            e_type = _entry_type(e)
            if e_type == "branch_summary":
                turn_start_index = j
                break
            if e_type != "message":
                continue
            msg = _entry_message_dict(e)
            if msg is None:
                continue
            role = msg.get("role")
            if role in ("user", "bashExecution", "custom"):
                turn_start_index = j
                break
        is_split_turn = turn_start_index != -1

    first_kept_entry_id = _entry_id(entries[cut_index])

    return CutPointResult(
        first_kept_entry_index=cut_index,
        turn_start_index=turn_start_index,
        is_split_turn=is_split_turn,
        first_kept_entry_id=first_kept_entry_id,
    )

def _entry_msg_tokens(e: Any, estimator: Any) -> int:
    msg = _entry_message_dict(e)
    if msg is None:
        return 0
    content = msg.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return estimator.estimate_text(content) + 4


def _estimate_live_view_tokens(entries: list[Any], estimator: Any) -> int:

    if not entries:
        return 0

    compaction_index = -1
    for i in range(len(entries) - 1, -1, -1):
        if _entry_type(entries[i]) == "compaction":
            compaction_index = i
            break


    if compaction_index == -1:
        return sum(
            _entry_msg_tokens(e, estimator)
            for e in entries
            if _entry_type(e) == "message"
        )

    # 如果之前产生压缩，那么数据就是 压缩+kept_entry+ rece_entry
    compaction = entries[compaction_index]
    first_kept_id = getattr(compaction, "first_kept_entry_id", None)
    selected = [compaction]
    if first_kept_id is not None:
        for i in range(compaction_index):
            if _entry_id(entries[i]) == first_kept_id:
                selected.extend(entries[i:compaction_index])
                break
    selected.extend(entries[compaction_index + 1 :])

    return sum(
        _entry_msg_tokens(e, estimator)
        for e in selected
        if _entry_type(e) == "message"
    )

async def prepare_compaction(
    entries: list[Any],
    settings: CompactionSettings,
    estimator: Any,
) -> CompactionPreparation:


    empty = CompactionPreparation(
        messages_to_summarize=[],
        turn_prefix_messages=[],
        first_kept_entry_id=None,
        is_split_turn=False,
        tokens_before=0,
        previous_summary=None,
        file_ops=CompactionDetails(),
        settings=settings,
        turn_start_entry_id=None,
    )

    if not entries:
        return empty


    if _entry_type(entries[-1]) == "compaction":
        return empty


    prev_compaction_index = -1
    for i in range(len(entries) - 1, -1, -1):
        if _entry_type(entries[i]) == "compaction":
            prev_compaction_index = i
            break


    previous_summary: str | None = None
    prev_file_ops = CompactionDetails()

    if prev_compaction_index >= 0:
        prev = entries[prev_compaction_index]

        if isinstance(prev, dict):
            previous_summary = prev.get("summary")
            prev_details = prev.get("details")
        else:
            previous_summary = getattr(prev, "summary", None)
            prev_details = getattr(prev, "details", None)

        if isinstance(prev_details, CompactionDetails):
            prev_file_ops = prev_details


    post_entries: list[Any] = list(entries[prev_compaction_index + 1 :])

    # 计算未压缩前的token 总量
    tokens_before = _estimate_live_view_tokens(entries, estimator)


    cut = await find_cut_point(
        post_entries,
        start_index=0,
        end_index=len(post_entries),
        keep_recent_tokens=settings.keep_recent_tokens,
        estimator=estimator,
    )

    history_end = (
        cut.turn_start_index if cut.is_split_turn else cut.first_kept_entry_index
    )

    def _entry_msg_at(index: int) -> dict[str, Any] | None:
        return _entry_message_dict(post_entries[index])

    messages_to_summarize: list[dict[str, Any]] = []
    for i in range(history_end):
        m = _entry_msg_at(i)
        if m is not None:
            messages_to_summarize.append(m)

    turn_prefix_messages: list[dict[str, Any]] = []
    turn_start_entry_id: str | None = None
    if cut.is_split_turn:
        for i in range(cut.turn_start_index, cut.first_kept_entry_index):
            m = _entry_msg_at(i)
            if m is not None:
                turn_prefix_messages.append(m)
        real_turn_start = prev_compaction_index + 1 + cut.turn_start_index
        if 0 <= real_turn_start < len(entries):
            turn_start_entry_id = _entry_id(entries[real_turn_start])

    return CompactionPreparation(
        messages_to_summarize=messages_to_summarize,
        turn_prefix_messages=turn_prefix_messages,
        first_kept_entry_id=cut.first_kept_entry_id,
        is_split_turn=cut.is_split_turn,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
        file_ops=prev_file_ops,
        settings=settings,
        turn_start_entry_id=turn_start_entry_id,
    )


SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a user and an AI assistant, then produce a "
    "structured summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in "
    "the conversation. ONLY output the structured summary."
)

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a highly concise structured checkpoint that another LLM will use to continue the work. Prioritize brevity. Omit any section that has no content.

Use this EXACT format:

## Goal
(What is the user trying to accomplish? List multiple if multi-part.)

## Constraints
(Bullet list of user rules/requirements. Omit if none.)

## Progress
### Done
(- [x] Completed tasks. Omit if none.)
### In Progress
(- [ ] Current work. Omit if none.)
### Blocked
(- Blockers. Omit if none.)

## Key Decisions
(- Decision: rationale. Omit if none.)

## Next Steps
(Numbered list, 1-3 items. Omit if none.)

## Critical Context
(Exact paths, IDs, errors. CRITICAL: List READ/MODIFIED files on separate lines:
 READ: <path>
 MODIFIED: <path>
Omit this entire section if no such context exists.)

Keep each bullet point short. Preserve exact file paths, function names, and error messages verbatim.
"""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages. Update the EXISTING summary provided in the <previous-summary> tags above.

RULES:
- PRESERVE all existing information unless new messages make it obsolete.
- Move items from "In Progress" to "Done" when completed in new messages.
- Update "Next Steps" and "Blocked" based on latest state.
- If something is no longer relevant, you may remove it.
- Omit any section that has no content.

Use this EXACT format (same as initial summary):

## Goal
[Preserve existing, add new if expanded]

## Constraints
[Preserve existing, add new]

## Progress
### Done
[- [x] Include previously done AND newly completed]
### In Progress
[- [ ] Update based on current state]
### Blocked
[- Current blockers, remove if resolved]

## Key Decisions
[- Preserve all previous, add new]

## Next Steps
[Update based on current state]

## Critical Context
[Preserve important context, add new if needed. Format for files: READ: <path> / MODIFIED: <path>]

Keep each bullet point short. Preserve exact file paths, function names, and error messages verbatim.
"""

TURN_PREFIX_SUMMARIZATION_PROMPT = """Summarize the early part of a conversation whose suffix has been kept verbatim.
Use exactly these sections. The kept suffix follows; this summary is for
context only.

## Original Request
[What the user originally asked]

## Early Progress
[Steps taken in the early turns that the kept suffix depends on]

## Context for Suffix
[Anything the kept suffix needs to understand]

Be concise. Focus on what's needed to understand the kept suffix.
"""

import asyncio as _asyncio
import uuid as _uuid

async def generate_summary_with_usage(
    messages: list[dict[str, Any]],
    previous_summary: str | None,
    llm_client: Any,
    settings: CompactionSettings,
    signal: Any | None = None,
    timeout: float = 120.0,
    cache_break: bool = True,
    run_id: str | None = None,
    prompt_override: str | None = None,
) -> CompactionResult:

    # Abort check before starting.
    if signal is not None and getattr(signal, "aborted", False):
        raise CompactionError(
            kind=CompactionErrorKind.ABORTED,
            message="aborted before summarization",
            run_id=run_id,
        )

    if prompt_override is not None:
        base_prompt = prompt_override
    else:
        base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT

    from context.serialization import serialize_conversation
    from agent.convert import convert_to_llm
    llm_message = convert_to_llm(messages)
    conversation_text = serialize_conversation(llm_message) if messages else "(empty)"

    prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
    if prompt_override is None and previous_summary:
        prompt_text += f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
    prompt_text += base_prompt

    summarization_messages = [{"role": "user", "content": prompt_text}]

    kwargs: dict[str, Any] = {}
    if cache_break:
        kwargs.setdefault("metadata", {})["cache_session_id"] = (
            f"summary-{_uuid.uuid4().hex[:12]}"
        )

    try:
        response = await _asyncio.wait_for(
            llm_client.chat(summarization_messages, **kwargs),
            timeout=timeout,
        )
    except _asyncio.TimeoutError as exc:
        raise CompactionError(
            kind=CompactionErrorKind.SUMMARIZATION_FAILED,
            message=f"LLM timed out after {timeout}s",
            run_id=run_id,
            cause=exc,
        ) from exc

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason is None and isinstance(response, dict):
        stop_reason = response.get("stop_reason")
    text = getattr(response, "text", None)
    if text is None:
        text = response if isinstance(response, str) else str(response)
    text = (text or "").strip()
    usage = getattr(response, "usage", None)

    if stop_reason == "aborted":
        raise CompactionError(
            kind=CompactionErrorKind.ABORTED,
            message="summarization aborted by LLM",
            run_id=run_id,
        )
    if stop_reason == "error":
        raise CompactionError(
            kind=CompactionErrorKind.SUMMARIZATION_FAILED,
            message=f"summarization error: {text or 'unknown'}",
            run_id=run_id,
        )
    if not text:
        raise CompactionError(
            kind=CompactionErrorKind.SUMMARIZATION_FAILED,
            message="empty summarization response",
            run_id=run_id,
        )

    # Parse Critical Context for READ:/MODIFIED: lines.
    details = await extract_file_operations(text, previous_details=None)

    # tokens_before: rough estimate of the input messages.
    from context.token_estimator import TokenEstimator
    tokens_before = TokenEstimator().estimate_message(messages)

    return CompactionResult(
        summary=text,
        details=details,
        tokens_before=tokens_before,
        usage=dict(usage) if usage else None,
    )

import re

_READ_RE = re.compile(r"^\s*READ:\s*(\S+)\s*$", re.MULTILINE)
_MODIFIED_RE = re.compile(r"^\s*MODIFIED:\s*(\S+)\s*$", re.MULTILINE)


async def extract_file_operations(
    summary_text: str,
    previous_details: CompactionDetails | None,
) -> CompactionDetails:

    reads = _READ_RE.findall(summary_text or "")
    mods = _MODIFIED_RE.findall(summary_text or "")

    prev_reads: list[str] = list(previous_details.read_files) if previous_details else []
    prev_mods: list[str] = list(previous_details.modified_files) if previous_details else []

    # Deduped union, preserving first-seen order.
    def _union(a: list[str], b: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for p in list(a) + list(b):
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    return CompactionDetails(
        read_files=_union(prev_reads, reads),
        modified_files=_union(prev_mods, mods),
    )

