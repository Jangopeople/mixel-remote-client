#!/usr/bin/env bash
# sync.sh: copies the Windows app sources to the build VM "win-build" and runs one step there. No secrets: ssh uses the
# host alias and key already set up on this Mac (~/.ssh/config).
#
#   next/tools/lab/windows/sync.sh test            # dotnet test of MixelRemote.Core.Tests
#   next/tools/lab/windows/sync.sh build           # dotnet build of MixelRemote.App (Release, x64)
#   next/tools/lab/windows/sync.sh all             # both
#   next/tools/lab/windows/sync.sh sync            # copy only
#
# The copy goes to W:\MixelRemoteBuild\<WIN_WORK> (default win-210), next/apps/mixel-remote-windows inside it; bin and obj are
# not copied, so the VM keeps its own build output (fast rebuilds). Output of each step is also kept in <WIN_WORK>\logs.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../../../apps/mixel-remote-windows" && pwd)"
WORK="${WIN_WORK:-win-210}"
HOST="${WIN_HOST:-win-build}"
STEP="${1:-all}"
ROOT="W:\\MixelRemoteBuild\\$WORK"
APP="$ROOT\\src\\next\\apps\\mixel-remote-windows"

sync_sources() {
  local tmp
  tmp="$(mktemp -d)"
  COPYFILE_DISABLE=1 tar --no-xattrs --exclude='._*' --exclude=bin --exclude=obj --exclude=.vs --exclude=artifacts -czf "$tmp/app.tgz" -C "$HERE" .
  ssh "$HOST" "New-Item -ItemType Directory -Force -Path '$APP','$ROOT\\logs' | Out-Null" >/dev/null
  scp -q "$tmp/app.tgz" "$HOST:W:/MixelRemoteBuild/$WORK/app.tgz"
  # Remove sources that are gone here (renamed or deleted files), then unpack.
  ssh "$HOST" "Get-ChildItem -Recurse -Include *.cs,*.xaml,._* '$APP\\src','$APP\\tests' -ErrorAction SilentlyContinue | Where-Object { \$_.FullName -notmatch '\\\\(bin|obj)\\\\' } | Remove-Item -Force; tar -xzf '$ROOT\\app.tgz' -C '$APP'; Remove-Item '$ROOT\\app.tgz'"
  rm -rf "$tmp"
}

run_test() {
  ssh "$HOST" "cd '$APP'; dotnet test tests\\MixelRemote.Core.Tests\\MixelRemote.Core.Tests.csproj -c Release --nologo 2>&1 | Tee-Object -FilePath '$ROOT\\logs\\test.log' | Select-Object -Last ${TAIL:-30}"
}

run_build() {
  ssh "$HOST" "cd '$APP'; dotnet build src\\MixelRemote.App -c Release -p:Platform=x64 --nologo 2>&1 | Tee-Object -FilePath '$ROOT\\logs\\build.log' | Select-Object -Last ${TAIL:-30}"
}

case "$STEP" in
  sync) sync_sources ;;
  test) sync_sources; run_test ;;
  build) sync_sources; run_build ;;
  all) sync_sources; run_test; run_build ;;
  *) echo "usage: $0 sync|test|build|all" >&2; exit 2 ;;
esac
