# Several sessions at once: Linux app (Mixel Remote 2.1.0)

App: `next/apps/mixel-remote-linux` (GTK 4 / libadwaita). Engine API: `docs/plans/multi-session-engine-api.md`.
The owner could connect to one computer at a time; the app now holds up to the engine's limit (`max_sessions`, 4).

## What changed
- **Status decoding** (`models.rs`): `RemoteStatus.sessions[]` (`SessionItem`, every field of the API) and `max_sessions`.
  An engine that sends no `sessions` is read through `technician_sessions()`: the single-session fields become one item
  (id = the connection service's session id, limit 1). `RemoteStatus::viewed_as(item)` is the status as one session sees it.
- **State** (`app.rs`): `State.focus` is the stable id of the session in front; `State.remote` and `State.session` describe that
  session (so every screen keeps reading them), `State.raw` is the status as it came, `State.parked` holds the other sessions'
  states (frame, chat, files, toggles, error…) by stable id. A session that ends drops its state.
- **Focus** (`App::sync_focus`, `focus_session`): the session whose window is active, or whose tab is in front in the active
  main window; clicking or typing in a session view also puts it in front. Devices in front means no session in front.
  Input, clipboard, chat, files, drawing, recording and every toolbar action (`session_action`, privacy, block, lock, restart,
  display, renew, invite, switch sides) act on the focused session only. `send_input_to(id, …)` refuses a session that is not in
  front (except "release all keys", which a view that lost the front sends to itself). Every call carries the stable id.
- **Pictures**: one loop per session (`next_video(id)`), one texture and one `SessionView` per session (tab or pop-out window).
  A session nobody looks at keeps its last picture and is not fetched after its first one.
- **Tabs and windows** (`workspace.rs`, `ui/mod.rs`): one tab per session item, keyed by the device's Remote ID and carrying
  the stable id (`SessionTab.id`); `Observed` lists the sessions. A tab's close button ends only its session
  (`remote_close {session: id}`, also for a request that still waits). A session ending removes only its tab/window/notice.
  A transport renewal (same id, new `session`) changes nothing on screen.
- **Connect**: not gated on `phase == "ready"`; enabled while `sessions < max_sessions` (`State::can_connect`). `remote_request`
  returns the new session's id (adapted). A refused request (`too_many_sessions`, `already_connected`, JSON error text) reads as a
  sentence: "You already have 4 computers open. End one before connecting to another." (de/fr/it in `po/`, via `scripts/make-po.py`).
- Restart-and-reconnect follows each restarted computer by Remote ID (`State.reconnects`), so other sessions do not disturb it.

## How it was tested
Build image `mixel-ui-linux-build` (Debian 12 arm64, Rust 1.99.1, GTK 4.8 / libadwaita 1.2), `next/` mounted, shared target cache.
- `cargo test -p mixel_remote_linux`: 69 passed, 0 failed, 1 ignored (3 runs in a row). New: 5 workspace tests (2 to 4 tabs,
  a session connecting, one ending, renewal, attempt among several sessions; 18 in all) and 12 `multi_tests` against the demo engine (tabs per session; pictures
  per stable id; input, clipboard, chat and toolbar only to the session in front; unfocused/ not-controllable sessions send
  nothing; closing one session; a session ending; fifth request, both the app's own check and the engine's typed error; request
  with room left; fallback without `sessions`; transport renewal; typed error in en/de/fr/it; hidden session stops fetching).
- `cargo clippy -p mixel_remote_linux --tests -- -D warnings` (also with `--features qa-session`): clean.
- `mixel-remote --screenshots` under Xvfb for every scene in English and the main scenes in German: 66 screenshots, no GTK/GLib/
  libadwaita warning. New scene `multi-session` (three sessions, one in its own window, one tab in front) was looked at.
- The engine crate compiles on Linux (`cfg(target_os = "linux")` blocks included).

## After the merge of feat/2.1.0 (0e3cbdd)
- The engine now keeps `sessions[].session` equal to the stable id (make-before-break renewal); the app keys on `id` and treats
  `session` only as "the transport's name", so nothing changed for it.
- The one-time-password warning and the "Reconnecting…" line (`logic::session_warnings`) are kept. The one-time warning follows the
  device (`State.password_peers`), not a single flag. `reconnecting` and `password_session` (time left) are top-level status
  fields, so they describe the oldest session and show in every session view while it applies.
- Fixed two lint failures that came with the merge (unused `use super::*` in `logic.rs` tests, `push_str(" ")` in `ui/settings.rs`).
- Re-run: `cargo test -p mixel_remote_linux` 71 passed, 0 failed, 1 ignored; `cargo clippy -p mixel_remote_linux --tests -- -D warnings` clean.

## Not verified
- No live session with two or more real hosts (no lab host was used); the engine's own tests cover that side.
- Window activation is judged from GTK's `is-active`; Xvfb without a window manager does not give real focus changes, so the
  hand-over between a pop-out window and the main window was checked by the screenshot scene and the unit tests, not by hand.
- The connecting card shows one connection at a time (a second Connect while the first still connects replaces the card; both
  sessions still open as tabs).
- The tray's "End session" ends the session in front, else the only session; with several and none in front it does nothing.

## 2.1.0 build-complete
- Per-session `password_session` and `unattended_session` (`SessionItem`; `RemoteStatus::viewed_as` copies them into the view of
  that session, so the 5-min/1-min warning shows only in its own session). An engine without `sessions[]` falls back to the
  top-level fields (`technician_sessions`). Restart-and-reconnect reads the session's own `unattended_session` too.
- Reconnect: a session with `reconnecting` or `reconnect_failed` (phase `outgoing`, top-level session empty) keeps its tab and
  window (`TabState::Reconnecting` / `ReconnectFailed`, no "ready" notice when it returns). The view's wait card reads
  "Reconnecting..." or "Could not reconnect" with Retry (`App::reconnect_session` -> `remote_reconnect {session: stable id}`);
  End uses the stable id (`end_session_id`), so it works while the top-level session is empty. de/fr/it in `po/`.
- Closing a popped-out window ends the session after "End session with <device>?" (`logic::window_close`); the "Back into a tab"
  button docks. Cancel keeps window and session.
- Sign-in: the "Continue with e-mail" step now has a real text entry (`signin.rs`), pre-filled with `Settings.last_signin_email`,
  any address accepted (`logic::signin_email`), sent to the engine as `login_hint`. The engine's `account_sign_in` ignores
  `login_hint` until it forwards it to the sign-in page (engine is not part of this change); the address is remembered either way.
- Tests: `cargo test -p mixel_remote_linux`: 81 passed, 0 failed, 1 ignored; `cargo clippy -p mixel_remote_linux --tests -- -D warnings` clean.
