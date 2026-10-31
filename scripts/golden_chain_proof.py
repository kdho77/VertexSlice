#!/usr/bin/env python3
"""POST both production chains to /v1/slice and golden_gate the LOCKED files.

Goldens and the input STLs are fetched, not committed.

  VS_CORE_STL, VS_CORE_GOLDEN
  VS_LIGHT_STL, VS_LIGHT_GOLDEN

or a directory VS_GOLDEN_DIR containing:

  procedural-default-round_2p5mm.stl
  ApexCore_95A_GOLDEN_2026-10-31.gcode
  M9_round_L_bottomtext_big_v3_leader.stl
  ApexLight_90A_GOLDEN_2026-10-31.gcode

Exit 0 only when both normalized diffs are 0. Exit 1 on a diff. Exit 2 when
the locked files were not provided.
"""
from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "scripts")]

from golden_gate import compare_paths, format_report  # noqa: E402

CORE_SHA = "54af16eda2e11dbef354f594dd501afe799d45f3ee679819b2c65908aaef5324"
LIGHT_SHA = "3a0a54a2322e579e2c5cd4e003f061a86af61d49310115299ce6856628424e67"


def _pair(kind: str) -> tuple[Path, Path] | None:
    stl = os.environ.get(f"VS_{kind}_STL")
    golden = os.environ.get(f"VS_{kind}_GOLDEN")
    if stl and golden:
        return Path(stl), Path(golden)
    root = os.environ.get("VS_GOLDEN_DIR")
    if not root:
        return None
    base = Path(root)
    if kind == "CORE":
        return (
            base / "procedural-default-round_2p5mm.stl",
            base / "ApexCore_95A_GOLDEN_2026-10-31.gcode",
        )
    return (
        base / "M9_round_L_bottomtext_big_v3_leader.stl",
        base / "ApexLight_90A_GOLDEN_2026-10-31.gcode",
    )


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _multipart(stl: Path, material: str) -> tuple[bytes, str]:
    boundary = "----vertexslicegolden"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )

    field("profile_id", "apex-v2-insole-medium")
    field("material", material)
    field("print_direction", "toe_first")
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"; filename="{stl.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
    )
    chunks.append(stl.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_slice(base_url: str, material: str, stl: Path, dest: Path) -> str:
    """POST /v1/slice and write the response body. Returns the X-Chain header."""
    body, content_type = _multipart(stl, material)
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/slice",
        data=body,
        headers={
            "Content-Type": content_type,
            "X-API-Key": os.environ.get("VERTEXSLICE_API_KEY", "dev-change-me"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2400) as resp:
            dest.write_bytes(resp.read())
            return resp.headers.get("X-Chain", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"/v1/slice {material} HTTP {exc.code}: {detail}") from exc


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class SliceServer:
    """One uvicorn process. The proof posts to POST /v1/slice."""

    def __init__(self) -> None:
        self.port = _free_port()
        env = os.environ.copy()
        env.setdefault("APEX_MIN_LAYER_TIME", "4")
        env.setdefault("VERTEXSLICE_API_KEY", "dev-change-me")
        self._log = tempfile.NamedTemporaryFile(prefix="golden_http_", suffix=".log", delete=False)
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=str(REPO),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    def wait_ready(self, timeout_s: float = 30) -> None:
        deadline = time.monotonic() + timeout_s
        url = f"http://127.0.0.1:{self.port}/health"
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                self._log.flush()
                last = Path(self._log.name).read_text(encoding="utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"uvicorn exited {self._proc.returncode}: {last}")
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        raise RuntimeError("uvicorn /health did not become ready")

    def slice(self, material: str, stl: Path, dest: Path) -> str:
        return post_slice(f"http://127.0.0.1:{self.port}", material, stl, dest)

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()


def _one(server: SliceServer, material: str, stl: Path, golden: Path, expect: str) -> int:
    digest = _sha(golden)
    if digest != expect:
        print(f"{material} golden sha256 {digest} != {expect}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix=f"golden_{material}_") as td:
        gcode = Path(td) / "slice.gcode"
        chain = server.slice(material, stl, gcode)
        result = compare_paths(gcode, golden)
    print(material, "chain", chain)
    print(format_report(result))
    return 0 if result.ok else 1


def main() -> int:
    core = _pair("CORE")
    light = _pair("LIGHT")
    if core is None or light is None or not all(p.is_file() for pair in (core, light) for p in pair):
        print(
            "LOCKED goldens/STLs not provided. Set VS_GOLDEN_DIR or "
            "VS_CORE_STL/VS_CORE_GOLDEN and VS_LIGHT_STL/VS_LIGHT_GOLDEN.",
            file=sys.stderr,
        )
        return 2
    os.environ["APEX_MIN_LAYER_TIME"] = os.environ.get("APEX_MIN_LAYER_TIME", "4")
    # check_gcode compares CONFIG_BLOCK when this is set in the server process.
    # ApexLight ignores it. Point it at the core golden before uvicorn starts.
    os.environ["VS_GCODE_GOLDEN"] = str(core[1])
    server = SliceServer()
    try:
        server.wait_ready()
        rc = _one(server, "apex_core", core[0], core[1], CORE_SHA)
        rc |= _one(server, "apex_light", light[0], light[1], LIGHT_SHA)
        return rc
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
