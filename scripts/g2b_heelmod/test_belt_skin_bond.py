#!/usr/bin/env python3
"""The toe slot with no internal bead gets two strips, and nothing else moves."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO / "validation")]

from belt_skin_bond import bond_apex_light, bond_gcode, measure_toe_air  # noqa: E402
from belt_skin_gate import score_gcode  # noqa: E402
from belt_skin_physical import audit_texts  # noqa: E402


def _pocket(filament_id: str = "ApexCore") -> str:
    # One layer, so the whole layer is the toe. Walls match the published
    # no-bead column: centres 1.706 mm apart, beads 1.04 mm, air 0.666 mm.
    return "\n".join(
        [
            "; HEADER_BLOCK_START",
            f'; filament_settings_id = "{filament_id}"',
            "; filament_diameter: 1.75",
            "; slow_down_layer_time = 0",
            "; HEADER_BLOCK_END",
            "M83",
            ";LAYER_CHANGE",
            ";Z:1.000",
            ";TYPE:Outer wall",
            ";WIDTH:1.040",
            "G1 X0 Y0 F1560",
            "G1 X20 Y0 E3.000",
            "G1 X0 Y1.706",
            "G1 X20 Y1.706 E3.000",
            "; estimated printing time (normal mode) = 0h 0m 2s",
            "; filament used [mm] = 6.00",
            "; filament used [g] = 0.02",
            "",
        ]
    )


def _subsequence(original: list[str], updated: list[str], skip: set[str]) -> bool:
    index = 0
    for line in updated:
        if index < len(original) and line == original[index]:
            index += 1
        elif index < len(original) and original[index] in skip:
            index += 1
            if line == original[index] if index < len(original) else False:
                index += 1
    return index == len(original) or all(original[i] in skip for i in range(index, len(original)))


class BeltSkinBondTest(unittest.TestCase):
    def test_empty_slot_is_filled_and_original_lines_stay(self) -> None:
        src = _pocket()
        before = measure_toe_air(src)
        self.assertGreater(before["bands"]["0-15"]["air_mm3"], 0.0)
        out = bond_gcode(src, material="apex_core")
        after = measure_toe_air(out)
        self.assertEqual(after["bands"]["0-15"]["air_mm3"], 0.0)
        self.assertEqual(after["bands"]["15-30"]["air_mm3"], 0.0)
        self.assertIn("belt_skin_bond filled_runs=", out)
        counter = [line for line in out.splitlines() if line.startswith("; belt_skin_bond filled_runs=")]
        self.assertTrue(counter)
        filled = int(counter[0].split("filled_runs=")[1].split()[0])
        widened = int(counter[0].split("widened=")[1])
        self.assertGreater(filled + widened, 0)
        # No new bead over the cap. Path points of the original moves stay.
        self.assertEqual(score_gcode_text(out)["beads_over_1_6"], 0)
        live = score_gcode_text(src)
        branch = score_gcode_text(out)
        self.assertLessEqual(branch["travel_mm"], live["travel_mm"] + 0.05)
        for line in src.splitlines():
            if line.startswith("G1") and "E" not in line.split(";", 1)[0]:
                self.assertIn(line, out.splitlines())
            if line.startswith(";TYPE:"):
                self.assertIn(line, out.splitlines())
        again = bond_gcode(out, material="apex_core")
        self.assertEqual(again, out)

    def test_hairline_fill_fails_the_physical_gate(self) -> None:
        # The shape this bond emitted before the printable-width limit: a
        # 0.10 mm strip at 750 mm/s that starts on an outer-wall vertex.
        live = _pocket()
        hair = live.replace(
            "G1 X20 Y1.706 E3.000",
            "G1 X20 Y1.706 E3.000\nG1 X0.000 Y0.853 E0.01000 F45000\nG1 X20.000 Y0.853 E0.20000 F45000",
        )
        audit = audit_texts(live, hair)
        for name in ("1_width", "2_feed", "3_outer_wall", "4_air", "5_time_filament"):
            self.assertFalse(audit["limits"][name]["ok"], name)
        self.assertFalse(audit["ok"])

    def test_thick_body_is_not_filled(self) -> None:
        lines = _pocket().splitlines()
        text = "\n".join(line.replace("Y1.706", "Y12.000") for line in lines) + "\n"
        out = bond_gcode(text, material="apex_core")
        self.assertEqual(out, text)

    def test_heel_band_is_not_filled(self) -> None:
        body = _pocket().splitlines()
        heel = [line if not line.startswith(";Z:") else ";Z:10.000" for line in body]
        text = "\n".join(
            [
                "; filament_diameter: 1.75",
                "; slow_down_layer_time = 0",
                "M83",
                ";LAYER_CHANGE",
                ";Z:0.000",
                ";TYPE:Outer wall",
                ";WIDTH:1.040",
                "G1 X0 Y5 F1560",
                "G1 X20 Y5 E3.000",
                *heel[1:],
            ]
        )
        # The open slot is at Z=10, which is the heel of a 0..10 part.
        out = bond_gcode(text, material="apex_core")
        self.assertNotIn("; belt_skin_bond run", out)

    def test_apex_light_hook_fills_and_forge_does_not(self) -> None:
        light = _pocket("ApexLight")
        out = bond_apex_light(light)
        self.assertIn("filled_runs=", out)
        self.assertEqual(measure_toe_air(out)["bands"]["0-15"]["air_mm3"], 0.0)
        # Existing width comments are not rewritten.
        self.assertIn(";WIDTH:1.040", out)
        forge = _pocket("ApexForge")
        self.assertEqual(bond_apex_light(forge), forge)
        bounce = _pocket("ApexBounce")
        self.assertEqual(bond_apex_light(bounce), bounce)

    def test_production_hooks_bond_apex_and_leave_forge(self) -> None:
        os.environ.pop("BELT_SKIN_BOND", None)
        from postprocess_g2b import clear_perimeter_crossings
        import topfix_apexcore

        light = _pocket("ApexLight")
        bonded = clear_perimeter_crossings(light)
        self.assertIn("; belt_skin_bond filled_runs=", bonded)
        filled = int(bonded.split("filled_runs=")[1].split()[0])
        self.assertGreater(filled, 0)
        forge = _pocket("ApexForge")
        self.assertNotIn("; belt_skin_bond", clear_perimeter_crossings(forge))
        work = Path("/tmp/belt-skin-bond-topfix")
        work.mkdir(exist_ok=True)
        src = work / "in.gcode"
        dest = work / "out.gcode"
        src.write_text(_pocket("ApexCore"))
        old = sys.argv
        sys.argv = ["topfix_apexcore.py", str(src), str(dest)]
        try:
            topfix_apexcore.main()
        finally:
            sys.argv = old
        written = dest.read_text()
        self.assertIn("; belt_skin_bond filled_runs=", written)
        self.assertGreater(int(written.split("filled_runs=")[1].split()[0]), 0)

    def test_disabled_env_keeps_apex_light(self) -> None:
        os.environ["BELT_SKIN_BOND"] = "0"
        try:
            src = _pocket("ApexLight")
            self.assertEqual(bond_apex_light(src), src)
        finally:
            os.environ.pop("BELT_SKIN_BOND", None)


def score_gcode_text(text: str) -> dict:
    path = Path("/tmp/belt-skin-bond-score.gcode")
    path.write_text(text)
    return score_gcode(path)


if __name__ == "__main__":
    unittest.main()
