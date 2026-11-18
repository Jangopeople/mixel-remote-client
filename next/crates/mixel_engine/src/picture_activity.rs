//! Whether a moving picture is on this computer's session right now (sent by the host, shown by the
//! technician). A file transfer gives way to it: the picture comes first on a slow line.
//!
//! Lab, 18 Nov 2026 (140 ms +-20, 1 % loss, 4 Mbit/s): a 106 kB/s transfer took a 1080p60 picture
//! down from 21 to 10 pictures a second; the file window is therefore smaller while pictures move.
use std::{collections::VecDeque, sync::Mutex, time::{Duration, Instant}};

/// Pictures inside [`SPAN`] that make a picture "moving" (a still desktop repeats one every 1.5 s).
const MOVING: usize = 6;
const SPAN: Duration = Duration::from_secs(1);
static RECENT: Mutex<VecDeque<Instant>> = Mutex::new(VecDeque::new());

/// A picture was sent (host) or put on the screen (technician).
pub fn picture(now: Instant) {
    let Ok(mut recent) = RECENT.lock() else { return };
    recent.push_back(now);
    while recent.front().is_some_and(|first| now.duration_since(*first) > SPAN) || recent.len() > 4 * MOVING * 8 {
        recent.pop_front();
    }
}

/// At least [`MOVING`] pictures in the last second.
pub fn moving(now: Instant) -> bool {
    let Ok(recent) = RECENT.lock() else { return false };
    recent.iter().filter(|at| now.duration_since(**at) <= SPAN).count() >= MOVING
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_picture_every_100_ms_is_moving_and_a_still_desktop_is_not() {
        let start = Instant::now();
        for step in 0..8 {
            picture(start + Duration::from_millis(step * 100));
        }
        assert!(moving(start + Duration::from_millis(800)));
        assert!(!moving(start + Duration::from_millis(3000)));
        let mut recent = RECENT.lock().unwrap();
        recent.clear();
        drop(recent);
        picture(start);
        picture(start + Duration::from_millis(1500));
        assert!(!moving(start + Duration::from_millis(1500)));
    }
}
