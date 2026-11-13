//! The technician's drawings on the computer being helped (host side). Strokes arrive from each
//! technician's control channel (`core_protocol::annotation`), are kept on one board per
//! process, keyed by technician and stroke id, and shown in a click-through, always-on-top,
//! transparent overlay over the shared display:
//! - Windows: two layered windows (pen and see-through highlighter) left out of the capture
//!   (`WDA_EXCLUDEFROMCAPTURE`), see `annotation_windows.rs`,
//! - macOS: a borderless window left out of the ScreenCaptureKit capture (`macos_guard`),
//! - Linux X11: a shaped override-redirect window without an input region, see
//!   `annotation_x11.rs`; it is part of the captured picture (`drawpic`).
//!
//! The whole board fades out a few seconds after the last change and is then cleared. Undo and
//! clear act at once; a technician's strokes go when their session ends, and everything goes when
//! the last technician leaves (`session_guard::Guard`).
use core_protocol::annotation::{self as wire, Message, Tool};
use std::time::{Duration, Instant};

/// What the overlay draws, in pixels from the top left of the shared display.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShapeKind {
    /// A round-capped line through the points.
    Line,
    /// A filled polygon (the arrowhead).
    Polygon,
    /// A filled circle at the first point, `width` across.
    Dot,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Shape {
    pub kind: ShapeKind,
    pub rgb: [u8; 3],
    /// Highlighter: see-through where the platform can blend.
    pub highlight: bool,
    pub width: f64,
    pub points: Vec<(f64, f64)>,
}

struct Stroke {
    owner: u64,
    id: u16,
    tool: Tool,
    colour: u8,
    width: u8,
    points: Vec<(u16, u16)>,
    done: bool,
    updated: Instant,
}

struct Rate {
    owner: u64,
    since: Instant,
    count: u32,
}

/// The strokes shown on this computer, with the timing that fades them out.
#[derive(Default)]
pub struct Board {
    strokes: Vec<Stroke>,
    rates: Vec<Rate>,
    last_change: Option<Instant>,
    version: u64,
}

impl Board {
    pub const fn new() -> Self {
        Self {
            strokes: Vec::new(),
            rates: Vec::new(),
            last_change: None,
            version: 0,
        }
    }

    pub fn is_empty(&self) -> bool {
        self.strokes.is_empty()
    }

    /// Grows whenever what the board shows changes.
    pub fn version(&self) -> u64 {
        self.version
    }

    fn changed(&mut self, now: Instant) -> bool {
        self.version += 1;
        self.last_change = Some(now);
        true
    }

    /// Whether `owner` may send another record now (at most `MAX_RECORDS_PER_SECOND`).
    fn allowed(&mut self, owner: u64, now: Instant) -> bool {
        let index = match self.rates.iter().position(|rate| rate.owner == owner) {
            Some(index) => index,
            None => {
                self.rates.push(Rate {
                    owner,
                    since: now,
                    count: 0,
                });
                self.rates.len() - 1
            }
        };
        let rate = &mut self.rates[index];
        if now.saturating_duration_since(rate.since) >= Duration::from_secs(1) {
            rate.since = now;
            rate.count = 0;
        }
        rate.count += 1;
        rate.count <= wire::MAX_RECORDS_PER_SECOND
    }

