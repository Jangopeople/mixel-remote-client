//! Linux X11: the technician's drawings in an override-redirect window over the shared screen,
//! owned by the drawing thread (see `annotation`). The window's shape is exactly the strokes and
//! it has no input region, so clicks pass through. X11 cannot leave it out of the screen
//! picture, so the technician sees the strokes in the picture (the host announces `drawpic`).
//! Without a compositor nothing blends: the highlighter is drawn lighter and below the pen, and
//! the fade-out is the window opacity hint, which compositors apply.
use crate::annotation::{Shape, ShapeKind};
use x11rb::{
    connection::Connection,
    protocol::{
        Event,
        shape::{self, ConnectionExt as _},
        xproto::{self, ConnectionExt as _},
    },
    rust_connection::RustConnection,
    wrapper::ConnectionExt as _,
};

fn fail<E>(_: E) -> String {
    "The X11 display refused the drawing overlay".into()
}

pub struct Overlay {
    connection: RustConnection,
    window: u32,
    mask: u32,
    /// Draws on the 1-bit shape mask.
    mask_pen: u32,
    /// Draws the colours on the window.
    pen: u32,
    size: (u16, u16),
    /// Turns 8-bit red, green and blue into a pixel value of the screen's visual.
    channels: [(u32, u32); 3],
    opacity_atom: u32,
    shapes: Vec<Shape>,
}

/// Shift and width of each colour channel of a TrueColor visual mask.
fn channel(mask: u32) -> (u32, u32) {
    if mask == 0 {
        return (0, 0);
    }
    (mask.trailing_zeros(), mask.count_ones())
}

fn pixel(channels: &[(u32, u32); 3], rgb: [u8; 3]) -> u32 {
    let mut value = 0;
    for ((shift, bits), level) in channels.iter().zip(rgb) {
        let bits = (*bits).min(8);
        if bits > 0 {
            value |= (u32::from(level) >> (8 - bits)) << shift;
        }
    }
    value
}

/// The highlighter where nothing blends: half way to white.
fn lighter(rgb: [u8; 3]) -> [u8; 3] {
    rgb.map(|level| ((u16::from(level) + 255) / 2) as u8)
}

fn coordinate(value: f64) -> i16 {
    value.round().clamp(-32768.0, 32767.0) as i16
}

impl Overlay {
    pub fn open(source: &str) -> Result<Self, String> {
        let (connection, default_screen) =
            x11rb::connect(None).map_err(|_| "The X11 display is not available".to_owned())?;
        let screen_index = source
            .strip_prefix("x11:")
            .and_then(|rest| rest.split(':').next())
            .and_then(|screen| screen.parse::<usize>().ok())
            .unwrap_or(default_screen);
        let roots = &connection.setup().roots;
        let screen = roots
            .get(screen_index)
            .or(roots.first())
            .ok_or("No X11 screen")?
            .clone();
        let size = (screen.width_in_pixels, screen.height_in_pixels);
        let visual = screen
            .allowed_depths
            .iter()
            .flat_map(|depth| depth.visuals.iter())
            .find(|visual| visual.visual_id == screen.root_visual)
            .ok_or("No X11 visual")?;
        let channels = [
            channel(visual.red_mask),
            channel(visual.green_mask),
            channel(visual.blue_mask),
        ];
        let window = connection.generate_id().map_err(fail)?;
        connection
            .create_window(
                screen.root_depth,
                window,
                screen.root,
                0,
                0,
                size.0,
                size.1,
                0,
                xproto::WindowClass::INPUT_OUTPUT,
                screen.root_visual,
                &xproto::CreateWindowAux::new()
                    .override_redirect(1)
                    .event_mask(xproto::EventMask::EXPOSURE),
            )
            .map_err(fail)?;
        let mask = connection.generate_id().map_err(fail)?;
        connection
            .create_pixmap(1, mask, window, size.0, size.1)
            .map_err(fail)?;
        let round = xproto::CreateGCAux::new()
            .cap_style(xproto::CapStyle::ROUND)
            .join_style(xproto::JoinStyle::ROUND);
        let mask_pen = connection.generate_id().map_err(fail)?;
        connection
            .create_gc(mask_pen, mask, &round.foreground(0))
            .map_err(fail)?;
        let pen = connection.generate_id().map_err(fail)?;
        connection.create_gc(pen, window, &round).map_err(fail)?;
        let opacity_atom = connection
            .intern_atom(false, b"_NET_WM_WINDOW_OPACITY")
            .map_err(fail)?
            .reply()
            .map_err(fail)?
            .atom;
        let overlay = Self {
            connection,
            window,
            mask,
            mask_pen,
            pen,
            size,
            channels,
            opacity_atom,
            shapes: Vec::new(),
        };
        // Nothing to see and nothing to click until strokes arrive.
        overlay.set_shape()?;
        overlay
            .connection
            .shape_rectangles(
                shape::SO::SET,
                shape::SK::INPUT,
                xproto::ClipOrdering::UNSORTED,
                window,
                0,
                0,
                &[],
            )
            .map_err(fail)?;
        overlay.connection.map_window(window).map_err(fail)?;
        overlay.connection.flush().map_err(fail)?;
        Ok(overlay)
    }

