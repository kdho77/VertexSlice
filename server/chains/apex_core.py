"""ApexCore golden chain: g2b slice, match, pullback, topfix, check_gcode."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from server.chains import CHAIN_APEX_CORE

REPO = Path(__file__).resolve().parents[2]
G2B = REPO / "scripts" / "g2b_heelmod"

# Founder approval 2026-11-06: Bounce is Core except these three deltas.
BOUNCE_NOZZLE_C = 220
BOUNCE_BED_C = 70
BOUNCE_PRINT_SPEED_SCALE = 0.8

# Absolute print-move speeds (mm/s) on the prepared Core process profile.
# Percentages (internal bridge, small perimeter, scarf, wipe, initial-layer
# travel), travel_speed, accelerations, retraction, and fan speeds are not
# scaled: a percentage of an already-scaled speed would compound.
BOUNCE_PRINT_SPEED_KEYS = (
    "outer_wall_speed",
    "inner_wall_speed",
    "sparse_infill_speed",
    "internal_solid_infill_speed",
    "top_surface_speed",
    "initial_layer_speed",
    "initial_layer_infill_speed",
    "gap_infill_speed",
    "bridge_speed",
    "ironing_speed",
    "skirt_speed",
    "support_speed",
    "support_interface_speed",
)

# Active bed is Cool Plate. Every plate setpoint is set so no plate stays off.
FILAMENT_BED_KEYS = (
    "cool_plate_temp",
    "cool_plate_temp_initial_layer",
    "hot_plate_temp",
    "hot_plate_temp_initial_layer",
    "eng_plate_temp",
    "eng_plate_temp_initial_layer",
    "textured_plate_temp",
    "textured_plate_temp_initial_layer",
)
PROCESS_BED_KEYS = (
    "first_layer_bed_temperature",
    "supertack_plate_temp",
    "supertack_plate_temp_initial_layer",
    "textured_cool_plate_temp",
    "textured_cool_plate_temp_initial_layer",
)


def bounce_after_lock() -> dict:
    """Overrides applied only after the locked ApexCore check."""
    bed = str(BOUNCE_BED_C)
    nozzle = str(BOUNCE_NOZZLE_C)
    return {
        "scale": {
            "process": {key: BOUNCE_PRINT_SPEED_SCALE for key in BOUNCE_PRINT_SPEED_KEYS},
        },
        "set": {
            "filament": {
                "nozzle_temperature": nozzle,
                "nozzle_temperature_initial_layer": nozzle,
                **{key: bed for key in FILAMENT_BED_KEYS},
            },
            "process": {
                # Header nozzle comes from the filament initial-layer key.
                # This process field is the other initial-layer nozzle value
                # the prepared Core profile carries (g2b left it at 260).
                "first_layer_temperature": nozzle,
                **{key: bed for key in PROCESS_BED_KEYS},
            },
        },
    }


def stage_commands(work: Path, stl: Path, final: Path, golden: Path | None = None) -> list[list[str]]:
    py = sys.executable
    raw = work / "raw.gcode"
    matched = work / "matched.gcode"
    pulled = work / "pulled.gcode"
    check = [py, str(G2B / "check_gcode.py"), str(final)]
    if golden is not None:
        check.append(str(golden))
    return [
        [py, str(G2B / "slice_apexcore_g2b.py"), "core", str(work), str(stl), str(raw)],
        [py, str(G2B / "postprocess_g2b.py"), "match", str(raw), str(matched)],
        [py, str(G2B / "pullback_v2.py"), str(matched), str(pulled)],
        [py, str(G2B / "topfix_apexcore.py"), str(pulled), str(final)],
        check,
    ]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("APEX_MIN_LAYER_TIME", "4")
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("ORCA_RESOURCES", os.environ.get("ORCA_RESOURCES", str(REPO / "resources")))
    return env


def prepare_belt_stl(stl_path: Path) -> tuple[Path, dict]:
    """Box prep before slice_apexcore_g2b: belt270, X center 140, toe-first."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from validation.stl_preprocess import prepare_orthocad_insole_stl

    return prepare_orthocad_insole_stl(Path(stl_path), x_center_mm=140, print_direction="toe_first")


def run_apex_core(
    stl_path: Path,
    work_dir: Path | None = None,
    timeout_s: int = 2400,
    process_overrides: dict | None = None,
    after_lock: dict | None = None,
) -> tuple[Path, dict]:
    stl_path = Path(stl_path)
    owned = work_dir is None
    work = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="apex_core_chain_"))
    work.mkdir(parents=True, exist_ok=True)
    prepped, prep_meta = prepare_belt_stl(stl_path)
    final = work / "apex_core.gcode"
    # Bounce's config block differs in temperature and speed, so it must not
    # be compared to the Core golden. Core (after_lock is None) still is.
    golden = None if after_lock else os.environ.get("VS_GCODE_GOLDEN")
    golden_path = Path(golden) if golden else None
    commands = stage_commands(work, prepped, final, golden_path)
    env = _env()
    # Absent on Core. A parent process that has the variable set must not
    # change the locked Core profile.
    env.pop("VS_AFTER_LOCK_JSON", None)
    if after_lock:
        env["VS_AFTER_LOCK_JSON"] = json.dumps(after_lock, separators=(",", ":"), sort_keys=True)
    density = (process_overrides or {}).get("sparse_infill_density")
    if density is not None and str(density).strip() != "":
        # One key. slice_apexcore_g2b applies it after the locked-profile check.
        env["VS_SPARSE_INFILL_DENSITY"] = str(density)
    t0 = time.perf_counter()
    # The slicer is the long stage. Later stages are pure Python.
    # Caller timeout is honored (the HTTP handler stays under the 270s proxy cutoff).
    timeouts = [timeout_s, 120, 120, 120, 60]
    for cmd, limit in zip(commands, timeouts):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=limit,
                env=env,
                cwd=str(REPO),
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            from server.slice_engine import SliceTimeoutError

            raise SliceTimeoutError(
                f"Slice exceeded {limit}s and was stopped before the 270s proxy cutoff. "
                "ApexLight right-foot requests were still inside the slicer when that cutoff "
                "closed the connection (HTTP 499), so the client got no G-code. "
                "No G-code was produced."
            ) from exc
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr)[-4000:]
            raise RuntimeError(f"apex_core stage failed ({Path(cmd[1]).name}): {tail}")
    if not final.is_file():
        raise RuntimeError("apex_core chain produced no gcode")
    stats = {
        "chain": CHAIN_APEX_CORE,
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "apex_min_layer_time": env.get("APEX_MIN_LAYER_TIME", "4"),
        "binary": env.get("ORCA_SLICER_BIN", ""),
        "preprocessed_stl": prep_meta.get("preprocessed_stl"),
        "rotation_z_deg": prep_meta.get("rotation_z_deg"),
        "x_center_mm": prep_meta.get("x_center_mm"),
    }
    if owned:
        stats["work_dir"] = str(work)
    return final, stats
