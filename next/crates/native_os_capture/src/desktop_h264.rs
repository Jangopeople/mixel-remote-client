//! Owner-thread baseline H.264 for Windows and Linux desktop sessions.
use crate::{
    damage::DamageTracker,
    h264::{EncodeError, EncodedH264, annex_b_to_avcc},
    stream::CapturedFrame,
};
use h264_reader::nal::{
    Nal, RefNal,
    sps::{FrameMbsFlags, SeqParameterSet},
};
use openh264::{
    OpenH264API,
    decoder::{Decoder, DecoderConfig},
    encoder::{
        BitRate, Encoder, EncoderConfig, FrameRate, Level, Profile, RateControlMode, UsageType,
    },
    formats::{BgraSliceU8, YUVBuffer, YUVSource},
};
use zeroize::{Zeroize, Zeroizing};

pub struct ScreenH264Encoder {
    encoder: Encoder,
    width: usize,
    height: usize,
    pixels: Zeroizing<Vec<u8>>,
    yuv: YUVBuffer,
    damage: DamageTracker,
    needs_keyframe: bool,
    started: bool,
}

impl ScreenH264Encoder {
    pub fn new(width: usize, height: usize, fps: u32, bitrate: u32) -> Result<Self, EncodeError> {
        if !(16..=1920).contains(&width)
            || !(16..=1088).contains(&height)
            || !width.is_multiple_of(2)
            || !height.is_multiple_of(2)
            || !(1..=30).contains(&fps)
            || !(1..=8_000_000).contains(&bitrate)
        {
            return Err(EncodeError::Configuration);
        }
        // Software encoding is the slowest step of a session: use the processor's
        // cores (one stays free for capture and the session itself).
        let threads = std::thread::available_parallelism()
            .map(|count| count.get().saturating_sub(1).clamp(1, 4) as u16)
            .unwrap_or(1);
        // Measured at 1920x1080 with motion: the screen-content mode needs about three
        // times as long per picture and ignores extra cores; the real-time mode with
        // slices spreads the work over the cores (pictures are about a quarter larger).
        let mut config = EncoderConfig::new()
            .num_threads(threads)
            .complexity(openh264::encoder::Complexity::Low)
            // The colour conversion below is BT.601, limited range: say so in the stream,
            // or decoders assume BT.709 for large pictures and colours shift slightly.
            .vui(
                openh264::encoder::VuiConfig::bt709()
                    .matrix_coefficients(openh264::encoder::MatrixCoefficients::Smpte170M),
            )
            .usage_type(UsageType::CameraVideoRealTime)
            .bitrate(BitRate::from_bps(bitrate))
            .max_frame_rate(FrameRate::from_hz(fps as f32))
            .profile(Profile::Baseline)
            .level(if width > 1280 || height > 720 { Level::Level_4_0 } else { Level::Level_3_1 })
            .rate_control_mode(RateControlMode::Bitrate)
            .adaptive_quantization(false)
            .background_detection(false)
            .skip_frames(true);
        if threads > 1 {
            config = config.max_slice_len(60_000);
        }
        let encoder = Encoder::with_api_config(OpenH264API::from_source(), config)
            .map_err(|_| EncodeError::Failed)?;
        Ok(Self {
            encoder,
            width,
            height,
            pixels: Zeroizing::new(vec![0; width * height * 4]),
            yuv: YUVBuffer::new(width, height),
            damage: DamageTracker::new(32).expect("fixed valid damage tile size"),
            needs_keyframe: true,
            started: false,
        })
    }

    pub fn needs_retry(&self) -> bool {
        self.needs_keyframe
    }

    pub fn encode(
        &mut self,
        frame: &CapturedFrame,
        _timestamp_us: u64,
        force: bool,
    ) -> Result<Option<EncodedH264>, EncodeError> {
        let force = force || self.needs_keyframe;
        if self.damage.update(&frame.frame()).is_empty() && !force {
            return Ok(None);
        }
        if frame.width() == self.width && frame.height() == self.height {
            // The usual case (the caller already fitted the frame): copy whole rows.
            let row = self.width * 4;
            for y in 0..self.height {
                let source = y * frame.stride();
                self.pixels[y * row..(y + 1) * row]
                    .copy_from_slice(&frame.pixels()[source..source + row]);
            }
        } else {
            for y in 0..self.height {
            let source_row = y * frame.height() / self.height * frame.stride();
            for x in 0..self.width {
                let source = source_row + x * frame.width() / self.width * 4;
                let target = (y * self.width + x) * 4;
                self.pixels[target..target + 4]
                    .copy_from_slice(&frame.pixels()[source..source + 4]);
            }
            }
        }
        self.yuv
            .read_bgra8(BgraSliceU8::new(&self.pixels, (self.width, self.height)));
        if force && self.started {
            self.encoder.force_intra_frame();
        }
        let access_unit = self
            .encoder
            .encode(&self.yuv)
            .map_err(|_| EncodeError::Failed)?;
        let annex_b = Zeroizing::new(access_unit.to_vec());
        if annex_b.is_empty() {
            self.needs_keyframe = force;
            return Ok(None);
        }
        let sample = annex_b_to_avcc(&annex_b)?;
        self.started = true;
        self.needs_keyframe =
            force && !(sample.keyframe && sample.sps.is_some() && sample.pps.is_some());
        if self.needs_keyframe {
            return Err(EncodeError::Stalled);
        }
        Ok(Some(EncodedH264 {
            keyframe: sample.keyframe,
            annex_b,
        }))
    }
}

