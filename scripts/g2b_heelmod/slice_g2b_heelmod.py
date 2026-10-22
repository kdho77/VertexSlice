#!/usr/bin/env python3
"""Slice the G2b heelmod insole on the def8eff480 OrcaBelt binary.

The process/filament/machine JSON is the CONFIG_BLOCK recovered from
ApexLight_round_2p5mm_G2b_toe.gcode, flattened onto the IdeaFormer presets
shipped in that binary. The only global edits are infill_wall_overlap 25%
and minimum_sparse_infill_area 0. Heel settings (layer ordinal >= 293) are
a BBS layer-config range, which this binary actually loads
(Metadata/layer_config_ranges.xml). The Prusa-named range file is ignored.

ApexCore uses the same heel structure on the ApexCore filament and the
0.42 mm medium process: outer/inner/sparse about 0.18 E/mm, its own gap-fill
ratio, +10% in the heel range, 225 C and fan 50/25 left as the filament
preset has them. Pressure advance stays ADVANCE=0.05.
The same physical heel plane (min_z 191.30) is layer ordinal 293 on
ApexLight's 0.46 mm spacing and ordinal 320 on ApexCore's 0.42 mm spacing.
ApexCore's default min_bead_width is already 85%, so a second global bead
slice does not change its geometry.
"""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = ["/tmp/g2b/src/validation", "/tmp/g2b/src"]

ORCA = os.environ.get("ORCA_SLICER_BIN", "/tmp/g2b/root/app/bin/orca-slicer")
RES = os.environ.get("ORCA_RESOURCES", "/tmp/g2b/root/app/resources")
PROFILES = Path(RES) / "profiles/IdeaFormer"
CFG = json.loads((HERE / "g2b_config.json").read_text())

# 0-based layer ordinal. Layers 0..292 stay on the G2b profile (plus the two
# global keys). Ordinal 293 is the first heel layer (print Z about 191.9).
HEEL_LAYER = 293
# slice_z is the layer midpoint. 191.30 sits between layer 292 (~190.93) and
# layer 293 (~191.58) on the G2b 0.46 mm belt spacing.
HEEL_MIN_Z = float(os.environ.get("HEEL_MIN_Z", "191.30"))
FILAMENT_DIAMETER = 1.75
Y_MACH_OFFSET_LIGHT = -2.136


def unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


def coerce(template, raw: str):
    val = unescape(raw)
    if isinstance(template, list):
        return [val]
    if isinstance(template, bool):
        return val in ("1", "true", "True")
    return val


def flatten(name: str, work: Path):
    from orca_profile_flatten import flatten_profile

    return json.loads(flatten_profile(PROFILES / name, work / (Path(name).name + ".flat.json")).read_text())


def overlay_g2b(machine: dict, process: dict, filament: dict) -> None:
    for dest in (machine, process, filament):
        for key, raw in CFG.items():
            if key in dest:
                dest[key] = coerce(dest[key], raw)
    claimed = set(machine) | set(process) | set(filament)
    for key, raw in CFG.items():
        if key not in claimed:
            process[key] = unescape(raw)
    machine["machine_start_gcode"] = unescape(CFG["machine_start_gcode"])


def assert_g2b_identity(process: dict, filament: dict, machine: dict) -> None:
    """Refuse the e2b2 / PR #7 profile drift called out in review."""
    checks = {
        "filter_out_gap_fill": "9999",
        "detect_narrow_internal_solid_infill": "1",
        "ensure_vertical_shell_thickness": "none",
        "top_shell_layers": "0",
        "reduce_crossing_wall": "0",
        "resolution": "0.01",
        "wipe": "1",
        "outer_wall_flow_ratio": "1.5466",
        "inner_wall_flow_ratio": "1.4154",
        "sparse_infill_flow_ratio": "1.6509",
        "internal_solid_infill_flow_ratio": "1.45",
        "internal_solid_infill_pattern": "monotonic",
        "initial_layer_min_bead_width": "85%",
        "min_bead_width": "50%",
        "print_direction": "toe_first",
        "outer_wall_speed": "26",
    }
    def scalar(v) -> str:
        if isinstance(v, list):
            return "" if not v else str(v[0])
        return str(v)

    for key, expect in checks.items():
        got = scalar(process.get(key, filament.get(key, machine.get(key))))
        if got != expect:
            raise SystemExit(f"G2b identity failed: {key}={got!r} expected {expect!r}")
    start = machine.get("machine_start_gcode", "")
    if "SET_PRESSURE_ADVANCE ADVANCE=0.05" not in start:
        raise SystemExit("machine_start_gcode lost SET_PRESSURE_ADVANCE ADVANCE=0.05")


def feature_flow(e_per_mm: float, layer_h: float, width: float, filament_flow: float) -> float:
    area = math.pi * (FILAMENT_DIAMETER / 2) ** 2
    return e_per_mm * area / (filament_flow * width * layer_h)


