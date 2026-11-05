# Mixel Remote portfolio acceptance

Michael selected the entire TeamViewer portfolio scope on 5 November 2026:
desktop remote support, Tensor-style enterprise controls, mobile support,
endpoint management and AI. He subsequently instructed that mobile software be
built without physical phones. Connected phones are not a prerequisite for
development. Simulator/emulator evidence must remain explicitly distinguished
from physical-device evidence.

This is the implementation and acceptance contract, not a declaration that
these products have been delivered. The existing desktop reliability work and
new mobile build work are parts of this larger scope.

## Shared product rules

- Ship Mixel identity, relay defaults and verified update paths.
- Reuse ISM's existing identities, organization/device records, tickets and
  session history. This repository remains the native-client/build pipeline;
  related ISM changes belong in its backend/frontend, following that repo's rules.
- Authentication, enrollment, policy enforcement and native session consent
  must agree. A management-console restriction alone is insufficient if direct
  client connections bypass it. Revocation must reach already active sessions.
- A feature is accepted only for the exact build and platforms exercised. A UI
  control, upstream source implementation, mocked receipt or compiled package
  alone is not an end-to-end pass.
- Keep release signing, private QA signing, unsigned simulator packages and
  runtime qualification separate. Marketplace changes follow the existing exact
  action confirmation rules. Do not remove existing approved releases.

## Desktop remote support

Acceptance covers Windows, macOS and Linux initiator/receiver combinations:

- Attended ordinary consent, reject, timeout, cancel and fresh consent after
  reconnect; no accidental elevation or password bypass.
- Enrolled unattended access with explicit grants, revocation, OS login-screen
  behavior and auditable technician identity.
- Keyboard layouts, Unicode/composed input, held-key/button cleanup, mouse,
  relative pointer, clipboard policy and exact clipboard roundtrips.
- Binary files, folders, overwrite/refusal, interruption/resume and permission
  errors, with exact content verification and correct destination identity.
- Multiple monitors, scaling/high DPI, changing resolution, supported codecs,
  constrained networks, HTTPS relay fallback and recovery after network changes.
- Session recording with a playable retained file; chat, supported audio/video,
  multiple supporters and handover with explicit authority transitions.
- Reboot/reconnect, remote terminal and authorized scripts, privacy/input block,
  Wake-on-LAN, remote printing and mass deployment on supported platforms.

## Enterprise management and Tensor-style controls

- Organization isolation; users, roles, groups and device ownership; managed
  enrollment, approval, device retirement and credential rotation.
- SSO/MFA, provisioning/deprovisioning, narrowly scoped service credentials and
  administrative recovery without using personal reviewer credentials.
- Conditional access enforced by the receiving client: allowed actors/devices,
  session capabilities, attended/unattended mode, expiry and immediate revocation.
- Central policies, versioning, conflict resolution, rollback and offline policy
  behavior. Report actual applied policy separately from desired configuration.
- Authenticated session/audit events, export, retention and recording access.
  Duplicate/replayed/forged events and cross-organization access must be rejected.
- Central device/connection health, deployment/update inventory, service-desk
  integration, APIs and documented third-party integration boundaries.

## Mobile support

- Build Android and iOS applications with Mixel identity, actual mobile native
  libraries, icons, deep links and platform-appropriate permission handling.
- Emulator/simulator cold launch, actual core loading, registration, desktop
  control and supported clipboard/files with independent peers and real effects.
- Android screen capture/input/service lifecycle, permission denial/revocation,
  foreground/background transitions and reconnect.
- Apple mobile capabilities must reflect the APIs actually available on the
  platform; viewing/screen sharing is not reported as unrestricted device input.
- Keep platform/OS support and emulation limits explicit. No physical-phone pass
  is inferred from a simulator or APK build.

## Endpoint management, DEX and industrial workflows

- Authenticated hardware/software inventory, health metrics and alert lifecycle.
- Signed, authorized jobs for scripts, software deployment and patching, with
  cancellation, time/resource bounds, actual execution receipts and rollback.
- Customer/device scoping, policy assignment, maintenance windows, remediation,
  offline queuing, update integrity and administrative auditability.
- Experience measurements and remediation evidence must come from actual
  observations; simulated metrics do not constitute deployed fleet monitoring.
- Industrial/OT and assisted-reality workflows require their own supported
  targets, collaboration/media paths and test environments. Desktop remote video
  is not equivalent evidence for these capabilities.

## AI assistance

- Diagnose from explicitly authorized device/session context; produce useful
  recommendations and session summaries with traceable supporting evidence.
- Integrate with tickets/knowledge and enforce organization visibility,
  retention, redaction and prompt-injection boundaries.
- Distinguish suggested actions from authorized execution. Model output cannot
  grant itself terminal/device access, approve consent or alter policy.
- Verify provider failures, missing context, unsupported recommendations and
  user rejection, as well as successful diagnostic/summary workflows.
- Real provider/connector acceptance requires approved access to that service;
  canned responses are not an implemented AI capability.

## Evidence and current baseline

The November 5 local evidence under the gitignored
`_scratch/native-proof/demo-2026-11-05/` qualifies the recorded Windows/macOS
ordinary-consent, video/mouse/keyboard and reconnect scenarios at their recorded
binary hashes. `_scratch/teamviewer-parity/extended-desktop-results.json`
additionally records the actual R6 Unicode clipboard roundtrip, playable VP9
recording and exact 1,048,602-byte binary roundtrip. These do not qualify the
enterprise, endpoint, mobile-host or AI sections.

The private iOS 26.5 simulator viewer additionally passed a real connection to
PC01, ordinary blue Accept on the independent Windows console, live video and
native iOS keyboard input (`iosproof` independently observed on Windows). Normal
disconnect, fresh consent on reconnect and rejection were exercised. Evidence
is in `_scratch/mobile-local-build/mobile-live-proof/ios-live-result.json`.
This is simulator viewer acceptance, not unrestricted iPhone input or capture.

The local mobile driver records `packageBuildQualified` and `runtimeQualified`
separately. The former must never automatically set the latter. The entire
portfolio remains incomplete until its required feature/platform acceptance
records exist and the remaining implementation gaps are closed.
