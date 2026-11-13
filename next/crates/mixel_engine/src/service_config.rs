//! Public service configuration travels inside the signed app bundle. Secrets
//! and administrator/service-role API keys are never accepted as client config.
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde::Deserialize;
#[cfg(target_os = "macos")]
use std::{fs::File, io::Read};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServiceConfig {
    pub schema: u16,
    pub identity_url: String,
    pub public_key: String,
    pub signaling_url: String,
    pub authority_url: String,
    pub authority_keys: Vec<String>,
    #[serde(default = "default_provider")]
    pub oauth_provider: String,
    #[serde(default)]
    pub sso_domain: Option<String>,
    #[serde(default)]
    pub relay_only: bool,
    #[serde(default)]
    pub customer_signup: bool,
}
/// This build only controls other computers (the TestFlight Mac controller, and every iPhone/iPad build).
pub const fn controller_only() -> bool {
    cfg!(feature = "testflight-controller") || cfg!(target_os = "ios")
}

pub fn require_host() -> Result<(), String> {
    if controller_only() {
        return Err("This Mixel Remote controller cannot host connections. Use the separately installed Mixel Remote host.".into());
    }
    Ok(())
}

fn default_provider() -> String {
    "keycloak".into()
}
pub fn has_service_overrides() -> bool {
    [
        "MIXEL_ISM_URL",
        "MIXEL_ISM_PUBLIC_KEY",
        "MIXEL_SIGNALING_URL",
        "MIXEL_PEER_AUTHORITY_URL",
        "MIXEL_PEER_AUTHORITY_PUBLIC_KEYS",
    ]
    .iter()
    .any(|name| std::env::var_os(name).is_some())
}
#[cfg(target_os = "macos")]
pub fn relay_fixture_enabled() -> bool {
    option_env!("MIXEL_BUILD_RELAY_FIXTURE") == Some("1")
        && native_os_capture::macos_bundle::identifier().as_deref()
            == Some("ch.mixel.remote.next.qa")
        && ServiceConfig::load().is_ok_and(|config| config.relay_only)
}
impl ServiceConfig {
    pub fn validate_public_bytes(bytes: &[u8]) -> Result<(), String> {
        if bytes.len() > 32768 {
            return Err("Service configuration exceeds its size limit".into());
        }
        let config: Self =
            serde_json::from_slice(bytes).map_err(|_| "Invalid public service configuration")?;
        config.validate()
    }
    pub fn load() -> Result<Self, String> {
        let config = if cfg!(target_os = "macos") && has_service_overrides() && !controller_only() {
            Self {
                schema: 2,
                identity_url: std::env::var("MIXEL_ISM_URL")
                    .map_err(|_| "The identity service configuration is missing")?,
                public_key: std::env::var("MIXEL_ISM_PUBLIC_KEY")
                    .map_err(|_| "The public service key is missing")?,
                signaling_url: std::env::var("MIXEL_SIGNALING_URL")
                    .map_err(|_| "The connection-service configuration is missing")?,
                authority_url: std::env::var("MIXEL_PEER_AUTHORITY_URL")
                    .map_err(|_| "The device identity authority is not configured")?,
                authority_keys: std::env::var("MIXEL_PEER_AUTHORITY_PUBLIC_KEYS")
                    .map_err(|_| "The trusted identity authority keys are missing")?
                    .split(',')
                    .map(str::trim)
                    .map(str::to_owned)
                    .collect(),
                oauth_provider: std::env::var("MIXEL_OAUTH_PROVIDER")
                    .unwrap_or_else(|_| default_provider()),
                sso_domain: std::env::var("MIXEL_SSO_DOMAIN")
                    .ok()
                    .filter(|value| !value.is_empty()),
                customer_signup: false,
                relay_only: std::env::var("MIXEL_RELAY_ONLY").is_ok_and(|value| value == "1"),
            }
        } else {
            Self::read_packaged()?
        };
        config.validate()?;
        Ok(config)
    }

    #[cfg(target_os = "macos")]
    fn read_packaged() -> Result<Self, String> {
        let executable = std::env::current_exe().map_err(|_| "The app bundle is unavailable")?;
        let contents = executable
            .parent()
            .and_then(|directory| directory.parent())
            .filter(|directory| directory.file_name().is_some_and(|name| name == "Contents"))
            .ok_or("Run the configured Mixel Remote app bundle")?;
        let path = contents.join("Resources/service-config.json");
        let metadata = std::fs::symlink_metadata(&path)
            .map_err(|_| "This development build has no bundled Mixel service configuration")?;
        if !metadata.is_file() || metadata.len() > 32768 {
            return Err("The bundled service configuration is invalid".into());
        }
        let mut bytes = Vec::new();
        File::open(path)
            .map_err(|_| "The bundled service configuration is unavailable")?
            .take(32769)
            .read_to_end(&mut bytes)
            .map_err(|_| "The service configuration could not be read")?;
        if bytes.len() > 32768 {
            return Err("The service configuration exceeds its size limit".into());
        }
        serde_json::from_slice(&bytes)
            .map_err(|_| "The bundled service configuration is invalid".into())
    }

