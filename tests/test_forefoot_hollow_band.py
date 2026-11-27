"""ApexForge and ApexBounce forefoot hollow fill on the generic slice path.

One function, ``apply_forefoot_hollow_band``, runs for both materials. It is
ApexCore #46's thick bead plus band infill, scaled to this layer height.
Needs the orca-slicer binary (ORCA_SLICER_BIN or the usual build/image path).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.material_y_offset import apply_material_y_offset  # noqa: E402
from server.forefoot_hollow_band import (  # noqa: E402
    _confine_added_sparse,
    _deposited_mm_scan,
    _with_filament,
    added_sparse_outside_hollow,
    apply_forefoot_hollow_band,
    gram_footers,
    hollow_column_count,
    scaled_forefoot_window,
    sparse_travel_violations,
)
from server.profile_registry import applied_infill_density, get_profile  # noqa: E402
from server.slice_engine import slice_stl  # noqa: E402
from server.slice_overrides import parse_slice_overrides  # noqa: E402

ORTHOCAD = REPO / "validation" / "test_models" / "orthocad_sample_top_insole.stl"
SAMPLE = REPO / "validation" / "test_models" / "insole_sample.stl"
PROFILE = "apex-v2-insole-95a"
BASE = "d928bdbd53cf0a45ce9a91b275bdc5cd51a77df7"
SKIN = {"Outer wall", "Overhang wall"}
Ere = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+))")
FILAMENT_MM = re.compile(r"; filament used \[mm\] = ([0-9.]+)")
FILAMENT_G = re.compile(r"; filament used \[g\] = ([0-9.]+)")
TEMP = re.compile(r"^(M104|M109|M140|M190)\b.*$")
LAYER_H = re.compile(r"^;\s*layer_height\s*=\s*([0-9.]+)\s*$", re.M)


# apex_light.py is not frozen against BASE. The hollow fill already changed it,
# and the belt Y offset is applied after fill_forefoot_hollow. That call order
# is locked in tests/test_material_y_offset.py and below.
_CHAIN_FILES = (
    "server/chains/apex_core.py",
    "scripts/g2b_heelmod/postprocess_g2b.py",
)


def _unchanged_vs_base() -> list[dict] | None:
    """None when this machine has no git. The slice image does not."""
    if shutil.which("git") is None:
        return None
    rows = []
    for path in _CHAIN_FILES:
        diff = subprocess.run(
            ["git", "diff", "--quiet", BASE, "--", path],
            cwd=REPO,
            check=False,
        )
        rows.append({"path": path, "identical_to_base": diff.returncode == 0})
    return rows


def _slicer_present() -> bool:
    try:
        from server.slice_engine import _slicer_binary

        _slicer_binary()
    except FileNotFoundError:
        return False
    return True


def _overrides() -> dict:
    overrides = parse_slice_overrides("1", "26", "toe_first")
    overrides["print_direction"] = "toe_first"
    overrides["sparse_infill_density"] = applied_infill_density(PROFILE, "26")
    return overrides


def _layer_height(text: str) -> float:
    match = LAYER_H.search(text)
    if match is None:
        raise AssertionError("layer_height missing")
    return float(match.group(1))


def _z_lines(text: str) -> list[str]:
    found: list[str] = []
    pending = False
    for line in text.splitlines():
        if line.startswith(";LAYER_CHANGE"):
            pending = True
        elif pending and line.startswith(";Z:"):
            found.append(line)
            pending = False
    return found


def _temps(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if TEMP.match(line.strip())]


def _filament(text: str) -> tuple[float, float]:
    mm = FILAMENT_MM.search(text)
    grams = FILAMENT_G.search(text)
    if mm is None or grams is None:
        raise AssertionError("filament header missing")
    return float(mm.group(1)), float(grams.group(1))


def _extrusions(text: str, height: float) -> list[tuple]:
    area = math.pi * (0.875 ** 2)
    corner = height * (1.0 - math.pi / 4.0)
    x = y = e = 0.0
    relative = False
    layer = -1
    kind = ""
    out = []
    for line in text.splitlines():
        raw = line.strip()
        if raw.startswith(";LAYER_CHANGE"):
            layer += 1
            continue
        if raw.startswith(";TYPE:"):
            kind = raw[6:].strip()
            continue
        body = raw.split(";", 1)[0].strip()
        if body.startswith("M82"):
            relative = False
            continue
        if body.startswith("M83"):
            relative = True
            continue
        if body.startswith("G92"):
            words = dict(Ere.findall(body))
            if "E" in words:
                e = float(words["E"])
            if "X" in words:
                x = float(words["X"])
            if "Y" in words:
                y = float(words["Y"])
            continue
        if not (body.startswith("G0") or body.startswith("G1")):
            continue
        words = dict(Ere.findall(body))
        ox, oy = x, y
        if "X" in words:
            x = float(words["X"])
        if "Y" in words:
            y = float(words["Y"])
        de = 0.0
        if "E" in words:
            value = float(words["E"])
            de = value if relative else value - e
            if not relative:
                e = value
        if (abs(x - ox) > 1e-9 or abs(y - oy) > 1e-9) and de > 1e-9:
            length = math.hypot(x - ox, y - oy) or 1e-9
            width = (de / length) * area / height + corner
            out.append((layer, kind, ox, oy, x, y, de, width))
    return out


def _skin_key(seg: tuple) -> tuple:
    return (
        seg[0],
        seg[1],
        round(seg[2], 3),
        round(seg[3], 3),
        round(seg[4], 3),
        round(seg[5], 3),
        round(seg[6], 5),
    )


def _seams(segs: list[tuple]) -> dict[int, tuple[float, float]]:
    found: dict[int, tuple[float, float]] = {}
    for seg in segs:
        if seg[1] in SKIN and seg[0] not in found:
            found[seg[0]] = (round(seg[2], 3), round(seg[3], 3))
    return found


def _off_bottom(segs: list[tuple], seams: dict[int, tuple[float, float]]) -> int:
    """Seam points more than 0.25 mm above that layer's lowest skin."""
    lows: dict[int, float] = {}
    for seg in segs:
        if seg[1] not in SKIN:
            continue
        y = min(seg[3], seg[5])
        lows[seg[0]] = y if seg[0] not in lows else min(lows[seg[0]], y)
    return sum(1 for layer, (_x, y) in seams.items() if y - lows.get(layer, y) > 0.25)


