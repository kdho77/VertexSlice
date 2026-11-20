#!/usr/bin/env python3
"""inner_shift.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Decision D3 (Kendon 2026-11-20 10:57 AM MT, "Yes that seems correct."): on ApexCore (classic walls) the inner wall and
the infill boundary may be placed closer to the outer wall, so the skin stops floating on the 0.33 mm air sheet that the
sqrt(2) belt stretch puts between the outer and inner wall. Bead widths, speeds and the outer wall stay exactly as live.
The runner runs this gate (and outer_lock/interior_lock with --d3) for ApexCore only; ApexLight stays fully locked.

usage: inner_shift.py LIVE.gcode CANDIDATE.gcode [--json out.json]   (needs shapely 2; imports outer_lock.py)
Exit 0 PASS, 1 FAIL. Physical G-code frame; width w = E/mm*A/H + H(1-pi/4), H = 0.46 (same model as the other gates).

Numbers behind the thresholds (measured on live ApexCore 918c5373, outer bead 1.04 mm, inner bead 1.12 mm):
  live outer->inner centre spacing: 1.000 mm on X-facing faces (unstretched), 1.414 mm on Y-facing faces (sqrt(2) x 1.000);
  touching spacing in the flow model (flat faces meet) = (1.04 + 1.12)/2 - H(1-pi/4) = 0.981 mm;
  so the inner wall may come in by at most 1.414 - 0.981 = 0.433 mm -> SHIFT_MAX = 0.434 + tolerance 0.02 = 0.454 mm.
  live infill comes no closer than 1.70 mm to the outer centreline (X faces) and sits at ~2.40 mm on Y faces (sqrt(2) x 1.70),
  so the infill boundary may come in by at most (sqrt(2)-1) x 1.70 = 0.704 mm (INF_SHIFT_MAX = 0.734 mm, reported), and
  changed infill may exist only within INF_BAND = 2.40 + 0.80 = 3.20 mm of the outer centreline (the interior pattern
  beyond that band must stay byte-for-byte on live's lines).
  Crowding: live's worst inner/infill overlap into the outer bead is 0.087 mm (X faces, rounded bead edges just meeting).
  CROWD_MAX_MM = H(1-pi/4) + 0.025 = 0.124 mm (11.9% of the 1.04 mm outer bead): beyond the rounded-edge allowance any
  overlap is material squeezed into the skin, which pushes it outward (the closed PR #37 failure mode).

Checks (all must pass):
 S0 rotation (seams back on the bottom, Kendon 11:38 AM / 12:17 PM MT): postprocess_g2b restarts closed Inner wall loops at the
    outer seam's X, so moving the outer seam to the plantar face moves the inner-wall start too. Before S1-S4 each closed
    Inner wall run (end within ROT_CLOSE 0.011 mm of its start) is un-split at its start (first and last segment merged
    when they are one straight segment cut at the start: junction <= 0.002 mm off the chord, same F, E/mm per
    outer_lock._epm_ok) and the candidate loop is cyclically re-indexed to start at the vertex matching live's start
    (the shift with the smallest summed vertex distance when the counts are equal, else the vertex nearest live's start).
    Nothing else is relaxed: S1-S7 then run on these runs unchanged. Rotated runs are reported.
 S1 structure: per layer the candidate has the same number of Inner wall runs in the same order; each run has no more
    segments than live (a pure offset adds no vertices) and at least 95% of live's; width-step count equal to live (both counted on segments >= 0.5 mm, see QUANT_LEN).
 S2 bead: per Inner wall segment (>= 0.5 mm; shorter ones are E/coordinate-quantum noise) E/mm within 1% and F identical (0.01 mm/s) to the live segment
    (paired by index when counts are equal, else the nearest live segment of the paired run).
 S3 shift: every Inner wall vertex (and 0.25 mm samples when counts differ) moves at most SHIFT_MAX; it never ends farther
    from the live outer-wall centreline than live (+0.01 mm: never outward); and the move is toward the outer wall
    (decrease in outer distance >= 0.7 x move length - 0.02 mm: no sideways sliding). Both-way Hausdorff <= SHIFT_MAX.
 S4 no zig-zag: per run, total absolute turning <= live + 0.2 rad and the number of turns sharper than 90 deg <= live.
 S5 crowding (widths of segments < 0.5 mm = median of the type): 0.25 mm samples of candidate Inner wall and infill beads: overlap into the nearest live outer/overhang bead
    ((w_outer + w_bead)/2 - centre distance) <= CROWD_MAX_MM, and the count above it <= live's (live has 0).
 S6 infill (Sparse infill, Internal solid infill, Solid infill): per type the F values are a subset of live's; length-weighted
    median E/mm within 1% of live; every segment >= 1 mm has E/mm within [0.99 x live min, 1.01 x live max] of that type;
    candidate infill that is not on a live infill line (> 0.02 mm away) must be within INF_BAND of the outer centreline and
    within INF_EXT_MAX (2.5 mm: a line end re-clipped INF_SHIFT_MAX closer to the wall at a grazing angle down to ~17 deg)
    of a live infill line of that type; live infill missing from the candidate must be within INF_BAND too; infill volume
    per type >= 99% of live (the region only grows). Growth is reported, not capped here: on these thin 45-degree sections
    moving the boundary up to 0.7 mm on both Y faces legitimately adds ~10% sparse infill (more in the small internal-solid
    areas); print_budget is the cost limit.
 S7 infill vs inner wall: candidate infill overlap into the candidate Inner wall bead never exceeds live's worst + 0.05 mm.
"""
import argparse, hashlib, json, math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outer_lock as ol
import shapely
from shapely import STRtree, LineString

