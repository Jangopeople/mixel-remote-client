# Pinned WebRTC dependency patch

Upstream crate: webrtc0.21.1, crates.io package SHA256 `b621e736302bef784948cc8d8099fd0a8fdb5757693c80a344ffbe38c49387d2`. Upstream Git revision `d658b5a803dd8dc389547450e245877310c9cbed`. Dual MIT/Apache licenses are preserved. Only the library source, README and licenses are vendored; normalized Cargo example/test/bench targets were omitted because those sources are not part of the app dependency.

The patch in `src/peer_connection/driver.rs` drains core events immediately after passing decapsulated TURN packets to the core, before draining media/data messages. Those reads can enqueue OnTrack/OnOpen after the driver's earlier event pass. Previously the first relayed RTP packet was dispatched before a receiver existed and was dropped; for H264 this removed SPS/PPS.

Regression: `core_protocol::stream_turn::tests` sends a single independently decoded generated H264 access unit through a real TCP/TLS TURN server and requires byte-identical SRTP reassembly including SPS/PPS. The unpatched registry crate failed with NALs6,5 instead of7,8,6,5 (920 instead of950bytes). Preserve this assertion when upgrading; remove this vendor override only after the upstream replacement passes it and the full workspace/network suites.
