use signaling_server::{
    auth::IsmAuth,
    server::{AppState, router},
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
    let auth = IsmAuth::new(
        std::env::var("MIXEL_ISM_URL")?.parse()?,
        std::env::var("MIXEL_ISM_PUBLIC_KEY")?,
    )?;
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
    let listener = tokio::net::TcpListener::bind(address).await?;
    eprintln!(
        "Mixel Remote staging signaling listening on {}",
        listener.local_addr()?
    );
    let state = AppState::new(Arc::new(auth), origins);
    let registry = state.registry.clone();
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
