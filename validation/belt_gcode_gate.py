#!/usr/bin/env python3
"""
Belt Printer G-code Validation Gate for IdeaFormer IR3 V2.

Validates that a G-code file follows belt printer rules before uploading.
All checks are derived from hard-won physical print failures:

- v7 crash:  Split Z/XY travel → nozzle descended through model
- v9 crash:  Z_mach oscillated within layer (no inclined Z on travel)
- v10 issue: Z on every travel move (redundant, differs from IdeaMaker)
- v11 fix:   Z set once per layer (combined XYZ), then XY-only → matches IdeaMaker

Exit codes:
  0 = PASS (safe to print)
  1 = FAIL (unsafe, do NOT print)
  2 = WARN (anomalies found, review before printing)
"""

import re
import sys
import json
import argparse
from collections import defaultdict
from pathlib import Path


# ── Rule thresholds (tuned from successful prints) ──────────────────────

Z_CONSTANCY_TOL = 0.01       # mm — max Z spread within a layer
FIRST_LAYER_Y_MAX = 2.0      # mm — Y_mach must be close to belt surface
Z_STEP_EXPECTED = 0.283      # mm — 0.2mm / cos(45°)
Z_STEP_TOL = 0.05            # mm — tolerance on Z step
MAX_Z_ONLY_MOVES = 0         # Z-only moves = belt moves without nozzle = crash
MAX_NEG_Z = 0                # negative Z = belt reversing past home
Z_PER_LAYER_MAX = 3          # max G1 commands with Z per layer (1 ideal, allow margin)
MIN_Y_HOPS_RATIO = 0.5       # heuristic: travel-after-retract often includes a Y lift
# R11: emitted G-code is already machine axes (see parse / check_gantry_clearance).
# IR3 firmware: Y = gantry (belt-normal), Z = belt travel. Nozzle-through-belt is
# gantry Y below the belt plane, not Z − Y/√2 (that formula assumed virtual
# slicer-space coordinates and is not this fork's emit path).
Y_GANTRY_MIN = -0.05         # mm — known machine Y below this during model motion
AXIS_WORD = re.compile(r"([XYZEF])(-?(?:\d+\.?\d*|\.\d+))")


# ── Slicer detection ────────────────────────────────────────────────────

def detect_slicer(lines, scan_lines=30):
    """Detect which slicer generated the G-code from header comments.

    Returns a lowercase string: 'orcaslicer', 'ideamaker', 'bambu', or 'unknown'.
    Only the first scan_lines lines are checked (header only).
    """
    for line in lines[:scan_lines]:
        l = line.lower()
        # ORCA_BELT: treat our fork identifier ("OrcaBelt" in the header) as orcaslicer.
        if "orcaslicer" in l or "orca slicer" in l or "orcabelt" in l or "orca belt" in l:
            return "orcaslicer"
        if "ideamaker" in l or "idea maker" in l:
            return "ideamaker"
        if "bambustudio" in l or "bambu studio" in l:
            return "bambu"
    return "unknown"


def _axis_words(cmd):
    """Parse XYZEF words from a G-code command (comment already stripped)."""
    return {m.group(1): float(m.group(2)) for m in AXIS_WORD.finditer(cmd)}


