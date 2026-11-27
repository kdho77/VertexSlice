"""scg_common.py — shared measurements for the frozen seam-center gates.

Imports validation/seam_fuse_gates/sfg_common.py (read-only). The bottom face is G1's
bottom_edge: the contiguous X extent of the loop path within 0.25 mm of that loop's min Y.
Thresholds are the ones in the grader brief and are not tuned to a candidate.
"""
from __future__ import annotations

import hashlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SFG = os.path.normpath(os.path.join(_HERE, "..", "seam_fuse_gates"))
if _SFG not in sys.path:
    sys.path.insert(0, _SFG)

import sfg_common as C  # noqa: E402

# Pinned so a silent edit of the imported fuse helpers fails the grader closed.
SFG_SHA256 = {
    "sfg_common.py": "366374e1052ec0f2e37d533f017a4d223717fb16de4e79b02ef0efa4b5bc1a21",
    "ctc_frozen.py": "5641f7ec52424040ca11a14090462ab2448b1fc8f3b51695530a18b22d1faf19",
    "g1_seam_bottom.py": "e80fe0064d885725ec14d39b905b56d8968dc7ef527e3fe8c04f84a401ed5b35",
}

CENTER_TOL = 0.10          # mm, seam X vs that layer's bottom-face center X
NARROW_MM = 3.0            # faces narrower than this keep today's fallback and are exempt
STEP_TOL = 0.10            # mm, A3: |seam X step| <= |face-center X step| + STEP_TOL
LC_X_TOL = 0.6             # mm, layer-change end X vs that layer's seam X
LC_BOTTOM_TOL = 0.55       # mm, G1's layer-change on-bottom tolerance (0.15 + 0.4 lift)
SAMPLE_X_TOL = 0.01        # mm, insole_sample seam X vs BASE
PATH_TOL = 0.005           # mm, vertex lies on the other file's wall path
EPM_REL = 0.005            # 0.5 % of the move's E
# Collinear splits are emitted at 0.001 mm XY and 5-decimal E. On a short piece that
# quantization moves E by about E/mm * 0.001 mm (~0.0002). It is not a flow change.
EPM_ABS = 2e-4
F_TOL = 0.5                # mm/min
WIPE_LEN_TOL = 0.02        # mm
TIME_FRAC = 0.005          # candidate <= BASE * (1 + TIME_FRAC)
FILAMENT_FRAC = 0.005
# Forefoot band on orthocad. Locked to the brief's layer index 79-117.
# On BASE those layers have G-code Z 52.69-77.41 mm, the ~53-78 mm ball
# (belt Z, the height in the file). FOREFOOT_MODE stays "layer".
FOREFOOT_MODE = "layer"
FOREFOOT_LO = 79
FOREFOOT_HI = 117          # inclusive
FOREFOOT_MM_LO = 53.0
FOREFOOT_MM_HI = 78.0

CORE = {
    "orthocad": "apex_core_orthocad.gcode",
    "insole2": "apex_core_insole2.gcode",
}
LIGHT = {
    "orthocad": "apex_light_orthocad.gcode",
    "insole2": "apex_light_insole2.gcode",
}
FANTEMP = {106, 107, 104, 109, 140, 190}


def pin_sfg() -> None:
    for name, want in SFG_SHA256.items():
        path = os.path.join(_SFG, name)
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if got != want:
            raise SystemExit("seam_fuse_gates/%s sha256 %s != pinned %s" % (name, got, want))


def sfg_dir() -> str:
    return _SFG


def gcode(directory: str, name: str) -> str:
    path = os.path.join(directory, name)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise SystemExit("missing gcode: %s" % path)
    return path


def face_of(chain: dict) -> dict | None:
    """X midpoint of G1's bottom_edge extent, and the width of that extent."""
    be = C.bottom_edge(chain)
    if be is None:
        return None
    xa, xb = be
    width = xb - xa
    return {"xa": xa, "xb": xb, "center_x": 0.5 * (xa + xb), "width": width, "narrow": width < NARROW_MM}


def _closed_flag(segs, pts, length) -> bool:
    if len(segs) < 3 or length <= 0:
        return False
    sx, sy = pts[0]
    acc = 0.0
    for s in segs:
        if acc >= 0.5 * length:
            dist, _t = C.seg_point_dist(sx, sy, s["x0"], s["y0"], s["x1"], s["y1"])
            if dist <= C.CLOSE_TOL:
                return True
        acc += C.seglen(s)
    s = segs[-1]
    return ((s["x1"] - sx) ** 2 + (s["y1"] - sy) ** 2) ** 0.5 <= C.CLOSE_TOL


