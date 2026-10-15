#!/usr/bin/env python3
"""Apex V2 first layer = layer height and first-layer gantry Y (~0.07) for toe and heel."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from apex_belt_y_trim import (  # noqa: E402
    APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
    FIRST_LAYER_Y_MIN_HI_MM,
    FIRST_LAYER_Y_MIN_LO_MM,
    LIVE_Y_RESIDUAL_SHIFT_MM,
    cura_matched_y_mach_offset_mm,
    predicted_first_layer_y_mm,
)
from apex_first_layer_y import (  # noqa: E402
    CURA_REF,
    assert_first_layer_y_in_band,
    assert_first_layer_y_near_cura,
    cura_ref_first_layer_y_min,
    first_layer_extrusion_y_bounds,
)
from apex_insole_densities import PROCESS_FILES  # noqa: E402
from apex_insole_tunables import (  # noqa: E402
    PRINT_DIRECTION_HEEL_FIRST,
    PRINT_DIRECTION_TOE_FIRST,
)
from orca_profile_flatten import flatten_profile  # noqa: E402
from stl_preprocess import prepare_orthocad_insole_stl  # noqa: E402

PROC_DIR = REPO / "resources/profiles/IdeaFormer/process"
COMMON = PROC_DIR / "fdm_process_apex_insole_common.json"
STL = REPO / "validation/test_models/orthocad_sample_top_insole.stl"
DIRECTIONS = (PRINT_DIRECTION_TOE_FIRST, PRINT_DIRECTION_HEEL_FIRST)


def test_band_check_fails_out_of_band_synthetic() -> list[str]:
    """assert_first_layer_y_in_band must fail when Y min is 0.62 (live toe miss)."""
    fails: list[str] = []
    gcode = "\n".join(
        [
            "PRINT_START",
            "M204 S3000",
            ";LAYER_CHANGE",
            ";TYPE:Inner wall",
            "G1 X10.0 Y0.62 E0.15",
            "G1 X20.0 Y0.80 E0.30",
            ";LAYER_CHANGE",
            ";TYPE:Inner wall",
            "G1 X10.0 Y1.20 E0.45",
            "",
        ]
    )
    y_min, y_max = first_layer_extrusion_y_bounds(gcode)
    if y_min is None or abs(y_min - 0.62) > 0.001:
        fails.append(f"synthetic Y min should be 0.62, got {y_min}")
    band = assert_first_layer_y_in_band(gcode, label="synthetic")
    if not band:
        fails.append("assert_first_layer_y_in_band passed on Y min 0.62 (must fail)")
    elif "0.620" not in band[0] and "0.62" not in band[0]:
        fails.append(f"band failure should cite 0.62, got {band}")
    return fails


def test_cura_ref_baseline() -> list[str]:
    fails: list[str] = []
    ref_text = CURA_REF.read_text(encoding="utf-8", errors="replace")
    fails.extend(assert_first_layer_y_near_cura(ref_text))
    if abs(cura_ref_first_layer_y_min() - 0.066) > 0.001:
        fails.append("unexpected Cura ref baseline")
    return fails


def test_apex_v2_first_layer_height() -> list[str]:
    fails: list[str] = []
    names = ["fdm_process_apex_insole_common.json", *PROCESS_FILES.values()]
    for name in names:
        path = PROC_DIR / name
        if not path.is_file():
            fails.append(f"missing process {path}")
            continue
        flat = flatten_profile(path)
        data = json.loads(flat.read_text())
        flat.unlink(missing_ok=True)
        raw = json.loads(path.read_text())
        lh = str(data.get("layer_height"))
        got = str(data.get("initial_layer_print_height"))
        if name == COMMON.name:
            if str(raw.get("initial_layer_print_height")) != str(raw.get("layer_height")):
                fails.append(
                    f"{name} initial_layer_print_height={raw.get('initial_layer_print_height')} "
                    f"!= layer_height={raw.get('layer_height')}"
                )
            if str(raw.get("first_layer_height")) != str(raw.get("layer_height")):
                fails.append(
                    f"{name} first_layer_height={raw.get('first_layer_height')} "
                    f"!= layer_height={raw.get('layer_height')}"
                )
        if got != lh:
            fails.append(f"{name} flattened initial_layer_print_height={got} != layer_height={lh}")
        if abs(float(got) - float(lh)) > 1e-9:
            fails.append(f"{name} first-layer height {got} != layer height {lh}")
        if abs(float(got) - APEX_INITIAL_LAYER_PRINT_HEIGHT_MM) > 1e-9:
            fails.append(f"{name} first-layer height {got} != {APEX_INITIAL_LAYER_PRINT_HEIGHT_MM}")
    return fails


def test_y_min_both_directions_from_sample_stl() -> list[str]:
    fails: list[str] = []
    if not STL.is_file():
        return [f"missing sample insole STL {STL}"]
    offsets: dict[str, float] = {}
    for direction in DIRECTIONS:
        prep, meta = prepare_orthocad_insole_stl(STL, print_direction=direction)
        if meta.get("print_direction") != direction:
            fails.append(f"{direction}: preprocess meta {meta.get('print_direction')}")
        off = cura_matched_y_mach_offset_mm(
            prep,
            initial_layer_print_height_mm=APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
            print_direction=direction,
        )
        y = predicted_first_layer_y_mm(
            prep,
            initial_layer_print_height_mm=APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
            y_mach_offset_mm=off,
            print_direction=direction,
        )
        residual = LIVE_Y_RESIDUAL_SHIFT_MM[direction]
        if residual >= 0:
            fails.append(f"{direction}: expected negative live residual, got {residual}")
        offsets[direction] = off
        print(f"  {direction}: y_mach_offset={off:.4f} predicted_Y_min={y:.4f}")
        if y < FIRST_LAYER_Y_MIN_LO_MM or y > FIRST_LAYER_Y_MIN_HI_MM:
            fails.append(
                f"{direction}: predicted first-layer Y min {y:.3f} mm not in "
                f"{FIRST_LAYER_Y_MIN_LO_MM:.2f}–{FIRST_LAYER_Y_MIN_HI_MM:.2f} mm"
            )
    if abs(offsets[PRINT_DIRECTION_TOE_FIRST] - offsets[PRINT_DIRECTION_HEEL_FIRST]) < 0.05:
        fails.append(
            f"toe/heel y_mach_offset nearly identical ({offsets}); expected per-direction measurement"
        )
    return fails


def _slicer_binary() -> Path | None:
    env = os.environ.get("ORCA_SLICER_BIN")
    if env and Path(env).is_file() and Path(env).stat().st_size > 500_000:
        return Path(env)
    for candidate in (
        REPO / "build/src/Release/orca-slicer",
        Path("/app/bin/orca-slicer"),
    ):
        if candidate.is_file() and candidate.stat().st_size > 500_000:
            return candidate
    return None


def test_slice_y_min_both_directions() -> list[str]:
    if _slicer_binary() is None:
        print("  SKIP slice Y min: orca-slicer binary not built")
        return []
    if not STL.is_file():
        return [f"missing sample insole STL {STL}"]

    from server.profile_registry import get_profile  # noqa: E402
    from server.slice_engine import slice_stl  # noqa: E402

    profile = get_profile("apex-v2-insole-medium")
    if profile is None:
        return ["missing apex-v2-insole-medium profile"]

    fails: list[str] = []
    for direction in DIRECTIONS:
        with tempfile.TemporaryDirectory(prefix=f"first_layer_y_{direction}_") as td:
            gcode, stats = slice_stl(
                STL,
                profile,
                work_dir=Path(td),
                belt_insole_place=True,
                print_direction=direction,
            )
            layer0 = stats.get("initial_layer_print_height_mm")
            if layer0 is not None and abs(float(layer0) - APEX_INITIAL_LAYER_PRINT_HEIGHT_MM) > 1e-6:
                fails.append(f"{direction}: slice used first layer {layer0}, expected {APEX_INITIAL_LAYER_PRINT_HEIGHT_MM}")
            text = gcode.read_text(encoding="utf-8", errors="replace")
            if "; initial_layer_print_height = 0.42" not in text:
                fails.append(f"{direction}: gcode missing initial_layer_print_height = 0.42")
            fails.extend(assert_first_layer_y_in_band(text, label=direction))
    return fails


def main() -> int:
    print("Apex V2 first-layer height + Y min (toe and heel)")
    fails: list[str] = []
    fails.extend(test_band_check_fails_out_of_band_synthetic())
    fails.extend(test_cura_ref_baseline())
    fails.extend(test_apex_v2_first_layer_height())
    fails.extend(test_y_min_both_directions_from_sample_stl())
    fails.extend(test_slice_y_min_both_directions())
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("PASS first-layer = layer height and Y min 0.03–0.12 for toe_first and heel_first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
