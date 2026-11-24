#!/usr/bin/env python3
"""run_gates.py — grade one candidate slice set with the frozen gates.

Directory layout (live and candidate):
  apex_forge_orthocad.gcode
  apex_bounce_orthocad.gcode
  apex_core_orthocad.gcode
  slice_times.json    {"apex_forge": {"elapsed_s": ...}, ...}

Optional --diff is a unified diff of the candidate tree against live main.
Exit 0 only when every required gate passes. ApexCore G1's live residual of 3
is reported and is not a pass line; byte identity locks that file.
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C
import g1_thin_bond, g2_thick_bond, g_hollow, g_surface, g_filament, g_time, g_core_identity, g_shared_path


MATERIALS = ("apex_forge", "apex_bounce", "apex_core")


def _gcode(directory, material):
    return os.path.join(directory, "%s_orthocad.gcode" % material)


def _times(directory):
    path = os.path.join(directory, "slice_times.json")
    if not os.path.isfile(path):
        return {}
    return json.load(open(path))


def grade(live_dir, cand_dir, diff_text=""):
    times = _times(cand_dir)
    reports = {}
    for material in MATERIALS:
        live = _gcode(live_dir, material)
        cand = _gcode(cand_dir, material)
        block = {
            "hollow": g_hollow.evaluate(cand),
            "G1_thin_bond": g1_thin_bond.evaluate(cand, live),
            "G2_thick_bond": g2_thick_bond.evaluate(cand, live),
            "surface": g_surface.evaluate(cand, live),
            "filament": g_filament.evaluate(cand, live),
            "slice_time": g_time.evaluate((times.get(material) or {}).get("elapsed_s")),
        }
        if material == "apex_core":
            block["core_identity"] = g_core_identity.evaluate(cand, live)
        reports[material] = block
    forge_pass = reports["apex_forge"]["hollow"]["result"] == "PASS"
    bounce_pass = reports["apex_bounce"]["hollow"]["result"] == "PASS"
    reports["shared_path"] = g_shared_path.evaluate(forge_pass, bounce_pass, diff_text)
    required = []
    for material in ("apex_forge", "apex_bounce"):
        for gate in ("hollow", "G1_thin_bond", "G2_thick_bond", "surface", "filament", "slice_time"):
            required.append(reports[material][gate]["result"] == "PASS")
    required.append(reports["apex_core"]["core_identity"]["result"] == "PASS")
    required.append(reports["apex_core"]["surface"]["result"] == "PASS")
    required.append(reports["apex_core"]["slice_time"]["result"] == "PASS")
    required.append(reports["shared_path"]["result"] == "PASS")
    overall = "PASS" if all(required) else "FAIL"
    return C.round_report({"result": overall, "materials": reports, "shared_path": reports["shared_path"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True)
    ap.add_argument("--cand", required=True)
    ap.add_argument("--diff")
    ap.add_argument("--json")
    a = ap.parse_args()
    diff = open(a.diff, errors="replace").read() if a.diff else ""
    rep = grade(a.live, a.cand, diff)
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({"result": rep["result"]}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
