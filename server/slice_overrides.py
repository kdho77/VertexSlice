"""Optional per-request overrides for Apex insole process settings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from validation.apex_insole_tunables import (
    format_infill_density,
    validate_infill_density_percent,
    validate_print_direction,
    validate_wall_loops,
)


def parse_slice_overrides(
    wall_loops: str | int | None,
    infill_density: str | float | int | None,
    print_direction: str | None = None,
) -> dict[str, Any]:
    """Return normalized overrides (empty if none). Raises ValueError on bad input."""
    out: dict[str, Any] = {}
    if wall_loops is not None and str(wall_loops).strip() != "":
        out["wall_loops"] = validate_wall_loops(int(wall_loops))
    if infill_density is not None and str(infill_density).strip() != "":
        raw = str(infill_density).strip().rstrip("%")
        out["sparse_infill_density"] = format_infill_density(int(round(float(raw))))
    if print_direction is not None and str(print_direction).strip() != "":
        out["print_direction"] = validate_print_direction(print_direction)
    return out


def apply_json_overrides(preset_json: Path, overrides: dict[str, Any]) -> None:
    """Patch a flattened machine/process JSON. User keys win when merged by caller."""
    if not overrides:
        return
    data = json.loads(preset_json.read_text())
    for key, value in overrides.items():
        if key == "wall_loops":
            data[key] = str(int(value))
        elif key == "sparse_infill_density":
            data[key] = value if str(value).endswith("%") else format_infill_density(value)
        elif key == "print_direction":
            data[key] = value
        elif isinstance(value, (list, dict)):
            data[key] = value
        elif isinstance(value, bool):
            data[key] = "1" if value else "0"
        else:
            data[key] = value if isinstance(value, str) else str(value)
    preset_json.write_text(json.dumps(data, indent=4) + "\n")


def apply_process_overrides(process_json: Path, overrides: dict[str, Any]) -> None:
    apply_json_overrides(process_json, overrides)


def apply_machine_overrides(machine_json: Path, overrides: dict[str, Any]) -> None:
    apply_json_overrides(machine_json, overrides)
