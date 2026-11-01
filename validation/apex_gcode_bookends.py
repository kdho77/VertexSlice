#!/usr/bin/env python3
"""Cura reference start/end g-code bookends for Vertex Apex V2 (PRINT_START/PRINT_END)."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REF = REPO / "validation/reference_gcode/apex_cura_insole_ref.gcode"

# Orca machine preset (placeholders for temps; retraction lengths match Cura ref).
MACHINE_START_GCODE = """PRINT_START
M104 S[nozzle_temperature_initial_layer]
M105
M109 S[nozzle_temperature_initial_layer]
M82 ;absolute extrusion mode

G92 E0
G92 E0
G1 F1800 E-1.5
M106 P1 S64
M106 S127.5
M204 S3000
"""

MACHINE_END_GCODE = """M107
G92 E0 ; Set Extruder to zero
G1 E-1 ; Retract 1mm
G90
G91
G92 Z0 ; Set Belt to zero again
G28 Y ; Home Y
G28 X ; Home X
M104 S0 ; Extruder heater off
M140 S0 ; Heated bed heater off
G92 Z0

M82 ;absolute extrusion mode
M104 S0
;End of Gcode
PRINT_END
"""


def _executable_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith(";"):
            continue
        out.append(s)
    return out


def _normalize_temp_line(line: str) -> str:
    return re.sub(r"\bS\d+\b", "S<TEMP>", line)


def normalize_bookend_line(line: str) -> str:
    """Normalize for comparison (temps, whitespace)."""
    s = line.strip()
    s = _normalize_temp_line(s)
    return s


def _start_bookend_stop(s: str) -> bool:
    """End of Cura/Orca PRINT_START block (before layer-0 travel or fan-off injection)."""
    if re.match(r"^M204\s+S3000\b", s):
        return True
    if s.startswith("G0 ") and "Z" in s:
        return True
    if s.startswith("G1 ") and re.search(r"\bZ", s):
        return True
    return False


def reference_start_bookend(path: Path | None = None) -> list[str]:
    """Executable lines from PRINT_START through pre-layer travel (Cura ref)."""
    path = path or REF
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    idx = next(i for i, l in enumerate(lines) if l.strip() == "PRINT_START")
    exec_lines: list[str] = ["PRINT_START"]
    for l in lines[idx + 1 :]:
        s = l.strip()
        if not s or s.startswith(";"):
            continue
        exec_lines.append(s)
        if _start_bookend_stop(s):
            break
    return exec_lines


def reference_end_bookend(path: Path | None = None) -> list[str]:
    path = path or REF
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    pe = max(i for i, l in enumerate(lines) if l.strip() == "PRINT_END")
    for i in range(pe - 1, -1, -1):
        if lines[i].strip() == "M107" and i + 1 < len(lines) and "Set Extruder to zero" in lines[i + 1]:
            block = [l.strip() for l in lines[i : pe + 1] if l.strip()]
            return block
    raise ValueError("reference end bookend not found")


def extract_sliced_start_bookend(gcode_text: str) -> list[str]:
    """Match reference_start_bookend: PRINT_START through M204 S3000 (or first Z move)."""
    lines = gcode_text.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip() == "PRINT_START"), None)
    if idx is None:
        raise ValueError("sliced gcode missing PRINT_START")
    exec_lines: list[str] = ["PRINT_START"]
    for l in lines[idx + 1 :]:
        s = l.strip()
        if not s or s.startswith(";"):
            continue
        exec_lines.append(s)
        if _start_bookend_stop(s):
            break
    return exec_lines


def extract_sliced_end_bookend(gcode_text: str) -> list[str]:
    lines = gcode_text.splitlines()
    pe = max(i for i, l in enumerate(lines) if l.strip() == "PRINT_END")
    for i in range(pe - 1, -1, -1):
        if lines[i].strip() == "M107" and i + 1 < len(lines) and "Set Extruder to zero" in lines[i + 1]:
            return [l.strip() for l in lines[i : pe + 1] if l.strip()]
    raise ValueError("sliced gcode end bookend not found")


def compare_bookends(actual: list[str], expected: list[str]) -> list[str]:
    fails: list[str] = []
    if len(actual) != len(expected):
        fails.append(f"bookend line count {len(actual)} vs ref {len(expected)}")
    for i, (a, e) in enumerate(zip(actual, expected)):
        na, ne = normalize_bookend_line(a), normalize_bookend_line(e)
        if na != ne:
            fails.append(f"line {i}: got {a!r} expected {e!r}")
    if len(actual) > len(expected):
        for extra in actual[len(expected) :]:
            fails.append(f"extra line {extra!r}")
    return fails
