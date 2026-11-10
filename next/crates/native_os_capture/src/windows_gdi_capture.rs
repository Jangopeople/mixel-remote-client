//! Compatibility capture for displays whose DXGI driver supplies an all-black
//! texture on a visible, ordinary Windows desktop.
use super::{Display, native};
use crate::stream::{CaptureError, CapturedFrame};
use std::{ffi::c_void, ptr::NonNull};
use windows::Win32::{
    Foundation::HANDLE,
    Graphics::Gdi::*,
    System::{StationsAndDesktops::*, Threading::GetCurrentThreadId},
    UI::WindowsAndMessaging::WSF_VISIBLE,
};
use zeroize::{Zeroize, Zeroizing};

pub(super) fn default_input_desktop() -> bool {
    // SAFETY: every queried object is valid for the scope of the call, and
    // OpenInputDesktop's owned handle is closed on every successful acquisition.
    unsafe {
        let Ok(station) = GetProcessWindowStation() else {
            return false;
        };
        let mut flags = USEROBJECTFLAGS::default();
        if GetUserObjectInformationW(
            HANDLE(station.0),
            UOI_FLAGS,
            Some((&raw mut flags).cast()),
            size_of::<USEROBJECTFLAGS>() as u32,
            None,
        )
        .is_err()
            || flags.dwFlags & WSF_VISIBLE as u32 == 0
        {
            return false;
        }
        let Ok(thread) = GetThreadDesktop(GetCurrentThreadId()) else {
            return false;
        };
        let Ok(input) = OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_READOBJECTS)
        else {
            return false;
        };
        let desktop_name = |desktop: HDESK| {
            let mut name = [0u16; 64];
            GetUserObjectInformationW(
                HANDLE(desktop.0),
                UOI_NAME,
                Some(name.as_mut_ptr().cast()),
                size_of_val(&name) as u32,
                None,
            )
            .ok()?;
            let end = name.iter().position(|unit| *unit == 0)?;
            Some(String::from_utf16_lossy(&name[..end]))
        };
        let current = desktop_name(thread);
        let active = desktop_name(input);
        let _ = CloseDesktop(input);
        current.as_deref() == Some("Default") && active == current
    }
}

pub(super) struct GdiCapture {
    screen: HDC,
    memory: HDC,
    bitmap: HBITMAP,
    previous: HGDIOBJ,
    pixels: Option<NonNull<u8>>,
    bytes: usize,
}

impl GdiCapture {
    pub(super) fn open(display: &Display) -> Result<Self, CaptureError> {
        if !default_input_desktop() {
            return Err(CaptureError::Permission);
        }
        let bytes = display.width as usize * display.height as usize * 4;
        let mut capture = Self {
            screen: HDC::default(),
            memory: HDC::default(),
            bitmap: HBITMAP::default(),
            previous: HGDIOBJ::default(),
            pixels: None,
            bytes,
        };
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: display.width as i32,
                biHeight: -(display.height as i32),
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        // SAFETY: the owner thread retains every handle. The initialized DIB
        // has exactly width * height * 4 bytes and a top-down layout.
        unsafe {
            capture.screen = GetDC(None);
            if capture.screen.is_invalid() {
                return Err(native(windows::core::Error::from_thread()));
            }
            capture.memory = CreateCompatibleDC(Some(capture.screen));
            if capture.memory.is_invalid() {
                return Err(native(windows::core::Error::from_thread()));
            }
            let mut pixels: *mut c_void = std::ptr::null_mut();
            capture.bitmap = CreateDIBSection(
                Some(capture.screen),
                &info,
                DIB_RGB_COLORS,
                &mut pixels,
                None,
                0,
            )
            .map_err(native)?;
            capture.pixels = NonNull::new(pixels.cast::<u8>());
            if capture.pixels.is_none() {
                return Err(CaptureError::Native(
                    "Windows returned no fallback frame storage".into(),
                ));
            }
            capture.previous = SelectObject(capture.memory, HGDIOBJ(capture.bitmap.0));
            if capture.previous.is_invalid() {
                return Err(native(windows::core::Error::from_thread()));
            }
        }
        Ok(capture)
    }

    pub(super) fn frame(&mut self, display: &Display) -> Result<CapturedFrame, CaptureError> {
        if !default_input_desktop() {
            return Err(CaptureError::Permission);
        }
        let storage = self.pixels.ok_or(CaptureError::Stopped)?;
        // SAFETY: all resources belong to this thread and have the selected
        // display's dimensions. Flush waits for the bitmap write before copying.
        let mut pixels = unsafe {
            BitBlt(
                self.memory,
                0,
                0,
                display.width as i32,
                display.height as i32,
                Some(self.screen),
                display.left,
                display.top,
                SRCCOPY | CAPTUREBLT,
            )
            .map_err(native)?;
            if !GdiFlush().as_bool() {
                return Err(native(windows::core::Error::from_thread()));
            }
            Zeroizing::new(std::slice::from_raw_parts(storage.as_ptr(), self.bytes).to_vec())
        };
        if !default_input_desktop() {
            return Err(CaptureError::Permission);
        }
        for pixel in pixels.as_chunks_mut::<4>().0 {
            pixel[3] = 255;
        }
        CapturedFrame::bgra(
            display.width as usize,
            display.height as usize,
            display.width as usize * 4,
            std::mem::take(&mut *pixels),
        )
        .map_err(|error| CaptureError::Native(error.to_string()))
    }
}

impl Drop for GdiCapture {
    fn drop(&mut self) {
        // SAFETY: deselect before deletion and release handles in reverse
        // acquisition order. The memory is writable until its bitmap is deleted.
        unsafe {
            if !self.previous.is_invalid() {
                let _ = SelectObject(self.memory, self.previous);
            }
            if let Some(pixels) = self.pixels.take() {
                std::slice::from_raw_parts_mut(pixels.as_ptr(), self.bytes).zeroize();
            }
            if !self.bitmap.is_invalid() {
                let _ = DeleteObject(HGDIOBJ(self.bitmap.0));
            }
            if !self.memory.is_invalid() {
                let _ = DeleteDC(self.memory);
            }
            if !self.screen.is_invalid() {
                let _ = ReleaseDC(None, self.screen);
            }
        }
    }
}
