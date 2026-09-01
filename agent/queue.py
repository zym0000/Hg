import uuid
from dataclasses import dataclass
from typing import Literal

from agent.message import UserMessage, AssistantMessage, ToolResultMessage, CustomMessage

AgentMessageT = UserMessage | AssistantMessage | ToolResultMessage | CustomMessage
QueueMode = Literal["all", "one-at-a-time"]

@dataclass
class PendingMessage:
    entry_id: str
    message: AgentMessageT


class PendingMessageQueue:
    def __init__(self, mode: QueueMode = "one-at-a-time") -> None:
        self._mode: QueueMode = mode
        self._messages: list[PendingMessage] = []

    @property
    def mode(self) -> QueueMode:
        return self._mode

    @mode.setter
    def mode(self, value: QueueMode) -> None:
        if value not in ("all", "one-at-a-time"):
            raise ValueError(f"Invalid QueueMode: {value}")
        self._mode = value

    def enqueue(self, message: AgentMessageT) -> str:
        entry_id = uuid.uuid4().hex
        self._messages.append(PendingMessage(entry_id=entry_id, message=message))
        return entry_id

    def has_items(self) -> bool:
        return len(self._messages) > 0

    def drain(self) -> list[AgentMessageT]:
        if not self._messages:
            return []
        if self._mode == "all":
            drained = [pm.message for pm in self._messages]
            self._messages = []
            return drained
        # one-at-a-time
        first = self._messages[0]
        self._messages = self._messages[1:]
        return [first.message]

    def clear(self) -> None:
        self._messages = []