def _hollow(text: str, _height: float, last: int) -> tuple[int, float]:
    """0.25 mm columns and the 0.46 mm envelope. ``_height`` is the sliced layer height."""
    return hollow_column_count(text, last)


def _bond_columns(text: str, height: float, last: int) -> dict[str, int]:
    """In-repo 0.5 mm column rule. ``_thick_bonded`` is the frozen-gate overlap."""
    g2b_dir = REPO / "scripts" / "g2b_heelmod"
    if str(g2b_dir) not in sys.path:
        sys.path.insert(0, str(g2b_dir))
    import postprocess_g2b as g2b

    saved = g2b.FOREFOOT_LAYER_H
    try:
        g2b.FOREFOOT_LAYER_H = height
        parsed = g2b._parse_moves(text.splitlines(keepends=True), reset_type=False)
        segs = [seg for seg in parsed["segs"] if 1 <= seg["layer"] <= last]
        cols = g2b._columns(segs)
    finally:
        g2b.FOREFOOT_LAYER_H = saved
    kinds = [seg["kind"] for seg in segs]
    total = unbonded = 0
    for key, beads in cols.items():
        if not (1 <= key[0] <= last):
            continue
        pair = g2b._pair_of(beads, kinds)
        if pair is None:
            continue
        plow, phigh, dlow, dhigh, _thick, _gap = pair
        total += 1
        if not g2b._thick_bonded(beads, plow, phigh, dlow, dhigh):
            unbonded += 1
    return {"columns": total, "unbonded": unbonded}


def _outline_beyond_mm(live: list[tuple], cand: list[tuple]) -> float:
    """How far a new bead sticks past the live outer-wall outline plus 0.02 mm."""
    from shapely.geometry import LineString, MultiPolygon, Polygon
    from shapely.ops import unary_union

    live_keys = {_skin_key(seg) for seg in live}
    skins: dict[int, list[tuple]] = defaultdict(list)
    for seg in live:
        if seg[1] in SKIN:
            skins[seg[0]].append(seg)
    new = [seg for seg in cand if _skin_key(seg) not in live_keys]
    worst = 0.0
    for layer in sorted({seg[0] for seg in new}):
        caps = []
        for seg in skins.get(layer, ()):
            if math.hypot(seg[4] - seg[2], seg[5] - seg[3]) < 1e-4:
                continue
            caps.append(
                LineString([(seg[2], seg[3]), (seg[4], seg[5])]).buffer(
                    max(seg[7] * 0.5, 0.05), cap_style=1, join_style=1
                )
            )
        if not caps:
            continue
        band = unary_union(caps)
        geoms = list(band.geoms) if isinstance(band, MultiPolygon) else [band]
        parts = []
        for geom in geoms:
            parts.append(geom)
            for ring in geom.interiors:
                parts.append(Polygon(ring))
        allowed = unary_union(parts).buffer(0.02)
        for seg in new:
            if seg[0] != layer or math.hypot(seg[4] - seg[2], seg[5] - seg[3]) < 1e-4:
                continue
            bead = LineString([(seg[2], seg[3]), (seg[4], seg[5])]).buffer(
                max(seg[7] * 0.5, 0.01), cap_style=1
            )
            if allowed.covers(bead):
                continue
            lo, hi = 0.0, 5.0
            for _ in range(14):
                mid = (lo + hi) / 2.0
                if allowed.buffer(mid).covers(bead):
                    hi = mid
                else:
                    lo = mid
            worst = max(worst, hi)
    return worst


