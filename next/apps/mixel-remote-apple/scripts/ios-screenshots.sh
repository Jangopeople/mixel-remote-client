#!/bin/bash
# ios-screenshots.sh <simulator-udid> <out-dir>: every iPhone screen of the real app (Debug build, scripted
# demo states) in light and dark, from a headless Simulator (no Simulator window is opened).
set -euo pipefail
SIM=$1; OUT=$2; HERE=$(cd "$(dirname "$0")/.." && pwd)
DD=${DERIVED_DATA:-$HOME/Library/Developer/Xcode/DerivedData/mixel-remote-native}
cd "$HERE" && xcodegen generate >/dev/null
xcodebuild -project MixelRemoteApple.xcodeproj -scheme MixelRemoteiOS -configuration Debug -derivedDataPath "$DD" -destination "id=$SIM" build CODE_SIGNING_ALLOWED=NO -quiet
APP="$DD/Build/Products/Debug-iphonesimulator/Mixel Remote.app"
xcrun simctl boot "$SIM" 2>/dev/null || true
xcrun simctl bootstatus "$SIM" -b >/dev/null
xcrun simctl install "$SIM" "$APP"
mkdir -p "$OUT"
for screen in signed-out second-step devices connect setup account session chat; do
  for look in light dark; do
    xcrun simctl ui "$SIM" appearance $look
    xcrun simctl launch --terminate-running-process "$SIM" ch.mixel.remote.controller -MixelDemo $screen >/dev/null
    sleep 4
    xcrun simctl io "$SIM" screenshot --type=png "$OUT/iphone-$screen-$look.png" >/dev/null 2>&1
  done
done
xcrun simctl terminate "$SIM" ch.mixel.remote.controller || true
echo "$(ls "$OUT" | wc -l | tr -d ' ') screenshots in $OUT"
