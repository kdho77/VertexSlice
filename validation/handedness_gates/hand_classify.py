#!/usr/bin/env python3
"""hand_classify.py - FROZEN (OrthoCAD PM, 2026-11-24, P0 L/R swap). Do not edit; checked by SHA256SUMS.
Physical-foot classifier for belt G-code (X across belt, Y height off the belt, Z along the belt, 45 deg gantry).
Calibration (measured 2026-11-24 on 27 box-pipeline files 10/25-11/02 that printed correctly, plus Kendon's physical
check of Montgomery 98fc7007 L on 11/24): the G-code frame (X, Y, Z) is a right-handed physical frame.
Rules, both print directions:
  along   zt = Z - Y (removes the 45 deg shear). Length = zt span of belt-contact extrusions (Y <= 0.6).
  heel    the end fifth (by zt) whose max Y is larger (heel cup) is the heel. heel_high = heel at high zt.
  medial  in the middle fifth (0.4-0.6 of length) the side (X < or >= bottom-footprint X centre) with the larger
          max Y is medial (arch flange). Inconclusive if |maxY(X-) - maxY(X+)| < 5.0 mm or |heel-toe| < 5.0 mm.
  foot    LEFT iff (medial on X+) == heel_high, else RIGHT.
A 180 deg turn on the belt (heel-first vs toe-first) flips both heel_high and the medial side, so the answer is
unchanged. Any mirror flips exactly one of them, so the answer flips.
usage: hand_classify.py FILE.gcode [...]   prints JSON per file.
"""
import json, re, sys
ARCH_MIN_MM = 5.0
END_MIN_MM = 5.0
_num = re.compile(r'([XYZEF])(-?\d*\.?\d+)')

def extrusions(path):
    x = y = z = 0.0
    out = []
    rel = True
    with open(path, errors='replace') as f:
        for line in f:
            if line.startswith('M82'): rel = False
            elif line.startswith('M83'): rel = True
            if not (line.startswith('G1') or line.startswith('G0')):
                continue
            ev = None
            for k, v in _num.findall(line.split(';', 1)[0]):
                v = float(v)
                if k == 'X': x = v
                elif k == 'Y': y = v
                elif k == 'Z': z = v
                elif k == 'E': ev = v
            if ev is not None and ev > 0:
                out.append((x, y, z))
    return out

def classify_points(P):
    if len(P) < 1000:
        return {"foot": "INCONCLUSIVE", "reason": "too few extrusions"}
    bot = [p for p in P if p[1] <= 0.6]
    if len(bot) < 200:
        return {"foot": "INCONCLUSIVE", "reason": "no belt-contact layer"}
    zts = [p[2] - p[1] for p in bot]
    z0, z1 = min(zts), max(zts)
    L = z1 - z0
    if L < 80:
        return {"foot": "INCONCLUSIVE", "reason": f"part length {L:.1f} mm"}
    cx = (min(p[0] for p in bot) + max(p[0] for p in bot)) / 2.0
    def band_max(a, b, side):
        best = None
        for (px, py, pz) in P:
            t = (pz - py - z0) / L
            if a <= t < b and ((px < cx) if side < 0 else (px >= cx)):
                if best is None or py > best: best = py
        return best if best is not None else 0.0
    lo_end = max(band_max(0.0, 0.2, -1), band_max(0.0, 0.2, 1))
    hi_end = max(band_max(0.8, 1.0001, -1), band_max(0.8, 1.0001, 1))
    am = band_max(0.4, 0.6, -1); ap = band_max(0.4, 0.6, 1)
    res = {"heel_end_maxY": round(max(lo_end, hi_end), 2), "toe_end_maxY": round(min(lo_end, hi_end), 2),
           "heel_high_zt": hi_end > lo_end, "arch_maxY_xminus": round(am, 2), "arch_maxY_xplus": round(ap, 2),
           "medial_side": "X+" if ap > am else "X-", "length_mm": round(L, 1), "x_centre": round(cx, 2)}
    if abs(hi_end - lo_end) < END_MIN_MM:
        res.update(foot="INCONCLUSIVE", reason="heel end not distinct"); return res
    if abs(ap - am) < ARCH_MIN_MM:
        res.update(foot="INCONCLUSIVE", reason="arch side not distinct"); return res
    res["foot"] = "LEFT" if ((ap > am) == (hi_end > lo_end)) else "RIGHT"
    return res

def classify(path):
    r = classify_points(extrusions(path)); r["file"] = path; return r

if __name__ == '__main__':
    for f in sys.argv[1:]:
        print(json.dumps(classify(f)))
