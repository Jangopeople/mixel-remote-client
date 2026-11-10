//! DXGI Desktop Duplication. Construct and poll on one capture-owner thread,
//! only after the host grants sharing of the selected display. No global capture
//! starts at module load. Dirty-frame transport and session policy live above it.
use crate::stream::{CaptureError, CapturedFrame};
#[path = "windows_gdi_capture.rs"]
mod gdi;
use std::time::Duration;
use windows::{
    Win32::{
        Foundation::HMODULE,
        Graphics::{
            Direct3D::D3D_DRIVER_TYPE_UNKNOWN,
            Direct3D11::*,
            Dxgi::{Common::*, *},
        },
    },
    core::Interface,
};
use zeroize::Zeroizing;
fn native(error: windows::core::Error) -> CaptureError {
    CaptureError::Native(format!("Windows desktop capture failed ({})", error.code()))
}
#[derive(Clone, Debug)]
pub struct Display {
    pub adapter: u32,
    pub output: u32,
    pub name: String,
    pub left: i32,
    pub top: i32,
    pub width: u32,
    pub height: u32,
}
pub fn displays() -> Result<Vec<Display>, CaptureError> {
    // SAFETY: DXGI returns reference-counted interfaces and initialized records.
    let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }.map_err(native)?;
    let mut displays = Vec::new();
    for adapter_index in 0..32 {
        let adapter = match unsafe { factory.EnumAdapters1(adapter_index) } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
            Err(error) => return Err(native(error)),
        };
        for output_index in 0..32 {
            let output = match unsafe { adapter.EnumOutputs(output_index) } {
                Ok(output) => output,
                Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                Err(error) => return Err(native(error)),
            };
            let description = unsafe { output.GetDesc() }.map_err(native)?;
            if !description.AttachedToDesktop.as_bool() {
                continue;
            }
            let width = i64::from(description.DesktopCoordinates.right)
                - i64::from(description.DesktopCoordinates.left);
            let height = i64::from(description.DesktopCoordinates.bottom)
                - i64::from(description.DesktopCoordinates.top);
            if !(1..=8192).contains(&width) || !(1..=8192).contains(&height) {
                continue;
            }
            let end = description
                .DeviceName
                .iter()
                .position(|unit| *unit == 0)
                .unwrap_or(description.DeviceName.len());
            displays.push(Display {
                adapter: adapter_index,
                output: output_index,
                name: String::from_utf16_lossy(&description.DeviceName[..end]),
                left: description.DesktopCoordinates.left,
                top: description.DesktopCoordinates.top,
                width: width as u32,
                height: height as u32,
            });
        }
    }
    Ok(displays)
}
struct Acquired(Option<IDXGIOutputDuplication>);
impl Acquired {
    fn release(mut self) -> Result<(), CaptureError> {
        if let Some(duplication) = self.0.take() {
            unsafe { duplication.ReleaseFrame() }.map_err(native)?;
        }
        Ok(())
    }
}
impl Drop for Acquired {
    fn drop(&mut self) {
        if let Some(duplication) = self.0.take() {
            let _ = unsafe { duplication.ReleaseFrame() };
        }
    }
}
struct Mapped<'a> {
    context: &'a ID3D11DeviceContext,
    texture: &'a ID3D11Texture2D,
}
impl Drop for Mapped<'_> {
    fn drop(&mut self) {
        unsafe {
            self.context.Unmap(self.texture, 0);
        }
    }
}
pub struct WindowsCapture {
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    duplication: IDXGIOutputDuplication,
    output: IDXGIOutput1,
    display: Display,
    fallback: Option<gdi::GdiCapture>,
    received_visible_frame: bool,
    staging: Option<(u32, u32, ID3D11Texture2D)>,
}
impl WindowsCapture {
    pub fn open(display: &Display) -> Result<Self, CaptureError> {
        let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }.map_err(native)?;
        let adapter = unsafe { factory.EnumAdapters1(display.adapter) }.map_err(native)?;
        let output = unsafe { adapter.EnumOutputs(display.output) }.map_err(native)?;
        let description = unsafe { output.GetDesc() }.map_err(native)?;
        if !description.AttachedToDesktop.as_bool() {
            return Err(CaptureError::Target);
        }
        // Re-resolve the selected display. A topology change must require a new
        // selection rather than silently sharing a different monitor.
        let end = description
            .DeviceName
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(description.DeviceName.len());
        if String::from_utf16_lossy(&description.DeviceName[..end]) != display.name {
            return Err(CaptureError::Target);
        }
        let adapter: IDXGIAdapter = adapter.cast().map_err(native)?;
        let mut device = None;
        let mut context = None;
        // SAFETY: output pointers address live initialized Options. An explicit
        // adapter requires UNKNOWN driver type and no software module.
        unsafe {
            D3D11CreateDevice(
                &adapter,
                D3D_DRIVER_TYPE_UNKNOWN,
                HMODULE::default(),
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                None,
                D3D11_SDK_VERSION,
                Some(&mut device),
                None,
                Some(&mut context),
            )
        }
        .map_err(native)?;
        let device =
            device.ok_or_else(|| CaptureError::Native("Windows returned no D3D device".into()))?;
        let context = context
            .ok_or_else(|| CaptureError::Native("Windows returned no D3D context".into()))?;
        let output: IDXGIOutput1 = output.cast().map_err(native)?;
        let duplication = unsafe { output.DuplicateOutput(&device) }.map_err(native)?;
        Ok(Self {
            device,
            context,
            duplication,
            output,
            display: display.clone(),
            fallback: None,
            received_visible_frame: false,
            staging: None,
        })
    }
    /// A timeout is a normal unchanged-screen result. Desktop switches, lock
    /// screens and display removal fail closed; the caller must reselect/reopen.
    pub fn next_frame(&mut self, timeout: Duration) -> Result<Option<CapturedFrame>, CaptureError> {
        let mut info = DXGI_OUTDUPL_FRAME_INFO::default();
        let mut resource = None;
        let milliseconds = timeout
            .as_millis()
            .clamp(1, if self.fallback.is_some() { 50 } else { 250 })
            as u32;
        match unsafe {
            self.duplication
                .AcquireNextFrame(milliseconds, &mut info, &mut resource)
        } {
            Ok(()) => {}
            Err(error) if error.code() == DXGI_ERROR_WAIT_TIMEOUT => {
                return if self.fallback.is_some() {
                    self.fallback_frame().map(Some)
                } else {
                    Ok(None)
                };
            }
            Err(error) if error.code() == DXGI_ERROR_ACCESS_LOST => {
                return Err(CaptureError::Target);
            }
            Err(error) => return Err(native(error)),
        }
        let acquired = Acquired(Some(self.duplication.clone()));
        let resource = resource
            .ok_or_else(|| CaptureError::Native("Windows returned no desktop resource".into()))?;
        let texture: ID3D11Texture2D = resource.cast().map_err(native)?;
        let mut description = D3D11_TEXTURE2D_DESC::default();
        unsafe {
            texture.GetDesc(&mut description);
        }
        let width = description.Width as usize;
        let height = description.Height as usize;
        if width == 0
            || height == 0
            || width > 8192
            || height > 8192
            || description.Format != DXGI_FORMAT_B8G8R8A8_UNORM
            || description.SampleDesc.Count != 1
        {
            return Err(CaptureError::Native(
                "Unsupported desktop texture layout".into(),
            ));
        }
        let stride = width
            .checked_mul(4)
            .ok_or_else(|| CaptureError::Native("Desktop row overflow".into()))?;
        let length = stride
            .checked_mul(height)
            .filter(|bytes| *bytes <= 256 * 1024 * 1024)
            .ok_or_else(|| CaptureError::Native("Desktop frame exceeds limit".into()))?;
        if !self
            .staging
            .as_ref()
            .is_some_and(|(w, h, _)| *w == description.Width && *h == description.Height)
        {
            description.Usage = D3D11_USAGE_STAGING;
            description.BindFlags = 0;
            description.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
            description.MiscFlags = 0;
            description.MipLevels = 1;
            description.ArraySize = 1;
            let mut staging = None;
            unsafe {
                self.device
                    .CreateTexture2D(&description, None, Some(&mut staging))
            }
            .map_err(native)?;
            self.staging = Some((
                description.Width,
                description.Height,
                staging.ok_or_else(|| {
                    CaptureError::Native("Windows returned no staging texture".into())
                })?,
            ));
        }
        let staging = &self.staging.as_ref().ok_or(CaptureError::Stopped)?.2;
        unsafe {
            self.context.CopyResource(staging, &texture);
        }
        let mut mapping = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe {
            self.context
                .Map(staging, 0, D3D11_MAP_READ, 0, Some(&mut mapping))
        }
        .map_err(native)?;
        let mapped = Mapped {
            context: &self.context,
            texture: staging,
        };
        let pitch = mapping.RowPitch as usize;
        if mapping.pData.is_null()
            || pitch < stride
            || pitch
                .checked_mul(height)
                .is_none_or(|bytes| bytes > 256 * 1024 * 1024)
        {
            return Err(CaptureError::Native("Invalid mapped desktop layout".into()));
        }
        let mut pixels = Zeroizing::new(vec![0; length]);
        for row in 0..height {
            // SAFETY: Map guarantees RowPitch rows for this texture. Bounds and
            // products were checked above; copy active pixels only, never padding.
            let source = unsafe {
                std::slice::from_raw_parts(mapping.pData.cast::<u8>().add(row * pitch), stride)
            };
            pixels[row * stride..(row + 1) * stride].copy_from_slice(source);
        }
        for pixel in pixels.as_chunks_mut::<4>().0 {
            pixel[3] = 255;
        }
        drop(mapped);
        let black = (!self.received_visible_frame || self.fallback.is_some())
            && pixels
                .as_chunks::<4>()
                .0
                .iter()
                .all(|pixel| pixel[..3] == [0, 0, 0]);
        if info.ProtectedContentMaskedOut.as_bool() {
            self.fallback = None;
        } else if black && !self.received_visible_frame {
            if self.fallback.is_none() && gdi::default_input_desktop() {
                self.fallback = gdi::GdiCapture::open(&self.display).ok();
            }
            if self.fallback.is_some() {
                acquired.release()?;
                return self.fallback_frame().map(Some);
            }
        } else if !black {
            self.received_visible_frame = true;
            self.fallback = None;
        }
        let rotation = unsafe { self.duplication.GetDesc() }.Rotation;
        let (out_width, out_height, rotated) = if rotation == DXGI_MODE_ROTATION_IDENTITY
            || rotation == DXGI_MODE_ROTATION_UNSPECIFIED
        {
            (width, height, std::mem::take(&mut *pixels))
        } else {
            rotate(width, height, &pixels, rotation)?
        };
        acquired.release()?;
        CapturedFrame::bgra(out_width, out_height, out_width * 4, rotated)
            .map(Some)
            .map_err(|error| CaptureError::Native(error.to_string()))
    }
    fn fallback_frame(&mut self) -> Result<CapturedFrame, CaptureError> {
        let desc = unsafe { self.output.GetDesc() }.map_err(native)?;
        let bounds = desc.DesktopCoordinates;
        if !desc.AttachedToDesktop.as_bool()
            || bounds.left != self.display.left
            || bounds.top != self.display.top
            || i64::from(bounds.right) - i64::from(bounds.left) != i64::from(self.display.width)
            || i64::from(bounds.bottom) - i64::from(bounds.top) != i64::from(self.display.height)
        {
            return Err(CaptureError::Target);
        }
        self.fallback
            .as_mut()
            .ok_or(CaptureError::Stopped)?
            .frame(&self.display)
    }
}
fn rotate(
    width: usize,
    height: usize,
    pixels: &[u8],
    rotation: DXGI_MODE_ROTATION,
) -> Result<(usize, usize, Vec<u8>), CaptureError> {
    let quarter =
        rotation == DXGI_MODE_ROTATION_ROTATE90 || rotation == DXGI_MODE_ROTATION_ROTATE270;
    let (out_width, out_height) = if quarter {
        (height, width)
    } else {
        (width, height)
    };
    if rotation != DXGI_MODE_ROTATION_IDENTITY
        && rotation != DXGI_MODE_ROTATION_UNSPECIFIED
        && !quarter
        && rotation != DXGI_MODE_ROTATION_ROTATE180
    {
        return Err(CaptureError::Native("Unsupported display rotation".into()));
    }
    let mut output = vec![0; pixels.len()];
    for y in 0..height {
        for x in 0..width {
            let (dx, dy) = if rotation == DXGI_MODE_ROTATION_ROTATE90 {
                (height - 1 - y, x)
            } else if rotation == DXGI_MODE_ROTATION_ROTATE180 {
                (width - 1 - x, height - 1 - y)
            } else if rotation == DXGI_MODE_ROTATION_ROTATE270 {
                (y, width - 1 - x)
            } else {
                (x, y)
            };
            output[(dy * out_width + dx) * 4..(dy * out_width + dx) * 4 + 4]
                .copy_from_slice(&pixels[(y * width + x) * 4..(y * width + x) * 4 + 4]);
        }
    }
    Ok((out_width, out_height, output))
}
