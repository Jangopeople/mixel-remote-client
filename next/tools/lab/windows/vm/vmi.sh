#!/usr/bin/env bash
# vmi.sh <script.ps1> [args...]: runs a PowerShell script INSIDE the logged-on desktop session of the lab VM (scheduled task, interactive)
# and prints its output file. VM host alias in VMHOST (default qa-win-v210). Output of the script: whatever it writes to stdout.
# vmi.sh -s <script.ps1>: same, but in the ssh session (elevated, no desktop).
set -euo pipefail
H="${VMHOST:-qa-win-v210}"
if [ "$1" = "-s" ]; then shift; S=$1; shift; scp -q "$S" "$H:C:/lab/$(basename "$S")"; ssh "$H" "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\lab\\$(basename "$S") $*"; exit; fi
S=$1; shift; B=$(basename "$S" .ps1)
scp -q "$S" "$H:C:/lab/$B.ps1"; scp -q "$(dirname "$0")/runi.ps1" "$H:C:/lab/runi.ps1"
ssh "$H" "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\lab\\runi.ps1 C:\\lab\\$B.ps1 $*"
for i in $(seq 1 ${WAIT:-120}); do
  if ssh "$H" "Test-Path C:\\lab\\$B.done" | grep -q True; then break; fi; sleep 1
done
ssh "$H" "Get-Content C:\\lab\\$B.out -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName lab_$B -Confirm:\$false -ErrorAction SilentlyContinue"
