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

echo "=== Gate 10: 2.2.10 freezes (display kept on, every-pixel change check, in-place OpenH264 bit rate, direct-session renewal) ==="
cd "$NEXT"
cargo test -p mixel_engine --lib --quiet -- a_small_change_between_sampled_columns_is_not_static
cargo test -p mixel_engine --lib --quiet -- a_session_is_direct_only_when_the_selected_pair_has_no_relay_candidate
cargo test -p native_os_capture --lib --quiet -- the_bit_rate_changes_in_place_without_a_complete_picture
grep -q 'pub fn keep_display_on(' "$NEXT/crates/native_os_capture/src/windows_capture.rs"
grep -q 'keep_display_on(true)' "$NEXT/crates/mixel_engine/src/capture_worker.rs"

echo "=== Gate 11: 2.2.11 Windows health on unattended PCs, always-on program update guard ==="
cd "$NEXT"
cargo test -p mixel_engine --lib --quiet -- health_with_no_signed_in_user_parses
cargo test -p mixel_engine --lib --quiet -- only_an_x64_console_program_may_replace_the_service_program
grep -q 'is_console_program(&copied)' "$NEXT/crates/mixel_engine/src/windows_signin_host.rs"
grep -q 'user="$($c.UserName)"' "$NEXT/crates/mixel_engine/src/approved_scripts.rs"

echo "=== Gate 12: 2.2.12 lost video packets repaired, sessions survive network drops, sign-in kept across restarts ==="
cd "$NEXT"
cargo test -p mixel_engine --lib --quiet -- one_lost_picture_on_a_clean_line_keeps_the_pace_but_repeated_ones_step_back
cargo test -p mixel_engine --lib --quiet -- a_late_report_holds_the_gate_well_under_a_second_not_three
cargo test -p signaling_server --lib --quiet -- a_side_that_loses_its_connection_comes_back_into_the_same_session
cargo test -p signaling_server --lib --quiet -- a_device_back_on_a_new_connection_takes_over_its_silent_old_one
cargo test -p mixel_engine --lib --quiet -- a_rotated_refresh_token_is_kept_before_anything_else_can_fail
cargo test -p mixel_engine --lib --quiet -- a_restore_keeps_trying_after_the_quick_attempts

echo "=== Gate 13: 2.2.13 long-distance sessions (repair packets negotiated only with apps that know them, full-screen software encoding when it keeps up) ==="
cd "$NEXT"
# transport.rs only compiles with native-webrtc; without it the filter matches 0 tests.
cargo test -p core_protocol --features native-webrtc --lib --quiet -- the_repair_stream_is_negotiated_only_with_apps_that_know_it
cargo test -p mixel_engine --lib --quiet -- software_full_screen_follows_the_measured_cost
grep -q 'repair={}' "$NEXT/crates/mixel_engine/src/remote_peer.rs"

echo "=== Gate 14: 2.2.14 Windows host after TeamViewer (follows the display left, silent desktop duplication falls back to GDI, no 100 ms sleep per GDI copy), Quick Support without unattended ==="
cd "$NEXT"
cargo test -p mixel_engine --lib --quiet -- quick_support_never_asks_the_broker_for_unattended_access
grep -q 'pub fn follow<' "$NEXT/crates/native_os_capture/src/windows_capture.rs"
grep -q 'windows_capture::follow(&display, &current)' "$NEXT/crates/mixel_engine/src/capture_worker.rs"
grep -q 'duplication_stale = true' "$NEXT/crates/native_os_capture/src/windows_capture.rs"
grep -q 'GDI_ONLY_INTERVAL' "$NEXT/crates/native_os_capture/src/windows_capture.rs"
grep -q 'startup_host = matches!(auth, RegistrationAuth::Startup' "$NEXT/crates/mixel_engine/src/remote_session.rs"

echo "=== Gate 15: 2.2.15 Windows unattended restore after Quick Support sign-in, GDI software encoding stays 720p ==="
cd "$NEXT"
cargo test -p mixel_engine --lib --quiet -- gdi_software_encoding_stays_at_720p
grep -q 'next.Host != previous.Host' "$NEXT/apps/mixel-remote-windows/src/MixelRemote.Core/ViewModels/HostModel.cs"
grep -q 'Host.RestoreUnattended()' "$NEXT/apps/mixel-remote-windows/src/MixelRemote.Core/ViewModels/AppModel.cs"

echo "=== ALL REGRESSION GATE CHECKS PASSED ==="
