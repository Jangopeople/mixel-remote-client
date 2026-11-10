//! Serial VideoToolbox hardware encoder. Each operation owns its completion
//! block; callbacks copy their borrowed sample before returning. At most one
//! input frame is in flight and a timeout invalidates the session.
use crate::{
    h264::{EncodeError, EncodedH264, MAX_ACCESS_UNIT, avcc_to_annex_b},
    stream::CapturedFrame,
};
use block2::RcBlock;
use objc2_core_foundation::{CFBoolean, CFDictionary, CFNumber, CFRetained, CFString, CFType};
use objc2_core_graphics::{CGColorSpace, kCGColorSpaceSRGB};
use objc2_core_media::{
    CMSampleBuffer, CMTime, CMVideoFormatDescriptionGetH264ParameterSetAtIndex,
    kCMVideoCodecType_H264,
};
use objc2_core_video::*;
use objc2_video_toolbox::*;
use std::{
    ptr::{self, NonNull},
    sync::mpsc,
    time::Duration,
};
fn status(operation: &'static str, value: i32) -> Result<(), EncodeError> {
    if value == 0 {
        Ok(())
    } else {
        Err(EncodeError::Native {
            operation,
            status: value,
        })
    }
}
pub struct HardwareH264Encoder {
    session: CFRetained<VTCompressionSession>,
    width: usize,
    height: usize,
    fps: u32,
    bitrate: u32,
    may_buffer: bool,
    last_timestamp: Option<u64>,
    failed: bool,
}
impl HardwareH264Encoder {
    pub fn new(width: usize, height: usize, fps: u32, bitrate: u32) -> Result<Self, EncodeError> {
        if !(2..=8192).contains(&width)
            || !(2..=8192).contains(&height)
            || !width.is_multiple_of(2)
            || !height.is_multiple_of(2)
            || !(1..=60).contains(&fps)
            || !(100_000..=100_000_000).contains(&bitrate)
        {
            return Err(EncodeError::Configuration);
        }
        // SAFETY: dictionary values match Apple's documented key types; create
        // writes an owned CF reference. No callback or refcon is retained here.
        unsafe {
            let required = CFDictionary::from_slices(
                &[kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder],
                &[CFBoolean::new(true)],
            );
            let mut raw = ptr::null_mut();
            let code = VTCompressionSession::create(
                None,
                width as i32,
                height as i32,
                kCMVideoCodecType_H264,
                Some(required.as_opaque()),
                None,
                None,
                None,
                ptr::null_mut(),
                NonNull::from(&mut raw),
            );
            if code != 0 {
                return Err(EncodeError::HardwareUnavailable);
            }
            let session =
                CFRetained::from_raw(NonNull::new(raw).ok_or(EncodeError::HardwareUnavailable)?);
            let mut encoder = Self {
                session,
                width,
                height,
                fps,
                bitrate,
                may_buffer: false,
                last_timestamp: None,
                failed: false,
            };
            encoder.property(
                kVTCompressionPropertyKey_RealTime,
                CFBoolean::new(true).as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_AllowFrameReordering,
                CFBoolean::new(false).as_ref(),
            )?;
            // The caller waits for each frame before submitting another.
            encoder.may_buffer = match encoder.property(
                kVTCompressionPropertyKey_MaxFrameDelayCount,
                CFNumber::new_i32(0).as_ref(),
            ) {
                Ok(()) => false,
                Err(EncodeError::Property { status, .. })
                    if status == kVTPropertyNotSupportedErr =>
                {
                    true
                }
                Err(error) => return Err(error),
            };
            encoder.property(
                kVTCompressionPropertyKey_ProfileLevel,
                kVTProfileLevel_H264_ConstrainedBaseline_AutoLevel.as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_AverageBitRate,
                CFNumber::new_i64(bitrate.into()).as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_ExpectedFrameRate,
                CFNumber::new_i32(fps as i32).as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_MaxKeyFrameInterval,
                CFNumber::new_i32(fps as i32).as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_ColorPrimaries,
                kCVImageBufferColorPrimaries_ITU_R_709_2.as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_TransferFunction,
                kCVImageBufferTransferFunction_sRGB.as_ref(),
            )?;
            encoder.property(
                kVTCompressionPropertyKey_YCbCrMatrix,
                kCVImageBufferYCbCrMatrix_ITU_R_709_2.as_ref(),
            )?;
            status("prepare", encoder.session.prepare_to_encode_frames())?;
            if !encoder.hardware_active()? {
                return Err(EncodeError::HardwareUnavailable);
            }
            Ok(encoder)
        }
    }
    fn property(&mut self, key: &CFString, value: &CFType) -> Result<(), EncodeError> {
        // SAFETY: this CFType is the live compression session; callers pass the
        // exact documented CF value type for each key.
        let code = unsafe { VTSessionSetProperty(self.session.as_ref(), key, Some(value)) };
        if code == 0 {
            Ok(())
        } else {
            Err(EncodeError::Property {
                key: key.to_string(),
                status: code,
            })
        }
    }
    pub fn hardware_active(&self) -> Result<bool, EncodeError> {
        let mut value: *const CFType = ptr::null();
        // SAFETY: typed output storage receives an owned CF property reference.
        unsafe {
            status(
                "hardware query",
                VTSessionCopyProperty(
                    self.session.as_ref(),
                    kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,
                    None,
                    (&mut value as *mut *const CFType).cast(),
                ),
            )?;
            let value = CFRetained::from_raw(
                NonNull::new(value.cast_mut()).ok_or(EncodeError::HardwareUnavailable)?,
            );
            Ok(value
                .downcast_ref::<CFBoolean>()
                .is_some_and(CFBoolean::as_bool))
        }
    }
    pub fn encode(
        &mut self,
        frame: &CapturedFrame,
        timestamp_us: u64,
        force_keyframe: bool,
    ) -> Result<EncodedH264, EncodeError> {
        if self.failed {
            return Err(EncodeError::Failed);
        }
        if frame.width() != self.width
            || frame.height() != self.height
            || timestamp_us > i64::MAX as u64
            || self.last_timestamp.is_some_and(|last| timestamp_us <= last)
        {
            return Err(EncodeError::Configuration);
        }
        let buffer = pixel_buffer(frame)?;
        let (tx, rx) = mpsc::sync_channel(1);
        let block = RcBlock::new(
            move |code: i32, flags: VTEncodeInfoFlags, sample: *mut CMSampleBuffer| {
                let result = if code != 0 {
                    Err(EncodeError::Native {
                        operation: "output",
                        status: code,
                    })
                } else if flags.contains(VTEncodeInfoFlags::FrameDropped) {
                    Err(EncodeError::Dropped)
                } else if sample.is_null() {
                    Err(EncodeError::Bitstream)
                }
                // SAFETY: sample is live for the duration of this completion block.
                else {
                    unsafe { copy_encoded(&*sample) }
                };
                let _ = tx.try_send(result);
            },
        );
        // SAFETY: owned input buffer remains unmodified, VT retains it for the
        // async encode; VT copies the output block which owns its channel sender.
        unsafe {
            let keyframe = CFDictionary::from_slices(
                &[kVTEncodeFrameOptionKey_ForceKeyFrame],
                &[CFBoolean::new(true)],
            );
            let properties = force_keyframe.then(|| keyframe.as_opaque());
            let mut flags = VTEncodeInfoFlags::empty();
            let code = self.session.encode_frame_with_output_handler(
                &buffer,
                CMTime::new(timestamp_us as i64, 1_000_000),
                CMTime::new(1, self.fps as i32),
                properties,
                &mut flags,
                RcBlock::as_ptr(&block),
            );
            if let Err(error) = status("encode", code) {
                self.failed = true;
                self.session.invalidate();
                return Err(error);
            }
            self.last_timestamp = Some(timestamp_us);
            if flags.contains(VTEncodeInfoFlags::FrameDropped) {
                return Err(EncodeError::Dropped);
            }
        }
        let result = if self.may_buffer {
            match rx.recv_timeout(Duration::from_millis(33)) {
                Ok(result) => result,
                Err(mpsc::RecvTimeoutError::Disconnected) => Err(EncodeError::Timeout),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    // SAFETY: the owned session is still active. Only the
                    // current frame can be pending on this serial owner.
                    let code = unsafe {
                        self.session
                            .complete_frames(CMTime::new(timestamp_us as i64, 1_000_000))
                    };
                    status("complete frame", code)
                        .and_then(|()| rx.try_recv().unwrap_or(Err(EncodeError::Timeout)))
                }
            }
        } else {
            rx.recv_timeout(Duration::from_millis(500))
                .unwrap_or(Err(EncodeError::Timeout))
        };
        self.finish_output(result)
    }

    pub(crate) fn recreate(&self) -> Result<Self, EncodeError> {
        Self::new(self.width, self.height, self.fps, self.bitrate)
    }

    fn finish_output(
        &mut self,
        result: Result<EncodedH264, EncodeError>,
    ) -> Result<EncodedH264, EncodeError> {
        match result {
            Ok(frame) => Ok(frame),
            // A reported dropped frame is a completed encode operation, not a
            // dead compression session. Keep the same timestamp sequence and
            // let the stream owner retry with an independently decodable IDR.
            Err(EncodeError::Dropped) => Err(EncodeError::Dropped),
            Err(error) => {
                self.failed = true;
                unsafe {
                    self.session.invalidate();
                }
                Err(error)
            }
        }
    }
}
impl Drop for HardwareH264Encoder {
    fn drop(&mut self) {
        unsafe {
            self.session.invalidate();
        }
    }
}

