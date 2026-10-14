#!/usr/bin/env python3
"""
Validate Apex V2 95A presets against the Cura reference G-code and (when built)
compare Orca slicer output within tolerance.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "validation"))

from apex_cura_reference import (  # noqa: E402
    CuraRefMetrics,
    compare_to_reference,
    measure_gcode,
    parse_reference,
)
from orca_profile_flatten import flatten_profile  # noqa: E402

REF = REPO / "validation/reference_gcode/apex_cura_insole_ref.gcode"
MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole @Vertex Apex V2.json"
FILAMENT = REPO / "resources/profiles/IdeaFormer/filament/VERTEX_APEX_CORE_95A.json"
MODEL = REPO / "validation/test_models/insole_sample.stl"
GEN = REPO / "validation/test_models/generate_insole_sample.py"
BIN = REPO / "build/src/Release/orca-slicer"
BIN_DBG = REPO / "build/src/Debug/orca-slicer"


def _binary() -> Path | None:
    for p in (BIN, BIN_DBG):
        if p.exists() and p.stat().st_size > 1_000_000:
            return p
    return None


def _run(cmd, timeout=600):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, errors="replace"
    )


def test_reference_file() -> list[str]:
    fails: list[str] = []
    if not REF.exists():
        return [f"missing reference gcode: {REF}"]
    metrics, detail = parse_reference(REF)
    if not detail.get("m109"):
        fails.append("reference missing M109 S225")
    if detail.get("belt_z_step_median") and abs(detail["belt_z_step_median"] - 0.6) > 0.05:
        fails.append(f"reference Z step {detail['belt_z_step_median']} != 0.6")
    outer = detail.get("outer_f") or []
    if not any(f == 2100 for _, f, _ in outer):
        fails.append(f"reference missing dominant outer F2100 (35 mm/s): {outer[:5]}")
    ref_text = REF.read_text(encoding="utf-8", errors="replace")
    if "F1200 ; Belt wall speed" not in ref_text:
        fails.append("reference missing belt-wall F1200 (20 mm/s)")
    fill = detail.get("fill_f") or []
    if not any(f == 3600 for _, f, _ in fill):
        fails.append(f"reference missing infill F3600 (60 mm/s): {fill[:5]}")
    skin = detail.get("skin_f") or []
    if not any(f == 3780 for _, f, _ in skin):
        fails.append(f"reference missing skin F3780 (63 mm/s): {skin[:5]}")
    travel = detail.get("travel_g0_f") or []
    if not travel or travel[0][0] != 12000:
        fails.append(f"reference travel F expected 12000 dominant, got {travel[:3]}")
    fans = detail.get("fans_start") or []
    if not any("S127.5" in f for f in fans):
        fails.append("reference missing part fan S127.5")
    if not any("P1 S64" in f for f in fans):
        fails.append("reference missing aux fan P1 S64")
    print(f"  reference OK: Z step median={detail.get('belt_z_step_median')}")
    return fails


def test_flattened_preset_matches_ref() -> list[str]:
    fails: list[str] = []
    ref = CuraRefMetrics()
    flat_p = flatten_profile(PROCESS)
    flat_f = flatten_profile(FILAMENT)
    p = json.loads(flat_p.read_text())
    f = json.loads(flat_f.read_text())
    flat_p.unlink(missing_ok=True)
    flat_f.unlink(missing_ok=True)

    checks = {
        "layer_height": ("0.42", ref.true_layer_mm),
        "outer_wall_speed": ("35", ref.outer_wall_mm_s),
        "inner_wall_speed": ("35", ref.outer_wall_mm_s),
        "sparse_infill_speed": ("60", ref.infill_mm_s),
        "top_surface_speed": ("63", ref.skin_mm_s),
        "travel_speed": ("200", ref.travel_mm_s),
        "wall_loops": ("3", ref.wall_loops_owner),
        "infill_direction": ("0", ref.infill_direction_deg),
    }
    for key, (exp, _) in checks.items():
        if str(p.get(key)) != exp:
            fails.append(f"process {key}={p.get(key)} expected {exp}")

    if str(f.get("nozzle_temperature_initial_layer", [""])[0]) != "225":
        fails.append("filament temp not 225")
    if float(f.get("filament_flow_ratio", ["1"])[0]) < 1.02:
        fails.append("filament flow ratio should achieve ~0.1796 E/mm (>=1.02)")
    e_per_mm = float(f.get("filament_flow_ratio", ["1.03"])[0]) * 1.0 * 0.42 / (math.pi * (1.75 / 2) ** 2)
    if abs(e_per_mm - ref.e_per_mm_nominal) > 0.015:
        fails.append(f"E/mm from flow ratio {e_per_mm:.4f} vs ref {ref.e_per_mm_nominal}")
    if str(f.get("filament_retraction_length", [""])[0]) != "1.5":
        fails.append("retraction length not 1.5")
    if str(f.get("close_fan_the_first_x_layers", [""])[0]) != "0":
        fails.append("unexpected fan-off-first-layers")
    vmax = float(f.get("filament_max_volumetric_speed", ["0"])[0])
    need = ref.infill_mm_s * ref.true_layer_mm * ref.line_width_mm
    if vmax < need * 0.95:
        fails.append(f"max volumetric {vmax} below infill need ~{need:.1f}")
    return fails


def test_slice_matches_ref() -> list[str]:
    binary = _binary()
    if binary is None:
        print("  SKIP slice-vs-ref: orca-slicer binary not built")
        return []

    if not MODEL.exists():
        _run([sys.executable, str(GEN)], timeout=60)

    ref_metrics, _ = parse_reference(REF)
    fails: list[str] = []
    with tempfile.TemporaryDirectory(prefix="apex_cura_match_") as td:
        outdir = Path(td)
        flat_m = flatten_profile(MACHINE, outdir / "machine.json")
        flat_p = flatten_profile(PROCESS, outdir / "process.json")
        flat_f = flatten_profile(FILAMENT, outdir / "filament.json")
        cmd = [
            str(binary),
            "--slice",
            "1",
            "--allow-newer-file",
            "--load-settings",
            f"{flat_m};{flat_p}",
            "--load-filaments",
            str(flat_f),
            "--outputdir",
            str(outdir),
            str(MODEL),
        ]
        r = _run(cmd, timeout=600)
        if r.returncode != 0:
            return [f"slice failed rc={r.returncode}: {(r.stdout + r.stderr)[-1200:]}"]
        gcodes = list(outdir.glob("**/*.gcode"))
        if not gcodes:
            return ["no gcode produced"]
        measured = measure_gcode(gcodes[0])
        fails = compare_to_reference(measured, ref_metrics, tol_speed_pct=0.15)
        cfg = gcodes[0].read_text(encoding="utf-8", errors="replace")
        if "; printer_is_belt = 1" not in cfg:
            fails.append("missing printer_is_belt in sliced config")
        if "; belt_axis = y" not in cfg:
            fails.append("missing belt_axis=y in sliced config")
        if not re.search(r"; wall_loops = 3", cfg):
            fails.append("wall_loops not 3 in sliced config")
        print(f"  slice measured: z_step={measured.get('belt_z_step')} outer={measured.get('outer_mm_s_mode')}")
    return fails


def main() -> int:
    print("Apex V2 Cura reference match tests")
    fails: list[str] = []
    fails.extend(test_reference_file())
    fails.extend(test_flattened_preset_matches_ref())
    fails.extend(test_slice_matches_ref())
    if fails:
        for f in fails:
            print(f"  FAIL {f}")
        return 1
    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
