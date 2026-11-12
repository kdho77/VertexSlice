"""Measure sparse-infill density inside modifier volumes from finished G-code.

Belt G-code is machine space. With the locked transform, a point maps back to the
placed model frame (the frame ``prepare_belt_3mf`` writes) as::

    model X = gcode X
    model Z = (gcode Y - y_mach_offset) / √2
    model Y = gcode Z - model Z

``y_mach_offset`` is the locked chain value: -1.146 ApexCore, -1.151 ApexLight.
Gyroid line spacing scales as 1/density, so a region's density is the global
density times (base spacing / region spacing). Path length per footprint area
is the fallback when a region is too small for a spacing sample. A region is
verified only when that estimate lands on the requested preset.
"""
from __future__ import annotations

import math
import statistics
import tempfile
from pathlib import Path

_SQRT2 = math.sqrt(2.0)
# Locked in slice_apexcore_g2b.CORE_Y_OFFSET and apex_light VS_FORCE_OFFSET.
CORE_Y_MACH_OFFSET_MM = -1.146
LIGHT_Y_MACH_OFFSET_MM = -1.151
_MATCH_ABS_PCT = 3.5
_MIN_INSIDE = 8


def y_mach_offset_mm(material: str | None, fallback: float | None = None) -> float:
    if material == "apex_core":
        return CORE_Y_MACH_OFFSET_MM
    if material == "apex_light":
        return LIGHT_Y_MACH_OFFSET_MM
    if fallback is not None:
        return float(fallback)
    return 0.0


def model_to_gcode(mx: float, my: float, mz: float, y_off: float) -> tuple[float, float, float]:
    """Inverse of the belt map, for tests that build G-code in model space."""
    return mx, mz * _SQRT2 + y_off, my + mz


def gcode_to_model(gx: float, gy: float, gz: float, y_off: float) -> tuple[float, float, float]:
    mz = (gy - y_off) / _SQRT2
    return gx, gz - mz, mz


def parse_sparse_segments(gcode: str, y_off: float) -> list[dict]:
    """Sparse-infill extrusions in the placed model frame."""
    feature = ""
    x = y = z = e_abs = None
    absolute_xyz = True
    absolute_e = False
    segments: list[dict] = []
    for raw in gcode.splitlines():
        if raw.startswith(";TYPE:"):
            feature = raw.split(":", 1)[1].strip()
            continue
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        head = line.split()[0]
        if head == "G90":
            absolute_xyz = True
            continue
        if head == "G91":
            absolute_xyz = False
            continue
        if head == "M82":
            absolute_e = True
            continue
        if head == "M83":
            absolute_e = False
            continue
        if head not in ("G0", "G1", "G2", "G3"):
            continue
        words: dict[str, float] = {}
        for part in line.split()[1:]:
            if len(part) > 1 and part[0] in "XYZEF":
                try:
                    words[part[0]] = float(part[1:])
                except ValueError:
                    pass
        px, py = x, y
        if absolute_xyz:
            x = words.get("X", x)
            y = words.get("Y", y)
            z = words.get("Z", z)
        else:
            if "X" in words and x is not None:
                x += words["X"]
            if "Y" in words and y is not None:
                y += words["Y"]
            if "Z" in words and z is not None:
                z += words["Z"]
        extruded = None
        if "E" in words:
            if absolute_e:
                if e_abs is None:
                    e_abs = words["E"]
                else:
                    extruded = words["E"] - e_abs
                    e_abs = words["E"]
            else:
                extruded = words["E"]
        if (
            feature != "Sparse infill"
            or extruded is None
            or extruded <= 1e-6
            or None in (px, py, x, y, z)
        ):
            continue
        mx0, my0, mz0 = gcode_to_model(px, py, z, y_off)
        mx1, my1, mz1 = gcode_to_model(x, y, z, y_off)
        length = math.hypot(mx1 - mx0, mz1 - mz0)
        if length <= 0.15:
            continue
        segments.append(
            {
                "mx0": mx0,
                "my0": my0,
                "mz0": mz0,
                "mx1": mx1,
                "my1": my1,
                "mz1": mz1,
                "mx": (mx0 + mx1) / 2,
                "my": (my0 + my1) / 2,
                "mz": (mz0 + mz1) / 2,
                "gz": z,
                "length": length,
                "e": extruded,
            }
        )
    return segments