class SparseFillStaysInHollowSections(unittest.TestCase):
    """Added sparse infill has to land in a previously hollow >=3 mm column.

    The column is the 0.25 mm tool: two outer skins at least 3 mm apart and
    more than 1 mm of air. A bead in some other 3–6 mm section is not kept.
    """

    def test_a_bead_outside_a_hollow_section_is_removed(self) -> None:
        skins = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "; filament used [mm] = 10.00",
                "; filament used [cm3] = 1.00",
                "; filament used [g] = 1.00",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
            ]
        ) + "\n"
        added = skins + "\n".join(
            [
                ";TYPE:Sparse infill",
                "G1 X104 Y2 F30000",
                "G1 X106 Y2 E0.90000",
                "G1 X49 Y2 F30000",
                "G1 X51 Y2 E1.00000",
                "G1 X104 Y10 F30000",
                "G1 X106 Y10 E1.10000",
            ]
        ) + "\n"
        confined = _confine_added_sparse(skins, added)
        self.assertEqual(added_sparse_outside_hollow(skins, confined), 0, confined)
        self.assertIn("G1 X106 Y2 E0.90000", confined)
        self.assertNotIn("E1.00000", confined)
        self.assertNotIn("E1.10000", confined)
        self.assertNotIn("X51", confined)
        self.assertNotIn("X106 Y10", confined)
        self.assertEqual(sparse_travel_violations(skins, confined), {"open_air": 0, "wall_rides": 0}, confined)

    def test_wall_ride_and_open_air_travels_stay_off_the_outer_wall(self) -> None:
        skins = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "; filament used [mm] = 10.00",
                "; filament used [cm3] = 1.00",
                "; filament used [g] = 1.00",
                "; total filament used [g] = 1.00",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X112 Y4 E0.90000",
            ]
        ) + "\n"
        # The continuation's absolute E already includes the kept bead (0.05).
        # The wall-ride and the open-air chord are the travels that must not survive.
        added = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "; filament used [mm] = 10.00",
                "; filament used [cm3] = 1.00",
                "; filament used [g] = 1.00",
                "; total filament used [g] = 1.00",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
                ";TYPE:Sparse infill",
                "G1 X104 Y2 F30000",
                "G1 X106 Y2 E0.85000",
                "G1 X100 Y0 F30000",
                "G1 X110 Y0 F30000",
                "G1 X50 Y20 F30000",
                "G1 X110 Y4 F30000",
                ";TYPE:Outer wall",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X112 Y4 E0.95000",
            ]
        ) + "\n"
        confined = _confine_added_sparse(skins, added)
        self.assertIn("G1 X112 Y4 E0.95000", confined)
        self.assertIn("G1 X110 Y0 E0.40000", confined)
        self.assertNotIn("G1 X110 Y0 F30000", confined)
        self.assertNotIn("X50 Y20", confined)
        self.assertEqual(added_sparse_outside_hollow(skins, confined), 0, confined)
        self.assertEqual(sparse_travel_violations(skins, confined), {"open_air": 0, "wall_rides": 0}, confined)
        self.assertIn(";LAYER_CHANGE", confined)

    def test_a_far_return_follows_a_live_travel(self) -> None:
        skins = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
                "G1 X105 Y2 F15000",
                "G1 X20 Y20 F15000",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X22 Y20 E0.90000",
            ]
        ) + "\n"
        added = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
                ";TYPE:Sparse infill",
                "G1 X104 Y2 F30000",
                "G1 X106 Y2 E0.85000",
                "G1 X20 Y20 F30000",
                ";TYPE:Outer wall",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X22 Y20 E0.95000",
            ]
        ) + "\n"
        confined = _confine_added_sparse(skins, added)
        self.assertIn("G1 X22 Y20 E0.95000", confined)
        self.assertIn("G1 X110 Y0 E0.40000", confined)
        self.assertNotIn("G1 X20 Y20 F30000", confined)
        self.assertEqual(sparse_travel_violations(skins, confined), {"open_air": 0, "wall_rides": 0}, confined)

    def test_a_dropped_bead_does_not_pull_the_nozzle_off_the_live_point(self) -> None:
        skins = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X112 Y4 E0.90000",
            ]
        ) + "\n"
        added = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
                ";TYPE:Sparse infill",
                "G1 X104 Y2 F30000",
                "G1 X106 Y2 E0.85000",
                "G1 X51 Y2 F30000",
                ";TYPE:Outer wall",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X112 Y4 E0.95000",
            ]
        ) + "\n"
        confined = _confine_added_sparse(skins, added)
        self.assertIn("G1 X106 Y2 E0.85000", confined)
        self.assertNotIn("X51", confined)
        self.assertIn("G1 X112 Y4 E0.95000", confined)
        self.assertEqual(sparse_travel_violations(skins, confined), {"open_air": 0, "wall_rides": 0}, confined)

    def test_gram_footers_match_the_kept_plastic(self) -> None:
        skins = "\n".join(
            [
                "; layer_height = 0.42",
                "; filament_diameter: 1.75",
                "; filament_density: 1.2",
                "; filament used [mm] = 10.00",
                "; filament used [cm3] = 1.00",
                "; filament used [g] = 1.00",
                "; total filament used [g] = 1.01",
                "M82",
                ";LAYER_CHANGE",
                ";LAYER_CHANGE",
                ";TYPE:Outer wall",
                "G1 X100 Y0 F1000",
                "G1 X110 Y0 E0.40000",
                "G1 X100 Y4 F30000",
                "G1 X110 Y4 E0.80000",
            ]
        ) + "\n"
        added = skins + "\n".join(
            [
                ";TYPE:Sparse infill",
                "G1 X104 Y2 F30000",
                "G1 X106 Y2 E10.80000",
                "G1 X49 Y2 F30000",
                "G1 X51 Y2 E20.80000",
            ]
        ) + "\n"
        confined = _confine_added_sparse(skins, added)
        updated = _with_filament(skins, confined)
        used, total = gram_footers(updated)
        self.assertIsNotNone(used)
        self.assertEqual(used, total)
        kept_mm = _deposited_mm_scan(confined) - _deposited_mm_scan(skins)
        self.assertEqual(kept_mm, Decimal("10"))
        added_g = kept_mm * Decimal(str(math.pi)) * (Decimal("1.75") / 2) ** 2 / Decimal(1000) * Decimal("1.2")
        expected = (Decimal("1.00") + added_g).quantize(Decimal("0.01"))
        # Live `; total filament used [g]` was 1.01, one hundredth above the
        # per-filament line. Both footers are rewritten to the same value.
        self.assertEqual(used, float(expected))
        rendered = format(expected, "f")
        self.assertIn(f"; filament used [g] = {rendered}", updated)
        self.assertIn(f"; total filament used [g] = {rendered}", updated)
        self.assertIn("; filament used [mm] = 20.00", updated)
        self.assertNotIn("E20.80000", updated)


