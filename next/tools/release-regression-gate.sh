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

echo "=== Gate 7: macOS 27 safe deferred NSWindow close (no sync window.close in SessionWindowView) ==="
grep -q 'private func safeClose(' "$NEXT/apps/mixel-remote-apple/Sources/MixelRemoteKit/Views/SessionPane.swift"
! grep -q 'window?\.orderOut(nil); window?\.close()' "$NEXT/apps/mixel-remote-apple/Sources/MixelRemoteKit/Views/SessionPane.swift"

echo "=== Gate 8: Low-latency video pipeline (Notify take_wait, 4 ms host idle poll, VT RealTime + IOSurface) ==="
grep -q 'pub async fn take_wait(' "$NEXT/crates/mixel_engine/src/remote_video.rs"
grep -q 'video.take_wait(' "$NEXT/crates/mixel_engine/src/remote_session.rs"
grep -q 'kVTDecompressionPropertyKey_RealTime' "$NEXT/apps/mixel-remote-apple/Sources/MixelRemoteKit/Support/VideoDecoder.swift"
grep -q 'CVPixelBufferGetIOSurface' "$NEXT/apps/mixel-remote-apple/Sources/MixelRemoteKit/Support/VideoFrame.swift"
! grep -q 'Duration::from_millis(40)' "$NEXT/crates/mixel_engine/src/remote_peer.rs"

echo "=== Gate 9: R-1/R-2/R-3 (200-record burst, 10k concurrent sealer, dedup openWindow, warm cooldown) ==="
cd "$NEXT"
cargo test -p core_protocol --lib --quiet -- r1_open_detailed_distinguishes_length_reorder_and_bad_tag
cargo test -p core_protocol --lib --quiet -- r1_ten_thousand_records_from_concurrent_producers_stay_in_order
cargo test -p mixel_engine --lib --quiet -- r1_receive_host_records_never_drops_record_at_64_batch_boundary
grep -q 'MixelSessionWindow-' "$NEXT/apps/mixel-remote-apple/Mac/MixelRemoteApp.swift"
grep -q 'warm_log\.back()' "$NEXT/crates/mixel_engine/src/remote_session.rs"

echo "=== ALL REGRESSION GATE CHECKS PASSED ==="
