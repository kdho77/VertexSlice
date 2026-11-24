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
_FILAMENT_G = re.compile(r"^(;\s*(?:total )?filament used \[g\] = )([0-9.]+)\s*$", re.M)
_E_TOKEN = re.compile(r"E([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_TAG = re.compile(r" ;VSABS:(\d+)")
_LOCK = threading.Lock()


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
        return _with_filament(text, bonded)
    relative_lines = _to_relative(original_lines)
    relative_text = "".join(relative_lines)
    bonded = _bond(relative_text, height, window)
    if bonded == relative_text:
        return text
    restored = _restore_absolute(original_lines, relative_lines, bonded.splitlines(keepends=True))
    return _with_filament(text, restored)


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
    after = _FILAMENT_G.sub(lambda m: add_to(m, added_g, "0.01"), after)
    return after