#[derive(Debug, thiserror::Error)]
pub enum DecodeError {
    #[error("invalid or unsupported H264 access unit")]
    Bitstream,
    #[error("decoded dimensions exceed the negotiated limit")]
    Dimensions,
    #[error("software H264 decoding failed")]
    Codec,
}

pub struct H264Decoder {
    decoder: Decoder,
    limits: (usize, usize),
    configured: bool,
}

impl H264Decoder {
    pub fn new(width: usize, height: usize) -> Result<Self, DecodeError> {
        // Landscape up to 1920x1088, or the same picture upright (a phone screen).
        if !(16..=1920).contains(&width) || !(16..=1920).contains(&height) {
            return Err(DecodeError::Dimensions);
        }
        Ok(Self {
            decoder: Decoder::with_api_config(OpenH264API::from_source(), DecoderConfig::new())
                .map_err(|_| DecodeError::Codec)?,
            limits: (width, height),
            configured: false,
        })
    }

    pub fn hardware_active(&self) -> bool {
        false
    }

    pub fn decode(
        &mut self,
        annex_b: &[u8],
        _timestamp_us: u64,
    ) -> Result<Option<CapturedFrame>, DecodeError> {
        let sample = annex_b_to_avcc(annex_b).map_err(|_| DecodeError::Bitstream)?;
        if let Some(sps) = sample.sps {
            if sps.get(1) != Some(&66) || !sample.keyframe || sample.pps.is_none() {
                return Err(DecodeError::Bitstream);
            }
            let sps = SeqParameterSet::from_bits(RefNal::new(sps, &[], true).rbsp_bits())
                .map_err(|_| DecodeError::Bitstream)?;
            let (width, height) = sps
                .pixel_dimensions()
                .map_err(|_| DecodeError::Dimensions)?;
            if width < 2
                || height < 2
                || width.min(height) > 1088
                || width as usize > self.limits.0
                || height as usize > self.limits.1
                || !matches!(sps.frame_mbs_flags, FrameMbsFlags::Frames)
                || sps.pic_width_in_mbs_minus1 as usize >= self.limits.0.div_ceil(16)
                || sps.pic_height_in_map_units_minus1 as usize >= self.limits.1.div_ceil(16)
            {
                return Err(DecodeError::Dimensions);
            }
            self.configured = true;
        } else if sample.pps.is_some() {
            return Err(DecodeError::Bitstream);
        }
        if !self.configured {
            return Ok(None);
        }
        let Some(decoded) = self
            .decoder
            .decode(annex_b)
            .map_err(|_| DecodeError::Codec)?
        else {
            return Ok(None);
        };
        let (width, height) = decoded.dimensions();
        if width < 2 || height < 2 || width.min(height) > 1088 || width > self.limits.0 || height > self.limits.1 {
            return Err(DecodeError::Dimensions);
        }
        let mut pixels = Zeroizing::new(vec![0; width * height * 4]);
        decoded.write_rgba8(&mut pixels);
        for pixel in pixels.as_chunks_mut::<4>().0 {
            pixel.swap(0, 2);
        }
        CapturedFrame::bgra(width, height, width * 4, std::mem::take(&mut *pixels))
            .map(Some)
            .map_err(|_| DecodeError::Codec)
    }
}

