#!/usr/bin/env python3
"""ApexLight G-code post-process to match Kendon's Cura VERTEX_APEX_LITE_90A file.

1. Belt wall: Cura prints the outer-wall segments that lie on the belt (gantry
   Y at the belt surface) at 20 mm/s on every layer. Orca's ``belt_wall_enabled``
   is declared in PrintConfig but never read by the slicer, so this rewrites
   outer-wall extrusions whose start and end Y are both <= BELT_WALL_MAX_Y_MM
   to F1200 and restores the commanded feedrate afterwards.
2. Fan: Cura runs part fan 0 on printed layer 0, then ramps linearly to 35 % by
   printed layer 6 (aux fan P1 to 18 %), with no overhang bursts. Orca's ramp is
   keyed to virtual belt layer ids, which include empty leading layers, so the
   ramp is rewritten per *printed* layer here.
"""
from __future__ import annotations

import re

try:
    from validation.apex_light import AUX_FAN_PERCENT, BELT_WALL_MM_S, FAN_MAX_PERCENT
except ImportError:  # validation/ on sys.path directly
    from apex_light import AUX_FAN_PERCENT, BELT_WALL_MM_S, FAN_MAX_PERCENT

BELT_WALL_MAX_Y_MM = 0.1
FAN_RAMP_LAYERS = 6

_NUM = r"(-?\d*\.?\d+)"


def _word(line: str, letter: str) -> float | None:
    m = re.search(rf"\b{letter}{_NUM}", line.split(";", 1)[0])
    return float(m.group(1)) if m else None


def _fan_lines(printed_idx: int) -> list[str]:
    k = min(printed_idx, FAN_RAMP_LAYERS) / FAN_RAMP_LAYERS
    main = round(255 * FAN_MAX_PERCENT / 100 * k, 1)
    aux = int(int(255 * AUX_FAN_PERCENT / 100) * k)  # Cura: 7,15,22,30,37,45
    return [f"M106 S{main:g}\n", f"M106 P1 S{aux}\n"]


SEAM_TARGET_X_MM = 140.0  # Cura VERTEX_APEX_LITE_90A seam cluster (bed centre, same in both)
GLIDE_MM = 0.7
GLIDE_MM_S = 15.6
_CLOSE_TOL = 0.05
SEAM_BAND_MM = 0.15
INNER_STACK_BAND_MM = 1.5  # inner loop may rise slightly to stay stacked on the outer seam
try:
    from validation.apex_light import TRAVEL_MM_S
except ImportError:
    from apex_light import TRAVEL_MM_S
TRAVEL_F = TRAVEL_MM_S * 60.0


def _xy(line: str):
    body = line.split(";", 1)[0]
    return _word(body, "X"), _word(body, "Y")


def _is_move(line: str) -> bool:
    return line.startswith(("G0 ", "G1 "))


def _nearest_on_seg(a, b, tx, ty):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((tx - ax) * dx + (ty - ay) * dy) / L2))
    return t, (ax + t * dx, ay + t * dy)


def _rewrite_xy(line: str, x: float, y: float) -> str:
    body, sep, cmt = line.rstrip("\n").partition(";")
    body = re.sub(rf"\s*\b[XY]{_NUM}", "", body).rstrip()
    parts = body.split(" ", 1)
    rest = (" " + parts[1]) if len(parts) > 1 else ""
    return f"{parts[0]} X{x:.3f} Y{y:.3f}{rest}" + (f" ;{cmt}" if sep else "") + "\n"


def _arc(pts):
    cum = [0.0]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        cum.append(cum[-1] + ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5)
    return cum


def _point_at(pts, cum, s):
    tot = cum[-1]
    s = s % tot if tot > 0 else 0.0
    for i in range(1, len(cum)):
        if cum[i] >= s:
            L = cum[i] - cum[i - 1]
            t = 0.0 if L == 0 else (s - cum[i - 1]) / L
            (ax, ay), (bx, by) = pts[i - 1], pts[i]
            return (ax + t * (bx - ax), ay + t * (by - ay))
    return pts[-1]


