# Mixel Remote performance audit — 23 November 2026

Scope: the installed native Mixel Remote 2.1.21, starting from shipping source
`3feffe3` on `release/2.1.1`. The root checkout's RustDesk pipeline is historical;
the audited implementation is the shared Rust engine and native desktop apps in
`next/`. The user's primary symptom was opening the app and connecting.

## Confirmed corrections

| Severity | Finding and reproduction | Correction / proof |
|---|---|---|
| P2 | Mac and Linux awaited a fixed 600 ms timer on initial device presence. The production Mac method measured 0.603461375 s against a scripted transport, failing its 300 ms budget. | Forced initial checks skip the background grace timer, matching Windows. The same method measures 0.000042541 s after correction. This is timer overhead, not an end-to-end network benchmark. |
| P2 | Startup fetched members and invitations before fetching devices. The original Apple test recorded members at command 3 and devices at command 6; the Windows regression also failed. | Startup loads company/plan context first, then enrollment and devices, then administration lists. All three platform regressions pass. Windows starts normal registration before the administration requests. |
| P2 | The real pacer bottoms out at 75,000 bytes/s, while the 540p threshold was 65,000. A test using the actual pacer returned 1280x720 where 960x540 was required. | The threshold is 85,000 bytes/s; recovery remains 110,000. The test now passes and preserves hysteresis and the existing encoder budget. The suspected encoder-floor overshoot was disproved by its budget test. |
| P2 | After a raw-to-compressed handover, consuming the compressed queue replayed an older raw picture. The original regression failed on the extra stale packet. | The first admitted compressed picture retires the raw fallback. The early raw picture remains available until compressed video actually arrives. Ownership, ordering and transport-renewal tests pass. |
| P3 | Swift captured a mutable decoder variable across a main-actor hop; the connection test also captured an unsynchronized mutable counter. | Snapshot the decoder mode before the hop; synchronize the test counter. All 126 Apple tests compile with warnings treated as errors. |
| QA | The broader TURN test suite did not compile because its fixture omitted the new `Route.defer` field. | The isolated backpressure fixture explicitly uses zero defer. All 39 protocol tests, including encrypted TCP/TLS TURN, pass. |

## Dependency review

