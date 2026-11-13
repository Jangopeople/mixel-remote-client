# Mixel Remote for Linux (native, 2.0.x)

GTK 4 + libadwaita app written in Rust with gtk4-rs, directly on the shared engine — no web view, no
foreign-language binding. From 2.0.0 it is *the* Linux app: package `mixel-remote`, the same store.mixel.ch
entry (linux x64, variant `managed`) and update channel (`stable/linux-x64`) as the 0.1.x Tauri app, which it
updates in place.

| Part | Where | What |
|---|---|---|
| Engine | `crates/mixel_engine` | Account, devices, sessions, files, chat, recording, host side, sign-in screen service |
| Commands | `crates/mixel_engine/src/native_commands.rs` (+ `_linux.rs`) | The desktop app's command names and JSON, shared with the Apple/Android apps (through `mixel_core_ffi`); this app calls it directly |
| State and actions | `src/app.rs`, pure logic `src/logic.rs` | Port of the native apps' view models (connection, devices, company, settings, set-up, host, session) |
| Screens | `src/ui/` | Sidebar + pages (AdwLeaflet), session window (GtkPicture), consent dialog, dialogs, tray (StatusNotifierItem via ksni) |
| Report export | `src/report_export.rs` | CSV and PDF of the session report: a port of `frontends/desktop/report-export.ts` (the portal's layout), byte-for-byte checked against it (`scripts/report-golden.mjs` writes `tests/fixtures/report-golden.*`) |
| Translations | `po/{de,fr,it}.po` (gettext, built into .mo by `build.rs`), `data/engine-texts.json` | `scripts/make-po.py` regenerates them and fails on a missing text |
| Package | `scripts/build-deb.sh`, `data/deb/{preinst,postinst}`, `data/*.desktop`, metainfo | Built on Debian 12 (glibc floor 2.35, GTK 4.6 / libadwaita 1.1 APIs only) |

Only GTK 4.6 / libadwaita 1.1 APIs are compiled in (`gnome_42`, `v1_1` features), so it runs on Ubuntu 22.04,
Debian 12 and newer.

## Taking over a 0.1.x computer
- Same Secret Service entries (device key `ch.mixel.remote.next.qa.device-identity.v1`, remembered sign-in),
  same engine data folder (`~/.local/share/ch.mixel.remote.next.qa`, a queued self-removal is still sent),
  same machine key and service for always-on / sign-in screen access (`/var/lib/mixel-remote`).
- `postinst` runs `--signin-update`, so the always-on service runs the new program right away.
- `/usr/bin/desktop_app` stays as a link to `/usr/bin/mixel-remote` (0.1.x start-up entries, 0.1.x updater).
- The 0.1.x settings (language, unattended display, this computer's registration, Wake-on-LAN addresses,
  AI server) are read once from the old web view's local storage (SQLite, UTF-16) into
  `~/.config/ch.mixel.remote/settings.json`.
- 0.1.x updates itself with plain `dpkg -i`: hard dependencies are libraries only and `preinst` refuses to
  unpack (old version stays) when one is missing; tools (`libsecret-tools`, GStreamer plugins, CUPS client,
  portal) are recommended. 2.0.0 updates itself with `apt-get install` (dependencies come along).

## Build and test (on the Debian 12 lab VM, never on the Mac)
```bash
python3 scripts/make-po.py                         # translations (fails on a missing one)
cargo clippy -p mixel_remote_linux --all-targets -- -D warnings
cargo clippy -p mixel_remote_linux --features qa-session --all-targets -- -D warnings
cargo test -p mixel_remote_linux                   # view models, formats, keys, video, i18n, settings
xvfb-run dbus-run-session -- target/debug/mixel-remote --screenshots shots en,de,fr,it   # every screen, light + dark
scripts/build-deb.sh 2.0.0 <source-root> <out>     # the .deb and glibc.txt
```

## Live checks (LAB only, `--features qa-session`; release builds contain none of it)
`--live-check`, `--live-session`, `--live-host`, `--live-pair` / `--live-colleague` (clipboard both ways with real
X clipboards, a second technician, switching sides), `--live-watch` and `--check-providers` run the engine against
the live service with a throwaway QA account's session file (never a real person's), in an isolated HOME,
D-Bus and keyring. See `src/live_check.rs`. Remove the QA account and its data afterwards.

## Wayland
Sharing works through the desktop's own dialog (chosen each time); the privacy screen, blocking keyboard and
mouse, the technician's pointer and sharing the sign-in screen are not possible on Wayland — the app says so
in Settings, in the allow card and in the technician's session window, and points to an Xorg session.
