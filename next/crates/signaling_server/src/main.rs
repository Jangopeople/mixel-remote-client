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
    let state = AppState::new(Arc::new(auth), origins)
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
    axum::serve(listener, router(state))
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            if let Ok(mut registry) = registry.lock() {
                registry.shutdown();
            }
        })
        .await?;
    Ok(())
}
