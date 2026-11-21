#!/usr/bin/env bash
# run_gates.sh - FROZEN runner for the seam-fuse gates G1, G3-G8 (PM, 2026-11-20, rev 2; G2 seam_line dropped by Kendon t2446u). Do not edit; checked by SHA256SUMS.
# usage (repo root): bash validation/seam_fuse_gates/run_gates.sh OUT_DIR BASE_SHA HEAD_SHA
#   OUT_DIR holds {live,pre29,cand}_{apex_core,apex_light}_{orthocad,insole2}.gcode from slice_all.sh
#   (tags exactly live, pre29, cand). Needs python3 with numpy.  Exit 0 only if every gate passes.
set -uo pipefail
OUT=${1:?out dir}; BASE=${2:?base sha}; HEAD=${3:?head sha}
G=validation/seam_fuse_gates
R="$OUT/gate_results"; mkdir -p "$R"
fail=0
echo "== manifest"; (cd validation && sha256sum -c seam_fuse_gates/SHA256SUMS) || { echo "MANIFEST MISMATCH: stop"; exit 2; }
for t in live pre29 cand; do for m in apex_core apex_light; do for s in orthocad insole2; do
  [ -s "$OUT/${t}_${m}_${s}.gcode" ] || { echo "missing $OUT/${t}_${m}_${s}.gcode: stop"; exit 2; }
done; done; done
run() {  # name, cmd...
  local name=$1; shift
  "$@" > "$R/$name.json" 2> "$R/$name.err"; local rc=$?
  local line; line=$(tail -1 "$R/$name.err")
  if [ $rc -eq 0 ]; then echo "PASS $name"; else echo "FAIL $name (rc=$rc) $line"; fail=1; fi
  echo "$name rc=$rc" >> "$R/summary.txt"
}
: > "$R/summary.txt"
for s in orthocad insole2; do
  C="$OUT/cand_apex_core_$s.gcode"; P="$OUT/pre29_apex_core_$s.gcode"; L="$OUT/live_apex_core_$s.gcode"
  run "G1_seam_bottom_$s" python3 $G/g1_seam_bottom.py "$C" --ref "$P"
  run "G3_seam_joint_$s" python3 $G/g3_seam_joint.py "$C" --ref "$P"
  run "G4_outer_match_pre29_$s" python3 $G/g4_outer_match_pre29.py "$C" "$P"
  run "G5_crossings_core_$s" python3 $G/g5_crossings.py "$C"
  run "G5_crossings_light_$s" python3 $G/g5_crossings.py "$OUT/cand_apex_light_$s.gcode" --baseline "$OUT/live_apex_light_$s.gcode"
  run "G6_apexlight_unchanged_$s" python3 $G/g6_apexlight_unchanged.py "$OUT/cand_apex_light_$s.gcode" "$OUT/live_apex_light_$s.gcode"
  run "G7_print_budget_$s" python3 $G/g7_print_budget.py "$C" "$L" "$P"
done
bash $G/g8_repo_guard.sh "$BASE" "$HEAD" > "$R/G8_repo_guard.txt" 2>&1 && echo "PASS G8_repo_guard" || { echo "FAIL G8_repo_guard"; tail -20 "$R/G8_repo_guard.txt"; fail=1; }
echo "G8_repo_guard rc=$fail" >> "$R/summary.txt"
[ $fail = 0 ] && echo "SEAM-FUSE GATES: PASS" || echo "SEAM-FUSE GATES: FAIL"
exit $fail
