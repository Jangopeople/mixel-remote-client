#!/usr/bin/env bash
# launch.sh <k>: starts the lab app in the VM with a fresh single-use QA session file (xfer/qa-session-w2[.k].json) and signs in.
set -e; cd "$(dirname "$0")"; L=../../xfer; K=${1:-0}; F=$L/qa-session-w2.json; [ "$K" != 0 ] && F=$L/qa-session-w2.$K.json
scp -q "$F" qa-win-v210:C:/lab/sess.json
./vmi.sh startapp.ps1 >/dev/null
./vmi.sh signin.ps1
