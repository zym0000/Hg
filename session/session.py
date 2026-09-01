from typing import Any

from session.storage.base import SessionStorage
from session.types import LanePointer, SessionMetadata, SessionStats


class Session:
    def __init__(self, storage: SessionStorage) -> None:
        self.storage = storage

    @property
    def cwd(self) -> str | None:
        md = getattr(self.storage, "_metadata", None)
        if md is None:
            return None
        return getattr(md, "cwd", None)

    async def get_metadata(self) -> SessionMetadata:
        return await self.storage.get_metadata()

    async def get_name(self) -> str | None:
        return await self.storage.get_name()

    async def set_name(self, name: str | None) -> None:
        await self.storage.set_name(name)

    async def get_label(self, entry_id: str) -> str | None:
        return await self.storage.get_label(entry_id)

    async def set_label(self, entry_id: str, label: str) -> None:
        await self.storage.set_label(entry_id, label)

    async def get_log(self, options: dict[str, Any] | None = None) -> list[Any]:
        return await self.storage.get_log(options)

    async def get_lanes(self) -> list[LanePointer]:
        return await self.storage.get_lanes()

    async def create_lane(self, name: str, leaf_id: str) -> None:
        await self.storage.create_lane(name, leaf_id)

    async def move_lane(self, name: str, leaf_id: str) -> None:
        await self.storage.move_lane(name, leaf_id)

    def view(self, lane: str) -> Any:
        from session.lane_view import LaneView
        return LaneView(self, lane)

    async def get_entry(self, entry_id: str) -> Any | None:
        return await self.storage.get_entry(entry_id)

    async def find_entries(self, query: dict[str, Any] | None = None) -> list[Any]:
        return await self.storage.find_entries(query)

    async def find_entries_on_branch(
        self, query: dict[str, Any] | None = None, start: str | None = None,
    ) -> list[Any]:
        return await self.storage.find_entries_on_branch(query, start)

    async def append_message(self, message: dict[str, Any], lane: str = "main") -> str:
        from session.types import MessageEntry
        entry = await self.storage.append_entry(MessageEntry(type="message", message=message), lane)
        return entry.id

    async def append_custom_entry(self, custom_type: str, data: Any, lane: str = "main") -> str:
        from session.types import CustomEntry
        entry = await self.storage.append_entry(CustomEntry(type="custom", custom_type=custom_type, data=data), lane)
        return entry.id

    async def append_entry(self, entry: Any, lane: str = "main") -> Any:
        return await self.storage.append_entry(entry, lane)

    async def find_records(self, query: dict[str, Any] | None = None) -> list[Any]:
        return await self.storage.find_records(query)

    async def append_record(self, record: Any, lane: str | None = None) -> Any:
        return await self.storage.append_record(record, lane=lane)

    async def find_open_operations(self, lane: str | None = None) -> list[Any]:
        return await self.storage.find_open_operations(lane)

    async def get_stats(self) -> SessionStats:
        return await self.storage.get_stats()