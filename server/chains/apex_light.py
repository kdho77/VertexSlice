"""ApexLight golden chain: h_curaE (pp_c, pp_d, efix) then optional beltslow/toptidy then post-edit.

Legacy ``postprocess_apex_light`` is not the server hook on this path. The q
build's ``postprocess_C`` still calls it internally; that is the box pp_c stage.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from server.chains import CHAIN_APEX_LIGHT
from tools.belt_pp.pipeline import finalize_apex_light

REPO = Path(__file__).resolve().parents[2]
H_CURAE = REPO / "tools" / "belt_pp" / "h_curaE.py"

# 4432a211e6 (PR #16 combing heap fix) matches ApexLight_90A_GOLDEN_2026-10-31.
# def8eff480 does not. Resources are parent(parent(binary)) / "resources".
DEFAULT_APEX_LIGHT_BIN = "/app/slicers/pr16/bin/orca-slicer-pr16"
APEX_LIGHT_BIN_ENV = "ORCA_SLICER_BIN_APEX_LIGHT"


def slicer_binary() -> str:
    chosen = os.environ.get(APEX_LIGHT_BIN_ENV, "").strip()
    return chosen or DEFAULT_APEX_LIGHT_BIN


def resources_for_binary(binary: str) -> str:
    path = Path(binary)
    if path.exists():
        path = path.resolve()
    return str(path.parent.parent / "resources")


# travelfix final_q / pr16 env16.sh. {$BON} is this process set.
GOLDEN_E_PO = {
    "reduce_crossing_wall": "1",
    "avoid_crossing_wall_fallback": "1",
    "top_shell_layers": "0",
    "top_shell_thickness": "0",
    "wall_loops": "2",
    "wall_generator": "arachne",
    "min_bead_width": "50%",
    "min_feature_size": "25%",
    "wall_transition_length": "100%",
    "wall_distribution_count": "1",
    "minimum_sparse_infill_area": "5",
    "solid_infill_below_area": "2",
    "ensure_vertical_shell_thickness": "none",
    "filter_out_gap_fill": "9999",
    "internal_solid_infill_flow_ratio": "1.45",
}


def golden_env(
    stl: Path,
    *,
    print_direction: str | None = None,
    wall_loops: int | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "VS_EFIX": "1",
            "VS_MAT": "apex_light",
            "VS_FORCE_OFFSET": "-1.151",
            "VS_WALLS": str(2 if wall_loops is None else wall_loops),
            "VS_FLOWMUL": "1.088",
            "VS_FLOWMUL_INNER": "1.083",
            "VS_FLOWMUL_INFILL": "1.19",
            "VS_E_PO": json.dumps(GOLDEN_E_PO),
            "VS_STL": str(stl),
            "VS_DIR": print_direction or "toe_first",
        }
    )
    # Golden recipe leaves these unset. A caller can still opt in.
    if os.environ.get("VS_BELTSLOW") != "1":
        env.pop("VS_BELTSLOW", None)
    if os.environ.get("VS_TOPTIDY") != "1":
        env.pop("VS_TOPTIDY", None)
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    binary = slicer_binary()
    env["ORCA_SLICER_BIN"] = binary
    env["ORCA_RESOURCES"] = resources_for_binary(binary)
    return env


def run_apex_light(
    stl_path: Path,
    work_dir: Path | None = None,
    timeout_s: int = 2400,
    process_overrides: dict | None = None,
    print_direction: str | None = None,
) -> tuple[Path, dict]:
    stl_path = Path(stl_path)
    owned = work_dir is None
    work = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="apex_light_chain_"))
    work.mkdir(parents=True, exist_ok=True)
    out = work / "apex_light.gcode"
    walls = None
    if process_overrides and process_overrides.get("wall_loops") is not None:
        walls = int(process_overrides["wall_loops"])
    env = golden_env(stl_path, print_direction=print_direction, wall_loops=walls)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(H_CURAE), str(out)],
        capture_output=True,
        text=True,
        timeout=max(timeout_s, 2400),
        env=env,
        cwd=str(REPO),
        errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-4000:]
        raise RuntimeError(f"apex_light h_curaE failed: {tail}")
    if not out.is_file():
        raise RuntimeError("apex_light chain produced no gcode")
    text = out.read_text(encoding="utf-8", errors="surrogateescape")
    if env.get("VS_BELTSLOW") == "1":
        from tools.belt_pp import beltslow

        text = beltslow.fix(text)
    if env.get("VS_TOPTIDY") == "1":
        from tools.belt_pp import toptidy

        text = toptidy.fix(text)
    text = finalize_apex_light(text)
    out.write_text(text, encoding="utf-8", errors="surrogateescape")
    stats = {
        "chain": CHAIN_APEX_LIGHT,
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "binary": env.get("ORCA_SLICER_BIN", ""),
        "stdout_tail": proc.stdout[-1500:],
        "stderr_tail": proc.stderr[-1500:],
    }
    if owned:
        stats["work_dir"] = str(work)
    return out, stats