    /// Applies one technician's message; true when what the board shows changed.
    pub fn apply(&mut self, owner: u64, message: Message, now: Instant) -> bool {
        if !self.allowed(owner, now) || message.validate().is_err() {
            return false;
        }
        match message {
            Message::Stroke {
                id,
                tool,
                colour,
                width,
                points,
                done,
            } => {
                let existing = self
                    .strokes
                    .iter()
                    .position(|stroke| stroke.owner == owner && stroke.id == id);
                if let Some(index) = existing
                    && !self.strokes[index].done
                {
                    let stroke = &mut self.strokes[index];
                    let room = wire::MAX_POINTS_PER_STROKE - stroke.points.len();
                    stroke.points.extend(points.into_iter().take(room));
                    stroke.done = done;
                    stroke.updated = now;
                    return self.changed(now);
                }
                // Nothing to add to a finished or unknown stroke (a repeated end).
                if points.is_empty() {
                    return false;
                }
                // A finished stroke with the same id: the id was used again for a new stroke.
                if let Some(index) = existing {
                    self.strokes.remove(index);
                }
                self.strokes.push(Stroke {
                    owner,
                    id,
                    tool,
                    colour,
                    width,
                    points,
                    done,
                    updated: now,
                });
                let mut extra = self
                    .strokes
                    .iter()
                    .filter(|stroke| stroke.owner == owner)
                    .count()
                    .saturating_sub(wire::MAX_STROKES);
                self.strokes.retain(|stroke| {
                    let drop = extra > 0 && stroke.owner == owner;
                    extra -= usize::from(drop);
                    !drop
                });
                self.changed(now)
            }
            Message::Undo => match self
                .strokes
                .iter()
                .rposition(|stroke| stroke.owner == owner)
            {
                Some(index) => {
                    self.strokes.remove(index);
                    self.changed(now)
                }
                None => false,
            },
            Message::Clear => self.forget(owner, now),
        }
    }

    /// Removes one technician's strokes (Clear, or their session ended).
    pub fn forget(&mut self, owner: u64, now: Instant) -> bool {
        let before = self.strokes.len();
        self.strokes.retain(|stroke| stroke.owner != owner);
        before != self.strokes.len() && self.changed(now)
    }

    pub fn clear(&mut self, now: Instant) -> bool {
        self.rates.clear();
        let had = !self.strokes.is_empty();
        self.strokes.clear();
        had && self.changed(now)
    }

    /// How opaque the board is now (1 while drawing and for `FADE_AFTER_MS`, then fading);
    /// `None` when nothing is shown. A faded board is cleared.
    pub fn opacity(&mut self, now: Instant) -> Option<f64> {
        let last = self.last_change.filter(|_| !self.strokes.is_empty())?;
        let fade_after = Duration::from_millis(wire::FADE_AFTER_MS);
        // A stroke still being drawn keeps the board; one abandoned half-way does not.
        if self.strokes.iter().any(|stroke| {
            !stroke.done && now.saturating_duration_since(stroke.updated) < fade_after
        }) {
            return Some(1.0);
        }
        let since = now.saturating_duration_since(last);
        if since < fade_after {
            return Some(1.0);
        }
        let fading =
            (since - fade_after).as_secs_f64() / Duration::from_millis(wire::FADE_MS).as_secs_f64();
        if fading < 1.0 {
            return Some(1.0 - fading);
        }
        self.clear(now);
        None
    }

    /// The shapes to draw on a display `width` by `height` pixels, oldest first.
    pub fn shapes(&self, width: f64, height: f64) -> Vec<Shape> {
        let place = |(x, y): (u16, u16)| {
            (
                f64::from(x) * (width - 1.0).max(0.0) / 65535.0,
                f64::from(y) * (height - 1.0).max(0.0) / 65535.0,
            )
        };
        let mut shapes = Vec::with_capacity(self.strokes.len() + 4);
        for stroke in &self.strokes {
            let rgb = wire::PALETTE[usize::from(stroke.colour).min(wire::PALETTE.len() - 1)];
            let line = wire::line_width(stroke.tool, stroke.width, height);
            let highlight = stroke.tool == Tool::Highlighter;
            let shape = |kind, width, points| Shape {
                kind,
                rgb,
                highlight,
                width,
                points,
            };
            if stroke.tool == Tool::Arrow {
                let (Some(first), Some(last)) = (stroke.points.first(), stroke.points.last())
                else {
                    continue;
                };
                shapes.extend(
                    arrow(place(*first), place(*last), line, height)
                        .into_iter()
                        .map(|(kind, points)| shape(kind, line, points)),
                );
                continue;
            }
            let mut points: Vec<(f64, f64)> = Vec::with_capacity(stroke.points.len());
            for point in stroke.points.iter().copied().map(place) {
                if points.last() != Some(&point) {
                    points.push(point);
                }
            }
            match points.len() {
                0 => {}
                1 => shapes.push(shape(ShapeKind::Dot, line, points)),
                _ => shapes.push(shape(ShapeKind::Line, line, points)),
            }
        }
        shapes
    }
}

