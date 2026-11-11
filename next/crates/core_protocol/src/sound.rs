//! Remote sound: the host's system sound as small compressed frames inside the
//! authenticated control channel. Mono, 24 kHz, IMA ADPCM (4 bits per sample),
//! 40 ms per frame; silence is not sent at all.
pub const FEATURE: &str = "sound";
/// Prefix of a control record that carries one sound frame from the host.
pub const RECORD: &[u8] = b"mixel-sound-v1:";
/// Controller to host: start or stop sending sound (`RECORD_SWITCH` + 1 or 0).
pub const RECORD_SWITCH: &[u8] = b"mixel-sound-switch-v1:";
pub const RATE: u32 = 24_000;
pub const FRAME_SAMPLES: usize = 960;
/// Header: predictor (i16), step index (u8), then two samples per byte.
pub const FRAME_BYTES: usize = 3 + FRAME_SAMPLES / 2;

const INDEX: [i8; 16] = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8];
const STEP: [i16; 89] = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
    80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
    544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499,
    2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
];

/// Coder state carried from frame to frame; each frame also carries it, so a
/// receiver can start at any frame.
#[derive(Clone, Copy, Default)]
pub struct State {
    predictor: i16,
    index: u8,
}

fn step(state: &mut State, code: u8) -> i16 {
    let size = i32::from(STEP[usize::from(state.index)]);
    let mut difference = size >> 3;
    if code & 4 != 0 {
        difference += size;
    }
    if code & 2 != 0 {
        difference += size >> 1;
    }
    if code & 1 != 0 {
        difference += size >> 2;
    }
    let value = if code & 8 != 0 {
        i32::from(state.predictor) - difference
    } else {
        i32::from(state.predictor) + difference
    };
    state.predictor = value.clamp(-32768, 32767) as i16;
    state.index = (i16::from(state.index) + i16::from(INDEX[usize::from(code)])).clamp(0, 88) as u8;
    state.predictor
}

/// Encodes exactly `FRAME_SAMPLES` samples.
pub fn encode(state: &mut State, samples: &[i16]) -> Option<Vec<u8>> {
    if samples.len() != FRAME_SAMPLES {
        return None;
    }
    let mut frame = Vec::with_capacity(FRAME_BYTES);
    frame.extend(state.predictor.to_be_bytes());
    frame.push(state.index);
    for pair in samples.chunks(2) {
        let mut byte = 0u8;
        for (position, sample) in pair.iter().enumerate() {
            let size = i32::from(STEP[usize::from(state.index)]);
            let mut difference = i32::from(*sample) - i32::from(state.predictor);
            let mut code = 0u8;
            if difference < 0 {
                code = 8;
                difference = -difference;
            }
            if difference >= size {
                code |= 4;
                difference -= size;
            }
            if difference >= size >> 1 {
                code |= 2;
                difference -= size >> 1;
            }
            if difference >= size >> 2 {
                code |= 1;
            }
            step(state, code);
            byte |= code << (position * 4);
        }
        frame.push(byte);
    }
    Some(frame)
}

/// Decodes one frame into `FRAME_SAMPLES` samples; `None` for a malformed frame.
pub fn decode(frame: &[u8]) -> Option<Vec<i16>> {
    if frame.len() != FRAME_BYTES || frame[2] > 88 {
        return None;
    }
    let mut state = State { predictor: i16::from_be_bytes([frame[0], frame[1]]), index: frame[2] };
    let mut samples = Vec::with_capacity(FRAME_SAMPLES);
    for byte in &frame[3..] {
        samples.push(step(&mut state, byte & 15));
        samples.push(step(&mut state, byte >> 4));
    }
    Some(samples)
}

/// Whether a frame is quiet enough to leave out.
pub fn silent(samples: &[i16]) -> bool {
    samples.iter().all(|sample| sample.unsigned_abs() < 48)
}

/// Mixes interleaved samples to mono and converts the rate to [`RATE`] (linear).
pub struct Resampler {
    channels: usize,
    ratio: f64,
    position: f64,
    previous: f32,
}
impl Resampler {
    pub fn new(channels: u16, rate: u32) -> Option<Self> {
        if channels == 0 || channels > 16 || !(8_000..=384_000).contains(&rate) {
            return None;
        }
        Some(Self { channels: usize::from(channels), ratio: f64::from(rate) / f64::from(RATE), position: 0.0, previous: 0.0 })
    }
    /// Appends converted samples for the interleaved `input` (-1.0..1.0) to `output`.
    pub fn push(&mut self, input: &[f32], output: &mut Vec<i16>) {
        for frame in input.chunks_exact(self.channels) {
            let mono = frame.iter().sum::<f32>() / self.channels as f32;
            while self.position < 1.0 {
                let value = self.previous + (mono - self.previous) * self.position as f32;
                output.push((value.clamp(-1.0, 1.0) * 32767.0) as i16);
                self.position += self.ratio;
            }
            self.position -= 1.0;
            self.previous = mono;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_tone_survives_coding_and_silence_is_detected() {
        let tone: Vec<i16> = (0..FRAME_SAMPLES * 3)
            .map(|n| ((n as f32 * 2.0 * std::f32::consts::PI * 440.0 / RATE as f32).sin() * 12000.0) as i16)
            .collect();
        let mut state = State::default();
        let mut error = 0f64;
        let mut power = 0f64;
        for (index, frame) in tone.chunks(FRAME_SAMPLES).enumerate() {
            let coded = encode(&mut state, frame).unwrap();
            assert_eq!(coded.len(), FRAME_BYTES);
            let back = decode(&coded).unwrap();
            if index > 0 {
                for (a, b) in frame.iter().zip(&back) {
                    error += f64::from(i32::from(*a) - i32::from(*b)).powi(2);
                    power += f64::from(*a).powi(2);
                }
            }
        }
        assert!(error / power < 0.01, "noise {}", error / power);
        assert!(silent(&[0, 5, -20]) && !silent(&tone[..64]));
        assert!(decode(&[0; 10]).is_none() && encode(&mut state, &tone[..10]).is_none());
        let mut bad = vec![0u8; FRAME_BYTES];
        bad[2] = 89;
        assert!(decode(&bad).is_none());
    }
    #[test]
    fn stereo_48k_becomes_mono_24k() {
        let mut resampler = Resampler::new(2, 48_000).unwrap();
        let input: Vec<f32> = (0..48_000).flat_map(|n| { let v = (n as f32 * 0.01).sin() * 0.5; [v, v] }).collect();
        let mut output = Vec::new();
        resampler.push(&input, &mut output);
        assert!((output.len() as i64 - 24_000).abs() <= 2, "{}", output.len());
        assert!(output.iter().any(|sample| sample.unsigned_abs() > 10_000));
        assert!(Resampler::new(0, 48_000).is_none());
    }
}
