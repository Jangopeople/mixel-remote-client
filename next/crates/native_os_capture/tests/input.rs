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
fn active() -> Session {
    let now = Instant::now();
    let mut session = Session::attended(now);
    session.decide(true, now).unwrap();
    session.authenticated_transport_ready().unwrap();
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
    let mut view = Session::attended_view_only(now);
    view.decide(true, now).unwrap();
    view.authenticated_transport_ready().unwrap();
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
