#!/usr/bin/env python3
"""ApexCore (Cura VERTEX_APEX_CORE_95A) slice overrides to match Kendon's Cura file.

Applied at slice time only for material=apex_core. Cura prints 1 wall (outer
only) + gyroid fill, outer 35 mm/s, fill 60 mm/s, constant fan 50 % (aux 25 %)
from layer 0, no overhang slow-down or overhang fan bursts. Flow ratios bring
Orca's measured E/mm to Cura's (outer 0.1859, fill 0.1796).

The Cura belt-wall slow-down (20 mm/s) is intentionally NOT applied here
(Kendon testing the faster belt wall; decision pending).
"""
from __future__ import annotations

DEFAULT_WALL_LOOPS = 1
TARGET_FIRST_LAYER_Y_MM = 0.066

E_PER_MM_OUTER = 0.1859
E_PER_MM_INFILL = 0.1796

# Measured-vs-target calibration on the OrthoCAD medium sample (local proot run).
OUTER_WALL_FLOW_RATIO = 0.9783
INFILL_FLOW_RATIO = 0.9142

PROCESS_OVERRIDES: dict[str, str] = {
    "wall_loops": str(DEFAULT_WALL_LOOPS),
    "enable_overhang_speed": "0",
    "bridge_speed": "35",
    "enable_overhang_bridge_fan": "0",
    "overhang_fan_speed": "50",
    "set_other_flow_ratios": "1",
    "outer_wall_flow_ratio": f"{OUTER_WALL_FLOW_RATIO:.4f}",
    "first_layer_flow_ratio": f"{OUTER_WALL_FLOW_RATIO:.4f}",
    "sparse_infill_flow_ratio": f"{INFILL_FLOW_RATIO:.4f}",
    "internal_solid_infill_flow_ratio": f"{INFILL_FLOW_RATIO:.4f}",
}


# Filament-scope keys (per-extruder arrays in Orca): no layer-time slow-down
# (Cura holds 35/60 mm/s), overhang fan = base fan (no 100 % bursts).
FILAMENT_OVERRIDES: dict = {
    "slow_down_for_layer_cooling": ["0"],
    "enable_overhang_bridge_fan": ["0"],
    "overhang_fan_speed": ["50"],
}
