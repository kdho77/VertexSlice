#!/usr/bin/env python3
"""Drop near-top seam retract/prime pairs that ApexLight v2 does not have.

The printed ApexCore 95A file is the pullback output with 19 of those pairs
removed, plus the bare G1 F12720 that restores travel feed after each prime.
The pairs sit on layers 32, 421-429, 433, 437 and 438.

A seam pair is the G1 E-0.5 retract, the G1 E0.45 prime, and that following
G1 F12720 on a `;C seam travel` block. It is near the top when its landing
is within 2.5 mm of the highest extrusion whose X is within ±1 mm on that
layer. It has no ApexLight v2 counterpart when its landing is in the locked
table below, or, if a reference file is passed, when that file has no seam
landing on the same layer within 1.5 mm.

LOCKED. The table is the ApexLight v2 result for the printed file. No changes
without Kendon's approval.
"""
from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from pathlib import Path

NEAR_TOP_MM = 2.5
X_BAND_MM = 1.0
COUNTERPART_MM = 1.5
WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))")

# layer, landing X, landing Y. Nineteen pairs, no ApexLight v2 counterpart.
NO_COUNTERPART = (
    (32, 104.294, 3.036),
    (421, 162.174, 17.928),
    (422, 160.638, 17.192),
    (423, 159.298, 16.750),
    (424, 158.389, 16.645),
    (424, 117.937, 16.871),
    (425, 156.677, 16.158),
    (425, 119.171, 16.590),
    (426, 155.262, 15.943),
    (426, 121.112, 16.012),
    (427, 153.768, 15.782),
    (427, 123.005, 15.566),
    (428, 152.189, 15.647),
    (428, 125.003, 15.256),
    (429, 150.443, 15.545),
    (429, 127.947, 14.747),
    (433, 135.041, 14.808),
    (437, 145.267, 18.610),
    (438, 144.210, 18.808),
)


def _body(line: str) -> str:
    return line.split(";", 1)[0].strip()


def _words(line: str) -> dict[str, str]:
    return dict(WORD.findall(_body(line)))


def _layer_extrusions(lines: list[str]) -> dict[int, list[tuple[float, float]]]:
    layer = -1
    x = y = None
    acc: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
            continue
        body = _body(line)
        if not (body.startswith("G0") or body.startswith("G1")):
            continue
        w = _words(line)
        if "X" in w:
            x = float(w["X"])
        if "Y" in w:
            y = float(w["Y"])
        if "E" in w and float(w["E"]) > 0 and ("X" in w or "Y" in w) and x is not None and y is not None:
            acc[layer].append((x, y))
    return acc


def _near_top(x: float, y: float, pts: list[tuple[float, float]]) -> bool:
    best = None
    for ex, ey in pts:
        if abs(ex - x) > X_BAND_MM:
            continue
        if best is None or ey > best[1] or (ey == best[1] and abs(ex - x) < abs(best[0] - x)):
            best = (ex, ey)
    if best is None:
        return False
    return math.hypot(best[0] - x, best[1] - y) <= NEAR_TOP_MM


def seam_pairs(lines: list[str]) -> list[dict]:
    """Seam retract/prime pairs and the G1 F12720 restore after the prime."""
    layer = -1
    pairs = []
    n = len(lines)
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
        if not line.startswith(";C seam travel"):
            continue
        retract = prime = restore = None
        sx = sy = None
        for j in range(i + 1, min(n, i + 8)):
            lj = lines[j].rstrip("\n")
            body = _body(lj)
            if body == "G1 E-0.5 F1800" and retract is None:
                retract = j
            if "seam travel" in lj:
                w = _words(lj)
                if "X" in w:
                    sx = float(w["X"])
                if "Y" in w:
                    sy = float(w["Y"])
            if "lower" in lj and body.startswith("G1"):
                w = _words(lj)
                if "Y" in w:
                    sy = float(w["Y"])
            if body == "G1 E0.45 F1800" and prime is None:
                prime = j
            if body == "G1 F12720" and prime is not None and j == prime + 1:
                restore = j
                break
        if None not in (retract, prime, restore, sx, sy):
            pairs.append({"layer": layer, "retract": retract, "prime": prime, "restore": restore, "x": sx, "y": sy})
    return pairs


