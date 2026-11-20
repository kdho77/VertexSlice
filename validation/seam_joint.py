#!/usr/bin/env python3
"""seam_joint.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Why: Kendon 2026-11-20 12:17 PM MT: "fix it and return the seam to the bottom and ABSOLUTLEY UNDER NO CONDITIONS DO YOU
REINTRODUCE ANY OTHE RFUCKING PROBLEMS AND DO NOT DARE BREAK THE DO NOT CROSS THE TOP SURFACE RULE ETC."
The defect PR #31 (dd3ffee1d, 2026-11-18) fixed, from its diff and commit message: before #31 every closed ApexCore
outer/overhang loop started at the belt-edge midpoint (_belt_split) with "retract 0.5 / lift +0.4 Y / prime 0.45" and NO
seam overlap (only a dry 0.7 mm wipe): each layer's joint was 0.05 mm of filament short (~0.12 mm^3) and uncovered, and
because the belt-edge midpoint is the insole midline, the starved joints stacked on top of each other down the sole
("The belt-side midpoint restart stacked a short prime on the insole midline"). #31 primed the retract in full, added a
0.4 mm overlap over the joint before the dry wipe, and (the part being reverted now) moved the start to the top face.
Stacking alone is not the defect: live ApexLight stacks its bottom seams the same way (consecutive starts within 1 mm in
X on most layers) with a full prime. This gate makes the starved / uncovered joint, and any travel that cuts a wall or
the top surface, impossible to bring back while the seam moves to the bottom.

usage: seam_joint.py LIVE.gcode CANDIDATE.gcode [--zero-travel-cuts] [--json out.json]      (needs shapely 2)        Exit 0 PASS, 1 FAIL.
Both files: same model, profile, material, request. Physical G-code frame. Per layer (";LAYER_CHANGE").

 J1 prime: after every retraction (negative E, on a pure-E move or on a wipe move) the pure-E primes before the next
    extruding XY move must restore it: deficit = retracted - primed. SHORT (deficit > E_TOL 1e-4 mm) count and OVER
    (primed - retracted > E_TOL) count must each be <= live's, and 0 where live has 0.
 J2 joint cover: every CLOSED perimeter loop (contiguous Outer/Overhang chain whose path returns to its start within
    0.011 mm after >= 3 segments) longer than SHORT_LOOP 3.0 mm must end with the seam overlap: one extruding segment from
    the start along the loop's first leg of min(OVERLAP 0.4, first leg) +/- 0.011 mm, at the first leg's E/mm (1%, or
    2 E + 2 XY quanta), followed by the dry wipe (;WIPE_START before any other extrusion). MISSING (no/short/off-path/
    under-extruded overlap or no wipe) and LONG (overlap > 0.411 mm: extra plastic on the joint) counts must each be <=
    live's and 0 where live has 0 (ApexLight has no overlap pass, so its counts are simply held at live).
 J3 the PR #31 signature: closed-loop seam starts that sit in a column (|dX| <= 1.0 mm from the previous layer's first
    closed-loop start) AND have a J1 short prime or a J2 missing cover: count <= live, 0 where live has 0. The column
    count itself is reported (informational; ApexLight live is the reference for a bottom seam).
 J4 travel cuts (stricter than check_travel_wall_crossings, which exempts a whole travel when one end is near a wall):
    every non-extruding XY move outside ;WIPE_START/;WIPE_END (seam travel, ;seam inside, ;lift, ;lower, every other
    travel) is intersected with the layer's Outer/Overhang bead centrelines (WALL) and with its Top surface / Internal
    solid infill / Bottom surface centrelines (SURF). An intersection counts unless it lies within END_MM 0.3 mm (along the
    move) of a move endpoint that is itself within END_MM of that same feature (the landing on / leaving from the seam).
    SKIN: in layers whose only extrusion is Outer/Overhang wall (the skin layers, no top-surface feature), any 0.25 mm
    sample of a travel that lies inside a closed perimeter ring and more than SKIN_CLEAR 0.55 mm (half the 1.04 mm bead +
    0.03) from every wall centreline is a travel over the skin.
    Default (ApexLight): WALL / SURF / SKIN counts each <= live's, 0 where live has 0, and NO NEW offender: every
    offending candidate travel must be one of live's offending travels (both endpoints within 0.01 mm).
    --zero-travel-cuts (runner passes it for ApexCore, whose seam travels this change rewrites; Kendon 12:17 PM MT "DO NOT
    DARE BREAK THE DO NOT CROSS THE TOP SURFACE RULE"): WALL, SURF and SKIN must each be 0 in the candidate (live
    ApexCore has 37 / 0 / 4 on orthocad and 2 / 0 / 2 on insole2 by this stricter count, all of them dorsal-seam travels
    that check_travel_wall_crossings exempts as seam approaches; the plantar-start prototype measured 0 / 0 / 0 on both).
"""
import argparse, hashlib, json, math, sys
from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon

