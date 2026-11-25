#!/usr/bin/env python3
"""True-RIGHT fixture: Y-mirror of the true-LEFT OrthoCAD STL, winding reversed.

LEFT is validation/test_models/orthocad_sample_top_insole.stl (toe +X, arch flange -Y).
Negating Y mirrors that foot. Reversing triangle order (v0, v2, v1) keeps the
solid outward after the mirror. No other vertex is moved.
"""
import struct
import sys
from pathlib import Path


def y_mirror_reverse_winding(src: Path, dst: Path) -> int:
    data = src.read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:200].lower():
        raise SystemExit(f"ASCII STL not supported: {src}")
    n = struct.unpack_from("<I", data, 80)[0]
    expect = 84 + n * 50
    if expect != len(data):
        raise SystemExit(f"binary STL length mismatch n={n} bytes={len(data)} expected={expect}")
    header = b"handedness-grade RIGHT y-mirror".ljust(80, b"\0")
    out = bytearray(header[:80])
    out += struct.pack("<I", n)
    for i in range(n):
        vals = struct.unpack_from("<12fH", data, 84 + i * 50)
        v0 = (vals[3], -vals[4], vals[5])
        v1 = (vals[6], -vals[7], vals[8])
        v2 = (vals[9], -vals[10], vals[11])
        # reversed winding: v0, v2, v1
        out += struct.pack("<12fH", 0.0, 0.0, 1.0, *v0, *v2, *v1, vals[12])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    return n


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} LEFT.stl RIGHT.stl")
    n = y_mirror_reverse_winding(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]} triangles={n} y-mirror winding-reversed")


if __name__ == "__main__":
    main()
