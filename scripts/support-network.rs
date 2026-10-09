// Mixel's HTTPS transport is an ephemeral fallback. Saved settings are unchanged.
use std::sync::atomic::{AtomicBool, Ordering};

static HTTPS_FALLBACK: AtomicBool = AtomicBool::new(false);

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

pub fn is_mixel_wss(url: &str) -> bool {
    let Some(authority) = url.strip_prefix("wss://").and_then(|rest| rest.split('/').next()) else {
        return false;
    };
    authority.eq_ignore_ascii_case("rs.mixel.ch")
        || authority.eq_ignore_ascii_case("rs.mixel.ch:443")
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
}
