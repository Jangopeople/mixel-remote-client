//! The engine commands of the native apps. Names, arguments and results match the desktop app's
//! window commands (`desktop_app/src/main.rs`, `saas_commands.rs`), so both apps behave the same and
//! the server sees the same calls. File and folder choices come from the native app as paths.
use mixel_engine::{account_auth::AccountAuth, device_identity::IdentityState, remote_session::RemoteSession};
use serde::de::DeserializeOwned;
use serde_json::{Map, Value, json};
use std::{
    path::PathBuf,
    sync::{Arc, atomic::AtomicBool},
};
use uuid::Uuid;

#[cfg(target_os = "macos")]
pub const PLATFORM: &str = "macos";
#[cfg(target_os = "ios")]
pub const PLATFORM: &str = "ios";
#[cfg(target_os = "windows")]
pub const PLATFORM: &str = "windows";
#[cfg(not(any(target_os = "macos", target_os = "ios", target_os = "windows")))]
pub const PLATFORM: &str = "other";

/// What the commands share: the account, the connection to Mixel and this computer's identity.
#[derive(Clone)]
pub struct Context {
    pub auth: AccountAuth,
    pub remote: RemoteSession,
    pub identity: Arc<IdentityState>,
    data_dir: PathBuf,
}

impl Context {
    pub fn new(data_dir: PathBuf) -> Result<Self, String> {
        if !data_dir.is_absolute() {
            return Err("The app data folder must be an absolute path".into());
        }
        std::fs::create_dir_all(&data_dir).map_err(|_| "The app data folder is unavailable")?;
        Ok(Self {
            auth: AccountAuth::default(),
            remote: RemoteSession::default(),
            identity: Arc::new(IdentityState::default()),
            data_dir,
        })
    }

    fn pending_removal(&self) -> PathBuf {
        self.data_dir.join("pending-self-removal.json")
    }

    /// Ends the sessions and goes offline, then forgets the sign-in in memory (as the desktop app
    /// does when it quits).
    pub async fn shutdown(&self) -> Result<(), String> {
        let stopped = self.remote.stop().await;
        let signed_out = self.auth.cancel().await;
        stopped.and(signed_out)
    }

    async fn offline(&self) -> bool {
        matches!(self.remote.status().await.phase.as_str(), "offline" | "error")
    }

    async fn open_identity(&self, create: bool) -> Result<(), String> {
        let saved = self.identity.clone();
        tokio::task::spawn_blocking(move || saved.open(create).map(|_| ()))
            .await
            .map_err(|_| "Device identity storage is unavailable")?
    }
}

pub fn uuid(text: &str) -> Result<Uuid, String> {
    text.parse().map_err(|_| "Invalid identifier".into())
}

/// The command's arguments. Keys may be written `snake_case` (preferred) or `camelCase`.
struct Args(Map<String, Value>);
impl Args {
    fn parse(raw: &str) -> Result<Self, String> {
        if raw.len() > 1 << 20 {
            return Err("The command arguments are too large".into());
        }
        let raw = if raw.trim().is_empty() { "{}" } else { raw };
        match serde_json::from_str(raw) {
            Ok(Value::Object(map)) => Ok(Self(map)),
            _ => Err("The command arguments must be a JSON object".into()),
        }
    }
    fn value(&self, name: &str) -> Value {
        if let Some(value) = self.0.get(name) {
            return value.clone();
        }
        let mut camel = String::new();
        let mut upper = false;
        for character in name.chars() {
            if character == '_' {
                upper = true;
            } else if upper {
                camel.extend(character.to_uppercase());
                upper = false;
            } else {
                camel.push(character);
            }
        }
        self.0.get(&camel).cloned().unwrap_or(Value::Null)
    }
    /// A required (or `Option`) argument.
    fn get<T: DeserializeOwned>(&self, name: &str) -> Result<T, String> {
        serde_json::from_value(self.value(name)).map_err(|_| format!("Missing or invalid argument: {name}"))
    }
}

fn out<T: serde::Serialize>(value: T) -> Result<String, String> {
    serde_json::to_string(&value).map_err(|_| "The result could not be prepared".into())
}

/// The device's system as the Mixel service records it (macos, windows or linux). iPhone and iPad
/// register as Apple devices ("macos"), as the current phone app does; their name says which.
fn os_name() -> &'static str {
    if cfg!(any(target_os = "macos", target_os = "ios")) {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    }
}

fn saas(error: signaling_server::auth::saas::SaasError) -> String {
    error.message
}

/// Open only validated HTTPS destinations in the system browser (the app may also open them itself).
async fn browser(url: &str) -> Result<(), String> {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        mixel_engine::system_browser::open(url).await
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = url;
        Err("Open this page from the app".into())
    }
}