class GcodeValidator:
    def __init__(self, filepath, verbose=False, text=None):
        self.filepath = filepath or "<memory>"
        self.verbose = verbose
        if text is not None:
            self.lines = text.splitlines()
        else:
            self.lines = Path(filepath).read_text(encoding="utf-8", errors="replace").splitlines()
        self.results = []
        self.layer_count = 0
        self.slicer = detect_slicer(self.lines)
        self.total_moves = 0

    def fail(self, rule, msg):
        self.results.append(("FAIL", rule, msg))

    def warn(self, rule, msg):
        self.results.append(("WARN", rule, msg))

    def ok(self, rule, msg):
        self.results.append(("OK", rule, msg))

    def parse(self):
        """Parse G-code with modal state.

        Tracks G90/G91 (XYZ), M82/M83 + G92 E (extruder), G92/G28 (known
        position). Start purge and retraction-only LAYER_CHANGE blocks are
        not the first model-extrusion layer.

        Emitted belt G-code is machine coordinates: GCodeWriter applies
        BeltTransform::inverse_transform_point before emit
        (Y_file = gantry / Y_mach, Z_file = belt / Z_mach). IR3 firmware
        has no belt transform ([printer] kinematics: corexy).
        """
        self.layer_count = 0
        self.model_layer_count = 0
        self.layer_z = defaultdict(list)
        self.layer_z_cmds = defaultdict(int)
        self.neg_z_moves = []
        self.z_only_moves = []
        self.ext_with_z = 0
        self.ext_no_z = 0
        self.travel_with_z = 0
        self.travel_no_z = 0
        self.first_layer_y = []
        self.first_model_layer_line = None
        self.y_hops = 0
        self.retract_count = 0
        self.total_moves = 0
        self.gantry_violations = []          # (line, model_layer, y, is_ext)
        self.unknown_pos_model_moves = 0     # model-phase moves with Y unknown
        self.seen_model_extrusion = False
        self.in_end_gcode = False
        self.abs_xyz = True                  # G90 default
        self.abs_e = True                    # M82 default (IR3 start emits M82)
        self.x = self.y = self.z = None      # None = unknown
        self.e_abs = 0.0
        self.pending_layer = 0               # LAYER_CHANGE count; model layer assigned on first XY+E+
        self.current_model_layer = 0
        pending_layer_y = []
        prev_y_known = None

        for i, line in enumerate(self.lines):
            lineno = i + 1
            raw = line.strip()
            if "; === IdeaFormer IR3 V2 Belt Printer End ===" in line or raw.startswith("; filament end gcode"):
                self.in_end_gcode = True

            if ";LAYER_CHANGE" in line:
                self.pending_layer += 1
                pending_layer_y = []
                continue

            cmd = line.split(";")[0].strip()
            if not cmd:
                continue
            words = cmd.split()
            code = words[0].upper()
            axes = _axis_words(cmd)

            if code in ("G90",):
                self.abs_xyz = True
                continue
            if code in ("G91",):
                self.abs_xyz = False
                continue
            if code == "M82":
                self.abs_e = True
                continue
            if code == "M83":
                self.abs_e = False
                continue
            if code == "G92":
                if "X" in axes:
                    self.x = axes["X"]
                if "Y" in axes:
                    self.y = axes["Y"]
                    prev_y_known = self.y
                if "Z" in axes:
                    self.z = axes["Z"]
                if "E" in axes:
                    self.e_abs = axes["E"]
                continue
            if code == "G28":
                # Homing: those axes are at an endstop, but the gate does not
                # assume live position_endstop. Mark them unknown so R11 cannot
                # invent a collision from stale Z/Y.
                named = [a for a in ("X", "Y", "Z") if a in axes]
                if not named:
                    self.x = self.y = self.z = None
                    prev_y_known = None
                else:
                    if "X" in named:
                        self.x = None
                    if "Y" in named:
                        self.y = None
                        prev_y_known = None
                    if "Z" in named:
                        self.z = None
                continue

            if code not in ("G0", "G1"):
                continue

            has_x, has_y, has_z, has_e = ("X" in axes, "Y" in axes, "Z" in axes, "E" in axes)
            if not (has_x or has_y or has_z or has_e):
                continue

            self.total_moves += 1

            def _apply_linear(cur, key):
                if key not in axes:
                    return cur
                val = axes[key]
                if self.abs_xyz:
                    return val
                return None if cur is None else cur + val

            new_x = _apply_linear(self.x, "X")
            new_y = _apply_linear(self.y, "Y")
            new_z = _apply_linear(self.z, "Z")

            delta_e = None
            if has_e:
                if self.abs_e:
                    delta_e = axes["E"] - self.e_abs
                    self.e_abs = axes["E"]
                else:
                    delta_e = axes["E"]
                    self.e_abs += delta_e

            is_retract = delta_e is not None and delta_e < -0.01
            is_extrude = delta_e is not None and delta_e > 0.01
            is_model_extrude = (
                is_extrude and (has_x or has_y) and not self.in_end_gcode
                and self.pending_layer > 0
            )

            if is_model_extrude and not self.seen_model_extrusion:
                self.seen_model_extrusion = True
                self.current_model_layer = 1
                self.model_layer_count = 1
                self.first_model_layer_line = lineno
                self.first_layer_y.extend(pending_layer_y)
            elif is_model_extrude and self.pending_layer > self.model_layer_count:
                self.model_layer_count = self.pending_layer
                self.current_model_layer = self.pending_layer

            in_model = self.seen_model_extrusion and not self.in_end_gcode
            layer_key = self.current_model_layer if in_model else 0

            if in_model and has_z and new_z is not None and layer_key > 0:
                self.layer_z[layer_key].append(f"{new_z:.6f}")
                self.layer_z_cmds[layer_key] += 1
                if new_z < -0.001:
                    self.neg_z_moves.append((lineno, new_z))
                if has_z and not has_x and not has_y and not has_e:
                    self.z_only_moves.append((lineno, raw))

            if in_model:
                if is_extrude and (has_x or has_y):
                    if has_z:
                        self.ext_with_z += 1
                    else:
                        self.ext_no_z += 1
                elif is_retract:
                    self.retract_count += 1
                elif has_x or has_y:
                    if has_z:
                        self.travel_with_z += 1
                    else:
                        self.travel_no_z += 1

                if self.current_model_layer == 1 and new_y is not None:
                    self.first_layer_y.append(new_y)

                if has_y and not is_extrude and new_y is not None and prev_y_known is not None:
                    if new_y > prev_y_known + 0.1:
                        self.y_hops += 1

                if new_y is None and (has_x or has_y or is_extrude):
                    self.unknown_pos_model_moves += 1
                elif new_y is not None and new_y < Y_GANTRY_MIN:
                    self.gantry_violations.append(
                        (lineno, self.current_model_layer, new_y, is_extrude)
                    )

            if new_y is not None and self.pending_layer > 0 and not self.in_end_gcode:
                pending_layer_y.append(new_y)

            self.x, self.y, self.z = new_x, new_y, new_z
            if new_y is not None:
                prev_y_known = new_y

        self.layer_count = self.model_layer_count

    def check_zmach(self):
        """R11: known machine gantry Y must stay above the belt plane.

        This fork emits machine coordinates (inverse transform already
        applied). Firmware Y is gantry height. The old Z − Y/√2 quantity
        is not nozzle clearance and is not used.
        Unknown Y (after G28, relative moves with no base) is reported,
        not scored as a collision.
        """
        if self.gantry_violations:
            ext_viol = [v for v in self.gantry_violations if v[3]]
            trav_viol = [v for v in self.gantry_violations if not v[3]]
            worst = min(self.gantry_violations, key=lambda x: x[2])
            msg = (
                f"{len(self.gantry_violations)} moves with known gantry Y < {Y_GANTRY_MIN}mm "
                f"({len(ext_viol)} extrusion, {len(trav_viol)} travel). "
                f"Worst: layer {worst[1]}, line {worst[0]}, Y={worst[2]:.3f}mm. "
                f"Y is IR3 gantry / belt-normal (machine coords)."
            )
            if ext_viol:
                self.fail("R11-GANTRY", msg)
            else:
                self.warn("R11-GANTRY", msg + " (travel only — no extrusion below belt)")
        elif self.unknown_pos_model_moves:
            self.ok(
                "R11-GANTRY",
                f"No known gantry-Y dive during model motion; "
                f"{self.unknown_pos_model_moves} model-phase moves had unknown Y "
                f"(G28/G91 without a known base) and were not scored as collisions",
            )
        else:
            self.ok(
                "R11-GANTRY",
                f"Known machine gantry Y ≥ {Y_GANTRY_MIN}mm on model moves "
                f"(emitted Y is Y_mach, not virtual Y)",
            )

    def check_z_constancy(self):
        """R1: Z must be constant within each layer (belt doesn't oscillate)."""
        violations = 0
        worst_spread = 0
        worst_layer = 0
        for layer in sorted(self.layer_z):
            z_strs = self.layer_z[layer]
            if not z_strs:
                continue
            z_vals = [float(z) for z in z_strs]
            spread = max(z_vals) - min(z_vals)
            if spread > Z_CONSTANCY_TOL:
                violations += 1
                if spread > worst_spread:
                    worst_spread = spread
                    worst_layer = layer

        if violations == 0:
            self.ok("R1-Z-CONST", "Z constant within every layer")
        else:
            self.fail("R1-Z-CONST",
                      f"{violations} layers with Z variation > {Z_CONSTANCY_TOL}mm "
                      f"(worst: layer {worst_layer}, spread={worst_spread:.3f}mm). "
                      f"Belt is oscillating! Check travel Z uses compute_belt_inclined_z().")

    def check_no_negative_z(self):
        """R2: Z must never go negative (belt can't reverse past home)."""
        if not self.neg_z_moves:
            self.ok("R2-NO-NEG-Z", "No negative Z values")
        else:
            worst = min(z for _, z in self.neg_z_moves)
            self.fail("R2-NO-NEG-Z",
                      f"{len(self.neg_z_moves)} moves with negative Z (min={worst:.3f}mm). "
                      f"Check belt_z_base subtraction and model placement.")

    def check_no_z_only(self):
        """R3: No Z-only moves (belt advancing without nozzle = crash risk)."""
        if not self.z_only_moves:
            self.ok("R3-NO-Z-ONLY", "No Z-only moves (no bare belt advances)")
        else:
            first = self.z_only_moves[0]
            self.fail("R3-NO-Z-ONLY",
                      f"{len(self.z_only_moves)} Z-only moves (belt advances without nozzle). "
                      f"First at line {first[0]}: {first[1]}. "
                      f"This caused the v7 crash — use combined XYZ for layer change.")

    def check_z_per_layer(self):
        """R4: Z should appear at most once per layer (layer change travel only)."""
        violations = 0
        worst_count = 0
        worst_layer = 0
        for layer in sorted(self.layer_z_cmds):
            count = self.layer_z_cmds[layer]
            if count > Z_PER_LAYER_MAX:
                violations += 1
                if count > worst_count:
                    worst_count = count
                    worst_layer = layer

        if violations == 0:
            self.ok("R4-Z-ONCE", f"Z appears ≤{Z_PER_LAYER_MAX}× per layer "
                    f"(travel_with_z={self.travel_with_z}, layers={self.layer_count})")
        else:
            avg = self.travel_with_z / max(self.layer_count, 1)
            self.fail("R4-Z-ONCE",
                      f"{violations} layers with Z on >{Z_PER_LAYER_MAX} moves "
                      f"(worst: layer {worst_layer} with {worst_count}). "
                      f"Avg {avg:.1f} Z-moves/layer. "
                      f"This was the v10 issue — within-layer travel must use XY only.")

    def check_no_extrusion_z(self):
        """R5: Extrusion lines must not contain Z (belt stays still during extrusion)."""
        if self.ext_with_z == 0:
            self.ok("R5-EXT-NO-Z", f"All {self.ext_no_z} extrusion moves are XY-only")
        else:
            self.warn("R5-EXT-NO-Z",
                      f"{self.ext_with_z} extrusion moves contain Z "
                      f"(should be XY-only — belt is constant during extrusion).")

    def check_y_hops(self):
        """R6: Heuristic — travel after retract often includes a gantry Y lift.

        Not a firmware requirement. Counted retracts use M82/M83 + G92 E
        (relative positive E is extrusion). WARN only; never FAIL.
        """
        if self.retract_count == 0 and self.y_hops == 0:
            self.ok("R6-Y-HOPS", "No retracts or Y-lifts counted in model motion")
        elif self.y_hops == 0:
            self.warn(
                "R6-Y-HOPS",
                f"Heuristic: 0 Y-lifts after {self.retract_count} retracts "
                f"(not a demonstrated machine requirement; review travel in preview)",
            )
        elif self.retract_count > 0 and self.y_hops / self.retract_count < MIN_Y_HOPS_RATIO:
            ratio = self.y_hops / self.retract_count
            self.warn(
                "R6-Y-HOPS",
                f"Heuristic: {self.y_hops} Y-lifts / {self.retract_count} retracts "
                f"(ratio={ratio:.2f}, informally ≥{MIN_Y_HOPS_RATIO}). "
                f"Not a firmware requirement.",
            )
        else:
            self.ok(
                "R6-Y-HOPS",
                f"Heuristic: {self.y_hops} Y-lifts / {self.retract_count} retracts "
                f"(gantry lifts; not a machine interlock)",
            )

    def check_first_layer_y(self):
        """R7: First *model-extrusion* layer Y must be close to the belt."""
        if not self.first_layer_y:
            self.warn(
                "R7-1ST-Y",
                "No Y on the first model-extrusion layer "
                "(empty/retract-only LAYER_CHANGE blocks are skipped; start purge is not model)",
            )
            return

        y_min = min(self.first_layer_y)
        y_max = max(self.first_layer_y)

        if y_max > FIRST_LAYER_Y_MAX:
            self.fail("R7-1ST-Y",
                      f"First layer Y range [{y_min:.3f}, {y_max:.3f}]mm — "
                      f"max {y_max:.3f} > {FIRST_LAYER_Y_MAX}mm. "
                      f"Nozzle too far from belt. Check model placement and belt_z_base.")
        elif y_min < 0:
            self.fail("R7-1ST-Y",
                      f"First layer has negative Y ({y_min:.3f}mm) — "
                      f"nozzle below belt surface!")
        else:
            self.ok("R7-1ST-Y", f"First layer Y range [{y_min:.3f}, {y_max:.3f}]mm")

    def check_z_step(self):
        """R8: Z step between layers should be ~0.283mm (0.2mm / cos45°)."""
        layer_z_vals = {}
        for layer in sorted(self.layer_z):
            z_strs = self.layer_z[layer]
            if z_strs:
                layer_z_vals[layer] = float(z_strs[0])

        if len(layer_z_vals) < 3:
            self.warn("R8-Z-STEP", "Too few layers to check Z step")
            return

        steps = []
        sorted_layers = sorted(layer_z_vals)
        for i in range(1, min(len(sorted_layers), 20)):
            l1, l2 = sorted_layers[i - 1], sorted_layers[i]
            step = layer_z_vals[l2] - layer_z_vals[l1]
            steps.append(step)

        if not steps:
            return

        avg_step = sum(steps) / len(steps)
        if abs(avg_step - Z_STEP_EXPECTED) > Z_STEP_TOL:
            self.warn("R8-Z-STEP",
                      f"Avg Z step={avg_step:.3f}mm (expected ~{Z_STEP_EXPECTED}mm). "
                      f"Check layer height and cos(45°) scaling.")
        else:
            self.ok("R8-Z-STEP", f"Z step={avg_step:.3f}mm (expected ~{Z_STEP_EXPECTED}mm)")

    def check_z_monotonic(self):
        """R9: Z must be monotonically increasing between layers (belt only advances)."""
        layer_z_vals = {}
        for layer in sorted(self.layer_z):
            z_strs = self.layer_z[layer]
            if z_strs:
                layer_z_vals[layer] = float(z_strs[0])

        reversals = 0
        sorted_layers = sorted(layer_z_vals)
        for i in range(1, len(sorted_layers)):
            l_prev, l_curr = sorted_layers[i - 1], sorted_layers[i]
            if layer_z_vals[l_curr] < layer_z_vals[l_prev] - 0.001:
                reversals += 1

        if reversals == 0:
            self.ok("R9-Z-MONO", "Z monotonically increasing between layers")
        else:
            self.fail("R9-Z-MONO",
                      f"{reversals} Z reversals between layers — belt going backward!")

    def validate(self):
        """Run all checks and return (fails, warns).

        Belt safety checks are only applied to OrcaSlicer G-code.
        Files from other slicers (IdeaMaker, Bambu, unknown) are passed
        immediately with no warnings — they have their own validation logic
        and are not expected to follow OrcaSlicer belt conventions.
        """
        if self.slicer != "orcaslicer":
            self.results.append(("OK", "SLICER-SKIP",
                                  f"Slicer detected: {self.slicer!r} — "
                                  f"OBP belt checks apply to OrcaSlicer only. Passing without checks."))
            return [], []

        self.parse()

        self.check_z_constancy()        # R1
        self.check_no_negative_z()      # R2
        self.check_no_z_only()          # R3
        self.check_z_per_layer()        # R4
        self.check_no_extrusion_z()     # R5
        self.check_y_hops()             # R6
        self.check_first_layer_y()      # R7
        self.check_z_step()             # R8
        self.check_z_monotonic()        # R9
        self.check_zmach()              # R11

        # Determine exit code
        fails = [r for r in self.results if r[0] == "FAIL"]
        warns = [r for r in self.results if r[0] == "WARN"]

        return fails, warns

    def print_report(self):
        """Print human-readable report."""
        fails, warns = self.validate()

        print(f"\n{'='*60}")
        print(f"  Belt G-code Validation: {Path(self.filepath).name}")
        print(f"  Slicer: {self.slicer}  |  Layers: {self.layer_count}  |  Moves: {self.total_moves}")
        print(f"{'='*60}\n")

        for status, rule, msg in self.results:
            icon = {"OK": "  PASS", "WARN": "  WARN", "FAIL": "**FAIL"}[status]
            print(f"  {icon}  [{rule}] {msg}")

        print(f"\n{'─'*60}")

        if fails:
            print(f"\n  RESULT: BLOCKED — {len(fails)} critical failure(s)")
            print(f"  Do NOT send this G-code to the printer.\n")
            return 1
        elif warns:
            print(f"\n  RESULT: WARNING — {len(warns)} anomaly(ies)")
            print(f"  Review before printing.\n")
            return 2
        else:
            print(f"\n  RESULT: PASSED — Safe to print")
            print(f"  All {len(self.results)} checks passed.\n")
            return 0

    def json_report(self):
        """Return JSON-serializable report."""
        fails, warns = self.validate()
        return {
            "file": str(self.filepath),
            "slicer": self.slicer,
            "layers": self.layer_count,
            "total_moves": self.total_moves,
            "result": "FAIL" if fails else ("WARN" if warns else "PASS"),
            "checks": [
                {"status": s, "rule": r, "message": m}
                for s, r, m in self.results
            ],
        }


