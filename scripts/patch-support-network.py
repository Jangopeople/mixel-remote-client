#!/usr/bin/env python3
"""Add runtime HTTPS fallback for the exact Mixel relay, with strict TLS."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))


def patch(path, before, after):
    file = REPO / path
    source = file.read_text(encoding="utf-8")
    if after in source:
        return
    if source.count(before) != 1:
        raise RuntimeError(f"Pinned HTTPS transport patch anchor changed: {path}")
    file.write_text(source.replace(before, after, 1), encoding="utf-8")


(REPO / "libs/hbb_common/src/mixel_support_network.rs").write_text(
    (ROOT / "scripts/support-network.rs").read_text(encoding="utf-8"), encoding="utf-8")
patch("libs/hbb_common/src/lib.rs", "pub mod websocket;", "pub mod websocket;\npub mod mixel_support_network;")
patch("libs/hbb_common/src/config.rs",
      '    option2bool(option, &Config::get_option(option))\n}\n\npub fn allow_insecure_tls_fallback()',
      '    option2bool(option, &Config::get_option(option))\n        || (!Config::is_proxy()\n            && crate::mixel_support_network::https_fallback_active(&Config::get_rendezvous_server()))\n}\n\npub fn allow_insecure_tls_fallback()')
patch("libs/hbb_common/src/websocket.rs",
      '''    if !use_ws() {
        return endpoint.to_string();
    }

    if endpoint.is_empty() {''',
      '''    let explicit_websocket = crate::config::option2bool(
        "allow-websocket", &Config::get_option("allow-websocket"));
    if crate::mixel_support_network::is_mixel_endpoint(endpoint)
        && (explicit_websocket || (!Config::is_proxy()
            && crate::mixel_support_network::https_fallback_active(endpoint)))
    {
        // Exact Mixel ports keep their own meaning even if the main app has
        // another server/relay configured with unrelated port numbers.
        let path = if endpoint.ends_with(":21117") { "relay" } else { "id" };
        return format!("wss://rs.mixel.ch/ws/{path}");
    }
    // Implicit HTTPS fallback may rewrite only this exact Mixel endpoint.
    // A simultaneous custom server or a later proxy setting stays native.
    if !explicit_websocket || !use_ws() {
        return endpoint.to_string();
    }

    if endpoint.is_empty() {''')
patch("libs/hbb_common/src/websocket.rs",
      '        let danger_accept_invalid_cert = get_cached_tls_accept_invalid_cert(&url);',
      '''        // A failed certificate must never downgrade Mixel's HTTPS support.
        // Passing Some(false) skips upstream's permissive certificate retries.
        let danger_accept_invalid_cert = if crate::mixel_support_network::is_mixel_wss(url) {
            Some(false)
        } else {
            get_cached_tls_accept_invalid_cert(&url)
        };''')
patch("libs/hbb_common/src/socket_client.rs",
      '    connect_tcp_local(target, None, ms_timeout).await\n}',
      '''    // Preserve the native path on normal networks. If its Mixel port is
    // blocked, retry through the existing certificate-verified HTTPS relay.
    match connect_tcp_local(target, None, ms_timeout).await {
        Ok(stream) => Ok(stream),
        Err(error) => {
            if !Config::is_proxy() && crate::mixel_support_network::enable_https_fallback(&target_str) {
                let endpoint = check_ws(&target_str);
                return Ok(Stream::WebSocket(
                    websocket::WsFramedStream::new(endpoint, None, None, ms_timeout).await?,
                ));
            }
            Err(error)
        }
    }
}''')
patch("src/rendezvous_mediator.rs",
      '''                            if fails >= MAX_FAILS2 {
                                Config::update_latency(&host, -1);''',
      '''                            if fails >= MAX_FAILS2 {
                                if hbb_common::mixel_support_network::enable_https_fallback(&host) {
                                    bail!("Native Mixel registration unavailable; retry through HTTPS");
                                }
                                Config::update_latency(&host, -1);''')
patch("src/rendezvous_mediator.rs",
      '''        } else {
            Self::start_udp(server, host).await
        }
    }''',
      '''        } else {
            match Self::start_udp(server.clone(), host.clone()).await {
                Ok(()) => Ok(()),
                Err(error) => {
                    if hbb_common::mixel_support_network::enable_https_fallback(&host) {
                        Self::start_tcp(server, host).await
                    } else {
                        Err(error)
                    }
                }
            }
        }
    }''')
print("Mixel HTTPS support fallback patched: native first, exact relay only, strict TLS, runtime settings")