pub async fn dispatch(context: &Context, command: &str, raw: &str) -> Result<String, String> {
    let args = Args::parse(raw)?;
    let auth = &context.auth;
    let remote = &context.remote;
    match command {
        // ---- Account ----
        "account_status" => {
            let restore = auth.clone();
            tokio::spawn(async move { restore.restore().await });
            out(auth.status().await)
        }
        "startup_settings" => {
            #[cfg(any(target_os = "macos", target_os = "windows"))]
            let autostart = tokio::task::spawn_blocking(mixel_engine::autostart::enabled).await.unwrap_or(false);
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let autostart = false;
            #[cfg(any(target_os = "macos", target_os = "windows"))]
            let launched = mixel_engine::autostart::launched_at_startup();
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let launched = false;
            out(json!({
                "remembered": auth.status().await.account.is_some_and(|account| account.remembered),
                "autostart": autostart,
                "launched_at_startup": launched,
                "restored": auth.restored().await,
            }))
        }
        "account_sign_in" => {
            #[cfg(feature = "qa-session")]
            if let Some(path) = std::env::var_os("MIXEL_QA_SESSION_FILE") {
                auth.begin_qa(path).await?;
                return out(Value::Null);
            }
            let url = auth.begin().await?;
            // The app opens the page itself (`show_link`: it shows the link to copy instead).
            if args.get::<Option<bool>>("open_browser")?.unwrap_or(false)
                && let Err(error) = browser(&url).await
            {
                auth.cancel().await?;
                return Err(error);
            }
            out(url)
        }
        "account_verify" => {
            let factor: Uuid = args.get("factor")?;
            let code: String = args.get("code")?;
            out(auth.verify_totp(factor, zeroize::Zeroizing::new(code)).await?)
        }
        "account_cancel" => out(auth.cancel().await?),
        "account_setup" => out(auth.complete_personal_setup(args.get("name")?).await?),
        "account_workspace" => {
            if !context.offline().await {
                return Err("Go offline before switching between company and personal devices".into());
            }
            out(auth.set_workspace(args.get("personal")?).await?)
        }
        "account_create_personal" => {
            if !context.offline().await {
                return Err("Go offline before adding a personal space".into());
            }
            out(auth.create_personal_space(args.get("name")?).await?)
        }
        "set_stay_signed_in" => out(auth.set_remember(args.get("enabled")?).await?),
        "set_autostart" => {
            #[cfg(any(target_os = "macos", target_os = "windows"))]
            {
                let enabled: bool = args.get("enabled")?;
                out(tokio::task::spawn_blocking(move || mixel_engine::autostart::set(enabled))
                    .await
                    .map_err(|_| "Cannot change the startup setting")??)
            }
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            Err("Starting with the computer is not available here".into())
        }
        "company_settings_url" => out(auth.company_settings_url(args.get("company_id")?).await?),
        "remote_signup_available" => out(mixel_engine::service_config::ServiceConfig::load()?.customer_signup),
        "legal_url" => {
            let page: String = args.get("page")?;
            let lang: Option<String> = args.get("lang")?;
            out(mixel_engine::legal::legal_url(&page, lang.as_deref())?)
        }
        // ---- Devices and enrolment ----
        "remote_devices" => {
            let after: Option<u32> = args.get("after")?;
            let search: String = args.get::<Option<String>>("search")?.unwrap_or_default();
            let credential = auth.credential(None).await?;
            let page = credential
                .ism
                .directory_scoped(&credential.token, credential.principal.is_personal(), after, &search)
                .await
                .map_err(|_| "The enrolled device directory is unavailable for this account")?;
            let fresh = auth.credential(None).await?;
            if fresh.principal.user != credential.principal.user
                || fresh.principal.tenant != credential.principal.tenant
                || fresh.principal.staff != credential.principal.staff
            {
                return Err("The signed-in account changed; reload the device directory".into());
            }
            out(page)
        }
        "enrollment_devices" => {
            let after: Option<Uuid> = args.get("after")?;
            let search: String = args.get::<Option<String>>("search")?.unwrap_or_default();
            let credential = auth.credential(None).await?;
            if !credential.principal.staff {
                return Err("Device enrollment requires an MFA-verified Mixel team account".into());
            }
            let page = credential
                .ism
                .enrollment_devices(&credential.token, after, &search)
                .await
                .map_err(|_| "Device enrollment records are unavailable for this account")?;
            let fresh = auth.credential(None).await?;
            if fresh.principal.user != credential.principal.user || !fresh.principal.staff {
                return Err("The enrollment account changed".into());
            }
            out(page)
        }
        "enrollment_companies" => {
            let credential = auth.credential(None).await?;
            if !credential.principal.staff {
                return Err("Adding a computer requires an MFA-verified Mixel team account".into());
            }
            out(credential
                .ism
                .enrollment_companies(&credential.token)
                .await
                .map_err(|_| "Companies are unavailable for this account")?)
        }
        "enrollment_current" => {
            let credential = auth.credential(None).await?;
            if !credential.principal.staff || context.open_identity(false).await.is_err() {
                return out(Value::Null);
            }
            out(credential
                .ism
                .enrollment_of_key(&credential.token, &context.identity.public_key()?)
                .await
                .map_err(|_| "This computer’s enrollment status is unavailable")?)
        }
        "enroll_this_device" => {
            let device: Uuid = args.get("device")?;
            if !args.get::<bool>("confirmed")? || device.is_nil() {
                return Err("Confirm the existing ISM record for this computer before enrollment".into());
            }
            if !context.offline().await {
                return Err("Go offline before enrolling this computer".into());
            }
            let cancelled = Arc::new(AtomicBool::new(false));
            let credential = auth.credential(Some(&cancelled)).await?;
            if !credential.principal.staff {
                return Err("Device enrollment requires an MFA-verified Mixel team account".into());
            }
            context.open_identity(true).await?;
            let enrolled = credential
                .ism
                .enroll(&credential.token, device, context.identity.public_key()?, &cancelled)
                .await
                .map_err(|_| "Enrollment could not be confirmed. Existing keys are never replaced; check the selected device record and refresh its enrollment status.")?;
            let fresh = auth.credential(None).await?;
            if fresh.principal.user != credential.principal.user || !fresh.principal.staff {
                return Err("The account changed; refresh this computer's enrollment status".into());
            }
            out(enrolled.connection_id)
        }
        "add_this_device" => {
            let company: Uuid = args.get("company")?;
            let name: String = args.get("name")?;
            let credential = auth.credential(None).await?;
            if !credential.principal.staff {
                return Err("Adding a computer requires an MFA-verified Mixel team account".into());
            }
            out(credential.ism.add_device(&credential.token, company, &name, os_name()).await.map_err(|_| {
                "This computer could not be added. A computer with this name may already be enrolled for the company; choose another name or pick it from the list."
            })?)
        }
        "register_personal_device" => {
            let name: String = args.get("name")?;
            if !context.offline().await {
                return Err("Go offline before registering this device".into());
            }
            let credential = auth.credential(None).await?;
            if !credential.principal.is_personal() {
                return Err("Set up your Remote account first".into());
            }
            let profile = credential.principal.user;
            let identity = tokio::task::spawn_blocking(move || IdentityState::personal(profile, true))
                .await
                .map_err(|_| "Device identity storage is unavailable")??;
            use signaling_server::auth::PersonalDeviceOs;
            #[cfg(target_os = "windows")]
            let os = PersonalDeviceOs::Windows;
            #[cfg(not(target_os = "windows"))]
            let os = PersonalDeviceOs::Macos;
            let id = credential
                .ism
                .register_personal_device(&credential.token, &name, os, &identity.public_key()?)
                .await
                .map_err(|_| "Could not register this device. Check the device name and account, then try again.")?;
            if auth.credential(None).await?.principal.user != profile {
                return Err("The signed-in account changed".into());
            }
            out(id)
        }
        "this_computer_name" => {
            #[cfg(target_os = "macos")]
            let name = std::process::Command::new("/usr/sbin/scutil")
                .arg("--get")
                .arg("ComputerName")
                .output()
                .ok()
                .filter(|output| output.status.success())
                .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned());
            #[cfg(target_os = "windows")]
            let name = std::env::var("COMPUTERNAME").ok().or(args.get::<Option<String>>("device_name")?);
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            let name: Option<String> = args.get("device_name")?;
            let name: String = name.unwrap_or_default().chars().filter(|c| !c.is_control()).take(80).collect();
            out(if name.trim().is_empty() { "This computer".to_owned() } else { name.trim().to_owned() })
        }
        "quick_phone_claim" => {
            let host: u32 = args.get("host")?;
            let credential = auth.credential(None).await?;
            if !credential.principal.staff {
                return out(false);
            }
            out(credential
                .ism
                .quick_phone_claim(&credential.token, host)
                .await
                .map_err(|_| "The phone could not be reached through Mixel. Please try again.")?)
        }
        // ---- The connection and sessions ----
        "remote_status" => out(remote.status().await),
        "remote_register" => {
            if context.pending_removal().exists() {
                return Err("This computer is being removed from Mixel Remote. It is removed as soon as it is online.".into());
            }
            let principal = auth.credential(None).await?.principal;
            let saved = context.identity.clone();
            let identity = tokio::task::spawn_blocking(move || {
                if principal.is_personal() {
                    return IdentityState::personal(principal.user, false).map(Arc::new);
                }
                saved.open(false)?;
                Ok(saved)
            })
            .await
            .map_err(|_| "Saved identity could not be loaded")??;
            out(remote.connect(auth.clone(), identity).await?)
        }
        "remote_disconnect" => out(remote.stop().await?),
        "remote_request" => {
            let host: u32 = args.get("host")?;
            let password: Option<String> = args.get("password")?;
            let unattended: Option<bool> = args.get("unattended")?;
            out(remote.request(host, password, unattended.unwrap_or_default()).await?)
        }
        "remote_unattended" => {
            mixel_engine::service_config::require_host()?;
            out(remote.configure_unattended(args.get("source")?, args.get("control")?).await?)
        }
        "remote_presence" => out(remote.presence(args.get("hosts")?).await?),
        "remote_pairing" => {
            mixel_engine::service_config::require_host()?;
            out(remote.pairing(args.get("confirmation")?).await?)
        }
        "remote_support_code" => {
            mixel_engine::service_config::require_host()?;
            out(remote.support_code(args.get("required")?).await?)
        }
        "remote_decide" => {
            mixel_engine::service_config::require_host()?;
            out(remote.decide(args.get("decision")?).await?)
        }
        "remote_clipboard" => out(remote.clipboard(args.get("session")?, args.get("push")?).await?),
        "remote_chat_messages" => {
            let session: Uuid = args.get("session")?;
            remote.require_chat_session(session).await?;
            out(remote.chat_hub().messages(session))
        }
        "remote_chat_send" => {
            let session: Uuid = args.get("session")?;
            remote.require_chat_session(session).await?;
            out(remote.chat_hub().send(session, args.get("text")?).await?)
        }
        "remote_file_status" => out(remote.file_scope(args.get("consent")?).await?.view()?),
        // The app shows its own Open panel first (only while `remote_file_status` says connected).
        "remote_file_send" => {
            let scope = remote.file_scope(args.get("consent")?).await?;
            scope.can_offer()?;
            let path: PathBuf = args.get::<String>("path")?.into();
            if !path.is_absolute() {
                return Err("Choose a file to send".into());
            }
            out(scope.offer(path).await?)
        }
        // The name the other side offered, for the app's Save panel.
        "remote_file_pending" => out(remote.file_scope(args.get("consent")?).await?.pending(args.get("id")?)?),
        "remote_file_accept" => {
            let scope = remote.file_scope(args.get("consent")?).await?;
            let id: Uuid = args.get("id")?;
            scope.pending(id)?;
            let path: PathBuf = args.get::<String>("path")?.into();
            if !path.is_absolute() {
                return Err("Choose where to save the received file".into());
            }
            out(scope.accept(id, path).await?)
        }
        "remote_file_cancel" => out(remote.file_scope(args.get("consent")?).await?.cancel(args.get("id")?)?),
        "remote_record_may_start" => {
            let session: Uuid = args.get("session")?;
            let status = remote.status().await;
            out(status.session == Some(session)
                && status.phase == "active"
                && !status.sharing
                && status.received > 0
                && remote.recording_hub().may_start(session))
        }
        "remote_record_start" => {
            let session: Uuid = args.get("session")?;
            let status = remote.status().await;
            if status.session != Some(session) || status.phase != "active" || status.sharing || status.received == 0 {
                return Err("Start a permitted recording after the remote screen appears".into());
            }
            let hub = remote.recording_hub();
            if !hub.may_start(session) {
                return Err("Recording permission expired".into());
            }
            let path: PathBuf = args.get::<String>("path")?.into();
            if !path.is_absolute() {
                return Err("Choose where to save the recording".into());
            }
            out(hub.start(session, path)?)
        }
        "remote_record_stop" => out(remote.recording_hub().stop(args.get("session")?)?),
        "remote_close" => out(remote.close(args.get("session")?).await?),
        "remote_stop_session" => out(remote.stop_session(args.get("consent")?).await?),
        "remote_restart" => out(remote.restart(args.get("session")?).await?),
        "remote_invite" => out(remote
            .invite(args.get("session")?, args.get("user")?, args.get("device")?, args.get("control")?)
            .await?),
        "remote_join" => out(remote.join(args.get("invitation")?).await?),
        "remote_dismiss_invitation" => {
            remote.dismiss_invitation(args.get("invitation")?).await;
            out(Value::Null)
        }
        "remote_switch_sides" => out(remote.switch_sides(args.get("session")?).await?),
        "remote_decide_join" => out(remote
            .decide_join(args.get("session")?, args.get("accept")?, args.get("control")?)
            .await?),
        "remote_remove_colleague" => out(remote.remove_colleague(args.get("session")?).await?),
        "meeting_create" => {
            let credential = auth.credential(None).await?;
            let response = reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(15))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|_| "Mixel Meet is unavailable")?
                .post("https://rs.mixel.ch/meet/api/rooms")
                .bearer_auth(credential.token.as_str())
                .json(&json!({ "title": "Mixel Remote session" }))
                .send()
                .await
                .map_err(|_| "Mixel Meet cannot be reached")?;
            if response.status().as_u16() == 429 {
                return Err("You have started many meetings. Please try again later.".into());
            }
            let room: Value = response
                .error_for_status()
                .map_err(|_| "The meeting could not be started")?
                .json()
                .await
                .map_err(|_| "The meeting could not be started")?;
            let id = room["room"].as_str().unwrap_or_default();
            let host = room["host"].as_str().unwrap_or_default();
            if !mixel_engine::session_guard::meeting_room(id) || !host.starts_with("https://rs.mixel.ch/meet/#") {
                return Err("The meeting could not be started".into());
            }
            out(json!({ "room": id, "invite": format!("https://rs.mixel.ch/meet/#{id}"), "host": host }))
        }
        // Checks a Mixel Meet link (only rs.mixel.ch/meet); the app opens it.
        "meeting_link" => {
            let link: String = args.get("link")?;
            let room = link.strip_prefix("https://rs.mixel.ch/meet/#").unwrap_or_default();
            let id = room.split('.').next().unwrap_or_default();
            let key_ok = room
                .split('.')
                .nth(1)
                .is_none_or(|key| key.len() == 32 && key.chars().all(|c| c.is_ascii_hexdigit()));
            if !mixel_engine::session_guard::meeting_room(id) || room.split('.').count() > 2 || !key_ok {
                return Err("This is not a Mixel Meet link".into());
            }
            out(link)
        }
        "ticket_note" => {
            let credential = auth.credential(None).await?;
            if !credential.principal.staff {
                return Err("Saving in a ticket requires an MFA-verified Mixel team account".into());
            }
            let ticket: String = args.get("ticket")?;
            let note: String = args.get("note")?;
            let (number, title) = credential
                .ism
                .ticket_note(&credential.token, ticket.trim(), &note, args.get("remote_id")?)
                .await
                .map_err(|_| "The note was not saved: check the ticket number")?;
            out(format!("{number} · {title}"))
        }
        "ai_connect" => {
            let address: String = args.get("address")?;
            let model: String = args.get("model")?;
            out(mixel_engine::ai_assistant::connect(&address, &model).await?)
        }
        "ai_ask" => {
            let address: String = args.get("address")?;
            let model: String = args.get("model")?;
            let system: String = args.get("system")?;
            let prompt: String = args.get("prompt")?;
            let image: Option<String> = args.get("image")?;
            out(mixel_engine::ai_assistant::ask(&address, &model, &system, &prompt, image.as_deref()).await?)
        }
        "rollout_create" => {
            let credential = auth.credential(None).await?;
            if !credential.principal.staff {
                return Err("Rollout requires an MFA-verified Mixel team account".into());
            }
            let label: String = args.get("label")?;
            let token = credential
                .ism
                .create_rollout_token(&credential.token, args.get("company")?, &label, args.get("days")?, args.get("computers")?)
                .await
                .map_err(|_| "The rollout token could not be created")?;
            deploy_link(token).await
        }
        "rollout_list" => {
            let credential = auth.credential(None).await?;
            out(credential
                .ism
                .rollout_tokens(&credential.token, args.get("company")?)
                .await
                .map_err(|_| "Rollout tokens are unavailable")?)
        }
        "rollout_revoke" => {
            let credential = auth.credential(None).await?;
            out(credential
                .ism
                .revoke_rollout_token(&credential.token, args.get("id")?)
                .await
                .map_err(|_| "The rollout token could not be revoked")?)
        }
        // Windows (2.0 and later) updates itself from the signed update channel, like the Tauri app did.
        #[cfg(not(target_os = "windows"))]
        "update_check" => update_check(&args).await,
        // ---- Company (SaaS) ----
        "saas_my_tenants" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_my_tenants(&c.token).await.map_err(saas)?)
        }
        "saas_tenant_overview" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_tenant_overview(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_signup" => {
            let terms: Option<bool> = args.get("terms")?;
            out(auth.create_company(args.get("company")?, terms == Some(true)).await?)
        }
        "saas_terms_status" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_terms_status(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_delete_company" => {
            let c = auth.credential(None).await?;
            let name: String = args.get("name")?;
            out(c.ism.saas_delete_company(&c.token, args.get("tenant")?, &name).await.map_err(saas)?)
        }
        "saas_delete_my_account" => {
            let c = auth.credential(None).await?;
            let email: String = args.get("email")?;
            out(c.ism.saas_delete_my_account(&c.token, &email).await.map_err(saas)?)
        }
        "saas_members" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_members(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_invite_member" => {
            let c = auth.credential(None).await?;
            let email: String = args.get("email")?;
            let role: String = args.get("role")?;
            let locale: Option<String> = args.get("locale")?;
            out(c.ism.saas_invite_member(&c.token, args.get("tenant")?, &email, &role, locale.as_deref()).await.map_err(saas)?)
        }
        "saas_resend_invite" => {
            let c = auth.credential(None).await?;
            let locale: Option<String> = args.get("locale")?;
            out(c.ism.saas_resend_invite(&c.token, args.get("tenant")?, args.get("invite")?, locale.as_deref()).await.map_err(saas)?)
        }
        "saas_invites" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_invites(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_revoke_invite" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_revoke_invite(&c.token, args.get("tenant")?, args.get("invite")?).await.map_err(saas)?)
        }
        "saas_set_member_role" => {
            let c = auth.credential(None).await?;
            let role: String = args.get("role")?;
            out(c.ism.saas_set_member_role(&c.token, args.get("tenant")?, args.get("member")?, &role).await.map_err(saas)?)
        }
        "saas_remove_member" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_remove_member(&c.token, args.get("tenant")?, args.get("member")?).await.map_err(saas)?)
        }
        "saas_devices" => {
            let c = auth.credential(None).await?;
            let search: Option<String> = args.get("search")?;
            out(c.ism.saas_devices(&c.token, args.get("tenant")?, args.get("group")?, search.as_deref()).await.map_err(saas)?)
        }
        "saas_rename_device" => {
            let c = auth.credential(None).await?;
            let name: String = args.get("name")?;
            out(c.ism.saas_rename_device(&c.token, args.get("tenant")?, args.get("device")?, &name).await.map_err(saas)?)
        }
        "saas_move_device" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_move_device(&c.token, args.get("tenant")?, args.get("device")?, args.get("group")?).await.map_err(saas)?)
        }
        "saas_remove_device" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_remove_device(&c.token, args.get("tenant")?, args.get("device")?).await.map_err(saas)?)
        }
        "saas_add_this_device" => {
            if !context.offline().await {
                return Err("Go offline before adding this computer".into());
            }
            let c = auth.credential(None).await?;
            context.open_identity(true).await?;
            let name: String = args.get("name")?;
            let enrolled = c
                .ism
                .saas_add_this_device(&c.token, args.get("tenant")?, &name, os_name(), &context.identity.public_key()?, args.get("group")?)
                .await
                .map_err(saas)?;
            if auth.credential(None).await?.principal.user != c.principal.user {
                return Err("The signed-in account changed".into());
            }
            out(enrolled)
        }
        "saas_groups" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_groups(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_create_group" => {
            let c = auth.credential(None).await?;
            let name: String = args.get("name")?;
            out(c.ism.saas_create_group(&c.token, args.get("tenant")?, &name).await.map_err(saas)?)
        }
        "saas_rename_group" => {
            let c = auth.credential(None).await?;
            let name: String = args.get("name")?;
            out(c.ism.saas_rename_group(&c.token, args.get("tenant")?, args.get("group")?, &name).await.map_err(saas)?)
        }
        "saas_delete_group" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_delete_group(&c.token, args.get("tenant")?, args.get("group")?).await.map_err(saas)?)
        }
        "saas_set_group_access" => {
            let c = auth.credential(None).await?;
            let technicians: Vec<Uuid> = args.get("technicians")?;
            out(c.ism.saas_set_group_access(&c.token, args.get("tenant")?, args.get("group")?, &technicians).await.map_err(saas)?)
        }
        "saas_device_presence" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_device_presence(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_rename_tenant" => {
            let c = auth.credential(None).await?;
            let name: String = args.get("name")?;
            out(c.ism.saas_rename_tenant(&c.token, args.get("tenant")?, &name).await.map_err(saas)?)
        }
        "saas_create_deploy_link" => {
            let c = auth.credential(None).await?;
            let label: String = args.get("label")?;
            let token = c
                .ism
                .saas_create_deploy_link(&c.token, args.get("tenant")?, &label, args.get("days")?, args.get("computers")?, args.get("group")?)
                .await
                .map_err(saas)?;
            deploy_link(token).await
        }
        "saas_deploy_links" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_deploy_links(&c.token, args.get("tenant")?).await.map_err(saas)?)
        }
        "saas_revoke_deploy_link" => {
            let c = auth.credential(None).await?;
            out(c.ism.saas_revoke_deploy_link(&c.token, args.get("tenant")?, args.get("link")?).await.map_err(saas)?)
        }
        "device_self_remove" => self_remove(context, &args).await,
        "device_self_remove_flush" => {
            let path = context.pending_removal();
            let Ok(raw) = std::fs::read(&path) else {
                return out(Value::Null);
            };
            let Ok(request) = serde_json::from_slice::<signaling_server::auth::saas::SelfRemoval>(&raw) else {
                let _ = std::fs::remove_file(&path);
                return out(Value::Null);
            };
            out(Some(mixel_engine::self_removal::deliver(&path, &request).await?))
        }
        "device_removal_pending" => out(context.pending_removal().exists()),
        "open_device_identity" => {
            let saved = context.identity.clone();
            let create: bool = args.get("create")?;
            out(tokio::task::spawn_blocking(move || saved.open(create))
                .await
                .map_err(|_| "Identity worker failed")??)
        }
        _ => platform(context, command, &args).await,
    }
}

