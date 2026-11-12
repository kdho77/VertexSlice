"""3MF modifier ingest. No slicer binary: placement, presets, and the 422 path."""
from __future__ import annotations

import io
import json
import struct
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "validation"))

from server.chains.apex_core import prepare_belt_stl  # noqa: E402
from server.threemf_modifiers import (  # noqa: E402
    ModifierRegionsNotApplied,
    ThreemfError,
    is_3mf_file,
    prepare_belt_3mf,
)
from validation.stl_preprocess import prepare_orthocad_insole_stl  # noqa: E402


def _box(x0, y0, z0, x1, y1, z1) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
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
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    return verts, faces


def write_modifier_3mf(
    path: Path,
    *,
    main: tuple[float, float, float, float, float, float] = (0, 0, 0, 36, 70, 4.6),
    modifier: tuple[float, float, float, float, float, float] | None = (0, 40, -1, 36, 68, 6),
    density: str = "20%",
    pattern: str | None = "gyroid",
    part_id: str = "2",
    application: str = "OrcaSlicer-2.3.0",
    object_density: str | None = None,
) -> None:
    """Orca project 3MF: one normal_part plus an optional modifier_part."""
    main_v, main_f = _box(*main)
    parts_xml = [
        '    <part id="1" subtype="normal_part">\n'
        '      <metadata key="name" value="insole"/>\n'
        + (
            f'      <metadata key="sparse_infill_density" value="{object_density}"/>\n'
            if object_density
            else ""
        )
        + "    </part>"
    ]
    components = [
        '    <component p:path="/3D/Objects/slab.model" objectid="1" '
        'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
    ]
    bodies = [_mesh_body(1, main_v, main_f)]
    if modifier is not None:
        meta = f'      <metadata key="sparse_infill_density" value="{density}"/>\n'
        if pattern is not None:
            meta += f'      <metadata key="sparse_infill_pattern" value="{pattern}"/>\n'
        meta += '      <metadata key="sparse_infill_speed" value="60"/>\n'
        parts_xml.append(
            f'    <part id="{part_id}" subtype="modifier_part">\n'
            '      <metadata key="name" value="met_pad"/>\n'
            f"{meta}"
            "    </part>"
        )
        components.append(
            f'    <component p:path="/3D/Objects/slab.model" objectid="{part_id}" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
        )
        bodies.append(_mesh_body(int(part_id), *_box(*modifier)))
    sub = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">\n'
        " <resources>\n"
        + "\n".join(bodies)
        + "\n </resources>\n <build/>\n</model>\n"
    )
    root = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" '
        'requiredextensions="p">\n'
        f' <metadata name="Application">{application}</metadata>\n'
        " <resources>\n"
        '  <object id="10" type="model">\n'
        "   <components>\n"
        + "\n".join(components)
        + "\n   </components>\n"
        "  </object>\n"
        " </resources>\n"
        ' <build>\n'
        '  <item objectid="10" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/>\n'
        " </build>\n"
        "</model>\n"
    )
    settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<config>\n"
        '  <object id="10">\n'
        '    <metadata key="name" value="insole"/>\n'
        + (
            f'    <metadata key="sparse_infill_density" value="{object_density}"/>\n'
            if object_density
            else ""
        )
        + "\n".join(parts_xml)
        + "\n  </object>\n"
        "</config>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>\n"
    )
    model_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/Objects/slab.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>\n"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        "</Types>\n"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("[Content_Types].xml", types)
        zout.writestr("_rels/.rels", rels)
        zout.writestr("3D/3dmodel.model", root)
        zout.writestr("3D/_rels/3dmodel.model.rels", model_rels)
        zout.writestr("3D/Objects/slab.model", sub)
        zout.writestr("Metadata/model_settings.config", settings)


