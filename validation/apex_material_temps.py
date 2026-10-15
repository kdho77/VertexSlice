"""Apex V2 material temps vs the current PRINT_START call line.

Current machine_start_gcode (fdm_vertex_apex_common):

    PRINT_START
    M104 S[nozzle_temperature_initial_layer]
    M105
    M109 S[nozzle_temperature_initial_layer]
    ...

No BED=/EXTRUDER= macro params. Nozzle temp is the placeholder on the M104/M109
lines after PRINT_START. Bed is not in that block: Marlin flavor emits
M140/M190 from filament plate temps when start G-code has no M140/M190.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"


def _first_int(value) -> int:
    if isinstance(value, list):
        value = value[0] if value else 0
    return int(str(value).strip())


def filament_temps(flat: dict) -> tuple[int, int]:
    nozzle = _first_int(flat.get("nozzle_temperature_initial_layer") or flat.get("nozzle_temperature"))
    bed_keys = (
        "hot_plate_temp_initial_layer",
        "cool_plate_temp_initial_layer",
        "eng_plate_temp_initial_layer",
        "textured_plate_temp_initial_layer",
    )
    beds = [_first_int(flat[k]) for k in bed_keys if k in flat]
    bed = beds[0] if beds else 0
    if beds and any(b != bed for b in beds):
        raise ValueError(f"inconsistent plate temps: {list(zip(bed_keys, beds))}")
    return nozzle, bed


def substitute_print_start(machine_start: str, nozzle: int, bed: int) -> str:
    """Apply the same placeholders the current PRINT_START block uses."""
    out = machine_start
    out = out.replace("[nozzle_temperature_initial_layer]", str(nozzle))
    out = out.replace("[bed_temperature_initial_layer]", str(bed))
    out = out.replace("[first_layer_temperature]", str(nozzle))
    out = out.replace("[first_layer_bed_temperature]", str(bed))
    return out


def render_expected_start_gcode(machine_start: str, nozzle: int, bed: int, gcode_flavor: str) -> str:
    """PRINT_START block plus Marlin auto bed line (if start gcode has no M140/M190)."""
    body = substitute_print_start(machine_start, nozzle, bed)
    prefix = ""
    flavor = (gcode_flavor or "").lower()
    has_bed_m = bool(re.search(r"\bM14[0]\b|\bM190\b", body))
    if flavor != "klipper" and not has_bed_m:
        prefix = f"M190 S{bed} ; set bed temperature and wait for it to be reached\n"
    return prefix + body


def assert_material_temps_in_gcode(gcode_text: str, nozzle: int, bed: int) -> list[str]:
    """Temps must appear on the PRINT_START call path and M104/M140 if present."""
    fails: list[str] = []
    lines = gcode_text.splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip() == "PRINT_START" or l.strip().startswith("PRINT_START ")), None)
    if idx is None:
        return ["gcode missing PRINT_START"]
    start_line = lines[idx].strip()
    if start_line != "PRINT_START":
        fails.append(f"PRINT_START call line changed: {start_line!r} (expected bare PRINT_START)")
    window = "\n".join(lines[max(0, idx - 8) : idx + 20])
    if not re.search(rf"\bM10[49]\s+S{nozzle}\b", window):
        fails.append(f"missing M104/M109 S{nozzle} near PRINT_START")
    if f"S{nozzle}" not in window:
        fails.append(f"nozzle {nozzle} not in PRINT_START window")
    m140s = re.findall(r"\bM14[0]\s+S(-?\d+)\b", gcode_text)
    m190s = re.findall(r"\bM190\s+S(-?\d+)\b", gcode_text)
    bed_cmds = [int(x) for x in m140s + m190s]
    if bed > 0:
        if bed not in bed_cmds:
            fails.append(f"bed {bed} missing from M140/M190 (found {bed_cmds[:6]})")
    elif bed_cmds:
        nonzero = [b for b in bed_cmds if b != 0]
        if nonzero:
            fails.append(f"bed off but M140/M190 set {nonzero}")
    return fails


def load_flat_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
