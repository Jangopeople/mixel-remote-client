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
#[tauri::command]
async fn start_capture(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, CaptureState>,
) -> Result<String, String> {
    main_window(&window)?;
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
fn main() {
    tauri::Builder::default()
        .manage(CaptureState::default())
        .invoke_handler(tauri::generate_handler![
            start_capture,
            stop_capture,
            next_frame
        ])
        .on_window_event(|window, event| {
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