struct WriteLock<'a>(&'a CVPixelBuffer);
impl Drop for WriteLock<'_> {
    fn drop(&mut self) {
        unsafe {
            CVPixelBufferUnlockBaseAddress(self.0, CVPixelBufferLockFlags::empty());
        }
    }
}
fn pixel_buffer(frame: &CapturedFrame) -> Result<CFRetained<CVPixelBuffer>, EncodeError> {
    // SAFETY: create writes an owned reference; the lock guard matches flags.
    unsafe {
        let mut raw = ptr::null_mut();
        status(
            "create pixels",
            CVPixelBufferCreate(
                None,
                frame.width(),
                frame.height(),
                kCVPixelFormatType_32BGRA,
                None,
                NonNull::from(&mut raw),
            ),
        )?;
        let buffer = CFRetained::from_raw(NonNull::new(raw).ok_or(EncodeError::Bitstream)?);
        status(
            "lock pixels",
            CVPixelBufferLockBaseAddress(&buffer, CVPixelBufferLockFlags::empty()),
        )?;
        {
            let _lock = WriteLock(&buffer);
            let stride = CVPixelBufferGetBytesPerRow(&buffer);
            let length = stride
                .checked_mul(frame.height())
                .filter(|size| *size <= 256 * 1024 * 1024)
                .ok_or(EncodeError::Bitstream)?;
            let base = CVPixelBufferGetBaseAddress(&buffer);
            if base.is_null()
                || stride < frame.width() * 4
                || CVPixelBufferGetDataSize(&buffer) < length
            {
                return Err(EncodeError::Bitstream);
            }
            // SAFETY: storage size checked against CoreVideo; buffer write-locked.
            let bytes = std::slice::from_raw_parts_mut(base.cast::<u8>(), length);
            bytes.fill(0);
            for y in 0..frame.height() {
                bytes[y * stride..y * stride + frame.width() * 4].copy_from_slice(
                    &frame.pixels()[y * frame.stride()..y * frame.stride() + frame.width() * 4],
                );
            }
        }
        let color =
            CGColorSpace::with_name(Some(kCGColorSpaceSRGB)).ok_or(EncodeError::Configuration)?;
        buffer.set_attachment(
            kCVImageBufferCGColorSpaceKey,
            color.as_ref(),
            CVAttachmentMode::ShouldPropagate,
        );
        buffer.set_attachment(
            kCVImageBufferColorPrimariesKey,
            kCVImageBufferColorPrimaries_ITU_R_709_2.as_ref(),
            CVAttachmentMode::ShouldPropagate,
        );
        buffer.set_attachment(
            kCVImageBufferTransferFunctionKey,
            kCVImageBufferTransferFunction_sRGB.as_ref(),
            CVAttachmentMode::ShouldPropagate,
        );
        buffer.set_attachment(
            kCVImageBufferYCbCrMatrixKey,
            kCVImageBufferYCbCrMatrix_ITU_R_709_2.as_ref(),
            CVAttachmentMode::ShouldPropagate,
        );
        Ok(buffer)
    }
}
unsafe fn copy_encoded(sample: &CMSampleBuffer) -> Result<EncodedH264, EncodeError> {
    // SAFETY: callback owns sample lifetime; retained format/block keep all
    // parameter pointers valid while copying. Size/count bounds precede slices.
    unsafe {
        let format = sample.format_description().ok_or(EncodeError::Bitstream)?;
        let data = sample.data_buffer().ok_or(EncodeError::Bitstream)?;
        let length = data.data_length();
        if length == 0 || length > MAX_ACCESS_UNIT {
            return Err(EncodeError::Bitstream);
        }
        let mut avcc = zeroize::Zeroizing::new(vec![0; length]);
        status(
            "copy encoded bytes",
            data.copy_data_bytes(
                0,
                length,
                NonNull::new(avcc.as_mut_ptr().cast()).ok_or(EncodeError::Bitstream)?,
            ),
        )?;
        let mut count = 0;
        let mut length_size = 0;
        status(
            "parameter metadata",
            CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                &format,
                0,
                ptr::null_mut(),
                ptr::null_mut(),
                &mut count,
                &mut length_size,
            ),
        )?;
        if count == 0 || count > 16 {
            return Err(EncodeError::Bitstream);
        }
        let mut parameters = Vec::with_capacity(count);
        for index in 0..count {
            let mut pointer = ptr::null();
            let mut size = 0;
            status(
                "parameter set",
                CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                    &format,
                    index,
                    &mut pointer,
                    &mut size,
                    ptr::null_mut(),
                    ptr::null_mut(),
                ),
            )?;
            if pointer.is_null() || size == 0 || size > 65535 {
                return Err(EncodeError::Bitstream);
            }
            let parameter = std::slice::from_raw_parts(pointer, size);
            parameters.push(if parameter[0] & 0x1f == 7 {
                crate::h264_low_delay::zero_reorder_baseline_sps(parameter)?
            } else {
                parameter.to_vec()
            });
        }
        avcc_to_annex_b(
            &avcc,
            length_size as usize,
            &parameters.iter().map(Vec::as_slice).collect::<Vec<_>>(),
        )
    }
}

