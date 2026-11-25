#!/usr/bin/env python3
"""Belt placement cancels Orca's det −1 reflection: Y mirror before Rz."""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from apex_bed_placement import APEX_INSOLE_X_CENTER_MM  # noqa: E402
from server.threemf_modifiers import prepare_belt_3mf  # noqa: E402
from stl_preprocess import _read_triangles, _rotate_z, _write_triangles, prepare_belt_stl  # noqa: E402

X_CENTER = APEX_INSOLE_X_CENTER_MM


def _signed_volume(tris: list[tuple[tuple[float, float, float], ...]]) -> float:
    vol = 0.0
    for (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) in tris:
        vol += x0 * (y1 * z2 - y2 * z1) + x1 * (y2 * z0 - y0 * z2) + x2 * (y0 * z1 - y1 * z0)
    return vol / 6.0


def _write_stl(path: Path, tris: list[tuple[tuple[float, float, float], ...]]) -> None:
    with path.open("wb") as handle:
        handle.write(b"belt mirror wedge".ljust(80, b"\0")[:80])
        handle.write(struct.pack("<I", len(tris)))
        for verts in tris:
            handle.write(struct.pack("<12f", 0.0, 0.0, 1.0, *verts[0], *verts[1], *verts[2]))
            handle.write(struct.pack("<H", 0))


def _wedge() -> list[tuple[tuple[float, float, float], ...]]:
    """Closed triangular prism, outward winding, apex biased toward +X."""
    p = (
        (0.0, 0.0, 0.0),
        (30.0, 0.0, 0.0),
        (10.0, 20.0, 0.0),
        (0.0, 0.0, 4.0),
        (30.0, 0.0, 4.0),
        (10.0, 20.0, 4.0),
    )
    faces = (
        (0, 2, 1),
        (3, 4, 5),
        (0, 1, 4),
        (0, 4, 3),
        (1, 2, 5),
        (1, 5, 4),
        (2, 0, 3),
        (2, 3, 5),
    )
    return [tuple(p[i] for i in face) for face in faces]


def _extents(tris: list[tuple[tuple[float, float, float], ...]]) -> tuple[float, float, float]:
    xs = [p[0] for tri in tris for p in tri]
    ys = [p[1] for tri in tris for p in tri]
    zs = [p[2] for tri in tris for p in tri]
    return max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)


def _x_center(tris: list[tuple[tuple[float, float, float], ...]]) -> float:
    xs = [p[0] for tri in tris for p in tri]
    return (min(xs) + max(xs)) / 2.0


def _negate_y(
    tris: list[tuple[tuple[float, float, float], ...]],
) -> list[tuple[tuple[float, float, float], ...]]:
    """Model-Y negation with winding reversed, the right-hand input live rotates."""
    return [tuple((x, -y, z) for x, y, z in reversed(tri)) for tri in tris]


def _rotate_only_centered(
    tris: list[tuple[tuple[float, float, float], ...]],
    deg: int,
    x_center: float,
) -> list[tuple[tuple[float, float, float], ...]]:
    """Live placement: Rz, then Y/Z min at 0 and X centred. No extra reflection."""
    rotated: list[tuple[tuple[float, float, float], ...]] = []
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for tri in tris:
        row = []
        for x, y, z in tri:
            xr, yr = _rotate_z(x, y, deg)
            row.append((xr, yr, z))
            xs.append(xr)
            ys.append(yr)
            zs.append(z)
        rotated.append(tuple(row))
    min_x, max_x = min(xs), max(xs)
    min_y, min_z = min(ys), min(zs)
    x_shift = x_center - (max_x - min_x) / 2.0 - min_x
    return [tuple((x + x_shift, y - min_y, z - min_z) for x, y, z in tri) for tri in rotated]


def _write_wedge_3mf(path: Path, tris: list[tuple[tuple[float, float, float], ...]]) -> None:
    vertices: list[tuple[float, float, float]] = []
    index: dict[tuple[float, float, float], int] = {}
    faces: list[tuple[int, int, int]] = []
    for tri in tris:
        ids = []
        for vert in tri:
            key = vert
            if key not in index:
                index[key] = len(vertices)
                vertices.append(vert)
            ids.append(index[key])
        faces.append((ids[0], ids[1], ids[2]))
    vert_xml = "\n".join(f'      <vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    face_xml = "\n".join(f'      <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    root = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        ' <metadata name="Application">OrcaSlicer-2.3.0</metadata>\n'
        " <resources>\n"
        '  <object id="1" type="model">\n'
        "   <mesh>\n"
        "    <vertices>\n"
        f"{vert_xml}\n"
        "    </vertices>\n"
        "    <triangles>\n"
        f"{face_xml}\n"
        "    </triangles>\n"
        "   </mesh>\n"
        "  </object>\n"
        " </resources>\n"
        ' <build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/></build>\n'
        "</model>\n"
    )
    with zipfile.ZipFile(path, "w") as zout:
        zout.writestr("3D/3dmodel.model", root)


