#!/usr/bin/env python3
"""OrthoCAD Apex belt layout: width on X, length on Z, X centered like Cura ref."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from apex_bed_placement import (  # noqa: E402
    APEX_INSOLE_X_CENTER_MM,
    assert_apex_insole_extrusion_layout,
    extrusion_bounds_from_path,
    extrusion_xyz_bounds,
)
from orca_profile_flatten import flatten_profile  # noqa: E402
from apex_belt_y_trim import APEX_INITIAL_LAYER_PRINT_HEIGHT_MM, write_belt_transform_ini  # noqa: E402
from apex_first_layer_y import assert_first_layer_y_in_band  # noqa: E402
from apex_toe_first import assert_toe_first_gcode  # noqa: E402
from stl_preprocess import prepare_orthocad_insole_stl  # noqa: E402

REF = REPO / "validation/reference_gcode/apex_cura_insole_ref.gcode"
STL = REPO / "validation/test_models/orthocad_sample_top_insole.stl"
MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole Medium @Vertex Apex V2.json"
FILAMENT = REPO / "resources/profiles/IdeaFormer/filament/VERTEX_APEX_CORE_95A.json"
BIN = REPO / "build/src/Release/orca-slicer"


def _binary() -> Path | None:
    import os

    env = os.environ.get("ORCA_SLICER_BIN")
    if env and Path(env).is_file():
        return Path(env)
    if BIN.is_file() and BIN.stat().st_size > 500_000:
        return BIN
    return None


def test_cura_ref_extrusion_layout_baseline() -> list[str]:
    if not REF.is_file():
        return [f"missing {REF}"]
    b = extrusion_bounds_from_path(REF)
    # Reference part width ~92 mm; length along belt (Z) ~322 mm.
    return assert_apex_insole_extrusion_layout(
        b, width_mm=92.0, length_along_belt_mm=322.0, center_tol_mm=3.0, length_tol_frac=0.02
    )


def test_orthocad_slice_layout() -> list[str]:
    binary = _binary()
    if binary is None:
        print("  SKIP orthocad slice layout: orca-slicer binary not built")
        return []
    if not STL.is_file():
        return [f"missing STL {STL}"]

    prep, meta = prepare_orthocad_insole_stl(STL)
    width = float(meta["oriented_x_across_belt_mm"])
    length = float(meta["oriented_y_along_belt_mm"])

    with tempfile.TemporaryDirectory(prefix="orthocad_orient_") as td:
        work = Path(td)
        flat_m = flatten_profile(MACHINE, work / "machine.json")
        flat_p = flatten_profile(PROCESS, work / "process.json")
        flat_f = flatten_profile(FILAMENT, work / "filament.json")
        proc = json.loads(flat_p.read_text())
        proc["wall_loops"] = "1"
        proc["sparse_infill_density"] = "26%"
        flat_p.write_text(json.dumps(proc))

        write_belt_transform_ini(
            work / "belt_transform.ini",
            prep,
            initial_layer_print_height_mm=APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
            print_direction="toe_first",
        )

        import os

        env = os.environ.copy()
        env["SLIC3R_RESOURCES_DIR"] = str(REPO / "resources")
        belt_ini = work / "belt_transform.ini"
        if belt_ini.is_file():
            env["ORCABELT_TRANSFORM_INI"] = str(belt_ini)
        cmd = [
            str(binary),
            "--slice",
            "1",
            "--allow-newer-file",
            "--orient",
            "0",
            "--arrange",
            "0",
            "--load-settings",
            f"{flat_m};{flat_p}",
            "--load-filaments",
            str(flat_f),
            "--outputdir",
            str(work),
            str(prep),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env, errors="replace")
        if r.returncode != 0:
            return [f"slice failed: {(r.stdout + r.stderr)[-500:]}"]
        gcodes = list(work.glob("**/*.gcode"))
        if not gcodes:
            return ["no gcode produced"]
        gcode_path = gcodes[0]
        bounds = extrusion_bounds_from_path(gcode_path)
        gtext = gcode_path.read_text(encoding="utf-8", errors="replace")

    fails = assert_apex_insole_extrusion_layout(bounds, width_mm=width, length_along_belt_mm=length)
    if abs(bounds.get("x_center", 0) - APEX_INSOLE_X_CENTER_MM) > 2.0:
        fails.append(f"x_center {bounds.get('x_center')} vs {APEX_INSOLE_X_CENTER_MM}")
    fails.extend(assert_toe_first_gcode(gtext))
    fails.extend(assert_first_layer_y_in_band(gtext, label="orthocad toe_first"))
    if meta.get("rotation_z_deg") != 270:
        fails.append(f"expected toe_first rotation_z_deg 270, got {meta.get('rotation_z_deg')}")
    return fails


def main() -> int:
    fails: list[str] = []
    fails.extend(test_cura_ref_extrusion_layout_baseline())
    fails.extend(test_orthocad_slice_layout())
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("PASS orthocad belt orientation layout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
