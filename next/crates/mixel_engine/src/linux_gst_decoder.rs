//! Linux hardware-first GStreamer H.264 decoder (`vah264dec` / `vaapih264dec` / `nvh264dec` /
//! `v4l2h264dec` / `avdec_h264`) supporting Baseline, Main, and High Profile (CABAC) up to 4K,
//! with automatic fallback to OpenH264 (`desktop_h264::H264Decoder`).
use gstreamer as gst;
use gstreamer::prelude::*;
use gstreamer_app::{AppSink, AppSrc};
use gstreamer_video::{VideoFrameExt, VideoFrameRef, VideoInfo};
use native_os_capture::{
    desktop_h264::{DecodeError, H264Decoder as SoftwareDecoder},
    h264::MAX_ACCESS_UNIT,
    stream::CapturedFrame,
};

const DECODER_CANDIDATES: [&str; 5] = [
    "vah264dec",
    "vaapih264dec",
    "nvh264dec",
    "v4l2h264dec",
    "avdec_h264",
];

fn best_decoder_element() -> Option<&'static str> {
    gst::init().ok()?;
    gst::ElementFactory::find("h264parse")?;
    gst::ElementFactory::find("videoconvert")?;
    DECODER_CANDIDATES
        .iter()
        .copied()
        .find(|name| gst::ElementFactory::find(name).is_some())
}

/// Whether this Linux system has a GStreamer H.264 decoder capable of High Profile (CABAC).
pub fn high_profile_available() -> bool {
    best_decoder_element().is_some()
}

struct GstH264Decoder {
    pipeline: gst::Pipeline,
    src: AppSrc,
    sink: AppSink,
    limits: (usize, usize),
    has_keyframe: bool,
}

impl GstH264Decoder {
    fn new(max_width: usize, max_height: usize) -> Result<Self, String> {
        let decoder = best_decoder_element().ok_or("No GStreamer H.264 decoder plugin installed")?;
        let desc = format!(
            "appsrc name=src is-live=true format=time caps=\"video/x-h264,stream-format=byte-stream,alignment=au\" \
             ! h264parse ! {decoder} ! videoconvert ! video/x-raw,format=BGRA \
             ! appsink name=sink sync=false drop=false max-buffers=4"
        );
        let pipeline = gst::parse::launch(&desc)
            .map_err(|e| format!("gst::parse::launch({decoder}): {e}"))?
            .downcast::<gst::Pipeline>()
            .map_err(|_| "GStreamer element is not a pipeline".to_owned())?;
        let src = pipeline
            .by_name("src")
            .ok_or("missing appsrc")?
            .downcast::<AppSrc>()
            .map_err(|_| "src is not AppSrc".to_owned())?;
        let sink = pipeline
            .by_name("sink")
            .ok_or("missing appsink")?
            .downcast::<AppSink>()
            .map_err(|_| "sink is not AppSink".to_owned())?;
        pipeline
            .set_state(gst::State::Playing)
            .map_err(|e| format!("GStreamer Playing failed: {e:?}"))?;
        Ok(Self {
            pipeline,
            src,
            sink,
            limits: (max_width, max_height),
            has_keyframe: false,
        })
    }

    fn decode(&mut self, annex_b: &[u8], timestamp_us: u64) -> Result<Option<CapturedFrame>, DecodeError> {
        if annex_b.is_empty() || annex_b.len() > MAX_ACCESS_UNIT {
            return Err(DecodeError::Bitstream);
        }
        if !self.has_keyframe {
            let has_idr = annex_b.windows(5).any(|w| {
                (w[0] == 0 && w[1] == 0 && w[2] == 1 && (w[3] & 0x1F) == 5)
                    || (w[0] == 0 && w[1] == 0 && w[2] == 0 && w[3] == 1 && (w[4] & 0x1F) == 5)
            });
            if !has_idr {
                return Err(DecodeError::NeedsKeyframe);
            }
            self.has_keyframe = true;
        }
        let mut buffer = gst::Buffer::from_mut_slice(annex_b.to_vec());
        if let Some(buf_ref) = buffer.get_mut() {
            buf_ref.set_pts(gst::ClockTime::from_useconds(timestamp_us));
        }
        self.src
            .push_buffer(buffer)
            .map_err(|e| DecodeError::Decoder(format!("GStreamer push_buffer: {e:?}")))?;

        let sample = self.sink.try_pull_sample(gst::ClockTime::from_mseconds(40));
        let Some(sample) = sample else {
            return Ok(None);
        };
        let caps = sample.caps().ok_or_else(|| DecodeError::Decoder("missing caps".into()))?;
        let info = VideoInfo::from_caps(caps)
            .map_err(|e| DecodeError::Decoder(format!("VideoInfo::from_caps: {e}")))?;
        let width = info.width() as usize;
        let height = info.height() as usize;
        if width < 2
            || height < 2
            || width > self.limits.0
            || height > self.limits.1
            || width.saturating_mul(height) > 35_389_440
        {
            return Err(DecodeError::Dimensions);
        }
        let buf = sample.buffer().ok_or_else(|| DecodeError::Decoder("missing buffer".into()))?;
        let frame = VideoFrameRef::from_buffer_readable(buf, &info)
            .map_err(|_| DecodeError::Decoder("cannot map GStreamer video frame".into()))?;
        let src_stride = frame.plane_stride()[0] as usize;
        let data = frame
            .plane_data(0)
            .map_err(|_| DecodeError::Decoder("missing BGRA plane".into()))?;
        let out_stride = width.checked_mul(4).ok_or(DecodeError::Dimensions)?;
        let mut pixels = vec![0u8; out_stride.checked_mul(height).ok_or(DecodeError::Dimensions)?];
        for y in 0..height {
            let src_row = &data[y * src_stride..y * src_stride + out_stride];
            let dst_row = &mut pixels[y * out_stride..(y + 1) * out_stride];
            dst_row.copy_from_slice(src_row);
        }
        CapturedFrame::bgra(width, height, out_stride, pixels).map(Some).map_err(|_| DecodeError::Dimensions)
    }
}

impl Drop for GstH264Decoder {
    fn drop(&mut self) {
        let _ = self.pipeline.set_state(gst::State::Null);
    }
}

enum Backend {
    GStreamer(GstH264Decoder),
    Software(SoftwareDecoder),
}

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
        match GstH264Decoder::new(max_width, max_height) {
            Ok(gst_dec) => Ok(Self { backend: Backend::GStreamer(gst_dec) }),
            Err(err) => {
                eprintln!("GStreamer H.264 decoder unavailable ({err}); falling back to OpenH264");
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
            Backend::GStreamer(gst_dec) => gst_dec.decode(annex_b, timestamp_us),
            Backend::Software(sw) => sw.decode(annex_b, timestamp_us),
        }
    }
}
