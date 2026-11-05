#![cfg(target_os = "macos")]
use core_protocol::{
    crypto::{Context, Handshake, Role},
    media::{H264Assembler, H264VideoSender, MediaError, TrackRemoteEvent},
    session::Session,
    transport::{NativeDataChannel, NativeDataChannelEvent, NativeTransport, local_fingerprint},
};
use ed25519_dalek::SigningKey;
use native_os_capture::{macos_encoder::HardwareH264Encoder, stream::CapturedFrame};
use rand_core::OsRng;
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
async fn opened(channel: &Arc<dyn NativeDataChannel>) {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            match channel.poll().await {
                Some(NativeDataChannelEvent::OnOpen) => break,
                None => panic!("control transport closed"),
                _ => {}
            }
        }
    })
    .await
    .unwrap();
}
#[tokio::test]
async fn hardware_h264_crosses_real_authenticated_webrtc_srtp_after_consent() {
    let mut host = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .unwrap();
    let mut viewer = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .unwrap();
    let sender = H264VideoSender::new(&host, 640, 420, 30).await.unwrap();
    let dc = host
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .unwrap();
    let host_key = SigningKey::generate(&mut OsRng);
    let controller_key = SigningKey::generate(&mut OsRng);
    let context = Context {
        session_id: [51; 32],
        controller: controller_key.verifying_key(),
        host: host_key.verifying_key(),
    };
    let now = Instant::now();
    let mut consent = Session::for_peer(now, context.clone(), Role::Host, false);
    assert!(matches!(
        sender
            .send(&consent, &[0, 0, 0, 1, 0x65, 1], Duration::from_millis(33))
            .await,
        Err(MediaError::Forbidden)
    ));
    consent.decide(true, now).unwrap();
    let offer = host
        .local_sdp(host.peer.create_offer(None).await.unwrap())
        .await
        .unwrap();
    let host_fingerprint = local_fingerprint(&offer.sdp).unwrap();
    viewer.peer.set_remote_description(offer).await.unwrap();
    let answer = viewer
        .local_sdp(viewer.peer.create_answer(None).await.unwrap())
        .await
        .unwrap();
    let viewer_fingerprint = local_fingerprint(&answer.sdp).unwrap();
    host.peer.set_remote_description(answer).await.unwrap();
    let remote_dc = tokio::time::timeout(Duration::from_secs(10), viewer.incoming_channels.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::join!(opened(&dc), opened(&remote_dc));
    let hh = Handshake::new(context.clone(), Role::Host, &host_key, host_fingerprint).unwrap();
    let ch = Handshake::new(
        context.clone(),
        Role::Controller,
        &controller_key,
        viewer_fingerprint,
    )
    .unwrap();
    let hello_h = hh.hello();
    let hello_c = ch.hello();
    let host_control = hh
        .finish(&hello_c, host.observed_fingerprint().await.unwrap())
        .unwrap();
    let _viewer_control = ch
        .finish(&hello_h, viewer.observed_fingerprint().await.unwrap())
        .unwrap();
    // Merely opening DTLS still cannot authorize media before this transition.
    assert!(matches!(
        sender
            .send(&consent, &[0, 0, 0, 1, 0x65, 1], Duration::from_millis(33))
            .await,
        Err(MediaError::Forbidden)
    ));
    consent
        .authenticated_transport_ready(&host_control.verified_session(), Instant::now())
        .unwrap();
    assert!(consent.can_view());
    assert!(!consent.can_control());
    // A valid grant for different certificates must not authorize this socket.
    let foreign_h = Handshake::new(context.clone(), Role::Host, &host_key, [9; 32]).unwrap();
    let foreign_c =
        Handshake::new(context.clone(), Role::Controller, &controller_key, [8; 32]).unwrap();
    let foreign_proof = foreign_h
        .finish(&foreign_c.hello(), [8; 32])
        .unwrap()
        .verified_session();
    let now = Instant::now();
    let mut foreign = Session::for_peer(now, context.clone(), Role::Host, false);
    foreign.decide(true, now).unwrap();
    foreign
        .authenticated_transport_ready(&foreign_proof, now)
        .unwrap();
    assert!(foreign.can_share_local_screen());
    assert!(matches!(
        sender
            .send(&foreign, &[0, 0, 0, 1, 0x65, 1], Duration::from_millis(33))
            .await,
        Err(MediaError::Forbidden)
    ));
    let now = Instant::now();
    let mut controller_grant = Session::for_peer(now, context, Role::Controller, true);
    controller_grant.decide(true, now).unwrap();
    controller_grant
        .authenticated_transport_ready(&_viewer_control.verified_session(), now)
        .unwrap();
    assert!(controller_grant.can_view());
    assert!(!controller_grant.can_share_local_screen());
    assert!(matches!(
        sender
            .send(
                &controller_grant,
                &[0, 0, 0, 1, 0x65, 1],
                Duration::from_millis(33)
            )
            .await,
        Err(MediaError::Forbidden)
    ));
    let receive = tokio::spawn(async move {
        let track = tokio::time::timeout(Duration::from_secs(10), viewer.incoming_tracks.recv())
            .await
            .unwrap()
            .unwrap();
        let mut assembler = H264Assembler::default();
        let mut frames = Vec::new();
        let mut packets = 0;
        tokio::time::timeout(Duration::from_secs(15), async {
            while let Some(event) = track.poll().await {
                if let TrackRemoteEvent::OnRtpPacket(packet) = event {
                    packets += 1;
                    if let Some(frame) = assembler
                        .push(
                            packet.header.sequence_number,
                            packet.header.timestamp,
                            packet.header.marker,
                            &packet.payload,
                        )
                        .unwrap()
                    {
                        frames.push(frame);
                        if frames.len() == 12 {
                            break;
                        }
                    }
                }
            }
        })
        .await
        .unwrap();
        viewer.close().await.unwrap();
        (frames, packets)
    });
    let mut encoder = HardwareH264Encoder::new(640, 420, 30, 2_000_000).unwrap();
    assert!(encoder.hardware_active().unwrap());
    for index in 0..12u64 {
        let mut pixels = vec![0u8; 640 * 420 * 4];
        for y in 0..420 {
            for x in 0..640 {
                let p = (y * 640 + x) * 4;
                let (r, g, b) = if x < 320 {
                    (16, 208, 80)
                } else {
                    (32, 64, 220)
                };
                pixels[p] = b;
                pixels[p + 1] = g;
                pixels[p + 2] = r;
                pixels[p + 3] = 255;
            }
        }
        let frame = CapturedFrame::bgra(640, 420, 2560, pixels).unwrap();
        let encoded = encoder.encode(&frame, index * 33333, index == 6).unwrap();
        sender
            .send(&consent, &encoded.annex_b, Duration::from_micros(33333))
            .await
            .unwrap();
    }
    let (frames, packets) = receive.await.unwrap();
    assert_eq!(frames.len(), 12);
    assert!(packets >= 12);
    if let Ok(output) = std::env::var("MIXEL_MEDIA_PROOF_DIR") {
        let output = std::path::PathBuf::from(output);
        std::fs::create_dir(&output).unwrap();
        let mut stream = Vec::new();
        for frame in &frames {
            stream.extend_from_slice(frame);
        }
        std::fs::write(output.join("received.h264"), stream).unwrap();
        std::fs::write(output.join("result.txt"),format!("frames={}\nrtp_packets={packets}\nhardware=true\nconsent_gated=true\nactual_dtls_certificates_verified=true\n",frames.len())).unwrap();
    }
    consent.close();
    assert!(matches!(
        sender
            .send(&consent, &[0, 0, 0, 1, 0x65, 1], Duration::from_millis(33))
            .await,
        Err(MediaError::Forbidden)
    ));
    host.close().await.unwrap();
    println!(
        "PASS: real hardware H264 -> consent/identity verified UDP WebRTC DTLS-SRTP -> 12 reassembled frames ({packets} RTP packets); no media before consent or after close"
    );
}
