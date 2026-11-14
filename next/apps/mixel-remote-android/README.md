# Mixel Remote for Android (native)

Kotlin, Jetpack Compose and Material 3 in Mixel's own colours (dynamic colour off), Android 8+
(API 26). The technician / controller app on the shared Rust engine — the same engine as the
desktop app and the native Apple apps. App id `ch.mixel.remote` (2.0.0 replaced the Tauri phone app
1.0.x, same upload key; 2.0.1 adds drawing on the remote screen, favorites and recent devices, session
reports with notes and CSV/PDF export, and the optional note after a session).

| Part | Where | What |
|---|---|---|
| Engine | `crates/mixel_engine` | account, devices, sessions, files, chat; on Android a controller-only build (`session_platform_ios.rs` is the phone platform for iOS and Android) |
| Kotlin interface | `crates/mixel_core_ffi` (UniFFI) | `MixelEngine.call(command, json)`, `nextVideo`, `input`, `shutdown`; `installAndroid(storage, clipboard)` |
| Native libraries | `app/src/main/jniLibs/<abi>` (not in git) | `scripts/build-core.sh` (cargo-ndk style: NDK 28 clang, arm64-v8a + armeabi-v7a + x86_64) |
| Generated Kotlin | `app/src/main/java/uniffi` (not in git) | written by `scripts/build-core.sh` |
| App | `app/src/main/java/ch/mixel/remote` | `engine/` (EngineApi, CoreEngine, FakeEngine), `app/` view models, `ui/` screens, `demo/` scripted states |

Secrets: the device key and the remembered sign-in are sealed with AES-GCM by an Android Keystore key
(`KeystoreStorage`, files in `noBackupFilesDir`, backups excluded). Sign-in: Mixel ID in a Custom
Tab (PKCE, the engine's private 127.0.0.1 callback redirects to `ch.mixel.remote://signed-in`, the
second step in the app). Video: the phone's H.264 decoder (MediaCodec onto a TextureView); if it
fails the engine decodes (openh264) and sends RGBA pictures.

## Build and test
```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@21 ANDROID_HOME=$HOME/Library/Android/sdk
scripts/build-core.sh release                 # engine for all ABIs (JOBS=4; one heavy build at a time)
python3 scripts/make-strings.py               # texts.json (fails on a missing DE/FR/IT translation)
./gradlew --max-workers=4 :app:testDebugUnitTest          # view models on the scripted engine
./gradlew --max-workers=4 :app:connectedDebugAndroidTest  # every screen light/dark/DE/FR/IT, TalkBack labels, screenshots
cp ~/.mixel-secrets/mixel-remote-upload.properties keystore.properties   # never commit
./gradlew --max-workers=4 :app:assembleRelease :app:bundleRelease
```
Screenshots of the screen tests are written to the app's `files/screenshots` (keep the APKs installed with
`-Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true` and pull them with `adb exec-out run-as`).
Demo screens in debug builds: `adb shell am start -n ch.mixel.remote/.MainActivity --es demo devices`
(`signed-out`, `second-step`, `devices`, `connect`, `setup`, `account`, `settings`, `session`, `chat`, `files`, `help`).

## Live checks (LAB only)
`FEATURES=qa-session scripts/build-core.sh release arm64-v8a`, a debug build, and a throwaway QA login from
`lab/qa-account.py mint qa-android-<n>@example.test <file>` (run on the VPS with sudo; never a real person).
Copy the file to the app's `no_backup/qa-session.json` (`adb shell run-as ch.mixel.remote …`): debug builds
then sign in with it instead of the browser. Remove the QA company in the app, the login with
`qa-account.py remove`, and rebuild the core without `qa-session` before any release build.

`adb shell am start -n ch.mixel.remote/.MainActivity --es qa-live <run>` runs the live session shared by every app
instead of the screens (the engine's `qa_live_technician`: early input and clipboard, pictures, input, clipboard both
ways, chat, drawing, end); lines and hand-shake files in `files/qa-live` (`run-as ch.mixel.remote`).

A host with the current engine for those checks (drawing needs a host that announces `draw`): build
`cargo build --release -p mixel_engine --example lab_linux_host` on a Linux lab VM (Ubuntu 24.04 has the
build libraries), then run it as root on an isolated X11 display — see the comment at the top of
`crates/mixel_engine/examples/lab_linux_host.rs`.

## Translations
Screen texts are English keys (`L("…")`). German, French and Italian come from the Apple apps' String
Catalog, the desktop app's tables (`assets/engine-texts.json`, `frontends/desktop/i18n-annotate.ts`,
`i18n-reports.ts`) and `ANDROID` in `scripts/make-strings.py`.
Engine messages are translated at run time with the desktop table.
