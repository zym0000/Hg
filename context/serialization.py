"""Serialization helpers for compaction summaries."""

from typing import Any

from agent.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_tail


_TOOL_RESULT_ROLES = frozenset({"tool", "toolResult"})

_TOOL_RESULT_MAX_LINES = DEFAULT_MAX_LINES
_TOOL_RESULT_MAX_BYTES = DEFAULT_MAX_BYTES


def _extract_text_content(content: Any) -> str:
    """Flatten message content to a single text string.

    - str → unchanged
    - list[dict{text,...}] → join text parts
    - anything else → str(content)
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if text:
                    parts.append(str(text))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def _maybe_truncate_tool_result(
    role: str,
    text: str,
    max_lines: int = _TOOL_RESULT_MAX_LINES,
    max_bytes: int = _TOOL_RESULT_MAX_BYTES,
) -> str:
    if role not in _TOOL_RESULT_ROLES:
        return text
    result = truncate_tail(text, max_lines=max_lines, max_bytes=max_bytes)
    if result.truncated:
        marker = f"... [truncated: kept last {result.output_lines} of {result.total_lines} lines, {result.output_bytes} of {result.total_bytes} bytes]"
        return f"{result.content}\n{marker}"
    return result.content


def serialize_conversation(
    messages: list[dict[str, Any]],
    tool_result_max_bytes: int = _TOOL_RESULT_MAX_BYTES,
    tool_result_max_lines: int = _TOOL_RESULT_MAX_LINES,
) -> str:
    
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        text = _extract_text_content(msg.get("content", ""))
        text = _maybe_truncate_tool_result(
            role, text,
            max_lines=tool_result_max_lines,
            max_bytes=tool_result_max_bytes,
        )
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def truncate_for_summary(text: str, max_chars: int) -> str:

    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
