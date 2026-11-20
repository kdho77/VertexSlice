#!/usr/bin/env python3
"""skin_bond.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.
Kendon 2026-11-20 10:27 AM MT: "even where I have infill the top layer is not connected to the infill ... the skin has
space between it and the infill and when I bend it it bubbles up and folds".

usage: skin_bond.py CANDIDATE.gcode [--live LIVE.gcode] [--relative-only] [--json out.json] [--H 0.46]

On the belt the foot-facing top skin and the shoe-facing bottom skin of the heel/arch are the top-most and bottom-most
wall beads of every layer cross-section (G-code Y). For each X column (COL_STEP) of each layer in the heel/arch band
(BAND_LO..BAND_HI of the layer count, first and last extruding layer excluded) whose bead span is > THICK_MIN mm:
  skin bead   = top-most (max Y) / bottom-most (min Y) bead; counted only if it is a wall bead.
  contact     = the next bead toward the inside overlaps the skin bead or leaves <= CONTACT_TOL mm of air.
  chain       = a contiguous run of touching beads (<= CONTACT_TOL) leads from the skin bead to an infill bead.
  unbonded run= consecutive columns in a layer with no contact (mm along X).
Bead width from E: w = E/mm * A_fil / H + H(1 - pi/4); vertical half-extent of a sloped bead = w/2*sqrt(1+slope^2).
PASS needs, for BOTH skins: contact_frac >= CONTACT_MIN, chain_frac >= CHAIN_MIN, unbonded_run_p90 <= RUN_P90_MAX_MM,
and (with --live) none of the three worse than live. --relative-only (used for the synthetic insole_sample.stl pairs)
drops the absolute thresholds and keeps only the no-worse-than-live checks.
"""
import sys, math, json, argparse, hashlib

# ---- PM PARAMETERS (frozen) ----
BAND_LO, BAND_HI = 0.30, 1.00
COL_STEP = 0.5
THICK_MIN = 4.0
CONTACT_TOL = 0.05
CONTACT_MIN = 0.30
CHAIN_MIN = 0.25
RUN_P90_MAX_MM = 12.0
# ---------------------------------
A_FIL = math.pi * (1.75 / 2) ** 2
WALL = ("Outer wall", "Overhang wall", "Inner wall")
INFILL = ("Sparse infill", "Internal solid infill", "Solid infill", "Top surface", "Bottom surface", "Gap infill", "Bridge")


def load(fn, H):
    adj = H * (1 - math.pi / 4)
    layers = []; x = y = None; typ = None; rel = True; E0 = 0.0
    for line in open(fn, errors="replace"):
        if line.startswith(";LAYER_CHANGE"):
            layers.append([]); continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip(); continue
        if line.startswith("M82"): rel = False
        if line.startswith("M83"): rel = True
        if line.startswith("G92"):
            for m in line.split(";")[0].split()[1:]:
                if m[0] == "E": E0 = float(m[1:])
            continue
        if line[:3] in ("G1 ", "G0 "):
            w = {}
            for m in line.split(";")[0].split()[1:]:
                if m[0] in "XYE":
                    try: w[m[0]] = float(m[1:])
                    except ValueError: pass
            nx, ny = w.get("X", x), w.get("Y", y); e = w.get("E", 0.0)
            if not rel and "E" in w: e, E0 = w["E"] - E0, w["E"]
            if layers and e > 0 and x is not None and (nx, ny) != (x, y):
                l = math.dist((x, y), (nx, ny)); layers[-1].append((x, y, nx, ny, e * A_FIL / l / H + adj, typ))
            x, y = nx, ny
    return layers


def column(sg, cx):
    iv = []
    for ax, ay, bx, by, w, t in sg:
        if abs(bx - ax) < 1e-9: continue
        tt = (cx - ax) / (bx - ax)
        if tt < 0 or tt > 1: continue
        yy = ay + tt * (by - ay); sl = abs((by - ay) / (bx - ax)); half = min(w / 2 * math.sqrt(1 + sl * sl), w * 2)
        iv.append((yy - half, yy + half, t, w))
    return iv


def side_stats(iv, top):
    """returns (is_wall_skin, gap_to_next, chain_reaches_infill)"""
    if top:
        seq = sorted(iv, key=lambda v: -v[1]); S = seq[0]
        edge = S[0]; gap = None; chain = False
        rest = [v for v in seq[1:] if v[0] < S[0]]
        if not rest: return S[2] in WALL, None, False
        gap = S[0] - max(v[1] for v in rest)
        # walk down while touching
        cur = edge; pool = sorted(rest, key=lambda v: -v[1])
        while True:
            nxt = [v for v in pool if v[1] >= cur - CONTACT_TOL and v[0] < cur]
            if not nxt: break
            if any(v[2] in INFILL for v in nxt): chain = True; break
            cur = min(v[0] for v in nxt); pool = [v for v in pool if v not in nxt]
        return S[2] in WALL, gap, chain
    seq = sorted(iv, key=lambda v: v[0]); S = seq[0]
    rest = [v for v in seq[1:] if v[1] > S[1]]
    if not rest: return S[2] in WALL, None, False
    gap = min(v[0] for v in rest) - S[1]
    cur = S[1]; pool = list(rest); chain = False
    while True:
        nxt = [v for v in pool if v[0] <= cur + CONTACT_TOL and v[1] > cur]
        if not nxt: break
        if any(v[2] in INFILL for v in nxt): chain = True; break
        cur = max(v[1] for v in nxt); pool = [v for v in pool if v not in nxt]
    return S[2] in WALL, gap, chain


