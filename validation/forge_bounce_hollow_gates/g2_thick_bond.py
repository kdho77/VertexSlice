#!/usr/bin/env python3
"""g2_thick_bond.py — FROZEN gate. Thick bond, self-vs-self, orthocad layers 1-165.

Every forefoot column whose section is 3-6 mm must have inner material touching
both skins, overlap >= 0.15 mm, and one connected stack. Zero unbonded columns.
Belt and dorsal air percent are reported. The pass line is 0 on both faces.
Envelope bead height is the frozen 0.46 mm. Live Forge/Bounce read 2776/2968,
belt air 65.4%, dorsal air 80.9%. Live ApexCore reads 0/8406.

usage: g2_thick_bond.py CANDIDATE.gcode [--base BASE.gcode] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def _faces(beads):
    pair = C._skin_pair(beads) if beads else None
    if pair is None or pair[4]:
        return None
    plow, phigh, dlow, dhigh, _s = pair
    belt = C.nearest_overlap(beads, plow, phigh, above=True)
    dorsal = C.nearest_overlap(beads, dlow, dhigh, above=False)
    connected = C.stack_connected(beads, C.THICK_OVERLAP)
    return belt, dorsal, connected


def evaluate(cand_path, base_path=None):
    base_path = base_path or cand_path
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    model = "orthocad"
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
            if len(examples) < 8:
                examples.append({"layer": key[0], "x_bin": key[1]})
    n = len(keys)
    fails = []
    if n == 0:
        fails.append("no 3-6 mm forefoot columns — fail closed")
    if unbonded:
        fails.append("%d of %d columns 3-6 mm are unbonded (need overlap >= %.2f mm through the stack)"
                     % (unbonded, n, C.THICK_OVERLAP))
    return {
        "gate": "G2_thick_bond",
        "forefoot_layers": "1-165",
        "bead_height_envelope_mm": C.H_BEAD,
        "thick_columns": n,
        "unbonded": unbonded,
        "belt_air_pct": (100.0 * belt_air / n) if n else None,
        "dorsal_air_pct": (100.0 * dorsal_air / n) if n else None,
        "examples": examples,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--base")
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = C.round_report(evaluate(a.cand, a.base))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "unbonded", "thick_columns", "belt_air_pct", "dorsal_air_pct", "failures")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
