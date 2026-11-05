#![cfg(feature = "native-webrtc")]
use core_protocol::{
    crypto::{Context, Handshake, Role},
    input::{self, Input},
    transport::{NativeTransport, local_fingerprint},
};
use ed25519_dalek::SigningKey;
use rand_core::OsRng;
use std::{sync::Arc, time::Duration};
use webrtc::data_channel::{DataChannel, DataChannelEvent};
async fn opened(dc: &Arc<dyn DataChannel>) {
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            match dc.poll().await {
                Some(DataChannelEvent::OnOpen) => break,
                None => panic!("channel ended"),
                _ => {}
            }
        }
    })
    .await
    .expect("DTLS/SCTP must open");
}
async fn received(dc: &Arc<dyn DataChannel>) -> Vec<u8> {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match dc.poll().await {
                Some(DataChannelEvent::OnMessage(message)) => {
                    assert!(!message.is_string);
                    return message.data.to_vec();
                }
                None | Some(DataChannelEvent::OnClose | DataChannelEvent::OnError) => {
                    panic!("channel failed")
                }
                _ => {}
            }
        }
    })
    .await
    .expect("binary input must arrive")
}
#[tokio::test]
async fn real_udp_ice_dtls_sctp_and_authenticated_encrypted_input_roundtrip() {
    let mut a = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .unwrap();
    let mut b = NativeTransport::new("127.0.0.1:0".into(), vec![])
        .await
        .unwrap();
    assert!(a.observed_fingerprint().await.is_err());
    let outbound = a
        .peer
        .create_data_channel("mixel-control-v1", None)
        .await
        .unwrap();
    assert!(outbound.ordered().await.unwrap());
    assert_eq!(outbound.max_retransmits().await.unwrap(), None);
    let offer = a
        .local_sdp(a.peer.create_offer(None).await.unwrap())
        .await
        .unwrap();
    let af = local_fingerprint(&offer.sdp).unwrap();
    b.peer.set_remote_description(offer).await.unwrap();
    let answer = b
        .local_sdp(b.peer.create_answer(None).await.unwrap())
        .await
        .unwrap();
    let bf = local_fingerprint(&answer.sdp).unwrap();
    a.peer.set_remote_description(answer).await.unwrap();
    let inbound = tokio::time::timeout(Duration::from_secs(15), b.incoming_channels.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::join!(opened(&outbound), opened(&inbound));
    assert_eq!(a.observed_fingerprint().await.unwrap(), bf);
    assert_eq!(b.observed_fingerprint().await.unwrap(), af);
    let ai = SigningKey::generate(&mut OsRng);
    let bi = SigningKey::generate(&mut OsRng);
    let context = Context {
        session_id: [21; 32],
        controller: ai.verifying_key(),
        host: bi.verifying_key(),
    };
    let ah = Handshake::new(context.clone(), Role::Controller, &ai, af).unwrap();
    let bh = Handshake::new(context, Role::Host, &bi, bf).unwrap();
    let hello_a = ah.hello();
    let hello_b = bh.hello();
    let mut ac = ah
        .finish(&hello_b, a.observed_fingerprint().await.unwrap())
        .unwrap();
    let mut bc = bh
        .finish(&hello_a, b.observed_fingerprint().await.unwrap())
        .unwrap();
    let packet = input::encode(&Input::Text("Mixel WebRTC Grüezi 中文 😀")).unwrap();
    let record = ac.seal(&packet).unwrap();
    outbound.send(record.as_slice().into()).await.unwrap();
    let wire = received(&inbound).await;
    assert_eq!(wire, record);
    let plaintext = bc.open(&wire).unwrap();
    assert_eq!(
        input::decode(&plaintext).unwrap(),
        Input::Text("Mixel WebRTC Grüezi 中文 😀")
    );
    let ack = bc.seal(b"accepted-input").unwrap();
    inbound.send(ack.as_slice().into()).await.unwrap();
    assert_eq!(
        ac.open(&received(&outbound).await).unwrap(),
        b"accepted-input"
    );
    a.close().await.unwrap();
    b.close().await.unwrap();
    println!(
        "PASS: actual UDP ICE + DTLS + SCTP; actual peer certificate hashes; signed X25519 + AES256GCM Unicode input both directions"
    );
}
