//! Native-only ISM browser sign-in. PKCE verifier and bearer/refresh tokens
//! never enter the webview, URLs, logs or plaintext storage. No credential is
//! loaded automatically from another application.
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use futures_util::StreamExt;
use rand_core::{OsRng, RngCore};
use reqwest::{Client, Url};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use signaling_server::auth::{IsmAuth, Principal};
use std::{
    sync::{
        Arc, Weak,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Mutex,
    task::JoinHandle,
};
use uuid::Uuid;
use zeroize::{Zeroize, Zeroizing};

#[derive(Clone, Deserialize)]
#[serde(transparent)]
struct Secret(String);
impl Drop for Secret {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}
#[derive(Clone, Deserialize)]
struct Tokens {
    access_token: Secret,
    refresh_token: Secret,
    expires_in: u64,
}
impl Tokens {
    fn valid(&self) -> bool {
        !self.access_token.0.is_empty()
            && self.access_token.0.len() <= 16000
            && !self.refresh_token.0.is_empty()
            && self.refresh_token.0.len() <= 16000
            && (1..=31 * 86400).contains(&self.expires_in)
    }
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Factor {
    pub id: Uuid,
    pub factor_type: String,
    pub status: String,
    pub friendly_name: Option<String>,
}
#[derive(Deserialize)]
struct User {
    id: Uuid,
    #[serde(default)]
    factors: Vec<Factor>,
}
#[derive(Clone, Serialize)]
pub struct Account {
    pub remembered: bool,
    pub has_company: bool,
    pub has_personal: bool,
    pub profile: Uuid,
    pub organization: Option<Uuid>,
    pub staff: bool,
    pub personal: bool,
    pub display_name: Option<String>,
    pub companies: Vec<CompanyAdministration>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct CompanyAdministration {
    pub company_id: Uuid,
    pub company_name: String,
}
#[derive(Clone, Serialize)]
pub struct Status {
    pub phase: String,
    pub message: String,
    pub account: Option<Account>,
    pub factors: Vec<Factor>,
}
struct Inner {
    generation: u64,
    status: Status,
    tokens: Option<Tokens>,
    subject: Option<Uuid>,
    principal: Option<Principal>,
    client: Option<Arc<AuthClient>>,
    expires_at: u64,
    refresh_at: u64,
    task: Option<JoinHandle<()>>,
    protected: Vec<Weak<AtomicBool>>,
    verification_attempts: u8,
    personal_mode: bool,
    remember: bool,
    restore_started: bool,
    restored: bool,
}
#[derive(Clone)]
pub struct AccountAuth {
    inner: Arc<Mutex<Inner>>,
    operation: Arc<Mutex<()>>,
}
/// Native callers only. This type deliberately has no Debug or Serialize.
pub struct Credential {
    pub token: Zeroizing<String>,
    pub principal: Principal,
    pub ism: IsmAuth,
}
impl Default for AccountAuth {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner {
                generation: 0,
                status: Status {
                    phase: "signed_out".into(),
                    message: "Sign in with your Mixel account".into(),
                    account: None,
                    factors: Vec::new(),
                },
                tokens: None,
                subject: None,
                principal: None,
                client: None,
                expires_at: 0,
                refresh_at: 0,
                task: None,
                protected: Vec::new(),
                verification_attempts: 0,
                personal_mode: false,
                remember: false,
                restore_started: false,
                restored: false,
            })),
            operation: Arc::new(Mutex::new(())),
        }
    }
}
struct AuthClient {
    http: Client,
    base: Url,
    key: String,
    ism: IsmAuth,
}
impl AuthClient {
    async fn companies(&self, token: &str) -> Result<Vec<CompanyAdministration>, String> {
        self.request(
            "/rest/v1/rpc/remote_my_company_administration",
            Some(serde_json::json!({})),
            Some(token),
        )
        .await
    }
    async fn logout(&self, access: &str) -> bool {
        let Ok(url) = self.base.join("/auth/v1/logout?scope=local") else {
            return false;
        };
        self.http
            .post(url)
            .header("apikey", &self.key)
            .bearer_auth(access)
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .is_ok_and(|response| response.status().is_success())
    }
    fn configured() -> Result<(Arc<Self>, String, Option<String>), String> {
        let config = crate::service_config::ServiceConfig::load()?;
        let base: Url = config
            .identity_url
            .parse()
            .map_err(|_| "Invalid identity service configuration")?;
        let key = config.public_key;
        let ism = IsmAuth::new(base.clone(), key.clone())
            .map_err(|_| "Invalid public identity service configuration")?;
        let provider = config.oauth_provider;
        let domain = config.sso_domain;
        let http = Client::builder()
            .timeout(Duration::from_secs(12))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| "Cannot initialize secure sign-in transport")?;
        Ok((
            Arc::new(Self {
                http,
                base,
                key,
                ism,
            }),
            provider,
            domain,
        ))
    }
    async fn request<T: serde::de::DeserializeOwned>(
        &self,
        path: &str,
        body: Option<serde_json::Value>,
        token: Option<&str>,
    ) -> Result<T, String> {
        let url = self
            .base
            .join(path)
            .map_err(|_| "Invalid identity request")?;
        let request = if let Some(body) = body {
            self.http.post(url).json(&body)
        } else {
            self.http.get(url)
        };
        let mut request = request.header("apikey", &self.key);
        if let Some(token) = token {
            request = request.bearer_auth(token);
        }
        let response = request
            .send()
            .await
            .map_err(|_| "The Mixel identity service is unavailable")?;
        if !response.status().is_success() {
            return Err(format!(
                "Identity request was denied (HTTP {})",
                response.status().as_u16()
            ));
        }
        let mut data = Zeroizing::new(Vec::new());
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| "Identity response interrupted")?;
            if data.len() + chunk.len() > 65536 {
                return Err("Identity response exceeds limit".into());
            }
            data.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&data)
            .map_err(|_| "Identity service returned an invalid response".into())
    }
    async fn inspect(&self, tokens: &Tokens) -> Result<(User, Option<Principal>, bool), String> {
        if !tokens.valid() {
            return Err("Identity service returned invalid session credentials".into());
        }
        let user: User = self
            .request("/auth/v1/user", None, Some(&tokens.access_token.0))
            .await?;
        let mut setup = false;
        let principal = match self.ism.authenticate(&tokens.access_token.0).await {
            Ok(principal) => Some(principal),
            Err(_) => {
                // A verified authenticator means the account can still raise
                // its assurance level (for example a staff member reaching
                // aal2), so offer the verification code step rather than
                // personal setup. Personal setup is only for an account that
                // cannot verify any further and has no Remote profile yet.
                let has_verified_totp = user.factors.iter().any(|factor| {
                    factor.status == "verified" && factor.factor_type == "totp"
                });
                setup = !has_verified_totp
                    && self
                        .ism
                        .personal_account(&tokens.access_token.0)
                        .await
                        .map_err(|_| "Remote account service could not be reached")?
                        .is_some_and(|account| account.profile_id.is_none());
                // GoTrue has verified this bearer above. Its AAL may only
                // select the next verification step; it never grants access.
                let claims = tokens
                    .access_token
                    .0
                    .split('.')
                    .nth(1)
                    .and_then(|part| URL_SAFE_NO_PAD.decode(part).ok())
                    .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok());
                if !setup
                    && claims
                        .as_ref()
                        .and_then(|value| value.get("aal"))
                        .and_then(|value| value.as_str())
                        != Some("aal1")
                {
                    return Err("This account has no current Mixel authorization".into());
                }
                None
            }
        };
        Ok((user, principal, setup))
    }
}
fn now() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .map_err(|_| "System clock is unavailable".into())
}
fn random() -> Result<Zeroizing<String>, String> {
    let mut bytes = Zeroizing::new([0u8; 32]);
    OsRng
        .try_fill_bytes(&mut *bytes)
        .map_err(|_| "OS secure randomness is unavailable")?;
    Ok(Zeroizing::new(URL_SAFE_NO_PAD.encode(bytes.as_ref())))
}
fn revoke(inner: &mut Inner) {
    for weak in inner.protected.drain(..) {
        if let Some(flag) = weak.upgrade() {
            flag.store(true, Ordering::Release);
        }
    }
    inner.tokens = None;
    inner.subject = None;
    inner.principal = None;
    inner.expires_at = 0;
    inner.refresh_at = 0;
    inner.status.account = None;
    inner.status.factors.clear();
    inner.verification_attempts = 0;
}
impl AccountAuth {
    pub async fn company_settings_url(&self, company_id: Uuid) -> Result<String, String> {
        let (generation, client, token) = {
            let inner = self.inner.lock().await;
            if inner.status.phase != "authenticated" || now()? >= inner.expires_at {
                return Err("Sign in with Mixel ID to manage a company".into());
            }
            let client = inner.client.clone().ok_or("Identity service unavailable")?;
            let token = Zeroizing::new(
                inner
                    .tokens
                    .as_ref()
                    .ok_or("Sign-in expired")?
                    .access_token
                    .0
                    .clone(),
            );
            (inner.generation, client, token)
        };
        let companies = client.companies(&token).await?;
        let inner = self.inner.lock().await;
        if inner.generation != generation
            || inner.status.phase != "authenticated"
            || now()? >= inner.expires_at
            || !companies
                .iter()
                .any(|company| company.company_id == company_id)
        {
            return Err("Company administration is unavailable for this account".into());
        }
        Ok(format!("https://admin.mixel.ch/clients/{company_id}"))
    }

