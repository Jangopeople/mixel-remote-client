# Multi-session: Mac app notes (Mixel Remote 2.1.0)

Engine API: `multi-session-engine-api.md`. App code: `next/apps/mixel-remote-apple`.

## What changed
- `RemoteStatus` decodes `sessions[]` (`RemoteSessionItem`, tolerant: every field optional) and `max_sessions`.
  `viewerSessions` returns the list, or, for an engine that sends no `sessions`, one item synthesized from the
  single-session fields (id = connection session id), so old engines still work with one session.
- `SessionModel` is now one model per session (`AppModel.sessionModels`, keyed by the computer's Remote ID, bound to
  the session's stable `id`). Every engine call (input, clipboard, chat, files, record, draw, close, ...) carries
  that stable id. `AppModel.session` is the focused session's model (else the oldest, else `idleSession`, which
  stands for "none" and ends a session this computer *shares*, by consent).
- Focus (`WorkspaceModel.isFocused`): selected tab, or the key session window; with one session that session.
  Input, clipboard, files, chat, record, draw, ctrl-alt-del, monitors, invite, switch-sides only go to the focused
  session. A session nobody focused sends nothing (a `releaseAll` still goes out, so keys never stay down).
- Pictures: each session model fetches `next_video(stable id)` for its own mailbox; hidden tabs keep fetching
  (needed so an H.264 stream never waits for a key frame when a tab comes to the front).
- Closing a tab / "End session" on a tab: `remote_close {session: id}` for that session only.
- Connect is not gated on `phase == "ready"`: `RemoteStatus.connectBlockedBySession` (old engine: any session;
  2.1: only while this computer is being helped). A request over the limit reaches the engine and its typed error
  `{"hint":"too_many_sessions","max":4,...}` is shown as a sentence (`EngineFailure.typed`; de/fr/it in
  `scripts/make-strings.py` NATIVE). `already_connected` has its own sentence.
- Transport renewal: same `id`, new `session`: tab, model, picture and selection stay.
- A popped-out window's red button (and Cmd-W) asks "End session with <device>?" and, on confirm, ends that session
  (`WorkspaceModel.windowCloseRequested`, 2.1.0); the separate "Back into a tab" button docks it.
- `scripts/build-core.sh`: `ARM_ONLY=1` builds Apple silicon only (lab builds).

## Tests
`swift test` (new `MultiSessionTests`: 2-4 tabs, focus routing, unfocused sends nothing, close ends one,
session end removes one tab, 5th request error, old engine fallback, renewal, per-session pictures, decoding).
Mock engine: `queue(frame:session:)`, `sentInputSessions`, `videoFetches`.

## Not verified
- No live two-session run in this task (needs the lab hosts; the lab core with `qa-session` is built for it).
- Hidden tabs decode pictures too (CPU with four sessions not measured).
- Drawing, the AI assistant and session reports follow the focused session only; per-session reports/notes
  for several sessions ending at once are not specially handled.
- iPhone/iPad app builds not rebuilt here (phone shows one session; `isFocused` is always true there).

## After the merge of feat/2.1.0 (engine make-before-break renewal)
- Merged cleanly in the Mac app (no conflict leftovers). `RemoteStatus` keeps `sessions`/`maxSessions` and `reconnecting`.
- The "Reconnecting… the session stays open" banner reads the whole status, so it shows in every session tab.
- The one-time-password and unattended flags exist only once in the status (oldest running session): `describing(item)`
  now hands them only to that session; other sessions use what this app sent itself (`requestFlags`). Test:
  `testPasswordAndReconnectingStateAreReadPerSession`.
- The engine now keeps `sessions[].session` stable too; the app keys by `id` and tolerates either.
- Lab core rebuilt with `FEATURES=qa-session ARM_ONLY=1 scripts/build-core.sh macos` (needed the engine's qa trace fix d8bfc9c).
- Results: swift build ok, swift test 90 executed / 0 failures, xcodebuild Debug MixelRemoteMac BUILD SUCCEEDED.

## 2.1.0 build-complete: per-session flags, reconnect, window close, row menu
- **Per-session flags.** `RemoteSessionItem` decodes `password_session {ends_at, seconds_left}`, `unattended_session`,
  `reconnecting` and `reconnect_failed` (all optional). `describing(item)` no longer hands the status's single value to the
  oldest session: each session reads its own, so a password session's 5-minute and 1-minute warnings show only in that
  session. An engine without `sessions[]` still gets one synthesized item carrying the status's own flags.
- **Reconnect.** A session that is retrying keeps its tab/window (`SessionTab.state` = `reconnecting`, then
  `reconnect_failed`; `SessionModel.link` = `.live/.reconnecting/.failed`) and shows "Reconnecting..." in the stage; after
  the engine gives up it shows "Could not reconnect" with Retry (`remote_reconnect {session: stable id}`) and End
  (`remote_close {session: stable id}`, also while the top-level `session` is empty). A retrying session is not a new
  request (`RemoteSessionItem.isNewRequest`): `requesting`, the connecting message and `cancelRequests()` ignore it.
- **Closing a session window** (red button, Cmd-W) asks "End session with <device>?" and ends the session on confirm
  (`WorkspaceModel.windowCloseRequested` + `confirmEnd`; the NSAlert and the window delegate guard are in
  `SessionWindowView`). Cancel keeps window and session. "Back into a tab" still docks. A window whose session is
  already gone closes without a question.
- **Row menu.** "Send files..." (connects first when needed, then opens the file picker and sends over the session's
  file channel; says so when the host did not allow files): `WorkspaceModel.sendFiles`. "Move to group..." already
  existed in the row menu (company `saas_move_device`); a test now covers it. The engine's `remote_admin_move_device`
  (group id + name) is not wired: the company command already covers the same move.
- Tests: `swift test` 104 executed, 0 failures. Mac Debug build (xcodegen + xcodebuild, scheme MixelRemoteMac, existing
  xcframework) BUILD SUCCEEDED. Not run: a live window close with the real NSAlert and a live reconnect (needs the lab).
