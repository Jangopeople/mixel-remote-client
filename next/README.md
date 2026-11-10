# Mixel Remote — new Rust workspace

The approved replacement is developed alongside the existing customer app.
Nothing in this directory is a published installer or a production replacement.
Existing RustDesk branding/build scripts and rs.mixel.ch remain independent.

## Current Mac-first build (2026-11-06)

The new Mac dashboard now contains native browser/PKCE Mixel sign-in, enrolled
nine-digit IDs, attended requests and source-specific Accept, signed device
identity tickets, actual WebRTC H264 media, native VideoToolbox decoding, selected
display keyboard/mouse control, tray Stop/Offline/Quit, and explicit text clipboard
transfers. These additions compile; the current customer path is **not runtime
qualified**. They are not a claim of complete TeamViewer portfolio parity.
Windows and Linux launch the managed workspace. They share the native Mixel ID,
permanent device registration, consent, and authenticated session engine with
macOS. Windows uses DXGI with a selected-display fallback on the ordinary
signed-in desktop. Linux hosting supports X11 and attended Wayland sharing.
On Wayland, accepting a request opens the compositor's display chooser.
Interactive sessions also require the compositor to grant keyboard and pointer
access. This grant is scoped to one session. The host uses the PipeWire
GStreamer plugin and the desktop's ScreenCast and RemoteDesktop portals.
The lab verified display pixels, keyboard and mouse input, and session cleanup
on Windows, X11 and GNOME Wayland with pinned test identities. Windows and X11
also verified file transfer and MP4 recording. Public Mixel ID and broker
qualification and native startup-host services are separate release gates.
Older verification below applies to its recorded checkpoint.

The Mac device directory now lists only enrolled records available to the
current ISM bearer through existing RLS, with name search, stable cursor pages
and an explicit Connect action. It does not equate enrollment with online
presence. Directory data and pending replies are cleared on sign-out/account
changes; native commands recheck account scope after requests.

Mac setup now lets an MFA-verified Mixel team member find an existing ISM device
record and explicitly enroll this Mac with its OS-protected key. The add-only
`remote_enroll_device` RPC never replaces a conflicting key; native code verifies
the returned row and independently reads back the persisted enrollment before
reporting a connection ID. Enrollment is disabled while registered/connected,
and a sign-out/expiry cancellation guard prevents a pending setup from starting
its write after protected account access is revoked. No production record has
been enrolled as part of implementation. This flow still needs isolated runtime
qualification and the staging migration/service configuration.

Clipboard access is an independent host checkbox, off by default for every
invitation. Send/Get actions operate on native plain-text clipboards, up to 1 MiB;
there is no background watcher, HTML/file clipboard import or clipboard-content
webview IPC. The encrypted protocol requires a bounded ordered transaction,
length and SHA256 verification before replacement. A denied/unsupported clipboard
format reports an operation error without treating it as permission to inject
keystrokes. Stop and account/peer expiry revoke future clipboard operations.

The connection broker accepts only device-bound tickets signed by an independent
identity authority. Full ISM bearer tokens go only to native ISM calls and the
trusted authority, never to the broker. The authority verifies the current ISM
profile/organization/MFA/enrollment and a fresh device private-key proof. Signed
public peer claims let customers verify internal technicians without exposing a
global enrollment directory. Accepted media handshakes bind both exact tickets,
room ID, server nonce, enrolled keys and actual DTLS certificates. Renewal cannot
switch the live account, enrollment key/generation or privileges.

Bundled `service-config.json` uses schema 2 with `identity_url`, public anonymous
`public_key`, `signaling_url`, HTTPS-origin `authority_url`, and `authority_keys`
(one to four strong Ed25519 public keys, 64 hexadecimal characters each). Optional
fields are `oauth_provider` (default `keycloak`), `sso_domain` and `relay_only`.
Identity/signing private keys and ISM service-role keys must never be bundled.
The authority runs as the separate `identity_authority` binary with a service-user
owned mode-0600 secret file selected by `MIXEL_PEER_AUTHORITY_KEY_FILE`; the broker
receives only `MIXEL_PEER_AUTHORITY_PUBLIC_KEYS`. Keep signer and broker processes,
secret mounts and TLS routes isolated. The authority binds loopback8093 by default;
the broker binds loopback8092. No authority key, staging configuration or production
routes have been provisioned by this source change.

The Mac file-transfer path is now wired through a separate ordered reliable
`mixel-files-v1` channel with independently derived directional encryption keys.
The host file checkbox defaults off on every invitation. Both peers explicitly
choose outgoing files through the native Open panel; every incoming file waits
for a local Save choice. Paths never enter the webview or network. One bounded
transfer in each direction uses an eight-chunk ACK window. A transport renewal
resumes only the same local consent and enrolled peer, and only from a receiver's
actual retained prefix whose offset and SHA256 match a transmitted checkpoint.
Private partial files publish through an exclusive no-overwrite rename after
hashing the actual synced file bytes. Completed receipts are bounded and allow a
lost final ACK to be recovered without rewriting a saved document. Stop/account
expiry cancel open pickers and disk operations; failed private-file cleanup
retains its native capability and postpones Quit. This implementation and its
new disk/rotation regression cases passed in the native Mac suite. Those
checks use real disk bytes and native owner threads with synthetic peers; actual
customer file transfer and a real screen session remain unqualified.

Mac multi-click packets preserve the controller's browser/OS click count through
input ownership and CoreGraphics `MouseEventClickState`; host receipt latency is
not used to infer a double click. Fractional trackpad scrolling accumulates before
sending wheel steps. The controller command-queue and ownership regressions passed. Actual
remote desktop double-click/scroll behavior remains unqualified.

Temporary signaling I/O loss, abnormal TCP reset, maintenance close and
502–504 responses use bounded exponential reconnect delays with jitter. TLS,
protocol, policy, account and enrollment failures stop. Each attempt revalidates
the current account, enrollment and scoped identity ticket; old native owners
must finish cleanup, old frames and queued commands are discarded, and no old
session/Accept grant is replayed. A new connection requires fresh Accept. The
retry budget resets only after at least 60 seconds of actual authenticated
registration. This recovery implementation and its failure-classification/backoff
cases passed in the native Mac suite; a real service outage/reconnect flow
remains unqualified.

Remaining Mac delivery work includes audio, quality/codec options,
unattended access, network-loss recovery, connected service configuration, and
final independent customer acceptance. Scheduled transport replacement now
refreshes TURN credentials and ECDH/DTLS keys before relay or native media grant
expiry, retaining the same accepted devices, accounts, source and grants. It
uses fresh signed identity claims and a new room binding, stops and joins the
old media owners, discards late retired-room ICE/Close traffic, and preserves
the original eight-hour hard limit. The controller also has an explicit Renew
connection action. This path compiles but is not yet runtime qualified. Signed QA packages are isolated and do not replace the
installed customer app; TestFlight/store qualification remains separate.

## Earlier implemented and verified checkpoints

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
The new native enrollment and delegated-grant adapters are built but still need
connected staging configuration and independent customer acceptance; this
workspace is not release-ready.

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
