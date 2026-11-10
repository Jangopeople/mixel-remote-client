use native_os_capture::h264::*;
#[test]
fn bounded_avcc_conversion_preserves_parameters_nals_and_keyframe() {
    let frame = avcc_to_annex_b(
        &[0, 0, 0, 3, 0x65, 0xaa, 0xbb, 0, 0, 0, 2, 0x41, 0xcc],
        4,
        &[&[0x67, 0x42], &[0x68, 0xff]],
    )
    .unwrap();
    assert!(frame.keyframe);
    assert_eq!(
        &*frame.annex_b,
        &[
            0, 0, 0, 1, 0x67, 0x42, 0, 0, 0, 1, 0x68, 0xff, 0, 0, 0, 1, 0x65, 0xaa, 0xbb, 0, 0, 0,
            1, 0x41, 0xcc
        ]
    );
    assert!(!avcc_to_annex_b(&[0, 2, 0x41, 3], 2, &[]).unwrap().keyframe);
    assert!(avcc_to_annex_b(&[1, 0x65], 1, &[]).unwrap().keyframe);
}
#[test]
fn malformed_truncated_oversized_or_reserved_nals_are_refused() {
    for bytes in [
        &[][..],
        &[0, 0, 0][..],
        &[0, 0, 0, 0][..],
        &[255, 255, 255, 255][..],
        &[0, 0, 0, 2, 0x65][..],
        &[0, 0, 0, 1, 0xe5][..],
        &[0, 0, 0, 1, 0x78][..],
    ] {
        assert!(avcc_to_annex_b(bytes, 4, &[]).is_err());
    }
    assert!(avcc_to_annex_b(&[0, 0, 1, 0x65], 3, &[]).is_err());
    assert!(avcc_to_annex_b(&[1, 0x65], 1, &[&[0x41]]).is_err());
    assert!(avcc_to_annex_b(&vec![0; MAX_ACCESS_UNIT + 1], 4, &[]).is_err());
    assert!(avcc_to_annex_b(&[1, 0x41].repeat(1025), 1, &[]).is_err());
}
#[test]
fn arbitrary_small_inputs_never_panic() {
    for size in 0..80 {
        for byte in 0..=255 {
            for prefix in [1, 2, 4] {
                let _ = avcc_to_annex_b(&vec![byte; size], prefix, &[]);
            }
        }
    }
}
#[cfg(target_os = "macos")]
#[test]
fn actual_hardware_encoder_produces_h264_and_enforces_timestamps() {
    use native_os_capture::{macos_encoder::HardwareH264Encoder, stream::CapturedFrame};
    let mut encoder = HardwareH264Encoder::new(320, 240, 30, 1_000_000)
        .expect("Mac QA requires its real H264 hardware encoder");
    assert!(encoder.hardware_active().unwrap());
    let frame = CapturedFrame::bgra(320, 240, 1280, vec![80; 320 * 240 * 4]).unwrap();
    let encoded = encoder.encode(&frame, 0, true).unwrap();
    assert!(encoded.keyframe);
    assert!(encoded.annex_b.len() > 20);
    assert!(matches!(
        encoder.encode(&frame, 0, false),
        Err(EncodeError::Configuration)
    ));
    encoder.encode(&frame, 33333, false).unwrap();
}

#[cfg(target_os = "macos")]
#[test]
fn screen_encoder_suppresses_static_frames_and_native_decoder_accepts_forced_refresh() {
    use native_os_capture::{
        macos_decoder::H264Decoder, macos_screen_encoder::ScreenH264Encoder, stream::CapturedFrame,
    };
    let mut encoder = ScreenH264Encoder::new(320, 240, 30, 1_000_000).unwrap();
    let mut decoder = H264Decoder::new(320, 240).unwrap();
    let frame = CapturedFrame::bgra(320, 240, 1280, vec![80; 320 * 240 * 4]).unwrap();
    let first = encoder.encode(&frame, 0, false).unwrap().unwrap();
    assert!(first.keyframe);
    let decoded = decoder.decode(&first.annex_b, 0).unwrap().unwrap();
    assert_eq!((decoded.width(), decoded.height()), (320, 240));
    assert!(encoder.encode(&frame, 33_333, false).unwrap().is_none());
    let refreshed = encoder.encode(&frame, 66_666, true).unwrap().unwrap();
    assert!(refreshed.keyframe);
    let decoded = decoder.decode(&refreshed.annex_b, 66_666).unwrap().unwrap();
    assert_eq!((decoded.width(), decoded.height()), (320, 240));
    assert!(!encoder.needs_retry());
    println!(
        "PASS: real Mac hardware encode/decode, static-frame suppression, and independently decodable forced refresh"
    );
}
