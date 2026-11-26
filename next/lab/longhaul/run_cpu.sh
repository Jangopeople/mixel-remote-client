#!/bin/bash
# run.sh <name> <wan-json> [secs]   -> $L/out/<name>.{host,viewer}.log
L=$(dirname "$0"); BIN=${BIN:-$(ls -t /home/user/mixel-remote-client/next/target/release/deps/mixel_engine-* | grep -v '\.d$' | head -1)}
mkdir -p $L/out; NAME=$1; SECS=${3:-60}
pgrep Xvfb >/dev/null || (Xvfb :77 -screen 0 1920x1080x24 -noreset -ac >/dev/null 2>&1 & sleep 1)
$L/net.sh "$2"
rm -rf /tmp/lh-host /tmp/lh-viewer; mkdir -p /tmp/lh-host /tmp/lh-viewer
T=remote_session::remote_session_longhaul_test::longhaul
HOME=/tmp/lh-host LONGHAUL_HEAVY=${HEAVY:-0} LONGHAUL_ROLE=host LONGHAUL_SOCK=/tmp/lh.sock DISPLAY=:77 ip netns exec hostns taskset -c ${HCPUS:-0-3} ${HBIN:-$BIN} --ignored --exact $T --nocapture > $L/out/$NAME.host.log 2>&1 &
HP=$!
sleep 0.5
HOME=/tmp/lh-viewer LONGHAUL_ROLE=viewer LONGHAUL_SOCK=/tmp/lh.sock LONGHAUL_SECS=$SECS DISPLAY=:77 timeout $((SECS+60)) ip netns exec techns ${VBIN:-$BIN} --ignored --exact $T --nocapture > $L/out/$NAME.viewer.log 2>&1
kill $HP 2>/dev/null; sleep 0.5; pkill -f "deps/mixel_engine-.*--ignored" 2>/dev/null
cp /tmp/lh-host/.local/state/mixel-remote/perf.log $L/out/$NAME.host.perf 2>/dev/null
cp /tmp/lh-viewer/.local/state/mixel-remote/perf.log $L/out/$NAME.viewer.perf 2>/dev/null
cp /tmp/wan.log $L/out/$NAME.wan.log
grep RESULT $L/out/$NAME.viewer.log || tail -20 $L/out/$NAME.viewer.log
