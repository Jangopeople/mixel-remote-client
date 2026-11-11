# quick_host — the host core of Mixel Quick Support for phones

A phone (or any device without a Mixel account) registers with a one-time **support code**, goes
online, asks its user when a technician wants to connect, and shares its screen in one attended
session. This crate is that logic without any desktop code; the apps around it supply the encoded
screen picture and carry out the technician's input.

| File | What |
|---|---|
| `src/lib.rs` | `Host`: register (`enroll`), go online (`start`), answer a request (`decide`), `push_frame`, events |
| `src/session.rs` | One session: encrypted link to the viewer, video out, input in |
| `src/wire.rs` | The connection service's messages a host needs |
| `src/ffi.rs` | C interface (`mqs_*`) for the iPhone app |
| `src/android.rs` | Java interface (`ch.mixel.quicksupport.Native`) for the Android app |
| `examples/lab_host.rs` | Lab stand-in for a phone: shares a generated upright picture |

## How a session comes about
1. A team member creates a support code (viewer ▸ "Help someone on their phone"; SQL `sql/013_support_codes.sql`).
   It is valid for 30 minutes and for one device.
2. The device calls `enroll(code, name)`: it gets a temporary registration (device record, Remote ID) under the
   company the code was made for. No account. Registrations are removed automatically after 8 hours.
3. `start(...)`: the device proves itself to the identity service with its own key (no user), connects to the
   connection service and switches to **attended support** (`configure_support_host`). From then on it can be
   reached by attended requests only: it can never be reached unattended and can never request a session itself.
4. A technician's request arrives as `Event::Request`; the app asks the person and calls `decide`.
5. The viewer and the device confirm each other's identity and the link itself, then pictures flow
   (`push_frame`: H.264 access units with start codes, baseline, parameter sets before every keyframe).
   Up to 1920 on the long side and 1088 on the short side, even numbers.
6. Either side ends the session. The viewer renews the link at the latest every 15 minutes; the core handles that.

## Lab
```bash
# 1. a support code (as a signed-in team member): viewer ▸ "Help someone on their phone", or the RPC remote_create_support_code
# 2. the stand-in phone
cargo run -p quick_host --example lab_host -- --state /tmp/qs.json --code XXXX-XXXX-XXXX --auto-accept
MQS_LOG=1 …   # traces the steps of the connection
# 3. connect from the viewer to the printed Remote ID
```
`window.__mixelRenew()` in the viewer's console renews the link at once (otherwise after about 12 minutes).

## Limits
- Relay addresses with their own encryption (`turns:`) are left out on purpose: they need the system certificate
  store, which this code cannot reach on phones. The media is end-to-end encrypted either way.
- No files, clipboard, chat, sound or printing on a phone host.
