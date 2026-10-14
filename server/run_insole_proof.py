#!/usr/bin/env python3
"""
Docker build proof: slice validation STLs with Apex presets and compare to Cura reference.
Exits 0 on success; prints JSON report with slice time, peak memory, bookends, and metrics.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from apex_cura_reference import compare_to_reference, measure_gcode, parse_reference  # noqa: E402
from apex_gcode_bookends import (  # noqa: E402
    compare_bookends,
    extract_sliced_end_bookend,
    extract_sliced_start_bookend,
    reference_end_bookend,
    reference_start_bookend,
)
from server.profile_registry import get_profile  # noqa: E402
from server.slice_engine import slice_stl  # noqa: E402
from apex_bed_placement import (  # noqa: E402
    assert_apex_insole_extrusion_layout,
    extrusion_xyz_bounds,
)
from apex_toe_first import assert_toe_first_gcode  # noqa: E402
from validation.stl_bed_fit import bounds_after_z_rotation, stl_axis_bounds  # noqa: E402

MODEL_SAMPLE = REPO / "validation" / "test_models" / "insole_sample.stl"
MODEL_ORTHOCAD = REPO / "validation" / "test_models" / "orthocad_sample_top_insole.stl"
REF = REPO / "validation" / "reference_gcode" / "apex_cura_insole_ref.gcode"
STORE_GCODE = Path(os.environ.get("VERTEXSLICE_PROOF_GCODE_DIR", "/cursor/stores/self"))


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


def _layer_count(text: str) -> int:
    return len(re.findall(r";LAYER_CHANGE|;LAYER:\d+", text))


def _est_print_time(text: str) -> str | None:
    m = re.search(r"; estimated printing time[^=]*=\s*([^\n]+)", text, re.I)
    return m.group(1).strip() if m else None


def _gcode_xyz_bounds_mm(text: str) -> dict[str, float]:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for line in text.splitlines():
        if not line.startswith(("G0", "G1")):
            continue
        for axis, bucket in (("X", xs), ("Y", ys), ("Z", zs)):
            m = re.search(rf"\b{axis}(-?[\d.]+)", line)
            if m:
                bucket.append(float(m.group(1)))
    if not xs:
        return {}
    return {
        "gcode_x_mm": round(max(xs) - min(xs), 3),
        "gcode_y_mm": round(max(ys) - min(ys), 3),
        "gcode_z_mm": round(max(zs) - min(zs), 3),
        "gcode_x_min": round(min(xs), 3),
        "gcode_x_max": round(max(xs), 3),
        "gcode_y_min": round(min(ys), 3),
        "gcode_y_max": round(max(ys), 3),
    }


def _stl_report(stl: Path, rotate_z: str | None) -> dict:
    (x0, x1), (y0, y1), (z0, z1) = stl_axis_bounds(stl)
    out: dict = {
        "stl_x_mm": round(x1 - x0, 3),
        "stl_y_mm": round(y1 - y0, 3),
        "stl_z_mm": round(z1 - z0, 3),
    }
    if rotate_z is not None:
        out.update(bounds_after_z_rotation(stl, int(rotate_z)))
    return out


def _bookend_check(gcode_text: str) -> tuple[list[str], dict]:
    fails: list[str] = []
    detail = {"start_match": False, "end_match": False}
    try:
        start_fails = compare_bookends(
            extract_sliced_start_bookend(gcode_text),
            reference_start_bookend(REF),
        )
        end_fails = compare_bookends(
            extract_sliced_end_bookend(gcode_text),
            reference_end_bookend(REF),
        )
        fails.extend(start_fails)
        fails.extend(end_fails)
        detail["start_match"] = not start_fails
        detail["end_match"] = not end_fails
    except ValueError as exc:
        fails.append(str(exc))
    return fails, detail


def _run_slice(
    label: str,
    stl: Path,
    profile_id: str,
    process_overrides: dict | None = None,
    compare_ref: bool = True,
    rotate_z: str | None = None,
    arrange: bool = False,
    belt_insole_place: bool = False,
    print_direction: str | None = None,
    store_name: str | None = None,
) -> dict:
    profile = get_profile(profile_id)
    if profile is None:
        raise RuntimeError(f"profile missing: {profile_id}")
    if not stl.is_file():
        raise FileNotFoundError(stl)

    ref_metrics, _ = parse_reference(REF)
    gcode_path, stats = slice_stl(
        stl,
        profile,
        process_overrides=process_overrides,
        rotate_z=rotate_z,
        arrange=arrange,
        belt_insole_place=belt_insole_place,
        print_direction=print_direction,
    )
    gcode_text = gcode_path.read_text(encoding="utf-8", errors="replace")
    measured = measure_gcode(gcode_path)

    compare_fails: list[str] = []
    if compare_ref:
        compare_fails = compare_to_reference(measured, ref_metrics, tol_speed_pct=0.18)

    bookend_fails, bookend_detail = _bookend_check(gcode_text)
    layout_fails: list[str] = []
    extrusion_bounds = extrusion_xyz_bounds(gcode_text)
    if belt_insole_place and extrusion_bounds:
        width = float(stats.get("oriented_x_across_belt_mm", 0))
        length = float(stats.get("oriented_y_along_belt_mm", 0))
        layout_fails = assert_apex_insole_extrusion_layout(
            extrusion_bounds, width_mm=width, length_along_belt_mm=length
        )
        layout_fails.extend(assert_toe_first_gcode(gcode_text))
    all_fails = compare_fails + bookend_fails + layout_fails

    store_name = store_name or f"proof_{label}.gcode"
    store_path = STORE_GCODE / store_name
    try:
        STORE_GCODE.mkdir(parents=True, exist_ok=True)
        store_path.write_text(gcode_text, encoding="utf-8")
        saved_to = str(store_path)
    except OSError:
        saved_to = str(gcode_path)

    return {
        "label": label,
        "stl": str(stl.relative_to(REPO)),
        "profile_id": profile_id,
        "process_overrides": process_overrides or {},
        "stl_dimensions": _stl_report(stl, rotate_z),
        "gcode_dimensions": _gcode_xyz_bounds_mm(gcode_text),
        "extrusion_bounds": extrusion_bounds,
        "placement": {k: stats[k] for k in stats if k.endswith("_mm") or "oriented" in k or "bed_width" in k},
        "gcode_bytes": gcode_path.stat().st_size,
        "gcode_saved_to": saved_to,
        "slice_seconds": stats.get("elapsed_s"),
        "peak_rss_kb": stats.get("peak_rss_kb"),
        "layer_count": _layer_count(gcode_text),
        "estimated_print_time": _est_print_time(gcode_text),
        "bookends": bookend_detail,
        "compare_to_reference_failures": compare_fails,
        "bookend_failures": bookend_fails,
        "layout_failures": layout_fails,
        "all_failures": all_fails,
        "measured_sample": {
            k: measured.get(k)
            for k in (
                "temp_225",
                "belt_z_step",
                "outer_mm_s_mode",
                "infill_mm_s_mode",
                "part_fan",
                "aux_fan",
            )
        },
    }


def main() -> int:
    if not MODEL_SAMPLE.is_file():
        gen = REPO / "validation" / "test_models" / "generate_insole_sample.py"
        subprocess.check_call([sys.executable, str(gen)])

    report: dict = {
        "real_slice_ran": True,
        "docker_image_size_mb": docker_image_size_mb(),
        "cura_ref_extrusion_bounds": (
            extrusion_xyz_bounds(REF.read_text(encoding="utf-8", errors="replace")) if REF.is_file() else {}
        ),
        "runs": [],
    }
    failures: list[str] = []

    try:
        report["runs"].append(
            _run_slice(
                "insole_sample_soft20",
                MODEL_SAMPLE,
                "apex-v2-insole-95a",
                process_overrides={"wall_loops": 1},
                compare_ref=True,
            )
        )
        report["runs"].append(
            _run_slice(
                "orthocad_medium26",
                MODEL_ORTHOCAD,
                "apex-v2-insole-medium",
                process_overrides={"wall_loops": 1, "sparse_infill_density": "26%"},
                compare_ref=False,
                belt_insole_place=True,
                store_name="orthocad_sample_top_medium26.gcode",
            )
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc), "real_slice_ran": False}, indent=2))
        return 1

    for run in report["runs"]:
        for f in run.get("all_failures") or []:
            failures.append(f"{run['label']}: {f}")

    report["failure_count"] = len(failures)
    report["proof_pass"] = not failures
    print(json.dumps(report, indent=2))

    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("PROOF PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
