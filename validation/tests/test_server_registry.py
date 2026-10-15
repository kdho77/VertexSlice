#!/usr/bin/env python3
"""Sanity-check headless server profile registry paths."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from server.profile_registry import get_profile, list_materials, list_profiles  # noqa: E402


def main() -> int:
    fails = []
    for entry in list_profiles():
        pid = entry["id"]
        p = get_profile(pid)
        if p is None:
            fails.append(pid)
            continue
        for label, path in (
            ("machine", p.machine),
            ("process", p.process),
            ("filament", p.filament),
        ):
            if not path.is_file():
                fails.append(f"{pid}: missing {label} {path}")
    mats = list_materials()
    if [m["id"] for m in mats] != ["apex_core", "apex_forge", "apex_bounce"]:
        fails.append(f"materials {mats}")
    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print(f"PASS {len(list_profiles())} profiles, {len(mats)} materials")
    return 0


if __name__ == "__main__":
    sys.exit(main())
