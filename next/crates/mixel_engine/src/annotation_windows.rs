//! Windows: the technician's drawings in two layered, click-through, always-on-top windows over
//! the shared display, owned by the drawing thread (see `annotation`). The highlighter window is
//! see-through; the pen window above it is opaque. Magenta is the see-through colour key, and
//! both windows are left out of the technician's picture (`WDA_EXCLUDEFROMCAPTURE`), so the
//! technician's app shows the strokes itself.
use crate::annotation::{Shape, ShapeKind};
use std::cell::Cell;
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    BS_SOLID, BeginPaint, BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, CreatePen,
    CreateSolidBrush, DeleteDC, DeleteObject, Ellipse, EndPaint, ExtCreatePen, FillRect, GetDC,
    HDC, HGDIOBJ, LOGBRUSH, PAINTSTRUCT, PS_ENDCAP_ROUND, PS_GEOMETRIC, PS_JOIN_ROUND, PS_SOLID,
    Polygon, Polyline, ReleaseDC, SRCCOPY, SelectObject,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::WindowsAndMessaging::*;
use windows::core::{PCWSTR, w};

/// See-through colour (BGR magenta); no palette colour uses it.
const KEY: u32 = 0x00FF_00FF;

thread_local! {
    /// Windows asked this thread's overlay to paint again (shown, uncovered).
    static REPAINT: Cell<bool> = const { Cell::new(false) };
}

unsafe extern "system" fn overlay_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == WM_PAINT {
        let mut paint = PAINTSTRUCT::default();
        // SAFETY: standard paint cycle on our own window; the drawing thread paints the content.
        unsafe {
            BeginPaint(window, &mut paint);
            let _ = EndPaint(window, &paint);
        }
        REPAINT.with(|repaint| repaint.set(true));
        return LRESULT(0);
    }
    if message == WM_ERASEBKGND {
        return LRESULT(1);
    }
    // SAFETY: default handling for everything else.
    unsafe { DefWindowProcW(window, message, wparam, lparam) }
}

pub struct Overlay {
    /// Highlighter below, pen above.
    layers: [HWND; 2],
    rect: (i32, i32, i32, i32),
}

/// Screen rectangle of the shared display; the whole desktop when it is not known.
fn rect(source: &str) -> (i32, i32, i32, i32) {
    if let Ok(display) = crate::session_platform::selected(source)
        && display.width > 0
        && display.height > 0
    {
        return (
            display.left,
            display.top,
            display.width as i32,
            display.height as i32,
        );
    }
    // SAFETY: plain system metrics.
    unsafe {
        (
            GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_YVIRTUALSCREEN),
            GetSystemMetrics(SM_CXVIRTUALSCREEN),
            GetSystemMetrics(SM_CYVIRTUALSCREEN),
        )
    }
}

impl Overlay {
    pub fn open(source: &str) -> Result<Self, String> {
        let rect = rect(source);
        let (x, y, width, height) = rect;
        if width <= 0 || height <= 0 {
            return Err("The shared display is not available".into());
        }
        // SAFETY: window creation on this thread, which pumps their messages (see `pump`).
        unsafe {
            let instance = GetModuleHandleW(PCWSTR::null()).map_err(|_| "No module handle")?;
            let class = w!("MixelRemoteDrawings");
            let description = WNDCLASSW {
                lpfnWndProc: Some(overlay_proc),
                hInstance: instance.into(),
                lpszClassName: class,
                ..Default::default()
            };
            // Registering again after the first overlay fails harmlessly.
            RegisterClassW(&description);
            let mut layers = [HWND::default(); 2];
            for layer in &mut layers {
                let window = CreateWindowExW(
                    WS_EX_TOPMOST
                        | WS_EX_TOOLWINDOW
                        | WS_EX_LAYERED
                        | WS_EX_TRANSPARENT
                        | WS_EX_NOACTIVATE,
                    class,
                    w!("Mixel Remote drawings"),
                    WS_POPUP,
                    x,
                    y,
                    width,
                    height,
                    None,
                    None,
                    Some(instance.into()),
                    None,
                )
                .map_err(|_| "Windows did not open the drawing overlay")?;
                let _ =
                    SetLayeredWindowAttributes(window, COLORREF(KEY), 0, LWA_COLORKEY | LWA_ALPHA);
                // The technician's picture does not contain the drawings: their app draws them.
                let _ = SetWindowDisplayAffinity(window, WDA_EXCLUDEFROMCAPTURE);
                *layer = window;
            }
            let overlay = Self { layers, rect };
            for window in overlay.layers {
                overlay.paint(window, &[], false);
                let _ = ShowWindow(window, SW_SHOWNOACTIVATE);
            }
            Ok(overlay)
        }
    }

