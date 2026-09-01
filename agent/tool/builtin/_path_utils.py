from __future__ import annotations

import os
from pathlib import Path

def path_exists(absolute_path: str) -> bool:
    return os.path.exists(absolute_path)

def resolve_to_cwd(path: str, cwd: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    return str(p.resolve())
