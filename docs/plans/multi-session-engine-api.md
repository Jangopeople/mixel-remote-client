# Several sessions at once: engine API (Mixel Remote 2.1.0)

Engine code: `next/crates/mixel_engine/src/remote_sessions.rs` (registry) and `remote_session.rs` (broker loop, commands).
Tests: `cargo test -p mixel_engine --lib multi_session` and `registry`, and
`cargo test -p signaling_server --test websocket_flow one_technician` (broker: four sessions to four hosts, mixed end order).

## What a technician can hold
- Up to **4** technician (viewer) sessions at once (`remote_status.max_sessions`), each with its own peer, transport,
  picture mailbox, input, clipboard, chat, file channel, drawing and recording state. Starting, ending or losing one
  never touches the others.
- One session per device: a second request to a device that already has one fails with `already_connected`.
- A **host** still serves one technician at a time, and a computer that views other computers does not host at the
  same time (unchanged).
- A fifth request fails at once with a typed error (not dropped): the command's error text is JSON
  `{"hint":"too_many_sessions","max":4,"message":"..."}` (`already_connected` uses the same shape). Other request errors stay plain text.

## `remote_status.sessions[]`
Technician sessions, oldest first. Each item (all fields always present):

| field | meaning |
|---|---|
| `id` | **stable id** of the session, chosen when it is requested, never changes. Bind tabs and windows to it. |
| `session` | the connection service's id of the session (the first transport's id); `null` until the service answered; since the make-before-break renewal of 2.1.0 it **stays the same through transport renewals** (engine `Peer.ui`); old ids of a transport are still accepted by the commands |
| `peer`, `device` | Remote ID (9 digits) of the device viewed |
| `phase` | `outgoing` (asked, waiting for Accept), `negotiating`, `active`, `closing` |
| `message` | the sentence for the person (English) |
| `consent` | consent id of the session (use for the file commands) |
| `received` | pictures received since the transport started |
| `control`, `clipboard`, `files`, `recording_permitted` | what the host granted |
| `recording` | recording view or null |
| `displays`, `display`, `display_all` | host displays |
| `print_jobs`, `host_actions`, `signin_id`, `host_macs`, `meeting_offer`, `peer_features`, `joined_session`, `switched` | as in the single-session status |
| `quality` | `{pictures, pictures_per_second, picture_age_ms}`; `pictures_per_second` is 0 when no picture came for 3 s |
| `started_at` | Unix seconds |

A session that ends disappears from the list; the failure sentence (declined, timed out, refused) is then in the
top-level `remote_status.message` while no other session is open (as before).

`remote_status.max_sessions` = 4. The single-session fields of the status (`phase`, `session`, `peer`, `control`, ...)
still describe one session (the oldest one that runs, else the oldest) when this app holds technician sessions, so an
app that does not know `sessions` keeps working with one session.

## Commands
- `remote_request {host, password?, unattended?}` now **returns the new session's stable id** (a JSON string) and the
  session is in `sessions` at once with `phase: "outgoing"`. Fails with the typed errors above.
- Every session-scoped command takes `session` = the stable `id` **or** the current `session` id (both resolve):
  `remote_input` / `input`, `remote_clipboard`, `remote_draw`, `remote_close`, `remote_restart`, video (`next_video`),
  `remote_chat_messages`, `remote_chat_send`, `remote_record_*`, `remote_invite`, `remote_switch_sides`.
  File commands take `consent`. **Always pass the stable `id`**: it survives a transport renewal.
- `remote_close {session}` ends only that session (it shows as `closing` until the connection service confirms, then
  leaves the list). Closing a session that is still `outgoing` cancels the request.
- `remote_switch_sides` works only while this is the only session.
- Old callers that pass `status.session` (no `sessions`) work unchanged.

## What an app must do
- Read `sessions`; one tab or window per item, keyed by `id` (the apps' redesign keys by the device's Remote ID `peer`:
  that is unique too, one session per device).
- Input, clipboard, toolbar actions go to the **focused** tab's session only. A hidden tab/window must not send input.
- Pictures: fetch per session (`next_video(id)`); each session has its own mailbox, so a background tab can keep
  its last picture or stop fetching (the engine keeps only the newest picture per session).
- Closing a tab/window ends that session only (`remote_close`).
- The Connect button stays usable while fewer than `max_sessions` sessions are open (do not gate on `phase == "ready"`).

## Resume and reconnect (integration agent's features) with several sessions
- A lost registration to the connection service keeps the session (resume, 30 s) when this app holds exactly **one** technician
  session (or hosts one); with two or more technician sessions they all end with the registration (message "Connection lost; sharing stopped").
- "The computer went away, asking again for a minute" (reconnect.rs) also works for the sole technician session only.