    pub fn size(&self) -> (f64, f64) {
        (f64::from(self.size.0), f64::from(self.size.1))
    }

    /// Reads X11 events; true when the window must be painted again.
    pub fn pump(&mut self) -> bool {
        let mut repaint = false;
        while let Ok(Some(event)) = self.connection.poll_for_event() {
            repaint |= matches!(event, Event::Expose(_));
        }
        repaint
    }

    pub fn raise(&mut self) {
        let _ = self.connection.configure_window(
            self.window,
            &xproto::ConfigureWindowAux::new().stack_mode(xproto::StackMode::ABOVE),
        );
        let _ = self.connection.flush();
    }

    pub fn show(&mut self, shapes: Option<&[Shape]>, alpha: f64) {
        if let Some(shapes) = shapes {
            // The highlighter goes below the pen: it is not see-through here.
            self.shapes = shapes
                .iter()
                .filter(|shape| shape.highlight)
                .chain(shapes.iter().filter(|shape| !shape.highlight))
                .cloned()
                .collect();
        }
        let opacity = (alpha.clamp(0.0, 1.0) * f64::from(u32::MAX)) as u32;
        let _ = self.connection.change_property32(
            xproto::PropMode::REPLACE,
            self.window,
            self.opacity_atom,
            xproto::AtomEnum::CARDINAL,
            &[opacity],
        );
        if shapes.is_some() {
            let _ = self.set_shape().and_then(|()| self.paint());
        }
        let _ = self.connection.flush();
    }

    /// The window's visible region: exactly the strokes.
    fn set_shape(&self) -> Result<(), String> {
        let connection = &self.connection;
        connection
            .change_gc(self.mask_pen, &xproto::ChangeGCAux::new().foreground(0))
            .map_err(fail)?;
        connection
            .poly_fill_rectangle(
                self.mask,
                self.mask_pen,
                &[xproto::Rectangle {
                    x: 0,
                    y: 0,
                    width: self.size.0,
                    height: self.size.1,
                }],
            )
            .map_err(fail)?;
        for shape in &self.shapes {
            self.draw(self.mask, self.mask_pen, shape, 1)?;
        }
        connection
            .shape_mask(
                shape::SO::SET,
                shape::SK::BOUNDING,
                self.window,
                0,
                0,
                self.mask,
            )
            .map_err(fail)?;
        Ok(())
    }

    fn paint(&self) -> Result<(), String> {
        for shape in &self.shapes {
            let rgb = if shape.highlight {
                lighter(shape.rgb)
            } else {
                shape.rgb
            };
            self.draw(self.window, self.pen, shape, pixel(&self.channels, rgb))?;
        }
        Ok(())
    }

    fn draw(&self, target: u32, pen: u32, shape: &Shape, colour: u32) -> Result<(), String> {
        let connection = &self.connection;
        let width = shape.width.round().clamp(1.0, 1000.0) as u32;
        connection
            .change_gc(
                pen,
                &xproto::ChangeGCAux::new()
                    .foreground(colour)
                    .line_width(width),
            )
            .map_err(fail)?;
        let points: Vec<xproto::Point> = shape
            .points
            .iter()
            .map(|(x, y)| xproto::Point {
                x: coordinate(*x),
                y: coordinate(*y),
            })
            .collect();
        match shape.kind {
            ShapeKind::Line => {
                connection
                    .poly_line(xproto::CoordMode::ORIGIN, target, pen, &points)
                    .map_err(fail)?;
            }
            ShapeKind::Polygon => {
                connection
                    .fill_poly(
                        target,
                        pen,
                        xproto::PolyShape::CONVEX,
                        xproto::CoordMode::ORIGIN,
                        &points,
                    )
                    .map_err(fail)?;
            }
            ShapeKind::Dot => {
                if let Some(centre) = points.first() {
                    let diameter = width.min(u32::from(u16::MAX)) as u16;
                    let radius = (diameter / 2) as i16;
                    connection
                        .poly_fill_arc(
                            target,
                            pen,
                            &[xproto::Arc {
                                x: centre.x.saturating_sub(radius),
                                y: centre.y.saturating_sub(radius),
                                width: diameter,
                                height: diameter,
                                angle1: 0,
                                angle2: 360 * 64,
                            }],
                        )
                        .map_err(fail)?;
                }
            }
        }
        Ok(())
    }
}

