#!/usr/bin/env python3
"""g4_seams.py — FROZEN gate G4. Do not edit; checked by SHA256SUMS.

G4 Seams. Every forefoot outer/overhang loop start lies on that loop's plantar
face (within 0.15 mm of its own bottom edge). The start stays at the same
station along the belt edge as BASE (X within 0.05 mm). Its Y may move inward
by up to 0.50 mm, because an inside-only wider bead keeps the outer edge put
and shifts the centreline up off the belt; a seam moved to the top is several
millimetres away and fails. The first Z-changing travel of each forefoot layer
ends within 0.55 mm of the plantar edge (the 0.40 mm belt-seam lift is inside
that) and within the same X / Y window of BASE. Open fragments may not add
off-bottom ends beyond BASE.

usage: g4_seams.py CANDIDATE.gcode --base BASE.gcode [--model orthocad|insole] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def _loops(parsed, model):
    last = C.FOREFOOT_LAST[model]
    chains = [c for c in C.perimeter_chains(parsed["events"]) if 1 <= c["layer"] <= last]
    return chains


def _match(cand_pts, base_pts):
    """Pair each start with one BASE start at the same belt-edge station.

    X is the station along the edge and is locked to 0.05 mm. Y is the
    centreline height: inward widening may lift it by up to SEAM_INWARD, and
    a move onto the top does not fit in that window.
    """
    used = set()
    bad = []
    for x, y in cand_pts:
        best = None
        for j, (bx, by) in enumerate(base_pts):
            if j in used:
                continue
            dx, dy = abs(x - bx), abs(y - by)
            if best is None or (dx, dy) < best[0]:
                best = ((dx, dy), j)
        if best is None or best[0][0] > C.SEAM_ALONG + 1e-9 or best[0][1] > C.SEAM_INWARD + 1e-9:
            bad.append({
                "x": round(x, 4), "y": round(y, 4),
                "dx": None if best is None else round(best[0][0], 4),
                "dy": None if best is None else round(best[0][1], 4),
            })
        else:
            used.add(best[1])
    if len(base_pts) != len(used):
        bad.append({"unmatched_base_starts": len(base_pts) - len(used)})
    return bad


def evaluate(cand_path, base_path, model):
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    cc, bc = _loops(cand, model), _loops(base, model)
    fails = []
    off = []
    for ch in cc:
        if not C.on_bottom(ch, ch["start"][0], ch["start"][1], C.SEAM_BOTTOM):
            off.append({"layer": ch["layer"], "closed": ch["closed"],
                        "x": round(ch["start"][0], 3), "y": round(ch["start"][1], 3),
                        "dist": round(C.dist_to_bottom(ch, ch["start"][0], ch["start"][1]), 3)})
    # Closed loops are the 165 forefoot seams. Open toe fragments are capped at BASE's off-bottom count.
    closed_off = [o for o in off if o["closed"]]
    open_off = [o for o in off if not o["closed"]]
    base_open_off = 0
    for ch in bc:
        if not ch["closed"] and not C.on_bottom(ch, ch["start"][0], ch["start"][1], C.SEAM_BOTTOM):
            base_open_off += 1
    if closed_off:
        fails.append("%d closed forefoot loop starts are off the plantar face (0 required)" % len(closed_off))
    if len(open_off) > base_open_off:
        fails.append("%d open-loop starts off the plantar face > BASE %d" % (len(open_off), base_open_off))

    by_c, by_b = {}, {}
    for ch in cc:
        if ch["closed"]:
            by_c.setdefault(ch["layer"], []).append(ch["start"])
    for ch in bc:
        if ch["closed"]:
            by_b.setdefault(ch["layer"], []).append(ch["start"])
    moved = []
    for layer in sorted(set(by_c) | set(by_b)):
        bad = _match(by_c.get(layer, []), by_b.get(layer, []))
        for item in bad:
            item["layer"] = layer
            moved.append(item)
    if moved:
        fails.append("%d closed-loop starts left their BASE station (X within %.2f mm, Y within %.2f mm)"
                     % (len(moved), C.SEAM_ALONG, C.SEAM_INWARD))

    lc_c, lc_b = C.layer_changes(cand, model), C.layer_changes(base, model)
    low = {}
    for ch in cc:
        low.setdefault(ch["layer"], ch)
        if ch["miny"] < low[ch["layer"]]["miny"]:
            low[ch["layer"]] = ch
    lc_bad = []
    for layer, (x, y, line) in sorted(lc_c.items()):
        if layer not in low:
            continue
        dist = C.dist_to_bottom(low[layer], x, y)
        base_pt = lc_b.get(layer)
        dx = None if base_pt is None else abs(x - base_pt[0])
        dy = None if base_pt is None else abs(y - base_pt[1])
        if (dist > C.LC_BOTTOM + 1e-9 or base_pt is None
                or dx > C.SEAM_ALONG + 1e-9 or dy > C.SEAM_INWARD + 1e-9):
            lc_bad.append({"layer": layer, "x": round(x, 3), "y": round(y, 3),
                           "dist_to_bottom": round(dist, 3),
                           "dx": None if dx is None else round(dx, 3),
                           "dy": None if dy is None else round(dy, 3),
                           "line": line})
    # A BASE layer-change that the candidate dropped is also a move.
    for layer in lc_b:
        if layer in low or any(c["layer"] == layer for c in cc):
            if layer not in lc_c:
                lc_bad.append({"layer": layer, "missing": True})
    if lc_bad:
        fails.append("%d forefoot layer changes left the plantar face or their BASE station" % len(lc_bad))
    n_closed = sum(1 for c in cc if c["closed"])
    return {
        "gate": "G4 seams",
        "model": model,
        "closed_loops": n_closed,
        "base_closed_loops": sum(1 for c in bc if c["closed"]),
        "closed_off_bottom": len(closed_off),
        "starts_moved": len(moved),
        "layer_changes_bad": len(lc_bad),
        "examples_off_bottom": closed_off[:8],
        "examples_moved": moved[:8],
        "examples_layer_change": lc_bad[:8],
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", choices=sorted(C.FOREFOOT_LAST))
    ap.add_argument("--json")
    a = ap.parse_args()
    model = C.detect_model(a.cand, a.model)
    rep = C.round_report(evaluate(a.cand, a.base, model))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "closed_loops", "closed_off_bottom", "starts_moved")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
