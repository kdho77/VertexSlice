#!/usr/bin/env python3
"""Gate the ApexCore slice: CONFIG_BLOCK matches the golden, and the print is toe-first.

usage: check_gcode.py SLICED.gcode [GOLDEN.gcode]

Without GOLDEN.gcode the block must still be toe-first and
slow_down_layer_time must equal APEX_MIN_LAYER_TIME (default 4).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def config_block(text: str) -> list[str]:
    lines: list[str] = []
    on = False
    for line in text.splitlines():
        if line.startswith("; CONFIG_BLOCK_START"):
            on = True
            continue
        if line.startswith("; CONFIG_BLOCK_END"):
            break
        if on:
            lines.append(line)
    return lines


def _setting(block: list[str], key: str) -> str | None:
    prefix = f"; {key} ="
    for line in block:
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def check(text: str, golden_text: str | None = None) -> None:
    block = config_block(text)
    if not block:
        raise SystemExit("check_gcode: CONFIG_BLOCK missing")
    direction = _setting(block, "print_direction")
    if direction != "toe_first":
        raise SystemExit(f"check_gcode: print_direction is {direction!r}, expected toe_first")
    expect_time = os.environ.get("APEX_MIN_LAYER_TIME", "4").strip() or "4"
    got_time = _setting(block, "slow_down_layer_time")
    if got_time != expect_time:
        raise SystemExit(
            f"check_gcode: slow_down_layer_time is {got_time!r}, expected {expect_time!r}"
        )
    if golden_text is not None:
        golden_block = config_block(golden_text)
        if block != golden_block:
            for i, (left, right) in enumerate(zip(block, golden_block)):
                if left != right:
                    raise SystemExit(
                        f"check_gcode: CONFIG_BLOCK line {i + 1} differs: {left!r} != {right!r}"
                    )
            raise SystemExit(
                f"check_gcode: CONFIG_BLOCK length {len(block)} != golden {len(golden_block)}"
            )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (1, 2):
        print("usage: check_gcode.py SLICED.gcode [GOLDEN.gcode]", file=sys.stderr)
        return 2
    text = Path(args[0]).read_text(encoding="utf-8", errors="replace")
    golden = None
    if len(args) == 2:
        golden = Path(args[1]).read_text(encoding="utf-8", errors="replace")
    check(text, golden)
    print("check_gcode ok", "toe_first", os.environ.get("APEX_MIN_LAYER_TIME", "4"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
