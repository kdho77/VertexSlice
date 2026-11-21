"""sfg_common.py - FROZEN shared parser for the seam-fuse gates (PM, 2026-11-20). Do not edit; checked by SHA256SUMS.

Frame: physical belt G-code as written by VertexSlice (G-code Y = 1.41421356 x virtual Y; Y = 0 is the belt). Every
layer is a 45-degree section of the insole; the BOTTOM (plantar / belt-contact) edge of a closed perimeter loop is the
part of that loop at its own minimum Y (within BOT_TOL). Handles M82/M83, G90/G91 for XYZ is assumed absolute (G91 only
appears in the end block; moves after it are ignored).
"""
import math, re

WORD = re.compile(r"([GMXYZEFS])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
PERIM_TYPES = {"Outer wall", "Overhang wall", "WALL-OUTER"}
BOT_TOL = 0.25          # mm: bottom-edge band, y <= loop min Y + BOT_TOL (same band as postprocess_g2b._belt_split)
CLOSE_TOL = 0.02        # mm: a chain is CLOSED when, after >= 3 segments and >= 50 % of its length, it passes within this of its start
TYPE_ALIAS = {"WALL-OUTER": "Outer wall", "WALL-INNER": "Inner wall", "FILL": "Sparse infill", "SKIN": "Solid infill"}


def seg_point_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return math.hypot(px - ax, py - ay), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def parse(fn):
    """Return list of events. Each event is a dict:
       kind: 'ext' (XY move with E>0), 'travel' (XY and/or Z move, no E>0), 'retract' (E<0, no XY), 'prime' (E>0, no XY),
             'cmd' (M-code), 'layer' (;LAYER_CHANGE or Cura ;LAYER:), 'type' (feature label), 'comment'
       plus x0,y0,x1,y1,z0,z1,e,f,line,layer,type,raw."""
    ev = []
    x = y = z = 0.0
    e_abs = 0.0
    rel = True
    f = 0.0
    layer = -1
    typ = None
    in_end = False
    with open(fn, errors="replace") as fh:
        for ln, raw in enumerate(fh, 1):
            s = raw.rstrip("\n")
            st = s.strip()
            if not st:
                continue
            if st.startswith(";"):
                if st.startswith(";LAYER_CHANGE") or re.match(r"^;LAYER:-?\d+", st):
                    layer += 1
                    ev.append({"kind": "layer", "layer": layer, "line": ln})
                elif st.startswith(";TYPE:"):
                    typ = TYPE_ALIAS.get(st[6:].strip(), st[6:].strip())
                    ev.append({"kind": "type", "type": typ, "layer": layer, "line": ln})
                else:
                    ev.append({"kind": "comment", "raw": st, "layer": layer, "line": ln, "type": typ})
                continue
            body = s.split(";", 1)[0].strip()
            if not body:
                continue
            w = dict(WORD.findall(body))
            if "M" in w:
                m = int(float(w["M"]))
                if m == 82:
                    rel = False
                elif m == 83:
                    rel = True
                ev.append({"kind": "cmd", "raw": body, "m": m, "layer": layer, "line": ln, "type": typ})
                continue
            if "G" not in w:
                continue
            g = int(float(w["G"]))
            if g == 91:
                in_end = True
            if g == 92:
                if "E" in w:
                    e_abs = float(w["E"])
                continue
            if g not in (0, 1) or in_end:
                continue
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            nz = float(w["Z"]) if "Z" in w else z
            if "F" in w:
                f = float(w["F"])
            de = 0.0
            if "E" in w:
                ev_e = float(w["E"])
                if rel:
                    de = ev_e
                else:
                    de = ev_e - e_abs
                    e_abs = ev_e
            moved_xy = (abs(nx - x) > 1e-9 or abs(ny - y) > 1e-9)
            d = {"x0": x, "y0": y, "z0": z, "x1": nx, "y1": ny, "z1": nz, "e": de, "f": f, "line": ln,
                 "layer": layer, "type": typ, "raw": body, "zmove": abs(nz - z) > 1e-9}
            if moved_xy and de > 1e-9:
                d["kind"] = "ext"
            elif moved_xy or d["zmove"]:
                d["kind"] = "travel"
            elif de < -1e-9:
                d["kind"] = "retract"
            elif de > 1e-9:
                d["kind"] = "prime"
            else:
                d["kind"] = "noop"
            ev.append(d)
            x, y, z = nx, ny, nz
    return ev


def seglen(d):
    return math.hypot(d["x1"] - d["x0"], d["y1"] - d["y0"])


def perimeter_chains(ev):
    """Contiguous runs of extruding moves whose type is a perimeter type (Outer/Overhang wall; Cura WALL-OUTER).
    A chain ends at any non-extruding XY/Z move, retract or a change to a non-perimeter type.
    Returns dicts: layer, segs (list of event indices), pts, length, closed, start, end, i_first, i_last."""
    chains = []
    cur = None
    for i, d in enumerate(ev):
        k = d["kind"]
        if k == "ext" and d["type"] in PERIM_TYPES or k == "ext" and d["type"] == "Outer wall":
            if cur is None:
                cur = {"layer": d["layer"], "segs": []}
            cur["segs"].append(i)
            continue
        if k in ("travel", "retract", "layer") or (k == "ext") or (k == "type" and d["type"] not in PERIM_TYPES):
            if cur is not None:
                chains.append(cur)
                cur = None
    if cur is not None:
        chains.append(cur)
    out = []
    for c in chains:
        segs = [ev[i] for i in c["segs"]]
        pts = [(segs[0]["x0"], segs[0]["y0"])] + [(s["x1"], s["y1"]) for s in segs]
        L = sum(seglen(s) for s in segs)
        sx, sy = pts[0]
        closed = False
        ret_seg = None  # index into segs of the segment where the path returns to the start
        if len(segs) >= 3 and L > 0:
            acc = 0.0
            for j, s in enumerate(segs):
                if acc >= 0.5 * L:
                    dist, _ = seg_point_dist(sx, sy, s["x0"], s["y0"], s["x1"], s["y1"])
                    if dist <= CLOSE_TOL:
                        closed = True
                        ret_seg = j
                        break
                acc += seglen(s)
            if not closed:
                s = segs[-1]
                if math.hypot(s["x1"] - sx, s["y1"] - sy) <= CLOSE_TOL:
                    closed, ret_seg = True, len(segs) - 1
        miny = min(p[1] for p in pts)
        out.append({"layer": c["layer"], "seg_idx": c["segs"], "segs": segs, "pts": pts, "length": L, "closed": closed,
                    "ret_seg": ret_seg, "start": pts[0], "end": pts[-1], "miny": miny,
                    "types": sorted({s["type"] for s in segs}), "z": segs[0]["z1"]})
    return out


def bottom_edge(chain, tol=BOT_TOL):
    """X extent (xa, xb) of the loop's bottom edge: the contiguous stretch of the loop path that stays within tol of the
    loop's min Y (segments with both ends in the band, plus single in-band vertices), the stretch holding the loop's
    lowest vertex. Returns None only for an empty chain."""
    miny = chain["miny"]
    ivs = []
    for s in chain["segs"]:
        in0, in1 = s["y0"] <= miny + tol, s["y1"] <= miny + tol
        if in0 and in1:
            a, b = sorted((s["x0"], s["x1"]))
            ivs.append([a, b])
        else:
            if in0:
                ivs.append([s["x0"], s["x0"]])
            if in1:
                ivs.append([s["x1"], s["x1"]])
    if not ivs:
        return None
    ivs.sort()
    merged = [ivs[0]]
    for a, b in ivs[1:]:
        if a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    lo = min(chain["pts"], key=lambda p: p[1])
    for a, b in merged:
        if a - 1e-6 <= lo[0] <= b + 1e-6:
            return (a, b)
    return tuple(max(merged, key=lambda iv: iv[1] - iv[0]))


def bottom_stretch(chain, tol=BOT_TOL):
    """Pieces of the loop path forming its bottom edge: in-band segments and in-band vertices (see bottom_edge) whose X
    lies in the bottom_edge() extent. Each piece is (x0, y0, x1, y1)."""
    be = bottom_edge(chain, tol)
    if be is None:
        return []
    miny = chain["miny"]
    out = []
    for s in chain["segs"]:
        for (xa, ya, xb, yb) in ((s["x0"], s["y0"], s["x1"], s["y1"]),):
            in0, in1 = ya <= miny + tol, yb <= miny + tol
            if in0 and in1 and be[0] - 1e-6 <= min(xa, xb) and max(xa, xb) <= be[1] + 1e-6:
                out.append((xa, ya, xb, yb))
            else:
                if in0 and be[0] - 1e-6 <= xa <= be[1] + 1e-6:
                    out.append((xa, ya, xa, ya))
                if in1 and be[0] - 1e-6 <= xb <= be[1] + 1e-6:
                    out.append((xb, yb, xb, yb))
    return out


def dist_to_bottom(chain, px, py):
    """Distance (mm) from (px, py) to the loop's bottom edge (bottom_stretch). inf if none."""
    best = float("inf")
    for (xa, ya, xb, yb) in bottom_stretch(chain):
        d, _ = seg_point_dist(px, py, xa, ya, xb, yb)
        best = min(best, d)
    return best


def on_bottom(chain, px, py, tol):
    """True if (px, py) lies within tol of the loop's bottom edge."""
    return dist_to_bottom(chain, px, py) <= tol


def footer(fn):
    t = open(fn, errors="replace").read()
    m = re.search(r"^; estimated printing time \(normal mode\) = (.+)$", t, re.M)
    g = re.search(r"^; filament used \[g\] = ([\d.]+)", t, re.M)
    mm = re.search(r"^; filament used \[mm\] = ([\d.]+)", t, re.M)
    secs = None
    if m:
        secs = 0
        for v, u in re.findall(r"(\d+)([dhms])", m.group(1)):
            secs += int(v) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u]
    return {"time_s": secs, "time": m.group(1).strip() if m else None,
            "filament_g": float(g.group(1)) if g else None, "filament_mm": float(mm.group(1)) if mm else None}


