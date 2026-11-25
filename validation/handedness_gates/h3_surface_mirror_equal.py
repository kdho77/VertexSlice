#!/usr/bin/env python3
"""h3_surface_mirror_equal.py - FROZEN gate H3 v2 (OrthoCAD PM, 2026-11-24 5:40 PM MT). Do not edit; checked by SHA256SUMS.
Kendon's locked rule: surface quality untouched, no scope growth. The handedness fix may only mirror the part across
the belt. So the candidate must be the LIVE file's mirror, with the same settings.
v2 (measurement fix, thresholds unchanged): v1 compared 0.5 mm point samples, thinned to 6000 points, nearest point to
nearest point, so sampling alone added up to ~0.3 mm and only a coordinate-exact mirror could pass; a real re-slice of the
mirrored mesh (PM local slice of PR #52, 5:20 PM MT) measured 0.35 mm median from sampling alone. v2 samples every 0.05 mm
with no thinning (KD-tree). v1 F compared exact per-segment feed values; Orca's min-layer-time slowdown rescales a few
slow segments by <1% when path lengths change by rounding, so v2 compares the outer-wall feed distribution instead.
usage: h3_surface_mirror_equal.py CANDIDATE.gcode LIVE.gcode     (same material, same input STL)   exit 0 PASS, 1 FAIL
  C  CONFIG_BLOCK (between ; CONFIG_BLOCK_START/END) byte-identical.
  N  same layer count (';Z:' markers), same count of ';TYPE:Outer wall' blocks within 2%.
  E  total extrusion (sum of positive E) within 1.0%.
  W  outer-wall geometry: per layer, candidate outer-wall segments (sampled every 0.05 mm) vs LIVE outer-wall segments
     mirrored across the belt (X -> cxL + cxC - X, cx = bottom-footprint X centre of each file). Symmetric max nearest
     distance <= 0.30 mm on >= 95% of layers that have outer wall in both, and median over layers <= 0.15 mm.
  F  outer-wall feedrate (F on Outer wall extrusions, weighted by segment length): mean within 0.5% and the 5/25/50/75/95
     percentiles each within 1% of LIVE.
"""
import json, re, sys
import numpy as np
from scipy.spatial import cKDTree
num = re.compile(r'([XYZEF])(-?\d*\.?\d+)')
STEP = 0.05

def parse(fn):
    txt = open(fn, errors='replace').read()
    cfg = txt[txt.find('; CONFIG_BLOCK_START'):txt.find('; CONFIG_BLOCK_END')]
    x = y = z = 0.0; f = 0.0; t = ''; layer = -1
    layers = {}; e_tot = 0.0; n_outer = 0; bot = []; fv = []; fw = []
    for line in txt.split('\n'):
        if line.startswith(';Z:'):
            layer += 1; continue
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
                p = np.array([px, py, pz]); q = np.array([x, y, z]); ln = float(np.linalg.norm(q - p))
                fv.append(f); fw.append(ln)
                n = max(2, int(ln / STEP) + 1)
                layers.setdefault(layer, []).append(p + (q - p) * np.linspace(0, 1, n)[:, None])
    layers = {k: np.vstack(v) for k, v in layers.items()}
    return dict(cfg=cfg, layers=layers, e=e_tot, n_outer=n_outer, nlayers=layer + 1, fv=np.array(fv), fw=np.array(fw),
                cx=(min(bot) + max(bot)) / 2 if bot else 0.0)

def wq(v, w, qs):
    o = np.argsort(v); v = v[o]; c = np.cumsum(w[o]); c = c / c[-1]
    return [float(v[np.searchsorted(c, q / 100.0)]) for q in qs]

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
    ds.append(max(float(cKDTree(b).query(a)[0].max()), float(cKDTree(a).query(b)[0].max())))
ds = np.array(ds) if ds else np.array([99.0])
res['W_layers'] = int(len(ds)); res['W_frac_le_0.30'] = round(float((ds <= 0.30).mean()), 4); res['W_median'] = round(float(np.median(ds)), 4)
res['W_ok'] = res['W_frac_le_0.30'] >= 0.95 and res['W_median'] <= 0.15
if len(C['fv']) and len(L['fv']):
    mc = float(np.average(C['fv'], weights=C['fw'])); ml = float(np.average(L['fv'], weights=L['fw']))
    qs = [5, 25, 50, 75, 95]; qc = wq(C['fv'], C['fw'], qs); ql = wq(L['fv'], L['fw'], qs)
    res['F_mean'] = [round(mc, 2), round(ml, 2)]; res['F_pct'] = [qc, ql]
    res['F_ok'] = abs(mc - ml) <= 0.005 * ml and all(abs(a - b) <= 0.01 * b for a, b in zip(qc, ql))
else:
    res['F_ok'] = False
ok = res['C_config_identical'] and res['N_ok'] and res['E_ok'] and res['W_ok'] and res['F_ok']
res['pass'] = ok
print(json.dumps(res, indent=1))
print(('PASS' if ok else 'FAIL') + ' H3v2 ' + ' '.join(f"{k}={res[k]}" for k in ('C_config_identical', 'N_ok', 'E_ok', 'W_ok', 'F_ok', 'W_median', 'W_frac_le_0.30')), file=sys.stderr)
sys.exit(0 if ok else 1)