def _inside(pt: tuple[float, float, float], box: dict, inset: float) -> bool:
    x, y, z = pt
    return (
        box["xmin"] + inset <= x <= box["xmax"] - inset
        and box["ymin"] + inset <= y <= box["ymax"] - inset
        and box["zmin"] + inset <= z <= box["zmax"] - inset
    )


def _box_volume(box: dict) -> float:
    return max(0.0, box["xmax"] - box["xmin"]) * max(0.0, box["ymax"] - box["ymin"]) * max(0.0, box["zmax"] - box["zmin"])


def _intersection_volume(a: dict, b: dict) -> float:
    dx = min(a["xmax"], b["xmax"]) - max(a["xmin"], b["xmin"])
    dy = min(a["ymax"], b["ymax"]) - max(a["ymin"], b["ymin"])
    dz = min(a["zmax"], b["zmax"]) - max(a["zmin"], b["zmin"])
    if dx <= 0 or dy <= 0 or dz <= 0:
        return 0.0
    return dx * dy * dz


def _cloud_area(segments: list[dict]) -> float:
    """XY is model X; the other in-plane axis is model Z (belt cross-section)."""
    if not segments:
        return 0.0
    xs = [seg["mx"] for seg in segments]
    zs = [v for seg in segments for v in (seg["mz0"], seg["mz1"])]
    return max(0.0, max(xs) - min(xs)) * max(0.0, max(zs) - min(zs))


