from dataclasses import dataclass
from typing import Literal

from session.mutations import SessionMutation

@dataclass(frozen=True)
class BranchForkOptions:
    scope: Literal["branch"] | None = None
    entry_id: str | None = None
    position: Literal["at", "before"] | None = None
    id: str | None = None
    parent_session_id: str | None = None

@dataclass(frozen=True)
class TreeForkOptions:
    scope: Literal["tree"] = "tree"
    id: str | None = None
    parent_session_id: str | None = None

ForkOptions = BranchForkOptions | TreeForkOptions

def compute_fork_mutations(state, options: ForkOptions) -> list[SessionMutation]:
    return state.create_fork_mutations(options)
