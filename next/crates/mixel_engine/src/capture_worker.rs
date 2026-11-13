//! The screen-capture owner thread: macOS ScreenCaptureKit, Windows desktop duplication, X11.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
use native_os_capture::stream::FrameMailbox;
use std::sync::Arc;

#[cfg(target_os = "macos")]
pub struct Worker {
    pub stop: std::sync::mpsc::SyncSender<()>,
    pub thread: Option<std::thread::JoinHandle<Result<(), String>>>,
    pub frames: Arc<FrameMailbox>,
}
#[cfg(target_os = "macos")]
impl Worker {
    pub fn start(source: String, confirm_full_display: bool) -> Result<Self, String> {
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
    pub fn stop(&mut self) -> Result<(), String> {
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
pub use native_os_capture::windows_capture::{
    Display as PlatformDisplay, WindowsCapture as PlatformCapture,
};
#[cfg(target_os = "linux")]
pub use native_os_capture::x11_capture::{Display as PlatformDisplay, X11Capture as PlatformCapture};
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
pub struct Worker {
    pub stop: Arc<std::sync::atomic::AtomicBool>,
    pub thread: Option<std::thread::JoinHandle<Result<(), String>>>,
    pub frames: Arc<FrameMailbox>,
}
#[cfg(any(target_os = "windows", target_os = "linux"))]
impl Worker {
    pub fn start(display: PlatformDisplay) -> Result<Self, String> {
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
    pub fn stop(&mut self) -> Result<(), String> {
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
