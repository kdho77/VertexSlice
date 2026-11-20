#!/usr/bin/env python3
"""Inner gap beads grow to a printable width. An empty outer-only slot does not."""
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


def _pocket_with_gap_bead() -> str:
    # E matches width 0.30 over 20 mm at layer height 0.46 and a 1.75 mm filament.
    # The bead sits in the 0.666 mm slot between the two outer walls.
    lines = _pocket().splitlines()
    bead = [
        "; layer_height = 0.46",
        "; gap_infill_speed = 26",
        "; internal_solid_infill_speed = 26",
        "; machine_max_speed_x = 500",
        "; machine_max_speed_y = 500",
        ";TYPE:Gap infill",
        ";WIDTH:0.300",
        "G1 X0 Y0.853 F3000",
        "G1 X20 Y0.853 E1.14761",
    ]
    # Insert the profile speeds and the bead before the time footer.
    out = []
    for line in lines:
        if line.startswith("; estimated printing time"):
            out.extend(bead)
        out.append(line)
    return "\n".join(out) + "\n"


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
    def test_outer_only_slot_is_not_sprayed(self) -> None:
        # Two outer walls and no inner bead. A hairline strip would start on
        # the outer wall and be under 0.45 mm. Leave the file unchanged.
        src = _pocket()
        self.assertEqual(bond_gcode(src, material="apex_core"), src)

    def test_inner_bead_grows_and_outer_wall_stays(self) -> None:
        src = _pocket_with_gap_bead()
        out = bond_gcode(src, material="apex_core")
        self.assertIn("widened=", out)
        widened = int(out.split("widened=")[1].split()[0])
        self.assertGreater(widened, 0)
        for line in src.splitlines():
            if "Y0 " in line or "Y1.706" in line:
                self.assertIn(line, out.splitlines())
        audit = audit_texts(src, out)
        self.assertTrue(audit["limits"]["1_width"]["ok"], audit["limits"]["1_width"])
        self.assertTrue(audit["limits"]["2_feed"]["ok"], audit["limits"]["2_feed"])
        self.assertTrue(audit["limits"]["3_outer_wall"]["ok"], audit["limits"]["3_outer_wall"])
        self.assertLess(measure_toe_air(out)["bands"]["0-15"]["air_mm3"], measure_toe_air(src)["bands"]["0-15"]["air_mm3"])

    def test_hairline_fill_fails_the_physical_gate(self) -> None:
        # The shape PR #36 emitted: a 0.10 mm strip at 750 mm/s off an outer vertex.
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
        # No inner bead, so ApexLight is not given an unprintable strip.
        self.assertEqual(bond_apex_light(light), light)
        grown = bond_apex_light(_pocket_with_gap_bead().replace("ApexCore", "ApexLight"))
        self.assertIn("widened=", grown)
        self.assertIn(";WIDTH:1.040", grown)
        forge = _pocket("ApexForge")
        self.assertEqual(bond_apex_light(forge), forge)
        bounce = _pocket("ApexBounce")
        self.assertEqual(bond_apex_light(bounce), bounce)

    def test_production_hooks_bond_apex_and_leave_forge(self) -> None:
        os.environ.pop("BELT_SKIN_BOND", None)
        from postprocess_g2b import clear_perimeter_crossings
        import topfix_apexcore

        light = _pocket_with_gap_bead().replace("ApexCore", "ApexLight")
        bonded = clear_perimeter_crossings(light)
        self.assertIn("widened=", bonded)
        self.assertGreater(int(bonded.split("widened=")[1].split()[0]), 0)
        forge = _pocket("ApexForge")
        self.assertNotIn("; belt_skin_bond", clear_perimeter_crossings(forge))
        work = Path("/tmp/belt-skin-bond-topfix")
        work.mkdir(exist_ok=True)
        src = work / "in.gcode"
        dest = work / "out.gcode"
        src.write_text(_pocket_with_gap_bead())
        old = sys.argv
        sys.argv = ["topfix_apexcore.py", str(src), str(dest)]
        try:
            topfix_apexcore.main()
        finally:
            sys.argv = old
        written = dest.read_text()
        self.assertIn("widened=", written)
        self.assertGreater(int(written.split("widened=")[1].split()[0]), 0)

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
