#!/usr/bin/env python3
"""A5 sample. insole_sample.stl ApexCore closed-outer seam X values stay at their BASE
X within 0.01 mm. The flat block is already one bottom edge; centering it does not move X.

usage: a5_sample.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S
import sfg_common as C

S.pin_sfg()


def seams(path: str) -> list[dict]:
    ev = C.parse(path)
    out = []
    for row in S.closed_outer_rows(ev):
        out.append({"layer": row["layer"], "seam_x": row["seam_x"], "seam_y": row["seam_y"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("cand_dir")
    ap.add_argument("--json")
    args = ap.parse_args()
    name = S.CORE["insole2"]
    base = seams(S.gcode(args.base_dir, name))
    cand = seams(S.gcode(args.cand_dir, name))
    fails = []
    worst = 0.0
    mismatched = []
    if len(base) != len(cand):
        fails.append("closed outer loop count %d != BASE %d" % (len(cand), len(base)))
    if not base:
        fails.append("BASE insole has no closed outer loops")
    xs = sorted({round(r["seam_x"], 3) for r in base})
    for i, (b, c) in enumerate(zip(base, cand)):
        if b["layer"] != c["layer"]:
            fails.append("loop %d layer %s != BASE layer %s" % (i, c["layer"], b["layer"]))
            break
        dx = abs(c["seam_x"] - b["seam_x"])
        worst = max(worst, dx)
        if dx > S.SAMPLE_X_TOL:
            mismatched.append({
                "layer": c["layer"], "cand_x": round(c["seam_x"], 4), "base_x": round(b["seam_x"], 4),
                "dx_mm": round(dx, 4),
            })
    if mismatched:
        fails.append("%d seams moved more than %.2f mm (max %.4f mm)" % (len(mismatched), S.SAMPLE_X_TOL, worst))
    status = "FAIL" if fails else "PASS"
    parts = [("loops", len(cand)), ("base_loops", len(base)), ("max_dx_mm", worst),
             ("moved", len(mismatched)), ("base_x_set", ",".join(str(x) for x in xs) if xs else "none")]
    rep = {"gate": "A5 sample", "result": status, "failures": fails, "max_dx_mm": worst,
           "base_seam_x": xs, "examples": mismatched[:8], "loops": [len(cand), len(base)],
           "tol_mm": S.SAMPLE_X_TOL}
    S.emit(status, "A5", parts, rep, args.json)


if __name__ == "__main__":
    main()
