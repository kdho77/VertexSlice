#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from apex_insole_tunables import (  # noqa: E402
    PRINT_DIRECTION_HEEL_FIRST,
    PRINT_DIRECTION_TOE_FIRST,
    orthocad_base_rotation_z_deg,
    validate_print_direction,
)


def main() -> int:
    fails: list[str] = []
    if validate_print_direction(None) != PRINT_DIRECTION_TOE_FIRST:
        fails.append("default print_direction")
    if orthocad_base_rotation_z_deg(PRINT_DIRECTION_TOE_FIRST) != 270:
        fails.append("toe_first rotation")
    if orthocad_base_rotation_z_deg(PRINT_DIRECTION_HEEL_FIRST) != 90:
        fails.append("heel_first rotation")
    try:
        validate_print_direction("sideways")
        fails.append("invalid direction should raise")
    except ValueError:
        pass
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("PASS print_direction tunables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
