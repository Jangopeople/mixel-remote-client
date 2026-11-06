# Mixel Remote — new Rust workspace

The approved replacement is developed alongside the existing customer app.
Nothing in this directory is a published installer or a production replacement.
Existing RustDesk branding/build scripts and rs.mixel.ch remain independent.

## Implemented and runnable

- `core_protocol`: bounded canonical bincode input with borrowed UTF-8 text;
  signed ephemeral X25519, HKDF-SHA256 and directional AES-256-GCM records;
  session/identity/DTLS binding, replay/order checks and a bounded key lifetime;
  attended consent state; native pure-Rust WebRTC transport via pinned webrtc0.21.1.
  Actual authenticated TURN/UDP, TURN/TCP and TURN/TLS relay-only transports are
  exercised, including byte-identical H264 SRTP, encrypted controls, a256-message
  burst, IPv6 local sockets, certificate rejection, a non-responsive UDP relay,
  bounded queues and actual allocation release. TCP/TLS aliases never fall back
  to physical UDP. Platform TLS trust verifies chain, name and expiry. The pinned
  vendor patch preserves the first relayed RTP packet's SPS/PPS; see
  `vendor/webrtc/MIXEL-PATCHES.md`. Late ICE candidates remain available after a
  bounded gather deadline so a failed server cannot hide a usable candidate.
- `signaling_server`: Tokio/Axum WebSocket server; ISM GoTrue/profile/org adapter;
  same-tenant requests or MFA-authenticated internal staff; ordinary host-only
  Accept/Reject; bounded peers/queues/sessions; consent/negotiation deadlines;
  participant/SDP sequencing; disconnect and shutdown cleanup; exact web origins.
  Bearers travel in the first WebSocket frame, never URLs or logs. Connections
  expire with the bearer or after 15 minutes unless explicitly renewed through
  verified ISM authentication. Renewal preserves the same user, tenant, staff
  privilege, host ID and existing consent; account/tenant/privilege changes,
  invalid or expired credentials disconnect and clean up sessions. Renewals are
  rate-limited and cannot block past the current authorization deadline. The executable accepts loopback binds only for isolated staging.
- `web_client`: compiled WASM sharing the native authenticated control protocol;
  browser RTCPeerConnection/DataChannel creation; canvas video rendering and
  validated aspect-fit pointer scaling. Keys remain in Rust WASM memory.
- `native_os_capture`: validated bounded BGRA frames and tile damage detection,
  horizontal/vertical coalescing, fragmented/full-frame fallback, resize/reset,
  ignored row padding, and zeroized retained pixels. The macOS adapter now uses
  direct objc2 ScreenCaptureKit bindings, read-locked pixel copies, a latest-frame
  mailbox, explicit errors and bounded stop handling. Own-process window capture
  is exercised without capturing other apps; full-display access is preflighted
  and still requires normal OS screen-recording permission.
- `desktop_app`: Tauri v2 with strict TypeScript frontend, a native capture owner
  thread, binary frame IPC and live preview. Start/Stop/Restart and a changed
  source-window text have been observed in the actual native app. This is a
  local capture QA interface, not the finished remote-support dashboard.
  A separate attended viewer now runs ScreenCaptureKit → independently verified
  VideoToolbox hardware H264 → actual UDP ICE/DTLS-SRTP → WebCodecs canvas.
  Explicit Accept/Reject, fresh consent, source recreation, disconnect and viewer
  closure are exercised in the actual native release app. The isolated identities
  are process-local, pinned QA fixtures; this does not enroll a customer device.
- Hardware media: bounded AVCC/Annex-B conversion, explicit sRGB/BT709 metadata,
  a zero-reorder baseline SPS, bounded loss-aware RTP assembly and cancellable
  backpressure. Media grants are bound to the signed handshake and both actual
  transport certificates. A grant for another peer or role cannot share a screen.
  Predictive encoded frames are preserved; stale raw captures can skip before
  encoding. Independent FFmpeg decoding checks every frame and colour samples.
- Input ownership router: active input capabilities, view-only denial, per-session
  held key/button claims, independent-session cleanup, retained failed releases
  and retry. A native CoreGraphics input adapter is implemented for macOS with
  explicit event-post permission checks, USB physical key mapping, Unicode,
  modifiers, dragging/buttons and scrolling. Its live input delivery remains
  **unqualified**: the signed QA app currently lacks OS Accessibility permission.
  The initial attempted delivery failed independently observed readback and is
  preserved as rejected evidence. The corrected app refuses keyboard control
  while permission is absent; it offers an explicit local OS permission action.
  Windows/Linux input adapters and runtime held-key cleanup remain pending.

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
or `next/target/`. pnpm installs the pinned TypeScript frontend dependencies.
No hosted CI runner is required.

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
SIGINT shutdown and public-bind refusal. The desktop TypeScript build and frontend
async lifecycle regressions are included. Browser and native GUI qualification
stay separate. Package a private native release QA app with:

```sh
python3 scripts/local/package-next-macos-qa.py --output _scratch/native-qa-new
```

Independently qualify hardware media on a Mac with ffmpeg and ffprobe installed:

```sh
python3 scripts/local/test-next-native-media.py --output _scratch/native-media-new
```

This runs actual native peers, rejects pre-consent/foreign-certificate/controller
sharing grants, decodes all twelve received frames, validates twenty-four colour
samples, and verifies zero-reorder SPS metadata. It does not replace GUI or
customer-session acceptance or claim an end-to-end latency target.

The package defaults to a local ad-hoc development signature and a separate QA
bundle ID. `--signing-identity` can use an existing local Developer ID identity
with hardened runtime and timestamping. Its evidence records actual authority,
source conservation and executable hashes. Neither mode is notarized, store-signed
or a replacement for the installed customer app.
The capture fixture stays within the app's own process. Full-display capture and
input permissions are not silently requested or bypassed.

Serve the generated `web/` directory on loopback and click its test button in a
real browser. Its visible checks verify browser ICE/DTLS/SCTP, actual certificates,
WASM-encrypted Unicode controls/replay denial, and decoded video canvas pixels.
It uses no camera, microphone, desktop access or third-party ICE server.

## Remaining approved implementation and acceptance

Native DXGI/PipeWire/X11 capture and cross-platform capture qualification;
full-display macOS permission-path qualification; audio; native macOS input delivery
qualification and Windows/Linux input adapters/runtime held-key cleanup; Windows/Linux hardware H.264 and negotiated VP8 fallback; authenticated device
registration and renewable grants against existing ISM devices; persistent
nine-digit IDs and dynamic access password; unattended services and encrypted
credential storage; tray/session UX; the complete Tauri v2 remote-support dashboard/session flow;
complete browser input lifecycle and focus handling; dedicated staging STUN/TURN
UDP/TCP failover and restrictive-NAT tests; cross-platform customer-session QA;
enterprise/mobile/endpoint/AI parity; sandboxed macOS TestFlight controller and
separate signed host packaging. Empty crates/stub adapters are not added just to
claim the final layout or these features are delivered.

Marketplace creation/upload/submission must preserve existing live/reviewed
releases and follow the repository's exact-action confirmation rule. No store,
production infrastructure, customer alias or download has changed here.

The engineering feature inventory is tracked in `parity-baseline.json`. It is an initial, source-linked implementation baseline, not an exhaustive audit or a parity completion claim. The user prioritizes completing the feature build before the final acceptance campaign; build and lint checks continue during implementation.
