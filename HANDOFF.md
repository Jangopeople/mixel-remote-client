# Mixel-Remote — Agent Handoff / Current State

## Reliability work in progress — 2026-10-10

The active review branch is `fix/remote-support-reliability`, draft PR
[7](https://github.com/Jangopeople/mixel-remote-client/pull/7). This is a large
attended-support, transport, native input/clipboard and installer verification
pass. It is **unfinished**: the fresh Windows ordinary-user startup still crashes,
and the production
HTTPS gateway awaits Michael's exact approval. No public release has been made.

The working acceptance criteria are a branded customer launch with registered
relay/key readiness, customer Accept before authenticated control even with saved
passwords/recent sessions, native-first automatic HTTPS fallback and reconnect,
working mouse/keyboard, bidirectional clipboard and SHA-verified file transfer,
and fresh packaged Linux/Windows/Mac builds with actual runtime/signature checks.
The Windows portable customer gate additionally requires 95 seconds of the same
PID/HWND, positive v2 IPC and retained kernel-owner consent. Keep every gate strict.
Full TeamViewer/pcvisit feature parity and an absence of every possible bug are not
established by these scoped tests.

The last completed all-platform application baseline is
`7f060ab92e6c9bcfa726e9d316e73605627b721e`. The newer application source
`5df042a8d88bb861da4979dfcc4735e67b1124f3` was built in
[38065522873](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38065522873)
for Linux and both Mac architectures, with publication disabled. The overall run
**failed**: Intel's final Rust link omitted Clang's availability runtime; the
Linux job's embedded HTTPS recovery test encountered an outgoing-session ordering
race. The ARM packaging/signing/notarization/launch job passed. Optional Linux
keep-awake containment and bounded asynchronous request coalescing are qualified
in the actual new DEB; four-mode artifact-only run `38067179820` passed independently.
The corrected Linux ordering is committed as `91d95e9`, with 48 regression tests;
exact-DEB native rerun `38068710521` completed success with independent all-four-mode
replay of 432 actual API-bound members. The Mac runtime correction and
explicit codec deployment targets are committed as `bc346be` after actual pinned
Rust1.81/cc1.2.13 link reproduction, native ARM/Intel archive controls and independent
review. Fresh Mac-only build `38069369883` is queued from exact
`bc346beb7498cc3017b4c6029c29b9038f3ed2a0`, with publication disabled.
Fresh packaged Mac verification remains required. Product corrections also include the Linux warm DBus
consent/handoff fix `235273c`, basename-only Windows support aliases `d6728bd`,
Windows UTF-16 terminal-NUL normalization `481215f`, and the unsigned AV1 variadic
argument correction `f0e41de`. The existing larger pass covers bounded/redacted
invite handling, kernel-owned attended consent, strict peer encryption, native
input/clipboard fixes, WSS receive freshness, same-process new-registration
recovery, early C++ SIGPIPE handling, branding and signed installer checks.

Fresh all-platform build
[38056398014](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38056398014)
from that exact baseline finished **failure** because of the Windows gate.
Configuration, bridge, all platform source preflights, Linux and both Mac jobs
passed. Builds use `publish_r2=false`; public downloads remain unchanged.

The exact fresh Linux DEB SHA256 is
`0bf6d261e2e8ca2c5ed80be1f73e8b119bd7e581ce66aa91b0e69879e7afc021`.
All three native desktop suites passed: live native relay, isolated fully blocked
HTTPS, and independently blocked native TCP/UDP. Independent replay bound all
346 evidence files to their direct GitHub ZIP/API IDs/digests before verifying
actual Accept/Disconnect, all nine changing/current decoded frame clocks, default
native keyboard/mouse, two-way clipboard/files, paused-GUI incoming restart and
network recovery with the same incoming PID/lease. HTTPS required a genuinely new
TLS registration tuple and stable guarded/key-positive readiness before the
first reconnect request. No incoming exit/OOM occurred; cleanup passed. A separate
clean Ubuntu apt-install proof passed exact dependency/launcher/desktop/service
and runner/core `ldd` checks. It runs amd64 emulation and is packaging proof.
The native sessions do not exercise a systemd-managed installed root-service restart.

Both fresh Mac jobs passed compilation, compiled consent checks, signing,
app/DMG Accepted notarization, stapling, Gatekeeper and actual cold/warm same-app
registered-ID/v2/relay/key readiness. Downloaded exact DMGs separately passed
read-only signature/ticket/Gatekeeper, bundle `ch.mixel.remote` and architecture
checks. ARM DMG SHA256 is
`8010d3e9570e0c789409de0eea85c4991242a45038262ec48a2fc6670d51dc62`;
Intel is `6a7a6af9645bc3384de8d953650dd5abe54fbe78fc210e49c36bf336d71c83fb`.
The first Intel proof passed validation but failed busy-volume detach. Its failure
is preserved. Exact owned-image cleanup then passed; a qualified v3 helper adds
bounded detach retries, exact image/mount/device ownership and no recursive
mounted-directory deletion. Nineteen helper controls, including disappearance
before normal or forced detach, passed. A fresh Intel v3 rerun passed with owned
mount removed. The v1, v2 and v3 inputs/results remain separate and immutable.
Intel CI executes via Rosetta on Apple Silicon, not native Intel hardware. These
checks do not prove a full Mac screen-control session or grant customer TCC access.

Fresh Windows passed compilation, native UTF-16/AV1 source regressions, compiled
consent, signing of the full payload, signed portable extraction, cold/warm URI,
and cold/warm QuickSupport launch with actual incoming v2/registered relay/key.
Its subsequent non-elevated ordinary GUI PID1788 exited `0xc0000374` before HWND;
the 95-second handoff did not occur. Exact signed Store payload retained in the
same run: inner ZIP SHA256
`4e9b9485b22fd2cdc25037f738bd5dbbebb8972687439c1d1099cad23b482ca9`.
No final Windows distribution artifact was emitted after that strict failure.
The heap crash has no confirmed product cause. Do not blame AV1, GPU, ACL or UAC
without attributed native evidence. Both UTF-16 and AV1 source corrections pass
native Windows execution; the AV1 original reproduces 12 wrong variadic values
and the fixed code passes all 14 cases against actual headers.

Windows fixture desktop permissions were separately established by native
A/B/A tests on Windows 2022 and 2025: minimal per-SID rights fail before Main with
`0x8007045a`, the canonical temporary per-SID grant succeeds, and returning to
minimal rights fails again. Exact original ACL hashes, account/SID and profile
are restored. An exclusive-file-lock snapshot-reader race is fixed and qualified.
Diagnostic `38059294364` at `0d5f4d04fc3130d5ff977bc4b939294912fb54bc`
proved a synthetic native `RaiseException(0xc0000374)` with actual control PID4348,
Main entry and one bounded PID/code/stack on Windows2022. Windows2025's control
stayed suspended before Main even though the originally retained primary hThread
reported previous suspend count zero. This rules out residual CREATE_SUSPENDED
as the sole explanation. Product PID6948 on Windows2022 remained alive/suspended
without a HWND; no product exception was captured. The retained-thread operation
runs once after actual filter READY plus owned attachment, accepts documented
previous-count0/no-op or1, rejects extra suspension and preserves execution markers.
Diagnostic `38060545546` at `a83cf38397d2cc78b9e58601248e7f7b5238efdc`
then used a bounded read-only native `.lastevent` query before cleanup. Both OS
controls responded immediately at the stopped initial breakpoint `80000003`,
with exact owned PIDs3464/3404, Mainfalse and no parser/evaluation errors. The
lowercase CDB `-g` correction then qualified in native run `38061358434` at
`e0dfeaf001ac105432b761eef5ff249260b19110`: both OS controls entered Main and
captured their exact `c0000374` PID/stack. Actual product PIDs6416/6572 advanced
to a hidden Mixel window, then stopped at owned invalid-handle `c0000008` events;
no product heap exception was captured. This is a debugger stop, not an attributed
customer heap cause. The next diagnostic captures those separate exact-PID/code/
chance stacks and preserves default exception handling before continuing. No
customer acceptance gate or application source changed for these diagnostic fixes.
Reviewed diagnostic source `05848d3b92a0495bf1a7a08386e73731cf4e4fd0`
adds separate exact-PID/code/first-or-second-chance invalid-handle stacks with
16-event, 40-frame and 256-module limits. Its native synthetic control must prove
an invalid non-pseudo CloseHandle returns FALSE/error6 before independently
capturing its controlled heap exception. Local compilation, 16 negative/parser/
privacy controls, finally capture and independent review passed. Both-OS native
run `38062609997` failed its strict synthetic invalid-handle capture gate. Both
controls entered Main, observed CloseHandle(NULL) returning FALSE/error6 and
captured their separate exact-PID heap exception, but neither qualified an
invalid-handle stack. Product launch did not run. The next diagnostic retains
the NULL return control, adds a distinct nonzero/non-pseudo invalid handle and
exports bounded scalar capture/rejection counts. Source
`77cad049fa688e48e86e760e39ea5fc6a403749e` passed exact local parser/finally,
PowerShell/C# and read-only timeout controls and independent review. Native run
`38063541272` failed on both OS controls: NULL returned FALSE/error6, but nonzero
handle1 raised code8 and terminated before returning or reaching the heap control.
Two invalid-handle marker blocks, exact owned PID/code rows and one first-chance
row were observed; the later unknown-chance/no-frame block made strict attribution
fail. No product launch occurred; cleanup passed. This establishes a debugger
side effect, not the original debugger-free customer heap cause. The next probe
will preserve individually complete bounded blocks as explicitly partial evidence
while leaving the overall qualification false. Native qualification is still
required. Full preflight `38062603429` at
`05848d3` passed all five jobs. The failed calibration result remains failed.
Normal customer tests remain creation-flags0 and debugger-free. Live debugger
input stays open while private stdout/stderr drain concurrently. Raw dumps,
raw debugger files and symbol caches are private and removed; artifacts retain
only sanitized PID/code/frame/module/event classifications. Do not conflate a
synthetic exception with actual product heap corruption.

Diagnostic source `b828ac641396ec3b6f3e7856d0a01a944f3fb813` retains individually
complete owned invalid-handle records as explicitly partial evidence when a later
block is rejected. A unique hex exception address and matching exception-record
code are required. The overall synthetic gate still fails on any rejected block.
Both heap and invalid-handle frame parsers reject trailing free text and malformed
short-index module rows; retained genuine native frames still replay exactly.
All five source-extracted PowerShell/C# checks and independent review pass.
Native diagnostic
[38065587379](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38065587379)
uses the unchanged signed `38056398014` payload and failed its strict synthetic
control on both OS versions. Each retained one qualified partial first-chance
code8 block with fifteen frames; full qualification stayed false. The native
exception address maps in the stack to `ntdll!KiRaiseUserExceptionDispatcher+0x3a`,
followed by `KERNELBASE!CloseHandle+0x49` on Windows2025 or `+0x4f` on Windows2022.
NULL returned FALSE/error6, while nonzero handle1 never returned and the control
exited code8 before its controlled heap exception. No product was launched.
Cleanup passed. This does not attribute the original debugger-free heap crash.
Exception stack return addresses are not exception instruction addresses; do not
infer an address-specific continuation policy from a stack symbol alone.

Diagnostic `63783ef786726203dd6d4c4f2ba1e83f84763400` narrows normalization
of the debugger-only CloseHandle notification to the owned PID/code8/first chance,
nonzero/nonwrapping target-resolved dispatcher and CloseHandle bases, exact offsets,
and consistent event/exception/frame-return addresses. A separate continuable
flags0 genuine invalid exception must remain fatal with no after-raise sentinel.
All six source-extracted root/independent control groups passed unchanged hashes.
Native run `38068070084` at that exact source finished **failure** on both OS
versions, but both calibrations passed: the genuine code8 negative stayed fatal,
NULL and handle1 returned FALSE/error6 after the exact mapped debugger notification,
and an independent owned synthetic heap yielded fourteen frames. Actual Windows2022
product PID3232 then produced an attributed heap `c0000374` with eighteen frames
behind `RtlFreeHeap` and the native core. Export-nearest `free_zero_copy_buffer_f64`
labels have huge offsets and do not identify the actual functions. Windows2025
PID3932 failed the visible-window gate without a heap capture; its bounded owned
query reported code `80000003`, whose origin is unestablished. Cleanup passed.
The exact signed core is stripped and has no qualified RSDS/PDB identity; a
same-source diagnostic symbol build is being prepared. Default debugger-free
ordinary launch and the 95-second customer gate remain unchanged and unpassed.

Independent old-core `.pdata`/IAT/string disassembly and the exact ordinary-user
log narrow the heap path to failed Windows clipboard-file context initialization.
`CliprdrClientContext::create` returns Err after C init already uninitializes the
context; dropping its error Box invokes C uninit a second time. Exact source-
extracted C init/uninit/format-map functions reproduce `AddressSanitizer:
heap-use-after-free` when CreateMutex fails then the Rust error-Drop contract runs.
The original failure is preserved under `clipboard-double-cleanup-reproduction`.
A strict pinned ownership/idempotence correction is being implemented; it is not
committed or qualified in a new Windows release yet. Matching symbol-build and
final both-OS debugger-free outer-QuickSupport verification remain necessary.

New Linux DEB SHA256 is
`af63e2b6f8ef320d42e6b5ee305bed04fe5e7808490797b863f39fdd5eadc6d8`.
Independent four-mode replay of `38067179820` binds all three actual GitHub ZIP
API digests and 424 members before checking customer Accept, current decoded video,
input, clipboard/files, process ownership, relay restart and new registration.
The saved-password case preserves GUI1075, warm sender1504 exit0, 22 auth0 samples
through 12.563 seconds, actual Accept, changing current video and unchanged access
preferences. All eight app stdout logs have zero unhandled/ScreenSaver errors and
seventeen contained generic keep-awake unavailable notices. The application build's
failed status remains recorded separately; this scoped proof does not qualify Windows.
The independently frozen review manifest is
`independent-linux-runtime-38067179820/independent-four-mode-review-manifest.json`
(SHA256 `8ac999fb78bb3b65a574dfac241826f6794f751f2dece36b5aee38586b855a2d`).

Audited Node24 checkout/artifact action updates are committed as `d55c7c7`.
Exact preflight `38066727820` passed all five jobs with no task-owned Node20
warning. The centrally managed Sentinel workflow was left unchanged.

New preflight runs `38065498067` at application `5df042a` and `38065579787` at
diagnostic `b828ac6` both passed all five jobs. Their exact metadata and passing
logs are retained as `preflight-5df042-success.*` and `preflight-b828ac-success.*`.

The old successful Linux runtime logs also exposed unhandled optional
`org.freedesktop.ScreenSaver` provider errors. The new keep-awake manager serializes
the backend call, records enabled state only after success, catches optional
backend errors without raw provider details, and retries on later lifecycle
requests. Its pending queue stays bounded: one queued callback for 10,002 requests
while one acquisition is held. Five actual original failure controls and thirteen
corrected Dart scenarios pass. Missing desktop keep-awake support must not abort
or misreport a remote session.

Read-only inspection of the exact old Mac DMGs found ARM core/service minima of
12.3 while its bundle advertised 10.14. Intel core, runner and service remain
10.14. The new pinned patch makes ARM build, runner, pods and bundle consistently
12.3 and preserves Intel 10.14. Explicit 10.15 permission availability guards
remove four original Intel compiler errors under warnings-as-errors without
changing the legacy permission fallback or requesting OS permission grants.
Eleven drift/architecture/atomic-validation controls and the complete fresh
branding pipeline pass. Native packaged verification of the correction is pending.

The earlier supplemental saved-password result remains failed and immutable:
actual valid-password authentication/video, Disconnect, warm same-GUI URI and
12 seconds of auth0 passed, but post-Accept changing-video sampling timed out.
Source-extracted controls reproduced a clock read before a slow capture, popup
occlusion of one RGB bar and a geometry change across capture. The corrected
sampler reads source immediately after capture, uses two agreeing bar tops and
rejects changed geometry. Its original three-second freshness, future tolerance,
clock sync and changing-frame requirements are unchanged. Forty-three consent/
fixture/provenance controls and retained-image regressions passed independently.
The new native saved-password mode requires exact mapped run/source/DEB and an
isolated blocked-native TLS fixture. It proves ordinary saved-password auth1
before the same password stays auth0 for twelve seconds after a warm attended
URI, then requires actual visible Accept, changing current video, TLS transport,
unchanged access preferences and Disconnect auth0. No IPC authorization is used.
Artifact-only native run `38061126199` at QA source
`e581a1c4e472f71b87ca3ae28277cf5d6f41fcfe` completed **success**. It explicitly binds
the three ordinary transport suites and supplemental password case to the exact
`7f060ab` DEB from build `38056398014`; it preserves the failed overall app build.
All three fresh ordinary suites passed independent exact-archive replay. The
native saved-password case also passed: ordinary auth1/current video, warm sender
PID1498 exit0, original GUI1075 and kernel lease, 22 auth0 observations spanning
0.102–12.600 seconds, actual blue Accept, auth1 and changing decoded clocks,
native TLS443, preserved preferences/password, Disconnect auth0 and cleanup.
Independent PNG decoding matches baseline clock1181 and accepted clock1253.
Fresh full preflight `38061125440` at that QA source passed all five jobs.
Evidence is `native-saved-password-38061126199` and
`independent-linux-runtime-38061126199`. The old failed password result remains
failed; these are fresh native results with independently qualified QA inputs.

Evidence and full source/run/SHA provenance live in ignored
`artifacts/verification-2026-10-09/`. The current canonical collection is
`final-build-38056398014`; strict Linux replay is
`linux-candidate-replay-38056398014`. Earlier `37952824679` installers and all
older diagnostic payloads remain diagnostic baselines, not final proof.

The deployed stock server rejects host RegisterPk on `/ws/id` with NOT_SUPPORT.
The reviewed loopback compatibility service, exact production action and draining
rollback are in `infra/relay-ws-bridge/README.md`. Isolated actual server/TLS/
encrypted-session and 100-host reconnect tests plus offline deployment tests
pass. All six offline deployment tests were rerun successfully on October10;
the reviewed bundle source-manifest digest is
`45760e7b76adc47b7efa795b655c060bd1cc7d04a3fa02e9857f7ebb02825264`.
Michael's explicit production approval is still pending. Do not infer it from
"continue", fixtures, builds or the broad original request. Recheck deployment
guards after exact approval. No production identity/database/DNS/server container,
public download, marketplace submission or installed local app/service changed.

Root owns docs, installer verification and final packaging. Pipeline owns Windows
native diagnosis/build/signature/provenance; invite owns supplemental saved-password
proof and QA sampling; experience owns independent native Linux replay/reviews.
Do not stop at a fixable failure. Keep the PR draft until final strict gates and
production decision/verification are resolved.

The earlier refreshed local TLS relay fixture was removed with the existing
manifest/label ownership checks. All three exact containers, its network and
private identity volume are absent; cleanup evidence is
`refreshed-owned-relay-cleanup-final/manifest.json`. Native CI fixtures are separate.

> **Purpose:** if Michael hits his Claude weekly limit, another agent (Codex,
> etc.) can pick up Mixel-Remote work from this file alone. Read this top to
> bottom before touching anything. The earlier operational notes below date from
> **2026-07-23**; the reliability status above is current for **2026-10-10**.
> Obey `AGENTS.md`, `GUARDRAILS.md`, `CODEX_CONVENTIONS.md` in this repo —
> especially the marketplace-release-safety rule (never cancel/replace a
> live or in-review store submission; updates go through the normal update
> path).

## What Mixel-Remote is

A rebranded fork of **RustDesk 1.4.6** used by Mixel IT to give SME customers
remote support. Not sold as software — used internally to deliver support.
Connects only to Mixel's own relay. Two repos are involved:

- **`Jangopeople/mixel-remote-client`** (this repo, private) — the fork +
  build pipeline that produces the branded, server-baked, signed clients.
- **`Jangopeople/mixel-ism`** — the web app (`apps/web`, Next.js) that hosts
  the `/remote-support` download page, the Microsoft Store MSIX workflow, and
  the KB. Deployed to Cloudflare Pages (`ism.mixel.ch` + `ism.mixel.mu`).

## Server (the relay) — do not confuse the two hosts

- **`rs.mixel.ch`** → the actual RustDesk relay. Resolves **directly** to the
  VPS `178.104.17.23` (NOT Cloudflare-proxied). Ports 21115-21119 open.
  Containers `hbbs` (rendezvous) + `hbbr` (relay) run via docker-compose in
  `/opt/rustdesk/` on the VPS. SSH: `ssh mixel-vps` (alias, **port 2222**).
- **`remote.mixel.ch`** → a DIFFERENT product (MeshCentral). Never target it
  for RustDesk work.
- **Canonical relay pub key** (verified live on VPS
  `/opt/rustdesk/data/id_ed25519.pub`): `OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo=`
  This is what the client bakes and the page shows. An old note called
  `MtLWP8YyUX…` canonical — that key is DEAD. Always verify against the VPS file.

## Build pipeline (this repo)

- `.github/workflows/build.yml` clones upstream RustDesk (pinned 1.4.6,
  `branding/branding.env::UPSTREAM_VERSION`), runs `scripts/apply-branding.sh`,
  then upstream `build.py --flutter`, packages per platform, signs Windows via
  Azure Trusted Signing, signs+notarizes macOS, uploads all artifacts to the
  R2 bucket `mixel-remote-binaries` (custom domain `download.mixel.ch`).
- **Partial rebuilds:** `gh workflow run build.yml --ref main -f only=windows`
  (or `linux`, `macos`, or `windows,linux`). Saves CI minutes.
- **`scripts/apply-branding.sh`** is where ALL branding lives. Key patches:
  - Bakes `rs.mixel.ch` + pub key into `libs/hbb_common/src/config.rs`
    (`RENDEZVOUS_SERVERS`, `RS_PUB_KEY`) — the server is compile-time default.
  - Seeds `DEFAULT_SETTINGS` so the Network settings UI *visibly shows*
    `rs.mixel.ch` + key (blank fields read as "unconfigured" to users).
  - Deep rebrand (Windows+Linux): process `mixel-remote.exe`, extract dir
    `%LOCALAPPDATA%\mixel-remote`, core lib `libmixel-remote.{dll,so}`,
    `RuntimeBroker_mixel-remote.exe`, file-props, About slogan.
  - `patch_string` helper escapes sed metachars (`&` in replacement) — do NOT
    revert that; unescaped `&` silently corrupts patches.

## Historical channel snapshot — 2026-07-23

| Channel | State |
|---|---|
| **Microsoft Store** ("Mixel ISM", product `9n2z1b4c5l9d`) | **1.5.6.0 LIVE** — baked, visible fields, deep-rebranded. Certifies same-day. Update workflow: `mixel-ism/.github/workflows/desktop-release-store.yml` builds the MSIX from an unsigned baked exe (Microsoft re-signs Store pkgs); submit via Partner Center as an **update** (bump `apps/desktop/msix/AppxManifest.xml` version each time). |
| **Windows .exe** (download.mixel.ch/Mixel-Remote-Support.exe) | Baked + visible fields + deep-rebranded + **Authenticode-signed** (Azure Trusted Signing, CN=Mixel International Services SARLS). |
| **Windows .msi** | Signed, but **stale** (still rustdesk-named internally; WiX only builds when present). For admin/Intune deploy. Rebrand not applied. |
| **macOS** Apple Silicon + Intel .dmg | Baked + visible fields + **signed & notarized** (Developer ID: Michael Lascar, 5277F8NDH4, bundle `ch.mixel.remote`). Fresh clean build `?v=1.4.6-8-clean`/`-6-clean`. About shows Mixel-Remote + Mixel copyright; RustDesk slogan removed. **NOT deep-rebranded**: core file still `liblibrustdesk.dylib` internally (Xcode/notarization risk; invisible inside signed .app). |
| **Linux .deb** | Baked + visible fields + deep-rebranded (`libmixel-remote.so`, `mixel-remote` binary, zero rustdesk-named files). |

## Signing (Azure Trusted Signing)

- Account `mixel-codesigning` (West Europe, Basic), cert profile `mixel-remote`
  (Public Trust, CN=Mixel International Services SARLS, identity validated to
  **2028-08-10**). Certs auto-rotate every 3 days — **no manual renewal**.
- CI auth: service principal `mixel-remote-signing-ci` (signer role only);
  secrets `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` in this
  repo's GitHub secrets. Wired in `build.yml` via `azure/trusted-signing-action@v2`.

## OPEN ITEMS (what a takeover agent should watch / do)

1. **SmartScreen reputation review (Windows direct .exe)** — submitted
   2026-07-22 to Microsoft's Security Intelligence portal
   (`microsoft.com/wdsi/filesubmission`, Software-developer persona, product
   "Microsoft Defender Smartscreen", "incorrectly detected"). The deep-rebrand
   reset the exe's download reputation, so fresh Windows machines see "Windows
   protected your PC". Decision expected 1-3 business days → email to
   michael@mixel.ch. When approved, verify the block is gone. **Lesson:** every
   new exe hash resets SmartScreen reputation — batch changes, don't reship
   the direct .exe often. Store + Intune bypass SmartScreen entirely.

2. **ToiToi (Store-blocked customer)** — their Windows machines block the MS
   Store and may enforce SmartScreen Block mode. Guaranteed fix = **Intune
   deploy of the signed .msi** (admin-deployed apps bypass SmartScreen). A
   German admin note was drafted but not yet sent. `.msi` is at
   download.mixel.ch/Mixel-Remote-Support.msi (signed).

3. **macOS customer freeze/"can't enter password"** — root cause was (a) an
   OLD download (About said RustDesk/Purslane) + (b) macOS TCC permissions not
   granted. Fix = re-download current build + grant **Screen Recording +
   Accessibility** + quit/reopen. The session/password flow itself is fine
   (verified: Linux controller connected to controlled with password, got
   video). Page now has a macOS permission block; KB article added (see below).
   If it still fails on the fresh build with permissions granted, get a screen
   recording — that would be a real bug.

4. **KB article** `supabase/seeds/src/kb_mixel_remote_macos.json` (in mixel-ism)
   — macOS freeze/permissions, trilingual, validated. SQL generated. **NOT yet
   applied to prod DB** — needs Michael's approval. Apply per
   `.agent/rules/kb-authoring-standard.md` pipeline (psql to `ism` DB on
   mixel-vps).

5. **macOS deep-dylib rename** — deliberately skipped (risk to notarization).
   Only do if Michael insists; test notarization carefully.

## How to deploy the web page (mixel-ism)

CF Pages git auto-deploy is BROKEN. From `mixel-ism/apps/web` on `main`:
`pnpm build && npx wrangler pages deploy out --project-name ism --branch main`.
Serves both `ism.mixel.ch` and `ism.mixel.mu` (the .mu CNAME must point at
`ism-8ha.pages.dev`). Bump `?v=` cache-busters in
`apps/web/src/app/remote-support/page.tsx` when an R2 binary is replaced.

## Hard rules (see AGENTS.md for full text)

- Confirm before any marketplace action affecting availability/review state.
- Never apply KB seeds to prod DB without explicit approval.
- Swiss Standard German in all customer text (no ß).
- New client builds are UPDATES, never remove/replace an approved store app.
