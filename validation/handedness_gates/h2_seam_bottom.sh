#!/usr/bin/env bash
# h2_seam_bottom.sh - FROZEN gate H2 (OrthoCAD PM, 2026-11-24). Do not edit; checked by SHA256SUMS.
# Kendon 2026-11-20: "The seams have always been on the bottom it's been a permanent rule since the beginning".
# usage (VertexSlice repo root): h2_seam_bottom.sh CANDIDATE.gcode LIVE.gcode   (same material, same input STL)
# Measures both files with the existing SHA-pinned seam gate G1 (validation/seam_fuse_gates/g1_seam_bottom.py; bottom =
# each loop's own min-Y belt edge, which a mirror across the belt does not move). The handedness fix may not add any
# off-bottom seam or layer change: for each of closed_off_bottom, layer_changes_off_bottom, open_ends_off_bottom and
# open_only_layer_changes_off_bottom, CANDIDATE <= LIVE; closed_loops and layer_changes within 2% of LIVE.
# Exit 0 PASS, 1 FAIL, 2 setup error.
set -uo pipefail
(cd validation && sha256sum -c --quiet seam_fuse_gates/SHA256SUMS) || { echo "seam_fuse_gates manifest mismatch"; exit 2; }
T=$(mktemp -d)
python3 validation/seam_fuse_gates/g1_seam_bottom.py "$1" --ref "$2" --json "$T/c.json" >/dev/null 2>&1
python3 validation/seam_fuse_gates/g1_seam_bottom.py "$2" --ref "$2" --json "$T/l.json" >/dev/null 2>&1
python3 - "$T/c.json" "$T/l.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))["cand"]; l = json.load(open(sys.argv[2]))["cand"]
keys = ["closed_off_bottom", "layer_changes_off_bottom", "open_ends_off_bottom", "open_only_layer_changes_off_bottom"]
bad = [f"{k} {c[k]} > live {l[k]}" for k in keys if c[k] > l[k]]
for k in ("closed_loops", "layer_changes"):
    if abs(c[k] - l[k]) > 0.02 * max(1, l[k]): bad.append(f"{k} {c[k]} vs live {l[k]}")
print(json.dumps({"gate": "H2_seam_bottom", "pass": not bad, "cand": {k: c[k] for k in keys + ["closed_loops", "layer_changes"]},
                  "live": {k: l[k] for k in keys + ["closed_loops", "layer_changes"]}, "failures": bad}, indent=1))
print(("PASS H2" if not bad else "FAIL H2 " + "; ".join(bad)), file=sys.stderr)
sys.exit(1 if bad else 0)
PY
