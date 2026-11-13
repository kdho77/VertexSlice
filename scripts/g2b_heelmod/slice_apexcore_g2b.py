#!/usr/bin/env python3
"""LOCKED ApexCore 95A profile. No changes without Kendon's approval.

Printed file sha256
1eb294be4315565e868c0960a569f4f4158253f41b69955dc15cd3da77fb826e
is the file from before the forefoot stitch. Kendon approved that stitch
on 2026-11-13 (PM): classic walls, gap fill on, min bead 25%, detect thin
walls. At 11:27 AM MT the same day he kept wall_loops at 2, so the heel and
arch still get two walls. Classic walls stitch a section that cannot hold
two closed loops. At 4:35 PM MT the extra print time was travels between
gap beads that already meet. This script prints each of those chains as one
path, and the lock drops gap polylines shorter than 8 mm. The belt-edge
center seam stays aligned. The numbers live in apexcore_95a_locked.json.
prepare() refuses to slice if a setting drifts from that file.

G2b CONFIG_BLOCK with the ApexCore filament swapped in. No heelmod: no
layer-range flow, min_bead, ensure_all, min-time, and no global
infill_wall_overlap or minimum_sparse_infill_area edits. Layer height stays
0.46 mm. ApexCore additions are nozzle temperature 225 °C, part fan 50% /
aux 25%, the locked wall/sparse/gap flow ratios, and pressure advance 0.05.
Layer-change retract is off and retraction length is 0.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO / "validation"), str(REPO), str(HERE)]


def _default_orca() -> str:
    for candidate in (
        Path("/app/bin/orca-slicer"),
        REPO / "build" / "src" / "Release" / "orca-slicer",
        REPO / "build" / "src" / "orca-slicer",
    ):
        if candidate.is_file():
            return str(candidate)
    return "/app/bin/orca-slicer"


os.environ.setdefault("ORCA_SLICER_BIN", _default_orca())
os.environ.setdefault("ORCA_RESOURCES", str(REPO / "resources"))
# Box rootfs lib dir, only when that tree is actually mounted.
lib = os.environ.get("G2B_LD_LIBRARY_PATH", "")
if lib and Path(lib).is_dir():
    prior = os.environ.get("LD_LIBRARY_PATH")
    os.environ["LD_LIBRARY_PATH"] = lib + (":" + prior if prior else "")

from slice_g2b_heelmod import (  # noqa: E402
    APEXCORE_FOREFOOT_STITCH,
    Y_MACH_OFFSET_LIGHT,
    assert_g2b_identity,
    flatten,
    overlay_g2b,
    slice_model,
)

# Measured on this binary at layer 0.46 mm, wall_loops 2, overlap 15%, with
# the volumetric 0.18 ratios (outer=inner 0.8925, sparse 1.0042, solid 0.9251):
# outer 0.1584, inner 0.1460, sparse 0.1783, solid 0.2031 E/mm. Scale each
# feature onto 0.18. Gap fill did not appear as its own feature; its ratio
# follows sparse down to ApexCore's 0.16 E/mm target.
OUTER_MEASURED = 0.1584
INNER_MEASURED = 0.1460
SPARSE_MEASURED = 0.1783
SOLID_MEASURED = 0.2031
OUTER_BASE = 0.8925
INNER_BASE = 0.8925
SPARSE_BASE = 1.0042
SOLID_BASE = 0.9251
GAP_E_PER_MM = 0.16
# Offset that puts the 0.46 mm toe on the belt the way the G2b file does
# (outer min Y about 0.02). The heelmod constant -2.136 clips the sole.
CORE_Y_OFFSET = -1.146
LOCK_PATH = HERE / "apexcore_95a_locked.json"


def _scalar(value) -> str:
    if isinstance(value, list):
        return "" if not value else str(value[0])
    return str(value)


def _enforce_locked(machine: dict, process: dict, filament: dict, y_offset: float) -> None:
    """Refuse the slice if the prepared profile is not the printed ApexCore 95A."""
    lock = json.loads(LOCK_PATH.read_text())
    if lock.get("status") != "LOCKED":
        raise SystemExit("apexcore_95a_locked.json is not marked LOCKED")
    if abs(y_offset - float(lock["y_mach_offset"])) > 1e-9:
        raise SystemExit(
            f"LOCKED ApexCore 95A: y_mach_offset {y_offset} != {lock['y_mach_offset']}. "
            "No changes without Kendon's approval."
        )
    owners = {"machine": machine, "process": process, "filament": filament}
    for key, spec in lock["settings"].items():
        dest = owners[spec["profile"]]
        got = _scalar(dest.get(key))
        if got != spec["value"]:
            raise SystemExit(
                f"LOCKED ApexCore 95A: {spec['profile']}.{key} is {got!r}, "
                f"printed profile requires {spec['value']!r}. "
                "No changes without Kendon's approval."
            )
    start = machine.get("machine_start_gcode", "")
    if lock["pressure_advance"] not in start:
        raise SystemExit("LOCKED ApexCore 95A: pressure advance line missing. No changes without Kendon's approval.")


def _g2b_off(profile: dict, key: str) -> None:
    cur = profile.get(key)
    profile[key] = ["0"] if isinstance(cur, list) or cur is None else "0"


def _apply_core_flows(process: dict, filament: dict) -> None:
    fil = float(filament.get("filament_flow_ratio", ["1.03"])[0])
    layer_h = float(process["layer_height"])
    outer = OUTER_BASE * (0.18 / OUTER_MEASURED)
    inner = INNER_BASE * (0.18 / INNER_MEASURED)
    sparse = SPARSE_BASE * (0.18 / SPARSE_MEASURED)
    solid = SOLID_BASE * (0.18 / SOLID_MEASURED)
    gap = sparse * (GAP_E_PER_MM / 0.18)
    process["outer_wall_flow_ratio"] = f"{outer:.4f}"
    process["inner_wall_flow_ratio"] = f"{inner:.4f}"
    process["sparse_infill_flow_ratio"] = f"{sparse:.4f}"
    process["internal_solid_infill_flow_ratio"] = f"{solid:.4f}"
    process["gap_fill_flow_ratio"] = f"{gap:.4f}"
    print(
        "flows",
        f"fil={fil}",
        f"layer={layer_h}",
        f"outer={outer:.4f}",
        f"inner={inner:.4f}",
        f"sparse={sparse:.4f}",
        f"solid={solid:.4f}",
        f"gap={gap:.4f}",
        f"first_layer={process.get('first_layer_flow_ratio')}",
    )


def prepare(work: Path, material: str) -> tuple[dict, dict, dict, float]:
    machine = flatten("machine/Vertex Apex V2 1.0 nozzle.json", work)
    process = flatten("process/0.42mm TPU Insole Medium @Vertex Apex V2.json", work)
    if material == "light":
        filament = flatten("filament/ApexLight.json", work)
        overlay_g2b(machine, process, filament)
        assert_g2b_identity(process, filament, machine)
        return machine, process, filament, Y_MACH_OFFSET_LIGHT
    if material != "core":
        raise SystemExit("material must be light or core")
    filament = flatten("filament/ApexCore.json", work)
    saved = json.loads(json.dumps(filament))
    overlay_g2b(machine, process, filament)
    assert_g2b_identity(process, filament, machine)
    # Filament block is ApexCore. Process/machine stay on the G2b CONFIG_BLOCK,
    # then the forefoot stitch Kendon approved on 2026-11-13 is applied on top.
    # wall_loops stays the shared G2b value of 2 (Kendon, 11:27 AM MT).
    # Shared G2b keys (ApexLight, seam, fan, flow targets, overlap) stay put.
    filament = saved
    for key, value in APEXCORE_FOREFOOT_STITCH.items():
        cur = process.get(key)
        process[key] = [value] if isinstance(cur, list) else value
    assert_g2b_identity(process, filament, machine, forefoot_stitch=True)
    if process.get("layer_height") != "0.46":
        raise SystemExit(f"layer height drifted to {process.get('layer_height')!r}")
    # ApexCore's filament retraction is 1.5 mm and the machine flag is on, so
    # the slicer emits a retract/prime on travels and at every layer change.
    # G2b has none of those. The flag is off and the length stays 0.
    _g2b_off(machine, "retract_when_changing_layer")
    _g2b_off(filament, "filament_retract_when_changing_layer")
    _g2b_off(filament, "filament_retraction_length")
    _apply_core_flows(process, filament)
    # Locked placement. An environment override would reprint a different file.
    y_offset = CORE_Y_OFFSET
    _enforce_locked(machine, process, filament, y_offset)
    start = machine.get("machine_start_gcode", "")
    if "SET_PRESSURE_ADVANCE ADVANCE=0.05" not in start:
        raise SystemExit("ApexCore start gcode lost pressure advance")
    if process.get("sparse_infill_pattern") != "gyroid":
        raise SystemExit(f"sparse infill pattern {process.get('sparse_infill_pattern')!r}")
    if process.get("infill_wall_overlap") != "15%":
        raise SystemExit(f"overlap drifted to {process.get('infill_wall_overlap')!r}")
    if str(process.get("minimum_sparse_infill_area")) != "5":
        raise SystemExit(f"min sparse drifted to {process.get('minimum_sparse_infill_area')!r}")
    # Lock still requires the printed 8 s value. The 2026-10-31 golden is the
    # same profile with slow_down_layer_time taken from APEX_MIN_LAYER_TIME
    # (default 4) after that check. 8 reproduces the 2026-10-22 golden.
    min_layer = os.environ.get("APEX_MIN_LAYER_TIME", "4").strip() or "4"

    def _write_setting(profile: dict, key: str, value: str) -> None:
        cur = profile.get(key)
        profile[key] = [value] if isinstance(cur, list) else value

    _write_setting(filament, "slow_down_layer_time", min_layer)
    _write_setting(process, "slow_down_layer_time", min_layer)
    # Hardness changes this key only, after the locked-profile check.
    raw_infill = os.environ.get("VS_SPARSE_INFILL_DENSITY", "").strip()
    if raw_infill:
        pct = int(round(float(raw_infill.rstrip("%"))))
        _write_setting(process, "sparse_infill_density", f"{pct}%")
        if _scalar(process.get("sparse_infill_pattern")) != "gyroid":
            raise SystemExit(f"sparse infill pattern {process.get('sparse_infill_pattern')!r}")
    return machine, process, filament, y_offset


_WORD = re.compile(r"([XYZEF])([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
# Gap-fill beads whose ends meet. Classic variable-width splits one medial
# axis into separate paths, and the planner travels between them instead of
# following the chain. 0.3 mm is the join tolerance; measured joins were 0.
_GAP_JOIN_MM = 0.3


def _g1_words(line: str) -> dict[str, str]:
    body = line.split(";", 1)[0]
    return dict(_WORD.findall(body))


def _is_gap_extrusion(line: str) -> bool:
    body = line.split(";", 1)[0].strip()
    if not body.startswith(("G0", "G1")):
        return False
    w = _g1_words(line)
    if "E" not in w or float(w["E"]) <= 0:
        return False
    return "X" in w or "Y" in w


def _is_travel(line: str) -> bool:
    body = line.split(";", 1)[0].strip()
    if not body.startswith(("G0", "G1")):
        return False
    w = _g1_words(line)
    if "X" not in w and "Y" not in w:
        return False
    return "E" not in w or float(w["E"]) <= 0


def _xy_of(line: str, x: float | None, y: float | None) -> tuple[float | None, float | None]:
    w = _g1_words(line)
    nx = float(w["X"]) if "X" in w else x
    ny = float(w["Y"]) if "Y" in w else y
    return nx, ny


def _rewrite_dest(line: str, x: float, y: float) -> str:
    def sub(match: re.Match[str]) -> str:
        axis, _value = match.group(1), match.group(2)
        if axis == "X":
            return f"X{x:.3f}"
        if axis == "Y":
            return f"Y{y:.3f}"
        return match.group(0)

    return _WORD.sub(sub, line, count=0)


def _chain_beads(beads: list[dict]) -> list[list[tuple[int, bool]]]:
    """Greedy chains. Each step is (bead index, reversed)."""
    n = len(beads)
    unused = set(range(n))
    grid: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def cell(x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / _GAP_JOIN_MM)), int(math.floor(y / _GAP_JOIN_MM)))

    def add(i: int) -> None:
        b = beads[i]
        for end, pt in ((0, b["pts"][0]), (1, b["pts"][-1])):
            grid.setdefault(cell(*pt), []).append((i, end))

    def drop(i: int) -> None:
        b = beads[i]
        for end, pt in ((0, b["pts"][0]), (1, b["pts"][-1])):
            bucket = grid.get(cell(*pt))
            if not bucket:
                continue
            grid[cell(*pt)] = [item for item in bucket if item[0] != i]

    for i in range(n):
        add(i)

    def near(x: float, y: float, skip: int) -> tuple[int, int] | None:
        cx, cy = cell(x, y)
        best = None
        best_d = _GAP_JOIN_MM
        for ix in (cx - 1, cx, cx + 1):
            for iy in (cy - 1, cy, cy + 1):
                for i, end in grid.get((ix, iy), ()):
                    if i == skip or i not in unused:
                        continue
                    pt = beads[i]["pts"][0] if end == 0 else beads[i]["pts"][-1]
                    d = math.hypot(pt[0] - x, pt[1] - y)
                    if d <= best_d:
                        best_d = d
                        best = (i, end)
        return best

    chains = []
    while unused:
        i = unused.pop()
        drop(i)
        # Deque of (index, reversed). Left is the current start.
        chain = [(i, False)]
        start = beads[i]["pts"][0]
        end = beads[i]["pts"][-1]
        grew = True
        while grew:
            grew = False
            nxt = near(end[0], end[1], -1)
            if nxt is not None:
                j, end_flag = nxt
                unused.remove(j)
                drop(j)
                # end_flag 0 means the bead's start meets us, so it is not reversed.
                reversed_bead = end_flag == 1
                chain.append((j, reversed_bead))
                end = beads[j]["pts"][0] if reversed_bead else beads[j]["pts"][-1]
                grew = True
                continue
            prv = near(start[0], start[1], -1)
            if prv is not None:
                j, end_flag = prv
                unused.remove(j)
                drop(j)
                # end_flag 1 means the bead's end meets our start, so it is not reversed.
                reversed_bead = end_flag == 0
                chain.insert(0, (j, reversed_bead))
                start = beads[j]["pts"][-1] if reversed_bead else beads[j]["pts"][0]
                grew = True
        chains.append(chain)
    return chains


def _emit_gap_chains(beads: list[dict], origin: tuple[float, float] | None) -> list[str]:
    if not beads:
        return []
    chains = _chain_beads(beads)
    # Nearest chain first, so the hop between chains stays short.
    pending = list(range(len(chains)))
    pos = origin
    out: list[str] = []

    def chain_start(ci: int) -> tuple[float, float]:
        i, rev = chains[ci][0]
        pts = beads[i]["pts"]
        return pts[-1] if rev else pts[0]

    while pending:
        if pos is None:
            pick = pending.pop(0)
        else:
            pick = min(pending, key=lambda ci: math.hypot(chain_start(ci)[0] - pos[0], chain_start(ci)[1] - pos[1]))
            pending.remove(pick)
        sx, sy = chain_start(pick)
        if pos is not None and math.hypot(sx - pos[0], sy - pos[1]) > _GAP_JOIN_MM:
            out.append("M204 S10000\n")
            out.append(f"G1 X{sx:.3f} Y{sy:.3f} F12720\n")
            out.append("M204 S8000\n")
        for i, rev in chains[pick]:
            bead = beads[i]
            out.extend(bead["header"])
            moves = bead["moves"]
            pts = bead["pts"]
            if not rev:
                out.extend(moves)
                pos = pts[-1]
            else:
                # Walk the polyline backwards. E on each move stays, destination swaps.
                for k, move in enumerate(reversed(moves)):
                    dest = pts[-(k + 2)]
                    out.append(_rewrite_dest(move, dest[0], dest[1]))
                pos = pts[0]
    return out


def chain_gap_fill_travels(text: str) -> str:
    """Print each gap-fill chain as one path.

    Extrusion geometry is unchanged. Travels between beads that already meet
    are dropped. The travel after the last bead of a gap block is kept: it
    positions the next feature, and dropping it makes that feature's first
    extrusion start at the last bead. Kendon, 2026-11-13 4:35 PM MT: the
    scoped slice added about an hour at the same filament weight, and that
    hour was the travels between beads that already meet.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    beads: list[dict] = []
    header: list[str] = []
    # Lines after the latest gap extrusion. Travels in here are either the
    # hop the chain replaces (if another bead follows) or the exit to the
    # next feature (if the block ends).
    tail: list[str] = []
    gap = False
    x: float | None = None
    y: float | None = None
    origin: tuple[float, float] | None = None

    def flush() -> None:
        nonlocal beads, header, tail, origin
        if beads:
            out.extend(_emit_gap_chains(beads, origin))
        out.extend(header)
        out.extend(tail)
        beads = []
        header = []
        tail = []
        origin = None

    for line in lines:
        if line.startswith(";TYPE:") or line.startswith(";LAYER_CHANGE"):
            flush()
            gap = line.startswith(";TYPE:Gap infill")
            if gap and x is not None and y is not None:
                origin = (x, y)
            out.append(line)
            continue
        if not gap:
            body = line.split(";", 1)[0].strip()
            if body.startswith(("G0", "G1")) and ("X" in body or "Y" in body):
                x, y = _xy_of(line, x, y)
            out.append(line)
            continue
        if _is_gap_extrusion(line):
            if x is None or y is None:
                header.append(line)
                continue
            # The travels since the previous bead are the ones the chain
            # replaces. Width, feed, and accel lines in that span belong to
            # this bead.
            if tail:
                header.extend(ln for ln in tail if not _is_travel(ln))
                tail = []
            nx, ny = _xy_of(line, x, y)
            # A bead that continues the previous one with no travel stays one bead.
            if (
                beads
                and not header
                and abs(beads[-1]["pts"][-1][0] - x) < 1e-6
                and abs(beads[-1]["pts"][-1][1] - y) < 1e-6
            ):
                beads[-1]["pts"].append((nx, ny))
                beads[-1]["moves"].append(line)
            else:
                beads.append({"pts": [(x, y), (nx, ny)], "moves": [line], "header": header})
                header = []
            x, y = nx, ny
            continue
        if _is_travel(line):
            tail.append(line)
            x, y = _xy_of(line, x, y)
            continue
        if tail:
            tail.append(line)
        else:
            header.append(line)

    flush()
    return "".join(out)


