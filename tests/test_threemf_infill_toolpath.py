"""Slice a slab 3MF and compare sparse-infill spacing inside vs outside the modifier.

Skipped when orca-slicer is not installed. The locked STL golden is not involved.
"""
from __future__ import annotations

import math
import os
import shutil
import statistics
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.profile_registry import get_profile  # noqa: E402
from server.slice_engine import _slicer_binary  # noqa: E402
from server.threemf_modifiers import prepare_belt_3mf  # noqa: E402
from tests.test_threemf_modifiers import write_modifier_3mf  # noqa: E402

# Gyroid line distance scales as 1/density (FillGyroid::DensityAdjust cancels).
_SQRT2 = math.sqrt(2.0)


def _binary() -> Path | None:
    env = os.environ.get("ORCA_SLICER_BIN")
    if env and Path(env).is_file():
        return Path(env)
    try:
        return _slicer_binary()
    except FileNotFoundError:
        return None


def _parse_sparse(gcode: str) -> list[dict]:
    """In-plane sparse-infill extrusions. G-code Y is gantry (√2 × virtual Y); X is model X."""
    feature = ""
    x = y = z = e_abs = None
    absolute_xyz = True
    absolute_e = False
    moves = []
    for raw in gcode.splitlines():
        line = raw.split(";", 1)[0].strip()
        if raw.startswith(";TYPE:"):
            feature = raw.split(":", 1)[1].strip()
            continue
        if not line:
            continue
        head = line.split()[0]
        if head == "G90":
            absolute_xyz = True
            continue
        if head == "G91":
            absolute_xyz = False
            continue
        if head == "M82":
            absolute_e = True
            continue
        if head == "M83":
            absolute_e = False
            continue
        if head not in ("G0", "G1", "G2", "G3"):
            continue
        words = {}
        for part in line.split()[1:]:
            if len(part) > 1 and part[0] in "XYZEF":
                try:
                    words[part[0]] = float(part[1:])
                except ValueError:
                    pass
        px, py = x, y
        if absolute_xyz:
            x = words.get("X", x)
            y = words.get("Y", y)
            z = words.get("Z", z)
        else:
            if "X" in words and x is not None:
                x += words["X"]
            if "Y" in words and y is not None:
                y += words["Y"]
            if "Z" in words and z is not None:
                z += words["Z"]
        extruded = None
        if "E" in words:
            if absolute_e:
                if e_abs is None:
                    e_abs = words["E"]
                else:
                    extruded = words["E"] - e_abs
                    e_abs = words["E"]
            else:
                extruded = words["E"]
        if (
            feature == "Sparse infill"
            and extruded is not None
            and extruded > 1e-6
            and None not in (px, py, x, y)
        ):
            x0, y0 = px, py / _SQRT2
            x1, y1 = x, y / _SQRT2
            length = math.hypot(x1 - x0, y1 - y0)
            if length <= 0:
                continue
            moves.append(
                {
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "x": (x0 + x1) / 2,
                    "y": (y0 + y1) / 2,
                    "z": z if z is not None else 0.0,
                    "length": length,
                    "e": extruded,
                }
            )
    return moves


