# Mixel Remote — new Rust workspace

The approved replacement is developed alongside the existing customer app.
Nothing in this directory is a published installer or a production replacement.
Existing RustDesk branding/build scripts and rs.mixel.ch remain independent.

## Implemented and runnable

- `core_protocol`: bounded canonical bincode input with borrowed UTF-8 text;
  signed ephemeral X25519, HKDF-SHA256 and directional AES-256-GCM records;
  session/identity/DTLS binding, replay/order checks and a bounded key lifetime;
  attended consent state; native pure-Rust WebRTC transport via webrtc 0.21.1.
- `signaling_server`: Tokio/Axum WebSocket server; ISM GoTrue/profile/org adapter;
  same-tenant requests or MFA-authenticated internal staff; ordinary host-only
  Accept/Reject; bounded peers/queues/sessions; consent/negotiation deadlines;
  participant/SDP sequencing; disconnect and shutdown cleanup; exact web origins.
  Bearers travel in the first WebSocket frame, never URLs or logs. Connections
  expire with the bearer or after 15 minutes; renewable authorization is not yet
  implemented. The executable accepts loopback binds only for isolated staging.
- `web_client`: compiled WASM sharing the native authenticated control protocol;
  browser RTCPeerConnection/DataChannel creation; canvas video rendering and
  validated aspect-fit pointer scaling. Keys remain in Rust WASM memory.
- `native_os_capture`: validated bounded BGRA frames and tile damage detection,
  horizontal/vertical coalescing, fragmented/full-frame fallback, resize/reset,
  ignored row padding, and zeroized retained pixels. OS capture/input adapters
  are not implemented in this crate yet.

The browser harness uses a generated video pattern sent through actual WebRTC;
it is explicitly **not** a native screen-capture or customer-session test.
Native end-to-end tests run an actual TCP/WebSocket server and real UDP WebRTC
peers, exchange signed identity/SDP after Accept, verify actual DTLS certificates,
round-trip encrypted input, disconnect, reconnect with fresh consent, and Reject.
Their identity service and pinned device identities are isolated test fixtures.
The ISM adapter is tested against local HTTP fixtures, not a live customer login.

## Trust boundary

Device identity keys supplied to `Context` / `ControlIdentity.begin` must come
from authenticated device enrollment or independently verified pairing. A key
received only through signaling is not a trust root. Actual remote DTLS leaf
certificates are hashed independently before `Handshake.finish`. An open
DataChannel alone grants no OS control. No unattended permission is inferred.
The missing enrollment/grant adapter prevents this workspace being release-ready.

## Local verification

Use Rust 1.99, Node, the wasm32-unknown-unknown target, and wasm-bindgen CLI
**0.2.130**. All generated output belongs under this repo's ignored `_scratch/`
or `next/target/`. No hosted CI runner is required.

From the repository root:

```sh
python3 scripts/local/test-next-workspace.py \
  --output _scratch/next-qa-run \
  --wasm-bindgen _scratch/next-tools/bin/wasm-bindgen
```

The evidence directory must be new. The driver records commands, exit codes,
logs, source conservation and artifact SHA-256 values. It runs fmt, native/WASM
Clippy with warnings denied, Rust tests (including actual network transports),
release builds, compiled WASM execution under Node, release binary health,
SIGINT shutdown and public-bind refusal. Browser qualification stays separate.

Serve the generated `web/` directory on loopback and click its test button in a
real browser. Its visible checks verify browser ICE/DTLS/SCTP, actual certificates,
WASM-encrypted Unicode controls/replay denial, and decoded video canvas pixels.
It uses no camera, microphone, desktop access or third-party ICE server.

## Remaining approved implementation and acceptance

Native DXGI/ScreenCaptureKit/PipeWire/X11 capture; audio; OS input and held-key
cleanup; hardware H.264 with negotiated VP8 fallback; authenticated device
registration and renewable grants against existing ISM devices; persistent
nine-digit IDs and dynamic access password; unattended services and encrypted
credential storage; tray/session UX; the Tauri v2 `desktop_app` crate/frontend;
complete browser input lifecycle and focus handling; dedicated staging STUN/TURN
UDP/TCP failover and restrictive-NAT tests; cross-platform customer-session QA;
enterprise/mobile/endpoint/AI parity; sandboxed macOS TestFlight controller and
separate signed host packaging. Empty crates/stub adapters are not added just to
claim the final layout or these features are delivered.

Marketplace creation/upload/submission must preserve existing live/reviewed
releases and follow the repository's exact-action confirmation rule. No store,
production infrastructure, customer alias or download has changed here.