impl Drop for Overlay {
    fn drop(&mut self) {
        let _ = self.connection.free_gc(self.pen);
        let _ = self.connection.free_gc(self.mask_pen);
        let _ = self.connection.free_pixmap(self.mask);
        let _ = self.connection.destroy_window(self.window);
        let _ = self.connection.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn colours_follow_the_visual_and_the_highlighter_is_lighter() {
        let truecolour = [
            channel(0x00FF_0000),
            channel(0x0000_FF00),
            channel(0x0000_00FF),
        ];
        assert_eq!(pixel(&truecolour, [0xE5, 0x32, 0x2D]), 0x00E5_322D);
        let sixteen = [channel(0xF800), channel(0x07E0), channel(0x001F)];
        assert_eq!(pixel(&sixteen, [255, 255, 255]), 0xFFFF);
        assert_eq!(pixel(&sixteen, [0, 0, 255]), 0x001F);
        assert_eq!(lighter([0, 128, 255]), [127, 191, 255]);
        assert_eq!(coordinate(1e9), i16::MAX);
    }

    /// Lab check in a real X11 session (Xvfb works): the overlay opens, shows strokes, stays
    /// click-through, and leaves nothing behind.
    /// `DISPLAY=:99 cargo test -p mixel_engine -- --ignored x11_drawings_show_and_close`
    #[test]
    #[ignore = "needs an X11 display"]
    fn x11_drawings_show_and_close() {
        let mut overlay = Overlay::open("x11:0:1").unwrap();
        let (width, height) = overlay.size();
        assert!(width > 100.0 && height > 100.0);
        let shapes = vec![
            Shape {
                kind: ShapeKind::Line,
                rgb: [0xE5, 0x32, 0x2D],
                highlight: false,
                width: 8.0,
                points: vec![(10.0, 10.0), (90.0, 10.0)],
            },
            Shape {
                kind: ShapeKind::Line,
                rgb: [0xFF, 0xD4, 0x00],
                highlight: true,
                width: 20.0,
                points: vec![(10.0, 60.0), (90.0, 60.0)],
            },
            Shape {
                kind: ShapeKind::Polygon,
                rgb: [0x1E, 0x7B, 0xFF],
                highlight: false,
                width: 4.0,
                points: vec![(50.0, 80.0), (40.0, 95.0), (60.0, 95.0)],
            },
            Shape {
                kind: ShapeKind::Dot,
                rgb: [0xFF, 0xFF, 0xFF],
                highlight: false,
                width: 6.0,
                points: vec![(5.0, 95.0)],
            },
        ];
        overlay.show(Some(&shapes), 1.0);
        overlay.pump();
        overlay.raise();
        // A round trip: everything above has been drawn.
        overlay
            .connection
            .get_input_focus()
            .unwrap()
            .reply()
            .unwrap();
        let (check, _) = x11rb::connect(None).unwrap();
        let root = check.setup().roots[0].root;
        let picture = |x: i16, y: i16| {
            let image = check
                .get_image(xproto::ImageFormat::Z_PIXMAP, root, x, y, 1, 1, u32::MAX)
                .unwrap()
                .reply()
                .unwrap();
            u32::from_le_bytes([image.data[0], image.data[1], image.data[2], 0])
        };
        // The strokes are on the screen (and so in the captured picture), the rest is untouched.
        assert_eq!(picture(50, 10), 0x00E5_322D, "pen");
        assert_eq!(
            picture(50, 60),
            pixel(&overlay.channels, lighter([0xFF, 0xD4, 0x00])),
            "highlighter"
        );
        assert_eq!(picture(50, 90), 0x001E_7BFF, "arrowhead");
        let outside = picture(200, 200);
        // No input region: a click goes to the window below.
        let input = check
            .shape_get_rectangles(overlay.window, shape::SK::INPUT)
            .unwrap()
            .reply()
            .unwrap();
        assert!(input.rectangles.is_empty());
        let bounding = check
            .shape_get_rectangles(overlay.window, shape::SK::BOUNDING)
            .unwrap()
            .reply()
            .unwrap();
        assert!(!bounding.rectangles.is_empty());
        overlay.show(Some(&[]), 0.5);
        overlay.show(None, 0.2);
        let window = overlay.window;
        drop(overlay);
        assert!(
            check
                .get_window_attributes(window)
                .unwrap()
                .reply()
                .is_err(),
            "the window is gone"
        );
        assert_eq!(picture(200, 200), outside);
        println!(
            "x11 drawings: pen, highlighter and head shown at {width}x{height}, click-through, closed"
        );
    }
}
