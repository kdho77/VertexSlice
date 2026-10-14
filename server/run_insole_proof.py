#!/usr/bin/env python3
"""
Docker build proof: slice insole_sample.stl with Apex 95A and compare to Cura reference.
Exits 0 on success; prints image size, slice time, peak memory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from apex_cura_reference import CuraRefMetrics, compare_to_reference, measure_gcode, parse_reference  # noqa: E402
from server.profile_registry import get_profile  # noqa: E402
from server.slice_engine import slice_stl  # noqa: E402

MODEL = REPO / "validation" / "test_models" / "insole_sample.stl"
REF = REPO / "validation" / "reference_gcode" / "apex_cura_insole_ref.gcode"


def docker_image_size_mb() -> float | None:
    try:
        out = subprocess.check_output(
            ["docker", "image", "inspect", "vertexslice-server:proof", "--format", "{{.Size}}"],
            text=True,
            timeout=30,
        ).strip()
        return round(int(out) / (1024 * 1024), 1)
    except Exception:
        return None


def main() -> int:
    profile = get_profile("apex-v2-insole-95a")
    if profile is None:
        print("FAIL: profile registry missing apex-v2-insole-95a")
        return 1
    if not MODEL.is_file():
        gen = REPO / "validation" / "test_models" / "generate_insole_sample.py"
        subprocess.check_call([sys.executable, str(gen)])

    ref_metrics, _ = parse_reference(REF)
    gcode_path, stats = slice_stl(MODEL, profile)
    measured = measure_gcode(gcode_path)
    fails = compare_to_reference(measured, ref_metrics, tol_speed_pct=0.18)

    report = {
        "gcode_bytes": gcode_path.stat().st_size,
        "slice_seconds": stats.get("elapsed_s"),
        "peak_rss_kb": stats.get("peak_rss_kb"),
        "compare_failures": fails,
        "docker_image_size_mb": docker_image_size_mb(),
    }
    print(json.dumps(report, indent=2))

    if fails:
        for f in fails:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("PROOF PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
