#!/usr/bin/env python3
"""Independent check that a candidate side reproduces the opposite live side.

Does not import the frozen gates. Expands modal G0/G1 to absolute XYZ, aligns
the two files by their belt-contact X centres (Y <= 0.6), and reports the
maximum nearest-point distance of extrusions. No mirror is applied.
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

WORD = __import__("re").compile(r"([XYZEF])(-?\d*\.?\d+)")


def extrusions(path: Path):
    x = y = z = 0.0
    e_abs = 0.0
    rel = True
    pts = []
    bot = []
    with path.open(errors="replace") as fh:
        for line in fh:
            if line.startswith("M82"):
                rel = False
                continue
            if line.startswith("M83"):
                rel = True
                continue
            code = line.split(";", 1)[0]
            if not code.startswith(("G0", "G1", "G00", "G01")):
                continue
            ev = None
            for k, v in WORD.findall(code):
                v = float(v)
                if k == "X":
                    x = v
                elif k == "Y":
                    y = v
                elif k == "Z":
                    z = v
                elif k == "E":
                    ev = v
            if ev is None:
                continue
            de = ev if rel else ev - e_abs
            if not rel:
                e_abs = ev
            if de > 0:
                pts.append((x, y, z))
                if y <= 0.6:
                    bot.append(x)
    P = np.asarray(pts, dtype=np.float64)
    cx = (min(bot) + max(bot)) / 2.0 if bot else 0.0
    return P, cx


def nn_stats(src, dst):
    tree = cKDTree(dst)
    parts = []
    for i in range(0, len(src), 20000):
        d, _ = tree.query(src[i:i + 20000], workers=1)
        parts.append(d)
    d = np.concatenate(parts)
    return {
        "max": float(d.max()),
        "median": float(np.median(d)),
        "frac_le_0.05": float((d <= 0.05).mean()),
        "frac_le_0.30": float((d <= 0.30).mean()),
    }


def compare(a: Path, b: Path) -> str:
    A, cxa = extrusions(a)
    B, cxb = extrusions(b)
    Bs = B.copy()
    Bs[:, 0] += cxa - cxb
    ab = nn_stats(A, Bs)
    ba = nn_stats(Bs, A)
    return (
        f"{a.name} vs {b.name}: extrusions {len(A)}/{len(B)} "
        f"x_centre {cxa:.3f}/{cxb:.3f} shift_mm={cxa - cxb:.5f} "
        f"cand_to_live max={ab['max']:.4f} median={ab['median']:.4f} "
        f"frac<=0.05={ab['frac_le_0.05']:.4f} frac<=0.30={ab['frac_le_0.30']:.4f} "
        f"live_to_cand max={ba['max']:.4f} median={ba['median']:.4f} "
        f"frac<=0.05={ba['frac_le_0.05']:.4f}"
    )


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/handedness_grade")
    pairs = []
    for mat in ("apex_core", "apex_light", "apex_forge", "apex_bounce"):
        pairs.append((root / f"cand_{mat}_left.gcode", root / f"live_{mat}_right.gcode"))
        pairs.append((root / f"cand_{mat}_right.gcode", root / f"live_{mat}_left.gcode"))
    heel = root / "cand_apex_core_left_heel.gcode"
    if heel.is_file():
        pairs.append((heel, root / "live_apex_core_right.gcode"))
    for a, b in pairs:
        print(compare(a, b), flush=True)


if __name__ == "__main__":
    main()
