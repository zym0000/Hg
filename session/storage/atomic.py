"""Atomic file publish: write to sibling .tmp, os.replace to target.

Guarantees:
- On any write_fn exception, .tmp is removed (best-effort) and exception re-raised.
- os.replace is atomic on POSIX and on Windows (when source/dest are on same volume).
"""
import os
from collections.abc import Awaitable, Callable
from pathlib import Path


async def publish_file_atomically(
    path: Path,
    write_fn: Callable[[Path], Awaitable[None]],
) -> None:
    path = Path(path)
    tmp = path.parent / f".{path.name}.tmp"
    try:
        await write_fn(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
