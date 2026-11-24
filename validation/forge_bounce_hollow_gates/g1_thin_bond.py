#!/usr/bin/env python3
"""g1_thin_bond.py — FROZEN gate. Thin bond, self-vs-self, orthocad layers 1-165.

Every frozen thin forefoot column (section thickness in [2.0, 3.0) mm, two skin
beads) must show skin-to-skin overlap >= 0.05 mm. Zero unbonded thin columns.
Bead height in the envelope is the frozen 0.46 mm, including on 0.42 mm files.
That is the instrument that reads live Forge/Bounce as 1474/5833 and live
ApexCore as 3/15493.

usage: g1_thin_bond.py CANDIDATE.gcode [--base BASE.gcode] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C


def _overlap_at(beads, base_beads):
    pair = C._skin_pair(beads) if beads else None
    if pair is None:
        return None
    if pair[4]:
        base = C._skin_pair(base_beads) if base_beads else None
        if base is None:
            return None
        lo, hi = pair[0], pair[1]
        if lo <= base[0] + C.THIN_OVERLAP and hi >= base[3] - C.THIN_OVERLAP:
            return hi - lo
        return None
    _th, gap = C.section_of(pair)
    return -gap


def evaluate(cand_path, base_path=None):
    base_path = base_path or cand_path
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    model = "orthocad"
    cc, bc = C.forefoot_columns(cand, model), C.forefoot_columns(base, model)
    keys = set(C.thin_keys(bc)) | set(C.thin_keys(cc))
    overlaps, bad = [], []
    for key in keys:
        ov = _overlap_at(cc.get(key), bc.get(key))
        if ov is None or ov < C.THIN_OVERLAP - 1e-9:
            bad.append({"layer": key[0], "x_bin": key[1]})
        else:
            overlaps.append(ov)
    fails = []
    if not keys:
        fails.append("no thin forefoot columns (section 2.0-3.0 mm) — fail closed")
    if bad:
        fails.append("%d unbonded thin columns (skin overlap < %.2f mm); 0 required" % (len(bad), C.THIN_OVERLAP))
    return {
        "gate": "G1_thin_bond",
        "forefoot_layers": "1-165",
        "bead_height_envelope_mm": C.H_BEAD,
        "thin_columns": len(keys),
        "unbonded": len(bad),
        "bonded": len(overlaps),
        "examples": bad[:8],
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
    print(json.dumps({k: rep[k] for k in ("gate", "result", "unbonded", "thin_columns", "failures")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