def _mesh_body(object_id: int, verts, faces) -> str:
    vxml = "\n".join(f'     <vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
    txml = "\n".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    return (
        f'  <object id="{object_id}" type="model">\n'
        "   <mesh>\n    <vertices>\n"
        f"{vxml}\n    </vertices>\n    <triangles>\n"
        f"{txml}\n    </triangles>\n   </mesh>\n  </object>"
    )


def _write_box_stl(path: Path, box) -> None:
    verts, faces = _box(*box)
    with path.open("wb") as handle:
        handle.write(b"vertexslice box".ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(faces)))
        for a, b, c in faces:
            flat = [0.0, 0.0, 1.0]
            for i in (a, b, c):
                flat.extend(verts[i])
            handle.write(struct.pack("<12f", *flat))
            handle.write(struct.pack("<H", 0))


def _settings(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("Metadata/model_settings.config").decode()


def _root_model(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read("3D/3dmodel.model").decode()


class DetectTest(unittest.TestCase):
    def test_extension_and_zip_magic(self):
        self.assertFalse(is_3mf_file(Path("model.stl"), filename="model.stl"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "x")
        raw = buf.getvalue()
        self.assertTrue(raw.startswith(b"PK\x03\x04"))
        tmp = Path(self.id().replace(".", "_") + ".bin")
        # Keep the assertion on the magic helper via a real file name that is not .3mf.
        magic = REPO / "tests" / "_zip_magic_probe.bin"
        magic.write_bytes(raw)
        try:
            self.assertTrue(is_3mf_file(magic, filename="model.stl"))
            self.assertTrue(is_3mf_file(Path("ignored.stl"), filename="model.3mf"))
        finally:
            magic.unlink(missing_ok=True)
        del tmp


class PrepareTest(unittest.TestCase):
    def test_region_density_survives_and_main_stays_global(self):
        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        src = folder / "model.3mf"
        write_modifier_3mf(src, object_density="99%", pattern=None, application="OrthoCAD")
        try:
            dest, meta = prepare_belt_3mf(src, dest_dir=folder / "out")
            self.assertEqual(meta["regions_applied"], 1)
            self.assertEqual(meta["modifiers"][0]["sparse_infill_density"], "20%")
            self.assertEqual(meta["modifiers"][0]["sparse_infill_pattern"], "gyroid")
            settings = _settings(dest)
            self.assertIn('subtype="modifier_part"', settings)
            self.assertIn('key="sparse_infill_density" value="20%"', settings)
            self.assertIn('key="sparse_infill_pattern" value="gyroid"', settings)
            self.assertNotIn("99%", settings)
            self.assertIn("OrcaSlicer-2.3.0", _root_model(dest))
            with zipfile.ZipFile(dest) as zf:
                project = json.loads(zf.read("Metadata/project_settings.config"))
            self.assertIsInstance(project["printable_height"], str)
            self.assertTrue(project["printable_height"].strip())
            self.assertIn('key="plater_id" value="1"', settings)
            bounds = meta["placed_main_bounds"]
            self.assertAlmostEqual(bounds["ymin"], 0.0, places=3)
            self.assertAlmostEqual(bounds["zmin"], 0.0, places=3)
            center = (bounds["xmin"] + bounds["xmax"]) / 2
            self.assertAlmostEqual(center, 140.0, places=2)
            mod = meta["placed_modifiers"][0]
            self.assertGreater(mod["xmin"], bounds["xmin"] + 10)
            self.assertLess(mod["xmax"], bounds["xmax"] + 0.1)
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()

    def test_placement_matches_stl_preprocess(self):
        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        box = (0.0, 0.0, 0.0, 30.0, 50.0, 4.0)
        stl = folder / "model.stl"
        src = folder / "model.3mf"
        _write_box_stl(stl, box)
        write_modifier_3mf(src, main=box, modifier=None)
        try:
            stl_out, _ = prepare_orthocad_insole_stl(stl, x_center_mm=140, print_direction="toe_first")
            _dest, meta = prepare_belt_3mf(src, dest_dir=folder / "out", x_center_mm=140, print_direction="toe_first")
            data = stl_out.read_bytes()
            n = struct.unpack_from("<I", data, 80)[0]
            xs, ys, zs = [], [], []
            for i in range(n):
                vals = struct.unpack_from("<12f", data, 84 + i * 50)
                for j in range(3, 12, 3):
                    xs.append(vals[j])
                    ys.append(vals[j + 1])
                    zs.append(vals[j + 2])
            bounds = meta["placed_main_bounds"]
            self.assertAlmostEqual(bounds["xmin"], min(xs), delta=0.02)
            self.assertAlmostEqual(bounds["xmax"], max(xs), delta=0.02)
            self.assertAlmostEqual(bounds["ymin"], min(ys), delta=0.02)
            self.assertAlmostEqual(bounds["ymax"], max(ys), delta=0.02)
            self.assertAlmostEqual(bounds["zmin"], min(zs), delta=0.02)
            self.assertAlmostEqual(bounds["zmax"], max(zs), delta=0.02)
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()

    def test_stl_prepare_is_the_orthocad_helper(self):
        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        stl = folder / "model.stl"
        _write_box_stl(stl, (0, 0, 0, 10, 20, 3))
        try:
            via_chain, _ = prepare_belt_stl(stl)
            direct, _ = prepare_orthocad_insole_stl(stl, x_center_mm=140, print_direction="toe_first")
            self.assertEqual(via_chain.read_bytes(), direct.read_bytes())
        finally:
            stl.unlink(missing_ok=True)
            folder.rmdir()

    def test_bad_density_is_422_class(self):
        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        src = folder / "model.3mf"
        write_modifier_3mf(src, density="22%")
        try:
            with self.assertRaises(ModifierRegionsNotApplied) as caught:
                prepare_belt_3mf(src, dest_dir=folder / "out")
            self.assertIn("22%", str(caught.exception))
            self.assertIn("14%", str(caught.exception))
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()

    def test_modifier_without_mesh_is_422_class(self):
        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        src = folder / "model.3mf"
        write_modifier_3mf(src, part_id="9")
        # part id 9 has a mesh. Point the part at a missing id while leaving the word in the file.
        settings = _settings(src).replace('id="9"', 'id="99"', 1)
        # That also renames the mesh object. Rebuild a settings-only mismatch:
        write_modifier_3mf(src, modifier=(0, 40, -1, 36, 68, 6), part_id="2")
        raw = (folder / "broken.3mf")
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(raw, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename.endswith("model_settings.config"):
                    data = data.replace(b'part id="2"', b'part id="99"', 1)
                zout.writestr(info.filename, data)
        try:
            with self.assertRaises(ModifierRegionsNotApplied) as caught:
                prepare_belt_3mf(raw, dest_dir=folder / "out")
            self.assertIn("no mesh", str(caught.exception))
            self.assertIn("99", str(caught.exception))
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()
        del settings

    def test_not_a_zip_is_rejected(self):
        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        src = folder / "model.3mf"
        src.write_bytes(b"not a zip")
        try:
            with self.assertRaises(ThreemfError):
                prepare_belt_3mf(src, dest_dir=folder / "out")
        finally:
            src.unlink()
            folder.rmdir()


class ChainBranchTest(unittest.TestCase):
    def test_stl_still_uses_prepare_belt_stl(self):
        import subprocess

        from server.chains import apex_core

        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        stl = folder / "model.stl"
        _write_box_stl(stl, (0, 0, 0, 8, 12, 2))
        seen: list[str] = []

        def fake_run(cmd, **_kwargs):
            for arg in cmd:
                text = str(arg)
                if text.endswith(".stl") or text.endswith(".3mf"):
                    seen.append(text)
                if text.endswith(".gcode"):
                    Path(text).parent.mkdir(parents=True, exist_ok=True)
                    Path(text).write_text("; gcode\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        try:
            with mock.patch("server.chains.apex_core.subprocess.run", fake_run):
                _path, stats = apex_core.run_apex_core(stl, work_dir=folder / "work")
            self.assertTrue(seen, "slice stage did not receive a model")
            self.assertTrue(seen[0].endswith(".stl"))
            self.assertNotIn("regions_applied", stats)
            self.assertEqual(Path(seen[0]).read_bytes(), prepare_belt_stl(stl)[0].read_bytes())
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()

    def test_3mf_does_not_call_stl_prepare(self):
        import subprocess

        from server.chains import apex_core

        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        src = folder / "model.3mf"
        write_modifier_3mf(src, density="14%")
        seen: list[str] = []

        def fake_run(cmd, **_kwargs):
            for arg in cmd:
                text = str(arg)
                if text.endswith(".stl") or text.endswith(".3mf"):
                    seen.append(text)
                if text.endswith(".gcode"):
                    Path(text).write_text("; gcode\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        try:
            with mock.patch("server.chains.apex_core.prepare_belt_stl", side_effect=AssertionError("STL prepare")):
                with mock.patch("server.chains.apex_core.subprocess.run", fake_run):
                    _path, stats = apex_core.run_apex_core(src, work_dir=folder / "work")
            self.assertEqual(stats["regions_applied"], 1)
            self.assertTrue(seen[0].endswith(".3mf"))
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()


class HttpTest(unittest.TestCase):
    def test_422_and_region_header(self):
        from fastapi.testclient import TestClient

        from server.app import app

        folder = Path(self.id().replace(".", "_"))
        folder.mkdir(exist_ok=True)
        bad = folder / "bad.3mf"
        good = folder / "good.3mf"
        write_modifier_3mf(bad, density="22%")
        write_modifier_3mf(good, density="20%")
        try:
            client = TestClient(app)
            rejected = client.post(
                "/v1/slice",
                data={"profile_id": "apex-v2-insole-95a", "material": "apex_core", "infill_density": "26"},
                files={"model": ("model.3mf", bad.read_bytes(), "model/3mf")},
            )
            self.assertEqual(rejected.status_code, 422)
            self.assertIn("22%", rejected.json()["detail"])

            def fake_slice(*_args, **_kwargs):
                gcode = folder / "out.gcode"
                gcode.write_text("; sliced\n", encoding="utf-8")
                return gcode, {
                    "elapsed_s": 1.0,
                    "regions_applied": 1,
                    "chain": "apex_core-g2b-v1",
                }

            with mock.patch("server.app.slice_stl", fake_slice):
                accepted = client.post(
                    "/v1/slice",
                    data={"profile_id": "apex-v2-insole-95a", "material": "apex_core", "infill_density": "26"},
                    files={"model": ("model.3mf", good.read_bytes(), "model/3mf")},
                )
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(accepted.headers["x-vertexslice-regions"], "1")
            self.assertEqual(accepted.text, "; sliced\n")

            def fake_stl(*_args, **_kwargs):
                gcode = folder / "stl.gcode"
                gcode.write_text("; stl\n", encoding="utf-8")
                return gcode, {"elapsed_s": 1.0, "chain": "apex_core-g2b-v1"}

            stl = folder / "model.stl"
            _write_box_stl(stl, (0, 0, 0, 4, 4, 2))
            with mock.patch("server.app.slice_stl", fake_stl):
                stl_resp = client.post(
                    "/v1/slice",
                    data={"profile_id": "apex-v2-insole-95a", "material": "apex_core", "infill_density": "26"},
                    files={"model": ("model.stl", stl.read_bytes(), "model/stl")},
                )
            self.assertEqual(stl_resp.status_code, 200, stl_resp.text)
            self.assertNotIn("x-vertexslice-regions", stl_resp.headers)
        finally:
            for path in folder.rglob("*"):
                if path.is_file():
                    path.unlink()
            for path in sorted(folder.rglob("*"), reverse=True):
                if path.is_dir():
                    path.rmdir()
            folder.rmdir()


if __name__ == "__main__":
    unittest.main()
