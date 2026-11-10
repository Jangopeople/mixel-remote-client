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
- Sign technicians in with Mixel ID. Use the Admin Cockpit company directory
  for ownership and support assignments. Reuse linked ISM inventory, tickets,
  and session history where those records exist.
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
- Full-screen viewing after the first accepted video frame. Keep End session
  available, ignore clicks outside the image, map drags to the image edges,
  release held input on transition and exit when the session stops.
- Session recording with a playable retained file; chat, supported audio/video,
  multiple supporters and handover with explicit authority transitions.
- Reboot/reconnect, remote terminal and authorized scripts, privacy/input block,
  Wake-on-LAN, remote printing and mass deployment on supported platforms.

## Enterprise management and Tensor-style controls

- Mixel IT technicians sign in with their own Mixel ID and choose a customer
  company in Admin Cockpit. The company owner or an authorized company admin
  can assign and revoke access for individual technicians or support teams.
  Verify that an unassigned technician cannot list or connect to the company's
  computers, including by typing a known Remote ID directly in the app.
- Add a computer by its Remote ID and confirm the pairing on that computer
  before it appears in the company inventory. The same ID remains through
  app updates, restart, password renewal, and authorized device key rotation.
- A customer can give a technician the computer ID and a one-time password
  for attended support. Verify that the password expires, is consumed on
  successful use, and cannot be used for a different computer or connection.
- A company admin can enable unattended access on a managed computer or
  server and assign who may connect. Verify a connection after reboot without
  an on-site user on supported platforms. Revoking the grant must deny the
  next connection and end active access. Audit entries identify the company,
  device, technician, and access mode.
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

## Commercial launch and Mixel integrations

- Onboard an independent IT support company with its own Mixel ID organization,
  administrators, technicians, customer companies, and devices. Verify that
  two independent providers can each manage multiple customers and that direct
  Remote ID lookups, sessions, audit records, and exports respect those
  boundaries. Verify technician removal and company offboarding.
- Verify the agreed sales and subscription workflow from purchase or trial
  activation through plan changes and cancellation. Enforce seat and feature
  entitlements on the server. Retest the limits with retries, concurrent
  requests, and an expired entitlement.
- Verify Mixel Remote as a standalone product for a provider using Mixel ID
  and Admin Cockpit. Verify that a provider can connect and disconnect each
  optional Mixel integration and that losing a connector does not grant
  access or stop ordinary remote support.
- **Mixel ISM.** Open a support session from the authorized customer's ticket
  or device record. Confirm the resulting session status and audit reference
  appear on the correct ticket. Retry the same event and verify that it does
  not create a duplicate session or attach it to another company.
- **Mixel PBX.** Start with a support call linked to the correct ISM ticket.
  From that ticket, open Mixel Remote and verify the call, ticket, and remote
  session stay linked. A caller number does not grant device access.
- Define the workflow and permissions for each additional Mixel app before
  offering it as an integration. Verify authorized use, rejected cross-company
  requests, duplicate delivery, outage recovery, and connector revocation.
- Before a commercial release, exercise these flows with independent test
  companies using the release builds and staging services. Require passing
  evidence for tenant isolation, account and device revocation, supported
  platforms, upgrades and rollback, and a security review of the release.

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
