# Mixel Remote client

Mixel Remote is Mixel’s branded remote-support desktop client, built from pinned
RustDesk 1.4.6. This repository contains the branding, attended-support patches,
and build/release pipeline. Upstream source is a gitignored build dependency.

Read `HANDOFF.md` and `AGENTS.md` before working on releases. Product identity is
`Mixel Remote` / `Mixel-Remote`, bundle ID `ch.mixel.remote`, and the configured
rendezvous/relay is `rs.mixel.ch`. `branding/branding.env` is authoritative.

## Attended customer support

Customers launch the Windows `Mixel-Remote-QS.exe` download or open an invitation
using the registered `mixel-remote://support` protocol. The customer window must
stay visible on cold launch and return to the foreground on a warm launch.
Support links announce the customer’s own device to the support service; the
technician still needs the customer’s explicit Accept for each connection.

The handoff waits for a stable device ID, online relay registration, confirmed
server key, and an attested incoming-service consent guard before announcing
readiness. macOS additionally requires Screen Recording and Accessibility; the
existing permission cards guide the customer through those OS grants. Heartbeats
retry bounded startup/network failures, use separate request nonces, and stop
when an invitation is revoked, expired, or superseded. Invite bearer data is
excluded from diagnostic logs. The consent guard is runtime state and does not
change saved login preferences.

The foreground customer app owns a process lease that survives an incoming
service restart. Multiple foreground owners are supported; the last owner's
exit releases that lease. Readiness requires `attended-runtime-v2`, so an older
installed component must be updated before a new client announces support ready.

Pinned ID/relay sessions reject missing or invalid peer identity and encryption
handshakes. They must never downgrade to an unencrypted session after a key or
protocol error. Intentional direct IP/LAN connections retain upstream behavior.

Normal networks use native transport first. When Mixel's native registration or
TCP ports fail, clients retry the exact `rs.mixel.ch` relay over certificate-verified
HTTPS on port 443. The fallback is runtime state and preserves saved server/proxy
settings. It cannot bypass an organization's explicit proxy or application policy.

Windows QuickSupport double-click launches also maintain the runtime consent
guard. The published Windows support aliases, including browser-added positive
copy numbers, select attended mode by executable basename. A parent folder
containing a QuickSupport marker does not change an ordinary app's mode.
Launching QuickSupport while an ordinary Mixel window is open restores
that window and delivers the exact QuickSupport flag through the pinned native
link dispatcher. The incoming service acknowledges its temporary consent guard
before the short-lived launcher exits; the foreground window then owns and
renews the process lease. The Windows test exercises this ordinary-to-attended
transition for 95 seconds, beyond the temporary guard's 90-second lifetime,
and verifies the original GUI's HWND, IPC server PID and kernel event ownership.
Linux uses a Mixel-specific DBus name so installed stock RustDesk cannot
consume a Mixel invitation. Its native DBus receiver takes its own consent
lease before queuing a valid support invitation to Dart, so the launching
process can exit without leaving an asynchronous handoff interval. A failed
guard or unavailable Dart receiver returns a non-success acknowledgment so
the launching process starts and retains its own attended window. A cold Linux
invitation starts the incoming service.
Linux packages explicitly install the OpenGL/EGL/GLES libraries and software
renderer. A valid nonroot X11 desktop without `logind` can display customer Accept
and resolve the account for file transfer instead of waiting forever; root and
genuine login-screen contexts remain guarded. A local override of the exact locked
input dependency preserves active X11 keyboard capture when signals interrupt
event polling. The override rejects revision drift and unrelated modifications.
For Mixel's automatic WSS registration, a 20-second received-frame deadline
detects a stale connection even when the socket has not closed. EOF, malformed
responses, send/read errors and expiry clear the runtime online/key-confirmation
state before retrying. The stored identity, relay pin and network preferences
are preserved. The 20-second deadline is a receive-freshness policy, not an
end-to-end recovery guarantee; connection and send operations have their own
bounded deadlines.

The Linux executable also installs broken-pipe handling before entering its Rust
library, so a disconnected native writer returns an I/O error rather than killing
the incoming support process. Other signal policies remain unchanged.

Keep-awake requests are serialized with a bounded pending queue. An unavailable
desktop screensaver provider cannot produce an unhandled session error or mark
the display as successfully inhibited. A later lifecycle request retries a failed
acquisition or release. Keeping the display awake still requires OS backend support.

Mixel clients must never update from the stock RustDesk update manifest. Native
and UI updater entry points are disabled for custom Mixel clients; releases come
from Mixel’s signed downloads or the platform store’s normal update mechanism.

## Source and packaging

- `scripts/apply-branding.sh` patches a fresh checkout and verifies identity,
  native library loader names, protocol registrations, and relay defaults.
- `scripts/patch-support-invite.py` generates the native/Dart attended handoff.
- `scripts/support-invite-reporter.dart` and `scripts/support-invite-guard.rs`
  contain the tested shared reporter and consent implementation.
- Windows builds sign the actual desktop EXEs and DLLs **before** embedding them
  in the portable wrapper, then sign the wrapper. Store packaging uses the full
  signed desktop payload, including Flutter assets, rather than the wrapper.
