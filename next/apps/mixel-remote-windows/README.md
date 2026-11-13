# Mixel Remote for Windows (2.0, WinUI 3)

The Windows app of Mixel Remote from version 2.0: WinUI 3 on .NET 10 (self-contained), on the shared Rust
engine. It replaces the Tauri app (0.1.x) **in place**: same install folder, same program name, same
"Mixel Remote" entry in Apps & features, same device identity, same sign-in service and printer, same
update channel. Windows 10 1809 (17763) or later, x64.

| Part | Where | What |
|---|---|---|
| Engine | `crates/mixel_engine` | Everything without a window (account, devices, sessions, files, chat, recording, host side). Shared with the Tauri, Mac and phone apps |
| C interface | `crates/mixel_core_capi` → `mixel_core.dll` | `mixel_engine_new / call / next_video / input / shutdown / free`, strings UTF-8, results JSON (see the crate docs). Command names and arguments are the desktop app's (`crates/mixel_core_ffi/src/commands.rs`) |
| Engine helper | `crates/mixel_core_capi/src/bin/helper.rs` → `mixel-remote-helper.exe` | Administrator steps (`--signin-*`, `--printer-*`), the SYSTEM service of sign-in screen and always-on access and its console host. Takes the 0.1 program's arguments; the update channel `windows/x64-program` carries it |
| Core library | `src/MixelRemote.Core` | Engine access (`NativeEngine` P/Invoke, `MockEngine`), models, view models, texts, demo fixtures. No WinUI: the tests run on plain .NET |
| App | `src/MixelRemote.App` | Windows, pages (built in code), session window (Direct3D swap chain via Win2D, keyboard hook), consent window, notification-area icon |
| Tests | `tests/MixelRemote.Core.Tests` | View models against the scripted engine, key map, frames, texts, 0.1 settings import |
| LAB only | `src/MixelRemote.Lab`, `tools/MixelRemote.LabCheck`, `src/MixelRemote.App/Lab/UiLiveCheck.cs` | Live checks with a throwaway QA account; never in a release build |

### Why a C interface and not generated C# bindings
UniFFI's C# generator (uniffi-bindgen-cs) trails UniFFI releases (the engine pins 0.31); the app needs
five calls whose payloads are already JSON or bytes. A fixed C signature has no generator to keep in step,
is covered by Rust tests (`cargo test -p mixel_core_capi`) and the app's own tests, and never lets a
panic cross the boundary. The Swift apps keep using UniFFI over the same `MixelEngine` object.

### Taking over 0.1 on an installed computer
- Install folder `%LOCALAPPDATA%\Mixel Remote`, program `mixel-remote.exe`, uninstall key `HKCU\…\Uninstall\Mixel Remote`
  (`installer/mixel-remote.nsi`). The 0.1 app's own updater runs the installer with `/S` and starts `mixel-remote.exe`.
- Device key and remembered sign-in: `%LOCALAPPDATA%\Mixel-Remote-Next-QA\` (user DPAPI), read by the same engine code.
- Start with Windows: `HKCU\…\Run\Mixel Remote` → `"%LOCALAPPDATA%\Mixel Remote\mixel-remote.exe" --autostart`.
- App data: `%LOCALAPPDATA%\ch.mixel.remote.desktop` (pending self-removal, `windows-app.json` preferences, log).
- Settings 0.1 kept in its web view (language, unattended sharing, this computer's registration, Wake-on-LAN
  addresses, automatic updates, AI server) are imported once from its local storage (`LegacySettings`).
- Always-on / sign-in screen service `MixelRemoteSignIn` (program `C:\Program Files\Mixel Remote Sign-in Screen\mixel-remote-signin.exe`):
  its own updater replaces the program with `windows/x64-program` (the helper); "Update sign-in screen access" in
  Settings does the same at once. `mixel-remote.exe --signin-*` (deploy-link command) hands over to the helper.

## Build (on win-build)
```powershell
# engine (release; LAB: --features qa-session into another target dir)
$env:MIXEL_DESKTOP_SERVICE_CONFIG = "next\deployment\public-service-config.json"; $env:MIXEL_ENGINE_APP_VERSION = '2.0.0'
cargo build --locked --release -p mixel_core_capi          # mixel_core.dll + mixel-remote-helper.exe
cargo test --locked --release -p mixel_core_capi -p mixel_core_ffi -p mixel_engine
cargo clippy --locked --release -p mixel_core_capi -p mixel_core_ffi -p mixel_engine --all-targets -- -D warnings
# copy both into apps\mixel-remote-windows\core\, then
python3 scripts/make-texts.py                               # texts.json (fails on a missing translation)
dotnet test tests\MixelRemote.Core.Tests -c Release
dotnet publish src\MixelRemote.App -c Release -r win-x64 -o out\app
makensis /DVERSION=2.0.0 /DSOURCE=out\app /DOUTFILE=Mixel-Remote-Windows-x64.exe installer\mixel-remote.nsi
```
Sign `mixel-remote.exe`, `mixel-remote-helper.exe` and `mixel_core.dll` before `makensis`, then the installer
(Azure Trusted Signing with jsign, as for 0.1). Builds keep their paths out of the binaries (`--remap-path-prefix`).

## Screenshots and demo
`mixel-remote.exe --screenshots <folder>` renders every screen with the demo engine (no account, no network) in
light and dark plus DE/FR/IT, as Windows draws the windows (needs a signed-in desktop).
`mixel-remote.exe --demo <screen>` opens the app on one demo screen.

## Live checks (LAB only)
Build with `-p:Lab=true` and the qa-session engine. With a throwaway QA account's session file:
- `MixelRemote.LabCheck check <session.json> <suffix> <folder>`: account, company, this computer, devices, groups,
  invitations, deploy links, updates, printing, sign-in screen, self-removal, deletion (needs user DPAPI: run in a
  desktop session or as SYSTEM, not in an ssh logon).
- `MixelRemote.LabCheck technician <session.json> <suffix> <folder> [<host-id> <tenant>]`: a real session.
- `mixel-remote.exe --live-session|--live-host <session.json> <suffix> <folder>`: the real app as technician / as host.

## Keyboard
Ctrl+1…4 sections, F5 refresh devices. In a session every key goes to the other computer (low-level hook while the
screen has the focus, Windows key and Alt+Tab included); Ctrl+Alt+End = Ctrl+Alt+Del, Ctrl+Alt+Home gives the
keyboard back, Ctrl+Alt+Enter full screen, Ctrl+Alt+Q ends the session.
