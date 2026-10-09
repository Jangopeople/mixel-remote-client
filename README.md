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

Windows QuickSupport double-click launches also maintain the runtime consent
guard. Linux uses a Mixel-specific DBus name so installed stock RustDesk cannot
consume a Mixel invitation. A cold Linux invitation starts the incoming service.

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
previous signed Store payload artifact. It never publishes downloads. The local
macOS signing helper `scripts/local/sign-and-publish-macos.sh` is a publication
action and must only be invoked for an authorized release.

## Deployment boundaries

The relay is `rs.mixel.ch`; `remote.mixel.ch` is a separate MeshCentral product.
Live relay identity changes, production data changes, and store submissions are
separate release decisions. The related `mixel-ism/infra/rustdesk/README.md`
records the prepared approval-required migration for the historically exposed
relay identity; do not silently rotate it and break existing customer clients.

The product remains self-hosted without a recurring RustDesk Server Pro license.
