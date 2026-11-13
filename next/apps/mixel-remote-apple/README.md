# Mixel Remote for Mac, iPhone and iPad (native, 2.0)

SwiftUI apps on the shared Rust engine. Since 2.0 they are the product on Apple platforms: the Mac app
replaces the Tauri desktop app 0.1.x in place (same identity and update channel); the Tauri app stays the
product on Windows and Linux until their native apps pass the same parity check.

| Part | Where | What |
|---|---|---|
| Engine | `crates/mixel_engine` | Everything without a window: account, devices, sessions, files, chat, recording, host side. Used by the Tauri app (`crates/desktop_app`) and here |
| Swift interface | `crates/mixel_core_ffi` (UniFFI) | One `MixelEngine` object: `call(command, json)` with the desktop app's command names, `nextVideo`, `input`, `shutdown` |
| XCFramework | `Core/MixelCoreFFI.xcframework` (not in git) | `scripts/build-core.sh [macos] [ios]` (universal Mac; iOS device + Apple-silicon simulator; iOS is controller only) |
| App code | `Sources/MixelRemoteKit` | View models, screens, translations, scripted engine for tests/screenshots |
| Engine bridge | `Sources/MixelCore` | Generated `mixel_core_ffi.swift` + `CoreEngine` |
| Mac app | `Mac/` | "Mixel Remote" 2.0, `ch.mixel.remote.next.qa`, executable `desktop_app` (the identity of the desktop app 0.1.x, so it is an in-place update); Debug builds are the lab copy `ch.mixel.remote.labtest` |
| iPhone/iPad app | `iOS/` | `ch.mixel.remote.controller` (App Store entry "Mixel Remote") |

## Build and test
```bash
scripts/build-core.sh macos ios                     # engine (4 jobs; one heavy build at a time)
cp ../../deployment/public-service-config.json Core/service-config.json
python3 scripts/make-strings.py                     # String Catalog (fails on a missing translation)
swift test                                          # view models + offscreen Mac screenshots (SNAPSHOT_DIR=…)
xcodegen generate && xcodebuild -scheme MixelRemoteMac -configuration Release -destination generic/platform=macOS build  # universal
scripts/ios-screenshots.sh <own-simulator-udid> <dir> # iPhone screens, headless Simulator, light + dark
```
Package tests on the iOS Simulator: move `MixelRemoteApple.xcodeproj` aside and run
`xcodebuild test -scheme MixelRemoteApple-Package -destination id=<sim>`.

## Live checks (LAB only)
Debug Mac builds have `--live-check <qa-session.json> <run>` and `--live-session <qa-session.json> <run> <dir>`
(needs `FEATURES=qa-session scripts/build-core.sh macos` and a throwaway QA account; never a real person).
Also `--live-prepare` / `--live-identity` (the in-place update test 0.1.22 → 2.0, see the release README).
Release builds contain none of them, and their core is built without `qa-session`. Remove the QA data and the
`ch.mixel.remote.labtest.*` Keychain items afterwards.

## Taking over from the desktop app 0.1.x (Mac)
- Same bundle identifier and executable name: the Keychain device key and remembered sign-in, the launch
  agents (`<bundle>.startup-host` always-on host, `ch.mixel.remote.autostart`), TCC approvals, the data
  folder (`Application Support/<bundle>`) and the signature check of the 0.1.x updater all carry over.
- Headless modes run before any window (`MixelCore.runCommandLine`): `--startup-host`, `--signin-enroll`
  (rollout script), `--prelogin-host`/`--provision-prelogin-identity`/`--validate-prelogin-settings`,
  `--validate-identity-scope`, `--validate-public-config`, `--version`.
- The web view settings of 0.1.x (language, automatic updates, unattended access, this computer, Wake-on-LAN
  addresses, AI server) are copied once into UserDefaults (`LegacySettings`).
- Updates: `update_check`/`update_install` on the signed channel `remote-updates/<channel>/macos-arm64`
  (universal DMG), installed silently when nothing would be interrupted, as in 0.1.x.
- Sign-in screen: the login window host is a copy of the app as `ch.mixel.remote.prelogin` with Apple's
  virtual HID keyboard entitlement. The release script adds it only when that Developer ID profile exists
  (`~/.mixel-secrets/mixel-remote-prelogin.provisionprofile`); until then Settings shows "Waiting for Apple".

## Translations
Screen texts are English keys (`L("…")`). German, French and Italian come from the desktop app's table
(`frontends/desktop/i18n-strings.ts`, bundled as `engine-texts.json`) plus `NATIVE` in `scripts/make-strings.py`.
Engine messages are translated at run time with the same table.