/// Deploy link or rollout token with the one-line command per operating system.
async fn deploy_link(token: String) -> Result<String, String> {
    let windows_command = mixel_engine::rollout::windows_rollout_command(&token).await?;
    let (macos_command, linux_command) = mixel_engine::rollout::unix_rollout_commands(&token)?;
    out(json!({ "token": token, "windows_command": windows_command, "macos_command": macos_command, "linux_command": linux_command }))
}

/// The native preview is published on store.mixel.ch as its own variant; it never installs the
/// desktop app's update. `variant`: the store variant of this build (`native-preview`).
#[cfg_attr(target_os = "windows", allow(dead_code))]
async fn update_check(args: &Args) -> Result<String, String> {
    #[derive(serde::Deserialize)]
    struct Row {
        version: String,
    }
    let variant: String = args.get::<Option<String>>("variant")?.unwrap_or_else(|| "native-preview".into());
    if variant.len() > 40 || !variant.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'-') {
        return Err("Invalid update variant".into());
    }
    let running = mixel_engine::version_parts(mixel_engine::agent::APP_VERSION);
    let url = format!(
        "https://store-api.mixel.ch/rest/v1/store_app_releases?select=version&app_slug=eq.mixel-remote&is_current=eq.true&platform=eq.{PLATFORM}&variant=eq.{variant}&limit=5"
    );
    let rows: Vec<Row> = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "Update check unavailable")?
        .get(url)
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "Update check unavailable")?
        .json()
        .await
        .map_err(|_| "Update check unavailable")?;
    out(rows
        .into_iter()
        .filter(|row| row.version.len() <= 40)
        .filter_map(|row| mixel_engine::version_parts(&row.version).map(|parts| (parts, row.version)))
        .filter(|(parts, _)| running.as_ref().is_some_and(|current| parts > current))
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, version)| json!({ "version": version, "installable": false, "silent": false, "needs_password": false })))
}

