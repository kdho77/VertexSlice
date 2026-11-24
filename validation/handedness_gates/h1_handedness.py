#!/usr/bin/env python3
"""h1_handedness.py - FROZEN gate H1 (OrthoCAD PM, 2026-11-24). Do not edit; checked by SHA256SUMS.
Kendon 2026-11-24 4:18 PM MT (P0): G-code labelled left is physically a right foot. Correct handedness is mandatory.
usage: h1_handedness.py LEFT_INPUT_SLICED.gcode RIGHT_INPUT_SLICED.gcode   exit 0 PASS, 1 FAIL
LEFT_INPUT_SLICED is the G-code the candidate server produced from a true-left input STL (fixture
validation/test_models/orthocad_sample_top_insole.stl measures LEFT: toe +X, arch flange -Y), RIGHT_INPUT_SLICED from its
Y-mirror (true right). PASS only if hand_classify says LEFT and RIGHT respectively (INCONCLUSIVE fails)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hand_classify import classify
l, r = classify(sys.argv[1]), classify(sys.argv[2])
ok = l["foot"] == "LEFT" and r["foot"] == "RIGHT"
print(json.dumps({"gate": "H1_handedness", "pass": ok, "left_input": l, "right_input": r}, indent=1))
print(("PASS" if ok else "FAIL") + f" H1 left-input->{l['foot']} right-input->{r['foot']}", file=sys.stderr)
sys.exit(0 if ok else 1)
