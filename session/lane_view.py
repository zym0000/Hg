from typing import Any

from session.session import Session


class LaneView:
    def __init__(self, session: Session, lane: str) -> None:
        self._session = session
        self._lane = lane

    async def append_message(self, message: dict, lane: str | None = None) -> str:
        return await self._session.append_message(message, lane=lane or self._lane)

    async def append_entry(self, entry: Any, lane: str | None = None) -> Any:
        return await self._session.append_entry(entry, lane=lane or self._lane)

    async def append_custom_entry(self, custom_type: str, data: Any, lane: str | None = None) -> str:
        return await self._session.append_custom_entry(custom_type, data, lane=lane or self._lane)

    async def find_entries(self, query: dict | None = None) -> list[Any]:
        all_entries = await self._session.find_entries(query)
        return [e for e in all_entries if getattr(e, "lane", None) == self._lane]

    async def find_entries_on_branch(self, query: dict | None = None, start: str | None = None) -> list[Any]:
        return await self._session.find_entries_on_branch(query, start)

    async def find_open_operations(self) -> list[Any]:
        return await self._session.find_open_operations(lane=self._lane)

    def get_lane_name(self) -> str:
        return self._lane

    @property
    def session(self) -> Session:
        return self._session
