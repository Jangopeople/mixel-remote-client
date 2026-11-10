# Quick Support elevation ("Accept and elevate")

Status 2026-11-10: the buttons are hidden in Windows Quick Support `1.4.6-support.noelevate.1`
(store.mixel.ch). The elevated mode itself is not fixed.

## What happens with the buttons shown
- Plain **Accept** works: video starts, the session stays up.
- **Accept and elevate**: the session is approved, then dropped about 0.1 s later without any message;
  the Mac reconnects by itself and the PC shows a new, blank white consent window. Same pattern as
  Michael's test in the relay log (six pair/close cycles 07:36–07:38 UTC).
- Cause (patched source, `src/server/mixel_receiver_input*.rs`, `src/server/connection.rs`):
  1. The elevation helper start is an "epoch boundary": every existing input generation is failed
     (`backend.matches_current()` false) and the 1 s timer closes the connection with the internal
     reason "Input queue capacity or helper trust failed". The controller is told nothing and retries.
  2. While the switch is in progress (`TRANSITION`), a new approval is refused
     (`Generation::new_role` returns None, so `receiver_input.authorize()` and the login fail).
  3. The helper must confirm within 10 s (`mixel-helper-startup` thread) or it is terminated. That
     window includes the time the person needs to answer the UAC prompt, so elevation rarely completes.
     After UAC "Yes" no `--portable-service` process was left running.
  4. The re-shown consent window paints blank white.
- The earlier QA of this build only ever used the ordinary Accept button (HANDOFF.md, 2026-11-07).


## Still open (the proper fix)
- Keep the approved session across the helper start: rebind the input generation to the new
  backend when the helper is confirmed (or back to the local custodian when it is refused), and
  tell the controller while input is paused, instead of closing with an internal reason.
- Start the helper's 10 s confirmation deadline after the UAC prompt was answered.
- The controller's own "Request elevation" menu reaches the same code path
  (`handle_elevation_request`) and still drops the session; it is not covered by the hidden buttons.
- The re-shown consent window painted blank white after UAC in the lab run.
- `scripts/patch-support-no-elevate.py` is not wired into `apply-branding.sh` / the build drivers.

## How the lab test was run
VM 133 (Windows 10, `ssh qa-win10`), Quick Support started in the desktop session by a scheduled
task, controller `/Applications/Mixel-Remote.app --connect <id>` on the Mac Mini, public relay.
Screenshots through Proxmox `screendump`; clicks through QMP `input-send-event` (the consent window
ignores injected clicks, and UAC runs on the secure desktop). Evidence and scripts:
`mixel-craft/_scratch/remote-audit-20261110/` and
`mixel-craft/releases/mixel-remote-qs-windows-1.4.6-noelevate.1/`.
