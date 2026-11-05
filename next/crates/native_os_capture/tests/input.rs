use core_protocol::{input::Input, session::Session};
use native_os_capture::input::*;
use std::{
    sync::{Arc, Mutex},
    time::Instant,
};
#[derive(Default)]
struct State {
    events: Vec<String>,
    fail_up: usize,
}
#[derive(Clone, Default)]
struct Fake(Arc<Mutex<State>>);
impl Injector for Fake {
    fn inject(&mut self, event: &Input<'_>) -> Result<(), InjectionError> {
        let mut state = self.0.lock().unwrap();
        if matches!(
            event,
            Input::Key { down: false, .. } | Input::Button { down: false, .. }
        ) && state.fail_up > 0
        {
            state.fail_up -= 1;
            return Err(InjectionError::Native("transient fixture failure".into()));
        }
        state.events.push(format!("{event:?}"));
        Ok(())
    }
}
fn proof() -> (
    core_protocol::crypto::Context,
    core_protocol::crypto::VerifiedSession,
) {
    use core_protocol::crypto::{Context, Handshake, Role};
    use ed25519_dalek::SigningKey;
    use rand_core::{OsRng, RngCore};
    let controller = SigningKey::generate(&mut OsRng);
    let host = SigningKey::generate(&mut OsRng);
    let mut session_id = [0; 32];
    OsRng.fill_bytes(&mut session_id);
    let context = Context {
        session_id,
        controller: controller.verifying_key(),
        host: host.verifying_key(),
    };
    let hh = Handshake::new(context.clone(), Role::Host, &host, [2; 32]).unwrap();
    let ch = Handshake::new(context.clone(), Role::Controller, &controller, [1; 32]).unwrap();
    let proof = hh.finish(&ch.hello(), [1; 32]).unwrap().verified_session();
    (context, proof)
}
fn active() -> Session {
    let now = Instant::now();
    let (context, proof) = proof();
    let mut session = Session::for_peer(now, context, core_protocol::crypto::Role::Host, true);
    session.decide(true, now).unwrap();
    session.authenticated_transport_ready(&proof, now).unwrap();
    session
}
#[test]
fn consent_and_closed_tokens_cannot_inject() {
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    assert!(matches!(
        router.register(Session::attended(Instant::now())),
        Err(InputError::Forbidden)
    ));
    let token = router.register(active()).unwrap();
    router.close(&token).unwrap();
    assert_eq!(
        router.dispatch(&token, &Input::Text("denied")),
        Err(InputError::Forbidden)
    );
    assert!(fake.0.lock().unwrap().events.is_empty());
}
#[test]
fn disconnect_releases_only_this_sessions_held_inputs() {
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    let a = router.register(active()).unwrap();
    let b = router.register(active()).unwrap();
    let shift = Input::Key {
        usage: 0xe1,
        down: true,
    };
    router.dispatch(&a, &shift).unwrap();
    router.dispatch(&b, &shift).unwrap();
    router
        .dispatch(
            &a,
            &Input::Button {
                button: 1,
                down: true,
            },
        )
        .unwrap();
    assert_eq!(router.held_count(), 2);
    router.close(&a).unwrap();
    assert_eq!(router.held_count(), 1);
    assert_eq!(fake.0.lock().unwrap().events.len(), 3); // one shift-down, mouse-down/up
    router
        .dispatch(
            &b,
            &Input::Key {
                usage: 0xe1,
                down: false,
            },
        )
        .unwrap();
    assert_eq!(router.held_count(), 0);
    assert_eq!(
        fake.0.lock().unwrap().events.last().unwrap(),
        "Key { usage: 225, down: false }"
    );
}
#[test]
fn failed_release_is_retained_and_retried_without_restoring_authority() {
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    let token = router.register(active()).unwrap();
    router
        .dispatch(
            &token,
            &Input::Key {
                usage: 4,
                down: true,
            },
        )
        .unwrap();
    fake.0.lock().unwrap().fail_up = 1;
    assert_eq!(router.close(&token), Err(InputError::Pending(1)));
    assert_eq!(router.held_count(), 1);
    assert_eq!(router.session_count(), 1);
    assert_eq!(
        router.dispatch(&token, &Input::Text("denied")),
        Err(InputError::Forbidden)
    );
    router.retry_closed().unwrap();
    assert_eq!(router.held_count(), 0);
    assert_eq!(router.session_count(), 0);
}
#[test]
fn focus_loss_releases_own_inputs_and_repeat_keeps_single_claim() {
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    let token = router.register(active()).unwrap();
    for _ in 0..4 {
        router
            .dispatch(
                &token,
                &Input::Key {
                    usage: 4,
                    down: true,
                },
            )
            .unwrap();
    }
    assert_eq!(router.held_count(), 1);
    assert_eq!(fake.0.lock().unwrap().events.len(), 4);
    router.dispatch(&token, &Input::ReleaseAll).unwrap();
    assert_eq!(router.held_count(), 0);
    router
        .dispatch(&token, &Input::Text("Grüezi 中文 😀"))
        .unwrap();
    assert_eq!(
        router.dispatch(
            &token,
            &Input::Button {
                button: 99,
                down: true
            }
        ),
        Err(InputError::Invalid)
    );
    assert_eq!(router.held_count(), 0);
}
#[test]
fn new_owner_during_failed_cleanup_is_not_released_by_old_cleanup() {
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    let a = router.register(active()).unwrap();
    router
        .dispatch(
            &a,
            &Input::Key {
                usage: 4,
                down: true,
            },
        )
        .unwrap();
    fake.0.lock().unwrap().fail_up = 1;
    assert!(router.close(&a).is_err());
    let b = router.register(active()).unwrap();
    router
        .dispatch(
            &b,
            &Input::Key {
                usage: 4,
                down: true,
            },
        )
        .unwrap();
    router.retry_closed().unwrap();
    assert_eq!(router.held_count(), 1);
    assert_eq!(fake.0.lock().unwrap().events.len(), 1);
    router.close(&b).unwrap();
    assert_eq!(router.held_count(), 0);
}

