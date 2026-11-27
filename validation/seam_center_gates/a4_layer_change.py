#!/usr/bin/env python3
"""A4 layer change. The end point of the first Z-changing travel on each layer that
prints a perimeter (G1's layer-change point) is within 0.6 mm in X of that layer's seam.
On a layer with a closed outer loop it is also within G1's 0.55 mm on-bottom tolerance.
Open-only layers (the toe/heel caps) use G1's LC-open rule: the off-bottom count must
not exceed BASE. That layer's seam is the start of its first closed outer loop, or the
first perimeter start when the layer has no closed loop.

usage: a4_layer_change.py BASE_DIR CAND_DIR [--json out.json]
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
    chains = S.outer_chains(ev)
    rows = S.closed_outer_rows(ev)
    by_rows: dict[int, list] = {}
    for row in rows:
        by_rows.setdefault(row["layer"], []).append(row)
    by_ch: dict[int, list] = {}
    for ch in chains:
        by_ch.setdefault(ch["layer"], []).append(ch)
    bad = []
    open_off = 0
    n = 0
    max_dx = 0.0
    max_bottom = 0.0
    for pt in S.layer_change_points(ev, chains):
        n += 1
        layer_rows = by_rows.get(pt["layer"], [])
        has_closed = bool(layer_rows)
        if has_closed:
            seam_x = layer_rows[0]["seam_x"]
        elif pt["layer"] in by_ch:
            seam_x = by_ch[pt["layer"]][0]["start"][0]
        else:
            seam_x = None
        dx = None if seam_x is None else abs(pt["x"] - seam_x)
        if dx is not None:
            max_dx = max(max_dx, dx)
        max_bottom = max(max_bottom, pt["dist_bottom"])
        reasons = []
        if seam_x is None:
            reasons.append("no seam")
        elif dx > S.LC_X_TOL:
            reasons.append("x")
        if pt["dist_bottom"] > S.LC_BOTTOM_TOL:
            if has_closed:
                reasons.append("bottom")
            else:
                open_off += 1
        if reasons:
            bad.append({
                "layer": pt["layer"], "why": reasons, "x": round(pt["x"], 4),
                "seam_x": None if seam_x is None else round(seam_x, 4),
                "dx_mm": None if dx is None else round(dx, 4),
                "dist_bottom_mm": round(pt["dist_bottom"], 4), "line": pt["line"],
            })
    return {
        "layer_changes": n, "fail": len(bad), "open_off": open_off,
        "max_dx_mm": max_dx, "max_dist_bottom_mm": max_bottom, "examples": bad[:8],
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
        base_m = measure(S.gcode(args.base_dir, name))
        if m["layer_changes"] == 0:
            fails.append("%s: no layer changes" % key)
        if m["fail"]:
            fails.append("%s: %d layer changes outside 0.6 mm X or %.2f mm of the bottom (max dx %.3f, max bottom %.3f)" % (
                key, m["fail"], S.LC_BOTTOM_TOL, m["max_dx_mm"], m["max_dist_bottom_mm"]))
        if m["open_off"] > base_m["open_off"]:
            fails.append("%s: %d open-layer changes off the bottom > BASE %d" % (key, m["open_off"], base_m["open_off"]))
        parts.append(("%s_fail" % key, m["fail"]))
        parts.append(("%s_open_off" % key, m["open_off"]))
        parts.append(("%s_base_open_off" % key, base_m["open_off"]))
        parts.append(("%s_max_dx_mm" % key, m["max_dx_mm"]))
        parts.append(("%s_max_bottom_mm" % key, m["max_dist_bottom_mm"]))
        parts.append(("%s_n" % key, m["layer_changes"]))
    status = "FAIL" if fails else "PASS"
    rep = {"gate": "A4 layer_change", "result": status, "failures": fails, "measured": measured,
           "x_tol_mm": S.LC_X_TOL, "bottom_tol_mm": S.LC_BOTTOM_TOL}
    S.emit(status, "A4", parts, rep, args.json)


if __name__ == "__main__":
    main()
