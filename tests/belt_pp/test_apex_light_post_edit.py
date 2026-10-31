"""Unit tests for ApexLight post-edit (no committed G-code fixtures)."""
from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

from tools.belt_pp import apex_light_post_edit
from tools.belt_pp.pipeline import finalize_apex_light, run_after_pp_d

GOLDEN_SHA256 = "3a0a54a2322e579e2c5cd4e003f061a86af61d49310115299ce6856628424e67"


class ApexLightPostEditTest(unittest.TestCase):
    def test_drops_pp_c_seam_retract_pair(self):
        inp = (
            "G1 F1200\n"
            ";C seam travel: lift + direct\n"
            "G1 E-0.5 F1800\n"
            "G1 Y1.451 F12720 ;lift\n"
            "G1 X1 Y1 F12720 ;seam travel\n"
            "G1 Y1.051 F12720 ;lower\n"
            "G1 E0.45 F1800\n"
            "G1 F12720\n"
        )
        out = apex_light_post_edit.apply_post_edit(inp)
        self.assertNotIn("G1 E-0.5 F1800", out)
        self.assertNotIn("G1 E0.45 F1800", out)
        self.assertIn(";C seam travel: lift + direct", out)
        self.assertIn("G1 Y1.451 F12720 ;lift", out)

    def test_keeps_heelroute_e15(self):
        inp = (
            ";PP_HEELROUTE_START\n"
            "G1 E-1.5 F1800\n"
            "G1 X1 Y1 F12720 ;heel route\n"
            "G1 E1.5 F1800\n"
            ";PP_HEELROUTE_END\n"
        )
        self.assertEqual(apex_light_post_edit.apply_post_edit(inp), inp)

    def test_fan_ramp_then_lock(self):
        inp = (
            "M106 S59.5\n"
            "M106 P1 S30\n"
            ";LAYER_CHANGE\n"
            "M106 S89.2\n"
            "M106 P1 S45\n"
            ";Z:1\n"
            ";LAYER_CHANGE\n"
            "M106 S89.2\n"
            "M106 P1 S45\n"
            ";Z:2\n"
        )
        out = apex_light_post_edit.apply_post_edit(inp)
        self.assertEqual(
            out,
            "M106 S59.5\n"
            "M106 P1 S30\n"
            ";LAYER_CHANGE\n"
            "M106 S89.2\n"
            "M106 P1 S89.2\n"
            ";Z:1\n"
            ";LAYER_CHANGE\n"
            ";Z:2\n",
        )

    def test_pipeline_skips_non_apex_material(self):
        body = "M106 S89.2\nM106 P1 S45\n"
        self.assertEqual(run_after_pp_d(body, material="apex_core"), body)

    def test_finalize_matches_pipeline(self):
        body = "M106 S89.2\nM106 P1 S45\n;LAYER_CHANGE\nM106 S89.2\n"
        self.assertEqual(finalize_apex_light(body), "M106 S89.2\nM106 P1 S89.2\n;LAYER_CHANGE\n")


@unittest.skipUnless(
    os.environ.get("VS_APEXLIGHT_GATE_IN") and os.environ.get("VS_APEXLIGHT_GATE_GOLDEN"),
    "Set VS_APEXLIGHT_GATE_IN and VS_APEXLIGHT_GATE_GOLDEN for byte gate",
)
class ApexLightByteGateTest(unittest.TestCase):
    def test_rebuilt_matches_golden_sha256(self):
        inp = Path(os.environ["VS_APEXLIGHT_GATE_IN"]).read_text(
            encoding="utf-8", errors="surrogateescape"
        )
        golden_bytes = Path(os.environ["VS_APEXLIGHT_GATE_GOLDEN"]).read_bytes()
        edited = apex_light_post_edit.apply_post_edit(inp)
        self.assertEqual(edited.encode("utf-8", errors="surrogateescape"), golden_bytes)
        self.assertEqual(hashlib.sha256(edited.encode()).hexdigest(), GOLDEN_SHA256)


if __name__ == "__main__":
    unittest.main()
