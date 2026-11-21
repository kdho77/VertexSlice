#!/usr/bin/env python3
"""A3 smoothness. For adjacent layers that each have a closed outer loop,
|seam X step| <= |face-center X step| + 0.10 mm on the longest closed loop of the layer.

A face narrower than 3 mm keeps today's fallback (the longest bottom move's midpoint),
which is allowed to step from side to side. A pair is not scored when either face is
narrower than 3 mm. Scoring those pairs would reject the required fallback.

usage: a3_smooth.py BASE_DIR CAND_DIR [--json out.json]
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
    best = S.primary_by_layer(rows)
    layers = sorted(best)
    bad = []
    scored = 0
    skipped_narrow = 0
    skipped_gap = 0
    max_excess = 0.0
    max_step = 0.0
    for a, b in zip(layers, layers[1:]):
        if b != a + 1:
            skipped_gap += 1
            continue
        ra, rb = best[a], best[b]
        if ra["narrow"] or rb["narrow"] or ra["missing_face"] or rb["missing_face"]:
            skipped_narrow += 1
            if ra["missing_face"] or rb["missing_face"]:
                bad.append({"layers": [a, b], "why": "missing bottom face"})
            continue
        ds = abs(rb["seam_x"] - ra["seam_x"])
        dc = abs(rb["center_x"] - ra["center_x"])
        excess = ds - dc - S.STEP_TOL
        max_step = max(max_step, ds)
        if excess > max_excess:
            max_excess = excess
        scored += 1
        if ds > dc + S.STEP_TOL:
            bad.append({
                "layers": [a, b], "seam_step_mm": round(ds, 4), "center_step_mm": round(dc, 4),
                "excess_mm": round(excess, 4),
                "seam_x": [round(ra["seam_x"], 4), round(rb["seam_x"], 4)],
                "center_x": [round(ra["center_x"], 4), round(rb["center_x"], 4)],
            })
    bad.sort(key=lambda r: -r.get("excess_mm", 0))
    return {
        "pairs_scored": scored, "pairs_fail": len(bad), "skipped_narrow": skipped_narrow,
        "skipped_gap": skipped_gap, "max_seam_step_mm": max_step, "max_excess_mm": max(0.0, max_excess),
        "examples": bad[:8],
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
        m = measure(S.gcode(args.cand_dir, name))
        measured[key] = m
        if m["pairs_scored"] == 0 and key == "orthocad":
            fails.append("orthocad: no adjacent closed-loop pairs scored")
        if m["pairs_fail"]:
            fails.append("%s: %d pairs |seam step| > |center step| + %.2f (max excess %.3f mm, max seam step %.3f mm)" % (
                key, m["pairs_fail"], S.STEP_TOL, m["max_excess_mm"], m["max_seam_step_mm"]))
        parts.append(("%s_fail_pairs" % key, m["pairs_fail"]))
        parts.append(("%s_max_seam_step_mm" % key, m["max_seam_step_mm"]))
        parts.append(("%s_max_excess_mm" % key, m["max_excess_mm"]))
        parts.append(("%s_scored" % key, m["pairs_scored"]))
    status = "FAIL" if fails else "PASS"
    rep = {"gate": "A3 smoothness", "result": status, "failures": fails, "measured": measured,
           "step_tol_mm": S.STEP_TOL, "narrow_mm": S.NARROW_MM}
    S.emit(status, "A3", parts, rep, args.json)


if __name__ == "__main__":
    main()
