"""Per-material Apex Belt V2 nozzle-height offset, applied to finished G-code.

Belt Y is nozzle height. A larger Y is farther from the belt; a smaller Y is
closer. The offsets below are the physical change versus the G-code this slicer
already emits (the saved ApexCore zero, about +0.5 mm, is already in that file):

    apex_light   +0.20 mm  nozzle higher (farther from the belt)
    apex_forge   -0.20 mm  nozzle lower  (closer to the belt)
    apex_bounce  -0.20 mm  nozzle lower
    apex_core     0.00 mm  the input string is returned unchanged

Sign. This pass adds the offset to every absolute motion Y (G0–G3). The sign
is the physical sign:

    emitted_Y = commanded_Y + offset_mm

So ``Y0.000`` becomes ``Y0.200`` for ApexLight and ``Y-0.200`` for ApexForge
and ApexBounce. Nothing clamps the result back to ``Y >= 0``. The C++ belt
transform does clamp ``Y_mach >= 0``, which is why a negative ``y_mach_offset``
cannot be used: the first-layer moves would be crushed to ``Y0.000`` and the
layer would be distorted. Running here, after the forefoot hollow fill, is
past that clamp, and the beads the fill inserts are shifted with everything
else.

G92 is the opposite sign. ``G92 Y<val>`` means "the current physical position
is now ``<val>``". With the nozzle at logical Y=0, ``G92 Y-0.200`` makes the
next ``G1 Y0.000`` travel +0.200 mm (higher). A fixed ``G92`` is also wrong
on this machine: ``PRINT_START`` in ``validation/printer_config/printer.cfg``
homes and then ``G1 Y20``, so the live coordinate at the end of the macro is
not 0. This pass does not emit ``G92 Y``.

Cancel safety. The shift is baked into the coordinates. No ``SET_GCODE_OFFSET``
is emitted, so a mid-print cancel cannot leave a modal offset for the next
job. Klipper's ``SET_GCODE_OFFSET`` writes ``homing_position``, and
``G28`` restores ``base_position`` from ``homing_position``
(``klippy/extras/gcode_move.py`` ``_handle_homing_home_axes``), so the offset
survives ``PRINT_START``'s ``G28``, the end-gcode ``G28 Y``, and
``CANCEL_PRINT``. Clearing it on the following job would require a
``SET_GCODE_OFFSET Y=0`` in every file, including ApexCore. ApexCore is not
modified. ``G92`` would be cleared by ``G28``, but it has the opposite sign
and depends on the live position. The shared machine start gcode is only
``PRINT_START`` plus heat, ``G92 E0``, and a retract — no Y offset command.

Limits. ``validation/printer_config/printer.cfg`` sets ``[stepper_y]
position_min: -5`` (X is the axis with ``position_min: 0``). ``Y-0.200`` is
inside that limit. Klipper checks the toolhead position after ``base_position``
is applied, so ``SET_GCODE_OFFSET Y=-0.200`` followed by ``G1 Y0`` is the same
physical target as ``G1 Y-0.200`` and hits the same limit.

A second call sees the marker and returns the same string.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

# Physical nozzle-height change, millimetres. Absent materials, including
# apex_core, are 0 and are not rewritten.
MATERIAL_Y_OFFSET_MM: dict[str, float] = {
    "apex_light": 0.20,
    "apex_forge": -0.20,
    "apex_bounce": -0.20,
}

MARKER_PREFIX = "; MATERIAL_Y_OFFSET "

_Y_WORD = re.compile(
    r"(?<![A-Za-z])Y([+-]?(?:\d+\.\d+|\d+\.|\.\d+|\d+))(?![0-9.eE])"
)
_GCODE = re.compile(r"(?<![0-9A-Za-z.])G(\d+)(?![0-9.])")
_OFFSETS: dict[str, Decimal] = {
    key: Decimal(str(value)) for key, value in MATERIAL_Y_OFFSET_MM.items()
}


def material_y_offset_mm(material: str | None) -> float:
    """Physical Y offset for ``material``. ApexCore and unknown ids are 0."""
    key = _material_key(material)
    if key == "" or key == "apex_core":
        return 0.0
    return float(MATERIAL_Y_OFFSET_MM.get(key, 0.0))


def apply_material_y_offset(gcode: str, material: str | None) -> str:
    """Shift absolute motion Y by this material's nozzle-height offset.

    ApexCore, an unknown material, and a zero offset return ``gcode`` itself.
    A file that already carries this pass's marker is returned unchanged.
    """
    key = _material_key(material)
    if key == "" or key == "apex_core":
        return gcode
    offset = _OFFSETS.get(key)
    if offset is None or offset == 0:
        return gcode
    if MARKER_PREFIX in gcode:
        return gcode

    absolute = True
    pieces: list[str] = []
    for line in gcode.splitlines(keepends=True):
        body, ending = _split_ending(line)
        rewritten, absolute = _rewrite_line(body, offset, absolute)
        pieces.append(rewritten + ending)
    text = "".join(pieces)
    if text and not text.endswith(("\n", "\r")):
        text += "\n"
    text += _marker(key, offset) + "\n"
    return text


def _material_key(material: str | None) -> str:
    if material is None:
        return ""
    return str(material).strip().lower()


def _marker(material: str, offset: Decimal) -> str:
    return (
        f"{MARKER_PREFIX}material={material} offset_mm={offset:+.3f} "
        "baked_into=absolute_Y modal=none"
    )


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _rewrite_line(body: str, offset: Decimal, absolute: bool) -> tuple[str, bool]:
    stripped = body.lstrip()
    if stripped == "" or stripped.startswith(";"):
        return body, absolute
    code, sep, comment = _split_comment(body)
    absolute, is_move = _update_modal(code, absolute)
    if not absolute or not is_move:
        return body, absolute
    rewritten = _Y_WORD.sub(lambda match: "Y" + _format_y(match.group(1), offset), code)
    return rewritten + sep + comment, absolute


def _split_comment(body: str) -> tuple[str, str, str]:
    idx = body.find(";")
    if idx < 0:
        return body, "", ""
    return body[:idx], ";", body[idx + 1 :]


def _update_modal(code: str, absolute: bool) -> tuple[bool, bool]:
    is_move = False
    for match in _GCODE.finditer(code):
        number = int(match.group(1))
        if number == 90:
            absolute = True
        elif number == 91:
            absolute = False
        elif number in (0, 1, 2, 3):
            is_move = True
    return absolute, is_move


def _format_y(token: str, offset: Decimal) -> str:
    places = 0
    if "." in token:
        places = len(token.split(".", 1)[1])
    places = max(places, 3)
    shifted = (Decimal(token) + offset).quantize(
        Decimal(10) ** -places, rounding=ROUND_HALF_UP
    )
    return f"{shifted:.{places}f}"