def _locked_no_counterpart(layer: int, x: float, y: float) -> bool:
    for lay, lx, ly in NO_COUNTERPART:
        if lay == layer and abs(lx - x) <= 0.02 and abs(ly - y) <= 0.02:
            return True
    return False


def _reference_landings(path: Path) -> list[tuple[int, float, float]]:
    text = path.read_text(errors="replace")
    return [(p["layer"], p["x"], p["y"]) for p in seam_pairs(text.splitlines(keepends=True))]


def _has_reference_counterpart(layer: int, x: float, y: float, refs: list[tuple[int, float, float]]) -> bool:
    for lay, lx, ly in refs:
        if lay == layer and math.hypot(lx - x, ly - y) <= COUNTERPART_MM:
            return True
    return False


def topfix(text: str, reference: Path | None = None) -> tuple[str, list[dict]]:
    """Return gcode with the locked near-top pairs removed, and the pairs removed."""
    lines = text.splitlines(keepends=True)
    extr = _layer_extrusions(lines)
    refs = _reference_landings(reference) if reference is not None else None
    drop: set[int] = set()
    removed = []
    for pair in seam_pairs(lines):
        if not _near_top(pair["x"], pair["y"], extr.get(pair["layer"], [])):
            continue
        if refs is None:
            orphan = _locked_no_counterpart(pair["layer"], pair["x"], pair["y"])
        else:
            orphan = not _has_reference_counterpart(pair["layer"], pair["x"], pair["y"], refs)
        if not orphan:
            continue
        drop.update((pair["retract"], pair["prime"], pair["restore"]))
        removed.append(pair)
    out = "".join(line for i, line in enumerate(lines) if i not in drop)
    return out, removed


def retract_events(text: str) -> list[list]:
    """Retract, prime, and pullback events in file order.

    Each event is [layer, kind, e, x, y]. kind is seam-retract, seam-prime,
    pull-retract, pull-prime, or other. A pullback that follows the seam lift
    (seam-follow) is a pull-retract; its prime sits on the lower line. x and y
    are rounded to 0.001 mm.
    """
    lines = text.splitlines()
    layer = -1
    x = y = None
    # Set on `;C seam travel`: "seam" if the retract is before the lift, "pull" if after.
    block = None
    events = []
    for i, line in enumerate(lines):
        if line.startswith(";LAYER_CHANGE"):
            layer += 1
            block = None
            continue
        if line.startswith(";C seam travel"):
            block = "open"
            continue
        if line.startswith(";C_SEAM_START") or line.startswith(";TYPE:"):
            block = None
        body = _body(line)
        if not (body.startswith("G0") or body.startswith("G1")):
            continue
        w = _words(line)
        if "X" in w:
            x = float(w["X"])
        if "Y" in w:
            y = float(w["Y"])
        if "E" not in w or "X" in w or "Y" in w or "Z" in w:
            continue
        e = float(w["E"])
        prev = lines[i - 1] if i else ""
        if prev.startswith(";C seam travel") and e < 0:
            kind = "seam-retract"
            block = "seam"
        elif e < 0 and (prev.startswith("M204") or ";lift" in prev):
            kind = "pull-retract"
            if block == "open":
                block = "pull"
        elif e > 0 and ";lower" in prev and block == "seam":
            kind = "seam-prime"
            block = None
        elif e > 0 and (";lower" in prev or (prev.startswith("G1") and "E" not in _body(prev))):
            kind = "pull-prime"
            block = None
        else:
            kind = "other"
        rx = None if x is None else round(x, 3)
        ry = None if y is None else round(y, 3)
        events.append([layer, kind, round(e, 3), rx, ry])
    return events


def main() -> None:
    args = sys.argv[1:]
    reference = None
    if "--reference" in args:
        i = args.index("--reference")
        reference = Path(args[i + 1])
        del args[i : i + 2]
    if len(args) != 2:
        raise SystemExit("usage: topfix_apexcore.py INPUT OUTPUT [--reference APEXLIGHT_V2.gcode]")
    src, dest = Path(args[0]), Path(args[1])
    text = src.read_text(errors="replace")
    out, removed = topfix(text, reference)
    dest.write_text(out)
    layers = sorted({p["layer"] for p in removed})
    print("topfix", "removed", len(removed), "layers", layers)


if __name__ == "__main__":
    main()