- macOS signs and notarizes/staples the app first, then its DMG; verification
  checks bundle identity, architecture, signatures, and Gatekeeper acceptance.
  Notarization uploads each artifact once, then retries bounded status waits
  against that exact submission ID. Rejections fail before stapling; JSON status
  evidence is retained even when Apple's service times out.
- Apple Silicon build and bundle metadata require macOS 12.3, matching the native
  core and service. Intel retains macOS 10.14, with explicit availability guards
  around the newer permission APIs. The Mac native build explicitly links the
  target compiler's availability runtime, including the pinned Rust1.81 final link.
  Same-name vcpkg overlay triplets set codec deployment targets before compilation;
  separate cache versions prevent reuse of newer-OS codec objects. Every native
  archive member must match its CPU architecture, and every encoded macOS minimum
  must fit the app's advertised minimum. Members without encoded OS requirements
  are recorded explicitly. Pinned source validation rejects inconsistent
  architecture targets and upstream drift before modifying the checkout.
- Linux produces a branded Debian package and verifies its actual cold/warm
  customer launch under an isolated X11/DBus desktop.

Internal Cargo/library symbols needed for upstream linking remain intentional.
Do not commit `rustdesk/`, build output, credentials, or private relay keys.

## Verification

PR preflight validates Python, release configuration/staging, the real pinned
source patch and its idempotence, Dart analysis/behavior, generated Rust updater
and startup branches, and Windows payload/framed-IPC behavior. Platform builds
also test the compiled common-crate guard and execute the customer launch path.

With a fresh pinned checkout (including submodules) at `rustdesk/`, run:

```sh
python3 scripts/test-support-invite.py
python3 scripts/test-generated-support-paths.py
python3 scripts/test-support-network.py
python3 scripts/test-secure-support.py
python3 scripts/test-support-linux.py
python3 scripts/test-support-signals.py
python3 scripts/test-support-input.py
python3 scripts/test-support-clipboard.py
python3 scripts/test-support-video.py
python3 scripts/test-support-macos.py
python3 scripts/test-support-wakelock.py
python3 scripts/test-support-lease.py
dart analyze scripts/support-invite-reporter.dart scripts/test-support-invite.dart
dart scripts/test-support-invite.dart
python3 scripts/test-support-macos-uri-routing.py
python3 scripts/test-release-pipeline.py
python3 scripts/test-notarize-macos.py
python3 scripts/test-macos-native-minimum.py
python3 scripts/test-branding.py
python3 scripts/test-support-launch-linux.py --self-test
python3 scripts/test-support-session-fixture.py
pwsh -NoProfile -File scripts/test-windows-pipeline.ps1
python3 -m compileall -q scripts
bash -n scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh
shellcheck scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh
actionlint
```

Set `RDREPO` for another upstream checkout and `DART_BIN` for a Dart executable
outside PATH. On macOS, `test-support-macos.py` requires the build's Rust1.81
compiler; set `MAC_RUSTC_BIN` if it is outside PATH. `MACOS_RUNTIME_LINK_TARGET`
selects `x86_64-apple-darwin` or `aarch64-apple-darwin` for its actual final-link
regression. The video control test needs actual libaom headers from the pinned
vcpkg installation or `AOM_INCLUDE_DIR`; it executes the generated Rust caller
against a C variadic receiver and rejects the original floating-point tile argument.
The real desktop smoke needs its target OS and compiled bundle;
source regression tests do not establish screen/video/input behavior on a remote
customer device. macOS permission grants remain controlled by the customer’s OS.

On Linux, `python3 scripts/test-support-clipboard.py --native-x11` additionally
executes the exact pinned clipboard listener against a disposable X server.
It verifies rapid ownership changes, selection filtering, callback errors and
shutdown. This requires Cargo, Xvfb and xclip. The local Cargo overrides preserve
the upstream locked versions and dependency edges; they fix interrupted input
polling and missed clipboard events without updating unrelated dependencies.

The Linux build also runs two real isolated desktops with the exact packaged
installer. The native test uses the live Mixel relay. Tests require customer
Accept, compare decoded video including a changing current frame marker, observe actual host
keyboard and mouse callbacks, transfer a synthetic file in both directions, and
recover from incoming-service restart and network loss. A second run creates a
disposable copy of the pinned relay with the registration gateway, blocks native
ports, and requests ordinary ID connections without the manual relay option.
It verifies automatic certificate-verified HTTPS on port 443 using an explicitly
recorded fixture CA and public relay pin. Initial, service-restart and network
recovery checks verify the actual incoming-process socket ownership and relay
endpoint. Network recovery requires a newly observed local/peer registration
socket tuple from the same incoming PID, followed by stable guarded readiness;
an old established socket or cached online value cannot satisfy that check.
File-transfer checks select the exact rendered filename and compare
the uploaded and downloaded bytes by SHA256. Process exit statuses, guarded
kernel ownership and memory diagnostics accompany recovery failures. The fixture
does not assert that the gateway has been deployed to the live server. Failure
stops the build; screenshots, native logs and the installer SHA256 remain in the
`linux-support-session-proof` artifact.

