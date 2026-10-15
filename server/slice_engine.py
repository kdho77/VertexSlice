"""Headless orca-slicer CLI wrapper with flattened presets."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "validation"))
from orca_profile_flatten import flatten_profile  # noqa: E402

from server.profile_registry import SliceProfile
from server.slice_overrides import apply_machine_overrides, apply_process_overrides
from validation.stl_bed_fit import bounds_after_z_rotation  # noqa: E402
from validation.stl_preprocess import prepare_belt_stl, prepare_orthocad_insole_stl  # noqa: E402


def _slicer_binary() -> Path:
    env = os.environ.get("ORCA_SLICER_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    for candidate in (
        Path("/app/bin/orca-slicer"),
        REPO_ROOT / "build" / "src" / "Release" / "orca-slicer",
        REPO_ROOT / "build" / "src" / "Debug" / "orca-slicer",
        REPO_ROOT / "build" / "src" / "orca-slicer",
    ):
        if candidate.is_file() and candidate.stat().st_size > 500_000:
            return candidate
    raise FileNotFoundError("orca-slicer binary not found (set ORCA_SLICER_BIN)")


def slice_stl(
    stl_path: Path,
    profile: SliceProfile,
    work_dir: Path | None = None,
    timeout_s: int = 600,
    process_overrides: dict | None = None,
    rotate_z: str | None = None,
    arrange: bool = False,
    bed_width_x_mm: float = 250.0,
    belt_insole_place: bool = False,
    x_center_mm: float | None = None,
    print_direction: str | None = None,
) -> tuple[Path, dict]:
    """
    Run headless slice. Returns (gcode_path, stats dict with elapsed_s, peak_rss_kb).
    """
    stl_path = Path(stl_path).resolve()
    if not stl_path.is_file():
        raise FileNotFoundError(stl_path)

    placement_stats: dict = {}
    slice_stl_path = stl_path
    belt_orient_disabled = False
    belt_trim_stl: Path | None = None
    belt_print_direction: str | None = None
    if belt_insole_place:
        from apex_bed_placement import APEX_INSOLE_X_CENTER_MM  # noqa: E402
        from apex_insole_tunables import DEFAULT_PRINT_DIRECTION, orthocad_base_rotation_z_deg, validate_print_direction  # noqa: E402

        xc = x_center_mm if x_center_mm is not None else APEX_INSOLE_X_CENTER_MM
        direction = validate_print_direction(print_direction or DEFAULT_PRINT_DIRECTION)
        rot_deg = orthocad_base_rotation_z_deg(direction)
        placement_stats = bounds_after_z_rotation(stl_path, rot_deg, bed_width_x_mm=bed_width_x_mm)
        slice_stl_path, prep_meta = prepare_orthocad_insole_stl(
            stl_path,
            bed_width_x_mm=bed_width_x_mm,
            x_center_mm=xc,
            print_direction=direction,
        )
        placement_stats.update(prep_meta)
        belt_trim_stl = slice_stl_path
        belt_print_direction = direction
        belt_orient_disabled = True
        arrange = False
        rotate_z = None
    elif rotate_z is not None and str(rotate_z).strip() != "":
        deg = int(rotate_z)
        placement_stats = bounds_after_z_rotation(stl_path, deg, bed_width_x_mm=bed_width_x_mm)
        from apex_bed_placement import APEX_INSOLE_X_CENTER_MM  # noqa: E402

        xc = x_center_mm if x_center_mm is not None else APEX_INSOLE_X_CENTER_MM
        slice_stl_path, prep_meta = prepare_belt_stl(
            stl_path, deg, bed_width_x_mm=bed_width_x_mm, x_center_mm=xc
        )
        placement_stats.update(prep_meta)
        belt_orient_disabled = True
        arrange = False
        rotate_z = None  # rotation baked into STL

    tmp_owned = work_dir is None
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="vertexslice_"))
    else:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

    binary = _slicer_binary()
    flat_m = flatten_profile(profile.machine, work_dir / "machine.json")
    apply_machine_overrides(flat_m, getattr(profile, "machine_overrides", None) or {})
    flat_p = flatten_profile(profile.process, work_dir / "process.json")
    merged_process = {**(getattr(profile, "process_overrides", None) or {}), **(process_overrides or {})}
    apply_process_overrides(flat_p, merged_process)
    flat_f = flatten_profile(profile.filament, work_dir / "filament.json")

    if belt_trim_stl is not None:
        from apex_belt_y_trim import (  # noqa: E402
            APEX_INITIAL_LAYER_PRINT_HEIGHT_MM,
            write_belt_transform_ini,
        )

        proc = json.loads(flat_p.read_text())
        try:
            layer0 = float(
                proc.get("initial_layer_print_height")
                or proc.get("first_layer_height")
                or APEX_INITIAL_LAYER_PRINT_HEIGHT_MM
            )
        except (TypeError, ValueError):
            layer0 = APEX_INITIAL_LAYER_PRINT_HEIGHT_MM
        try:
            layer_h = float(proc.get("layer_height") or layer0)
        except (TypeError, ValueError):
            layer_h = layer0
        placement_stats["initial_layer_print_height_mm"] = layer0
        placement_stats["layer_height_mm"] = layer_h
        target_y = getattr(profile, "target_first_layer_y_mm", None)
        if target_y is not None:
            placement_stats["target_first_layer_y_mm"] = target_y
        placement_stats["belt_y_mach_offset_mm"] = write_belt_transform_ini(
            work_dir / "belt_transform.ini",
            belt_trim_stl,
            initial_layer_print_height_mm=layer0,
            layer_height_mm=layer_h,
            print_direction=belt_print_direction,
            **({"target_y_mm": target_y} if target_y is not None else {}),
        )

    env = os.environ.copy()
    resources = os.environ.get("ORCA_RESOURCES", str(REPO_ROOT / "resources"))
    env["SLIC3R_RESOURCES_DIR"] = resources
    belt_ini = work_dir / "belt_transform.ini"
    if belt_ini.is_file():
        env["ORCABELT_TRANSFORM_INI"] = str(belt_ini)

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
        str(work_dir),
        str(slice_stl_path),
    ]
    if belt_orient_disabled:
        cmd.extend(["--orient", "0", "--arrange", "0"])
    elif arrange:
        cmd.extend(["--arrange", "1"])
    if rotate_z is not None and str(rotate_z).strip() != "":
        cmd.extend(["--rotate", str(rotate_z)])

    t0 = time.perf_counter()
    # GNU time -v for peak RSS when available
    time_v = Path("/usr/bin/time")
    if time_v.is_file():
        wrapper = [str(time_v), "-v", *cmd]
    else:
        wrapper = cmd

    proc = subprocess.run(
        wrapper,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=str(work_dir),
        env=env,
        errors="replace",
    )
    elapsed = time.perf_counter() - t0
    peak_rss_kb = _parse_peak_rss(proc.stderr + proc.stdout)

    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-3000:]
        raise RuntimeError(f"orca-slicer exit {proc.returncode}: {tail}")

    gcodes = list(work_dir.glob("**/*.gcode"))
    if not gcodes:
        raise RuntimeError("no gcode produced")

    stats = {
        "elapsed_s": round(elapsed, 3),
        "peak_rss_kb": peak_rss_kb,
        "binary": str(binary),
        "stdout_tail": proc.stdout[-1500:],
        "stderr_tail": proc.stderr[-1500:],
        **placement_stats,
    }
    if tmp_owned:
        stats["work_dir"] = str(work_dir)
    return gcodes[0], stats


def _parse_peak_rss(text: str) -> int | None:
    for line in text.splitlines():
        if "Maximum resident set size" in line:
            parts = line.split(":")[-1].strip().split()
            if parts:
                try:
                    return int(parts[0])
                except ValueError:
                    pass
    return None