def stl_triangles(stl: Path):
    data = stl.read_bytes()
    n = struct.unpack_from("<I", data, 80)[0]
    verts = []
    tris = []
    index = {}

    def vid(x, y, z):
        key = (round(x, 5), round(y, 5), round(z, 5))
        i = index.get(key)
        if i is None:
            i = len(verts)
            index[key] = i
            verts.append(key)
        return i

    off = 84
    for _ in range(n):
        vals = struct.unpack_from("<12f", data, off)
        off += 50
        tris.append((vid(*vals[3:6]), vid(*vals[6:9]), vid(*vals[9:12])))
    return verts, tris


def write_3mf(path: Path, stl: Path, options: dict[str, str], min_z: float, layer_height: str) -> None:
    verts, tris = stl_triangles(stl)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        '<resources><object id="1" type="model"><mesh><vertices>',
    ]
    for x, y, z in verts:
        parts.append(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>')
    parts.append("</vertices><triangles>")
    for a, b, c in tris:
        parts.append(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>')
    parts.append("</triangles></mesh></object></resources>")
    parts.append('<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></build></model>')
    # layer_height is repeated at the belt-scaled spacing so the generated
    # height profile stays uniform. This binary reads the value as virtual-Z
    # spacing and does not scale a range override a second time.
    opts = dict(options)
    opts.setdefault("layer_height", layer_height)
    body = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<objects>", ' <object id="1">', f'  <range min_z="{min_z:.4f}" max_z="10000">']
    for key, val in opts.items():
        body.append(f'   <option opt_key="{key}">{val}</option>')
    body += ["  </range>", " </object>", "</objects>", ""]
    ct = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", "".join(parts))
        zf.writestr("Metadata/layer_config_ranges.xml", "\n".join(body))


def slice_model(work: Path, model: Path, y_offset: float) -> Path:
    ini = work / "belt_transform.ini"
    ini.write_text(f"y_mach_offset = {y_offset:.6f}\n")
    env = os.environ.copy()
    env["SLIC3R_RESOURCES_DIR"] = RES
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    env["ORCABELT_TRANSFORM_INI"] = str(ini)
    for old in work.glob("*.gcode"):
        old.unlink()
    cmd = [
        ORCA, "--slice", "1", "--allow-newer-file", "--arrange", "0",
        "--load-settings", f"{work / 'machine.json'};{work / 'process.json'}",
        "--load-filaments", str(work / "filament.json"),
        "--outputdir", str(work), str(model),
    ]
    print("slicing", model.name, "y", y_offset, flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, errors="replace", timeout=2400)
    log = work / "slice.log"
    log.write_text(proc.stdout + "\n" + proc.stderr)
    print("exit", proc.returncode, flush=True)
    if proc.returncode != 0:
        print((proc.stdout + proc.stderr)[-2000:])
        raise SystemExit("slice failed")
    found = list(work.glob("*.gcode"))
    if not found:
        print((proc.stdout + proc.stderr)[-2000:])
        raise SystemExit("no gcode")
    return found[0]


def prepare_light(work: Path, min_bead_global: str | None) -> tuple[dict, dict, dict]:
    machine = flatten("machine/Vertex Apex V2 1.0 nozzle.json", work)
    process = flatten("process/0.42mm TPU Insole Medium @Vertex Apex V2.json", work)
    filament = flatten("filament/ApexLight.json", work)
    overlay_g2b(machine, process, filament)
    assert_g2b_identity(process, filament, machine)
    process["infill_wall_overlap"] = "25%"
    process["minimum_sparse_infill_area"] = "0"
    if min_bead_global:
        process["min_bead_width"] = min_bead_global
    return machine, process, filament


def prepare_core(work: Path) -> tuple[dict, dict, dict, dict[str, str]]:
    """ApexCore medium profile. Flows target ~0.18 E/mm; gap fill is its own ratio."""
    machine = flatten("machine/Vertex Apex V2 1.0 nozzle.json", work)
    process = flatten("process/0.42mm TPU Insole Medium @Vertex Apex V2.json", work)
    filament = flatten("filament/ApexCore.json", work)
    # Keep the belt start block, including pressure advance, from the G2b machine.
    # Absolute E on this binary ignores flow ratios (E becomes path length).
    # G2b is relative, and the post-process assumes relative E.
    machine["machine_start_gcode"] = unescape(CFG["machine_start_gcode"])
    machine["use_relative_e_distances"] = "1"
    # Relative E is rejected unless each layer resets E. G2b does that here.
    machine["before_layer_change_gcode"] = unescape(CFG["before_layer_change_gcode"])
    # Heel flow ratios are ignored unless per-feature flow is enabled. The
    # medium preset leaves this off; turning it on does not change the toe
    # ratios written below.
    process["set_other_flow_ratios"] = "1"
    process["print_direction"] = "toe_first"
    process["infill_wall_overlap"] = "25%"
    process["minimum_sparse_infill_area"] = "0"
    process["wall_loops"] = "1"
    # Filament preset already carries 225 C and fan 50/25. Do not copy ApexLight.
    fil = float(filament.get("filament_flow_ratio", ["1.03"])[0])
    lh = float(process.get("layer_height", "0.42"))
    width = 1.0
    # Volumetric ratio for 0.18 E/mm at a 1 mm bead. On this binary the
    # shear-corrected gcode does not land on that number: at ratio 1.0008 the
    # length-weighted rates were outer 0.1843, sparse 0.1638 (width 1.0),
    # solid 0.1778. Scale each feature so the measured rate is 0.18. Gap fill
    # uses the same width-1 correction as sparse, aimed at 0.16 rather than
    # ApexLight's 0.192.
    volumetric = feature_flow(0.18, lh, width, fil)
    outer = volumetric * (0.18 / 0.1843)
    sparse = volumetric * (0.18 / 0.1638)
    solid = volumetric * (0.18 / 0.1778)
    gap = sparse * (0.16 / 0.18)
    for key, val in (
        ("outer_wall_flow_ratio", outer),
        ("inner_wall_flow_ratio", outer),
        ("sparse_infill_flow_ratio", sparse),
        ("internal_solid_infill_flow_ratio", solid),
        ("gap_fill_flow_ratio", gap),
    ):
        process[key] = f"{val:.4f}"
    heel = {
        "outer_wall_flow_ratio": f"{outer * 1.10:.4f}",
        "inner_wall_flow_ratio": f"{outer * 1.10:.4f}",
        "sparse_infill_flow_ratio": f"{sparse * 1.10:.4f}",
        "internal_solid_infill_flow_ratio": f"{solid * 1.10:.4f}",
        "gap_fill_flow_ratio": f"{gap * 1.10:.4f}",
        "min_bead_width": "85%",
        "ensure_vertical_shell_thickness": "ensure_all",
    }
    print("apexcore filament_flow", fil, "layer", lh, "outer_ratio", f"{outer:.4f}", "gap_ratio", f"{gap:.4f}")
    if "SET_PRESSURE_ADVANCE ADVANCE=0.05" not in machine["machine_start_gcode"]:
        raise SystemExit("ApexCore start gcode lost pressure advance")
    return machine, process, filament, heel


def main() -> None:
    material = sys.argv[1]  # light | core
    work = Path(sys.argv[2])
    stl = Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    min_bead = os.environ.get("MIN_BEAD_GLOBAL")
    if material == "light":
        machine, process, filament = prepare_light(work, min_bead)
        heel = {
            "outer_wall_flow_ratio": "1.70",
            "inner_wall_flow_ratio": "1.56",
            "min_bead_width": "85%",
            "ensure_vertical_shell_thickness": "ensure_all",
        }
        y_offset = Y_MACH_OFFSET_LIGHT
        lh = 0.46 * math.sqrt(2)
        min_z = HEEL_MIN_Z
    elif material == "core":
        machine, process, filament, heel = prepare_core(work)
        if min_bead:
            process["min_bead_width"] = min_bead
        # Same STL and belt math as ApexLight. ApexCore's first-layer target is
        # 0.05 mm higher (0.066 vs 0.016), so the offset shifts by that amount.
        # Probed on this mesh at h=0.42: offset 20 -> first extrusion Y 21.066.
        # target 0.066 gives y_mach_offset = -1.0. Light's -2.136 clamps Core to Y=0.
        y_offset = float(os.environ.get("CORE_Y_OFFSET", "-1.0"))
        lh = float(process.get("layer_height", "0.42")) * math.sqrt(2)
        # Same physical heel plane as ApexLight. Virtual Z is the model, not the
        # layer spacing, so the cutoff stays at the Light layer-293 plane.
        min_z = HEEL_MIN_Z
    else:
        raise SystemExit("material must be light or core")
    for name, data in (("machine.json", machine), ("process.json", process), ("filament.json", filament)):
        (work / name).write_text(json.dumps(data, indent=2))
    (work / "heel_range.json").write_text(json.dumps({"min_z": min_z, "options": heel}, indent=2))
    mf = work / "model.3mf"
    print("building 3mf", flush=True)
    write_3mf(mf, stl, heel, min_z, f"{lh:.8f}")
    print("3mf", mf.stat().st_size, "min_z", round(min_z, 4), "lh_virt", round(lh, 6), flush=True)
    gcode = slice_model(work, mf, y_offset)
    dest = Path(sys.argv[4]) if len(sys.argv) > 4 else work / "sliced.gcode"
    dest.write_bytes(gcode.read_bytes())
    print("wrote", dest, dest.stat().st_size, flush=True)


if __name__ == "__main__":
    main()
