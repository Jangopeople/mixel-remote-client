//! Owner-thread Windows Media Foundation H.264 decoder (`CLSID_CMSH264DecoderMFT`)
//! supporting Baseline, Main, and High Profile (CABAC) up to 4K with low-latency mode
//! and automatic fallback to OpenH264 (`desktop_h264::H264Decoder`) when MF is unavailable.
use crate::{
    desktop_h264::{DecodeError, H264Decoder as SoftwareDecoder},
    h264::MAX_ACCESS_UNIT,
    stream::CapturedFrame,
};
use std::mem::ManuallyDrop;
use windows::{
    Win32::{
        Foundation::RPC_E_CHANGED_MODE,
        Media::MediaFoundation::*,
        System::Com::{CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, CoUninitialize},
    },
    core::GUID,
};

/// Microsoft inbox H.264 Video Decoder MFT (`CLSID_CMSH264DecoderMFT`).
const CLSID_MS_H264_DECODER_MFT: GUID = GUID::from_u128(0x62ce7e72_4c71_4d20_b15d_452831a87d9d);

fn unpack_u64(v: u64) -> (usize, usize) {
    ((v >> 32) as usize, (v & 0xFFFF_FFFF) as usize)
}

/// Fast NV12 -> BGRA conversion (BT.601 limited range matching our encoders) with opaque alpha.
fn nv12_to_bgra(
    nv12: &[u8],
    width: usize,
    height: usize,
    stride: usize,
    uv_offset: usize,
) -> Result<CapturedFrame, DecodeError> {
    let needed_y = stride.checked_mul(height).ok_or(DecodeError::Dimensions)?;
    let uv_rows = height / 2;
    let needed_uv = stride.checked_mul(uv_rows).ok_or(DecodeError::Dimensions)?;
    if uv_offset < needed_y || uv_offset.saturating_add(needed_uv) > nv12.len() {
        return Err(DecodeError::Codec);
    }
    let out_stride = width.checked_mul(4).ok_or(DecodeError::Dimensions)?;
    let out_len = out_stride.checked_mul(height).ok_or(DecodeError::Dimensions)?;
    let mut bgra = vec![0u8; out_len];
    let y_plane = &nv12[..needed_y];
    let uv_plane = &nv12[uv_offset..uv_offset + needed_uv];

    for y in 0..height {
        let y_row = &y_plane[y * stride..y * stride + width];
        let uv_row = &uv_plane[(y / 2) * stride..(y / 2) * stride + width];
        let dst_row = &mut bgra[y * out_stride..(y + 1) * out_stride];
        for x in 0..width {
            let y_val = (i32::from(y_row[x]) - 16).max(0);
            let uv_idx = (x & !1).min(width.saturating_sub(2));
            let u = i32::from(uv_row[uv_idx]) - 128;
            let v = i32::from(uv_row[uv_idx + 1]) - 128;
            let c = 298 * y_val;
            let r = ((c + 409 * v + 128) >> 8).clamp(0, 255) as u8;
            let g = ((c - 100 * u - 208 * v + 128) >> 8).clamp(0, 255) as u8;
            let b = ((c + 516 * u + 128) >> 8).clamp(0, 255) as u8;
            let o = x * 4;
            dst_row[o] = b;
            dst_row[o + 1] = g;
            dst_row[o + 2] = r;
            dst_row[o + 3] = 255;
        }
    }
    CapturedFrame::bgra(width, height, out_stride, bgra)
        .map_err(|_| DecodeError::Dimensions)
}

struct MfH264Decoder {
    mft: IMFTransform,
    com_owned: bool,
    mf_started: bool,
    limits: (usize, usize),
    width: usize,
    height: usize,
    stride: usize,
    output_buf_size: u32,
    sample_buf: Option<IMFSample>,
    has_keyframe: bool,
}

impl MfH264Decoder {
    fn new(max_width: usize, max_height: usize) -> Result<Self, String> {
        unsafe {
            let hr = CoInitializeEx(None, COINIT_MULTITHREADED);
            let com_owned = if hr.is_ok() {
                true
            } else if hr == RPC_E_CHANGED_MODE {
                false
            } else {
                return Err(format!("CoInitializeEx failed: {hr:?}"));
            };
            if let Err(e) = MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET) {
                if com_owned {
                    CoUninitialize();
                }
                return Err(format!("MFStartup failed: {e}"));
            }
            let mft: IMFTransform = match CoCreateInstance(&CLSID_MS_H264_DECODER_MFT, None, CLSCTX_INPROC_SERVER) {
                Ok(m) => m,
                Err(e) => {
                    let _ = MFShutdown();
                    if com_owned {
                        CoUninitialize();
                    }
                    return Err(format!("CoCreateInstance(CMSH264DecoderMFT) failed: {e}"));
                }
            };
            // Enable low-latency decoding so B-frame/reorder buffering never adds 1-4 frames of lag.
            if let Ok(attrs) = mft.GetAttributes() {
                let _ = attrs.SetUINT32(&CODECAPI_AVLowLatencyMode, 1);
            }
            let in_type = MFCreateMediaType().map_err(|e| format!("MFCreateMediaType: {e}"))?;
            in_type.SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video).map_err(|e| e.to_string())?;
            in_type.SetGUID(&MF_MT_SUBTYPE, &MFVideoFormat_H264).map_err(|e| e.to_string())?;
            mft.SetInputType(0, &in_type, 0).map_err(|e| format!("SetInputType: {e}"))?;

