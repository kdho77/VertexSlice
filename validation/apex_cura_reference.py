#!/usr/bin/env python3
"""Metrics extracted from the known-good Cura Apex insole reference G-code."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_REF = REPO / "validation/reference_gcode/apex_cura_insole_ref.gcode"

# Nominal E/mm for 1.0 mm line, 0.42 mm layer, 1.75 mm filament, ~2.8% flow tweak.
E_PER_MM_NOMINAL = 0.1796
FILAMENT_CS = math.pi * (1.75 / 2.0) ** 2


@dataclass
class CuraRefMetrics:
    nozzle_c: float = 225.0
    belt_z_step_mm: float = 0.6
    true_layer_mm: float = 0.42
    line_width_mm: float = 1.0
    e_per_mm_nominal: float = E_PER_MM_NOMINAL
    outer_wall_mm_s: float = 35.0
    belt_wall_mm_s: float = 20.0
    infill_mm_s: float = 60.0
    skin_mm_s: float = 63.0
    travel_mm_s: float = 200.0
    retraction_mm: float = 1.5
    retraction_mm_s: float = 30.0
    part_fan_pwm: float = 127.5
    aux_fan_pwm: float = 64.0
    m204_values: tuple[int, ...] = (3000, 3500, 4000, 4500, 4750, 5000, 6500, 8249, 9999)
    wall_loops_owner: int = 1
    infill_direction_deg: float = 0.0
    infill_density_note: str = (
        "Kendon Soft 20% rectilinear (authoritative); Cura ref validated at this density."
    )
    infill_density_percent: float = 20.0


def _f_to_mm_s(f: int) -> float:
    return f / 60.0


def parse_reference(path: Path | None = None) -> tuple[CuraRefMetrics, dict]:
    """Parse reference file and return metrics plus raw measurement detail."""
    path = path or DEFAULT_REF
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    detail: dict = {}

    detail["m104"] = bool(re.search(r"M104 S225", "\n".join(lines[:30])))
    detail["m109"] = bool(re.search(r"M109 S225", "\n".join(lines[:30])))
    detail["m82"] = any("M82" in l for l in lines[:30])

    fans = [l.strip() for l in lines[:40] if l.strip().startswith("M106")]
    detail["fans_start"] = fans

    # Z layer step from ;LAYER: blocks
    layer_z: list[float] = []
    cur_z: float | None = None
    for l in lines:
        if l.startswith(";LAYER:"):
            if cur_z is not None:
                layer_z.append(cur_z)
            cur_z = None
        if "Z" in l and (l.startswith("G0") or l.startswith("G1")):
            m = re.search(r"Z([\d.]+)", l)
            if m:
                cur_z = float(m.group(1))
    deltas = [round(layer_z[i + 1] - layer_z[i], 3) for i in range(len(layer_z) - 1) if layer_z[i + 1] > 0]
    detail["z_deltas_sample"] = deltas[:10]
    if deltas:
        detail["belt_z_step_median"] = sorted(deltas)[len(deltas) // 2]

    role = None
    by_role: Counter[tuple[str, int]] = Counter()
    for l in lines:
        if ";TYPE:" in l:
            role = l.split(";TYPE:")[1].strip()
        s = l.strip()
        if not s.startswith("G1") or "E" not in s:
            continue
        m = re.search(r" F(\d+)", s)
        if m and role:
            by_role[(role, int(m.group(1)))] += 1

    def _top(role: str, n=3):
        items = [(c, f) for (r, f), c in by_role.items() if r == role]
        items.sort(reverse=True)
        return [(f, c, _f_to_mm_s(f)) for f, c in items[:n]]

    detail["outer_f"] = _top("WALL-OUTER")
    detail["fill_f"] = _top("FILL")
    detail["skin_f"] = _top("SKIN")

    g0 = Counter()
    for l in lines:
        if l.strip().startswith("G0"):
            m = re.search(r" F(\d+)", l)
            if m:
                g0[int(m.group(1))] += 1
    detail["travel_g0_f"] = g0.most_common(5)

    m204 = Counter()
    for l in lines:
        m = re.search(r"M204 S(\d+)", l)
        if m:
            m204[int(m.group(1))] += 1
    detail["m204_counts"] = dict(sorted(m204.items()))

    metrics = CuraRefMetrics()
    if detail.get("belt_z_step_median"):
        metrics.belt_z_step_mm = float(detail["belt_z_step_median"])
    return metrics, detail


def measure_gcode(path: Path) -> dict:
    """Measure the same features from an Orca-emitted G-code file."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: dict = {}

    header = "\n".join(lines[:80])
    out["temp_225"] = bool(re.search(r"M10[49] S225", header) or re.search(r"M10[49] S225", "\n".join(lines)))
    out["m82"] = any("M82" in l for l in lines[:80])
    out["part_fan"] = re.findall(r"M106 S([\d.]+)", "\n".join(lines[:120]))
    out["aux_fan"] = re.findall(r"M106 P1 S([\d.]+)", "\n".join(lines[:120]))
    out["retract_mm"] = []
    for l in lines[:500]:
        m = re.search(r"G1 F(\d+) E-([\d.]+)", l.strip())
        if m:
            out["retract_mm"].append((float(m.group(2)), int(m.group(1))))

    role = None
    speeds: dict[str, list[float]] = {}
    for l in lines:
        if ";TYPE:" in l or ";TYPE:" in l.upper():
            if ";TYPE:" in l:
                role = l.split(";TYPE:")[1].strip().upper()
        if "TYPE:EXTERNAL" in l:
            role = "WALL-OUTER"
        if "TYPE:INTERNAL" in l:
            role = "WALL-INNER"
        s = l.strip()
        if not s.startswith("G1") or "E" not in s:
            continue
        m = re.search(r" F(\d+)", s)
        if not m:
            continue
        f = int(m.group(1))
        mm_s = _f_to_mm_s(f)
        key = role or "UNKNOWN"
        if "OUTER" in key or key == "PERIMETER":
            speeds.setdefault("outer", []).append(mm_s)
        elif "INNER" in key:
            speeds.setdefault("inner", []).append(mm_s)
        elif "FILL" in key or "INFILL" in key:
            speeds.setdefault("infill", []).append(mm_s)
        elif "SKIN" in key or "SOLID" in key or "TOP" in key:
            speeds.setdefault("skin", []).append(mm_s)

    for k, vals in speeds.items():
        if vals:
            c = Counter(round(v, 1) for v in vals)
            out[f"{k}_mm_s_mode"] = c.most_common(3)

    g0 = []
    for l in lines:
        if l.strip().startswith("G0"):
            m = re.search(r" F(\d+)", l)
            if m:
                g0.append(_f_to_mm_s(int(m.group(1))))
    if g0:
        out["travel_mm_s_mode"] = Counter(round(v, 1) for v in g0).most_common(3)

    m204 = [int(m.group(1)) for l in lines for m in [re.search(r"M204 S(\d+)", l)] if m]
    out["m204_values"] = sorted(set(m204))

    # belt Z steps from ;LAYER_CHANGE or layer comments
    layer_z = []
    z = None
    for l in lines:
        if ";LAYER_CHANGE" in l or l.startswith(";LAYER:"):
            if z is not None:
                layer_z.append(z)
        if re.search(r"\bZ", l) and l.strip().startswith(("G0", "G1")):
            m = re.search(r"Z([\d.]+)", l)
            if m:
                z = float(m.group(1))
    dz = [round(layer_z[i + 1] - layer_z[i], 3) for i in range(len(layer_z) - 1) if layer_z[i + 1] > 0.01]
    out["z_deltas"] = dz[:10]
    if dz:
        out["belt_z_step"] = sorted(dz)[len(dz) // 2]

    cfg = "\n".join(lines[-4000:])
    out["layer_height_cfg"] = re.search(r"; layer_height = ([\d.]+)", cfg)
    out["outer_wall_speed_cfg"] = re.search(r"; outer_wall_speed = ([\d.]+)", cfg)
    out["sparse_infill_speed_cfg"] = re.search(r"; sparse_infill_speed = ([\d.]+)", cfg)

    return out


def compare_to_reference(sliced: dict, ref: CuraRefMetrics, tol_speed_pct: float = 0.12) -> list[str]:
    """Return list of failure messages; empty means within tolerance."""
    fails: list[str] = []

    if not sliced.get("temp_225"):
        fails.append("sliced gcode missing M104/M109 S225")

    part = sliced.get("part_fan", [])
    if not any(abs(float(x) - ref.part_fan_pwm) < 2 for x in part[:5]):
        fails.append(f"part fan PWM expected ~{ref.part_fan_pwm}, got {part[:5]}")

    aux = sliced.get("aux_fan", [])
    if not any(abs(float(x) - ref.aux_fan_pwm) < 2 for x in aux[:5]):
        fails.append(f"aux fan P1 PWM expected ~{ref.aux_fan_pwm}, got {aux[:5]}")

    retr = sliced.get("retract_mm", [])
    if not any(abs(r[0] - ref.retraction_mm) < 0.15 for r in retr[:10]):
        fails.append(f"retraction expected ~{ref.retraction_mm} mm, got {retr[:5]}")
    if retr:
        mm_s = _f_to_mm_s(retr[0][1])
        if abs(mm_s - ref.retraction_mm_s) > 5:
            fails.append(f"retraction speed expected ~{ref.retraction_mm_s} mm/s, got {mm_s:.1f}")

    def _check_mode(key: str, target: float):
        modes = sliced.get(f"{key}_mm_s_mode") or []
        if not modes:
            return
        best = modes[0][0]
        if abs(best - target) / target > tol_speed_pct:
            fails.append(f"{key} speed mode {best} mm/s vs ref {target} mm/s")

    _check_mode("outer", ref.outer_wall_mm_s)
    _check_mode("infill", ref.infill_mm_s)
    _check_mode("skin", ref.skin_mm_s)
    travel_modes = sliced.get("travel_mm_s_mode") or []
    if travel_modes:
        best = travel_modes[0][0]
        if abs(best - ref.travel_mm_s) / ref.travel_mm_s > tol_speed_pct:
            fails.append(f"travel speed mode {best} mm/s vs ref {ref.travel_mm_s}")

    z_step = sliced.get("belt_z_step")
    if z_step is not None and abs(z_step - ref.belt_z_step_mm) > 0.1:
        fails.append(f"belt Z step {z_step} vs ref {ref.belt_z_step_mm}")

    m204s = sliced.get("m204_values") or []
    if m204s and (min(m204s) < 2500 or max(m204s) > 12000):
        fails.append(f"M204 accel outside Cura-like range: {m204s[:8]}")

    return fails
