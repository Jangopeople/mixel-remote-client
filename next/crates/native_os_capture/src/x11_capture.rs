//! Capture an explicitly selected X11 root screen through the local X server.
use crate::stream::{CaptureError, CapturedFrame};
use std::time::{Duration, Instant};
use x11rb::{
    connection::Connection,
    protocol::xproto::{ConnectionExt, ImageFormat, ImageOrder, Screen, VisualClass},
    rust_connection::RustConnection,
};
use zeroize::Zeroizing;

const MAX_PIXELS: usize = 16_777_216;
const FRAME_INTERVAL: Duration = Duration::from_nanos(1_000_000_000 / 20);

pub struct Display {
    pub screen: usize,
    pub root: u32,
    pub width: u16,
    pub height: u16,
}

fn local_display_number(display: &str) -> Option<&str> {
    let number = display
        .strip_prefix(':')
        .or_else(|| display.strip_prefix("unix:"))?;
    let (display, screen) = number.split_once('.').unwrap_or((number, "0"));
    let is_number = |value: &str| {
        value.bytes().all(|byte| byte.is_ascii_digit()) && value.parse::<u16>().is_ok()
    };
    (is_number(display) && is_number(screen)).then_some(number)
}

pub fn session_available() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_none()
        && !std::env::var("XDG_SESSION_TYPE").is_ok_and(|value| value.eq_ignore_ascii_case("wayland"))
        && std::env::var("DISPLAY").is_ok_and(|display| local_display_number(&display).is_some())
}

pub(crate) fn connect() -> Result<RustConnection, CaptureError> {
    if std::env::var_os("WAYLAND_DISPLAY").is_some()
        || std::env::var("XDG_SESSION_TYPE")
            .is_ok_and(|value| value.eq_ignore_ascii_case("wayland"))
    {
        return Err(CaptureError::Native(
            "Wayland screen sharing requires the system screen-sharing portal".into(),
        ));
    }
    let display = std::env::var("DISPLAY").map_err(|_| CaptureError::Permission)?;
    let number = local_display_number(&display).ok_or(CaptureError::Permission)?;
    // An explicit unix transport prevents the library from falling back to TCP.
    RustConnection::connect(Some(&format!("unix/unix:{number}")))
        .map(|(connection, _)| connection)
        .map_err(|_| CaptureError::Permission)
}

pub fn displays() -> Result<Vec<Display>, CaptureError> {
    let connection = connect()?;
    Ok(connection
        .setup()
        .roots
        .iter()
        .enumerate()
        .filter_map(|(screen, root)| {
            let geometry = connection.get_geometry(root.root).ok()?.reply().ok()?;
            dimensions(geometry.width, geometry.height).ok()?;
            PixelFormat::for_root(&connection, root).ok()?;
            Some(Display {
                screen,
                root: root.root,
                width: geometry.width,
                height: geometry.height,
            })
        })
        .collect())
}

fn dimensions(width: u16, height: u16) -> Result<(usize, usize), CaptureError> {
    let (width, height) = (usize::from(width), usize::from(height));
    if width < 2 || height < 2 || width * height > MAX_PIXELS {
        return Err(CaptureError::Target);
    }
    Ok((width, height))
}

struct PixelFormat {
    masks: [u32; 3],
    bytes: usize,
    padding: usize,
    little_endian: bool,
}

impl PixelFormat {
    fn for_root(connection: &RustConnection, root: &Screen) -> Result<Self, CaptureError> {
        let format = connection
            .setup()
            .pixmap_formats
            .iter()
            .find(|format| format.depth == root.root_depth)
            .ok_or(CaptureError::Target)?;
        let visual = root
            .allowed_depths
            .iter()
            .flat_map(|depth| &depth.visuals)
            .find(|visual| {
                visual.visual_id == root.root_visual && visual.class == VisualClass::TRUE_COLOR
            })
            .ok_or(CaptureError::Target)?;
        let masks = [visual.blue_mask, visual.green_mask, visual.red_mask];
        let valid_mask = |mask: u32| {
            if mask == 0 || mask.ilog2() >= u32::from(format.bits_per_pixel) {
                return false;
            }
            let channel = mask >> mask.trailing_zeros();
            channel & channel.wrapping_add(1) == 0
        };
        if !matches!(format.bits_per_pixel, 16 | 24 | 32)
            || !matches!(format.scanline_pad, 8 | 16 | 32)
            || !masks.into_iter().all(valid_mask)
            || (masks[0] & masks[1]) | (masks[1] & masks[2]) | (masks[0] & masks[2]) != 0
        {
            return Err(CaptureError::Target);
        }
        Ok(Self {
            masks,
            bytes: usize::from(format.bits_per_pixel / 8),
            padding: usize::from(format.scanline_pad / 8),
            little_endian: connection.setup().image_byte_order == ImageOrder::LSB_FIRST,
        })
    }