/// A straight line from `from` to `to` with a filled head at `to`.
pub fn arrow(
    from: (f64, f64),
    to: (f64, f64),
    line: f64,
    height: f64,
) -> Vec<(ShapeKind, Vec<(f64, f64)>)> {
    let (dx, dy) = (to.0 - from.0, to.1 - from.1);
    let length = dx.hypot(dy);
    if length < 1.0 {
        return vec![(ShapeKind::Dot, vec![to])];
    }
    let (ux, uy) = (dx / length, dy / length);
    let head = (line * 3.0)
        .max(12.0 * height.max(1.0) / 1080.0)
        .min(length);
    // The head is 28 degrees to each side of the line.
    let half = head * 0.53;
    let base = (to.0 - ux * head, to.1 - uy * head);
    let mut parts = Vec::with_capacity(2);
    // The line stops inside the head so the tip stays sharp.
    let stop = head * 0.8;
    if length > head {
        parts.push((
            ShapeKind::Line,
            vec![from, (to.0 - ux * stop, to.1 - uy * stop)],
        ));
    }
    parts.push((
        ShapeKind::Polygon,
        vec![
            to,
            (base.0 - uy * half, base.1 + ux * half),
            (base.0 + uy * half, base.1 - ux * half),
        ],
    ));
    parts
}

/// The optional features this app announces for drawing (see `core_protocol::annotation`).
pub fn features() -> Vec<&'static str> {
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    return vec![wire::FEATURE];
    #[cfg(target_os = "linux")]
    return if crate::linux_guard::x11_available() {
        vec![wire::FEATURE, wire::FEATURE_IN_PICTURE]
    } else {
        Vec::new()
    };
    #[allow(unreachable_code)]
    Vec::new()
}

struct Shared {
    board: Board,
    /// The drawing thread runs (only where there is an overlay).
    #[cfg_attr(
        not(any(target_os = "windows", target_os = "macos", target_os = "linux")),
        allow(dead_code)
    )]
    renderer: bool,
}
static SHARED: std::sync::Mutex<Shared> = std::sync::Mutex::new(Shared {
    board: Board::new(),
    renderer: false,
});
static WAKE: std::sync::Condvar = std::sync::Condvar::new();
static NEXT_OWNER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

fn shared() -> std::sync::MutexGuard<'static, Shared> {
    SHARED.lock().unwrap_or_else(|error| error.into_inner())
}

/// One technician's session on this computer; their strokes go when it is dropped.
pub struct Owner(u64);
impl Owner {
    pub fn new() -> Self {
        Self(NEXT_OWNER.fetch_add(1, std::sync::atomic::Ordering::Relaxed))
    }
    /// A drawing record from this technician (without the record prefix). Invalid or excess
    /// records are ignored; drawing never ends the session.
    pub fn receive(&self, bytes: &[u8]) {
        let Ok(message) = Message::decode(bytes) else {
            return;
        };
        let mut shared = shared();
        if shared.board.apply(self.0, message, Instant::now()) {
            start_renderer(&mut shared);
            WAKE.notify_all();
        }
    }
}
impl Default for Owner {
    fn default() -> Self {
        Self::new()
    }
}
impl Drop for Owner {
    fn drop(&mut self) {
        let mut shared = shared();
        shared.board.rates.retain(|rate| rate.owner != self.0);
        if shared.board.forget(self.0, Instant::now()) {
            WAKE.notify_all();
        }
    }
}

