use signaling_server::{auth::Principal, protocol::*, registry::Registry};
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, watch};
use uuid::Uuid;
fn register(
    r: &mut Registry,
) -> (
    u32,
    Uuid,
    mpsc::Receiver<ServerMessage>,
    watch::Receiver<bool>,
) {
    let (tx, mut rx) = mpsc::channel(64);
    let (stop, stopped) = watch::channel(false);
    let (id, generation) = r
        .register(
            Principal {
                user: Uuid::new_v4(),
                tenant: Some(Uuid::nil()),
                staff: false,
                expires_at: u64::MAX,
            },
            tx,
            stop,
        )
        .unwrap();
    assert!(matches!(
        rx.try_recv().unwrap(),
        ServerMessage::Ready { .. }
    ));
    (id, generation, rx, stopped)
}
#[test]
fn timeout_duplicate_decision_session_limits_and_stale_disconnect() {
    let mut r = Registry::default();
    let now = Instant::now();
    let (a, ag, mut ar, _as) = register(&mut r);
    let (b, bg, mut br, _bs) = register(&mut r);
    r.handle(a, ag, ClientMessage::Request { host: b }, now)
        .unwrap();
    let ServerMessage::Requested { session } = ar.try_recv().unwrap() else {
        panic!()
    };
    br.try_recv().unwrap();
    assert_eq!(
        r.handle(
            b,
            bg,
            ClientMessage::Decide {
                session,
                accept: true
            },
            now + Duration::from_secs(90)
        ),
        Err(ErrorCode::Forbidden)
    );
    assert!(matches!(
        ar.try_recv().unwrap(),
        ServerMessage::Closed {
            reason: CloseReason::Expired,
            ..
        }
    ));
    br.try_recv().unwrap();
    r.disconnect(a, Uuid::new_v4());
    assert_eq!(r.counts(), (2, 0));
    for _ in 0..4 {
        r.handle(a, ag, ClientMessage::Request { host: b }, now)
            .unwrap();
    }
    assert_eq!(
        r.handle(a, ag, ClientMessage::Request { host: b }, now),
        Err(ErrorCode::Busy)
    );
    r.disconnect(a, ag);
    assert_eq!(r.counts(), (1, 0));
}
#[test]
fn slow_consumer_is_stopped_instead_of_unbounded_buffering() {
    let mut r = Registry::default();
    let (a, ag, mut ar, _as) = register(&mut r);
    let (tx, _rx) = mpsc::channel(1);
    let (stop, stopped) = watch::channel(false);
    let (b, bg) = r
        .register(
            Principal {
                user: Uuid::new_v4(),
                tenant: Some(Uuid::nil()),
                staff: false,
                expires_at: u64::MAX,
            },
            tx,
            stop,
        )
        .unwrap();
    // Ready occupies the only slot. Delivering consent must stop this peer.
    r.handle(a, ag, ClientMessage::Request { host: b }, Instant::now())
        .unwrap();
    assert!(*stopped.borrow());
    assert!(matches!(
        ar.try_recv().unwrap(),
        ServerMessage::Requested { .. }
    ));
    r.disconnect(b, bg);
    assert!(matches!(
        ar.try_recv().unwrap(),
        ServerMessage::Closed {
            reason: CloseReason::Disconnected,
            ..
        }
    ));
    assert_eq!(r.counts(), (1, 0));
}
#[test]
fn shutdown_stops_all_peers_and_removes_sessions() {
    let mut r = Registry::default();
    let (a, ag, _ar, astop) = register(&mut r);
    let (b, _bg, _br, bstop) = register(&mut r);
    r.handle(a, ag, ClientMessage::Request { host: b }, Instant::now())
        .unwrap();
    r.shutdown();
    assert!(*astop.borrow() && *bstop.borrow());
    assert_eq!(r.counts(), (0, 0));
}

#[test]
fn renewal_preserves_identity_tenant_privilege_and_generation() {
    let mut registry = Registry::default();
    let original = Principal {
        user: Uuid::new_v4(),
        tenant: Some(Uuid::new_v4()),
        staff: false,
        expires_at: 100,
    };
    let (tx, _rx) = mpsc::channel(64);
    let (stop, _stopped) = watch::channel(false);
    let (id, generation) = registry.register(original.clone(), tx, stop).unwrap();
    let mut renewed = original.clone();
    renewed.expires_at = 200;
    registry.renew(id, generation, renewed.clone()).unwrap();
    assert_eq!(
        registry.renew(id, Uuid::new_v4(), renewed),
        Err(ErrorCode::Forbidden)
    );
    for changed in [
        Principal {
            user: Uuid::new_v4(),
            ..original.clone()
        },
        Principal {
            tenant: Some(Uuid::new_v4()),
            ..original.clone()
        },
        Principal {
            staff: true,
            ..original.clone()
        },
    ] {
        assert_eq!(
            registry.renew(id, generation, changed),
            Err(ErrorCode::Forbidden)
        );
    }
    assert_eq!(registry.counts(), (1, 0));
}
