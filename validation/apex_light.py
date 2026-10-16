#!/usr/bin/env python3
"""ApexLight (foaming TPU / Cura VERTEX_APEX_LITE_90A) slice constants.

Process/machine overrides are applied at slice time so ApexCore/Forge/Bounce
presets stay byte-identical. E/mm feature-flow ratios compensate Orca's true
layer height (0.65·sin45 ≈ 0.46) so extruded volume matches Kendon's Cura
file (Cura uses belt step 0.65 as the extrusion height).
"""
from __future__ import annotations

import math

FILAMENT_DIAMETER_MM = 1.75
FILAMENT_CS_MM2 = math.pi * (FILAMENT_DIAMETER_MM / 2.0) ** 2

CURA_NAME = "VERTEX_APEX_LITE_90A"
BELT_STEP_MM = 0.65
LAYER_HEIGHT_MM = round(BELT_STEP_MM * math.sin(math.radians(45.0)), 2)  # 0.46
FLOW_RATIO = 0.72
NOZZLE_C = 260
BED_C = 0
DEFAULT_WALL_LOOPS = 2
LINE_WIDTH_MM = 1.0

OUTER_WALL_MM_S = 26.0
INNER_WALL_MM_S = 42.0
INFILL_MM_S = 26.0
FIRST_LAYER_MM_S = 10.0
BELT_WALL_MM_S = 20.0
TRAVEL_MM_S = 212.0
PRINT_ACCEL = 8000
TRAVEL_ACCEL = 10000

FAN_MAX_PERCENT = 35
FAN_CLOSE_FIRST_LAYERS = 1
FAN_FULL_SPEED_LAYER = 7  # layer 0 off, ~6%/layer to 35% by layer 6
AUX_FAN_PERCENT = 18

# Measured from Kendon's Cura ApexLight file (1.75 mm filament).
E_PER_MM_OUTER = 0.1919
E_PER_MM_INNER = 0.1787  # re-measured from Kendon Cura ref (was 0.1757)
E_PER_MM_INFILL = 0.2054
E_PER_MM_TOL = 0.03

# Cura ApexLight layer-0 min extrusion Y is 0.016 (not the 0.07 Core/Forge/Bounce band).
TARGET_FIRST_LAYER_Y_MM = 0.016
FIRST_LAYER_Y_TOL_MM = 0.03

# PRINT_START / PRINT_END call lines match the Cura file (bare macros).
# Shared machine_end_gcode already ends with PRINT_END; only start differs.
MACHINE_START_GCODE = """PRINT_START
M104 S[nozzle_temperature_initial_layer]
M105
M109 S[nozzle_temperature_initial_layer]
M82 ;absolute extrusion mode

M83 ;relative extrusion mode
"""


def e_per_mm(
    flow_ratio: float,
    line_width_mm: float,
    layer_height_mm: float,
    feature_flow_ratio: float = 1.0,
) -> float:
    return flow_ratio * feature_flow_ratio * line_width_mm * layer_height_mm / FILAMENT_CS_MM2


def feature_flow_for_target(target_e_per_mm: float, *, width_mm: float = LINE_WIDTH_MM) -> float:
    """Feature flow so Orca volume at LAYER_HEIGHT_MM matches a Cura E/mm target."""
    return target_e_per_mm * FILAMENT_CS_MM2 / (FLOW_RATIO * width_mm * LAYER_HEIGHT_MM)


# Orca widens belt-inclined extrusions per move (;WIDTH 1.0-1.4 on a 1.0 nominal
# line), so measured E/mm runs above the nominal-width estimate. These factors are
# measured E/mm(nominal) / Cura target on the OrthoCAD medium sample (local proot
# run of the GHCR :headless image); filament_flow_ratio stays 0.72.
OUTER_WALL_E_CAL = 1.020  # classic walls (was 0.961 under Arachne)
INNER_WALL_E_CAL = 1.007  # classic walls (was 0.962 under Arachne)
INFILL_E_CAL = 0.930

OUTER_WALL_FLOW_RATIO = feature_flow_for_target(E_PER_MM_OUTER) * OUTER_WALL_E_CAL
INNER_WALL_FLOW_RATIO = feature_flow_for_target(E_PER_MM_INNER) * INNER_WALL_E_CAL
INFILL_FLOW_RATIO = feature_flow_for_target(E_PER_MM_INFILL) * INFILL_E_CAL

PROCESS_OVERRIDES: dict[str, str] = {
    "layer_height": f"{LAYER_HEIGHT_MM:.2f}",
    "first_layer_height": f"{LAYER_HEIGHT_MM:.2f}",
    "initial_layer_print_height": f"{LAYER_HEIGHT_MM:.2f}",
    "wall_loops": str(DEFAULT_WALL_LOOPS),
    "outer_wall_speed": str(int(OUTER_WALL_MM_S)),
    "inner_wall_speed": str(int(INNER_WALL_MM_S)),
    "sparse_infill_speed": str(int(INFILL_MM_S)),
    "internal_solid_infill_speed": str(int(INFILL_MM_S)),
    "top_surface_speed": str(int(INFILL_MM_S)),
    "gap_infill_speed": str(int(INFILL_MM_S)),
    "initial_layer_speed": str(int(FIRST_LAYER_MM_S)),
    "travel_speed": str(int(TRAVEL_MM_S)),
    "default_acceleration": str(PRINT_ACCEL),
    "outer_wall_acceleration": str(PRINT_ACCEL),
    "inner_wall_acceleration": str(PRINT_ACCEL),
    "sparse_infill_acceleration": str(PRINT_ACCEL),
    "internal_solid_infill_acceleration": str(PRINT_ACCEL),
    "top_surface_acceleration": str(PRINT_ACCEL),
    "initial_layer_acceleration": str(PRINT_ACCEL),
    "travel_acceleration": str(TRAVEL_ACCEL),
    "set_other_flow_ratios": "1",
    "outer_wall_flow_ratio": f"{OUTER_WALL_FLOW_RATIO:.4f}",
    "inner_wall_flow_ratio": f"{INNER_WALL_FLOW_RATIO:.4f}",
    "sparse_infill_flow_ratio": f"{INFILL_FLOW_RATIO:.4f}",
    "first_layer_flow_ratio": f"{OUTER_WALL_FLOW_RATIO:.4f}",
    "belt_wall_enabled": "1",
    "sparse_infill_pattern": "gyroid",
    # Zit/ooze fix (TPU expert, 2026-10-16): aligned seam, combed travel, loop wipe.
    "seam_position": "aligned",
    "seam_gap": "0",
    "reduce_crossing_wall": "1",
    "max_travel_detour_distance": "0",
    "wipe_on_loops": "1",
    "wipe_speed": "60%",
    # Classic walls: closed loops only (Arachne emits open variable-width strips at the top).
    "wall_generator": "classic",
    "filter_out_gap_fill": "1",  # keep gap fill: without it classic walls leave 0.6-3 mm voids
}


MACHINE_OVERRIDES: dict = {
    "use_relative_e_distances": "1",
    # Orca refuses relative E without a per-layer reset (exit 205).
    "before_layer_change_gcode": ";BEFORE_LAYER_CHANGE\nG92 E0\n;[layer_z]\n",
    "retraction_length": ["0"],
    "wipe": ["1"],
    "wipe_distance": ["0.7"],
    "retract_before_wipe": ["0%"],
    "retraction_minimum_travel": ["2"],
    "machine_start_gcode": MACHINE_START_GCODE,
    "machine_max_acceleration_travel": [str(TRAVEL_ACCEL)],
    "machine_max_acceleration_extruding": [str(TRAVEL_ACCEL)],
}
