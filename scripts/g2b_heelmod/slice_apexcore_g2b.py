#!/usr/bin/env python3
"""ApexCore file with the G2b CONFIG_BLOCK and ApexCore filament swapped in.

No heelmod: no layer-range flow, min_bead, ensure_all, min-time, and no
global infill_wall_overlap or minimum_sparse_infill_area edits. Layer height
is the 0.42 mm spacing ApexCore G2b-equivalents use on this branch.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = ["/tmp/g2b/root/app/validation", "/tmp/g2b/src/validation", "/tmp/g2b/src", str(HERE)]

os.environ.setdefault("ORCA_SLICER_BIN", "/tmp/g2b/root/app/bin/orca-slicer")
os.environ.setdefault("ORCA_RESOURCES", "/tmp/g2b/root/app/resources")
lib = "/tmp/g2b/root/usr/lib/x86_64-linux-gnu"
os.environ["LD_LIBRARY_PATH"] = lib + (":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else "")

from slice_g2b_heelmod import (  # noqa: E402
    Y_MACH_OFFSET_LIGHT,
    assert_g2b_identity,
    feature_flow,
    flatten,
    overlay_g2b,
    slice_model,
)

# Shear-corrected rates at volumetric ratio 1.0008 (0.18 mm target, 0.42 mm
# layer, 1 mm width, filament flow 1.03) on this binary. Each feature is
# scaled so the measured E/mm is the target. Gap fill is not the wall ratio.
OUTER_AT_VOL = 0.1843
SPARSE_AT_VOL = 0.1638
SOLID_AT_VOL = 0.1778
LAYER_H = 0.42
GAP_E_PER_MM = 0.16


def _apply_core_flows(process: dict, filament: dict) -> None:
    fil = float(filament.get("filament_flow_ratio", ["1.03"])[0])
    volumetric = feature_flow(0.18, LAYER_H, 1.0, fil)
    outer = volumetric * (0.18 / OUTER_AT_VOL)
    sparse = volumetric * (0.18 / SPARSE_AT_VOL)
    solid = volumetric * (0.18 / SOLID_AT_VOL)
    gap = sparse * (GAP_E_PER_MM / 0.18)
    process["outer_wall_flow_ratio"] = f"{outer:.4f}"
    process["inner_wall_flow_ratio"] = f"{outer:.4f}"
    process["sparse_infill_flow_ratio"] = f"{sparse:.4f}"
    process["internal_solid_infill_flow_ratio"] = f"{solid:.4f}"
    process["gap_fill_flow_ratio"] = f"{gap:.4f}"
    process["first_layer_flow_ratio"] = f"{outer:.4f}"
    print(
        "flows",
        f"fil={fil}",
        f"layer={LAYER_H}",
        f"outer={outer:.4f}",
        f"inner={outer:.4f}",
        f"sparse={sparse:.4f}",
        f"solid={solid:.4f}",
        f"gap={gap:.4f}",
    )


def prepare(work: Path, material: str) -> tuple[dict, dict, dict, float]:
    machine = flatten("machine/Vertex Apex V2 1.0 nozzle.json", work)
    process = flatten("process/0.42mm TPU Insole Medium @Vertex Apex V2.json", work)
    if material == "light":
        filament = flatten("filament/ApexLight.json", work)
        overlay_g2b(machine, process, filament)
        assert_g2b_identity(process, filament, machine)
        return machine, process, filament, Y_MACH_OFFSET_LIGHT
    if material != "core":
        raise SystemExit("material must be light or core")
    filament = flatten("filament/ApexCore.json", work)
    saved = json.loads(json.dumps(filament))
    overlay_g2b(machine, process, filament)
    assert_g2b_identity(process, filament, machine)
    # Filament block is ApexCore. Process/machine stay on the G2b CONFIG_BLOCK.
    filament = saved
    process["layer_height"] = f"{LAYER_H}"
    process["initial_layer_print_height"] = f"{LAYER_H}"
    if "first_layer_height" in process:
        process["first_layer_height"] = f"{LAYER_H}"
    _apply_core_flows(process, filament)
    # Probed on this mesh at 0.42 mm: offset 20 -> first extrusion Y 21.066,
    # so -1.0 lands the first bead at Y 0.066.
    y_offset = float(os.environ.get("CORE_Y_OFFSET", "-1.0"))
    start = machine.get("machine_start_gcode", "")
    if "SET_PRESSURE_ADVANCE ADVANCE=0.05" not in start:
        raise SystemExit("ApexCore start gcode lost pressure advance")
    if process.get("sparse_infill_pattern") != "gyroid":
        raise SystemExit(f"sparse infill pattern {process.get('sparse_infill_pattern')!r}")
    if process.get("infill_wall_overlap") != "15%":
        raise SystemExit(f"overlap drifted to {process.get('infill_wall_overlap')!r}")
    if str(process.get("minimum_sparse_infill_area")) != "5":
        raise SystemExit(f"min sparse drifted to {process.get('minimum_sparse_infill_area')!r}")
    return machine, process, filament, y_offset


def main() -> None:
    material = sys.argv[1]
    work = Path(sys.argv[2])
    stl = Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    machine, process, filament, y_offset = prepare(work, material)
    for name, data in (("machine.json", machine), ("process.json", process), ("filament.json", filament)):
        (work / name).write_text(json.dumps(data, indent=2) + "\n")
    fil = filament
    print(
        "temp", fil.get("nozzle_temperature"),
        "fan", fil.get("fan_min_speed"), fil.get("fan_max_speed"),
        "aux", fil.get("additional_cooling_fan_speed"),
        "layer", process.get("layer_height"),
        "initial", process.get("initial_layer_print_height"),
        "walls", process.get("wall_loops"),
        "y", y_offset,
    )
    if os.environ.get("PREP_ONLY") == "1":
        return
    gcode = slice_model(work, stl, y_offset)
    dest = Path(sys.argv[4]) if len(sys.argv) > 4 else work / "sliced.gcode"
    dest.write_bytes(gcode.read_bytes())
    print("wrote", dest, dest.stat().st_size)


if __name__ == "__main__":
    main()
