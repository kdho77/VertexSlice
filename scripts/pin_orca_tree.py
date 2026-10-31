#!/usr/bin/env python3
"""Overlay a pinned Orca tree onto a deps build without touching unchanged files.

Dockerfile.server builds two slicer commits from one deps stage. Replacing
only the files that differ keeps ninja incremental on the second compile.
deps/ and deps_src/ are never modified.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Compile inputs plus the resources tree each binary ships beside itself.
PIN_PATHS = (
    "src",
    "resources",
    "cmake",
    "CMakeLists.txt",
    "version.inc",
    "build_linux.sh",
)


def _same(src: Path, dest: Path) -> bool:
    if src.is_symlink() or dest.is_symlink():
        return src.is_symlink() and dest.is_symlink() and os.readlink(src) == os.readlink(dest)
    if not dest.is_file():
        return False
    if src.stat().st_size != dest.stat().st_size:
        return False
    return src.read_bytes() == dest.read_bytes()


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    if src.is_symlink():
        dest.symlink_to(os.readlink(src))
        return
    dest.write_bytes(src.read_bytes())


def _sync_file(src: Path, dest: Path) -> int:
    if _same(src, dest):
        return 0
    _copy(src, dest)
    return 1


def _sync_dir(src: Path, dest: Path) -> int:
    changed = 0
    wanted: set[Path] = set()
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                rel = path.relative_to(src)
                wanted.add(rel)
                changed += _sync_file(path, dest / rel)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            rel = path.relative_to(src)
            wanted.add(rel)
            changed += _sync_file(path, dest / rel)
    for dirpath, dirnames, filenames in os.walk(dest, topdown=False, followlinks=False):
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            if path.relative_to(dest) not in wanted:
                path.unlink()
                changed += 1
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                if path.relative_to(dest) not in wanted:
                    path.unlink()
                    changed += 1
                continue
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    return changed


def sync_pin(pin_root: Path, dest_root: Path) -> int:
    """Make dest_root's pin paths match pin_root. Returns the number of updates."""
    pin_root = Path(pin_root)
    dest_root = Path(dest_root)
    changed = 0
    for rel in PIN_PATHS:
        src = pin_root / rel
        if not src.exists() and not src.is_symlink():
            raise FileNotFoundError(f"pinned tree is missing {rel}")
        dest = dest_root / rel
        if src.is_symlink() or src.is_file():
            changed += _sync_file(src, dest)
        else:
            changed += _sync_dir(src, dest)
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: pin_orca_tree.py PIN_ROOT DEST_ROOT", file=sys.stderr)
        return 2
    changed = sync_pin(Path(argv[1]), Path(argv[2]))
    print(f"pin_orca_tree updated {changed} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
