from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LLMContext:
    messages: list[dict[str, Any]]
    tools: list[Any]  # list[AgentTool]; stream_fn converts to provider schemas
    system_prompt: str | None = None