# Mixel Remote `2.1.22` vs. TeamViewer — Deep Quality Parity Audit (2026-11-23)

## 1. Summary

Mixel Remote `2.1.22` already matches or beats TeamViewer on core architecture: native apps on macOS/Windows/Linux/iOS/Android, end-to-end encrypted WebRTC with one-way-delay congestion control (`vrate2`), 47–57 FPS measured over 215 ms CH ↔ Mauritius WAN (`perf.log`), 64 concurrent sessions per technician, simultaneous host + viewer operation, rich 32 MB clipboard (`clip2`), Swiss/EU self-hosted infrastructure (`mixel-edge`), and zero per-channel annual lock-in.

- **Top 3 must-have quality gaps:** (1) Windows UAC / Secure Desktop prompts freezing capture & blocking input in normal sessions + split Remote IDs; (2) Native cursor shape sync (static arrow only — no I-beam/resize/hand/busy cursor); (3) Hard `1920×1088` encoder/protocol cap + Baseline 4:2:0 + nearest-neighbor CPU downscaling crushing 4K/Retina and dual-monitor `"All screens"` (`3840×1080` → `1920×540`).
- **Top 3 differentiators where Mixel can win:** (1) Native macOS/Windows/Linux/iOS/Android clients with Swiss-hosted E2E infrastructure (nDSG/GDPR native, no telemetry or commercial-use nag popups); (2) Unlimited/64 concurrent sessions + simultaneous host/viewer + multi-viewer unattended mirror included at a fraction of TeamViewer's per-channel price; (3) Built-in AI screen assistant & ticket-note generator integrated directly into Mixel ISM.

---

## 2. Competitor Positioning & Pricing (Public Sources, Checked 2026-11-23)

