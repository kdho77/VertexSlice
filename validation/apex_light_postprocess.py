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


try:
    from validation.apex_belt_seam import TRAVEL_F, apply_belt_seam_postprocess
except ImportError:
    from apex_belt_seam import TRAVEL_F, apply_belt_seam_postprocess

BELT_CONTACT_Y_MM = 0.25


def _xy(line: str):
    body = line.split(";", 1)[0]
    return _word(body, "X"), _word(body, "Y")


def _is_move(line: str) -> bool:
    return line.startswith(("G0 ", "G1 "))


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
                    if min(py, ny) <= BELT_CONTACT_Y_MM:
                        px, py = nx, ny
                        continue
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
    text = apply_belt_seam_postprocess(text)
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