E_TOL = 1e-4
E_REL = 0.01
E_ABS = 1.5e-5
SHORT_LOOP = 3.0
OVERLAP = 0.4
TAIL_TOL = 0.011
ROT_CLOSE = 0.011
XY_TOL = 0.01
COLUMN_MM = 1.0
END_MM = 0.3
SKIN_CLEAR = 0.55
STEP = 0.25
PERI = ("Outer wall", "Overhang wall")
SURF = ("Top surface", "Internal solid infill", "Bottom surface")


def parse(fn):
    """layers of events: ('X', type, x0, y0, x1, y1, de) extrusion, ('T', x0, y0, x1, y1) travel, ('R', de) retraction
    (negative E, incl. on a wipe move), ('P', de) pure-E prime, ('W',) wipe start, ('w',) wipe end"""
    layers = [[]]; x = y = z = 0.0; E = 0.0; rel = True; t = None; wipe = False
    for line in open(fn, errors="replace"):
        if line.startswith(";LAYER_CHANGE"):
            layers.append([]); continue
        if line.startswith(";TYPE:"):
            t = line[6:].strip(); continue
        if line.startswith(";WIPE_START"):
            wipe = True; layers[-1].append(("W",)); continue
        if line.startswith(";WIPE_END"):
            wipe = False; layers[-1].append(("w",)); continue
        s = line.split(";")[0].split()
        if not s:
            continue
        c = s[0]
        if c == "M83": rel = True; continue
        if c == "M82": rel = False; continue
        if c == "G92":
            for m in s[1:]:
                if m[0] == "E": E = float(m[1:])
            continue
        if c not in ("G0", "G1"):
            continue
        w = {}
        for m in s[1:]:
            try: w[m[0]] = float(m[1:])
            except ValueError: pass
        nx, ny, nz = w.get("X", x), w.get("Y", y), w.get("Z", z)
        de = 0.0
        if "E" in w:
            de = w["E"] if rel else w["E"] - E
            if not rel: E = w["E"]
        d = math.hypot(nx - x, ny - y)
        if de < -1e-12:
            layers[-1].append(("R", de))
        if d > 1e-9 and de > 1e-12 and not wipe:
            layers[-1].append(("X", t, x, y, nx, ny, de))
        elif d > 1e-9 and not wipe:
            layers[-1].append(("T", x, y, nx, ny))
        elif d <= 1e-9 and de > 1e-12:
            layers[-1].append(("P", de))
        x, y, z = nx, ny, nz
    return layers


def j1(layers):
    short = over = 0; short_at = []
    for li, lay in enumerate(layers):
        ret = pri = 0.0; open_ = False
        for ev in lay:
            if ev[0] == "R":
                if not open_:
                    ret = pri = 0.0
                open_ = True; ret += -ev[1]
            elif ev[0] == "P" and open_:
                pri += ev[1]
            elif ev[0] == "X" and open_:
                if ret - pri > E_TOL:
                    short += 1; short_at.append((li, round(ev[2], 3), round(ev[3], 3)))
                elif pri - ret > E_TOL:
                    over += 1
                open_ = False
    return short, over, short_at


def chains(lay):
    """contiguous Outer/Overhang chains; returns (chain, index of the first event after it)"""
    out = []; cur = None
    for k, ev in enumerate(lay):
        if ev[0] == "X" and ev[1] in PERI:
            if cur and abs(cur[-1][4] - ev[2]) < 1e-9 and abs(cur[-1][5] - ev[3]) < 1e-9:
                cur.append(ev); continue
            if cur: out.append((cur, k))
            cur = [ev]
        else:
            if cur: out.append((cur, k))
            cur = None
    if cur: out.append((cur, len(lay)))
    return out


def seg_len(e):
    return math.hypot(e[4] - e[2], e[5] - e[3])