def measure(fn, H):
    L = load(fn, H); N = len(L)
    ext = [i for i, l in enumerate(L) if l]
    first, last = ext[0], ext[-1]
    R = {s: {"cols": 0, "contact": 0, "chain": 0, "gaps": [], "runs": [], "air_mm3": 0.0} for s in ("top", "bottom")}
    for li in range(int(BAND_LO * N), int(math.ceil(BAND_HI * N))):
        if li >= N or li in (first, last) or not L[li]: continue
        sg = L[li]; xs = [v for s in sg for v in (s[0], s[2])]
        x0 = math.floor(min(xs)) + COL_STEP / 2
        run = {"top": 0.0, "bottom": 0.0}
        k = 0
        while x0 + k * COL_STEP < max(xs):
            cx = x0 + k * COL_STEP; k += 1
            iv = column(sg, cx)
            thick = len(iv) >= 2 and (max(v[1] for v in iv) - min(v[0] for v in iv)) > THICK_MIN
            for s in ("top", "bottom"):
                if not thick:
                    if run[s]: R[s]["runs"].append(run[s]); run[s] = 0.0
                    continue
                wall, gap, chain = side_stats(iv, s == "top")
                if not wall or gap is None: continue
                r = R[s]; r["cols"] += 1; r["gaps"].append(gap)
                if gap <= CONTACT_TOL:
                    r["contact"] += 1
                    if run[s]: r["runs"].append(run[s]); run[s] = 0.0
                else:
                    r["air_mm3"] += gap * COL_STEP * H; run[s] += COL_STEP
                if chain: r["chain"] += 1
        for s in run:
            if run[s]: R[s]["runs"].append(run[s])
    out = {"file": fn.split("/")[-1], "layers": N, "band": [BAND_LO, BAND_HI], "excluded_layers": [first + 1, last + 1]}
    for s, r in R.items():
        g = sorted(r["gaps"]); runs = sorted(r["runs"])
        q = lambda a, p: round(a[min(len(a) - 1, int(p * len(a)))], 3) if a else 0.0
        out[s] = {"cols": r["cols"], "contact_frac": round(r["contact"] / max(1, r["cols"]), 3),
                  "chain_frac": round(r["chain"] / max(1, r["cols"]), 3),
                  "gap_under_skin_mm_p10_p50_p90": [q(g, .1), q(g, .5), q(g, .9)],
                  "unbonded_run_p90_mm": q(runs, .9), "unbonded_run_max_mm": runs[-1] if runs else 0.0}
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cand"); ap.add_argument("--live"); ap.add_argument("--json"); ap.add_argument("--relative-only", action="store_true")
    ap.add_argument("--H", type=float, default=0.46); ap.add_argument("--live-H", type=float, default=0.46)
    a = ap.parse_args()
    rep = {"skin_bond_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "params": {"BAND": [BAND_LO, BAND_HI], "COL_STEP": COL_STEP, "THICK_MIN": THICK_MIN, "CONTACT_TOL": CONTACT_TOL,
                      "CONTACT_MIN": CONTACT_MIN, "CHAIN_MIN": CHAIN_MIN, "RUN_P90_MAX_MM": RUN_P90_MAX_MM}}
    c = measure(a.cand, a.H); rep["cand"] = c; fails = []
    for s in ("top", "bottom"):
        m = c[s]
        if a.relative_only: break
        if m["cols"] == 0: fails.append("%s skin: no measurable columns" % s)
        if m["contact_frac"] < CONTACT_MIN: fails.append("%s skin contact_frac %.3f < %.2f" % (s, m["contact_frac"], CONTACT_MIN))
        if m["chain_frac"] < CHAIN_MIN: fails.append("%s skin chain_frac %.3f < %.2f" % (s, m["chain_frac"], CHAIN_MIN))
        if m["unbonded_run_p90_mm"] > RUN_P90_MAX_MM: fails.append("%s skin unbonded_run_p90 %.1f mm > %.1f" % (s, m["unbonded_run_p90_mm"], RUN_P90_MAX_MM))
    if a.relative_only and not a.live: fails.append("--relative-only needs --live")
    if a.live:
        l = measure(a.live, a.live_H); rep["live"] = l
        for s in ("top", "bottom"):
            if c[s]["contact_frac"] < l[s]["contact_frac"]: fails.append("%s skin contact_frac worse than live" % s)
            if c[s]["chain_frac"] < l[s]["chain_frac"]: fails.append("%s skin chain_frac worse than live" % s)
            if c[s]["unbonded_run_p90_mm"] > l[s]["unbonded_run_p90_mm"]: fails.append("%s skin unbonded_run_p90 worse than live" % s)
    rep["failures"] = fails; rep["result"] = "PASS" if not fails else "FAIL"
    if a.json: json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
