#!/usr/bin/env python3
"""Slice ApexLight belt fixtures with CI orca-slicer (raw STL; C++ applies toe-first belt)."""
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
HEADLESS = Path(os.environ.get("VSLICE_HEADLESS", "/tmp/vslice_headless"))
sys.path.insert(0, str(HEADLESS / "validation"))
sys.path[:0] = [str(REPO), str(HEADLESS)]

from apex_belt_y_trim import (  # noqa: E402
    Y_PROBE_OFFSET_MM,
    measured_y_mach_offset_mm,
    write_belt_transform_ini,
)
from apex_first_layer_y import first_layer_extrusion_y_bounds  # noqa: E402
from server.profile_registry import get_profile  # noqa: E402
from server.slice_overrides import apply_json_overrides, apply_machine_overrides, apply_process_overrides  # noqa: E402
from validation.orca_profile_flatten import flatten_profile  # noqa: E402

FIX = REPO / "tests/fixtures/belt_profiles"
STL = Path(os.environ.get("VS_STL", "/tmp/insole_round_2p5mm.stl"))
BINARY = Path(os.environ.get("ORCA_SLICER_BIN", "/tmp/squashfs-root/bin/orca-slicer"))
RES = Path(os.environ.get("ORCA_RESOURCES", "/tmp/orca_res"))

# Full-toe slice layer band for heel cup coupon (toe-first, layers 215–425 of ~439).
HEEL_LAYER_LO = int(os.environ.get("HEEL_LAYER_LO", "215"))
HEEL_LAYER_HI = int(os.environ.get("HEEL_LAYER_HI", "425"))
HEEL_LAYER_DENOM = int(os.environ.get("HEEL_LAYER_DENOM", "439"))