    pub async fn credential(
        &self,
        protect: Option<&Arc<AtomicBool>>,
    ) -> Result<Credential, String> {
        let mut inner = self.inner.lock().await;
        if inner.status.phase != "authenticated" || now()? >= inner.expires_at {
            return Err("A current, verified Mixel sign-in is required".into());
        }
        inner.protected.retain(|item| item.strong_count() > 0);
        if let Some(cancel) = protect {
            if inner.protected.len() >= 16 {
                return Err("Too many protected account operations".into());
            }
            inner.protected.push(Arc::downgrade(cancel));
        }
        Ok(Credential {
            token: Zeroizing::new(
                inner
                    .tokens
                    .as_ref()
                    .ok_or("Sign-in expired")?
                    .access_token
                    .0
                    .clone(),
            ),
            principal: inner
                .principal
                .clone()
                .ok_or("Account authorization unavailable")?,
            ism: inner
                .client
                .as_ref()
                .ok_or("Identity service unavailable")?
                .ism
                .clone(),
        })
    }
    pub async fn status(&self) -> Status {
        let mut inner = self.inner.lock().await;
        if inner.tokens.is_some() && now().is_ok_and(|time| time >= inner.expires_at) {
            revoke(&mut inner);
            inner.status.phase = "error".into();
            inner.status.message = "Account authorization expired; sign in again".into();
        }
        inner.status.clone()
    }
    #[cfg(feature = "qa-session")]
    pub async fn begin_qa(&self, path: std::ffi::OsString) -> Result<(), String> {
        let (client, _, _) = AuthClient::configured()?;
        let tokens: Tokens = serde_json::from_slice(
            &std::fs::read(path).map_err(|_| "QA session file unreadable")?,
        )
        .map_err(|_| "QA session file invalid")?;
        if !tokens.valid() {
            return Err("QA session file invalid".into());
        }
        let generation = {
            let mut inner = self.inner.lock().await;
            inner.generation += 1;
            inner.status = Status {
                phase: "verifying".into(),
                message: "Loading lab session".into(),
                account: None,
                factors: Vec::new(),
            };
            inner.client = Some(client.clone());
            inner.generation
        };
        let this = self.clone();
        let task = tokio::spawn(async move {
            if let Err(error) = this.apply(generation, client, tokens, None).await {
                this.fail(generation, &error).await;
                return;
            }
            this.maintain(generation).await;
        });
        self.inner.lock().await.task = Some(task);
        Ok(())
    }
    pub async fn begin(&self) -> Result<String, String> {
        let (client, provider, domain) = AuthClient::configured()?;
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .map_err(|_| "Cannot open the private sign-in callback")?;
        let address = listener
            .local_addr()
            .map_err(|_| "Sign-in callback unavailable")?;
        let verifier = random()?;
        let state = random()?;
        let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
        let redirect = format!("http://{address}/auth/callback?state={}", state.as_str());
        let generation = {
            let mut inner = self.inner.lock().await;
            if matches!(
                inner.status.phase.as_str(),
                "starting"
                    | "waiting_browser"
                    | "verifying"
                    | "mfa_required"
                    | "profile_setup"
                    | "authenticated"
            ) {
                return Err("Finish or cancel the current sign-in before starting another".into());
            }
            inner.generation = inner
                .generation
                .checked_add(1)
                .ok_or("Sign-in generation exhausted")?;
            inner.status = Status {
                phase: "starting".into(),
                message: "Opening secure browser sign-in".into(),
                account: None,
                factors: Vec::new(),
            };
            inner.client = Some(client.clone());
            inner.generation
        };
        let authorization: Result<Url, String> = if let Some(domain) = domain {
            #[derive(Deserialize)]
            struct Redirect {
                url: String,
            }
            client.request::<Redirect>("/auth/v1/sso",Some(serde_json::json!({"domain":domain,"redirect_to":redirect,"skip_http_redirect":true,"code_challenge":challenge,"code_challenge_method":"s256"})),None).await.and_then(|reply|reply.url.parse().map_err(|_|"Invalid SSO redirect".into()))
        } else {
            let mut url = client
                .base
                .join("/auth/v1/authorize")
                .map_err(|_| "Invalid sign-in endpoint")?;
            url.query_pairs_mut()
                .append_pair("provider", &provider)
                .append_pair("scopes", "openid profile email")
                .append_pair("redirect_to", &redirect)
                .append_pair("code_challenge", &challenge)
                .append_pair("code_challenge_method", "s256");
            Ok(url)
        };
        let url = match authorization {
            Ok(url)
                if url.scheme() == "https"
                    && url.host_str().is_some()
                    && url.username().is_empty()
                    && url.password().is_none() =>
            {
                url
            }
            _ => {
                self.fail(generation, "Cannot start secure browser sign-in")
                    .await;
                return Err("Cannot start secure browser sign-in".into());
            }
        };
        let this = self.clone();
        let mut inner = self.inner.lock().await;
        if inner.generation != generation || inner.status.phase != "starting" {
            return Err("Sign-in was cancelled".into());
        }
        inner.status.phase = "waiting_browser".into();
        inner.status.message = "Complete sign-in in your browser, then return here".into();
        // Set the waiting state before starting the callback task. A fast
        // callback must never have its verified state overwritten by begin.
        let task = tokio::spawn(async move {
            let result=async {
                let code=receive_code(listener,&state).await?;
                let tokens:Tokens=client.request("/auth/v1/token?grant_type=pkce",Some(serde_json::json!({"auth_code":code.as_str(),"code_verifier":verifier.as_str()})),None).await?;
                this.apply(generation,client,tokens,None).await
            }.await;
            if let Err(error) = result {
                this.fail(generation, &error).await;
                return;
            }
            this.maintain(generation).await;
        });
        inner.task = Some(task);
        Ok(url.to_string())
    }
    async fn apply(
        &self,
        generation: u64,
        client: Arc<AuthClient>,
        tokens: Tokens,
        expected: Option<Uuid>,
    ) -> Result<(), String> {
        let (user, principal, setup) = client.inspect(&tokens).await?;
        // One Mixel ID can have a company scope (team member or organization
        // member) and a personal Remote account. The chosen workspace decides
        // which one this app uses; the other stays available to switch to.
        let personal_mode = self.inner.lock().await.personal_mode;
        let (principal, has_company, has_personal) = match principal {
            Some(found) if found.is_personal() => {
                let company = client
                    .ism
                    .authenticate_organization(&tokens.access_token.0)
                    .await
                    .ok()
                    .filter(|scope| !scope.is_personal());
                let has_company = company.is_some();
                let needs_code = company.is_none()
                    && !personal_mode
                    && user
                        .factors
                        .iter()
                        .any(|factor| factor.status == "verified" && factor.factor_type == "totp")
                    && tokens
                        .access_token
                        .0
                        .split('.')
                        .nth(1)
                        .and_then(|part| URL_SAFE_NO_PAD.decode(part).ok())
                        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                        .and_then(|claims| claims.get("aal").and_then(|v| v.as_str()).map(str::to_owned))
                        .as_deref()
                        == Some("aal1");
                if needs_code {
                    (None, false, true)
                } else if personal_mode || company.is_none() {
                    (Some(found), has_company, true)
                } else {
                    (company, true, true)
                }
            }
            Some(found) => (Some(found), true, false),
            None => (None, false, false),
        };
        let display_name = if principal.as_ref().is_some_and(Principal::is_personal) {
            client
                .ism
                .personal_account(&tokens.access_token.0)
                .await
                .map_err(|_| "Remote account service could not be reached")?
                .and_then(|account| account.display_name)
        } else {
            None
        };
        if expected.is_some_and(|id| id != user.id) {
            return Err("Sign-in identity changed; sign in again".into());
        }
        let companies = if principal.is_some() {
            // Older identity deployments may lack the optional company RPC.
            client
                .companies(&tokens.access_token.0)
                .await
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        let factors: Vec<_> = user
            .factors
            .into_iter()
            .filter(|factor| factor.status == "verified" && factor.factor_type == "totp")
            .take(16)
            .collect();
        let mut inner = self.inner.lock().await;
        if inner.generation != generation {
            return Err("Sign-in was cancelled".into());
        }
        if let (Some(old), Some(new)) = (&inner.principal, &principal)
            && (old.user != new.user || old.tenant != new.tenant || old.staff != new.staff)
        {
            return Err("Account authorization changed; sign in again".into());
        }
        if principal.is_none() && factors.is_empty() && !setup {
            return Err("This Mixel ID does not have a verified Remote account. Verify your email with Mixel ID or contact your company administrator.".into());
        }
        inner.subject = Some(user.id);
        inner.expires_at = now()?.saturating_add(tokens.expires_in).min(
            principal
                .as_ref()
                .map_or(u64::MAX, |value| value.expires_at),
        );
        if inner.expires_at <= now()? {
            return Err("Account authorization expired".into());
        }
        let remaining = inner.expires_at.saturating_sub(now()?);
        if remaining < 5 {
            return Err("Account authorization is too close to expiry".into());
        }
        inner.refresh_at = inner.expires_at - (remaining / 5).clamp(1, 60);
        inner.tokens = Some(tokens);
        inner.status = if let Some(principal) = &principal {
            Status {
                phase: "authenticated".into(),
                message: "Mixel account verified".into(),
                account: Some(Account {
                    remembered: inner.remember,
                    has_company,
                    has_personal,
                    profile: principal.user,
                    organization: principal.tenant,
                    staff: principal.staff,
                    personal: principal.is_personal(),
                    display_name,
                    companies,
                }),
                factors: Vec::new(),
            }
        } else if setup {
            Status {
                phase: "profile_setup".into(),
                message: "Mixel ID verified. Enter your name to set up Remote.".into(),
                account: None,
                factors: Vec::new(),
            }
        } else {
            Status {
                phase: "mfa_required".into(),
                message: "Additional verification is required for this Mixel account".into(),
                account: None,
                factors,
            }
        };
        inner.principal = principal;
        let keep = (inner.remember && inner.status.phase == "authenticated")
            .then(|| Zeroizing::new(inner.tokens.as_ref().map(|t| t.refresh_token.0.clone()).unwrap_or_default()));
        drop(inner);
        if let Some(token) = keep {
            let _ = tokio::task::spawn_blocking(move || crate::session_store::save(&token)).await;
        }
        Ok(())
    }
    /// Turns "stay signed in on this computer" on or off.
    pub async fn set_remember(&self, enabled: bool) -> Result<(), String> {
        let token = {
            let mut inner = self.inner.lock().await;
            inner.remember = enabled;
            if let Some(account) = inner.status.account.as_mut() {
                account.remembered = enabled;
            }
            (enabled && inner.status.phase == "authenticated")
                .then(|| inner.tokens.as_ref().map(|t| Zeroizing::new(t.refresh_token.0.clone())))
                .flatten()
        };
        tokio::task::spawn_blocking(move || match token {
            Some(token) => crate::session_store::save(&token),
            None => {
                if !enabled {
                    crate::session_store::clear();
                }
                Ok(())
            }
        })
        .await
        .map_err(|_| "Session storage unavailable")?
    }
    /// Whether this launch restored a saved session (used for auto-available).
    pub async fn restored(&self) -> bool {
        self.inner.lock().await.restored
    }
    /// Once per app start: sign in again from a saved session, if any.
    pub async fn restore(&self) {
        {
            let mut inner = self.inner.lock().await;
            if inner.restore_started || inner.status.phase != "signed_out" {
                return;
            }
            inner.restore_started = true;
        }
        let Ok(Ok(Some(saved))) = tokio::task::spawn_blocking(crate::session_store::load).await else {
            return;
        };
        let Ok((client, _, _)) = AuthClient::configured() else {
            return;
        };
        let generation = {
            let mut inner = self.inner.lock().await;
            if inner.status.phase != "signed_out" {
                return;
            }
            inner.generation += 1;
            inner.remember = true;
            inner.status = Status {
                phase: "verifying".into(),
                message: "Signing in with your saved session".into(),
                account: None,
                factors: Vec::new(),
            };
            inner.client = Some(client.clone());
            inner.generation
        };
        let this = self.clone();
        let task = tokio::spawn(async move {
            let result: Result<(), String> = async {
                let tokens: Tokens = client
                    .request(
                        "/auth/v1/token?grant_type=refresh_token",
                        Some(serde_json::json!({"refresh_token": saved.as_str()})),
                        None,
                    )
                    .await?;
                this.apply(generation, client, tokens, None).await
            }
            .await;
            if result.is_err() {
                let _ = tokio::task::spawn_blocking(crate::session_store::clear).await;
                let mut inner = this.inner.lock().await;
                if inner.generation == generation {
                    revoke(&mut inner);
                    inner.remember = false;
                    inner.status.phase = "signed_out".into();
                    inner.status.message = "Your saved sign-in expired. Sign in again.".into();
                }
                return;
            }
            this.inner.lock().await.restored = true;
            this.maintain(generation).await;
        });
        self.inner.lock().await.task = Some(task);
    }
    async fn maintain(&self, generation: u64) {
        loop {
            tokio::time::sleep(Duration::from_secs(1)).await;
            let due = {
                let inner = self.inner.lock().await;
                if inner.generation != generation || inner.tokens.is_none() {
                    return;
                }
                let Ok(time) = now() else {
                    drop(inner);
                    self.fail(generation, "System clock is unavailable").await;
                    return;
                };
                if time >= inner.expires_at {
                    drop(inner);
                    self.fail(generation, "Account authorization expired; sign in again")
                        .await;
                    return;
                }
                inner.status.phase == "authenticated" && time >= inner.refresh_at
            };
            if due && let Err(error) = self.refresh(generation).await {
                self.fail(generation, &error).await;
                return;
            }
        }
    }
    async fn refresh(&self, generation: u64) -> Result<(), String> {
        let _operation = self.operation.lock().await;
        let (client, refresh, subject) = {
            let inner = self.inner.lock().await;
            if inner.generation != generation || inner.status.phase != "authenticated" {
                return Err("Sign-in was cancelled".into());
            }
            (
                inner.client.clone().ok_or("Identity service unavailable")?,
                Zeroizing::new(
                    inner
                        .tokens
                        .as_ref()
                        .ok_or("Sign-in expired")?
                        .refresh_token
                        .0
                        .clone(),
                ),
                inner.subject.ok_or("Sign-in identity unavailable")?,
            )
        };
        let tokens: Tokens = client
            .request(
                "/auth/v1/token?grant_type=refresh_token",
                Some(serde_json::json!({"refresh_token":refresh.as_str()})),
                None,
            )
            .await?;
        self.apply(generation, client, tokens, Some(subject))
            .await?;
        if self.inner.lock().await.status.phase != "authenticated" {
            return Err("Account authorization changed; sign in again".into());
        }
        Ok(())
    }
    async fn fail(&self, generation: u64, message: &str) {
        let mut inner = self.inner.lock().await;
        if inner.generation != generation {
            return;
        }
        revoke(&mut inner);
        inner.status.phase = "error".into();
        inner.status.message = message.to_owned();
    }
    pub async fn complete_personal_setup(&self, display_name: String) -> Result<(), String> {
        let _operation = self.operation.lock().await;
        let display_name = display_name.trim();
        if !(1..=80).contains(&display_name.chars().count())
            || display_name.chars().any(char::is_control)
        {
            return Err("Enter a display name between 1 and 80 characters".into());
        }
        let (client, tokens, generation, subject) = {
            let inner = self.inner.lock().await;
            if inner.status.phase != "profile_setup" || now()? >= inner.expires_at {
                return Err("Verify your Mixel ID again before setting up Remote".into());
            }
            (
                inner.client.clone().ok_or("Identity service unavailable")?,
                inner.tokens.clone().ok_or("Sign-in expired")?,
                inner.generation,
                inner.subject.ok_or("Sign-in identity unavailable")?,
            )
        };
        let profile: Uuid = client
            .request(
                "/rest/v1/rpc/remote_create_personal_account",
                Some(serde_json::json!({"requested_name": display_name})),
                Some(&tokens.access_token.0),
            )
            .await?;
        self.apply(generation, client, tokens, Some(subject))
            .await?;
        let current = self.inner.lock().await;
        if !current
            .principal
            .as_ref()
            .is_some_and(|principal| principal.is_personal() && principal.user == profile)
        {
            return Err("Remote account could not be verified; sign in again".into());
        }
        Ok(())
    }

    /// Switches between the company and the personal workspace of the same
    /// Mixel ID. Everything bound to the previous scope is cancelled.
    pub async fn set_workspace(&self, personal: bool) -> Result<(), String> {
        let _operation = self.operation.lock().await;
        let (client, tokens, generation, subject) = {
            let mut inner = self.inner.lock().await;
            let account = inner
                .status
                .account
                .as_ref()
                .filter(|_| inner.status.phase == "authenticated")
                .ok_or("Sign in before switching workspace")?;
            if (personal && !account.has_personal) || (!personal && !account.has_company) {
                return Err("This workspace is not available for your Mixel ID".into());
            }
            if account.personal == personal {
                return Ok(());
            }
            inner.personal_mode = personal;
            for weak in inner.protected.drain(..) {
                if let Some(flag) = weak.upgrade() {
                    flag.store(true, Ordering::Release);
                }
            }
            inner.principal = None;
            (
                inner.client.clone().ok_or("Identity service unavailable")?,
                inner.tokens.clone().ok_or("Sign-in expired")?,
                inner.generation,
                inner.subject.ok_or("Sign-in identity unavailable")?,
            )
        };
        self.apply(generation, client, tokens, Some(subject)).await
    }
    /// Adds a personal Remote account to a Mixel ID that already has a
    /// company scope, and switches to it.
    pub async fn create_personal_space(&self, display_name: String) -> Result<(), String> {
        let display_name = display_name.trim().to_owned();
        if !(1..=80).contains(&display_name.chars().count())
            || display_name.chars().any(char::is_control)
        {
            return Err("Enter a display name between 1 and 80 characters".into());
        }
        {
            let _operation = self.operation.lock().await;
            let (client, tokens) = {
                let inner = self.inner.lock().await;
                if inner.status.phase != "authenticated"
                    || inner.status.account.as_ref().is_none_or(|account| account.has_personal)
                {
                    return Err("A personal space is already available for this Mixel ID".into());
                }
                (
                    inner.client.clone().ok_or("Identity service unavailable")?,
                    inner.tokens.clone().ok_or("Sign-in expired")?,
                )
            };
            let _: Uuid = client
                .request(
                    "/rest/v1/rpc/remote_create_personal_account",
                    Some(serde_json::json!({"requested_name": display_name})),
                    Some(&tokens.access_token.0),
                )
                .await?;
            let mut inner = self.inner.lock().await;
            if let Some(account) = inner.status.account.as_mut() {
                account.has_personal = true;
            }
        }
        self.set_workspace(true).await
    }

    /// Self-service sign-up from the app: a Mixel ID without a company (not set up yet, or only a
    /// personal space) creates one (a 14-day Business trial, this account its owner) and switches
    /// to it. The database checks the confirmed email address and that no company exists yet.
    pub async fn create_company(
        &self,
        name: String,
    ) -> Result<signaling_server::auth::saas::SaasSignup, String> {
        let created = {
            let _operation = self.operation.lock().await;
            let (client, tokens, generation) = {
                let inner = self.inner.lock().await;
                let allowed = match inner.status.phase.as_str() {
                    "profile_setup" => true,
                    "authenticated" => inner
                        .status
                        .account
                        .as_ref()
                        .is_some_and(|account| !account.has_company && !account.staff),
                    _ => false,
                };
                if !allowed {
                    return Err("This Mixel ID already belongs to a company".into());
                }
                if now()? >= inner.expires_at {
                    return Err("Verify your Mixel ID again before creating a company".into());
                }
                (
                    inner.client.clone().ok_or("Identity service unavailable")?,
                    inner.tokens.clone().ok_or("Sign-in expired")?,
                    inner.generation,
                )
            };
            let created = client
                .ism
                .saas_signup(&tokens.access_token.0, &name)
                .await
                .map_err(|error| error.message)?;
            let mut inner = self.inner.lock().await;
            if inner.generation != generation {
                return Err("Sign-in was cancelled".into());
            }
            if let Some(account) = inner.status.account.as_mut() {
                account.has_company = true;
            }
            created
        };
        if self.inner.lock().await.status.phase == "authenticated" {
            // A personal space stays; the app now uses the new company.
            self.set_workspace(false).await?;
        } else {
            let (client, tokens, generation, subject) = {
                let inner = self.inner.lock().await;
                (
                    inner.client.clone().ok_or("Identity service unavailable")?,
                    inner.tokens.clone().ok_or("Sign-in expired")?,
                    inner.generation,
                    inner.subject.ok_or("Sign-in identity unavailable")?,
                )
            };
            self.apply(generation, client, tokens, Some(subject)).await?;
        }
        Ok(created)
    }

    pub async fn verify_totp(&self, factor: Uuid, code: Zeroizing<String>) -> Result<(), String> {
        let _operation = self
            .operation
            .try_lock()
            .map_err(|_| "Another identity operation is running")?;
        if !matches!(code.len(), 6 | 8) || !code.bytes().all(|b| b.is_ascii_digit()) {
            return Err("Enter the current 6- or 8-digit verification code".into());
        }
        let (generation, client, access, subject) = {
            let mut inner = self.inner.lock().await;
            if inner.status.phase != "mfa_required"
                || !inner.status.factors.iter().any(|item| item.id == factor)
            {
                return Err("This verification factor is unavailable".into());
            }
            if inner.verification_attempts >= 5 {
                revoke(&mut inner);
                inner.status.phase = "error".into();
                inner.status.message = "Verification attempt limit reached; sign in again".into();
                return Err(inner.status.message.clone());
            }
            inner.verification_attempts += 1;
            (
                inner.generation,
                inner.client.clone().ok_or("Identity service unavailable")?,
                Zeroizing::new(
                    inner
                        .tokens
                        .as_ref()
                        .ok_or("Sign-in expired")?
                        .access_token
                        .0
                        .clone(),
                ),
                inner.subject.ok_or("Sign-in identity unavailable")?,
            )
        };
        #[derive(Deserialize)]
        struct Challenge {
            id: Uuid,
        }
        let challenge: Challenge = client
            .request(
                &format!("/auth/v1/factors/{factor}/challenge"),
                Some(serde_json::json!({})),
                Some(&access),
            )
            .await?;
        let tokens: Tokens = client
            .request(
                &format!("/auth/v1/factors/{factor}/verify"),
                Some(serde_json::json!({"challenge_id":challenge.id,"code":code.as_str()})),
                Some(&access),
            )
            .await?;
        self.apply(generation, client, tokens, Some(subject)).await
    }
    pub async fn cancel(&self) -> Result<(), String> {
        let mut inner = self.inner.lock().await;
        inner.generation = inner
            .generation
            .checked_add(1)
            .ok_or("Sign-in generation exhausted")?;
        if let Some(task) = inner.task.take() {
            task.abort();
        }
        let revoke_remote = inner.client.clone().zip(
            inner
                .tokens
                .as_ref()
                .map(|tokens| Zeroizing::new(tokens.access_token.0.clone())),
        );
        let generation = inner.generation;
        revoke(&mut inner);
        inner.remember = false;
        inner.restored = false;
        inner.client = None;
        inner.status.phase = "signed_out".into();
        inner.status.message = "Signed out locally; no session credentials are retained".into();
        drop(inner);
        let _ = tokio::task::spawn_blocking(crate::session_store::clear).await;
        if let Some((client, access)) = revoke_remote {
            let revoked = client.logout(&access).await;
            let mut inner = self.inner.lock().await;
            if inner.generation == generation {
                inner.status.message=if revoked{"Signed out of this Mixel Remote session"}else{"Signed out locally; the identity service could not confirm remote session revocation"}.into();
            }
        }
        Ok(())
    }
}
async fn receive_code(listener: TcpListener, state: &str) -> Result<Zeroizing<String>, String> {
    let address = listener
        .local_addr()
        .map_err(|_| "Sign-in callback unavailable")?;
    tokio::time::timeout(Duration::from_secs(180),async {
        for _ in 0..256 {
            let (mut stream,peer)=listener.accept().await.map_err(|_|"Sign-in callback failed")?;
            if !peer.ip().is_loopback(){continue;}
            let mut request=Zeroizing::new(Vec::new());
            let read=tokio::time::timeout(Duration::from_secs(2),async {
                let mut chunk=[0;1024];
                while !request.windows(4).any(|bytes|bytes==b"\r\n\r\n") {
                    let count=stream.read(&mut chunk).await.map_err(|_|())?;if count==0||request.len()+count>8192{return Err(());}
                    request.extend_from_slice(&chunk[..count]);
                }Ok::<_,()>(())
            }).await;
            let code=if matches!(read,Ok(Ok(()))){callback_code(&request,address,state)}else{None};
            let (status,body)=if code.is_some(){("200 OK","Mixel Remote sign-in received. Return to the app to continue.")}else{("400 Bad Request","This is not an authorized Mixel Remote sign-in callback.")};
            let response=format!("HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\nConnection: close\r\n\r\n{body}",body.len());
            let _=tokio::time::timeout(Duration::from_secs(2),stream.write_all(response.as_bytes())).await;
            if let Some(code)=code{return Ok(code);}
        }
        Err("Sign-in callback request limit reached".to_owned())
    }).await.map_err(|_|"Browser sign-in expired; start a fresh sign-in".to_owned())?
}
fn callback_code(
    request: &[u8],
    address: std::net::SocketAddr,
    state: &str,
) -> Option<Zeroizing<String>> {
    let text = std::str::from_utf8(request).ok()?;
    let mut lines = text.split("\r\n");
    let mut first = lines.next()?.split(' ');
    if first.next()? != "GET" {
        return None;
    }
    let target = first.next()?;
    if !matches!(first.next()?, "HTTP/1.1" | "HTTP/1.0")
        || first.next().is_some()
        || !target.starts_with("/auth/callback?")
    {
        return None;
    }
    let expected = address.to_string();
    let hosts: Vec<_> = lines
        .filter_map(|line| line.split_once(':'))
        .filter(|(name, _)| name.eq_ignore_ascii_case("host"))
        .collect();
    if hosts.len() != 1 || hosts[0].1.trim() != expected {
        return None;
    }
    let url: Url = format!("http://{address}{target}").parse().ok()?;
    if url.path() != "/auth/callback" {
        return None;
    }
    let parameters: Vec<_> = url.query_pairs().collect();
    let states: Vec<_> = parameters
        .iter()
        .filter(|(name, _)| name == "state")
        .collect();
    let codes: Vec<_> = parameters
        .iter()
        .filter(|(name, _)| name == "code")
        .collect();
    if states.len() != 1
        || codes.len() != 1
        || states[0].1 != state
        || codes[0].1.is_empty()
        || codes[0].1.len() > 2048
        || codes[0].1.chars().any(char::is_control)
    {
        return None;
    }
    Some(Zeroizing::new(codes[0].1.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn native_callback_accepts_only_the_matching_browser_transaction() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let receiver = tokio::spawn(async move { receive_code(listener, "native-fixture").await });
        for (target, expected_status) in [
            ("/auth/callback?state=another-attempt&code=ignored", "400"),
            ("/auth/callback?state=native-fixture&code=one&code=two", "400"),
            ("/auth/callback?state=native-fixture&code=verified", "200"),
        ] {
            let mut connection = tokio::net::TcpStream::connect(address).await.unwrap();
            let request = format!("GET {target} HTTP/1.1\r\nHost: {address}\r\n\r\n");
            connection.write_all(request.as_bytes()).await.unwrap();
            let mut response = String::new();
            connection.read_to_string(&mut response).await.unwrap();
            assert!(response.starts_with(&format!("HTTP/1.1 {expected_status}")));
        }
        assert_eq!(receiver.await.unwrap().unwrap().as_str(), "verified");
    }
}
