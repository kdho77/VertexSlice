#!/usr/bin/env python3
"""Slice SAMPLE_Top through the production chains and apply the physical gate.

The unbonded slice is the e42391c7 geometry: this change does not touch the
slicer binary. BELT_SKIN_BOND=0 keeps the chain from bonding, then this script
applies the same bond the chain hooks call. The job fails when any limit in
validation/belt_skin_physical.py fails. Widths come from the extrusion, not
from the planned ;WIDTH.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts" / "g2b_heelmod"), str(REPO / "validation")]

from belt_skin_bond import bond_gcode, gcode_md5  # noqa: E402
from belt_skin_physical import audit_texts  # noqa: E402
from server.profile_registry import get_profile  # noqa: E402
from server.slice_engine import slice_stl  # noqa: E402

STL = REPO / "validation" / "test_models" / "orthocad_sample_top_insole.stl"
MATERIALS = ("apex_core", "apex_light")


def _diff_stat(before: str, after: str) -> dict:
    import difflib

    added = removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"lines_added": added, "lines_removed": removed}


def _counter(text: str) -> tuple[int, int]:
    for line in text.splitlines():
        if line.startswith("; belt_skin_bond filled_runs="):
            filled = int(line.split("filled_runs=")[1].split()[0])
            widened = int(line.split("widened=")[1])
            return filled, widened
    return 0, 0


def _footer(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return ""


def _one(material: str, work: Path) -> dict:
    # The chain bonds unless this is off. Off produces the e42391c7 G-code,
    # because the slicer sources are unchanged.
    os.environ["BELT_SKIN_BOND"] = "0"
    profile = get_profile("apex-v2-insole-95a", material=material)
    if profile is None:
        raise SystemExit(f"missing profile for {material}")
    path, stats = slice_stl(
        STL,
        profile,
        work / material,
        timeout_s=1800,
        process_overrides={"wall_loops": 1, "sparse_infill_density": "26%"},
        belt_insole_place=True,
        print_direction="toe_first",
        material=material,
    )
    raw = path.read_text(errors="replace")
    bonded = bond_gcode(raw, material=material)
    audit = audit_texts(raw, bonded)
    filled, widened = _counter(bonded)
    raw_md5 = gcode_md5(raw)
    out_md5 = gcode_md5(bonded)
    reasons = [name for name, row in audit["limits"].items() if not row["ok"]]
    elapsed = float(stats.get("elapsed_s", 0.0))
    if material == "apex_light" and elapsed >= 260.0:
        reasons.append(f"slice {elapsed:.1f}s")
    return {
        "material": material,
        "slice_s": round(elapsed, 1),
        "filled_runs": filled,
        "widened": widened,
        "md5_unbonded": raw_md5,
        "md5_bonded": out_md5,
        "diff": _diff_stat(raw, bonded),
        "time": _footer(bonded, "; estimated printing time"),
        "filament_g": _footer(bonded, "; filament used [g]"),
        "total_filament_g": _footer(bonded, "; total filament used [g]"),
        "audit": audit,
        "ok": not reasons,
        "reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not STL.is_file() or STL.stat().st_size < 100000:
        raise SystemExit(f"SAMPLE_Top STL missing: {STL}")
    rows = []
    with tempfile.TemporaryDirectory(prefix="belt-skin-proof-") as tmp:
        work = Path(tmp)
        for material in MATERIALS:
            rows.append(_one(material, work))
    report = {"stl": str(STL), "materials": rows, "ok": all(row["ok"] for row in rows)}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
