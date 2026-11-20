#!/usr/bin/env python3
"""outer_lock.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Kendon 2026-11-20 9:50 AM MT: "It needs to fix the hollow without changing the outside
surface at all ... the outside surface cannot change and the avoid crossing cannot change
etc we are only fixing the inside".

usage: outer_lock.py LIVE.gcode CANDIDATE.gcode [--ctc scripts/g2b_heelmod/check_travel_wall_crossings.py] [--d3] [--rotate] [--all] [--json out.json]
  --d3  (runner passes it for ApexCore only, decision D3 allowed): "Inner wall" leaves the B lock and Inner wall + infill
        leave the C first/last-layer comparison; those are checked by inner_shift.py instead. Outer/overhang walls, seams,
        run order, config, crossings, retracts, wipes and z-moves stay exactly as below.
  --all (ApexLight when it must stay identical to live): B locks EVERY extrusion type, gap fill and infill included.
  --rotate (runner passes it for ApexCore only; Kendon 2026-11-20 11:38 AM / 12:17 PM MT: seams back on the bottom):
        the ONLY outer/overhang change allowed is a cyclic rotation of a CLOSED perimeter loop's start point. Per layer the
        Outer+Overhang beads are chained (contiguous segments); a chain is closed when its path returns to its start
        (<= ROT_CLOSE 0.011 mm) after >= 3 segments; the remainder after the last such return is the seam overlap tail. The Outer/Overhang ;TYPE: label is not compared inside a closed loop (it travels with the comment
        lines when the loop is rotated; both are the one perimeter). A candidate closed chain passes only if it is a live
        closed chain with only the start moved: setting aside at most the candidate's START vertex (only if it lies on a
        live segment: a split point, not a new corner) and at most live's START vertex (only if it lies on a candidate
        segment), the vertex lists are equal under one cyclic shift (each within XY_TOL 0.01 mm); every
        candidate segment lies on the live path with that live segment's E/mm (E_REL; a split piece: 2 E + 2 XY quanta)
        and F (F_TOL), and vice versa; total length equal (+/- 0.02 mm); and its seam overlap tail exists exactly where live
        has one, is ONE segment of min(OVERLAP_MM 0.4, the candidate's first leg) +/- 0.011 mm along that first leg from
        the start, at that leg's E/mm and at live's overlap F (postprocess_g2b _overlap_line: the overlap length follows
        the first leg, so a short dorsal leg gave live shorter overlaps). Live's own chains compared with themselves pass trivially (the no-op). Open chains, every other type, run order,
        config, crossings, retracts near the outer wall, wipes and Z moves stay exactly as below (travels are free only
        through E/F: the seam travel to the new start must still add no crossing and no extra retract/wipe/Z move).
Both files must come from the same model, profile, material and request parameters.
Exit 0 = PASS, 1 = FAIL, 2 = usage/parse error.

Checks (all strict; any failure fails the gate):
 A  layer count identical.
 B  LOCKED features (Outer wall, Overhang wall, Inner wall, Skirt, Brim, Top surface,
    Bottom surface, Ironing, Support, Support interface): per layer, every extrusion run
    in the candidate equals one live run of the same type and vice versa:
    same segment count; every vertex within 0.01 mm; E per mm within 1% (or |dE|<=1.5e-5,
    one G-code E quantum); feedrate identical (|dF|<=0.01 mm/s). Width-step count per run
    must equal live. Run ORDER must equal live too (seams = run start points identical within
    0.01 mm). A split, merged, moved, re-widened, re-speeded, re-ordered or re-seamed
    outer/inner wall fails.
 C  first and last extruding layer: ALL extrusion identical (same rule as B, all types).
 D  config block: identical line for line to live (no setting may differ, incl. infill),
    and reduce_crossing_wall = 1.
 E  crossings (check_travel_wall_crossings.py --json on both): candidate perimeter,
    outer-only, surface, gap, chain-hop, gap-exit and seam-block crossings never above live (0 wherever live is 0);
    skin_interior_travels <= live. seam_approaches is reported (informational).
 F  retracts within 0.6 mm of an outer/overhang wall <= live; ;WIPE_START count <= live;
    z-hop (Z moves) count == live.
"""
import argparse, hashlib, json, math, re, subprocess, sys
from collections import defaultdict

A_FIL = math.pi * (1.75 / 2) ** 2
XY_TOL = 0.01
E_REL = 0.01
E_ABS = 1.5e-5
F_TOL = 0.01
ROT_CLOSE = 0.011
ROT_TAIL = 0.011
OVERLAP_MM = 0.4
RETRACT_NEAR = 0.6
LOCKED = {"Outer wall", "Overhang wall", "Inner wall", "Skirt", "Brim", "Top surface",
          "Bottom surface", "Ironing", "Support", "Support interface"}
SURFACE = ("Outer wall", "Overhang wall")
D3_FREE = {"Inner wall", "Sparse infill", "Internal solid infill", "Solid infill"}
CROSS_ZERO = ("perimeter_crossings", "outer_only_crossings", "surface_crossings", "gap_crossings",
              "chain_hop_crossings", "gap_exit_crossings", "seam_block_crossings")


def parse(fn):
    layers = [[]]
    x = y = z = 0.0
    E = 0.0
    rel = True
    F = 0.0
    t = None
    wipe = False
    cfg = []
    in_cfg = False
    with open(fn, errors="replace") as fh:
        for line in fh:
            if line.startswith("; CONFIG_BLOCK_START"):
                in_cfg = True
                continue
            if line.startswith("; CONFIG_BLOCK_END"):
                in_cfg = False
                continue
            if in_cfg:
                cfg.append(line.rstrip("\n"))
                continue
            if line.startswith(";LAYER_CHANGE"):
                layers.append([])
                continue
            if line.startswith(";TYPE:"):
                t = line[6:].strip()
                layers[-1].append(("B",))
                continue
            if line.startswith(";WIPE_START"):
                wipe = True
                layers[-1].append(("W",))
                continue
            if line.startswith(";WIPE_END"):
                wipe = False
                continue
            s = line.split(";")[0].split()
            if not s:
                continue
            c = s[0]
            if c == "M83":
                rel = True
                continue
            if c == "M82":
                rel = False
                continue
            if c == "G92":
                for m in s[1:]:
                    if m[0] == "E":
                        E = float(m[1:])
                continue
            if c not in ("G0", "G1"):
                continue
            w = {}
            for m in s[1:]:
                try:
                    w[m[0]] = float(m[1:])
                except ValueError:
                    pass
            if "F" in w:
                F = w["F"] / 60.0
            nx, ny, nz = w.get("X", x), w.get("Y", y), w.get("Z", z)
            de = 0.0
            if "E" in w:
                de = w["E"] if rel else w["E"] - E
                if not rel:
                    E = w["E"]
            if nz != z:
                layers[-1].append(("Z",))
            d = math.hypot(nx - x, ny - y)
            if d > 1e-9 and de > 1e-9 and not wipe:
                layers[-1].append(("X", t, x, y, nx, ny, de, de / d, F))
            elif d > 1e-9:
                layers[-1].append(("T",))
            elif de < -1e-9:
                layers[-1].append(("R", x, y))
            x, y, z = nx, ny, nz
    return layers, cfg


def runs(layer, types=None):
    out = []
    cur = None
    for ev in layer:
        if ev[0] == "X" and (types is None or ev[1] in types):
            if cur and cur["t"] == ev[1] and abs(cur["end"][0] - ev[2]) < 1e-9 and abs(cur["end"][1] - ev[3]) < 1e-9:
                cur["s"].append(ev)
                cur["end"] = (ev[4], ev[5])
            else:
                if cur:
                    out.append(cur)
                cur = {"t": ev[1], "s": [ev], "end": (ev[4], ev[5])}
        else:
            if cur:
                out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return out


def width_steps(r):
    s = r["s"]
    return sum(1 for a, b in zip(s, s[1:]) if abs(b[7] - a[7]) > max(E_REL * a[7], 1e-9) and abs(b[6] - a[6]) > E_ABS)


def run_equal(a, b):
    """return (ok, max_xy, max_erel, max_df)"""
    if a["t"] != b["t"] or len(a["s"]) != len(b["s"]):
        return False, float("inf"), float("inf"), float("inf")
    mxy = me = mf = 0.0
    ok = True
    for p, q in zip(a["s"], b["s"]):
        dxy = max(math.hypot(p[2] - q[2], p[3] - q[3]), math.hypot(p[4] - q[4], p[5] - q[5]))
        er = abs(p[7] - q[7]) / max(p[7], 1e-12)
        df = abs(p[8] - q[8])
        mxy, me, mf = max(mxy, dxy), max(me, er), max(mf, df)
        if dxy > XY_TOL or (er > E_REL and abs(p[6] - q[6]) > E_ABS) or df > F_TOL:
            ok = False
    if width_steps(a) != width_steps(b):
        ok = False
    return ok, mxy, me, mf


def compare_runs(la, lb):
    """match runs; returns (unmatched_live, unmatched_cand, order_changed, worst)"""
    used = [False] * len(la)
    unmatched_c = []
    order = []
    worst = [0.0, 0.0, 0.0]
    for j, rb in enumerate(lb):
        hit = None
        sx, sy = rb["s"][0][2], rb["s"][0][3]
        for i, ra in enumerate(la):
            if used[i] or ra["t"] != rb["t"]:
                continue
            if math.hypot(ra["s"][0][2] - sx, ra["s"][0][3] - sy) > XY_TOL:
                continue
            ok, mxy, me, mf = run_equal(ra, rb)
            if ok:
                hit = i
                break
        if hit is None:
            # record nearest-start deviation for the report
            best = None
            for i, ra in enumerate(la):
                if used[i] or ra["t"] != rb["t"]:
                    continue
                d = math.hypot(ra["s"][0][2] - sx, ra["s"][0][3] - sy)
                if best is None or d < best[0]:
                    best = (d, i)
            if best is not None:
                ok, mxy, me, mf = run_equal(la[best[1]], rb)
                worst = [max(worst[0], best[0] if mxy == float("inf") else mxy), max(worst[1], me if me != float("inf") else 0), max(worst[2], mf if mf != float("inf") else 0)]
            unmatched_c.append(j)
        else:
            used[hit] = True
            order.append(hit)
    unmatched_l = [i for i, u in enumerate(used) if not u]
    order_changed = order != sorted(order)
    return unmatched_l, unmatched_c, order_changed, worst


def surface_chains(layer):
    """contiguous Outer/Overhang bead chains of one layer (type markers do not break a chain; travel/retract/wipe/Z do)"""
    out = []; cur = None
    for ev in layer:
        if ev[0] == "B":
            continue
        if ev[0] == "X" and ev[1] in SURFACE:
            if cur and abs(cur[-1][4] - ev[2]) < 1e-9 and abs(cur[-1][5] - ev[3]) < 1e-9:
                cur.append(ev); continue
            if cur: out.append(cur)
            cur = [ev]; continue
        if cur: out.append(cur)
        cur = None
    if cur: out.append(cur)
    return out


def _epm_ok(a_epm, b_epm, b_de, b_len):
    """E/mm within E_REL, or (a short split piece) its E within 2 E quanta + 2 coordinate quanta (0.002 mm x E/mm) of
    live E/mm x its length: a split point is written at 0.001 mm and its E at 1e-5"""
    return abs(b_epm - a_epm) <= E_REL * max(a_epm, 1e-12) or abs(b_de - a_epm * b_len) <= 2 * E_ABS + 0.002 * a_epm


def canon_loop(chain):
    """closed chain -> (segments [x0, y0, x1, y1, de, F] up to the return to the start, overlap tail segments, start xy);
    None for an open chain (no return to the start within ROT_CLOSE after >= 3 segments)"""
    if len(chain) < 3:
        return None
    sx, sy = chain[0][2], chain[0][3]
    k = None
    for j in range(2, len(chain)):        # the LAST return to the start (an earlier near-return is a micro segment)
        if math.hypot(chain[j][4] - sx, chain[j][5] - sy) <= ROT_CLOSE:
            k = j
    if k is None:
        return None
    segs = [[e[2], e[3], e[4], e[5], e[6], e[8]] for e in chain[:k + 1]]
    tail = [[e[2], e[3], e[4], e[5], e[6], e[8]] for e in chain[k + 1:]]
    return segs, tail, (sx, sy)


def _dist_path(pt, segs):
    best = (float("inf"), -1)
    for i, g in enumerate(segs):
        x0, y0, x1, y1 = g[0], g[1], g[2], g[3]
        dx, dy = x1 - x0, y1 - y0; L2 = dx * dx + dy * dy
        u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((pt[0] - x0) * dx + (pt[1] - y0) * dy) / L2))
        d = math.hypot(pt[0] - (x0 + u * dx), pt[1] - (y0 + u * dy))
        if d < best[0]:
            best = (d, i)
    return best


def _beads_match(sa, sb):
    """every segment of sb: its midpoint lies on sa (<= XY_TOL) and E/mm, F equal the sa segment it lies on"""
    for g in sb:
        L = math.hypot(g[2] - g[0], g[3] - g[1])
        d, i = _dist_path(((g[0] + g[2]) / 2, (g[1] + g[3]) / 2), sa)
        if d > XY_TOL:
            return False
        h = sa[i]; Lh = math.hypot(h[2] - h[0], h[3] - h[1])
        if abs(g[5] - h[5]) > F_TOL or Lh <= 1e-9 or L <= 1e-9 or not _epm_ok(h[4] / Lh, g[4] / L, g[4], L):
            return False
    return True


def loops_equal_rotated(cl, cc):
    """True when candidate loop cc is live loop cl with only its start point moved (cyclic rotation):
    vertices: after setting aside at most the candidate's START vertex and at most live's START vertex, each only if it
    lies on the other loop's path (a split point on a straight segment, not a new corner), the remaining vertex lists
    are equal under one cyclic shift (each within XY_TOL); beads: every candidate segment lies on
    the live path with the live segment's E/mm and F, and vice versa; total length equal (+/- 2 XY_TOL); overlap tail:
    same length (ROT_TAIL), E/mm and F as live's, starting at the candidate start and lying on the candidate loop."""
    sl, tl, _ = cl; sc, tc, (sx, sy) = cc
    vl = [(g[0], g[1]) for g in sl]; vc = [(g[0], g[1]) for g in sc]
    ok = False
    for drop_l in (False, True):          # only the two START points may be set aside, each only if it lies on the other path
        for drop_c in (False, True):
            if drop_l and _dist_path(vl[0], sc)[0] > XY_TOL:
                continue
            if drop_c and _dist_path(vc[0], sl)[0] > XY_TOL:
                continue
            rl = vl[1:] if drop_l else vl; rc = vc[1:] if drop_c else vc
            n = len(rl)
            if n != len(rc) or n < 3:
                continue
            for k in range(n):
                if all(math.hypot(rl[i][0] - rc[(i + k) % n][0], rl[i][1] - rc[(i + k) % n][1]) <= XY_TOL for i in range(n)):
                    ok = True; break
            if ok:
                break
        if ok:
            break
    if not ok:
        return False
    Ll = sum(math.hypot(g[2] - g[0], g[3] - g[1]) for g in sl); Lc = sum(math.hypot(g[2] - g[0], g[3] - g[1]) for g in sc)
    if abs(Ll - Lc) > 2 * XY_TOL:
        return False
    if not (_beads_match(sl, sc) and _beads_match(sc, sl)):
        return False
    if bool(tl) != bool(tc):
        return False
    if tl:
        # the seam overlap: one segment re-extruding min(OVERLAP_MM 0.4, first leg) along the candidate's first leg, at
        # that leg's E/mm and at live's overlap feed (postprocess_g2b _overlap_line); its length follows the new first leg
        if len(tc) != len(tl):
            return False
        g = tc[0]; f0 = sc[0]
        Tc = math.hypot(g[2] - g[0], g[3] - g[1]); F0 = math.hypot(f0[2] - f0[0], f0[3] - f0[1])
        if abs(Tc - min(OVERLAP_MM, F0)) > ROT_TAIL or Tc <= 1e-9 or F0 <= 1e-9:
            return False
        if not _epm_ok(f0[4] / F0, g[4] / Tc, g[4], Tc) or abs(g[5] - tl[0][5]) > F_TOL:
            return False
        if math.hypot(g[0] - sx, g[1] - sy) > ROT_CLOSE or _dist_path((g[2], g[3]), [f0])[0] > XY_TOL:
            return False
    return True


def chain_equal_strict(a, b):
    if len(a) != len(b):
        return False
    for p, q in zip(a, b):
        dxy = max(math.hypot(p[2] - q[2], p[3] - q[3]), math.hypot(p[4] - q[4], p[5] - q[5]))
        if p[1] != q[1] or dxy > XY_TOL or (abs(p[7] - q[7]) / max(p[7], 1e-12) > E_REL and abs(p[6] - q[6]) > E_ABS) or abs(p[8] - q[8]) > F_TOL:
            return False
    return True


def compare_surface_rotated(la_layer, lb_layer):
    """returns (unmatched_live, unmatched_cand, order_changed, rotated_count)"""
    A = surface_chains(la_layer); Bc = surface_chains(lb_layer)
    CA = [canon_loop(c) for c in A]; CB = [canon_loop(c) for c in Bc]
    used = [False] * len(A); order = []; um_c = []; rot = 0
    for j, cb in enumerate(Bc):
        hit = None
        for i, ca in enumerate(A):
            if used[i]:
                continue
            if CA[i] is not None and CB[j] is not None:
                if loops_equal_rotated(CA[i], CB[j]):
                    hit = i
                    if math.hypot(ca[0][2] - cb[0][2], ca[0][3] - cb[0][3]) > XY_TOL:
                        rot += 1
                    break
            elif CA[i] is None and CB[j] is None:
                if chain_equal_strict(ca, cb):
                    hit = i; break
        if hit is None:
            um_c.append(j)
        else:
            used[hit] = True; order.append(hit)
    return [i for i, u in enumerate(used) if not u], um_c, order != sorted(order), rot


def near_outer_retracts(layer):
    segs = [(e[2], e[3], e[4], e[5]) for e in layer if e[0] == "X" and e[1] in SURFACE]
    if not segs:
        return 0
    n = 0
    for e in layer:
        if e[0] != "R":
            continue
        px, py = e[1], e[2]
        for x0, y0, x1, y1 in segs:
            dx, dy = x1 - x0, y1 - y0
            L2 = dx * dx + dy * dy
            u = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / L2))
            if math.hypot(px - (x0 + u * dx), py - (y0 + u * dy)) <= RETRACT_NEAR:
                n += 1
                break
    return n


def ctc(script, fn):
    p = subprocess.run([sys.executable, script, fn, "--json"], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live")
    ap.add_argument("cand")
    ap.add_argument("--ctc", default="scripts/g2b_heelmod/check_travel_wall_crossings.py")
    ap.add_argument("--json")
    ap.add_argument("--d3", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    a = ap.parse_args()
    if a.all and (a.d3 or a.rotate):
        print("--all excludes --d3 and --rotate"); sys.exit(2)
    locked = set(LOCKED)
    if a.d3:
        locked.discard("Inner wall")
    L, cl = parse(a.live)
    C, cc = parse(a.cand)
    fails = []
    rep = {"live": a.live, "candidate": a.cand, "outer_lock_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "mode": ("all" if a.all else "+".join([m for m, f in (("d3", a.d3), ("rotate", a.rotate)) if f]) or "strict")}

    # A
    rep["layers"] = [len(L), len(C)]
    if len(L) != len(C):
        fails.append("A layer count %d != live %d" % (len(C), len(L)))

    # B
    per_type = defaultdict(lambda: {"live_runs": 0, "cand_runs": 0, "unmatched_live": 0, "unmatched_cand": 0, "layers_bad": 0})
    worst = [0.0, 0.0, 0.0]
    bad_layers = []
    order_changes = 0
    wsteps = [0, 0]
    rotated = 0
    for li in range(min(len(L), len(C))):
        ra = runs(L[li], None if a.all else locked)
        rb = runs(C[li], None if a.all else locked)
        if a.rotate:
            ul, uc, oc, nrot = compare_surface_rotated(L[li], C[li])
            rotated += nrot
            d = per_type["Outer+Overhang (rotation rule)"]
            d["live_runs"] += len(surface_chains(L[li])); d["cand_runs"] += len(surface_chains(C[li]))
            d["unmatched_live"] += len(ul); d["unmatched_cand"] += len(uc)
            if ul or uc:
                d["layers_bad"] += 1; bad_layers.append(li)
            if oc:
                order_changes += 1
            ra = [r for r in ra if r["t"] not in SURFACE]; rb = [r for r in rb if r["t"] not in SURFACE]
        else:
            wsteps[0] += sum(width_steps(r) for r in ra if r["t"] in SURFACE)
            wsteps[1] += sum(width_steps(r) for r in rb if r["t"] in SURFACE)
        for t in set(r["t"] for r in ra + rb):
            ta = [r for r in ra if r["t"] == t]
            tb = [r for r in rb if r["t"] == t]
            ul, uc, oc, w = compare_runs(ta, tb)
            d = per_type[t]
            d["live_runs"] += len(ta)
            d["cand_runs"] += len(tb)
            d["unmatched_live"] += len(ul)
            d["unmatched_cand"] += len(uc)
            worst = [max(worst[k], w[k]) for k in range(3)]
            if ul or uc:
                d["layers_bad"] += 1
                bad_layers.append(li)
            if oc:
                order_changes += 1
    rep["locked"] = dict(per_type)
    rep["outer_width_steps"] = {"live": wsteps[0], "cand": wsteps[1]}
    rep["worst_unmatched"] = {"xy_mm": worst[0], "e_rel": worst[1], "f_mm_s": worst[2]}
    rep["run_order_changes_layers"] = order_changes
    rep["rotated_closed_loops"] = rotated
    rep["bad_layers_first20"] = sorted(set(bad_layers))[:20]
    for t, d in per_type.items():
        if d["unmatched_live"] or d["unmatched_cand"]:
            fails.append("B %s changed: %d live runs / %d candidate runs unmatched on %d layers" % (t, d["unmatched_live"], d["unmatched_cand"], d["layers_bad"]))
    if order_changes:
        fails.append("B run order of locked features changed on %d layers (order must equal live)" % order_changes)
    if wsteps[1] != wsteps[0]:
        fails.append("B outer/overhang width steps %d != live %d" % (wsteps[1], wsteps[0]))

    # C
    def ext_layers(LL):
        return [i for i, l in enumerate(LL) if any(e[0] == "X" for e in l)]
    el, ec = ext_layers(L), ext_layers(C)
    for name, i, j in (("first", el[0] if el else None, ec[0] if ec else None), ("last", el[-1] if el else None, ec[-1] if ec else None)):
        if i is None or i != j:
            fails.append("C %s extruding layer index differs" % name)
            continue
        keep = (lambda r: r["t"] not in D3_FREE) if a.d3 else (lambda r: True)
        if a.rotate:
            u1, u2, _, _ = compare_surface_rotated(L[i], C[j])
            ul, uc, _, _ = compare_runs([r for r in runs(L[i]) if keep(r) and r["t"] not in SURFACE], [r for r in runs(C[j]) if keep(r) and r["t"] not in SURFACE])
            ul, uc = ul + u1, uc + u2
        else:
            ul, uc, _, _ = compare_runs([r for r in runs(L[i]) if keep(r)], [r for r in runs(C[j]) if keep(r)])
        if ul or uc:
            fails.append("C %s extruding layer %d changed (%d/%d runs unmatched)" % (name, i, len(ul), len(uc)))

    # D
    rcw = [ln for ln in cc if re.match(r";\s*reduce_crossing_wall\s*=", ln)]
    rep["reduce_crossing_wall"] = rcw
    if not rcw or rcw[0].split("=")[1].strip() != "1":
        fails.append("D reduce_crossing_wall != 1")
    diff = [(x, y) for x, y in zip(cl, cc) if x != y]
    if len(cl) != len(cc) or diff:
        rep["config_diff"] = diff[:20]
        fails.append("D config block differs from live (%d lines differ, %d vs %d lines)" % (len(diff), len(cc), len(cl)))

    # E
    jl, jc = ctc(a.ctc, a.live), ctc(a.ctc, a.cand)
    if jl is None or jc is None:
        fails.append("E crossing checker failed to run (%s)" % a.ctc)
    else:
        tl, tc = jl["totals"], jc["totals"]
        rep["crossings"] = {"live": tl, "cand": tc}
        for k in CROSS_ZERO:
            if tc.get(k, 0) > tl.get(k, 0):
                fails.append("E %s = %s (live %s; candidate may never exceed live, and must be 0 where live is 0)" % (k, tc.get(k), tl.get(k, 0)))
        if tc.get("skin_interior_travels", 0) > tl.get("skin_interior_travels", 0):
            fails.append("E skin_interior_travels %s > live %s" % (tc.get("skin_interior_travels"), tl.get("skin_interior_travels")))

    # F
    rl = sum(near_outer_retracts(l) for l in L)
    rc = sum(near_outer_retracts(l) for l in C)
    wl = sum(1 for l in L for e in l if e[0] == "W")
    wc = sum(1 for l in C for e in l if e[0] == "W")
    zl = sum(1 for l in L for e in l if e[0] == "Z")
    zc = sum(1 for l in C for e in l if e[0] == "Z")
    rep["retracts_near_outer"] = [rl, rc]
    rep["wipes"] = [wl, wc]
    rep["z_moves"] = [zl, zc]
    if rc > rl:
        fails.append("F retracts near outer wall %d > live %d" % (rc, rl))
    if wc > wl:
        fails.append("F wipes %d > live %d" % (wc, wl))
    if zc != zl:
        fails.append("F Z moves %d != live %d" % (zc, zl))

    rep["result"] = "PASS" if not fails else "FAIL"
    rep["failures"] = fails
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps({k: rep[k] for k in ("result", "mode", "failures", "layers", "outer_width_steps", "worst_unmatched", "run_order_changes_layers", "rotated_closed_loops", "retracts_near_outer", "wipes", "z_moves")}, indent=1))
    if "crossings" in rep:
        print("crossings live:", rep["crossings"]["live"])
        print("crossings cand:", rep["crossings"]["cand"])
    for t, d in sorted(rep["locked"].items()):
        print("locked %-14s %s" % (t, d))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