def j2(layers):
    missing = long_ = loops = 0; missing_at = set(); starts = []
    for li, lay in enumerate(layers):
        first = None
        for ch, kafter in chains(lay):
            if len(ch) < 3:
                continue
            sx, sy = ch[0][2], ch[0][3]
            k = None
            for j in range(2, len(ch)):
                if math.hypot(ch[j][4] - sx, ch[j][5] - sy) <= ROT_CLOSE:
                    k = j
            if k is None:
                continue
            loop, tail = ch[:k + 1], ch[k + 1:]
            if first is None:
                first = (sx, sy)
            if sum(seg_len(e) for e in loop) <= SHORT_LOOP:
                continue
            loops += 1
            f0 = loop[0]; L0 = seg_len(f0); want = min(OVERLAP, L0)
            ok = False
            if sum(seg_len(e) for e in tail) > OVERLAP + TAIL_TOL:
                long_ += 1
            if len(tail) == 1:
                g = tail[0]; Lg = seg_len(g)
                on = abs(Lg - want) <= TAIL_TOL and math.hypot(g[2] - sx, g[3] - sy) <= ROT_CLOSE
                if on:
                    dx, dy = f0[4] - f0[2], f0[5] - f0[3]; L2 = dx * dx + dy * dy
                    u = max(0.0, min(1.0, ((g[4] - f0[2]) * dx + (g[5] - f0[3]) * dy) / L2)) if L2 else 0.0
                    on = math.hypot(g[4] - (f0[2] + u * dx), g[5] - (f0[3] + u * dy)) <= XY_TOL
                epm0 = f0[6] / L0 if L0 else 0.0
                eok = Lg > 0 and (abs(g[6] / Lg - epm0) <= E_REL * epm0 or abs(g[6] - epm0 * Lg) <= 2 * E_ABS + 0.002 * epm0)
                wipe_next = False
                for ev in lay[kafter:]:
                    if ev[0] == "W":
                        wipe_next = True; break
                    if ev[0] == "X":
                        break
                ok = on and eok and wipe_next
            if not ok:
                missing += 1; missing_at.add((li, round(sx, 3), round(sy, 3)))
        starts.append((li, first))
    return missing, long_, loops, missing_at, starts


def j3(starts, short_at, missing_at):
    col = n = bad = 0; prev = None
    for li, st in starts:
        if st is None:
            prev = None; continue
        n += 1
        incol = prev is not None and abs(st[0] - prev[0]) <= COLUMN_MM
        col += incol
        key = (li, round(st[0], 3), round(st[1], 3))
        deficit = key in missing_at or any(s[0] == li and math.hypot(s[1] - st[0], s[2] - st[1]) <= 0.02 for s in short_at)
        if incol and deficit:
            bad += 1
        prev = st
    return bad, col, n


def cuts(travels, feats, tol=END_MM):
    if not feats:
        return 0, [], []
    tree = STRtree(feats); n = 0; ex = []; off = []
    for (x0, y0, x1, y1) in travels:
        L = math.hypot(x1 - x0, y1 - y0)
        if L <= 1e-9:
            continue
        tl = LineString([(x0, y0), (x1, y1)]); p0 = Point(x0, y0); p1 = Point(x1, y1)
        hit = False
        for i in tree.query(tl):
            f = feats[int(i)]
            inter = tl.intersection(f)
            if inter.is_empty:
                continue
            geoms = list(getattr(inter, "geoms", [inter]))
            for p in geoms:
                if p.geom_type != "Point":
                    p = p.representative_point()
                s = tl.project(p)
                near0 = s <= tol and f.distance(p0) <= tol
                near1 = (L - s) <= tol and f.distance(p1) <= tol
                if not (near0 or near1):
                    hit = True; break
            if hit:
                break
        if hit:
            n += 1; off.append((x0, y0, x1, y1))
            if len(ex) < 5: ex.append([round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)])
    return n, ex, off


