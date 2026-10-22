#!/usr/bin/env python3
"""Slice ApexLight / ApexCore G2b heelmod toe + heel coupon (layers 293–440)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "validation")]

from validation.apex_belt_y_trim import (  # noqa: E402
    Y_PROBE_OFFSET_MM,
    measured_y_mach_offset_mm,
    stl_aabb_mm,
    write_belt_transform_ini,
)
from validation.apex_first_layer_y import first_layer_extrusion_y_bounds  # noqa: E402
from validation.g2b_3mf import write_belt_3mf  # noqa: E402
from server.profile_registry import get_profile  # noqa: E402
from server.slice_overrides import apply_json_overrides, apply_machine_overrides, apply_process_overrides  # noqa: E402
from validation.apex_core import (  # noqa: E402
    FILAMENT_OVERRIDES as CORE_FILAMENT_OVERRIDES,
    OUTER_WALL_FLOW_RATIO as CORE_OUTER_FLOW,
    PROCESS_OVERRIDES as CORE_PROCESS_OVERRIDES,
    TARGET_FIRST_LAYER_Y_MM as CORE_TARGET_Y,
)
from validation.apex_light import MACHINE_START_GCODE as LIGHT_START  # noqa: E402
from validation.apex_light_postprocess import postprocess_apex_light  # noqa: E402
from validation.g2b_heel_postprocess import apply_g2b_heel_guards, apply_heel_flow_scale  # noqa: E402
from validation.winepath import winepath  # noqa: E402
from validation.orca_profile_flatten import flatten_profile  # noqa: E402

# G2b slicer + seam baseline (belt branch): heel end-cap seam post-process landed here.
G2B_CONFIG_SHA = "1868e505aa08b18bce1f9f7599741cb1aebdaae9"

FIX = REPO / "tests/fixtures/belt_profiles"
ARTIFACTS = Path(os.environ.get("G2B_ARTIFACTS", "/opt/cursor/artifacts"))
WINE_ORCA = Path(os.environ.get("WINE_ORCA_EXE", "/tmp/vslice_portable/orca-slicer.exe"))
BINARY = Path(os.environ.get("ORCA_SLICER_BIN", str(WINE_ORCA)))
RES = Path(os.environ.get("ORCA_RESOURCES", "/tmp/vslice_portable/resources"))
USE_WINE = os.environ.get("G2B_USE_WINE", "1") == "1" and WINE_ORCA.is_file()
STL = Path(os.environ.get("VS_STL", "/tmp/round_2p5mm_belt270.stl"))

HEEL_LAYER_LO = int(os.environ.get("HEEL_LAYER_LO", "293"))
HEEL_LAYER_HI = int(os.environ.get("HEEL_LAYER_HI", "440"))
HEEL_LAYER_START = HEEL_LAYER_LO
PLATE_X = float(os.environ.get("PLATE_X", "125"))

GLOBAL_PROC = {
    "infill_wall_overlap": "25%",
    "minimum_sparse_infill_area": "0",
    "slow_down_for_layer_cooling": "1",
    "slow_down_layer_time": "4",
    "slow_down_min_speed": "20",
}

LIGHT_G2B_PROC = {
    **GLOBAL_PROC,
    "min_bead_width": "50%",
    "initial_layer_min_bead_width": "50%",
    "wall_generator": "arachne",
    "outer_wall_flow_ratio": "1.547",
    "inner_wall_flow_ratio": "1.441",
    "sparse_infill_flow_ratio": "1.655",
    "internal_solid_infill_flow_ratio": "1.655",
    "first_layer_flow_ratio": "1.547",
}

LIGHT_HEEL_OPTS = {
    "outer_wall_flow_ratio": "1.70",
    "inner_wall_flow_ratio": "1.56",
    "min_bead_width": "85%",
    "initial_layer_min_bead_width": "85%",
    "ensure_vertical_shell_thickness": "ensure_all",
}

CORE_HEEL_OUTER = round(CORE_OUTER_FLOW * 1.10, 4)
CORE_HEEL_INNER = round(1.0 * 1.10, 4)
CORE_HEEL_OPTS = {
    "outer_wall_flow_ratio": f"{CORE_HEEL_OUTER:.4f}",
    "inner_wall_flow_ratio": f"{CORE_HEEL_INNER:.4f}",
    "min_bead_width": "85%",
    "initial_layer_min_bead_width": "85%",
    "ensure_vertical_shell_thickness": "ensure_all",
}


def _load_fixture(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    skip = {"type", "name", "from", "instantiation", "inherits", "notes"}
    return {k: (v if isinstance(v, list) else str(v)) for k, v in data.items() if k not in skip}


def _flatten(profile, work: Path) -> tuple[Path, Path, Path]:
    flat_m = flatten_profile(profile.machine, work / "machine.json")
    apply_machine_overrides(flat_m, profile.machine_overrides or {})
    flat_p = flatten_profile(profile.process, work / "process.json")
    apply_process_overrides(flat_p, profile.process_overrides or {})
    flat_f = flatten_profile(profile.filament, work / "filament.json")
    apply_json_overrides(flat_f, profile.filament_overrides or {})
    return flat_m, flat_p, flat_f


def _heel_z_light() -> tuple[float, float]:
    lh = 0.46
    return (HEEL_LAYER_START - 1) * lh, 1.0e6


def _heel_z_core(stl: Path) -> tuple[float, float]:
    lh = 0.42
    _, _, z0, _, _, z1 = stl_aabb_mm(stl)
    span = max(z1 - z0, lh)
    return z0 + 0.67 * span, z1 + lh * 2


def merge_light():
    prof = get_profile("apex-v2-insole-medium", material="apex_light")
    proc = {**prof.process_overrides, **_load_fixture(FIX / "ApexLight_belt_process.json"), **LIGHT_G2B_PROC}
    mach = {
        **prof.machine_overrides,
        **_load_fixture(FIX / "ApexLight_belt_machine.json"),
        "machine_start_gcode": LIGHT_START,
    }
    fil = {
        **prof.filament_overrides,
        **_load_fixture(FIX / "ApexLight_belt_filament.json"),
        "slow_down_layer_time": ["4"],
        "slow_down_min_speed": ["20"],
    }
    return replace(
        prof,
        process_overrides=proc,
        machine_overrides=mach,
        filament_overrides=fil,
        target_first_layer_y_mm=0.016,
        gcode_postprocess=postprocess_apex_light,
    )


def merge_core():
    prof = get_profile("apex-v2-insole-medium", material="apex_core")
    proc = {
        **CORE_PROCESS_OVERRIDES,
        **_load_fixture(FIX / "ApexCore_belt_process.json"),
        **GLOBAL_PROC,
        "set_other_flow_ratios": "1",
        "outer_wall_flow_ratio": f"{CORE_OUTER_FLOW:.4f}",
        "inner_wall_flow_ratio": "1",
        "sparse_infill_flow_ratio": "0.9142",
        "internal_solid_infill_flow_ratio": "0.9142",
        "first_layer_flow_ratio": f"{CORE_OUTER_FLOW:.4f}",
        "min_bead_width": "50%",
        "initial_layer_min_bead_width": "50%",
    }
    mach = {**prof.machine_overrides, **_load_fixture(FIX / "ApexLight_belt_machine.json")}
    fil = {
        **CORE_FILAMENT_OVERRIDES,
        **_load_fixture(FIX / "ApexCore_belt_filament.json"),
    }
    return replace(
        prof,
        process_overrides=proc,
        machine_overrides=mach,
        filament_overrides=fil,
        target_first_layer_y_mm=CORE_TARGET_Y,
        gcode_postprocess=None,
    )


def heel_coupon_stl(src: Path, dest: Path, denom: int) -> Path:
    import trimesh

    mesh = trimesh.load(src, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        mesh = mesh.dump(concatenate=True)
    x0, x1 = mesh.bounds[:, 0]
    frac_lo = HEEL_LAYER_LO / denom
    frac_hi = HEEL_LAYER_HI / denom
    x_hi = x1 - frac_lo * (x1 - x0)
    x_lo = x1 - frac_hi * (x1 - x0)
    cx = mesh.triangles_center[:, 0]
    heel = mesh.submesh([(cx >= x_lo) & (cx <= x_hi)], append=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    heel.export(dest)
    return dest


def slice_job(
    stl: Path,
    profile,
    dest: Path,
    heel_range: tuple[float, float, dict[str, str]],  # min_z, max_z, modifier opts
    *,
    layer_h: float,
    target_y: float,
) -> int:
    work = Path(tempfile.mkdtemp(prefix="g2b_"))
    flat_m, flat_p, flat_f = _flatten(profile, work)
    write_belt_transform_ini(
        work / "belt_transform.ini",
        stl,
        initial_layer_print_height_mm=layer_h,
        layer_height_mm=layer_h,
        print_direction="toe_first",
        target_y_mm=target_y,
    )
    z0, z1, heel_opts = heel_range
    env = os.environ.copy()
    env["SLIC3R_RESOURCES_DIR"] = winepath(RES) if USE_WINE else str(RES)
    env["ORCABELT_TRANSFORM_INI"] = winepath(work / "belt_transform.ini") if USE_WINE else str(work / "belt_transform.ini")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    env["WINEDEBUG"] = "-all"

    slice_input = stl
    if USE_WINE:
        exe = ["wine", str(WINE_ORCA)]
        settings = f"{winepath(flat_m)};{winepath(flat_p)}"
        fil = winepath(flat_f)
        outdir = winepath(work)
        model = winepath(slice_input)
    else:
        exe = [str(BINARY)]
        settings = f"{flat_m};{flat_p}"
        fil = str(flat_f)
        outdir = str(work)
        model = str(slice_input)

    cmd = exe + [
        "--slice",
        "1",
        "--allow-newer-file",
        "--arrange",
        "0",
        "--load-settings",
        settings,
        "--load-filaments",
        fil,
        "--outputdir",
        outdir,
        model,
    ]

    def run_once() -> Path:
        for old in work.glob("**/*.gcode"):
            old.unlink()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, errors="replace", timeout=1800)
        if proc.returncode != 0:
            raise RuntimeError(f"orca exit {proc.returncode}: {(proc.stdout + proc.stderr)[-3000:]}")
        found = list(work.glob("**/*.gcode"))
        if not found:
            raise RuntimeError("no gcode")
        return found[0]

    ini = work / "belt_transform.ini"
    ini.write_text(f"y_mach_offset = {Y_PROBE_OFFSET_MM:.6f}\n", encoding="utf-8")
    probe = run_once()
    y_min, _ = first_layer_extrusion_y_bounds(probe.read_text(encoding="utf-8", errors="replace"))
    if y_min is None:
        raise RuntimeError("belt Y probe failed")
    offset = measured_y_mach_offset_mm(y_min, Y_PROBE_OFFSET_MM, target_y)
    ini.write_text(f"y_mach_offset = {offset:.6f}\n", encoding="utf-8")
    text = run_once().read_text(encoding="utf-8", errors="replace")
    post = getattr(profile, "gcode_postprocess", None)
    if post:
        text = post(text)
    from validation.apex_belt_seam import apply_belt_seam_postprocess

    text = apply_belt_seam_postprocess(text)
    outer_scale = float(heel_opts.get("outer_wall_flow_ratio", "1")) / float(
        profile.process_overrides.get("outer_wall_flow_ratio", "1")
    )
    inner_scale = float(heel_opts.get("inner_wall_flow_ratio", "1")) / float(
        profile.process_overrides.get("inner_wall_flow_ratio", "1")
    )
    text = apply_heel_flow_scale(
        text,
        heel_layer_start=HEEL_LAYER_START,
        outer_scale=outer_scale,
        inner_scale=inner_scale,
    )
    text = apply_g2b_heel_guards(text, heel_layer_start=HEEL_LAYER_START)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return text.count(";LAYER_CHANGE")


def ensure_stl() -> None:
    if STL.is_file():
        return
    gz = Path("/cursor/stores/bot/vsbrief/insole_round_2p5mm.stl.gz")
    STL.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["gunzip", "-c", str(gz)], check=True, stdout=open(STL, "wb"))


def run_material(name: str, profile, layer_h: float, target_y: float, heel_meta: tuple[float, float, dict]) -> None:
    toe = ARTIFACTS / f"Apex{name}_round_2p5mm_G2b_heelmod_toe.gcode"
    coupon_path = ARTIFACTS / f"Apex{name}_round_2p5mm_G2b_heel_coupon.gcode"
    denom = slice_job(STL, profile, toe, heel_meta, layer_h=layer_h, target_y=target_y)
    heel = heel_coupon_stl(STL, Path(f"/tmp/{name.lower()}_heel_coupon.stl"), denom)
    slice_job(heel, profile, coupon_path, heel_meta, layer_h=layer_h, target_y=target_y)
    print(f"{name}: toe layers={denom} -> {toe}")


def main() -> int:
    if USE_WINE and not WINE_ORCA.is_file():
        print("missing WINE_ORCA_EXE", file=sys.stderr)
        return 2
    if not USE_WINE and not Path(BINARY).is_file():
        print("missing ORCA_SLICER_BIN", file=sys.stderr)
        return 2
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ensure_stl()
    t0 = time.perf_counter()
    zl = _heel_z_light()
    run_material("Light", merge_light(), 0.46, 0.016, (zl[0], zl[1], LIGHT_HEEL_OPTS))
    zc = _heel_z_core(STL)
    run_material("Core", merge_core(), 0.42, CORE_TARGET_Y, (zc[0], zc[1], CORE_HEEL_OPTS))
    print(f"G2B_CONFIG_SHA={G2B_CONFIG_SHA} done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
