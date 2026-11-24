#!/bin/bash
# Mixel Remote pre-release regression gate (enforces 2.2.6+ audit criteria).
# A release with a red gate does not ship.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
NEXT="$ROOT/next"

echo "=== Gate 1: SDP H.264 High 4.2 (profile-level-id=64002a) ==="
grep -q 'profile-level-id=64002a' "$NEXT/crates/core_protocol/src/media.rs"

echo "=== Gate 2: W-1 vidle advertisement & 1 s idle heartbeat ==="
grep -q 'pub const VIDEO_IDLE_FEATURE: &str = "vidle"' "$NEXT/crates/mixel_engine/src/remote_control.rs"

echo "=== Gate 3: W-3 hardware passthrough recording (no forced mode=sw) ==="
! grep -q '!recording.as_ref().is_some_and(|scope| scope.is_recording())' "$NEXT/crates/mixel_engine/src/remote_peer.rs"
! grep -q '\.prefix("\.mixel-recording-")' "$NEXT/crates/mixel_engine/src/remote_recording.rs"

echo "=== Gate 4: Q-5b per-session perf log fields ==="
grep -q 'viewer session={} host={}' "$NEXT/crates/mixel_engine/src/remote_peer.rs"

echo "=== Gate 5: Rust core_protocol + mixel_engine (incl. W-1, W-2, W-4 tests) ==="
cd "$NEXT"
cargo test -p core_protocol --lib --quiet
cargo test -p mixel_engine --lib --quiet -- w1_viewer_liveness_never_kills_legacy_host_or_healthy_idle_host
cargo test -p mixel_engine --lib --quiet -- w2_2000_pointer_moves_and_50_clicks_never_busy
cargo test -p mixel_engine --lib --quiet -- w4_static_frame_suppresses_dither_and_detects_real_change

echo "=== Gate 6: macOS Audit226RegressionTests (W-3 unfocused Stop, N-10b 4-language popover) ==="
if [ -d "$NEXT/apps/mixel-remote-apple/Core/MixelCoreFFI.xcframework" ]; then
  cd "$NEXT/apps/mixel-remote-apple"
  rm -rf /tmp/mixel-gate-dd
  xcodebuild test -scheme MixelRemoteApple-Package -destination 'platform=macOS' -derivedDataPath /tmp/mixel-gate-dd -only-testing:MixelRemoteKitTests/Audit226RegressionTests -quiet
  rm -rf /tmp/mixel-gate-dd
fi

echo "=== ALL REGRESSION GATE CHECKS PASSED ==="
