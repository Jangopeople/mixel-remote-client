# Mixel Remote browser viewer

A technician's controller for phones, tablets and any browser, without installing anything.
Live at `https://ism.mixel.ch/remote-next/` (served by the ISM web app as a static folder).

## What it does
- Uses the ISM sign-in already in the browser (team accounts need the second step; the viewer asks for
  the authenticator code if it is missing). Personal accounts work, too.
- Registers the browser once as a device with its own Remote ID (key kept in the browser's storage).
- Lists the account's computers with online state; one tap connects. Remote ID, one-time password and
  unattended access as in the desktop app.
- Session: picture up to Full HD, mouse, keyboard, touch (tap = click, double tap, long press = right click,
  drag, two-finger scroll), zoom 1-3x with two-finger panning, special keys bar, typing field for on-screen
  keyboards, display switching including "All", Ctrl+Alt+Del, remote sound, full screen.
- Sessions stay up beyond 15 minutes (ticket renewal and transport restart like the desktop app).
Not in the viewer: files, clipboard, chat, recording, printing, restart/Wake-on-LAN buttons.

## Parts
- `viewer.js`, `texts.js` (EN/DE/FR/IT), `index.html`, `viewer.css`: plain modules, no build step.
- `pkg/`: `crates/web_client` compiled to WASM. All identity checks (tickets, host key pinning, session
  binding) and the control channel encryption are the shared Rust protocol code.
- Protocol description: `next/docs/controller-protocol.md`.

## Build and publish
```bash
cd next && cargo build -p web_client --target wasm32-unknown-unknown --release
wasm-bindgen --target web --out-dir frontends/web/viewer/pkg target/wasm32-unknown-unknown/release/web_client.wasm   # wasm-bindgen-cli 0.2.130
```
Copy `index.html viewer.js viewer.css texts.js pkg/web_client.js pkg/web_client_bg.wasm` into
`mixel-ism/apps/web/public/remote-next/`, open a pull request there, merge, then build and deploy ISM
(`pnpm build && pnpm deploy:cloudflare` in `apps/web`). ISM's `_headers` holds the viewer's own strict
Content-Security-Policy.

## Why it lives on the ISM origin
The connection service accepts exactly one web origin (`MIXEL_WEB_ORIGINS=https://ism.mixel.ch`), the ISM API
allows that origin, and the sign-in is there. The identity authority refuses browsers in general; nginx on
`ism-api.mixel.ch` answers the preflight and hides the Origin header for `https://ism.mixel.ch` only
(`/etc/nginx/conf.d/mixel-remote-ticket-origin.conf`, location `/v1/device-ticket`).

## Local test against the live services
`_scratch/lab/webdev/server.mjs` serves this folder on `http://localhost:5173/remote-next/` and passes the
WebSocket and the ticket request on without an Origin header. Put a lab session
(`/tmp/lab-session.py mint` on the server) into `localStorage['sb-ism-auth-token']`.
`window.__mixelSound()` in the console shows sound and record counters.
