#!/usr/bin/env python3
"""ApexLight vs Kendon's Cura VERTEX_APEX_LITE_90A numbers (no G-code fixture)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from apex_belt_y_trim import (  # noqa: E402
    TARGET_FIRST_LAYER_Y_MM as CORE_TARGET_FIRST_LAYER_Y_MM,
    cura_matched_y_mach_offset_mm,
    predicted_first_layer_y_mm,
)
from apex_gcode_bookends import MACHINE_END_GCODE  # noqa: E402
from apex_insole_densities import KENDON_INFILL_DENSITY_PRESETS  # noqa: E402
from apex_insole_tunables import (  # noqa: E402
    PRINT_DIRECTION_HEEL_FIRST,
    PRINT_DIRECTION_TOE_FIRST,
)
from apex_core import (  # noqa: E402
    PROCESS_OVERRIDES as APEX_CORE_PROCESS_OVERRIDES,
    TARGET_FIRST_LAYER_Y_MM as APEX_CORE_TARGET_FIRST_LAYER_Y_MM,
)
from apex_light import (  # noqa: E402
    AUX_FAN_PERCENT,
    BELT_STEP_MM,
    DEFAULT_WALL_LOOPS,
    E_PER_MM_INFILL,
    E_PER_MM_INNER,
    E_PER_MM_OUTER,
    E_PER_MM_TOL,
    FAN_CLOSE_FIRST_LAYERS,
    FAN_FULL_SPEED_LAYER,
    FAN_MAX_PERCENT,
    FIRST_LAYER_MM_S,
    FLOW_RATIO,
    INFILL_MM_S,
    INNER_WALL_MM_S,
    LAYER_HEIGHT_MM,
    LINE_WIDTH_MM,
    MACHINE_START_GCODE,
    OUTER_WALL_MM_S,
    PRINT_ACCEL,
    PROCESS_OVERRIDES,
    TARGET_FIRST_LAYER_Y_MM,
    FIRST_LAYER_Y_TOL_MM,
    TRAVEL_ACCEL,
    TRAVEL_MM_S,
    e_per_mm,
    feature_flow_for_target,
)
from apex_material_temps import filament_temps  # noqa: E402
from orca_profile_flatten import flatten_profile  # noqa: E402
from server.profile_registry import (  # noqa: E402
    FILAMENT_95A,
    get_profile,
    list_materials,
    resolve_material,
)
from server.slice_overrides import apply_process_overrides, parse_slice_overrides  # noqa: E402
from stl_preprocess import prepare_orthocad_insole_stl  # noqa: E402

PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole @Vertex Apex V2.json"
MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
STL = REPO / "validation/test_models/orthocad_sample_top_insole.stl"


def _flatten(path: Path) -> dict:
    flat = flatten_profile(path)
    data = json.loads(flat.read_text())
    flat.unlink(missing_ok=True)
    return data


def test_registry_lists_apex_light() -> list[str]:
    fails: list[str] = []
    ids = [m["id"] for m in list_materials()]
    if "apex_light" not in ids:
        fails.append(f"GET-equivalent materials missing apex_light: {ids}")
    spec = resolve_material("apex_light")
    if spec.display_name != "ApexLight" or spec.nozzle_c != 260 or spec.bed_c != 0:
        fails.append(f"apex_light spec {spec}")
    if spec.default_wall_loops != DEFAULT_WALL_LOOPS:
        fails.append(f"apex_light default walls {spec.default_wall_loops}")
    if spec.target_first_layer_y_mm != TARGET_FIRST_LAYER_Y_MM:
        fails.append(f"apex_light first-layer Y target {spec.target_first_layer_y_mm}")
    light = get_profile("apex-v2-insole-medium", material="apex_light")
    if light is None or light.filament.name != "ApexLight.json":
        fails.append("profile+apex_light did not select ApexLight.json")
    return fails


def test_filament_matches_cura_numbers() -> list[str]:
    fails: list[str] = []
    flat = _flatten(resolve_material("apex_light").filament)
    nozzle, bed = filament_temps(flat)
    if (nozzle, bed) != (260, 0):
        fails.append(f"temps {nozzle}/{bed} != 260/0")
    flow = float(flat.get("filament_flow_ratio", ["0"])[0])
    if abs(flow - FLOW_RATIO) > 1e-9:
        fails.append(f"flow {flow} != {FLOW_RATIO}")
    if str(flat.get("filament_retraction_length", [""])[0]) != "0":
        fails.append(f"retraction {flat.get('filament_retraction_length')!r} != 0")
    if str(flat.get("filament_retract_when_changing_layer", ["1"])[0]) not in ("0", "false", "False"):
        fails.append("retract_when_changing_layer not off")
    if str(flat.get("close_fan_the_first_x_layers", [""])[0]) != str(FAN_CLOSE_FIRST_LAYERS):
        fails.append(f"close_fan_the_first_x_layers {flat.get('close_fan_the_first_x_layers')}")
    if str(flat.get("full_fan_speed_layer", [""])[0]) != str(FAN_FULL_SPEED_LAYER):
        fails.append(f"full_fan_speed_layer {flat.get('full_fan_speed_layer')}")
    if float(flat.get("fan_max_speed", ["0"])[0]) != FAN_MAX_PERCENT:
        fails.append(f"fan_max_speed {flat.get('fan_max_speed')} != {FAN_MAX_PERCENT}")
    if float(flat.get("fan_min_speed", ["0"])[0]) != FAN_MAX_PERCENT:
        fails.append(f"fan_min_speed {flat.get('fan_min_speed')} != {FAN_MAX_PERCENT}")
    if float(flat.get("additional_cooling_fan_speed", ["0"])[0]) != AUX_FAN_PERCENT:
        fails.append(f"aux fan {flat.get('additional_cooling_fan_speed')} != {AUX_FAN_PERCENT}")
    # ~6%/layer: 35% / (full_fan_speed_layer - close_first) = 35/6 ≈ 5.83
    ramp = FAN_MAX_PERCENT / (FAN_FULL_SPEED_LAYER - FAN_CLOSE_FIRST_LAYERS)
    if abs(ramp - 6.0) > 0.5:
        fails.append(f"fan ramp {ramp:.2f}%/layer not ~6")
    return fails


def test_process_overrides_match_cura() -> list[str]:
    fails: list[str] = []
    if abs(LAYER_HEIGHT_MM - BELT_STEP_MM * math.sin(math.radians(45.0))) > 0.01:
        fails.append(f"layer_height {LAYER_HEIGHT_MM} != 0.65*sin45")
    if PROCESS_OVERRIDES.get("layer_height") != f"{LAYER_HEIGHT_MM:.2f}":
        fails.append("process layer_height")
    if PROCESS_OVERRIDES.get("first_layer_height") != PROCESS_OVERRIDES.get("layer_height"):
        fails.append("first layer thickness must equal layer height")
    if PROCESS_OVERRIDES.get("initial_layer_print_height") != PROCESS_OVERRIDES.get("layer_height"):
        fails.append("initial_layer_print_height must equal layer height")
    expect = {
        "wall_loops": "2",
        "outer_wall_speed": str(int(OUTER_WALL_MM_S)),
        "inner_wall_speed": str(int(INNER_WALL_MM_S)),
        "sparse_infill_speed": str(int(INFILL_MM_S)),
        "initial_layer_speed": str(int(FIRST_LAYER_MM_S)),
        "travel_speed": str(int(TRAVEL_MM_S)),
        "default_acceleration": str(PRINT_ACCEL),
        "travel_acceleration": str(TRAVEL_ACCEL),
        "sparse_infill_pattern": "gyroid",
        "wall_generator": "classic",
        "filter_out_gap_fill": "1",
    }
    for k, v in expect.items():
        if PROCESS_OVERRIDES.get(k) != v:
            fails.append(f"process {k}={PROCESS_OVERRIDES.get(k)!r} expected {v}")
    spec = resolve_material("apex_light")
    if spec.process_overrides.get("use_relative_e_distances"):
        fails.append("relative extrusion belongs on machine overrides")
    if spec.machine_overrides.get("use_relative_e_distances") != "1":
        fails.append("machine missing use_relative_e_distances=1 (M83)")
    start = spec.machine_overrides.get("machine_start_gcode") or ""
    if start.splitlines()[0] != "PRINT_START":
        fails.append(f"PRINT_START call line {start.splitlines()[:1]}")
    if "M83" not in start:
        fails.append("apex_light start missing M83")
    if not MACHINE_END_GCODE.strip().endswith("PRINT_END"):
        fails.append("PRINT_END call line changed on shared machine end gcode")
    if start != MACHINE_START_GCODE:
        fails.append("machine start override drifted from apex_light template")
    user = parse_slice_overrides(4, None)
    if user.get("wall_loops") != 4:
        fails.append("wall_loops user override not accepted")
    return fails


def test_e_per_mm_within_3pct() -> list[str]:
    fails: list[str] = []
    # Nominal volume formula (no E_CAL). Classic-wall E_CAL is an Orca width
    # compensation checked on live G-code (0.192 / 0.180 / 0.2065 ±1%).
    targets = {
        "outer": E_PER_MM_OUTER,
        "inner": E_PER_MM_INNER,
        "infill": E_PER_MM_INFILL,
    }
    for name, target in targets.items():
        feat = feature_flow_for_target(target)
        got = e_per_mm(FLOW_RATIO, LINE_WIDTH_MM, LAYER_HEIGHT_MM, feat)
        err = abs(got - target) / target
        if err > E_PER_MM_TOL:
            fails.append(f"{name} E/mm {got:.4f} vs Cura {target} ({err:.1%} > {E_PER_MM_TOL:.0%})")
    return fails


def test_hardness_infill_unchanged() -> list[str]:
    fails: list[str] = []
    for label, pct in KENDON_INFILL_DENSITY_PRESETS.items():
        pid = {
            "Extra Soft": "apex-v2-insole-extra-soft",
            "Soft": "apex-v2-insole-soft",
            "Medium": "apex-v2-insole-medium",
            "Hard": "apex-v2-insole-hard",
            "Extra Hard": "apex-v2-insole-extra-hard",
        }[label]
        light = get_profile(pid, material="apex_light")
        core = get_profile(pid, material="apex_core")
        if light is None or core is None:
            fails.append(f"missing profile {pid}")
            continue
        if light.process != core.process:
            fails.append(f"{pid}: apex_light swapped hardness process file")
        if "sparse_infill_density" in (light.process_overrides or {}):
            fails.append(f"{pid}: material override stomps hardness infill")
        flat = _flatten(light.process)
        got = str(flat.get("sparse_infill_density", "")).rstrip("%")
        if got != str(pct):
            fails.append(f"{pid} infill {got} != {pct}")
        if str(flat.get("sparse_infill_pattern", "")).lower() != "gyroid":
            fails.append(f"{pid} pattern not gyroid")
    return fails


def test_existing_materials_unchanged() -> list[str]:
    fails: list[str] = []
    core95 = _flatten(FILAMENT_95A)
    n, b = filament_temps(core95)
    if (n, b) != (225, 0):
        fails.append(f"95A temps changed {(n, b)}")
    if str(core95.get("filament_flow_ratio", [""])[0]) != "1.03":
        fails.append(f"95A flow changed {core95.get('filament_flow_ratio')}")
    if str(core95.get("filament_retraction_length", [""])[0]) != "1.5":
        fails.append(f"95A retraction changed {core95.get('filament_retraction_length')}")
    proc = _flatten(PROCESS)
    if str(proc.get("layer_height")) != "0.42":
        fails.append(f"shared process layer_height {proc.get('layer_height')}")
    if str(proc.get("wall_loops")) != "1":
        fails.append(f"shared process wall_loops {proc.get('wall_loops')}")
    if str(proc.get("outer_wall_speed")) != "35":
        fails.append(f"shared process outer_wall_speed {proc.get('outer_wall_speed')}")
    machine = _flatten(MACHINE)
    start = machine.get("machine_start_gcode") or ""
    if not start.startswith("PRINT_START\n"):
        fails.append("shared PRINT_START call line changed")
    if "M83" in start.split("M82", 1)[-1][:80] and "G1 F1800 E-1.5" not in start:
        fails.append("shared machine start lost core retract/fans")
    if str(machine.get("use_relative_e_distances")) not in ("0", "false", "False"):
        fails.append(f"shared machine relative E changed {machine.get('use_relative_e_distances')}")
    end = (machine.get("machine_end_gcode") or "").strip()
    if not end.endswith("PRINT_END"):
        fails.append("shared PRINT_END call line changed")
    core = get_profile("apex-v2-insole-95a", material="apex_core")
    if core is None:
        fails.append("missing apex_core profile")
    else:
        if core.process_overrides != dict(APEX_CORE_PROCESS_OVERRIDES):
            fails.append("apex_core process overrides drifted from validation/apex_core.py")
        if core.gcode_postprocess is not None:
            fails.append("apex_core must not use ApexLight belt-wall 20 mm/s postprocess")
        if core.target_first_layer_y_mm != APEX_CORE_TARGET_FIRST_LAYER_Y_MM:
            fails.append(f"apex_core first-layer Y {core.target_first_layer_y_mm}")
    if abs(CORE_TARGET_FIRST_LAYER_Y_MM - 0.07) > 1e-9:
        fails.append(f"shared first-layer Y target {CORE_TARGET_FIRST_LAYER_Y_MM} != 0.07")
    if abs(APEX_CORE_TARGET_FIRST_LAYER_Y_MM - 0.066) > 1e-9:
        fails.append(f"apex_core Cura Y {APEX_CORE_TARGET_FIRST_LAYER_Y_MM} != 0.066")
    for mid in ("apex_forge", "apex_bounce"):
        spec = resolve_material(mid)
        if spec.target_first_layer_y_mm is not None:
            fails.append(f"{mid} should keep shared 0.07 first-layer Y, got {spec.target_first_layer_y_mm}")
        if spec.gcode_postprocess is not None:
            fails.append(f"{mid} must not use ApexLight gcode postprocess")
    return fails


def test_y_offsets_recomputed_for_layer_height() -> list[str]:
    fails: list[str] = []
    if not STL.is_file():
        return [f"missing sample STL {STL}"]
    spec = resolve_material("apex_light")
    if spec.target_first_layer_y_mm != TARGET_FIRST_LAYER_Y_MM:
        fails.append(f"apex_light target Y {spec.target_first_layer_y_mm} != {TARGET_FIRST_LAYER_Y_MM}")
    if abs(TARGET_FIRST_LAYER_Y_MM - 0.016) > 1e-9:
        fails.append(f"ApexLight Cura layer-0 Y target {TARGET_FIRST_LAYER_Y_MM} != 0.016")
    offsets: dict[str, float] = {}
    for direction in (PRINT_DIRECTION_TOE_FIRST, PRINT_DIRECTION_HEEL_FIRST):
        prep, _meta = prepare_orthocad_insole_stl(STL, print_direction=direction)
        off = cura_matched_y_mach_offset_mm(
            prep,
            initial_layer_print_height_mm=LAYER_HEIGHT_MM,
            layer_height_mm=LAYER_HEIGHT_MM,
            print_direction=direction,
            target_y_mm=TARGET_FIRST_LAYER_Y_MM,
        )
        y = predicted_first_layer_y_mm(
            prep,
            initial_layer_print_height_mm=LAYER_HEIGHT_MM,
            layer_height_mm=LAYER_HEIGHT_MM,
            y_mach_offset_mm=off,
            print_direction=direction,
            target_y_mm=TARGET_FIRST_LAYER_Y_MM,
        )
        offsets[direction] = off
        print(f"  apex_light {direction}: y_mach_offset={off:.4f} predicted_Y={y:.4f}")
        if abs(y - TARGET_FIRST_LAYER_Y_MM) > FIRST_LAYER_Y_TOL_MM:
            fails.append(
                f"{direction}: predicted Y {y:.3f} not {TARGET_FIRST_LAYER_Y_MM} ± {FIRST_LAYER_Y_TOL_MM}"
            )
    if abs(offsets[PRINT_DIRECTION_TOE_FIRST] - offsets[PRINT_DIRECTION_HEEL_FIRST]) < 0.05:
        fails.append(f"toe/heel offsets nearly identical {offsets}")
    return fails


def test_overrides_apply_on_flattened_process() -> list[str]:
    fails: list[str] = []
    import tempfile

    spec = resolve_material("apex_light")
    with tempfile.TemporaryDirectory(prefix="apex_light_ov_") as td:
        dest = Path(td) / "process.json"
        dest.write_text(json.dumps(_flatten(PROCESS)))
        apply_process_overrides(dest, spec.process_overrides)
        data = json.loads(dest.read_text())
        if str(data.get("layer_height")) != f"{LAYER_HEIGHT_MM:.2f}":
            fails.append(f"applied layer_height {data.get('layer_height')}")
        if str(data.get("wall_loops")) != "2":
            fails.append(f"applied wall_loops {data.get('wall_loops')}")
        apply_process_overrides(dest, {"wall_loops": 3})
        data = json.loads(dest.read_text())
        if str(data.get("wall_loops")) != "3":
            fails.append("user wall_loops did not override material default")
    return fails


def main() -> int:
    print("ApexLight Cura VERTEX_APEX_LITE_90A match")
    tests = (
        test_registry_lists_apex_light,
        test_filament_matches_cura_numbers,
        test_process_overrides_match_cura,
        test_e_per_mm_within_3pct,
        test_hardness_infill_unchanged,
        test_existing_materials_unchanged,
        test_y_offsets_recomputed_for_layer_height,
        test_overrides_apply_on_flattened_process,
    )
    fails: list[str] = []
    for fn in tests:
        part = fn()
        if part:
            print(f"  FAIL {fn.__name__}")
            fails.extend(part)
        else:
            print(f"  PASS {fn.__name__}")
    if fails:
        for f in fails:
            print(f"FAIL {f}")
        return 1
    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
