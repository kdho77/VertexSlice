#!/usr/bin/env python3
"""Headless API override validation."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from server.slice_overrides import parse_slice_overrides  # noqa: E402


def main() -> int:
    fails: list[str] = []
    o = parse_slice_overrides(2, "30")
    if o.get("wall_loops") != 2 or o.get("sparse_infill_density") != "30%":
        fails.append(f"unexpected parse: {o}")
    try:
        parse_slice_overrides(0, None)
        fails.append("wall_loops 0 should fail")
    except ValueError:
        pass
    try:
        parse_slice_overrides(None, 3)
        fails.append("infill 3% should fail")
    except ValueError:
        pass
    if parse_slice_overrides(None, None):
        fails.append("empty should yield no overrides")
    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print("PASS server slice overrides")
    return 0


if __name__ == "__main__":
    sys.exit(main())
