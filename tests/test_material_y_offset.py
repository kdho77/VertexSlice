"""Per-material belt nozzle-height offsets baked into absolute Y.

ApexCore is returned unchanged. ApexLight moves the nozzle +0.20 mm higher
(physical Y larger). ApexForge and ApexBounce move it -0.20 mm lower
(physical Y smaller), including below 0, because the C++ belt clamp has
already run. The shift is in the coordinates, so a cancel leaves nothing for
the next job to inherit.
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.material_y_offset import (  # noqa: E402
    MATERIAL_Y_OFFSET_MM,
    apply_material_y_offset,
    material_y_offset_mm,
)
from server.profile_registry import resolve_material  # noqa: E402

# Start block matches resources/profiles/IdeaFormer/machine/fdm_vertex_apex_common.json
# (PRINT_START, heat, G92 E0, retract). The two motion lines are the first
# travel and the first extrusion. The end block is the machine end gcode.
_JOB = """PRINT_START
M104 S245
M105
M109 S245
M82 ;absolute extrusion mode
G92 E0
G1 F1800 E-1.5
M204 S3000
G0 F3600 X141.29 Y0.000 Z0.6
G1 F2100 X141.29 Y8.994 E2.08456
M107
G92 E0 ; Set Extruder to zero
G1 E-1 ; Retract 1mm
G90
G91
G92 Z0 ; Set Belt to zero again
G28 Y ; Home Y
G28 X ; Home X
PRINT_END
"""

_LIGHT_MOVE = "G0 F3600 X141.29 Y0.200 Z0.6"
_LIGHT_EXTRUDE = "G1 F2100 X141.29 Y9.194 E2.08456"
_LOWER_MOVE = "G0 F3600 X141.29 Y-0.200 Z0.6"
_LOWER_EXTRUDE = "G1 F2100 X141.29 Y8.794 E2.08456"
_MARKER = "; MATERIAL_Y_OFFSET material={material} offset_mm={offset} baked_into=absolute_Y modal=none"


def _marker(material: str) -> str:
    offset = Decimal(str(MATERIAL_Y_OFFSET_MM[material]))
    return _MARKER.format(material=material, offset=f"{offset:+.3f}")


def _motion_ys(text: str) -> list[Decimal]:
    found: list[Decimal] = []
    for line in text.splitlines():
        code = line.split(";", 1)[0].strip()
        if not code.startswith(("G0 ", "G1 ", "G2 ", "G3 ")):
            continue
        for word in code.split():
            if word.startswith("Y"):
                found.append(Decimal(word[1:]))
    return found


class MaterialYOffset(unittest.TestCase):
    def test_constants_are_the_calibrated_physical_deltas(self) -> None:
        self.assertEqual(
            MATERIAL_Y_OFFSET_MM,
            {"apex_light": 0.20, "apex_forge": -0.20, "apex_bounce": -0.20},
        )
        self.assertNotIn("apex_core", MATERIAL_Y_OFFSET_MM)
        self.assertEqual(material_y_offset_mm("apex_light"), 0.20)
        self.assertEqual(material_y_offset_mm("apex_forge"), -0.20)
        self.assertEqual(material_y_offset_mm("apex_bounce"), -0.20)
        self.assertEqual(material_y_offset_mm("apex_core"), 0.0)
        self.assertEqual(material_y_offset_mm(None), 0.0)
        self.assertEqual(material_y_offset_mm(""), 0.0)
        self.assertEqual(material_y_offset_mm("not_a_material"), 0.0)

    def test_registry_exposes_the_same_offsets(self) -> None:
        self.assertEqual(resolve_material("apex_core").material_y_offset_mm, 0.0)
        self.assertEqual(resolve_material(None).material_y_offset_mm, 0.0)
        self.assertEqual(resolve_material("").material_y_offset_mm, 0.0)
        for material, offset in MATERIAL_Y_OFFSET_MM.items():
            self.assertEqual(resolve_material(material).material_y_offset_mm, offset)

    def test_apex_core_is_the_same_string(self) -> None:
        for material in ("apex_core", None, "", "  ", "ApexCore", "not_a_material"):
            result = apply_material_y_offset(_JOB, material)
            self.assertIs(result, _JOB)
        self.assertNotIn("MATERIAL_Y_OFFSET", _JOB)
        self.assertNotIn("SET_GCODE_OFFSET", _JOB)

    def test_apex_light_raises_the_nozzle_by_0_20(self) -> None:
        result = apply_material_y_offset(_JOB, "apex_light")
        self.assertIn(_LIGHT_MOVE, result)
        self.assertIn(_LIGHT_EXTRUDE, result)
        self.assertEqual(_motion_ys(result), [Decimal("0.200"), Decimal("9.194")])
        self.assertTrue(result.endswith(_marker("apex_light") + "\n"))
        self.assertEqual(result.splitlines()[0], "PRINT_START")

    def test_apex_forge_and_apex_bounce_lower_the_nozzle_by_0_20(self) -> None:
        for material in ("apex_forge", "apex_bounce"):
            result = apply_material_y_offset(_JOB, material)
            self.assertIn(_LOWER_MOVE, result)
            self.assertIn(_LOWER_EXTRUDE, result)
            self.assertEqual(_motion_ys(result), [Decimal("-0.200"), Decimal("8.794")])
            self.assertTrue(result.endswith(_marker(material) + "\n"))
            # The start block through the retract is shared and stays put.
            self.assertEqual(result.split("M204 S3000\n", 1)[0], _JOB.split("M204 S3000\n", 1)[0])

    def test_negative_offset_is_not_clamped_at_zero(self) -> None:
        text = "G1 Y0.000\nG1 Y0.100\nG1 Y0.200\n"
        result = apply_material_y_offset(text, "apex_forge")
        self.assertIn("G1 Y-0.200\n", result)
        self.assertIn("G1 Y-0.100\n", result)
        self.assertIn("G1 Y0.000\n", result)
        self.assertNotIn("Y0.000\nG1 Y0.000\nG1 Y0.000", result)

    def test_every_absolute_y_shifts_by_exactly_the_offset(self) -> None:
        samples = ["0", "0.0", "0.000", "0.066", "8.994", "1.5", ".5", "20", "0.100", "+1.250"]
        lines = "\n".join(f"G1 X1 Y{sample} Z2 E3" for sample in samples) + "\n"
        for material, sign in (("apex_light", 1), ("apex_forge", -1), ("apex_bounce", -1)):
            result = apply_material_y_offset(lines, material)
            got = _motion_ys(result)
            self.assertEqual(len(got), len(samples))
            expected_delta = Decimal("0.20") * sign
            for sample, value in zip(samples, got):
                self.assertEqual(value - Decimal(sample), expected_delta)

    def test_sign_is_the_physical_direction(self) -> None:
        """Direct sign, proved against the G92 opposite-sign formula.

        Physical Y is nozzle height. +0.20 mm higher means the motion word
        itself grows by +0.20. While the nozzle is at logical Y=0, the G92
        that produces that same +0.20 mm travel is ``G92 Y-0.200`` (it labels
        the current physical point -0.200, so ``G1 Y0.000`` then moves up).
        This pass does not emit that G92. The move word carries the direct sign.
        """
        parked = "G1 X1 Y0.000 Z0\n"
        higher = apply_material_y_offset(parked, "apex_light")
        lower = apply_material_y_offset(parked, "apex_bounce")
        self.assertIn("G1 X1 Y0.200 Z0\n", higher)
        self.assertIn("G1 X1 Y-0.200 Z0\n", lower)
        g92_for_higher = Decimal("0") - Decimal("0.20")
        g92_for_lower = Decimal("0") - Decimal("-0.20")
        self.assertEqual(g92_for_higher, Decimal("-0.20"))
        self.assertEqual(g92_for_lower, Decimal("0.20"))
        self.assertNotIn(f"G92 Y{g92_for_higher:.3f}", higher)
        self.assertNotIn("G92 Y", higher)
        self.assertNotIn("G92 Y", lower)
        self.assertNotIn("SET_GCODE_OFFSET", higher)
        self.assertNotIn("SET_GCODE_OFFSET", lower)
        # SET_GCODE_OFFSET Y=+0.200 would add +0.200 to a later G1 Y0.000.
        # The rewritten word is that sum already.
        self.assertEqual(Decimal("0.000") + Decimal("0.20"), Decimal("0.200"))
        self.assertEqual(Decimal("0.000") + Decimal("-0.20"), Decimal("-0.200"))

    def test_cancel_leaves_no_modal_offset_for_the_next_job(self) -> None:
        forge = apply_material_y_offset(_JOB, "apex_forge")
        self.assertNotIn("SET_GCODE_OFFSET", forge)
        self.assertNotRegex(forge, r"(?m)^[^;\n]*\bG92\s+Y")
        self.assertIn("modal=none", forge)
        self.assertIn("G28 Y ; Home Y", forge)
        self.assertIn("PRINT_START", forge)
        # A cancelled Forge job does not change the next ApexCore file.
        core_job = "PRINT_START\nG28\nG1 X1 Y0.500 Z1\nPRINT_END\n"
        self.assertIs(apply_material_y_offset(core_job, "apex_core"), core_job)
        # Running the offset again does not stack to -0.40 mm.
        self.assertIs(apply_material_y_offset(forge, "apex_forge"), forge)
        self.assertNotIn("Y-0.400", forge)
        self.assertNotIn("Y-0.40", forge)

    def test_idempotent_for_each_offset_material(self) -> None:
        for material in ("apex_light", "apex_forge", "apex_bounce", " Apex_Light "):
            canonical = material.strip().lower()
            once = apply_material_y_offset(_JOB, material)
            twice = apply_material_y_offset(once, canonical)
            self.assertIs(twice, once)
            self.assertEqual(once.count("MATERIAL_Y_OFFSET"), 1)
            self.assertEqual(_motion_ys(once)[0] - Decimal("0.000"), Decimal(str(MATERIAL_Y_OFFSET_MM[canonical])))

    def test_relative_y_g28_g92_comments_and_other_axes_stay(self) -> None:
        text = (
            "G1 X1.250 Y0.000 Z9.500 E1.250 F3000\n"
            "G91\n"
            "G1 Y1.000 ; relative delta, not a height\n"
            "G90\n"
            "G1 Y2.000\n"
            "G3 X2 Y0.000 I0.500 J-0.250\n"
            "G92 E0\n"
            "G92 Z0\n"
            "G28 Y ; Home Y\n"
            "; keep Y0.000 in this comment\n"
            "G1 X1 Y0.000 ; keep Y9.999 in the comment\n"
        )
        result = apply_material_y_offset(text, "apex_light")
        self.assertIn("G1 X1.250 Y0.200 Z9.500 E1.250 F3000\n", result)
        self.assertIn("G1 Y1.000 ; relative delta, not a height\n", result)
        self.assertIn("G1 Y2.200\n", result)
        self.assertIn("G3 X2 Y0.200 I0.500 J-0.250\n", result)
        self.assertIn("G92 E0\n", result)
        self.assertIn("G92 Z0\n", result)
        self.assertIn("G28 Y ; Home Y\n", result)
        self.assertIn("; keep Y0.000 in this comment\n", result)
        self.assertIn("G1 X1 Y0.200 ; keep Y9.999 in the comment\n", result)

    def test_same_line_g91_does_not_shift_and_g90_does(self) -> None:
        text = "G91 G1 Y1.000\nG90 G1 Y1.000\n"
        result = apply_material_y_offset(text, "apex_forge")
        self.assertIn("G91 G1 Y1.000\n", result)
        self.assertIn("G90 G1 Y0.800\n", result)

    def test_wired_after_the_hollow_fills_and_core_is_not_wired(self) -> None:
        light = (REPO / "server/chains/apex_light.py").read_text(encoding="utf-8")
        engine = (REPO / "server/slice_engine.py").read_text(encoding="utf-8")
        core = (REPO / "server/chains/apex_core.py").read_text(encoding="utf-8")
        self.assertLess(
            light.index("fill_forefoot_hollow(text)"),
            light.index("apply_material_y_offset("),
        )
        self.assertLess(
            engine.index("apply_forefoot_hollow_band(text)"),
            engine.index("apply_material_y_offset("),
        )
        self.assertLess(
            engine.index('if material in ("apex_forge", "apex_bounce")'),
            engine.index("apply_material_y_offset("),
        )
        self.assertIn('"material_y_offset_mm"', light)
        self.assertIn('stats["material_y_offset_mm"]', engine)
        self.assertNotIn("material_y_offset", core)
        self.assertNotIn("apply_material_y_offset", core)
        # ApexCore returns from slice_stl before the forge/bounce block.
        self.assertLess(
            engine.index('if material in ("apex_core", "apex_light")'),
            engine.index('if material in ("apex_forge", "apex_bounce")'),
        )


if __name__ == "__main__":
    unittest.main()