H = 0.46
ADJ = H * (1 - math.pi / 4)
SHIFT_MAX = 0.454
INF_SHIFT_MAX = 0.734
INF_EXT_MAX = 2.50
INF_BAND = 3.20
CROWD_MAX_MM = ADJ + 0.025
SEG_MIN_RATIO = 0.95
TURN_TOL = 0.2
TOWARD_K = 0.7
STEP = 0.25
QUANT_LEN = 0.5
INFILL = ("Sparse infill", "Internal solid infill", "Solid infill")
OUTER = ("Outer wall", "Overhang wall")


def width(ev):
    return ev[7] * ol.A_FIL / H + ADJ


def samples(segs):
    """0.25 mm samples along the beads; a segment shorter than QUANT_LEN takes the median width of the longer segments of
    the same type in the list (of all its segments if none is longer) (its own E/mm is writer-quantum noise)"""
    pts, ws = [], []
    med = {}
    for t in set(e[1] for e in segs):
        v = sorted(width(e) for e in segs if e[1] == t and math.hypot(e[4] - e[2], e[5] - e[3]) >= QUANT_LEN)
        if not v:
            v = sorted(width(e) for e in segs if e[1] == t)
        med[t] = v[len(v) // 2]
    for e in segs:
        d = math.hypot(e[4] - e[2], e[5] - e[3]); n = max(1, int(d / STEP))
        for k in range(n):
            t = (k + 0.5) / n
            pts.append((e[2] + t * (e[4] - e[2]), e[3] + t * (e[5] - e[3])))
            ws.append(width(e) if d >= QUANT_LEN else med[e[1]])
    return np.array(pts).reshape(-1, 2), np.array(ws)


def lines(segs):
    return [LineString([(e[2], e[3]), (e[4], e[5])]) for e in segs]


def nearest(tree, pts):
    if len(pts) == 0:
        return np.array([], int), np.array([])
    (pi, si), d = tree.query_nearest(shapely.points(pts), return_distance=True, all_matches=False)
    out_i = np.zeros(len(pts), int); out_d = np.full(len(pts), np.inf)
    out_i[pi] = si; out_d[pi] = d
    return out_i, out_d


def seglen(e):
    return math.hypot(e[4] - e[2], e[5] - e[3])


def wsteps(r):
    """width steps counted on segments >= QUANT_LEN only (E/mm of shorter segments is dominated by the 0.001 mm
    coordinate and 1e-5 E quanta of the G-code writer)"""
    v = [e[7] for e in r["s"] if seglen(e) >= QUANT_LEN]
    return sum(1 for a, b in zip(v, v[1:]) if abs(b - a) > ol.E_REL * a)


def turning(r):
    s = r["s"]; tot = 0.0; sharp = 0
    for a, b in zip(s, s[1:]):
        a1 = math.atan2(a[5] - a[3], a[4] - a[2]); a2 = math.atan2(b[5] - b[3], b[4] - b[2])
        d = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi); tot += d
        if d > math.pi / 2: sharp += 1
    return tot, sharp


