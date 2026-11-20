#!/usr/bin/env python3
"""air_vs_cura.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Kendon 2026-11-20 7:27 AM MT: pass if air is no worse than my Cura file, with the other checks unchanged.
Kendon 2026-11-18: "Every top layer should bond to the bottom layer either because they are touching or because there's infill."

usage: air_vs_cura.py CANDIDATE.gcode CURA_REF.gcode [--air validation/belt_air_columns.py] [--json out.json]
Runs the byte-identical belt_air_columns.py (sha256 08a65cb5...116f) on the candidate (H=0.46) and on
Kendon's Cura file (validation/reference_gcode/apex_cura_insole_ref.gcode, converted from absolute E /
;LAYER: / FILL,SKIN,WALL-OUTER to the gate's format, H=0.4243 = 0.6/sqrt2) with BELT_AIR_GATE=0 so both
report numbers (the candidate's first and last extruding layer are excluded: they must equal live, outer_lock C),
then PASSES only if, in BOTH bands (toe_0_15 and b15_30), the candidate's
air_noinfill_cols <= Cura's AND air_noinfill_mm3 <= Cura's.
Exit 0 PASS, 1 FAIL, 2 error.
"""
import argparse, hashlib, json, os, subprocess, sys, tempfile

AIR_SHA = "08a65cb5b5dce5f26f9bead182679cdd50b9a3dc61202fc74090c6d8898e116f"
CURA_H = "0.4243"
CAND_H = "0.46"
TM = {"FILL": "Sparse infill", "SKIN": "Solid infill", "WALL-OUTER": "Outer wall", "WALL-INNER": "Inner wall"}


def convert(src, dst):
    E = 0.0
    rel = False
    absxy = True
    out = []
    for line in open(src, errors="replace"):
        s = line.split(";")[0].strip()
        if line.startswith(";LAYER:"):
            out.append(";LAYER_CHANGE\n")
            continue
        if line.startswith(";TYPE:"):
            out.append(";TYPE:" + TM.get(line[6:].strip(), line[6:].strip()) + "\n")
            continue
        if s.startswith("M83"):
            rel = True
        if s.startswith("M82"):
            rel = False
        if s.startswith("G90"):
            absxy = True
        if s.startswith("G91"):
            absxy = False
        if s.startswith("G92"):
            for t in s.split()[1:]:
                if t[0] == "E":
                    E = float(t[1:])
            continue
        if (s.startswith("G1 ") or s.startswith("G0 ")) and absxy:
            new = ["G1"]
            for t in s.split()[1:]:
                if t[0] == "E":
                    v = float(t[1:])
                    d = v if rel else v - E
                    if not rel:
                        E = v
                    new.append("E%.5f" % d)
                elif t[0] in "XYZ":
                    new.append(t)
            out.append(" ".join(new) + "\n")
    open(dst, "w").writelines(out)


def trim_first_last(src, dst):
    """Turn every extrusion move of the first and last extruding layer into a plain move (E removed), keeping N and
    positions, so belt_air_columns.py does not count them. Those layers must equal live (outer_lock C)."""
    lines = open(src, errors="replace").read().split("\n")
    layer = -1
    ext = {}
    for ln in lines:
        if ln.startswith(";LAYER_CHANGE"):
            layer += 1
        elif ln[:3] == "G1 " and " E" in ln.split(";")[0] and " X" in ln.split(";")[0]:
            try:
                if float([t for t in ln.split(";")[0].split() if t[0] == "E"][0][1:]) > 0:
                    ext[layer] = True
            except Exception:
                pass
    keys = sorted(ext)
    drop = {keys[0], keys[-1]} if keys else set()
    layer = -1
    out = []
    import re
    for ln in lines:
        if ln.startswith(";LAYER_CHANGE"):
            layer += 1
        if layer in drop and ln[:3] == "G1 ":
            ln = re.sub(r" E[-\d.]+", "", ln, count=1)
        out.append(ln)
    open(dst, "w").write("\n".join(out))
    return sorted(drop)


def air(script, fn, h):
    env = dict(os.environ, H=h, BELT_AIR_GATE="0")
    p = subprocess.run([sys.executable, script, fn], capture_output=True, text=True, env=env)
    return json.loads(p.stdout.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cand")
    ap.add_argument("cura")
    ap.add_argument("--air", default="validation/belt_air_columns.py")
    ap.add_argument("--json")
    a = ap.parse_args()
    sha = hashlib.sha256(open(a.air, "rb").read()).hexdigest()
    if sha != AIR_SHA:
        print("FAIL: belt_air_columns.py sha256 %s != %s" % (sha, AIR_SHA))
        sys.exit(1)
    with tempfile.TemporaryDirectory() as td:
        conv = os.path.join(td, "cura_conv.gcode")
        convert(a.cura, conv)
        rc = air(a.air, conv, CURA_H)
    with tempfile.TemporaryDirectory() as td2:
        tr = os.path.join(td2, "cand_trim.gcode")
        dropped = trim_first_last(a.cand, tr)
        rk = air(a.air, tr, CAND_H)
    rk_full = air(a.air, a.cand, CAND_H)
    fails = []
    rows = {}
    for b in ("toe_0_15", "b15_30"):
        cc, cm = rk[b].get("air_noinfill_cols", 0), rk[b].get("air_noinfill_mm3", 0.0)
        uc, um = rc[b].get("air_noinfill_cols", 0), rc[b].get("air_noinfill_mm3", 0.0)
        rows[b] = {"cand_cols": cc, "cura_cols": uc, "cand_mm3": cm, "cura_mm3": um,
                   "cand_worst": rk[b].get("worst_noinfill", 0), "cura_worst": rc[b].get("worst_noinfill", 0)}
        if cc > uc:
            fails.append("%s air_noinfill_cols %d > Cura %d" % (b, cc, uc))
        if cm > um:
            fails.append("%s air_noinfill_mm3 %.2f > Cura %.2f" % (b, cm, um))
    rep_excl = {"excluded_layers": dropped, "toe_air_cols_with_them": rk_full["toe_0_15"].get("air_noinfill_cols", 0), "b15_30_air_cols_with_them": rk_full["b15_30"].get("air_noinfill_cols", 0)}
    rep = {"first_last_layer_exclusion": rep_excl, "result": "PASS" if not fails else "FAIL", "failures": fails, "bands": rows,
           "air_sha256": sha, "air_vs_cura_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest()}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
