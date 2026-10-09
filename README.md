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
guard. Linux uses a Mixel-specific DBus name so installed stock RustDesk cannot
consume a Mixel invitation. A cold Linux invitation starts the incoming service.
Linux packages explicitly install the OpenGL/EGL/GLES libraries and software
renderer. A valid nonroot X11 desktop without `logind` can display customer Accept
and resolve the account for file transfer instead of waiting forever; root and
genuine login-screen contexts remain guarded. A local override of the exact locked
input dependency preserves active X11 keyboard capture when signals interrupt
event polling. The override rejects revision drift and unrelated modifications.

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
python3 scripts/test-support-input.py
python3 scripts/test-support-clipboard.py
python3 scripts/test-support-lease.py
dart analyze scripts/support-invite-reporter.dart scripts/test-support-invite.dart
dart scripts/test-support-invite.dart
python3 scripts/test-release-pipeline.py
python3 scripts/test-branding.py
python3 scripts/test-support-launch-linux.py --self-test
pwsh -NoProfile -File scripts/test-windows-pipeline.ps1
python3 -m compileall -q scripts
bash -n scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh
shellcheck scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh
actionlint
```

Set `RDREPO` for another upstream checkout and `DART_BIN` for a Dart executable
outside PATH. The real desktop smoke needs its target OS and compiled bundle;
source regression tests do not establish screen/video/input behavior on a remote
customer device. macOS permission grants remain controlled by the customer’s OS.

On Linux, `python3 scripts/test-support-clipboard.py --native-x11` additionally
executes the exact pinned clipboard listener against a disposable X server.
It verifies rapid ownership changes, selection filtering, callback errors and
shutdown. This requires Cargo, Xvfb and xclip. The local Cargo overrides preserve
the upstream locked versions and dependency edges; they fix interrupted input
polling and missed clipboard events without updating unrelated dependencies.

The Linux build also runs two real isolated desktops with the exact packaged
installer. The native test uses the live Mixel relay. Tests require customer Accept, compare
decoded video and host input, transfer a synthetic file in both directions, and
recover from incoming-service restart and network loss. A second run creates a
disposable copy of the pinned relay with the registration gateway, blocks native
ports, and requests ordinary ID connections without the manual relay option.
It verifies automatic certificate-verified HTTPS on port 443 using an explicitly
recorded fixture CA and public relay pin. This does not assert that the gateway
has been deployed to the live server. Failure
stops the build; screenshots, native logs and the installer SHA256 remain in the
`linux-support-session-proof` artifact.

The isolated HTTPS regression is also runnable with
`python3 scripts/test-support-session-https-linux.py --deb <installer> --proofs <new-directory>`.
Its ownership checks and cleanup protect unrelated Docker resources. See
[the gateway deployment and rollback scope](infra/relay-ws-bridge/README.md)
before any production change.

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
- `Mixel-Remote-Support-Windows.exe` and `Mixel-Remote-Support.exe` — Windows client.
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

`runtime_artifact_run` runs a read-only Windows launch diagnostic against a
previous signed Store payload artifact. `runtime_macos_artifact_run` verifies and
launches both signed/notarized Mac installers. `runtime_linux_artifact_run` runs
the native/HTTPS two-desktop session tests against an existing Linux artifact.
All diagnostic inputs require `publish_r2=false` and a positive build run ID.
The local
macOS signing helper `scripts/local/sign-and-publish-macos.sh` is a publication
action and must only be invoked for an authorized release.

## Deployment boundaries

The relay is `rs.mixel.ch`; `remote.mixel.ch` is a separate MeshCentral product.
Live relay identity changes, production data changes, and store submissions are
separate release decisions. The related `mixel-ism/infra/rustdesk/README.md`
records the prepared approval-required migration for the historically exposed
relay identity; do not silently rotate it and break existing customer clients.

The product remains self-hosted without a recurring RustDesk Server Pro license.
