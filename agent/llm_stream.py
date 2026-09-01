import json
import re
from typing import Any, AsyncIterator

from agent.llm_context import LLMContext
from agent.message import AssistantMessage, TextContent, ToolCallContent, Usage
from agent.tool.schema import make_tool_schema

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _make_assistant(
    model: Any,
    text: str,
    tool_calls: list[dict],
) -> AssistantMessage:
    content: list = []
    if text:
        content.append(TextContent(type="text", text=text))
    for tc in tool_calls:
        # arguments may be str (JSON) or dict — normalize to dict for dataclass.
        args_raw = tc.get("args", "")
        if isinstance(args_raw, str):
            try:
                args_dict = json.loads(args_raw) if args_raw else {}
            except json.JSONDecodeError:
                args_dict = {}
        elif isinstance(args_raw, dict):
            args_dict = args_raw
        else:
            args_dict = {}
        content.append(
            ToolCallContent(
                type="toolCall",
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=args_dict,
            )
        )
    return AssistantMessage(
        role="assistant",
        content=content,
        api="",
        provider="",
        model=str(model) if model is not None else "",
        usage=Usage(),
        stop_reason="stop",
        error_message=None,
        timestamp=0,
    )

#openai 协议方式，如果是其他模型，则需要换一种解析方式
def make_llm_stream_fn(llm_client: Any):

    async def stream_fn(
        model: Any, context: LLMContext, options: Any = None
    ) -> AsyncIterator[dict]:
        text_buf: list[str] = []
        tool_calls: list[dict] = []
        try:
            tool_schema = (
                [make_tool_schema(t) for t in context.tools]
                if context.tools else None
            )

            agen = llm_client.chat_stream(
                messages=context.messages,
                tool_schema=tool_schema,
                system_prompt=context.system_prompt,
            )
            async for item in agen:
                if isinstance(item, str):
                    text_buf.append(item)
                    yield {"type": "text_delta", "delta": item}
                else:
                    for tc in getattr(item, "tool_calls", None) or []:
                        tc_id = getattr(tc, "id", None) or tc.get("id", "")
                        fn = getattr(tc, "function", None) or tc.get("function", {})
                        tc_name = getattr(fn, "name", None) or fn.get("name", "")
                        tc_args = getattr(fn, "arguments", None) or fn.get("arguments", "")
                        tool_calls.append(
                            {"id": tc_id, "name": tc_name, "args": tc_args}
                        )
                        yield {
                            "type": "toolcall_delta",
                            "toolCallId": tc_id,
                            "toolName": tc_name,
                            "args": tc_args,
                        }
        except Exception as e:
            yield {"type": "error", "error_message": str(e)}
            return

        full_text = _THINK_RE.sub("", "".join(text_buf)).strip()

        done_msg = _make_assistant(model, full_text, tool_calls)
        yield {"type": "start", "partial": done_msg}
        yield {"type": "done", "message": done_msg}

    return stream_fn