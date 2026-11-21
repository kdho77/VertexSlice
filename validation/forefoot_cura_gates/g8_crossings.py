#!/usr/bin/env python3
"""g8_crossings.py — FROZEN gate G8. Do not edit; checked by SHA256SUMS.

G8 Crossings. Travel crossings of walls are counted by crossings_frozen.py,
a byte-for-byte copy of scripts/g2b_heelmod/check_travel_wall_crossings.py
(sha256 5641f7ec52424040ca11a14090462ab2448b1fc8f3b51695530a18b22d1faf19).
perimeter_crossings, surface_crossings and seam_block_crossings must all be 0,
matching BASE ApexCore. Skin-interior travels are reported and may not exceed BASE.

usage: g8_crossings.py CANDIDATE.gcode --base BASE.gcode [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crossings_frozen

KEYS = ("perimeter_crossings", "surface_crossings", "seam_block_crossings")
HERE = os.path.dirname(os.path.abspath(__file__))


def _counts(path):
    report = crossings_frozen.analyze(open(path, errors="replace").read())
    totals = report["totals"]
    return {k: totals[k] for k in KEYS}, totals.get("skin_interior_travels", 0), report.get("samples", [])[:6]


def evaluate(cand_path, base_path):
    cand, skin_c, samples = _counts(cand_path)
    base, skin_b, _samples_b = _counts(base_path)
    fails = []
    for k in KEYS:
        if cand[k] > 0:
            fails.append("%s %d > 0" % (k, cand[k]))
        elif cand[k] > base[k]:
            fails.append("%s %d > BASE %d" % (k, cand[k], base[k]))
    if skin_c > skin_b:
        fails.append("skin_interior_travels %d > BASE %d" % (skin_c, skin_b))
    frozen = os.path.join(HERE, "crossings_frozen.py")
    return {
        "gate": "G8 crossings",
        "crossings_frozen_sha256": hashlib.sha256(open(frozen, "rb").read()).hexdigest(),
        "cand": cand,
        "base": base,
        "cand_skin_interior_travels": skin_c,
        "base_skin_interior_travels": skin_b,
        "samples": samples,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("--base", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = evaluate(a.cand, a.base)
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "cand")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
