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
    let state = AppState::new(Arc::new(auth), origins)
        .with_max_connections(max_connections)
        .with_support_policy(policy)
        .with_turn(signaling_server::turn::TurnConfig::from_env()?);
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
    axum::serve(listener, router(state))
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            if let Ok(mut registry) = shutdown_registry.lock() {
                registry.shutdown();
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
