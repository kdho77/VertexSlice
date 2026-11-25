#!/usr/bin/env python3
"""Bad-change proofs against the frozen gates. Does not edit those gates.

  scale-x IN OUT          X coordinates on G0/G1 * 1.01 (1% scale). H3 must FAIL.
  move-seam IN OUT        Lift one closed outer-wall seam off that loop's belt edge.
                          The loop stays closed (start and end move together). H2 must FAIL.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "seam_fuse_gates"))
import sfg_common as C  # noqa: E402  (frozen module, imported read-only)


def scale_x(src: Path, dst: Path, factor: float = 1.01) -> None:
    num = re.compile(r"X(-?\d*\.?\d+)")
    lines = src.read_text(errors="replace").split("\n")
    out = []
    n = 0
    for line in lines:
        if line.startswith(("G0", "G1", "G00", "G01")):
            code, sep, com = line.partition(";")

            def repl(m, factor=factor):
                return f"X{float(m.group(1)) * factor:.5f}"

            code2, k = num.subn(repl, code)
            n += k
            line = code2 + (sep + com if sep else "")
        out.append(line)
    dst.write_text("\n".join(out))
    print(f"scale-x factor={factor} x_tokens={n} -> {dst}")


def _set_y_on_line(line: str, new_y: float) -> str:
    code, sep, com = line.partition(";")
    code2, k = re.subn(r"Y(-?\d*\.?\d+)", f"Y{new_y:.5f}", code, count=1)
    if k != 1:
        raise SystemExit(f"line has no Y word to move: {line[:120]!r}")
    return code2 + (sep + com if sep else "")


def move_seam(src: Path, dst: Path, lift_mm: float = 4.0) -> None:
    """Move one closed outer seam off the belt edge and keep that loop closed."""
    text = src.read_text(errors="replace")
    lines = text.split("\n")
    ev = C.parse(str(src))
    chains = C.perimeter_chains(ev)
    target = None
    for ch in chains:
        if ch["closed"] and "Outer wall" in ch["types"] and C.on_bottom(ch, ch["start"][0], ch["start"][1], 0.15):
            target = ch
            break
    if target is None:
        raise SystemExit("no closed outer seam on the belt edge")
    sx, sy = target["start"]
    ex, ey = target["end"]
    if abs(sx - ex) > C.CLOSE_TOL or abs(sy - ey) > C.CLOSE_TOL:
        raise SystemExit(f"closed loop start {target['start']} != end {target['end']}")
    first_i = target["seg_idx"][0]
    setter = None
    for j in range(first_i - 1, -1, -1):
        d = ev[j]
        if d["kind"] in ("ext", "travel") and abs(d["y1"] - sy) <= 1e-4 and abs(d["x1"] - sx) <= 1e-3:
            setter = d
            break
    if setter is None:
        raise SystemExit("could not find the move that places the seam")
    last = target["segs"][-1]
    new_y = sy + lift_mm
    for ev_line, label in ((setter["line"], "seam-travel"), (last["line"], "seam-end")):
        raw = lines[ev_line - 1]
        if "Y" not in raw.split(";", 1)[0]:
            raise SystemExit(f"{label} line {ev_line} has no Y word: {raw[:120]!r}")
        lines[ev_line - 1] = _set_y_on_line(raw, new_y)
    dst.write_text("\n".join(lines))
    # Confirm the mutation did what H2 measures: still one more off-bottom closed seam.
    ev2 = C.parse(str(dst))
    ch2 = C.perimeter_chains(ev2)
    moved = [c for c in ch2 if c["closed"] and "Outer wall" in c["types"]
             and not C.on_bottom(c, c["start"][0], c["start"][1], 0.15)]
    print(
        f"move-seam layer={target['layer']} from_y={sy:.3f} to_y={new_y:.3f} "
        f"closed_outer_off_bottom_now={len(moved)} -> {dst}"
    )
    if not moved:
        raise SystemExit("seam lift did not leave a closed outer seam off the belt edge")


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] not in ("scale-x", "move-seam"):
        raise SystemExit(f"usage: {sys.argv[0]} scale-x|move-seam IN OUT")
    src, dst = Path(sys.argv[2]), Path(sys.argv[3])
    if sys.argv[1] == "scale-x":
        scale_x(src, dst)
    else:
        move_seam(src, dst)


if __name__ == "__main__":
    main()
