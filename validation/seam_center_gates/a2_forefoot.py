#!/usr/bin/env python3
"""A2 forefoot. A1 on the orthocad ball band only: sfg_common layers 79-117 inclusive
(0-based ;LAYER_CHANGE count). On BASE those layers sit at G-code Z 52.69-77.41 mm,
the brief's ~53-78 mm from the toe tip. along_mm in the report is the 45-degree belt
arc (dZ * sqrt(2)) and is not what the gate selects. Zero off-center loops.
Faces < 3 mm stay exempt.

usage: a2_forefoot.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S
import sfg_common as C

S.pin_sfg()


def along_mm(row: dict, toe_z: float) -> float:
    """Belt arc from the toe tip, reported only. A2 selects layers, not this arc."""
    return (row["z"] - toe_z) * (2.0 ** 0.5)


def measure(path: str) -> dict:
    ev = C.parse(path)
    rows = S.closed_outer_rows(ev)
    toe_z = min((r["z"] for r in rows), default=0.0)
    band = []
    for row in rows:
        dist = along_mm(row, toe_z)
        row["along_mm"] = dist
        if S.in_forefoot(row, dist):
            band.append(row)
    off = []
    exempt = 0
    max_dx = 0.0
    worst = None
    alongs = []
    for row in band:
        alongs.append(row["along_mm"])
        if row["missing_face"]:
            off.append({"layer": row["layer"], "why": "no bottom face"})
            continue
        dx = abs(row["seam_x"] - row["center_x"])
        if dx > max_dx:
            max_dx = dx
            worst = row
        if row["narrow"]:
            exempt += 1
            continue
        if dx > S.CENTER_TOL:
            off.append({
                "layer": row["layer"], "seam_x": round(row["seam_x"], 4),
                "center_x": round(row["center_x"], 4), "dx_mm": round(dx, 4),
                "width_mm": round(row["width"], 4), "along_mm": round(row["along_mm"], 3),
                "z": round(row["z"], 4),
            })
    off.sort(key=lambda r: -r.get("dx_mm", 0))
    return {
        "loops": len(band), "exempt": exempt, "off": len(off), "max_dx_mm": max_dx,
        "along_min": min(alongs) if alongs else None, "along_max": max(alongs) if alongs else None,
        "layers": sorted({r["layer"] for r in band}),
        "worst": None if worst is None else {
            "layer": worst["layer"], "dx_mm": round(max_dx, 4),
            "seam_x": round(worst["seam_x"], 4),
            "center_x": round(worst["center_x"], 4),
            "along_mm": round(worst["along_mm"], 3),
        },
        "examples": off[:8], "mode": S.FOREFOOT_MODE,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("cand_dir")
    ap.add_argument("--json")
    args = ap.parse_args()
    name = S.CORE["orthocad"]
    cand = measure(S.gcode(args.cand_dir, name))
    base = measure(S.gcode(args.base_dir, name))
    fails = []
    if cand["loops"] == 0:
        fails.append("no closed outer loops in the forefoot band")
    if cand["off"]:
        fails.append("%d forefoot loops off center (max dx %.3f mm)" % (cand["off"], cand["max_dx_mm"]))
    if cand["exempt"] > base["exempt"]:
        fails.append("forefoot narrow exemptions %d > BASE %d" % (cand["exempt"], base["exempt"]))
    status = "FAIL" if fails else "PASS"
    parts = [
        ("mode", S.FOREFOOT_MODE), ("loops", cand["loops"]), ("off", cand["off"]),
        ("max_dx_mm", cand["max_dx_mm"]), ("exempt", cand["exempt"]),
        ("base_exempt", base["exempt"]),
        ("along_min", cand["along_min"] if cand["along_min"] is not None else -1),
        ("along_max", cand["along_max"] if cand["along_max"] is not None else -1),
    ]
    rep = {"gate": "A2 forefoot", "result": status, "failures": fails, "cand": cand,
           "base_exempt": base["exempt"], "center_tol_mm": S.CENTER_TOL,
           "forefoot_layers": [S.FOREFOOT_LO, S.FOREFOOT_HI],
           "forefoot_mm": [S.FOREFOOT_MM_LO, S.FOREFOOT_MM_HI]}
    S.emit(status, "A2", parts, rep, args.json)


if __name__ == "__main__":
    main()
