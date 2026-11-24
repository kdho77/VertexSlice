#!/usr/bin/env python3
"""g2_thick_bond.py — frozen G2 thick-bond, ApexLight hollow-band gates.

Same pass line as the forefoot Cura-copy G2. Every 3-6 mm forefoot column
must have inner material touching both skins by >= 0.15 mm, in one connected
stack. Zero unbonded. Belt and dorsal air percents are reported. The pass
line is 0, not Cura's residual. Self-vs-self on live ApexLight fails.
"""
from __future__ import annotations

import ahg_common as C


def _faces(beads):
    pair = C._skin_pair(beads) if beads else None
    if pair is None or pair[4]:
        return None
    plow, phigh, dlow, dhigh, _s = pair
    belt = C.nearest_overlap(beads, plow, phigh, above=True)
    dorsal = C.nearest_overlap(beads, dlow, dhigh, above=False)
    connected = C.stack_connected(beads, C.THICK_OVERLAP)
    return belt, dorsal, connected


def evaluate(cand_path, base_path, model="orthocad"):
    cand = C.parse(cand_path)
    base = C.parse(base_path)
    cc, bc = C.forefoot_columns(cand, model), C.forefoot_columns(base, model)
    keys = set(C.thick_keys(bc)) | set(C.thick_keys(cc))
    belt_air = dorsal_air = unbonded = 0
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
    n = len(keys)
    fails = []
    if n == 0:
        fails.append("no 3-6 mm forefoot columns — fail closed, nothing was measured")
    if unbonded:
        fails.append(
            "%d of %d columns 3-6 mm are unbonded (need overlap >= %.2f mm through the stack)"
            % (unbonded, n, C.THICK_OVERLAP)
        )
    return {
        "gate": "G2 thick_bond",
        "model": model,
        "forefoot_layers": "1-%d" % C.FOREFOOT_LAST[model],
        "thick_columns": n,
        "unbonded": unbonded,
        "belt_air_pct": (100.0 * belt_air / n) if n else None,
        "dorsal_air_pct": (100.0 * dorsal_air / n) if n else None,
        "overlap_required_mm": C.THICK_OVERLAP,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }
