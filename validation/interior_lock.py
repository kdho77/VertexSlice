#!/usr/bin/env python3
"""interior_lock.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Kendon 2026-11-20 9:50 AM MT: "It needs to fix the hollow without changing the outside surface at all ...
the outside surface cannot change and the avoid crossing cannot change etc we are only fixing the inside"

usage: interior_lock.py LIVE.gcode CANDIDATE.gcode [--d3] [--rotate] [--json out.json]     (needs shapely; imports outer_lock.py from the same directory)
  --d3 (runner passes it for ApexCore only, decision D3 allowed): Inner wall and infill runs leave I1 (inner_shift.py checks
       them), and the interior I in I2 is computed from the live outer/overhang walls and the CANDIDATE inner walls.
  --rotate (runner passes it for ApexCore only: seams back on the bottom): in I1 the Outer/Overhang walls are compared with
       outer_lock.py's rotation rule (a closed perimeter loop may only have its start point moved: same vertices, path,
       E/mm, F; overlap tail per postprocess_g2b), every other non-gap-fill run stays identical and in order.
Exit 0 PASS, 1 FAIL.  All geometry is in the physical G-code frame (X, Y as written; width from E at H=0.46,
w = E_per_mm*A/H + H*(1-pi/4), the same model as belt_air_columns.py).

Checks
 I1 every NON-gap-fill extrusion run (Sparse infill, Internal solid infill, Solid infill, Bridge, Top/Bottom
    surface, walls, skirt, anything else) is identical to live AND in the same order: vertices <=0.01 mm, E/mm
    within 1% (or 1 E quantum), F identical, same segment count. Only ';TYPE:Gap infill' runs may differ.
 I2 candidate gap-fill runs that are not byte-for-byte live runs ("new/changed"), and live gap-fill runs that
    disappeared ("removed/changed"), may exist only inside the measured thin zone T:
      O  = live Outer+Overhang wall footprints (flat-capped buffers of half the measured width), closed by 0.1 mm,
           holes filled = the live outer-wall outline (its boundary is the outer face);
      W  = union of live Outer+Overhang+Inner wall footprints;  I = O - W (interior between wall inner faces);
      T  = union of COL_STEP (0.25 mm) wide X-column strips of O whose Y span (lowest to highest outer face) is
           <= SLOT_MAX_THICK (4.0 mm): the belt_air_columns thin-column definition.
    A new/changed run must satisfy: centreline inside T (+0.02 mm); bead footprint inside O (+0.005 mm: never past an
    outer face) and inside I grown by INNER_POKE_TOL (0.13 mm: it may overlap a wall's inner face by at most that).
    A live gap run that disappeared must have its centreline inside T.
 I3 placement: in every layer no Outer/Overhang/Inner wall run comes after a gap-fill run (live never does).
 I4 volumetric flow (E/mm * filament area * F) of every new/changed gap-bead segment:
      drawn at F <= gap_infill_speed: <= SLOW_CAP = min(live's max over its slow gap segments, filament_max_volumetric_speed);
        if live has no slow gap segment (live ApexLight has no gap fill at all) SLOW_CAP = filament_max_volumetric_speed (35).
      drawn faster than gap_infill_speed: <= FAST_CAP = live's max over its fast gap segments (ApexCore 44.32 at F12720),
        or filament_max_volumetric_speed if live has none;
    and the number of gap-fill segments drawn faster than gap_infill_speed (live has 29) must not exceed live's.
 I5 width steps: within a new/changed gap bead, a step larger than WIDTH_STEP_MM (0.10 mm) must be followed
    by at least MIN_CONST_RUN_MM (2.0 mm) of constant width (width within 0.10 mm of the run's start of
    that group), except for the first and last group of the bead. Enforced.
 I6 first and last extruding layer: gap-fill identical to live too.
 I7 if ALLOW_NEW_GAP_BEADS is False: per layer, candidate new/changed gap runs <= live removed/changed runs.
 I8 bead width cap: every segment of a new/changed gap bead has E-derived width <= BEAD_MAX_W + BEAD_W_TOL (1.5 + 0.05 mm).
 I9 no double deposit: a new/changed gap bead's footprint may overlap (a) the footprints of the OTHER gap-fill runs of the
    candidate layer and (b) itself (sum of its segment footprints minus their union) by at most OVERLAP_MAX_FRAC (10%)
    of its own footprint area each. A copy of a bead laid back over itself or over another bead fails.
 Straddling live beads: a live gap run whose centreline is partly outside T (+0.02 mm) must stay byte-identical (any
    change to it fails I2 as "changed live gap run outside thin zone"). To add width inside T next to it, add a separate
    new bead inside T; I9 limits its overlap with the straddling bead. The count of straddling live runs is reported.
"""
import argparse, hashlib, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outer_lock as ol
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