A third run preserves the customer's native UDP registration while blocking
its TCP ports, and preserves the technician's TCP while blocking its UDP.
It verifies ordinary ID and file-transfer sessions without a manual relay flag,
including a late switch to HTTPS inside the incoming request path.

The isolated HTTPS regression is also runnable with
`python3 scripts/test-support-session-https-linux.py --deb <installer> --proofs <new-directory>`.
Its ownership checks and cleanup protect unrelated Docker resources. See
[the gateway deployment and rollback scope](infra/relay-ws-bridge/README.md)
before any production change.

The separate native saved-password consent test first proves that a valid
permanent password automatically authorizes the ordinary app with changing video.
After a warm attended-support URI, it verifies the same original GUI and kernel
lease, keeps the same password unauthorized for at least twelve seconds, then
requires an actual customer Accept click and two changing current video frames.
It also verifies encrypted HTTPS, unchanged saved access preferences and customer
Disconnect. The synthetic password exists only in disposable peer HOME directories.
It never authorizes a session by writing IPC state.

For an existing build, collect its exact artifact before running that native case:

```bash
python3 scripts/collect-linux-runtime-artifact.py --run-id <build-run-id> --output <new-artifact-root>
python3 scripts/test-support-session-https-linux.py \
  --deb <new-artifact-root>/artifacts/linux/<installer.deb> \
  --proofs <new-proof-directory> --require-native --saved-password-consent \
  --artifact-root <new-artifact-root> --artifact-run-id <build-run-id> \
  --artifact-source-commit <full-application-source-sha> --expected-sha256 <installer-sha256>
```

The collector verifies the repository, dispatched workflow, run/source, exact
artifact ID, GitHub archive digest and canonical DEB bytes. The session manifest
records application source separately from the current QA source. A failed
overall multi-platform build remains failed even when its Linux artifact passes
these scoped tests. The saved-password case requires a native amd64 host and a
complete isolated TLS fixture; emulated execution is rejected.

## Build and release

Use `build.yml` workflow dispatch on a review branch with `publish_r2=false` to
build verified artifacts without changing public downloads. `only` accepts an
exact comma list of `linux,macos,windows`; empty selects all. `upstream_version`
must be a valid explicit release tag. The default remains the branding version.

After verification and the required release approval, publication can use a
release tag or workflow dispatch with `publish_r2=true`. Publication fails on
missing signing/notarization/upload credentials, an incomplete selected build,
or an upload error. Partial releases preserve unselected platform aliases. Every
selected artifact is validated before staging customer aliases; canonical files
upload before aliases, and `release-manifest.json` records exact SHA256 digests.

Customer aliases:

- `Mixel-Remote-QS.exe` — Windows attended QuickSupport.
- `Mixel-Remote-Support-Windows.exe` and `Mixel-Remote-Support.exe` — the same Windows attended QuickSupport download.
- `Mixel-Remote-Support-Apple-Silicon.dmg` and `Mixel-Remote-Support-Intel.dmg`.
- `Mixel-Remote-Support.deb` — Linux.
- `Mixel-Remote-Support.msi` — only when the current build actually produced MSI.

Bump the `/remote-support` download cache-busters in `mixel-ism` when aliases are
published. Store updates require a new package version and the normal submission
path, preserving approved/live/review submissions. Never alter marketplace
availability or review state without Michael’s exact written confirmation.

Required GitHub secrets are `APPLE_CERT_P12_BASE64`, `APPLE_CERT_P12_PASSWORD`,
`APPLE_NOTARY_USER`, `APPLE_NOTARY_PASSWORD`, `APPLE_NOTARY_TEAM_ID`,
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
`CLOUDFLARE_API_TOKEN`, and `CLOUDFLARE_ACCOUNT_ID`. macOS artifact-only builds may
be unsigned when Apple secrets are absent; such builds cannot be published.

`runtime_artifact_run` runs an artifact-only Windows launch diagnostic against a
previous signed Store payload artifact. `runtime_macos_artifact_run` verifies and
launches both signed/notarized Mac installers. `runtime_linux_artifact_run` runs
the native/HTTPS/mixed two-desktop session tests and separate saved-password
consent case against an exactly collected Linux artifact.
All diagnostic inputs require `publish_r2=false` and a positive build run ID.
The Windows crash diagnostic creates a disposable standard-account fixture in
CI, verifies a controlled native exception, and retains only PID-bound stack
symbols. It restores temporary desktop/registry settings and removes the owned
account, profile, payload copy and private debugger files. Customer acceptance
tests run with ordinary process creation and no debugger.
The local macOS signing helper `scripts/local/sign-and-publish-macos.sh` is a publication
action and must only be invoked for an authorized release.

## Deployment boundaries

The relay is `rs.mixel.ch`; `remote.mixel.ch` is a separate MeshCentral product.
Live relay identity changes, production data changes, and store submissions are
separate release decisions. The related `mixel-ism/infra/rustdesk/README.md`
records the prepared approval-required migration for the historically exposed
relay identity; do not silently rotate it and break existing customer clients.

The product remains self-hosted without a recurring RustDesk Server Pro license.
