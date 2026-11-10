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

## In the pipeline
`apply-branding.sh` takes the layer out first (`--restore --check`, then `--restore --guarded`) and
applies it last, after every receiver layer. `--guarded` proves on a private copy that the earlier
owners' refusal gates accept the tree before anything is written, so a later refusal still leaves
the source untouched. `receiver_input_integration_projection.py` sees through the two invocation
blocks (`elevation_view`) and its private-copy helpers step under the layer first.
Checked on 2026-11-10: pipeline from a clean 1.4.6 checkout and on a repeated pass (identical
output), `test-support-elevation.py`, `test-branding.py` (33 checks, with `RDREPO` set),
`test-input-lifecycle-integration-projection.py`, `test-receiver-input-integration.py`,
`test-workflow-logical-views.py`, `test-support-receiver-input.py`, `test-release-pipeline.py`.
Not run here: the native Windows build driver (`scripts/local/build-windows.py`, needs win-build);
its next run will include the layer through `apply-branding.sh` and record new source receipts.
`scripts/patch-support-no-elevate.py` is no longer needed and is not called.

## Known limits
- Privacy mode and "block user input" are switched off by a helper switch and must be turned on again.
- If the helper dies mid-session its scopes cannot be retired; the session ends after 60 s (before: at once).
- Not tested: elevation with transmitted administrator credentials, Windows 11.

## Lab test
See the release README: QA VM 133 (Windows 10) as the PC; controllers: the Mac app and the Linux Quick
Support on QA VM 132, driven with QMP clicks (`/root/qa133-qmp.py`, `/root/qa132-qmp.py` on mixel-proxmox).
