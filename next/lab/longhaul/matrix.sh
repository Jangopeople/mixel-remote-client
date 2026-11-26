#!/bin/bash
# matrix.sh <tag>: the standard long-haul set, 45 s each, heavy content
L=$(dirname "$0"); T=$1; export HEAVY=${HEAVY:-200}
p(){ echo "{\"a2b\":{\"delay_ms\":105,\"jitter_ms\":6,\"loss\":$3,\"rate_mbps\":$1,\"queue_ms\":$2},\"b2a\":{\"delay_ms\":105,\"jitter_ms\":6,\"loss\":$3,\"rate_mbps\":20,\"queue_ms\":100}}"; }
for c in "A 50 100 0" "B 4 300 0" "C 4 300 0.005" "D 20 100 0.01" "E 1.5 500 0" "F 8 200 0.02"; do
  set -- $c
  echo "== $T-$1 up=${2}Mbit queue=${3}ms loss=$4"
  $L/run.sh $T-$1 "$(p $2 $3 $4)" ${SECS:-45}
done
