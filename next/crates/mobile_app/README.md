# Mixel Remote for iPhone, iPad and Android

One app for both platforms (`ch.mixel.remote`). You sign in with your Mixel ID, register the phone
once, and control your computers from it: see the screen, tap, type, special keys, right click,
Ctrl+Alt+Del, switch monitors, hear the remote sound, start a meeting.

## How it is built

| Part | Where | What it does |
|---|---|---|
| Screen and session logic | `frontends/web/viewer` (shared with the browser viewer) | Everything you see; the session's sound and picture arrive here directly |
| Bridge | `frontends/mobile/bridge.js` | Hands the viewer the app's network calls, connection and sign-in |
| Page bundle | `frontends/mobile/build.mjs` → `frontends/mobile/dist` | Viewer + bridge, built before every app build |
| App shell | `crates/mobile_app` (Tauri 2, Rust) | Sign-in with Mixel ID, calls to the account and connection service, opening a meeting in the phone's browser |
| Address rules | `crates/mobile_app/src/rules.rs` | The only addresses the app talks to and shows; covered by tests |

Why a shell and not only the website: the Mixel account service and the connection service accept
the Mixel ISM website or a native app, not other web pages. The shell makes those calls itself.
Web pages shown during sign-in (Mixel ID, Microsoft sign-in) cannot call into the app.

Sign-in: the app opens the Mixel ID page inside itself. When Mixel ID sends the browser back to
`https://ism.mixel.ch/remote-next/app-return`, the app catches that address, exchanges the one-time
code for the session and shows itself again. The ✕ button (or Android's back key) cancels.

## Build

Needs Xcode, the Android SDK with NDK 28.2 (`NDK_HOME`), Rust targets for iOS and Android, tauri-cli 2.

```bash
cd next/crates/mobile_app
cargo test -p mobile_app                                    # address rules
cargo tauri ios build --debug --target aarch64-sim          # iPhone simulator app
cargo tauri android build --debug --apk --target aarch64    # Android test app
cargo tauri android build --aab                             # Android store package (signed)
cargo tauri ios build --export-method app-store-connect     # iPhone store package (needs the App ID and profile)
```

Android store signing: copy `~/.mixel-secrets/mixel-remote-upload.properties` to
`gen/android/keystore.properties` (not in git). The upload key is `~/.mixel-secrets/mixel-remote-upload.jks`.

Lab builds: add `--features lab-session`. The app then takes a test sign-in from
`http://127.0.0.1:5173/dev-session.json` (Android emulator: `10.0.2.2`), served by
`lab/scripts/webdev/server.mjs`. Store builds do not contain this.

## Not in this version
- The phone cannot be controlled itself; it is a controller only.
- File transfer, clipboard pictures and printing are desktop features.
- The registration key is kept in the app's own storage, not in the system keychain.
