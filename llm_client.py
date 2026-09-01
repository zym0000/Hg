import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import openai


_DEBUG_LLM = os.getenv("HARNESS_DEBUG_LLM") == "1"


def _with_system_prompt(
    messages: List[Dict[str, Any]],
    system_prompt: Optional[str],
) -> List[Dict[str, Any]]:
    if not system_prompt:
        return messages
    if messages and messages[0].get("role") == "system":
        return [{"role": "system", "content": system_prompt}, *messages[1:]]
    return [{"role": "system", "content": system_prompt}, *messages]

@dataclass
class ChatResponse:
    text: str = ""
    usage: Optional[Dict[str, int]] = None   # token 用量 {"prompt_tokens": ..., "completion_tokens": ...}
    tool_calls: Optional[List[Dict]] = None  # 原生 tool_calls 列表
    reasoning_content:Optional[str] = None   # 兼容deepseek 工具调用，要求如果有返回需要带

class LLMClient:
    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

        # 使用 openai 库的异步客户端
        self._client = openai.AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL"),
            timeout=timeout,
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tool_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> ChatResponse:
        msgs = _with_system_prompt(messages, system_prompt)
        kwargs = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tool_schema:
            kwargs["tools"] = tool_schema

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise RuntimeError(f"LLM call failed: {e}") from e

        choice = response.choices[0]
        text = choice.message.content or ""
        reasoning = getattr(choice.message,"reasoning_content",None)

        # 提取 tool_calls
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatResponse(text=text, usage=usage, tool_calls=tool_calls,reasoning_content=reasoning)

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tool_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[Union[str, ChatResponse]]:
        
        msgs = _with_system_prompt(messages, system_prompt)
        kwargs = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tool_schema:
            kwargs["tools"] = tool_schema

        if _DEBUG_LLM:
            print(
                f"[DEBUG_LLM] >>> chat_stream kwargs:\n"
                f"  model={self.model}\n"
                f"  messages_count={len(msgs)}\n"
                f"  tools_count={len(tool_schema) if tool_schema else 0}\n"
                f"  messages=\n{json.dumps(msgs, ensure_ascii=False, indent=2)}",
                flush=True,
            )

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            if _DEBUG_LLM:
                print(f"[DEBUG_LLM] !!! error: {e}", flush=True)
            raise RuntimeError(f"LLM stream call failed: {e}") from e

        collected_text = ""
        collected_tool_calls: List[Dict[str, Any]] = []
        final_usage = None
        collected_reasoning = ""

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue


            if delta.content:
                collected_text += delta.content
                yield delta.content

            if getattr(delta,'reasoning_content',None):
                collected_reasoning += delta.reasoning_content


            if delta.tool_calls:
                for tc_delta in delta.tool_calls:

                    while len(collected_tool_calls) <= tc_delta.index:
                        collected_tool_calls.append({
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                    if tc_delta.id:
                        collected_tool_calls[tc_delta.index]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            collected_tool_calls[tc_delta.index]["function"]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            collected_tool_calls[tc_delta.index]["function"]["arguments"] += tc_delta.function.arguments

            # 最后一个 chunk 通常带有 usage
            if chunk.usage:
                final_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }


        final_response = ChatResponse(
            text=collected_text,                # 完整文本（可能被中间 yield 拆散了，这里汇总备用）
            usage=final_usage,
            tool_calls=collected_tool_calls if collected_tool_calls else None,
            reasoning_content= collected_reasoning if collected_reasoning else None
        )
        yield final_response

    async def aclose(self) -> None:
        """关闭底层 httpx 连接池。"""
        client = getattr(self, "_client", None)
        if client is None:
            return
        if hasattr(client, "aclose"):
            await client.aclose()
        elif hasattr(client, "close"):
            await asyncio.to_thread(client.close)