def _polylines(segments: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    current: list[dict] = []
    for seg in segments:
        if current:
            prev = current[-1]
            if math.hypot(seg["x0"] - prev["x1"], seg["y0"] - prev["y1"]) > 0.05:
                lines.append(current)
                current = []
        current.append(seg)
    if current:
        lines.append(current)
    return lines


def _spacing(moves: list[dict], x0: float, x1: float) -> dict:
    chosen = [m for m in moves if x0 <= m["x"] <= x1]
    by_layer: dict[float, list[dict]] = {}
    for move in chosen:
        by_layer.setdefault(round(move["z"], 3), []).append(move)
    samples: list[float] = []
    for layer in by_layer.values():
        lines = [ln for ln in _polylines(layer) if sum(s["length"] for s in ln) > 1.0]
        if len(lines) < 2:
            continue
        clouds = [[(s["x"], s["y"]) for s in ln] for ln in lines]
        for i, cloud in enumerate(clouds):
            for x, y in cloud[:: max(1, len(cloud) // 8)]:
                best = None
                for j, other in enumerate(clouds):
                    if i == j:
                        continue
                    for ox, oy in other:
                        dist = math.hypot(x - ox, y - oy)
                        if dist < 0.5:
                            continue
                        if best is None or dist < best:
                            best = dist
                if best is not None and best < 12:
                    samples.append(best)
    length = sum(m["length"] for m in chosen)
    filament = sum(m["e"] for m in chosen)
    return {
        "count": len(chosen),
        "layers": len(by_layer),
        "length_mm": round(length, 2),
        "filament_mm": round(filament, 2),
        "e_per_mm": round((filament / length), 5) if length else 0.0,
        "spacing_mm": round(statistics.median(samples), 3) if samples else 0.0,
        "width_mm": round(x1 - x0, 3),
        "length_per_mm": round((length / (x1 - x0)), 3) if x1 > x0 else 0.0,
    }


class ToolpathTest(unittest.TestCase):
    def test_region_spacing_tracks_density(self):
        binary = _binary()
        if binary is None:
            self.skipTest("orca-slicer binary not available")
        os.environ["ORCA_SLICER_BIN"] = str(binary)
        os.environ.setdefault("ORCA_RESOURCES", str(REPO / "resources"))
        profile = get_profile("apex-v2-insole-95a", material="apex_core")
        self.assertIsNotNone(profile)
        folder = REPO / "tests" / "_toolpath_work"
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
        from server.slice_engine import slice_stl

        # Across-belt axis after the 270° belt move is the input Y axis.
        # Modifier occupies input Y 40..68, which lands on the high-X side.
        report = {}
        try:
            for label, density in (("soft20", "20%"), ("extra14", "14%")):
                src = folder / f"{label}.3mf"
                # Model Z is the belt cross-section height. Two 1 mm walls need
                # more than 4.6 mm or the layer is all perimeter and has no infill.
                write_modifier_3mf(
                    src,
                    main=(0, 0, 0, 36, 70, 16),
                    modifier=(0, 40, -1, 36, 68, 18),
                    density=density,
                    pattern="gyroid",
                )
                _placed, meta = prepare_belt_3mf(src, dest_dir=folder / label)
                mod = meta["placed_modifiers"][0]
                main = meta["placed_main_bounds"]
                # Inset past the 2×1 mm walls and the density boundary.
                inside = (mod["xmin"] + 3.0, min(mod["xmax"], main["xmax"]) - 3.0)
                outside = (main["xmin"] + 3.0, mod["xmin"] - 3.0)
                gcode_path, stats = slice_stl(
                    src,
                    profile,
                    work_dir=folder / f"{label}_slice",
                    timeout_s=900,
                    process_overrides={"sparse_infill_density": "26%"},
                    belt_insole_place=True,
                    print_direction="toe_first",
                    material="apex_core",
                )
                text = gcode_path.read_text(encoding="utf-8", errors="replace")
                moves = _parse_sparse(text)
                inn = _spacing(moves, *inside)
                out = _spacing(moves, *outside)
                report[label] = {
                    "regions_applied": stats.get("regions_applied"),
                    "inside_x": inside,
                    "outside_x": outside,
                    "inside": inn,
                    "outside": out,
                    "spacing_ratio_inside_over_outside": (inn["spacing_mm"] / out["spacing_mm"])
                    if out["spacing_mm"]
                    else None,
                    "length_density_ratio_inside_over_outside": (inn["length_per_mm"] / out["length_per_mm"])
                    if out["length_per_mm"]
                    else None,
                }
                self.assertEqual(stats.get("regions_applied"), 1)
                self.assertGreater(inn["count"], 20, report[label])
                self.assertGreater(out["count"], 20, report[label])
                self.assertGreater(inn["spacing_mm"], out["spacing_mm"], report[label])
                # Same bead. Density changes how far apart the lines are.
                self.assertAlmostEqual(inn["e_per_mm"], out["e_per_mm"], delta=0.01, msg=report)
            # 20% lines are farther apart than 26% by 26/20. 14% by 26/14.
            soft = report["soft20"]["spacing_ratio_inside_over_outside"]
            extra = report["extra14"]["spacing_ratio_inside_over_outside"]
            self.assertAlmostEqual(soft, 26 / 20, delta=0.35, msg=report)
            self.assertAlmostEqual(extra, 26 / 14, delta=0.45, msg=report)
            # Path length per millimetre of width tracks density, so the softer band is shorter.
            soft_len = report["soft20"]["length_density_ratio_inside_over_outside"]
            extra_len = report["extra14"]["length_density_ratio_inside_over_outside"]
            self.assertAlmostEqual(soft_len, 20 / 26, delta=0.15, msg=report)
            self.assertAlmostEqual(extra_len, 14 / 26, delta=0.15, msg=report)
        finally:
            note = folder / "report.txt"
            note.write_text(repr(report), encoding="utf-8")
            print("TOOLPATH", report)
