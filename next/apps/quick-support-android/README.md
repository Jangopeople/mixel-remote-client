# Mixel Quick Support for Android (`ch.mixel.quicksupport`)

The customer's side of a phone support session: type the technician's support code, allow the session,
and the technician sees the screen — and, if the customer switched it on, can tap and type.
No account. Plain Android (Kotlin, no support libraries); the session logic is the Rust host core
`crates/quick_host`, built as `libquick_host.so`.

| File | What |
|---|---|
| `MainActivity.kt` | The one screen: code, status, Remote ID, allow/decline, end |
| `Support.kt` | State of the connection and the link to the host core |
| `ShareService.kt` | Screen capture and H.264 encoding while a session runs (visible foreground service) |
| `ControlService.kt` | Optional accessibility service: carries out taps, swipes, Back/Home and typing |
| `Native.kt` | The host core's functions |

## Build
```bash
./build-native.sh debug      # host core for arm64 (emulator on Apple silicon, most phones)
./build-native.sh            # release, arm64 + arm32 + x86_64
ANDROID_HOME=~/Library/Android/sdk ./gradlew :app:assembleDebug     # test app
ANDROID_HOME=~/Library/Android/sdk ./gradlew :app:bundleRelease     # store package (needs keystore.properties)
```

## What the technician's input does on the phone
| From the viewer | On the phone |
|---|---|
| Click / drag | Tap, long press, swipe |
| Right click | Back |
| Middle click, system key | Home |
| Wheel | Scroll |
| Typing, Backspace, Enter | Into the text field that has the focus |
| Esc | Back |

Operating the phone needs the accessibility service "Mixel Quick Support", which the customer switches on
in the system settings and can switch off at any time. Without it the technician can only look.

## Store notes
Google reviews apps with an accessibility service strictly: the listing needs the prominent disclosure and the
declaration form (what it is used for: remote support at the user's request; no data collected through it).