/// This computer removes itself: the request is signed with its device key, saved, then sent.
async fn self_remove(context: &Context, args: &Args) -> Result<String, String> {
    use mixel_engine::self_removal::{deliver, save_pending};
    use signaling_server::auth::saas::SelfRemoval;
    #[derive(serde::Deserialize)]
    struct RegistrationRef {
        device: Uuid,
        connection_id: u32,
        generation: u64,
    }
    if !args.get::<bool>("confirmed")? {
        return Err("Confirm that this computer should be removed".into());
    }
    if !context.offline().await {
        return Err("Go offline before removing this computer".into());
    }
    context.open_identity(false).await?;
    let identity = &context.identity;
    let registration = match args.get::<Option<RegistrationRef>>("registration")? {
        Some(known) => known,
        None => {
            let credential = context.auth.credential(None).await?;
            let device = credential
                .ism
                .device_for_key(&credential.token, &identity.public_key()?)
                .await
                .map_err(|_| "This computer has no Mixel Remote registration to remove")?;
            let enrolled = credential
                .ism
                .authenticate_device(&credential.token, device)
                .await
                .map_err(|_| "This computer's registration is unavailable")?;
            RegistrationRef { device, connection_id: enrolled.connection_id, generation: enrolled.generation }
        }
    };
    let issued_at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| "The clock of this computer is wrong")?
        .as_secs();
    let request = SelfRemoval::new(registration.device, registration.connection_id, registration.generation, issued_at, |text| {
        identity.sign_self_removal(text)
    })?;
    if !request.verify(&identity.public_key()?) {
        return Err("The removal request could not be signed".into());
    }
    let path = context.pending_removal();
    let json = serde_json::to_vec(&request).map_err(|_| "The removal request could not be saved")?;
    save_pending(&path, &json).map_err(|_| "The removal request could not be saved")?;
    out(deliver(&path, &request).await?)
}

