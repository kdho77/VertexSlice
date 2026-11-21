#!/usr/bin/env python3
"""Build the deliberate bad candidates for the pre-launch proof.

Each candidate is P2 (scripted-correct) plus one defect, except P3(a), which
puts every closed outer seam at the bottom-face point nearest X=140.

  p3a  clamp seam X to 140 on the bottom face     -> A1, A3
  p3b  layer 100 seam 0.5 mm off its face center  -> A1, A2
  p3c  layer 100 seam on the dorsal-most vertex   -> G1, A1
  p3d  one outer-wall vertex moved 0.1 mm         -> A6
  p3e  every >=10 mm fast outer move set to F1200 -> A6, A7
  p3f  one ApexLight byte changed (M106 S14.9->S14.8) -> A8

Not a gate. Does not edit scripts/, src/, or server/.
"""
from __future__ import annotations

import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "validation", "seam_center_gates"))
sys.path.insert(0, os.path.join(_ROOT, "validation", "seam_fuse_gates"))
sys.path.insert(0, _HERE)

import scg_common as S  # noqa: E402
import sfg_common as C  # noqa: E402
import scripted_correct as SC  # noqa: E402

NAMES = (
    "apex_core_orthocad.gcode",
    "apex_core_insole2.gcode",
    "apex_light_orthocad.gcode",
    "apex_light_insole2.gcode",
)
F_TOKEN = re.compile(r"F[-+]?\d*\.?\d+")


def _copy_quad(src: str, dst: str) -> None:
    os.makedirs(dst, exist_ok=True)
    for name in NAMES:
        shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))


def _orthocad_from(base_dir: str, dst_dir: str, policy: str, only_layer: int | None) -> dict:
    os.makedirs(dst_dir, exist_ok=True)
    stats = SC.transform_file(
        os.path.join(base_dir, "apex_core_orthocad.gcode"),
        os.path.join(dst_dir, "apex_core_orthocad.gcode"),
        policy,
        only_layer,
    )
    for name in NAMES[1:]:
        src = os.path.join(base_dir, name)
        shutil.copyfile(src, os.path.join(dst_dir, name))
    return stats


def _set_word(line: str, letter: str, value: float, prec: int) -> str:
    token = "%s%.*f" % (letter, prec, value)
    body, sep, comment = line.partition(";")
    pat = re.compile(letter + r"[-+]?\d*\.?\d+")
    if pat.search(body):
        body = pat.sub(token, body, count=1)
    else:
        body = body.rstrip() + " " + token
        if not body.endswith(" ") and sep:
            pass
    if sep:
        return body.rstrip() + " ;" + comment if not body.endswith("\n") else body
    if line.endswith("\n") and not body.endswith("\n"):
        return body.rstrip() + "\n"
    return body


def nudge_vertex(src: str, dst: str) -> dict:
    """Move one mid-loop outer vertex 0.1 mm off the BASE path. No other edit."""
    lines = open(src, newline="").read().splitlines(keepends=True)
    ev = C.parse(src)
    chosen = None
    for ch in S.outer_chains(ev):
        if not ch["closed"] or ch["layer"] < 20:
            continue
        segs = ch["segs"]
        if len(segs) < 6:
            continue
        seg = segs[3]
        length = C.seglen(seg)
        if length < 1.0:
            continue
        dx = seg["x1"] - seg["x0"]
        dy = seg["y1"] - seg["y0"]
        px, py = -dy / length, dx / length
        chosen = {
            "layer": ch["layer"],
            "line": seg["line"],
            "old": (seg["x1"], seg["y1"]),
            "new": (seg["x1"] + 0.1 * px, seg["y1"] + 0.1 * py),
            "length": length,
        }
        break
    if chosen is None:
        raise SystemExit("no vertex to nudge")
    idx = chosen["line"] - 1
    lines[idx] = _set_word(lines[idx], "X", chosen["new"][0], 3)
    lines[idx] = _set_word(lines[idx], "Y", chosen["new"][1], 3)
    if not lines[idx].endswith("\n"):
        lines[idx] += "\n"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", newline="") as fh:
        fh.writelines(lines)
    chosen["old"] = [round(v, 4) for v in chosen["old"]]
    chosen["new"] = [round(v, 4) for v in chosen["new"]]
    chosen["length"] = round(chosen["length"], 3)
    return chosen


