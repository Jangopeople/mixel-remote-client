# Quick Support elevation ("Accept and elevate", "Request elevation")

Status 2026-11-10: fixed in the Windows core by `scripts/patch-support-elevation.py`; signed release
`1.4.6-support.elevate.1` is the current Windows Support file on store.mixel.ch
(see `mixel-craft/releases/mixel-remote-qs-windows-1.4.6-elevate.1/`).

## Why it never worked
1. After Windows consent the helper is a successor process running as SYSTEM (launched process, then
   an elevated process, then the SYSTEM process). The input layer accepted only the directly launched
   process (`startup_confirmed`), and the broker cannot open a SYSTEM process to identify it
   (`pipe_peer`), so the helper was dropped at its first pipe message.
2. `quiesce_helper_transition` marked every input owner dead and failed; the connection's 1 s timer
   then closed the session with an internal reason. New approvals were refused while the switch ran.
3. The `mixel-helper-startup` thread gave the helper 10 s, including the time the person needs to
   answer the consent prompt.

## What the patch does (apply after `patch-support-input-lifecycle.py`)
- `mixel_receiver_input.rs`: a helper start, stop or refusal ends the scopes of the old backend, never
  the approval. Owners get a `rebind` flag; `Receiver::rebind_tick` (called from the connection's 1 s
  timer) retires the old scopes through their own backend and opens fresh ones on the current backend
  once the switch is settled. `authorize`/`set_permission` during a switch are deferred, not refused.
  Still true: an existing scope never changes backend, and a fresh grant waits for every old receipt.
- `mixel_receiver_input_windows.rs`: `lend_helper` (helper lends the broker a synchronize/limited-query
  handle to itself) and `pipe_helper` (broker accepts one successor per open start: PID and session from
  the kernel pipe, image must be this exe, identity on the leases fixed to SYSTEM so anything else fails
  closed). Deadline: 150 s for consent, then 20 s for the helper; exit code 1 of the launched process
  means consent was refused and recovery starts at once.
- `ipc.rs`, `portable_service.rs`: new first message `ReceiverHelperHandle`; a second start request
  during a switch is refused before it can reset the shared memory.
- `connection.rs`: calls `rebind_tick`. `core_main.rs`: exit code 1 when consent fails.

## In the build
`scripts/local/build-windows.py` applies the layer as its last source step (`elevation-layer`:
`--check`, then apply), directly after `windows-compiler-integration` and before `fetch-locked`.
This is the order the published build was made in.

It is deliberately NOT in `apply-branding.sh` (tried in bbb9a0d, taken out again):
- `patch-windows-compiler-integration.py` pins the complete branded files (`beforeSha256`) of three
  files the layer changes and would refuse;
- the shared Mac bridge capsule binds the whole Rust source closure, and the native fixtures in the
  CI workflow check the final branded files (`--source-root "$RDREPO"`);
- the receiver projections pin `apply-branding.sh` itself.
So the shared pipeline, the projections, the bridge and the fixtures keep their pinned source; only
the Windows build adds the layer on top. Mac and Linux builds do not carry it and do not need it
(the change is local to the Windows host; controllers are unaffected).
The break-glass GitHub workflow's Windows job does not apply the layer: a build made there has no
working elevation. The script keeps `--restore` (exact inverse) and `--restore --guarded`.

Checked on 2026-11-10:
- win-build's Python, final script, on the shipped core's source (`_scratch\diag-r2\b\rustdesk`):
  `--check` ok, apply gives six files with the same SHA-256 as the source compiled for
  `1.4.6-support.elevate.1`; `--restore` gives the original six files back.
- Mac: `apply-branding.sh` at this branch on a clean 1.4.6 checkout, then the compiler profile for the
  three shared files, then the layer: same six files (line endings aside).
- `test-support-elevation.py`, `test-local-windows-build.py` (Python 3.11, step order pinned),
  `test-windows-compiler-integration.py`, the three receiver/workflow projection tests,
  `test-release-pipeline.py`; with bbb9a0d's wiring also `test-branding.py` (33 checks).
Not run: the driver itself on win-build. It needs 80 GiB free (68 available on 2026-11-10), the
repository checkout there at this commit, and a bridge capsule from a Mac build of this commit; its
last full attempt there (w10, 2026-11-04) stopped at `check-entire-app`, later builds were
continuations of the compiled core.
`scripts/patch-support-no-elevate.py` is no longer needed and is not called.

## Known limits
- Privacy mode and "block user input" are switched off by a helper switch and must be turned on again.
- If the helper dies mid-session its scopes cannot be retired; the session ends after 60 s (before: at once).
- Not tested: elevation with transmitted administrator credentials, Windows 11.

## Lab test
See the release README: QA VM 133 (Windows 10) as the PC; controllers: the Mac app and the Linux Quick
Support on QA VM 132, driven with QMP clicks (`/root/qa133-qmp.py`, `/root/qa132-qmp.py` on mixel-proxmox).
