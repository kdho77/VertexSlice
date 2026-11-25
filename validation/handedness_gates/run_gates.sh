#!/usr/bin/env bash
# run_gates.sh - FROZEN runner for handedness gates H1-H3 (OrthoCAD PM, 2026-11-24; v3 6:00 PM MT: H2/H3 compare each
# candidate side with the live file of the OPPOSITE side, which is the same placed mesh; see h3_surface_mirror_equal.py). Do not edit; checked by SHA256SUMS.
# usage (VertexSlice repo root): bash validation/handedness_gates/run_gates.sh OUT_DIR
# OUT_DIR must hold, for MAT in apex_core apex_light (and apex_forge apex_bounce when the server offers them):
#   cand_${MAT}_left.gcode  cand_${MAT}_right.gcode   candidate server, input = fixture LEFT / its Y-mirror (RIGHT)
#   live_${MAT}_left.gcode  live_${MAT}_right.gcode   live (main) server, same inputs and form fields
#   (POST /v1/slice, profile_id=apex-v2-insole-95a, wall_loops=1, infill_density=26, print_direction=toe_first,
#    belt_insole_place=true; LEFT = validation/test_models/orthocad_sample_top_insole.stl, RIGHT = that STL with
#    Y negated and triangle winding reversed. Also cand/live_apex_core_left_heel.gcode with print_direction=heel_first
#    if the server accepts heel_first.)
# Exit 0 only if every gate passes.
set -uo pipefail
OUT=${1:?out dir}; G=validation/handedness_gates; R="$OUT/handedness_results"; mkdir -p "$R"; fail=0
(cd validation && sha256sum -c handedness_gates/SHA256SUMS) || { echo "MANIFEST MISMATCH: stop"; exit 2; }
run() { local n=$1; shift; "$@" > "$R/$n.json" 2> "$R/$n.err"; local rc=$?; echo "$( [ $rc -eq 0 ] && echo PASS || echo FAIL ) $n $(tail -1 "$R/$n.err")"; [ $rc -eq 0 ] || fail=1; }
n=0
for MAT in apex_core apex_light apex_forge apex_bounce; do
  [ -s "$OUT/cand_${MAT}_left.gcode" ] || { [ $MAT = apex_core -o $MAT = apex_light ] && { echo "missing cand_${MAT}_left.gcode: stop"; exit 2; }; continue; }
  n=$((n+1))
  run "H1_handedness_$MAT" python3 $G/h1_handedness.py "$OUT/cand_${MAT}_left.gcode" "$OUT/cand_${MAT}_right.gcode"
  for S in left right; do
    O=$([ $S = left ] && echo right || echo left)
    run "H2_seam_bottom_${MAT}_$S" bash $G/h2_seam_bottom.sh "$OUT/cand_${MAT}_$S.gcode" "$OUT/live_${MAT}_$O.gcode"
    run "H3_surface_equal_${MAT}_$S" python3 $G/h3_surface_mirror_equal.py "$OUT/cand_${MAT}_$S.gcode" "$OUT/live_${MAT}_$O.gcode" --opposite
  done
done
if [ -s "$OUT/cand_apex_core_left_heel.gcode" ]; then
  run "H1h_heel_first_left" python3 -c "import sys,json;sys.path.insert(0,'$G');from hand_classify import classify;r=classify('$OUT/cand_apex_core_left_heel.gcode');print(json.dumps(r));print(r['foot'],file=sys.stderr);sys.exit(0 if r['foot']=='LEFT' else 1)"
fi
echo "materials gated: $n"; [ $fail -eq 0 ] && echo ALL_HANDEDNESS_GATES_PASS || echo HANDEDNESS_GATES_FAIL
exit $fail
