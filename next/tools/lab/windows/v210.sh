#!/usr/bin/env bash
# v210.sh <step...>: sync the tracked sources of this worktree (plus local edits) to win-build (W:\MixelRemoteBuild\v210-win) and run build steps of v210.ps1
# (core|corelab|app|applab|pack|packlab|all|sync). Own folder and target dirs. No secrets (ssh alias).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../../../.." && pwd)"; HOST="${WIN_HOST:-win-build}"; ROOT='W:\MixelRemoteBuild\v210-win'
tmp="$(mktemp -d)"
( cd "$HERE"; { git ls-files -z next; git ls-files -z --others --exclude-standard next; } | grep -zv '^next/tools/lab/xfer' | COPYFILE_DISABLE=1 tar --no-xattrs -czf "$tmp/src.tgz" --null -T - )
ssh "$HOST" "New-Item -ItemType Directory -Force -Path '$ROOT\\src','$ROOT\\logs' | Out-Null" >/dev/null
scp -q "$tmp/src.tgz" "$HOST:W:/MixelRemoteBuild/v210-win/src.tgz"
scp -q "$(dirname "$0")/v210.ps1" "$HOST:W:/MixelRemoteBuild/v210-win/v210.ps1"
ssh "$HOST" "tar -xzf '$ROOT\\src.tgz' -C '$ROOT\\src'; Remove-Item '$ROOT\\src.tgz'"; rm -rf "$tmp"
for s in "$@"; do [ "$s" = sync ] || ssh "$HOST" "powershell -NoProfile -ExecutionPolicy Bypass -File '$ROOT\\v210.ps1' $s"; done