def crowd(lay, outer_tree, outer_w, types):
    segs = [e for e in lay if e[0] == "X" and e[1] in types]
    if not segs or outer_tree is None:
        return 0, 0.0
    p, w = samples(segs)
    si, d = nearest(outer_tree, p)
    ov = (outer_w[si] + w) / 2 - d
    return int((ov > CROWD_MAX_MM + 1e-9).sum()), float(ov.max()) if len(ov) else 0.0


ROT_CLOSE = 0.011


def _closed(r):
    s = r["s"]
    return len(s) >= 3 and math.hypot(s[-1][4] - s[0][2], s[-1][5] - s[0][3]) <= ROT_CLOSE


def unsplit(r):
    """closed loop: merge the last and first segment when they are one straight segment cut at the start"""
    if not _closed(r):
        return r
    s = list(r["s"]); a, b = s[-1], s[0]
    la = math.hypot(a[4] - a[2], a[5] - a[3]); lb = math.hypot(b[4] - b[2], b[5] - b[3])
    cx, cy = b[4] - a[2], b[5] - a[3]; L = math.hypot(cx, cy)
    if la <= 1e-9 or lb <= 1e-9 or L <= 1e-9 or abs(a[8] - b[8]) > ol.F_TOL:
        return r
    if ((a[4] - a[2]) * cx + (a[5] - a[3]) * cy) <= 0 or ((b[4] - b[2]) * cx + (b[5] - b[3]) * cy) <= 0:
        return r
    if abs((a[4] - a[2]) * cy - (a[5] - a[3]) * cx) / L > 0.002 or not ol._epm_ok(a[7], b[7], b[6], lb):
        return r
    m = ("X", a[1], a[2], a[3], b[4], b[5], a[6] + b[6], (a[6] + b[6]) / L, a[8])
    return {"t": r["t"], "s": [m] + s[1:-1], "end": (b[4], b[5])}


