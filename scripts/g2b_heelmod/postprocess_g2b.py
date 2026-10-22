#!/usr/bin/env python3
"""G2b post-process: shear E fix, belt-side seam, min-layer-time, coupon cut.

efix rescales every extruding XY move from the belt-shear length
sqrt(dx^2+2*dy^2) back to the true XY length. It never changes F.

The seam block matches the G2b toe file: belt-side start on closed wall
loops, retract 0.5 / lift +0.4 Y / prime 0.45, travel at the machine travel
feed, and a 0.7 mm outer wipe at 60% of that wall's feed. Infill travels get
the lift only (no retract). E on split edges is divided by true XY length so
outer E/mm stays flat across a layer.

Min-time slows a layer whose true-XY time is under 8 s. Feed is scaled and
clamped to a 10 mm/s floor. E is not touched.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
WALLS = {"Outer wall", "Inner wall", "Overhang wall"}
INFILL = {
    "Sparse infill",
    "Internal solid infill",
    "Gap infill",
    "Top surface",
    "Bottom surface",
    "Bridge",
    "Internal Bridge",
}
RETRACT = 0.5
PRIME = 0.45
LIFT = 0.4
RETRACT_F = 1800
TRAVEL_F = 12720  # 212 mm/s
WIPE_MM = 0.7
WIPE_FRAC = 0.6


def _body(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _words(line: str) -> dict[str, str]:
    return dict(WORD.findall(_body(line)))


def _is_g01(line: str) -> bool:
    b = _body(line)
    return b.startswith("G0 ") or b.startswith("G1 ") or b in ("G0", "G1") or b.startswith("G0") or b.startswith("G1")


def efix(text: str) -> tuple[str, int]:
    """E *= hypot(dx,dy) / sqrt(dx^2+2*dy^2) on extruding XY moves. Relative E only."""
    out: list[str] = []
    x = y = 0.0
    n = 0
    for line in text.splitlines(keepends=True):
        body, sep, com = line.partition(";")
        b = body.strip()
        if b.startswith(("G0 ", "G1 ", "G0", "G1")):
            d = dict(WORD.findall(b))
            nx = float(d["X"]) if "X" in d else x
            ny = float(d["Y"]) if "Y" in d else y
            if "E" in d and float(d["E"]) > 0 and ("X" in d or "Y" in d):
                dx, dy = nx - x, ny - y
                den = math.sqrt(dx * dx + 2 * dy * dy)
                if den > 0:
                    e = float(d["E"]) * math.hypot(dx, dy) / den
                    body = re.sub(r"E[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", f"E{e:.5f}", body, count=1)
                    n += 1
                    line = body + sep + com
            x, y = nx, ny
        out.append(line)
    return "".join(out), n


def _fmt(n: float, nd: int) -> str:
    return f"{n:.{nd}f}"


def _belt_split(segments: list[dict]) -> list[dict]:
    """Rotate a closed wall loop so it starts at the belt-side (min-Y) edge midpoint."""
    if len(segments) < 3:
        return segments
    pts = [(segments[0]["x0"], segments[0]["y0"])] + [(s["x1"], s["y1"]) for s in segments]
    if math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) > 0.8:
        return segments
    ymin = min(p[1] for p in pts)
    best_i = None
    best_len = -1.0
    for i, s in enumerate(segments):
        if s["y0"] <= ymin + 0.25 and s["y1"] <= ymin + 0.25:
            length = math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"])
            if length > best_len:
                best_len = length
                best_i = i
    if best_i is None or best_len < 2.0:
        # Fall back to the vertex nearest the belt, ties broken toward the loop's X mid-span.
        xs = [p[0] for p in pts]
        mid_x = 0.5 * (min(xs) + max(xs))
        best_d = 1e9
        best_i = 0
        for i, s in enumerate(segments):
            d = (s["y0"] - ymin) * 1000 + abs(s["x0"] - mid_x)
            if d < best_d:
                best_d = d
                best_i = i
        return segments[best_i:] + segments[:best_i]

    s = segments[best_i]
    length = math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) or 1.0
    t = 0.5
    mx = s["x0"] + (s["x1"] - s["x0"]) * t
    my = s["y0"] + (s["y1"] - s["y0"]) * t
    e = s["e"]
    f = s["f"]
    comments = s["comments"]

    def _synth(x1: float, y1: float, ee: float, ff: str | None) -> str:
        parts = ["G1", f"X{_fmt(x1, 3)}", f"Y{_fmt(y1, 3)}", f"E{ee:.5f}"]
        if ff:
            parts.append(f"F{float(ff):.0f}")
        return " ".join(parts) + "\n"

    # Start at the midpoint: midpoint → edge end, rest of the loop, then edge start → midpoint.
    b = dict(s)
    b.update(x0=mx, y0=my, e=e * (1 - t), raw=_synth(s["x1"], s["y1"], e * (1 - t), f), comments=list(comments))
    a = dict(s)
    a.update(x1=mx, y1=my, e=e * t, raw=_synth(mx, my, e * t, f), comments=list(comments))
    return [b] + segments[best_i + 1 :] + segments[:best_i] + [a]


def _emit_approach(out: list[str], x: float, y: float, z: float, dx: float, dy: float, dz: float, retract: bool) -> None:
    out.append(";C seam travel: lift + direct\n")
    if retract:
        out.append(f"G1 E-{RETRACT:.1f} F{RETRACT_F:.0f}\n")
    out.append(f"G1 Y{_fmt(y + LIFT, 3)} F{TRAVEL_F:.0f} ;lift\n")
    travel = f"G1 X{_fmt(dx, 3)} Y{_fmt(dy + LIFT, 3)}"
    if z is None or abs((dz or 0) - (z or 0)) > 1e-4:
        travel += f" Z{_fmt(dz, 3)}"
    travel += f" F{TRAVEL_F:.0f} ;seam travel\n"
    out.append(travel)
    out.append(f"G1 Y{_fmt(dy, 3)} F{TRAVEL_F:.0f} ;lower\n")
    if retract:
        out.append(f"G1 E{PRIME:.2f} F{RETRACT_F:.0f}\n")
    out.append(f"G1 F{TRAVEL_F:.0f}\n")


def seam(text: str, *, retract_min_mm: float | None = None, skip_infill_travel: bool = False) -> tuple[str, dict]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    x = y = z = None
    f = None
    typ = ""
    i = 0
    n = len(lines)
    counts = {"seam_travel": 0, "retract": 0, "prime": 0, "wipe": 0, "seam_start": 0}

    def extrusion(line: str) -> bool:
        if not _is_g01(line):
            return False
        w = _words(line)
        return "E" in w and float(w["E"]) > 0 and ("X" in w or "Y" in w)

    def travelish(line: str) -> bool:
        if not _is_g01(line):
            return False
        w = _words(line)
        if "E" in w and float(w["E"]) > 0:
            return False
        return "X" in w or "Y" in w or "Z" in w

    while i < n:
        line = lines[i]
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            out.append(line)
            i += 1
            continue
        if travelish(line):
            j = i
            gap: list[str] = []
            dest_type = typ
            while j < n:
                lj = lines[j]
                if lj.startswith(";TYPE:"):
                    dest_type = lj[6:].strip()
                    gap.append(lj)
                    j += 1
                    continue
                if lj.startswith(";LAYER_CHANGE") and j != i:
                    break
                if extrusion(lj):
                    break
                if travelish(lj) or lj.startswith(";") or _body(lj).startswith("M") or _body(lj).startswith("G92") or _body(lj) == "":
                    gap.append(lj)
                    j += 1
                    continue
                if _is_g01(lj) and not travelish(lj) and not extrusion(lj):
                    # Feed-only or E-only. Keep it inside the gap only when no XY follows immediately.
                    gap.append(lj)
                    j += 1
                    continue
                break
            if j < n and extrusion(lines[j]) and x is not None:
                src_type = typ
                dx, dy, dz = x, y, z
                feeds: list[str] = []
                for tl in gap:
                    if travelish(tl):
                        w = _words(tl)
                        if "X" in w:
                            dx = float(w["X"])
                        if "Y" in w:
                            dy = float(w["Y"])
                        if "Z" in w:
                            dz = float(w["Z"])
                        if "F" in w:
                            f = float(w["F"])
                    elif _is_g01(tl) and not extrusion(tl):
                        feeds.append(tl if tl.endswith("\n") else tl + "\n")
                # Parse the extrusion run before emitting the approach, so a rotated
                # wall can be travelled to at its belt-side start.
                segs: list[dict] = []
                comments: list[str] = []
                k = j
                cx, cy, cz = dx, dy, dz
                while k < n and not travelish(lines[k]) and not lines[k].startswith(";LAYER_CHANGE"):
                    lj = lines[k]
                    if lj.startswith(";TYPE:"):
                        if segs:
                            break
                        dest_type = lj[6:].strip()
                        comments.append(lj if lj.endswith("\n") else lj + "\n")
                        k += 1
                        continue
                    if extrusion(lj):
                        w = _words(lj)
                        nx = float(w["X"]) if "X" in w else cx
                        ny = float(w["Y"]) if "Y" in w else cy
                        if "F" in w:
                            f = float(w["F"])
                        segs.append(
                            {
                                "x0": cx,
                                "y0": cy,
                                "x1": nx,
                                "y1": ny,
                                "e": float(w["E"]),
                                "f": w.get("F"),
                                "raw": lj if lj.endswith("\n") else lj + "\n",
                                "comments": comments,
                            }
                        )
                        comments = []
                        cx, cy = nx, ny
                        if "Z" in w:
                            cz = float(w["Z"])
                        k += 1
                        continue
                    if lj.startswith(";") or _body(lj).startswith("M") or _body(lj) == "":
                        comments.append(lj if lj.endswith("\n") else lj + "\n")
                        k += 1
                        continue
                    break
                # G2b leaves infill-to-infill travels as plain moves. Seaming
                # them is what produced 1752 travels / 1134 retracts against
                # the reference file's 1368 / 682.
                if skip_infill_travel and src_type in INFILL and dest_type in INFILL:
                    for ln in lines[i:k]:
                        out.append(ln if ln.endswith("\n") else ln + "\n")
                        if ln.startswith(";TYPE:"):
                            typ = ln[6:].strip()
                        if _is_g01(ln):
                            w = _words(ln)
                            if "X" in w:
                                x = float(w["X"])
                            if "Y" in w:
                                y = float(w["Y"])
                            if "Z" in w:
                                z = float(w["Z"])
                            if "F" in w:
                                f = float(w["F"])
                    i = k
                    continue
                wall = dest_type in WALLS
                if wall and segs:
                    segs = _belt_split(segs)
                    dx, dy = segs[0]["x0"], segs[0]["y0"]
                travel_dist = math.hypot(dx - x, dy - y)
                # Retract only on wall approaches of at least retraction_minimum_travel (2 mm).
                do_retract = wall and (retract_min_mm is None or travel_dist + 1e-6 >= retract_min_mm)
                for tl in gap:
                    if not travelish(tl) and not (_is_g01(tl) and not extrusion(tl)):
                        out.append(tl)
                        if tl.startswith(";TYPE:"):
                            typ = tl[6:].strip()
                _emit_approach(out, x, y, z if z is not None else 0.0, dx, dy, dz if dz is not None else 0.0, do_retract)
                counts["seam_travel"] += 1
                if do_retract:
                    counts["retract"] += 1
                    counts["prime"] += 1
                out.extend(feeds)
                x, y, z = dx, dy, dz
                if wall and segs:
                    out.append(";C_SEAM_START\n")
                    counts["seam_start"] += 1
                for s in segs:
                    out.extend(s["comments"])
                    out.append(s["raw"] if s["raw"].endswith("\n") else s["raw"] + "\n")
                    x, y = s["x1"], s["y1"]
                if wall and segs:
                    if dest_type in ("Outer wall", "Overhang wall"):
                        last = segs[-1]
                        span = math.hypot(last["x1"] - last["x0"], last["y1"] - last["y0"])
                        if span > 1e-6:
                            ux = (last["x1"] - last["x0"]) / span
                            uy = (last["y1"] - last["y0"]) / span
                            feed = float(last["f"]) if last["f"] else (f or 1560.0)
                            wx = last["x1"] + ux * WIPE_MM
                            wy = last["y1"] + uy * WIPE_MM
                            out.append(";WIPE_START\n")
                            out.append(f"G1 X{_fmt(wx, 3)} Y{_fmt(wy, 3)} F{feed * WIPE_FRAC:.0f} ;glide\n")
                            out.append(";WIPE_END\n")
                            counts["wipe"] += 1
                            x, y = wx, wy
                    out.append(";C_SEAM_END\n")
                else:
                    out.extend(comments)
                z = cz
                i = k
                continue
        # Default: copy the line and track position.
        if _is_g01(line):
            w = _words(line)
            if "X" in w:
                x = float(w["X"])
            if "Y" in w:
                y = float(w["Y"])
            if "Z" in w:
                z = float(w["Z"])
            if "F" in w:
                f = float(w["F"])
        out.append(line)
        i += 1
    return "".join(out), counts


def mintime(text: str, target_s: float = 8.0, floor_mm_s: float = 10.0) -> tuple[str, dict]:
    """Scale F on XY moves so a layer takes at least target_s, never below floor_mm_s. E unchanged."""
    lines = text.splitlines(keepends=True)
    # Split into preamble + layers + whatever trails the last layer (kept with the last layer).
    breaks = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    if not breaks:
        return text, {"under_target": 0, "still_under": 0, "stretched": 0}
    chunks = []
    if breaks[0] > 0:
        chunks.append(("pre", lines[: breaks[0]]))
    for a, b in zip(breaks, breaks[1:] + [len(lines)]):
        chunks.append(("layer", lines[a:b]))

    floor_f = floor_mm_s * 60.0
    stretched = 0
    still_under = 0
    under_before = 0
    out_lines: list[str] = []

    def layer_time(chunk: list[str], x0, y0, f0: float) -> tuple[float, object, object, float]:
        x, y, f = x0, y0, f0
        total = 0.0
        for line in chunk:
            if not _is_g01(line):
                continue
            w = _words(line)
            if "F" in w:
                f = float(w["F"])
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            if x is not None and nx is not None and ("X" in w or "Y" in w) and f > 0:
                total += math.hypot(nx - x, ny - y) / (f / 60.0)
            x, y = nx, ny
        return total, x, y, f

    def _scale_line(ln: str, scale: float) -> str:
        if not _is_g01(ln):
            return ln
        w = _words(ln)
        # Retract and prime are E-only. Leave their feed alone so a short layer
        # does not turn the seam retract into a crawl. E is never rewritten.
        if "E" in w and "X" not in w and "Y" not in w:
            return ln

        def _scale_f(m: re.Match) -> str:
            return "F" + f"{max(float(m.group(1)) * scale, floor_f):.0f}"

        return re.sub(r"F([+-]?(?:\d+\.?\d*|\.\d+))", _scale_f, ln)

    cx = cy = None
    cf = 1200.0
    for kind, chunk in chunks:
        if kind != "layer":
            _, cx, cy, cf = layer_time(chunk, cx, cy, cf)
            out_lines.extend(chunk)
            continue
        t, _, _, _ = layer_time(chunk, cx, cy, cf)
        if t <= 0 or t >= target_s - 1e-6:
            _, cx, cy, cf = layer_time(chunk, cx, cy, cf)
            out_lines.extend(chunk)
            continue
        under_before += 1
        scale = t / target_s
        scaled = [_scale_line(ln, scale) for ln in chunk]
        t2, cx, cy, cf = layer_time(scaled, cx, cy, cf)
        stretched += 1
        if t2 < target_s - 1e-3:
            still_under += 1
        out_lines.extend(scaled)
    return "".join(out_lines), {"under_target_before": under_before, "stretched": stretched, "still_under": still_under}


def splice(toe: str, heel: str, index: int) -> str:
    """Layers [0, index) from the toe slice, index..end from the bead slice.

    Both files must share layer count and Z. min_bead_width is a print-object
    key on this binary, so the heel bead floor is a second global slice spliced
    in rather than a layer-range option.
    """
    toe_lines = toe.splitlines(keepends=True)
    heel_lines = heel.splitlines(keepends=True)
    tb = [i for i, l in enumerate(toe_lines) if l.startswith(";LAYER_CHANGE")]
    hb = [i for i, l in enumerate(heel_lines) if l.startswith(";LAYER_CHANGE")]
    if index >= len(tb) or index >= len(hb):
        raise SystemExit(f"splice index {index} but toe has {len(tb)} layers and heel has {len(hb)}")
    if len(tb) != len(hb):
        raise SystemExit(f"splice layer count mismatch toe {len(tb)} heel {len(hb)}")
    return "".join(toe_lines[: tb[index]] + heel_lines[hb[index] :])


def coupon(text: str, start_index: int) -> str:
    """Keep the preamble and layers whose 0-based ;LAYER_CHANGE ordinal is >= start_index."""
    lines = text.splitlines(keepends=True)
    breaks = [i for i, l in enumerate(lines) if l.startswith(";LAYER_CHANGE")]
    if start_index >= len(breaks):
        raise SystemExit(f"coupon start {start_index} but file has {len(breaks)} layers")
    keep = lines[: breaks[0]] + lines[breaks[start_index] :]
    return "".join(keep)


def feature_stats(text: str, end_index: int | None = None) -> dict:
    """Path length and E/mm by feature for 0-based layers [0, end_index). None = whole file."""
    lines = text.splitlines()
    layer = -1
    typ = ""
    x = y = None
    acc: dict[str, list[float]] = {}
    filament = 0.0
    time_s = 0.0
    f = 1200.0
    dirs = {0: [0.0, 0.0], 30: [0.0, 0.0], 60: [0.0, 0.0], 90: [0.0, 0.0]}
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
            if end_index is not None and layer >= end_index:
                break
            continue
        if end_index is not None and layer >= end_index:
            break
        if layer < 0:
            continue
        if line.startswith(";TYPE:"):
            typ = line[6:].strip()
            continue
        if line.startswith(";WIPE") or "glide" in line:
            continue
        if not _is_g01(line):
            continue
        w = _words(line)
        if "F" in w:
            f = float(w["F"])
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        if x is not None and nx is not None and ("X" in w or "Y" in w):
            dist = math.hypot((nx or 0) - x, (ny or 0) - y)
            if f > 0:
                time_s += dist / (f / 60.0)
            e = float(w["E"]) if "E" in w else 0.0
            if e > 0 and dist > 0 and typ:
                key = typ
                slot = acc.setdefault(key, [0.0, 0.0])
                slot[0] += dist
                slot[1] += e
                filament += e
                if typ in ("Outer wall", "Overhang wall"):
                    ang = abs(math.degrees(math.atan2(ny - y, nx - x)))
                    bucket = min(dirs, key=lambda b: abs(b - ang))
                    dirs[bucket][0] += dist
                    dirs[bucket][1] += e
        x, y = nx, ny
    feats = {}
    for k, (length, e) in sorted(acc.items()):
        feats[k] = {"length_mm": length, "e_mm": e, "e_per_mm": (e / length) if length else 0.0}
    outer_dir = {b: (dirs[b][1] / dirs[b][0] if dirs[b][0] else 0.0) for b in dirs}
    return {"features": feats, "filament_mm": filament, "time_s": time_s, "outer_e_per_mm_by_dir": outer_dir, "layers": layer}


def layer_times(text: str) -> list[float]:
    lines = text.splitlines()
    times: list[float] = []
    x = y = None
    f = 1200.0
    cur = 0.0
    started = False
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            if started:
                times.append(cur)
            started = True
            cur = 0.0
            continue
        if not started or not _is_g01(line):
            continue
        w = _words(line)
        if "F" in w:
            f = float(w["F"])
        nx = float(w["X"]) if "X" in w else x
        ny = float(w["Y"]) if "Y" in w else y
        if x is not None and nx is not None and ("X" in w or "Y" in w) and f > 0:
            cur += math.hypot(nx - x, ny - y) / (f / 60.0)
        x, y = nx, ny
    if started:
        times.append(cur)
    return times


def main() -> None:
    cmd = sys.argv[1]
    src = Path(sys.argv[2])
    text = src.read_text(errors="replace")
    if cmd == "efix":
        out, n = efix(text)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
    elif cmd == "splice":
        heel = Path(sys.argv[3]).read_text(errors="replace")
        index = int(sys.argv[4])
        Path(sys.argv[5]).write_text(splice(text, heel, index))
        print("spliced at", index)
    elif cmd == "coupon":
        Path(sys.argv[3]).write_text(coupon(text, int(sys.argv[4])))
        print("coupon from", sys.argv[4])
    elif cmd == "match":
        # G2b toe seam, without the heelmod min-time pass.
        out, n = efix(text)
        out, counts = seam(out, retract_min_mm=2.0, skip_infill_travel=True)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
        print("seam", counts)
    elif cmd == "all":
        out, n = efix(text)
        out, counts = seam(out)
        out, mt = mintime(out)
        Path(sys.argv[3]).write_text(out)
        print("efix", n)
        print("seam", counts)
        print("mintime", mt)
        times = layer_times(out)
        under = sum(1 for t in times if t < 8.0 - 1e-3)
        print("layers", len(times), "under_8s", under)
    else:
        raise SystemExit(f"unknown {cmd}")


if __name__ == "__main__":
    main()