    /// iPhone and iPad: `service-config.json` inside the signed app bundle (next to the program).
    #[cfg(target_os = "ios")]
    fn read_packaged() -> Result<Self, String> {
        use std::io::Read;
        let executable = std::env::current_exe().map_err(|_| "The app bundle is unavailable")?;
        let path = executable.parent().ok_or("The app bundle is unavailable")?.join("service-config.json");
        let mut bytes = Vec::new();
        std::fs::File::open(path)
            .map_err(|_| "This build has no bundled Mixel service configuration")?
            .take(32769)
            .read_to_end(&mut bytes)
            .map_err(|_| "The service configuration could not be read")?;
        if bytes.len() > 32768 {
            return Err("The service configuration exceeds its size limit".into());
        }
        serde_json::from_slice(&bytes).map_err(|_| "The bundled service configuration is invalid".into())
    }

    #[cfg(any(target_os = "windows", target_os = "linux"))]
    fn read_packaged() -> Result<Self, String> {
        // Embed the same bounded public config in the executable. A mutable
        // file beside an installer cannot redirect the sign-in or peer trust.
        let bytes = include_bytes!(concat!(env!("OUT_DIR"), "/desktop-service-config.json"));
        if bytes.is_empty() {
            return Err("This development build has no bundled Mixel service configuration".into());
        }
        serde_json::from_slice(bytes)
            .map_err(|_| "The bundled service configuration is invalid".into())
    }
    fn validate(&self) -> Result<(), String> {
        if self.schema != 2
            || self.identity_url.len() > 2048
            || self.signaling_url.len() > 2048
            || self.authority_url.len() > 2048
        {
            return Err("Unsupported service configuration".into());
        }
        for raw in [&self.identity_url, &self.signaling_url, &self.authority_url] {
            if raw
                .chars()
                .any(|value| value.is_ascii_control() || value.is_whitespace())
                || reqwest::Url::parse(raw).is_ok_and(|url| url.port() == Some(0))
            {
                return Err("Invalid secure service URL".into());
            }
        }
        let identity: reqwest::Url = self
            .identity_url
            .parse()
            .map_err(|_| "Invalid identity service URL")?;
        signaling_server::auth::IsmAuth::new(identity, self.public_key.clone())
            .map_err(|_| "A public HTTPS Mixel identity service is required")?;
        let public = self.public_key.starts_with("sb_publishable_") || {
            let parts: Vec<_> = self.public_key.split('.').collect();
            parts.len() == 3
                && parts.iter().all(|part| !part.is_empty())
                && URL_SAFE_NO_PAD
                    .decode(parts[1])
                    .ok()
                    .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                    .is_some_and(|claims| {
                        claims.get("role").and_then(|role| role.as_str()) == Some("anon")
                    })
        };
        if !public {
            return Err(
                "Only an anonymous or publishable public service key may be bundled".into(),
            );
        }
        let authority: reqwest::Url = self
            .authority_url
            .parse()
            .map_err(|_| "Invalid device identity authority URL")?;
        if authority.scheme() != "https"
            || authority.host_str().is_none()
            || !authority.username().is_empty()
            || authority.password().is_some()
            || authority.query().is_some()
            || authority.fragment().is_some()
            || !matches!(authority.path(), "" | "/")
        {
            return Err("The device identity authority must use a public HTTPS origin".into());
        }
        self.roots()?;
        let signaling: reqwest::Url = self
            .signaling_url
            .parse()
            .map_err(|_| "Invalid connection-service URL")?;
        if signaling.scheme() != "wss"
            || signaling.host_str().is_none()
            || !signaling.username().is_empty()
            || signaling.password().is_some()
            || signaling.query().is_some()
            || signaling.fragment().is_some()
        {
            return Err("The Mixel connection service must use WSS without URL credentials".into());
        }
        if self.oauth_provider.is_empty()
            || self.oauth_provider.len() > 80
            || !self.oauth_provider.bytes().all(|byte| {
                byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_' || byte == b'-'
            })
        {
            return Err("Invalid sign-in provider configuration".into());
        }
        if self.sso_domain.as_ref().is_some_and(|domain| {
            domain.is_empty()
                || domain.len() > 253
                || !domain
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'.' || byte == b'-')
        }) {
            return Err("Invalid enterprise SSO domain".into());
        }
        Ok(())
    }
    pub fn roots(&self) -> Result<Vec<ed25519_dalek::VerifyingKey>, String> {
        if self.authority_keys.is_empty()
            || self.authority_keys.len() > 4
            || self.authority_keys.iter().any(|value| value.len() != 64)
        {
            return Err("Invalid identity authority key configuration".into());
        }
        signaling_server::delegated_auth::public_keys(&self.authority_keys.join(","))
            .map_err(|_| "Invalid identity authority public key".into())
    }
}