def _polylines(segments: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    current: list[dict] = []
    for seg in segments:
        if current:
            prev = current[-1]
            if math.hypot(seg["mx0"] - prev["mx1"], seg["mz0"] - prev["mz1"]) > 0.05:
                lines.append(current)
                current = []
        current.append(seg)
    if current:
        lines.append(current)
    return lines


def _layer_spacings(segments: list[dict]) -> list[float]:
    by_layer: dict[float, list[dict]] = {}
    for seg in segments:
        by_layer.setdefault(round(seg["gz"], 2), []).append(seg)
    samples: list[float] = []
    for layer in by_layer.values():
        clouds = []
        for line in _polylines(layer):
            if sum(seg["length"] for seg in line) < 0.8:
                continue
            pts = [(seg["mx"], seg["mz"]) for seg in line]
            step = max(1, len(pts) // 6)
            clouds.append(pts[::step])
        if len(clouds) < 2:
            continue
        for i, cloud in enumerate(clouds):
            for x, z in cloud:
                best = None
                for j, other in enumerate(clouds):
                    if i == j:
                        continue
                    for ox, oz in other:
                        dist = math.hypot(x - ox, z - oz)
                        if dist < 0.45:
                            continue
                        if best is None or dist < best:
                            best = dist
                if best is not None and best < 20:
                    samples.append(best)
    return samples


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def analyze_regions(
    gcode: str,
    *,
    regions: list[dict],
    part_bounds: dict,
    global_density_pct: float,
    y_off: float,
) -> dict:
    """Compare each modifier's sparse infill with the surrounding base."""
    segments = parse_sparse_segments(gcode, y_off)
    inset = 1.5
    base_box = part_bounds
    base_segments = []
    for seg in segments:
        pt = (seg["mx"], seg["my"], seg["mz"])
        if not _inside(pt, base_box, 2.0):
            continue
        if any(_inside(pt, region, -1.0) for region in regions):
            continue
        base_segments.append(seg)
    base_spacing = _median(_layer_spacings(base_segments))
    base_length = sum(seg["length"] for seg in base_segments)
    base_area = _cloud_area(base_segments)
    base_density = (base_length / base_area) if base_area else 0.0
    reports = []
    for region in regions:
        inside = [seg for seg in segments if _inside((seg["mx"], seg["my"], seg["mz"]), region, inset)]
        if len(inside) < _MIN_INSIDE:
            inside = [seg for seg in segments if _inside((seg["mx"], seg["my"], seg["mz"]), region, 0.0)]
        spacing_samples = _layer_spacings(inside)
        spacing = _median(spacing_samples)
        length = sum(seg["length"] for seg in inside)
        area = _cloud_area(inside)
        length_density = (length / area) if area else 0.0
        spacing_pct = None
        if spacing and base_spacing and base_spacing > 0 and len(spacing_samples) >= 8:
            spacing_pct = global_density_pct * (base_spacing / spacing)
        length_pct = None
        if length_density > 0 and base_density > 0:
            length_pct = global_density_pct * (length_density / base_density)
        measured = spacing_pct if spacing_pct is not None else length_pct
        method = "spacing" if spacing_pct is not None else ("length" if length_pct is not None else "none")
        overlap = _intersection_volume(region, part_bounds)
        volume = _box_volume(region)
        layers = sorted({round(seg["gz"], 2) for seg in inside})
        z_vals = [v for seg in inside for v in (seg["mz0"], seg["mz1"])]
        verified, reason = _verdict(
            measured,
            float(region["density"]),
            global_density_pct,
            len(inside),
        )
        reports.append(
            {
                "id": region.get("id"),
                "requested_pct": region["density"],
                "measured_pct": None if measured is None else round(measured, 2),
                "method": method,
                "spacing_mm": None if spacing is None else round(spacing, 3),
                "base_spacing_mm": None if base_spacing is None else round(base_spacing, 3),
                "length_mm": round(length, 2),
                "base_length_mm": round(base_length, 2),
                "length_pct": None if length_pct is None else round(length_pct, 2),
                "overlap_mm3": round(overlap, 1),
                "overlap_fraction": round(overlap / volume, 3) if volume else 0.0,
                "layers": len(layers),
                "z_min_mm": None if not z_vals else round(min(z_vals), 2),
                "z_max_mm": None if not z_vals else round(max(z_vals), 2),
                "segments": len(inside),
                "verified": verified,
                "reason": reason,
            }
        )
    verified_n = sum(1 for item in reports if item["verified"])
    return {
        "prepared": len(regions),
        "verified": verified_n,
        "global_density_pct": global_density_pct,
        "base_segments": len(base_segments),
        "regions": reports,
    }


def _verdict(measured: float | None, requested: float, global_pct: float, inside_count: int) -> tuple[bool, str]:
    if inside_count < _MIN_INSIDE:
        return False, f"only {inside_count} sparse-infill segments inside the region"
    if measured is None:
        return False, "no base sparse infill to compare against"
    tol = max(_MATCH_ABS_PCT, 0.15 * requested)
    err = abs(measured - requested)
    gap = abs(requested - global_pct)
    if err > tol:
        return False, f"measured {measured:.1f}% vs requested {requested:.0f}% (limit {tol:.1f} points)"
    if gap >= 2 and abs(measured - global_pct) < 0.45 * gap:
        return False, f"measured {measured:.1f}% stayed near the global {global_pct:.0f}% instead of {requested:.0f}%"
    return True, "measured density matches the requested preset"


def format_region_failure(report: dict) -> str:
    lines = [
        f"{report['verified']} of {report['prepared']} modifier regions changed the sparse infill to the requested preset.",
    ]
    for region in report["regions"]:
        if region["verified"]:
            continue
        depth = ""
        if region["z_min_mm"] is not None:
            depth = f", model Z {region['z_min_mm']}–{region['z_max_mm']} mm across {region['layers']} layers"
        lines.append(
            f"region {region['id']}: requested {region['requested_pct']}% "
            f"measured {region['measured_pct']}% ({region['reason']}); "
            f"overlap {region['overlap_fraction']:.0%} of the modifier box{depth}"
        )
    lines.append("Refusing to return G-code that does not show those modifiers.")
    return " ".join(lines)


def verify_sliced_regions(
    model_path: Path,
    gcode: str,
    *,
    material: str | None,
    global_density_pct: float,
    print_direction: str = "toe_first",
    belt_insole_place: bool = True,
    y_offset_fallback: float | None = None,
) -> dict:
    """Place the 3MF the same way the slice did, then measure each modifier."""
    from server.threemf_modifiers import prepare_belt_3mf

    direction = "toe_first" if material == "apex_core" else print_direction
    with tempfile.TemporaryDirectory(prefix="region_infill_") as tmp:
        _dest, meta = prepare_belt_3mf(
            Path(model_path),
            dest_dir=Path(tmp),
            print_direction=direction,
            place=belt_insole_place,
        )
    regions = list(meta.get("placed_modifiers") or [])
    if not regions:
        return {
            "prepared": 0,
            "verified": 0,
            "global_density_pct": global_density_pct,
            "base_segments": 0,
            "regions": [],
        }
    return analyze_regions(
        gcode,
        regions=regions,
        part_bounds=meta["placed_main_bounds"],
        global_density_pct=global_density_pct,
        y_off=y_mach_offset_mm(material, y_offset_fallback),
    )
