#!/usr/bin/env python3
"""Belt-side wall seam rotation, end-cap passes, and infill anchoring for ApexLight."""
from __future__ import annotations

import re

try:
    from validation.apex_light import TRAVEL_MM_S
except ImportError:
    from apex_light import TRAVEL_MM_S

SEAM_TARGET_X_MM = 140.0
GLIDE_MM = 0.7
GLIDE_MM_S = 15.6
_CLOSE_TOL = 0.05
SEAM_BAND_MM = 0.15
INNER_STACK_BAND_MM = 1.5
BELT_BAND_MM = 0.25
LATERAL_CUP_LO = 339
LATERAL_CUP_HI = 391
HEEL_END_CAP_LO = 425
HEEL_END_CAP_HI = 441
END_CAP_MAX_F_MM_S = 20.0
_NUM = r"(-?\d*\.?\d+)"
TRAVEL_F = TRAVEL_MM_S * 60.0
_LOOP_ROLES = {"Outer wall": ("Outer wall", "Overhang wall"), "Inner wall": ("Inner wall",)}


def _word(line: str, letter: str) -> float | None:
    m = re.search(rf"\b{letter}{_NUM}", line.split(";", 1)[0])
    return float(m.group(1)) if m else None


def _xy(line: str):
    body = line.split(";", 1)[0]
    return _word(body, "X"), _word(body, "Y")


def _is_move(line: str) -> bool:
    return line.startswith(("G0 ", "G1 "))


def _nearest_on_seg(a, b, tx, ty):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((tx - ax) * dx + (ty - ay) * dy) / l2))
    return t, (ax + t * dx, ay + t * dy)


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
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg == 0 else (s - cum[i - 1]) / seg
            (ax, ay), (bx, by) = pts[i - 1], pts[i]
            return (ax + t * (bx - ax), ay + t * (by - ay))
    return pts[-1]


def _path(pts, cum, s0, s1):
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


def rotate_wall_seams(
    text: str,
    *,
    _inner_x: dict | None = None,
    _collect: dict | None = None,
    layer_lo: int | None = None,
    layer_hi: int | None = None,
    kinds: frozenset[str] | None = None,
    outer_glide: bool = True,
) -> str:
    if kinds is None:
        kinds = frozenset({"Outer wall", "Inner wall"})
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
        in_range = layer_lo is None or (layer_lo <= layer <= (layer_hi if layer_hi is not None else layer))
        body0 = l.split(";")[0]
        ext = _is_move(l) and (_word(l, "E") or 0) > 0 and ("X" in body0 or "Y" in body0)
        kind = "Outer wall" if role in _LOOP_ROLES["Outer wall"] else ("Inner wall" if role == "Inner wall" else None)
        if not in_range or not (ext and kind) or kind not in kinds:
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
        segs = []
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
        new.extend(segs[k + 1 :])
        new.extend(segs[:k])
        if t > 1e-9:
            new.append((a, q, e * t, sf, sr, []))
        if sQ > 1e-6 and cum[-1] - sQ > 1e-6:
            for px, py in _path(pts, cum, 0.0, sQ):
                if max(py, ty) <= ty + BELT_BAND_MM + 0.05:
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g} ;seam travel\n")
                else:
                    out.append(f"G1 X{px:.3f} Y{ty:.3f} F{TRAVEL_F:g} ;seam travel\n")
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
        s_end = sQ
        if kind == "Outer wall" and outer_glide and GLIDE_MM < cum[-1] / 2:
            out.append(";WIPE_START\n")
            for px, py in _path(pts, cum, sQ, sQ + GLIDE_MM):
                out.append(f"G1 X{px:.3f} Y{py:.3f} F{glide_f:g} ;glide\n")
            out.append(";WIPE_END\n")
            s_end = sQ + GLIDE_MM
        if abs(((s_end % cum[-1]) + 1e-9) % cum[-1]) > 1e-6:
            for px, py in _path(pts, cum, s_end, cum[-1]):
                if max(py, ty) <= ty + BELT_BAND_MM + 0.05:
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g} ;seam travel\n")
                else:
                    out.append(f"G1 X{px:.3f} Y{ty:.3f} F{TRAVEL_F:g} ;seam travel\n")
                    out.append(f"G1 X{px:.3f} Y{py:.3f} F{TRAVEL_F:g} ;seam travel\n")
        if cur_role != crole:
            out.append(f";TYPE:{crole}\n")
        out.append(f"G1 F{cf:g}\n")
        x, y, f, role = start[0], start[1], cf, crole
        i = j
    return "".join(out)


def _layer_index(lines: list[str]) -> dict[int, tuple[int, int]]:
    starts = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    return {n: (starts[n], starts[n + 1] if n + 1 < len(starts) else len(lines)) for n in range(len(starts))}


