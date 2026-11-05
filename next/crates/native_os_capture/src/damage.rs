use thiserror::Error;
use zeroize::{Zeroize, Zeroizing};
const MAX_DIMENSION: usize = 8192;
const MAX_BYTES: usize = 256 * 1024 * 1024;
const MAX_REGIONS: usize = 256;
#[derive(Debug, Error, PartialEq, Eq)]
pub enum FrameError {
    #[error("invalid or oversized BGRA frame layout")]
    Layout,
    #[error("tile size must be a power of two between 8 and 128")]
    Tile,
}
/// The validated slice may include row padding; damage detection ignores it.
pub struct Frame<'a> {
    width: usize,
    height: usize,
    stride: usize,
    bytes: &'a [u8],
}
impl<'a> Frame<'a> {
    pub fn bgra(
        width: usize,
        height: usize,
        stride: usize,
        bytes: &'a [u8],
    ) -> Result<Self, FrameError> {
        if width == 0 || height == 0 || width > MAX_DIMENSION || height > MAX_DIMENSION {
            return Err(FrameError::Layout);
        }
        let row = width.checked_mul(4).ok_or(FrameError::Layout)?;
        let size = stride.checked_mul(height).ok_or(FrameError::Layout)?;
        if stride < row || size > MAX_BYTES || bytes.len() < size {
            return Err(FrameError::Layout);
        }
        Ok(Self {
            width,
            height,
            stride,
            bytes,
        })
    }
    pub fn width(&self) -> usize {
        self.width
    }
    pub fn height(&self) -> usize {
        self.height
    }
    fn row(&self, y: usize) -> &[u8] {
        &self.bytes[y * self.stride..y * self.stride + self.width * 4]
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: usize,
    pub y: usize,
    pub width: usize,
    pub height: usize,
}
impl Rect {
    fn full(frame: &Frame<'_>) -> Self {
        Self {
            x: 0,
            y: 0,
            width: frame.width,
            height: frame.height,
        }
    }
}
/// Stores one tightly packed reference frame. Call `update` for every captured
/// frame, and reset after capture source changes, dropped output, or reconnect.
/// A returned rectangle is a raw-pixel region, not a standalone codec frame:
/// inter-frame video encoders must preserve their reference-frame semantics.
pub struct DamageTracker {
    tile: usize,
    width: usize,
    height: usize,
    previous: Zeroizing<Vec<u8>>,
}
impl DamageTracker {
    pub fn new(tile: usize) -> Result<Self, FrameError> {
        if !(8..=128).contains(&tile) || !tile.is_power_of_two() {
            return Err(FrameError::Tile);
        }
        Ok(Self {
            tile,
            width: 0,
            height: 0,
            previous: Zeroizing::new(Vec::new()),
        })
    }
    pub fn reset(&mut self) {
        self.width = 0;
        self.height = 0;
        self.previous.zeroize();
    }
    pub fn update(&mut self, frame: &Frame<'_>) -> Vec<Rect> {
        if self.width != frame.width || self.height != frame.height {
            self.width = frame.width;
            self.height = frame.height;
            self.previous.resize(frame.width * frame.height * 4, 0);
            self.copy(frame);
            return vec![Rect::full(frame)];
        }
        let mut regions: Vec<Rect> = Vec::new();
        let mut dirty_pixels = 0usize;
        for y in (0..frame.height).step_by(self.tile) {
            let height = self.tile.min(frame.height - y);
            let mut run: Option<Rect> = None;
            for x in (0..frame.width).step_by(self.tile) {
                let width = self.tile.min(frame.width - x);
                let dirty = (y..y + height).any(|row| {
                    frame.row(row)[x * 4..(x + width) * 4]
                        != self.previous
                            [(row * frame.width + x) * 4..(row * frame.width + x + width) * 4]
                });
                if dirty {
                    dirty_pixels += width * height;
                    if let Some(run) = &mut run {
                        run.width += width;
                    } else {
                        run = Some(Rect {
                            x,
                            y,
                            width,
                            height,
                        });
                    }
                } else if let Some(rect) = run.take() {
                    Self::merge(&mut regions, rect);
                }
            }
            if let Some(rect) = run {
                Self::merge(&mut regions, rect);
            }
            // Stop scanning fragmented/full-screen movement; one full region is
            // cheaper than an unbounded rectangle list or excess encoder work.
            if regions.len() > MAX_REGIONS || dirty_pixels * 10 >= frame.width * frame.height * 7 {
                self.copy(frame);
                return vec![Rect::full(frame)];
            }
        }
        self.copy(frame);
        regions
    }
    fn merge(regions: &mut Vec<Rect>, rect: Rect) {
        if let Some(previous) = regions.iter_mut().rev().find(|prior| {
            prior.x == rect.x && prior.width == rect.width && prior.y + prior.height == rect.y
        }) {
            previous.height += rect.height;
        } else {
            regions.push(rect);
        }
    }
    fn copy(&mut self, frame: &Frame<'_>) {
        for y in 0..frame.height {
            self.previous[y * frame.width * 4..(y + 1) * frame.width * 4]
                .copy_from_slice(frame.row(y));
        }
    }
}
