//! Owner-thread Windows Media Foundation hardware H.264 encoder (Intel QuickSync,
//! NVIDIA NVENC, AMD AMF) with automatic seamless fallback to OpenH264 (`desktop_h264`).
use crate::{
    damage::DamageTracker,
    desktop_h264::ScreenH264Encoder as SoftwareEncoder,
    h264::{EncodeError, EncodedH264, MAX_ACCESS_UNIT, annex_b_to_avcc},
    stream::CapturedFrame,
};
use std::{
    mem::ManuallyDrop,
    time::{Duration, Instant},
};
use windows::{
    Win32::{
        Foundation::RPC_E_CHANGED_MODE,
        Media::MediaFoundation::*,
        System::{
            Com::{COINIT_MULTITHREADED, CoInitializeEx, CoTaskMemFree, CoUninitialize},
            Variant::VARIANT,
        },
    },
    core::Interface,
};
use zeroize::Zeroizing;

/// Maximum wall-clock wait for an async hardware MFT to accept input or emit an encoded frame
/// before falling back seamlessly to OpenH264 (prevents any GPU driver stall or deadlock).
const MF_EVENT_WAIT_LIMIT: Duration = Duration::from_millis(200);
/// Maximum consecutive input frames an async MFT may buffer without emitting output before
/// falling back to OpenH264.
const MAX_EMPTY_ASYNC_FRAMES: u32 = 8;

fn pack_u64(hi: u32, lo: u32) -> u64 {
    (u64::from(hi) << 32) | u64::from(lo)
}

