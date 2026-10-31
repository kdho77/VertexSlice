"""Deterministic ApexLight post-edit (efix output -> print-ready G-code).

Applied after pp_d on the ApexLight heel-route pipeline. Does not modify
PP_HEELROUTE ``G1 E±1.5 F1800`` blocks. ApexCore and other materials skip this stage.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_RETRACT_DROP = frozenset({"G1 E-0.5 F1800", "G1 E0.45 F1800"})
_MAX_FAN_CMD = "M106 S89.2"
_MAX_PART_FAN = "M106 P1 S89.2"


def apply_post_edit(text: str) -> str:
    """Return *text* with ApexLight fan lock and pp_c seam retract lines removed."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    seen_max_fan = False
    part_fan_set = False

    for line in lines:
        stripped = line.strip()
        if stripped in _RETRACT_DROP:
            continue

        if seen_max_fan and part_fan_set:
            if stripped == _MAX_FAN_CMD or stripped.startswith("M106 P1 S"):
                continue

        if stripped == _MAX_FAN_CMD and not seen_max_fan:
            seen_max_fan = True
            out.append(line)
            continue

        if seen_max_fan and not part_fan_set and stripped.startswith("M106 P1 S"):
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            out.append(_MAX_PART_FAN + newline)
            part_fan_set = True
            continue

        out.append(line)

    return "".join(out)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _cli(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] in ("-h", "--help"):
        print(
            "usage: python3 -m tools.belt_pp.apex_light_post_edit IN.gcode EXPECTED.gcode",
            file=sys.stderr,
        )
        print("  Applies post-edit to IN and compares sha256 to EXPECTED (byte match gate).", file=sys.stderr)
        return 2 if len(argv) != 3 else 0

    inp_path = Path(argv[1])
    expected_path = Path(argv[2])
    raw = inp_path.read_text(encoding="utf-8", errors="surrogateescape")
    edited = apply_post_edit(raw)
    digest = sha256_text(edited)
    expected = expected_path.read_bytes()
    expected_digest = hashlib.sha256(expected).hexdigest()

    if edited.encode("utf-8", errors="surrogateescape") != expected:
        out_lines = edited.splitlines()
        exp_lines = expected.decode("utf-8", errors="surrogateescape").splitlines()
        for i, (a, b) in enumerate(zip(out_lines, exp_lines)):
            if a != b:
                print(f"first differing line {i + 1}:", file=sys.stderr)
                print(f"  post-edit: {a!r}", file=sys.stderr)
                print(f"  expected:  {b!r}", file=sys.stderr)
                break
        else:
            if len(out_lines) != len(exp_lines):
                print(
                    f"line count post-edit={len(out_lines)} expected={len(exp_lines)}",
                    file=sys.stderr,
                )
        print(f"sha256 post-edit={digest}", file=sys.stderr)
        print(f"sha256 expected={expected_digest}", file=sys.stderr)
        return 1

    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
