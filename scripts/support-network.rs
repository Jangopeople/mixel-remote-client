// Mixel's HTTPS transport is an ephemeral fallback. Saved settings are unchanged.
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

static HTTPS_FALLBACK: AtomicBool = AtomicBool::new(false);
static REGISTRATION_FAILURES: AtomicU32 = AtomicU32::new(0);

pub fn is_mixel_endpoint(endpoint: &str) -> bool {
    let (host, port) = endpoint.rsplit_once(':').unwrap_or((endpoint, "21116"));
    host.eq_ignore_ascii_case("rs.mixel.ch")
        && matches!(port, "21115" | "21116" | "21117")
}

pub fn enable_https_fallback(endpoint: &str) -> bool {
    if !is_mixel_endpoint(endpoint) {
        return false;
    }
    HTTPS_FALLBACK.store(true, Ordering::SeqCst);
    true
}

pub fn https_fallback_active(endpoint: &str) -> bool {
    is_mixel_endpoint(endpoint) && HTTPS_FALLBACK.load(Ordering::SeqCst)
}

pub fn reset_https_fallback(endpoint: &str) -> bool {
    if !is_mixel_endpoint(endpoint) && !is_mixel_wss(endpoint) {
        return false;
    }
    HTTPS_FALLBACK.store(false, Ordering::SeqCst);
    true
}

pub fn registration_retry_delay_ms(endpoint: &str) -> u64 {
    if !is_mixel_endpoint(endpoint) && !is_mixel_wss(endpoint) {
        return 0;
    }
    let failures = REGISTRATION_FAILURES.fetch_update(Ordering::SeqCst, Ordering::SeqCst,
        |value| Some(value.saturating_add(1).min(4))).unwrap_or(4);
    1_000 << failures.min(3)
}

pub fn registration_succeeded(endpoint: &str) {
    if is_mixel_endpoint(endpoint) || is_mixel_wss(endpoint) {
        REGISTRATION_FAILURES.store(0, Ordering::SeqCst);
    }
}

pub fn is_mixel_wss(url: &str) -> bool {
    let Some(authority) = url.strip_prefix("wss://").and_then(|rest| rest.split('/').next()) else {
        return false;
    };
    authority.eq_ignore_ascii_case("rs.mixel.ch")
        || authority.eq_ignore_ascii_case("rs.mixel.ch:443")
}

pub fn relay_only_transport(endpoint: &str, websocket: bool) -> bool {
    // A concurrent attempt can enable the process-wide fallback while this
    // particular socket remains native. Select from its actual transport and
    // exact target, never the cached/global WebSocket preference.
    websocket && (is_mixel_endpoint(endpoint) || is_mixel_wss(endpoint))
}

pub fn registration_receive_timeout_ms(mixel_websocket: bool, keep_alive_ms: i32) -> u64 {
    // The Mixel bridge forwards a genuine server registration reply every 10s.
    // Use a 20s receive-freshness limit; an in-flight send still has its bounded
    // socket timeout. Native/custom/proxy retain the upstream keep-alive policy.
    if mixel_websocket {
        20_000
    } else {
        keep_alive_ms as u64 * 3 / 2
    }
}

#[cfg(test)]
mod mixel_support_network_tests {
    use super::*;

    #[test]
    fn fallback_only_accepts_the_exact_mixel_native_endpoints() {
        for endpoint in ["rs.mixel.ch", "RS.MIXEL.CH:21115", "rs.mixel.ch:21116", "rs.mixel.ch:21117"] {
            assert!(is_mixel_endpoint(endpoint));
        }
        for endpoint in ["", "rs.mixel.ch.attacker.example:21116", "user@rs.mixel.ch:21116", "127.0.0.1:21116", "other.example:21116", "rs.mixel.ch:443", "rs.mixel.ch:1234"] {
            assert!(!is_mixel_endpoint(endpoint));
            assert!(!enable_https_fallback(endpoint));
            assert!(!https_fallback_active(endpoint));
        }
    }

    #[test]
    fn runtime_fallback_does_not_follow_custom_server_changes() {
        assert!(enable_https_fallback("rs.mixel.ch:21116"));
        assert!(https_fallback_active("rs.mixel.ch"));
        assert!(https_fallback_active("rs.mixel.ch:21117"));
        assert!(!https_fallback_active("other.example"));
    }

    #[test]
    fn strict_tls_matches_exact_https_authority() {
        for url in ["wss://rs.mixel.ch/ws/id", "wss://RS.MIXEL.CH:443/ws/relay"] {
            assert!(is_mixel_wss(url));
        }
        for url in ["ws://rs.mixel.ch/ws/id", "wss://rs.mixel.ch.attacker.example/ws/id", "wss://user@rs.mixel.ch/ws/id", "wss://rs.mixel.ch:444/ws/id", "wss://other.example/ws/id"] {
            assert!(!is_mixel_wss(url));
        }
    }

    #[test]
    fn relay_policy_requires_the_actual_mixel_websocket() {
        enable_https_fallback("rs.mixel.ch");
        assert!(!relay_only_transport("rs.mixel.ch:21116", false));
        assert!(relay_only_transport("rs.mixel.ch:21116", true));
        assert!(relay_only_transport("wss://rs.mixel.ch/ws/id", true));
        assert!(!relay_only_transport("other.example:21116", true));
        assert!(!relay_only_transport("wss://other.example/ws/id", true));
        assert!(!relay_only_transport("rs.mixel.ch.attacker.example:21116", true));
    }

    #[test]
    fn failed_gateway_returns_to_native_with_bounded_retry() {
        registration_succeeded("rs.mixel.ch");
        enable_https_fallback("rs.mixel.ch");
        assert!(!reset_https_fallback("other.example:21116"));
        assert!(https_fallback_active("rs.mixel.ch"));
        assert!(reset_https_fallback("wss://rs.mixel.ch/ws/id"));
        assert!(!https_fallback_active("rs.mixel.ch"));
        assert_eq!(registration_retry_delay_ms("other.example"), 0);
        assert_eq!(registration_retry_delay_ms("rs.mixel.ch"), 1_000);
        assert_eq!(registration_retry_delay_ms("rs.mixel.ch"), 2_000);
        assert_eq!(registration_retry_delay_ms("rs.mixel.ch"), 4_000);
        for _ in 0..8 {assert_eq!(registration_retry_delay_ms("rs.mixel.ch"), 8_000);}
        registration_succeeded("rs.mixel.ch");
        assert_eq!(registration_retry_delay_ms("rs.mixel.ch"), 1_000);
    }

    #[test]
    fn registration_freshness_is_bounded_only_for_actual_mixel_websocket() {
        for keep_alive in [1_000, 60_000, 600_000] {
            assert_eq!(registration_receive_timeout_ms(true, keep_alive), 20_000);
            assert_eq!(registration_receive_timeout_ms(false, keep_alive), keep_alive as u64 * 3 / 2);
        }
    }
}