# >>> PM PARAMETERS (changed only by the PM before launch; the brief is rebuilt with the new sha) >>>
ALLOW_NEW_GAP_BEADS = True      # KENDON DECISION D1 (2026-11-20 11:11 AM MT): interior gap-fill beads allowed
SLOT_MAX_THICK = 4.0
COL_STEP = 0.25
BEAD_MAX_W = 1.5               # = ApexCore I4 cap: live's max slow gap flow 16.84 mm3/s at 26 mm/s is a 1.51 mm bead
INNER_POKE_TOL = 0.13
OUTER_FACE_TOL = 0.005
MIN_CONST_RUN_MM = 2.0
WIDTH_STEP_MM = 0.10
BEAD_W_TOL = 0.05
OVERLAP_MAX_FRAC = 0.10
# <<< PM PARAMETERS <<<

H = 0.46
ADJ = H * (1 - math.pi / 4)
GAP = "Gap infill"
WALLS = ("Outer wall", "Overhang wall", "Inner wall")


def width(ev):
    return ev[7] * ol.A_FIL / H + ADJ


def seg_poly(ev, w=None):
    w = width(ev) if w is None else w
    p = LineString([(ev[2], ev[3]), (ev[4], ev[5])])
    return p.buffer(w / 2.0, cap_style="flat")


def match_runs(la, lb):
    """greedy identical-run match; returns (unmatched_a, unmatched_b)"""
    used = [False] * len(la)
    ub = []
    for rb in lb:
        sx, sy = rb["s"][0][2], rb["s"][0][3]
        hit = None
        for i, ra in enumerate(la):
            if used[i] or math.hypot(ra["s"][0][2] - sx, ra["s"][0][3] - sy) > ol.XY_TOL:
                continue
            if ol.run_equal(ra, rb)[0]:
                hit = i
                break
        if hit is None:
            ub.append(rb)
        else:
            used[hit] = True
    return [la[i] for i, u in enumerate(used) if not u], ub


def geometry(live_layer, cand_layer=None):
    walls = [ev for ev in live_layer if ev[0] == "X" and ev[1] in WALLS]
    if cand_layer is not None:   # --d3: live outer/overhang walls + candidate inner walls
        walls = [ev for ev in walls if ev[1] != "Inner wall"] + [ev for ev in cand_layer if ev[0] == "X" and ev[1] == "Inner wall"]
    outer = [ev for ev in walls if ev[1] in ("Outer wall", "Overhang wall")]
    if not outer:
        return None
    Wall = unary_union([seg_poly(e) for e in walls])
    Out = unary_union([seg_poly(e) for e in outer]).buffer(0.1).buffer(-0.1)
    polys = list(Out.geoms) if hasattr(Out, "geoms") else [Out]
    O = unary_union([Polygon(p.exterior) for p in polys if not p.is_empty])
    I = O.difference(Wall)
    # T: X columns (0.25 mm strips, the belt_air_columns / belt_skin_slot definition) whose outline span in Y
    # (lowest to highest outer face) is <= SLOT_MAX_THICK. Y is the cross-section direction on the belt.
    strips = []
    x0, _, x1, _ = O.bounds
    x = math.floor(x0 / COL_STEP) * COL_STEP + COL_STEP / 2.0
    ya, yb = O.bounds[1] - 1.0, O.bounds[3] + 1.0
    while x <= x1 + COL_STEP:
        cut = O.intersection(LineString([(x, ya), (x, yb)]))
        if not cut.is_empty:
            b = cut.bounds
            if b[3] - b[1] <= SLOT_MAX_THICK + 1e-9:
                strips.append(box(x - COL_STEP / 2.0 - 1e-4, b[1], x + COL_STEP / 2.0 + 1e-4, b[3]))
        x += COL_STEP
    T = unary_union(strips) if strips else Polygon()
    return O, I, T


def run_line(r):
    pts = [(r["s"][0][2], r["s"][0][3])] + [(s[4], s[5]) for s in r["s"]]
    return LineString(pts) if len(pts) > 1 else None


