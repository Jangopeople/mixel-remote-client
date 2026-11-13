//! A session picture from the engine. "MRF2": a 32-byte header (magic, width, height, stride,
//! sequence, timestamp; little endian) followed by RGBA rows (the fourth byte is padding). "MRF1":
//! the same with BGRA rows. "MRH1" (H.264 for windows that decode themselves) is not asked for.

#[derive(Debug, PartialEq, Eq)]
pub enum Problem {
    Invalid,
    Compressed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layout {
    Rgba,
    Bgra,
}

pub struct Frame {
    pub width: usize,
    pub height: usize,
    pub stride: usize,
    pub sequence: u64,
    pub layout: Layout,
    /// Rows of 4-byte pixels with an opaque fourth byte.
    pub pixels: Vec<u8>,
}

fn u32_at(bytes: &[u8], at: usize) -> usize {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]) as usize
}

/// Checks the header and bounds exactly as the desktop app does (at most 1920 × 1088, or upright).
pub fn decode(mut data: Vec<u8>, after: u64) -> Result<Frame, Problem> {
    if data.len() <= 32 || data[0] != b'M' || data[1] != b'R' {
        return Err(Problem::Invalid);
    }
    if &data[2..4] == b"H1" {
        return Err(Problem::Compressed);
    }
    let layout = match &data[2..4] {
        b"F2" => Layout::Rgba,
        b"F1" => Layout::Bgra,
        _ => return Err(Problem::Invalid),
    };
    let (width, height, stride) = (u32_at(&data, 4), u32_at(&data, 8), u32_at(&data, 12));
    let sequence = u64::from_le_bytes(data[16..24].try_into().map_err(|_| Problem::Invalid)?);
    let fits = width >= 2 && height >= 2 && width.max(height) <= 1920 && width.min(height) <= 1088;
    if !fits || stride < width * 4 || data.len() != 32 + stride * height || sequence <= after {
        return Err(Problem::Invalid);
    }
    data.drain(..32);
    // The padding byte is not alpha: make every pixel opaque so the picture never shows through.
    for row in data.chunks_exact_mut(stride) {
        for pixel in row[..width * 4].as_chunks_mut::<4>().0 {
            pixel[3] = 255;
        }
    }
    Ok(Frame { width, height, stride, sequence, layout, pixels: data })
}

/// A calm stand-in desktop in the engine's format (for screenshots and tests): wallpaper, two
/// windows and a task bar.
pub fn sample(width: usize, height: usize, sequence: u64) -> Vec<u8> {
    let mut out = Vec::with_capacity(32 + width * height * 4);
    out.extend(b"MRF2");
    for value in [width as u32, height as u32, (width * 4) as u32] {
        out.extend(value.to_le_bytes());
    }
    out.extend(sequence.to_le_bytes());
    out.extend((sequence * 3000).to_le_bytes());
    let inside = |x: usize, y: usize, left: f64, top: f64, w: f64, h: f64| {
        let (fx, fy) = (x as f64 / width as f64, y as f64 / height as f64);
        fx >= left && fx < left + w && fy >= top && fy < top + h
    };
    for y in 0..height {
        for x in 0..width {
            let t = (x + y) as f64 / (width + height) as f64;
            let mut color = [(41.0 + t * 74.0) as u8, (69.0 - t * 13.0) as u8, (115.0 - t * 13.0) as u8];
            if inside(x, y, 0.08, 0.15, 0.50, 0.55) {
                color = [247, 247, 250];
            }
            if inside(x, y, 0.08, 0.15, 0.50, 0.05) {
                color = [230, 232, 237];
            }
            if inside(x, y, 0.48, 0.38, 0.42, 0.46) {
                color = [255, 255, 255];
            }
            if inside(x, y, 0.52, 0.73, 0.16, 0.05) {
                color = [186, 32, 52];
            }
            if inside(x, y, 0.0, 0.93, 1.0, 0.07) {
                color = [30, 32, 38];
            }
            out.extend(color);
            out.push(0);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sample_frames_decode_opaque_and_in_order() {
        let frame = decode(sample(64, 40, 2), 1).unwrap();
        assert_eq!((frame.width, frame.height, frame.stride, frame.sequence), (64, 40, 256, 2));
        assert_eq!(frame.layout, Layout::Rgba);
        assert!(frame.pixels.as_chunks::<4>().0.iter().all(|pixel| pixel[3] == 255));
        assert_eq!(decode(sample(64, 40, 2), 2).err(), Some(Problem::Invalid), "not newer than the last");
    }

    #[test]
    fn bad_headers_and_sizes_are_refused() {
        let mut compressed = sample(8, 8, 1);
        compressed[2..4].copy_from_slice(b"H1");
        assert_eq!(decode(compressed, 0).err(), Some(Problem::Compressed));
        let mut truncated = sample(8, 8, 1);
        truncated.pop();
        assert_eq!(decode(truncated, 0).err(), Some(Problem::Invalid));
        assert_eq!(decode(sample(1, 8, 1), 0).err(), Some(Problem::Invalid));
        assert_eq!(decode(vec![0; 40], 0).err(), Some(Problem::Invalid));
        let mut bgra = sample(8, 8, 1);
        bgra[2..4].copy_from_slice(b"F1");
        assert_eq!(decode(bgra, 0).unwrap().layout, Layout::Bgra);
    }
}
