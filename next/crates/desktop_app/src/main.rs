#![cfg_attr(all(target_os = "windows", not(debug_assertions), not(test)), windows_subsystem = "windows")]

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod session_platform;
#[cfg(target_os = "linux")]
mod wayland_portal;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod account_auth;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod saas_commands;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod device_identity;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod session_store;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod host_actions;
mod session_guard;
mod host_print;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod agent;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod ai_assistant;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod self_update;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod autostart;
#[cfg(target_os = "macos")]
mod local_session;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_chat;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_clipboard;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_control;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_file_transport;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_files;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_peer;
#[cfg(target_os = "macos")]
mod macos_picker;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_recording;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_recovery;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_session;
mod remote_sound;
#[cfg(target_os = "macos")]
mod macos_startup_host;
#[cfg(target_os = "windows")]
mod windows_signin_host;
#[cfg(target_os = "macos")]
mod macos_support;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod device_tickets;
#[cfg(target_os = "macos")]
mod macos_tray;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod remote_video;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod service_config;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod system_browser;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
use native_os_capture::stream::FrameMailbox;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
use std::sync::{Arc, Mutex};
use tauri::Manager;

#[cfg(target_os = "macos")]
struct Worker {
    stop: std::sync::mpsc::SyncSender<()>,
    thread: Option<std::thread::JoinHandle<Result<(), String>>>,
    frames: Arc<FrameMailbox>,
}
#[cfg(target_os = "macos")]
impl Worker {
    fn start(source: String, confirm_full_display: bool) -> Result<Self, String> {
        let (stop, rx) = std::sync::mpsc::sync_channel(1);
        let (ready, wait) = std::sync::mpsc::sync_channel(1);
        let thread = std::thread::Builder::new()
            .name("mixel-capture-owner".into())
            .spawn(move || {
                let result = (|| {
                    if let Some(id) = source.strip_prefix("display:") {
                        if !confirm_full_display {
                            return Err("Confirm sharing the selected display".into());
                        }
                        let id: u32 = id.parse().map_err(|_| "Invalid display source")?;
                        let display = native_os_capture::macos::displays()
                            .map_err(|e| e.to_string())?
                            .into_iter()
                            .find(|display| display.id == id)
                            .ok_or("The selected display is unavailable")?;
                        return native_os_capture::macos::MacStream::start(
                            display.id,
                            display.width,
                            display.height,
                            30,
                        )
                        .map_err(|e| e.to_string());
                    }
                    if source != "own-fixture" || confirm_full_display {
                        return Err("Invalid capture source or confirmation".into());
                    }
                    let target = native_os_capture::macos::own_windows()
                        .map_err(|e| e.to_string())?
                        .into_iter()
                        .find(|w| w.title == "Mixel-Remote Capture Fixture")
                        .ok_or("The app's capture fixture window is unavailable")?;
                    let stream = native_os_capture::macos::MacStream::start_own_window(
                        target.id, 640, 420, 30,
                    )
                    .map_err(|e| e.to_string())?;
                    Ok::<_, String>(stream)
                })();
                let mut stream = match result {
                    Ok(stream) => {
                        let _ = ready.send(Ok(stream.frames.clone()));
                        stream
                    }
                    Err(error) => {
                        let _ = ready.send(Err(error.clone()));
                        return Err(error);
                    }
                };
                let _ = rx.recv();
                stream.stop().map_err(|e| e.to_string())
            })
            .map_err(|e| e.to_string())?;
        match wait.recv_timeout(std::time::Duration::from_secs(35)) {
            Ok(Ok(frames)) => Ok(Self {
                stop,
                thread: Some(thread),
                frames,
            }),
            Ok(Err(error)) => {
                let _ = thread.join();
                Err(error)
            }
            Err(_) => {
                let _ = stop.try_send(());
                if thread.is_finished() {
                    let _ = thread.join();
                }
                Err("Capture startup timed out".into())
            }
        }
    }
    fn stop(&mut self) -> Result<(), String> {
        let _ = self.stop.try_send(());
        self.frames
            .close(native_os_capture::stream::CaptureError::Stopped);
        if let Some(thread) = self.thread.take() {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
            while !thread.is_finished() {
                if std::time::Instant::now() >= deadline {
                    return Err(
                        "The capture owner did not stop promptly; frame delivery has been revoked"
                            .into(),
                    );
                }
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
            thread
                .join()
                .map_err(|_| "Capture owner thread failed".to_owned())??;
        }
        Ok(())
    }
}
#[cfg(target_os = "macos")]
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
#[cfg(target_os = "windows")]
use native_os_capture::windows_capture::{
    Display as PlatformDisplay, WindowsCapture as PlatformCapture,
};
#[cfg(target_os = "linux")]
use native_os_capture::x11_capture::{Display as PlatformDisplay, X11Capture as PlatformCapture};
/// Lab builds only (feature `qa-session`): append a capture event to
/// %TEMP%\mixel-capture.log. Release builds compile this to nothing.
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[allow(unused_variables)]
fn lab_capture_log(message: &str) {
    #[cfg(feature = "qa-session")]
    {
        use std::io::Write;
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(std::env::temp_dir().join("mixel-capture.log"))
        {
            let _ = writeln!(file, "{:?} {message}", std::time::SystemTime::now());
        }
    }
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
struct Worker {
    stop: Arc<std::sync::atomic::AtomicBool>,
    thread: Option<std::thread::JoinHandle<Result<(), String>>>,
    frames: Arc<FrameMailbox>,
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
impl Worker {
    fn start(display: PlatformDisplay) -> Result<Self, String> {
        use std::sync::atomic::{AtomicBool, Ordering};
        let stop = Arc::new(AtomicBool::new(false));
        let frames = Arc::new(FrameMailbox::default());
        let (ready, wait) = std::sync::mpsc::sync_channel(1);
        let cancelled = stop.clone();
        let delivery = frames.clone();
        let thread = std::thread::Builder::new()
            .name("mixel-display-capture".into())
            .spawn(move || {
                // Capture the desktop that is on screen (the SYSTEM host starts on the
                // sign-in desktop while a user may be signed in).
                #[cfg(target_os = "windows")]
                native_os_capture::windows_capture::attach_input_desktop();
                let first = PlatformCapture::open(&display);
                #[cfg(target_os = "windows")]
                let first = match first {
                    // Shown desktop not readable yet (secure prompt): wait for it below.
                    Err(native_os_capture::stream::CaptureError::Target) => {
                        let _ = ready.send(Ok(()));
                        lab_capture_log("desktop not readable yet; waiting");
                        None
                    }
                    other => Some(other),
                };
                #[cfg(not(target_os = "windows"))]
                let first = Some(first);
                let capture = match first {
                    None => None,
                    Some(Ok(capture)) => {
                        let _ = ready.send(Ok(()));
                        Some(capture)
                    }
                    Some(Err(error)) => {
                        let text = error.to_string();
                        let _ = ready.send(Err(text.clone()));
                        delivery.close(error);
                        return Err(text);
                    }
                };
                let mut capture = capture;
                while !cancelled.load(Ordering::Acquire) {
                    let Some(active) = capture.as_mut() else {
                        // Windows replaced the user's desktop (lock screen or a
                        // secure prompt). The previous duplication is released;
                        // reattach to the same display once it is back.
                        std::thread::sleep(std::time::Duration::from_millis(500));
                        #[cfg(target_os = "windows")]
                        native_os_capture::windows_capture::attach_input_desktop();
                        match PlatformCapture::open(&display) {
                            Ok(next) => {
                                lab_capture_log("desktop reattached");
                                capture = Some(next);
                            }
                            Err(error) => lab_capture_log(&format!("reopen failed: {error}")),
                        }
                        continue;
                    };
                    match active.next_frame(std::time::Duration::from_millis(100)) {
                        Ok(Some(frame)) => delivery.publish(frame),
                        Ok(None) => {}
                        #[cfg(target_os = "windows")]
                        Err(native_os_capture::stream::CaptureError::Target) => {
                            lab_capture_log("desktop lost; waiting for it to return");
                            capture = None;
                        }
                        Err(error) => {
                            let text = error.to_string();
                            delivery.close(error);
                            return Err(text);
                        }
                    }
                }
                delivery.close(native_os_capture::stream::CaptureError::Stopped);
                Ok(())
            })
            .map_err(|error| error.to_string())?;
        match wait.recv_timeout(std::time::Duration::from_secs(10)) {
            Ok(Ok(())) => Ok(Self {
                stop,
                thread: Some(thread),
                frames,
            }),
            result => {
                stop.store(true, Ordering::Release);
                frames.close(native_os_capture::stream::CaptureError::Stopped);
                if thread.is_finished() {
                    let _ = thread.join();
                }
                Err(match result {
                    Ok(Err(error)) => error,
                    _ => "Display capture initialization timed out".into(),
                })
            }
        }
    }
    fn stop(&mut self) -> Result<(), String> {
        self.stop.store(true, std::sync::atomic::Ordering::Release);
        self.frames
            .close(native_os_capture::stream::CaptureError::Stopped);
        if let Some(thread) = self.thread.take() {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
            while !thread.is_finished() {
                if std::time::Instant::now() >= deadline {
                    return Err("The display capture owner did not stop promptly; frame delivery has been revoked".into());
                }
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
            thread
                .join()
                .map_err(|_| "Display capture owner failed".to_owned())??;
        }
        Ok(())
    }
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
#[derive(serde::Serialize)]
struct CaptureTarget {
    id: String,
    name: String,
    full_display: bool,
}
#[tauri::command]
async fn capture_targets(window: tauri::WebviewWindow) -> Result<Vec<CaptureTarget>, String> {
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    if service_config::controller_only() {
        return Ok(Vec::new());
    }
    #[cfg(target_os = "macos")]
    {
        let mut targets = vec![CaptureTarget {
            id: "own-fixture".into(),
            name: "Development fixture (this app only)".into(),
            full_display: false,
        }];
        if service_config::relay_fixture_enabled() {
            targets.push(CaptureTarget {
                id: "qa-relay-fixture".into(),
                name: "Mixel QA test window (view only)".into(),
                full_display: false,
            });
        }
        if native_os_capture::macos::screen_access() {
            let displays = tauri::async_runtime::spawn_blocking(native_os_capture::macos::displays)
                .await
                .map_err(|_| "Display discovery failed")?
                .map_err(|e| e.to_string())?;
            targets.extend(displays.into_iter().map(|display| CaptureTarget {
                id: format!("display:{}", display.id),
                name: format!(
                    "Display {} · {} × {}",
                    display.id, display.width, display.height
                ),
                full_display: true,
            }));
        }
        Ok(targets)
    }
    #[cfg(target_os = "windows")]
    {
        native_os_capture::windows_capture::displays()
            .map(|displays| {
                displays
                    .into_iter()
                    .map(|display| CaptureTarget {
                        id: format!(
                            "dxgi:{}:{}:{}",
                            display.adapter, display.output, display.name
                        ),
                        name: format!("{} · {} × {}", display.name, display.width, display.height),
                        full_display: true,
                    })
                    .collect()
            })
            .map_err(|error| error.to_string())
    }
    #[cfg(target_os = "linux")]
    {
        if wayland_portal::available() {
            return Ok(vec![CaptureTarget {
                id: wayland_portal::SOURCE.into(),
                name: "Choose a display in the system sharing dialog".into(),
                full_display: true,
            }]);
        }
        tauri::async_runtime::spawn_blocking(|| {
            native_os_capture::x11_capture::displays()
                .map(|displays| {
                    displays
                        .into_iter()
                        .map(|display| CaptureTarget {
                            id: format!("x11:{}:{}", display.screen, display.root),
                            name: format!(
                                "X11 screen {} · {} × {}",
                                display.screen, display.width, display.height
                            ),
                            full_display: true,
                        })
                        .collect()
                })
                .map_err(|error| error.to_string())
        })
        .await
        .map_err(|_| "X11 display discovery failed".to_owned())?
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        Ok(Vec::new())
    }
}
#[derive(Clone, Default)]
struct CaptureState {
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    worker: Arc<Mutex<Option<Worker>>>,
}
fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Capture commands are restricted to the local main window".into());
    }
    #[cfg(target_os = "macos")]
    if window
        .state::<QuitState>()
        .0
        .load(std::sync::atomic::Ordering::Acquire)
        != 0
    {
        return Err("Mixel Remote is closing its active sessions".into());
    }
    Ok(())
}
#[cfg(target_os = "macos")]
fn ensure_fixture(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.app_handle().get_webview_window("fixture").is_none() {
        tauri::WebviewWindowBuilder::new(
            window.app_handle(),
            "fixture",
            tauri::WebviewUrl::App("fixture.html".into()),
        )
        .title("Mixel-Remote Capture Fixture")
        .inner_size(640.0, 420.0)
        .position(50.0, 60.0)
        .focused(false)
        .build()
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
#[tauri::command]
async fn start_capture(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, CaptureState>,
    target: String,
    confirm_full_display: bool,
) -> Result<String, String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    if target == "own-fixture" {
        ensure_fixture(&window)?;
    }
    #[cfg(target_os = "macos")]
    {
        if target != "own-fixture" && !target.starts_with("display:") {
            return Err("Select a valid capture source".into());
        }
        let state = state.inner().clone();
        tauri::async_runtime::spawn_blocking(move || {
            let mut worker = state.worker.lock().map_err(|_| "Capture state failed")?;
            if worker.is_some() {
                return Err("Capture is already running".into());
            }
            let display = target.starts_with("display:");
            *worker = Some(Worker::start(target, confirm_full_display)?);
            Ok(if display {
                "ScreenCaptureKit is capturing the selected display"
            } else {
                "ScreenCaptureKit is capturing this app's fixture window only"
            }
            .into())
        })
        .await
        .map_err(|e| e.to_string())?
    }
    #[cfg(target_os = "windows")]
    {
        if !confirm_full_display {
            return Err(
                "Confirm sharing the entire selected display before starting capture".into(),
            );
        }
        let display = native_os_capture::windows_capture::displays()
            .map_err(|error| error.to_string())?
            .into_iter()
            .find(|display| {
                format!(
                    "dxgi:{}:{}:{}",
                    display.adapter, display.output, display.name
                ) == target
            })
            .ok_or("Selected display is no longer available")?;
        let state = state.inner().clone();
        tauri::async_runtime::spawn_blocking(move || {
            let mut worker = state.worker.lock().map_err(|_| "Capture state failed")?;
            if worker.is_some() {
                return Err("Capture is already running".into());
            }
            *worker = Some(Worker::start(display)?);
            Ok("DXGI is capturing the entire selected display".into())
        })
        .await
        .map_err(|error| error.to_string())?
    }
    #[cfg(target_os = "linux")]
    {
        if !confirm_full_display {
            return Err(
                "Confirm sharing the entire selected display before starting capture".into(),
            );
        }
        let state = state.inner().clone();
        tauri::async_runtime::spawn_blocking(move || {
            let mut worker = state.worker.lock().map_err(|_| "Capture state failed")?;
            if worker.is_some() {
                return Err("Capture is already running".into());
            }
            let display = native_os_capture::x11_capture::displays()
                .map_err(|error| error.to_string())?
                .into_iter()
                .find(|display| format!("x11:{}:{}", display.screen, display.root) == target)
                .ok_or("Selected X11 screen is no longer available")?;
            *worker = Some(Worker::start(display)?);
            Ok("X11 is capturing the entire selected screen".into())
        })
        .await
        .map_err(|_| "X11 capture owner failed".to_owned())?
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        let _ = (state, target, confirm_full_display);
        Err("No display capture adapter is included in this build".into())
    }
}
#[tauri::command]
async fn stop_capture(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, CaptureState>,
) -> Result<String, String> {
    main_window(&window)?;
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    {
        let state = state.inner().clone();
        tauri::async_runtime::spawn_blocking(move || {
            if let Some(mut worker) = state
                .worker
                .lock()
                .map_err(|_| "Capture state failed")?
                .take()
            {
                worker.stop()?;
            }
            Ok("Capture stopped; retained frame slot cleared".into())
        })
        .await
        .map_err(|e| e.to_string())?
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        let _ = state;
        Ok("Capture is stopped".into())
    }
}
#[tauri::command]
async fn next_frame(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, CaptureState>,
    after: u64,
) -> Result<tauri::ipc::Response, String> {
    main_window(&window)?;
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    {
        let frames = state
            .worker
            .lock()
            .map_err(|_| "Capture state failed")?
            .as_ref()
            .ok_or("Capture is stopped")?
            .frames
            .clone();
        tauri::async_runtime::spawn_blocking(move || {
            let (sequence, frame) = match frames.next(after, std::time::Duration::from_secs(3)) {
                Ok(frame) => frame,
                Err(native_os_capture::stream::CaptureError::Timeout) => {
                    return Ok(tauri::ipc::Response::new(Vec::<u8>::new()));
                }
                Err(error) => return Err(error.to_string()),
            };
            let mut packet = Vec::with_capacity(20 + frame.pixels().len());
            packet.extend((frame.width() as u32).to_le_bytes());
            packet.extend((frame.height() as u32).to_le_bytes());
            packet.extend((frame.stride() as u32).to_le_bytes());
            packet.extend(sequence.to_le_bytes());
            packet.extend(frame.pixels());
            Ok(tauri::ipc::Response::new(packet))
        })
        .await
        .map_err(|e| e.to_string())?
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        let _ = (state, after);
        Err("Native capture is unavailable on this target".into())
    }
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
fn request_input_access(window: tauri::WebviewWindow) -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    { Ok(native_os_capture::macos_input::MacInjector::request_post_access()) }
    #[cfg(not(target_os = "macos"))]
    { Ok(session_platform::input_access()) }
}
#[cfg(target_os = "macos")]
#[tauri::command]
fn open_local_viewer(window: tauri::WebviewWindow) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    ensure_fixture(&window)?;
    if let Some(viewer) = window.app_handle().get_webview_window("viewer") {
        viewer.set_focus().map_err(|e| e.to_string())?;
    } else {
        tauri::WebviewWindowBuilder::new(
            window.app_handle(),
            "viewer",
            tauri::WebviewUrl::App("viewer.html".into()),
        )
        .title("Mixel-Remote — Local WebRTC Viewer")
        .inner_size(820.0, 650.0)
        .build()
        .map_err(|e| e.to_string())?;
    }
    Ok(())
}
#[cfg(target_os = "macos")]
fn session_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if !matches!(window.label(), "main" | "viewer") {
        return Err("Session commands are restricted to the local dashboard and viewer".into());
    }
    Ok(())
}
#[cfg(target_os = "macos")]
#[tauri::command]
fn local_session_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, local_session::LocalSession>,
) -> Result<local_session::Status, String> {
    session_window(&window)?;
    state.status()
}
#[cfg(target_os = "macos")]
#[tauri::command]
async fn request_local_session(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, local_session::LocalSession>,
    control: bool,
) -> Result<local_session::Status, String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    if window.label() != "viewer" {
        return Err("Only the local viewer can request this QA session".into());
    }
    ensure_fixture(&window)?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.request(control))
        .await
        .map_err(|e| e.to_string())?
}
#[cfg(target_os = "macos")]
#[tauri::command]
fn decide_local_session(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, local_session::LocalSession>,
    request: u64,
    accept: bool,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    state.decide(request, accept)
}
#[cfg(target_os = "macos")]
#[tauri::command]
async fn send_local_input(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, local_session::LocalSession>,
    request: u64,
    event: local_session::ControlInput,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    if window.label() != "viewer" {
        return Err("Only the local viewer can send QA input".into());
    }
    state.can_send_input(request)?;
    let fixture = window
        .app_handle()
        .get_webview_window("fixture")
        .ok_or("QA fixture is closed")?;
    fixture.set_focus().map_err(|e| e.to_string())?;
    // Native focus requests and CGEvent posts cross different event queues.
    // Wait for the target window, then leave it focused while posted events
    // drain. Returning focus to the viewer on submission races actual delivery.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(1);
    while !fixture.is_focused().map_err(|e| e.to_string())? {
        if std::time::Instant::now() >= deadline {
            return Err("QA fixture did not receive native focus".into());
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.send_input(request, event))
        .await
        .map_err(|e| e.to_string())?
}
#[cfg(target_os = "macos")]
#[tauri::command]
async fn close_local_session(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, local_session::LocalSession>,
    request: Option<u64>,
) -> Result<(), String> {
    session_window(&window)?;
    if window.label() == "viewer" && request.is_none() {
        return Err("The viewer must identify its own QA session".into());
    }
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || match request {
        Some(request) => state.stop_request(request),
        None => state.stop(),
    })
    .await
    .map_err(|e| e.to_string())?
}
#[cfg(target_os = "macos")]
#[tauri::command]
async fn next_local_video(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, local_session::LocalSession>,
    request: u64,
) -> Result<tauri::ipc::Response, String> {
    if window.label() != "viewer" {
        return Err("Video is restricted to the local viewer".into());
    }
    let state = state.inner().clone();
    let packet = tauri::async_runtime::spawn_blocking(move || state.next_video(request))
        .await
        .map_err(|e| e.to_string())??;
    Ok(tauri::ipc::Response::new(packet))
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn prove_device_identity(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, std::sync::Arc<device_identity::IdentityState>>,
    challenge: core_protocol::device_login::DeviceLoginChallenge,
) -> Result<Vec<u8>, String> {
    main_window(&window)?;
    state.prove(challenge)
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn open_device_identity(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, std::sync::Arc<device_identity::IdentityState>>,
    create: bool,
) -> Result<device_identity::PublicIdentity, String> {
    main_window(&window)?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.open(create))
        .await
        .map_err(|_| "Identity worker failed".to_string())?
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
) -> Result<account_auth::Status, String> {
    main_window(&window)?;
    let restore = state.inner().clone();
    tauri::async_runtime::spawn(async move { restore.restore().await });
    Ok(state.status().await)
}
#[derive(serde::Serialize)]
struct StartupSettings {
    remembered: bool,
    autostart: bool,
    launched_at_startup: bool,
    restored: bool,
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn startup_settings(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
) -> Result<StartupSettings, String> {
    main_window(&window)?;
    let autostart = tauri::async_runtime::spawn_blocking(autostart::enabled)
        .await
        .unwrap_or(false);
    Ok(StartupSettings {
        remembered: state
            .status()
            .await
            .account
            .is_some_and(|account| account.remembered),
        autostart,
        launched_at_startup: autostart::launched_at_startup(),
        restored: state.restored().await,
    })
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[derive(serde::Serialize)]
struct SignInScreen {
    supported: bool,
    outdated: bool,
    /// A technician is connected through the always-on host right now.
    technician: bool,
    connection_id: Option<u32>,
    name: Option<String>,
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn signin_screen_status(window: tauri::WebviewWindow) -> Result<SignInScreen, String> {
    main_window(&window)?;
    #[cfg(target_os = "windows")]
    {
        let status = windows_signin_host::public_status();
        let outdated = status.is_some()
            && tauri::async_runtime::spawn_blocking(windows_signin_host::program_outdated)
                .await
                .unwrap_or(false);
        Ok(SignInScreen {
            supported: true,
            outdated,
            technician: windows_signin_host::technician_connected(),
            connection_id: status.as_ref().map(|status| status.connection_id),
            name: status.map(|status| status.name),
        })
    }
    #[cfg(not(target_os = "windows"))]
    Ok(SignInScreen { supported: false, outdated: false, technician: false, connection_id: None, name: None })
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn signin_screen_enable(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    company: uuid::Uuid,
    name: String,
) -> Result<u32, String> {
    main_window(&window)?;
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (auth, company, name);
        Err("Sign-in screen access is available on Windows".into())
    }
    #[cfg(target_os = "windows")]
    {
        let cancelled = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let credential = auth.credential(Some(&cancelled)).await?;
        if !credential.principal.staff {
            return Err("Sign-in screen access requires an MFA-verified Mixel team account".into());
        }
        let name = name.trim().to_owned();
        if name.is_empty() || name.chars().count() > 60 || name.chars().any(char::is_control) {
            return Err("Enter a computer name of up to 60 characters".into());
        }
        // Step 1 (administrator): create the computer-wide key.
        tauri::async_runtime::spawn_blocking(|| windows_signin_host::run_elevated("--signin-provision"))
            .await
            .map_err(|_| "Administrator step failed")??;
        let key = windows_signin_host::provisioned_public_key()?;
        let ism = &credential.ism;
        let token = &credential.token;
        let device = match ism.enrollment_of_key(token, &key).await.map_err(|_| "The enrolment status is unavailable")? {
            Some(current) => current.device,
            None => {
                let device = ism
                    .add_device(token, company, &format!("{name} (sign-in screen)"), "windows")
                    .await
                    .map_err(|_| "The sign-in screen record could not be added for this company")?;
                ism.enroll(token, device, key, &cancelled)
                    .await
                    .map_err(|_| "The sign-in screen could not be enrolled")?;
                device
            }
        };
        let enrolled = ism.authenticate_device(token, device).await.map_err(|_| "The sign-in screen enrolment could not be confirmed")?;
        if enrolled.public_key != key {
            return Err("The sign-in screen enrolment does not match this computer".into());
        }
        let tenant = ism.device_tenant(token, device).await.map_err(|_| "The company of this computer is unavailable")?;
        let settings = windows_signin_host::Settings {
            version: 1,
            device,
            profile: enrolled.principal.user,
            tenant,
            connection_id: enrolled.connection_id,
            generation: enrolled.generation,
            control: true,
        };
        let payload = serde_json::to_vec(&serde_json::json!({ "settings": settings, "name": name }))
            .map_err(|_| "Cannot prepare the sign-in screen settings")?;
        let hex: String = payload.iter().map(|byte| format!("{byte:02x}")).collect();
        // Step 2 (administrator): save the settings and start the Windows service.
        tauri::async_runtime::spawn_blocking(move || windows_signin_host::run_elevated(&format!("--signin-install {hex}")))
            .await
            .map_err(|_| "Administrator step failed")??;
        Ok(enrolled.connection_id)
    }
}
/// Finds or checks the company's AI server (empty address: try this computer and the usual server).
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn ai_connect(window: tauri::WebviewWindow, address: String, model: String) -> Result<ai_assistant::Server, String> {
    main_window(&window)?;
    ai_assistant::connect(&address, &model).await
}
/// Asks the AI server; `image` is an optional base64 screenshot of the session the technician sees.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn ai_ask(
    window: tauri::WebviewWindow,
    address: String,
    model: String,
    system: String,
    prompt: String,
    image: Option<String>,
) -> Result<String, String> {
    main_window(&window)?;
    ai_assistant::ask(&address, &model, &system, &prompt, image.as_deref()).await
}
/// Team members: save a session note as an internal note on an ISM ticket.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn ticket_note(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    ticket: String,
    note: String,
    remote_id: Option<u32>,
) -> Result<String, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Saving in a ticket requires an MFA-verified Mixel team account".into());
    }
    let (number, title) = credential
        .ism
        .ticket_note(&credential.token, ticket.trim(), &note, remote_id)
        .await
        .map_err(|_| "The note was not saved: check the ticket number")?;
    Ok(format!("{number} · {title}"))
}
#[derive(serde::Serialize)]
struct RemotePrinting {
    supported: bool,
    installed: bool,
}
/// Remote printing on this computer: whether the "Mixel Remote Printer" exists (Windows).
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_printing_status(window: tauri::WebviewWindow) -> Result<RemotePrinting, String> {
    main_window(&window)?;
    let installed = tauri::async_runtime::spawn_blocking(host_print::installed)
        .await
        .unwrap_or(false);
    Ok(RemotePrinting { supported: cfg!(target_os = "windows"), installed })
}
/// Adds or removes the "Mixel Remote Printer" (one administrator prompt).
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_printing_set(window: tauri::WebviewWindow, enabled: bool) -> Result<(), String> {
    main_window(&window)?;
    #[cfg(target_os = "windows")]
    return tauri::async_runtime::spawn_blocking(move || {
        windows_signin_host::run_elevated(if enabled { "--printer-install" } else { "--printer-remove" })
    })
    .await
    .map_err(|_| "The printer could not be changed".to_owned())?;
    #[cfg(not(target_os = "windows"))]
    {
        let _ = enabled;
        Err("Remote printing is set up on Windows computers".into())
    }
}
/// A print job received in a session: print it on the default printer, or open it.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn print_job(window: tauri::WebviewWindow, path: String, open: bool) -> Result<String, String> {
    main_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        if open {
            host_print::open(&path).map(|()| "opened".to_owned())
        } else {
            host_print::print(&path).map(str::to_owned)
        }
    })
    .await
    .map_err(|_| "The print job could not be handled".to_owned())?
}
/// Sends a Wake-on-LAN signal from this computer for each address.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn wake_on_lan(window: tauri::WebviewWindow, macs: Vec<String>) -> Result<(), String> {
    main_window(&window)?;
    if macs.is_empty() || macs.len() > 4 {
        return Err("No network address is known for this computer yet".into());
    }
    tauri::async_runtime::spawn_blocking(move || macs.iter().try_for_each(|mac| host_actions::wake(mac)))
        .await
        .map_err(|_| "The wake signal could not be sent")?
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[derive(serde::Serialize)]
struct RolloutCreated {
    token: String,
    /// PowerShell line for deployment tools or an administrator prompt on each Windows computer.
    windows_command: String,
}
/// Team members: create a company rollout token and the command to run on each computer.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn rollout_create(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    company: uuid::Uuid,
    label: String,
    days: u16,
    computers: u16,
) -> Result<RolloutCreated, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Rollout requires an MFA-verified Mixel team account".into());
    }
    let token = credential
        .ism
        .create_rollout_token(&credential.token, company, &label, days, computers)
        .await
        .map_err(|_| "The rollout token could not be created")?;
    let windows_command = windows_rollout_command(&token).await?;
    Ok(RolloutCreated { token, windows_command })
}
/// The PowerShell line (run as administrator) that installs the latest Windows build and enrols the
/// computer with a rollout token or SaaS deploy link, with always-on access.
async fn windows_rollout_command(token: &str) -> Result<String, String> {
    if !token.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte)) {
        return Err("The rollout token could not be created".into());
    }
    let installer = self_update::latest_for("windows", "x64").await?;
    Ok(rollout_command_for(token, &self_update::file_url(&installer)))
}
fn rollout_command_for(token: &str, installer: &str) -> String {
    // Single quotes only, so the line also survives being wrapped in "…" by deployment tools.
    format!(
        "$t='{token}'; $f=Join-Path $env:TEMP 'Mixel-Remote-Setup.exe'; Invoke-WebRequest -UseBasicParsing '{installer}' -OutFile $f; Start-Process $f '/S' -Wait; $p=Start-Process (Join-Path $env:LOCALAPPDATA 'Mixel Remote\\mixel-remote.exe') -ArgumentList '--signin-enroll',$t -Wait -PassThru; exit $p.ExitCode"
    )
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn rollout_list(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    company: uuid::Uuid,
) -> Result<Vec<signaling_server::auth::RolloutToken>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential
        .ism
        .rollout_tokens(&credential.token, company)
        .await
        .map_err(|_| "Rollout tokens are unavailable".into())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn rollout_revoke(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    id: uuid::Uuid,
) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential
        .ism
        .revoke_rollout_token(&credential.token, id)
        .await
        .map_err(|_| "The rollout token could not be revoked".into())
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn signin_screen_update(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    #[cfg(target_os = "windows")]
    return tauri::async_runtime::spawn_blocking(|| windows_signin_host::run_elevated("--signin-update"))
        .await
        .map_err(|_| "Administrator step failed")?;
    #[cfg(not(target_os = "windows"))]
    Err("Sign-in screen access is available on Windows".into())
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn signin_screen_disable(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    #[cfg(target_os = "windows")]
    return tauri::async_runtime::spawn_blocking(|| windows_signin_host::run_elevated("--signin-remove"))
        .await
        .map_err(|_| "Administrator step failed")?;
    #[cfg(not(target_os = "windows"))]
    Err("Sign-in screen access is available on Windows".into())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn set_stay_signed_in(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    enabled: bool,
) -> Result<(), String> {
    main_window(&window)?;
    state.set_remember(enabled).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn set_autostart(window: tauri::WebviewWindow, enabled: bool) -> Result<(), String> {
    main_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || autostart::set(enabled))
        .await
        .map_err(|_| "Cannot change the startup setting")?
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_sign_in(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    show_link: Option<bool>,
) -> Result<Option<String>, String> {
    main_window(&window)?;
    #[cfg(feature = "qa-session")]
    if let Some(path) = std::env::var_os("MIXEL_QA_SESSION_FILE") {
        state.begin_qa(path).await?;
        return Ok(None);
    }
    let url = state.begin().await?;
    if show_link == Some(true) {
        return Ok(Some(url));
    }
    if let Err(error) = system_browser::open(&url).await {
        state.cancel().await?;
        return Err(error);
    }
    Ok(Some(url))
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn open_company_settings(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    company_id: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    let url = state.company_settings_url(company_id).await?;
    system_browser::open(&url).await
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_setup(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    name: String,
) -> Result<(), String> {
    main_window(&window)?;
    state.complete_personal_setup(name).await
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_workspace(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    remote: tauri::State<'_, remote_session::RemoteSession>,
    personal: bool,
) -> Result<(), String> {
    main_window(&window)?;
    if !matches!(remote.status().await.phase.as_str(), "offline" | "error") {
        return Err("Go offline before switching between company and personal devices".into());
    }
    state.set_workspace(personal).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_create_personal(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    remote: tauri::State<'_, remote_session::RemoteSession>,
    name: String,
) -> Result<(), String> {
    main_window(&window)?;
    if !matches!(remote.status().await.phase.as_str(), "offline" | "error") {
        return Err("Go offline before adding a personal space".into());
    }
    state.create_personal_space(name).await
}
fn version_parts(version: &str) -> Option<Vec<u64>> {
    let core = version.split(['-', '+']).next()?;
    let parts: Option<Vec<u64>> = core.split('.').map(|part| part.parse().ok()).collect();
    parts.filter(|parts| (1..=4).contains(&parts.len()))
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[derive(serde::Serialize)]
struct UpdateInfo {
    version: String,
    /// A signed installer is available for an in-app install.
    installable: bool,
    /// It may be installed now without asking: nothing is in use and the sign-in survives a restart.
    silent: bool,
    /// Installing asks for the administrator password (Linux).
    needs_password: bool,
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
fn newer_than_running(window: &tauri::WebviewWindow, version: &str) -> bool {
    let running = window.app_handle().package_info().version.to_string();
    matches!((version_parts(&running), version_parts(version)), (Some(current), Some(next)) if next > current)
}
/// Newer published version of this app, if any, and whether it can be installed in the app.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn update_check(
    window: tauri::WebviewWindow,
    remote: tauri::State<'_, remote_session::RemoteSession>,
    auth: tauri::State<'_, account_auth::AccountAuth>,
) -> Result<Option<UpdateInfo>, String> {
    main_window(&window)?;
    // Lab builds never update themselves unless a test channel is set on purpose.
    if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
        return Ok(None);
    }
    if let Ok(manifest) = self_update::latest().await {
        if !newer_than_running(&window, &manifest.version) {
            return Ok(None);
        }
        let idle = matches!(remote.status().await.phase.as_str(), "offline" | "error" | "ready");
        let account = auth.status().await;
        let restart_keeps_sign_in = account.phase != "authenticated"
            || account.account.as_ref().is_some_and(|account| account.remembered);
        return Ok(Some(UpdateInfo {
            version: manifest.version,
            installable: true,
            silent: idle && restart_keeps_sign_in && !self_update::NEEDS_PASSWORD,
            needs_password: self_update::NEEDS_PASSWORD,
        }));
    }
    // Fallback: the store listing (download only).
    #[derive(serde::Deserialize)]
    struct Row {
        version: String,
    }
    let (platform, variant) = if cfg!(target_os = "macos") {
        ("macos", "")
    } else if cfg!(target_os = "windows") {
        ("windows", "managed")
    } else {
        ("linux", "managed")
    };
    let url = format!("https://store-api.mixel.ch/rest/v1/store_app_releases?select=version&app_slug=eq.mixel-remote&is_current=eq.true&platform=eq.{platform}&variant=eq.{variant}&limit=5");
    let rows: Vec<Row> = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "Update check unavailable")?
        .get(url)
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "Update check unavailable")?
        .json()
        .await
        .map_err(|_| "Update check unavailable")?;
    Ok(rows
        .into_iter()
        .filter(|row| row.version.len() <= 40 && newer_than_running(&window, &row.version))
        .filter_map(|row| version_parts(&row.version).map(|parts| (parts, row.version)))
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, version)| UpdateInfo { version, installable: false, silent: false, needs_password: false }))
}
/// Downloads, verifies and installs the newest signed version, then restarts the app.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn update_install(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
        return Err("Lab builds do not update themselves".into());
    }
    let manifest = self_update::latest().await?;
    if !newer_than_running(&window, &manifest.version) {
        return Err("This is already the newest version".into());
    }
    self_update::download_and_install(manifest).await?;
    let app = window.app_handle().clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
        app.exit(0);
    });
    Ok(())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn open_update_page(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    system_browser::open("https://store.mixel.ch/app/mixel-remote").await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn open_remote_management(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    system_browser::open("https://admin.mixel.ch/remote").await
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn open_mixel_registration(window: tauri::WebviewWindow) -> Result<(), String> {
    main_window(&window)?;
    let config = service_config::ServiceConfig::load()?;
    if !config.customer_signup {
        return Err("Public Mixel ID registration is unavailable on this service".into());
    }
    system_browser::open("https://auth.mixel.ch/if/flow/mixel-remote-enrollment/").await
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_signup_available(window: tauri::WebviewWindow) -> Result<bool, String> {
    main_window(&window)?;
    Ok(service_config::ServiceConfig::load()?.customer_signup)
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_verify(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
    factor: uuid::Uuid,
    code: String,
) -> Result<(), String> {
    main_window(&window)?;
    state
        .verify_totp(factor, zeroize::Zeroizing::new(code))
        .await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn account_cancel(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, account_auth::AccountAuth>,
) -> Result<(), String> {
    main_window(&window)?;
    state.cancel().await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
) -> Result<remote_session::Status, String> {
    main_window(&window)?;
    Ok(state.status().await)
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn copy_connection_id(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    let id = state
        .status()
        .await
        .host
        .ok_or("Make this computer available before copying its connection ID")?;
    session_platform::write_text(&id.to_string())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_devices(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    after: Option<u32>,
    search: String,
) -> Result<signaling_server::auth::DevicePage, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    let page = credential
        .ism
        .directory_scoped(&credential.token, credential.principal.is_personal(), after, &search)
        .await
        .map_err(|_| "The enrolled device directory is unavailable for this account")?;
    let fresh = auth.credential(None).await?;
    if fresh.principal.user != credential.principal.user
        || fresh.principal.tenant != credential.principal.tenant
        || fresh.principal.staff != credential.principal.staff
    {
        return Err("The signed-in account changed; reload the device directory".into());
    }
    Ok(page)
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn enrollment_devices(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    after: Option<uuid::Uuid>,
    search: String,
) -> Result<signaling_server::auth::InventoryPage, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Device enrollment requires an MFA-verified Mixel team account".into());
    }
    let page = credential
        .ism
        .enrollment_devices(&credential.token, after, &search)
        .await
        .map_err(|_| "Device enrollment records are unavailable for this account")?;
    let fresh = auth.credential(None).await?;
    if fresh.principal.user != credential.principal.user || !fresh.principal.staff {
        return Err("The enrollment account changed".into());
    }
    Ok(page)
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn enrollment_companies(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
) -> Result<Vec<signaling_server::auth::EnrollmentCompany>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Adding a computer requires an MFA-verified Mixel team account".into());
    }
    credential
        .ism
        .enrollment_companies(&credential.token)
        .await
        .map_err(|_| "Companies are unavailable for this account".into())
}
/// Quick Support for phones: before connecting to an ID, claim it if it is a phone. `false` for computers
/// and for accounts that are not Mixel team accounts (the ordinary request then decides).
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn quick_phone_claim(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    host: u32,
) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Ok(false);
    }
    credential
        .ism
        .quick_phone_claim(&credential.token, host)
        .await
        .map_err(|_| "The phone could not be reached through Mixel. Please try again.".into())
}
/// Mixel Meet for a session: a room (to show this screen to the other side, or to invite a colleague).
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn meeting_create(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
) -> Result<serde_json::Value, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    let response = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "Mixel Meet is unavailable")?
        .post("https://rs.mixel.ch/meet/api/rooms")
        .bearer_auth(credential.token.as_str())
        .json(&serde_json::json!({ "title": "Mixel Remote session" }))
        .send()
        .await
        .map_err(|_| "Mixel Meet cannot be reached")?;
    if response.status().as_u16() == 429 {
        return Err("You have started many meetings. Please try again later.".into());
    }
    let room: serde_json::Value = response.error_for_status().map_err(|_| "The meeting could not be started")?.json().await.map_err(|_| "The meeting could not be started")?;
    let id = room["room"].as_str().unwrap_or_default();
    let host = room["host"].as_str().unwrap_or_default();
    if !session_guard::meeting_room(id) || !host.starts_with("https://rs.mixel.ch/meet/#") {
        return Err("The meeting could not be started".into());
    }
    Ok(serde_json::json!({ "room": id, "invite": format!("https://rs.mixel.ch/meet/#{id}"), "host": host }))
}
/// Opens a Mixel Meet room (only rs.mixel.ch/meet) in the browser.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn meeting_open(window: tauri::WebviewWindow, link: String) -> Result<(), String> {
    main_window(&window)?;
    let room = link.strip_prefix("https://rs.mixel.ch/meet/#").unwrap_or_default();
    let id = room.split('.').next().unwrap_or_default();
    let key_ok = room.split('.').nth(1).is_none_or(|key| key.len() == 32 && key.chars().all(|c| c.is_ascii_hexdigit()));
    if !session_guard::meeting_room(id) || room.split('.').count() > 2 || !key_ok {
        return Err("This is not a Mixel Meet link".into());
    }
    system_browser::open(&link).await
}
/// This computer's name as the system shows it, to suggest a name for its one-time setup.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn this_computer_name(window: tauri::WebviewWindow) -> Result<String, String> {
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    let name = std::process::Command::new("/usr/sbin/scutil").arg("--get").arg("ComputerName").output().ok()
        .filter(|output| output.status.success()).map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned());
    #[cfg(target_os = "windows")]
    let name = std::env::var("COMPUTERNAME").ok();
    #[cfg(target_os = "linux")]
    let name = std::fs::read_to_string("/etc/hostname").ok().map(|text| text.trim().to_owned());
    let name: String = name.unwrap_or_default().chars().filter(|c| !c.is_control()).take(80).collect();
    Ok(if name.trim().is_empty() { "This computer".into() } else { name.trim().to_owned() })
}
/// Quick Support for phones: the technician creates a one-time code for the person to type
/// into the Mixel Quick Support app on their phone.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn support_code_create(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    company: uuid::Uuid,
) -> Result<String, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Support codes require an MFA-verified Mixel team account".into());
    }
    credential
        .ism
        .create_support_code(&credential.token, company)
        .await
        .map_err(|_| "The support code could not be created. Please try again.".into())
}
/// The phone that registered with a support code created here, once it has.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn support_code_device(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    code: String,
) -> Result<Option<signaling_server::auth::SupportDevice>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Support codes require an MFA-verified Mixel team account".into());
    }
    credential
        .ism
        .support_code_device(&credential.token, &code)
        .await
        .map_err(|_| "The support code could not be checked. Please try again.".into())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn add_this_device(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    company: uuid::Uuid,
    name: String,
) -> Result<uuid::Uuid, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Err("Adding a computer requires an MFA-verified Mixel team account".into());
    }
    let os = if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    };
    credential
        .ism
        .add_device(&credential.token, company, &name, os)
        .await
        .map_err(|_| "This computer could not be added. A computer with this name may already be enrolled for the company; choose another name or pick it from the list.".into())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn enrollment_current(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    identity: tauri::State<'_, Arc<device_identity::IdentityState>>,
) -> Result<Option<signaling_server::auth::CurrentEnrollment>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    if !credential.principal.staff {
        return Ok(None);
    }
    let saved = identity.inner().clone();
    if tauri::async_runtime::spawn_blocking(move || saved.open(false))
        .await
        .map_err(|_| "Device identity storage is unavailable")?
        .is_err()
    {
        return Ok(None);
    }
    credential
        .ism
        .enrollment_of_key(&credential.token, &identity.public_key()?)
        .await
        .map_err(|_| "This computer’s enrollment status is unavailable".into())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn enroll_this_device(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    identity: tauri::State<'_, Arc<device_identity::IdentityState>>,
    remote: tauri::State<'_, remote_session::RemoteSession>,
    device: uuid::Uuid,
    confirmed: bool,
) -> Result<u32, String> {
    main_window(&window)?;
    if !confirmed || device.is_nil() {
        return Err("Confirm the existing ISM record for this computer before enrollment".into());
    }
    if !matches!(remote.status().await.phase.as_str(), "offline" | "error") {
        return Err("Go offline before enrolling this computer".into());
    }
    let cancelled = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let credential = auth.credential(Some(&cancelled)).await?;
    if !credential.principal.staff {
        return Err("Device enrollment requires an MFA-verified Mixel team account".into());
    }
    let saved = identity.inner().clone();
    tauri::async_runtime::spawn_blocking(move || saved.open(true))
        .await
        .map_err(|_| "Device identity storage is unavailable")??;
    let enrolled=credential.ism.enroll(&credential.token,device,identity.public_key()?,&cancelled).await.map_err(|_|"Enrollment could not be confirmed. Existing keys are never replaced; check the selected device record and refresh its enrollment status.")?;
    let fresh = auth.credential(None).await?;
    if fresh.principal.user != credential.principal.user || !fresh.principal.staff {
        return Err("The account changed; refresh this computer's enrollment status".into());
    }
    Ok(enrolled.connection_id)
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn register_personal_device(
    window: tauri::WebviewWindow,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    name: String,
) -> Result<u32, String> {
    main_window(&window)?;
    if !matches!(
        window
            .state::<remote_session::RemoteSession>()
            .status()
            .await
            .phase
            .as_str(),
        "offline" | "error"
    ) {
        return Err("Go offline before registering this device".into());
    }
    let credential = auth.credential(None).await?;
    if !credential.principal.is_personal() {
        return Err("Set up your Remote account first".into());
    }
    let profile = credential.principal.user;
    let identity = tauri::async_runtime::spawn_blocking(move || {
        device_identity::IdentityState::personal(profile, true)
    })
    .await
    .map_err(|_| "Device identity storage is unavailable")??;
    use signaling_server::auth::PersonalDeviceOs;
    #[cfg(target_os = "macos")]
    let os = PersonalDeviceOs::Macos;
    #[cfg(target_os = "windows")]
    let os = PersonalDeviceOs::Windows;
    #[cfg(target_os = "linux")]
    let os = PersonalDeviceOs::Linux;
    let id = credential
        .ism
        .register_personal_device(&credential.token, &name, os, &identity.public_key()?)
        .await
        .map_err(
            |_| "Could not register this device. Check the device name and account, then try again.",
        )?;
    if auth.credential(None).await?.principal.user != profile {
        return Err("The signed-in account changed".into());
    }
    Ok(id)
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_register(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    auth: tauri::State<'_, account_auth::AccountAuth>,
    identity: tauri::State<'_, Arc<device_identity::IdentityState>>,
) -> Result<(), String> {
    main_window(&window)?;
    if saas_commands::removal_pending(&window) {
        return Err("This computer is being removed from Mixel Remote. It is removed as soon as it is online.".into());
    }
    let principal = auth.credential(None).await?.principal;
    let saved = identity.inner().clone();
    let identity = tauri::async_runtime::spawn_blocking(move || {
        if principal.is_personal() {
            return device_identity::IdentityState::personal(principal.user, false).map(Arc::new);
        }
        saved.open(false)?;
        Ok(saved)
    })
    .await
    .map_err(|_| "Saved identity could not be loaded")??;
    state.connect(auth.inner().clone(), identity).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn request_screen_access(window: tauri::WebviewWindow) -> Result<bool, String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    { tauri::async_runtime::spawn_blocking(native_os_capture::macos::request_screen_access)
        .await
        .map_err(|_| "Screen-recording permission request failed".into()) }
    #[cfg(not(target_os = "macos"))]
    { Ok(session_platform::capture_access()) }
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_disconnect(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
) -> Result<(), String> {
    main_window(&window)?;
    state.stop().await
}
#[cfg(target_os = "macos")]
#[tauri::command]
async fn connect_support_id(window: tauri::WebviewWindow, id: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    tauri::async_runtime::spawn_blocking(move || macos_support::connect(&id))
        .await
        .map_err(|_| "The Support connection could not start")?
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_request(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    host: u32,
    password: Option<String>,
    unattended: Option<bool>,
) -> Result<(), String> {
    main_window(&window)?;
    state
        .request(host, password, unattended.unwrap_or_default())
        .await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_unattended(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    source: Option<String>,
    control: bool,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    if source.as_deref() == Some("qa-relay-fixture")
        && !control
        && service_config::relay_fixture_enabled()
    {
        ensure_fixture(&window)?;
    }
    state.configure_unattended(source, control).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_presence(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    hosts: Vec<u32>,
) -> Result<(), String> {
    main_window(&window)?;
    state.presence(hosts).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_pairing(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    confirmation: Option<uuid::Uuid>,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    state.pairing(confirmation).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_support_code(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    required: bool,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    state.support_code(required).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_decide(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    decision: remote_session::Decision,
) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    service_config::require_host()?;
    main_window(&window)?;
    #[cfg(target_os = "macos")]
    if decision.accept
        && decision.source == "qa-relay-fixture"
        && service_config::relay_fixture_enabled()
    {
        ensure_fixture(&window)?;
    }
    state.decide(decision).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_input(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
    event: remote_session::InputEvent,
) -> Result<bool, String> {
    main_window(&window)?;
    state.input(session, event).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_clipboard(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
    push: bool,
) -> Result<(), String> {
    main_window(&window)?;
    state.clipboard(session, push).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_chat_messages(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
) -> Result<Option<Vec<remote_chat::ChatMessage>>, String> {
    main_window(&window)?;
    state.require_chat_session(session).await?;
    Ok(state.chat_hub().messages(session))
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_chat_send(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
    text: String,
) -> Result<(), String> {
    main_window(&window)?;
    state.require_chat_session(session).await?;
    state.chat_hub().send(session, text).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_file_status(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    consent: uuid::Uuid,
) -> Result<remote_files::View, String> {
    main_window(&window)?;
    state.file_scope(consent).await?.view()
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_file_send(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    consent: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    let scope = state.file_scope(consent).await?;
    scope.can_offer()?;
    #[cfg(target_os = "macos")]
    let path = macos_picker::select(app, scope.clone(), None).await?;
    #[cfg(not(target_os = "macos"))]
    let path = {
        let _ = app;
        let _permit = NATIVE_PICKER.try_acquire().map_err(|_| "Finish the current file selection first")?;
        if !scope.picker_live(None) {
            return Err("File transfer permission expired before selection".into());
        }
        rfd::AsyncFileDialog::new().set_parent(&window)
            .set_title("Choose a file to send")
            .pick_file().await.map(|file| file.path().to_owned())
    };
    if let Some(path) = path {
        scope.can_offer()?;
        scope.offer(path).await?;
    }
    Ok(())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_file_accept(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    consent: uuid::Uuid,
    id: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    let scope = state.file_scope(consent).await?;
    let name = scope.pending(id)?;
    #[cfg(target_os = "macos")]
    let path = {
        let _ = name;
        macos_picker::select(app, scope.clone(), Some(id)).await?
    };
    #[cfg(not(target_os = "macos"))]
    let path = {
        let _ = app;
        let _permit = NATIVE_PICKER.try_acquire().map_err(|_| "Finish the current file selection first")?;
        if !scope.picker_live(Some(id)) {
            return Err("File transfer permission expired before selection".into());
        }
        rfd::AsyncFileDialog::new().set_parent(&window)
            .set_title("Choose where to save the received file")
            .set_file_name(name)
            .save_file().await.map(|file| file.path().to_owned())
    };
    if let Some(path) = path {
        scope.pending(id)?;
        scope.accept(id, path).await?;
    }
    Ok(())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_file_cancel(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    consent: uuid::Uuid,
    id: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    state.file_scope(consent).await?.cancel(id)
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
static NATIVE_PICKER: tokio::sync::Semaphore = tokio::sync::Semaphore::const_new(1);

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_record_start(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    let status = state.status().await;
    if status.session != Some(session)
        || status.phase != "active"
        || status.sharing
        || status.received == 0
    {
        return Err("Start a permitted recording after the remote screen appears".into());
    }
    let hub = state.recording_hub();
    #[cfg(target_os = "macos")]
    let watched = hub.clone();
    #[cfg(target_os = "macos")]
    let path = macos_picker::select_scoped(
        app,
        move || watched.may_start(session),
        || {
            use native_os_capture::macos_file_picker::FilePicker;
            if service_config::controller_only() {
                let token = uuid::Uuid::new_v4().simple().to_string();
                FilePicker::record_into_folder(&format!(
                    "Mixel Remote recording {}.mp4",
                    &token[..12]
                ))
            } else {
                FilePicker::save_recording()
            }
        },
    )
    .await?;
    #[cfg(not(target_os = "macos"))]
    let path = {
        let _ = app;
        if !hub.may_start(session) {
            return Err("Recording permission expired".into());
        }
        let _permit = NATIVE_PICKER.try_acquire().map_err(|_| "Finish the current file selection first")?;
        rfd::AsyncFileDialog::new()
            .set_parent(&window)
            .set_title("Save Mixel Remote recording")
            .set_file_name("Mixel Remote recording.mp4")
            .add_filter("MP4 video", &["mp4"])
            .save_file().await.map(|file| file.path().to_owned())
    };
    if let Some(path) = path {
        hub.start(session, path)?;
    }
    Ok(())
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_record_stop(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    state.recording_hub().stop(session)
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_close(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    state.close(session).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_stop_session(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    consent: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    state.stop_session(consent).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_restart(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
) -> Result<(), String> {
    main_window(&window)?;
    state.restart(session).await
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_next_video(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: uuid::Uuid,
    compressed: Option<bool>,
) -> Result<tauri::ipc::Response, String> {
    main_window(&window)?;
    let packet = state.next_video(session, compressed.unwrap_or(false)).await?;
    #[cfg(feature = "qa-session")]
    if !packet.is_empty() {
        remote_video::QA_FETCHED.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    Ok(tauri::ipc::Response::new(packet))
}
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
async fn remote_view_fullscreen(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, remote_session::RemoteSession>,
    session: Option<uuid::Uuid>,
) -> Result<(), String> {
    main_window(&window)?;
    if let Some(session) = session {
        let status = state.status().await;
        if status.phase != "active" || status.sharing || status.session != Some(session) {
            return Err("The remote screen is no longer available".into());
        }
        #[cfg(target_os = "macos")]
        if window.is_fullscreen().map_err(|err| err.to_string())? {
            return Err("Leave macOS full screen before using remote full screen".into());
        }
    }
    #[cfg(target_os = "macos")]
    { window.set_simple_fullscreen(session.is_some()).map_err(|err| err.to_string()) }
    #[cfg(not(target_os = "macos"))]
    { window.set_fullscreen(session.is_some()).map_err(|err| err.to_string()) }
}
#[cfg(target_os = "macos")]
#[derive(Default)]
struct QuitState(std::sync::atomic::AtomicU8);
#[cfg(target_os = "macos")]
fn request_quit(app: tauri::AppHandle) {
    use std::sync::atomic::Ordering;
    if app
        .state::<QuitState>()
        .0
        .compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return;
    }
    let remote = app.state::<remote_session::RemoteSession>().inner().clone();
    let auth = app.state::<account_auth::AccountAuth>().inner().clone();
    let local = app.state::<local_session::LocalSession>().inner().clone();
    let capture = app.state::<CaptureState>().inner().clone();
    tauri::async_runtime::spawn(async move {
        let stopped = remote.stop().await;
        let local_stopped = tokio::time::timeout(
            std::time::Duration::from_secs(30),
            tauri::async_runtime::spawn_blocking(move || {
                let local_result = local.stop();
                let capture_result = capture
                    .worker
                    .lock()
                    .map_err(|_| "Capture state failed".to_owned())
                    .and_then(|mut worker| match worker.take() {
                        Some(mut worker) => worker.stop(),
                        None => Ok(()),
                    });
                local_result.and(capture_result)
            }),
        )
        .await
        .map_err(|_| "Local media cleanup timed out".to_owned())
        .and_then(|value| value.map_err(|_| "Local media cleanup failed".to_owned()))
        .and_then(|value| value);
        let signed_out = auth.cancel().await;
        if let Err(error) = stopped.and(local_stopped).and(signed_out) {
            remote
                .report_error(format!(
                    "Quit postponed: {error}. Close the app again to retry cleanup."
                ))
                .await;
            app.state::<QuitState>().0.store(0, Ordering::Release);
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        } else {
            app.state::<QuitState>().0.store(2, Ordering::Release);
            app.exit(0);
        }
    });
}
fn main() {
    #[cfg(target_os = "windows")]
    {
        if let Some(result) = std::env::args().nth(1).and_then(|command| host_print::admin(&command)) {
            if let Err(error) = result {
                eprintln!("{error}");
                std::process::exit(2);
            }
            return;
        }
        let mut args = std::env::args().skip(1);
        if let Some(command) = args.next().filter(|arg| arg.starts_with("--signin-")) {
            let result = match command.as_str() {
                "--signin-service" => windows_signin_host::run_service(),
                "--signin-host" => service_config::require_host().and_then(|()| windows_signin_host::run_host()),
                _ => windows_signin_host::admin(&command, args.next().as_deref()),
            };
            if let Err(error) = result {
                eprintln!("{error}");
                std::process::exit(2);
            }
            return;
        }
    }
    #[cfg(target_os = "macos")]
    if std::env::args_os()
        .nth(1)
        .and_then(|arg| arg.into_string().ok())
        .is_some_and(|arg| {
            matches!(
                arg.as_str(),
                "--startup-host"
                    | "--prelogin-host"
                    | "--provision-prelogin-identity"
                    | "--validate-prelogin-settings"
            )
        })
    {
        if let Err(error) = service_config::require_host().and_then(|()| macos_startup_host::run())
        {
            eprintln!("{error}");
            std::process::exit(2);
        }
        return;
    }
    #[cfg(target_os = "macos")]
    if std::env::args_os().nth(1).as_deref()
        == Some(std::ffi::OsStr::new("--validate-identity-scope"))
    {
        // Reads bundle metadata only: no Keychain, browser, socket or webview.
        match device_identity::validated_bundle_scope() {
            Ok(scope) => println!("PASS: {scope}"),
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(2);
            }
        }
        return;
    }
    // Packaging validates the exact public resource bytes using the same
    // parser and Ed25519 point checks as runtime, before signing. This mode
    // creates no webview, account, keychain access, connection or OS prompt.
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    if std::env::args_os().nth(1).as_deref()
        == Some(std::ffi::OsStr::new("--validate-embedded-service"))
    {
        match service_config::ServiceConfig::load() {
            Ok(_) => println!("PASS: embedded public service configuration validated"),
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(2);
            }
        }
        return;
    }
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    if std::env::args_os().nth(1).as_deref()
        == Some(std::ffi::OsStr::new("--validate-public-config"))
    {
        use std::io::Read;
        let mut bytes = Vec::new();
        let result = std::io::stdin()
            .take(32769)
            .read_to_end(&mut bytes)
            .map_err(|_| "Cannot read public service configuration".to_owned())
            .and_then(|_| service_config::ServiceConfig::validate_public_bytes(&bytes));
        match result {
            Ok(()) => println!("PASS: public service configuration and authority keys validated"),
            Err(error) => {
                eprintln!("{error}");
                std::process::exit(2);
            }
        }
        return;
    }
    let builder = tauri::Builder::default().manage(CaptureState::default());
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    let builder = builder.manage(account_auth::AccountAuth::default());
    #[cfg(target_os = "macos")]
    let builder = builder
        .manage(local_session::LocalSession::default())
        .manage(remote_session::RemoteSession::default())
        .manage(QuitState::default())
        .setup(|app| {
            macos_tray::install(app.handle())?;
            if native_os_capture::macos_bundle::identifier().as_deref()
                == Some("ch.mixel.remote.next.qa.controller")
                && let Some(window) = app.get_webview_window("main")
            {
                window.set_title("Mixel-Remote — Test controller")?;
            }
            Ok(())
        })
        .manage(std::sync::Arc::new(
            device_identity::IdentityState::default(),
        ))
        .invoke_handler(tauri::generate_handler![
            capture_targets,
            start_capture,
            stop_capture,
            next_frame,
            open_local_viewer,
            request_input_access,
            account_status,
            account_sign_in,
            open_company_settings,
            account_verify,
            account_setup,
            account_workspace,
            account_create_personal,
            open_remote_management,
            open_mixel_registration,
            remote_signup_available,
            register_personal_device,
            account_cancel,
            remote_status,
            copy_connection_id,
            remote_devices,
            enrollment_devices,
            enrollment_companies,
            saas_commands::saas_my_tenants,
            saas_commands::saas_signup,
            saas_commands::saas_members,
            saas_commands::saas_invite_member,
            saas_commands::saas_invites,
            saas_commands::saas_revoke_invite,
            saas_commands::saas_set_member_role,
            saas_commands::saas_remove_member,
            saas_commands::saas_tenant_overview,
            saas_commands::saas_devices,
            saas_commands::saas_rename_device,
            saas_commands::saas_move_device,
            saas_commands::saas_remove_device,
            saas_commands::saas_add_this_device,
            saas_commands::saas_groups,
            saas_commands::saas_create_group,
            saas_commands::saas_rename_group,
            saas_commands::saas_delete_group,
            saas_commands::saas_set_group_access,
            saas_commands::saas_create_deploy_link,
            saas_commands::saas_deploy_links,
            saas_commands::saas_revoke_deploy_link,
            saas_commands::device_self_remove,
            saas_commands::device_self_remove_flush,
            support_code_create,
            this_computer_name,
            meeting_create,
            meeting_open,
            quick_phone_claim,
            support_code_device,
            add_this_device,
            enrollment_current,
            enroll_this_device,
            remote_register,
            remote_disconnect,
            remote_request,
            connect_support_id,
            remote_support_code,
            remote_pairing,
            remote_presence,
            update_check,
            update_install,
            wake_on_lan,
            ai_connect,
            ai_ask,
            ticket_note,
            remote_printing_status,
            remote_printing_set,
            print_job,
            rollout_create,
            rollout_list,
            rollout_revoke,
            startup_settings,
            set_stay_signed_in,
            set_autostart,
            open_update_page,
            remote_unattended,
            remote_decide,
            remote_input,
            remote_clipboard,
            remote_chat_messages,
            remote_chat_send,
            remote_file_status,
            remote_file_send,
            remote_file_accept,
            remote_file_cancel,
            remote_record_start,
            remote_record_stop,
            request_screen_access,
            remote_close,
            remote_stop_session,
            remote_restart,
            remote_next_video,
            remote_view_fullscreen,
            local_session_status,
            request_local_session,
            decide_local_session,
            close_local_session,
            next_local_video,
            send_local_input,
            open_device_identity,
            prove_device_identity
        ]);
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    let builder = builder
        .manage(remote_session::RemoteSession::default())
        .manage(std::sync::Arc::new(
            device_identity::IdentityState::default(),
        ))
        .invoke_handler(tauri::generate_handler![
            capture_targets,
            start_capture,
            stop_capture,
            next_frame,
            open_device_identity,
            prove_device_identity,
            account_status,
            account_sign_in,
            open_company_settings,
            account_setup,
            account_workspace,
            account_create_personal,
            open_remote_management,
            open_mixel_registration,
            remote_signup_available,
            account_verify,
            account_cancel,
            remote_devices,
            enrollment_devices,
            enrollment_companies,
            saas_commands::saas_my_tenants,
            saas_commands::saas_signup,
            saas_commands::saas_members,
            saas_commands::saas_invite_member,
            saas_commands::saas_invites,
            saas_commands::saas_revoke_invite,
            saas_commands::saas_set_member_role,
            saas_commands::saas_remove_member,
            saas_commands::saas_tenant_overview,
            saas_commands::saas_devices,
            saas_commands::saas_rename_device,
            saas_commands::saas_move_device,
            saas_commands::saas_remove_device,
            saas_commands::saas_add_this_device,
            saas_commands::saas_groups,
            saas_commands::saas_create_group,
            saas_commands::saas_rename_group,
            saas_commands::saas_delete_group,
            saas_commands::saas_set_group_access,
            saas_commands::saas_create_deploy_link,
            saas_commands::saas_deploy_links,
            saas_commands::saas_revoke_deploy_link,
            saas_commands::device_self_remove,
            saas_commands::device_self_remove_flush,
            support_code_create,
            this_computer_name,
            meeting_create,
            meeting_open,
            quick_phone_claim,
            support_code_device,
            add_this_device,
            enrollment_current,
            register_personal_device,
            enroll_this_device,
            remote_status,
            copy_connection_id,
            remote_register,
            remote_disconnect,
            remote_request,
            remote_support_code,
            remote_pairing,
            remote_presence,
            update_check,
            update_install,
            wake_on_lan,
            ai_connect,
            ai_ask,
            ticket_note,
            remote_printing_status,
            remote_printing_set,
            print_job,
            rollout_create,
            rollout_list,
            rollout_revoke,
            startup_settings,
            signin_screen_status,
            signin_screen_enable,
            signin_screen_disable,
            signin_screen_update,
            set_stay_signed_in,
            set_autostart,
            open_update_page,
            remote_unattended,
            remote_decide,
            remote_input,
            remote_clipboard,
            remote_chat_messages,
            remote_chat_send,
            remote_file_status,
            remote_file_send,
            remote_file_accept,
            remote_file_cancel,
            remote_record_start,
            remote_record_stop,
            request_screen_access,
            request_input_access,
            remote_close,
            remote_stop_session,
            remote_restart,
            remote_next_video,
            remote_view_fullscreen
        ]);
    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        start_capture,
        stop_capture,
        next_frame
    ]);
    let app = builder
        .on_window_event(|window, event| {
            #[cfg(target_os = "macos")]
            if window.label() == "main"
                && let tauri::WindowEvent::CloseRequested { api, .. } = event
                && window
                    .state::<QuitState>()
                    .0
                    .load(std::sync::atomic::Ordering::Acquire)
                    != 2
            {
                api.prevent_close();
                let _ = window.hide();
            }
            #[cfg(target_os = "macos")]
            if matches!(window.label(), "main" | "viewer" | "fixture")
                && matches!(event, tauri::WindowEvent::Destroyed)
            {
                let _ = window.state::<local_session::LocalSession>().stop();
            }
            if window.label() == "fixture" && matches!(event, tauri::WindowEvent::Destroyed) {
                #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
                if let Ok(mut worker) = window.state::<CaptureState>().worker.lock()
                    && let Some(mut worker) = worker.take()
                {
                    let _ = worker.stop();
                }
            }
            if window.label() == "main" && matches!(event, tauri::WindowEvent::Destroyed) {
                #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
                if let Ok(mut worker) = window.state::<CaptureState>().worker.lock()
                    && let Some(mut worker) = worker.take()
                {
                    let _ = worker.stop();
                }
                #[cfg(target_os = "macos")]
                request_quit(window.app_handle().clone());
                #[cfg(not(target_os = "macos"))]
                {
                    let remote = window.state::<remote_session::RemoteSession>().inner().clone();
                    let app = window.app_handle().clone();
                    tauri::async_runtime::spawn(async move {
                        let _ = remote.stop().await;
                        session_platform::close_clipboard();
                        session_platform::clear_clipboard_files();
                        host_print::clear_received();
                        app.exit(0);
                    });
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Mixel Remote desktop runtime failed");
    app.run(|app, event| {
        #[cfg(target_os = "macos")]
        if let tauri::RunEvent::ExitRequested { api, .. } = event
            && app
                .state::<QuitState>()
                .0
                .load(std::sync::atomic::Ordering::Acquire)
                != 2
        {
            api.prevent_exit();
            request_quit(app.clone());
        }
        #[cfg(not(target_os = "macos"))]
        let _ = (app, event);
    });
}

#[cfg(test)]
mod update_version_tests {
    #[test]
    fn compares_release_versions_numerically() {
        assert!(super::version_parts("0.1.10") > super::version_parts("0.1.9"));
        assert_eq!(super::version_parts("1.4.6-support.elevate.1"), Some(vec![1, 4, 6]));
        assert_eq!(super::version_parts("abc"), None);
    }
}

#[cfg(test)]
mod rollout_command_tests {
    #[test]
    fn deploy_command_carries_the_token_in_single_quotes_only() {
        let line = super::rollout_command_for("mrt_ab-c_D1", "https://download.mixel.ch/x.exe");
        assert!(line.starts_with("$t='mrt_ab-c_D1'; "));
        assert!(line.contains("'https://download.mixel.ch/x.exe'"));
        assert!(line.contains("'--signin-enroll',$t"));
        assert!(!line.contains('"'));
    }
}
