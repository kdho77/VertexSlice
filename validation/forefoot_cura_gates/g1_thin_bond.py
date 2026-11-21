#!/usr/bin/env python3
"""g1_thin_bond.py — FROZEN gate G1. Do not edit; checked by SHA256SUMS.

G1 Thin bond. Every frozen thin forefoot column (BASE section thickness in
[2.0, 3.0) mm, two skin beads) must show skin-to-skin overlap >= 0.05 mm on
the candidate. A column the candidate newly makes thin has the same bar.
Zero unbonded thin columns. The median signed gap is reported against Cura's
-0.45 mm (negative means overlap); that Cura figure is not the pass line.

usage: g1_thin_bond.py CANDIDATE.gcode --base BASE.gcode [--model orthocad|insole] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def _overlap_at(beads, base_beads):
    """Return the skin overlap in mm, or None when the column is unbonded.

    Two skins: overlap = -(dorsal inner edge - plantar inner edge).
    One skin: it bonds only when that bead still reaches both BASE outer edges,
    so a missing skin cannot hide inside a single short bead.
    """
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


def evaluate(cand_path, base_path, model):
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    cc, bc = C.forefoot_columns(cand, model), C.forefoot_columns(base, model)
    keys = set(C.thin_keys(bc)) | set(C.thin_keys(cc))
    overlaps, gaps, bad = [], [], []
    for key in keys:
        ov = _overlap_at(cc.get(key), bc.get(key))
        if ov is None or ov < C.THIN_OVERLAP - 1e-9:
            bad.append({"layer": key[0], "x_bin": key[1], "x_mm": round((key[1] + 0.5) * C.COLUMN_MM, 3),
                        "overlap_mm": None if ov is None else round(ov, 4)})
        else:
            overlaps.append(ov)
            gaps.append(-ov)
    # Median gap on the candidate's own two-skin thin columns (the PM's reported figure).
    cand_gaps = []
    for key in C.thin_keys(cc):
        g = C.column_gap(cc[key])
        if g is not None:
            cand_gaps.append(g)
    base_gaps = []
    for key in C.thin_keys(bc):
        g = C.column_gap(bc[key])
        if g is not None:
            base_gaps.append(g)
    fails = []
    if not keys:
        fails.append("no thin forefoot columns (section 2.0-3.0 mm) — fail closed, nothing was measured")
    if bad:
        fails.append("%d unbonded thin columns (skin overlap < %.2f mm); 0 required" % (len(bad), C.THIN_OVERLAP))
    return {
        "gate": "G1 thin_bond",
        "model": model,
        "stl": C.MODEL_NAMES[model],
        "forefoot_layers": "1-%d" % C.FOREFOOT_LAST[model],
        "thin_columns": len(keys),
        "unbonded": len(bad),
        "bonded": len(overlaps),
        "median_gap_mm": C.percentile(cand_gaps, 0.5),
        "base_median_gap_mm": C.percentile(base_gaps, 0.5),
        "cura_median_gap_mm": C.CURA_THIN_GAP,
        "overlap_required_mm": C.THIN_OVERLAP,
        "examples": bad[:12],
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
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "unbonded", "median_gap_mm", "base_median_gap_mm")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
