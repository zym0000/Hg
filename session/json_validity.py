from __future__ import annotations

import math
from typing import Any

from session.errors import SessionError, SessionErrorKind


def _invalid_payload(reason: str) -> None:
    raise SessionError(SessionErrorKind.INVALID_PAYLOAD, f"Durable payload {reason}")


def assert_json_serializable(value: Any) -> None:
    active: set[int] = set()
    # (object, is_exit) — push entry nodes with False, exit sentinels with True
    # so we can pop the active-set membership before returning up the tree.
    stack: list[tuple[Any, bool]] = [(value, False)]
    while stack:
        obj, is_exit = stack.pop()
        if is_exit:
            active.discard(id(obj))
            continue
        if obj is None or isinstance(obj, (str, bool)):
            continue
        if isinstance(obj, int):
            # bool is a subclass of int; already handled above.
            continue
        if isinstance(obj, float):
            # NaN: NaN != NaN. +inf / -inf compare equal to themselves.
            if obj != obj or obj == math.inf or obj == -math.inf:
                _invalid_payload("contains a non-finite number")
            continue
        if not isinstance(obj, (dict, list, tuple)):
            _invalid_payload(f"contains {type(obj).__name__}")
        if id(obj) in active:
            _invalid_payload("contains a cycle")
        active.add(id(obj))
        stack.append((obj, True))
        if isinstance(obj, dict):
            for k, v in obj.items():
                if not isinstance(k, str):
                    _invalid_payload("has a non-string key")
                stack.append((v, False))
        else:  # list or tuple
            for v in obj:
                stack.append((v, False))
