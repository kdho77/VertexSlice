"""ApexCore #46 forefoot hollow fill on the ApexForge / ApexBounce generic path.

The method is the hollow half of ``forefoot_cura_bond`` in
``scripts/g2b_heelmod/postprocess_g2b.py``: ``_thick_cura_fill`` then
``_band_sparse_infill``. A 3–6 mm section gets one fill bead pressed into the
outer walls. Empty sections 3 mm or thicker in the forefoot band get that
file's sparse infill. On the 0.46 mm ApexCore file the band is layers 156–165.

The thin-skin widen in ``forefoot_cura_bond`` (2–3 mm skins grown until they
overlap) is not called. On this 0.42 mm file it moves outer-wall lines and
seam points, and some of those beads land more than 0.02 mm outside the live
printed outline. The thick bead and the band infill leave every existing
wall, skin, seam, and layer-change line in place. Sections under 3 mm stay
as the slicer wrote them.

Added sparse infill is then restricted to a previously hollow forefoot
column. The column is the 0.25 mm tool: outer skins at least 3 mm apart,
more than 1 mm of air between the inner faces, and no fill already in that
air. The bead's midpoint has to sit in that air (the same half-millimetre
contact the bead uses to touch the skins). A bead in a 3–6 mm section that
was not hollow is not written. Dropping it does not leave a travel on that
path: the chord would be open air, or a ride along the outer wall. The
nozzle still reaches the next kept point, on a travel that stays in the
hollow air or on a non-extruding travel the live file already had. A hop
shorter than a bead, off the wall rather than along it, is only the step
into that air. The walls, seams, and layer-change lines are still the ones
the slicer wrote.

``; filament used [g]`` and ``; total filament used [g]`` are written as the
same value: the live grams plus the plastic this pass actually kept.

This module does not change the ApexCore file. Two adaptations the ApexCore
file does not need:

- Layer numbers scale by ``0.46 / layer_height`` so the band is the same
  physical stretch of the insole. At 0.42 mm that is layers 171–181, and the
  forefoot window is layers through 181 (``165 * 0.46 / 0.42``).
- Generic G-code is absolute extrusion (M82). The fill is the relative-E
  (M83) method. The copy it sees is relative; every line it does not edit is
  restored byte for byte, and inserted extrusion is written back as absolute
  E so later moves keep their own extrusion length.
"""
from __future__ import annotations

import difflib
import heapq
import math
import re
import sys
import threading
from decimal import Decimal
from pathlib import Path

_CORE_H = Decimal("0.46")
_CORE_FOREFOOT = 165
_CORE_BAND_FIRST = 156
_CORE_BAND_LAST = 165
_CORE_SHORT_FOREFOOT = 31
_CORE_ORTHOCAD_LAYERS = 300
_CORE_SHORT_LO = 40
_CORE_SHORT_HI = 220

_LAYER_H = re.compile(r"^;\s*layer_height\s*=\s*([0-9.]+)\s*$", re.M)
_DIAMETER = re.compile(r"^;\s*filament_diameter:\s*([0-9.]+)\s*$", re.M)
_DENSITY = re.compile(r"^;\s*filament_density:\s*([0-9.]+)\s*$", re.M)
_FILAMENT_MM = re.compile(r"^(;\s*filament used \[mm\] = )([0-9.]+)\s*$", re.M)
_FILAMENT_CM3 = re.compile(r"^(;\s*filament used \[cm3\] = )([0-9.]+)\s*$", re.M)
_E_TOKEN = re.compile(r"E([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_WORD = re.compile(r"([GMXYZEFS])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_HEIGHT = re.compile(r"^;HEIGHT:([0-9.]+)")
_TAG = re.compile(r" ;VSABS:(\d+)")
_LOCK = threading.Lock()
# Same instrument the forefoot filament rule uses. Bead height stays 0.46 mm
# on a 0.42 mm file; that is what decides which columns were hollow.
_TOOL_MM = 0.25
_HOLLOW_SECTION_MIN = 3.0
_HOLLOW_AIR_MIN = 1.0
_H_BEAD = 0.46
_COS_MIN = 0.25
_FOREFOOT_CAP = 165
_REF_LAYER_H = 0.46
# A hop into the hollow air, not a chord across the part. One bead is ~0.5 mm.
_BRIDGE_MAX_MM = 0.75
_WALL_RIDE_GAP_MM = 0.35
_WALL_RIDE_MIN_MM = 0.50
_FILAMENT_G_LINE = re.compile(r"^(;\s*filament used \[g\] = )([0-9.]+)\s*$", re.M)
_FILAMENT_G_TOTAL = re.compile(r"^(;\s*total filament used \[g\] = )([0-9.]+)\s*$", re.M)
_SKIN_TYPES = {"Outer wall", "Overhang wall", "WALL-OUTER"}
_FILL_TYPES = {"Sparse infill", "Internal solid infill", "Solid infill", "Gap infill"}
_LEADER_TYPES = {"Skirt", "Brim", "Support", "Support interface", "Prime tower", "Wipe tower"}
_TYPE_ALIAS = {"WALL-OUTER": "Outer wall", "WALL-INNER": "Inner wall", "FILL": "Sparse infill", "SKIN": "Solid infill"}


def scaled_forefoot_window(layer_height_mm: float, layer_count: int) -> dict | None:
    """Physical equivalent of the ApexCore #46 forefoot windows.

    Returns None for a layer count the ApexCore cutoff also leaves untouched.
    """
    height = Decimal(str(layer_height_mm))
    if height <= 0:
        return None
    equiv = Decimal(layer_count) * height / _CORE_H
    if equiv >= _CORE_ORTHOCAD_LAYERS:
        last_core = _CORE_FOREFOOT
    elif _CORE_SHORT_LO <= equiv <= _CORE_SHORT_HI:
        last_core = _CORE_SHORT_FOREFOOT
    else:
        return None
    last = _scale(last_core, height)
    band_first = _scale(_CORE_BAND_FIRST, height)
    band_last = _scale(_CORE_BAND_LAST, height)
    return {
        "layer_height_mm": float(height),
        "last_layer": last,
        "band_first": band_first,
        "band_last": band_last,
    }


def apply_forefoot_hollow_band(text: str) -> str:
    """Run the ApexCore #46 forefoot bond. ApexCore G-code is not passed here."""
    height = _layer_height(text)
    if height is None:
        return text
    window = scaled_forefoot_window(float(height), _layer_count(text))
    if window is None or window["last_layer"] < window["band_last"]:
        # The short ApexCore bucket ends before the sparse band, and #46 leaves
        # that file unchanged. The thick bead on this path's 12 mm sample also
        # crosses the printed outline, so it is not applied here either.
        return text
    # Generic Forge/Bounce files are M82. The bond writes relative extrusion, so
    # an absolute file is converted for the call and then put back.
    original_lines = text.splitlines(keepends=True)
    if not _uses_absolute_e(original_lines):
        bonded = _bond(text, height, window)
        if bonded == text:
            return text
        confined = _confine_added_sparse(text, bonded)
        if confined == text:
            return text
        return _with_filament(text, confined)
    relative_lines = _to_relative(original_lines)
    relative_text = "".join(relative_lines)
    bonded = _bond(relative_text, height, window)
    if bonded == relative_text:
        return text
    restored = _restore_absolute(original_lines, relative_lines, bonded.splitlines(keepends=True))
    confined = _confine_added_sparse(text, restored)
    if confined == text:
        return text
    return _with_filament(text, confined)


def _scale(layer: int, height: Decimal) -> int:
    return int(round(Decimal(layer) * _CORE_H / height))


def _layer_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith(";LAYER_CHANGE"))


