"""Content fingerprint of this slicer build, reported by GET /health as "build".

OrthoCAD keys its cached belt G-code on this value, so G-code sliced by one build is never
reused after any change to the server code, the validation/placement code, the profiles or the
slicer binary (Kendon 2026-11-24 10:09 PM MT: "make sure cached gcode don't get reloaded").

The hash is computed from file contents, not from a version string someone has to remember to
bump. It is computed once in a background thread at startup; until it is ready /health reports
"build": null and callers treat the build as unknown (no reuse).
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

APP_ROOT = Path(os.environ.get("VERTEXSLICE_APP_ROOT", str(Path(__file__).resolve().parents[1])))

# Everything that can change the G-code for a given request.
_TREES = ("server", "validation")
_PROFILE_TREES = ("resources/profiles/IdeaFormer",)
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "test_models", "tests"}

_lock = threading.Lock()
_value: str | None = None
_started = False


def _iter_files(root: Path, rel: str):
    base = root / rel
    if base.is_file():
        yield base
        return
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            yield Path(dirpath) / name


def compute_build(root: Path | None = None, slicer_bin: str | None = None) -> str:
    root = root or APP_ROOT
    digest = hashlib.sha256()
    rels = list(_TREES) + list(_PROFILE_TREES)
    paths: list[tuple[str, Path]] = []
    for rel in rels:
        for path in _iter_files(root, rel):
            paths.append((path.relative_to(root).as_posix(), path))
    binary = slicer_bin if slicer_bin is not None else os.environ.get("ORCA_SLICER_BIN", "")
    if binary and Path(binary).is_file():
        paths.append(("bin:" + Path(binary).name, Path(binary)))
    for rel, path in sorted(paths):
        digest.update(rel.encode() + b"\0")
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _compute_in_background() -> None:
    global _value
    value = compute_build()
    with _lock:
        _value = value


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_compute_in_background, name="build-info", daemon=True).start()


def current_build() -> str | None:
    with _lock:
        return _value
