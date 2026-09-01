import json
from typing import Any

from agent.message import (
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

AgentMessage = (
    UserMessage
    | AssistantMessage
    | ToolResultMessage
    | CustomMessage
    | CompactionSummaryMessage
    | BranchSummaryMessage
)

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"

BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "\n</summary>"


def _content_blocks_to_text(blocks: list[TextContent | ImageContent | ThinkingContent | ToolCallContent]) -> str:
    """Concatenate text + thinking blocks; ignore tool calls (handled separately)."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, (TextContent, ThinkingContent)):
            parts.append(block.text)
    return "".join(parts)


def _content_blocks_to_openai_list(blocks: list[Any]) -> list[dict[str, Any]]:
    """For user messages with mixed text + image content: emit OpenAI array shape."""
    out: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextContent):
            out.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            out.append({"type": "image", "data": block.data, **({"mime_type": block.mime_type} if block.mime_type else {})})
    return out


def _user_to_dict(msg: UserMessage) -> dict[str, Any]:
    has_image = any(isinstance(b, ImageContent) for b in msg.content)
    if has_image or len(msg.content) != 1 or not isinstance(msg.content[0], TextContent):
        return {"role": "user", "content": _content_blocks_to_openai_list(msg.content)}
    return {"role": "user", "content": msg.content[0].text}


def _assistant_to_dict(msg: AssistantMessage) -> dict[str, Any]:
    text = _content_blocks_to_text(msg.content)
    tool_calls: list[dict[str, Any]] | None = None
    for block in msg.content:
        if isinstance(block, ToolCallContent):
            if tool_calls is None:
                tool_calls = []
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.arguments),
                    },
                }
            )
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": tool_calls,
    }


def _tool_result_to_dict(msg: ToolResultMessage) -> dict[str, Any]:
    text = "".join(b.text for b in msg.content if isinstance(b, TextContent))
    return {
        "role": "tool",
        "tool_call_id": msg.tool_call_id,
        "content": text,
    }


def _custom_to_dict(msg: CustomMessage) -> dict[str, Any]:
    if isinstance(msg.content, str):
        return {"role": "user", "content": msg.content}
    text = "".join(b.text for b in msg.content if isinstance(b, TextContent))
    return {"role": "user", "content": text}


def _compaction_summary_to_dict(msg: CompactionSummaryMessage) -> dict[str, Any]:
    body = f"{COMPACTION_SUMMARY_PREFIX}{msg.summary}{COMPACTION_SUMMARY_SUFFIX}"
    return {
        "role": "user",
        "content": [{"type": "text", "text": body}],
    }


def _branch_summary_to_dict(msg: BranchSummaryMessage) -> dict[str, Any]:
    body = f"{BRANCH_SUMMARY_PREFIX}{msg.summary}{BRANCH_SUMMARY_SUFFIX}"
    return {
        "role": "user",
        "content": [{"type": "text", "text": body}],
    }


def convert_to_llm(messages: list[AgentMessage | dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            out.append(msg)
        elif isinstance(msg, UserMessage):
            out.append(_user_to_dict(msg))
        elif isinstance(msg, AssistantMessage):
            out.append(_assistant_to_dict(msg))
        elif isinstance(msg, ToolResultMessage):
            out.append(_tool_result_to_dict(msg))
        elif isinstance(msg, CustomMessage):
            out.append(_custom_to_dict(msg))
        elif isinstance(msg, CompactionSummaryMessage):
            out.append(_compaction_summary_to_dict(msg))
        elif isinstance(msg, BranchSummaryMessage):
            out.append(_branch_summary_to_dict(msg))
        else:
            raise TypeError(f"Unknown AgentMessage type: {type(msg).__name__}")
    return out