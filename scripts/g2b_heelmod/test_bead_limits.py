#!/usr/bin/env python3
"""Gap and solid beads stay within one and a half nozzle diameters.

close_thin_skins widened those beads to 2.98 mm and ran back over the same
line. That post-process is gone. These cases are what it must not do again.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]

import check_gcode  # noqa: E402
from bead_limits import violations  # noqa: E402


def _block(body: str) -> str:
    return (
        "; CONFIG_BLOCK_START\n"
        "; print_direction = toe_first\n"
        "; slow_down_layer_time = 4\n"
        "; CONFIG_BLOCK_END\n"
        + body
    )


class BeadLimitTest(unittest.TestCase):
    def test_one_millimetre_bead_passes(self) -> None:
        # 0.18 E/mm is the locked ApexCore rate. Flow-model width is about 1.04 mm.
        text = "\n".join(
            [
                ";TYPE:Gap infill",
                ";WIDTH:1.0",
                "G1 X0 Y0 F1560",
                "G1 X10 Y0 E1.8",
                ";TYPE:Internal solid infill",
                ";WIDTH:1",
                "G1 X0 Y2 E1.8",
                ";TYPE:Top surface",
                "G1 X10 Y2 E1.8",
                ";TYPE:Bottom surface",
                "G1 X0 Y4 E1.8",
            ]
        )
        self.assertEqual(violations(text), [])

    def test_width_comment_over_1_5_nozzle_fails(self) -> None:
        text = "\n".join(
            [
                ";TYPE:Gap infill",
                ";WIDTH:2.98",
                "G1 X0 Y0 F1560",
                "G1 X10 Y0 E1.8",
            ]
        )
        found = violations(text)
        self.assertEqual(len(found), 1)
        self.assertIn("2.98 mm", found[0])

    def test_solid_flow_over_1_5_nozzle_fails_even_when_width_says_1(self) -> None:
        # E that models to about 2.98 mm on a 10 mm move, with a 1.0 mm comment.
        text = "\n".join(
            [
                ";TYPE:Internal solid infill",
                ";WIDTH:1.0",
                "G1 X0 Y0 F1560",
                "G1 X10 Y0 E5.51",
            ]
        )
        found = violations(text)
        self.assertTrue(any("wider than 1.50 mm" in item for item in found))

    def test_out_and_back_retrace_fails(self) -> None:
        text = "\n".join(
            [
                ";TYPE:Gap infill",
                ";WIDTH:1.0",
                "G1 X0 Y0 F1560",
                "G1 X10 Y0 E1.8",
                "G1 X0 Y0 E1.8",
            ]
        )
        found = violations(text)
        self.assertTrue(any("retraces" in item for item in found))

    def test_retrace_with_a_travel_between_fails(self) -> None:
        text = "\n".join(
            [
                ";LAYER_CHANGE",
                ";TYPE:Top surface",
                ";WIDTH:1.0",
                "G1 X0 Y0 F1560",
                "G1 X8 Y1 E1.44",
                "G0 X8 Y1 F12720",
                "G1 X0 Y0 E1.44 F1560",
            ]
        )
        found = violations(text)
        self.assertTrue(any("retraces" in item for item in found))

    def test_forward_continuation_is_not_a_retrace(self) -> None:
        text = "\n".join(
            [
                ";TYPE:Bottom surface",
                ";WIDTH:1.0",
                "G1 X0 Y0 F1560",
                "G1 X5 Y0 E0.9",
                "G1 X10 Y0 E0.9",
            ]
        )
        self.assertEqual(violations(text), [])

    def test_outer_wall_is_not_scored(self) -> None:
        text = "\n".join(
            [
                ";TYPE:Outer wall",
                ";WIDTH:1.2",
                "G1 X0 Y0 F1560",
                "G1 X10 Y0 E1.8",
                "G1 X0 Y0 E1.8",
            ]
        )
        self.assertEqual(violations(text), [])

    def test_extrusion_at_travel_feed_fails(self) -> None:
        text = "\n".join(
            [
                ";TYPE:Gap infill",
                ";WIDTH:1.0",
                "G1 X0 Y0 F1560",
                "G1 X10 Y0 E1.8 F12720",
            ]
        )
        found = violations(text)
        self.assertTrue(any("F12720" in item for item in found))

    def test_config_only_block_stays_clean(self) -> None:
        check_gcode.check(_block(""))

    def test_check_rejects_a_wide_bead(self) -> None:
        body = "\n".join(
            [
                ";TYPE:Gap infill",
                ";WIDTH:2.98",
                "G1 X0 Y0 F1560",
                "G1 X4 Y0 E0.72",
                "",
            ]
        )
        with self.assertRaises(SystemExit) as caught:
            check_gcode.check(_block(body))
        self.assertIn("2.98 mm", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