def _estimate_seconds(text: str) -> float:
    """Cruise time for extrusions, trapezoid time for travels.

    This is the figure that matches the pre-stitch slicer footer (about
    83.5 min against the written 1h 24m 20s). The slicer's own footer on a
    stitched file counts the travels between gap beads that already meet.
    """
    x = y = None
    feed = 3000.0
    accel = 8000.0
    total = 0.0
    for line in text.splitlines():
        if line.startswith("M204"):
            match = re.search(r"S([0-9.]+)", line)
            if match:
                accel = float(match.group(1))
            continue
        if line.startswith(";") or not line.startswith(("G0", "G1")):
            continue
        words = _g1_words(line)
        if "F" in words:
            feed = float(words["F"])
        nx = float(words["X"]) if "X" in words else x
        ny = float(words["Y"]) if "Y" in words else y
        de = float(words["E"]) if "E" in words else 0.0
        dist = math.hypot((nx or 0) - (x or 0), (ny or 0) - (y or 0)) if x is not None and nx is not None else 0.0
        speed = feed / 60.0
        if de > 0 and dist > 0 and speed > 0:
            total += dist / speed
        elif dist > 0.05 and speed > 0 and accel > 0:
            accel_dist = speed * speed / (2 * accel)
            if dist >= 2 * accel_dist:
                total += 2 * (speed / accel) + (dist - 2 * accel_dist) / speed
            else:
                total += 2 * math.sqrt(dist / accel)
        if nx is not None:
            x, y = nx, ny
    return total


