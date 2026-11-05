//! Single-slot frame delivery. A slow consumer skips stale frames rather than
//! retaining sensitive screen images in an unbounded queue.
use crate::damage::{Frame, FrameError};
use std::{
    sync::{Arc, Condvar, Mutex},
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

pub struct CapturedFrame {
    width: usize,
    height: usize,
    stride: usize,
    pixels: Zeroizing<Vec<u8>>,
    pub captured_at: Instant,
}
impl CapturedFrame {
    pub fn bgra(
        width: usize,
        height: usize,
        stride: usize,
        pixels: Vec<u8>,
    ) -> Result<Self, FrameError> {
        let pixels = Zeroizing::new(pixels);
        Frame::bgra(width, height, stride, &pixels)?;
        if stride.checked_mul(height) != Some(pixels.len()) {
            return Err(FrameError::Layout);
        }
        Ok(Self {
            width,
            height,
            stride,
            pixels,
            captured_at: Instant::now(),
        })
    }
    pub fn frame(&self) -> Frame<'_> {
        // The layout is validated once at construction; fields are immutable.
        Frame::bgra(self.width, self.height, self.stride, &self.pixels)
            .expect("validated immutable capture layout")
    }
    pub fn pixels(&self) -> &[u8] {
        &self.pixels
    }
    pub fn width(&self) -> usize {
        self.width
    }
    pub fn height(&self) -> usize {
        self.height
    }
    pub fn stride(&self) -> usize {
        self.stride
    }
}
#[derive(Clone, Debug, thiserror::Error, PartialEq, Eq)]
pub enum CaptureError {
    #[error("screen recording permission is required")]
    Permission,
    #[error("capture target is no longer available")]
    Target,
    #[error("capture operation timed out")]
    Timeout,
    #[error("capture stopped: {0}")]
    Native(String),
    #[error("capture stopped")]
    Stopped,
}
#[derive(Default)]
struct Slot {
    sequence: u64,
    frame: Option<Arc<CapturedFrame>>,
    closed: Option<CaptureError>,
}
#[derive(Default)]
pub struct FrameMailbox {
    slot: Mutex<Slot>,
    changed: Condvar,
}
impl FrameMailbox {
    pub fn publish(&self, frame: CapturedFrame) {
        if let Ok(mut slot) = self.slot.lock() {
            if slot.closed.is_some() {
                return;
            }
            let Some(sequence) = slot.sequence.checked_add(1) else {
                slot.closed = Some(CaptureError::Stopped);
                slot.frame = None;
                self.changed.notify_all();
                return;
            };
            slot.sequence = sequence;
            slot.frame = Some(Arc::new(frame));
            self.changed.notify_all();
        }
    }
    pub fn close(&self, reason: CaptureError) {
        if let Ok(mut slot) = self.slot.lock() {
            if slot.closed.is_none() {
                slot.closed = Some(reason);
            }
            slot.frame = None;
            self.changed.notify_all();
        }
    }
    pub fn next(
        &self,
        after: u64,
        timeout: Duration,
    ) -> Result<(u64, Arc<CapturedFrame>), CaptureError> {
        let slot = self.slot.lock().map_err(|_| CaptureError::Stopped)?;
        let (slot, wait) = self
            .changed
            .wait_timeout_while(slot, timeout, |s| s.closed.is_none() && s.sequence <= after)
            .map_err(|_| CaptureError::Stopped)?;
        if let Some(error) = &slot.closed {
            return Err(error.clone());
        }
        if wait.timed_out() && slot.sequence <= after {
            return Err(CaptureError::Timeout);
        }
        slot.frame
            .as_ref()
            .map(|frame| (slot.sequence, frame.clone()))
            .ok_or(CaptureError::Timeout)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sequence_exhaustion_closes_and_releases_the_last_frame() {
        let queue = FrameMailbox::default();
        queue.publish(CapturedFrame::bgra(1, 1, 4, vec![0; 4]).unwrap());
        let (_, frame) = queue.next(0, Duration::ZERO).unwrap();
        let weak = Arc::downgrade(&frame);
        drop(frame);
        queue.slot.lock().unwrap().sequence = u64::MAX;
        queue.publish(CapturedFrame::bgra(1, 1, 4, vec![1; 4]).unwrap());
        assert!(weak.upgrade().is_none());
        assert!(matches!(
            queue.next(0, Duration::ZERO),
            Err(CaptureError::Stopped)
        ));
    }
}
