#!/usr/bin/env python3
"""A1 center. Every closed ApexCore outer-wall loop's seam (start X) is within 0.10 mm of
that loop's own bottom-face center X (G1 bottom_edge midpoint). A face narrower than 3 mm
is exempt and counted. The exemption count must be <= the same count on BASE (the
scripted-correct transform does not change face width, so that count is the P2 reference).

usage: a1_center.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S
import sfg_common as C

S.pin_sfg()


def measure(path: str) -> dict:
    ev = C.parse(path)
    rows = S.closed_outer_rows(ev)
    off = []
    exempt = 0
    missing = 0
    max_dx = 0.0
    max_dx_all = 0.0
    worst = None
    for row in rows:
        if row["missing_face"]:
            missing += 1
            off.append({"layer": row["layer"], "why": "no bottom face", "seam_x": round(row["seam_x"], 3)})
            continue
        dx = abs(row["seam_x"] - row["center_x"])
        if dx > max_dx_all:
            max_dx_all = dx
        if row["narrow"]:
            exempt += 1
            continue
        if dx > max_dx:
            max_dx = dx
            worst = row
        if dx > S.CENTER_TOL:
            off.append({
                "layer": row["layer"], "seam_x": round(row["seam_x"], 4),
                "center_x": round(row["center_x"], 4), "dx_mm": round(dx, 4),
                "width_mm": round(row["width"], 4), "z": round(row["z"], 4),
            })
    off.sort(key=lambda r: -r.get("dx_mm", 0))
    return {
        "loops": len(rows), "exempt": exempt, "missing_face": missing, "off": len(off),
        "max_dx_mm": max_dx, "max_dx_all_mm": max_dx_all,
        "worst": None if worst is None else {
            "layer": worst["layer"], "seam_x": round(worst["seam_x"], 4),
            "center_x": None if worst["center_x"] is None else round(worst["center_x"], 4),
            "dx_mm": round(max_dx, 4), "width_mm": None if worst["width"] is None else round(worst["width"], 4),
            "z": round(worst["z"], 4),
        },
        "examples": off[:8],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("cand_dir")
    ap.add_argument("--json")
    args = ap.parse_args()
    fails = []
    measured = {}
    parts = []
    for key, name in S.CORE.items():
        cand = measure(S.gcode(args.cand_dir, name))
        base = measure(S.gcode(args.base_dir, name))
        measured[key] = {"cand": cand, "base_exempt": base["exempt"]}
        if cand["loops"] == 0:
            fails.append("%s: no closed outer loops" % key)
        if cand["off"] or cand["missing_face"]:
            fails.append("%s: %d closed loops off center (max dx %.3f mm)" % (key, cand["off"], cand["max_dx_mm"]))
        if cand["exempt"] > base["exempt"]:
            fails.append("%s: narrow exemptions %d > BASE %d" % (key, cand["exempt"], base["exempt"]))
        parts.append(("%s_max_dx_mm" % key, cand["max_dx_mm"]))
        parts.append(("%s_off" % key, cand["off"]))
        parts.append(("%s_exempt" % key, cand["exempt"]))
        parts.append(("%s_base_exempt" % key, base["exempt"]))
        parts.append(("%s_loops" % key, cand["loops"]))
    status = "FAIL" if fails else "PASS"
    rep = {"gate": "A1 center", "result": status, "failures": fails, "measured": measured,
           "center_tol_mm": S.CENTER_TOL, "narrow_mm": S.NARROW_MM}
    S.emit(status, "A1", parts, rep, args.json)


if __name__ == "__main__":
    main()