def _load_fixture(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    skip = {"type", "name", "from", "instantiation", "inherits", "notes"}
    out: dict = {}
    for k, v in data.items():
        if k in skip:
            continue
        out[k] = v if isinstance(v, list) else str(v)
    return out


def merge_profile():
    prof = get_profile("apex-v2-insole-medium", material="apex_light")
    if prof is None:
        raise RuntimeError("apex_light profile missing")
    proc = {**prof.process_overrides, **_load_fixture(FIX / "ApexLight_belt_process.json")}
    mach = {**prof.machine_overrides, **_load_fixture(FIX / "ApexLight_belt_machine.json")}
    fil_raw = json.loads((FIX / "ApexLight_belt_filament.json").read_text(encoding="utf-8"))
    skip = {"type", "name", "from", "instantiation", "inherits", "notes"}
    fil = {**prof.filament_overrides, **{k: v for k, v in fil_raw.items() if k not in skip}}
    return replace(
        prof,
        process_overrides=proc,
        machine_overrides=mach,
        filament_overrides=fil,
        target_first_layer_y_mm=0.016,
    )


def flatten_presets(profile, work: Path) -> tuple[Path, Path, Path]:
    flat_m = flatten_profile(profile.machine, work / "machine.json")
    apply_machine_overrides(flat_m, profile.machine_overrides or {})
    flat_p = flatten_profile(profile.process, work / "process.json")
    apply_process_overrides(flat_p, profile.process_overrides or {})
    flat_f = flatten_profile(profile.filament, work / "filament.json")
    apply_json_overrides(flat_f, profile.filament_overrides or {})
    return flat_m, flat_p, flat_f


def heel_coupon_stl(src: Path, dest: Path) -> Path:
    """Heel cup band matching full-file layers HEEL_LAYER_LO–HEEL_LAYER_HI (toe at +X)."""
    import trimesh

    mesh = trimesh.load(src, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        mesh = mesh.dump(concatenate=True)
    x0, x1 = mesh.bounds[:, 0]
    frac_lo = HEEL_LAYER_LO / HEEL_LAYER_DENOM
    frac_hi = HEEL_LAYER_HI / HEEL_LAYER_DENOM
    # Toe-first: forefoot is high +X; belt layers advance toward low X (heel cup).
    x_hi = x1 - frac_lo * (x1 - x0)
    x_lo = x1 - frac_hi * (x1 - x0)
    cx = mesh.triangles_center[:, 0]
    keep = (cx >= x_lo) & (cx <= x_hi)
    heel = mesh.submesh([keep], append=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    heel.export(dest)
    return dest


def slice_raw(stl: Path, profile, dest: Path, target_y: float = 0.016) -> None:
    work = Path(tempfile.mkdtemp(prefix="vs_apex_"))
    flat_m, flat_p, flat_f = flatten_presets(profile, work)
    write_belt_transform_ini(
        work / "belt_transform.ini",
        stl,
        initial_layer_print_height_mm=0.46,
        layer_height_mm=0.46,
        print_direction="toe_first",
        target_y_mm=target_y,
    )
    env = os.environ.copy()
    env["SLIC3R_RESOURCES_DIR"] = str(RES)
    env["ORCABELT_TRANSFORM_INI"] = str(work / "belt_transform.ini")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    env["LD_LIBRARY_PATH"] = str(BINARY.parent.parent / "usr" / "lib") + ":" + env.get("LD_LIBRARY_PATH", "")

    cmd = [
        str(BINARY),
        "--slice",
        "1",
        "--allow-newer-file",
        "--arrange",
        "0",
        "--load-settings",
        f"{flat_m};{flat_p}",
        "--load-filaments",
        str(flat_f),
        "--outputdir",
        str(work),
        str(stl),
    ]

    def run_once() -> Path:
        for old in work.glob("**/*.gcode"):
            old.unlink()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, errors="replace", timeout=1200)
        if proc.returncode != 0:
            raise RuntimeError(f"orca-slicer exit {proc.returncode}: {(proc.stdout + proc.stderr)[-2500:]}")
        found = list(work.glob("**/*.gcode"))
        if not found:
            raise RuntimeError("no gcode produced")
        return found[0]

    ini = work / "belt_transform.ini"
    ini.write_text(f"y_mach_offset = {Y_PROBE_OFFSET_MM:.6f}\n", encoding="utf-8")
    probe = run_once()
    y_min, _ = first_layer_extrusion_y_bounds(probe.read_text(encoding="utf-8", errors="replace"))
    if y_min is None:
        raise RuntimeError("belt Y probe: no first-layer extrusion")
    offset = measured_y_mach_offset_mm(y_min, Y_PROBE_OFFSET_MM, target_y)
    ini.write_text(f"y_mach_offset = {offset:.6f}\n", encoding="utf-8")
    final = run_once()
    text = final.read_text(encoding="utf-8", errors="replace")
    post = getattr(profile, "gcode_postprocess", None)
    if post is not None:
        text = post(text)
    if profile.process_overrides.get("slow_down_for_layer_cooling") == "1":
        from validation.apex_belt_layer_slowdown import apply_belt_layer_slowdown

        text = apply_belt_layer_slowdown(
            text,
            slow_down_layer_time=float(profile.filament_overrides.get("slow_down_layer_time", ["8"])[0]),
            slow_down_min_speed=float(profile.filament_overrides.get("slow_down_min_speed", ["8"])[0]),
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def main() -> int:
    if not BINARY.is_file():
        print("ORCA_SLICER_BIN missing", file=sys.stderr)
        return 2
    if not STL.is_file():
        subprocess.run(
            ["gunzip", "-c", "/cursor/stores/bot/vsbrief/insole_round_2p5mm.stl.gz"],
            check=True,
            stdout=open(STL, "wb"),
        )
    profile = merge_profile()
    t0 = time.perf_counter()
    slice_raw(STL, profile, Path(os.environ.get("TOE_GCODE", "/cursor/stores/bot/apexlight_toe.gcode")))
    heel = heel_coupon_stl(STL, Path("/tmp/insole_heel_coupon.stl"))
    slice_raw(
        heel,
        profile,
        Path(os.environ.get("HEEL_GCODE", "/cursor/stores/bot/apexlight_heel_coupon_fan.gcode")),
    )
    print(f"done in {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
