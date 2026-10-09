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
                return match websocket::WsFramedStream::new(endpoint, None, None, ms_timeout).await {
                    Ok(stream) => Ok(Stream::WebSocket(stream)),
                    Err(error) => {
                        crate::mixel_support_network::reset_https_fallback(&target_str);
                        Err(error)
                    }
                };
            }
            Err(error)
        }
    }
}''')
patch("libs/hbb_common/src/socket_client.rs",
      '''        return Ok(Stream::WebSocket(
            websocket::WsFramedStream::new(target_str, None, None, ms_timeout).await?,
        ));''',
      '''        return match websocket::WsFramedStream::new(target_str.clone(), None, None, ms_timeout).await {
            Ok(stream) => Ok(Stream::WebSocket(stream)),
            Err(error) => {
                if !Config::is_proxy() && !crate::config::option2bool(
                    "allow-websocket", &Config::get_option("allow-websocket"))
                {
                    crate::mixel_support_network::reset_https_fallback(&target_str);
                }
                Err(error)
            }
        };''')
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
patch("src/rendezvous_mediator.rs",
      '''    keep_alive: i32,
}''',
      '''    keep_alive: i32,
    mixel_relay_only: bool,
}''')
patch("src/rendezvous_mediator.rs",
      '        self, keys::*, option2bool, use_ws, Config, CONNECT_TIMEOUT, REG_INTERVAL, RENDEZVOUS_PORT,',
      '        self, keys::*, option2bool, Config, CONNECT_TIMEOUT, REG_INTERVAL, RENDEZVOUS_PORT,')
patch("src/rendezvous_mediator.rs",
      '''            addr: addr.clone(),
            host: host.clone(),''',
      '''            addr: addr.clone(),
            mixel_relay_only: false,
            host: host.clone(),''')
patch("src/rendezvous_mediator.rs",
      '''            addr: conn.local_addr().into_target_addr()?,
            host: host.clone(),''',
      '''            addr: conn.local_addr().into_target_addr()?,
            mixel_relay_only: hbb_common::mixel_support_network::relay_only_transport(
                &host, matches!(&conn, Stream::WebSocket(_))),
            host: host.clone(),''')
patch("src/rendezvous_mediator.rs",
      '    pub async fn start_tcp(server: ServerPtr, host: String) -> ResultType<()> {',
      '''    pub async fn start_tcp(server: ServerPtr, host: String) -> ResultType<()> {
        let result = Self::start_tcp_inner(server, host.clone()).await;
        if result.is_err() && !Config::is_proxy() && !config::option2bool(
            "allow-websocket", &Config::get_option("allow-websocket"))
            && hbb_common::mixel_support_network::reset_https_fallback(&host)
        {
            // An unsupported/offline gateway cannot trap later native recovery.
            // The outer registration loop retries; this delay is bounded at 8s.
            Config::reset_online();
            let delay = hbb_common::mixel_support_network::registration_retry_delay_ms(&host);
            sleep(delay as f32 / 1000.).await;
        }
        result
    }

    async fn start_tcp_inner(server: ServerPtr, host: String) -> ResultType<()> {''')
patch("src/rendezvous_mediator.rs",
      '''            Some(rendezvous_message::Union::RegisterPkResponse(rpr)) => {
                update_latency();''',
      '''            Some(rendezvous_message::Union::RegisterPkResponse(rpr)) => {
                if self.mixel_relay_only
                    && rpr.result.enum_value() == Ok(register_pk_response::Result::NOT_SUPPORT)
                {
                    Config::set_key_confirmed(false);
                    Config::set_host_key_confirmed(&self.host_prefix, false);
                    bail!("Mixel HTTPS registration unsupported; retry native transport");
                }
                update_latency();''')
patch("src/rendezvous_mediator.rs",
      '''                    Ok(register_pk_response::Result::OK) => {
                        Config::set_key_confirmed(true);''',
      '''                    Ok(register_pk_response::Result::OK) => {
                        hbb_common::mixel_support_network::registration_succeeded(&self.host);
                        Config::set_key_confirmed(true);''')
patch("src/rendezvous_mediator.rs",
      '''            || use_ws()
            || crate::is_udp_disabled()''',
      '''            || config::option2bool("allow-websocket", &Config::get_option("allow-websocket"))
            || hbb_common::mixel_support_network::https_fallback_active(&host)
            || crate::is_udp_disabled()''')
patch("src/rendezvous_mediator.rs",
      '        let relay = use_ws() || Config::is_proxy();',
      '''        let relay = self.mixel_relay_only || Config::is_proxy()
            || config::option2bool("allow-websocket", &Config::get_option("allow-websocket"));''')
patch("src/rendezvous_mediator.rs",
      '        let relay = use_ws() || Config::is_proxy() || ph.force_relay;',
      '''        let relay = self.mixel_relay_only || Config::is_proxy() || ph.force_relay
            || config::option2bool("allow-websocket", &Config::get_option("allow-websocket"));''')

# Registration can remain native while a later ID socket independently falls
# back to HTTPS. Check that new socket before publishing a local TCP address or
# constructing direct TCP/IPv6 attempts. Other native/UDP paths stay intact.
patch("src/rendezvous_mediator.rs",
      '''        if peer_addr_v6.port() > 0 && !relay {
            socket_addr_v6 = start_ipv6(
                peer_addr_v6,
                addr,''',
      '''        let direct_tcp_candidate = is_ipv4(&self.addr)
            && !relay && !config::is_disable_tcp_listen();
        if peer_addr_v6.port() > 0 && !relay && !direct_tcp_candidate {
            socket_addr_v6 = start_ipv6(
                peer_addr_v6,
                addr,''')
patch("src/rendezvous_mediator.rs",
      '        if is_ipv4(&self.addr) && !relay && !config::is_disable_tcp_listen() {',
      '        if direct_tcp_candidate {')
patch("src/rendezvous_mediator.rs",
      '''        socket_addr_v6: bytes::Bytes,
    ) -> ResultType<()> {
        let peer_addr = AddrMangle::decode(&fla.socket_addr);''',
      '''        mut socket_addr_v6: bytes::Bytes,
    ) -> ResultType<()> {
        let peer_addr = AddrMangle::decode(&fla.socket_addr);''')
patch("src/rendezvous_mediator.rs",
      '''        log::debug!("Handle intranet from {:?}", peer_addr);
        let mut socket = connect_tcp(&*self.host, CONNECT_TIMEOUT).await?;
        let local_addr = socket.local_addr();''',
      '''        log::debug!("Handle intranet from {:?}", peer_addr);
        let mut socket = connect_tcp(&*self.host, CONNECT_TIMEOUT).await?;
        if hbb_common::mixel_support_network::relay_only_transport(
            &self.host, matches!(&socket, Stream::WebSocket(_)))
        {
            drop(socket);
            return self.create_relay(
                fla.socket_addr.into(), relay_server, Uuid::new_v4().to_string(),
                server, true, true, socket_addr_v6,
                fla.control_permissions.into_option(),
            ).await;
        }
        let peer_addr_v6 = AddrMangle::decode(&fla.socket_addr_v6);
        if peer_addr_v6.port() > 0 {
            socket_addr_v6 = start_ipv6(
                peer_addr_v6, peer_addr, server.clone(),
                fla.control_permissions.clone().into_option(),
            ).await;
        }
        let local_addr = socket.local_addr();''')
patch("src/rendezvous_mediator.rs",
      '''        if peer_addr_v6.port() > 0 && !relay {
            socket_addr_v6 = start_ipv6(
                peer_addr_v6,
                peer_addr,''',
      '''        let direct_tcp_candidate = ph.udp_port <= 0 && !relay
            && ph.nat_type.enum_value() != Ok(NatType::SYMMETRIC)
            && Config::get_nat_type() != NatType::SYMMETRIC as i32
            && !config::is_disable_tcp_listen();
        if peer_addr_v6.port() > 0 && !relay && !direct_tcp_candidate {
            socket_addr_v6 = start_ipv6(
                peer_addr_v6,
                peer_addr,''')
patch("src/rendezvous_mediator.rs",
      '        let msg_punch = PunchHoleSent {',
      '        let mut msg_punch = PunchHoleSent {')
patch("src/rendezvous_mediator.rs",
      '''        let mut socket = {
            let socket = connect_tcp(&*self.host, CONNECT_TIMEOUT).await?;
            let local_addr = socket.local_addr();''',
      '''        let mut socket = {
            let socket = connect_tcp(&*self.host, CONNECT_TIMEOUT).await?;
            if hbb_common::mixel_support_network::relay_only_transport(
                &self.host, matches!(&socket, Stream::WebSocket(_)))
            {
                drop(socket);
                return self.create_relay(
                    msg_punch.socket_addr.into(), msg_punch.relay_server,
                    Uuid::new_v4().to_string(), server, true, true,
                    msg_punch.socket_addr_v6, control_permissions,
                ).await;
            }
            if peer_addr_v6.port() > 0 {
                msg_punch.socket_addr_v6 = start_ipv6(
                    peer_addr_v6, peer_addr, server.clone(), control_permissions.clone(),
                ).await;
            }
            let local_addr = socket.local_addr();''')

# A WebSocket gateway has no usable peer/local TCP adapter address. Force this
# exact connection through the existing authenticated relay path after the
# actual rendezvous socket exists, before NAT selection and PunchHoleRequest.
patch("src/client.rs",
      '''    pub force_relay: bool,
    pub direct: Option<bool>,''',
      '''    pub force_relay: bool,
    pub mixel_runtime_force_relay: bool,
    pub direct: Option<bool>,''')
patch("src/client.rs",
      '''        self.force_relay =
            config::option2bool("force-always-relay", &self.get_option("force-always-relay"))
                || force_relay
                || use_ws()
                || Config::is_proxy();''',
      '''        // The implicit process-wide fallback belongs to one transport,
        // not every peer. Explicit WebSocket/proxy/user choices stay intact.
        self.mixel_runtime_force_relay = false;
        self.force_relay =
            config::option2bool("force-always-relay", &self.get_option("force-always-relay"))
                || force_relay
                || config::option2bool("allow-websocket", &Config::get_option("allow-websocket"))
                || Config::is_proxy();''')
patch("src/client.rs",
      '''        if self.force_relay {
            config
                .options
                .insert("force-always-relay".to_owned(), "Y".to_owned());
        }''',
      '''        if self.force_relay && !self.mixel_runtime_force_relay {
            config
                .options
                .insert("force-always-relay".to_owned(), "Y".to_owned());
        }''')
patch("src/ui_session_interface.rs",
      '''        if true == force_relay {
            self.lc.write().unwrap().force_relay = true;
        }''',
      '''        if true == force_relay {
            let mut lch = self.lc.write().unwrap();
            lch.force_relay = true;
            // This explicit user choice keeps upstream's saved relay behavior.
            lch.mixel_runtime_force_relay = false;
        }''')
patch("src/client.rs",
      '        stop_udp_tx: Option<oneshot::Sender<()>>,',
      '        mut stop_udp_tx: Option<oneshot::Sender<()>>,')
patch("src/client.rs",
      '''        if crate::get_ipv6_punch_enabled() {
            crate::test_ipv6().await;
        }''',
      '''        // IPv6 discovery waits for the actual rendezvous transport below.''')
patch("src/client.rs",
      '''        let mut socket = socket?;
        let my_addr = socket.local_addr();''',
      '''        let mut socket = socket?;
        // Mixel HTTPS relay mode follows this socket, not a global preference.
        if hbb_common::mixel_support_network::relay_only_transport(
            &rendezvous_server, matches!(&socket, Stream::WebSocket(_)))
        {
            let lch = interface.get_lch();
            let mut lch = lch.write().unwrap();
            if !lch.force_relay {
                lch.mixel_runtime_force_relay = true;
            }
            lch.force_relay = true;
        }
        if interface.is_force_relay() {
            udp = (None, None);
            if let Some(tx) = stop_udp_tx.take() {
                let _ = tx.send(());
            }
        }
        let my_addr = socket.local_addr();
        if crate::get_ipv6_punch_enabled() && !interface.is_force_relay() {
            crate::test_ipv6().await;
        }''')
patch("src/client.rs",
      '        let my_nat_type = crate::get_nat_type(100).await;',
      '''        let my_nat_type = if interface.is_force_relay() {
            NatType::SYMMETRIC as i32
        } else {
            crate::get_nat_type(100).await
        };''')
patch("src/client.rs",
      '        let mut ipv6 = if crate::get_ipv6_punch_enabled() {',
      '        let mut ipv6 = if crate::get_ipv6_punch_enabled() && !interface.is_force_relay() {')
patch("src/client.rs",
      '                            if ph.is_udp && s.is_some() {',
      '                            if !interface.is_force_relay() && ph.is_udp && s.is_some() {')
patch("src/client.rs",
      '                            if !ph.socket_addr_v6.is_empty() && s.is_some() {',
      '                            if !interface.is_force_relay() && !ph.socket_addr_v6.is_empty() && s.is_some() {')
patch("src/client.rs",
      '                        if let Some(s) = ipv6.0 {',
      '                        if let Some(s) = ipv6.0.filter(|_| !interface.is_force_relay()) {')
patch("src/client.rs",
      '        if is_local || peer_nat_type == NatType::SYMMETRIC {',
      '        if interface.is_force_relay() || is_local || peer_nat_type == NatType::SYMMETRIC {')
direct_attempts = '''        let mut connect_futures = Vec::new();
        let fut = connect_tcp_local(peer, Some(local_addr), connect_timeout);
        connect_futures.push(
            async move {
                let conn = fut.await?;
                Ok((conn, None, "TCP"))
            }
            .boxed(),
        );
        if let Some(udp_socket_nat) = udp_socket_nat {
            connect_futures.push(udp_nat_connect(udp_socket_nat, "UDP", connect_timeout).boxed());
        }
        if let Some(udp_socket_v6) = udp_socket_v6 {
            connect_futures.push(udp_nat_connect(udp_socket_v6, "IPv6", connect_timeout).boxed());
        }
        // Run all connection attempts concurrently, return the first successful one
        let (mut conn, kcp, mut typ) = match select_ok(connect_futures).await {
            Ok(conn) => (Ok(conn.0 .0), conn.0 .1, conn.0 .2),
            Err(e) => (Err(e), None, ""),
        };'''
direct_guard = '''        let (mut conn, kcp, mut typ) = if interface.is_force_relay() {
            // Do not construct or poll direct TCP, UDP or IPv6 futures. Continue
            // through the existing relay request and authenticated encryption.
            (Err(anyhow!("Direct connection disabled for forced relay")), None, "")
        } else {
''' + direct_attempts.replace('        let (mut conn, kcp, mut typ) = match', '        match').replace('        };', '        }') + '''
        };'''
patch("src/client.rs", direct_attempts, direct_guard)
print("Mixel HTTPS support fallback patched: native first, exact socket-owned relay mode, no forced direct probes, strict TLS, runtime settings")
