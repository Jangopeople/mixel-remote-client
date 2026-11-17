use signaling_server::{
    delegated_auth::{TicketAuth, public_keys},
    server::{AppState, router},
    support_policy::SupportPolicy,
};
use std::{collections::HashSet, net::SocketAddr, sync::Arc};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let address: SocketAddr = std::env::var("MIXEL_SIGNAL_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8092".into())
        .parse()?;
    // Staging service is loopback-only. TLS/origin policy belongs at its local
    // reverse proxy. Production rs.mixel.ch is deliberately untouched.
    if !address.ip().is_loopback() {
        return Err("signaling must bind loopback behind a TLS proxy".into());
    }
    let auth = TicketAuth::new(public_keys(&std::env::var(
        "MIXEL_PEER_AUTHORITY_PUBLIC_KEYS",
    )?)?)?;
    let origins: HashSet<_> = std::env::var("MIXEL_WEB_ORIGINS")?
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .collect();
    if origins.is_empty()
        || origins.iter().any(|s| {
            reqwest::Url::parse(s).map_or(true, |url| {
                url.scheme() != "https" || url.origin().ascii_serialization() != *s
            })
        })
    {
        return Err("exact HTTPS web origins are required".into());
    }
    let policy = SupportPolicy::new(
        std::env::var("MIXEL_SUPPORT_POLICY_URL")?.parse()?,
        std::env::var("MIXEL_SUPPORT_POLICY_TOKEN")?,
    )?;
    // Open connections: MIXEL_MAX_CONNECTIONS or the default, within the open-file limit.
    let open_files = signaling_server::limits::raise_open_files();
    let max_connections = signaling_server::limits::max_connections(
        std::env::var("MIXEL_MAX_CONNECTIONS").ok().as_deref(),
        open_files,
    )?;
    eprintln!(
        "at most {max_connections} open connections (open-file limit {})",
        open_files.map_or("unknown".into(), |n| n.to_string())
    );
    let require_signed = std::env::var("MIXEL_AGENT_REQUIRE_SIGNED").is_ok_and(|value| value == "1");
    let state = AppState::new(Arc::new(auth), origins)
        .with_max_connections(max_connections)
        .with_support_policy(policy)
        .with_signed_commands_required(require_signed)
        .with_turn(signaling_server::turn::TurnConfig::from_env()?);
    // Running sessions survive a broker restart: saved at shutdown, restored at start (no secrets).
    let handover_file = std::env::var("MIXEL_HANDOVER_FILE").ok().map(std::path::PathBuf::from);
    if let Some(path) = &handover_file {
        let restored = signaling_server::handover::load(path, &state.registry);
        eprintln!("handover: {restored} running sessions restored, waiting for both sides to resume");
    }
    let listener = tokio::net::TcpListener::bind(address).await?;
    eprintln!(
        "Mixel Remote staging signaling listening on {}",
        listener.local_addr()?
    );
    let registry = state.registry.clone();
    // Online / last-seen record for the Remote portal (optional; SQL 025).
    if let Some(reporter) = signaling_server::presence::PresenceReporter::from_env()? {
        reporter.spawn(registry.clone());
        eprintln!("presence reporting every {} s", signaling_server::presence::INTERVAL.as_secs());
    }
    // Session report of Remote SaaS companies (same API and token as presence; SQL 029).
    let sessions = signaling_server::sessions::SessionReporter::from_env()?.map(Arc::new);
    if let Some(reporter) = &sessions {
        reporter.clone().spawn(registry.clone());
        eprintln!("session reporting every {} s", signaling_server::sessions::INTERVAL.as_secs());
    }
    let shutdown_registry = registry.clone();
    let shutdown_state = state.clone();
    axum::serve(listener, router(state))
        .with_graceful_shutdown(async move {
            stop_signal().await;
            shutdown_state.begin_shutdown();
            match &handover_file {
                Some(path) => {
                    let saved = signaling_server::handover::save(path, &shutdown_registry);
                    eprintln!("handover: {saved} running sessions saved for the next broker");
                }
                None => {
                    if let Ok(mut registry) = shutdown_registry.lock() {
                        registry.shutdown();
                    }
                }
            }
        })
        .await?;
    // The ends of the sessions the shutdown closed (a lost end is closed by the next broker's list).
    if let Some(reporter) = sessions {
        let _ = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            reporter.run_once(&registry, std::time::Instant::now()),
        )
        .await;
    }
    Ok(())
}

/// SIGINT (docker stop with STOPSIGNAL SIGINT, Ctrl-C) or SIGTERM.
async fn stop_signal() {
    #[cfg(unix)]
    {
        let Ok(mut term) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) else {
            let _ = tokio::signal::ctrl_c().await;
            return;
        };
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {}
            _ = term.recv() => {}
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
