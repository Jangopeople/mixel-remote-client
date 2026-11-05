#![cfg(feature = "native-webrtc")]
use core_protocol::media::H264Assembler;
#[test]
fn assemble_fragments_with_sequence_wrap_and_ignore_old_duplicates() {
    let mut reader = H264Assembler::default();
    assert!(
        reader
            .push(65535, 10, false, &[0x7c, 0x85, 1, 2])
            .unwrap()
            .is_none()
    );
    assert!(
        reader
            .push(65535, 10, false, &[0x7c, 0x85, 1, 2])
            .unwrap()
            .is_none()
    );
    let frame = reader
        .push(0, 10, true, &[0x7c, 0x45, 3, 4])
        .unwrap()
        .unwrap();
    assert_eq!(&*frame, &[0, 0, 0, 1, 0x65, 1, 2, 3, 4]);
    assert!(!reader.needs_keyframe());
}
#[test]
fn lost_fragment_requires_a_new_keyframe_and_never_emits_corrupt_p_frames() {
    let mut reader = H264Assembler::default();
    reader.push(1, 10, true, &[0x65, 1]).unwrap();
    reader.push(2, 20, false, &[0x7c, 0x81, 2]).unwrap();
    assert!(
        reader
            .push(4, 20, true, &[0x7c, 0x41, 3])
            .unwrap()
            .is_none()
    );
    assert!(reader.needs_keyframe());
    assert!(reader.push(5, 30, true, &[0x41, 4]).unwrap().is_none());
    assert!(reader.push(6, 40, true, &[0x65, 5]).unwrap().is_some());
    assert!(!reader.needs_keyframe());
}
#[test]
fn mid_fragment_join_and_invalid_packets_do_not_escape_as_frames() {
    let mut reader = H264Assembler::default();
    assert!(
        reader
            .push(1, 10, true, &[0x7c, 0x45, 3])
            .unwrap()
            .is_none()
    );
    assert!(reader.push(2, 20, true, &[]).is_err());
    assert!(reader.push(3, 20, true, &vec![0; 65508]).is_err());
}

#[test]
fn truncated_aggregation_and_arbitrary_payloads_never_panic() {
    for bad in [
        &[0x78, 0][..],
        &[0x78, 0, 1, 0x67][..],
        &[0x7c, 0xc5, 1][..],
    ] {
        assert!(H264Assembler::default().push(1, 10, true, bad).is_err());
    }
    for size in 0..64 {
        for byte in 0..=255 {
            let _ = H264Assembler::default().push(1, 10, true, &vec![byte; size]);
        }
    }
}

#[test]
fn fragmented_nal_cannot_change_type_or_reference_priority_midstream() {
    for end in [[0x7c, 0x41, 3], [0x5c, 0x45, 3]] {
        let mut reader = H264Assembler::default();
        reader.push(1, 10, false, &[0x7c, 0x85, 1, 2]).unwrap();
        assert!(reader.push(2, 10, true, &end).is_err());
        assert!(reader.needs_keyframe());
        assert!(reader.push(3, 20, true, &[0x65, 4]).unwrap().is_some());
    }
}