    pub fn size(&self) -> (f64, f64) {
        (f64::from(self.rect.2), f64::from(self.rect.3))
    }

    /// Handles this thread's window messages; true when the windows need painting again.
    pub fn pump(&mut self) -> bool {
        let mut message = MSG::default();
        // SAFETY: messages of windows owned by this thread.
        unsafe {
            while PeekMessageW(&mut message, None, 0, 0, PM_REMOVE).as_bool() {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        REPAINT.with(|repaint| repaint.replace(false))
    }

    /// Stays above windows that opened later (the highlighter below the pen).
    pub fn raise(&mut self) {
        for window in self.layers {
            // SAFETY: our own windows.
            unsafe {
                let _ = SetWindowPos(
                    window,
                    Some(HWND_TOPMOST),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                );
            }
        }
    }

    pub fn show(&mut self, shapes: Option<&[Shape]>, alpha: f64) {
        let opacity = |share: f64| (alpha.clamp(0.0, 1.0) * share * 255.0).round() as u8;
        for (index, window) in self.layers.into_iter().enumerate() {
            let highlight = index == 0;
            if let Some(shapes) = shapes {
                self.paint(window, shapes, highlight);
            }
            let share = if highlight {
                core_protocol::annotation::HIGHLIGHT_ALPHA
            } else {
                1.0
            };
            // SAFETY: our own window.
            unsafe {
                let _ = SetLayeredWindowAttributes(
                    window,
                    COLORREF(KEY),
                    opacity(share),
                    LWA_COLORKEY | LWA_ALPHA,
                );
            }
        }
    }

    /// Paints the highlighter or the pen shapes onto one window (off screen first, no flicker).
    fn paint(&self, window: HWND, shapes: &[Shape], highlight: bool) {
        let (_, _, width, height) = self.rect;
        // SAFETY: device contexts and GDI objects created here are selected out and deleted here.
        unsafe {
            let screen = GetDC(Some(window));
            if screen.is_invalid() {
                return;
            }
            let memory = CreateCompatibleDC(Some(screen));
            let bitmap = CreateCompatibleBitmap(screen, width, height);
            let previous = SelectObject(memory, HGDIOBJ(bitmap.0));
            let back = CreateSolidBrush(COLORREF(KEY));
            FillRect(
                memory,
                &RECT {
                    left: 0,
                    top: 0,
                    right: width,
                    bottom: height,
                },
                back,
            );
            let _ = DeleteObject(HGDIOBJ(back.0));
            for shape in shapes.iter().filter(|shape| shape.highlight == highlight) {
                draw(memory, shape);
            }
            let _ = BitBlt(screen, 0, 0, width, height, Some(memory), 0, 0, SRCCOPY);
            SelectObject(memory, previous);
            let _ = DeleteObject(HGDIOBJ(bitmap.0));
            let _ = DeleteDC(memory);
            ReleaseDC(Some(window), screen);
        }
    }
}

/// GDI colour (BGR) of a shape; never the see-through key.
fn colour(shape: &Shape) -> COLORREF {
    let [red, green, blue] = shape.rgb;
    let value = u32::from(red) | (u32::from(green) << 8) | (u32::from(blue) << 16);
    COLORREF(if value == KEY { 0x00FE_00FF } else { value })
}

fn point((x, y): (f64, f64)) -> POINT {
    POINT {
        x: x.round() as i32,
        y: y.round() as i32,
    }
}

/// SAFETY: `dc` is a valid memory device context.
unsafe fn draw(dc: HDC, shape: &Shape) {
    let colour = colour(shape);
    let points: Vec<POINT> = shape.points.iter().copied().map(point).collect();
    // SAFETY: pens and brushes are created, selected and deleted here.
    unsafe {
        if shape.kind == ShapeKind::Line {
            let brush = LOGBRUSH {
                lbStyle: BS_SOLID,
                lbColor: colour,
                lbHatch: 0,
            };
            let pen = ExtCreatePen(
                PS_GEOMETRIC | PS_SOLID | PS_ENDCAP_ROUND | PS_JOIN_ROUND,
                shape.width.round().max(1.0) as u32,
                &brush,
                None,
            );
            let previous = SelectObject(dc, HGDIOBJ(pen.0));
            let _ = Polyline(dc, &points);
            SelectObject(dc, previous);
            let _ = DeleteObject(HGDIOBJ(pen.0));
            return;
        }
        let pen = CreatePen(PS_SOLID, 1, colour);
        let brush = CreateSolidBrush(colour);
        let previous_pen = SelectObject(dc, HGDIOBJ(pen.0));
        let previous_brush = SelectObject(dc, HGDIOBJ(brush.0));
        match shape.kind {
            ShapeKind::Polygon => {
                let _ = Polygon(dc, &points);
            }
            _ => {
                if let Some(centre) = points.first() {
                    let radius = (shape.width / 2.0).round().max(1.0) as i32;
                    let _ = Ellipse(
                        dc,
                        centre.x - radius,
                        centre.y - radius,
                        centre.x + radius + 1,
                        centre.y + radius + 1,
                    );
                }
            }
        }
        SelectObject(dc, previous_pen);
        SelectObject(dc, previous_brush);
        let _ = DeleteObject(HGDIOBJ(pen.0));
        let _ = DeleteObject(HGDIOBJ(brush.0));
    }
}

impl Drop for Overlay {
    fn drop(&mut self) {
        for window in self.layers {
            // SAFETY: our own windows, destroyed on the thread that created them.
            unsafe {
                let _ = DestroyWindow(window);
            }
        }
        self.pump();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn colours_are_bgr_and_never_the_see_through_key() {
        let shape = |rgb| Shape {
            kind: ShapeKind::Line,
            rgb,
            highlight: false,
            width: 3.0,
            points: vec![],
        };
        assert_eq!(colour(&shape([0xE5, 0x32, 0x2D])).0, 0x002D_32E5);
        assert_ne!(colour(&shape([0xFF, 0x00, 0xFF])).0, KEY);
        assert_eq!(point((10.4, 10.6)), POINT { x: 10, y: 11 });
    }

    /// Lab check on a Windows desktop: shows strokes for a moment, then closes.
    /// `cargo test -p mixel_engine -- --ignored windows_overlay_opens_and_closes`
    #[test]
    #[ignore = "needs an interactive Windows desktop"]
    fn windows_overlay_opens_and_closes() {
        let mut overlay = Overlay::open("").unwrap();
        let (width, height) = overlay.size();
        assert!(width > 0.0 && height > 0.0);
        let shapes = vec![
            Shape {
                kind: ShapeKind::Line,
                rgb: [0xE5, 0x32, 0x2D],
                highlight: false,
                width: 8.0,
                points: vec![(100.0, 100.0), (400.0, 300.0)],
            },
            Shape {
                kind: ShapeKind::Line,
                rgb: [0xFF, 0xD4, 0x00],
                highlight: true,
                width: 28.0,
                points: vec![(100.0, 400.0), (600.0, 400.0)],
            },
        ];
        overlay.show(Some(&shapes), 1.0);
        for _ in 0..20 {
            overlay.pump();
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        overlay.show(None, 0.5);
        overlay.raise();
        drop(overlay);
    }
}
