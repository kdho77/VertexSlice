#!/usr/bin/env bash
# Independent production-path slicer for the handedness grade.
# Overlay method copied from validation/seam_fuse_gates/slice_all.sh:
# copy app.py out of the image, change only SLICE_GATEWAY_TIMEOUT_S 260 -> 880
# in that copy, bind-mount it read-only into the throwaway container.
# The image, the repo, Railway, and the frozen gates are not modified.
# The constant only bounds the wait; it is applied the same way for every image.
#
# usage (repo root or anywhere):
#   slice_production.sh OUT_DIR TAG=IMAGE [TAG=IMAGE ...]
# writes OUT_DIR/${TAG}_${material}_{left,right}.gcode
# and OUT_DIR/${TAG}_apex_core_left_heel.gcode when heel_first is accepted.
# apex_core and apex_light are required. apex_forge and apex_bounce are sliced
# when the server accepts them (HTTP 200). A 4xx on those two is "not offered".
set -euo pipefail
OUT=${1:?out dir}; shift
mkdir -p "$OUT"
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
KEY=gate-local-key
LEFT="$ROOT/validation/test_models/orthocad_sample_top_insole.stl"
RIGHT="$OUT/orthocad_sample_top_insole_right_ymirror.stl"
python3 "$HERE/make_right_stl.py" "$LEFT" "$RIGHT"
DOCKER=${DOCKER:-docker}
if ! $DOCKER info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi
port=18100
for spec in "$@"; do
  tag=${spec%%=*}; img=${spec#*=}
  port=$((port + 1)); name="hgg_${tag}"
  $DOCKER rm -f "$name" >/dev/null 2>&1 || true
  ov="$OUT/.overlay_${tag}"; mkdir -p "$ov"
  cid=$($DOCKER create "$img")
  $DOCKER cp "$cid:/app/server/app.py" "$ov/app.py"
  $DOCKER rm "$cid" >/dev/null
  sudo chmod 644 "$ov/app.py"
  n=$(grep -c '^SLICE_GATEWAY_TIMEOUT_S = 260$' "$ov/app.py" || true)
  if [ "$n" != 1 ]; then
    echo "app.py in $img does not have exactly one 'SLICE_GATEWAY_TIMEOUT_S = 260' line ($n); stop"
    exit 3
  fi
  sed -i 's/^SLICE_GATEWAY_TIMEOUT_S = 260$/SLICE_GATEWAY_TIMEOUT_S = 880  # test-harness overlay only (handedness grade)/' "$ov/app.py"
  $DOCKER run -d --name "$name" -p "$port:8080" -e VERTEXSLICE_API_KEY="$KEY" \
    -v "$(cd "$ov" && pwd)/app.py:/app/server/app.py:ro" "$img" >/dev/null
  ok=0
  for i in $(seq 1 120); do
    curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 && { ok=1; break; }
    sleep 2
  done
  if [ "$ok" != 1 ]; then
    echo "server $tag did not become healthy"
    $DOCKER logs "$name" | tail -30
    exit 1
  fi
  echo "healthy $tag image=$img port=$port"

  slice_one() {
    local mat=$1 side=$2 direction=$3 stl=$4 outfile=$5 required=$6
    local t0 code bytes sec
    t0=$(date +%s)
    code=$(curl -sS -m 1000 -w '%{http_code}' -D "$outfile.hdr" -o "$outfile.part" \
      -H "X-API-Key: $KEY" \
      -F profile_id=apex-v2-insole-95a \
      -F "model=@$stl" \
      -F wall_loops=1 \
      -F infill_density=26 \
      -F "print_direction=$direction" \
      -F belt_insole_place=true \
      -F "material=$mat" \
      "http://127.0.0.1:$port/v1/slice") || code=000
    bytes=$(stat -c %s "$outfile.part" 2>/dev/null || echo 0)
    sec=$(( $(date +%s) - t0 ))
    echo "$tag $mat $side $direction http=$code seconds=$sec bytes=$bytes"
    if [ "$code" = 200 ] && [ "$bytes" -gt 10000 ]; then
      mv "$outfile.part" "$outfile"
      return 0
    fi
    echo "---- body head ($tag $mat $side $direction) ----"
    head -c 400 "$outfile.part" 2>/dev/null || true
    echo
    rm -f "$outfile.part"
    if [ "$required" = 1 ]; then
      echo "required slice failed: $tag $mat $side $direction"
      $DOCKER logs "$name" 2>&1 | tail -30
      $DOCKER rm -f "$name" >/dev/null
      exit 1
    fi
    echo "optional slice not kept: $tag $mat $side $direction"
    return 0
  }

  for mat in apex_core apex_light apex_forge apex_bounce; do
    req=1
    if [ "$mat" = apex_forge ] || [ "$mat" = apex_bounce ]; then
      req=0
    fi
    slice_one "$mat" left toe_first "$LEFT" "$OUT/${tag}_${mat}_left.gcode" "$req"
    slice_one "$mat" right toe_first "$RIGHT" "$OUT/${tag}_${mat}_right.gcode" "$req"
  done
  # heel_first is optional: keep the file only when the server accepts it.
  slice_one apex_core left heel_first "$LEFT" "$OUT/${tag}_apex_core_left_heel.gcode" 0
  $DOCKER rm -f "$name" >/dev/null
done
echo ALL_GRADE_SLICES_DONE
