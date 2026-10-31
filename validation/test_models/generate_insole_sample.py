#!/usr/bin/env python3
"""Generate a minimal insole-like STL for Apex V2 profile validation."""
from __future__ import annotations

import struct
from pathlib import Path

OUT = Path(__file__).with_name("insole_sample.stl")


def _tri_normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    return (nx, ny, nz)


def _box_tris(x0, y0, z0, x1, y1, z1):
    verts = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    faces = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (2, 3, 7),
        (2, 7, 6),
        (1, 2, 6),
        (1, 6, 5),
        (0, 4, 7),
        (0, 7, 3),
    ]
    tris = []
    for i, j, k in faces:
        tris.append((verts[i], verts[j], verts[k]))
    return tris


def write_binary_stl(path: Path, triangles: list) -> None:
    header = b"Vertex Apex insole sample" + b" " * (80 - 25)
    with path.open("wb") as f:
        f.write(header[:80])
        f.write(struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            n = _tri_normal(a, b, c)
            f.write(struct.pack("<3f", *n))
            f.write(struct.pack("<3f", *a))
            f.write(struct.pack("<3f", *b))
            f.write(struct.pack("<3f", *c))
            f.write(struct.pack("<H", 0))


def main() -> None:
    # Toe at low Y (enters belt first on IR3/Apex axis mapping).
    tris = _box_tris(120.0, 0.0, 0.0, 170.0, 90.0, 12.0)
    write_binary_stl(OUT, tris)
    print(f"Wrote {OUT} ({len(tris)} triangles)")


if __name__ == "__main__":
    main()