| Product | Positioning | Published Pricing (Annual Billing) | Public Sources |
| :--- | :--- | :--- | :--- |
| **TeamViewer Remote** (Market Leader) | Enterprise & SME remote support benchmark; 4K/60fps, seamless Windows UAC/service, VoIP, Tensor SSO | **Remote Access:** ~€21.50–€24.90/mo (3 devices, 1 channel) · **Business:** ~€43.90–€50.90/mo (1 user, 1 channel) · **Premium:** ~€104/mo (15 users, 1 channel) · **Corporate:** ~€212/mo (30 users, 3 channels) · **Tensor:** custom quote. 12-month upfront commitment; CHF converted + VAT. | [teamviewer.com](https://www.teamviewer.com/en/pricing/), [teamviewer.com features](https://www.teamviewer.com/en/products/remote/) |
| **Mixel Remote `2.1.22`** | Swiss-hosted, E2E-encrypted, native multi-platform remote support integrated with Mixel Suite & ISM | Included in Mixel Remote / Mixel Suite SME plans; no per-channel surcharges; 64 concurrent sessions per technician. | Internal (`next/` codebase, `2.1.22`) |

---

## 3. Quality Parity Matrix

Cells: ✅ full · 🟡 partial · ❌ missing · 💰 paid tier only.

### A. Video & Visual Quality

| Capability | Mixel Remote `2.1.22` | TeamViewer | Code Evidence / Source | Gap Type |
| :--- | :---: | :---: | :--- | :--- |
| Hardware H.264 encode (macOS VT, Windows MF) | ✅ | ✅ | `native_os_capture/src/macos_encoder.rs`, `windows_mf_encoder.rs` | — |
| Hardware H.264 encode/decode on Linux (VA-API/NVENC) | ❌ | ✅ | `mixel_engine/src/session_platform.rs:3-4` (software OpenH264 only) | must-have |
| Hardware H.264 decode on Windows viewer (DXVA2/D3D11) | ❌ | ✅ | `session_platform.rs:5-8`, `RemoteScreen.cs:227` (CPU OpenH264 + `CanvasBitmap`) | must-have |
| Zero-copy GPU presentation (macOS/Windows/Linux) | 🟡 | ✅ | Android `H264Decoder.kt:52` is zero-copy; macOS `VideoDecoder.swift:152` copies `CVPixelBuffer` → `CGImage`; Windows copies BGRA; Linux drains 8 MB/frame | must-have |
| Native 4K / Retina / 1440p & sharp `"All screens"` | ❌ | ✅ | `core_protocol/src/media.rs:52`, `remote_peer.rs:976, 2848`, `windows_mf_encoder.rs:173`, `desktop_h264.rs:36` hard-cap at `1920×1088` (`3840×1080` crushed to `1920×540`) | must-have |
| High-clarity text (High Profile / 4:4:4 / idle lossless pass) | ❌ | ✅ | Constrained Baseline 4:2:0 only (`macos_encoder.rs:110`, `windows_mf_encoder.rs:167, 281` `QualityVsSpeed=0`, `desktop_h264.rs:58` `CameraVideoRealTime`) | must-have |
| High-quality downscaling (bilinear/GPU vs nearest-neighbor) | ❌ | ✅ | `desktop_h264.rs:132-141` & `windows_mf_encoder.rs:94-118` use scalar nearest-neighbor CPU downscaling | must-have |
| In-place bitrate adaptation (no IDR glitch on macOS/Linux) | 🟡 | ✅ | Windows MF updates in-place (`remote_peer.rs:2088`); macOS/Linux recreate encoder on bitrate change (`remote_peer.rs:1516`) | nice-to-have |
| 60 FPS capture across all platforms | 🟡 | ✅ | Windows 60 FPS; macOS `capture_worker.rs:31-36` caps capture at `30` FPS | must-have |
| Bitrate ceiling > 6 Mbps for 4K / LAN | 🟡 | ✅ | `remote_peer.rs:1516-1522` caps >720p at `6 Mbps` | nice-to-have |

### B. Latency & Input Responsiveness

| Capability | Mixel Remote `2.1.22` | TeamViewer | Code Evidence / Source | Gap Type |
| :--- | :---: | :---: | :--- | :--- |
| Native cursor shape sync (I-beam, resize, hand, busy) | ❌ | ✅ | `macos.rs:413` hides host cursor; `RemoteScreenMac.swift:117` & `RemoteScreen.cs:149` show static `.arrow`/`.crosshair` only | must-have |
| AltGr (Right Alt) from macOS → European Windows hosts | ❌ | ✅ | `RemoteScreenMac.swift:222-241` collapses Right Option (`230`) into Left Alt (`226`) | must-have |
| Cross-layout keyboard translation & dead keys (CH ↔ US) | 🟡 | ✅ | Desktop viewers send HID scancodes only (`RemoteScreenMac.swift:243`, `RemoteScreen.cs:80`) | must-have |
| Smooth trackpad pixel scrolling & pinch-to-zoom | 🟡 | ✅ | `RemoteScreenMac.swift:192-207` quantizes wheel to coarse `Int16` ticks; no pinch zoom | nice-to-have |
| Mac `⌘C`/`⌘V` → Windows `Ctrl+C`/`Ctrl+V` mapping | ✅ | ✅ | `RemoteScreenMac.swift:204-272`, `SessionModel.swift:556` | — |

### C. Network, WAN & Reconnect Resilience

| Capability | Mixel Remote `2.1.22` | TeamViewer | Code Evidence / Source | Gap Type |
| :--- | :---: | :---: | :--- | :--- |
| Direct UDP P2P + STUN + UDP/TCP/TLS TURN fallback | ✅ | ✅ | `core_protocol/src/stream_turn.rs`, `transport.rs` | — |
| Adaptive congestion control + NACK + reorder buffer | ✅ | ✅ | `video_rate.rs` (`vrate2`), `video_pacing.rs`, `media.rs:424` (47–57 FPS @ 215 ms RTT) | — |
| Forward Error Correction (FlexFEC/ULPFEC) for lossy WAN | ❌ | ✅ | `core_protocol/src/transport.rs:42-57` (NACK + TWCC only; 1–3% loss stalls 250–450 ms) | differentiator |
| Attended-session seamless ICE restart on network switch | ❌ | ✅ | `mixel_engine/src/reconnect.rs:1-25` (only unattended/password sessions auto-reconnect) | must-have |
| Sleep/wake session token survival without forced logout | ✅ | ✅ | `account_auth.rs` (shipped in `2.1.20`) | — |

### D. Audio, Clipboard, File Transfer & Session Tools

| Capability | Mixel Remote `2.1.22` | TeamViewer | Code Evidence / Source | Gap Type |
| :--- | :---: | :---: | :--- | :--- |
| Remote system audio (Opus 48 kHz stereo over RTP/UDP) | 🟡 | ✅ | `core_protocol/src/sound.rs:1-12` uses 24 kHz mono IMA ADPCM (96 kbps) over ordered SCTP control channel (HoL-blocks input/audio) | must-have |
| Two-way VoIP (technician ↔ customer mic) | ❌ | ✅ | Not implemented (`remote_sound.rs` is host→viewer system audio only) | nice-to-have |
| Bidirectional clipboard (text + images + files) | ✅ | ✅ | `core_protocol/src/clipboard.rs` (`clip2`, up to 32 MB, SHA-256 dedup) | — |
| Drag-and-drop file transfer onto remote screen | ❌ | ✅ | No drop handlers on macOS/Windows/Linux viewers; dialog-only via Files panel | must-have |
| Privacy / Black screen + Block remote input | ✅ | ✅ | `host_actions.rs`, `SessionPane.swift:390-391` | — |
| Remote printing (Windows / macOS / Linux) | 🟡 | ✅ | Implemented (`host_print.rs`), but Windows uses single fixed `job.pdf` spool path (`host_print.rs:67`) | must-have |
| Session recording with audio track | 🟡 | ✅ | `remote_recording.rs:1` is video-only MP4 | nice-to-have |
| Screen annotation / whiteboard + chat + AI ticket note | ✅ | 🟡 | `DrawingViews.swift`, `AssistantModel.swift` (AI screen explanation & ticket note) | differentiator |

### E. Unattended, Pre-Login / UAC, WoL & Fleet Reliability

| Capability | Mixel Remote `2.1.22` | TeamViewer | Code Evidence / Source | Gap Type |
| :--- | :---: | :---: | :--- | :--- |
| Seamless Windows UAC / Secure Desktop + unified Device ID | ❌ | ✅ | `windows_signin_host.rs:1-5` stops on user login & uses a separate Remote ID; `windows_input.rs:1-2` fails on UAC; `capture_worker.rs:188` stalls on `ACCESS_LOST` | must-have |
| macOS login-window host & FileVault authrestart | 🟡 | ✅ | `macos_prelogin_install.rs:13` gated on Apple virtual HID entitlement; no `fdesetup authrestart` helper | nice-to-have |
| Wake-on-LAN over WAN (peer-relayed on target subnet) | 🟡 | ✅ | `native_commands.rs:1540` sends WoL from local machine only | differentiator |
| Multi-arch self-updater (`target_arch` aware) | 🟡 | ✅ | `self_update.rs:87-95` hardcodes `macos-arm64` / `windows-x64` / `linux-x64` | must-have |
| Concurrent sessions (64 tabs + host/viewer simultaneously) | ✅ | 💰 | Shipped in `2.1.22` (`MAX_SESSIONS=64`, `MAX_PER_PEER=64`, multi-viewer mirror) | differentiator |

### F. UI/UX & Multi-Monitor Workflow

| Capability | Mixel Remote `2.1.22` | TeamViewer | Code Evidence / Source | Gap Type |
| :--- | :---: | :---: | :--- | :--- |
| Quality preset selector (Auto / Optimize Speed / Quality) | ❌ | ✅ | `SessionPane.swift:357-385`, `SessionView.cs:441-470` are read-only stats popovers | must-have |
| 1:1 Original Size + Zoom/Pan across all desktop viewers | 🟡 | ✅ | Windows has `Actual size` (`RemoteScreen.cs:40`); macOS (`RemoteScreenMac.swift:65`) & Linux (`session.rs:30`) are Fit-only | must-have |
| Multi-monitor: one window per remote monitor | ❌ | ✅ | Monitor switcher + crushed `"All screens"` only | differentiator |
| Native dark mode, DE/FR/IT/EN localization, tabs + pop-out windows | ✅ | ✅ | `ConsoleTheme.swift`, `Translator.swift`, `WorkspaceModel.swift` | — |

---

## 4. Ranked Gap List (Scored: Impact × Confidence ÷ Effort)

Effort scale: `S = 1`, `M = 2`, `L = 3`, `XL = 5`.

| Rank | Gap | Impact (1–5) | Conf. (1–3) | Effort | Score | Why It Matters |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **1** | **Fix macOS AltGr (`230`) + layout-aware typing mode** | 5 | 3 | S (1) | **15.0** | Swiss/DE/FR Mac technicians cannot type `@ € \ \| { } [ ]` on Windows hosts today. |
| **2** | **Fix self-updater `target_arch` + Windows remote print spool race** | 4 | 3 | S (1) | **12.0** | Prevents silent update breakage on Intel Macs / ARM64 devices and dropped print jobs. |
| **3** | **Native cursor shape sync (I-beam, resize, hand, busy)** | 5 | 3 | M (2) | **7.5** | Biggest perceived responsiveness & usability upgrade — makes remote feel local like TeamViewer. |
| **4** | **Drag-and-drop file transfer onto remote screen** | 4 | 3 | M (2) | **6.0** | Technicians expect to drag an installer/PDF straight onto the remote desktop. |
| **5** | **Quality selector (Auto/Speed/Quality) + 1:1 Original Size on macOS/Linux** | 4 | 3 | M (2) | **6.0** | Lets technicians choose sharp text vs. low-latency motion and inspect HiDPI screens 1:1. |
| **6** | **Lift `1920×1088` cap → 4K/HiDPI + High Profile + bilinear/GPU downscale + 60 FPS macOS** | 5 | 3 | L (3) | **5.0** | Eliminates blurry text, nearest-neighbor aliasing, and crushed `1920×540` `"All screens"`. |
| **7** | **Zero-copy GPU decode/present (macOS `AVSampleBufferDisplayLayer`, Windows MF/D3D11, Linux VA-API)** | 5 | 3 | L (3) | **5.0** | Cuts 10–25 ms latency, halves CPU/battery usage, enables smooth 4K 60 FPS. |
| **8** | **Migrate audio to Opus 48 kHz stereo over RTP/UDP (+ MP4 audio mux)** | 4 | 3 | L (3) | **4.0** | Eliminates SCTP head-of-line blocking on input & audio, improves sound clarity at lower bitrate. |
| **9** | **Unified Windows LocalSystem service (single Device ID + seamless UAC & Login Screen)** | 5 | 3 | XL (5) | **3.0** | #1 enterprise Windows IT blocker — lets technicians click UAC & survive login transitions. |
| **10** | **WAN FlexFEC + attended ICE restart + peer-relayed Wake-on-LAN** | 4 | 3 | XL (5) | **2.4** | Removes 250–450 ms intercontinental packet-loss stalls, survives Wi-Fi switches, enables WAN WoL. |

---

## 5. Build-Ready Backlog (Top 10 Items)

### Item 1 — Fix macOS AltGr (Right Alt `230`) & Optional Layout-Aware Keyboard Mode (`S`)
- **User story:** As a Swiss/DE/FR technician on macOS controlling a Windows host, I can type `@`, `€`, `\`, `|`, `{`, `}`, `[`, `]`, `~` with Right Option (AltGr) and toggle layout-aware typing when host/client keyboard layouts differ.
- **Affected code:** `next/apps/mixel-remote-apple/Sources/MixelRemoteKit/Views/RemoteScreenMac.swift` (`syncModifiers`, `keyDown`), `next/apps/mixel-remote-apple/Sources/MixelRemoteKit/ViewModels/SessionModel.swift`, `next/apps/mixel-remote-windows/src/MixelRemote.App/Session/RemoteScreen.cs`.
- **Acceptance criteria:**
  - Right Option (`NX_DEVICERALTKEYMASK` / `0x40`) sends HID `230` (Right Alt) instead of collapsing to `226` (Left Alt).
  - Unit tests verify Right Option emits usage `230` while Left Option emits `226`, and modifiers never invert after `⌘Tab`.
  - Optional per-session toggle in More menu: "Send characters (match my keyboard)" using `Input::Text` for printable non-shortcut keys.
- **i18n:** Add 2 strings to `make-strings.py` (`en`, `de`, `fr`, `it`).

### Item 2 — Fix Self-Updater `target_arch` Detection & Windows Print Unique Spool Files (`S`)
- **User story:** As an Intel Mac / ARM64 user, auto-updates fetch the right architecture; as a technician printing multiple documents rapidly, no print job overwrites another.
- **Affected code:** `next/crates/mixel_engine/src/self_update.rs:87-95`, `next/crates/mixel_engine/src/host_print.rs:67, 117`.
- **Acceptance criteria:**
  - `self_update.rs` uses `cfg!(target_arch = ...)` (keeping universal macOS DMG mapping explicit).
  - `host_print.rs` writes per-job UUID spool files (`job-<uuid>.pdf`) and cleans them after transfer.
  - Unit tests cover concurrent print spool creation and arch mapping.

### Item 3 — Native Cursor Shape Sync (`M`)
- **User story:** As a technician, I see the remote computer's real cursor shape (I-beam, resize arrows, hand, busy spinner) locally at zero latency.
- **Affected code:** `crates/core_protocol/src/input.rs` (new `CursorShape` message: standard cursor enum + optional RGBA bitmap + hotspot + cache hash), `crates/native_os_capture/src/{macos.rs, windows_capture.rs, x11_capture.rs}`, `RemoteScreenMac.swift`, `RemoteScreen.cs`, `linux/src/ui/session.rs`.
- **Acceptance criteria:**
  - Host polls/observes cursor shape changes and sends deduplicated updates (hash-cached, <1 KB/s).
  - Viewers apply `NSCursor` / WinUI `InputSystemCursor` / `gdk::Cursor` immediately without redrawing video.
  - Unit + live test verify arrow → I-beam → resize cursor transitions.

### Item 4 — Drag-and-Drop File Transfer onto Remote Screen (`M`)
- **User story:** As a technician, I can drag a file from Finder/Explorer onto the remote screen window to send it immediately.
- **Affected code:** `RemoteScreenMac.swift` (`registerForDraggedTypes([.fileURL])` + `performDragOperation`), `RemoteScreen.cs` (`AllowDrop = true`, `Drop`), `linux/src/ui/session.rs` (`gtk::DropTarget`), `SessionModel.swift` / `SessionModel.cs`.
- **Acceptance criteria:**
  - Dragging a file over the remote screen highlights a clean drop overlay (`"Drop to send to <device>"`); dropping invokes `sendFile(path:)`.
  - Respects `remote.files` permission (shows clear toast if file transfer is disabled).
  - i18n keys added in `en`, `de`, `fr`, `it`.

### Item 5 — Quality Selector (Auto / Speed / Quality) + 1:1 Original Size & Zoom on macOS/Linux (`M`)
- **User story:** As a technician, I can switch between Auto, Optimize Speed, and Optimize Quality, and toggle Fit-to-Window vs. 1:1 Actual Size on macOS and Linux.
- **Affected code:** `crates/mixel_engine/src/remote_peer.rs` (quality preset action `quality:auto|speed|quality`), `SessionPane.swift`, `RemoteScreenMac.swift` (`NSScrollView` + 1:1 / Fit toggle), `SessionView.cs`, `linux/src/ui/session.rs`.
- **Acceptance criteria:**
  - Quality popover allows switching `Auto` / `Optimize speed` (prefer 60 fps + lower res) / `Optimize quality` (prefer native res + higher bitrate ceiling).
  - macOS & Linux viewers support `Fit` and `Actual size (1:1)` with smooth panning, matching Windows.

### Item 6 — 4K / Retina / Sharp Multi-Monitor Video + High Profile + Bilinear/GPU Scaling + 60 FPS macOS (`L`)
- **User story:** As a technician viewing a 4K/Retina display or `"All screens"`, text is razor-sharp instead of downscaled to 1080p or nearest-neighbor aliased.
- **Affected code:** `crates/core_protocol/src/media.rs:52`, `crates/mixel_engine/src/{remote_video.rs, remote_peer.rs, capture_worker.rs}`, `crates/native_os_capture/src/{macos_encoder.rs, windows_mf_encoder.rs, desktop_h264.rs}`.
- **Acceptance criteria:**
  - Raise protocol/encoder bounds from `1920×1088` to `3840×2160` (and `7680×2160` for dual-4K `"All screens"`), bitrate ceiling to `16–20 Mbps` on LAN.
  - Switch encoders to H.264 High Profile (`kVTProfileLevel_H264_High_AutoLevel` / `eAVEncH264VProfile_High`, `QualityVsSpeed = 50`, OpenH264 `ScreenContentRealTime` + AQ on) and replace nearest-neighbor downscaling with bilinear/box filter (or D3D11 GPU VideoProcessor on Windows).
  - Raise macOS ScreenCaptureKit capture from `30` to `60` FPS (adaptive to pacing), and update macOS VideoToolbox bitrate in-place (`VTSessionSetProperty`) without encoder restart.
  - Add idle sharp-frame refinement (clean QP boost after 300 ms of static screen).

### Item 7 — Zero-Copy Hardware Decode & GPU Presentation on Desktop Viewers (`L`)
- **User story:** As a technician running 4–8 simultaneous sessions on a laptop, decoding stays cool, silent, and ultra-low-latency.
- **Affected code:** `apps/mixel-remote-apple/Sources/MixelRemoteKit/{Support/VideoDecoder.swift, Views/RemoteScreenMac.swift}`, `crates/native_os_capture/src/` (Windows MF hardware decoder + Linux VA-API), `apps/mixel-remote-windows/.../RemoteScreen.cs`, `apps/mixel-remote-linux/src/video.rs`.
- **Acceptance criteria:**
  - macOS renders `CMSampleBuffer` directly via `AVSampleBufferDisplayLayer` (zero `CGImage` copies).
  - Windows decodes `MRH1` via Media Foundation DXVA2/D3D11 and presents directly without CPU OpenH264.
  - Linux decodes via VA-API/GStreamer hwdec with fallback to OpenH264, eliminating the 8 MB `drain(..32)` memmove.

### Item 8 — Opus 48 kHz Stereo Audio over Dedicated RTP/UDP Track (+ Recording Mux) (`L`)
- **User story:** As a technician listening to remote system audio, sound is crisp stereo with zero stutter even while transferring clipboard/files, and is included in MP4 recordings.
- **Affected code:** `crates/core_protocol/src/sound.rs`, `crates/mixel_engine/src/{remote_sound.rs, remote_peer.rs, remote_recording.rs}`.
- **Acceptance criteria:**
  - Negotiate `opus-rtp` capability (fallback to `mixel-sound-v1` ADPCM for older peers).
  - Stream 48 kHz stereo Opus at 48–64 kbps with 20 ms frames over a separate WebRTC audio track (no SCTP HoL blocking).
  - Mux audio track into MP4 recordings when sound is enabled.

### Item 9 — Unified Windows LocalSystem Service (Single Device ID + Seamless UAC & Login Screen) (`XL`)
- **User story:** As an IT technician supporting Windows machines, I use one Device ID per PC, can click UAC prompts during normal sessions, and stay connected across login/logout/reboot.
- **Affected code:** `crates/mixel_engine/src/{windows_signin_host.rs, capture_worker.rs}`, `crates/native_os_capture/src/{windows_input.rs, windows_capture.rs}`, Windows installer/service registration.
- **Acceptance criteria:**
  - Single `MixelRemoteService` (LocalSystem) owns the device identity & broker connection and spawns a session-0/active-console capture+input helper with `uiAccess`/SYSTEM token across `Winlogon` (UAC/login) and `Default` desktops.
  - UAC prompts render live and accept clicks/keystrokes without stalling DXGI capture.
  - Logging in or locking Windows keeps the same Remote ID and session alive.

### Item 10 — WAN FlexFEC + Attended ICE Restart + Peer-Relayed Wake-on-LAN (`XL`)
- **User story:** As a technician supporting intercontinental clients (CH ↔ MU), 1–3% packet loss never causes 400 ms freezes, Wi-Fi switches resume without re-consent, and WoL wakes sleeping office PCs remotely.
- **Affected code:** `crates/core_protocol/src/transport.rs`, `crates/mixel_engine/src/{reconnect.rs, host_actions.rs, native_commands.rs}`, `crates/signaling_server/src/registry.rs`.
- **Acceptance criteria:**
  - Adaptive XOR/FlexFEC parity packets recover single-packet loss in 0 RTT on high-RTT links.
  - Attended sessions perform WebRTC ICE restart on transient network drop (up to 30 s) without requiring a new user consent dialog.
  - Broker routes signed `WakeOnLan` requests to an online peer on the same tenant/LAN subnet to broadcast the magic packet.

---

## 6. Where Mixel Remote `2.1.22` Is Already Ahead of TeamViewer

- **Swiss-hosted, zero-lock-in sovereignty:** Self-hosted on Mixel's own Swiss infrastructure (`mixel-edge`) with nDSG/GDPR compliance by architecture and zero commercial-use nag popups or forced annual seat/channel escalations.
- **64 concurrent sessions + simultaneous host/viewer + multi-viewer unattended mirror:** TeamViewer charges €104–€212+/mo for 1–3 concurrent channels; Mixel Remote `2.1.22` supports up to 64 concurrent sessions per technician out of the box.
- **Measured intercontinental WAN efficiency:** `vrate2` one-way-delay congestion control + token-bucket pacing achieves **47–57 FPS at 215 ms RTT with 0–10 ms queue delay** (measured CH ↔ Mauritius).
- **Native AI Support Assistant & ISM Integration:** Built-in screen explanation and automated service-desk ticket note generation directly inside the session (`AssistantModel.swift`).
- **Lightweight, 100% native clients** (SwiftUI on Apple, WinUI/C# on Windows, GTK4/Rust on Linux) with Ed25519-signed self-updates and 32 MB rich clipboard (`clip2`).
