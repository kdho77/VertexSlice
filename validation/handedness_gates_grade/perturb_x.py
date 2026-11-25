#!/usr/bin/env python3
"""Copy a binary STL and add +1e-5 mm to every vertex X. Winding is unchanged.

Normals are left as stored. The script reports how many X coordinates actually
change once packed back into float32, because 1e-5 mm is near a float32 ulp
for coordinates of tens of millimetres.
"""
import struct
import sys
from pathlib import Path

DX = 1e-5


def perturb(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:200].lower():
        raise SystemExit(f"ASCII STL not supported: {src}")
    n = struct.unpack_from("<I", data, 80)[0]
    if 84 + n * 50 != len(data):
        raise SystemExit(f"binary STL length mismatch n={n} bytes={len(data)}")
    header = b"handedness-grade RIGHT x+1e-5".ljust(80, b"\0")
    out = bytearray(header[:80])
    out += struct.pack("<I", n)
    changed = 0
    verts = 0
    for i in range(n):
        vals = list(struct.unpack_from("<12fH", data, 84 + i * 50))
        for k in (3, 6, 9):
            verts += 1
            packed = struct.unpack("<f", struct.pack("<f", vals[k] + DX))[0]
            if packed != vals[k]:
                changed += 1
            vals[k] = packed
        out += struct.pack("<12fH", *vals)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    print(f"wrote {dst} triangles={n} vertex_x={verts} x_changed_in_float32={changed} dx_mm={DX}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} SRC.stl DST.stl")
    perturb(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
