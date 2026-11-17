# Several sessions at once: Windows app (Mixel Remote 2.1.0)

Branch `feat/multi-20261117`. App: `next/apps/mixel-remote-windows`. Engine API: `docs/plans/multi-session-engine-api.md`.

## What changed
- **Status**: `RemoteStatus` decodes `sessions[]` (`SessionStatus`: every field of the API doc, `quality` included) and `max_sessions`.
  An engine that sends none reads as one session made from the single-session fields (`TechnicianSessions`, `SessionLimit` = 1).
- **One model per session**: `AppModel.Sessions` / `SessionFor(peer)` hold a `SessionModel` for every listed session (keyed by the device's
  Remote ID, bound to the stable `id`). Every engine call of a session carries the stable id (input, `next_video`, clipboard, draw, chat, record,
  invite, switch sides, `remote_close`), so a transport renewal (new `session`, same `id`) keeps the tab, the model, the picture and the focus.
  `AppModel.Session` is now "the focused session, else the oldest, else the model without a session" (the hosted side still uses that one).
- **Tabs and windows**: `WorkspaceModel.SessionTabs` makes one `SessionTab` per running session (`Id` added); pop-out windows are per peer as before.
- **Focus**: `WorkspaceModel.Focused` is the one session that gets keyboard, mouse, wheel, clipboard, toolbar actions (privacy, block, lock, restart,
  display, draw, record, chat/file sends, invite, switch sides). It is the session of the active pop-out window (`WindowActivated`/`WindowDeactivated`),
  else the tab in front while the main window is active (`MainActivated`). Unfocused sessions send nothing, not even a toggled toolbar state;
  only a release of held keys is let through, and only if that session was sent input before. The keyboard hook of a background screen never swallows keys.
- **Pictures**: every session fetches `next_video(id)` on its own; a session whose tab is not in front (and has no window) keeps its last picture and
  stops fetching after its first picture (`WorkspaceModel.IsVisible`). Hidden session views are not redrawn at every status.
- **Close**: closing a tab/window or "End session" calls `remote_close(id)` for that session only (also cancels a request that is still `outgoing`).
  Tray "End session" ends the shared session, the focused one or the only one; with several and none in front it opens the window.
- **Connect**: stays usable while fewer than `max_sessions` are open (`AppModel.CanRequestSession` / `CanStartConnection`; no `phase == "ready"` gate;
  while one is still being prepared the next may start). A 5th request is sent to the engine, which answers `too_many_sessions`; the JSON error text is read
  (`EngineFailure.Hint`/`Max`) and shown as "You already have 4 sessions open, the most Mixel Remote allows. Close one to start another."
  (also `already_connected`). Texts in de/fr/it: `scripts/windows_texts.py`, `python3 scripts/make-texts.py`.
- **Restart of the remote computer**: the reconnect plan now lives in a dormant model that outlives the ended session and reconnects when the computer is back.
- Files: `Models/Models.cs`, `Engine/Engine.cs`, `Engine/MockEngine.cs` (per-session pictures, per-session input log), `ViewModels/{AppModel,SessionModel,WorkspaceModel,DevicesModel}.cs`,
  `Demo/DemoFixtures.cs` (`Multi`), `Session/{SessionView,PopOutWindow,RemoteScreen,ToolPanel}.cs`, `Shell/{MainWindow,TrayIcon}.cs`, `Pages/{RemotePage,DevicesPage}.cs`.

## How it was tested
- Unit tests (`tests/MixelRemote.Core.Tests/MultiSessionTests.cs`, plus the adjusted older tests): `dotnet test` on win-build (`_scratch/win/test.ps1`, folder
  `W:\MixelRemoteBuild\multi-20261117`): 2/3/4 sessions give 2/3/4 tabs, focus routing of input/clipboard/toolbar, pop-out window focus, unfocused sessions send
  nothing, closing one ends only that session, a session end removes only its tab, 5th request error, fallback without `sessions`, transport renewal keeps the tab,
  background session stops fetching, JSON decoding of the full status, typed errors in en/de/fr/it.
- The app (`MixelRemote.App`) builds on win-build; the engine (`mixel_core_capi`) builds there too (`_scratch/win/rust.ps1`) and the app copies that `mixel_core.dll`.