/// Commands that need this computer itself: its screens, keyboard permission, printer, sign-in screen.
#[cfg(target_os = "macos")]
async fn platform(context: &Context, command: &str, args: &Args) -> Result<String, String> {
    use mixel_engine::{host_actions, host_print, ipp_printer, macos_prelogin_install, service_config};
    match command {
        "capture_targets" => {
            service_config::require_host()?;
            let mut targets = Vec::new();
            if native_os_capture::macos::screen_access() {
                let displays = tokio::task::spawn_blocking(native_os_capture::macos::displays)
                    .await
                    .map_err(|_| "Display discovery failed")?
                    .map_err(|error| error.to_string())?;
                targets.extend(displays.into_iter().map(|display| {
                    json!({
                        "id": format!("display:{}", display.id),
                        "name": format!("Display {} · {} × {}", display.id, display.width, display.height),
                        "full_display": true,
                    })
                }));
            }
            out(targets)
        }
        "request_screen_access" => {
            service_config::require_host()?;
            out(tokio::task::spawn_blocking(native_os_capture::macos::request_screen_access)
                .await
                .map_err(|_| "Screen-recording permission request failed")?)
        }
        "screen_access" => out(native_os_capture::macos::screen_access()),
        "request_input_access" => {
            service_config::require_host()?;
            out(native_os_capture::macos_input::MacInjector::request_post_access())
        }
        "copy_connection_id" => {
            service_config::require_host()?;
            let id = context
                .remote
                .status()
                .await
                .host
                .ok_or("Make this computer available before copying its connection ID")?;
            out(mixel_engine::session_platform::write_text(&id.to_string())?)
        }
        "connect_support_id" => {
            service_config::require_host()?;
            let id: String = args.get("id")?;
            out(tokio::task::spawn_blocking(move || mixel_engine::macos_support::connect(&id))
                .await
                .map_err(|_| "The Support connection could not start")??)
        }
        "wake_on_lan" => {
            let macs: Vec<String> = args.get("macs")?;
            if macs.is_empty() || macs.len() > 4 {
                return Err("No network address is known for this computer yet".into());
            }
            out(tokio::task::spawn_blocking(move || macs.iter().try_for_each(|mac| host_actions::wake(mac)))
                .await
                .map_err(|_| "The wake signal could not be sent")??)
        }
        "remote_printing_status" => {
            let installed = tokio::task::spawn_blocking(host_print::installed).await.unwrap_or(false);
            let supported = tokio::task::spawn_blocking(host_print::print_system_available).await.unwrap_or(false);
            out(json!({ "supported": supported, "installed": installed }))
        }
        "remote_printing_set" => {
            let enabled: bool = args.get("enabled")?;
            out(tokio::task::spawn_blocking(move || ipp_printer::set_installed(enabled))
                .await
                .map_err(|_| "The printer could not be changed")??)
        }
        "print_job" => {
            let path: String = args.get("path")?;
            let open: bool = args.get("open")?;
            out(tokio::task::spawn_blocking(move || {
                if open {
                    host_print::open(&path).map(|()| "opened".to_owned())
                } else {
                    host_print::print(&path).map(str::to_owned)
                }
            })
            .await
            .map_err(|_| "The print job could not be handled")??)
        }
        "signin_screen_status" => {
            let status = macos_prelogin_install::public_status();
            out(json!({
                "supported": macos_prelogin_install::shipped_bundle().is_some(),
                "outdated": false,
                "technician": false,
                "connection_id": status.as_ref().map(|status| status.connection_id),
                "name": status.map(|status| status.name),
                "wayland_greeter": false,
            }))
        }
        "signin_screen_disable" => out(tokio::task::spawn_blocking(|| {
            macos_prelogin_install::run_as_administrator(&macos_prelogin_install::remove_script()).map(|_| ())
        })
        .await
        .map_err(|_| "Administrator step failed")??),
        "open_browser" => {
            let url: String = args.get("url")?;
            out(browser(&url).await?)
        }
        _ => Err(format!("Unknown command: {command}")),
    }
}

