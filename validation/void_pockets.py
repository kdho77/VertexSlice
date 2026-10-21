#!/usr/bin/env python3
"""Inscribed-circle air pockets on a belt G-code extrusion footprint.

A pocket is a connected unextruded region inside the outer wall whose largest
inscribed circle, after each extrusion is dilated by half its bead, is wider
than 1.5 mm. Sparse infill is dilated by one extra half bead (0.50 mm) so a
gyroid cell drops out. A remaining region that touches a perimeter is a pocket.
Width is the inscribed diameter. Length is the span along the pocket's long axis.
"""

import argparse
import math
import re
import sys
from collections import defaultdict

import numpy as np
from scipy import ndimage

AXIS = re.compile(r"([XYZEF])(-?(?:\d+\.?\d*|\.\d+))")
WIDTH = re.compile(r";WIDTH:([0-9.]+)")
PITCH = 0.10  # mm
LIMIT = 1.5   # mm inscribed diameter


def _words(cmd):
    return {m.group(1): float(m.group(2)) for m in AXIS.finditer(cmd)}


def parse_layers(path):
    """Return {layer_index: [(x0,y0,x1,y1,width,kind), ...]} in file millimetres."""
    layers = defaultdict(list)
    x = y = None
    width = 0.8
    kind = "other"
    layer = 0
    rel_e = True
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith(";LAYER_CHANGE"):
                layer += 1
                continue
            if line.startswith(";TYPE:"):
                name = line[6:].strip().lower()
                if "outer" in name or "inner" in name or name.startswith("wall"):
                    kind = "wall"
                elif "sparse" in name or name == "internal infill" or name == "fill":
                    kind = "infill"
                elif "gap" in name:
                    kind = "gap"
                else:
                    kind = "other"
                continue
            m = WIDTH.search(line)
            if m:
                width = float(m.group(1))
            cmd = line.split(";", 1)[0].strip()
            if not cmd:
                continue
            code = cmd.split()[0].upper()
            if code == "M83":
                rel_e = True
                continue
            if code == "M82":
                rel_e = False
                continue
            if code not in ("G0", "G1"):
                continue
            w = _words(cmd)
            nx, ny = x, y
            if "X" in w:
                nx = w["X"]
            if "Y" in w:
                ny = w["Y"]
            extrude = False
            if "E" in w and x is not None and (nx != x or ny != y):
                de = w["E"] if rel_e else None
                # Absolute E is not used by these belt profiles. Treat a positive
                # relative E as extrusion. A missing mode still counts E>0 after M83.
                if de is not None and de > 0.01:
                    extrude = True
            if extrude and layer > 0 and width > 0.05:
                layers[layer].append((x, y, nx, ny, width, kind))
            if "X" in w:
                x = nx
            if "Y" in w:
                y = ny
    return layers


def _disk(radius_px):
    r = max(int(math.ceil(radius_px)), 0)
    if r == 0:
        return np.ones((1, 1), dtype=bool)
    yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= (radius_px + 0.15) ** 2


def _stamp(mask, segs, origin):
    h, w = mask.shape
    ox, oy = origin
    for x0, y0, x1, y1 in segs:
        length = math.hypot(x1 - x0, y1 - y0)
        n = max(int(length / PITCH) + 1, 2)
        ts = np.linspace(0.0, 1.0, n)
        xs = np.rint((x0 + (x1 - x0) * ts - ox) / PITCH).astype(np.int32)
        ys = np.rint((y0 + (y1 - y0) * ts - oy) / PITCH).astype(np.int32)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        mask[ys[ok], xs[ok]] = True


