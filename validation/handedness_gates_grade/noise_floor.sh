#!/usr/bin/env bash
# Live-image-only noise floor for H2 on apex_forge and apex_bounce.
# Does not start the candidate image and does not edit frozen gates.
#
# (a) each side sliced twice with an identical request; H2 is run2 vs run1
# (b) RIGHT fixture with every vertex X += 1e-5 mm, winding unchanged,
#     sliced once and compared with that material's live right run1
#
# usage: noise_floor.sh OUT_DIR GATES_ROOT
# GATES_ROOT is a checkout of the frozen gates (contains validation/).
set -euo pipefail
OUT=${1:?out dir}
GATES=${2:?gates root}
mkdir -p "$OUT"
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
KEY=gate-local-key
LEFT="$ROOT/validation/test_models/orthocad_sample_top_insole.stl"
RIGHT="$OUT/right_ymirror.stl"
PERT="$OUT/right_ymirror_x1e-5.stl"
IMG=ghcr.io/kdho77/vertexslice-server@sha256:8cac1d54c5e1a12f4693412c32fe40dd9c5682b9279e2b1d38f1225760aca579
python3 "$HERE/make_right_stl.py" "$LEFT" "$RIGHT"
python3 "$HERE/perturb_x.py" "$RIGHT" "$PERT"
DOCKER=${DOCKER:-docker}
if ! $DOCKER info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi
name=hgg_noise_live
port=18240
$DOCKER rm -f "$name" >/dev/null 2>&1 || true
ov="$OUT/.overlay_live"
mkdir -p "$ov"
cid=$($DOCKER create "$IMG")
$DOCKER cp "$cid:/app/server/app.py" "$ov/app.py"
$DOCKER rm "$cid" >/dev/null
sudo chmod 644 "$ov/app.py"
n=$(grep -c '^SLICE_GATEWAY_TIMEOUT_S = 260$' "$ov/app.py" || true)
if [ "$n" != 1 ]; then
  echo "app.py does not have exactly one timeout line ($n); stop"
  exit 3
fi
sed -i 's/^SLICE_GATEWAY_TIMEOUT_S = 260$/SLICE_GATEWAY_TIMEOUT_S = 880  # test-harness overlay only (handedness grade)/' "$ov/app.py"
$DOCKER run -d --name "$name" -p "$port:8080" -e VERTEXSLICE_API_KEY="$KEY" \
  -v "$(cd "$ov" && pwd)/app.py:/app/server/app.py:ro" "$IMG" >/dev/null
ok=0
for i in $(seq 1 120); do
  curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 && { ok=1; break; }
  sleep 2
done
if [ "$ok" != 1 ]; then
  echo "live server did not become healthy"
  $DOCKER logs "$name" | tail -30
  exit 1
fi
echo "healthy live port=$port"

slice_one() {
  local mat=$1 side=$2 stl=$3 outfile=$4
  local t0 code bytes sec
  t0=$(date +%s)
  code=$(curl -sS -m 1000 -w '%{http_code}' -D "$outfile.hdr" -o "$outfile.part" \
    -H "X-API-Key: $KEY" \
    -F profile_id=apex-v2-insole-95a \
    -F "model=@$stl" \
    -F wall_loops=1 \
    -F infill_density=26 \
    -F print_direction=toe_first \
    -F belt_insole_place=true \
    -F "material=$mat" \
    "http://127.0.0.1:$port/v1/slice") || code=000
  bytes=$(stat -c %s "$outfile.part" 2>/dev/null || echo 0)
  sec=$(( $(date +%s) - t0 ))
  echo "live $mat $side http=$code seconds=$sec bytes=$bytes -> $(basename "$outfile")"
  if [ "$code" = 200 ] && [ "$bytes" -gt 10000 ]; then
    mv "$outfile.part" "$outfile"
    return 0
  fi
  head -c 400 "$outfile.part" 2>/dev/null || true
  echo
  $DOCKER logs "$name" | tail -20
  $DOCKER rm -f "$name" >/dev/null
  exit 1
}

for mat in apex_forge apex_bounce; do
  for run in run1 run2; do
    slice_one "$mat" "left_$run" "$LEFT" "$OUT/live_${mat}_left_${run}.gcode"
    slice_one "$mat" "right_$run" "$RIGHT" "$OUT/live_${mat}_right_${run}.gcode"
  done
  slice_one "$mat" right_x1e5 "$PERT" "$OUT/live_${mat}_right_x1e5.gcode"
done
$DOCKER rm -f "$name" >/dev/null
echo NOISE_SLICES_DONE

extrusions() {
  python3 - "$1" <<'PY'
import re, sys
word = re.compile(r"([XYZEF])(-?\d*\.?\d+)")
n = 0
total = 0.0
rel = True
e_abs = 0.0
with open(sys.argv[1], errors="replace") as fh:
    for line in fh:
        if line.startswith("M82"):
            rel = False
            continue
        if line.startswith("M83"):
            rel = True
            continue
        code = line.split(";", 1)[0]
        if not code.startswith(("G0", "G1", "G00", "G01")):
            continue
        ev = None
        for k, v in word.findall(code):
            if k == "E":
                ev = float(v)
        if ev is None:
            continue
        de = ev if rel else ev - e_abs
        if not rel:
            e_abs = ev
        if de > 0:
            n += 1
            total += de
print(f"extrusions={n} total_E={total:.5f}")
PY
}

run_h2() {
  local label=$1 cand=$2 live=$3
  echo "---- H2 $label ----"
  echo -n "cand "; extrusions "$cand"
  echo -n "live "; extrusions "$live"
  (cd "$GATES" && bash validation/handedness_gates/h2_seam_bottom.sh "$cand" "$live") || true
}

for mat in apex_forge apex_bounce; do
  for side in left right; do
    run_h2 "${mat}_${side}_run2_vs_run1" \
      "$OUT/live_${mat}_${side}_run2.gcode" \
      "$OUT/live_${mat}_${side}_run1.gcode"
  done
  run_h2 "${mat}_right_x1e5_vs_run1" \
    "$OUT/live_${mat}_right_x1e5.gcode" \
    "$OUT/live_${mat}_right_run1.gcode"
done
echo NOISE_FLOOR_DONE
