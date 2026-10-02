// Mixel attended support guard. Runtime only: never change saved login preferences.
static MIXEL_SUPPORT_INVITE_UNTIL: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

fn support_invite_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

fn support_invite_guard_active(now: u64, until: u64) -> bool {
    now != 0 && now < until
}

pub fn renew_support_invite_attended() {
    MIXEL_SUPPORT_INVITE_UNTIL.store(
        support_invite_now_ms().saturating_add(90_000),
        std::sync::atomic::Ordering::SeqCst,
    );
}

pub fn support_invite_requires_click() -> bool {
    support_invite_guard_active(
        support_invite_now_ms(),
        MIXEL_SUPPORT_INVITE_UNTIL.load(std::sync::atomic::Ordering::SeqCst),
    )
}

pub fn support_invite_must_wait(required: bool, accepted: bool) -> bool {
    required && !accepted
}

#[cfg(test)]
mod mixel_support_invite_tests {
    use super::*;

    #[test]
    fn guard_requires_current_explicit_support_handoff() {
        assert!(!support_invite_guard_active(1_000, 0));
        assert!(!support_invite_guard_active(0, 90_000));
        assert!(support_invite_guard_active(1_000, 91_000));
        assert!(!support_invite_guard_active(91_000, 91_000));
        assert!(!support_invite_guard_active(91_001, 91_000));
    }

    #[test]
    fn support_handoff_renews_attended_guard() {
        renew_support_invite_attended();
        assert!(support_invite_requires_click());
    }

    #[test]
    fn guard_renewal_is_safe_during_concurrent_service_reads() {
        let readers: Vec<_> = (0..8)
            .map(|_| {
                std::thread::spawn(|| {
                    for _ in 0..1_000 {
                        renew_support_invite_attended();
                        assert!(support_invite_requires_click());
                    }
                })
            })
            .collect();
        for reader in readers {
            reader.join().unwrap();
        }
    }

    #[test]
    fn password_recent_session_2fa_and_switch_cannot_replace_customer_accept() {
        // The attended gate deliberately takes no password/trusted-session/2FA
        // input. Only the customer's connection-manager Authorize event releases it.
        assert!(support_invite_must_wait(true, false));
        assert!(!support_invite_must_wait(true, true));
        assert!(!support_invite_must_wait(false, false));
        assert!(!support_invite_must_wait(false, true));
    }
}
