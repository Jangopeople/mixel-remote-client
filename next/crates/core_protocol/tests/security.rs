use core_protocol::{
    crypto::*,
    input::{self, Input},
    session::*,
};
use ed25519_dalek::SigningKey;
use rand_core::OsRng;
use std::time::{Duration, Instant};

fn pair() -> (Handshake, Handshake) {
    let a = SigningKey::generate(&mut OsRng);
    let b = SigningKey::generate(&mut OsRng);
    let context = Context {
        session_id: [19; 32],
        controller: a.verifying_key(),
        host: b.verifying_key(),
    };
    (
        Handshake::new(context.clone(), Role::Controller, &a, [1; 32]).unwrap(),
        Handshake::new(context, Role::Host, &b, [2; 32]).unwrap(),
    )
}
fn channels() -> (ControlChannel, ControlChannel) {
    let (a, b) = pair();
    let ah = a.hello();
    let bh = b.hello();
    (
        a.finish(&bh, [2; 32]).unwrap(),
        b.finish(&ah, [1; 32]).unwrap(),
    )
}
#[test]
fn encrypted_borrowed_unicode_input_both_directions() {
    let (mut a, mut b) = channels();
    for value in [
        Input::Text("Grüezi 中文 😀"),
        Input::Key {
            usage: 0xe1,
            down: true,
        },
        Input::ReleaseAll,
    ] {
        let encoded = input::encode(&value).unwrap();
        let record = a.seal(&encoded).unwrap();
        assert!(!record.windows(encoded.len()).any(|w| w == encoded));
        let plain = b.open(&record).unwrap();
        let decoded = input::decode(&plain).unwrap();
        assert_eq!(decoded, value);
        if let Input::Text(text) = decoded {
            assert!(text.as_ptr() >= plain.as_ptr());
            assert!(text.as_ptr() < plain.as_ptr().wrapping_add(plain.len()));
        }
        let reply = b.seal(b"ack").unwrap();
        assert_eq!(a.open(&reply).unwrap(), b"ack");
    }
}
#[test]
fn tamper_replay_reflection_and_reorder_are_rejected() {
    let (mut a, mut b) = channels();
    let first = a.seal(b"one").unwrap();
    let second = a.seal(b"two").unwrap();
    assert_eq!(b.open(&second), Err(CryptoError::Record));
    let mut forged = first.clone();
    *forged.last_mut().unwrap() ^= 1;
    assert_eq!(b.open(&forged), Err(CryptoError::Record));
    assert_eq!(a.open(&first), Err(CryptoError::Record));
    assert_eq!(b.open(&first).unwrap(), b"one");
    assert_eq!(b.open(&first), Err(CryptoError::Record));
    assert_eq!(b.open(&second).unwrap(), b"two");
    assert_eq!(a.seal(&[0; 4097]), Err(CryptoError::Record));
}
#[test]
fn untrusted_identity_changed_fingerprint_and_wrong_session_fail() {
    let (a, b) = pair();
    let mut bh = b.hello();
    bh.ephemeral[0] ^= 1;
    assert!(matches!(
        a.finish(&bh, [2; 32]),
        Err(CryptoError::Authentication)
    ));
    let (a, b) = pair();
    assert!(matches!(
        a.finish(&b.hello(), [3; 32]),
        Err(CryptoError::Authentication)
    ));
    let (a, _) = pair();
    let (_, other_host) = pair();
    assert!(matches!(
        a.finish(&other_host.hello(), [2; 32]),
        Err(CryptoError::Authentication)
    ));
}
#[test]
fn binary_packet_bounds_and_canonical_encoding() {
    let mut bytes = input::encode(&Input::ReleaseAll).unwrap();
    bytes.push(0);
    assert!(input::decode(&bytes).is_err());
    assert!(input::decode(&[0; 4097]).is_err());
    assert!(input::encode(&Input::Text(&"x".repeat(2049))).is_err());
    assert!(input::encode(&Input::Text("bad\0text")).is_err());
    assert!(
        input::encode(&Input::Button {
            button: 6,
            down: true
        })
        .is_err()
    );
    assert!(
        input::encode(&Input::Key {
            usage: 0xffff,
            down: true
        })
        .is_err()
    );
    // Truncated and unknown discriminants must not panic.
    for len in 0..80 {
        for byte in 0..=255 {
            let _ = input::decode(&vec![byte; len]);
        }
    }
}
#[test]
fn explicit_consent_rejection_timeout_and_disconnect() {
    let now = Instant::now();
    let mut s = Session::attended(now);
    assert!(!s.can_control());
    assert_eq!(
        s.authenticated_transport_ready(),
        Err(SessionError::Forbidden)
    );
    s.decide(true, now).unwrap();
    assert!(!s.can_control());
    s.authenticated_transport_ready().unwrap();
    assert!(s.can_control());
    s.close();
    assert!(!s.can_control());
    assert_eq!(s.decide(true, now), Err(SessionError::Forbidden));
    let mut rejected = Session::attended(now);
    rejected.decide(false, now).unwrap();
    assert_eq!(
        rejected.authenticated_transport_ready(),
        Err(SessionError::Forbidden)
    );
    let mut expired = Session::attended(now);
    assert_eq!(
        expired.decide(true, now + CONSENT_LIFETIME),
        Err(SessionError::Forbidden)
    );
    let mut pending = Session::attended(now);
    assert!(pending.expire(now + Duration::from_secs(91)));
    assert!(!Session::attended(now).can_control());
}
