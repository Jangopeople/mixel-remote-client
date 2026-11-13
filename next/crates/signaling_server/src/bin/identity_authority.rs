//! Independent device delegation authority. Private key never belongs in the
//! broker container or a desktop service-config.json resource.
use ed25519_dalek::SigningKey;
use signaling_server::{
    auth::IsmAuth,
    identity_authority::{Authority, router},
    support_policy::SupportPolicy,
};
use std::{net::SocketAddr, path::Path};
#[cfg(unix)]
use std::io::Read;
#[cfg(unix)]
use zeroize::Zeroizing;

#[cfg(unix)]
fn signing_key(path: &Path) -> Result<SigningKey, Box<dyn std::error::Error>> {
    use std::{
        fs::OpenOptions,
        os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    };
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    let metadata = file.metadata()?;
    // SAFETY: geteuid has no preconditions and only reads this process identity.
    #[allow(unsafe_code)]
    let uid = unsafe { libc::geteuid() };
    if !metadata.is_file()
        || metadata.permissions().mode() & 0o077 != 0
        || metadata.uid() != uid
        || metadata.len() > 128
    {
        return Err(
            "identity signer requires a private regular file owned by its service user".into(),
        );
    }
    let mut data = Zeroizing::new(Vec::new());
    file.by_ref().take(129).read_to_end(&mut data)?;
    let mut seed = Zeroizing::new([0u8; 32]);
    if data.len() == 32 {
        seed.copy_from_slice(&data);
    } else {
        let text = std::str::from_utf8(&data)?.trim();
        if text.len() != 64 || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("identity signing key must contain exactly32 secret bytes".into());
        }
        for (index, byte) in seed.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&text[index * 2..index * 2 + 2], 16)?;
        }
    }
    if *seed == [0; 32] {
        return Err("identity signing key must be generated with secure randomness".into());
    }
    Ok(SigningKey::from_bytes(&seed))
}
#[cfg(not(unix))]
fn signing_key(_path: &Path) -> Result<SigningKey, Box<dyn std::error::Error>> {
    Err("identity authority requires a Unix private-secret mount".into())
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::var("MIXEL_PEER_AUTHORITY_KEY_FILE")?;
    let signer = signing_key(Path::new(&path))?;
    if std::env::args().nth(1).as_deref() == Some("--public-key") {
        let public: String = signer
            .verifying_key()
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        println!("{public}");
        return Ok(());
    }
    let address: SocketAddr = std::env::var("MIXEL_IDENTITY_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8093".into())
        .parse()?;
    if !address.ip().is_loopback() {
        return Err("identity authority must bind loopback behind its own HTTPS proxy".into());
    }
    let auth = IsmAuth::new(
        std::env::var("MIXEL_ISM_URL")?.parse()?,
        std::env::var("MIXEL_ISM_PUBLIC_KEY")?,
    )?;
    let listener = tokio::net::TcpListener::bind(address).await?;
    eprintln!(
        "Mixel Remote identity authority listening on {}",
        listener.local_addr()?
    );
    let mut authority = Authority::new(auth, signer);
    match (
        std::env::var("MIXEL_STARTUP_HOST_POLICY_URL"),
        std::env::var("MIXEL_STARTUP_HOST_POLICY_TOKEN"),
    ) {
        (Ok(url), Ok(token)) => {
            authority = authority
                .with_startup_policy(SupportPolicy::for_startup_host(url.parse()?, token)?);
        }
        (Err(std::env::VarError::NotPresent), Err(std::env::VarError::NotPresent)) => {}
        _ => return Err("startup host policy configuration must supply both URL and token".into()),
    }
    axum::serve(listener, router(authority))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
