#!/usr/bin/env bash
# launch.sh <k> [label]: starts the lab app in the VM with a fresh single-use QA session file (xfer/qa-session-<label|w2>[.k].json) and signs in.
set -e; cd "$(dirname "$0")"; L=../../xfer; K=${1:-0}; W=${2:-w2}; F=$L/qa-session-$W.json; [ "$K" != 0 ] && F=$L/qa-session-$W.$K.json
scp -q "$F" ${VMHOST:-qa-win-v210}:C:/lab/sess.json
./vmi.sh startapp.ps1 >/dev/null
./vmi.sh signin.ps1
