//! ScreenCaptureKit FFI. SCStream stays on its owner thread; pixel buffers are
//! copied while read-locked and only owned Rust frames cross threads.
use crate::{
    stream::{CaptureError, CapturedFrame, FrameMailbox},
};
use block2::RcBlock;
use dispatch2::{DispatchQueue, DispatchRetained};
use objc2::{
    AnyThread, ClassType, DefinedClass, define_class, msg_send, rc::Retained,
    runtime::ProtocolObject, sel,
};
use objc2_core_foundation::{CFArray, CFDictionary, CFNumber, CFRetained, CFType};
use objc2_core_graphics::CGPreflightScreenCaptureAccess;
use objc2_core_media::{CMSampleBuffer, CMTime};
use objc2_core_video::*;
use objc2_foundation::{NSArray, NSError, NSObject, NSObjectProtocol};
use objc2_screen_capture_kit::{
    SCContentFilter, SCFrameStatus, SCShareableContent, SCStream, SCStreamConfiguration,
    SCStreamDelegate, SCStreamFrameInfoStatus, SCStreamOutput, SCStreamOutputType,
};
use std::{
    sync::{Arc, mpsc},
    time::Duration,
};

// SCShareableContent is an immutable snapshot, documented as available from an
// asynchronous completion handler. Retaining it permits a single ownership
// transfer to the capture owner; no mutable Objective-C object is shared.
struct Content(Retained<SCShareableContent>);
unsafe impl Send for Content {}
/// Read permission state only. Listing sources never opens a system prompt.
pub fn screen_access() -> bool {
    CGPreflightScreenCaptureAccess()
}
/// Keep the WindowServer client initialized on the main thread while the
/// headless LoginWindow agent runs its networking and media workers.
pub fn run_login_window<T: Send>(worker: impl FnOnce() -> T + Send) -> Result<T, &'static str> {
    use objc2::MainThreadMarker;
    use objc2_app_kit::NSApplication;
    use objc2_foundation::{NSDate, NSDefaultRunLoopMode, NSRunLoop};

    let main = MainThreadMarker::new().ok_or("Login host must initialize on its main thread")?;
    let app = NSApplication::sharedApplication(main);
    app.finishLaunching();
    let run_loop = NSRunLoop::currentRunLoop();
    std::thread::scope(|scope| {
        let task = scope.spawn(worker);
        while !task.is_finished() {
            objc2::rc::autoreleasepool(|_| {
                let deadline = NSDate::dateWithTimeIntervalSinceNow(0.05);
                // SAFETY: Foundation exports an immutable default-mode name.
                if !run_loop.runMode_beforeDate(unsafe { NSDefaultRunLoopMode }, &deadline) {
                    std::thread::sleep(Duration::from_millis(10));
                }
            });
        }
        task.join()
            .map_err(|_| "Login host worker stopped unexpectedly")
    })
}

pub fn is_login_window_console() -> bool {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata("/dev/console").is_ok_and(|console| console.uid() == 0)
}
pub fn main_display() -> u32 {
    objc2_core_graphics::CGMainDisplayID()
}
/// Left, top, width and height of a display in global coordinates (points; main display at 0,0).
pub fn display_bounds(id: u32) -> (f64, f64, f64, f64) {
    let bounds = objc2_core_graphics::CGDisplayBounds(id);
    (bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height)
}
/// Call only from the local user's explicit screen-sharing permission action.
pub fn request_screen_access() -> bool {
    objc2_core_graphics::CGRequestScreenCaptureAccess()
}
fn content(own_only: bool) -> Result<Retained<SCShareableContent>, CaptureError> {
    if !own_only && !CGPreflightScreenCaptureAccess() {
        return Err(CaptureError::Permission);
    }
    query_content(own_only)
}

/// Run the public ScreenCaptureKit authorization check in the approved
/// login-window host. This queries display metadata and never starts a stream.
pub fn probe_login_display(display: u32) -> Result<(), CaptureError> {
    let snapshot = query_content(false)?;
    // SAFETY: the retained snapshot has an immutable display array.
    unsafe { snapshot.displays() }
        .iter()
        .any(|item| unsafe { item.displayID() } == display)
        .then_some(())
        .ok_or(CaptureError::Target)
}