def cap_end_cap_extrusion_speed(text: str, lo: int = HEEL_END_CAP_LO, hi: int = HEEL_END_CAP_HI) -> str:
    max_f = END_CAP_MAX_F_MM_S * 60.0
    lines = text.splitlines(keepends=True)
    spans = _layer_index(lines)
    out = list(lines)
    for layer, (a, b) in spans.items():
        if layer < lo or layer > hi:
            continue
        y = 0.0
        for j in range(a, b):
            s = out[j].split(";", 1)[0].strip()
            if not s.startswith(("G0", "G1")):
                continue
            ny = _word(out[j], "Y")
            ny = y if ny is None else ny
            e = _word(out[j], "E")
            if e is not None and e > 0 and max(y, ny) <= BELT_BAND_MM + 0.02:
                body, sep, cmt = out[j].rstrip("\n").partition(";")
                body = re.sub(rf"\s*\bF{_NUM}", "", body).rstrip()
                cur = _word(out[j], "F") or max_f
                nf = min(cur, max_f)
                out[j] = f"{body} F{nf:g}" + (f" ;{cmt}" if sep else "") + "\n"
            y = ny
    return "".join(out)


def anchor_sparse_infill_belt(text: str, lo: int = LATERAL_CUP_LO, hi: int = LATERAL_CUP_HI) -> str:
    """Sparse infill paths in the cup band start from the belt-side (min Y) vertex."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = 0.0
    layer = -1
    i, n = 0, len(lines)
    while i < n:
        l = lines[i]
        if l.startswith(";LAYER_CHANGE"):
            layer += 1
        if not (lo <= layer <= hi) or not l.startswith(";TYPE:Sparse infill"):
            if _is_move(l):
                nx, ny = _xy(l)
                x = nx if nx is not None else x
                y = ny if ny is not None else y
            out.append(l)
            i += 1
            continue
        out.append(l)
        i += 1
        paths: list[tuple[tuple[float, float], list]] = []
        cur = None
        px, py = x, y
        role = "Sparse infill"
        while i < n:
            lj = lines[i]
            if lj.startswith((";TYPE:", ";LAYER_CHANGE", "; stop printing")):
                break
            if lj.startswith(";TYPE:"):
                role = lj[6:].strip()
                i += 1
                continue
            if _is_move(lj):
                e = _word(lj, "E")
                nx, ny = _xy(lj)
                nx = px if nx is None else nx
                ny = py if ny is None else ny
                if e is not None and e > 0 and (nx, ny) != (px, py):
                    if cur is None:
                        cur = [(px, py), []]
                        paths.append(cur)
                    cur[1].append(((nx, ny), lj))
                elif (nx, ny) != (px, py):
                    cur = None
                px, py = nx, ny
            else:
                out.append(lj)
            i += 1
        cx, cy = x, y
        for st, segs in paths:
            if not segs:
                continue
            pts = [st] + [s[0] for s in segs]
            belt_y = min(p[1] for p in pts)
            belt_pts = [p for p in pts if p[1] <= belt_y + 0.05]
            entry = min(belt_pts, key=lambda p: (p[1], abs(p[0] - SEAM_TARGET_X_MM)))
            if abs(entry[0] - cx) > 0.02 or abs(entry[1] - cy) > 0.02:
                out.append(f"G1 X{entry[0]:.3f} Y{entry[1]:.3f} F{TRAVEL_F:g} ;infill belt entry\n")
            for end, raw in segs:
                out.append(raw)
            cx, cy = segs[-1][0]
        x, y = px, py
    return "".join(out)


def fix_off_belt_travels(text: str, layer: int = 19, min_y: float = 1.5) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    cur_layer = -1
    x = y = 0.0
    for l in lines:
        if l.startswith(";LAYER_CHANGE"):
            cur_layer += 1
        if cur_layer == layer and _is_move(l) and (_word(l, "E") or 0) <= 0:
            nx, ny = _xy(l)
            nx = x if nx is None else nx
            ny = y if ny is None else ny
            if ny >= min_y and nx > 100.0:
                belt_y = 0.016
                out.append(f"G1 X{nx:.3f} Y{belt_y:.3f} F{TRAVEL_F:g} ;belt detour\n")
                out.append(f"G1 X{nx:.3f} Y{ny:.3f} F{TRAVEL_F:g} ;belt detour\n")
                x, y = nx, ny
                continue
        if _is_move(l):
            nx, ny = _xy(l)
            x = nx if nx is not None else x
            y = ny if ny is not None else y
        out.append(l)
    return "".join(out)


def apply_belt_seam_postprocess(text: str) -> str:
    outer_x: dict = {}
    text = rotate_wall_seams(text, _collect=outer_x, kinds=frozenset({"Outer wall"}))
    text = rotate_wall_seams(text, _inner_x=outer_x, kinds=frozenset({"Inner wall"}))
    text = rotate_wall_seams(
        text,
        _inner_x=outer_x,
        layer_lo=LATERAL_CUP_LO,
        layer_hi=LATERAL_CUP_HI,
        kinds=frozenset({"Inner wall"}),
        outer_glide=False,
    )
    text = rotate_wall_seams(
        text,
        _inner_x=outer_x,
        layer_lo=HEEL_END_CAP_LO,
        layer_hi=HEEL_END_CAP_HI,
        kinds=frozenset({"Outer wall", "Inner wall"}),
        outer_glide=False,
    )
    text = anchor_sparse_infill_belt(text)
    text = cap_end_cap_extrusion_speed(text)
    text = fix_off_belt_travels(text)
    return text
