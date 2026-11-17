#!/bin/bash
# push.sh <worktree> <label>: packs the tracked sources of <worktree>/next into xfer/src-<label>.tgz
# (what the lab VMs build from; no build output, no untracked files).
set -eu
L=$(cd "$(dirname "$0")" && pwd); WT=$(cd "$1" && pwd); LABEL=$2
mkdir -p "$L/xfer"
git -C "$WT" ls-files -z next | tar -C "$WT" -czf "$L/xfer/src-$LABEL.tgz" --null -T -
ls -l "$L/xfer/src-$LABEL.tgz" | awk '{print $5, $9}'
