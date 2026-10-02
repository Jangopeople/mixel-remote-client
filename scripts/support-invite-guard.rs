// Mixel attended support guard. Runtime only: never change saved login preferences.
static MIXEL_SUPPORT_INVITE_UNTIL: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

pub const SUPPORT_INVITE_ATTESTATION: &str = "attended-runtime-v1";

pub fn support_invite_guard_is_confirmed(value: &str) -> bool {
    value == SUPPORT_INVITE_ATTESTATION
}

pub fn resolve_support_invite_attestation(
    ipc_reachable: bool,
    remote_proof: Option<&str>,
) -> &'static str {
    if !ipc_reachable {
        return "guard-unavailable";
    }
    match remote_proof {
        Some(SUPPORT_INVITE_ATTESTATION) => SUPPORT_INVITE_ATTESTATION,
        Some("") => "guard-unavailable",
        _ => "service-update-required",
    }
}

pub fn is_support_invite_arg(value: &str) -> bool {
    value.starts_with("mixel-remote://support?") || value == "--support-invite"
}

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

pub fn effective_support_approve_mode(saved: &str, attended: bool) -> String {
    if attended {
        "click".to_owned()
    } else {
        saved.to_owned()
    }
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
    fn support_uri_is_an_incoming_support_launch_not_outbound_connection() {
        assert!(is_support_invite_arg(
            "mixel-remote://support?invite=synthetic&apikey=synthetic"
        ));
        assert!(is_support_invite_arg("--support-invite"));
        assert!(!is_support_invite_arg("mixel-remote://123456"));
        assert!(!is_support_invite_arg(
            "mixel-remote://support.attacker.example?invite=synthetic"
        ));
        assert!(!is_support_invite_arg(
            "mixel-remote://support-something?invite=synthetic"
        ));
        assert!(!is_support_invite_arg("--connect"));
        assert!(!is_support_invite_arg(""));
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

    #[test]
    fn saved_password_mode_shows_customer_accept_during_attended_support() {
        let saved = "password";
        let effective = effective_support_approve_mode(saved, true);
        assert_eq!(effective, "click");
        assert_ne!(effective, "password"); // Existing CM predicate displays Accept.
        assert_eq!(saved, "password"); // No preference mutation.
        assert_eq!(effective_support_approve_mode(saved, false), "password");
        assert_eq!(effective_support_approve_mode("both", true), "click");
        assert_eq!(effective_support_approve_mode("both", false), "both");
    }

    #[test]
    fn legacy_generic_option_echo_cannot_confirm_runtime_guard() {
        assert!(!support_invite_guard_is_confirmed("Y"));
        assert!(!support_invite_guard_is_confirmed(""));
        assert!(!support_invite_guard_is_confirmed("click"));
        assert!(support_invite_guard_is_confirmed("attended-runtime-v1"));
    }

    #[test]
    fn external_old_installed_or_portable_service_cannot_borrow_local_guard() {
        assert_eq!(
            resolve_support_invite_attestation(true, Some("Y")),
            "service-update-required"
        );
        assert_eq!(
            resolve_support_invite_attestation(true, None),
            "service-update-required"
        );
        assert_eq!(
            resolve_support_invite_attestation(false, None),
            "guard-unavailable"
        );
        assert_eq!(
            resolve_support_invite_attestation(false, Some(SUPPORT_INVITE_ATTESTATION)),
            "guard-unavailable"
        );
        assert_eq!(
            resolve_support_invite_attestation(true, Some(SUPPORT_INVITE_ATTESTATION)),
            SUPPORT_INVITE_ATTESTATION
        );
    }
}