def typed_chains(ev, types: set[str]) -> list[dict]:
    """Same chain rules as sfg_common.perimeter_chains, for an explicit type set."""
    chains = []
    cur = None
    for i, d in enumerate(ev):
        k = d["kind"]
        if k == "ext" and d["type"] in types:
            if cur is None:
                cur = {"layer": d["layer"], "segs": []}
            cur["segs"].append(i)
            continue
        if k in ("travel", "retract", "layer") or k == "ext" or (k == "type" and d["type"] not in types):
            if cur is not None:
                chains.append(cur)
                cur = None
    if cur is not None:
        chains.append(cur)
    out = []
    for c in chains:
        segs = [ev[i] for i in c["segs"]]
        pts = [(segs[0]["x0"], segs[0]["y0"])] + [(s["x1"], s["y1"]) for s in segs]
        length = sum(C.seglen(s) for s in segs)
        miny = min(p[1] for p in pts)
        out.append({
            "layer": c["layer"], "seg_idx": c["segs"], "segs": segs, "pts": pts, "length": length,
            "closed": _closed_flag(segs, pts, length), "start": pts[0], "end": pts[-1], "miny": miny,
            "types": sorted({s["type"] for s in segs}), "z": segs[0]["z1"],
        })
    return out


def outer_chains(ev) -> list[dict]:
    return C.perimeter_chains(ev)


def inner_chains(ev) -> list[dict]:
    return typed_chains(ev, {"Inner wall"})


def closed_outer_rows(ev) -> list[dict]:
    rows = []
    for ch in outer_chains(ev):
        if not ch["closed"]:
            continue
        face = face_of(ch)
        rows.append({
            "layer": ch["layer"],
            "seam_x": ch["start"][0],
            "seam_y": ch["start"][1],
            "end_x": ch["end"][0],
            "end_y": ch["end"][1],
            "z": ch["z"],
            "miny": ch["miny"],
            "length": ch["length"],
            "center_x": None if face is None else face["center_x"],
            "width": None if face is None else face["width"],
            "narrow": False if face is None else face["narrow"],
            "missing_face": face is None,
            "chain": ch,
        })
    return rows


def primary_by_layer(rows: list[dict]) -> dict[int, dict]:
    """Longest closed outer loop on each layer (the insole outline)."""
    best: dict[int, dict] = {}
    for row in rows:
        lay = row["layer"]
        if lay not in best or row["length"] > best[lay]["length"]:
            best[lay] = row
    return best


def in_forefoot(row: dict, along_mm: float | None = None) -> bool:
    if FOREFOOT_MODE == "along":
        if along_mm is None:
            return False
        return FOREFOOT_MM_LO - 1e-9 <= along_mm <= FOREFOOT_MM_HI + 1e-9
    return FOREFOOT_LO <= row["layer"] <= FOREFOOT_HI


def layer_change_points(ev, chains) -> list[dict]:
    """First Z-changing travel of each layer that prints a perimeter. Same selection as G1."""
    by_layer: dict[int, list] = {}
    for ch in chains:
        by_layer.setdefault(ch["layer"], []).append(ch)
    out = []
    seen = set()
    for d in ev:
        if d["kind"] == "travel" and d["zmove"] and d["layer"] not in seen and d["layer"] in by_layer:
            seen.add(d["layer"])
            lch = by_layer[d["layer"]]
            low = min(lch, key=lambda c: c["miny"])
            out.append({
                "layer": d["layer"], "x": d["x1"], "y": d["y1"], "z": d["z1"], "line": d["line"],
                "dist_bottom": C.dist_to_bottom(low, d["x1"], d["y1"]),
                "low": low, "chains": lch,
            })
    return out


def retract_prime_sequence(ev) -> list[tuple]:
    seq = []
    for d in ev:
        if d["kind"] in ("retract", "prime"):
            seq.append((d["kind"], round(d["e"], 5), round(d["f"], 1)))
    return seq


def fan_temp_sequence(ev) -> list[str]:
    return [d["raw"] for d in ev if d["kind"] == "cmd" and d.get("m") in FANTEMP]


def wipes_in_order(ev) -> list[dict]:
    """Dry-wipe travels between ;WIPE_START and ;WIPE_END, in file order."""
    out = []
    inw = False
    bucket: list[dict] = []
    for d in ev:
        if d["kind"] == "comment" and str(d.get("raw", "")).startswith(";WIPE_START"):
            inw = True
            bucket = []
            continue
        if d["kind"] == "comment" and str(d.get("raw", "")).startswith(";WIPE_END"):
            if inw:
                out.append(bucket)
            inw = False
            bucket = []
            continue
        if inw and d["kind"] == "travel":
            bucket.append(d)
    return out


def wipe_len(move: dict) -> float:
    return C.seglen(move)


def line(status: str, gate: str, parts: list[tuple[str, object]]) -> str:
    body = " ".join("%s=%s" % (k, _fmt(v)) for k, v in parts)
    return "%s %s %s" % (status, gate, body)


def _fmt(v) -> str:
    if isinstance(v, float):
        return "%.4f" % v
    return str(v)


def emit(status: str, gate: str, parts: list[tuple[str, object]], rep: dict, json_path: str | None) -> None:
    import json
    text = line(status, gate, parts)
    print(text)
    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        json.dump(rep, open(json_path, "w"), indent=1)
    sys.exit(0 if status == "PASS" else 1)