fn query_content(own_only: bool) -> Result<Retained<SCShareableContent>, CaptureError> {
    let (tx, rx) = mpsc::sync_channel(1);
    let block = RcBlock::new(
        move |content: *mut SCShareableContent, error: *mut NSError| {
            let result = if !error.is_null() {
                // SAFETY: Apple supplies a valid NSError for this callback's lifetime.
                Err(CaptureError::Native(unsafe {
                    let error = &*error;
                    format!(
                        "{} {}: {}",
                        error.domain(),
                        error.code(),
                        error.localizedDescription()
                    )
                }))
            } else {
                // SAFETY: callback argument is live; retain before the callback ends.
                unsafe { Retained::retain(content) }
                    .map(Content)
                    .ok_or(CaptureError::Target)
            };
            let _ = tx.try_send(result);
        },
    );
    // SAFETY: ScreenCaptureKit copies the completion block for async use.
    unsafe {
        if own_only {
            let supported: bool = msg_send![SCShareableContent::class(),respondsToSelector:sel!(getCurrentProcessShareableContentWithCompletionHandler:)];
            if !supported {
                return Err(CaptureError::Native(
                    "current-process capture requires a newer macOS".into(),
                ));
            }
            SCShareableContent::getCurrentProcessShareableContentWithCompletionHandler(&block);
        } else {
            SCShareableContent::getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler(false,true,&block);
        }
    }
    rx.recv_timeout(Duration::from_secs(10))
        .map_err(|_| CaptureError::Timeout)?
        .map(|c| c.0)
}
#[derive(Debug, Clone)]
pub struct Display {
    pub id: u32,
    pub width: usize,
    pub height: usize,
}
pub fn displays() -> Result<Vec<Display>, CaptureError> {
    let content = content(false)?;
    // SAFETY: content is retained and its display array is an immutable snapshot.
    Ok(unsafe { content.displays() }
        .iter()
        .map(|d| unsafe {
            Display {
                id: d.displayID(),
                width: d.width() as usize,
                height: d.height() as usize,
            }
        })
        .collect())
}
#[derive(Debug, Clone)]
pub struct OwnWindow {
    pub id: u32,
    pub title: String,
}
pub fn own_windows() -> Result<Vec<OwnWindow>, CaptureError> {
    let content = content(true)?;
    // SAFETY: immutable current-process snapshot. Explicit PID check prevents
    // accidental expansion to another application's windows.
    Ok(unsafe { content.windows() }
        .iter()
        .filter_map(|w| unsafe {
            if w.owningApplication()
                .is_none_or(|app| app.processID() as u32 != std::process::id())
            {
                return None;
            }
            Some(OwnWindow {
                id: w.windowID(),
                title: w.title().map(|s| s.to_string()).unwrap_or_default(),
            })
        })
        .collect())
}
#[derive(Clone, Copy)]
enum Target {
    Display(u32),
    OwnWindow(u32),
}

/// Windows of this app that the technician must not see (the privacy cover, the drawing
/// overlay), per owner, with a version that grows on every change, and the version each running
/// display capture has applied.
type ExcludedWindows = (u64, Vec<(Excluded, Vec<u32>)>);
static EXCLUDED: std::sync::Mutex<ExcludedWindows> = std::sync::Mutex::new((0, Vec::new()));
static APPLIED: std::sync::Mutex<Vec<(u64, u64)>> = std::sync::Mutex::new(Vec::new());
static NEXT_STREAM: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

/// Who left windows out of the capture; each owner replaces only its own list.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Excluded {
    PrivacyCover,
    Drawings,
}

/// Leaves these windows of `owner` out of every display capture from now on (an empty list
/// takes them back in); the windows of other owners stay excluded. Returns the version.
pub fn set_excluded_windows(owner: Excluded, ids: Vec<u32>) -> u64 {
    let mut excluded = EXCLUDED.lock().unwrap_or_else(|error| error.into_inner());
    excluded.0 += 1;
    excluded.1.retain(|(who, _)| *who != owner);
    if !ids.is_empty() {
        excluded.1.push((owner, ids));
    }
    excluded.0
}

/// Whether every running display capture has applied `version` (true when none runs).
pub fn exclusions_applied(version: u64) -> bool {
    APPLIED
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .iter()
        .all(|(_, applied)| *applied >= version)
}

fn excluded_now() -> (u64, Vec<u32>) {
    let excluded = EXCLUDED.lock().unwrap_or_else(|error| error.into_inner());
    (excluded.0, excluded.1.iter().flat_map(|(_, ids)| ids.iter().copied()).collect())
}

