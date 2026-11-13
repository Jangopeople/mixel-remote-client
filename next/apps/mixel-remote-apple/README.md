# Mixel Remote for Mac, iPhone and iPad (native)

SwiftUI apps on the shared Rust engine. The Tauri desktop app stays the reference and keeps working.

| Part | Where | What |
|---|---|---|
| Engine | `crates/mixel_engine` | Everything without a window: account, devices, sessions, files, chat, recording, host side. Used by the Tauri app (`crates/desktop_app`) and here |
| Swift interface | `crates/mixel_core_ffi` (UniFFI) | One `MixelEngine` object: `call(command, json)` with the desktop app's command names, `nextVideo`, `input`, `shutdown` |
| XCFramework | `Core/MixelCoreFFI.xcframework` (not in git) | `scripts/build-core.sh [macos] [ios]` (universal Mac; iOS device + Apple-silicon simulator; iOS is controller only) |
| App code | `Sources/MixelRemoteKit` | View models, screens, translations, scripted engine for tests/screenshots |
| Engine bridge | `Sources/MixelCore` | Generated `mixel_core_ffi.swift` + `CoreEngine` |
| Mac app | `Mac/` | `ch.mixel.remote.native` ("Mixel Remote Preview"): window, Settings, menu bar extra, menus |
| iPhone/iPad app | `iOS/` | `ch.mixel.remote.controller` (App Store entry "Mixel Remote") |

## Build and test
```bash
scripts/build-core.sh macos ios                     # engine (4 jobs; one heavy build at a time)
cp ../../deployment/public-service-config.json Core/service-config.json
python3 scripts/make-strings.py                     # String Catalog (fails on a missing translation)
swift test                                          # view models + offscreen Mac screenshots (SNAPSHOT_DIR=…)
xcodegen generate && xcodebuild -scheme MixelRemoteMac -configuration Release build
scripts/ios-screenshots.sh <own-simulator-udid> <dir> # iPhone screens, headless Simulator, light + dark
```
Package tests on the iOS Simulator: move `MixelRemoteApple.xcodeproj` aside and run
`xcodebuild test -scheme MixelRemoteApple-Package -destination id=<sim>`.

## Live checks (LAB only)
Debug Mac builds have `--live-check <qa-session.json> <run>` and `--live-session <qa-session.json> <run> <dir>`
(needs `FEATURES=qa-session scripts/build-core.sh macos` and a throwaway QA account; never a real person).
Release builds contain neither, and their core is built without `qa-session`. Remove the QA data and the
`ch.mixel.remote.native.*` Keychain items afterwards.

## Translations
Screen texts are English keys (`L("…")`). German, French and Italian come from the desktop app's table
(`frontends/desktop/i18n-strings.ts`, bundled as `engine-texts.json`) plus `NATIVE` in `scripts/make-strings.py`.
Engine messages are translated at run time with the same table.
