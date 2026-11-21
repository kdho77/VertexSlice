#!/usr/bin/env python3
"""g3_outer_edge.py — FROZEN gate G3. Do not edit; checked by SHA256SUMS.

G3 Outer edge. The printed outer edge is the outer-wall centreline offset by
half the E-derived bead width along the outward normal. Against BASE, the p99
of those distances is <= 0.02 mm and nothing exceeds 0.05 mm. Both directions
are measured, so a missing stretch fails as well as a moved one. Bead height
must stay at BASE's 0.46 mm.

usage: g3_outer_edge.py CANDIDATE.gcode --base BASE.gcode [--model orthocad|insole] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def evaluate(cand_path, base_path, model):
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    dists, missing = C.edge_distances(cand, base, model)
    finite = [d for d in dists if d != float("inf")]
    p99 = C.percentile(finite, 0.99)
    mx = max(finite) if finite else None
    fails = []
    if not finite and missing == 0:
        fails.append("no printed outer-edge samples in the forefoot — fail closed")
    if missing:
        fails.append("%d outer-edge samples have no counterpart on the other file" % missing)
    if p99 is None or p99 > C.EDGE_P99 + 1e-9:
        fails.append("p99 outer-edge deviation %s mm > %.2f mm" % (p99, C.EDGE_P99))
    if mx is None or mx > C.EDGE_MAX + 1e-9:
        fails.append("max outer-edge deviation %s mm > %.2f mm" % (mx, C.EDGE_MAX))
    bh_c, bh_b = cand["bead_height"], base["bead_height"]
    if bh_b is not None and (bh_c is None or abs(bh_c - bh_b) > 0.005):
        fails.append("bead height %s mm differs from BASE %s mm" % (bh_c, bh_b))
    return {
        "gate": "G3 outer_edge",
        "model": model,
        "samples": len(finite),
        "missing_samples": missing,
        "p99_mm": p99,
        "max_mm": mx,
        "p99_limit_mm": C.EDGE_P99,
        "max_limit_mm": C.EDGE_MAX,
        "cand_bead_height_mm": bh_c,
        "base_bead_height_mm": bh_b,
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
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "p99_mm", "max_mm", "samples")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
