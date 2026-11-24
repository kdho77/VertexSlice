#!/usr/bin/env python3
"""g_surface.py — FROZEN gate. Outer walls, skins, seams, layer changes == live.

Locked extrusions (Outer wall, Overhang wall, Inner wall) are identical to the
live file on every layer. Closed-loop starts stay on that loop's plantar edge
and at the live station (0.05 mm). The first Z-changing travel of each layer
stays on the plantar face and at the live station. A seam or layer change moved
off the bottom fails. An outer-wall path move fails.

usage: g_surface.py CANDIDATE.gcode --base LIVE.gcode [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def _closed_starts(parsed):
    out = {}
    for ch in C.perimeter_chains(parsed["events"]):
        if not ch["closed"] or ch["layer"] < 1:
            continue
        out.setdefault(ch["layer"], []).append(ch)
    return out


def _match_starts(cand_chains, base_chains):
    bad = []
    off = []
    for layer in sorted(set(cand_chains) | set(base_chains)):
        cc = cand_chains.get(layer, [])
        bc = base_chains.get(layer, [])
        used = set()
        for ch in cc:
            if not C.on_bottom(ch, ch["start"][0], ch["start"][1], C.SEAM_BOTTOM):
                off.append({"layer": layer, "x": round(ch["start"][0], 3), "y": round(ch["start"][1], 3)})
            best = None
            for j, bh in enumerate(bc):
                if j in used:
                    continue
                dx = abs(ch["start"][0] - bh["start"][0])
                dy = abs(ch["start"][1] - bh["start"][1])
                if best is None or (dx, dy) < best[0]:
                    best = ((dx, dy), j)
            if best is None or best[0][0] > C.SEAM_ALONG + 1e-9 or best[0][1] > C.SEAM_ALONG + 1e-9:
                bad.append({"layer": layer, "x": round(ch["start"][0], 3), "y": round(ch["start"][1], 3)})
            else:
                used.add(best[1])
        if len(bc) != len(used):
            bad.append({"layer": layer, "unmatched_live_starts": len(bc) - len(used)})
    return off, bad


def evaluate(cand_path, base_path):
    cand, base = C.parse(cand_path), C.parse(base_path)
    ck, bk = C.locked_extrusions(cand), C.locked_extrusions(base)
    from collections import Counter
    cc, bc = Counter(ck), Counter(bk)
    wall_delta = sum((cc - bc).values()) + sum((bc - cc).values())
    off, moved = _match_starts(_closed_starts(cand), _closed_starts(base))

    def all_layer_changes(parsed):
        out = {}
        for d in parsed["events"]:
            if d["kind"] == "travel" and d["zmove"] and d["layer"] >= 1 and d["layer"] not in out:
                out[d["layer"]] = (d["x1"], d["y1"], d["line"])
        return out
    lc_c, lc_b = all_layer_changes(cand), all_layer_changes(base)
    low = {}
    for ch in C.perimeter_chains(cand["events"]):
        if ch["layer"] < 1:
            continue
        if ch["layer"] not in low or ch["miny"] < low[ch["layer"]]["miny"]:
            low[ch["layer"]] = ch
    lc_bad = []
    for layer, (x, y, line) in lc_c.items():
        base_pt = lc_b.get(layer)
        plantar = low[layer]["miny"] if layer in low else None
        above = None if plantar is None else y - plantar
        dx = None if base_pt is None else abs(x - base_pt[0])
        dy = None if base_pt is None else abs(y - base_pt[1])
        live_above = None
        if base_pt is not None and plantar is not None:
            live_above = base_pt[1] - plantar
        # Identical to live (0.05 mm). A move up off the plantar face, relative
        # to where live already sits, also fails. Live itself is the station.
        moved_lc = base_pt is None or dx > C.SEAM_ALONG + 1e-9 or dy > C.SEAM_ALONG + 1e-9
        lifted = (above is not None and live_above is not None and above > live_above + C.SEAM_ALONG + 1e-9)
        if moved_lc or lifted:
            lc_bad.append({"layer": layer, "x": round(x, 3), "y": round(y, 3),
                           "above_plantar_mm": None if above is None else round(above, 3)})
    for layer in lc_b:
        if layer not in lc_c and layer in low:
            lc_bad.append({"layer": layer, "missing": True})
    fails = []
    if wall_delta:
        fails.append("%d locked wall/skin segments differ from live (outer, overhang, inner)" % wall_delta)
    if off:
        fails.append("%d closed loop starts are off the plantar face" % len(off))
    if moved:
        fails.append("%d closed loop starts left the live station" % len(moved))
    if lc_bad:
        fails.append("%d layer changes left the plantar face or the live station" % len(lc_bad))
    return {
        "gate": "surface",
        "locked_segments": len(ck),
        "live_locked_segments": len(bk),
        "locked_delta": wall_delta,
        "closed_off_bottom": len(off),
        "starts_moved": len(moved),
        "layer_changes_bad": len(lc_bad),
        "examples_off_bottom": off[:6],
        "examples_layer_change": lc_bad[:6],
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--base", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = C.round_report(evaluate(a.cand, a.base))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "locked_delta", "closed_off_bottom", "starts_moved", "layer_changes_bad", "failures")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