/// Fast BT.601 limited-range BGRA -> NV12 conversion (2x2 chroma subsampling)
/// into a preallocated tightly packed NV12 buffer (`width * height * 3 / 2`).
fn bgra_to_nv12(frame: &CapturedFrame, width: usize, height: usize, out: &mut [u8]) {
    let y_len = width * height;
    let (y_plane, uv_plane) = out.split_at_mut(y_len);
    let pixels = frame.pixels();
    let stride = frame.stride();
    let same_size = frame.width() == width && frame.height() == height;

    if same_size {
        for y in (0..height).step_by(2) {
            let row0 = &pixels[y * stride..y * stride + width * 4];
            let row1 = &pixels[(y + 1) * stride..(y + 1) * stride + width * 4];
            let (y_out0, y_out1) = y_plane[y * width..(y + 2) * width].split_at_mut(width);
            let uv_out = &mut uv_plane[(y / 2) * width..(y / 2 + 1) * width];
            for x in (0..width).step_by(2) {
                let i0 = x * 4;
                let i1 = i0 + 4;
                let (b00, g00, r00) = (i32::from(row0[i0]), i32::from(row0[i0 + 1]), i32::from(row0[i0 + 2]));
                let (b01, g01, r01) = (i32::from(row0[i1]), i32::from(row0[i1 + 1]), i32::from(row0[i1 + 2]));
                let (b10, g10, r10) = (i32::from(row1[i0]), i32::from(row1[i0 + 1]), i32::from(row1[i0 + 2]));
                let (b11, g11, r11) = (i32::from(row1[i1]), i32::from(row1[i1 + 1]), i32::from(row1[i1 + 2]));

                y_out0[x] = (((66 * r00 + 129 * g00 + 25 * b00 + 128) >> 8) + 16).clamp(16, 235) as u8;
                y_out0[x + 1] = (((66 * r01 + 129 * g01 + 25 * b01 + 128) >> 8) + 16).clamp(16, 235) as u8;
                y_out1[x] = (((66 * r10 + 129 * g10 + 25 * b10 + 128) >> 8) + 16).clamp(16, 235) as u8;
                y_out1[x + 1] = (((66 * r11 + 129 * g11 + 25 * b11 + 128) >> 8) + 16).clamp(16, 235) as u8;

                let r = (r00 + r01 + r10 + r11 + 2) >> 2;
                let g = (g00 + g01 + g10 + g11 + 2) >> 2;
                let b = (b00 + b01 + b10 + b11 + 2) >> 2;
                uv_out[x] = (((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128).clamp(16, 240) as u8;
                uv_out[x + 1] = (((112 * r - 94 * g - 18 * b + 128) >> 8) + 128).clamp(16, 240) as u8;
            }
        }
    } else {
        let (sw, sh) = (frame.width(), frame.height());
        for y in (0..height).step_by(2) {
            let sy0 = (y * sh / height).min(sh - 1) * stride;
            let sy1 = ((y + 1) * sh / height).min(sh - 1) * stride;
            let (y_out0, y_out1) = y_plane[y * width..(y + 2) * width].split_at_mut(width);
            let uv_out = &mut uv_plane[(y / 2) * width..(y / 2 + 1) * width];
            for x in (0..width).step_by(2) {
                let sx0 = (x * sw / width).min(sw - 1) * 4;
                let sx1 = ((x + 1) * sw / width).min(sw - 1) * 4;
                let (b00, g00, r00) = (i32::from(pixels[sy0 + sx0]), i32::from(pixels[sy0 + sx0 + 1]), i32::from(pixels[sy0 + sx0 + 2]));
                let (b01, g01, r01) = (i32::from(pixels[sy0 + sx1]), i32::from(pixels[sy0 + sx1 + 1]), i32::from(pixels[sy0 + sx1 + 2]));
                let (b10, g10, r10) = (i32::from(pixels[sy1 + sx0]), i32::from(pixels[sy1 + sx0 + 1]), i32::from(pixels[sy1 + sx0 + 2]));
                let (b11, g11, r11) = (i32::from(pixels[sy1 + sx1]), i32::from(pixels[sy1 + sx1 + 1]), i32::from(pixels[sy1 + sx1 + 2]));

                y_out0[x] = (((66 * r00 + 129 * g00 + 25 * b00 + 128) >> 8) + 16).clamp(16, 235) as u8;
                y_out0[x + 1] = (((66 * r01 + 129 * g01 + 25 * b01 + 128) >> 8) + 16).clamp(16, 235) as u8;
                y_out1[x] = (((66 * r10 + 129 * g10 + 25 * b10 + 128) >> 8) + 16).clamp(16, 235) as u8;
                y_out1[x + 1] = (((66 * r11 + 129 * g11 + 25 * b11 + 128) >> 8) + 16).clamp(16, 235) as u8;

                let r = (r00 + r01 + r10 + r11 + 2) >> 2;
                let g = (g00 + g01 + g10 + g11 + 2) >> 2;
                let b = (b00 + b01 + b10 + b11 + 2) >> 2;
                uv_out[x] = (((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128).clamp(16, 240) as u8;
                uv_out[x + 1] = (((112 * r - 94 * g - 18 * b + 128) >> 8) + 128).clamp(16, 240) as u8;
            }
        }
    }
}

struct MfGuard {
    com_initialized: bool,
    mf_started: bool,
}
impl Drop for MfGuard {
    fn drop(&mut self) {
        unsafe {
            if self.mf_started {
                let _ = MFShutdown();
            }
            if self.com_initialized {
                CoUninitialize();
            }
        }
    }
}

struct MfHardwareEncoder {
    _guard: MfGuard,
    transform: IMFTransform,
    codec_api: Option<ICodecAPI>,
    events: Option<IMFMediaEventGenerator>,
    width: usize,
    height: usize,
    frame_duration_100ns: i64,
    provides_samples: bool,
    min_output_bytes: u32,
    nv12: Zeroizing<Vec<u8>>,
    damage: DamageTracker,
    needs_keyframe: bool,
    priming: bool,
    pending_inputs: u32,
    consecutive_empty: u32,
}

fn apply_low_latency_codec_api(api: &ICodecAPI, fps: u32, bitrate: u32) {
    unsafe {
        let _ = api.SetValue(&CODECAPI_AVLowLatencyMode, &VARIANT::from(true));
        let _ = api.SetValue(
            &CODECAPI_AVEncCommonRateControlMode,
            &VARIANT::from(eAVEncCommonRateControlMode_LowDelayVBR.0 as u32),
        );
        let _ = api.SetValue(&CODECAPI_AVEncCommonMeanBitRate, &VARIANT::from(bitrate));
        let _ = api.SetValue(&CODECAPI_AVEncMPVDefaultBPictureCount, &VARIANT::from(0u32));
        let _ = api.SetValue(&CODECAPI_AVEncMPVGOPSize, &VARIANT::from(fps * 10));
        let _ = api.SetValue(&CODECAPI_AVEncCommonQualityVsSpeed, &VARIANT::from(0u32));
    }
}

impl MfHardwareEncoder {
    fn try_new(width: usize, height: usize, fps: u32, bitrate: u32) -> Result<Self, EncodeError> {
        if !(16..=1920).contains(&width)
            || !(16..=1088).contains(&height)
            || !width.is_multiple_of(2)
            || !height.is_multiple_of(2)
            || !(1..=60).contains(&fps)
            || !(100_000..=12_000_000).contains(&bitrate)
        {
            return Err(EncodeError::Configuration);
        }
        unsafe {
            let hr = CoInitializeEx(None, COINIT_MULTITHREADED);
            let com_initialized = hr.is_ok();
            if hr.is_err() && hr != RPC_E_CHANGED_MODE {
                return Err(EncodeError::HardwareUnavailable);
            }
            if MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET).is_err() {
                if com_initialized {
                    CoUninitialize();
                }
                return Err(EncodeError::HardwareUnavailable);
            }
            let guard = MfGuard {
                com_initialized,
                mf_started: true,
            };

            let input_reg = MFT_REGISTER_TYPE_INFO {
                guidMajorType: MFMediaType_Video,
                guidSubtype: MFVideoFormat_NV12,
            };
            let output_reg = MFT_REGISTER_TYPE_INFO {
                guidMajorType: MFMediaType_Video,
                guidSubtype: MFVideoFormat_H264,
            };
            let mut raw_activates: *mut Option<IMFActivate> = std::ptr::null_mut();
            let mut count = 0u32;
            MFTEnumEx(
                MFT_CATEGORY_VIDEO_ENCODER,
                MFT_ENUM_FLAG_HARDWARE | MFT_ENUM_FLAG_SORTANDFILTER,
                Some(&input_reg),
                Some(&output_reg),
                &mut raw_activates,
                &mut count,
            )
            .map_err(|_| EncodeError::HardwareUnavailable)?;
            if count == 0 || raw_activates.is_null() {
                if !raw_activates.is_null() {
                    CoTaskMemFree(Some(raw_activates.cast()));
                }
                return Err(EncodeError::HardwareUnavailable);
            }

            let activates_slice = std::slice::from_raw_parts_mut(raw_activates, count as usize);
            let mut chosen: Option<IMFTransform> = None;
            for slot in activates_slice.iter_mut() {
                if let Some(activate) = slot.take()
                    && chosen.is_none()
                    && let Ok(transform) = activate.ActivateObject::<IMFTransform>()
                {
                    chosen = Some(transform);
                }
            }
            CoTaskMemFree(Some(raw_activates.cast()));

            let transform = chosen.ok_or(EncodeError::HardwareUnavailable)?;

            // Unlock async hardware MFTs (Intel QSV, NVENC, AMD AMF are async MFTs).
            let mut events: Option<IMFMediaEventGenerator> = None;
            if let Ok(attrs) = transform.GetAttributes()
                && attrs.GetUINT32(&MF_TRANSFORM_ASYNC).unwrap_or(0) != 0
            {
                attrs
                    .SetUINT32(&MF_TRANSFORM_ASYNC_UNLOCK, 1)
                    .map_err(|_| EncodeError::HardwareUnavailable)?;
                events = transform.cast::<IMFMediaEventGenerator>().ok();
                if events.is_none() {
                    return Err(EncodeError::HardwareUnavailable);
                }
            }

            // Set low-latency ICodecAPI properties BEFORE SetOutputType/SetInputType:
            // Intel QuickSync MFT reads AVLowLatencyMode during type negotiation to
            // configure a 0-B-frame, 1-in-1-out pipeline instead of multi-frame lookahead.
            let codec_api = transform.cast::<ICodecAPI>().ok();
            if let Some(api) = &codec_api {
                apply_low_latency_codec_api(api, fps, bitrate);
            }

            // Configure H.264 output type first (required by Media Foundation encoders).
            let out_type = MFCreateMediaType().map_err(|_| EncodeError::HardwareUnavailable)?;
            out_type
                .SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            out_type
                .SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_H264)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            out_type
                .SetUINT32(&MF_MT_AVG_BITRATE, bitrate)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            out_type
                .SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            out_type
                .SetUINT64(&MF_MT_FRAME_SIZE, pack_u64(width as u32, height as u32))
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            out_type
                .SetUINT64(&MF_MT_FRAME_RATE, pack_u64(fps, 1))
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            let _ = out_type.SetUINT32(&MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Base.0 as u32);
            transform
                .SetOutputType(0, &out_type, 0)
                .map_err(|_| EncodeError::HardwareUnavailable)?;

            // Configure NV12 input type.
            let in_type = MFCreateMediaType().map_err(|_| EncodeError::HardwareUnavailable)?;
            in_type
                .SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            in_type
                .SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_NV12)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            in_type
                .SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            in_type
                .SetUINT64(&MF_MT_FRAME_SIZE, pack_u64(width as u32, height as u32))
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            in_type
                .SetUINT64(&MF_MT_FRAME_RATE, pack_u64(fps, 1))
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            let _ = in_type.SetUINT32(&MF_MT_DEFAULT_STRIDE, width as u32);
            transform
                .SetInputType(0, &in_type, 0)
                .map_err(|_| EncodeError::HardwareUnavailable)?;

            // Re-apply ICodecAPI after media types for drivers (e.g. NVENC/AMF) that only
            // accept rate-control properties once stream types are configured.
            if let Some(api) = &codec_api {
                apply_low_latency_codec_api(api, fps, bitrate);
            }

            let out_info = transform
                .GetOutputStreamInfo(0)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            let provides_samples = (out_info.dwFlags
                & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 as u32
                    | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES.0 as u32))
                != 0;
            let min_output_bytes = out_info.cbSize.max((width * height) as u32);

            transform
                .ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0)
                .map_err(|_| EncodeError::HardwareUnavailable)?;
            transform
                .ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0)
                .map_err(|_| EncodeError::HardwareUnavailable)?;

            Ok(Self {
                _guard: guard,
                transform,
                codec_api,
                events,
                width,
                height,
                frame_duration_100ns: 10_000_000 / i64::from(fps),
                provides_samples,
                min_output_bytes,
                nv12: Zeroizing::new(vec![0u8; width * height * 3 / 2]),
                damage: DamageTracker::new(32).expect("fixed valid damage tile size"),
                needs_keyframe: true,
                priming: false,
                pending_inputs: 0,
                consecutive_empty: 0,
            })
        }
    }

    fn create_input_sample(&self, timestamp_us: u64) -> Result<IMFSample, EncodeError> {
        unsafe {
            let sample = MFCreateSample().map_err(|_| EncodeError::Failed)?;
            let len = self.nv12.len() as u32;
            let buffer = MFCreateMemoryBuffer(len).map_err(|_| EncodeError::Failed)?;
            let mut ptr: *mut u8 = std::ptr::null_mut();
            buffer
                .Lock(&mut ptr, None, None)
                .map_err(|_| EncodeError::Failed)?;
            std::ptr::copy_nonoverlapping(self.nv12.as_ptr(), ptr, self.nv12.len());
            buffer.Unlock().map_err(|_| EncodeError::Failed)?;
            buffer
                .SetCurrentLength(len)
                .map_err(|_| EncodeError::Failed)?;
            sample
                .AddBuffer(&buffer)
                .map_err(|_| EncodeError::Failed)?;
            sample
                .SetSampleTime((timestamp_us * 10) as i64)
                .map_err(|_| EncodeError::Failed)?;
            sample
                .SetSampleDuration(self.frame_duration_100ns)
                .map_err(|_| EncodeError::Failed)?;
            Ok(sample)
        }
    }

    fn pull_output(&mut self) -> Result<Option<Zeroizing<Vec<u8>>>, EncodeError> {
        unsafe {
            for attempt in 0..2 {
                let prealloc = if self.provides_samples {
                    None
                } else {
                    let sample = MFCreateSample().map_err(|_| EncodeError::Failed)?;
                    let buffer = MFCreateMemoryBuffer(self.min_output_bytes)
                        .map_err(|_| EncodeError::Failed)?;
                    sample
                        .AddBuffer(&buffer)
                        .map_err(|_| EncodeError::Failed)?;
                    Some(sample)
                };
                let mut output_buffers = [MFT_OUTPUT_DATA_BUFFER {
                    dwStreamID: 0,
                    pSample: ManuallyDrop::new(prealloc),
                    dwStatus: 0,
                    pEvents: ManuallyDrop::new(None),
                }];
                let mut status = 0u32;
                let result = self
                    .transform
                    .ProcessOutput(0, &mut output_buffers, &mut status);
                // Take ownership directly without cloning so COM refcounts never leak.
                let [buf] = output_buffers;
                let sample = ManuallyDrop::into_inner(buf.pSample);
                let _events = ManuallyDrop::into_inner(buf.pEvents);

                match result {
                    Ok(()) => {
                        let Some(sample) = sample else {
                            return Ok(None);
                        };
                        let contiguous = sample
                            .ConvertToContiguousBuffer()
                            .map_err(|_| EncodeError::Failed)?;
                        let mut ptr: *mut u8 = std::ptr::null_mut();
                        let mut current_len = 0u32;
                        contiguous
                            .Lock(&mut ptr, None, Some(&mut current_len))
                            .map_err(|_| EncodeError::Failed)?;
                        if current_len == 0
                            || current_len as usize > MAX_ACCESS_UNIT
                            || ptr.is_null()
                        {
                            let _ = contiguous.Unlock();
                            return Ok(None);
                        }
                        let slice = std::slice::from_raw_parts(ptr, current_len as usize);
                        let bytes = Zeroizing::new(slice.to_vec());
                        contiguous.Unlock().map_err(|_| EncodeError::Failed)?;
                        return Ok(Some(bytes));
                    }
                    Err(error) if error.code() == MF_E_TRANSFORM_NEED_MORE_INPUT => {
                        return Ok(None);
                    }
                    Err(error) if error.code() == MF_E_TRANSFORM_STREAM_CHANGE && attempt == 0 => {
                        // Refresh output media type and stream info after format negotiation, then retry.
                        if let Ok(available) = self.transform.GetOutputAvailableType(0, 0) {
                            let _ = self.transform.SetOutputType(0, &available, 0);
                        }
                        if let Ok(out_info) = self.transform.GetOutputStreamInfo(0) {
                            self.provides_samples = (out_info.dwFlags
                                & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 as u32
                                    | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES.0 as u32))
                                != 0;
                            self.min_output_bytes =
                                out_info.cbSize.max((self.width * self.height) as u32);
                        }
                        continue;
                    }
                    Err(_) => return Err(EncodeError::Failed),
                }
            }
            Ok(None)
        }
    }

    fn encode(
        &mut self,
        frame: &CapturedFrame,
        timestamp_us: u64,
        force: bool,
    ) -> Result<Option<EncodedH264>, EncodeError> {
        let force = force || self.needs_keyframe;
        if !self.damage.has_changed(&frame.frame()) && !force && !self.priming {
            return Ok(None);
        }
        bgra_to_nv12(frame, self.width, self.height, &mut self.nv12);
        let sample = self.create_input_sample(timestamp_us)?;

        if force
            && let Some(api) = &self.codec_api
        {
            unsafe {
                let _ = api.SetValue(&CODECAPI_AVEncVideoForceKeyFrame, &VARIANT::from(1u32));
            }
        }

        let annex_b = if let Some(events) = self.events.clone() {
            // Asynchronous hardware MFT (Intel QSV / NVENC / AMD AMF).
            // NEVER call blocking GetEvent(0): if the MFT requests another input frame before
            // emitting output (pipeline priming), blocking GetEvent(0) deadlocks permanently.
            let mut fed = false;
            if self.pending_inputs > 0 {
                self.pending_inputs -= 1;
                unsafe {
                    self.transform
                        .ProcessInput(0, &sample, 0)
                        .map_err(|_| EncodeError::Failed)?;
                }
                fed = true;
            }
            let mut produced: Option<Zeroizing<Vec<u8>>> = None;
            let mut mft_requested_more_input = false;
            let deadline = Instant::now() + MF_EVENT_WAIT_LIMIT;
            loop {
                match unsafe { events.GetEvent(MF_EVENT_FLAG_NO_WAIT) } {
                    Ok(event) => {
                        let kind = unsafe { event.GetType().map_err(|_| EncodeError::Failed)? };
                        if kind == METransformNeedInput.0 as u32 {
                            if !fed {
                                unsafe {
                                    self.transform
                                        .ProcessInput(0, &sample, 0)
                                        .map_err(|_| EncodeError::Failed)?;
                                }
                                fed = true;
                            } else {
                                self.pending_inputs += 1;
                                mft_requested_more_input = true;
                                if produced.is_some() {
                                    break;
                                }
                            }
                        } else if kind == METransformHaveOutput.0 as u32 {
                            if let Some(bytes) = self.pull_output()? {
                                produced = Some(bytes);
                                if fed {
                                    break;
                                }
                            }
                        } else if kind == MEError.0 as u32 {
                            return Err(EncodeError::Failed);
                        }
                    }
                    Err(error) if error.code() == MF_E_NO_EVENTS_AVAILABLE => {
                        if produced.is_some() && fed {
                            break;
                        }
                        // MFT already accepted our frame AND asked for another input frame,
                        // and no output event is queued yet: return Ok(None) immediately so
                        // the caller feeds the next frame instead of stalling.
                        if fed && mft_requested_more_input {
                            break;
                        }
                        if Instant::now() >= deadline {
                            return Err(EncodeError::Failed);
                        }
                        std::thread::sleep(Duration::from_millis(1));
                    }
                    Err(_) => return Err(EncodeError::Failed),
                }
            }
            if !fed {
                return Err(EncodeError::Failed);
            }
            produced
        } else {
            // Synchronous hardware MFT.
            unsafe {
                self.transform
                    .ProcessInput(0, &sample, 0)
                    .map_err(|_| EncodeError::Failed)?;
            }
            self.pull_output()?
        };

        let Some(annex_b) = annex_b else {
            self.priming = true;
            self.consecutive_empty += 1;
            if self.consecutive_empty >= MAX_EMPTY_ASYNC_FRAMES {
                return Err(EncodeError::Failed);
            }
            return Ok(None);
        };
        self.priming = false;
        self.consecutive_empty = 0;
        let parsed = annex_b_to_avcc(&annex_b)?;
        self.needs_keyframe =
            force && !(parsed.keyframe && parsed.sps.is_some() && parsed.pps.is_some());
        Ok(Some(EncodedH264 {
            keyframe: parsed.keyframe,
            annex_b,
        }))
    }
}