    fn bgra(
        &self,
        width: usize,
        height: usize,
        image: &[u8],
    ) -> Result<CapturedFrame, CaptureError> {
        let line_bytes = width * self.bytes;
        let stride = line_bytes.next_multiple_of(self.padding);
        let expected = stride * height;
        if image.len() != expected.next_multiple_of(4) {
            return Err(CaptureError::Native("Invalid X11 image storage".into()));
        }
        let mut pixels = Vec::with_capacity(width * height * 4);
        let direct = self.little_endian && self.masks == [0xff, 0xff00, 0xff0000];
        for row in image[..expected].chunks_exact(stride) {
            for pixel in row[..line_bytes].chunks_exact(self.bytes) {
                if direct {
                    pixels.extend_from_slice(&[pixel[0], pixel[1], pixel[2], 255]);
                    continue;
                }
                let mut storage = [0; 4];
                let value = if self.little_endian {
                    storage[..self.bytes].copy_from_slice(pixel);
                    u32::from_le_bytes(storage)
                } else {
                    storage[4 - self.bytes..].copy_from_slice(pixel);
                    u32::from_be_bytes(storage)
                };
                for mask in self.masks {
                    let shift = mask.trailing_zeros();
                    let channel = u64::from((value & mask) >> shift);
                    let maximum = u64::from(mask >> shift);
                    pixels.push(((channel * 255 + maximum / 2) / maximum) as u8);
                }
                pixels.push(255);
            }
        }
        CapturedFrame::bgra(width, height, width * 4, pixels)
            .map_err(|_| CaptureError::Native("Invalid converted X11 image".into()))
    }
}

pub struct X11Capture {
    connection: RustConnection,
    root: u32,
    depth: u8,
    format: PixelFormat,
    next: Instant,
}

impl X11Capture {
    pub fn open(display: &Display) -> Result<Self, CaptureError> {
        let connection = connect()?;
        let root = connection
            .setup()
            .roots
            .get(display.screen)
            .filter(|root| root.root == display.root)
            .ok_or(CaptureError::Target)?;
        let format = PixelFormat::for_root(&connection, root)?;
        let depth = root.root_depth;
        Ok(Self {
            connection,
            root: display.root,
            depth,
            format,
            next: Instant::now(),
        })
    }

    pub fn next_frame(&mut self, timeout: Duration) -> Result<Option<CapturedFrame>, CaptureError> {
        let delay = self.next.saturating_duration_since(Instant::now());
        if delay > timeout {
            std::thread::sleep(timeout);
            return Ok(None);
        }
        std::thread::sleep(delay);
        self.next = Instant::now() + FRAME_INTERVAL;
        let geometry = self
            .connection
            .get_geometry(self.root)
            .map_err(native)?
            .reply()
            .map_err(native)?;
        let (width, height) = dimensions(geometry.width, geometry.height)?;
        let reply = self
            .connection
            .get_image(
                ImageFormat::Z_PIXMAP,
                self.root,
                0,
                0,
                geometry.width,
                geometry.height,
                u32::MAX,
            )
            .map_err(native)?
            .reply()
            .map_err(native)?;
        let data = Zeroizing::new(reply.data);
        if reply.depth != self.depth {
            return Err(CaptureError::Target);
        }
        self.format.bgra(width, height, &data).map(Some)
    }
}

fn native(_: impl std::error::Error) -> CaptureError {
    CaptureError::Native("The local X11 capture connection closed or rejected the request".into())
}
