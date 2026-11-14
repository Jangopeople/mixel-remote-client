# Pinned WebRTC dependency patch

Upstream crate: webrtc0.21.1, crates.io package SHA256 `b621e736302bef784948cc8d8099fd0a8fdb5757693c80a344ffbe38c49387d2`. Upstream Git revision `d658b5a803dd8dc389547450e245877310c9cbed`. Dual MIT/Apache licenses are preserved. Only the library source, README and licenses are vendored; normalized Cargo example/test/bench targets were omitted because those sources are not part of the app dependency.

The patch in `src/peer_connection/driver.rs` drains core events immediately after passing decapsulated TURN packets to the core, before draining media/data messages. Those reads can enqueue OnTrack/OnOpen after the driver's earlier event pass. Previously the first relayed RTP packet was dispatched before a receiver existed and was dropped; for H264 this removed SPS/PPS.

Regression: `core_protocol::stream_turn::tests` sends a single independently decoded generated H264 access unit through a real TCP/TLS TURN server and requires byte-identical SRTP reassembly including SPS/PPS. The unpatched registry crate failed with NALs6,5 instead of7,8,6,5 (920 instead of950bytes). Preserve this assertion when upgrading; remove this vendor override only after the upstream replacement passes it and the full workspace/network suites.

## UDP receive buffer (14 Nov 2026)

`src/peer_connection/driver.rs` asks for a 4 MiB receive buffer on every UDP socket it binds (`enlarge_receive_buffer`, `socket2` is now a normal dependency). A keyframe arrives as one burst; a Mac being helped sends full-HD keyframes of several hundred kilobytes and more. Windows keeps 64 KiB per UDP socket by default and drops the rest silently (no counter moves), so a Windows technician on a fast network never received a whole keyframe from a Mac, asked for a new one every second and lost the session after 12 s ("Remote video delivery timed out"; measured: 29,456 of about 31,000 packets missing).

Regression: `core_protocol::transport::receive_buffer_tests::a_keyframe_sized_burst_waiting_in_the_socket_is_not_dropped` (it matters most on Windows: `cargo test -p core_protocol receive_buffer`). Keep the call when upgrading the crate.

## Packets handed back for pacing (14 Nov 2026)

`src/media_stream/track_local/static_sample.rs`: `TrackLocalStaticSample::packetize_sample` does the first half of `write_sample` (same sequence numbers, timestamps and payload type) and returns the RTP packets; `write_sample` is now that plus the unchanged write loop. The host writes the packets itself with `TrackLocal::write_rtp`, spread over time (`mixel_engine::video_pacing`), instead of handing a whole keyframe to the socket in one burst. Nothing changes on the wire.

Regression: `core_protocol::stream_turn::tests` (byte-identical picture through `send`, which now runs through `packetize_sample`) and `mixel_engine::video_pacing::tests`. Keep the method when upgrading the crate.