def _layer_height(text: str) -> Decimal | None:
    match = _LAYER_H.search(text)
    if match:
        return Decimal(match.group(1))
    return None


def _load_g2b():
    root = Path(__file__).resolve().parents[1]
    g2b = root / "scripts" / "g2b_heelmod"
    if str(g2b) not in sys.path:
        sys.path.insert(0, str(g2b))
    import postprocess_g2b as module

    return module


def _bond(text: str, height: Decimal, window: dict) -> str:
    """Thick fill, then band infill, at this file's layer height.

    Same two functions ``forefoot_cura_bond`` calls for the hollow. The thin
    widen is not called: it rewrites outer walls. See the module docstring.
    """
    g2b = _load_g2b()
    with _LOCK:
        saved = (
            g2b.FOREFOOT_LAYER_H,
            g2b._BAND_FIRST,
            g2b._BAND_LAST,
            g2b._BAND_E_PER_MM,
        )
        try:
            g2b.FOREFOOT_LAYER_H = float(height)
            g2b._BAND_FIRST = window["band_first"]
            g2b._BAND_LAST = window["band_last"]
            # Same bead cross-section. E scales with layer height because the
            # 0.18 mm/mm constant was written for the 0.46 mm ApexCore bead.
            g2b._BAND_E_PER_MM = saved[3] * float(height) / float(_CORE_H)
            stats = {
                "applied": False,
                "last_layer": window["last_layer"],
                "layers": text.count(";LAYER_CHANGE"),
            }
            filled, stats = g2b._thick_cura_fill(text, stats)
            out, _stats = g2b._band_sparse_infill(filled, stats)
            return out
        finally:
            (
                g2b.FOREFOOT_LAYER_H,
                g2b._BAND_FIRST,
                g2b._BAND_LAST,
                g2b._BAND_E_PER_MM,
            ) = saved


