"""GET /health reports a content fingerprint of the slicer build (OrthoCAD's G-code cache key)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server import build_info


def _tree(root: Path, placement: str = "rotate", binary: bytes = b"orca-1") -> Path:
    (root / "server").mkdir(parents=True, exist_ok=True)
    (root / "validation").mkdir(parents=True, exist_ok=True)
    (root / "resources/profiles/IdeaFormer").mkdir(parents=True, exist_ok=True)
    (root / "server" / "app.py").write_text("app = 1\n")
    (root / "validation" / "stl_preprocess.py").write_text(f"PLACEMENT = {placement!r}\n")
    (root / "resources/profiles/IdeaFormer" / "p.json").write_text("{}")
    (root / "bin").mkdir(exist_ok=True)
    (root / "bin" / "orca-slicer").write_bytes(binary)
    return root / "bin" / "orca-slicer"


class BuildInfoTest(unittest.TestCase):
    def test_same_content_same_build(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ba = _tree(Path(a))
            bb = _tree(Path(b))
            self.assertEqual(
                build_info.compute_build(Path(a), str(ba)), build_info.compute_build(Path(b), str(bb))
            )

    def test_placement_change_changes_build(self):
        # VertexSlice #52 changed only validation/stl_preprocess.py; that alone must change the build.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ba = _tree(Path(a), placement="rotate")
            bb = _tree(Path(b), placement="mirror-then-rotate")
            self.assertNotEqual(
                build_info.compute_build(Path(a), str(ba)), build_info.compute_build(Path(b), str(bb))
            )

    def test_slicer_binary_change_changes_build(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ba = _tree(Path(a), binary=b"orca-1")
            bb = _tree(Path(b), binary=b"orca-2")
            self.assertNotEqual(
                build_info.compute_build(Path(a), str(ba)), build_info.compute_build(Path(b), str(bb))
            )

    def test_pycache_ignored(self):
        with tempfile.TemporaryDirectory() as a:
            bin_a = _tree(Path(a))
            before = build_info.compute_build(Path(a), str(bin_a))
            (Path(a) / "server" / "__pycache__").mkdir()
            (Path(a) / "server" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"x")
            self.assertEqual(before, build_info.compute_build(Path(a), str(bin_a)))

    def test_build_is_hex_sha256(self):
        with tempfile.TemporaryDirectory() as a:
            bin_a = _tree(Path(a))
            value = build_info.compute_build(Path(a), str(bin_a))
            self.assertRegex(value, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
