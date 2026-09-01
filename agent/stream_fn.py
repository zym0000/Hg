from typing import AsyncIterator, Callable

_default_stream_fn: Callable[..., AsyncIterator[dict]] | None = None

def set_default_stream_fn(fn: Callable[..., AsyncIterator[dict]] | None) -> None:
    global _default_stream_fn
    _default_stream_fn = fn

def get_default_stream_fn() -> Callable[..., AsyncIterator[dict]]:
    if _default_stream_fn is None:
        raise RuntimeError("Default stream_fn not set. Call set_default_stream_fn() first.")
    return _default_stream_fn