/// Removes every drawing at once (the last technician's session ended).
pub fn clear_all() {
    let mut shared = shared();
    if shared.board.clear(Instant::now()) {
        WAKE.notify_all();
    }
}

#[cfg(any(target_os = "windows", target_os = "macos", target_os = "linux"))]
fn start_renderer(shared: &mut Shared) {
    if !shared.renderer {
        shared.renderer = std::thread::Builder::new()
            .name("mixel-remote-drawings".into())
            .spawn(render)
            .is_ok();
    }
}
#[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
fn start_renderer(_: &mut Shared) {}

#[cfg(target_os = "windows")]
use crate::annotation_windows::Overlay;
#[cfg(target_os = "linux")]
use crate::annotation_x11::Overlay;
#[cfg(target_os = "macos")]
use mac::Overlay;

/// The overlay's own thread: opens it when there is something to show, redraws on changes and
/// while fading, closes it when the board is empty. Idle (no overlay) it sleeps until woken.
#[cfg(any(target_os = "windows", target_os = "macos", target_os = "linux"))]
fn render() {
    let mut overlay: Option<(String, Overlay)> = None;
    let (mut shown, mut alpha_shown) = (u64::MAX, -1.0f64);
    let mut last_raise = Instant::now();
    loop {
        let mut state = shared();
        let alpha = state.board.opacity(Instant::now());
        let Some(alpha) = alpha else {
            let closing = overlay.take();
            if closing.is_some() {
                drop(state);
                drop(closing);
                (shown, alpha_shown) = (u64::MAX, -1.0);
                continue;
            }
            drop(WAKE.wait(state).unwrap_or_else(|error| error.into_inner()));
            continue;
        };
        drop(state);
        let source = crate::session_guard::shared_source();
        if overlay.as_ref().is_none_or(|(open, _)| *open != source) {
            overlay = None;
            match Overlay::open(&source) {
                Ok(opened) => overlay = Some((source, opened)),
                Err(error) => {
                    eprintln!("drawings overlay: {error}");
                    // Try again later; the board fades and clears in the meantime.
                    std::thread::sleep(Duration::from_secs(1));
                    continue;
                }
            }
            (shown, alpha_shown) = (u64::MAX, -1.0);
        }
        let Some((_, open)) = overlay.as_mut() else {
            continue;
        };
        let repaint = open.pump();
        state = shared();
        let version = state.board.version();
        if repaint || version != shown || (alpha - alpha_shown).abs() > 0.01 {
            let (width, height) = open.size();
            let shapes = if repaint || version != shown {
                Some(state.board.shapes(width, height))
            } else {
                None
            };
            drop(state);
            open.show(shapes.as_deref(), alpha);
            (shown, alpha_shown) = (version, alpha);
            // At most about 60 pictures a second, however fast records arrive.
            std::thread::sleep(Duration::from_millis(15));
        } else {
            drop(state);
        }
        if last_raise.elapsed() >= Duration::from_secs(1) {
            last_raise = Instant::now();
            open.raise();
        }
        // Smooth while fading; otherwise often enough to answer the window system.
        let pause = Duration::from_millis(if alpha < 1.0 { 16 } else { 40 });
        let state = shared();
        let _ = WAKE
            .wait_timeout(state, pause)
            .unwrap_or_else(|error| error.into_inner());
    }
}

#[cfg(target_os = "macos")]
mod mac {
    use super::{Shape, ShapeKind};
    use native_os_capture::macos_guard;

