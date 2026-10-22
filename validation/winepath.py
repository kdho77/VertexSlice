"""Map Linux paths to Wine Z: drive paths."""
from __future__ import annotations

from pathlib import Path


def winepath(path: Path) -> str:
    p = path.resolve()
    return "Z:" + str(p).replace("/", "\\")