def _path(pts, cum, s0, s1):
    """Points along the closed loop from arc position s0 to s1, the shorter way (s1 included)."""
    tot = cum[-1]
    fwd = (s1 - s0) % tot
    step = fwd if fwd <= tot - fwd else -((s0 - s1) % tot)
    lo, hi = (s0, s0 + step) if step >= 0 else (s0 + step, s0)
    verts = []
    for k in range(-1, 2):
        for c in cum[1:-1]:
            v = c + k * tot
            if lo < v < hi:
                verts.append(v)
    verts.sort(reverse=step < 0)
    return [_point_at(pts, cum, v) for v in verts] + [_point_at(pts, cum, s0 + step)]


_LOOP_ROLES = {"Outer wall": ("Outer wall", "Overhang wall"), "Inner wall": ("Inner wall",)}


def rotate_wall_seams(text: str, _inner_x: dict | None = None, _collect: dict | None = None) -> str:
    """Kendon rule: every wall loop starts/ends on the belt edge, at one consistent X.

    Closed outer loops (Outer + Overhang wall pieces) start at the belt-contact point
    nearest SEAM_TARGET_X_MM; closed inner loops at their lowest edge nearest that X.
    The extrusion segments are only reordered (the split segment's E is divided
    proportionally), so per-loop E is unchanged. Moves from Orca's start point to the
    new seam (and back after the loop) are non-extruding travels that follow the loop
    path itself, so they cannot cross other walls. Outer loops get a Cura-style 0.7 mm
    glide at GLIDE_MM_S after the seam. Requires relative E (M83).
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = 0.0
    f = 0.0
    role = ""
    i = 0
    n = len(lines)
    glide_f = GLIDE_MM_S * 60.0
    layer = -1
    while i < n:
        l = lines[i]
        if l.startswith(";TYPE:"):
            role = l[6:].strip()
        elif l.startswith(";LAYER_CHANGE"):
            role = ""
            layer += 1
        body0 = l.split(";")[0]
        ext = _is_move(l) and (_word(l, "E") or 0) > 0 and ("X" in body0 or "Y" in body0)
        kind = "Outer wall" if role in _LOOP_ROLES["Outer wall"] else ("Inner wall" if role == "Inner wall" else None)
        if not (ext and kind):
            if _is_move(l):
                nx, ny = _xy(l)
                fv = _word(l, "F")
                f = fv if fv is not None else f
                x = nx if nx is not None else x
                y = ny if ny is not None else y
            out.append(l)
            i += 1
            continue
        allowed = _LOOP_ROLES[kind]
        start = (x, y)
        segs = []  # (a, b, e, f, role, prefix_lines)
        pending: list[str] = []
        j = i
        cx, cy, cf, crole = x, y, f, role
        while j < n:
            lj = lines[j]
            if _is_move(lj):
                body = lj.split(";")[0]
                e = _word(lj, "E")
                if e is None or e <= 0 or not ("X" in body or "Y" in body):
                    break
                nx, ny = _xy(lj)
                nx = cx if nx is None else nx
                ny = cy if ny is None else ny
                fv = _word(lj, "F")
                cf = fv if fv is not None else cf
                segs.append(((cx, cy), (nx, ny), e, cf, crole, pending))
                pending = []
                cx, cy = nx, ny
                j += 1
            elif lj.startswith(";TYPE:"):
                r = lj[6:].strip()
                if r not in allowed:
                    break
                crole = r
                pending.append(lj)
                j += 1
            elif lj.startswith(";LAYER_CHANGE") or lj.startswith("; stop printing"):
                break
            else:
                pending.append(lj)
                j += 1
        closed = len(segs) >= 3 and abs(cx - start[0]) < _CLOSE_TOL and abs(cy - start[1]) < _CLOSE_TOL
        stop = j - len(pending)
        if not closed:
            out.extend(lines[i:stop])
            x, y, f, role = cx, cy, cf, crole
            i = stop
            continue
        ty = min(min(sg[0][1], sg[1][1]) for sg in segs)
        tx = SEAM_TARGET_X_MM
        if kind == "Inner wall" and _inner_x and _inner_x.get(layer):
            # Stack the inner seam on this layer's nearest outer seam.
            tx = min(_inner_x[layer], key=lambda v: abs(v - (sum(p_[0][0] for p_ in segs) / len(segs))))

        def _pick(band):
            cand = [k for k, sg in enumerate(segs) if max(sg[0][1], sg[1][1]) <= ty + band] or list(range(len(segs)))
            best = None
            for k in cand:
                t, q = _nearest_on_seg(segs[k][0], segs[k][1], tx, ty)
                d = (q[0] - tx) ** 2 + (q[1] - ty) ** 2
                if best is None or d < best[0]:
                    best = (d, k, t, q)
            return best

        best = _pick(SEAM_BAND_MM)
        if kind == "Inner wall" and abs(best[3][0] - tx) > 1.0:
            best = _pick(INNER_STACK_BAND_MM)
        _, k, t, q = best
        if _collect is not None and kind == "Outer wall":
            _collect.setdefault(layer, []).append(q[0])
        pts = [segs[0][0]] + [sg[1] for sg in segs]
        cum = _arc(pts)
        sQ = cum[k] + t * (cum[k + 1] - cum[k])
        a, b, e, sf, sr, pre = segs[k]
        new = []
        if t < 1.0 - 1e-9:
            new.append((q, b, e * (1 - t), sf, sr, pre))
        new.extend(segs[k + 1:])
        new.extend(segs[:k])
        if t > 1e-9:
            new.append((a, q, e * t, sf, sr, []))
        # Travel along the loop from Orca's start to the seam.
        if sQ > 1e-6 and cum[-1] - sQ > 1e-6:
            for px, py in _path(pts, cum, 0.0, sQ):
                out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g} ;seam travel\n")
        last_f = None
        cur_role = role
        for a2, b2, e2, f2, r2, pre2 in new:
            pre2 = [p2 for p2 in pre2 if not p2.startswith(";TYPE:")]
            out.extend(pre2)
            if r2 != cur_role:
                out.append(f";TYPE:{r2}\n")
                cur_role = r2
            fs = f" F{f2:g}" if f2 != last_f else ""
            out.append(f"G1 X{b2[0]:.3f} Y{b2[1]:.3f} E{e2:.5f}{fs}\n")
            last_f = f2
        out.extend(p2 for p2 in pending if not p2.startswith(";TYPE:"))
        sEnd = sQ
        if kind == "Outer wall":
            if GLIDE_MM < cum[-1] / 2:
                out.append(";WIPE_START\n")
                for px, py in _path(pts, cum, sQ, sQ + GLIDE_MM):
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{glide_f:g} ;glide\n")
                out.append(";WIPE_END\n")
            sEnd = sQ + GLIDE_MM
        # Return along the loop to Orca's start point so Orca's own travels stay valid.
        if abs(((sEnd % cum[-1]) + 1e-9) % cum[-1]) > 1e-6:
            for px, py in _path(pts, cum, sEnd, cum[-1]):
                out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g} ;seam travel\n")
        if cur_role != crole:
            out.append(f";TYPE:{crole}\n")
        out.append(f"G1 F{cf:g}\n")
        x, y, f, role = start[0], start[1], cf, crole
        i = j
    return "".join(out)


def reorder_gap_fill(text: str) -> str:
    """Chain each Gap infill block's paths nearest-neighbour (paths may be reversed).

    Orca's classic gap fill emits hundreds of short paths per layer in an order that
    jumps back and forth across the part (~160k travels on an insole). Reordering keeps
    every extrusion move (same E, same width) and only shortens the travels between them.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = 0.0
    i, n = 0, len(lines)
    while i < n:
        l = lines[i]
        if not l.startswith(";TYPE:Gap infill"):
            if _is_move(l):
                nx, ny = _xy(l)
                x = nx if nx is not None else x
                y = ny if ny is not None else y
            out.append(l)
            i += 1
            continue
        out.append(l)
        i += 1
        paths = []  # [start, [(end, e, f, width_line)]]
        cur = None
        px, py = x, y
        width = None
        f = None
        while i < n:
            lj = lines[i]
            if lj.startswith((";TYPE:", ";LAYER_CHANGE", "; stop printing")):
                break
            if lj.startswith(";WIDTH:"):
                width = lj
            elif _is_move(lj):
                fv = _word(lj, "F")
                e = _word(lj, "E")
                nx, ny = _xy(lj)
                nx = px if nx is None else nx
                ny = py if ny is None else ny
                if e is not None and e > 0 and (nx, ny) != (px, py):
                    if fv is not None:
                        f = fv
                    if cur is None:
                        cur = [(px, py), []]
                        paths.append(cur)
                    cur[1].append(((nx, ny), e, f, width))
                elif (nx, ny) != (px, py):
                    cur = None
                elif fv is not None and e is None:
                    f = fv
                px, py = nx, ny
            # other lines (M204, M73) are dropped inside the block; accel is restored below
            i += 1
        last_f = None
        cx, cy = x, y
        while paths:
            best = None
            for k, (st, segs) in enumerate(paths):
                en = segs[-1][0]
                for rev, q in ((False, st), (True, en)):
                    d = (q[0] - cx) ** 2 + (q[1] - cy) ** 2
                    if best is None or d < best[0]:
                        best = (d, k, rev)
            _, k, rev = best
            st, segs = paths.pop(k)
            if rev:
                pts = [st] + [sg[0] for sg in segs]
                segs = [(pts[m], segs[m][1], segs[m][2], segs[m][3]) for m in range(len(segs) - 1, -1, -1)]
                st = pts[-1]
            if (abs(st[0] - cx) > 1e-4 or abs(st[1] - cy) > 1e-4):
                out.append(f"G1 X{st[0]:.3f} Y{st[1]:.3f} F{TRAVEL_F:g}\n")
                last_f = None
            for (ex, ey), e, sf, w in segs:
                if w:
                    out.append(w)
                fs = f" F{sf:g}" if sf != last_f and sf is not None else ""
                out.append(f"G1 X{ex:.3f} Y{ey:.3f} E{e:.5f}{fs}\n")
                last_f = sf
            cx, cy = segs[-1][0]
        if abs(px - cx) > 1e-4 or abs(py - cy) > 1e-4:
            # Hand back at Orca's expected position so the next feature starts correctly.
            out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g}\n")
        if f is not None:
            out.append(f"G1 F{f:g}\n")
        x, y = px, py
    return "".join(out)