def run_fp(r):
    return unary_union([seg_poly(s) for s in r["s"]])


def const_groups(r):
    """groups of consecutive segments whose width stays within WIDTH_STEP_MM of the group's first width"""
    groups = []
    cur = None
    for s in r["s"]:
        w = width(s)
        d = math.hypot(s[4] - s[2], s[5] - s[3])
        if cur is None or abs(w - cur["w0"]) > WIDTH_STEP_MM:
            cur = {"w0": w, "len": 0.0}
            groups.append(cur)
        cur["len"] += d
    return groups


def cfg_float(cfg, key):
    for ln in cfg:
        if ln.startswith("; %s =" % key):
            try:
                return float(ln.split("=")[1].strip())
            except ValueError:
                return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live")
    ap.add_argument("cand")
    ap.add_argument("--json")
    ap.add_argument("--d3", action="store_true")
    ap.add_argument("--rotate", action="store_true")
    a = ap.parse_args()
    L, cl = ol.parse(a.live)
    C, cc = ol.parse(a.cand)
    fails = []
    rep = {"interior_lock_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
           "mode": "+".join([m for m, f in (("d3", a.d3), ("rotate", a.rotate)) if f]) or "strict", "params": {"ALLOW_NEW_GAP_BEADS": ALLOW_NEW_GAP_BEADS, "SLOT_MAX_THICK": SLOT_MAX_THICK, "BEAD_MAX_W": BEAD_MAX_W,
                      "INNER_POKE_TOL": INNER_POKE_TOL, "OUTER_FACE_TOL": OUTER_FACE_TOL, "MIN_CONST_RUN_MM": MIN_CONST_RUN_MM,
                      "WIDTH_STEP_MM": WIDTH_STEP_MM, "BEAD_W_TOL": BEAD_W_TOL, "OVERLAP_MAX_FRAC": OVERLAP_MAX_FRAC}}
    if len(L) != len(C):
        fails.append("layer count differs")
        n = min(len(L), len(C))
    else:
        n = len(L)
    gap_speed = cfg_float(cc, "gap_infill_speed") or 26.0
    live_slow_max = live_fast_max = 0.0
    for lay in L:
        for ev in lay:
            if ev[0] == "X" and ev[1] == GAP:
                f = ev[7] * ol.A_FIL * ev[8]
                if ev[8] <= gap_speed + 0.01:
                    live_slow_max = max(live_slow_max, f)
                else:
                    live_fast_max = max(live_fast_max, f)
    live_gap_max = max(live_slow_max, live_fast_max)
    cfg_cap = cfg_float(cc, "filament_max_volumetric_speed") or 35.0
    cap = min(live_slow_max, cfg_cap) if live_slow_max > 0 else cfg_cap
    fast_cap = live_fast_max if live_fast_max > 0 else cfg_cap
    def fast_count(LL):
        return sum(1 for lay in LL for ev in lay if ev[0] == "X" and ev[1] == GAP and ev[8] > gap_speed + 0.01)
    fast_l, fast_c = fast_count(L), fast_count(C)
    rep["gap_segments_faster_than_gap_speed"] = [fast_l, fast_c]
    ext = [i for i, l in enumerate(L) if any(e[0] == "X" for e in l)]
    first_last = {ext[0], ext[-1]} if ext else set()

    nongap_bad = 0
    nongap_types = {}
    new_runs = new_len = new_vol = 0.0
    changed_live = 0
    out_of_T = out_of_O = out_of_I = const_bad = flow_bad = placement_bad = growth_bad = wide_bad = dbl_bad = straddle = 0
    worst = {"flow": 0.0, "fast_flow": 0.0, "width": 0.0, "overlap_other_frac": 0.0, "overlap_self_frac": 0.0, "outside_O_area": 0.0, "outside_I_area": 0.0, "min_group_mm": None}
    samples = []
    for li in range(n):
        rl, rc = ol.runs(L[li]), ol.runs(C[li])
        skip = {GAP} | (ol.D3_FREE if a.d3 else set()) | (set(ol.SURFACE) if a.rotate else set())
        nl = [r for r in rl if r["t"] not in skip]
        nc = [r for r in rc if r["t"] not in skip]
        ok = len(nl) == len(nc) and all(a["t"] == b["t"] and ol.run_equal(a, b)[0] for a, b in zip(nl, nc))
        if a.rotate:
            u1, u2, oc_, _ = ol.compare_surface_rotated(L[li], C[li])
            if u1 or u2 or oc_:
                ok = False
                nongap_types["Outer+Overhang (rotation rule)"] = nongap_types.get("Outer+Overhang (rotation rule)", 0) + 1
        if not ok:
            nongap_bad += 1
            for ra, rb in zip(nl, nc):
                if not (ra["t"] == rb["t"] and ol.run_equal(ra, rb)[0]):
                    nongap_types[rb["t"]] = nongap_types.get(rb["t"], 0) + 1
                    break
            if len(nl) != len(nc):
                nongap_types["(run count)"] = nongap_types.get("(run count)", 0) + 1
        # placement
        seen_gap = False
        for r in rc:
            if r["t"] == GAP:
                seen_gap = True
            elif r["t"] in WALLS and seen_gap:
                placement_bad += 1
                break
        gl = [r for r in rl if r["t"] == GAP]
        gc = [r for r in rc if r["t"] == GAP]
        rem, new = match_runs(gl, gc)
        if li in first_last and (rem or new):
            fails.append("I6 first/last extruding layer %d gap-fill changed" % li)
        if not ALLOW_NEW_GAP_BEADS and len(new) > len(rem):
            growth_bad += 1
        if not (rem or new):
            continue
        geo = geometry(L[li], C[li] if a.d3 else None)
        if geo is not None:
            for r in gl:
                ln_ = run_line(r)
                if ln_ is not None and ln_.difference(geo[2].buffer(0.02)).length > 1e-3 and ln_.intersection(geo[2]).length > 1e-3:
                    straddle += 1
        for r in new:
            new_runs += 1
            ln = sum(math.hypot(s[4] - s[2], s[5] - s[3]) for s in r["s"])
            new_len += ln
            new_vol += sum(s[7] * ol.A_FIL * math.hypot(s[4] - s[2], s[5] - s[3]) for s in r["s"])
            fl = [s[7] * ol.A_FIL * s[8] for s in r["s"] if s[8] <= gap_speed + 0.01]
            ff = [s[7] * ol.A_FIL * s[8] for s in r["s"] if s[8] > gap_speed + 0.01]
            fmax = max(fl) if fl else 0.0
            ffmax = max(ff) if ff else 0.0
            worst["flow"] = max(worst["flow"], fmax)
            worst["fast_flow"] = max(worst["fast_flow"], ffmax)
            if fmax > cap + 1e-6 or ffmax > fast_cap + 1e-6:
                flow_bad += 1
            wmax = max(width(s) for s in r["s"])
            worst["width"] = max(worst["width"], wmax)
            if wmax > BEAD_MAX_W + BEAD_W_TOL:
                wide_bad += 1
                if len(samples) < 10:
                    samples.append("layer %d: gap bead at (%.2f,%.2f) width %.2f mm > %.2f" % (li, r["s"][0][2], r["s"][0][3], wmax, BEAD_MAX_W + BEAD_W_TOL))
            segp = [seg_poly(s_) for s_ in r["s"]]
            fpu = unary_union(segp)
            if fpu.area > 1e-9:
                selfo = (sum(q.area for q in segp) - fpu.area) / fpu.area
                others = [o for o in gc if o is not r]
                oth = unary_union([run_fp(o) for o in others]) if others else Polygon()
                otho = fpu.intersection(oth).area / fpu.area if not oth.is_empty else 0.0
                worst["overlap_self_frac"] = max(worst["overlap_self_frac"], selfo)
                worst["overlap_other_frac"] = max(worst["overlap_other_frac"], otho)
                if selfo > OVERLAP_MAX_FRAC or otho > OVERLAP_MAX_FRAC:
                    dbl_bad += 1
                    if len(samples) < 10:
                        samples.append("layer %d: gap bead at (%.2f,%.2f) double deposit (self %.0f%%, other gap %.0f%%)" % (li, r["s"][0][2], r["s"][0][3], 100 * selfo, 100 * otho))
            g = const_groups(r)
            if len(g) > 2:
                inner = [x["len"] for x in g[1:-1]]
                m = min(inner)
                worst["min_group_mm"] = m if worst["min_group_mm"] is None else min(worst["min_group_mm"], m)
                if m < MIN_CONST_RUN_MM:
                    const_bad += 1
            if geo is None:
                out_of_T += 1
                continue
            O, I, T = geo
            line = run_line(r)
            if line is not None and not line.difference(T.buffer(0.02)).is_empty and line.difference(T.buffer(0.02)).length > 1e-3:
                out_of_T += 1
                if len(samples) < 10:
                    samples.append("layer %d: gap run at (%.2f,%.2f) outside thin zone" % (li, r["s"][0][2], r["s"][0][3]))
            fp = run_fp(r)
            ao = fp.difference(O.buffer(OUTER_FACE_TOL)).area
            ai = fp.difference(I.buffer(INNER_POKE_TOL)).area
            worst["outside_O_area"] = max(worst["outside_O_area"], ao)
            worst["outside_I_area"] = max(worst["outside_I_area"], ai)
            if ao > 1e-3:
                out_of_O += 1
                if len(samples) < 10:
                    samples.append("layer %d: gap run at (%.2f,%.2f) pokes past an outer face (area %.3f mm2)" % (li, r["s"][0][2], r["s"][0][3], ao))
            if ai > 1e-3:
                out_of_I += 1
                if len(samples) < 10:
                    samples.append("layer %d: gap run at (%.2f,%.2f) not between wall inner faces (area %.3f mm2 beyond %.2f mm poke)" % (li, r["s"][0][2], r["s"][0][3], ai, INNER_POKE_TOL))
        for r in rem:
            changed_live += 1
            if geo is None:
                out_of_T += 1
                continue
            line = run_line(r)
            if line is not None and line.difference(geo[2].buffer(0.02)).length > 1e-3:
                out_of_T += 1
                if len(samples) < 10:
                    samples.append("layer %d: changed live gap run at (%.2f,%.2f) was outside thin zone" % (li, r["s"][0][2], r["s"][0][3]))

    rep.update({"nongap_layers_changed": nongap_bad, "nongap_changed_types": nongap_types,
                "new_or_changed_gap_runs": int(new_runs), "new_gap_len_mm": round(new_len, 1), "new_gap_vol_mm3": round(new_vol, 1),
                "removed_or_changed_live_gap_runs": changed_live, "flow_cap_mm3s": cap, "fast_flow_cap_mm3s": fast_cap, "live_gap_max_mm3s": live_gap_max,
                "live_slow_gap_max_mm3s": live_slow_max, "live_fast_gap_max_mm3s": live_fast_max, "straddling_live_runs_on_changed_layers": straddle,
                "runs_too_wide": wide_bad, "runs_double_deposit": dbl_bad,
                "config_cap_mm3s": cfg_cap, "worst": worst,
                "runs_outside_thin_zone": out_of_T, "runs_past_outer_face": out_of_O, "runs_not_between_inner_faces": out_of_I,
                "runs_flow_over_cap": flow_bad, "runs_width_step_violation": const_bad, "layers_wall_after_gap": placement_bad,
                "layers_new_gap_growth_forbidden": growth_bad, "samples": samples})
    if nongap_bad:
        fails.append("I1 non-gap-fill extrusion changed on %d layers %s" % (nongap_bad, nongap_types))
    if out_of_T:
        fails.append("I2 %d gap runs outside the measured thin zone" % out_of_T)
    if out_of_O:
        fails.append("I2 %d gap runs poke past an outer face" % out_of_O)
    if out_of_I:
        fails.append("I2 %d gap runs not between two wall inner faces" % out_of_I)
    if placement_bad:
        fails.append("I3 wall run after gap-fill run on %d layers" % placement_bad)
    if fast_c > fast_l:
        fails.append("I4 gap-fill segments faster than gap_infill_speed %d > live %d" % (fast_c, fast_l))
    if flow_bad:
        fails.append("I4 %d gap beads above flow cap (slow %.2f mm3/s, max %.2f; fast %.2f, max %.2f)" % (flow_bad, cap, worst["flow"], fast_cap, worst["fast_flow"]))
    if wide_bad:
        fails.append("I8 %d gap beads wider than %.2f mm (max %.2f)" % (wide_bad, BEAD_MAX_W + BEAD_W_TOL, worst["width"]))
    if dbl_bad:
        fails.append("I9 %d gap beads double-deposit (overlap > %.0f%%)" % (dbl_bad, 100 * OVERLAP_MAX_FRAC))
    if const_bad:
        fails.append("I5 %d gap beads with a width step not held for %.1f mm" % (const_bad, MIN_CONST_RUN_MM))
    if growth_bad:
        fails.append("I7 new gap-fill beads not allowed (D1) on %d layers" % growth_bad)
    rep["result"] = "PASS" if not fails else "FAIL"
    rep["failures"] = fails
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