def _build_transform(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("3D/3dmodel.model").decode()
    start = xml.index('transform="') + len('transform="')
    return xml[start : xml.index('"', start)]


def _linear_det(transform: str) -> float:
    n = [float(part) for part in transform.split()]
    a, d, g = n[0], n[1], n[2]
    b, e, h = n[3], n[4], n[5]
    c, f, i = n[6], n[7], n[8]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _mesh_bytes(path: Path) -> bytes:
    """Triangle count and records. The 80-byte header is not part of the mesh."""
    data = path.read_bytes()
    return data[80:]


class BeltMirrorPlacementTests(unittest.TestCase):
    def _oracle_stl(self, folder: Path, source: list[tuple[tuple[float, float, float], ...]], deg: int) -> Path:
        oracle = _rotate_only_centered(_negate_y(source), deg, X_CENTER)
        path = folder / f"oracle_{deg}.stl"
        _write_triangles(path, oracle)
        return path

    def test_prepare_belt_stl_matches_rotated_y_negation(self) -> None:
        source = _wedge()
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            src = folder / "wedge.stl"
            _write_stl(src, source)
            for deg in (270, 90):
                out, meta = prepare_belt_stl(src, deg, bed_width_x_mm=250.0, x_center_mm=X_CENTER)
                self.assertTrue(meta.get("mirror_y_pre_rotation"))
                self.assertEqual(meta["x_center_mm"], X_CENTER)
                self.assertEqual(meta["rotation_z_deg"], deg)
                oracle = self._oracle_stl(folder, source, deg)
                self.assertEqual(out.read_bytes(), oracle.read_bytes())
                prepared = _read_triangles(out)
                self.assertAlmostEqual(_x_center(prepared), X_CENTER, delta=0.01)
                src_vol = _signed_volume(source)
                prep_vol = _signed_volume(prepared)
                self.assertGreater(src_vol, 0.0)
                self.assertGreater(prep_vol, 0.0)
                self.assertAlmostEqual(prep_vol, src_vol, delta=abs(src_vol) * 0.02 + 1e-3)
                self.assertEqual(_extents(prepared), _extents(_read_triangles(oracle)))

    def test_prepare_belt_3mf_matches_rotated_y_negation(self) -> None:
        source = _wedge()
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            stl = folder / "wedge.stl"
            src = folder / "wedge.3mf"
            _write_stl(stl, source)
            _write_wedge_3mf(src, source)
            for direction, deg in (("toe_first", 270), ("heel_first", 90)):
                stl_out, stl_meta = prepare_belt_stl(stl, deg, bed_width_x_mm=250.0, x_center_mm=X_CENTER)
                dest, meta = prepare_belt_3mf(
                    src,
                    dest_dir=folder / direction,
                    x_center_mm=X_CENTER,
                    print_direction=direction,
                )
                self.assertTrue(meta.get("mirror_y_pre_rotation"))
                self.assertTrue(stl_meta.get("mirror_y_pre_rotation"))
                self.assertEqual(meta["x_center_mm"], X_CENTER)
                self.assertEqual(meta["rotation_z_deg"], deg)
                det = _linear_det(_build_transform(dest))
                self.assertLess(det, 0.0)
                self.assertAlmostEqual(abs(det), 1.0, delta=1e-6)
                oracle = self._oracle_stl(folder, source, deg)
                placed_path = Path(meta["placed_main_stl"])
                self.assertEqual(_mesh_bytes(placed_path), _mesh_bytes(stl_out))
                self.assertEqual(_mesh_bytes(placed_path), _mesh_bytes(oracle))
                bounds = meta["placed_main_bounds"]
                self.assertAlmostEqual((bounds["xmin"] + bounds["xmax"]) / 2.0, X_CENTER, delta=0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