def postprocess_apex_light(text: str) -> str:
    text = reorder_gap_fill(text)
    outer_x: dict = {}
    rotate_wall_seams(text, _collect=outer_x)  # pass 1: outer seam X per layer
    text = rotate_wall_seams(text, _inner_x=outer_x)
    lines = text.splitlines(keepends=True)
    try:
        first_lc = next(i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE"))
        end_custom = max(i for i, l in enumerate(lines) if l.startswith(";TYPE:Custom"))
    except (StopIteration, ValueError):
        return text
    if end_custom < first_lc:
        return text

    # Which layer blocks actually extrude.
    starts = [i for i in range(first_lc, end_custom) if lines[i].startswith(";LAYER_CHANGE")]
    bounds = list(zip(starts, starts[1:] + [end_custom]))
    printed: dict[int, int] = {}
    n = 0
    for a, b in bounds:
        for j in range(a, b):
            l = lines[j]
            if l.startswith("G1 ") and (_word(l, "E") or 0) > 0 and ("X" in l or "Y" in l):
                printed[a] = n
                n += 1
                break

    belt_f = BELT_WALL_MM_S * 60.0
    out: list[str] = []
    role = ""
    y = 0.0
    commanded_f: float | None = None
    emitted_f: float | None = None
    for i, l in enumerate(lines):
        in_print = first_lc <= i < end_custom
        if in_print and l.startswith("M106"):
            continue  # replaced by the per-printed-layer ramp
        if l.startswith(";TYPE:"):
            role = l[6:].strip()
        if l.startswith(("G0 ", "G1 ")):
            f = _word(l, "F")
            if f is not None:
                commanded_f = f
            ny = _word(l, "Y")
            ny = y if ny is None else ny
            e = _word(l, "E")
            on_belt = (
                in_print
                and role == "Outer wall"
                and e is not None
                and e > 0
                and max(y, ny) <= BELT_WALL_MAX_Y_MM
                and ("X" in l.split(";")[0] or "Y" in l.split(";")[0])
            )
            if on_belt:
                body, sep, cmt = l.rstrip("\n").partition(";")
                body = re.sub(rf"\s*\bF{_NUM}", "", body).rstrip()
                l = f"{body} F{belt_f:g}" + (f" ;{cmt}" if sep else "") + "\n"
                emitted_f = belt_f
            elif emitted_f is not None and f is None and commanded_f is not None and emitted_f != commanded_f:
                body, sep, cmt = l.rstrip("\n").partition(";")
                l = f"{body.rstrip()} F{commanded_f:g}" + (f" ;{cmt}" if sep else "") + "\n"
                emitted_f = commanded_f
            else:
                emitted_f = f if f is not None else emitted_f
            y = ny
        out.append(l)
        if i in printed:
            out.extend(_fan_lines(printed[i]))
    return "".join(out)
