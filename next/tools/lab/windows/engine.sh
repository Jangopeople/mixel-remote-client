#!/usr/bin/env bash
# engine.sh test|build|all [features]: packs the tracked sources of next/ (git ls-files, no build output), unpacks them on win-build
# under W:\MixelRemoteBuild\win-210\src and runs engine.ps1 there (engine tests + clippy, release core DLL). No secrets (ssh alias).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../../../.." && pwd)"
HOST="${WIN_HOST:-win-build}"; STEP="${1:-all}"; FEATURES="${2:-}"
ROOT='W:\MixelRemoteBuild\win-210'
tmp="$(mktemp -d)"
git -C "$HERE" ls-files -z next | COPYFILE_DISABLE=1 tar -C "$HERE" --no-xattrs -czf "$tmp/src.tgz" --null -T -
ssh "$HOST" "New-Item -ItemType Directory -Force -Path '$ROOT\\src','$ROOT\\logs' | Out-Null" >/dev/null
scp -q "$tmp/src.tgz" "$HOST:W:/MixelRemoteBuild/win-210/src.tgz"
scp -q "$(dirname "$0")/engine.ps1" "$HOST:W:/MixelRemoteBuild/win-210/engine.ps1"
ssh "$HOST" "tar -xzf '$ROOT\\src.tgz' -C '$ROOT\\src'; Remove-Item '$ROOT\\src.tgz'"
rm -rf "$tmp"
ssh "$HOST" "& '$ROOT\\engine.ps1' -Step $STEP -Features '$FEATURES'"