#[cfg(test)]
mod recovery_tests {
    use super::*;

    #[test]
    fn callback_drop_preserves_real_hardware_session_and_timestamp_ordering() {
        let mut encoder = HardwareH264Encoder::new(320, 240, 30, 1_000_000)
            .expect("Mac QA requires a real hardware H264 encoder");
        let frame = CapturedFrame::bgra(320, 240, 1280, vec![80; 320 * 240 * 4]).unwrap();
        assert!(encoder.encode(&frame, 0, true).unwrap().keyframe);
        // Deterministically inject the asynchronous callback outcome through
        // the production completion path; do not claim an OS overload occurred.
        assert!(matches!(
            encoder.finish_output(Err(EncodeError::Dropped)),
            Err(EncodeError::Dropped)
        ));
        assert!(matches!(
            encoder.encode(&frame, 0, true),
            Err(EncodeError::Configuration)
        ));
        assert!(encoder.encode(&frame, 33_333, true).unwrap().keyframe);
        assert!(encoder.hardware_active().unwrap());
        assert!(matches!(
            encoder.finish_output(Err(EncodeError::Timeout)),
            Err(EncodeError::Timeout)
        ));
        assert!(matches!(
            encoder.encode(&frame, 66_666, true),
            Err(EncodeError::Failed)
        ));
        println!(
            "PASS: injected callback drop recovered on the same real VideoToolbox hardware session; duplicate timestamps and fatal timeout still refused"
        );
    }
}
