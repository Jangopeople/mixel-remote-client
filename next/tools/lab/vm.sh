#!/bin/bash
# vm.sh <vm-id> <script> [args...]: runs tools/lab/vm/<script> on a lab VM in the background and waits for it
# (the script writes its output to LOG, default /root/lab-<script>.log, and ends with a line LN_DONE).
# The script travels through the file server (xfer.py must run). WAIT=<seconds> limits the wait (default 3600);
# TAILN=<lines> is how much of the log is printed at the end (default 40).
set -u
L=$(cd "$(dirname "$0")" && pwd); VM=$1; S=$2; shift 2
cp "$L/vm/$S" "$L/xfer/lab-$S" || exit 1
HOSTIP=${LAB_BIND:-10.124.1.97}; PORT=${LAB_PORT:-8771}
LOG=${LOG:-/root/lab-$S.log}
ARGS=$(printf ' %q' "$@")
"$L/vx.sh" $VM "curl -fsS -o /root/lab-$S http://$HOSTIP:$PORT/lab-$S && : > $LOG && (LOG=$LOG nohup bash /root/lab-$S$ARGS > /dev/null 2>&1 &) ; echo started" || exit 1
END=$(( $(date +%s) + ${WAIT:-3600} ))
while [ $(date +%s) -lt $END ]; do
  sleep 10
  if "$L/vx.sh" $VM "grep -q '^LN_DONE' $LOG" 2>/dev/null; then break; fi
done
"$L/vx.sh" $VM "tail -n ${TAILN:-40} $LOG"
