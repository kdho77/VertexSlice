"""Headless orca-slicer CLI wrapper with flattened presets."""
from __future__ import annotations

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
from server.slice_overrides import apply_process_overrides


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
) -> tuple[Path, dict]:
    """
    Run headless slice. Returns (gcode_path, stats dict with elapsed_s, peak_rss_kb).
    """
    stl_path = Path(stl_path).resolve()
    if not stl_path.is_file():
        raise FileNotFoundError(stl_path)

    tmp_owned = work_dir is None
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="vertexslice_"))
    else:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

    binary = _slicer_binary()
    flat_m = flatten_profile(profile.machine, work_dir / "machine.json")
    flat_p = flatten_profile(profile.process, work_dir / "process.json")
    apply_process_overrides(flat_p, process_overrides or {})
    flat_f = flatten_profile(profile.filament, work_dir / "filament.json")

    env = os.environ.copy()
    resources = os.environ.get("ORCA_RESOURCES", str(REPO_ROOT / "resources"))
    env["SLIC3R_RESOURCES_DIR"] = resources

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
        str(stl_path),
    ]

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
