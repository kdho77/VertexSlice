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
from server.slice_overrides import apply_json_overrides, apply_machine_overrides, apply_process_overrides
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


def forced_belt_y_offset_mm() -> float | None:
    """Box VS_FORCE_OFFSET. When set, the belt Y probe must not run."""
    raw = os.environ.get("VS_FORCE_OFFSET", "").strip()
    if not raw:
        return None
    return float(raw)


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
    material: str | None = None,
) -> tuple[Path, dict]:
    """
    Run headless slice. Returns (gcode_path, stats dict with elapsed_s, peak_rss_kb).

    ApexCore and ApexLight with an explicit ``material`` run the production
    chain. Callers that omit ``material`` (h_curaE's inner slice) stay on the
    single orca-slicer path so the chain cannot recurse.
    """
    stl_path = Path(stl_path).resolve()
    if not stl_path.is_file():
        raise FileNotFoundError(stl_path)

    if material in ("apex_core", "apex_light"):
        from server.chains import run_material_chain

        return run_material_chain(
            material,
            stl_path,
            profile,
            work_dir,
            timeout_s,
            process_overrides,
            print_direction,
        )

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
    apply_json_overrides(flat_f, getattr(profile, "filament_overrides", None) or {})

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
        # Do not pass --orient together with --arrange 0. This orca-slicer binary
        # SIGABRTs immediately (glibc "corrupted double-linked list (not small)",
        # exit 134) when both flags are present. Reproduced in the proof image:
        # either flag alone is fine; the pair is not. PrintConfig orient default
        # is already 0 (disabled); CLI need_arrange defaults true, so --arrange 0
        # is the flag that keeps the preprocessed belt mesh in place.
        cmd.extend(["--arrange", "0"])
    elif arrange:
        cmd.extend(["--arrange", "1"])
    if rotate_z is not None and str(rotate_z).strip() != "":
        cmd.extend(["--rotate", str(rotate_z)])

    time_v = Path("/usr/bin/time")
    t0 = time.perf_counter()
    peak_rss_kb = None

    def _run_once() -> tuple[Path, subprocess.CompletedProcess]:
        nonlocal peak_rss_kb
        for old in work_dir.glob("**/*.gcode"):
            old.unlink()
        # GNU time -v for peak RSS when available
        wrapper = [str(time_v), "-v", *cmd] if time_v.is_file() else cmd
        proc = subprocess.run(
            wrapper,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(work_dir),
            env=env,
            errors="replace",
        )
        rss = _parse_peak_rss(proc.stderr + proc.stdout)
        if rss is not None:
            peak_rss_kb = max(peak_rss_kb or 0, rss)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr)[-3000:]
            raise RuntimeError(f"orca-slicer exit {proc.returncode}: {tail}")
        found = list(work_dir.glob("**/*.gcode"))
        if not found:
            raise RuntimeError("no gcode produced")
        return found[0], proc

    if belt_ini.is_file():
        forced = forced_belt_y_offset_mm()
        if forced is not None:
            # Golden ApexLight uses VS_FORCE_OFFSET=-1.151. A probe slice would
            # move the first layer off the locked print.
            belt_ini.write_text(f"y_mach_offset = {forced:.6f}\n", encoding="utf-8")
            placement_stats["belt_y_mach_offset_mm"] = forced
            placement_stats["belt_y_offset_forced"] = True
        else:
            # Measured first-layer Y trim (replaces the h=0.2 residual constants).
            # y_mach_offset adds 1:1 to every gantry Y (BeltTransform::inverse_transform_point),
            # but the C++ clamps Y_mach >= 0, so probe with a large positive offset where
            # nothing clamps, measure the first printed layer's extrusion Y min, then
            # re-slice with the exact offset that lands it on the material target.
            from apex_belt_y_trim import (  # noqa: E402
                Y_PROBE_OFFSET_MM,
                TARGET_FIRST_LAYER_Y_MM,
                measured_y_mach_offset_mm,
            )
            from apex_first_layer_y import first_layer_extrusion_y_bounds  # noqa: E402

            target_y = getattr(profile, "target_first_layer_y_mm", None)
            target_y = TARGET_FIRST_LAYER_Y_MM if target_y is None else target_y
            belt_ini.write_text(f"y_mach_offset = {Y_PROBE_OFFSET_MM:.6f}\n", encoding="utf-8")
            probe_gcode, _ = _run_once()
            probe_y_min, _ = first_layer_extrusion_y_bounds(
                probe_gcode.read_text(encoding="utf-8", errors="replace")
            )
            if probe_y_min is None:
                raise RuntimeError("belt Y probe: no first-layer extrusion found")
            offset = measured_y_mach_offset_mm(probe_y_min, Y_PROBE_OFFSET_MM, target_y)
            belt_ini.write_text(f"y_mach_offset = {offset:.6f}\n", encoding="utf-8")
            placement_stats["belt_y_probe_first_layer_y_mm"] = probe_y_min
            placement_stats["belt_y_mach_offset_geometric_mm"] = placement_stats.get("belt_y_mach_offset_mm")
            placement_stats["belt_y_mach_offset_mm"] = offset

    gcode_path, proc = _run_once()
    elapsed = time.perf_counter() - t0

    postprocess = getattr(profile, "gcode_postprocess", None)
    if postprocess is not None:
        gcode_path.write_text(
            postprocess(gcode_path.read_text(encoding="utf-8", errors="replace")),
            encoding="utf-8",
        )
    gcodes = [gcode_path]

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
