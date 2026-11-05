use native_os_capture::damage::*;
#[test]
fn first_unchanged_padding_resize_and_reset() {
    let mut tracker = DamageTracker::new(16).unwrap();
    let mut bytes = vec![0u8; 80 * 20];
    let first = Frame::bgra(17, 20, 80, &bytes).unwrap();
    assert_eq!(
        tracker.update(&first),
        [Rect {
            x: 0,
            y: 0,
            width: 17,
            height: 20
        }]
    );
    assert!(tracker.update(&first).is_empty());
    bytes[79] = 12;
    assert!(
        tracker
            .update(&Frame::bgra(17, 20, 80, &bytes).unwrap())
            .is_empty()
    );
    bytes[(19 * 80) + 16 * 4] = 1;
    assert_eq!(
        tracker.update(&Frame::bgra(17, 20, 80, &bytes).unwrap()),
        [Rect {
            x: 16,
            y: 16,
            width: 1,
            height: 4
        }]
    );
    assert_eq!(
        tracker.update(&Frame::bgra(16, 20, 80, &bytes).unwrap()),
        [Rect {
            x: 0,
            y: 0,
            width: 16,
            height: 20
        }]
    );
    tracker.reset();
    assert_eq!(
        tracker
            .update(&Frame::bgra(16, 20, 80, &bytes).unwrap())
            .len(),
        1
    );
}
#[test]
fn merge_dirty_tiles_and_full_screen_fallback() {
    let mut tracker = DamageTracker::new(8).unwrap();
    let mut bytes = vec![0; 64 * 64 * 4];
    tracker.update(&Frame::bgra(64, 64, 256, &bytes).unwrap());
    for y in 8..24 {
        for x in 8..24 {
            bytes[(y * 64 + x) * 4] = 1;
        }
    }
    assert_eq!(
        tracker.update(&Frame::bgra(64, 64, 256, &bytes).unwrap()),
        [Rect {
            x: 8,
            y: 8,
            width: 16,
            height: 16
        }]
    );
    bytes.fill(255);
    assert_eq!(
        tracker.update(&Frame::bgra(64, 64, 256, &bytes).unwrap()),
        [Rect {
            x: 0,
            y: 0,
            width: 64,
            height: 64
        }]
    );
}
#[test]
fn malformed_or_overflowing_frames_are_refused_before_indexing() {
    for layout in [
        (0, 1, 4),
        (1, 0, 4),
        (8193, 1, 32772),
        (2, 2, 4),
        (1, 2, usize::MAX),
        (8, 8, 32),
    ] {
        assert!(Frame::bgra(layout.0, layout.1, layout.2, &[0; 16]).is_err());
    }
    for tile in [0, 1, 7, 9, 129, 256] {
        assert!(DamageTracker::new(tile).is_err());
    }
}
#[test]
fn every_changed_pixel_is_covered_across_edge_tiles() {
    let (w, h) = (67, 59);
    let mut bytes = vec![0; w * h * 4];
    let mut tracker = DamageTracker::new(8).unwrap();
    tracker.update(&Frame::bgra(w, h, w * 4, &bytes).unwrap());
    for round in 0..100 {
        let mut changed = Vec::new();
        for n in 0..13 {
            let x = (round * 17 + n * 11) % w;
            let y = (round * 7 + n * 13) % h;
            bytes[(y * w + x) * 4] ^= 1;
            changed.push((x, y));
        }
        let regions = tracker.update(&Frame::bgra(w, h, w * 4, &bytes).unwrap());
        for (x, y) in changed {
            assert!(
                regions
                    .iter()
                    .any(|r| x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
            );
        }
        assert!(
            regions
                .iter()
                .all(|r| r.x + r.width <= w && r.y + r.height <= h)
        );
        assert!(
            tracker
                .update(&Frame::bgra(w, h, w * 4, &bytes).unwrap())
                .is_empty()
        );
    }
}
