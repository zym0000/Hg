"""dict ↔ AgentMessage conversion.

Storage writes dicts (jsonl-friendly). Agent state uses frozen dataclasses.
This module bridges the two.

 serializes AgentMessage via JSON.stringify; Python has no
equivalent (frozen dataclasses are not directly json-serializable), so
we convert to dict at write time and rehydrate at read time.
"""
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from agent.message import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
    UserMessage,
)

def _content_from_dict(c: Any) -> Any:
    if not isinstance(c, dict):
        return c
    t = c.get("type")
    if t == "text":
        return TextContent(type="text", text=c.get("text", ""))
    if t == "image":
        return ImageContent(
            type="image", data=c.get("data", ""),
            mime_type=c.get("mime_type"),
        )
    if t in ("toolCall", "tool_call"):
        args = c.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                args = {}
        return ToolCallContent(
            type="toolCall",
            id=c.get("id", ""),
            name=c.get("name", ""),
            arguments=args if isinstance(args, dict) else {},
        )
    return c


def _content_to_dict(c: Any) -> Any:
    if is_dataclass(c):
        return asdict(c)
    return c


def dict_to_agent_message(d: dict) -> Any:
    """Rehydrate a dict into an AgentMessage dataclass."""
    role = d.get("role")
    if role == "user":
        content = d.get("content", "")
        if isinstance(content, str):
            content_list = [TextContent(type="text", text=content)] if content else []
        else:
            content_list = [_content_from_dict(c) for c in content]
        return UserMessage(role="user", content=content_list, timestamp=d.get("timestamp", 0))

    if role == "assistant":
        text = d.get("content") or ""
        tool_calls = d.get("tool_calls") or []
        content_list: list = []
        if text:
            content_list.append(TextContent(type="text", text=text))
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            content_list.append(_content_from_dict({
                "type": "toolCall",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", ""),
            }))
        return AssistantMessage(
            role="assistant",
            content=content_list,
            api=d.get("api", "") or "",
            provider=d.get("provider", "") or "",
            model=d.get("model", "") or "",
            usage=Usage(),
            stop_reason=d.get("stop_reason", "stop") or "stop",
            error_message=d.get("error_message"),
            timestamp=d.get("timestamp", 0),
        )

    if role in ("tool", "toolResult"):
        raw_content = d.get("content", "")
        if isinstance(raw_content, str):
            content_list = [TextContent(type="text", text=raw_content)] if raw_content else []
        else:
            content_list = [_content_from_dict(c) for c in raw_content]
        return ToolResultMessage(
            tool_call_id=d.get("tool_call_id", ""),
            tool_name=d.get("tool_name", d.get("name", "")),
            content=content_list,
            is_error=bool(d.get("is_error", False)),
            timestamp=d.get("timestamp", 0),
        )

    raise ValueError(f"Unknown message role: {role!r}")


def agent_message_to_dict(msg: Any) -> dict:
    """Serialize an AgentMessage dataclass to a dict for storage."""
    d: dict[str, Any] = {"role": msg.role}

    if isinstance(msg, UserMessage):
        if len(msg.content) == 1 and isinstance(msg.content[0], TextContent):
            d["content"] = msg.content[0].text
        else:
            d["content"] = [_content_to_dict(c) for c in msg.content]
        if getattr(msg, "timestamp", 0):
            d["timestamp"] = msg.timestamp
        return d

    if isinstance(msg, AssistantMessage):
        text = ""
        tool_calls: list[dict] = []
        for c in msg.content:
            if isinstance(c, TextContent):
                text += c.text
            elif isinstance(c, ToolCallContent):
                tool_calls.append({
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments) if c.arguments else "",
                    },
                })
        d["content"] = text
        if tool_calls:
            d["tool_calls"] = tool_calls
        for k in ("api", "provider", "model", "stop_reason", "error_message", "timestamp"):
            v = getattr(msg, k, None)
            if v is not None and v != "":
                d[k] = v
        return d

    if isinstance(msg, ToolResultMessage):
        d["role"] = "tool"
        d["tool_call_id"] = msg.tool_call_id
        d["tool_name"] = msg.tool_name
        if len(msg.content) == 1 and isinstance(msg.content[0], TextContent):
            d["content"] = msg.content[0].text
        else:
            d["content"] = [_content_to_dict(c) for c in msg.content]
        if msg.is_error:
            d["is_error"] = True
        if getattr(msg, "timestamp", 0):
            d["timestamp"] = msg.timestamp
        return d

    # Fallback: asdict
    return asdict(msg)