/// Windows screen encoder: tries Media Foundation hardware H.264 first (Intel QuickSync,
/// NVIDIA NVENC, AMD AMF) and seamlessly falls back to OpenH264 on VMs or if a driver fails.
pub struct WindowsScreenH264Encoder {
    backend: Backend,
    width: usize,
    height: usize,
    fps: u32,
    bitrate: u32,
}

enum Backend {
    Hardware(Box<MfHardwareEncoder>),
    Software(Box<SoftwareEncoder>),
}

impl WindowsScreenH264Encoder {
    pub fn new(width: usize, height: usize, fps: u32, bitrate: u32) -> Result<Self, EncodeError> {
        let disable_hw = std::env::var("MIXEL_NO_HW_ENC").is_ok_and(|v| v == "1");
        let backend = if !disable_hw
            && let Ok(hw) = MfHardwareEncoder::try_new(width, height, fps, bitrate)
        {
            Backend::Hardware(Box::new(hw))
        } else {
            // Software OpenH264 keeps a 30 FPS bit budget so WAN bitrates don't starve it.
            Backend::Software(Box::new(SoftwareEncoder::new(
                width,
                height,
                fps.min(30),
                bitrate,
            )?))
        };
        Ok(Self {
            backend,
            width,
            height,
            fps,
            bitrate,
        })
    }

