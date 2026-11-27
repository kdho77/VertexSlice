#!/usr/bin/env python3
"""g1_thin_bond.py — frozen G1 thin-bond, ApexLight hollow-band gates.

Same pass line as the forefoot Cura-copy G1. Every thin forefoot column
(section 2.0-3.0 mm, two skins) must overlap by >= 0.05 mm. Zero unbonded.
Self-vs-self on live ApexLight fails; that is the no-op.
"""
from __future__ import annotations

import ahg_common as C


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


def evaluate(cand_path, base_path, model="orthocad"):
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    cc, bc = C.forefoot_columns(cand, model), C.forefoot_columns(base, model)
    keys = set(C.thin_keys(bc)) | set(C.thin_keys(cc))
    overlaps, bad = [], []
    for key in keys:
        ov = _overlap_at(cc.get(key), bc.get(key))
        if ov is None or ov < C.THIN_OVERLAP - 1e-9:
            bad.append({"layer": key[0], "x_bin": key[1]})
        else:
            overlaps.append(ov)
    cand_gaps = []
    for key in C.thin_keys(cc):
        g = C.column_gap(cc[key])
        if g is not None:
            cand_gaps.append(g)
    fails = []
    if not keys:
        fails.append("no thin forefoot columns (section 2.0-3.0 mm) — fail closed, nothing was measured")
    if bad:
        fails.append("%d unbonded thin columns (skin overlap < %.2f mm); 0 required" % (len(bad), C.THIN_OVERLAP))
    return {
        "gate": "G1 thin_bond",
        "model": model,
        "forefoot_layers": "1-%d" % C.FOREFOOT_LAST[model],
        "thin_columns": len(keys),
        "unbonded": len(bad),
        "bonded": len(overlaps),
        "median_gap_mm": C.percentile(cand_gaps, 0.5),
        "overlap_required_mm": C.THIN_OVERLAP,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }
