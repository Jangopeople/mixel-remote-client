#!/bin/bash
# vx.sh <vm-id> <shell command...>: runs one command in a lab VM as root (qemu guest agent through the Proxmox host).
# Prints the command's output and exits with its exit code. Short commands only (the agent call has a timeout:
# VX_TIMEOUT seconds, default 90); long jobs go through vm.sh.
set -u
VM=$1; shift
ENC=$(printf '%s' "$*" | base64 | tr -d '\n')
OUT=$(ssh -o BatchMode=yes mixel-proxmox "qm guest exec $VM --timeout ${VX_TIMEOUT:-90} -- /bin/bash -c 'echo $ENC | base64 -d | /bin/bash'" 2>&1)
printf '%s' "$OUT" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    sys.stderr.write(raw); sys.exit(255)
sys.stdout.write(d.get("out-data", "")); sys.stderr.write(d.get("err-data", ""))
sys.exit(d.get("exitcode", 0 if d.get("exited") else 255))'
