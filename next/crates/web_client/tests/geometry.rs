use web_client::{normalized_pointer, video_rect};
#[test]
fn scaling_letterboxing_and_outside_drag_are_precise() {
    assert_eq!(
        normalized_pointer(400.0, 300.0, 800.0, 600.0).unwrap(),
        (32768, 32768)
    );
    assert_eq!(
        normalized_pointer(-1.0, 999.0, 800.0, 600.0).unwrap(),
        (0, 65535)
    );
    assert_eq!(
        video_rect(1000.0, 1000.0, 1920.0, 1080.0).unwrap(),
        [0.0, 218.75, 1000.0, 562.5]
    );
    assert_eq!(
        video_rect(1000.0, 500.0, 1000.0, 1000.0).unwrap(),
        [250.0, 0.0, 500.0, 500.0]
    );
}
#[test]
fn invalid_layout_never_produces_input() {
    for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        assert!(normalized_pointer(1.0, 1.0, bad, 10.0).is_err());
        assert!(video_rect(100.0, 100.0, bad, 100.0).is_err());
    }
    assert!(normalized_pointer(f64::NAN, 1.0, 10.0, 10.0).is_err());
}
