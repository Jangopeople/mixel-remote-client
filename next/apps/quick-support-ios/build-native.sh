#!/bin/bash
# Builds the host core (crates/quick_host) for iPhone and the simulator and packs it as QuickHost.xcframework.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../.."
for target in aarch64-apple-ios aarch64-apple-ios-sim; do
  IPHONEOS_DEPLOYMENT_TARGET=15.0 cargo build -p quick_host --lib --release --target "$target"
done
rm -rf "$here/QuickHost.xcframework"
xcodebuild -create-xcframework \
  -library target/aarch64-apple-ios/release/libquick_host.a -headers "$here/QuickHost" \
  -library target/aarch64-apple-ios-sim/release/libquick_host.a -headers "$here/QuickHost" \
  -output "$here/QuickHost.xcframework" | tail -1
du -sh "$here/QuickHost.xcframework" | cut -f1