impl Drop for ScreenH264Encoder {
    fn drop(&mut self) {
        // Replace retained YUV with a neutral image before releasing its buffer.
        self.pixels.zeroize();
        self.pixels.resize(self.width * self.height * 4, 0);
        self.yuv
            .read_bgra8(BgraSliceU8::new(&self.pixels, (self.width, self.height)));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn padded_bgra_scales_and_decodes_with_repeatable_keyframes() {
        let mut pixels = vec![0; 400 * 64];
        for row in pixels.as_chunks_mut::<400>().0 {
            for pixel in row[..384].as_chunks_mut::<4>().0 {
                pixel.copy_from_slice(&[31, 64, 185, 255]);
            }
        }
        let frame = CapturedFrame::bgra(96, 64, 400, pixels).unwrap();
        let mut encoder = ScreenH264Encoder::new(48, 32, 20, 300_000).unwrap();
        let mut decoder = H264Decoder::new(48, 32).unwrap();
        for sequence in 0..2 {
            let encoded = encoder.encode(&frame, sequence, true).unwrap().unwrap();
            assert!(encoded.keyframe);
            let output = decoder.decode(&encoded.annex_b, sequence).unwrap().unwrap();
            assert_eq!((output.width(), output.height()), (48, 32));
            for (actual, expected) in output.pixels()[..3].iter().zip([31u8, 64, 185]) {
                assert!(actual.abs_diff(expected) < 16, "{actual} versus {expected}");
            }
            assert!(matches!(
                H264Decoder::new(32, 32)
                    .unwrap()
                    .decode(&encoded.annex_b, 0),
                Err(DecodeError::Dimensions)
            ));
        }
        assert!(encoder.encode(&frame, 2, false).unwrap().is_none());
    }

    /// A phone shares an upright picture (Quick Support for phones): both decoders show it,
    /// and still refuse anything larger than Full HD turned on its side.
    #[cfg(target_os = "macos")]
    #[test]
    fn upright_phone_pictures_decode_on_both_decoders() {
        use crate::{
            macos_decoder::H264Decoder as MacDecoder,
            macos_screen_encoder::ScreenH264Encoder as MacEncoder,
        };
        let (width, height) = (886usize, 1920usize);
        let mut pixels = vec![255u8; width * height * 4];
        for (index, pixel) in pixels.as_chunks_mut::<4>().0.iter_mut().enumerate() {
            if (index / width) < height / 2 {
                *pixel = [30, 60, 200, 255];
            }
        }
        let frame = CapturedFrame::bgra(width, height, width * 4, pixels).unwrap();
        let mut encoder = MacEncoder::new(width, height, 20, 2_000_000).unwrap();
        let encoded = (1..10)
            .find_map(|number| encoder.encode(&frame, number * 50_000, true).unwrap())
            .expect("VideoToolbox did not produce an independent frame");
        let decoded = MacDecoder::new(1920, 1920).unwrap().decode(&encoded.annex_b, 0).unwrap().unwrap();
        assert_eq!((decoded.width(), decoded.height()), (width, height));
        let decoded = H264Decoder::new(1920, 1920).unwrap().decode(&encoded.annex_b, 0).unwrap().unwrap();
        assert_eq!((decoded.width(), decoded.height()), (width, height));
        // Top half blue-ish, bottom half white (BGRA).
        let at = |x: usize, y: usize| { let o = y * decoded.stride() + x * 4; decoded.pixels()[o..o + 3].to_vec() };
        assert!(at(400, 300)[2] > 150 && at(400, 300)[0] < 90, "top: {:?}", at(400, 300));
        assert!(at(400, 1700).iter().all(|value| *value > 200), "bottom: {:?}", at(400, 1700));
    }
    #[cfg(target_os = "macos")]
    #[test]
    fn desktop_software_and_macos_hardware_codecs_interoperate() {
        use crate::{
            macos_decoder::H264Decoder as MacDecoder,
            macos_screen_encoder::ScreenH264Encoder as MacEncoder,
        };
        let mut pixels = vec![0; 320 * 240 * 4];
        for pixel in pixels.as_chunks_mut::<4>().0 {
            *pixel = [34, 78, 190, 255];
        }
        let frame = CapturedFrame::bgra(320, 240, 1280, pixels).unwrap();
        let mut desktop_encoder = ScreenH264Encoder::new(320, 240, 20, 500_000).unwrap();
        let mut mac_decoder = MacDecoder::new(320, 240).unwrap();
        let encoded = desktop_encoder.encode(&frame, 0, true).unwrap().unwrap();
        let decoded = mac_decoder.decode(&encoded.annex_b, 0).unwrap().unwrap();
        assert_eq!((decoded.width(), decoded.height()), (320, 240));
        let mut mac_encoder = MacEncoder::new(320, 240, 20, 500_000).unwrap();
        let mut desktop_decoder = H264Decoder::new(320, 240).unwrap();
        let encoded = (1..10)
            .find_map(|frame_number| {
                mac_encoder
                    .encode(&frame, frame_number * 50_000, true)
                    .unwrap()
            })
            .expect("VideoToolbox did not produce an independent frame");
        let decoded = desktop_decoder
            .decode(&encoded.annex_b, 0)
            .unwrap()
            .unwrap();
        assert_eq!((decoded.width(), decoded.height()), (320, 240));
        // Full HD in several slices (one per encoder thread) decodes on both sides, too.
        let mut pixels = vec![255u8; 1920 * 1080 * 4];
        for (index, pixel) in pixels.as_chunks_mut::<4>().0.iter_mut().enumerate() {
            if (index / 1920 + index % 1920) % 7 < 3 {
                *pixel = [20, 90, (index % 251) as u8, 255];
            }
        }
        let frame = CapturedFrame::bgra(1920, 1080, 1920 * 4, pixels).unwrap();
        let mut desktop_encoder = ScreenH264Encoder::new(1920, 1080, 30, 6_000_000).unwrap();
        let encoded = desktop_encoder.encode(&frame, 0, true).unwrap().unwrap();
        let slices = encoded.annex_b.windows(4).filter(|window| *window == [0, 0, 0, 1]).count();
        assert!(slices >= 3, "expected SPS, PPS and at least one slice, got {slices}");
        let decoded = MacDecoder::new(1920, 1088).unwrap().decode(&encoded.annex_b, 0).unwrap().unwrap();
        assert_eq!((decoded.width(), decoded.height()), (1920, 1080));
        let decoded = H264Decoder::new(1920, 1088).unwrap().decode(&encoded.annex_b, 0).unwrap().unwrap();
        assert_eq!((decoded.width(), decoded.height()), (1920, 1080));
        // The decoded picture matches the source (centre pixel of a white area and a coloured one).
        let at = |x: usize, y: usize| { let o = y * decoded.stride() + x * 4; decoded.pixels()[o..o + 3].to_vec() };
        let source = |x: usize, y: usize| { let o = (y * 1920 + x) * 4; frame.pixels()[o..o + 3].to_vec() };
        for (x, y) in [(960, 540), (10, 10), (1900, 1070)] {
            for (a, b) in at(x, y).iter().zip(source(x, y)) {
                assert!((i16::from(*a) - i16::from(b)).abs() < 90, "pixel {x},{y}: {a} vs {b}");
            }
        }
    }

    /// Manual speed comparison: `cargo test -p native_os_capture --release bench_1080p -- --ignored --nocapture`.
    #[test]
    #[ignore]
    fn bench_1080p_encoder_variants() {
        use openh264::encoder::Complexity;
        let (width, height) = (1920usize, 1080usize);
        let frames: Vec<Vec<u8>> = (0..12)
            .map(|n| {
                let mut pixels = vec![255u8; width * height * 4];
                // A moving block and a band of "text" noise that moves down, like scrolling.
                for y in 200..420 {
                    for x in (n * 120)..(n * 120 + 220) {
                        pixels[(y * width + x) * 4..(y * width + x) * 4 + 3].copy_from_slice(&[60, 20, 220]);
                    }
                }
                for y in (n * 40)..(n * 40 + 300) {
                    for x in 60..1500 {
                        let value = (((x * 7 + y * 13) ^ (x / 3)) % 3 == 0) as u8 * 200;
                        pixels[(y * width + x) * 4..(y * width + x) * 4 + 3].copy_from_slice(&[value; 3]);
                    }
                }
                pixels
            })
            .collect();
        for (name, usage, threads, slice) in [
            ("screen, 1 thread", UsageType::ScreenContentRealTime, 1u16, None),
            ("screen, 4 threads + slices", UsageType::ScreenContentRealTime, 4, Some(60_000u32)),
            ("camera, 1 thread", UsageType::CameraVideoRealTime, 1, None),
            ("camera, 4 threads + slices", UsageType::CameraVideoRealTime, 4, Some(60_000)),
        ] {
            let mut config = EncoderConfig::new()
                .num_threads(threads)
                .complexity(Complexity::Low)
                .usage_type(usage)
                .bitrate(BitRate::from_bps(6_000_000))
                .max_frame_rate(FrameRate::from_hz(30.0))
                .profile(Profile::Baseline)
                .level(Level::Level_4_0)
                .rate_control_mode(RateControlMode::Bitrate)
                .adaptive_quantization(false)
                .background_detection(false)
                .skip_frames(true);
            if let Some(slice) = slice {
                config = config.max_slice_len(slice);
            }
            let mut encoder = Encoder::with_api_config(OpenH264API::from_source(), config).unwrap();
            let mut yuv = YUVBuffer::new(width, height);
            let mut convert = std::time::Duration::ZERO;
            let mut encode = std::time::Duration::ZERO;
            let mut bytes = 0usize;
            for pixels in &frames {
                let started = std::time::Instant::now();
                yuv.read_bgra8(BgraSliceU8::new(pixels, (width, height)));
                convert += started.elapsed();
                let started = std::time::Instant::now();
                bytes += encoder.encode(&yuv).unwrap().to_vec().len();
                encode += started.elapsed();
            }
            println!(
                "{name}: convert {:.1} ms, encode {:.1} ms per frame, {} kB per frame",
                convert.as_secs_f64() * 1000.0 / frames.len() as f64,
                encode.as_secs_f64() * 1000.0 / frames.len() as f64,
                bytes / frames.len() / 1000
            );
        }
    }
}
