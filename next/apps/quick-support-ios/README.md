# Mixel Quick Support for iPhone and iPad (`ch.mixel.quicksupport`)

The customer's side of a phone support session on Apple devices: type the technician's support code,
then start the screen broadcast. The technician who issued the code sees the screen. Apple allows no
app to operate another app, so the technician cannot tap or type; they guide the customer instead.

| Part | What |
|---|---|
| `App/` | The app (SwiftUI): code entry, Remote ID, Apple's broadcast button |
| `Broadcast/` | Screen-broadcast extension: receives the screen pictures, encodes them (VideoToolbox, H.264 baseline) and runs the whole session with the host core. It keeps running while the customer uses other apps |
| `Shared/` | Shared keychain storage (device key, registration) and the texts in four languages |
| `QuickHost/` | C header of the host core (`crates/quick_host/src/ffi.rs`) |

Consent: the extension has no screen of its own, so it cannot ask during a session. Starting the
broadcast is the consent, and the host core accepts only the technician who issued the code
(`mqs_accept_issuer`); anyone else is declined.

## Build
```bash
./build-native.sh                 # host core for iPhone and simulator → QuickHost.xcframework
xcodegen generate                 # MixelQuickSupport.xcodeproj from project.yml
xcodebuild -project MixelQuickSupport.xcodeproj -scheme QuickSupport -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' -derivedDataPath build CODE_SIGN_IDENTITY=- ARCHS=arm64 build
```
A device or store build needs the two App IDs (`ch.mixel.quicksupport`, `ch.mixel.quicksupport.broadcast`)
with the shared keychain group, and an App Store Connect entry.

## Cannot be tested without a real iPhone
The screen broadcast does not run in the simulator. Code entry and registration do. The first real
check is on an iPhone (TestFlight): start the broadcast, connect from the viewer, watch the memory
of the extension (iOS ends broadcast extensions above about 50 MB).
