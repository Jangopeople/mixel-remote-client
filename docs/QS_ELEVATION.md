# Quick Support elevation ("Accept and elevate", "Request elevation")

Status 2026-11-10: fixed in the Windows core by `scripts/patch-support-elevation.py`; signed test
release `1.4.6-support.elevate.1` (see `mixel-craft/releases/mixel-remote-qs-windows-1.4.6-elevate.1/`).
`1.4.6-support.noelevate.1` on store.mixel.ch still hides the buttons until the new build is published.

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

## Not wired into the pipeline yet
`apply-branding.sh` and the Windows build driver do not call `patch-support-elevation.py`; their
source inventories and receipts would have to be regenerated. Until then apply it by hand to the
prepared tree (`python3 scripts/patch-support-elevation.py <rustdesk-root>`, idempotent, refuses on any
mismatch). `scripts/patch-support-no-elevate.py` is only needed for builds without this patch.

## Known limits
- Privacy mode and "block user input" are switched off by a helper switch and must be turned on again.
- If the helper dies mid-session its scopes cannot be retired; the session ends after 60 s (before: at once).
- Not tested: elevation with transmitted administrator credentials, Windows 11.

## Lab test
See the release README: QA VM 133 (Windows 10) as the PC; controllers: the Mac app and the Linux Quick
Support on QA VM 132, driven with QMP clicks (`/root/qa133-qmp.py`, `/root/qa132-qmp.py` on mixel-proxmox).
