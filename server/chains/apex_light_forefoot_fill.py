"""Fill ApexLight forefoot hollows with Cura's sparse gyroid.

This is the band fill already in ``postprocess_g2b`` (``_band_paths`` and
``_band_block``): gyroid pieces in the empty columns, then that function's
own connector through any column the wave missed. Every existing line is
copied unchanged. Nothing is inserted into a section the 0.25 mm hollow
tool does not already call empty (outer thickness >= 3 mm, air > 1 mm, no
non-skin plastic in the air). Skins, seams, and layer-change lines are not
rewritten, so their stations stay the bytes the slicer wrote.

ApexCore does not import this module. ``finalize_apex_light`` runs first;
this pass only sees the file that pass already emitted.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_COLUMN_MM = 0.25
_SECTION_MM = 3.0
_AIR_MM = 1.0
_SAMPLE_MM = 0.2
_COS_MIN = 0.25
_LEADER = {"Skirt", "Brim", "Support", "Support interface", "Prime tower", "Wipe tower"}
_HEIGHT_046 = 0.46
_FOREFOOT_046 = 165


def _g2b():
    root = Path(__file__).resolve().parents[2]
    g2b = root / "scripts" / "g2b_heelmod"
    if str(g2b) not in sys.path:
        sys.path.insert(0, str(g2b))
    import postprocess_g2b as mod

    return mod


def _skin_pair(beads: list[tuple[float, float, float]]):
    """(plantar_lo, plantar_hi, dorsal_lo, dorsal_hi, single) or None."""
    if not beads:
        return None
    lo_yc = min(b[2] for b in beads)
    hi_yc = max(b[2] for b in beads)
    if hi_yc - lo_yc < 0.20:
        lo = min(b[0] for b in beads)
        hi = max(b[1] for b in beads)
        return (lo, hi, lo, hi, True)
    mid = (lo_yc + hi_yc) * 0.5
    plow = phigh = dlow = dhigh = None
    for lo, hi, yc in beads:
        if yc <= mid:
            plow = lo if plow is None else min(plow, lo)
            phigh = hi if phigh is None else max(phigh, hi)
        if yc >= mid:
            dlow = lo if dlow is None else min(dlow, lo)
            dhigh = hi if dhigh is None else max(dhigh, hi)
    if plow is None or dlow is None:
        return None
    return (plow, phigh, dlow, dhigh, False)


def _section(pair) -> tuple[float, float]:
    plow, phigh, dlow, dhigh, single = pair
    if single:
        return dhigh - plow, -(dhigh - plow)
    return dhigh - plow, dlow - phigh


def hollow_columns(segs: list[dict], last_layer: int) -> dict[tuple[int, int], tuple[float, float]]:
    """(layer, 0.25 mm bin) -> (inner plantar face, inner dorsal face).

    Same column rules as the frozen 0.25 mm hollow tool: shallow skins only,
    the E-derived envelope, air strictly over 1 mm, and any non-skin bead
    that meets that air closes the column.
    """
    skins: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    cover: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for seg in segs:
        layer = seg["layer"]
        if layer < 1 or layer > last_layer:
            continue
        if seg["type"] in _LEADER:
            continue
        x0, y0, x1, y1 = seg["x0"], seg["y0"], seg["x1"], seg["y1"]
        length = seg["L"]
        if length < 1e-6 or seg["e"] <= 1e-9:
            continue
        dx, dy = x1 - x0, y1 - y0
        cos = seg["cos"]
        width = seg["w"]
        if seg["kind"] == "skin":
            if cos < _COS_MIN:
                continue
            yhalf = (width * 0.5) / cos
            xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
            b0 = int(math.floor(xa / _COLUMN_MM))
            b1 = int(math.floor(xb / _COLUMN_MM))
            for binn in range(b0, b1 + 1):
                lo = max(xa, binn * _COLUMN_MM)
                hi = min(xb, (binn + 1) * _COLUMN_MM)
                if hi - lo <= 1e-9:
                    continue
                xc = (lo + hi) * 0.5
                if abs(dx) > 1e-12:
                    t = (xc - x0) / dx
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                else:
                    t = 0.5
                yc = y0 + t * dy
                skins.setdefault((layer, binn), []).append((yc - yhalf, yc + yhalf, yc))
            continue
        yhalf = width * 0.5
        sin = abs(dy) / length
        xrad = yhalf * sin
        n = max(1, int(math.ceil(length / _SAMPLE_MM)))
        for i in range(n + 1):
            t = i / n
            x = x0 + dx * t
            y = y0 + dy * t
            along = min(t, 1.0 - t) * length
            rad = yhalf if along <= yhalf else xrad
            b0 = int(math.floor((x - rad) / _COLUMN_MM))
            b1 = int(math.floor((x + rad) / _COLUMN_MM))
            for binn in range(b0, b1 + 1):
                blo = binn * _COLUMN_MM
                bhi = blo + _COLUMN_MM
                if x + rad < blo or x - rad > bhi:
                    continue
                cover.setdefault((layer, binn), []).append((y - yhalf, y + yhalf))
    found: dict[tuple[int, int], tuple[float, float]] = {}
    for key, beads in skins.items():
        pair = _skin_pair(beads)
        if pair is None or pair[4]:
            continue
        thickness, air = _section(pair)
        if thickness < _SECTION_MM or air <= _AIR_MM:
            continue
        phigh, dlow = pair[1], pair[2]
        occupied = False
        for ya, yb in cover.get(key, ()):
            if min(yb, dlow) - max(ya, phigh) > 0.0:
                occupied = True
                break
        if not occupied:
            found[key] = (phigh, dlow)
    return found


def _bead_height(text: str) -> float | None:
    vals = []
    for line in text.splitlines():
        if not line.startswith(";HEIGHT:"):
            continue
        try:
            hv = float(line[8:])
        except ValueError:
            continue
        if 0.2 <= hv <= 0.55:
            vals.append(hv)
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def _forefoot_limit(layer_count: int, text: str, g2b) -> int | None:
    """Orthocad forefoot only: layers 1-165, or the physical window at another bead height.

    ``insole_sample`` (about 40-220 layers) is not this defect. A partial fill
    there would change that file and leave the columns above layer 31 hollow,
    so those files are copied through.
    """
    last = g2b.forefoot_last_layer(layer_count)
    if last != g2b.FOREFOOT_ORTHOCAD_LAST:
        return None
    height = _bead_height(text)
    if height is None or height <= 1e-6:
        return last
    physical = int(round(_FOREFOOT_046 * _HEIGHT_046 / height))
    return max(last, physical)


def _words(g2b, line: str) -> dict[str, str]:
    return g2b._ff_words(line)


def _block_e(g2b, block: list[str]) -> float:
    total = 0.0
    for line in block:
        words = _words(g2b, line)
        if "E" in words:
            total += float(words["E"])
    return total


def _bump_filament(text: str, added_mm: float, area: float) -> str:
    if added_mm <= 1e-6:
        return text
    lines = text.splitlines(keepends=True)
    old_mm = None
    for line in lines:
        if line.startswith("; filament used [mm] = "):
            old_mm = float(line.split("=", 1)[1])
            break
    if old_mm is None:
        return text
    new_mm = old_mm + added_mm
    new_cm3 = new_mm * area / 1000.0
    old_cm3 = old_mm * area / 1000.0
    density = 1.2
    for line in lines:
        if line.startswith("; filament used [g] = ") and old_cm3 > 1e-9:
            density = float(line.split("=", 1)[1]) / old_cm3
            break

    def rewrite(line: str, value: float) -> str:
        body = line.split("=", 1)[1].strip()
        nd = len(body.split(".")[1]) if "." in body else 0
        nl = ""
        if line.endswith("\r\n"):
            nl = "\r\n"
        elif line.endswith("\n"):
            nl = "\n"
        prefix = line.split("=", 1)[0]
        return f"{prefix}= {value:.{nd}f}{nl}"

    out = []
    for line in lines:
        if line.startswith("; filament used [mm] = "):
            out.append(rewrite(line, new_mm))
        elif line.startswith("; filament used [cm3] = "):
            out.append(rewrite(line, new_cm3))
        elif line.startswith("; filament used [g] = "):
            out.append(rewrite(line, new_cm3 * density))
        else:
            out.append(line)
    return "".join(out)


def _newline(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def fill_forefoot_hollow(text: str, last_layer: int | None = None) -> tuple[str, dict]:
    """Add Cura sparse infill into hollow forefoot columns. Other lines stay.

    ``last_layer`` overrides the cutoff. Production leaves it unset and only
    fills an orthocad-length file (layers 1-165, or the physical window when
    the bead height is not 0.46 mm).
    """
    g2b = _g2b()
    lines = text.splitlines(keepends=True)
    parsed = g2b._parse_moves(lines, reset_type=False)
    if last_layer is None:
        last_layer = _forefoot_limit(parsed["layers"], text, g2b)
    stats: dict = {
        "applied": False,
        "last_layer": last_layer,
        "hollow_before": 0,
        "hollow_after": 0,
        "layers": 0,
        "extrusions": 0,
        "added_mm": 0.0,
    }
    if not last_layer or last_layer < 1:
        stats["reason"] = "out of scope"
        return text, stats
    hollow = hollow_columns(parsed["segs"], last_layer)
    stats["hollow_before"] = len(hollow)
    if not hollow:
        stats["reason"] = "no hollow"
        return text, stats
    by_layer: dict[int, dict[int, tuple[float, float]]] = {}
    for (layer, binn), gap in hollow.items():
        by_layer.setdefault(layer, {})[binn] = gap

    nl = _newline(lines)
    layer = -1
    x = y = 0.0
    feed = 0.0
    absolute = True
    e_rel = False
    typ = ""
    width = ""
    z_at: dict[int, float] = {}
    end_at: dict[int, int] = {}
    state: dict[int, tuple[float, float, float, str, str, bool]] = {}
    for index, line in enumerate(lines):
        raw = line.strip()
        if raw.startswith(";LAYER_CHANGE"):
            if layer >= 0:
                end_at[layer] = index
                state[layer] = (x, y, feed, typ, width, e_rel)
            layer += 1
            continue
        if raw.startswith(";TYPE:"):
            typ = raw[6:].strip()
            continue
        if raw.startswith(";WIDTH:"):
            width = raw
            continue
        if raw.startswith(";Z:") and layer not in z_at:
            try:
                z_at[layer] = float(raw[3:])
            except ValueError:
                pass
            continue
        body = raw.split(";", 1)[0].strip()
        if body.startswith("G90"):
            absolute = True
            continue
        if body.startswith("G91"):
            if layer >= 0 and layer not in end_at:
                end_at[layer] = index
                state[layer] = (x, y, feed, typ, width, e_rel)
            break
        if body.startswith("M83"):
            e_rel = True
            continue
        if body.startswith("M82"):
            e_rel = False
            continue
        if not g2b._is_g01(line):
            continue
        words = _words(g2b, body)
        if "X" in words:
            x = float(words["X"]) if absolute else x + float(words["X"])
        if "Y" in words:
            y = float(words["Y"]) if absolute else y + float(words["Y"])
        if "F" in words:
            feed = float(words["F"])
    if layer >= 0 and layer not in end_at:
        end_at[layer] = len(lines)
        state[layer] = (x, y, feed, typ, width, e_rel)

    edges: dict[int, list[tuple[float, float, float, float]]] = {}
    for seg in parsed["segs"]:
        if seg["kind"] == "skin" and 1 <= seg["layer"] <= last_layer:
            edges.setdefault(seg["layer"], []).append((seg["x0"], seg["y0"], seg["x1"], seg["y1"]))

    blocks: dict[int, list[str]] = {}
    added_mm = 0.0
    n_ext = 0
    for lay, cols in by_layer.items():
        if lay not in state:
            continue
        ex, ey, fd, tp, wd, rel = state[lay]
        if not rel or not absolute:
            stats["reason"] = "extrusion or positioning is not the ApexLight mode"
            return text, stats
        paths = g2b._band_paths(z_at.get(lay), cols)
        if not paths:
            continue
        block = g2b._band_block(paths, ex, ey, fd, tp, wd, edges.get(lay, []), nl)
        n_ext += sum(1 for line in block if " E" in line.split(";", 1)[0] and "E-" not in line.split(";", 1)[0])
        added_mm += _block_e(g2b, block)
        blocks[end_at[lay]] = block
    if not blocks:
        stats["reason"] = "nothing to insert"
        return text, stats
    out: list[str] = []
    for index, line in enumerate(lines):
        if index in blocks:
            out.extend(blocks[index])
        out.append(line)
    if len(lines) in blocks:
        out.extend(blocks[len(lines)])
    joined = "".join(out)
    joined = _bump_filament(joined, added_mm, parsed["area"])
    after = g2b._parse_moves(joined.splitlines(keepends=True), reset_type=False)
    stats["applied"] = True
    stats["layers"] = len(blocks)
    stats["extrusions"] = n_ext
    stats["added_mm"] = round(added_mm, 5)
    stats["hollow_after"] = len(hollow_columns(after["segs"], last_layer))
    return joined, stats