def pockets_on_layer(segs):
    if not segs:
        return []
    xs = [s[0] for s in segs] + [s[2] for s in segs]
    ys = [s[1] for s in segs] + [s[3] for s in segs]
    pad = 2.0
    origin = (min(xs) - pad, min(ys) - pad)
    w = int(math.ceil((max(xs) + pad - origin[0]) / PITCH)) + 2
    h = int(math.ceil((max(ys) + pad - origin[1]) / PITCH)) + 2
    if w * h > 8_000_000 or w < 2 or h < 2:
        return []

    buckets = defaultdict(lambda: {"wall": [], "infill": [], "other": []})
    for x0, y0, x1, y1, width, kind in segs:
        key = max(1, int(round(width / 0.05)))
        slot = kind if kind in ("wall", "infill") else "other"
        buckets[key][slot].append((x0, y0, x1, y1))

    wall = np.zeros((h, w), dtype=bool)
    infill = np.zeros((h, w), dtype=bool)
    other = np.zeros((h, w), dtype=bool)
    for key, groups in buckets.items():
        radius = (key * 0.05) / 2.0 / PITCH
        disk = _disk(radius)
        for slot, dest in (("wall", wall), ("infill", infill), ("other", other)):
            if not groups[slot]:
                continue
            center = np.zeros((h, w), dtype=bool)
            _stamp(center, groups[slot], origin)
            dest |= ndimage.binary_dilation(center, structure=disk)

    # Close the outer wall so the border flood cannot leak into the part.
    # The closed wall is only a flood barrier; the footprint below is the beads.
    barrier = ndimage.binary_closing(wall, structure=np.ones((3, 3), dtype=bool))
    seeds = []
    for sx, sy in ((0, 0), (0, h - 1), (w - 1, 0), (w - 1, h - 1)):
        if not barrier[sy, sx]:
            seeds.append((sy, sx))
    if not seeds:
        return []
    free = ~barrier
    labels, _ = ndimage.label(free)
    outside_ids = {labels[sy, sx] for sy, sx in seeds if labels[sy, sx] != 0}
    outside = np.isin(labels, list(outside_ids)) if outside_ids else np.ones_like(barrier)
    # Half-bead dilation is already in the footprint. An extra half bead on
    # sparse infill (0.50 mm, half of the 1.0 mm infill line) seals gyroid
    # cells. What remains and touches a wall is a wall channel. Filtering on
    # wall-pixel vs infill-pixel counts drops those channels.
    seal = ndimage.binary_dilation(infill, structure=_disk(0.50 / PITCH))
    void = ~outside & ~wall & ~seal & ~other
    if not void.any():
        return []
    edt = ndimage.distance_transform_edt(void) * PITCH
    comp, n = ndimage.label(void)
    wall_near = ndimage.binary_dilation(wall, structure=np.ones((3, 3), dtype=bool))
    found = []
    for i in range(1, n + 1):
        region = comp == i
        if int(region.sum()) < 4:
            continue
        if not np.any(region & wall_near):
            continue
        width = 2.0 * float(edt[region].max())
        if width <= LIMIT:
            continue
        ys, xs = np.nonzero(region)
        coords = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1)
        coords -= coords.mean(axis=0)
        if len(coords) < 3:
            length = width
        else:
            cov = coords.T @ coords
            vals, vecs = np.linalg.eigh(cov)
            axis = vecs[:, int(np.argmax(vals))]
            proj = coords @ axis
            length = max(float(proj.max() - proj.min()) * PITCH, width)
        found.append((width, length))
    return found


def report(path, only=None):
    layers = parse_layers(path)
    total = 0
    worst = None
    per_layer = []
    for idx in sorted(layers):
        if only is not None and idx not in only:
            continue
        found = pockets_on_layer(layers[idx])
        if not found:
            continue
        total += len(found)
        layer_worst = max(found, key=lambda t: (t[0], t[1]))
        per_layer.append((idx, len(found), layer_worst))
        if worst is None or (layer_worst[0], layer_worst[1]) > (worst[1], worst[2]):
            worst = (idx, layer_worst[0], layer_worst[1])
    print(f"{path}")
    print(f"  pockets > {LIMIT:.1f} mm: {total}")
    if worst is None:
        print("  worst: none")
    else:
        print(f"  worst: L{worst[0]}  {worst[1]:.2f} x {worst[2]:.2f} mm")
    for idx, count, pocket in sorted(per_layer, key=lambda r: -r[2][0])[:8]:
        w, length = pocket[0], pocket[1]
        print(f"    L{idx}: {count}  worst {w:.2f} x {length:.2f} mm")
    return total, worst


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gcode", nargs="+")
    parser.add_argument("--layers", default="", help="Comma-separated layer indices to measure")
    args = parser.parse_args()
    only = None
    if args.layers:
        only = {int(part) for part in args.layers.split(",") if part}
    failed = False
    for path in args.gcode:
        total, _worst = report(path, only)
        if total:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
