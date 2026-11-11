# Mixel Meet

Small meetings in the browser: screen sharing, voice, video and chat for up to 8 people.
Nothing to install; guests open a link, type a name and wait until the host lets them in.

- `server.mjs` – rooms, waiting room, host rights and signaling (Node 22, one dependency: `ws`).
- `public/` – the meeting page (English, German, French, Italian).
- `test/` – `npm test` starts the real service and walks through a whole meeting.
- `deploy/` – systemd unit, nginx block and `deploy.sh`.

Sound and picture go between the participants' browsers through Mixel's relay and are encrypted
between each pair; the service never sees them. Chat passes through the service and is not stored.

## Who can do what

| | Host (link with key) | Guest (invitation link) |
|---|---|---|
| Create a meeting | signed-in Mixel account only (`POST /meet/api/rooms` with the ISM session) | – |
| Join | straight in | waits until a host admits |
| Admit, decline, remove, lock, end for all | yes | no |
| Microphone, camera, screen sharing, chat | yes | yes |

A meeting ends when the host ends it, one hour after the last person left, or after 12 hours.

## Settings (`/etc/mixel-meet.env`, mode 600)

```
MEET_PORT=8097
MEET_PUBLIC_URL=https://rs.mixel.ch/meet/
MEET_ISM_KEY=<public ISM key, same as the browser viewer>
MEET_TURN_SECRET=<MIXEL_TURN_SHARED_SECRET of the broker>
MEET_TURN_URLS=turn:rs.mixel.ch:3479?transport=udp,turn:rs.mixel.ch:3479?transport=tcp,turns:rs.mixel.ch:5350?transport=tcp
```

`MEET_RELAY_ONLY=0` allows direct connections (default: relay only, so participants do not see
each other's network address). `MEET_DEV=1` is for local tests only.

## Local test

```
MEET_DEV=1 MEET_RELAY_ONLY=0 MEET_PUBLIC_URL=http://localhost:8097/meet/ MEET_ISM_KEY=… node server.mjs
```

`?fake=NAME&auto=1` joins with a generated picture and tone instead of camera and microphone;
`window.__mixelMeet()` returns connection figures.

## Limits

Everyone sends to everyone (mesh), which is why a meeting holds 8 people. No recording,
no dial-in by phone, no calendar invitations.
