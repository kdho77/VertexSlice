#!/usr/bin/env python3
"""Slice OrthoCAD STL with Apex V2 Medium 26% preset; write gcode + summary JSON."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "validation"))

from apex_cura_reference import parse_reference  # noqa: E402
from orca_profile_flatten import flatten_profile  # noqa: E402

MACHINE = REPO / "resources/profiles/IdeaFormer/machine/Vertex Apex V2 1.0 nozzle.json"
PROCESS = REPO / "resources/profiles/IdeaFormer/process/0.42mm TPU Insole Medium @Vertex Apex V2.json"
FILAMENT = REPO / "resources/profiles/IdeaFormer/filament/VERTEX_APEX_CORE_95A.json"
REF = REPO / "validation/reference_gcode/apex_cura_insole_ref.gcode"
BIN = REPO / "build/src/Release/orca-slicer"


def _binary() -> Path:
    for p in (
        BIN,
        REPO / "build/src/Debug/orca-slicer",
        Path("/app/bin/orca-slicer"),
    ):
        if p.is_file() and p.stat().st_size > 500_000:
            return p
    raise FileNotFoundError("orca-slicer binary not found")


def _layer_count(text: str) -> int:
    return len(re.findall(r";LAYER_CHANGE|;LAYER:\d+", text))


def _est_time_s(text: str) -> float | None:
    m = re.search(r"; estimated printing time[^=]*=\s*([^\n]+)", text, re.I)
    if m:
        return None  # parse if present
    m2 = re.search(r";TIME:(\d+)", text)
    if m2:
        return float(m2.group(1))
    m3 = re.search(r";Print Time:(\d+)", text)
    if m3:
        return float(m3.group(1))
    elapsed = re.findall(r";TIME_ELAPSED:([\d.]+)", text)
    if elapsed:
        return float(elapsed[-1])
    return None


def _header_settings_diff(sliced_cfg: str, ref_text: str) -> dict:
    keys = [
        "layer_height",
        "wall_loops",
        "sparse_infill_density",
        "sparse_infill_pattern",
        "infill_direction",
        "outer_wall_speed",
        "sparse_infill_speed",
        "nozzle_temperature",
        "filament_flow_ratio",
    ]
    def grab(block: str, key: str) -> str | None:
        m = re.search(rf"; {key} = ([^\n]+)", block)
        return m.group(1).strip() if m else None

    ref_tail = ref_text[-8000:]
    out: dict[str, dict[str, str | None]] = {}
    for k in keys:
        out[k] = {"sliced": grab(sliced_cfg, k), "cura_ref": grab(ref_tail, k)}
    return out


def main() -> int:
    stl = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/cursor/stores/self/orthocad_sample_top_insole.stl")
    out_gcode = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/cursor/stores/self/orthocad_sample_top_medium26.gcode")
    if not stl.is_file():
        print(f"Missing STL: {stl}", file=sys.stderr)
        return 1
    binary = _binary()
    with tempfile.TemporaryDirectory(prefix="orthocad_slice_") as td:
        work = Path(td)
        flat_m = flatten_profile(MACHINE, work / "machine.json")
        flat_p = flatten_profile(PROCESS, work / "process.json")
        flat_f = flatten_profile(FILAMENT, work / "filament.json")
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
            str(work),
            str(stl.resolve()),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, errors="replace")
        if r.returncode != 0:
            print((r.stdout + r.stderr)[-2000:], file=sys.stderr)
            return r.returncode
        gcodes = list(work.glob("**/*.gcode"))
        if not gcodes:
            print("no gcode", file=sys.stderr)
            return 1
        text = gcodes[0].read_text(encoding="utf-8", errors="replace")
    out_gcode.write_text(text, encoding="utf-8")
    ref_text = REF.read_text(encoding="utf-8", errors="replace")
    summary = {
        "gcode_path": str(out_gcode),
        "gcode_bytes": out_gcode.stat().st_size,
        "layer_count": _layer_count(text),
        "est_time_seconds": _est_time_s(text),
        "settings_vs_cura_ref": _header_settings_diff(text[-12000:], ref_text),
        "has_print_start": "PRINT_START" in text.splitlines()[:30],
        "has_print_end": "PRINT_END" in text,
    }
    summary_path = out_gcode.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
