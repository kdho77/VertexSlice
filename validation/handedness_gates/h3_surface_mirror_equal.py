#!/usr/bin/env python3
"""h3_surface_mirror_equal.py - FROZEN gate H3 (OrthoCAD PM, 2026-11-24). Do not edit; checked by SHA256SUMS.
Kendon's locked rule: surface quality untouched, no scope growth. The handedness fix may only mirror the part across
the belt. So the candidate must be the LIVE file's mirror, with the same settings.
usage: h3_surface_mirror_equal.py CANDIDATE.gcode LIVE.gcode     (same material, same input STL)   exit 0 PASS, 1 FAIL
  C  CONFIG_BLOCK (between ; CONFIG_BLOCK_START/END) byte-identical.
  N  same layer count (';Z:' / ';LAYER_CHANGE' markers), same count of ';TYPE:Outer wall' blocks within 2%.
  E  total extrusion (sum of positive E) within 1.0%.
  W  outer-wall geometry: per layer, candidate outer-wall polyline points vs LIVE outer-wall points mirrored across the
     belt (X -> cxL + cxC - X, cx = bottom-footprint X centre of each file). Symmetric max nearest distance <= 0.30 mm on
     >= 95% of layers that have outer wall in both, and median over layers <= 0.15 mm.
  F  outer-wall feedrate multiset (F values on Outer wall extrusions, rounded to 1 mm/min) identical within 1% per value.
"""
import json, re, sys
from collections import Counter
import numpy as np
num = re.compile(r'([XYZEF])(-?\d*\.?\d+)')

def parse(fn):
    txt = open(fn, errors='replace').read()
    cfg = txt[txt.find('; CONFIG_BLOCK_START'):txt.find('; CONFIG_BLOCK_END')]
    x = y = z = 0.0; f = 0.0; t = ''; layer = -1
    layers = {}; e_tot = 0.0; feeds = Counter(); n_outer = 0; bot = []
    for line in txt.split('\n'):
        if line.startswith(';Z:') or line.startswith(';LAYER_CHANGE'):
            if line.startswith(';Z:'): layer += 1
            continue
        if line.startswith(';TYPE:'):
            t = line[6:].strip(); n_outer += t == 'Outer wall'; continue
        if not line.startswith(('G0', 'G1')): continue
        px, py, pz = x, y, z; ev = None
        for k, v in num.findall(line.split(';', 1)[0]):
            v = float(v)
            if k == 'X': x = v
            elif k == 'Y': y = v
            elif k == 'Z': z = v
            elif k == 'E': ev = v
            elif k == 'F': f = v
        if ev is not None and ev > 0:
            e_tot += ev
            if y <= 0.6: bot.append(x)
            if t == 'Outer wall':
                feeds[round(f)] += 1
                seg = np.array([[px, py, pz], [x, y, z]])
                n = max(2, int(np.linalg.norm(seg[1] - seg[0]) / 0.5) + 1)
                pts = seg[0] + (seg[1] - seg[0]) * np.linspace(0, 1, n)[:, None]
                layers.setdefault(layer, []).append(pts)
    layers = {k: np.vstack(v) for k, v in layers.items()}
    return dict(cfg=cfg, layers=layers, e=e_tot, feeds=feeds, n_outer=n_outer, nlayers=layer + 1,
                cx=(min(bot) + max(bot)) / 2 if bot else 0.0)

def nn_max(a, b):
    best = 0.0
    for i in range(0, len(a), 2000):
        d = np.sqrt(((a[i:i + 2000, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1)
        best = max(best, float(d.max()))
    return best

C, L = parse(sys.argv[1]), parse(sys.argv[2])
res = {}
res['C_config_identical'] = C['cfg'] == L['cfg'] and len(C['cfg']) > 1000
res['N_layers'] = [C['nlayers'], L['nlayers']]
res['N_ok'] = C['nlayers'] == L['nlayers'] and abs(C['n_outer'] - L['n_outer']) <= 0.02 * max(1, L['n_outer'])
res['E'] = [round(C['e'], 2), round(L['e'], 2)]
res['E_ok'] = abs(C['e'] - L['e']) <= 0.01 * L['e']
ds = []
for k in sorted(set(C['layers']) & set(L['layers'])):
    a = C['layers'][k]; b = L['layers'][k].copy(); b[:, 0] = L['cx'] + C['cx'] - b[:, 0]
    if len(a) > 6000: a = a[:: len(a) // 6000 + 1]
    if len(b) > 6000: b = b[:: len(b) // 6000 + 1]
    ds.append(max(nn_max(a, b), nn_max(b, a)))
ds = np.array(ds) if ds else np.array([99.0])
res['W_layers'] = int(len(ds)); res['W_frac_le_0.30'] = round(float((ds <= 0.30).mean()), 4); res['W_median'] = round(float(np.median(ds)), 4)
res['W_ok'] = res['W_frac_le_0.30'] >= 0.95 and res["W_median"] <= 0.15
keys = set(C['feeds']) | set(L['feeds'])
res['F_ok'] = all(abs(C['feeds'][k] - L['feeds'][k]) <= max(2, 0.01 * L['feeds'][k]) for k in keys)
ok = res['C_config_identical'] and res['N_ok'] and res['E_ok'] and res['W_ok'] and res['F_ok']
res['pass'] = ok
print(json.dumps(res, indent=1))
print(('PASS' if ok else 'FAIL') + ' H3 ' + ' '.join(f"{k}={res[k]}" for k in ('C_config_identical', 'N_ok', 'E_ok', 'W_ok', 'F_ok', 'W_median', 'W_frac_le_0.30')), file=sys.stderr)
sys.exit(0 if ok else 1)