/// Windows: this computer's screens, printer, sign-in screen and start-up. Administrator steps run
/// the engine helper next to the app (`app_scope::program`), elevated with one prompt each.
#[cfg(target_os = "windows")]
async fn platform(context: &Context, command: &str, args: &Args) -> Result<String, String> {
    use mixel_engine::{host_actions, host_print, self_update, service_config, windows_signin_host as signin};
    let newer = |version: &str| {
        matches!(
            (mixel_engine::version_parts(mixel_engine::agent::APP_VERSION), mixel_engine::version_parts(version)),
            (Some(running), Some(offered)) if offered > running
        )
    };
    match command {
        // The signed update channel (windows/x64): {version, installable, silent, needs_password} or null.
        // `silent`: nothing would be interrupted (no session) and the sign-in survives the restart.
        "update_check" => {
            if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
                return out(Value::Null);
            }
            let Ok(manifest) = self_update::latest().await else { return out(Value::Null) };
            if !newer(&manifest.version) {
                return out(Value::Null);
            }
            let idle = matches!(context.remote.status().await.phase.as_str(), "offline" | "error" | "ready");
            let account = context.auth.status().await;
            let keeps_sign_in = account.phase != "authenticated" || account.account.as_ref().is_some_and(|account| account.remembered);
            out(json!({ "version": manifest.version, "installable": true, "silent": idle && keeps_sign_in, "needs_password": false }))
        }
        // Downloads, checks (size, SHA-256, Mixel's code signature) and installs it; the app quits then.
        "update_install" => {
            if cfg!(feature = "qa-session") && std::env::var_os("MIXEL_UPDATE_CHANNEL").is_none() {
                return Err("Lab builds do not update themselves".into());
            }
            let manifest = self_update::latest().await?;
            if !newer(&manifest.version) {
                return Err("This is already the newest version".into());
            }
            out(self_update::download_and_install(manifest).await?)
        }
        "capture_targets" => {
            service_config::require_host()?;
            let displays = tokio::task::spawn_blocking(native_os_capture::windows_capture::displays)
                .await
                .map_err(|_| "Display discovery failed")?
                .map_err(|error| error.to_string())?;
            out(displays
                .into_iter()
                .map(|display| {
                    json!({
                        "id": format!("dxgi:{}:{}:{}", display.adapter, display.output, display.name),
                        "name": format!("{} · {} × {}", display.name, display.width, display.height),
                        "full_display": true,
                    })
                })
                .collect::<Vec<_>>())
        }
        // Windows needs no screen-recording or accessibility permission.
        "screen_access" | "request_screen_access" | "request_input_access" => out(true),
        "copy_connection_id" => {
            service_config::require_host()?;
            let id = context
                .remote
                .status()
                .await
                .host
                .ok_or("Make this computer available before copying its connection ID")?;
            out(mixel_engine::session_platform::write_text(&id.to_string())?)
        }
        "wake_on_lan" => {
            let macs: Vec<String> = args.get("macs")?;
            if macs.is_empty() || macs.len() > 4 {
                return Err("No network address is known for this computer yet".into());
            }
            out(tokio::task::spawn_blocking(move || macs.iter().try_for_each(|mac| host_actions::wake(mac)))
                .await
                .map_err(|_| "The wake signal could not be sent")??)
        }
        "remote_printing_status" => {
            let installed = tokio::task::spawn_blocking(host_print::installed).await.unwrap_or(false);
            out(json!({ "supported": true, "installed": installed }))
        }
        "remote_printing_set" => {
            let enabled: bool = args.get("enabled")?;
            out(tokio::task::spawn_blocking(move || {
                signin::run_elevated(if enabled { "--printer-install" } else { "--printer-remove" })
            })
            .await
            .map_err(|_| "The printer could not be changed")??)
        }
        "print_job" => {
            let path: String = args.get("path")?;
            let open: bool = args.get("open")?;
            out(tokio::task::spawn_blocking(move || {
                if open {
                    host_print::open(&path).map(|()| "opened".to_owned())
                } else {
                    host_print::print(&path).map(str::to_owned)
                }
            })
            .await
            .map_err(|_| "The print job could not be handled")??)
        }
        "signin_screen_status" => {
            let status = signin::public_status();
            let outdated = status.is_some() && tokio::task::spawn_blocking(signin::program_outdated).await.unwrap_or(false);
            out(json!({
                "supported": mixel_engine::app_scope::program().is_ok(),
                "outdated": outdated,
                "technician": signin::technician_connected(),
                "connection_id": status.as_ref().map(|status| status.connection_id),
                "name": status.map(|status| status.name),
                "wayland_greeter": false,
            }))
        }
        "signin_screen_enable" => signin_screen_enable(context, args).await,
        "signin_screen_disable" => out(tokio::task::spawn_blocking(|| signin::run_elevated("--signin-remove"))
            .await
            .map_err(|_| "Administrator step failed")??),
        "signin_screen_update" => out(tokio::task::spawn_blocking(|| signin::run_elevated("--signin-update"))
            .await
            .map_err(|_| "Administrator step failed")??),
        "open_browser" => {
            let url: String = args.get("url")?;
            out(browser(&url).await?)
        }
        _ => Err(format!("Unknown command: {command}")),
    }
}

