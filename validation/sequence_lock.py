#!/usr/bin/env python3
"""sequence_lock.py - FROZEN gate (OrthoCAD PM, 2026-11-20). Do not edit; CI checks its sha256.

Why: layer-time-driven fan/slowdown changes were traced to frosty toe tips on ApexCore. Thermal and motion
commands must not change when interior beads are added.
usage: sequence_lock.py LIVE.gcode CANDIDATE.gcode [--json out.json]
Exit 0 PASS, 1 FAIL.
Compares, per layer (layer index = count of ';LAYER_CHANGE' lines, 0 = header), the ORDERED list of these commands
(comment stripped, whitespace normalised, values exact): M104 M109 M140 M190 M141 M191 M106 M107 M201 M202 M203 M204
M205 M220 M221 M900 M572 SET_PRESSURE_ADVANCE SET_VELOCITY_LIMIT PRINT_START PRINT_END. Layer-start lines
(';_SET_FAN_SPEED_CHANGING_LAYER' and ';FAN_SPEED' style comments) are not compared; the M106 values are.
Any difference in order, count or value fails. M73 progress lines are ignored (they follow print time).
EXCEPTION, M204 only (acceleration): Orca toggles M204 between the travel and print values around every travel, so a
legal new bead with its own travel adds a toggle pair (and live layers without any travel have no M204 at all). M204 is
therefore left out of the ordered list and checked as sets: every M204 value the candidate uses in a layer must be one
that live uses somewhere in the file (no new acceleration value), and every M204 value live uses in a layer must still
appear in that candidate layer (none dropped).
Everything else (M104 M109 M106 M107 M205 M900 M572 SET_PRESSURE_ADVANCE ...) stays strict and ordered.
"""
import argparse, hashlib, json, sys

CMDS = {"M104", "M109", "M140", "M190", "M141", "M191", "M106", "M107", "M201", "M202", "M203", "M204", "M205",
        "M220", "M221", "M900", "M572", "SET_PRESSURE_ADVANCE", "SET_VELOCITY_LIMIT", "PRINT_START", "PRINT_END"}


SET_ONLY = {"M204"}


def seq(fn):
    out = [[]]
    sets = [set()]
    for line in open(fn, errors="replace"):
        if line.startswith(";LAYER_CHANGE"):
            out.append([])
            sets.append(set())
            continue
        s = line.split(";")[0].split()
        if s and s[0] in CMDS:
            if s[0] in SET_ONLY:
                sets[-1].add(" ".join(s))
            else:
                out[-1].append(" ".join(s))
    return out, sets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live")
    ap.add_argument("cand")
    ap.add_argument("--json")
    a = ap.parse_args()
    (L, LS), (C, CS) = seq(a.live), seq(a.cand)
    fails = []
    diffs = []
    allowed = set().union(*LS) if LS else set()
    sdiffs = [i for i in range(min(len(LS), len(CS))) if not (CS[i] <= allowed and LS[i] <= CS[i])]
    if sdiffs:
        i = sdiffs[0]
        fails.append("%d layers break the M204 set rule (new value or a live value dropped); first layer %d: live %s vs cand %s (file-wide live values %s)" % (len(sdiffs), i, sorted(LS[i]), sorted(CS[i]), sorted(allowed)))
    if len(L) != len(C):
        fails.append("layer count %d != live %d" % (len(C), len(L)))
    for i in range(min(len(L), len(C))):
        if L[i] != C[i]:
            diffs.append(i)
    if diffs:
        i = diffs[0]
        fails.append("%d layers differ in M104/M109/M106/M107/M204/SET_PRESSURE_ADVANCE sequence; first layer %d: live %s vs cand %s" % (len(diffs), i, L[i][:6], C[i][:6]))
    rep = {"result": "PASS" if not fails else "FAIL", "failures": fails, "commands_live": sum(map(len, L)), "commands_cand": sum(map(len, C)),
           "layers_differing": len(diffs), "layers_m204_set_differing": len(sdiffs), "first_layers": diffs[:10], "sequence_lock_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest()}
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