            let mut decoder = Self {
                mft,
                com_owned,
                mf_started: true,
                limits: (max_width, max_height),
                width: 0,
                height: 0,
                stride: 0,
                output_buf_size: 0,
                sample_buf: None,
                has_keyframe: false,
            };
            decoder.select_nv12_output()?;
            let _ = decoder.mft.ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
            let _ = decoder.mft.ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
            Ok(decoder)
        }
    }

    unsafe fn select_nv12_output(&mut self) -> Result<(), String> {
        let mut idx = 0u32;
        loop {
            let out_type = match unsafe { self.mft.GetOutputAvailableType(0, idx) } {
                Ok(t) => t,
                Err(_) => return Err("MF H.264 decoder did not offer NV12 output".into()),
            };
            if let Ok(subtype) = unsafe { out_type.GetGUID(&MF_MT_SUBTYPE) }
                && subtype == MFVideoFormat_NV12
            {
                unsafe { self.mft.SetOutputType(0, &out_type, 0) }
                    .map_err(|e| format!("SetOutputType(NV12): {e}"))?;
                let (w, h) = unsafe { out_type.GetUINT64(&MF_MT_FRAME_SIZE) }
                    .map(unpack_u64)
                    .unwrap_or((0, 0));
                let stride = unsafe { out_type.GetUINT32(&MF_MT_DEFAULT_STRIDE) }
                    .map(|s| (s as i32).unsigned_abs() as usize)
                    .unwrap_or(w);
                self.width = w;
                self.height = h;
                self.stride = stride.max(w);
                let info = unsafe { self.mft.GetOutputStreamInfo(0) }
                    .map_err(|e| format!("GetOutputStreamInfo: {e}"))?;
                let min_nv12 = (self.stride * self.height * 3 / 2) as u32;
                self.output_buf_size = info.cbSize.max(min_nv12).max(16);
                self.sample_buf = None;
                return Ok(());
            }
            idx += 1;
        }
    }

    fn ensure_output_sample(&mut self) -> Result<IMFSample, DecodeError> {
        if let Some(sample) = &self.sample_buf {
            return Ok(sample.clone());
        }
        unsafe {
            let sample = MFCreateSample().map_err(|_| DecodeError::Codec)?;
            let buffer = MFCreateMemoryBuffer(self.output_buf_size)
                .map_err(|_| DecodeError::Codec)?;
            sample.AddBuffer(&buffer).map_err(|_| DecodeError::Codec)?;
            self.sample_buf = Some(sample.clone());
            Ok(sample)
        }
    }

    fn decode(&mut self, annex_b: &[u8], timestamp_us: u64) -> Result<Option<CapturedFrame>, DecodeError> {
        if annex_b.is_empty() || annex_b.len() > MAX_ACCESS_UNIT {
            return Err(DecodeError::Bitstream);
        }
        if !self.has_keyframe {
            // Wait for an IDR / SPS access unit before feeding predictive frames.
            let has_idr = annex_b.windows(5).any(|w| {
                (w[0] == 0 && w[1] == 0 && w[2] == 1 && (w[3] & 0x1F) == 5)
                    || (w[0] == 0 && w[1] == 0 && w[2] == 0 && w[3] == 1 && (w[4] & 0x1F) == 5)
            });
            if !has_idr {
                return Ok(None);
            }
            self.has_keyframe = true;
        }

        unsafe {
            let in_sample = MFCreateSample().map_err(|_| DecodeError::Codec)?;
            let in_buf = MFCreateMemoryBuffer(annex_b.len() as u32)
                .map_err(|_| DecodeError::Codec)?;
            let mut ptr: *mut u8 = std::ptr::null_mut();
            in_buf.Lock(&mut ptr, None, None).map_err(|_| DecodeError::Codec)?;
            std::ptr::copy_nonoverlapping(annex_b.as_ptr(), ptr, annex_b.len());
            let _ = in_buf.Unlock();
            in_buf.SetCurrentLength(annex_b.len() as u32)
                .map_err(|_| DecodeError::Codec)?;
            in_sample.AddBuffer(&in_buf).map_err(|_| DecodeError::Codec)?;
            let _ = in_sample.SetSampleTime((timestamp_us * 10) as i64);

            self.mft
                .ProcessInput(0, &in_sample, 0)
                .map_err(|_| DecodeError::Codec)?;

            let mut latest: Option<CapturedFrame> = None;
            for _ in 0..8 {
                let out_sample = self.ensure_output_sample()?;
                let mut out_data = [MFT_OUTPUT_DATA_BUFFER {
                    dwStreamID: 0,
                    pSample: ManuallyDrop::new(Some(out_sample.clone())),
                    dwStatus: 0,
                    pEvents: ManuallyDrop::new(None),
                }];
                let mut status = 0u32;
                let res = self.mft.ProcessOutput(0, &mut out_data, &mut status);
                // Release our cloned sample reference and any events returned by MF.
                let _ = ManuallyDrop::take(&mut out_data[0].pSample);
                let _ = ManuallyDrop::take(&mut out_data[0].pEvents);
                match res {
                    Ok(()) => {
                        let width = self.width;
                        let height = self.height;
                        if width < 2
                            || height < 2
                            || width > self.limits.0
                            || height > self.limits.1
                            || width.saturating_mul(height) > 35_389_440
                        {
                            return Err(DecodeError::Dimensions);
                        }
                        let buf = out_sample
                            .ConvertToContiguousBuffer()
                            .map_err(|_| DecodeError::Codec)?;
                        let mut data_ptr: *mut u8 = std::ptr::null_mut();
                        let mut cur_len = 0u32;
                        buf.Lock(&mut data_ptr, None, Some(&mut cur_len))
                            .map_err(|_| DecodeError::Codec)?;
                        let slice = std::slice::from_raw_parts(data_ptr, cur_len as usize);
                        let stride = self.stride.max(width);
                        // MF aligns NV12 chroma plane on `stride * height` (or 16-row-aligned height).
                        let uv_offset = if slice.len() >= stride * ((height + 15) & !15) * 3 / 2 && (height & 15) != 0 {
                            stride * ((height + 15) & !15)
                        } else {
                            stride * height
                        };
                        let frame = nv12_to_bgra(slice, width, height, stride, uv_offset);
                        let _ = buf.Unlock();
                        latest = Some(frame?);
                    }
                    Err(e) if e.code() == MF_E_TRANSFORM_STREAM_CHANGE => {
                        self.select_nv12_output().map_err(|_| DecodeError::Codec)?;
                        continue;
                    }
                    Err(e) if e.code() == MF_E_TRANSFORM_NEED_MORE_INPUT => break,
                    Err(_) => return Err(DecodeError::Codec),
                }
            }
            Ok(latest)
        }
    }
}

