# Mixel Remote client

Mixel Remote is Mixel’s branded remote-support desktop client, built from pinned
RustDesk 1.4.6. This repository contains the branding, attended-support patches,
and build/release pipeline. Upstream source is a gitignored build dependency.

Read `HANDOFF.md` and `AGENTS.md` before working on releases. Product identity is
`Mixel Remote` / `Mixel-Remote`, bundle ID `ch.mixel.remote`, and the configured
rendezvous/relay is `rs.mixel.ch`. `branding/branding.env` is authoritative.

## Current proof status — 2026-10-15

Active reliability work is on `fix/remote-support-reliability`
([draft PR7](https://github.com/Jangopeople/mixel-remote-client/pull/7)).
`HANDOFF.md` records current source, failed runs and retained evidence. Private
qualification binds the exact pushed HEAD/tree, including any later
documentation-only checkpoint. Recent private candidates exposed a Windows
verifier timeout and a Mac test-listener readiness failure before the product
peer check. Their bounded verifier, owned-fixture and CI-budget corrections
require a fresh fully green build and preflight before package acceptance.
**Complete application acceptance remains unfinished.**

Malformed support links are refused before legacy Mac IPC or fallback launch.
Reserved host and credential-name recognition is independent of strict invite
validation; valid links retain the existing authenticated peer handoff. The
terminal source layer preserves exact historical restoration and refuses mixed
profiles. Its mandatory URI controls include the generated native sender,
handler, startup and receiver boundaries, with explicit fake transport seams.
Source proofs and the held `5df303f` candidate do not qualify final installers.

The candidate corrects native API/toolchain compatibility, input-device creation
and button/wheel forwarding, fingerprint cache races, first-use keyboard mode,
transfer-window fitting, unsafe fatal signal handling and exited Windows
diagnostic ordering. Normal cleanup, manual GPU choices and Windows consent
guards remain preserved. Actual source/native/composition tests pass; full
Flutter3.27.4 analysis is0 errors/0 warnings with285 existing infos.

Historical ce Linux → signed50bf Windows PC01 passes video/mouse, exact47B
mixed-case/path typing before and after reconnect, both48B Unicode clipboard
directions with a distinct sentinel, a65,536B exact file roundtrip and fitted
first/reopened transfer windows. These qualify the tested historical packages.
Fresh combined-source packages, Mac connected-desktop acceptance and full signed
Windows95 remain pending. Physical accented keys, native oversized stored frame
and Wayland GUI are not inferred from other controls.

The reviewed production HTTPS gateway is deployed with only loopback8090 and
nginx `/ws/id` changed. Original native services, identities/start times and the
existing PC01 connection were preserved. Actual signed/encryptedHTTPS35s and
native UDP/TCP2s tests pass. Registration-send count is unmeasured; customer
HTTPS desktop acceptance remains separate. No public package aliases or
marketplace state changed.

The Mac updater now stages and verifies replacement, preserves the old app,
rolls back failures and propagates actual synchronous CLI results. Interrupted
moves and incomplete plist recovery are covered. Root actual native13 reports
`Ran 13 tests in 70.738s / OK`; combined fatal/cleanup/Quit, release18, SDK6,
discovery11, dispatcher and lint checks pass. All four branding newline profiles
and strict refusal controls pass independently. Privileged signatures/services
are mocked in these isolated updater tests; installed/live GUI update remains
unqualified. The next private Linux build also requires a full desktop session
through the production HTTPS gateway. See `HANDOFF.md` for closed proof hashes,
retained failures, consumed approvals and current NAS/shared-Mac access constraints.

The following earlier checkpoint records are retained for provenance; the current
checkpoint above supersedes their pending-state wording.

Committed6467c86 adds the source-qualified first-use Linux X11/source1 → supported Windows Translate default (saved choices conserved) and file-transfer initial/restored frame fitting in reported work-area coordinates. Exact11 carrier240 freezef3cb6f1e0e709059d041a2085ad95242413eab4483a2a466595f13374ff6dc80 and peer/source/unit addenda are fully root-rehashed. Root current integration runs all four complete LF/CRLF branding profiles twice,83 strict refusals, Rust1.75 productioncfg4 with warnings denied, actual Flutter geometry8/ownership88, full application analyzer0errors/0warnings/282infos, release18/SDK6 and actionlint/shellcheck/diff-check successfully. Root bounded219 closurecb6e2f888f923bf8d138b4cb07450bb2ad624d2cbe5f1922f1bc83f7b24eb45c is closed. Native package build and empty-profile default/file-window UI acceptance remain required. Fresh345 auto2022 and manual2022 native mandatoryStd8 now pass raw[2,0,0,2,5,6,6,2] with exact temporaryQA setup/restoration/cleanup; both wholepreflights subsequently FAIL because the Mac texture Dart fixture bypasses existing lowercase .exe SDK discovery and tries dart.EXE.exe. Windows2025 remains pending; this is not wholepreflight or packaged95 acceptance. Minimal fixture correction is being prepared; product assertions and guard bytes remain unchanged.

The signed Windows package from source50bf290 includes the warm-launch fix.
Its actual PC01 archive, outer executable, running executable and loaded core
file hashes match the private producer; Authenticode is Valid. Cold launch is
Ready. A second launch exits and preserves incoming PID6636 and its start time.
Fresh ordinary blue consent, video, mouse, basic input, both native clipboard
directions,53B text receive,64KiB binary upload/return, disconnect and another
fresh-consent reconnect pass. The approved read-only95 observation passes both
samples with attended-runtime-v2, kernel active, no errors and memoryActive=true.
The second starts95.1994435 seconds after the first completes. All five policy
scopes are Undefined afterward and the approved window is closed. This separate
observation does not establish ordinary-client-to-QS or genuine Standard8 acceptance.

Private Linux build38323409716 completes SUCCESS at exact aad8946, publish=false.
Its new DEB SHA256 is ff424cb15c9a2e4ef07b945a3ded2e0695315d012269251432c439a03c715e97.
Three actual packaged remote sessions pass: native relay, isolated HTTPS and mixed
TCP/UDP recovery. Each exercises consent, changing video, keyboard, mouse, both
native clipboard directions, exact encrypted file transfer and reconnect. Full
application analysis reports0 errors/0 warnings/282 information diagnostics; the
whole native build retains upstream/dependency warnings and is not warning-free.
The queued-modifier correction uses historical XKeyEvent state; the old Shift
failure reproduces, and256 modifier masks plus composed source/SDK/ABI controls
pass. In a fresh controller using this DEB, Linux-US to Windows-DE-CH Translate
input produces exact capitals, path backslashes, quotes, @/:/+ and y/z. Native
Ctrl+C returns38 exact bytes; Unicode paste with accents/Chinese/emoji followed by
Home/End/Backspace editing returns50 exact bytes. Ordinary locator Ctrl+C does not
forward reliably through either console; the visible console Control button
qualifies actual Mixel shortcuts. Default STRING xclip queries are ineligible:
UTF8_STRING is advertised and succeeds. Earlier wrapper failures remain retained.
Updated live reconnect passes fresh ordinary consent, persisted Translate/input1
and a new47-byte path/text/native copy. Both Windows saved UTF8 files52/50 arrive
through dedicated-consent native file transfer unchanged, with exact hashes.
Root72 closed proof is948b8c731c16f17127c14bc68fa8ffbda478983f6ceee40ceb34d276d148d7ae.
Current PC01 PID/start and executable/core-file hashes are rechecked; all five
policy scopes remain Undefined and the read-only check window is closed. Unicode
qualifies clipboard and editing, not physical accented-key input. Keyboard defaults
are unchanged; physical Map mode follows the remote layout.

Both optional Windows2022/2025 native CI fixtures now pass the entire unchanged
original Standard eight cases, actual raw codes [2,0,0,2,5,6,6,2]. The previously
qualified temporary15 permission applies only to the newly owned QA SID. Original
DACL, owner, group, control and LABEL restore exactly, with owned cleanup PASS.
Both actual optional jobs succeed. These records do not establish the minimum
permission mask, internal API route or packaged application acceptance. The
separate fixed4 and fixed8 diagnostics retain six Win32 access5 results on both
runners; neither narrower grant is sufficient in those observations.

All three572 whole preflights still fail the original unprovisioned main gate:
first Standard raw5, remaining seven unrun; all other executed source jobs pass.
The historic signed50bf build also retains ordinary-to-QS raw5 before95, and
publication stays skipped. Reviewed14-file commit5c4a57b integrates main/package
adapters against the qualified fixture: exact hosted context, strict source
profiles, independent restoration before owned cleanup and retained accounts on
unknown cleanup. Original Standard8/guard/runtime95 and release gates are conserved;
only five exact CI steps activate setup. Root source7/package8/original9 pass;
owner196, peer199 and independent187 closures are fully root-rehashed. Actual
mandatory preflights38330233109 (Windows2022),38330235582 (Windows2025) and
autoPR38330186508 finish FAILURE at three new test-fixture CRLF assumptions.
Production canonical reading and9 source pins pass; native setup, original8,
restoration/cleanup and packaged tests are unreached. All1405 original provider
members are rehashed. The unchanged complete CRLF carrier reproduces the three
failures; the test-only correction passes full8 on both LF andCRLF. A separate
downstream packaged8 CRLF profile reproduces three fixture failures. Commit34562d7
fixes only both test fixtures, preserving strict pins/refusals and original runtime
functions. Root actual source8 and packaged9 pass on LF andCRLF, using the same
PowerShell7.6.3 with separate task-owned startup/module caches. Earlier local
runtime failures and invalid diagnostic attribution are retained and corrected.
Root335 proofdb9f9d73644c16c5dca3f1254cf5f78a0d40b362e56d1ac4770cabd0e834be87
is closed. Fresh mandatory2022 run38334364360 and2025 run38334366573 run exact345
with all optional diagnostics disabled; actual native results remain pending. Fresh signed ordinary-to-QS95 remains
unqualified. No Windows product permission
fallback, PC01 namespace ACL change or public release is performed.

The private a32 Mac/Linux build38301480127 finishes SUCCESS. Linux passes native,
isolated HTTPS and mixed recovery sessions. Both Mac architectures pass signing,
notarization, staples, Gatekeeper and native deployment-target checks. Their
complete current native GUI/session path still needs manual Mac unlock. Production
HTTPS gateway deployment remains separately awaiting exact approval; isolated TLS
proof does not establish public production HTTPS availability.

The complete official Flutter3.27.4 SDK contains the correction for the verified
Mac accessibility crash. Its exact source compatibility overlay passes complete
application analysis with **0 errors and 0 warnings**,282 information diagnostics,
27 cleanup tests,11 termination tests,4 manager cases,5 desktop dependency cases,
and native plugin compilation against both supported architecture targets.
Owner, independent peer and root proofs are retained with exact source hashes.
The corrected signed package still needs repeated real VM sessions before the
Mac crash can be called fixed in the shipped app.

Actual [257 PR verification](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38268549344)
exposed two remaining test integration defects: an old exact Dart3.5.4 requirement
and Windows Git-archive line-ending conversion before whole-file verification.
Both narrow corrections now pass owner, independent peer and root checks; exact
source pins and fatal gates stay in place. All six branding corruption cases
now execute from independent fully composed inputs, retain their original exact
fatal markers and leave the baseline unchanged. Root full branding and reporter
14/0 verification pass. These test corrections are committed in2025226.
Mac38268611960 and Linux38268739088 stopped
at the old Dart requirement before compilation; Windows38268640460 now completes
FAILURE at the unchanged ordinary native5 consent baseline, with signed diagnostic
installers retained privately. Fresh unpublished202
[Mac retry](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38272354222)
and [Linux retry](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38272382611)
complete SUCCESS for both Mac architectures and Linux native/HTTPS/mixed sessions.
Root verifies the new original ARM ZIP/DMG, app signature, notarization tickets and
Gatekeeper. Live launch is presently blocked by the Mac lock screen; manual unlock
is requested. Repeated live sessions remain required.

[Current202 PR verification](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38272334688)
now passes upstream source, Mac native lifecycle, release and HTTPS fixture jobs.
Both Windows Core/Desktop source suites report10 tests/OK, then the unchanged
first genuine Standard-account native consent gate fails with access error5;
its remaining seven cases are unrun and owned cleanup passes. No cause or
standard-user/customer qualification is inferred. A separate read-only child-self
job-limit diagnostic is committed in312 after fixing the singleton JSON envelope
found in peer review. Root12 source controls, full Windows metadata compilation
and exact original gate/launcher conservation pass. Actual312 Windows2022/2025
runs exposed a test-only historical JSON expectation that incorrectly required
Desktop5 to enumerate a singleton array. Commit3bbf8b9 preserves its actual strict
refusal and the Core historical reproduction; root12 source controls pass/OK.
Fresh [Windows2022](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38277762353)
and [Windows2025](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38277794513)
checks pass both actual Core/Desktop12 suites, then fail the original genuine
Standard native5 consent gate. All57 child-self job queries per run succeed with
UI/extended flags0; the cause remains unproven and the original failure is fatal.
The subsequent [f894 Windows2022](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38280165605)
and [Windows2025](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38280201975)
runs pass both actual Core/Desktop14 suites. All57 owned child records per run
pass four independent explicit global/session directory QUERY/TRAVERSE opens,
including owned-handle and module cleanup, then the original Standard event
gate still fails with native5. Commit572 separately measures name-creation
access without creating objects or changing permissions. Actual
[Windows2022](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38283380857)
records57 owned children: all explicit QUERY/TRAVERSE and global name-creation
opens pass, while session name-creation opens pass for the elevated4 but return
NTSTATUS0xC0000022 for secondary52 and the first Standard child. The original
Standard event error5 remains fatal. Actual
[Windows2025](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38283374439)
records the same difference for36 children before a sharing violation reading an
already published fixture result; its Standard matrix is unrun. Both actual
Core/Desktop14 suites pass on both runners. Commit94's bounded sharing-violation
retry passes owner/peer/root15 suites and portable warning-fatal tests. Actual633
Windows stops before native observations in an analyzer negative fixture; commit979
preserves LF/CRLF checkout profiles and exact failure markers, with two complete
macro/Dart/Flutter runs passing. Fresh [Windows2022](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38288384025)
and [Windows2025](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38288414777)
fail before native observations on979 at the clean dependency fixture's strict
unrelated-source check. A controlled whole-CRLF reproduction yields51 apparent
changed files and matching stderr; original runner temporary bytes are unretained.
Commit ee60ffa now materializes all53 tracked dependency files under each policy.
Two complete runs pass all four app/dependency newline combinations, eight whole
profile transitions, exact byte/config restoration, original zero-write refusals,
source16 and actual Dart/full Flutter0errors/0warnings/282infos. Root rehashes all
owner18,084 and independent241 proof members and verifies stopped source/control
conservation. Actual Windows verification remains required; the production helper
is unchanged. A read-only DACL/LABEL observer
passes corrected C#50 mocked controls and full22 owner/independent/root suites; it
preserves native0 query failures, explicit unresolved cleanup and the original
fatal consent gate. The observer is committed in5fb541b. Fresh QA-bundle
dependency glue is committed in37b91dc, with13 exact package members,12 source
bindings and separately trusted manifest verification. Owner/peer/root bundle17
checks and Core source controls pass. Package compiler/loader boundaries are mocked;
one GUI metadata check requires actual Windows. Root rehashes all70/429 source
proof members. Fresh actual [Windows2022](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38295921179)
and [Windows2025](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38295945985)
runs on37b91dc fail before native checks after the full analyzer/profile tests
pass: the expanded fixture producer no longer creates the canonical pipeline-lf
path required by downstream SDK checks. Commit480ada2 restores that canonical path
while retaining all four distinct profiles; owner/peer/root source and actual SDK
consumer checks332/36/13 pass. Both37b original collectors are sealed and all717
members are rehashed. Fresh [Windows2022](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38298534768)
stops earlier at its unchanged minimal-before child desktop identity predicate:
the owned process exits0x8007045a, while its separate retained primary-token query
reports the correct owned SID, non-elevated state and session2. Original desktop
permission hashes restore successfully; the child initialization cause is unproved.
The [separate Windows2025 run](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38298591617)
has both optional QA and GUI jobs enabled for actual Windows compilation and
isolated loader observations. No new native consent pass is inferred.
The event route and cause remain unproved; production consent and original fatal
gates remain unchanged.
The manual run also exposed a separate owned-Xvfb fixture startup/cleanup defect.
Its committed ffa3794 correction passes six actual process controls locally and
[fresh native Linux execution](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38282008644):
rapid real clipboard changes, PRIMARY filtering, callback Stop/fatal propagation,
idle/active shutdown, unavailable-server refusal and connection loss all pass.
The exact202 Linux DEB passes all [four fresh artifact-only scenarios](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38277649575),
including a proven working saved password that authorizes the ordinary app but
cannot bypass the actual customer Accept after attended handoff. Native/HTTPS/
mixed full sessions, saved preferences and resource cleanup pass. Mac native compilation
still reports upstream warnings, separate from the clean Dart analyzer. An audit
identified a reachable pinned-rdev C callback ABI defect. Commit633c135 corrects
its public C pointer ABI and borrowed-event lifetime while preserving converters,
Swiss mapping, permissions and the shipping panic=abort profile. Both actual
ARM/Intel C trampoline tests pass; full application analysis stays0errors/0warnings.
Whole-script LF/CRLF controls also reject changed or missing owned dependencies
before any earlier valid overlay writes. Root and independent reviews pass.
The [new unpublished signed Mac build](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38285256902)
completes SUCCESS for both architecture installers, including complete compilation,
clean Dart analysis, native ABI tests, signing, notarization and cold/warm URL health.
Root rehashes517 original source/log/artifact records and separately verifies the
ARM package's six app/DMG signature/staple/Gatekeeper checks. Live keyboard/session
qualification remains open: the Mac lock screen prevents the ordinary launch.

The older [52 Windows diagnostic build](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38263261359)
passes compilation, analysis, signing and cold/warm QuickSupport IPC, but fails
the ordinary runtime baseline with native access error5. Its95-second transition
test was not reached. Genuine Standard-user native qualification still fails;
the supported descriptor/token observations do not yet establish its cause.
The signed inner payload is retained privately for diagnosis. There is no release
qualification or public upload from these failures. Detailed provenance, original
logs, passing output and explicit limits are in HANDOFF.md.

## Earlier proof status and historical package results

The historical52 checkpoint used review source
`52f861bb570dd90d35cea0fbd5c22c205f2539fc` on
`fix/remote-support-reliability` ([draft PR7](https://github.com/Jangopeople/mixel-remote-client/pull/7)).
Complete application acceptance remains unfinished.

Actual full Flutter3.24.5 analysis now reports **0 errors and 0 warnings**;
227 existing info diagnostics remain reported. Root and independent peer verified
four original constructor errors/five warnings, unchanged Client permission
defaults/all15 terminal keys, complete LF/CRLF branding twice and16 fatal controls.
Whole-app analyzer gates run after each OS build. Awaited Mac cleanup passes27
real Flutter widget tests plus bounded/error controls. Root also passed17 release,
full branding,35 generated Rust,7 HTTP,14 readiness and9 local Windows source tests.

The [Mac build38237133182](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38237133182)
completedSUCCESS on exact6dfe44d for both architectures with publication disabled.
Cold/warm launch, runtime IPC readiness, signing, notarization and Gatekeeper pass;
11 build warnings per architecture remain retained. Root rehashed278 original proof
members and checked the downloaded ARM app/DMG. Its22 native paths and target
metadata match CI; whole-file hashes differ after signing, as explicitly recorded.
**Actual normal6dfe PC01 sessions reproduced a black Mac viewer after Accept.**
A separate direct diagnostic launch displayed VP9 video and handled remote mouse
and letters, but an ordinary relaunch failed again. The39-member private live
record is closed and independently reviewed; it does not qualify reliability.
A demonstrated pinned Flutter startup frame-readiness race is corrected in7c75245:
the public view getter prepares native VSync before engine startup. Root and peer
passed38 fatal controls, exact engine regressions, all four complete native plugin
files against both pinned SDK targets, full branding and idempotence. The new
[unpublished Mac build38244708073](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38244708073)
completedSUCCESS on both architectures with publication disabled. All actual
source/full-app analysis, cold/warm launch, IPC, signing/notarization/Gatekeeper
checks pass;11 native warnings per architecture remain retained. Root checked
original ZIP/DMG digests, all six app/DMG signature/staple/Gatekeeper checks and22
native target metadata paths, and rehashed204 original build members.
**Actual normal7c PC01 testing passes three fresh consent/video/input sessions.**
Same-process reconnect, fresh ordinary relaunch, native resize and Hide/Restore
pass; the second session preserves manual video/input for154seconds. Viewer close
restores Ready and native menu Quit removes both owned GUI PIDs. The38-member
private live proof is frozen and independently reviewed; the source, ordinary
launch, screenshots and exact scope are recorded in HANDOFF.md. This does not
qualify complete async Quit cleanup, new clipboard/files/network-fault cases or
physical Swiss modifiers. The main-engine native Quit coordinator and awaited
timer/subscription cleanup are now committed in813ae15. Owner, peer and root
source/native/Dart/full-app tests pass, including56 materialization controls,
20 native controls,4 manager cases and11 actual Flutter tests. Full analysis is
0errors0warnings227infos and complete LF/CRLF branding twice is idempotent.
The [new unpublished Mac build38254639726](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38254639726)
completedSUCCESS on both architectures; actual native Quit of that signed package
removed its first live owned GUI, but **the package fails live reliability**: its
second ordinary PC01 session and a subsequent idle main-window launch both crash
with the pinned Flutter engine's unrecognized startEditing selector. Original
private crash records, engine UUID and exact disassembly bind the known
[Flutter accessibility role-transition defect](https://github.com/flutter/flutter/issues/151428).
Its [official engine correction](https://github.com/flutter-team-archive/engine/pull/54364)
preserves accessibility/text editing. A complete compatible Flutter3.27.4 SDK is
being qualified before a new package and repeated live tests; no correction of
those crashes is claimed yet. Commit1736856 fixes an older cleanup test's composition/Windows
archive handling and the early restore-before-refusal defect. Owner103 and
independent117 refusal controls pass before any actual mutation, preserving the
earlier verifier and all12 generated app files byte-identically to813. The complete
older macro passes LF/CRLF branding twice, Dart and27 Flutter tests; root rehashed
both final freezes and independently replayed56+103 materialization controls.
Actual173 Windows CI then exposed raw verifier hash refusal before native tests.
Commitb884743 preserves its exact canonical source pin while accepting homogeneous
LF/CRLF checkout line endings; mixed endings and tampered source still refuse
without mutation. Owner/peer/root56+109, additional roundtrip/refusal controls and
full branding with actual CRLF verifier pass. Actual b884 CI passes complete
branding then finds direct caller stdout still using cp1252 for a cleanup status
message. A separate173 diagnostic build fails the window status print before
compilation, so it produced no new Windows payload. Commit e2d80e5 selects UTF-8
for direct and inherited Python processes in both workflows. Root release18,
actionlint, warning-fatal compilation and independent review pass; exact statements
from both failed helpers reproduce the original refusal and preserve Unicode plus
fatal exit status with the corrected workflow environment. Fresh Windows CI remains
required: automatic PR38263095325 and unpublished Windows diagnostic38263261359
are running on exact52f source. New fixture observations use supported read-only
APIs on the exact owned event/child while preserving the original Standard gate.
Owner, independent peer and root source controls pass, including the full10-test
suite with Rust1.75/Core7.6.3 and conservation of all10 original PowerShell functions.
These mocked/API-source controls do not qualify actual Windows native access.
The failed813 Mac package's38-member private live proof and independent
32-member source regression are retained: original source throws the Objective-C
exception, official correction passes nine class/focus/editing/cleanup cases.
The isolated3.27.4 SDK already passes actual27 cleanup/11 termination Flutter tests
and complete native compilation against ARM/Intel headers; its newly exposed
warnings and plugin metadata are being corrected before a rebuilt live candidate.

Windows [PR preflight38244703474](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38244703474)
passed the original owned non-elevated desktop/session check, stock PowerShell5
health and corrected UTF8 controls, then exposed the analyzer fixture selecting
WSL bash instead of the active Git Bash. Commit343ac48 adds explicit dispatcher
shell selection and private shell-fixture LF preservation. Root/peer full local
replay and all mandatory fatal-order controls pass. That343 run exposed an
unsupported Bash version assumption;0d1a7d2 replaced it with validated public
runtime paths. [Actual0d PR38249250204](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38249250204)
passed the Git Bash runtime query, then found captured Python child stdout still
using cp1252.43526db makes only those captured test pipes use UTF8, with actual
original-failing/corrected Unicode child controls and unchanged caller environment.
Root/peer complete local replay passes. [Actual435 PR38251209501](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38251209501)
then reached the first native current-caller probe (absent/error2) and failed when
the parent tried to delete an acknowledgment already consumed by its child.
813ae15 fixes that residual cleanup; real Core controls and independent review
pass while unlink/race failures remain fatal. The unchanged Standard native5 gate
still failed in435 and its cause remains open. [Actual813 PR38254538407](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38254538407)
now passes both real Core/Desktop5 source suites and all four same-caller native
guard phases, then fails at the original Standard-account native5 gate. Owned
cleanup passes; the remaining seven Standard cases were not reached. The failure
is retained and the release gate is unchanged. Complete ordinary-to-support
qualification remains unfinished. Fatal
identity diagnostics preserve the original gate, QUERY-only handles and cleanup.
No public download, marketplace or production relay has changed.

The approved live PC01 read-only probe now verifies an actual signed4f inner-app
ordinary-to-QuickSupport handoff: unchanged PID4344/HWND852896, attended-runtime-v2,
branded relay/key/ID and active guard at samples352.429seconds apart. The chosen
test account is an admin member with non-elevated desktop; this does not qualify
genuine Standard accounts or the portable outer launch. Only Process temporarily
used RemoteSigned, then all five scopes returned to Undefined and effective policy
Restricted. The approved console close also closed its attached test app. Root and
peer verified the18-member private record and15 screenshots; exact scope and
original CI failures remain in HANDOFF.md. Complete application acceptance is open.

The supplied Windows VM now has real4f attended-session proof: actual customer
Accept, video/mouse/letters, bidirectional file transfers with matching SHA256,
and native clipboard transfers including äöü ÄÖÜ €, capitalization and three lines.
The first Mac viewer stayed black, and a normal reconnection's stale painting
recovered only after a native window transition. This remains an unresolved
reliability limitation; the evidence does not prove its cause. Synthetic automation
modifiers and actual physical Swiss keyboard shortcuts remain unqualified.

The new Mac startup correction registers every plugin and starts the pinned
Flutter engine before native view/delegate events. Failed startup returns a real
FlutterError and retains no broken window. The follow-up native close correction
removes a closing window from reuse immediately and retains its engine until
Dart replies, releasing outside the reply callback, with a five-second fallback.
This fixes the reproduced immediate engine shutdown before asynchronous delivery;
complete remote/file cleanup and process termination acknowledgement remain
under investigation. Root and independent peer passed33 drift/ownership controls,
original-failing and corrected Swift startup/close controls, the actual five-second
fallback and both reply races, complete pinned Flutter SDK checks for ARM12.3 and
Intel10.14, full branding/idempotence and17 release tests. Root rehashed311 closed
source evidence files, SHAe619cfc3523113b37e061388ab1920e96d4d964d5d48647fc622e866f6f86049.

The first unpublished Mac build38221542496 failed before installer compilation
because its Python test wrapper ignored helper arguments. The committed fixture
correction now executes both actual pinned helpers in order and rejects each
missing hook independently. Full pixel-buffer/Rust/Dart controls passed; root
rehashed its10-member proof, SHA4bbc214ec6dec67d037c7b4b00a51c7f9bef4754db8e2d707860b7bd3e2adca1.
The replacement [unpublished Mac build38224838596](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38224838596)
completed SUCCESS against exact16bf68d with publication disabled. Both source
preflights, compiled guard/PNG and signing/notarization passed. Root rehashed all258
installer proof files; both apps/DMGs pass signatures/staples/Gatekeeper and native
inventories. An isolated ordinary16bf Mac viewer then passed actual PC01 blue
Accept, VP9 video, mouse control and five native letter keys. Complete new live
acceptance remains unfinished; the first consent panel briefly appeared blank,
while a fresh retry rendered normally. The older closed clipboard/file proof below
remains bound to4f76633.

The separately committed b505e4a display-state correction synchronizes a new
engine from actual application visibility/activity through pinned public Flutter
callbacks. Root and peer reproduced the original unseeded hidden state and passed
all four lifecycle states, future focus/hide/restore callbacks,37 drift controls,
and complete Apple Silicon/Intel SDK checks. The actual stale-video cause and
packaged repair remain unqualified. The completed16bf installers do not contain
this later correction or the complete Dart cleanup now integrated in6dfe44d.
Its [Mac build38229357648](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38229357648)
failed: Intel hit a close-fixture timing assumption; ARM built and notarized the
app, passed cold URL/health smoke, then exited during warm URL restore before
DMG creation. Original logs are preserved. Commit4545dff fixes only the fixture
with three explicit valid queue orders and a separate actual5s fallback; root/peer
native and both SDK checks pass. The warm app exit remains under investigation.

Windows calling-thread observation is committed as9a8ff72 and keeps the native
access-denied outcome fatal. It observes the same fixture thread before the exact
unchanged event APIs, preserves their native errors and writes immutable records
only for an explicit CI request. Root and independent peer passed five executable
source controls with pinned Rust1.75, complete Windows console/GUI metadata checks
and the actual PS7.6.3 reader's12 valid/26 malformed cases. All16 frozen files were
rehashed, SHA15e765eb86fc5af41534140e5797139afcd670b874f53d6f02dd6a487c90ff75.
The subsequent c915e25 two-file test change requires real Desktop PS5.1 on
Windows and runs six focused controls before the unchanged native lease gate.
Root independently reran those six controls and rehashed the six-file ordering
proof, SHA39e859fa2fecb0788160ba25f663d488958e6c57a4d876351bbec8b9fd5d5f46.
Exactc915 [PR preflight38225298001](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38225298001)
completed FAILURE at the unchanged native access-denied5 gate. Six focused
controls passed on both real PS7.6.6 and stock DesktopPS5.1.20348.5622. All238
calling-thread observations report no thread token; they do not establish an
impersonation cause. Root rehashed its44-file evidence. Commitbaa7f1a fixes the
runtime helper's stock5 Int32 predicate and the premature-close pipe fixture.
Exactbaa [preflight38230808010](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38230808010)
passed actual stock Desktop5.1 integer/type/malformed/native-PID/read-only/lease
and framing tests, including the original parser failure. Root rehashed its51
proof files. The mandatory native5 gate still fails:52 named probes return5,
but4 unnamed create/close probes return0/0. A same-host/current-caller comparison
is being added; no native failure is ignored. These diagnostics do not qualify
the Windows product or release.

Real Linux and both Mac installers exposed a UI branding gap: Flutter's Mixel PNG
was missing and its toolbar used the upstream SVG. The committed correction
installs the authoritative icon, removes the fallback and adds fatal compiled
asset/manifest checks on all platforms. The follow-up4f correction also fixes four
capitalized translation values and the desktop footer link to https://mixel.ch.
Lowercase lookup keys and help URLs are preserved. Twelve focused controls,
complete fresh branding/idempotence,17 pipeline controls, syntax/diff checks and
independent review passed. Root matched all four executed4f Git blobs and sealed20
source-proof members.

The final 4f [Linux/both-Mac build38152004127](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38152004127)
completed SUCCESS; all three compiled asset gates passed. The same-source
[packaged Linux runtime38153851244](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38153851244)
also completed SUCCESS. Original archives and actual behavior passed replay in
native, HTTPS, mixed and saved-password consent modes: customer Accept/Disconnect,
changing video, keyboard/mouse, bidirectional clipboard/files, service/network
recovery, same GUI ownership and cleanup. The valid saved password remained
unauthorized for at least12 seconds until the customer clicked Accept. Root
rehashed all871 frozen files, five original ZIPs/788 members and20 actual PNG
phases, SHA `172649a80e418d1cab0248894c8d835798ca80749dbee22335fa55a0f5ce38d7`.
The actual DEB SHA is
`053cc4edcf296717ab6434771a5c46f9e195bfc79c6af7d2ababd7a897c9dd5f`.

Both current Mac installers passed signing, notarization/staples, Gatekeeper,
bundle/codec inventories, cold/warm launch and owned cleanup. Root rehashed
all220 frozen files and nine original ZIPs/52 members, SHA
`3fa46046618f90fffac330c63849240d67ab81cda04a8d3375c86f23771238a8`.
The DEB and both downloaded DMGs contain the exact Mixel PNG/manifest and no
upstream SVG. Actual current footer/localization patch execution is bound to the
successful build logs; translated UI and clicked-footer behavior are not yet
qualified. Eleven Mac warnings per architecture remain recorded. Full Mac
screen/input/TCC and oldest-OS hardware acceptance remain unfinished.

Separate [4f Windows diagnostic38152055680](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38152055680)
completed FAILURE. Application compilation, compiled attended guard/PNG, signing,
signature verification and ordinary portable launch passed. QuickSupport step31
failed at the same foreground/incomingPID5420/session2 with native access-denied5;
the95-second positive was not reached. The mandatory original-source outcome
step33 also failed, and customer/public upload skipped. Original artifacts and
logs are retained for diagnosis. Publication is false; source, compiled and
ordinary-to-QuickSupport95 gates remain fatal. Root rehashed its52-file failure
freeze, SHA `44cb87fa40d8b381fc4b9fd477fb87e9d330a7579e6db8a3fdd56559a4776abd`.
Sixteen Rust warnings/five summary headers remain recorded. Windows acceptance
is unfinished.

The previous8d [Linux/both-Mac38149813748](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38149813748)
completed SUCCESS. All three compiled icon/manifest gates passed. Original Linux
API archives/all356 members and native/HTTPS/mixed sessions passed replay, with
video/input, bidirectional clipboard/files, reconnect and owned cleanup. The
actual DEB and both independently mounted signed Mac installers contain the exact
Mixel PNG and no upstream SVG. Root rehashed403 Linux and214 Mac proof members.
Both Mac app+DMG signatures/staples/Gatekeeper, target bundle/codec inventories,
cold/warm launch and owned cleanup passed;11 warnings per architecture are retained.
The same8d [package runtime38151645133](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38151645133)
was dispatched before advancing source and completedSUCCESS. Independent artifact/behavior replay passed all four modes; root rehashed864 frozen members. This proof retains8d's
locale/footer limitations and cannot substitute for4f. The8d
[Windows diagnostic38149816059](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38149816059)
completedFAILURE after compiled guard/PNG, signing and ordinary portable launch passed. Its ordinary-to-QS baseline again failed before95seconds; root rehashed all52 original failure-proof members. Actual foreground/incomingPID6020/session2 showed native5;16 Rust diagnostics/5 summary headers remain recorded, and95seconds was not reached. Automatic source preflight preserved native5 with exact cleanup. Source/compiled/customer gates remain fatal.

The previous b613
[Linux producer38147874487](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38147874487)
completed SUCCESS; original API archives/all356 members and the native/HTTPS/
mixed video/input/clipboard/file/recovery sessions passed independent replay.
Its same-source [package runtime38149740298](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38149740298)
completed SUCCESS. All four modes, including saved-password consent, passed
strict artifact/behavior replay; root rehashed858 proof members. This verifies
the corrected replaced-window observation within its source scope; that DEB retains the old icon and cannot qualify the new assets.

The previous ef
[Linux/both-Mac38146937582](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38146937582)
completed selected SUCCESS. Both signed/stapled/Gatekeeper Mac installers passed
independent original API/bundle/codec replay, cold/warm launch and owned cleanup;
root rehashed211 frozen members. Actual disabled legacy setup caching and
preserved codec-cache restoration passed. Eleven warnings per architecture remain
recorded. Both ef installers retain the old UI icon. Full Mac screen/input/TCC
and oldest-OS hardware runtime remain unqualified.

The ef [Windows38146935547](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38146935547)
completed FAILURE after compilation, compiled attended guard, signing and ordinary
portable launch passed. Its same-PID ordinary-to-QS baseline returned native
access-denied5 before the95-second transition. The original source-outcome gate
also failed and blocked customer/public upload. The signed payload is retained
for diagnosis and cannot qualify Windows release. Automatic ef and b613
preflights also preserved that native failure. Source/compiled/customer gates
remain fatal in the new diagnostic build.

The prior exact eef
[Linux/Mac producer38145073414](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38145073414)
completed SUCCESS. Linux native/HTTPS/mixed producer sessions passed, and both Mac
installers compiled, signed and notarized. Independent Mac replay verified nine
original API ZIPs, signed/stapled/Gatekeeper app+DMG, target architecture/minimum
metadata, cold/warm launch and owned cleanup; root rehashed the157-member freeze.
Actual warnings remain recorded (13 ARM/13 Intel). Full real Mac screen/input
control still requires local host unlock; signing and launch cannot substitute
for that session or oldest-OS hardware runtime.

The separate eef
[package runtime38146778404](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38146778404)
completed FAILURE. Native and mixed sessions passed; ordinary HTTPS failed during
service restart because xdotool queried a replaced connection-manager window
(BadWindow). Earlier functional checks and the separate saved-password consent
step passed. Original failed-run artifacts remain preserved, and four-mode
qualification remains false. The narrow QA observation correction is committed in b6137a0:
reselect the matching CM inside the unchanged60-second bound and retry only that
literal activation/geometry error. The blue Accept criteria, current ownership,
capture and later customer click remain mandatory. The exact old method reproduces
the failure; root independently passed all58 corrected fixture tests,17 pipeline
tests and5 Linux collector controls. The b613 producer and four-mode package runtime passed as recorded above.

The prior eef
[Windows producer38145088436](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38145088436)
failed branding before compilation because Git archive applied Windows CRLF
defaults to88 of the98 pinned renderer files. The correction preserves exact LF
blob bytes with per-command options while retaining every revision/tree/inventory
check. An actual source-extracted Git regression and hostile-config fresh fetch
passed. Full Swift/CoreVideo/Rust/Dart/branding verification passed with explicit
Dart3.5.4/Rust1.75. An initial missing-Dart attempt remains recorded separately.
The ef Windows archive boundary passed; source and customer runtime gates failed
as recorded above. Current4f Linux runtime and scoped Mac installer qualification
passed as recorded above; Windows/full Mac application acceptance remains pending.

The Mac correction fixes retained closed CoreVideo buffers, failed texture ID0
registration and packed-row corruption in multi-display rendering. It retires
only the matching native pointer under the frame callback lock before freeing the
texture, preserves newer handoff generations, validates source/destination bounds,
copies rows using their actual strides and zeros padding. Both targeted CPUs
passed102 visible-pixel/padding cases, eight injected API failures and15 malformed/
overflow inputs, with valid-frame recovery. Native Rust lock, Dart async/analyzer,
18 source-drift refusals, complete branding/idempotence and actual pinned bridge
generation passed; generated-code analysis reported “No issues found!”. Root
rehashed the independent117-member source/native proof. The signed eef installer
proof above adds application evidence within its recorded scope.

The separately reviewed ef684fa cache correction disables run-vcpkg's obsolete
executable/ports setup cache and preserves actions/cache@v5 compiled codecs,
dependency pins, paths, keys and installation commands. Eight source-bound controls
prove setup still runs and failures remain fatal; workflow lint and17 pipeline
tests pass. Actual ef,8d and4f Mac setup/cache execution passed; the current4f
installer replay also passed. External SDK/deprecation warnings remain recorded.

The earlier e3
[Linux artifact runtime38140460128](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38140460128)
completed SUCCESS with publication false. Independent replay passed native/HTTPS/
mixed sessions plus saved-password consent: real customer Accept, changing clock/
RGB frames, keyboard/mouse, bidirectional clipboard/files, reconnect/service
recovery, same-GUI ownership, TLS443 and owned cleanup. Five original provider
ZIPs and all356 producer/432 runtime members were rehashed. This older proof stays
bound to e3 and cannot qualify later source. The earlier
[Windows diagnostic38138585648](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38138585648)
compiled and signed, and its ordinary portable launch passed; QuickSupport failed
the ordinary same-owned-PID baseline with native access-denied5. The original
source gate also remained fatal, and customer/public upload skipped. No complete
Windows customer or standard-account qualification is claimed.

The current source rejects ZIP member names truncated by the parser, including
entries that appear to be ignored directories. Three original validation gaps
were reproduced with actual synthetic ZIP bytes. Linux5 and Windows7 collector
tests and independent review pass; historical proof freezes remain immutable.
Corrected observation run38142676201 passed all9 actual Windows source tests and
three native A-before/B/A-after observations. Every untouched native probe stayed
access-denied5 despite successful USER32 initialization in B; owned cleanup passed
and every qualification flag remains false. This rejects that proposed GUI-context
explanation in the secondary-logon fixture and does not qualify the application.

The source-bound QA compilation in
[38120029931](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38120029931)
targets exact commit `c0a3d1ccb644a83df5615b2ac737cff10c94ed87`. Its thirteen-member
ZIP/twelve-member manifest/eleven committed source bindings were independently
verified. The overall run failed: the original Windows standard-account probe
returned native access-denied5; the other four original jobs and optional QA
compilation passed. Exact owned cleanup passed. Reproduced cleanup failures have
been fixed, but they do not establish the cause of the initial native5 result.

On the supplied PC01 desktop, both complete hash-verified observer scripts ran
under actual stock Windows PowerShell5.1.19041.6456. Ten pure source controls
passed, followed by four real native GUID lease observations: absent2, owner
active0, separate process active0, then absent2 after owner termination. Actual
owned cleanup passed. All execution-policy scopes were restored to Undefined,
effective Restricted, and the QA window was closed. PC01's user-selected account
belongs to Administrators: this current-caller result does not qualify genuine
standard-account8 or customer-application95 acceptance. The ignored verification
directory retains actual screenshots and a clearly labeled transcription.
The NAS session subsequently expired; the VM console requires user sign-in
before actual compiled application testing can continue there.

The platform-specific evidence below remains bound to its recorded source and
artifact; it does not qualify a new signed producer.

Mac source controls reproduce the original NULL pixel-metadata crash in actual
CoreFoundation on ARM macOS12.3 and Intel macOS10.14 targets. The corrected
unknown-depth fallback, known encodings, once-only authorization cleanup and
unused communications-pipe handling pass all 16 native controls per architecture;
22 drift/upgrade rejections, full branding idempotence and actual pinned Rust1.81
final links pass. Intel runs via Rosetta on this ARM Mac. These controls mock
privilege calls and use read-only permission checks with `prompt=false`.
They do not grant TCC access or prove a complete Mac screen-control session.

The historical signed Mac build
[38074383950](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38074383950)
at `984578e98563cb26abca8817b94babcde2d8b4d5` passed both architecture jobs,
including the integrated bundle-minimum gate, actual v6 certificate import and
keychain cleanup, compiled consent, signatures, Accepted app/DMG notarization,
stapling, Gatekeeper and cold/warm registered-ID/v2/relay/key launch. Independent
read-only archive and bundle replay passed. Its overall run remains failed
because Windows MSVC activation failed. Those signed Mac artifacts precede the
new Mac fixes and cannot qualify the current source.

Windows native MSVC activation and actual C/Rust clipboard AddressSanitizer
controls passed with all five preflight jobs in
[38075972859](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38075972859)
at `beb1f45fdfdc8a03eaf439d03a8410f10cdd5309`. Producer
[38076082858](https://github.com/Jangopeople/mixel-remote-client/actions/runs/38076082858)
then passed those native steps but failed source preflight after Git Bash selected
Git's `link.exe`; compilation, signing and customer launch were skipped. The
current source selects the exact imported x64 MSVC linker after Bash startup in
all four Rust build/test steps and adds a real native Rust compile/run preflight
with Unicode paths and arguments. Compilation, signing and ordinary portable
launch have since passed in the current4f producer recorded above. Genuine
standard-account and signed QuickSupport95 acceptance remain unfinished; the
native5 failure is preserved.

Native Linux four-mode session and saved-password consent proof remains qualified
against its exact earlier DEB; source/run digests and retained failures are in
`HANDOFF.md`. Complete acceptance requires the fresh artifact-specific gates and
the production HTTPS gateway's exact approval and verification. Michael's
production approval is pending. These proofs establish the recorded behaviors;
they do not establish complete TeamViewer/pcvisit feature parity or an absence
of every possible bug.

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
- Mac display-mode metadata may be absent. The native helper returns unknown
  depth `0` before CoreFoundation comparison/release. A successfully created
  authorization reference is released once on cancellation, denial or errors,
  and the legacy execution path does not request an unused communications pipe.
  The deprecated public compatibility APIs remain; their warnings are retained.
- Every bundled Mac native binary is checked before signing: the requested CPU
  must be present and its encoded deployment minimum must fit the bundle metadata.
  Internal framework links are resolved within the app; a failed rerun removes any
  stale successful proof. Certificate import uses the audited Node24 action and
  removes its temporary build keychain after the job.
- Windows clipboard cleanup belongs to its creating context. Failed initialization
  and Rust error-Box Drop release native resources once; an unnamed mutex avoids
  an unnecessary shared cross-process object. The worker receives shutdown before
  a late window can wait, destroys its own window/listener and joins before the
  pending response, module reference and context state are released. Native
  AddressSanitizer controls exercise the actual pinned C and Rust create/Drop.
- Windows imports the installed x64 MSVC environment through an owned batch file.
  Each Bash step that compiles Rust then sources
  `scripts/use-windows-msvc-bash.sh` after shell initialization, checks the exact
  installed compiler/linker identity and prevents Git's `link.exe` taking priority.
- Linux produces a branded Debian package and verifies its actual cold/warm
  customer launch under an isolated X11/DBus desktop.

Internal Cargo/library symbols needed for upstream linking remain intentional.
Do not commit `rustdesk/`, build output, credentials, or private relay keys.

## Verification

PR preflight validates Python, release configuration/staging, the real pinned
source patch and its idempotence, Dart analysis/behavior, generated Rust updater
and startup branches, and Windows payload/framed-IPC behavior. Native Windows CI
also executes actual C/Rust clipboard ownership/AddressSanitizer controls and a
Rust compile/run under Git Bash. Native Mac preflight exercises the extracted
CoreFoundation/authorization failure boundaries, deployment availability and the
actual pinned Rust final link. Platform builds also test the compiled common-crate
guard and execute the customer launch path.

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
python3 scripts/test-support-windows-clipboard.py
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
python3 scripts/test-macos-bundle-minimum.py
python3 scripts/test-windows-release-runtime.py
python3 scripts/test-branding.py
python3 scripts/test-support-launch-linux.py --self-test
python3 scripts/test-support-session-fixture.py
pwsh -NoProfile -File scripts/test-windows-pipeline.ps1
python3 -m compileall -q scripts
bash -n scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh scripts/use-windows-msvc-bash.sh
shellcheck scripts/apply-branding.sh scripts/local/sign-and-publish-macos.sh scripts/use-windows-msvc-bash.sh
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
Set `runtime_windows_release_gate=true` together with `runtime_artifact_run` to
run the normal customer QuickSupport handoff against a completed successful
Windows build. The collector independently verifies both GitHub ZIP digests,
same-source producer job, exact signed launcher aliases and inner payload
checksum. Both Windows runner versions use the actual signed portable QS outer
launcher, ordinary process creation, and the unchanged95-second PID/HWND/IPC/
consent-owner requirements. This mode skips native crash-debugger preparation;
publication and mixing other platform artifact inputs are rejected.
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
