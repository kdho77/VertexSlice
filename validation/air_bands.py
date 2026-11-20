#!/usr/bin/env python3
"""air_bands.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Whole-length air check: the same column model as validation/belt_air_columns.py (X step 0.5, columns whose
bead span <= 4 mm, air > 0.05 mm with no infill crossing; H=0.46), measured in EVERY 10% band of the layer
range (0-10, 10-20, ... 90-100). The candidate must be no worse than LIVE in every band, both in columns
and in mm3 (air_noinfill_cols and air_noinfill_mm3). This is in addition to the two Cura bands in air_vs_cura.py.
Self-check: its 0-15% and 15-30% numbers on the candidate must equal belt_air_columns.py's, otherwise FAIL.
usage: air_bands.py LIVE.gcode CANDIDATE.gcode [--air validation/belt_air_columns.py] [--json out.json]
"""
import argparse, collections, hashlib, json, math, os, subprocess, sys
import numpy as np

AIR_SHA = "08a65cb5b5dce5f26f9bead182679cdd50b9a3dc61202fc74090c6d8898e116f"
H = 0.46
A = math.pi * (1.75 / 2) ** 2
ADJ = H * (1 - 0.25 * math.pi)


def load(fn):
    layers = []
    x = y = None
    typ = None
    for line in open(fn, errors="replace"):
        if line.startswith(";LAYER_CHANGE"):
            layers.append([])
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
        if line[:3] == "G1 ":
            w = dict((m[0], float(m[1:])) for m in line.split(";")[0].split()[1:] if m[0] in "XYZE")
            nx, ny = w.get("X", x), w.get("Y", y)
            e = w.get("E", 0)
            if layers and e > 0 and x is not None and (nx, ny) != (x, y):
                l = math.dist((x, y), (nx, ny))
                layers[-1].append((x, y, nx, ny, e * A / l / H + ADJ, typ))
            x, y = nx, ny
    return layers


def band(layers, lo, hi):
    N = len(layers)
    r = collections.Counter()
    ws = collections.defaultdict(float)
    for i in range(int(lo * N), int(hi * N)):
        sg = layers[i]
        if not sg:
            continue
        xs = [v for s in sg for v in (s[0], s[2])]
        for cx in np.arange(math.floor(min(xs)) + 0.25, max(xs), 0.5):
            iv = []
            for ax, ay, bx, by, w, t in sg:
                if abs(bx - ax) < 1e-9:
                    continue
                tt = (cx - ax) / (bx - ax)
                if tt < 0 or tt > 1:
                    continue
                yy = ay + tt * (by - ay)
                sl = abs((by - ay) / (bx - ax))
                half = min(w / 2 * math.sqrt(1 + sl * sl), w * 2)
                iv.append((yy - half, yy + half, t, w))
            if len(iv) < 2:
                continue
            iv.sort()
            span = max(b for a, b, t, w in iv) - iv[0][0]
            if span > 4:
                continue
            r["cols"] += 1
            air = 0
            m = iv[0][1]
            for a, b, t, w in iv[1:]:
                if a > m + 0.05:
                    air += a - m
                m = max(m, b)
            types = set(t for a, b, t, w in iv)
            inf = any(t in ("Sparse infill", "Internal solid infill", "Solid infill", "Top surface", "Bottom surface") for t in types)
            if air > 0 and not inf:
                r["air_noinfill_cols"] += 1
                ws["air_noinfill_mm3"] += air * 0.5 * H
    return {"cols": r["cols"], "air_noinfill_cols": r["air_noinfill_cols"], "air_noinfill_mm3": round(ws["air_noinfill_mm3"], 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live")
    ap.add_argument("cand")
    ap.add_argument("--air", default="validation/belt_air_columns.py")
    ap.add_argument("--json")
    a = ap.parse_args()
    fails = []
    sha = hashlib.sha256(open(a.air, "rb").read()).hexdigest()
    if sha != AIR_SHA:
        fails.append("belt_air_columns.py sha256 %s != %s" % (sha, AIR_SHA))
    L, C = load(a.live), load(a.cand)
    bands = [(k / 10.0, (k + 1) / 10.0) for k in range(10)]
    rows = []
    for lo, hi in bands:
        bl, bc = band(L, lo, hi), band(C, lo, hi)
        rows.append({"band": "%d-%d%%" % (round(lo * 100), round(hi * 100)), "live": bl, "cand": bc})
        if bc["air_noinfill_cols"] > bl["air_noinfill_cols"]:
            fails.append("band %d-%d%% air_noinfill_cols %d > live %d" % (round(lo * 100), round(hi * 100), bc["air_noinfill_cols"], bl["air_noinfill_cols"]))
        if bc["air_noinfill_mm3"] > bl["air_noinfill_mm3"] + 1e-9:
            fails.append("band %d-%d%% air_noinfill_mm3 %.2f > live %.2f" % (round(lo * 100), round(hi * 100), bc["air_noinfill_mm3"], bl["air_noinfill_mm3"]))
    # self-check against the byte-identical belt_air_columns.py on the candidate
    p = subprocess.run([sys.executable, a.air, a.cand], capture_output=True, text=True, env=dict(os.environ, H=str(H), BELT_AIR_GATE="0"))
    ref = json.loads(p.stdout.strip().splitlines()[-1])
    mine = {"toe_0_15": band(C, 0, 0.15), "b15_30": band(C, 0.15, 0.30)}
    for b in ("toe_0_15", "b15_30"):
        if ref[b].get("air_noinfill_cols", 0) != mine[b]["air_noinfill_cols"] or abs(ref[b].get("air_noinfill_mm3", 0) - mine[b]["air_noinfill_mm3"]) > 0.011:
            fails.append("self-check: %s differs from belt_air_columns.py (%s vs %s)" % (b, mine[b], {k: ref[b].get(k, 0) for k in ("air_noinfill_cols", "air_noinfill_mm3")}))
    rep = {"result": "PASS" if not fails else "FAIL", "failures": fails, "bands": rows, "air_bands_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest()}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({"result": rep["result"], "failures": fails}, indent=1))
    for r in rows:
        print("%-8s live cols/mm3 %5d/%8.2f  cand %5d/%8.2f" % (r["band"], r["live"]["air_noinfill_cols"], r["live"]["air_noinfill_mm3"], r["cand"]["air_noinfill_cols"], r["cand"]["air_noinfill_mm3"]))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