def _format_duration(seconds: float) -> str:
    whole = int(round(seconds))
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes}m {secs}s"


def rewrite_print_time(text: str) -> str:
    """Replace the slicer time footer with the chained-toolpath estimate."""
    marker = "; estimated printing time (normal mode) ="
    if marker not in text:
        return text
    pretty = _format_duration(_estimate_seconds(text))
    out: list[str] = []
    replaced = False
    for line in text.splitlines(keepends=True):
        if not replaced and line.startswith(marker):
            newline = "\n" if line.endswith("\n") else ""
            out.append(
                "; print time is the chained gap-fill toolpath"
                " (cruise extrusion, accel-limited travel)."
                " The slicer counted travels between gap beads that already meet."
                + newline
            )
            out.append(f"{marker} {pretty}{newline}")
            replaced = True
            continue
        out.append(line)
    return "".join(out)


def main() -> None:
    material = sys.argv[1]
    work = Path(sys.argv[2])
    stl = Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    machine, process, filament, y_offset = prepare(work, material)
    for name, data in (("machine.json", machine), ("process.json", process), ("filament.json", filament)):
        (work / name).write_text(json.dumps(data, indent=2) + "\n")
    fil = filament
    print(
        "temp", fil.get("nozzle_temperature"),
        "fan", fil.get("fan_min_speed"), fil.get("fan_max_speed"),
        "aux", fil.get("additional_cooling_fan_speed"),
        "layer", process.get("layer_height"),
        "initial", process.get("initial_layer_print_height"),
        "walls", process.get("wall_loops"),
        "y", y_offset,
        "layer_retract", machine.get("retract_when_changing_layer"),
        "fil_retract_len", fil.get("filament_retraction_length"),
        "fil_layer_retract", fil.get("filament_retract_when_changing_layer"),
    )
    if os.environ.get("PREP_ONLY") == "1":
        return
    gcode = slice_model(work, stl, y_offset)
    dest = Path(sys.argv[4]) if len(sys.argv) > 4 else work / "sliced.gcode"
    text = rewrite_print_time(chain_gap_fill_travels(gcode.read_text(errors="replace")))
    dest.write_text(text)
    print("wrote", dest, dest.stat().st_size)


if __name__ == "__main__":
    main()
