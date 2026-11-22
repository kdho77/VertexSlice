#!/usr/bin/env python3
"""g2_thick_bond.py — FROZEN gate G2. Do not edit; checked by SHA256SUMS.

G2 Thick toe bond. Every forefoot column whose section is 3-6 mm must have the
inner material touching both outer skins, overlap >= 0.15 mm, and the beads
between those skins must form one chain with that same overlap. A gap left in
the middle fails even when both skins touch something. Air percent (columns
whose belt or dorsal skin overlaps the next bead by < 0.15 mm) is reported
next to the Cura figures 46% / 39%. The pass line is 0% on both faces.

usage: g2_thick_bond.py CANDIDATE.gcode --base BASE.gcode [--model orthocad|insole] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def _faces(beads):
    pair = C._skin_pair(beads) if beads else None
    if pair is None or pair[4]:
        return None
    plow, phigh, dlow, dhigh, _s = pair
    belt = C.nearest_overlap(beads, plow, phigh, above=True)
    dorsal = C.nearest_overlap(beads, dlow, dhigh, above=False)
    connected = C.stack_connected(beads, C.THICK_OVERLAP)
    return belt, dorsal, connected


def evaluate(cand_path, base_path, model):
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    cc, bc = C.forefoot_columns(cand, model), C.forefoot_columns(base, model)
    keys = set(C.thick_keys(bc)) | set(C.thick_keys(cc))
    belt_air = dorsal_air = unbonded = 0
    examples = []
    for key in keys:
        faces = _faces(cc.get(key))
        bad = faces is None
        if not bad:
            belt, dorsal, connected = faces
            if belt is None or belt < C.THICK_OVERLAP - 1e-9:
                belt_air += 1
                bad = True
            if dorsal is None or dorsal < C.THICK_OVERLAP - 1e-9:
                dorsal_air += 1
                bad = True
            if not connected:
                bad = True
        else:
            belt_air += 1
            dorsal_air += 1
        if bad:
            unbonded += 1
            if len(examples) < 12:
                examples.append({"layer": key[0], "x_bin": key[1],
                                 "x_mm": round((key[1] + 0.5) * C.COLUMN_MM, 3)})
    n = len(keys)
    belt_pct = 100.0 * belt_air / n if n else None
    dorsal_pct = 100.0 * dorsal_air / n if n else None
    fails = []
    if n == 0:
        fails.append("no 3-6 mm forefoot columns — fail closed, nothing was measured")
    if unbonded:
        fails.append("%d of %d columns 3-6 mm are unbonded (need overlap >= %.2f mm through the stack)"
                     % (unbonded, n, C.THICK_OVERLAP))
    return {
        "gate": "G2 thick_bond",
        "model": model,
        "stl": C.MODEL_NAMES[model],
        "forefoot_layers": "1-%d" % C.FOREFOOT_LAST[model],
        "thick_columns": n,
        "unbonded": unbonded,
        "belt_air_pct": belt_pct,
        "dorsal_air_pct": dorsal_pct,
        "cura_belt_air_pct": C.CURA_AIR_BELT,
        "cura_dorsal_air_pct": C.CURA_AIR_DORSAL,
        "overlap_required_mm": C.THICK_OVERLAP,
        "examples": examples,
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
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "unbonded", "belt_air_pct", "dorsal_air_pct")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