#[test]
fn active_view_only_session_cannot_get_an_input_capability() {
    let now = Instant::now();
    let (context, proof) = proof();
    let mut view = Session::for_peer(now, context, core_protocol::crypto::Role::Host, false);
    view.decide(true, now).unwrap();
    view.authenticated_transport_ready(&proof, now).unwrap();
    assert!(view.can_view());
    assert!(!view.can_control());
    let mut router = InputRouter::new(Fake::default());
    assert!(matches!(router.register(view), Err(InputError::Forbidden)));
}
#[test]
fn input_capacity_is_bounded_and_unknown_up_does_not_touch_os_state() {
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    let token = router.register(active()).unwrap();
    router
        .dispatch(
            &token,
            &Input::Key {
                usage: 4,
                down: false,
            },
        )
        .unwrap();
    assert!(fake.0.lock().unwrap().events.is_empty());
    for _ in 1..32 {
        router.register(active()).unwrap();
    }
    assert!(matches!(
        router.register(active()),
        Err(InputError::Capacity)
    ));
}

#[test]
fn encrypted_input_rejects_cross_session_grants_without_consuming_the_record() {
    use core_protocol::{
        crypto::{Context, Handshake, Role},
        input::encode,
    };
    use ed25519_dalek::SigningKey;
    use rand_core::OsRng;
    let controller = SigningKey::generate(&mut OsRng);
    let host = SigningKey::generate(&mut OsRng);
    let context = Context {
        session_id: [73; 32],
        controller: controller.verifying_key(),
        host: host.verifying_key(),
    };
    let hh = Handshake::new(context.clone(), Role::Host, &host, [2; 32]).unwrap();
    let ch = Handshake::new(context.clone(), Role::Controller, &controller, [1; 32]).unwrap();
    let controller_hello = ch.hello();
    let mut sender = ch.finish(&hh.hello(), [2; 32]).unwrap();
    let mut receiver = hh.finish(&controller_hello, [1; 32]).unwrap();
    let now = Instant::now();
    let mut session = Session::for_peer(now, context, Role::Host, true);
    session.decide(true, now).unwrap();
    session
        .authenticated_transport_ready(&receiver.verified_session(), now)
        .unwrap();
    let fake = Fake::default();
    let mut router = InputRouter::new(fake.clone());
    let wrong = router.register(active()).unwrap();
    let correct = router.register(session).unwrap();
    let packet = sender
        .seal(&encode(&Input::Text("bound input")).unwrap())
        .unwrap();
    assert_eq!(
        router.dispatch_encrypted(&wrong, &mut receiver, &packet),
        Err(InputError::Forbidden)
    );
    assert!(fake.0.lock().unwrap().events.is_empty());
    router
        .dispatch_encrypted(&correct, &mut receiver, &packet)
        .unwrap();
    assert_eq!(fake.0.lock().unwrap().events.len(), 1);
    assert!(
        router
            .dispatch_encrypted(&correct, &mut receiver, &packet)
            .is_err()
    );
    assert_eq!(fake.0.lock().unwrap().events.len(), 1);
}