impl Drop for MfH264Decoder {
    fn drop(&mut self) {
        unsafe {
            let _ = self.mft.ProcessMessage(MFT_MESSAGE_COMMAND_FLUSH, 0);
            self.sample_buf = None;
            if self.mf_started {
                let _ = MFShutdown();
            }
            if self.com_owned {
                CoUninitialize();
            }
        }
    }
}

enum Backend {
    MediaFoundation(MfH264Decoder),
    Software(SoftwareDecoder),
}

/// Windows H.264 decoder: uses Microsoft Media Foundation (`CMSH264DecoderMFT`, Baseline/Main/High Profile)
/// with seamless automatic fallback to OpenH264.
pub struct H264Decoder {
    backend: Backend,
}

impl H264Decoder {
    pub fn new(max_width: usize, max_height: usize) -> Result<Self, DecodeError> {
        if max_width < 2
            || max_height < 2
            || max_width > 8192
            || max_height > 8192
            || max_width.checked_mul(max_height).is_none_or(|p| p > 35_389_440)
        {
            return Err(DecodeError::Dimensions);
        }
        match MfH264Decoder::new(max_width, max_height) {
            Ok(mf) => Ok(Self { backend: Backend::MediaFoundation(mf) }),
            Err(err) => {
                eprintln!("MF H.264 decoder unavailable ({err}); falling back to OpenH264");
                Ok(Self { backend: Backend::Software(SoftwareDecoder::new(max_width, max_height)?) })
            }
        }
    }

    pub fn decode(
        &mut self,
        annex_b: &[u8],
        timestamp_us: u64,
    ) -> Result<Option<CapturedFrame>, DecodeError> {
        match &mut self.backend {
            Backend::MediaFoundation(mf) => mf.decode(annex_b, timestamp_us),
            Backend::Software(sw) => sw.decode(annex_b, timestamp_us),
        }
    }
}
