# Linux Pro Console port: status (17 Nov 2026)

Branch `feat/ui-linux-20261117` (from `feat/ui-20261117` 7b6dbc3). App: `next/apps/mixel-remote-linux`. Version unchanged (2.0.18).

## Built
- `workspace.rs`: port of the Mac `WorkspaceModel` (tabs, connecting attempt and its 4 steps, ready notice, pop-out/dock, remembered "when ready" / "open in"), pure, 13 unit tests.
- `view.rs`: console filters (views, Online/Unattended/Update/OS chips, search by name, ID, note, sort), version compare; 5 tests.
- `ui/mod.rs`: AdwTabBar/TabView (Devices + one tab per session + "+"), sidebar, section pages, floating cards, pop-out windows, sign-in gating, appearance.
- `ui/devices.rs` (console table), `sidebar.rs`, `sessions.rs`, `cards.rs`, `signin.rs`, session toolbar (`session.rs`), Settings groups (Sessions, Unattended access, Updates, General + Appearance, Account & security, About).
- Persisted in `settings.json`: `when_ready`, `open_in`, `appearance`.
- All new strings in po/de, fr, it (via `scripts/make-po.py`).

## Not wired (needs the engine)
- Version / Unattended badges and device notes read `remote_admin_devices` (docs/mixel-remote-device-admin-api.md) in one place, `App::load_device_meta`. The command does not exist in the engine yet: the call fails quietly and the table shows no badges ("—" for version).
- Quick Support button on the sign-in screen opens https://remote.mixel.ch (no anonymous-support engine command).
- Several simultaneous sessions: the engine reports one; `Workspace::session_tabs` is the only place to extend.

## Checks
- Build container: `_scratch/dock` (Debian 12, arm64, Rust 1.99.1); `_scratch/shots.sh` renders every scene via `--screenshots`.
- No live session was run (owner's decision).