    pub fn needs_retry(&self) -> bool {
        match &self.backend {
            Backend::Hardware(hw) => hw.needs_keyframe,
            Backend::Software(sw) => sw.needs_retry(),
        }
    }

    pub fn left_out(&self) -> bool {
        match &self.backend {
            Backend::Hardware(hw) => hw.priming,
            Backend::Software(sw) => sw.left_out(),
        }
    }

    /// Updates the target bitrate in-place via Media Foundation `ICodecAPI` without tearing down
    /// the hardware MFT or forcing a large IDR keyframe. Returns `true` if applied in-place.
    pub fn set_bitrate(&mut self, bitrate: u32) -> bool {
        if !(100_000..=12_000_000).contains(&bitrate) {
            return false;
        }
        self.bitrate = bitrate;
        match &mut self.backend {
            Backend::Hardware(hw) => {
                if let Some(api) = &hw.codec_api {
                    unsafe {
                        api.SetValue(&CODECAPI_AVEncCommonMeanBitRate, &VARIANT::from(bitrate))
                            .is_ok()
                    }
                } else {
                    false
                }
            }
            Backend::Software(_) => false,
        }
    }

    pub fn encode(
        &mut self,
        frame: &CapturedFrame,
        timestamp_us: u64,
        force: bool,
    ) -> Result<Option<EncodedH264>, EncodeError> {
        match &mut self.backend {
            Backend::Hardware(hw) => match hw.encode(frame, timestamp_us, force) {
                Ok(out) => Ok(out),
                Err(_) => {
                    // Seamless runtime fallback to OpenH264 without dropping the session.
                    let mut sw = SoftwareEncoder::new(
                        self.width,
                        self.height,
                        self.fps.min(30),
                        self.bitrate,
                    )?;
                    let out = sw.encode(frame, timestamp_us, true)?;
                    self.backend = Backend::Software(Box::new(sw));
                    Ok(out)
                }
            },
            Backend::Software(sw) => sw.encode(frame, timestamp_us, force),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::desktop_h264::H264Decoder;

    #[test]
    fn windows_screen_encoder_encodes_and_decodes_with_automatic_backend() {
        let (width, height) = (320usize, 240usize);
        let mut encoder = WindowsScreenH264Encoder::new(width, height, 60, 1_500_000).unwrap();
        let mut decoder = H264Decoder::new(width, height).unwrap();
        let mut pixels = vec![40u8; width * height * 4];
        for i in 0..(width * height) {
            pixels[i * 4] = (i % 251) as u8;
            pixels[i * 4 + 1] = ((i / width) % 251) as u8;
            pixels[i * 4 + 2] = 180;
            pixels[i * 4 + 3] = 255;
        }
        let frame = CapturedFrame::bgra(width, height, width * 4, pixels).unwrap();
        let encoded = encoder
            .encode(&frame, 33_000, true)
            .unwrap()
            .expect("keyframe emitted");
        assert!(encoded.keyframe);
        let decoded = decoder
            .decode(&encoded.annex_b, 33_000)
            .unwrap()
            .expect("decoded frame");
        assert_eq!((decoded.width(), decoded.height()), (width, height));
        // Unchanged frame is suppressed by damage tracking.
        assert!(encoder.encode(&frame, 66_000, false).unwrap().is_none());
    }
}
