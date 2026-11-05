//! ScreenCaptureKit FFI. SCStream stays on its owner thread; pixel buffers are
//! copied while read-locked and only owned Rust frames cross threads.
use crate::{
    damage::Frame,
    stream::{CaptureError, CapturedFrame, FrameMailbox},
};
use block2::RcBlock;
use dispatch2::{DispatchQueue, DispatchRetained};
use objc2::{
    AnyThread, ClassType, DefinedClass, define_class, msg_send, rc::Retained,
    runtime::ProtocolObject, sel,
};
use objc2_core_graphics::CGPreflightScreenCaptureAccess;
use objc2_core_media::{CMSampleBuffer, CMTime};
use objc2_core_video::*;
use objc2_foundation::{NSArray, NSError, NSObject, NSObjectProtocol};
use objc2_screen_capture_kit::{
    SCContentFilter, SCShareableContent, SCStream, SCStreamConfiguration, SCStreamDelegate,
    SCStreamOutput, SCStreamOutputType,
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
fn content(own_only: bool) -> Result<Retained<SCShareableContent>, CaptureError> {
    if !own_only && !CGPreflightScreenCaptureAccess() {
        return Err(CaptureError::Permission);
    }
    let (tx, rx) = mpsc::sync_channel(1);
    let block = RcBlock::new(
        move |content: *mut SCShareableContent, error: *mut NSError| {
            let result = if !error.is_null() {
                // SAFETY: Apple supplies a valid NSError for this callback's lifetime.
                Err(CaptureError::Native(unsafe {
                    (&*error).localizedDescription().to_string()
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
struct PixelLock<'a>(&'a CVPixelBuffer);
impl Drop for PixelLock<'_> {
    fn drop(&mut self) {
        // SAFETY: this guard exists only following a successful read-only lock.
        unsafe {
            CVPixelBufferUnlockBaseAddress(self.0, CVPixelBufferLockFlags::ReadOnly);
        }
    }
}
unsafe fn copy_sample(sample: &CMSampleBuffer) -> Result<Option<CapturedFrame>, CaptureError> {
    // SAFETY: caller holds the live callback sample.
    let Some(buffer) = (unsafe { sample.image_buffer() }) else {
        return Ok(None);
    };
    if CVPixelBufferGetPixelFormatType(&buffer) != kCVPixelFormatType_32BGRA {
        return Err(CaptureError::Native("unexpected pixel format".into()));
    }
    // SAFETY: valid retained CVPixelBuffer, matched read-only unlock via guard.
    if unsafe { CVPixelBufferLockBaseAddress(&buffer, CVPixelBufferLockFlags::ReadOnly) } != 0 {
        return Err(CaptureError::Native("pixel buffer lock failed".into()));
    }
    let _lock = PixelLock(&buffer);
    let (w, h, stride) = (
        CVPixelBufferGetWidth(&buffer),
        CVPixelBufferGetHeight(&buffer),
        CVPixelBufferGetBytesPerRow(&buffer),
    );
    let size = stride
        .checked_mul(h)
        .filter(|n| *n <= 256 * 1024 * 1024)
        .ok_or_else(|| CaptureError::Native("invalid pixel buffer size".into()))?;
    let base = CVPixelBufferGetBaseAddress(&buffer);
    if base.is_null() || CVPixelBufferGetDataSize(&buffer) < size {
        return Err(CaptureError::Native("missing pixel storage".into()));
    }
    // SAFETY: successful lock, non-null storage, bounds checked against CoreVideo
    // data size. Read-only bytes are copied before the lock/sample is released.
    let bytes = unsafe { std::slice::from_raw_parts(base.cast::<u8>(), size) };
    Frame::bgra(w, h, stride, bytes).map_err(|e| CaptureError::Native(e.to_string()))?;
    Ok(Some(
        CapturedFrame::bgra(w, h, stride, bytes.to_vec())
            .map_err(|e| CaptureError::Native(e.to_string()))?,
    ))
}
pub struct MacStream {
    stream: Retained<SCStream>,
    output: Retained<MixelCaptureOutput>,
    queue: DispatchRetained<DispatchQueue>,
    pub frames: Arc<FrameMailbox>,
    active: bool,
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
            let filter = match target {
                Target::Display(id) => {
                    let displays = content.displays();
                    let display = displays
                        .iter()
                        .find(|d| d.displayID() == id)
                        .ok_or(CaptureError::Target)?;
                    SCContentFilter::initWithDisplay_excludingWindows(
                        SCContentFilter::alloc(),
                        &display,
                        &NSArray::new(),
                    )
                }
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
            let mut capture = Self {
                stream,
                output,
                queue,
                frames,
                active: true,
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
    pub fn stop(&mut self) -> Result<(), CaptureError> {
        if !self.active {
            return Ok(());
        }
        self.active = false;
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
