#!/usr/bin/env python3
"""Kendon infill-density process presets (95A baseline, rectilinear)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from apex_insole_densities import KENDON_INFILL_DENSITY_PRESETS, PROCESS_FILES  # noqa: E402
from orca_profile_flatten import flatten_profile  # noqa: E402

PROC_DIR = REPO / "resources/profiles/IdeaFormer/process"


def main() -> int:
    fails: list[str] = []
    for label, pct in KENDON_INFILL_DENSITY_PRESETS.items():
        rel = PROCESS_FILES[label]
        path = PROC_DIR / rel
        if not path.is_file():
            fails.append(f"missing process file for {label}: {path}")
            continue
        flat = flatten_profile(path)
        data = json.loads(flat.read_text())
        flat.unlink(missing_ok=True)
        got = str(data.get("sparse_infill_density", "")).rstrip("%")
        if got != str(pct):
            fails.append(f"{label}: sparse_infill_density={data.get('sparse_infill_density')} expected {pct}%")
        if str(data.get("sparse_infill_pattern", "")).lower() != "rectilinear":
            fails.append(f"{label}: pattern not rectilinear")
        if str(data.get("infill_direction")) != "0":
            fails.append(f"{label}: infill_direction not 0")

    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print(f"PASS Kendon infill densities ({len(KENDON_INFILL_DENSITY_PRESETS)} presets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
