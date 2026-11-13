//! Read-locked copies of CoreVideo pixel buffers (Mac capture, Mac and iPhone decoding).
use crate::{
    damage::Frame,
    stream::{CaptureError, CapturedFrame},
};
use objc2_core_video::*;

struct PixelLock<'a>(&'a CVPixelBuffer);
impl Drop for PixelLock<'_> {
    fn drop(&mut self) {
        // SAFETY: this guard exists only following a successful read-only lock.
        unsafe {
            CVPixelBufferUnlockBaseAddress(self.0, CVPixelBufferLockFlags::ReadOnly);
        }
    }
}
/// Caller owns a live CVPixelBuffer for the duration of this read-locked copy.
pub(crate) unsafe fn copy_pixel_buffer(
    buffer: &CVPixelBuffer,
) -> Result<CapturedFrame, CaptureError> {
    if CVPixelBufferGetPixelFormatType(buffer) != kCVPixelFormatType_32BGRA {
        return Err(CaptureError::Native("unexpected pixel format".into()));
    }
    // SAFETY: valid retained CVPixelBuffer, matched read-only unlock via guard.
    if unsafe { CVPixelBufferLockBaseAddress(buffer, CVPixelBufferLockFlags::ReadOnly) } != 0 {
        return Err(CaptureError::Native("pixel buffer lock failed".into()));
    }
    let _lock = PixelLock(buffer);
    let (w, h, stride) = (
        CVPixelBufferGetWidth(buffer),
        CVPixelBufferGetHeight(buffer),
        CVPixelBufferGetBytesPerRow(buffer),
    );
    let size = stride
        .checked_mul(h)
        .filter(|n| *n <= 256 * 1024 * 1024)
        .ok_or_else(|| CaptureError::Native("invalid pixel buffer size".into()))?;
    let base = CVPixelBufferGetBaseAddress(buffer);
    if base.is_null() || CVPixelBufferGetDataSize(buffer) < size {
        return Err(CaptureError::Native("missing pixel storage".into()));
    }
    // SAFETY: successful lock, non-null storage, bounds checked against CoreVideo
    // data size. Read-only bytes are copied before the lock/sample is released.
    let bytes = unsafe { std::slice::from_raw_parts(base.cast::<u8>(), size) };
    Frame::bgra(w, h, stride, bytes).map_err(|e| CaptureError::Native(e.to_string()))?;
    CapturedFrame::bgra(w, h, stride, bytes.to_vec())
        .map_err(|error| CaptureError::Native(error.to_string()))
}
