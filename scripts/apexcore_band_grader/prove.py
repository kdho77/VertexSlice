"""Prove the gates on an identical pair and on a deliberately bad copy.

The bad copy, built from the baseline G-code:

- insole2: one extrusion coordinate is changed (not a comment)
- orthocad layer 50: one outer-wall extrusion line is deleted
- orthocad layer 50: one ``;seam travel`` X is shifted by 5 mm
- orthocad layer 160: an extrusion is appended on the outer wall with a huge E
- orthocad layer 160: an extrusion is appended in the thin toe
- orthocad layer 160: an unretracted travel is appended across the outer wall
- orthocad layer 180: one sparse-infill extrusion line is deleted

A separate synthetic shell shows the hollow-column rule can pass and can fail.
G1 is checked on the real diff, on an empty diff, and on a diff that names an extra file.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from gcode import Segment, load_gcode  # noqa: E402
from gates import (  # noqa: E402
    ALLOWED_FILES,
    gate_g1,
    grade_pair,
)
from geometry import column_report  # noqa: E402


def _split_layers(text: str) -> tuple[str, list[str]]:
    marker = ";LAYER_CHANGE\n"
    parts = text.split(marker)
    preamble = parts[0]
    layers = [marker + part for part in parts[1:]]
    return preamble, layers


def _join(preamble: str, layers: list[str]) -> str:
    return preamble + "".join(layers)


def _mutate_insole(text: str) -> str:
    needle = "\nG1 X"
    index = text.find(needle)
    if index < 0:
        raise SystemExit("insole2 has no G1 X line to mutate")
    # Shift the first X word by rewriting the token in place with a different value.
    line_end = text.find("\n", index + 1)
    line = text[index + 1 : line_end]
    tokens = line.split(" ")
    replaced = False
    for i, token in enumerate(tokens):
        if token.startswith("X"):
            tokens[i] = "X1.000"
            replaced = True
            break
    if not replaced:
        raise SystemExit("failed to rewrite insole2 X")
    return text[: index + 1] + " ".join(tokens) + text[line_end:]


def _mutate_orthocad(text: str) -> tuple[str, dict]:
    preamble, layers = _split_layers(text)
    notes = {}
    layer = layers[50]
    lines = layer.splitlines(keepends=True)
    deleted = None
    seam_shifted = False
    for i, line in enumerate(lines):
        # Layer 50 keeps the previous ;TYPE: (outer wall) instead of repeating it.
        if deleted is None and line.startswith("G1 ") and "X" in line and " E" in line:
            try:
                e_tok = next(tok for tok in line.split() if tok.startswith("E"))
                if float(e_tok[1:]) > 0:
                    deleted = line.strip()
                    lines[i] = ""
            except (StopIteration, ValueError):
                pass
        if (not seam_shifted) and "seam travel" in line and "X" in line:
            parts = line.split(" ")
            for p, token in enumerate(parts):
                if token.startswith("X"):
                    parts[p] = f"X{float(token[1:]) + 5.0:.3f}"
                    seam_shifted = True
                    break
            lines[i] = " ".join(parts)
            if not lines[i].endswith("\n"):
                lines[i] += "\n"
    if deleted is None or not seam_shifted:
        raise SystemExit(f"layer 50 mutation failed deleted={deleted!r} seam={seam_shifted}")
    layers[50] = "".join(lines)
    notes["layer_50_deleted_outer_wall"] = deleted
    notes["layer_50_seam_shifted"] = True

    # Append bad geometry. Previous nozzle position is whatever the layer ended at;
    # these moves are explicit enough for distance, thickness, and E-per-mm.
    bad = (
        ";TYPE:Sparse infill\n"
        ";WIDTH:1\n"
        "G1 X150.000 Y0.021 F1560\n"
        "G1 X160.000 Y0.021 E50.000\n"
        "G1 X180.000 Y0.900 E10.000\n"
        "G1 X120.000 Y2.000 F9000\n"
        "G1 X120.000 Y-2.000\n"
    )
    layers[160] = layers[160].rstrip("\n") + "\n" + bad
    notes["layer_160_appended"] = bad.strip().splitlines()

    infill_deleted = None
    lines180 = layers[180].splitlines(keepends=True)
    in_infill = False
    for i, line in enumerate(lines180):
        if line.startswith(";TYPE:Sparse infill"):
            in_infill = True
        elif line.startswith(";TYPE:"):
            in_infill = False
        if in_infill and line.startswith("G1 ") and " E" in line and "X" in line:
            infill_deleted = line.strip()
            lines180[i] = ""
            break
    if infill_deleted is None:
        raise SystemExit("layer 180 has no sparse infill extrusion to delete")
    layers[180] = "".join(lines180)
    notes["layer_180_deleted_infill"] = infill_deleted
    return _join(preamble, layers), notes


def _synthetic_shell() -> tuple[dict, dict]:
    """Two walls 5 mm apart. Empty interior must count; a crossing infill line must not."""

    def wall(y: float) -> Segment:
        return Segment(0.0, y, 20.0, y, 3.6, 1560.0, "Outer wall", 1.0, "extrude", False, 0, "")

    def infill() -> Segment:
        return Segment(0.0, 2.5, 20.0, 2.5, 3.6, 1560.0, "Sparse infill", 1.0, "extrude", False, 1, "")

    from gcode import Layer

    empty = Layer(0, [], 0.0, [wall(0.0), wall(5.0)], [])
    filled = Layer(0, [], 0.0, [wall(0.0), wall(5.0), infill()], [])
    return column_report(empty, 0.25), column_report(filled, 0.25)


def _summarize(gates: list[dict]) -> dict:
    return {gate["gate"]: {"pass": gate["pass"], "measured": gate["measured"]} for gate in gates}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--hollow-step", type=float, default=0.25)
    args = parser.parse_args()

    work = Path(args.work_dir)
    if work.exists():
        shutil.rmtree(work)
    bad = work / "bad"
    same = work / "same"
    bad.mkdir(parents=True)
    same.mkdir(parents=True)
    src = Path(args.baseline_dir)
    ortho_src = (src / "cand_apex_core_orthocad.gcode").read_text(encoding="utf-8", errors="replace")
    insole_src = (src / "cand_apex_core_insole2.gcode").read_text(encoding="utf-8", errors="replace")
    (same / "cand_apex_core_orthocad.gcode").write_text(ortho_src, encoding="utf-8")
    (same / "cand_apex_core_insole2.gcode").write_text(insole_src, encoding="utf-8")
    mutated, notes = _mutate_orthocad(ortho_src)
    (bad / "cand_apex_core_orthocad.gcode").write_text(mutated, encoding="utf-8")
    (bad / "cand_apex_core_insole2.gcode").write_text(_mutate_insole(insole_src), encoding="utf-8")

    def run(candidate: Path, label: str) -> list[dict]:
        print(f"loading {label}", flush=True)
        base_pre, base_layers = load_gcode(str(src / "cand_apex_core_orthocad.gcode"))
        cand_pre, cand_layers = load_gcode(str(candidate / "cand_apex_core_orthocad.gcode"))
        insole_base = (src / "cand_apex_core_insole2.gcode").read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        insole_cand = (candidate / "cand_apex_core_insole2.gcode").read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        print(f"grading {label}", flush=True)
        return grade_pair(
            base_pre,
            base_layers,
            cand_pre,
            cand_layers,
            command=f"python3 scripts/apexcore_band_grader/prove.py --case {label}",
            hollow_step=args.hollow_step,
            include_g2=True,
            insole_base=insole_base,
            insole_cand=insole_cand,
        )

    identity = run(same, "identity")
    bad_gates = run(bad, "bad-copy")
    empty_shell, filled_shell = _synthetic_shell()
    g1_empty = gate_g1([], "git diff --name-only <sha>..<sha>  # empty")
    g1_extra = gate_g1(list(ALLOWED_FILES) + ["README.md"], "synthetic name-status with README.md")
    identity_expect_pass = {"G2", "G3", "G4", "G5", "G6", "G7", "G8", "G10"}
    bad_expect_fail = {"G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10"}
    problems = []
    ident_map = {gate["gate"]: gate["pass"] for gate in identity}
    bad_map = {gate["gate"]: gate["pass"] for gate in bad_gates}
    for gate in identity_expect_pass:
        if not ident_map.get(gate, False):
            problems.append(f"identity {gate} should PASS")
    if ident_map.get("G9", True):
        problems.append("identity G9 should FAIL because the unfixed baseline is the candidate")
    for gate in bad_expect_fail:
        if bad_map.get(gate, True):
            problems.append(f"bad-copy {gate} should FAIL")
    if not g1_empty["pass"]:
        problems.append("empty diff should PASS G1")
    if g1_extra["pass"]:
        problems.append("extra file should FAIL G1")
    if empty_shell["columns"] <= 0:
        problems.append("synthetic empty shell should be hollow")
    if filled_shell["columns"] != 0:
        problems.append("synthetic filled shell should not be hollow")

    payload = {
        "hollow_step_mm": args.hollow_step,
        "mutations": notes,
        "identity": _summarize(identity),
        "bad_copy": _summarize(bad_gates),
        "g1_empty_diff": g1_empty,
        "g1_extra_file": g1_extra,
        "synthetic_empty_shell": empty_shell,
        "synthetic_filled_shell": filled_shell,
        "problems": problems,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("identity", {k: v["pass"] for k, v in payload["identity"].items()})
    print("bad", {k: v["pass"] for k, v in payload["bad_copy"].items()})
    print("synthetic empty", empty_shell["columns"], "filled", filled_shell["columns"])
    print("problems", problems)
    print("wrote", out)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
