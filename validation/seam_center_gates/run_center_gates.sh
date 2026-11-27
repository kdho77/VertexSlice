#!/usr/bin/env bash
# run_center_gates.sh — frozen seam-center grader.
# usage: bash validation/seam_center_gates/run_center_gates.sh BASE_DIR CAND_DIR [JSON_DIR]
# BASE_DIR and CAND_DIR each hold:
#   apex_core_orthocad.gcode  apex_core_insole2.gcode
#   apex_light_orthocad.gcode apex_light_insole2.gcode
# Exit 0 only when every gate passes. One PASS/FAIL line per gate, with measured values.
set -uo pipefail
BASE=${1:?BASE_dir}
CAND=${2:?CAND_dir}
OUT=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
cd "$ROOT"
G=validation/seam_center_gates
echo "== manifest"
(cd validation && sha256sum -c seam_center_gates/SHA256SUMS) || { echo "MANIFEST MISMATCH: stop"; exit 2; }
for f in apex_core_orthocad.gcode apex_core_insole2.gcode apex_light_orthocad.gcode apex_light_insole2.gcode; do
  [ -s "$BASE/$f" ] || { echo "missing $BASE/$f"; exit 2; }
  [ -s "$CAND/$f" ] || { echo "missing $CAND/$f"; exit 2; }
done
fail=0
run() {
  local name=$1
  shift
  local extra=()
  if [ -n "$OUT" ]; then
    mkdir -p "$OUT"
    extra=(--json "$OUT/${name}.json")
  fi
  local line rc
  line=$("$@" "${extra[@]}")
  rc=$?
  if [ -n "$line" ]; then
    echo "$line"
  else
    echo "FAIL $name rc=$rc"
  fi
  if [ "$rc" -ne 0 ]; then
    fail=1
  fi
}
run A1 python3 "$G/a1_center.py" "$BASE" "$CAND"
run A2 python3 "$G/a2_forefoot.py" "$BASE" "$CAND"
run A3 python3 "$G/a3_smooth.py" "$BASE" "$CAND"
run A4 python3 "$G/a4_layer_change.py" "$BASE" "$CAND"
run A5 python3 "$G/a5_sample.py" "$BASE" "$CAND"
run A6 python3 "$G/a6_path.py" "$BASE" "$CAND"
run A7 python3 "$G/a7_time.py" "$BASE" "$CAND"
run A8 python3 "$G/a8_apexlight.py" "$BASE" "$CAND"
run A9 python3 "$G/a9_crossings.py" "$BASE" "$CAND"
run G1 python3 "$G/g1_bottom.py" "$BASE" "$CAND"
if [ "$fail" -eq 0 ]; then
  echo "SEAM-CENTER GATES: PASS"
else
  echo "SEAM-CENTER GATES: FAIL"
fi
exit "$fail"
