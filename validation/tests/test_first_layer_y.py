#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from apex_first_layer_y import (  # noqa: E402
    CURA_REF,
    assert_first_layer_y_near_cura,
    cura_ref_first_layer_y_min,
)

def main() -> int:
    fails: list[str] = []
    ref_text = CURA_REF.read_text(encoding="utf-8", errors="replace")
    fails.extend(assert_first_layer_y_near_cura(ref_text))
    if abs(cura_ref_first_layer_y_min() - 0.066) > 0.001:
        fails.append("unexpected Cura ref baseline")
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("PASS first-layer Y reference checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