def align(r0, r1):
    """re-index closed candidate loop r1 to start where live loop r0 starts; returns (r1', rotated?)"""
    if not (_closed(r0) and _closed(r1)):
        return r1, False
    s0, s1 = r0["s"], r1["s"]; n = len(s1)
    if len(s0) == n:
        best = min(range(n), key=lambda k: sum(math.hypot(s1[(i + k) % n][2] - s0[i][2], s1[(i + k) % n][3] - s0[i][3]) for i in range(n)))
    else:
        best = min(range(n), key=lambda k: math.hypot(s1[k][2] - s0[0][2], s1[k][3] - s0[0][3]))
    if best == 0:
        return r1, False
    s = s1[best:] + s1[:best]
    return {"t": r1["t"], "s": s, "end": (s[-1][4], s[-1][5])}, True


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("live"); ap.add_argument("cand"); ap.add_argument("--json")
    a = ap.parse_args()
    L, _ = ol.parse(a.live); C, _ = ol.parse(a.cand)
    fails = []
    rep = {"inner_shift_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "params": {"SHIFT_MAX": SHIFT_MAX, "INF_SHIFT_MAX": INF_SHIFT_MAX, "INF_BAND": INF_BAND, "CROWD_MAX_MM": round(CROWD_MAX_MM, 4),
                      "SEG_MIN_RATIO": SEG_MIN_RATIO, "TURN_TOL": TURN_TOL, "TOWARD_K": TOWARD_K}}
    if len(L) != len(C):
        fails.append("layer count differs"); 
    n = min(len(L), len(C))
    cnt = {k: 0 for k in ("S1", "S2", "S3_far", "S3_out", "S3_side", "S4", "S6_off", "S6_band")}
    worst = {"shift": 0.0, "outward": 0.0, "crowd_cand": 0.0, "crowd_live": 0.0, "inf_shift": 0.0, "inf_inner_ov_live": 0.0, "inf_inner_ov_cand": 0.0}
    crowd_c = crowd_l = 0
    rotated_inner = 0
    samples_txt = []
    # S6 global stats
    st = {}
    for t in INFILL:
        st[t] = {"F_l": set(), "F_c": set(), "e_l": [], "e_c": [], "vol_l": 0.0, "vol_c": 0.0}
    for LL, key in ((L, "l"), (C, "c")):
        for lay in LL:
            for e in lay:
                if e[0] == "X" and e[1] in INFILL:
                    d = math.hypot(e[4] - e[2], e[5] - e[3]); s_ = st[e[1]]
                    s_["F_" + key].add(round(e[8], 2)); s_["e_" + key].append((e[7], d)); s_["vol_" + key] += e[7] * ol.A_FIL * d
    for li in range(n):
        lo = [e for e in L[li] if e[0] == "X" and e[1] in OUTER]
        tree = STRtree(lines(lo)) if lo else None
        ow = np.array([width(e) for e in lo]) if lo else None
        def dout(pts):
            return nearest(tree, pts)[1] if tree is not None else np.full(len(pts), np.inf)
        # S1-S4 inner wall
        ri = [r for r in ol.runs(L[li], ("Inner wall",))]
        rc = [r for r in ol.runs(C[li], ("Inner wall",))]
        if len(ri) != len(rc):
            cnt["S1"] += 1
            if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall runs %d != live %d" % (li, len(rc), len(ri)))
        for r0, r1 in zip(ri, rc):
            r0 = unsplit(r0); r1 = unsplit(r1)
            r1, rotd = align(r0, r1)
            rotated_inner += rotd
            n0, n1 = len(r0["s"]), len(r1["s"])
            if n1 > n0 or n1 < math.floor(SEG_MIN_RATIO * n0) or wsteps(r0) != wsteps(r1):
                cnt["S1"] += 1
                if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall run segments %d vs live %d, width steps %d vs %d" % (li, n1, n0, wsteps(r1), wsteps(r0)))
                continue
            t0, k0 = turning(r0); t1, k1 = turning(r1)
            if t1 > t0 + TURN_TOL or k1 > k0:
                cnt["S4"] += 1
                if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall zig-zag (turning %.2f vs %.2f rad, sharp %d vs %d)" % (li, t1, t0, k1, k0))
            if n1 == n0:
                pairs = list(zip(r0["s"], r1["s"]))
                bad2 = any((min(seglen(p), seglen(q)) >= QUANT_LEN and abs(q[7] - p[7]) / max(p[7], 1e-12) > ol.E_REL) or abs(q[8] - p[8]) > ol.F_TOL for p, q in pairs)
                v0 = np.array([(p[4], p[5]) for p in r0["s"]] + [(r0["s"][0][2], r0["s"][0][3])])
                v1 = np.array([(q[4], q[5]) for q in r1["s"]] + [(r1["s"][0][2], r1["s"][0][3])])
            else:
                l0 = STRtree(lines(r0["s"]))
                mids = np.array([((q[2] + q[4]) / 2, (q[3] + q[5]) / 2) for q in r1["s"]])
                si, _ = nearest(l0, mids)
                bad2 = any((seglen(q) >= QUANT_LEN and seglen(r0["s"][j]) >= QUANT_LEN and abs(q[7] - r0["s"][j][7]) / max(r0["s"][j][7], 1e-12) > ol.E_REL) or abs(q[8] - r0["s"][j][8]) > ol.F_TOL for q, j in zip(r1["s"], si))
                v1, _ = samples(r1["s"])
                ln0 = LineString([(p[2], p[3]) for p in r0["s"]] + [(r0["s"][-1][4], r0["s"][-1][5])])
                proj = [ln0.interpolate(ln0.project(shapely.Point(p))) for p in v1]
                v0 = np.array([(q.x, q.y) for q in proj])
            if bad2:
                cnt["S2"] += 1
                if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall bead E/mm or F differs from live" % li)
            mv = np.hypot(v1[:, 0] - v0[:, 0], v1[:, 1] - v0[:, 1])
            d0, d1 = dout(v0), dout(v1)
            fin = np.isfinite(d0) & np.isfinite(d1)
            worst["shift"] = max(worst["shift"], float(mv.max()) if len(mv) else 0.0)
            if len(mv) and mv.max() > SHIFT_MAX + 1e-9:
                cnt["S3_far"] += 1
                if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall moved %.3f mm > %.3f" % (li, mv.max(), SHIFT_MAX))
            if fin.any():
                outw = (d1 - d0)[fin]
                worst["outward"] = max(worst["outward"], float(outw.max()))
                if outw.max() > 0.01:
                    cnt["S3_out"] += 1
                    if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall moved away from the outer wall by %.3f mm" % (li, outw.max()))
                side = ((d0 - d1) < TOWARD_K * mv - 0.02) & fin & (mv > 0.03)
                if side.any():
                    cnt["S3_side"] += 1
                    if len(samples_txt) < 12: samples_txt.append("layer %d: Inner wall moved sideways (not toward the outer wall) at %d points" % (li, int(side.sum())))
        # S5 crowding
        if tree is not None:
            cc, mc = crowd(C[li], tree, ow, ("Inner wall",) + INFILL)
            cl, ml = crowd(L[li], tree, ow, ("Inner wall",) + INFILL)
            crowd_c += cc; crowd_l += cl
            worst["crowd_cand"] = max(worst["crowd_cand"], mc); worst["crowd_live"] = max(worst["crowd_live"], ml)
            if cc > cl and len(samples_txt) < 12:
                samples_txt.append("layer %d: %d inner/infill samples overlap the outer bead by more than %.3f mm (max %.3f)" % (li, cc, CROWD_MAX_MM, mc))
        # S6 / S7 infill geometry
        for t in INFILL:
            fl = [e for e in L[li] if e[0] == "X" and e[1] == t]
            fc = [e for e in C[li] if e[0] == "X" and e[1] == t]
            if not fl and not fc:
                continue
            pl, _ = samples(fl) if fl else (np.zeros((0, 2)), None)
            pc, _ = samples(fc) if fc else (np.zeros((0, 2)), None)
            tl = STRtree(lines(fl)) if fl else None
            tc = STRtree(lines(fc)) if fc else None
            _, dc = nearest(tl, pc) if tl is not None else (None, np.full(len(pc), np.inf))
            _, dl = nearest(tc, pl) if tc is not None else (None, np.full(len(pl), np.inf))
            newp = pc[dc > 0.02]; gone = pl[dl > 0.02]
            if len(newp):
                worst["inf_shift"] = max(worst["inf_shift"], float(dc[dc > 0.02].max()))
                if dc.max() > INF_EXT_MAX:
                    cnt["S6_off"] += 1
                    if len(samples_txt) < 12: samples_txt.append("layer %d: %s lies %.3f mm from any live %s line (> %.2f)" % (li, t, dc.max(), t, INF_EXT_MAX))
                if (dout(newp) > INF_BAND).any():
                    cnt["S6_band"] += 1
                    if len(samples_txt) < 12: samples_txt.append("layer %d: %s changed farther than %.2f mm from the outer wall (pattern changed)" % (li, t, INF_BAND))
            if len(gone) and (dout(gone) > INF_BAND).any():
                cnt["S6_band"] += 1
                if len(samples_txt) < 12: samples_txt.append("layer %d: live %s removed farther than %.2f mm from the outer wall" % (li, t, INF_BAND))
        for LL, k in ((L, "inf_inner_ov_live"), (C, "inf_inner_ov_cand")):
            iw = [e for e in LL[li] if e[0] == "X" and e[1] == "Inner wall"]
            fi = [e for e in LL[li] if e[0] == "X" and e[1] in INFILL]
            if iw and fi:
                p, w = samples(fi); si, d = nearest(STRtree(lines(iw)), p)
                iww = np.array([width(e) for e in iw])
                worst[k] = max(worst[k], float(((iww[si] + w) / 2 - d).max()))
    rep["counts"] = cnt; rep["crowd_samples_over_cap"] = {"live": crowd_l, "cand": crowd_c}
    rep["worst"] = {k: round(v, 4) for k, v in worst.items()}
    rep["rotated_inner_loops"] = rotated_inner
    names = {"S1": "S1 Inner wall run structure changed (runs/segments/width steps) on %d runs/layers",
             "S2": "S2 Inner wall bead width (E/mm) or speed changed on %d runs",
             "S3_far": "S3 Inner wall moved more than SHIFT_MAX on %d runs",
             "S3_out": "S3 Inner wall moved AWAY from the outer wall on %d runs",
             "S3_side": "S3 Inner wall moved sideways (not a pure offset toward the outer wall) on %d runs",
             "S4": "S4 Inner wall zig-zag / extra turning on %d runs",
             "S6_off": "S6 infill lies more than INF_EXT_MAX from any live infill line on %d layer-types",
             "S6_band": "S6 infill changed outside the boundary band (pattern change) on %d layer-types"}
    for k, v in cnt.items():
        if v:
            fails.append(names[k] % v)
    if crowd_c > crowd_l:
        fails.append("S5 inner/infill beads crowd the outer bead: %d samples overlap it by more than %.3f mm (live %d; max %.3f mm)" % (crowd_c, CROWD_MAX_MM, crowd_l, worst["crowd_cand"]))
    s6 = {}
    for t, s_ in st.items():
        if not s_["e_l"] and not s_["e_c"]:
            continue
        def wmed(v):
            v = sorted(v); tot = sum(d for _, d in v); acc = 0.0
            for e, d in v:
                acc += d
                if acc >= tot / 2: return e
            return 0.0
        ml, mc = wmed(s_["e_l"]), wmed(s_["e_c"])
        lmin = min(e for e, _ in s_["e_l"]) if s_["e_l"] else 0; lmax = max(e for e, _ in s_["e_l"]) if s_["e_l"] else 0
        outr = sum(1 for e, d in s_["e_c"] if d >= 1.0 and (e < 0.99 * lmin or e > 1.01 * lmax))
        vr = s_["vol_c"] / s_["vol_l"] if s_["vol_l"] else float("inf")
        s6[t] = {"F_live": sorted(s_["F_l"]), "F_cand": sorted(s_["F_c"]), "median_e_live": ml, "median_e_cand": mc, "segs_e_out_of_range": outr, "vol_ratio": round(vr, 4)}
        if not s_["F_c"] <= s_["F_l"]:
            fails.append("S6 %s speeds %s not all in live %s" % (t, sorted(s_["F_c"] - s_["F_l"]), sorted(s_["F_l"])))
        if ml and abs(mc - ml) / ml > 0.01:
            fails.append("S6 %s median E/mm %.5f vs live %.5f (> 1%%: width/density changed)" % (t, mc, ml))
        if outr:
            fails.append("S6 %s %d segments >= 1 mm with E/mm outside live range" % (t, outr))
        if vr < 0.99:
            fails.append("S6 %s volume ratio %.3f < 0.99 of live (infill removed)" % (t, vr))
    rep["infill"] = s6
    if worst["inf_inner_ov_cand"] > worst["inf_inner_ov_live"] + 0.05:
        fails.append("S7 infill overlaps the inner wall by %.3f mm (live worst %.3f + 0.05)" % (worst["inf_inner_ov_cand"], worst["inf_inner_ov_live"]))
    rep["samples"] = samples_txt
    rep["result"] = "PASS" if not fails else "FAIL"; rep["failures"] = fails
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