Updated cookie 0.18.2 to 0.18.3 and url 2.5.8 to 2.5.9, and the Linux migration
reader to rusqlite 0.41.1 / libsqlite3-sys 0.39.1. These clear
[RUSTSEC-2026-0594](https://rustsec.org/advisories/RUSTSEC-2026-0594),
[RUSTSEC-2026-0592](https://rustsec.org/advisories/RUSTSEC-2026-0592) and
[RUSTSEC-2026-0459](https://rustsec.org/advisories/RUSTSEC-2026-0459) in the lockfile.
This does not claim all advisories were exploitable in this app. The SQLite
reader links the system library; the dependency upgrade is not an OS SQLite patch.

`next/deny.toml` accepts seven individually identified maintenance notices with
reasons, without suppressing vulnerability advisories. Bincode replacement needs
a versioned peer-protocol migration; gettext's encoding dependency and the
platform D-Bus/macro dependencies need compatible parent-library migrations.
These are recorded maintenance work, with no patched compatible versions listed
by the advisories. No Linear connector is available in this session, so this
repository is the tracking record.

Manual security review followed the changed data paths: no authentication,
consent, encryption, tenant scopes, wire format or file-access privileges were
relaxed. Semgrep ran 83 applicable rules across the changed Rust/C# production
files with zero findings. Swift was reviewed manually and compiled with warnings
as errors. No Sentry integration is present in these native client source paths;
local performance telemetry and native tests supplied runtime evidence.

## Actual verification

```text
Rust protocol / signaling / engine / capture / bridges: 367 passed; 0 failed
Apple native UI/model suite: Executed 126 tests, with 0 failures
Windows native model suite: Failed: 0, Passed: 140, Skipped: 0
Linux native suite: 88 passed; 0 failed
Linux display-only GTK regression: 1 passed; 0 failed; 0 ignored
Clippy (Mac six crates and native Linux): -D warnings, exit 0
Semgrep: Ran 83 rules on 5 files: 0 findings
cargo deny check advisories: advisories ok
Universal Mac Release: ** BUILD SUCCEEDED **
Linux native executable build: Finished dev profile
```

The Apple suite renders the actual screens offscreen in light/dark and locale
fixtures. No layout redesign was made. Real Mac encrypted peers also passed live
H.264 video, pointer/key/wheel/release, clipboard, chat, drawing protocol,
byte-exact file transfer and readable MP4 output. This is a local native session,
not a new public cross-country latency qualification.

The explicit manual exclusions in the Rust suites are the local printer-list
mutation test and the encoder benchmark. Linux's display-only GTK test also passes separately under Xvfb with the Cairo
renderer and local VFS (1 passed, 0 failed, 0 ignored). Total: 722 distinct tests
passed. The first display-only invocation used the VM's unavailable GPU/GVFS;
explicit fixture renderer/VFS settings remove those environment warnings without
changing the application.

## Installed application and live check

Updated `/Applications/Mixel Remote.app` in place from build 226 to 227, keeping
version 2.1.21, bundle ID `ch.mixel.remote.next.qa`, both Intel and Apple Silicon,
and Developer ID team `5277F8NDH4`. The signed designated requirement matches the
previous installation exactly. Deep/strict signature verification passes.
Executable SHA256:
`3039be64f22967d6344855ed599bcf0b2d00f2df6f7d82f309ba55cc84cf9710`.

The previous app exited through its normal Quit menu. The updated installed app
opened, restored the signed-in account, retained its Remote ID and displayed all
11 existing devices and their online state. No account reset or reenrollment was
needed. The temporary lab app was removed and unregistered; its DerivedData,
framework build, old-app copies and duplicate app registration were cleaned up.
There is one Mixel Remote app in Applications and one running installed process.
No customer download alias, marketplace listing or public release was changed.

## Warning decisions

- OpenH264 warns about disabled internal frame skipping and its slice-size
  constraint. These are intentional existing settings: the outer pacer owns
  backpressure, and the regression proves the final changed picture is emitted.
  Enabling internal skipping again would reintroduce the recorded low-FPS issue.
  Keep the settings; any future codec change must measure delivered FPS, frame
  size and the final static screen under bandwidth limits.
- Xcode skips AppIntents metadata extraction because this app has no AppIntents
  dependency. There are no intents to extract; no runtime feature is missing.
  The actual Swift compiler passes with warnings treated as errors.
- The standalone native-session test cannot open the app-bundled drawing overlay;
  it still verifies the encrypted annotation protocol. It is not overlay UI proof.
- The first Linux build was killed by guest OOM: Proxmox had ballooned the QA VM
  to 2 GB. With verified host headroom, its existing 6 GB allocation was retained
  temporarily, compilation used one job and no debug symbols, and tests/build/
  Clippy passed. The original balloon setting and stopped power state are restored
  after QA. No production VM was changed.

## Follow-up: installed public-service connections, 23 November 2026

The original audit did **not** include a connection from the installed controller
to the user's two real online computers. Its earlier completion statement was too
broad. This follow-up closes that specific verification gap without presenting it
as an exhaustive audit of every deployment or an explanation of every earlier
failure.

- Installed build 227 connected to the Windows laptop through its normal Connect
  action. The live remote desktop appeared; a remote application window was
  dragged and restored, proving actual input delivery. Normal End session passed.
- A second Windows Connect also reached ready, with 4.268 s between the CUA click
  and its ready accessibility observation (includes observation overhead).
  That repeat session also ended normally.
- While the first Windows session was active, normal Connect to the MacBook also
  reached ready. Its desktop opened and the displayed live decoder rate changed
  between 17, 35 and 19 FPS; local telemetry confirms hardware decoding and new
  received frame samples. The two actual sessions coexisted and Mac End passed.
- The installed app remains open and registered, with zero test session tabs left.
  No additional app was built or installed during these checks.
- Broker health returned HTTP 200; public TURN UDP binding and certificate-verified
  TURN TLS both succeeded. These probes alone are not session acceptance; the
  live desktop observations above are the session evidence.

The current default `WhenReady.stay` leaves the technician in Devices/Sessions and
adds an Open notice and a session tab. Both observed connections needed Open/Show
before the remote desktop was displayed. This is an existing explicit navigation
choice, not a reproduced transport refusal. The user was asked whether their
failure means staying in Devices, remaining on Connecting/Starting video, or an
error/black picture. Do not claim the reported failure's cause is established
until that distinction is known. No navigation preference was silently changed.
Remote desktop images were not saved into repository evidence or PR attachments.
