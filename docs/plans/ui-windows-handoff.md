# Windows "Pro Console" port: handoff (17 Nov 2026)

Branch `feat/ui-win-20261117` (from `feat/ui-20261117` 7b6dbc3). Windows app: `next/apps/mixel-remote-windows`.

## Built
- Core (no WinUI, unit tested): `WorkspaceModel` (tabs per Remote ID, pop-out/dock, connect attempt with 4 steps, ready notice,
  `WhenReady`/`OpenIn` persisted in `IPreferences`), `DevicesConsole.cs` (views All/Online/Favourites/Recent/group, filters,
  grouped table sorted by status, search incl. note), `DeviceAdmin.cs` (`IDeviceAdmin` + `EngineDeviceAdmin` over the
  `remote_admin_*` commands of the device-admin API; the engine has none yet, so badges/notes appear only when it answers),
  `ReportsModel` (period/technician/device, notes, CSV, dependency-free PDF), `PointerPolicy`, password-session warnings
  (`remote_status.password_session.ends_at`, not reported by the engine yet), appearance setting.
- App: `Shell/MainWindow*.cs` (title-bar tabs, sidebar, notices), `Pages/DevicesPage.cs` (table), `SessionsPage`, `ReportsPage`,
  `SettingsPage` (6 sections), sign-in layout in `AccountPage`, `Session/SessionView.cs` (toolbar of the mockup),
  `Session/PopOutWindow.cs`.
- Texts: every new string in `scripts/windows_texts.py` (de/fr/it), `python3 scripts/make-texts.py`.

## Not done / unverified
- No launch: win-build has no interactive session (`qwinsta`: console at logon screen), WinUI exits 0xC000027B in session 0
  and as a SYSTEM task. Needs a logged-on desktop (VM 134 or a logged-in builder) for `mixel-remote.exe --screenshots <dir> light|dark`.
  New demo screens: connecting, ready-notice, session-tab, popout, sessions, reports.
- DPI 100-200%: layout is in DIPs, the table drops Version below 1100 and Last seen below 900 DIPs; not looked at on screen.
- Engine: `remote_admin_*`, several sessions at once, `password_session` (docs/mixel-remote-ui-engine-requests.md).
- Picture-quality popover is read only (the engine has no quality setting); the host cursor is never drawn into the picture,
  so the local cursor is the one pointer (`PointerPolicy`; hidden only if a host ever draws its own).

## Build / test (win-build, own folder W:\MixelRemoteBuild\ui-win-20261117)
`_scratch/win/sync.sh <worktree>`; on win-build `ui-win\test.ps1`, `ui-win\build.ps1 [-Publish]` (scripts are gitignored).
