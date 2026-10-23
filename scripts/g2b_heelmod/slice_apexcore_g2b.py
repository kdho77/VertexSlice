#!/usr/bin/env python3
"""LOCKED ApexCore 95A profile. No changes without Kendon's approval.

Printed file sha256
1eb294be4315565e868c0960a569f4f4158253f41b69955dc15cd3da77fb826e.
The numbers live in apexcore_95a_locked.json. prepare() refuses to slice
if a setting drifts from that file.

G2b CONFIG_BLOCK with the ApexCore filament swapped in. No heelmod: no
layer-range flow, min_bead, ensure_all, min-time, and no global
infill_wall_overlap or minimum_sparse_infill_area edits. Layer height stays
0.46 mm. ApexCore additions are nozzle temperature 225 °C, part fan 50% /
aux 25%, the locked wall/sparse/gap flow ratios, and pressure advance 0.05.
Layer-change retract is off and retraction length is 0.
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
    flatten,
    overlay_g2b,
    slice_model,
)

# Measured on this binary at layer 0.46 mm, wall_loops 2, overlap 15%, with
# the volumetric 0.18 ratios (outer=inner 0.8925, sparse 1.0042, solid 0.9251):
# outer 0.1584, inner 0.1460, sparse 0.1783, solid 0.2031 E/mm. Scale each
# feature onto 0.18. Gap fill did not appear as its own feature; its ratio
# follows sparse down to ApexCore's 0.16 E/mm target.
OUTER_MEASURED = 0.1584
INNER_MEASURED = 0.1460
SPARSE_MEASURED = 0.1783
SOLID_MEASURED = 0.2031
OUTER_BASE = 0.8925
INNER_BASE = 0.8925
SPARSE_BASE = 1.0042
SOLID_BASE = 0.9251
GAP_E_PER_MM = 0.16
# Offset that puts the 0.46 mm toe on the belt the way the G2b file does
# (outer min Y about 0.02). The heelmod constant -2.136 clips the sole.
CORE_Y_OFFSET = -1.146
LOCK_PATH = HERE / "apexcore_95a_locked.json"


def _scalar(value) -> str:
    if isinstance(value, list):
        return "" if not value else str(value[0])
    return str(value)


def _enforce_locked(machine: dict, process: dict, filament: dict, y_offset: float) -> None:
    """Refuse the slice if the prepared profile is not the printed ApexCore 95A."""
    lock = json.loads(LOCK_PATH.read_text())
    if lock.get("status") != "LOCKED":
        raise SystemExit("apexcore_95a_locked.json is not marked LOCKED")
    if abs(y_offset - float(lock["y_mach_offset"])) > 1e-9:
        raise SystemExit(
            f"LOCKED ApexCore 95A: y_mach_offset {y_offset} != {lock['y_mach_offset']}. "
            "No changes without Kendon's approval."
        )
    owners = {"machine": machine, "process": process, "filament": filament}
    for key, spec in lock["settings"].items():
        dest = owners[spec["profile"]]
        got = _scalar(dest.get(key))
        if got != spec["value"]:
            raise SystemExit(
                f"LOCKED ApexCore 95A: {spec['profile']}.{key} is {got!r}, "
                f"printed profile requires {spec['value']!r}. "
                "No changes without Kendon's approval."
            )
    start = machine.get("machine_start_gcode", "")
    if lock["pressure_advance"] not in start:
        raise SystemExit("LOCKED ApexCore 95A: pressure advance line missing. No changes without Kendon's approval.")


def _g2b_off(profile: dict, key: str) -> None:
    cur = profile.get(key)
    profile[key] = ["0"] if isinstance(cur, list) or cur is None else "0"


def _apply_core_flows(process: dict, filament: dict) -> None:
    fil = float(filament.get("filament_flow_ratio", ["1.03"])[0])
    layer_h = float(process["layer_height"])
    outer = OUTER_BASE * (0.18 / OUTER_MEASURED)
    inner = INNER_BASE * (0.18 / INNER_MEASURED)
    sparse = SPARSE_BASE * (0.18 / SPARSE_MEASURED)
    solid = SOLID_BASE * (0.18 / SOLID_MEASURED)
    gap = sparse * (GAP_E_PER_MM / 0.18)
    process["outer_wall_flow_ratio"] = f"{outer:.4f}"
    process["inner_wall_flow_ratio"] = f"{inner:.4f}"
    process["sparse_infill_flow_ratio"] = f"{sparse:.4f}"
    process["internal_solid_infill_flow_ratio"] = f"{solid:.4f}"
    process["gap_fill_flow_ratio"] = f"{gap:.4f}"
    print(
        "flows",
        f"fil={fil}",
        f"layer={layer_h}",
        f"outer={outer:.4f}",
        f"inner={inner:.4f}",
        f"sparse={sparse:.4f}",
        f"solid={solid:.4f}",
        f"gap={gap:.4f}",
        f"first_layer={process.get('first_layer_flow_ratio')}",
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
    if process.get("layer_height") != "0.46":
        raise SystemExit(f"layer height drifted to {process.get('layer_height')!r}")
    # ApexCore's filament retraction is 1.5 mm and the machine flag is on, so
    # the slicer emits a retract/prime on travels and at every layer change.
    # G2b has none of those. The flag is off and the length stays 0.
    _g2b_off(machine, "retract_when_changing_layer")
    _g2b_off(filament, "filament_retract_when_changing_layer")
    _g2b_off(filament, "filament_retraction_length")
    _apply_core_flows(process, filament)
    # Locked placement. An environment override would reprint a different file.
    y_offset = CORE_Y_OFFSET
    _enforce_locked(machine, process, filament, y_offset)
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
        "layer_retract", machine.get("retract_when_changing_layer"),
        "fil_retract_len", fil.get("filament_retraction_length"),
        "fil_layer_retract", fil.get("filament_retract_when_changing_layer"),
    )
    if os.environ.get("PREP_ONLY") == "1":
        return
    gcode = slice_model(work, stl, y_offset)
    dest = Path(sys.argv[4]) if len(sys.argv) > 4 else work / "sliced.gcode"
    dest.write_bytes(gcode.read_bytes())
    print("wrote", dest, dest.stat().st_size)


if __name__ == "__main__":
    main()