## Not verified
- The app was not started: win-build has no interactive desktop, so tabs, pop-out windows, the toolbar and the real picture of four sessions were not looked at,
  and focus events of real windows (`Activated`) were not exercised; only the model rules behind them are tested.
- No live run against a real engine with four hosts (the engine has its own tests and a broker test).
- Starting a second connection while the first is still prepared replaces the connect panel of the first (its session still turns up as a tab/notice, but a
  refusal of the first would not be shown in the panel).

## After the merge of feat/2.1.0 (engine: make-before-break renewal, stable `sessions[].session`)
- Merged tree rebuilt on win-build: engine (`mixel_core_capi`) builds, app publishes with the new `mixel_core.dll`; `dotnet test`: 94 passed, 0 failed;
  `cargo test -p mixel_engine --lib multi_session`: 6 passed. Keying by `id` is unchanged, so nothing in the app depended on `session` changing at renewal.
- `SessionModel.ReconnectingNotice()` exists per session model and the session view shows it with `PasswordWarning()`. `reconnecting` is connection-wide,
  so the banner appears in every open session (correct). `password_session` is a single top-level field of the engine (not per session): with several
  sessions the password-limit warning would show in all of them. Engine need: `password_session` inside each `sessions[]` item.
- Still not verified: no interactive launch (see above).

## 2.1.0 build-complete (Windows)
- **Per-session password and unattended**: `SessionStatus` decodes `password_session {ends_at, seconds_left}`, `unattended_session`, `reconnecting`, `reconnect_failed`.
  `SessionStatus.Project` now hands each session model its own `PasswordSession` / `UnattendedSession` (the top-level values only serve an older engine
  without `sessions[]`, via `LegacySession`). The 5-minute and 1-minute warning therefore shows only in the session it belongs to.
- **Reconnect**: a session with `reconnecting` or `reconnect_failed` keeps its tab (states `reconnecting` / `failed`), its window and its model; the empty screen
  says "Reconnecting..." or "Could not reconnect" (`SessionModel.WaitingText`), and after the minute a Retry button sends `remote_reconnect` with the stable id
  (`SessionModel.Retry`). End (toolbar, tab, Sessions page, window close) sends `remote_close` with the stable id, which does not depend on the top-level
  `session` (empty while retrying). "Cancel" of a request no longer ends a session that only asks its computer again (`SessionStatus.Asking`). A session that
  comes back is not announced again as "ready".
- **Closing a session window ends the session**: `WorkspaceModel.WindowCloseRequested` decides (ask "End session with <device>?" when its session runs, close
  silently when it is gone), `ConfirmWindowClose` ends only that session; `PopOutWindow` shows the ContentDialog. The toolbar's "Back into a tab" (`Dock`) is
  the only way to dock. Same as the Mac.
- **Gaps D**: the favourite star (list row, context menu, Favourites view) already exists on Windows and is the same call as on the Mac and Linux
  (`saas_set_favorite` / `saas_favorites`, company devices only; `remote_admin_set_device_favorite` is the admin-console variant and is not used by any app
  for the personal star); tested in `Favorites_come_first_and_recent_devices_are_offered`. Picture quality stays read only: `native_commands.rs` has no
  command that sets quality, bitrate, or frame rate, so no control was added (the Mac popover is read only too).
- **Texts**: "Reconnecting...", "Could not reconnect", "Retry", "Ask the remote computer again", "End session with %@?" in de/fr/it (`scripts/windows_texts.py`, `make-texts.py`).
- **Lab**: `tools/lab/windows/sync.sh test|build|all` copies the app sources (no bin/obj, no AppleDouble files) to `W:\MixelRemoteBuild\win-210` on win-build and runs
  `dotnet test` / `dotnet build` there. No secrets (ssh alias only).
- **Verified** on win-build: `dotnet test` of `MixelRemote.Core.Tests`: 111 passed, 0 failed (new: `ReconnectTests.cs`, 16 tests; changed: the window-close tests in
  `WorkspaceTests.cs`); `dotnet build` of `MixelRemote.App` (Release x64): 0 warnings, 0 errors. The engine DLL was not rebuilt (the app tests do not load it).
- **Not verified**: the app was not started (win-build has no interactive desktop): the Retry button, the "Reconnecting..." screen and the close-window dialog
  are built and their decisions are unit-tested, but not looked at on screen; no run against a real engine with a host going away.