    pub struct Overlay {
        size: (f64, f64),
        shapes: Vec<f64>,
    }
    impl Overlay {
        pub fn open(source: &str) -> Result<Self, String> {
            let id = source
                .strip_prefix("display:")
                .and_then(|id| id.parse::<u32>().ok())
                .unwrap_or_else(native_os_capture::macos::main_display);
            let (left, top, width, height) = native_os_capture::macos::display_bounds(id);
            macos_guard::draw_open(left, top, width, height)?;
            Ok(Self {
                size: (width, height),
                shapes: Vec::new(),
            })
        }
        pub fn size(&self) -> (f64, f64) {
            self.size
        }
        pub fn pump(&mut self) -> bool {
            false
        }
        pub fn raise(&mut self) {}
        pub fn show(&mut self, shapes: Option<&[Shape]>, alpha: f64) {
            if let Some(shapes) = shapes {
                self.shapes = encode(shapes);
            }
            macos_guard::draw_show(&self.shapes, alpha);
        }
    }
    impl Drop for Overlay {
        fn drop(&mut self) {
            macos_guard::draw_close();
        }
    }

    /// The layout `macos_guard.m` reads: kind, red, green, blue, alpha, width, count, points.
    pub fn encode(shapes: &[Shape]) -> Vec<f64> {
        let mut values = Vec::new();
        for shape in shapes {
            let kind = match shape.kind {
                ShapeKind::Line => 0.0,
                ShapeKind::Polygon => 1.0,
                ShapeKind::Dot => 2.0,
            };
            let alpha = if shape.highlight {
                core_protocol::annotation::HIGHLIGHT_ALPHA
            } else {
                1.0
            };
            values.extend([
                kind,
                f64::from(shape.rgb[0]) / 255.0,
                f64::from(shape.rgb[1]) / 255.0,
                f64::from(shape.rgb[2]) / 255.0,
            ]);
            values.extend([alpha, shape.width, shape.points.len() as f64]);
            for (x, y) in &shape.points {
                values.extend([*x, *y]);
            }
        }
        values
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stroke(id: u16, tool: Tool, points: Vec<(u16, u16)>, done: bool) -> Message {
        Message::Stroke {
            id,
            tool,
            colour: 0,
            width: 1,
            points,
            done,
        }
    }

    #[test]
    fn strokes_grow_live_and_belong_to_their_technician() {
        let start = Instant::now();
        let mut board = Board::new();
        assert!(board.apply(1, stroke(7, Tool::Pen, vec![(0, 0)], false), start));
        assert!(board.apply(
            1,
            stroke(7, Tool::Pen, vec![(65535, 0), (65535, 65535)], false),
            start
        ));
        // A colleague's stroke with the same id is a different stroke.
        assert!(board.apply(
            2,
            stroke(7, Tool::Highlighter, vec![(10, 10), (20, 20)], true),
            start
        ));
        assert!(board.apply(1, stroke(7, Tool::Pen, vec![], true), start));
        let shapes = board.shapes(101.0, 51.0);
        assert_eq!(shapes.len(), 2);
        assert_eq!(shapes[0].kind, ShapeKind::Line);
        assert_eq!(
            shapes[0].points,
            vec![(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)]
        );
        assert!(!shapes[0].highlight && shapes[1].highlight);
        assert_eq!(shapes[0].rgb, wire::PALETTE[0]);
        // Undo takes this technician's last stroke only; Clear only theirs.
        assert!(board.apply(2, Message::Undo, start));
        assert!(!board.apply(2, Message::Undo, start));
        assert_eq!(board.shapes(101.0, 51.0).len(), 1);
        assert!(board.apply(3, stroke(1, Tool::Pen, vec![(5, 5)], true), start));
        assert!(board.apply(1, Message::Clear, start));
        assert_eq!(board.shapes(101.0, 51.0).len(), 1);
        assert!(board.forget(3, start));
        assert!(board.is_empty());
        // A finished id used again starts a new stroke; an end without points for an unknown
        // stroke shows nothing.
        assert!(board.apply(1, stroke(9, Tool::Pen, vec![(1, 1)], true), start));
        // A repeated end changes nothing.
        assert!(!board.apply(1, stroke(9, Tool::Pen, vec![], true), start));
        assert!(board.apply(1, stroke(9, Tool::Pen, vec![(2, 2)], false), start));
        assert_eq!(board.strokes.len(), 1);
        assert_eq!(board.strokes[0].points, vec![(2, 2)]);
        assert!(!board.apply(1, stroke(10, Tool::Pen, vec![], true), start));
    }

    #[test]
    fn limits_are_kept() {
        let start = Instant::now();
        let mut board = Board::new();
        // Points per stroke.
        for round in 0..70u64 {
            let now = start + Duration::from_secs(round);
            assert!(board.apply(1, stroke(1, Tool::Pen, vec![(1, 1); 64], false), now));
        }
        assert_eq!(board.strokes[0].points.len(), wire::MAX_POINTS_PER_STROKE);
        // Strokes per technician: the oldest go.
        let later = start + Duration::from_secs(100);
        for id in 0..(wire::MAX_STROKES as u16 + 5) {
            board.apply(
                2,
                stroke(id + 100, Tool::Pen, vec![(1, 1)], true),
                later + Duration::from_secs(u64::from(id)),
            );
        }
        let mine: Vec<u16> = board
            .strokes
            .iter()
            .filter(|stroke| stroke.owner == 2)
            .map(|stroke| stroke.id)
            .collect();
        assert_eq!(mine.len(), wire::MAX_STROKES);
        assert_eq!(mine[0], 105);
        assert_eq!(
            board
                .strokes
                .iter()
                .filter(|stroke| stroke.owner == 1)
                .count(),
            1
        );
        // Records per second, per technician.
        let now = start + Duration::from_secs(1000);
        let mut accepted = 0;
        for index in 0..300u16 {
            if board.apply(3, stroke(index, Tool::Pen, vec![(1, 1)], true), now) {
                accepted += 1;
            }
        }
        assert_eq!(accepted, wire::MAX_RECORDS_PER_SECOND);
        assert!(board.apply(
            3,
            stroke(999, Tool::Pen, vec![(1, 1)], true),
            now + Duration::from_secs(1)
        ));
        // Invalid messages change nothing.
        let before = board.version();
        assert!(!board.apply(
            5,
            Message::Stroke {
                id: 1,
                tool: Tool::Pen,
                colour: 9,
                width: 1,
                points: vec![(0, 0)],
                done: true
            },
            now
        ));
        assert_eq!(board.version(), before);
    }

    #[test]
    fn the_board_fades_after_the_last_change_and_is_then_cleared() {
        let start = Instant::now();
        let at = |ms: u64| start + Duration::from_millis(ms);
        let mut board = Board::new();
        assert_eq!(board.opacity(start), None);
        board.apply(1, stroke(1, Tool::Pen, vec![(1, 1)], false), at(0));
        // Still drawing (updated recently): fully visible.
        board.apply(1, stroke(1, Tool::Pen, vec![(2, 2)], false), at(4000));
        assert_eq!(board.opacity(at(8000)), Some(1.0));
        board.apply(1, stroke(1, Tool::Pen, vec![], true), at(8000));
        assert_eq!(board.opacity(at(12999)), Some(1.0));
        let half = board.opacity(at(13300)).unwrap();
        assert!((half - 0.5).abs() < 0.01, "{half}");
        // A new stroke while fading brings the board back.
        board.apply(
            1,
            stroke(2, Tool::Arrow, vec![(5, 5), (60000, 60000)], true),
            at(13400),
        );
        assert_eq!(board.opacity(at(13500)), Some(1.0));
        assert_eq!(board.opacity(at(19000)), None);
        assert!(board.is_empty());
        // A stroke abandoned half-way (no end arrives) fades too.
        board.apply(1, stroke(3, Tool::Pen, vec![(1, 1)], false), at(20000));
        assert_eq!(board.opacity(at(24000)), Some(1.0));
        assert!(board.opacity(at(25300)).is_some_and(|alpha| alpha < 1.0));
        assert_eq!(board.opacity(at(25700)), None);
    }

    #[test]
    fn arrows_get_a_head_at_the_last_point() {
        let parts = arrow((0.0, 0.0), (100.0, 0.0), 4.0, 1080.0);
        assert_eq!(parts.len(), 2);
        assert_eq!(parts[0].0, ShapeKind::Line);
        assert_eq!(parts[0].1[0], (0.0, 0.0));
        assert!((parts[0].1[1].0 - 90.4).abs() < 1e-9);
        let (kind, head) = &parts[1];
        assert_eq!(*kind, ShapeKind::Polygon);
        assert_eq!(head[0], (100.0, 0.0));
        // 12 long, symmetric around the line.
        assert!((head[1].0 - 88.0).abs() < 1e-9 && (head[2].0 - 88.0).abs() < 1e-9);
        assert!((head[1].1 + head[2].1).abs() < 1e-9 && head[1].1.abs() > 6.0);
        // A very short arrow is only a head; no length is a dot.
        assert_eq!(arrow((0.0, 0.0), (5.0, 0.0), 4.0, 1080.0).len(), 1);
        assert_eq!(
            arrow((3.0, 3.0), (3.2, 3.0), 4.0, 1080.0)[0].0,
            ShapeKind::Dot
        );
        // On the board an arrow goes from the first to the last point, whatever lies between.
        let mut board = Board::new();
        board.apply(
            1,
            Message::Stroke {
                id: 1,
                tool: Tool::Arrow,
                colour: 3,
                width: 2,
                points: vec![(0, 0), (40000, 9000), (65535, 0)],
                done: true,
            },
            Instant::now(),
        );
        let shapes = board.shapes(1001.0, 1081.0);
        assert_eq!(shapes.len(), 2);
        assert_eq!(shapes[0].points[0], (0.0, 0.0));
        assert_eq!(shapes[1].points[0], (1000.0, 0.0));
        assert_eq!(shapes[1].rgb, wire::PALETTE[3]);
        assert_eq!(shapes[0].width, wire::line_width(Tool::Arrow, 2, 1081.0));
    }

    #[test]
    fn a_single_point_is_a_dot_and_repeated_points_are_merged() {
        let mut board = Board::new();
        board.apply(
            1,
            stroke(1, Tool::Highlighter, vec![(9, 9), (9, 9)], true),
            Instant::now(),
        );
        let shapes = board.shapes(100.0, 100.0);
        assert_eq!(shapes.len(), 1);
        assert_eq!(shapes[0].kind, ShapeKind::Dot);
        assert_eq!(
            shapes[0].width,
            wire::line_width(Tool::Highlighter, 1, 100.0)
        );
    }

    #[test]
    fn owners_are_distinct_and_their_strokes_go_with_them() {
        let first = Owner::new();
        let second = Owner::new();
        assert_ne!(first.0, second.0);
        let mut board = Board::new();
        board.apply(
            first.0,
            stroke(1, Tool::Pen, vec![(1, 1)], true),
            Instant::now(),
        );
        board.apply(
            second.0,
            stroke(1, Tool::Pen, vec![(1, 1)], true),
            Instant::now(),
        );
        assert!(board.forget(first.0, Instant::now()));
        assert_eq!(board.strokes.len(), 1);
    }

    /// Lab check in a real X11 session (Xvfb works): records from a technician show on the
    /// screen through the drawing thread, Undo takes the last stroke away at once, and the rest
    /// fades and is gone a few seconds later.
    /// `DISPLAY=:99 cargo test -p mixel_engine -- --ignored --test-threads=1 x11_`
    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "needs an X11 display"]
    fn x11_drawings_appear_and_fade() {
        use x11rb::{connection::Connection, protocol::xproto::ConnectionExt as _};
        let (check, _) = x11rb::connect(None).unwrap();
        let screen = check.setup().roots[0].clone();
        let picture = |x: f64, y: f64| {
            let (x, y) = (
                (x * f64::from(screen.width_in_pixels - 1)) as i16,
                (y * f64::from(screen.height_in_pixels - 1)) as i16,
            );
            let image = check
                .get_image(
                    x11rb::protocol::xproto::ImageFormat::Z_PIXMAP,
                    screen.root,
                    x,
                    y,
                    1,
                    1,
                    u32::MAX,
                )
                .unwrap()
                .reply()
                .unwrap();
            u32::from_le_bytes([image.data[0], image.data[1], image.data[2], 0])
        };
        let before = (picture(0.5, 0.1), picture(0.5, 0.3));
        crate::session_guard::set_shared_source(&format!("x11:0:{}", screen.root));
        let technician = Owner::new();
        let send = |message: Message| technician.receive(&message.encode().unwrap());
        // Rows 0.1 and 0.3 of the screen (the view-only session lab test draws across the middle).
        let (left, right, top, middle) = (6553, 58981, 6553, 19660);
        send(Message::Stroke {
            id: 1,
            tool: Tool::Pen,
            colour: 0,
            width: 4,
            points: vec![(left, top), (32767, top)],
            done: false,
        });
        send(Message::Stroke {
            id: 1,
            tool: Tool::Pen,
            colour: 0,
            width: 4,
            points: vec![(right, top)],
            done: true,
        });
        send(Message::Stroke {
            id: 2,
            tool: Tool::Pen,
            colour: 3,
            width: 4,
            points: vec![(left, middle), (right, middle)],
            done: true,
        });
        std::thread::sleep(Duration::from_millis(600));
        assert_eq!(
            picture(0.5, 0.1),
            0x00E5_322D,
            "the red stroke is on the screen"
        );
        assert_eq!(
            picture(0.5, 0.3),
            0x001E_7BFF,
            "the blue stroke is on the screen"
        );
        send(Message::Undo);
        std::thread::sleep(Duration::from_millis(400));
        assert_eq!(
            picture(0.5, 0.3),
            before.1,
            "Undo took the blue stroke away"
        );
        assert_eq!(picture(0.5, 0.1), 0x00E5_322D);
        std::thread::sleep(Duration::from_millis(
            wire::FADE_AFTER_MS + wire::FADE_MS + 800,
        ));
        assert_eq!(picture(0.5, 0.1), before.0, "faded and gone");
        assert!(
            shared()
                .board
                .strokes
                .iter()
                .all(|stroke| stroke.owner != technician.0)
        );
        // A colleague's strokes go when their session ends.
        let colleague = Owner::new();
        colleague.receive(
            &Message::Stroke {
                id: 1,
                tool: Tool::Highlighter,
                colour: 1,
                width: 4,
                points: vec![(left, middle), (right, middle)],
                done: true,
            }
            .encode()
            .unwrap(),
        );
        std::thread::sleep(Duration::from_millis(400));
        assert_ne!(
            picture(0.5, 0.3),
            before.1,
            "the colleague's highlight is shown"
        );
        drop(colleague);
        std::thread::sleep(Duration::from_millis(400));
        assert_eq!(
            picture(0.5, 0.3),
            before.1,
            "gone with the colleague's session"
        );
        println!("x11 drawings: shown, undone, faded and removed with the session");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_shapes_are_flattened_for_the_shim() {
        let shapes = vec![
            Shape {
                kind: ShapeKind::Line,
                rgb: [255, 0, 51],
                highlight: true,
                width: 4.0,
                points: vec![(1.0, 2.0), (3.0, 4.0)],
            },
            Shape {
                kind: ShapeKind::Dot,
                rgb: [0, 0, 0],
                highlight: false,
                width: 6.0,
                points: vec![(5.0, 6.0)],
            },
        ];
        let values = mac::encode(&shapes);
        assert_eq!(values.len(), 7 + 4 + 7 + 2);
        assert_eq!(
            &values[..7],
            &[0.0, 1.0, 0.0, 0.2, wire::HIGHLIGHT_ALPHA, 4.0, 2.0]
        );
        assert_eq!(&values[11..18], &[2.0, 0.0, 0.0, 0.0, 1.0, 6.0, 1.0]);
    }
}
