"""File C post-process (local test only).

Changes vs B's apex_light_postprocess (all other B behaviour kept: belt-wall F1200, fan ramp,
0.7 mm glide at 15.6 mm/s, travel 212 mm/s):
 1. Seam X per layer = midpoint of that layer's belt-contact span (outer-wall segments within
    0.1 mm of the layer's lowest gantry Y), instead of a fixed X140. Closed outer AND inner loops
    start at the point of their lowest edge nearest that X.
 2. Arachne open inner paths that cross the seam X on the belt side are split there and printed
    as two halves that both START at the seam point (q -> start reversed, then q -> end), so they
    finish where Orca expected. Contiguous open+closed inner runs are decomposed so the closed
    part is rotated too (B left those unrotated).
 3. Every seam travel (into a seam start or out of a loop/glide end) is replaced by one direct
    move: optional 0.5 mm retract (travel >= 2 mm, the profile's retraction_minimum_travel),
    gantry-Y lift 0.4 mm, straight move to the target at the lifted height, lower, unretract.
    No travel follows the belt-side wall any more. Z (belt) is never moved except where Orca
    already moved it (layer change).
"""
from __future__ import annotations
import re, math

LIFT_MM = 0.4
RETRACT_MM = 0.5
RETRACT_F = 30 * 60.0          # profile retraction_speed 30 mm/s
RETRACT_MIN_TRAVEL = 3.0       # D: retract only on travels >= 3 mm
RESTART_EXTRA = -0.05          # D: unretract 0.05 mm less than the retract
BELT_SIDE_BAND = 1.6           # D: travel end within 1.6 mm of the layer's lowest Y = belt side
BELT_BAND = 0.1                # belt-contact: within 0.1 mm of the layer's lowest Y
OPEN_BELT_BAND = 1.6           # open inner paths: split only where they run on the belt side
_NUM = r"(-?\d*\.?\d+)"


def _w(line, letter):
    m = re.search(rf"\b{letter}{_NUM}", line.split(";", 1)[0])
    return float(m.group(1)) if m else None


def _is_move(l):
    return l.startswith(("G0 ", "G1 "))


def _is_ext(l):
    b = l.split(";")[0]
    return _is_move(l) and (_w(l, "E") or 0) > 0 and ("X" in b or "Y" in b)


def _is_xy_travel(l):
    b = l.split(";")[0]
    return _is_move(l) and ("X" in b or "Y" in b) and not ((_w(l, "E") or 0) > 0)


TOP_ENV = {}
TOP_REVERSE = False  # tested: moves rim-tip starts to junctions, but both are within 2 mm of the top in layers 415-437 -> no gain
TOP_BAND = 2.0   # D: "top side" = within 2 mm (in-plane, gantry Y x 0.707) of the upper outer-wall envelope


def is_top(layer, p, ymin):
    if p[1] <= ymin + OPEN_BELT_BAND:
        return False
    return any(math.hypot(p[0] - ex, (p[1] - ey) * 0.7071) <= TOP_BAND for ex, ey in TOP_ENV.get(layer, ()) if abs(ex - p[0]) <= TOP_BAND + 1)