def toolpath_seconds(fn):
    """Print-time estimate computed from the FINAL G-code (after every post-process step), identical for every file:
    extruding moves at their feedrate (cruise), non-extruding XY moves > 0.05 mm accel-limited (trapezoid / triangle at
    the current M204). Same model as main's slice_apexcore_g2b._estimate_seconds (frozen copy). Slicer footers are not
    comparable across LIVE / PRE-29 (#29 rewrites the footer; PRE-29 keeps the slicer's pre-post-process figure)."""
    x = y = None
    feed = 3000.0
    accel = 8000.0
    total = 0.0
    with open(fn, errors="replace") as fh:
        for line in fh:
            if line.startswith("M204"):
                m = re.search(r"S([0-9.]+)", line)
                if m:
                    accel = float(m.group(1))
                continue
            if line.startswith(";") or not line.startswith(("G0", "G1")):
                continue
            w = dict(WORD.findall(line.split(";", 1)[0]))
            if "F" in w:
                feed = float(w["F"])
            nx = float(w["X"]) if "X" in w else x
            ny = float(w["Y"]) if "Y" in w else y
            de = float(w["E"]) if "E" in w else 0.0
            dist = math.hypot((nx or 0) - (x or 0), (ny or 0) - (y or 0)) if x is not None and nx is not None else 0.0
            speed = feed / 60.0
            if de > 0 and dist > 0 and speed > 0:
                total += dist / speed
            elif dist > 0.05 and speed > 0 and accel > 0:
                ad = speed * speed / (2 * accel)
                total += (2 * (speed / accel) + (dist - 2 * ad) / speed) if dist >= 2 * ad else 2 * math.sqrt(dist / accel)
            if nx is not None:
                x, y = nx, ny
    return total


def net_e_mm(fn):
    """Net filament fed (mm) over the whole file: every extruding, priming and retracting move summed (relative or
    absolute E handled by parse())."""
    return sum(d["e"] for d in parse(fn) if d["kind"] in ("ext", "prime", "retract"))