/// A display filter that leaves out this app's excluded windows.
/// SAFETY: `content` is a retained immutable snapshot.
unsafe fn display_filter(content: &SCShareableContent, id: u32, excluded: &[u32]) -> Result<Retained<SCContentFilter>, CaptureError> {
    unsafe {
        let displays = content.displays();
        let display = displays
            .iter()
            .find(|d| d.displayID() == id)
            .ok_or(CaptureError::Target)?;
        let own = std::process::id();
        let windows: Vec<Retained<objc2_screen_capture_kit::SCWindow>> = content
            .windows()
            .iter()
            .filter(|window| {
                excluded.contains(&window.windowID())
                    && window.owningApplication().is_some_and(|app| app.processID() as u32 == own)
            })
            .collect();
        let windows = NSArray::from_retained_slice(&windows);
        Ok(SCContentFilter::initWithDisplay_excludingWindows(SCContentFilter::alloc(), &display, &windows))
    }
}
struct OutputIvars {
    mailbox: Arc<FrameMailbox>,
}
define_class!(
    #[unsafe(super=NSObject)]
    #[ivars=OutputIvars]
    struct MixelCaptureOutput;
    unsafe impl NSObjectProtocol for MixelCaptureOutput {}
    unsafe impl SCStreamOutput for MixelCaptureOutput {
        #[unsafe(method(stream:didOutputSampleBuffer:ofType:))]
        fn sample(&self, _stream: &SCStream, sample: &CMSampleBuffer, kind: SCStreamOutputType) {
            if kind != SCStreamOutputType::Screen {
                return;
            }
            // SAFETY: ScreenCaptureKit supplies a live CMSampleBuffer throughout
            // this callback. No sample-buffer pointer escapes the callback.
            match unsafe { copy_sample(sample) } {
                Ok(Some(frame)) => self.ivars().mailbox.publish(frame),
                Ok(None) => {} // idle/incomplete frame has no image buffer
                Err(error) => self.ivars().mailbox.close(error),
            }
        }
    }
    unsafe impl SCStreamDelegate for MixelCaptureOutput {
        #[unsafe(method(stream:didStopWithError:))]
        fn stopped(&self, _stream: &SCStream, error: &NSError) {
            self.ivars().mailbox.close(CaptureError::Native(
                error.localizedDescription().to_string(),
            ));
        }
    }
);
unsafe fn copy_sample(sample: &CMSampleBuffer) -> Result<Option<CapturedFrame>, CaptureError> {
    // ScreenCaptureKit marks unchanged frames Idle; do not copy or encode the
    // previous surface again. Blank/suspended/stopped is a revoked source,
    // not evidence that its previous sensitive image is still shareable.
    let status = unsafe { sample.sample_attachments_array(false) }
        .and_then(|attachments| {
            // SAFETY: CMSampleBuffer documents an array of Core Foundation
            // dictionary objects. This only restores its erased element type;
            // each element's dictionary type is checked before accessing it.
            let typed = unsafe { CFRetained::cast_unchecked::<CFArray<CFType>>(attachments) };
            typed.get(0)
        })
        .and_then(|value| value.downcast::<CFDictionary>().ok())
        .map(|dictionary| {
            // SAFETY: the concrete dictionary type was checked above. Apple
            // specifies CFString keys and Core Foundation values for these
            // metadata dictionaries, restoring the erased generic types.
            unsafe { CFRetained::cast_unchecked::<CFDictionary<CFType, CFType>>(dictionary) }
        })
        .and_then(|dictionary| {
            // SAFETY: Apple declares this metadata key as NSString, which is
            // toll-free bridged to CFString/CFType. No ownership is transferred.
            let key = unsafe { &*(SCStreamFrameInfoStatus as *const _ as *const CFType) };
            dictionary.get(key)
        })
        .and_then(|value| value.downcast::<CFNumber>().ok())
        .and_then(|number| number.as_isize())
        .map(SCFrameStatus)
        .ok_or_else(|| CaptureError::Native("ScreenCaptureKit frame status is invalid".into()))?;
    match status {
        SCFrameStatus::Complete => {}
        SCFrameStatus::Idle | SCFrameStatus::Started => return Ok(None),
        SCFrameStatus::Blank | SCFrameStatus::Suspended | SCFrameStatus::Stopped => {
            return Err(CaptureError::Target);
        }
        _ => {
            return Err(CaptureError::Native(
                "ScreenCaptureKit frame status is unsupported".into(),
            ));
        }
    }
    // SAFETY: caller holds the live callback sample.
    let Some(buffer) = (unsafe { sample.image_buffer() }) else {
        return Ok(None);
    };
    // SAFETY: this live screen sample owns the returned CVPixelBuffer.
    unsafe { copy_pixel_buffer(&buffer) }.map(Some)
}
pub(crate) use crate::apple_pixels::copy_pixel_buffer;
pub struct MacStream {
    stream: Retained<SCStream>,
    output: Retained<MixelCaptureOutput>,
    queue: DispatchRetained<DispatchQueue>,
    pub frames: Arc<FrameMailbox>,
    active: bool,
    /// Display captures: the display and the exclusion version applied (see `set_excluded_windows`).
    display: Option<u32>,
    stream_id: u64,
    applied: u64,
}
impl MacStream {
    pub fn start(
        display_id: u32,
        width: usize,
        height: usize,
        fps: u32,
    ) -> Result<Self, CaptureError> {
        Self::start_target(Target::Display(display_id), width, height, fps)
    }
    pub fn start_own_window(
        window_id: u32,
        width: usize,
        height: usize,
        fps: u32,
    ) -> Result<Self, CaptureError> {
        Self::start_target(Target::OwnWindow(window_id), width, height, fps)
    }
    fn start_target(
        target: Target,
        width: usize,
        height: usize,
        fps: u32,
    ) -> Result<Self, CaptureError> {
        if width == 0 || height == 0 || width > 8192 || height > 8192 || !(1..=60).contains(&fps) {
            return Err(CaptureError::Native("invalid capture configuration".into()));
        }
        let content = content(matches!(target, Target::OwnWindow(_)))?;
        // SAFETY: retained immutable snapshot, validated settings, live delegate
        // and serial callback queue retained for the entire capture lifecycle.
        unsafe {
            let (version, excluded) = excluded_now();
            let filter = match target {
                Target::Display(id) => display_filter(&content, id, &excluded)?,
                Target::OwnWindow(id) => {
                    let windows = content.windows();
                    let window = windows
                        .iter()
                        .find(|w| {
                            w.windowID() == id
                                && w.owningApplication()
                                    .is_some_and(|app| app.processID() as u32 == std::process::id())
                        })
                        .ok_or(CaptureError::Target)?;
                    SCContentFilter::initWithDesktopIndependentWindow(
                        SCContentFilter::alloc(),
                        &window,
                    )
                }
            };
            let config = SCStreamConfiguration::new();
            config.setWidth(width);
            config.setHeight(height);
            config.setPixelFormat(kCVPixelFormatType_32BGRA);
            config.setCapturesAudio(false);
            config.setColorSpaceName(objc2_core_graphics::kCGColorSpaceSRGB);
            config.setMinimumFrameInterval(CMTime::new(1, fps as i32));
            config.setQueueDepth(3);
            let frames = Arc::new(FrameMailbox::default());
            let output = MixelCaptureOutput::alloc().set_ivars(OutputIvars {
                mailbox: frames.clone(),
            });
            let output: Retained<MixelCaptureOutput> = msg_send![super(output), init];
            let queue = DispatchQueue::new("ch.mixel.remote.capture", None);
            let stream = SCStream::initWithFilter_configuration_delegate(
                SCStream::alloc(),
                &filter,
                &config,
                Some(ProtocolObject::from_ref(&*output)),
            );
            stream
                .addStreamOutput_type_sampleHandlerQueue_error(
                    ProtocolObject::from_ref(&*output),
                    SCStreamOutputType::Screen,
                    Some(&queue),
                )
                .map_err(|e| CaptureError::Native(e.localizedDescription().to_string()))?;
            let (tx, rx) = mpsc::sync_channel(1);
            let started = RcBlock::new(move |error: *mut NSError| {
                let result = if error.is_null() {
                    Ok(())
                } else {
                    Err(CaptureError::Native(
                        (&*error).localizedDescription().to_string(),
                    ))
                };
                let _ = tx.try_send(result);
            });
            let display = match target {
                Target::Display(id) => Some(id),
                Target::OwnWindow(_) => None,
            };
            let stream_id = NEXT_STREAM.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            if display.is_some() {
                APPLIED.lock().unwrap_or_else(|error| error.into_inner()).push((stream_id, version));
            }
            let mut capture = Self {
                stream,
                output,
                queue,
                frames,
                active: true,
                display,
                stream_id,
                applied: version,
            };
            capture
                .stream
                .startCaptureWithCompletionHandler(Some(&started));
            match rx.recv_timeout(Duration::from_secs(10)) {
                Ok(Ok(())) => Ok(capture),
                Ok(Err(e)) => {
                    let _ = capture.stop();
                    Err(e)
                }
                Err(_) => {
                    let _ = capture.stop();
                    Err(CaptureError::Timeout)
                }
            }
        }
    }
    /// Applies a changed window exclusion (privacy cover) to this running display capture.
    /// Called from the capture owner between pictures; cheap when nothing changed.
    pub fn sync_exclusions(&mut self) -> Result<(), CaptureError> {
        let Some(id) = self.display.filter(|_| self.active) else { return Ok(()) };
        let (version, excluded) = excluded_now();
        if version == self.applied {
            return Ok(());
        }
        let content = content(false)?;
        // SAFETY: retained snapshot; the stream is retained and the completion block is copied.
        let filter = unsafe { display_filter(&content, id, &excluded)? };
        let (tx, rx) = mpsc::sync_channel(1);
        let updated = RcBlock::new(move |error: *mut NSError| {
            let _ = tx.try_send(error.is_null());
        });
        // SAFETY: as above.
        unsafe { self.stream.updateContentFilter_completionHandler(&filter, Some(&updated)) };
        let applied = rx.recv_timeout(Duration::from_secs(2)).map_err(|_| CaptureError::Timeout)?;
        if !applied {
            return Err(CaptureError::Native("ScreenCaptureKit did not apply the window exclusion".into()));
        }
        self.applied = version;
        if let Some(entry) = APPLIED.lock().unwrap_or_else(|error| error.into_inner()).iter_mut().find(|(stream, _)| *stream == self.stream_id) {
            entry.1 = version;
        }
        Ok(())
    }
    pub fn stop(&mut self) -> Result<(), CaptureError> {
        if !self.active {
            return Ok(());
        }
        self.active = false;
        APPLIED.lock().unwrap_or_else(|error| error.into_inner()).retain(|(stream, _)| *stream != self.stream_id);
        self.frames.close(CaptureError::Stopped);
        let (tx, rx) = mpsc::sync_channel(1);
        // Keep delegate and queue alive until the actual asynchronous completion,
        // including when our bounded wait times out.
        let output = self.output.clone();
        let queue = self.queue.clone();
        let stopped = RcBlock::new(move |error: *mut NSError| {
            let _keep_alive = (&output, &queue);
            let result = if error.is_null() {
                Ok(())
            } else {
                // SAFETY: framework owns live NSError for callback duration.
                Err(CaptureError::Native(unsafe {
                    (&*error).localizedDescription().to_string()
                }))
            };
            let _ = tx.try_send(result);
        });
        // SAFETY: stream retained; framework copies callback before returning.
        unsafe {
            self.stream.stopCaptureWithCompletionHandler(Some(&stopped));
        }
        rx.recv_timeout(Duration::from_secs(3))
            .map_err(|_| CaptureError::Timeout)?
    }
}
impl Drop for MacStream {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

#[cfg(test)]
mod exclusion_tests {
    use super::*;

    #[test]
    fn each_owner_replaces_only_its_own_excluded_windows() {
        let start = set_excluded_windows(Excluded::PrivacyCover, vec![11, 12]);
        let next = set_excluded_windows(Excluded::Drawings, vec![21]);
        assert!(next > start);
        let mut ids = excluded_now().1;
        ids.sort_unstable();
        assert_eq!(ids, vec![11, 12, 21]);
        // The drawing overlay closing leaves the privacy cover out of the picture.
        set_excluded_windows(Excluded::Drawings, Vec::new());
        assert_eq!(excluded_now().1, vec![11, 12]);
        set_excluded_windows(Excluded::Drawings, vec![22]);
        set_excluded_windows(Excluded::PrivacyCover, Vec::new());
        assert_eq!(excluded_now().1, vec![22]);
        let last = set_excluded_windows(Excluded::Drawings, Vec::new());
        assert!(excluded_now().1.is_empty());
        assert_eq!(excluded_now().0, last);
    }
}