def layer_seam_x(text):
    """layer index (same counting as rotate: -1 before first ;LAYER_CHANGE) -> belt-span midpoint X."""
    layer = -1; role = ""; x = y = 0.0
    segs = {}
    for l in text.splitlines():
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
        elif l.startswith(";TYPE:"):
            role = l[6:].strip()
        if _is_move(l):
            nx, ny = _w(l, "X"), _w(l, "Y")
            nx = x if nx is None else nx; ny = y if ny is None else ny
            if _is_ext(l) and role in ("Outer wall", "Overhang wall"):
                segs.setdefault(layer, []).append((x, y, nx, ny))
            x, y = nx, ny
    out = {}
    for k, ss in segs.items():
        env = {}
        for x0, y0, x1, y1 in ss:
            m = max(1, int(math.hypot(x1 - x0, y1 - y0) / 0.3))
            for t in range(m + 1):
                X = x0 + (x1 - x0) * t / m; Y = y0 + (y1 - y0) * t / m
                b = int(X // 1); env[b] = max(env.get(b, -1e9), Y)
        TOP_ENV[k] = sorted((b + 0.5, v) for b, v in env.items())
        ymin = min(min(s[1], s[3]) for s in ss)
        xs = [v for s in ss if max(s[1], s[3]) <= ymin + BELT_BAND for v in (s[0], s[2])]
        if xs:
            out[k] = ((min(xs) + max(xs)) / 2, ymin)
    return out


def process_walls(text, PP, seam):
    """Seam-rotate closed loops at the per-layer seam X; split open inner paths at it."""
    lines = text.splitlines(keepends=True)
    out = []
    x = y = 0.0; f = 0.0; role = ""; layer = -1
    i, n = 0, len(lines)
    glide_f = PP.GLIDE_MM_S * 60.0
    TF = PP.TRAVEL_F
    allowed_all = ("Outer wall", "Overhang wall", "Inner wall")
    stats = {"loops": 0, "split": 0, "decomposed": 0}

    def emit(segs, cur_role, last_f=None):
        for a2, b2, e2, f2, r2, pre2 in segs:
            out.extend(p for p in pre2 if not p.startswith(";TYPE:"))
            if r2 != cur_role:
                out.append(f";TYPE:{r2}\n"); cur_role = r2
            fs = f" F{f2:g}" if f2 != last_f else ""
            out.append(f"G1 X{b2[0]:.3f} Y{b2[1]:.3f} E{e2:.5f}{fs}\n")
            last_f = f2
        return cur_role

    def travel_to(p, pos, tag):
        if math.dist(p, pos) > 1e-3:
            out.append(f"G1 X{p[0]:.3f} Y{p[1]:.3f} F{TF:g} ;{tag}\n")

    while i < n:
        l = lines[i]
        if l.startswith(";TYPE:"):
            role = l[6:].strip()
        elif l.startswith(";LAYER_CHANGE"):
            layer += 1          # role kept across layer change (B's fix)
        if not (_is_ext(l) and role in allowed_all):
            if _is_move(l):
                nx, ny = _w(l, "X"), _w(l, "Y"); fv = _w(l, "F")
                f = fv if fv is not None else f
                x = nx if nx is not None else x; y = ny if ny is not None else y
            out.append(l); i += 1; continue
        kind_outer = role in ("Outer wall", "Overhang wall")
        allowed = ("Outer wall", "Overhang wall") if kind_outer else ("Inner wall",)
        start = (x, y)
        segs = []; pending = []
        j = i; cx, cy, cf, crole = x, y, f, role
        while j < n:
            lj = lines[j]
            if _is_move(lj):
                body = lj.split(";")[0]; e = _w(lj, "E")
                if e is None or e <= 0 or not ("X" in body or "Y" in body):
                    break
                nx, ny = _w(lj, "X"), _w(lj, "Y")
                nx = cx if nx is None else nx; ny = cy if ny is None else ny
                fv = _w(lj, "F"); cf = fv if fv is not None else cf
                segs.append(((cx, cy), (nx, ny), e, cf, crole, pending)); pending = []
                cx, cy = nx, ny; j += 1
            elif lj.startswith(";TYPE:"):
                r = lj[6:].strip()
                if r not in allowed:
                    break
                crole = r; pending.append(lj); j += 1
            elif lj.startswith(";LAYER_CHANGE") or lj.startswith("; stop printing"):
                break
            else:
                pending.append(lj); j += 1
        sx, ymin_l = seam.get(layer, (PP.SEAM_TARGET_X_MM, 0.0))
        # Decompose the run into pieces: (closed?, segs)
        pts = [segs[0][0]] + [s[1] for s in segs]
        close = lambda a, b: abs(a[0] - b[0]) < PP._CLOSE_TOL and abs(a[1] - b[1]) < PP._CLOSE_TOL
        pieces = None
        if len(segs) >= 3 and close(pts[0], pts[-1]):
            pieces = [(True, segs)]
        else:
            # open prefix + closed suffix (pk == pn), or closed prefix (p0 == pk) + open suffix
            k_suf = next((k for k in range(1, len(segs) - 2) if close(pts[k], pts[-1])), None)
            k_pre = next((k for k in range(len(segs), 2, -1) if close(pts[0], pts[k])), None)
            if k_suf is not None and (k_pre is None or len(segs) - k_suf >= k_pre):
                pieces = [(False, segs[:k_suf]), (True, segs[k_suf:])]
            elif k_pre is not None and k_pre < len(segs):
                pieces = [(True, segs[:k_pre]), (False, segs[k_pre:])]
            else:
                pieces = [(False, segs)]
            if len(pieces) > 1:
                stats["decomposed"] += 1
        stop = j - len(pending)
        if len(pieces) == 1 and not pieces[0][0] and (kind_outer or not (_crosses(segs, sx, ymin_l) or _want_rev(segs, sx, ymin_l) or _top_rev(segs, layer, ymin_l, sx))):
            out.extend(lines[i:stop]); x, y, f, role = cx, cy, cf, crole; i = stop; continue
        pos = start; cur_role = role
        for is_closed, ps in pieces:
            p0 = ps[0][0]
            if not is_closed:
                k = _crosses(ps, sx, ymin_l) if not kind_outer else None
                if k is None and not kind_outer and (_want_rev(ps, sx, ymin_l) or _top_rev(ps, layer, ymin_l, sx)):
                    # orient: start at the end nearer the seam X, finish at the far (end-cap) end
                    rv = [(sg[1], sg[0], sg[2], sg[3], sg[4], sg[5]) for sg in reversed(ps)]
                    travel_to(rv[0][0], pos, "seam travel")
                    out.append(";C_SEAM_START\n")
                    cur_role = emit(rv, cur_role); pos = rv[-1][1]
                    out.append(";C_SEAM_END\n")
                    stats["reversed"] = stats.get("reversed", 0) + 1
                    continue
                if k is None:
                    if math.dist(p0, pos) > 1e-3:
                        travel_to(p0, pos, "seam return")
                    cur_role = emit(ps, cur_role); pos = ps[-1][1]
                    continue
                kk, q = k
                a, b, e, sf, sr, pre = ps[kk]
                L = math.dist(a, b); t = 0.0 if L == 0 else math.dist(a, q) / L
                # half 2 first: q -> a (reversed), then segs kk-1..0 reversed; ends at p0
                rev = [(q, a, e * t, sf, sr, pre)] if t > 1e-9 else []
                for m in range(kk - 1, -1, -1):
                    s = ps[m]; rev.append((s[1], s[0], s[2], s[3], s[4], s[5]))
                fwd = ([(q, b, e * (1 - t), sf, sr, [p for p in pre if p.startswith(";WIDTH")])] if t < 1 - 1e-9 else []) + list(ps[kk + 1:])
                for half in (rev, fwd):
                    if not half: continue
                    travel_to(q, pos, "seam travel")
                    out.append(";C_SEAM_START\n")
                    cur_role = emit(half, cur_role)
                    pos = half[-1][1]
                    out.append(";C_SEAM_END\n")
                stats["split"] += 1
                continue
            # closed loop: rotate to nearest point on lowest edge to sx
            ty = min(min(sg[0][1], sg[1][1]) for sg in ps)

            def _pick(band):
                cand = [k for k, sg in enumerate(ps) if max(sg[0][1], sg[1][1]) <= ty + band] or list(range(len(ps)))
                best = None
                for k in cand:
                    t, q = PP._nearest_on_seg(ps[k][0], ps[k][1], sx, ty)
                    d = (q[0] - sx) ** 2 + (q[1] - ty) ** 2
                    if best is None or d < best[0]:
                        best = (d, k, t, q)
                return best
            best = _pick(PP.SEAM_BAND_MM)
            if not kind_outer and abs(best[3][0] - sx) > 1.0:
                best = _pick(PP.INNER_STACK_BAND_MM)
            _, k, t, q = best
            lp = [ps[0][0]] + [sg[1] for sg in ps]
            cum = PP._arc(lp)
            sQ = cum[k] + t * (cum[k + 1] - cum[k])
            a, b, e, sf, sr, pre = ps[k]
            new = []
            if t < 1.0 - 1e-9: new.append((q, b, e * (1 - t), sf, sr, pre))
            new.extend(ps[k + 1:]); new.extend(ps[:k])
            if t > 1e-9: new.append((a, q, e * t, sf, sr, []))
            travel_to(q, pos, "seam travel")
            out.append(";C_SEAM_START\n")
            cur_role = emit(new, cur_role)
            pos = q
            if kind_outer and PP.GLIDE_MM < cum[-1] / 2:
                out.append(";WIPE_START\n")
                for px, py in PP._path(lp, cum, sQ, sQ + PP.GLIDE_MM):
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{glide_f:g} ;glide\n")
                    pos = (px, py)
                out.append(";WIPE_END\n")
            out.append(";C_SEAM_END\n")
            stats["loops"] += 1
        out.extend(p for p in pending if not p.startswith(";TYPE:"))
        # Orca's following lines assume the nozzle at the run end (cx, cy): hand back via a
        # seam travel (collapsed later into one lifted direct move).
        travel_to((cx, cy), pos, "seam return")
        if cur_role != crole:
            out.append(f";TYPE:{crole}\n")
        out.append(f"G1 F{cf:g}\n")
        x, y, f, role = cx, cy, cf, crole
        i = j
    return "".join(out), stats


def _top_rev(segs, layer, ymin, sx):
    """D: open path starting on the top side: start at the end nearer the seam X (the junction
    with the inner loop), so the free tip at the rim is only an end (no start / prime there)."""
    if not TOP_REVERSE:
        return False
    a, b = segs[0][0], segs[-1][1]
    return is_top(layer, a, ymin) and abs(b[0] - sx) < abs(a[0] - sx) - 0.5


def _want_rev(segs, sx, ymin):
    a, b = segs[0][0], segs[-1][1]
    return a[1] <= ymin + OPEN_BELT_BAND and b[1] <= ymin + OPEN_BELT_BAND and abs(a[0] - sx) > abs(b[0] - sx) + 0.5


def _crosses(segs, sx, ymin):
    """Index + point where an open path crosses X=sx on the belt side (lowest such crossing)."""
    best = None
    for k, (a, b, *_r) in enumerate(segs):
        if (a[0] - sx) * (b[0] - sx) <= 0 and a[0] != b[0]:
            t = (sx - a[0]) / (b[0] - a[0]); q = (sx, a[1] + t * (b[1] - a[1]))
            if q[1] <= ymin + OPEN_BELT_BAND and (best is None or q[1] < best[1][1]):
                best = (k, q)
    if best is None: return None
    k, q = best
    # avoid degenerate splits right at a path end (< 0.3 mm)
    pts = [segs[0][0]] + [s[1] for s in segs]
    if math.dist(q, pts[0]) < 0.3 or math.dist(q, pts[-1]) < 0.3:
        return None
    return best


def collapse_seam_travels(text, TF, seam_map=None):
    """Replace every seam travel by: [retract] lift, direct move, lower, [unretract]."""
    lines = text.splitlines(keepends=True)
    out = []
    x = y = 0.0; z = None; f = None
    first_lc = next((k for k, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")), len(lines))
    region = []      # (line_index_in_lines, line)
    seam_from = False
    stats = {"seam_travels": 0, "retracts": 0, "nonbelt_plain": 0}
    layer = -1; pos_layer = -1
    ymin_of = lambda k: (seam_map or {}).get(k, (0, 0.0))[1]

    def track(l):
        nonlocal x, y, z, f
        if _is_move(l):
            nx, ny, nz, nf = _w(l, "X"), _w(l, "Y"), _w(l, "Z"), _w(l, "F")
            x = nx if nx is not None else x; y = ny if ny is not None else y
            z = nz if nz is not None else z; f = nf if nf is not None else f

    def flush(seam):
        nonlocal region
        trav = [k for k, (idx, l) in enumerate(region) if idx > first_lc and _is_xy_travel(l)]
        if not (seam and trav):
            for _, l in region:
                out.append(l); track(l)
            region = []; return
        stats["seam_travels"] += 1
        last = trav[-1]
        # target = endpoint of the last travel (track through the travel lines virtually)
        tx, ty, tz, tf = x, y, z, f
        for k, (idx, l) in enumerate(region):
            if _is_move(l):
                nx, ny, nz, nf = _w(l, "X"), _w(l, "Y"), _w(l, "Z"), _w(l, "F")
                tx = nx if nx is not None else tx; ty = ny if ny is not None else ty
                tz = nz if nz is not None else tz; tf = nf if nf is not None else tf
            if k == last: break
        if not (y <= ymin_of(pos_layer) + BELT_SIDE_BAND or ty <= ymin_of(layer) + BELT_SIDE_BAND):
            stats["nonbelt_plain"] += 1; stats["seam_travels"] -= 1
            for _, l in region:
                out.append(l); track(l)
            region = []; return
        zmove = any(_w(region[k][1], "Z") is not None for k in trav)
        for k, (idx, l) in enumerate(region):
            if k in trav and k != last:
                continue
            if k == last:
                d = math.hypot(tx - x, ty - y)
                if d < 1e-3:
                    if tf is not None:
                        out.append(f"G1 F{tf:g}\n"); track(f"G1 F{tf:g}\n")
                    continue
                # D: only prime (unretract) where the travel lands on the belt side
                retract = d >= RETRACT_MIN_TRAVEL and ty <= ymin_of(layer) + BELT_SIDE_BAND
                blk = [";C seam travel: lift + direct\n"]
                if retract:
                    blk.append(f"G1 E-{RETRACT_MM:g} F{RETRACT_F:g}\n"); stats["retracts"] += 1
                blk.append(f"G1 Y{y + LIFT_MM:.3f} F{TF:g} ;lift\n")
                zs = f" Z{tz:.3f}" if zmove else ""
                blk.append(f"G1 X{tx:.3f} Y{ty + LIFT_MM:.3f}{zs} F{TF:g} ;seam travel\n")
                blk.append(f"G1 Y{ty:.3f} F{TF:g} ;lower\n")
                if retract:
                    blk.append(f"G1 E{RETRACT_MM + RESTART_EXTRA:g} F{RETRACT_F:g}\n")
                if tf is not None:
                    blk.append(f"G1 F{tf:g}\n")
                for b_ in blk:
                    out.append(b_); track(b_)
                continue
            out.append(l); track(l)
        region = []

    for idx, l in enumerate(lines):
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
        if l.startswith(";C_SEAM_END"):
            flush(False)          # glide etc. before the marker stays as is
            out.append(l); seam_from = True; continue
        if _is_ext(l):
            seam = seam_from or any(r.startswith(";C_SEAM_START") for _, r in region)
            flush(seam); seam_from = False
            out.append(l); track(l); pos_layer = layer; continue
        region.append((idx, l))
    flush(False)
    return "".join(out), stats


def postprocess_C(text, PP):
    text = PP.reorder_gap_fill(text)
    seam = layer_seam_x(text)
    text, s1 = process_walls(text, PP, seam)
    text, s2 = collapse_seam_travels(text, PP.TRAVEL_F, seam)
    # B's tail (belt-wall F1200 + per-printed-layer fan ramp), with B's rotation disabled.
    orig = PP.rotate_wall_seams
    PP.rotate_wall_seams = lambda t, **kw: t
    try:
        text = PP.postprocess_apex_light(text)
    finally:
        PP.rotate_wall_seams = orig
    import sys
    print("pp_c stats", s1, s2, file=sys.stderr)
    return text
