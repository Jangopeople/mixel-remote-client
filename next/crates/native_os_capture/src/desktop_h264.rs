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
        let config = EncoderConfig::new()
            .usage_type(UsageType::ScreenContentRealTime)
            .bitrate(BitRate::from_bps(bitrate))
            .max_frame_rate(FrameRate::from_hz(fps as f32))
            .profile(Profile::Baseline)
            .level(if width > 1280 || height > 720 { Level::Level_4_0 } else { Level::Level_3_1 })
            .rate_control_mode(RateControlMode::Bitrate)
            .adaptive_quantization(false)
            .background_detection(false)
            .skip_frames(true);
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
        for y in 0..self.height {
            let source_row = y * frame.height() / self.height * frame.stride();
            for x in 0..self.width {
                let source = source_row + x * frame.width() / self.width * 4;
                let target = (y * self.width + x) * 4;
                self.pixels[target..target + 4]
                    .copy_from_slice(&frame.pixels()[source..source + 4]);
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
        if !(16..=1920).contains(&width) || !(16..=1088).contains(&height) {
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
        if width < 2 || height < 2 || width > self.limits.0 || height > self.limits.1 {
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
    }
}