def slow_fast_walls(src: str, dst: str) -> dict:
    """Set F1200 on every closed-outer extrusion that is already >= 10 mm and faster than F1200.

    The following extrusion keeps the old feed when it had none of its own, so the
    slowdown does not leak through a modal F. No vertex is added.
    """
    lines = open(src, newline="").read().splitlines(keepends=True)
    ev = C.parse(src)
    edits: dict[int, str] = {}
    slowed = 0.0
    n = 0
    loops = 0
    for ch in S.outer_chains(ev):
        if not ch["closed"]:
            continue
        hit = False
        segs = ch["segs"]
        for k, seg in enumerate(segs):
            length = C.seglen(seg)
            if seg["f"] <= 1200.5 or length < 10.0:
                continue
            raw = lines[seg["line"] - 1]
            body = raw.split(";", 1)[0]
            if F_TOKEN.search(body) is None:
                continue
            edits[seg["line"] - 1] = F_TOKEN.sub("F1200", raw, count=1)
            if k + 1 < len(segs):
                nxt = segs[k + 1]
                nraw = lines[nxt["line"] - 1]
                nbody = nraw.split(";", 1)[0]
                if F_TOKEN.search(nbody) is None and nxt["line"] - 1 not in edits:
                    old = "F%.0f" % seg["f"]
                    if ";" in nraw:
                        code, com = nraw.split(";", 1)
                        pinned = code.rstrip() + " " + old + " ;" + com
                    else:
                        pinned = nraw.rstrip("\n") + " " + old + ("\n" if nraw.endswith("\n") else "")
                    edits[nxt["line"] - 1] = pinned
            slowed += length
            n += 1
            hit = True
        if hit:
            loops += 1
    for idx, text in edits.items():
        lines[idx] = text if text.endswith("\n") else text + "\n"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", newline="") as fh:
        fh.writelines(lines)
    return {"moves": n, "loops": loops, "slowed_mm": round(slowed, 1)}


def flip_light_byte(src: str, dst: str) -> dict:
    """One byte, not on the generated-by stamp. ApexLight fan lines are fractional (M106 S14.9)."""
    data = open(src, "rb").read()
    needle = b"M106 S14.9"
    at = data.find(needle)
    if at < 0:
        raise SystemExit("M106 S14.9 not found")
    line_start = data.rfind(b"\n", 0, at) + 1
    line_end = data.find(b"\n", at)
    line = data[line_start:line_end]
    if line.startswith(b"; generated by"):
        raise SystemExit("refusing to edit the generated-by line")
    data = data[:at] + b"M106 S14.8" + data[at + len(needle):]
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, "wb").write(data)
    return {"byte": at, "line": line.decode("ascii", "replace"), "old": "M106 S14.9", "new": "M106 S14.8"}


def build(base_dir: str, p2_dir: str, out_root: str) -> None:
    print("p3a", _orthocad_from(base_dir, os.path.join(out_root, "p3a"), "fixed140", None))
    print("p3b", _orthocad_from(base_dir, os.path.join(out_root, "p3b"), "shift", 100))
    print("p3c", _orthocad_from(base_dir, os.path.join(out_root, "p3c"), "side", 100))

    d = os.path.join(out_root, "p3d")
    _copy_quad(p2_dir, d)
    print("p3d", nudge_vertex(
        os.path.join(p2_dir, "apex_core_orthocad.gcode"),
        os.path.join(d, "apex_core_orthocad.gcode"),
    ))

    e = os.path.join(out_root, "p3e")
    _copy_quad(p2_dir, e)
    print("p3e orthocad", slow_fast_walls(
        os.path.join(p2_dir, "apex_core_orthocad.gcode"),
        os.path.join(e, "apex_core_orthocad.gcode"),
    ))
    print("p3e insole", slow_fast_walls(
        os.path.join(p2_dir, "apex_core_insole2.gcode"),
        os.path.join(e, "apex_core_insole2.gcode"),
    ))

    f = os.path.join(out_root, "p3f")
    _copy_quad(p2_dir, f)
    print("p3f", flip_light_byte(
        os.path.join(p2_dir, "apex_light_orthocad.gcode"),
        os.path.join(f, "apex_light_orthocad.gcode"),
    ))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], sys.argv[3])
