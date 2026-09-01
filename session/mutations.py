from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class SessionMutation:
    kind: Literal["entry", "record", "lane", "fact"]
    fact_kind: Literal["name", "label"] | None = None
    # EntryMutation fields
    entry: dict[str, Any] | None = None
    lane: str | None = None
    # RecordMutation fields
    record: dict[str, Any] | None = None
    # LaneMutation fields
    seq: int | None = None
    leaf_id: str | None = None
    # NameFactMutation fields
    name: str | None = None
    # LabelFactMutation fields
    target_id: str | None = None
    label: str | None = None


def entry_mutation(*, entry: dict[str, Any], lane: str | None = None) -> SessionMutation:
    return SessionMutation(kind="entry", entry=entry, lane=lane)


def record_mutation(*, record: dict[str, Any]) -> SessionMutation:
    return SessionMutation(kind="record", record=record)


def lane_mutation(*, seq: int, lane: str, leaf_id: str | None) -> SessionMutation:
    return SessionMutation(kind="lane", seq=seq, lane=lane, leaf_id=leaf_id)


def name_fact_mutation(*, seq: int, name: str | None) -> SessionMutation:
    return SessionMutation(kind="fact", fact_kind="name", seq=seq, name=name)


def label_fact_mutation(*, seq: int, target_id: str, label: str | None) -> SessionMutation:
    return SessionMutation(
        kind="fact", fact_kind="label", seq=seq, target_id=target_id, label=label,
    )
