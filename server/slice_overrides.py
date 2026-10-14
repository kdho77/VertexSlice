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


def apply_process_overrides(process_json: Path, overrides: dict[str, Any]) -> None:
    if not overrides:
        return
    data = json.loads(process_json.read_text())
    if "wall_loops" in overrides:
        data["wall_loops"] = str(overrides["wall_loops"])
    if "sparse_infill_density" in overrides:
        data["sparse_infill_density"] = overrides["sparse_infill_density"]
    if "print_direction" in overrides:
        data["print_direction"] = overrides["print_direction"]
    process_json.write_text(json.dumps(data, indent=4) + "\n")
