//! Bounded serial VideoToolbox H264 decoding. Native codec and pixel objects
//! remain on their owner thread; only copied, zeroizing BGRA leaves callbacks.
use crate::{h264::annex_b_to_avcc, stream::CapturedFrame};
use block2::RcBlock;
use objc2_core_foundation::{CFBoolean, CFDictionary, CFNumber, CFRetained, CFType};
use objc2_core_media::*;
use objc2_core_video::*;
use objc2_video_toolbox::*;
use std::{
    ptr::{self, NonNull},
    sync::mpsc,
    time::Duration,
};
use zeroize::Zeroizing;

#[derive(Debug, thiserror::Error)]
pub enum DecodeError {
    #[error("invalid or unsupported H264 access unit")]
    Bitstream,
    #[error("decoded dimensions exceed the negotiated limit")]
    Dimensions,
    #[error("VideoToolbox decoder {operation} failed ({status})")]
    Native {
        operation: &'static str,
        status: i32,
    },
    #[error("native video decoding timed out")]
    Timeout,
    #[error("native decoder failed and must be recreated")]
    Failed,
    #[error("native decoded pixel storage is invalid")]
    Pixels,
}
fn status(operation: &'static str, code: i32) -> Result<(), DecodeError> {
    if code == 0 {
        Ok(())
    } else {
        Err(DecodeError::Native {
            operation,
            status: code,
        })
    }
}
pub struct H264Decoder {
    session: Option<CFRetained<VTDecompressionSession>>,
    format: Option<CFRetained<CMFormatDescription>>,
    sps: Zeroizing<Vec<u8>>,
    pps: Zeroizing<Vec<u8>>,
    max_width: usize,
    max_height: usize,
    width: usize,
    height: usize,
    last_timestamp: Option<u64>,
    failed: bool,
    hardware: bool,
}
impl H264Decoder {
    pub fn new(max_width: usize, max_height: usize) -> Result<Self, DecodeError> {
        if max_width < 2
            || max_height < 2
            || max_width > 8192
            || max_height > 8192
            || max_width
                .checked_mul(max_height)
                .is_none_or(|pixels| pixels > 16_777_216)
        {
            return Err(DecodeError::Dimensions);
        }
        Ok(Self {
            session: None,
            format: None,
            sps: Zeroizing::new(Vec::new()),
            pps: Zeroizing::new(Vec::new()),
            max_width,
            max_height,
            width: 0,
            height: 0,
            last_timestamp: None,
            failed: false,
            hardware: false,
        })
    }
    pub fn hardware_active(&self) -> bool {
        self.hardware
    }
    fn invalidate(&mut self) {
        if let Some(session) = self.session.take() {
            unsafe {
                session.invalidate();
            }
        }
        self.format = None;
    }
    fn configure(&mut self) -> Result<(), DecodeError> {
        if self.sps.len() < 4 || self.pps.is_empty() || self.sps[1] != 66 {
            return Err(DecodeError::Bitstream);
        }
        // SAFETY: two nonempty parameter vectors remain live during creation;
        // CoreMedia copies parameter sets into its owned format description.
        let format = unsafe {
            let mut pointers = [NonNull::from(&self.sps[0]), NonNull::from(&self.pps[0])];
            let mut sizes = [self.sps.len(), self.pps.len()];
            let mut output = ptr::null();
            status(
                "format",
                CMVideoFormatDescriptionCreateFromH264ParameterSets(
                    None,
                    2,
                    NonNull::new(pointers.as_mut_ptr()).ok_or(DecodeError::Bitstream)?,
                    NonNull::new(sizes.as_mut_ptr()).ok_or(DecodeError::Bitstream)?,
                    4,
                    NonNull::from(&mut output),
                ),
            )?;
            CFRetained::from_raw(NonNull::new(output.cast_mut()).ok_or(DecodeError::Bitstream)?)
        };
        let dimensions = unsafe { CMVideoFormatDescriptionGetDimensions(&format) };
        if dimensions.width < 2
            || dimensions.height < 2
            || dimensions.width as usize > self.max_width
            || dimensions.height as usize > self.max_height
        {
            return Err(DecodeError::Dimensions);
        }
        self.invalidate();
        self.width = dimensions.width as usize;
        self.height = dimensions.height as usize;
        // SAFETY: documented CF attribute value types; output storage receives
        // an owned session. No callback/refcon is registered at construction.
        let session = unsafe {
            let specification = CFDictionary::from_slices(
                &[kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder],
                &[CFBoolean::new(true)],
            );
            let pixel = CFNumber::new_i64(kCVPixelFormatType_32BGRA.into());
            let width = CFNumber::new_i64(dimensions.width.into());
            let height = CFNumber::new_i64(dimensions.height.into());
            let attributes = CFDictionary::from_slices(
                &[
                    kCVPixelBufferPixelFormatTypeKey,
                    kCVPixelBufferWidthKey,
                    kCVPixelBufferHeightKey,
                ],
                &[&*pixel, &*width, &*height],
            );
            let mut output = ptr::null_mut();
            status(
                "create",
                VTDecompressionSession::create(
                    None,
                    &format,
                    Some(specification.as_opaque()),
                    Some(attributes.as_opaque()),
                    ptr::null(),
                    NonNull::from(&mut output),
                ),
            )?;
            CFRetained::from_raw(NonNull::new(output).ok_or(DecodeError::Failed)?)
        };
        self.session = Some(session);
        self.format = Some(format);
        let session = self.session.as_ref().ok_or(DecodeError::Failed)?;
        unsafe {
            status(
                "realtime",
                VTSessionSetProperty(
                    session.as_ref(),
                    kVTDecompressionPropertyKey_RealTime,
                    Some(CFBoolean::new(true).as_ref()),
                ),
            )?;
            let mut property: *const CFType = ptr::null();
            status(
                "hardware query",
                VTSessionCopyProperty(
                    session.as_ref(),
                    kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
                    None,
                    (&mut property as *mut *const CFType).cast(),
                ),
            )?;
            let property =
                CFRetained::from_raw(NonNull::new(property.cast_mut()).ok_or(DecodeError::Failed)?);
            self.hardware = property
                .downcast_ref::<CFBoolean>()
                .is_some_and(CFBoolean::as_bool);
        }
        Ok(())
    }
    pub fn decode(
        &mut self,
        annex_b: &[u8],
        timestamp_us: u64,
    ) -> Result<Option<CapturedFrame>, DecodeError> {
        if self.failed {
            return Err(DecodeError::Failed);
        }
        let result = self.decode_inner(annex_b, timestamp_us);
        if result.is_err() {
            self.failed = true;
            self.invalidate();
        }
        result
    }
    fn decode_inner(
        &mut self,
        annex_b: &[u8],
        timestamp_us: u64,
    ) -> Result<Option<CapturedFrame>, DecodeError> {
        if timestamp_us > i64::MAX as u64
            || self.last_timestamp.is_some_and(|last| timestamp_us <= last)
        {
            return Err(DecodeError::Bitstream);
        }
        let unit = annex_b_to_avcc(annex_b).map_err(|_| DecodeError::Bitstream)?;
        let changed = unit.sps.is_some_and(|value| value != self.sps.as_slice())
            || unit.pps.is_some_and(|value| value != self.pps.as_slice());
        if (changed || self.session.is_none()) && !unit.keyframe {
            return Err(DecodeError::Bitstream);
        }
        if let Some(sps) = unit.sps {
            self.sps = Zeroizing::new(sps.to_vec());
        }
        if let Some(pps) = unit.pps {
            self.pps = Zeroizing::new(pps.to_vec());
        }
        if changed || self.session.is_none() {
            self.configure()?;
        }
        let format = self.format.as_ref().ok_or(DecodeError::Failed)?;
        let sample = sample_buffer(&unit.bytes, format, timestamp_us)?;
        let (tx, rx) = mpsc::sync_channel(1);
        let width = self.width;
        let height = self.height;
        let block = RcBlock::new(
            move |code: i32,
                  flags: VTDecodeInfoFlags,
                  image: *mut CVImageBuffer,
                  _pts: CMTime,
                  _duration: CMTime| {
                let result = if code != 0 {
                    Err(DecodeError::Native {
                        operation: "output",
                        status: code,
                    })
                } else if flags.contains(VTDecodeInfoFlags::FrameDropped) {
                    Ok(None)
                } else if image.is_null() {
                    Err(DecodeError::Pixels)
                } else {
                    // SAFETY: VT owns this live image throughout the callback. Its
                    // dimensions are checked before a read-locked owned copy.
                    unsafe {
                        let image = &*image;
                        if CVPixelBufferGetWidth(image) != width
                            || CVPixelBufferGetHeight(image) != height
                        {
                            Err(DecodeError::Dimensions)
                        } else {
                            crate::apple_pixels::copy_pixel_buffer(image)
                                .map(Some)
                                .map_err(|_| DecodeError::Pixels)
                        }
                    }
                };
                let _ = tx.try_send(result);
            },
        );
        let session = self.session.as_ref().ok_or(DecodeError::Failed)?;
        // SAFETY: sample storage is owned by CoreMedia; VT copies/retains it and
        // the completion block for asynchronous use. No borrowed image escapes.
        unsafe {
            status(
                "decode",
                session.decode_frame_with_output_handler(
                    &sample,
                    VTDecodeFrameFlags::empty(),
                    ptr::null_mut(),
                    RcBlock::as_ptr(&block),
                ),
            )?;
        }
        self.last_timestamp = Some(timestamp_us);
        rx.recv_timeout(Duration::from_secs(2))
            .map_err(|_| DecodeError::Timeout)?
    }
}
impl Drop for H264Decoder {
    fn drop(&mut self) {
        self.invalidate();
    }
}
fn sample_buffer(
    bytes: &[u8],
    format: &CMFormatDescription,
    timestamp: u64,
) -> Result<CFRetained<CMSampleBuffer>, DecodeError> {
    if bytes.is_empty() {
        return Err(DecodeError::Bitstream);
    }
    // SAFETY: CoreMedia allocates its own backing block. All supplied buffers,
    // lengths, timing entries and output pointers remain valid for each call.
    unsafe {
        let mut output = ptr::null_mut();
        status(
            "block",
            CMBlockBuffer::create_with_memory_block(
                None,
                ptr::null_mut(),
                bytes.len(),
                None,
                ptr::null(),
                0,
                bytes.len(),
                kCMBlockBufferAssureMemoryNowFlag,
                NonNull::from(&mut output),
            ),
        )?;
        let block = CFRetained::from_raw(NonNull::new(output).ok_or(DecodeError::Failed)?);
        status(
            "copy",
            CMBlockBuffer::replace_data_bytes(
                NonNull::new(bytes.as_ptr().cast_mut().cast()).ok_or(DecodeError::Bitstream)?,
                &block,
                0,
                bytes.len(),
            ),
        )?;
        let timing = CMSampleTimingInfo {
            duration: CMTime::new(1, 30),
            presentationTimeStamp: CMTime::new(timestamp as i64, 1_000_000),
            decodeTimeStamp: CMTime::new(timestamp as i64, 1_000_000),
        };
        let size = bytes.len();
        let mut output = ptr::null_mut();
        status(
            "sample",
            CMSampleBuffer::create_ready(
                None,
                Some(&block),
                Some(format),
                1,
                1,
                &timing,
                1,
                &size,
                NonNull::from(&mut output),
            ),
        )?;
        Ok(CFRetained::from_raw(
            NonNull::new(output).ok_or(DecodeError::Failed)?,
        ))
    }
}
