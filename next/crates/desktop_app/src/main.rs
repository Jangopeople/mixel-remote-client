#[cfg(target_os = "macos")]
mod local_session;
use native_os_capture::stream::FrameMailbox;
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
    fn start() -> Result<Self, String> {
        let (stop, rx) = std::sync::mpsc::sync_channel(1);
        let (ready, wait) = std::sync::mpsc::sync_channel(1);
        let thread = std::thread::Builder::new()
            .name("mixel-capture-owner".into())
            .spawn(move || {
                let result = (|| {
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
                let _ = thread.join();
                Err("Capture startup timed out".into())
            }
        }
    }
    fn stop(&mut self) -> Result<(), String> {
        let _ = self.stop.try_send(());
        self.thread
            .take()
            .map(|thread| {
                thread
                    .join()
                    .map_err(|_| "Capture owner thread failed".to_owned())?
            })
            .unwrap_or(Ok(()))
    }
}
#[cfg(target_os = "macos")]
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
#[derive(Clone, Default)]
struct CaptureState {
    #[cfg(target_os = "macos")]
    worker: Arc<Mutex<Option<Worker>>>,
}
fn main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Capture commands are restricted to the local main window".into());
    }
    Ok(())
}
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
) -> Result<String, String> {
    main_window(&window)?;
    ensure_fixture(&window)?;
    #[cfg(target_os = "macos")]
    {
        let state = state.inner().clone();
        tauri::async_runtime::spawn_blocking(move || {
            let mut worker = state.worker.lock().map_err(|_| "Capture state failed")?;
            if worker.is_some() {
                return Err("Capture is already running".into());
            }
            *worker = Some(Worker::start()?);
            Ok("ScreenCaptureKit is capturing this app's fixture window only".into())
        })
        .await
        .map_err(|e| e.to_string())?
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = state;
        Err("This native capture QA currently runs on macOS".into())
    }
}
#[tauri::command]
async fn stop_capture(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, CaptureState>,
) -> Result<String, String> {
    main_window(&window)?;
    #[cfg(target_os = "macos")]
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
    #[cfg(not(target_os = "macos"))]
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
    #[cfg(target_os = "macos")]
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
            let (sequence, frame) = frames
                .next(after, std::time::Duration::from_secs(3))
                .map_err(|e| e.to_string())?;
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
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (state, after);
        Err("Native capture is unavailable on this target".into())
    }
}
#[cfg(target_os = "macos")]
#[tauri::command]
fn request_input_access(window: tauri::WebviewWindow) -> Result<bool, String> {
    main_window(&window)?;
    Ok(native_os_capture::macos_input::MacInjector::request_post_access())
}
#[cfg(target_os = "macos")]
#[tauri::command]
fn open_local_viewer(window: tauri::WebviewWindow) -> Result<(), String> {
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
) -> Result<(), String> {
    session_window(&window)?;
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.stop())
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
fn main() {
    let builder = tauri::Builder::default().manage(CaptureState::default());
    #[cfg(target_os = "macos")]
    let builder = builder
        .manage(local_session::LocalSession::default())
        .invoke_handler(tauri::generate_handler![
            start_capture,
            stop_capture,
            next_frame,
            open_local_viewer,
            request_input_access,
            local_session_status,
            request_local_session,
            decide_local_session,
            close_local_session,
            next_local_video,
            send_local_input
        ]);
    #[cfg(not(target_os = "macos"))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        start_capture,
        stop_capture,
        next_frame
    ]);
    builder
        .on_window_event(|window, event| {
            #[cfg(target_os = "macos")]
            if matches!(window.label(), "main" | "viewer" | "fixture")
                && matches!(event, tauri::WindowEvent::Destroyed)
            {
                let _ = window.state::<local_session::LocalSession>().stop();
            }
            if window.label() == "fixture" && matches!(event, tauri::WindowEvent::Destroyed) {
                #[cfg(target_os = "macos")]
                if let Ok(mut worker) = window.state::<CaptureState>().worker.lock()
                    && let Some(mut worker) = worker.take()
                {
                    let _ = worker.stop();
                }
            }
            if window.label() == "main" && matches!(event, tauri::WindowEvent::Destroyed) {
                #[cfg(target_os = "macos")]
                if let Ok(mut worker) = window.state::<CaptureState>().worker.lock()
                    && let Some(mut worker) = worker.take()
                {
                    let _ = worker.stop();
                }
                window.app_handle().exit(0);
            }
        })
        .run(tauri::generate_context!())
        .expect("Mixel Remote desktop runtime failed");
}
