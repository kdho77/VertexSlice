#!/usr/bin/env python3
"""Build a scripted-correct ApexCore file from BASE G-code.

Rotates each closed outer loop so its start is the X midpoint of that loop's
bottom face (G1 bottom_edge), by a collinear split. Faces narrower than 3 mm
are left on today's seam. Closed inner loops are rotated to that outer seam's
X with the same belt-side hit the post-processor uses (_hit_anchor / _rotate_at).
Travels, the 0.4 mm lift, the dry wipe (0.7 mm, same feed) and retract/prime
lines stay in their existing sequence; only the seam point moves.

This is the P2 reference. It is not the worker's fix and it does not edit
scripts/g2b_heelmod.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "validation", "seam_center_gates"))
sys.path.insert(0, os.path.join(_ROOT, "validation", "seam_fuse_gates"))

import scg_common as S  # noqa: E402
import sfg_common as C  # noqa: E402

LIFT = 0.4
WIPE_MM = 0.7


def _fmt(n: float) -> str:
    return f"{n:.3f}"


def _has_f(raw: str) -> bool:
    body = raw.split(";", 1)[0]
    return "F" in body and any(p.startswith("F") and p[1:2].isdigit() or (len(p) > 1 and p[1] in "+-.") for p in body.split())


def _body(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _words(line: str) -> dict:
    return dict(C.WORD.findall(_body(line)))


def _ensure_f(raw: str, feed: float, modal: float | None) -> tuple[str, float]:
    if _has_f(raw):
        tok = _words(raw).get("F")
        return raw if raw.endswith("\n") else raw + "\n", float(tok) if tok else feed
    if modal is None or abs(feed - modal) > 0.5:
        nl = "\n" if raw.endswith("\n") else ""
        core = raw[:-1] if nl else raw
        # Keep an end-of-line comment attached.
        if ";" in core:
            code, com = core.split(";", 1)
            core = code.rstrip() + f" F{feed:.0f} ;" + com
        else:
            core = core.rstrip() + f" F{feed:.0f}"
        return core + (nl or "\n"), feed
    return raw if raw.endswith("\n") else raw + "\n", modal


def _synth(x1: float, y1: float, e: float, feed: float) -> str:
    return f"G1 X{_fmt(x1)} Y{_fmt(y1)} E{e:.5f} F{feed:.0f}\n"


def _groups(lines: list[str], chain: dict) -> list[dict]:
    segs = chain["segs"]
    groups = []
    for idx, seg in enumerate(segs):
        line_no = seg["line"]
        if idx == 0:
            pre: list[str] = []
        else:
            pre = lines[segs[idx - 1]["line"]: line_no - 1]
        groups.append({"pre": pre, "raw": lines[line_no - 1], "seg": seg})
    return groups


def _point_at_x(chain: dict, x_want: float):
    """Lowest bottom-band point on the loop with this X. None if the face misses it."""
    miny = chain["miny"]
    best = None
    for i, s in enumerate(chain["segs"]):
        x0, y0, x1, y1 = s["x0"], s["y0"], s["x1"], s["y1"]
        dx = x1 - x0
        if abs(dx) < 1e-9:
            if abs(x0 - x_want) > 1e-3:
                continue
            for t, y in ((0.0, y0), (1.0, y1)):
                if y <= miny + C.BOT_TOL + 1e-3:
                    key = (y, abs(x0 - x_want))
                    if best is None or key < best[0]:
                        best = (key, i, t, x0, y)
            continue
        t = (x_want - x0) / dx
        if t < -1e-4 or t > 1.0 + 1e-4:
            continue
        t = min(1.0, max(0.0, t))
        x = x0 + dx * t
        y = y0 + (y1 - y0) * t
        if y > miny + C.BOT_TOL + 1e-3:
            continue
        key = (y, abs(x - x_want))
        if best is None or key < best[0]:
            best = (key, i, t, x, y)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _snap(chain: dict, i: int, t: float, x_want: float) -> tuple[int, float]:
    segs = chain["segs"]
    length = C.seglen(segs[i])
    if t <= 1e-4:
        return i, 0.0
    if t >= 1.0 - 1e-4:
        return (i + 1) % len(segs), 0.0
    if min(t, 1.0 - t) * length >= 0.02:
        return i, t
    # A sliver shorter than 0.02 mm snaps to the nearer vertex when that vertex
    # is still within 0.10 mm in X of the target. Otherwise keep the split.
    end_i = i if t < 0.5 else (i + 1) % len(segs)
    vx = segs[end_i]["x0"]
    if abs(vx - x_want) <= S.CENTER_TOL:
        return end_i, 0.0
    return i, t


def _split(group: dict, t: float) -> tuple[dict, dict]:
    s = group["seg"]
    feed = float(s["f"])
    mx = s["x0"] + (s["x1"] - s["x0"]) * t
    my = s["y0"] + (s["y1"] - s["y0"]) * t
    e = float(s["e"])
    bseg = dict(s)
    bseg.update(x0=mx, y0=my, e=e * (1.0 - t))
    aseg = dict(s)
    aseg.update(x1=mx, y1=my, e=e * t)
    b = {"pre": list(group["pre"]), "raw": _synth(s["x1"], s["y1"], e * (1.0 - t), feed), "seg": bseg}
    keep = [ln for ln in group["pre"] if ln.startswith((";TYPE:", ";WIDTH:", ";HEIGHT:"))]
    a = {"pre": keep, "raw": _synth(mx, my, e * t, feed), "seg": aseg}
    return b, a


def _rotate(groups: list[dict], i: int, t: float) -> list[dict]:
    n = len(groups)
    if t <= 1e-4:
        return groups[i:] + groups[:i]
    if t >= 1.0 - 1e-4:
        j = (i + 1) % n
        return groups[j:] + groups[:j]
    b, a = _split(groups[i], t)
    return [b] + groups[i + 1:] + groups[:i] + [a]


def _emit(groups: list[dict]) -> list[str]:
    out = []
    modal = None
    for g in groups:
        raw, modal = _ensure_f(g["raw"], float(g["seg"]["f"]), modal)
        out.extend(g["pre"])
        out.append(raw)
    return out


def _hit_anchor(chain: dict, anchor_x: float):
    ymin = chain["miny"]
    best = None
    for i, s in enumerate(chain["segs"]):
        dx = s["x1"] - s["x0"]
        if abs(dx) < 1e-9:
            continue
        t = (anchor_x - s["x0"]) / dx
        if t < -1e-4 or t > 1.0 + 1e-4:
            continue
        t = min(1.0, max(0.0, t))
        y = s["y0"] + (s["y1"] - s["y0"]) * t
        if y > 2.5 or y > ymin + 0.75:
            continue
        if best is None or y < best[0] - 1e-6:
            best = (y, i, t, s["x0"] + dx * t, y)
    return None if best is None else best[1:]


def _rewrite_approach(lines: list[str], first_line: int, old: tuple[float, float], new: tuple[float, float]) -> list[tuple[int, int, list[str]]]:
    """Retarget the seam travel / lower just before this loop. Returns replacements."""
    sx, sy = new
    ox, oy = old
    lo = max(0, first_line - 25)
    window = lines[lo:first_line - 1]
    kind = "lift"
    for ln in window:
        if "seam travel: inside" in ln:
            kind = "inside"
        elif "lift + direct" in ln:
            kind = "lift"
    reps = []
    tagged = False
    for idx in range(first_line - 2, lo - 1, -1):
        raw = lines[idx]
        body = _body(raw)
        if body.startswith("G1") and ";lower" in raw:
            reps.append((idx, idx + 1, [f"G1 Y{_fmt(sy)} F12720 ;lower\n"]))
            continue
        if ";seam travel" in raw or ";seam inside" in raw:
            w = _words(raw)
            if "X" not in w:
                continue
            x = float(w["X"])
            y = float(w["Y"]) if "Y" in w else oy
            near_old = abs(x - ox) <= 0.05 and (abs(y - oy) <= 0.05 or abs(y - (oy + LIFT)) <= 0.05)
            if not near_old and ";seam inside" in raw:
                continue
            if kind == "lift" and ";seam travel" in raw:
                dest_y = sy + LIFT
            else:
                dest_y = sy
            parts = ["G1", f"X{_fmt(sx)}", f"Y{_fmt(dest_y)}"]
            if "Z" in w:
                parts.append("Z" + w["Z"])
            feed = w.get("F", "12720")
            parts.append(f"F{float(feed):.0f}")
            tag = " ;seam travel" if ";seam travel" in raw else " ;seam inside"
            reps.append((idx, idx + 1, [" ".join(parts) + tag + "\n"]))
            tagged = True
            if ";seam travel" in raw:
                break
    if tagged:
        return reps
    # The first toe loop is positioned by a plain travel, with no seam tag.
    for idx in range(first_line - 2, lo - 1, -1):
        raw = lines[idx]
        body = _body(raw)
        if not body.startswith(("G0", "G1")):
            if raw.startswith(";LAYER_CHANGE"):
                break
            continue
        w = _words(raw)
        if "E" in w and float(w["E"]) > 0 and ("X" in w or "Y" in w):
            break
        if "X" not in w and "Y" not in w:
            continue
        parts = ["G1" if body.startswith("G1") else "G0", f"X{_fmt(sx)}", f"Y{_fmt(sy)}"]
        if "Z" in w:
            parts.append("Z" + w["Z"])
        if "F" in w:
            parts.append(f"F{float(w['F']):.0f}")
        com = ""
        if ";" in raw:
            com = " ;" + raw.split(";", 1)[1].strip()
        reps.append((idx, idx + 1, [" ".join(parts) + com + "\n"]))
        break
    return reps


def _rewrite_wipe(lines: list[str], last_line: int, end: tuple[float, float], direction: tuple[float, float]) -> list[tuple[int, int, list[str]]]:
    ux, uy = direction
    span = math.hypot(ux, uy)
    if span < 1e-9:
        return []
    ux, uy = ux / span, uy / span
    wx = end[0] + ux * WIPE_MM
    wy = end[1] + uy * WIPE_MM
    hi = min(len(lines), last_line + 8)
    for idx in range(last_line, hi):
        if lines[idx].startswith(";WIPE_START"):
            for j in range(idx + 1, hi):
                if lines[j].startswith(";WIPE_END"):
                    return []
                if lines[j].startswith("G1") or lines[j].startswith("G0"):
                    feed = _words(lines[j]).get("F", "7632")
                    return [(j, j + 1, [f"G1 X{_fmt(wx)} Y{_fmt(wy)} F{float(feed):.0f} ;glide\n"])]
    return []


def _apply(lines: list[str], reps: list[tuple[int, int, list[str]]]) -> None:
    # Drop nested duplicates (same start).
    seen = set()
    uniq = []
    for start, end, new in reps:
        if start in seen:
            continue
        seen.add(start)
        uniq.append((start, end, new))
    for start, end, new in sorted(uniq, key=lambda r: -r[0]):
        lines[start:end] = new


def _target_outer(chain: dict, policy: str, only_layer: int | None) -> tuple[int, float, float] | None:
    face = S.face_of(chain)
    if face is None or face["narrow"]:
        return None
    if policy == "center":
        x_want = face["center_x"]
    elif policy == "fixed140":
        x_want = min(max(140.0, face["xa"]), face["xb"])
    elif policy == "shift":
        if only_layer is not None and chain["layer"] != only_layer:
            return None
        x_want = face["center_x"] + 0.5
        if x_want > face["xb"]:
            x_want = face["center_x"] - 0.5
    elif policy == "side":
        if only_layer is not None and chain["layer"] != only_layer:
            return None
        # Dorsal-most vertex: not on the bottom face.
        best_i, best_y = 0, -1e9
        for i, s in enumerate(chain["segs"]):
            if s["y0"] > best_y:
                best_y, best_i = s["y0"], i
        return best_i, 0.0, chain["segs"][best_i]["x0"]
    else:
        raise SystemExit("unknown policy " + policy)
    hit = _point_at_x(chain, x_want)
    if hit is None:
        return None
    i, t, _x, _y = hit
    i, t = _snap(chain, i, t, x_want)
    return i, t, x_want


def transform_lines(lines: list[str], policy: str = "center", only_layer: int | None = None) -> dict:
    text = "".join(lines)
    with tempfile.NamedTemporaryFile("w", suffix=".gcode", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        ev = C.parse(tmp)
    finally:
        os.unlink(tmp)
    outers = [c for c in S.outer_chains(ev) if c["closed"]]
    inners = [c for c in S.inner_chains(ev) if c["closed"]]
    # File order: outers set the anchor, inners consume it.
    events = [("outer", c) for c in outers] + [("inner", c) for c in inners]
    events.sort(key=lambda item: item[1]["segs"][0]["line"])
    reps: list[tuple[int, int, list[str]]] = []
    anchor = None
    moved_outer = moved_inner = skipped = missed = 0
    for kind, chain in events:
        if kind == "outer":
            target = _target_outer(chain, policy, only_layer if policy in ("shift", "side") else None)
            if policy in ("shift", "side") and target is None and (only_layer is None or chain["layer"] != only_layer):
                # Other layers stay centered so the bad file is P2 plus one defect.
                target = _target_outer(chain, "center", None)
            if target is None:
                anchor = chain["start"][0]
                skipped += 1
                continue
            i, t, x_want = target
            groups = _groups(lines, chain)
            rotated = _rotate(groups, i, t)
            new_start = (rotated[0]["seg"]["x0"], rotated[0]["seg"]["y0"])
            if math.hypot(new_start[0] - chain["start"][0], new_start[1] - chain["start"][1]) <= 0.02:
                anchor = chain["start"][0]
                skipped += 1
                continue
            span0 = chain["segs"][0]["line"] - 1
            span1 = chain["segs"][-1]["line"]
            reps.append((span0, span1, _emit(rotated)))
            reps.extend(_rewrite_approach(lines, chain["segs"][0]["line"], chain["start"], new_start))
            last = rotated[-1]["seg"]
            reps.extend(_rewrite_wipe(
                lines, chain["segs"][-1]["line"], (last["x1"], last["y1"]),
                (last["x1"] - last["x0"], last["y1"] - last["y0"]),
            ))
            anchor = new_start[0]
            moved_outer += 1
        else:
            if anchor is None:
                missed += 1
                continue
            hit = _hit_anchor(chain, anchor)
            if hit is None:
                missed += 1
                continue
            i, t, _x, _y = hit
            i, t = _snap(chain, i, t, anchor)
            groups = _groups(lines, chain)
            rotated = _rotate(groups, i, t)
            new_start = (rotated[0]["seg"]["x0"], rotated[0]["seg"]["y0"])
            if math.hypot(new_start[0] - chain["start"][0], new_start[1] - chain["start"][1]) <= 0.02:
                continue
            span0 = chain["segs"][0]["line"] - 1
            span1 = chain["segs"][-1]["line"]
            reps.append((span0, span1, _emit(rotated)))
            reps.extend(_rewrite_approach(lines, chain["segs"][0]["line"], chain["start"], new_start))
            moved_inner += 1
    _apply(lines, reps)
    # The post-processor reroutes a seam travel that now cuts a wall. Same function,
    # read-only: extrusion, feeds, and travels that do not cross stay as written.
    g2b = os.path.join(_ROOT, "scripts", "g2b_heelmod")
    if g2b not in sys.path:
        sys.path.insert(0, g2b)
    import postprocess_g2b as PP
    fixed = PP.clear_perimeter_crossings("".join(lines))
    lines[:] = fixed.splitlines(keepends=True)
    return {"moved_outer": moved_outer, "moved_inner": moved_inner, "skipped": skipped, "inner_miss": missed}


def transform_file(src: str, dst: str, policy: str = "center", only_layer: int | None = None) -> dict:
    lines = open(src, newline="").read().splitlines(keepends=True)
    if not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    stats = transform_lines(lines, policy, only_layer)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", newline="") as fh:
        fh.writelines(lines)
    return stats


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    policy = sys.argv[3] if len(sys.argv) > 3 else "center"
    only = int(sys.argv[4]) if len(sys.argv) > 4 else None
    print(transform_file(src, dst, policy, only))