def _newline(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _split(line: str) -> tuple[str, str, str, str]:
    nl = _newline(line)
    body = line[: len(line) - len(nl)] if nl else line
    code, sep, comment = body.partition(";")
    return code, sep, comment, nl


def _command(code: str) -> str:
    token = code.strip().split(" ", 1)[0] if code.strip() else ""
    return token.upper()


def _is_command(code: str) -> bool:
    cmd = _command(code)
    return cmd.startswith("G") or cmd.startswith("M") or cmd.startswith("T")


def _is_g92(code: str) -> bool:
    return _command(code).startswith("G92")


def _e_value(code: str) -> Decimal | None:
    match = _E_TOKEN.search(code)
    if match is None:
        return None
    return Decimal(match.group(1))


def _set_e(line: str, value: Decimal) -> str:
    code, sep, comment, nl = _split(line)
    token = "E" + format(value.quantize(Decimal("0.00001")), "f")
    code, count = _E_TOKEN.subn(token, code, count=1)
    if count == 0:
        return line
    return code + sep + comment + nl


def _strip_tag(line: str) -> str:
    nl = _newline(line)
    body = line[: len(line) - len(nl)] if nl else line
    return _TAG.sub("", body) + nl


def _tag_of(line: str) -> int | None:
    match = _TAG.search(line)
    if match is None:
        return None
    return int(match.group(1))


def _uses_absolute_e(lines: list[str]) -> bool:
    for line in lines:
        cmd = _command(_split(line)[0])
        if cmd.startswith("M82"):
            return True
        if cmd.startswith("M83"):
            return False
    return False


def _to_relative(lines: list[str]) -> list[str]:
    """Copy an M82 file into the relative extrusion the ApexCore bond expects."""
    e_abs = Decimal(0)
    out: list[str] = []
    for index, line in enumerate(lines):
        code, sep, comment, nl = _split(line)
        if not _is_command(code):
            out.append(line)
            continue
        tagged_comment = f"{sep}{comment} ;VSABS:{index}" if sep else f" ;VSABS:{index}"
        if _command(code).startswith("M82"):
            code = re.sub(r"M82", "M83", code, count=1)
            out.append(code + tagged_comment + nl)
            continue
        value = _e_value(code)
        if value is not None and not _is_g92(code):
            delta = value - e_abs
            e_abs = value
            rewritten = _set_e(code + nl, delta)
            rcode, rsep, rcomment, rnl = _split(rewritten)
            note = f"{rsep}{rcomment} ;VSABS:{index}" if rsep else f" ;VSABS:{index}"
            out.append(rcode + note + rnl)
            continue
        if value is not None and _is_g92(code):
            e_abs = value
        out.append(code + tagged_comment + nl)
    return out


def _apply_absolute(line: str, e_abs: Decimal) -> Decimal:
    code = _split(line)[0]
    value = _e_value(code)
    if value is None:
        return e_abs
    return value


def _g92_e(line: str) -> bool:
    code = _split(line)[0]
    return _is_g92(code) and _e_value(code) is not None


def _emit_relative(line: str, e_abs: Decimal) -> tuple[str, Decimal, bool]:
    """Interpret ``line`` as a relative-E move and write absolute E."""
    plain = _strip_tag(line)
    code, sep, comment, nl = _split(plain)
    value = _e_value(code)
    if value is None:
        return plain, e_abs, False
    if _is_g92(code):
        return plain, value, False
    updated = e_abs + value
    return _set_e(plain, updated), updated, True


def _restore_absolute(original: list[str], relative: list[str], produced: list[str]) -> str:
    """Put absolute E back. Lines the bond did not edit are the original bytes."""
    cursor = 0
    e_abs = Decimal(0)
    dirty = False
    out: list[str] = []
    limit = len(relative)

    def take_original(index: int, clear_on_g92: bool) -> None:
        nonlocal e_abs, dirty
        out.append(original[index])
        e_abs = _apply_absolute(original[index], e_abs)
        if clear_on_g92 and _g92_e(original[index]):
            dirty = False

    for line in produced:
        idx = _tag_of(line)
        if idx is not None and idx < cursor:
            written, e_abs, moved = _emit_relative(line, e_abs)
            out.append(written)
            if moved:
                dirty = True
            continue
        if idx is None:
            if cursor < limit and line == relative[cursor]:
                if not dirty:
                    take_original(cursor, True)
                else:
                    out.append(original[cursor])
                cursor += 1
            else:
                written, e_abs, moved = _emit_relative(line, e_abs)
                out.append(written)
                if moved:
                    dirty = True
            continue
        if idx > cursor:
            dirty = True
        plain = _strip_tag(line)
        kept = plain == _strip_tag(relative[idx])
        if kept and not dirty:
            take_original(idx, True)
        elif kept and _g92_e(original[idx]):
            take_original(idx, True)
        else:
            written, e_abs, moved = _emit_relative(plain, e_abs)
            out.append(written)
            if moved or not kept:
                dirty = True
        cursor = idx + 1
    return "".join(out)


def _equivalent_last(layer_height_mm: float) -> int:
    """Physical-equivalent forefoot window: ceil(165 * 0.46 / layer_height)."""
    return int(math.ceil(_FOREFOOT_CAP * _REF_LAYER_H / float(layer_height_mm) - 1e-9))


def _file_bead_height(text: str) -> float | None:
    heights = []
    for line in text.splitlines():
        match = _HEIGHT.match(line.strip())
        if match is None:
            continue
        value = float(match.group(1))
        if 0.2 <= value <= 0.55:
            heights.append(value)
    if heights:
        heights.sort()
        return heights[len(heights) // 2]
    named = _layer_height(text)
    if named is None or named <= 0:
        return None
    return float(named)


def _gate_events(text: str) -> list[dict]:
    """Extrusion events in the filament rule's frame.

    Layer 0 is the first ``;LAYER_CHANGE``. Skirt, brim, support, and prime
    tower moves are leaders and are not fill. Moves after the end-script G91
    are ignored.
    """
    x = y = z = e_abs = 0.0
    relative = True
    layer = -1
    typ = None
    in_end = False
    events: list[dict] = []
    for ln, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(";"):
            if stripped.startswith(";LAYER:"):
                try:
                    layer = int(stripped.split(":", 1)[1])
                except ValueError:
                    layer += 1
            elif stripped.startswith(";LAYER_CHANGE"):
                layer += 1
            elif stripped.startswith(";TYPE:"):
                name = stripped[6:].strip()
                typ = _TYPE_ALIAS.get(name, name)
            continue
        body = stripped.split(";", 1)[0].strip()
        if not body:
            continue
        words = dict(_WORD.findall(body))
        if "M" in words:
            code = int(float(words["M"]))
            if code == 82:
                relative = False
            elif code == 83:
                relative = True
            continue
        if "G" not in words:
            continue
        g = int(float(words["G"]))
        if g == 91 and not in_end:
            in_end = True
        if g == 92:
            if "E" in words:
                e_abs = float(words["E"])
            continue
        if g not in (0, 1) or in_end:
            continue
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        nz = float(words["Z"]) if "Z" in words else z
        de = 0.0
        if "E" in words:
            stated = float(words["E"])
            de = stated if relative else stated - e_abs
            if not relative:
                e_abs = stated
        moved = abs(nx - x) > 1e-9 or abs(ny - y) > 1e-9
        leader = typ in _LEADER_TYPES if moved else False
        if moved and de > 1e-9 and not leader:
            events.append(
                {
                    "kind": "ext",
                    "layer": layer,
                    "line": ln,
                    "type": typ,
                    "x0": x,
                    "y0": y,
                    "x1": nx,
                    "y1": ny,
                    "e": de,
                    "leader": False,
                }
            )
        x, y, z = nx, ny, nz
    return events


def _segment_key(ev: dict) -> tuple:
    return (
        ev["layer"],
        ev.get("type"),
        round(ev["x0"], 3),
        round(ev["y0"], 3),
        round(ev["x1"], 3),
        round(ev["y1"], 3),
    )


def _skin_pair(beads: list[tuple[float, float, float, str]]):
    skins = [b for b in beads if b[3] == "skin"]
    if not skins:
        return None
    lo_yc = min(b[2] for b in skins)
    hi_yc = max(b[2] for b in skins)
    if hi_yc - lo_yc < 0.20:
        return (min(b[0] for b in skins), max(b[1] for b in skins), min(b[0] for b in skins), max(b[1] for b in skins), True)
    mid = (lo_yc + hi_yc) * 0.5
    plow = phigh = dlow = dhigh = None
    for lo, hi, yc, _k in skins:
        if yc <= mid:
            plow = lo if plow is None else min(plow, lo)
            phigh = hi if phigh is None else max(phigh, hi)
        if yc >= mid:
            dlow = lo if dlow is None else min(dlow, lo)
            dhigh = hi if dhigh is None else max(dhigh, hi)
    if plow is None or dlow is None:
        return None
    return plow, phigh, dlow, dhigh, False


def _hollow_index(events: list[dict], area: float, last: int) -> dict[tuple[int, int], tuple[float, float]]:
    """(layer, 0.25 mm bin) -> (inner plantar face, inner dorsal face).

    A column is hollow when the outer skins are at least 3 mm apart, the air
    between the inner faces is over 1 mm, and no fill already overlaps that
    air. An inner wall does not clear the air.
    """
    cap = _H_BEAD * (1.0 - math.pi / 4.0)
    cols: dict[tuple[int, int], list[tuple[float, float, float, str]]] = {}
    for ev in events:
        layer = ev["layer"]
        if layer < 1 or layer > last:
            continue
        x0, y0, x1, y1, e = ev["x0"], ev["y0"], ev["x1"], ev["y1"], ev["e"]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6 or e <= 1e-9:
            continue
        cos = abs(dx) / length
        if cos < _COS_MIN:
            continue
        width = (e / length) * area / _H_BEAD + cap
        yhalf = (width * 0.5) / cos
        xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
        b0 = int(math.floor(xa / _TOOL_MM))
        b1 = int(math.floor(xb / _TOOL_MM))
        name = ev.get("type") or ""
        if name in _SKIN_TYPES:
            kind = "skin"
        elif "wall" in name.lower():
            kind = "wall"
        else:
            kind = "fill"
        for binn in range(b0, b1 + 1):
            lo = max(xa, binn * _TOOL_MM)
            hi = min(xb, (binn + 1) * _TOOL_MM)
            if hi - lo <= 1e-9:
                continue
            xc = (lo + hi) * 0.5
            if abs(dx) > 1e-12:
                t = (xc - x0) / dx
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            else:
                t = 0.5
            yc = y0 + t * dy
            cols.setdefault((layer, binn), []).append((yc - yhalf, yc + yhalf, yc, kind))
    found: dict[tuple[int, int], tuple[float, float]] = {}
    for key, beads in cols.items():
        pair = _skin_pair(beads)
        if pair is None or pair[4]:
            continue
        plow, phigh, dlow, dhigh, _single = pair
        thickness = dhigh - plow
        gap = dlow - phigh
        if thickness < _HOLLOW_SECTION_MIN or gap <= _HOLLOW_AIR_MIN:
            continue
        if any(k == "fill" and min(hi, dlow) - max(lo, phigh) > 1e-6 for lo, hi, _yc, k in beads):
            continue
        found[key] = (phigh, dlow)
    return found


def _in_hollow(ev: dict, index: dict[tuple[int, int], tuple[float, float]]) -> bool:
    """True when the bead midpoint sits in a hollow column's air, or within 0.5 mm of it."""
    xa, xb = (ev["x0"], ev["x1"]) if ev["x0"] <= ev["x1"] else (ev["x1"], ev["x0"])
    b0 = int(math.floor(xa / _TOOL_MM))
    b1 = int(math.floor(xb / _TOOL_MM))
    y = (ev["y0"] + ev["y1"]) * 0.5
    for binn in range(b0 - 1, b1 + 2):
        hit = index.get((ev["layer"], binn))
        if hit is None:
            continue
        if (hit[0] - 0.5) <= y <= (hit[1] + 0.5):
            return True
    return False


def hollow_column_count(text: str, last: int) -> tuple[int, float]:
    """Hollow columns on layers 1..last, on the 0.25 mm tool with the 0.46 mm envelope."""
    height = _file_bead_height(text)
    if height is None or height <= 0:
        return 0, 0.0
    diam = _DIAMETER.search(text)
    diameter = float(diam.group(1)) if diam else 1.75
    area = math.pi * (diameter * 0.5) ** 2
    index = _hollow_index(_gate_events(text), area, last)
    air = 0.0
    for phigh, dlow in index.values():
        air = max(air, dlow - phigh)
    return len(index), air


def added_sparse_outside_hollow(before: str, after: str) -> int:
    """Added sparse-infill extrusions that are not in a previously hollow >=3 mm column."""
    height = _file_bead_height(before)
    if height is None or height <= 0:
        return 0
    last = _equivalent_last(height)
    diam = _DIAMETER.search(before)
    diameter = float(diam.group(1)) if diam else 1.75
    area = math.pi * (diameter * 0.5) ** 2
    index = _hollow_index(_gate_events(before), area, last)
    base_keys: dict[tuple, int] = {}
    for ev in _gate_events(before):
        key = _segment_key(ev)
        base_keys[key] = base_keys.get(key, 0) + 1
    outside = 0
    for ev in _gate_events(after):
        key = _segment_key(ev)
        if base_keys.get(key, 0) > 0:
            base_keys[key] -= 1
            continue
        name = ev.get("type") or ""
        if name not in _FILL_TYPES:
            continue
        if 1 <= ev["layer"] <= last and _in_hollow(ev, index):
            continue
        outside += 1
    return outside


def gram_footers(text: str) -> tuple[float | None, float | None]:
    """``; filament used [g]`` and ``; total filament used [g]``, if present."""
    used = _FILAMENT_G_LINE.search(text)
    total = _FILAMENT_G_TOTAL.search(text)
    return (
        float(used.group(2)) if used else None,
        float(total.group(2)) if total else None,
    )


def sparse_travel_violations(before: str, after: str) -> dict[str, int]:
    """Added Sparse infill travels that are open-air chords or wall rides.

    A travel the live file already makes is allowed, and so is one that stays
    in a previously hollow >=3 mm column. Counts are zero when every added
    sparse travel is one of those, or a short hop into that air that does not
    run along an outer or overhang wall.
    """
    height = _file_bead_height(before)
    if height is None or height <= 0:
        return {"open_air": 0, "wall_rides": 0}
    last = _equivalent_last(height)
    diam = _DIAMETER.search(before)
    diameter = float(diam.group(1)) if diam else 1.75
    area = math.pi * (diameter * 0.5) ** 2
    events = _gate_events(before)
    index = _hollow_index(events, area, last)
    live = _travel_segments(before)
    walls = _wall_segments(events)
    open_air = wall_rides = 0
    for seg in _sparse_travels(after):
        kind = _travel_violation(seg["layer"], seg["x0"], seg["y0"], seg["x1"], seg["y1"], index, live, walls)
        if kind == "open_air":
            open_air += 1
        elif kind == "wall_ride":
            wall_rides += 1
    return {"open_air": open_air, "wall_rides": wall_rides}


def _layer_groups(lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith(";LAYER_CHANGE") and current:
            groups.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        groups.append(current)
    return groups


def _added_line_numbers(before: str, after: str) -> set[int]:
    """1-based line numbers in ``after`` that are not copied from ``before``."""
    b_lines = before.splitlines()
    a_lines = after.splitlines()
    b_groups = _layer_groups(b_lines)
    a_groups = _layer_groups(a_lines)
    added: set[int] = set()
    if len(b_groups) != len(a_groups):
        pairs = [(0, b_lines, 0, a_lines)]
    else:
        pairs = []
        b_at = a_at = 0
        for b_group, a_group in zip(b_groups, a_groups):
            pairs.append((b_at, b_group, a_at, a_group))
            b_at += len(b_group)
            a_at += len(a_group)
    for _b_at, b_group, a_at, a_group in pairs:
        matcher = difflib.SequenceMatcher(a=b_group, b=a_group, autojunk=False)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                for offset in range(j1, j2):
                    added.add(a_at + offset + 1)
    return added


def _travel_segments(text: str) -> dict[int, list[tuple[float, float, float, float]]]:
    """Non-extruding XY moves, keyed by the same layer numbers as ``_gate_events``."""
    found: dict[int, list[tuple[float, float, float, float]]] = {}
    for seg in _sparse_travels(text, sparse_only=False):
        found.setdefault(seg["layer"], []).append((seg["x0"], seg["y0"], seg["x1"], seg["y1"]))
    return found


def _wall_segments(events: list[dict]) -> dict[int, list[tuple[float, float, float, float]]]:
    found: dict[int, list[tuple[float, float, float, float]]] = {}
    for ev in events:
        if ev.get("type") not in _SKIN_TYPES:
            continue
        found.setdefault(ev["layer"], []).append((ev["x0"], ev["y0"], ev["x1"], ev["y1"]))
    return found


def _sparse_travels(text: str, sparse_only: bool = True) -> list[dict]:
    x = y = e_abs = 0.0
    relative = True
    layer = -1
    typ = None
    out: list[dict] = []
    for ln, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(";"):
            if stripped.startswith(";LAYER:"):
                try:
                    layer = int(stripped.split(":", 1)[1])
                except ValueError:
                    layer += 1
            elif stripped.startswith(";LAYER_CHANGE"):
                layer += 1
            elif stripped.startswith(";TYPE:"):
                name = stripped[6:].strip()
                typ = _TYPE_ALIAS.get(name, name)
            continue
        body = stripped.split(";", 1)[0].strip()
        if not body:
            continue
        words = dict(_WORD.findall(body))
        if "M" in words:
            code = int(float(words["M"]))
            if code == 82:
                relative = False
            elif code == 83:
                relative = True
            continue
        if "G" not in words:
            continue
        g = int(float(words["G"]))
        if g == 92:
            if "E" in words:
                e_abs = float(words["E"])
            continue
        if g not in (0, 1):
            continue
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        de = 0.0
        if "E" in words:
            stated = float(words["E"])
            de = stated if relative else stated - e_abs
            if not relative:
                e_abs = stated
        moved = abs(nx - x) > 1e-9 or abs(ny - y) > 1e-9
        if moved and de <= 1e-9 and (not sparse_only or typ == "Sparse infill"):
            out.append({"line": ln, "layer": layer, "x0": x, "y0": y, "x1": nx, "y1": ny})
        x, y = nx, ny
    return out


def _point_in_air(layer: int, x: float, y: float, index: dict[tuple[int, int], tuple[float, float]]) -> bool:
    binn = int(math.floor(x / _TOOL_MM))
    for b in range(binn - 1, binn + 2):
        hit = index.get((layer, b))
        if hit is None:
            continue
        xa = b * _TOOL_MM
        xb = (b + 1) * _TOOL_MM
        if x < xa - 1e-6 or x > xb + 1e-6:
            continue
        if hit[0] + 1e-6 < y < hit[1] - 1e-6:
            return True
    return False


def _segment_in_air(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    index: dict[tuple[int, int], tuple[float, float]],
) -> bool:
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1e-9:
        return _point_in_air(layer, x0, y0, index)
    steps = max(1, int(math.ceil(length / _TOOL_MM)))
    for step in range(steps + 1):
        t = step / steps
        if not _point_in_air(layer, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, index):
            return False
    return True


def _point_segment_distance(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx, dy = x1 - x0, y1 - y0
    len2 = dx * dx + dy * dy
    if len2 < 1e-12:
        return math.hypot(px - x0, py - y0)
    t = ((px - x0) * dx + (py - y0) * dy) / len2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def _overlap_length(
    ax0: float, ay0: float, ax1: float, ay1: float, bx0: float, by0: float, bx1: float, by1: float
) -> float:
    dx, dy = bx1 - bx0, by1 - by0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0
    ux, uy = dx / length, dy / length

    def project(x: float, y: float) -> float:
        return (x - bx0) * ux + (y - by0) * uy

    a0, a1 = project(ax0, ay0), project(ax1, ay1)
    if a0 > a1:
        a0, a1 = a1, a0
    return max(0.0, min(a1, length) - max(a0, 0.0))


def _on_live_travel(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    live: dict[int, list[tuple[float, float, float, float]]],
) -> bool:
    for sx0, sy0, sx1, sy1 in live.get(layer, ()):
        if _point_segment_distance(x0, y0, sx0, sy0, sx1, sy1) > 0.05:
            continue
        if _point_segment_distance(x1, y1, sx0, sy0, sx1, sy1) > 0.05:
            continue
        dx, dy = x1 - x0, y1 - y0
        sx, sy = sx1 - sx0, sy1 - sy0
        if math.hypot(dx, dy) < 1e-9 or math.hypot(sx, sy) < 1e-9:
            return True
        cross = abs(dx * sy - dy * sx)
        if cross <= 0.05 * math.hypot(dx, dy):
            return True
    return False


def _rides_wall(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> bool:
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 0.2:
        return False
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    for wx0, wy0, wx1, wy1 in walls.get(layer, ()):
        wlen = math.hypot(wx1 - wx0, wy1 - wy0)
        if wlen < 0.2:
            continue
        ux, uy = (wx1 - wx0) / wlen, (wy1 - wy0) / wlen
        if abs(dx * ux + dy * uy) < 0.85:
            continue
        if _point_segment_distance(x0, y0, wx0, wy0, wx1, wy1) > _WALL_RIDE_GAP_MM:
            continue
        if _point_segment_distance(x1, y1, wx0, wy0, wx1, wy1) > _WALL_RIDE_GAP_MM:
            continue
        if _overlap_length(x0, y0, x1, y1, wx0, wy0, wx1, wy1) > _WALL_RIDE_MIN_MM:
            return True
    return False


def _travel_violation(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    index: dict[tuple[int, int], tuple[float, float]],
    live: dict[int, list[tuple[float, float, float, float]]],
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> str | None:
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0.02:
        return None
    if _on_live_travel(layer, x0, y0, x1, y1, live):
        return None
    if _rides_wall(layer, x0, y0, x1, y1, walls):
        return "wall_ride"
    if _segment_in_air(layer, x0, y0, x1, y1, index):
        return None
    in0 = _point_in_air(layer, x0, y0, index)
    in1 = _point_in_air(layer, x1, y1, index)
    if length <= _BRIDGE_MAX_MM and in0 != in1:
        return None
    return "open_air"


def _entry_point(
    layer: int,
    x: float,
    y: float,
    index: dict[tuple[int, int], tuple[float, float]],
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> tuple[float, float] | None:
    if _point_in_air(layer, x, y, index):
        return (x, y)
    best: tuple[float, float] | None = None
    best_d = _BRIDGE_MAX_MM + 1.0
    binn = int(math.floor(x / _TOOL_MM))
    span = int(math.ceil(_BRIDGE_MAX_MM / _TOOL_MM)) + 3
    for b in range(binn - span, binn + span + 1):
        hit = index.get((layer, b))
        if hit is None:
            continue
        phigh, dlow = hit
        if dlow - phigh < 0.08:
            continue
        inset = min(0.05, (dlow - phigh) * 0.15)
        xa, xb = b * _TOOL_MM, (b + 1) * _TOOL_MM
        cx = min(max(x, xa + 1e-3), xb - 1e-3)
        cy = min(max(y, phigh + inset), dlow - inset)
        if not _point_in_air(layer, cx, cy, index):
            continue
        dist = math.hypot(cx - x, cy - y)
        if dist > _BRIDGE_MAX_MM or dist >= best_d:
            continue
        if _rides_wall(layer, x, y, cx, cy, walls):
            continue
        best_d = dist
        best = (cx, cy)
    return best


def _dedupe_path(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for point in path:
        rounded = (round(point[0], 3), round(point[1], 3))
        if not out or math.hypot(rounded[0] - out[-1][0], rounded[1] - out[-1][1]) > 0.02:
            out.append(rounded)
    return out


def _path_clean(
    layer: int,
    path: list[tuple[float, float]],
    index: dict[tuple[int, int], tuple[float, float]],
    live: dict[int, list[tuple[float, float, float, float]]],
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> bool:
    for start, dest in zip(path, path[1:]):
        if _travel_violation(layer, start[0], start[1], dest[0], dest[1], index, live, walls) is not None:
            return False
    return True


def _route_in_air(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    index: dict[tuple[int, int], tuple[float, float]],
) -> list[tuple[float, float]] | None:
    if not (_point_in_air(layer, x0, y0, index) and _point_in_air(layer, x1, y1, index)):
        return None
    if _segment_in_air(layer, x0, y0, x1, y1, index):
        return [(x0, y0), (x1, y1)]
    xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
    b0 = int(math.floor(xa / _TOOL_MM))
    b1 = int(math.floor((xb - 1e-9) / _TOOL_MM))
    lo_y, hi_y = -1e9, 1e9
    for b in range(b0, b1 + 1):
        hit = index.get((layer, b))
        if hit is None:
            return None
        lo_y = max(lo_y, hit[0])
        hi_y = min(hi_y, hit[1])
    if hi_y - lo_y < 0.05:
        return None
    inset = min(0.02, (hi_y - lo_y) * 0.1)
    target = min(max((y0 + y1) * 0.5, lo_y + inset), hi_y - inset)
    if not _segment_in_air(layer, x0, y0, x0, target, index):
        return None
    if not _segment_in_air(layer, x0, target, x1, target, index):
        return None
    if not _segment_in_air(layer, x1, target, x1, y1, index):
        return None
    path = [(x0, y0)]
    if abs(y0 - target) > 1e-6:
        path.append((x0, target))
    if abs(x0 - x1) > 1e-6:
        path.append((x1, target))
    if abs(path[-1][0] - x1) > 1e-6 or abs(path[-1][1] - y1) > 1e-6:
        path.append((x1, y1))
    return path


def _route_bridges(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    index: dict[tuple[int, int], tuple[float, float]],
    live: dict[int, list[tuple[float, float, float, float]]],
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> list[tuple[float, float]] | None:
    start = (x0, y0) if _point_in_air(layer, x0, y0, index) else _entry_point(layer, x0, y0, index, walls)
    goal = (x1, y1) if _point_in_air(layer, x1, y1, index) else _entry_point(layer, x1, y1, index, walls)
    if start is None or goal is None:
        return None
    mid = _route_in_air(layer, start[0], start[1], goal[0], goal[1], index)
    if mid is None:
        return None
    path: list[tuple[float, float]] = []
    if (x0, y0) != start:
        path.append((x0, y0))
    path.extend(mid)
    if (x1, y1) != goal:
        path.append((x1, y1))
    path = _dedupe_path(path)
    if not _path_clean(layer, path, index, live, walls):
        return None
    return path


def _route_via_live(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    index: dict[tuple[int, int], tuple[float, float]],
    live: dict[int, list[tuple[float, float, float, float]]],
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> list[tuple[float, float]] | None:
    """Reach a point that is outside the hollow by riding travels the live file already has."""
    segs = live.get(layer, [])
    if not segs or len(segs) > 4000:
        return None
    nodes: list[tuple[float, float]] = []
    index_of: dict[tuple[float, float], int] = {}

    def add(px: float, py: float) -> int:
        key = (round(px, 3), round(py, 3))
        found = index_of.get(key)
        if found is not None:
            return found
        index_of[key] = len(nodes)
        nodes.append(key)
        return index_of[key]

    start_i = add(x0, y0)
    goal_i = add(x1, y1)
    for sx0, sy0, sx1, sy1 in segs:
        add(sx0, sy0)
        add(sx1, sy1)
        span = math.hypot(sx1 - sx0, sy1 - sy0)
        steps = max(1, int(span))
        for step in range(1, steps):
            t = step / steps
            mx = sx0 + (sx1 - sx0) * t
            my = sy0 + (sy1 - sy0) * t
            if _point_in_air(layer, mx, my, index):
                add(mx, my)
                break
    bridges: list[tuple[int, int]] = []
    for px, py in ((x0, y0), (x1, y1)):
        if _point_in_air(layer, px, py, index):
            continue
        entry = _entry_point(layer, px, py, index, walls)
        if entry is None:
            continue
        bridges.append((add(px, py), add(entry[0], entry[1])))

    adj: list[list[tuple[int, list[tuple[float, float]]]]] = [[] for _ in nodes]

    def link(i: int, j: int, poly: list[tuple[float, float]]) -> None:
        if i != j:
            adj[i].append((j, poly))

    for sx0, sy0, sx1, sy1 in segs:
        dx, dy = sx1 - sx0, sy1 - sy0
        len2 = dx * dx + dy * dy
        on: list[tuple[float, int]] = []
        for i, (px, py) in enumerate(nodes):
            if _point_segment_distance(px, py, sx0, sy0, sx1, sy1) > 0.05:
                continue
            t = 0.0 if len2 < 1e-12 else ((px - sx0) * dx + (py - sy0) * dy) / len2
            on.append((t, i))
        on.sort()
        ordered: list[int] = []
        for _t, i in on:
            if not ordered or ordered[-1] != i:
                ordered.append(i)
        for i, j in zip(ordered, ordered[1:]):
            poly = [nodes[i], nodes[j]]
            if _travel_violation(layer, poly[0][0], poly[0][1], poly[1][0], poly[1][1], index, live, walls) is not None:
                continue
            link(i, j, poly)
            link(j, i, [nodes[j], nodes[i]])
    for i, j in bridges:
        poly = [nodes[i], nodes[j]]
        if _travel_violation(layer, poly[0][0], poly[0][1], poly[1][0], poly[1][1], index, live, walls) is not None:
            continue
        link(i, j, poly)
        link(j, i, [nodes[j], nodes[i]])
    portals = [i for i, point in enumerate(nodes) if _point_in_air(layer, point[0], point[1], index)]
    if len(portals) >= 2:
        hub = portals[0]
        hx, hy = nodes[hub]
        for i in portals[1:]:
            path = _route_in_air(layer, hx, hy, nodes[i][0], nodes[i][1], index)
            if path is None:
                continue
            path = _dedupe_path(path)
            if not _path_clean(layer, path, index, live, walls):
                continue
            link(hub, i, path)
            link(i, hub, list(reversed(path)))

    dist = [1e18] * len(nodes)
    dist[start_i] = 0.0
    prev: list[tuple[int, list[tuple[float, float]]] | None] = [None] * len(nodes)
    heap = [(0.0, start_i)]
    while heap:
        cost, i = heapq.heappop(heap)
        if cost != dist[i]:
            continue
        if i == goal_i:
            break
        for j, poly in adj[i]:
            length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:]))
            nxt = cost + length
            if nxt < dist[j]:
                dist[j] = nxt
                prev[j] = (i, poly)
                heapq.heappush(heap, (nxt, j))
    if dist[goal_i] >= 1e17:
        return None
    pieces: list[list[tuple[float, float]]] = []
    cursor = goal_i
    guard = 0
    while cursor != start_i:
        item = prev[cursor]
        if item is None:
            return None
        parent, poly = item
        pieces.append(poly)
        cursor = parent
        guard += 1
        if guard > len(nodes):
            return None
    path: list[tuple[float, float]] = []
    for poly in reversed(pieces):
        if not path:
            path.extend(poly)
        else:
            path.extend(poly[1:])
    cleaned = _dedupe_path(path)
    if _path_clean(layer, cleaned, index, live, walls):
        return cleaned
    if _path_clean(layer, path, index, live, walls):
        return path
    return None


def _route_confined(
    layer: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    index: dict[tuple[int, int], tuple[float, float]],
    live: dict[int, list[tuple[float, float, float, float]]],
    walls: dict[int, list[tuple[float, float, float, float]]],
) -> list[tuple[float, float]] | None:
    if math.hypot(x1 - x0, y1 - y0) <= 0.02:
        return [(round(x1, 3), round(y1, 3))]
    straight = _dedupe_path([(x0, y0), (x1, y1)])
    if _path_clean(layer, straight, index, live, walls):
        return straight
    if _point_in_air(layer, x0, y0, index) and _point_in_air(layer, x1, y1, index):
        inner = _route_in_air(layer, x0, y0, x1, y1, index)
        if inner is not None:
            inner = _dedupe_path(inner)
            if _path_clean(layer, inner, index, live, walls):
                return inner
    bridged = _route_bridges(layer, x0, y0, x1, y1, index, live, walls)
    if bridged is not None:
        return bridged
    return _route_via_live(layer, x0, y0, x1, y1, index, live, walls)


def _confine_added_sparse(before: str, after: str) -> str:
    """Drop added sparse infill that is not inside a previously hollow >=3 mm column.

    Kept moves keep their own extrusion length. Absolute E after a dropped
    move is rewritten so the next kept move does not inherit the dropped
    plastic. A dropped extrusion is not left as a travel on that path. The
    nozzle is brought to the next kept point on a confined travel, so the
    following extrusion does not stretch across the gap. Lines the bond did
    not add are copied through, including every outer wall, seam, and layer
    change.
    """
    if before == after:
        return after
    height = _file_bead_height(before)
    if height is None or height <= 0:
        return after
    last = _equivalent_last(height)
    diam = _DIAMETER.search(before)
    diameter = float(diam.group(1)) if diam else 1.75
    area = math.pi * (diameter * 0.5) ** 2
    events = _gate_events(before)
    index = _hollow_index(events, area, last)
    live = _travel_segments(before)
    walls = _wall_segments(events)
    base_keys: dict[tuple, int] = {}
    for ev in events:
        key = _segment_key(ev)
        base_keys[key] = base_keys.get(key, 0) + 1
    drop: set[int] = set()
    for ev in _gate_events(after):
        key = _segment_key(ev)
        if base_keys.get(key, 0) > 0:
            base_keys[key] -= 1
            continue
        name = ev.get("type") or ""
        if name not in _FILL_TYPES:
            continue
        if 1 <= ev["layer"] <= last and _in_hollow(ev, index):
            continue
        drop.add(ev["line"])
    if not drop and sparse_travel_violations(before, after) == {"open_air": 0, "wall_rides": 0}:
        return after
    added = _added_line_numbers(before, after)
    lines = after.splitlines(keepends=True)
    e_in = Decimal(0)
    e_out = Decimal(0)
    relative = False
    layer = -1
    typ: str | None = None
    x = y = 0.0
    emitted_x: float | None = None
    emitted_y: float | None = None
    diverged = False
    route_layer = -1
    route_nl = "\n"
    buffer: list[str] = []
    kept: list[str] = []

    def emit_route(dest_x: float, dest_y: float) -> None:
        nonlocal emitted_x, emitted_y
        if emitted_x is None or emitted_y is None:
            # No XY has been written yet, so the nozzle is still at the origin
            # this parser uses. Snapping without a travel would make the next
            # extrusion start at that origin.
            emitted_x, emitted_y = 0.0, 0.0
        if math.hypot(dest_x - emitted_x, dest_y - emitted_y) <= 0.02:
            emitted_x, emitted_y = dest_x, dest_y
            return
        path = _route_confined(route_layer, emitted_x, emitted_y, dest_x, dest_y, index, live, walls)
        if path is None:
            path = _dedupe_path([(emitted_x, emitted_y), (dest_x, dest_y)])
        for px, py in path[1:]:
            kept.append(f"G1 X{px:.3f} Y{py:.3f} F30000{route_nl}")
        emitted_x, emitted_y = dest_x, dest_y

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        nl = _newline(line) or "\n"
        if stripped.startswith(";"):
            if stripped.startswith(";LAYER:"):
                try:
                    layer = int(stripped.split(":", 1)[1])
                except ValueError:
                    layer += 1
            elif stripped.startswith(";LAYER_CHANGE"):
                layer += 1
            elif stripped.startswith(";TYPE:"):
                name = stripped[6:].strip()
                typ = _TYPE_ALIAS.get(name, name)
            if diverged:
                buffer.append(line)
            else:
                kept.append(line)
            continue
        code = _split(line)[0]
        cmd = _command(code)
        if cmd.startswith("M83"):
            relative = True
        elif cmd.startswith("M82"):
            relative = False
        value = _e_value(code)
        if _is_g92(code) and value is not None:
            e_in = value
            e_out = value
            if diverged:
                buffer.append(line)
            else:
                kept.append(line)
            continue
        is_move = cmd.startswith("G0") or cmd.startswith("G1")
        if not is_move:
            if diverged:
                buffer.append(line)
            else:
                kept.append(line)
            continue
        words = dict(_WORD.findall(code))
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        delta: Decimal | None = None
        if value is not None:
            if relative:
                delta = value
            else:
                delta = value - e_in
                e_in = value
        moved = abs(nx - x) > 1e-9 or abs(ny - y) > 1e-9
        extruding = delta is not None and delta > Decimal("0.0000001") and moved
        file_x, file_y = x, y
        x, y = nx, ny
        bad_travel = False
        if moved and not extruding and number in added and typ == "Sparse infill":
            bad_travel = diverged or (
                _travel_violation(layer, file_x, file_y, nx, ny, index, live, walls) is not None
            )
        if number in drop or bad_travel:
            if not diverged:
                diverged = True
                route_layer = layer
                route_nl = nl
            continue
        if not moved:
            if delta is not None:
                e_out = e_out + delta
                if not relative and value is not None and e_out != value:
                    line = _set_e(line, e_out)
            if diverged:
                buffer.append(line)
            else:
                kept.append(line)
            continue
        if diverged or (
            emitted_x is not None
            and emitted_y is not None
            and math.hypot(emitted_x - file_x, emitted_y - file_y) > 0.02
        ):
            if not diverged:
                route_layer = layer
                route_nl = nl
            emit_route(file_x, file_y)
            kept.extend(buffer)
            buffer.clear()
            diverged = False
        elif emitted_x is None:
            emitted_x, emitted_y = file_x, file_y
        if delta is not None:
            e_out = e_out + delta
            if not relative and value is not None and e_out != value:
                line = _set_e(line, e_out)
        kept.append(line)
        emitted_x, emitted_y = nx, ny
    if buffer:
        kept.extend(buffer)
    return "".join(kept)


def _deposited_mm_scan(text: str) -> Decimal:
    e_abs = Decimal(0)
    relative = False
    total = Decimal(0)
    for line in text.splitlines():
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        cmd = code.split(" ", 1)[0].upper()
        if cmd.startswith("M83"):
            relative = True
            continue
        if cmd.startswith("M82"):
            relative = False
            continue
        if cmd.startswith("G92"):
            value = _e_value(code)
            if value is not None:
                e_abs = value
            continue
        if not (cmd.startswith("G0") or cmd.startswith("G1")):
            continue
        value = _e_value(code)
        delta = Decimal(0)
        if value is not None:
            if relative:
                delta = value
            else:
                delta = value - e_abs
                e_abs = value
        moved_xy = ("X" in code.split(";", 1)[0]) or ("Y" in code.split(";", 1)[0])
        if moved_xy and delta > 0:
            total += delta
    return total


def _with_filament(before: str, after: str) -> str:
    added = _deposited_mm_scan(after) - _deposited_mm_scan(before)
    if added == 0:
        return after
    diam = _DIAMETER.search(before)
    dens = _DENSITY.search(before)
    diameter = Decimal(diam.group(1)) if diam else Decimal("1.75")
    density = Decimal(dens.group(1)) if dens else Decimal("1.2")
    area = Decimal(str(math.pi)) * (diameter / 2) ** 2
    added_cm3 = added * area / Decimal(1000)
    added_g = added_cm3 * density

    def add_to(match: re.Match[str], extra: Decimal, places: str) -> str:
        updated = (Decimal(match.group(2)) + extra).quantize(Decimal(places))
        return match.group(1) + format(updated, "f")

    after = _FILAMENT_MM.sub(lambda m: add_to(m, added, "0.01"), after, count=1)
    after = _FILAMENT_CM3.sub(lambda m: add_to(m, added_cm3, "0.01"), after, count=1)
    # One value for both gram lines. Adding the extra to each line separately
    # can leave `; filament used [g]` and `; total filament used [g]` apart
    # when their live values were not already the same hundredth.
    used = _FILAMENT_G_LINE.search(after)
    total = _FILAMENT_G_TOTAL.search(after)
    base = Decimal(used.group(2)) if used else (Decimal(total.group(2)) if total else None)
    if base is None:
        return after
    shared = format((base + added_g).quantize(Decimal("0.01")), "f")

    def put(match: re.Match[str]) -> str:
        return match.group(1) + shared

    if used:
        after = _FILAMENT_G_LINE.sub(put, after, count=1)
    if total:
        after = _FILAMENT_G_TOTAL.sub(put, after, count=1)
    return after
