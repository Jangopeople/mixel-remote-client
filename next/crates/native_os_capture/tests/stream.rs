use native_os_capture::{damage::FrameError, stream::*};
use std::{sync::Arc, time::Duration};
fn frame(value: u8) -> CapturedFrame {
    CapturedFrame::bgra(2, 2, 8, vec![value; 16]).unwrap()
}
#[test]
fn latest_frame_replaces_and_releases_stale_pixels() {
    let queue = FrameMailbox::default();
    queue.publish(frame(1));
    let (sequence, first) = queue.next(0, Duration::ZERO).unwrap();
    assert_eq!(sequence, 1);
    let weak = Arc::downgrade(&first);
    drop(first);
    for value in 2..=200 {
        queue.publish(frame(value));
    }
    assert!(weak.upgrade().is_none());
    let (sequence, last) = queue.next(1, Duration::ZERO).unwrap();
    assert_eq!(sequence, 200);
    assert_eq!(last.pixels(), [200; 16]);
    assert!(matches!(
        queue.next(sequence, Duration::from_millis(1)),
        Err(CaptureError::Timeout)
    ));
}
#[test]
fn stop_releases_slot_rejects_new_frames_and_wakes_waiter() {
    let queue = Arc::new(FrameMailbox::default());
    queue.publish(frame(1));
    let (_, first) = queue.next(0, Duration::ZERO).unwrap();
    let weak = Arc::downgrade(&first);
    drop(first);
    let waiting = queue.clone();
    let worker = std::thread::spawn(move || waiting.next(1, Duration::from_secs(30)));
    queue.close(CaptureError::Stopped);
    queue.publish(frame(2));
    assert!(matches!(worker.join().unwrap(), Err(CaptureError::Stopped)));
    assert!(weak.upgrade().is_none());
    assert!(matches!(
        queue.next(0, Duration::ZERO),
        Err(CaptureError::Stopped)
    ));
}
#[test]
fn native_error_survives_later_stop_cleanup() {
    let queue = FrameMailbox::default();
    queue.close(CaptureError::Native("display removed".into()));
    queue.close(CaptureError::Stopped);
    assert!(
        matches!(queue.next(0,Duration::ZERO),Err(CaptureError::Native(message)) if message=="display removed")
    );
}
#[test]
fn immutable_frame_rejects_inexact_or_invalid_storage() {
    assert!(matches!(
        CapturedFrame::bgra(2, 2, 8, vec![0; 17]),
        Err(FrameError::Layout)
    ));
    assert!(CapturedFrame::bgra(2, 2, 8, vec![0; 15]).is_err());
    let frame = frame(1);
    assert_eq!((frame.width(), frame.height(), frame.stride()), (2, 2, 8));
    assert_eq!(frame.frame().width(), 2);
}
