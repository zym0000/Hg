"""SessionStorage Protocol.

Defines the storage contract for Session persistence. Backends:
- InMemorySessionStorage (test default; )
- JsonlSessionStorage (v1 production default; )

All methods are async. `query` parameters are dicts; backend may interpret
keys it understands and ignore the rest (forward compat).
"""

from typing import Any, Protocol

from session.types import (
    LanePointer,
    SessionMetadata,
    SessionStats,
)


class SessionStorage(Protocol):
    async def get_metadata(self) -> SessionMetadata:
        ...

    async def get_name(self) -> str | None:
        ...

    async def set_name(self, name: str | None) -> None:
        ...

    async def get_lanes(self) -> list[LanePointer]:
        ...

    async def create_lane(self, name: str, leaf_id: str) -> None:
        ...

    async def move_lane(self, name: str, leaf_id: str) -> None:
        ...

    async def append_entry(self, entry: Any, lane: str) -> Any:
        """Assigns id/parent_id/seq/timestamp; returns the entry with those fields populated."""
        ...

    async def find_entries(self, query: dict[str, Any] | None = None) -> list[Any]:
        ...

    async def find_entries_on_branch(
        self, query: dict[str, Any] | None = None, start: str | None = None,
    ) -> list[Any]:
        ...

    async def get_entry(self, entry_id: str) -> Any | None:
        ...

    async def append_record(self, record: Any) -> Any:
        ...

    async def find_records(self, query: dict[str, Any] | None = None) -> list[Any]:
        ...

    async def find_open_operations(self, lane: str | None = None) -> list[Any]:
        """Return operation_started records without matching operation_finished.

 lane=None returns all open operations across all lanes.
 lane="main" returns only main-lane operations.
"""
        ...

    async def get_label(self, entry_id: str) -> str | None:
        ...

    async def set_label(self, entry_id: str, label: str) -> None:
        ...

    async def get_stats(self) -> SessionStats:
        ...

    async def get_log(self, options: dict[str, Any] | None = None) -> list[Any]:
        ...

    async def fork(
        self,
        metadata: "SessionMetadata",
        options: "ForkOptions & SessionCreateOptions",
    ) -> "Self":
        """Create a new storage instance whose state is derived from this one.

 Returns a new storage of the same backend kind (InMemory or Jsonl), with
 state initialized by replaying compute_fork_mutations(self._state, options).
"""
        ...