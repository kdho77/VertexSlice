#!/usr/bin/env bash
# slice_all.sh - FROZEN (PM, 2026-11-20). Do not edit; checked by SHA256SUMS.
# usage: slice_all.sh OUT_DIR TAG=IMAGE [TAG=IMAGE ...]     e.g. live=ghcr.io/...@sha256:dd51... pre29=vs:pre29 cand=vs:proof
# Slices ApexCore and ApexLight through POST /v1/slice on orthocad_sample_top_insole.stl and insole_sample.stl for every
# image, writing OUT_DIR/<tag>_<material>_<model>.gcode (+ .hdr). Any non-200 or empty body exits nonzero.
#
# Gateway timeout (PR #38 finding): server/app.py hard-codes SLICE_GATEWAY_TIMEOUT_S = 260 (not an env var). On GitHub
# runners LIVE ApexLight orthocad takes longer than 260 s (164 s on an 8-core box, >270 s in CI run 44813646632), so the
# server itself answers 504; the client's curl -m 900 was never the limit. This harness therefore copies app.py OUT of
# each image, changes exactly that one constant to 880 in the copy, and bind-mounts the copy read-only into the TEST
# container only. The repo's server/app.py, the images, Railway and production are not changed. The constant does not
# affect G-code (it only bounds the wait), and the same overlay is applied to every image so all three are equal.
set -euo pipefail
OUT=${1:?out dir}; shift
mkdir -p "$OUT"
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
KEY=gate-local-key
port=18090
for spec in "$@"; do
  tag=${spec%%=*}; img=${spec#*=}
  port=$((port+1)); name="sfg_${tag}"
  docker rm -f "$name" >/dev/null 2>&1 || true
  ov="$OUT/.overlay_${tag}"; mkdir -p "$ov"
  cid=$(docker create "$img"); docker cp "$cid:/app/server/app.py" "$ov/app.py"; docker rm "$cid" >/dev/null
  n=$(grep -c '^SLICE_GATEWAY_TIMEOUT_S = 260$' "$ov/app.py" || true)
  [ "$n" = 1 ] || { echo "app.py in $img does not have exactly one 'SLICE_GATEWAY_TIMEOUT_S = 260' line ($n); stop"; exit 3; }
  sed -i 's/^SLICE_GATEWAY_TIMEOUT_S = 260$/SLICE_GATEWAY_TIMEOUT_S = 880  # test-harness overlay only (slice_all.sh)/' "$ov/app.py"
  docker run -d --name "$name" -p "$port:8080" -e VERTEXSLICE_API_KEY="$KEY" -v "$(cd "$ov" && pwd)/app.py:/app/server/app.py:ro" "$img" >/dev/null
  ok=0; for i in $(seq 1 120); do curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 && { ok=1; break; }; sleep 2; done
  [ "$ok" = 1 ] || { echo "server $tag did not become healthy"; docker logs "$name" | tail -20; exit 1; }
  for mat in apex_core apex_light; do
    for stl in orthocad:orthocad_sample_top_insole insole2:insole_sample; do
      f="$OUT/${tag}_${mat}_${stl%%:*}"
      t0=$(date +%s)
      code=$(curl -sS -m 900 -w '%{http_code}' -D "$f.hdr" -o "$f.gcode" -H "X-API-Key: $KEY" \
        -F profile_id=apex-v2-insole-95a -F "model=@$ROOT/validation/test_models/${stl##*:}.stl" \
        -F wall_loops=1 -F infill_density=26 -F print_direction=toe_first -F belt_insole_place=true -F "material=$mat" \
        "http://127.0.0.1:$port/v1/slice") || code=000
      echo "$tag $mat ${stl%%:*} http=$code seconds=$(( $(date +%s)-t0 )) bytes=$(stat -c %s "$f.gcode" 2>/dev/null || echo 0)"
      [ "$code" = 200 ] && [ -s "$f.gcode" ] || { echo "slice failed: $tag $mat ${stl%%:*}"; docker logs "$name" 2>&1 | tail -20; docker rm -f "$name" >/dev/null; exit 1; }
    done
  done
  docker rm -f "$name" >/dev/null
done
echo ALL_SLICES_OK