def j4(layers):
    W = S = K = 0; exW = []; exS = []; exK = []; offW = []; offS = []; offK = []
    for li, lay in enumerate(layers):
        trav = [(e[1], e[2], e[3], e[4]) for e in lay if e[0] == "T"]
        peri = [LineString([(e[2], e[3]), (e[4], e[5])]) for e in lay if e[0] == "X" and e[1] in PERI]
        surf = [LineString([(e[2], e[3]), (e[4], e[5])]) for e in lay if e[0] == "X" and e[1] in SURF]
        n, ex, of = cuts(trav, peri); W += n; exW += [[li] + e for e in ex]; offW += [(li,) + t for t in of]
        n, ex, of = cuts(trav, surf); S += n; exS += [[li] + e for e in ex]; offS += [(li,) + t for t in of]
        types = set(e[1] for e in lay if e[0] == "X")
        if peri and not (types - set(PERI)):
            rings = []
            for ch, _ in chains(lay):
                pts = [(ch[0][2], ch[0][3])] + [(e[4], e[5]) for e in ch]
                if len(pts) >= 4 and math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) <= 2.0:
                    pg = Polygon(pts).buffer(0)
                    if not pg.is_empty:
                        rings.append(pg)
            if rings:
                ptree = STRtree(peri)
                for (x0, y0, x1, y1) in trav:
                    L = math.hypot(x1 - x0, y1 - y0); m = max(1, int(L / STEP))
                    hit = False
                    for k in range(m + 1):
                        P = Point(x0 + (x1 - x0) * k / m, y0 + (y1 - y0) * k / m)
                        if any(r.contains(P) for r in rings):
                            j = int(ptree.query_nearest(P)[0])
                            if peri[j].distance(P) > SKIN_CLEAR:
                                hit = True; break
                    if hit:
                        K += 1; offK.append((li, x0, y0, x1, y1))
                        if len(exK) < 3: exK.append([li, round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)])
    return W, S, K, exW[:5], exS[:5], exK, {"W": offW, "S": offS, "K": offK}


def measure(fn):
    L = parse(fn)
    s, o, sa = j1(L)
    m, lg, loops, ma, st = j2(L)
    b, col, n = j3(st, sa, ma)
    W, S, K, eW, eS, eK, OFF = j4(L)
    return {"J1_short_primes": s, "J1_over_primes": o, "J2_closed_loops_gt3mm": loops, "J2_missing_cover": m, "J2_long_overlap": lg,
            "J3_stacked_deficit": b, "J3_column_starts": col, "J3_layers_with_closed_start": n,
            "J4_wall_cuts": W, "J4_surface_cuts": S, "J4_skin_travels": K,
            "examples": {"J1_short": sa[:5], "J2_missing": sorted(ma)[:5], "J4_wall": eW, "J4_surface": eS, "J4_skin": eK}, "_off": OFF}


def new_offenders(lo, co):
    n = 0; ex = []
    for t in co:
        if not any(u[0] == t[0] and math.hypot(u[1] - t[1], u[2] - t[2]) <= XY_TOL and math.hypot(u[3] - t[3], u[4] - t[4]) <= XY_TOL for u in lo):
            n += 1
            if len(ex) < 5: ex.append([t[0]] + [round(v, 3) for v in t[1:]])
    return n, ex


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("live"); ap.add_argument("cand"); ap.add_argument("--json")
    ap.add_argument("--zero-travel-cuts", action="store_true")
    a = ap.parse_args()
    l = measure(a.live); c = measure(a.cand)
    lo = l.pop("_off"); co = c.pop("_off")
    fails = []; newo = {}
    for k in ("J1_short_primes", "J1_over_primes", "J2_missing_cover", "J2_long_overlap", "J3_stacked_deficit", "J4_wall_cuts", "J4_surface_cuts", "J4_skin_travels"):
        if a.zero_travel_cuts and k.startswith("J4"):
            if c[k] > 0:
                fails.append("%s %d (ApexCore: 0 required; live %d)" % (k, c[k], l[k]))
            continue
        if c[k] > l[k]:
            fails.append("%s %d > live %d%s" % (k, c[k], l[k], " (0 required)" if l[k] == 0 else ""))
    for key, name in (("W", "J4_wall_cuts"), ("S", "J4_surface_cuts"), ("K", "J4_skin_travels")):
        n, ex = new_offenders(lo[key], co[key]); newo[name] = {"new": n, "examples": ex}
        if n and not a.zero_travel_cuts:
            fails.append("%s: %d NEW offending travel(s) not in live (no new offender allowed)" % (name, n))
    rep = {"seam_joint_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "params": {"E_TOL": E_TOL, "SHORT_LOOP": SHORT_LOOP, "OVERLAP": OVERLAP, "TAIL_TOL": TAIL_TOL, "COLUMN_MM": COLUMN_MM, "END_MM": END_MM, "SKIN_CLEAR": SKIN_CLEAR},
           "mode": "zero-travel-cuts (ApexCore)" if a.zero_travel_cuts else "no-worse-than-live, no new offender",
           "live": l, "cand": c, "new_offenders": newo, "result": "PASS" if not fails else "FAIL", "failures": fails}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