def main():
    parser = argparse.ArgumentParser(
        description="Belt printer G-code validation gate for IdeaFormer IR3 V2"
    )
    parser.add_argument("gcode", help="Path to G-code file to validate")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--upload", action="store_true",
                        help="Upload to IdeaFormer if validation passes")
    parser.add_argument("--name", type=str, default=None,
                        help="Filename on printer (default: same as input)")
    args = parser.parse_args()

    gcode_path = Path(args.gcode)
    if not gcode_path.exists():
        print(f"Error: {gcode_path} not found", file=sys.stderr)
        sys.exit(1)

    validator = GcodeValidator(str(gcode_path), verbose=args.verbose)

    if args.json:
        report = validator.json_report()
        print(json.dumps(report, indent=2))
        exit_code = {"PASS": 0, "WARN": 2, "FAIL": 1}[report["result"]]
    else:
        exit_code = validator.print_report()

    # Upload only if PASS (exit_code=0)
    if args.upload:
        if exit_code == 0:
            import subprocess
            dest_name = args.name or gcode_path.name
            dest = f"ideaformer@<PRINTER_HOST>:printer_data/gcodes/{dest_name}"
            print(f"  Uploading to IdeaFormer: {dest_name}")
            result = subprocess.run(
                ["sshpass", "-p", "1234", "scp", str(gcode_path), dest],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                print(f"  Upload complete.")
            else:
                print(f"  Upload failed: {result.stderr.strip()}", file=sys.stderr)
                exit_code = 1
        elif exit_code == 2:
            print(f"  Skipping upload — warnings found. Use --force-upload to override.")
        else:
            print(f"  BLOCKED — will not upload failed G-code to printer.")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