/// Sign-in screen and always-on access (Windows): a computer-wide key, a device record
/// "<name> (sign-in screen)" of the company, and the service. Two administrator prompts.
#[cfg(target_os = "windows")]
async fn signin_screen_enable(context: &Context, args: &Args) -> Result<String, String> {
    use mixel_engine::windows_signin_host as signin;
    let company: Uuid = args.get("company")?;
    let name: String = args.get("name")?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let credential = context.auth.credential(Some(&cancelled)).await?;
    if !credential.principal.staff {
        return Err("Sign-in screen access requires an MFA-verified Mixel team account".into());
    }
    let name = name.trim().to_owned();
    if name.is_empty() || name.chars().count() > 60 || name.chars().any(char::is_control) {
        return Err("Enter a computer name of up to 60 characters".into());
    }
    // Step 1 (administrator): the computer-wide key.
    tokio::task::spawn_blocking(|| signin::run_elevated("--signin-provision"))
        .await
        .map_err(|_| "Administrator step failed")??;
    let key = signin::provisioned_public_key()?;
    let ism = &credential.ism;
    let token = &credential.token;
    let device = match ism.enrollment_of_key(token, &key).await.map_err(|_| "The enrolment status is unavailable")? {
        Some(current) => current.device,
        None => {
            let device = ism
                .add_device(token, company, &format!("{name} (sign-in screen)"), "windows")
                .await
                .map_err(|_| "The sign-in screen record could not be added for this company")?;
            ism.enroll(token, device, key, &cancelled)
                .await
                .map_err(|_| "The sign-in screen could not be enrolled")?;
            device
        }
    };
    let enrolled = ism
        .authenticate_device(token, device)
        .await
        .map_err(|_| "The sign-in screen enrolment could not be confirmed")?;
    if enrolled.public_key != key {
        return Err("The sign-in screen enrolment does not match this computer".into());
    }
    let tenant = ism.device_tenant(token, device).await.map_err(|_| "The company of this computer is unavailable")?;
    let settings = signin::Settings {
        version: 1,
        device,
        profile: enrolled.principal.user,
        tenant,
        connection_id: enrolled.connection_id,
        generation: enrolled.generation,
        control: true,
    };
    let payload = serde_json::to_vec(&json!({ "settings": settings, "name": name }))
        .map_err(|_| "Cannot prepare the sign-in screen settings")?;
    let hex: String = payload.iter().map(|byte| format!("{byte:02x}")).collect();
    // Step 2 (administrator): save the settings and start the service.
    tokio::task::spawn_blocking(move || signin::run_elevated(&format!("--signin-install {hex}")))
        .await
        .map_err(|_| "Administrator step failed")??;
    out(enrolled.connection_id)
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
async fn platform(_context: &Context, command: &str, _args: &Args) -> Result<String, String> {
    match command {
        "capture_targets" => out(Vec::<Value>::new()),
        "screen_access" | "request_screen_access" | "request_input_access" => out(false),
        _ => Err(format!("Unknown command: {command}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context() -> Context {
        let folder = std::env::temp_dir().join(format!("mixel-ffi-test-{}", Uuid::new_v4()));
        Context::new(folder).unwrap()
    }

    #[test]
    fn arguments_accept_snake_and_camel_case() {
        let args = Args::parse(r#"{"company_id":"3b241101-e2bb-4255-8caf-4136c566a962","remoteId":7}"#).unwrap();
        let company: Uuid = args.get("company_id").unwrap();
        assert_eq!(company.to_string(), "3b241101-e2bb-4255-8caf-4136c566a962");
        assert_eq!(args.get::<u32>("remote_id").unwrap(), 7);
        assert_eq!(args.get::<Option<u32>>("missing").unwrap(), None);
        assert!(args.get::<u32>("missing").is_err());
        assert!(Args::parse("[1]").is_err());
        assert!(Args::parse("").is_ok());
        assert!(Args::parse(&"x".repeat((1 << 20) + 1)).is_err());
    }

    #[test]
    fn data_folder_must_be_absolute() {
        assert!(Context::new(PathBuf::from("relative/folder")).is_err());
    }

    #[tokio::test]
    async fn unknown_commands_and_bad_arguments_fail_with_a_message() {
        let context = context();
        let error = dispatch(&context, "no_such_command", "{}").await.unwrap_err();
        assert!(error.contains("Unknown command"));
        let error = dispatch(&context, "remote_request", "{}").await.unwrap_err();
        assert_eq!(error, "Missing or invalid argument: host");
        assert!(dispatch(&context, "remote_status", "not json").await.is_err());
    }

    #[tokio::test]
    async fn signed_out_engine_reports_offline_and_refuses_account_calls() {
        let context = context();
        let status: Value = serde_json::from_str(&dispatch(&context, "remote_status", "{}").await.unwrap()).unwrap();
        assert_eq!(status["phase"], "offline");
        #[cfg(target_os = "macos")]
        assert_eq!(status["platform"], "macos");
        let account: Value = serde_json::from_str(&dispatch(&context, "account_status", "{}").await.unwrap()).unwrap();
        assert!(account["phase"].is_string());
        assert!(dispatch(&context, "saas_my_tenants", "{}").await.is_err());
        assert!(dispatch(&context, "remote_devices", r#"{"search":""}"#).await.is_err());
        // Offline: no pending removal, and a removal needs confirmation first.
        assert_eq!(dispatch(&context, "device_removal_pending", "{}").await.unwrap(), "false");
        assert_eq!(
            dispatch(&context, "device_self_remove", r#"{"confirmed":false}"#).await.unwrap_err(),
            "Confirm that this computer should be removed"
        );
    }

    #[tokio::test]
    async fn file_and_recording_paths_must_be_absolute_and_sessions_real() {
        let context = context();
        let session = Uuid::new_v4();
        assert_eq!(
            dispatch(&context, "remote_record_may_start", &format!(r#"{{"session":"{session}"}}"#)).await.unwrap(),
            "false"
        );
        assert_eq!(
            dispatch(&context, "remote_record_start", &format!(r#"{{"session":"{session}","path":"/tmp/x.mp4"}}"#))
                .await
                .unwrap_err(),
            "Start a permitted recording after the remote screen appears"
        );
        assert!(dispatch(&context, "remote_file_send", &format!(r#"{{"consent":"{session}","path":"x"}}"#)).await.is_err());
    }

    #[tokio::test]
    async fn meeting_links_are_checked_and_legal_pages_fixed() {
        let context = context();
        assert!(dispatch(&context, "meeting_link", r#"{"link":"https://evil.example/meet/#abc"}"#).await.is_err());
        let url: String =
            serde_json::from_str(&dispatch(&context, "legal_url", r#"{"page":"privacy","lang":"fr"}"#).await.unwrap()).unwrap();
        assert_eq!(url, "https://remote.mixel.ch/legal/privacy?lang=fr");
        assert!(dispatch(&context, "legal_url", r#"{"page":"../x"}"#).await.is_err());
    }
}