class ForefootWindow(unittest.TestCase):
    def test_orthocad_042_matches_the_046_band(self) -> None:
        window = scaled_forefoot_window(0.42, 481)
        self.assertEqual(window["last_layer"], 181)
        self.assertEqual(window["band_first"], 171)
        self.assertEqual(window["band_last"], 181)

    def test_core_height_is_the_unscaled_window(self) -> None:
        window = scaled_forefoot_window(0.46, 439)
        self.assertEqual(window["last_layer"], 165)
        self.assertEqual(window["band_first"], 156)
        self.assertEqual(window["band_last"], 165)

    def test_short_part_is_left_untouched(self) -> None:
        self.assertIsNone(scaled_forefoot_window(0.42, 30))


@unittest.skipUnless(_slicer_present(), "orca-slicer binary not present")
class GenericForefootHollowBand(unittest.TestCase):
    def test_forge_and_bounce_share_the_fill(self) -> None:
        self.assertTrue(ORTHOCAD.is_file(), ORTHOCAD)
        self.assertTrue(SAMPLE.is_file(), SAMPLE)
        out_dir = Path(os.environ.get("FOREFOOT_GCODE_DIR", "")).expanduser() if os.environ.get("FOREFOOT_GCODE_DIR") else None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)

        import server.forefoot_hollow_band as band

        calls: list[str] = []
        real = band.apply_forefoot_hollow_band

        def spy(text: str) -> str:
            calls.append("apply_forefoot_hollow_band")
            return real(text)

        band.apply_forefoot_hollow_band = spy
        report: dict = {"calls": [], "materials": {}}
        try:
            for material in ("apex_forge", "apex_bounce"):
                report["materials"][material] = {
                    "orthocad": self._slice_pair(material, ORTHOCAD, calls),
                    "insole_sample": self._slice_pair(material, SAMPLE, calls),
                }
            before = len(calls)
            core = self._slice_core()
            report["apex_core"] = core
            report["calls_during_core"] = len(calls) - before
            report["calls"] = calls
        finally:
            band.apply_forefoot_hollow_band = real
            if out_dir is not None:
                (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        self.assertGreaterEqual(calls.count("apply_forefoot_hollow_band"), 4)
        self.assertEqual(report["calls_during_core"], 0)
        forge = report["materials"]["apex_forge"]["orthocad"]
        bounce = report["materials"]["apex_bounce"]["orthocad"]
        self.assertEqual(forge["window"], bounce["window"])
        self.assertLess(forge["slice_s"], 260.0)
        self.assertLess(bounce["slice_s"], 260.0)
        for row in (forge, bounce):
            self.assertEqual(row["hollow_165"], 0, row)
            self.assertEqual(row["hollow_181"], 0, row)
            self.assertEqual(row["skins_identical"], True, row)
            self.assertEqual(row["seams_identical"], True, row)
            self.assertEqual(row["z_identical"], True, row)
            self.assertEqual(row["temps_identical"], True, row)
            self.assertEqual(row["off_bottom_live"], row["off_bottom_cand"])
            self.assertLessEqual(row["outline_beyond_mm"], 0.02, row)
            self.assertEqual(row["added_sparse_outside_hollow"], 0, row)
            self.assertEqual(row["travel_open_air"], 0, row)
            self.assertEqual(row["travel_wall_rides"], 0, row)
            self.assertEqual(row["filament_g_cand"], row["filament_g_total_cand"], row)
            self.assertGreater(row["filament_mm_cand"], row["filament_mm_live"])
            self.assertFalse(row["vsabs"])
        self.assertTrue(any(line.startswith("M109 S245") for line in forge["temps"]))
        self.assertTrue(any(line.startswith("M109 S225") for line in bounce["temps"]))
        self.assertTrue(any(line.startswith("M190 S60") for line in bounce["temps"]))
        for material in ("apex_forge", "apex_bounce"):
            sample = report["materials"][material]["insole_sample"]
            self.assertLess(sample["window"]["last_layer"], sample["window"]["band_last"])
            self.assertEqual(sample["byte_identical"], True, sample)
            self.assertEqual(sample["skins_identical"], True, sample)
            self.assertEqual(sample["seams_identical"], True, sample)
            self.assertEqual(sample["z_identical"], True, sample)
            self.assertEqual(sample["outline_beyond_mm"], 0.0, sample)
            self.assertFalse(sample["vsabs"])

    def _slice_pair(self, material: str, stl: Path, calls: list[str]) -> dict:
        profile = get_profile(PROFILE, material=material)
        self.assertIsNotNone(profile, material)
        seen: dict[str, str] = {}
        import server.forefoot_hollow_band as band

        real = band.apply_forefoot_hollow_band

        def capture(text: str) -> str:
            seen["before"] = text
            out = real(text)
            seen["after"] = out
            return out

        band.apply_forefoot_hollow_band = capture
        started = time.perf_counter()
        try:
            gcode, stats = slice_stl(
                stl,
                profile,
                belt_insole_place=True,
                print_direction="toe_first",
                process_overrides=_overrides(),
                material=material,
                timeout_s=880,
            )
            produced = gcode.read_text(encoding="utf-8", errors="replace")
        finally:
            band.apply_forefoot_hollow_band = real
        elapsed = time.perf_counter() - started
        before = seen["before"]
        after = seen["after"]
        # The band result is not the file on disk: the nozzle-height shift runs
        # after apply_forefoot_hollow_band.
        self.assertEqual(produced, apply_material_y_offset(after, material))
        height = _layer_height(after)
        live_segs = _extrusions(before, height)
        cand_segs = _extrusions(after, height)
        live_skins = Counter(_skin_key(seg) for seg in live_segs if seg[1] in SKIN)
        cand_skins = Counter(_skin_key(seg) for seg in cand_segs if seg[1] in SKIN)
        live_seams = _seams(live_segs)
        cand_seams = _seams(cand_segs)
        window = scaled_forefoot_window(height, after.count(";LAYER_CHANGE"))
        hollow_165, _air_165 = _hollow(after, height, 165)
        hollow_181, air_181 = _hollow(after, height, 181)
        mm_live, g_live = _filament(before)
        mm_cand, g_cand = _filament(after)
        _used_g, total_g = gram_footers(after)
        travels = sparse_travel_violations(before, after)
        row = {
            "material": material,
            "stl": stl.name,
            "layers": after.count(";LAYER_CHANGE"),
            "layer_height": height,
            "window": window,
            "slice_s": round(elapsed, 3),
            "engine_elapsed_s": stats.get("elapsed_s"),
            "hollow_165": hollow_165,
            "hollow_181": hollow_181,
            "max_air_181_mm": round(air_181, 3),
            "skins_identical": live_skins == cand_skins,
            "seams_identical": live_seams == cand_seams,
            "z_identical": _z_lines(before) == _z_lines(after),
            "temps_identical": _temps(before) == _temps(after),
            "temps": _temps(after),
            "off_bottom_live": _off_bottom(live_segs, live_seams),
            "off_bottom_cand": _off_bottom(cand_segs, cand_seams),
            "outline_beyond_mm": round(_outline_beyond_mm(live_segs, cand_segs), 4),
            "added_sparse_outside_hollow": added_sparse_outside_hollow(before, after),
            "filament_mm_live": mm_live,
            "filament_mm_cand": mm_cand,
            "filament_g_live": g_live,
            "filament_g_cand": g_cand,
            "filament_g_total_cand": total_g,
            "travel_open_air": travels["open_air"],
            "travel_wall_rides": travels["wall_rides"],
            "bond_165_live": _bond_columns(before, height, 165),
            "bond_165": _bond_columns(after, height, 165),
            "byte_identical": before == after,
            "vsabs": "VSABS" in after,
        }
        out_dir = os.environ.get("FOREFOOT_GCODE_DIR")
        if out_dir and stl.name.startswith("orthocad"):
            dest = Path(out_dir) / f"{material}_orthocad.gcode"
            dest.write_text(after, encoding="utf-8")
            (Path(out_dir) / f"{material}_orthocad_live.gcode").write_text(before, encoding="utf-8")
        elif out_dir:
            (Path(out_dir) / f"{material}_insole_sample.gcode").write_text(after, encoding="utf-8")
        calls.append(material)
        return row

    def _slice_core(self) -> dict:
        profile = get_profile(PROFILE, material="apex_core")
        self.assertIsNotNone(profile)
        started = time.perf_counter()
        gcode, stats = slice_stl(
            ORTHOCAD,
            profile,
            belt_insole_place=True,
            print_direction="toe_first",
            process_overrides=_overrides(),
            material="apex_core",
            timeout_s=880,
        )
        text = gcode.read_text(encoding="utf-8", errors="replace")
        out_dir = os.environ.get("FOREFOOT_GCODE_DIR")
        if out_dir:
            (Path(out_dir) / "apex_core_orthocad.gcode").write_text(text, encoding="utf-8")
        unchanged = _unchanged_vs_base()
        if unchanged is not None:
            for row in unchanged:
                self.assertTrue(row["identical_to_base"], row["path"])
        return {
            "slice_s": round(time.perf_counter() - started, 3),
            "engine_elapsed_s": stats.get("elapsed_s"),
            "chain": stats.get("chain"),
            "layers": text.count(";LAYER_CHANGE"),
            "bytes": len(text.encode("utf-8")),
            "unchanged_vs_base": unchanged,
        }


class ApexCoreSources(unittest.TestCase):
    def test_core_light_and_g2b_match_live_main(self) -> None:
        rows = _unchanged_vs_base()
        if rows is None:
            self.skipTest("git is not installed")
        for row in rows:
            self.assertTrue(row["identical_to_base"], row["path"])
        light = (REPO / "server/chains/apex_light.py").read_text(encoding="utf-8")
        self.assertLess(
            light.index("fill_forefoot_hollow(text)"),
            light.index("apply_material_y_offset("),
        )


if __name__ == "__main__":
    unittest.main()
