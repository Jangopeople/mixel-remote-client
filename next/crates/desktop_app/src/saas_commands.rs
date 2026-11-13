//! Remote SaaS commands for the desktop UI: creating a company, the signed-in person's company
//! (tenant), its members and invitations, devices, groups and deploy links, "add this computer",
//! and the offline-first self-removal of this computer signed with its own device key.
use crate::{account_auth, device_identity, main_window, remote_session};
use signaling_server::auth::saas::{
    SaasDeployLink, SaasDevice, SaasDevicePresence, SaasEnrollment, SaasError, SaasGroup, SaasInvite, SaasMember,
    SaasSignup, SaasTenant, SelfRemoval,
};
use std::{path::PathBuf, sync::Arc};
use tauri::Manager;
use uuid::Uuid;

type Auth<'a> = tauri::State<'a, account_auth::AccountAuth>;

fn message(error: SaasError) -> String {
    error.message
}

fn this_os() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else {
        "linux"
    }
}

#[tauri::command]
pub async fn saas_my_tenants(window: tauri::WebviewWindow, auth: Auth<'_>) -> Result<Vec<SaasTenant>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_my_tenants(&credential.token).await.map_err(message)
}

#[tauri::command]
pub async fn saas_tenant_overview(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<serde_json::Value, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_tenant_overview(&credential.token, tenant).await.map_err(message)
}

/// Self-service sign-up from the app (a Mixel ID without a company): creates the company with a
/// 14-day Business trial, this account its owner, and switches the app to it.
/// `terms`: the person ticked "I accept the Terms of Service and the Data Processing Agreement"
/// (the database refuses a sign-up without it and stores the accepted version and time).
#[tauri::command]
pub async fn saas_signup(window: tauri::WebviewWindow, auth: Auth<'_>, company: String, terms: Option<bool>) -> Result<SaasSignup, String> {
    main_window(&window)?;
    auth.create_company(company, terms == Some(true)).await
}

#[tauri::command]
pub async fn saas_terms_status(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<serde_json::Value, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_terms_status(&credential.token, tenant).await.map_err(message)
}

/// Owner: deletes the whole company at once (devices, invitations, members and their accounts,
/// company data); the subscription ends. `name` is the company name typed to confirm.
#[tauri::command]
pub async fn saas_delete_company(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, name: String) -> Result<serde_json::Value, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_delete_company(&credential.token, tenant, &name).await.map_err(message)
}

/// The signed-in person deletes their own account. `email` is their address typed to confirm.
#[tauri::command]
pub async fn saas_delete_my_account(window: tauri::WebviewWindow, auth: Auth<'_>, email: String) -> Result<serde_json::Value, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_delete_my_account(&credential.token, &email).await.map_err(message)
}

#[tauri::command]
pub async fn open_legal_page(window: tauri::WebviewWindow, page: String, lang: Option<String>) -> Result<(), String> {
    main_window(&window)?;
    crate::system_browser::open(&mixel_engine::legal::legal_url(&page, lang.as_deref())?).await
}

#[tauri::command]
pub async fn saas_members(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<Vec<SaasMember>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_members(&credential.token, tenant).await.map_err(message)
}

/// Owners/admins: invites a person. Mixel Remote e-mails the invitation link in `locale` (the app's
/// language; else the inviter's profile language). The link's secret exists only in that e-mail.
/// Returns the invitation id.
#[tauri::command]
pub async fn saas_invite_member(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    tenant: Uuid,
    email: String,
    role: String,
    locale: Option<String>,
) -> Result<Uuid, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_invite_member(&credential.token, tenant, &email, &role, locale.as_deref()).await.map_err(message)
}

/// Owners/admins: sends the invitation e-mail again with a new link (the previous link stops working).
#[tauri::command]
pub async fn saas_resend_invite(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    tenant: Uuid,
    invite: Uuid,
    locale: Option<String>,
) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_resend_invite(&credential.token, tenant, invite, locale.as_deref()).await.map_err(message)
}

#[tauri::command]
pub async fn saas_invites(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<Vec<SaasInvite>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_invites(&credential.token, tenant).await.map_err(message)
}

#[tauri::command]
pub async fn saas_revoke_invite(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, invite: Uuid) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_revoke_invite(&credential.token, tenant, invite).await.map_err(message)
}

#[tauri::command]
pub async fn saas_set_member_role(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    tenant: Uuid,
    member: Uuid,
    role: String,
) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_set_member_role(&credential.token, tenant, member, &role).await.map_err(message)
}

/// Removes a member; for the caller's own profile: leaves the company.
#[tauri::command]
pub async fn saas_remove_member(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, member: Uuid) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_remove_member(&credential.token, tenant, member).await.map_err(message)
}

#[tauri::command]
pub async fn saas_devices(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    tenant: Uuid,
    group: Option<Uuid>,
    search: Option<String>,
) -> Result<Vec<SaasDevice>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_devices(&credential.token, tenant, group, search.as_deref()).await.map_err(message)
}

#[tauri::command]
pub async fn saas_rename_device(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, device: Uuid, name: String) -> Result<String, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_rename_device(&credential.token, tenant, device, &name).await.map_err(message)
}

#[tauri::command]
pub async fn saas_move_device(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, device: Uuid, group: Option<Uuid>) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_move_device(&credential.token, tenant, device, group).await.map_err(message)
}

/// Removes a device of the company (revoke + delete); its running sessions end.
#[tauri::command]
pub async fn saas_remove_device(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, device: Uuid) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_remove_device(&credential.token, tenant, device).await.map_err(message)
}

/// "Add this computer": registers this computer's device key in the company (technicians and up).
#[tauri::command]
pub async fn saas_add_this_device(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    identity: tauri::State<'_, Arc<device_identity::IdentityState>>,
    remote: tauri::State<'_, remote_session::RemoteSession>,
    tenant: Uuid,
    name: String,
    group: Option<Uuid>,
) -> Result<SaasEnrollment, String> {
    main_window(&window)?;
    if !matches!(remote.status().await.phase.as_str(), "offline" | "error") {
        return Err("Go offline before adding this computer".into());
    }
    let credential = auth.credential(None).await?;
    let saved = identity.inner().clone();
    tauri::async_runtime::spawn_blocking(move || saved.open(true))
        .await
        .map_err(|_| "Device identity storage is unavailable")??;
    let enrolled = credential
        .ism
        .saas_add_this_device(&credential.token, tenant, &name, this_os(), &identity.public_key()?, group)
        .await
        .map_err(message)?;
    if auth.credential(None).await?.principal.user != credential.principal.user {
        return Err("The signed-in account changed".into());
    }
    Ok(enrolled)
}

#[tauri::command]
pub async fn saas_groups(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<Vec<SaasGroup>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_groups(&credential.token, tenant).await.map_err(message)
}

#[tauri::command]
pub async fn saas_create_group(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, name: String) -> Result<Uuid, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_create_group(&credential.token, tenant, &name).await.map_err(message)
}

#[tauri::command]
pub async fn saas_device_presence(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<Vec<SaasDevicePresence>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_device_presence(&credential.token, tenant).await.map_err(message)
}

#[tauri::command]
pub async fn saas_rename_tenant(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, name: String) -> Result<String, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_rename_tenant(&credential.token, tenant, &name).await.map_err(message)
}

#[tauri::command]
pub async fn saas_rename_group(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, group: Uuid, name: String) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_rename_group(&credential.token, tenant, group, &name).await.map_err(message)
}

#[tauri::command]
pub async fn saas_delete_group(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, group: Uuid) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_delete_group(&credential.token, tenant, group).await.map_err(message)
}

#[tauri::command]
pub async fn saas_set_group_access(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    tenant: Uuid,
    group: Uuid,
    technicians: Vec<Uuid>,
) -> Result<i32, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_set_group_access(&credential.token, tenant, group, &technicians).await.map_err(message)
}

/// A deploy link: the secret (shown once) and, per operating system, the one-line command that
/// installs Mixel Remote and enrols the computer with always-on access (run as administrator or by
/// a deployment tool).
#[derive(serde::Serialize)]
pub struct DeployLink {
    token: String,
    windows_command: String,
    macos_command: String,
    linux_command: String,
}

/// Owners/admins: a deploy link (rollout token) for mass rollout; the secret is shown once.
#[tauri::command]
pub async fn saas_create_deploy_link(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    tenant: Uuid,
    label: String,
    days: u16,
    computers: u16,
    group: Option<Uuid>,
) -> Result<DeployLink, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    let token = credential
        .ism
        .saas_create_deploy_link(&credential.token, tenant, &label, days, computers, group)
        .await
        .map_err(message)?;
    let windows_command = mixel_engine::rollout::windows_rollout_command(&token).await?;
    let (macos_command, linux_command) = mixel_engine::rollout::unix_rollout_commands(&token)?;
    Ok(DeployLink { token, windows_command, macos_command, linux_command })
}

#[tauri::command]
pub async fn saas_deploy_links(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid) -> Result<Vec<SaasDeployLink>, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_deploy_links(&credential.token, tenant).await.map_err(message)
}

#[tauri::command]
pub async fn saas_revoke_deploy_link(window: tauri::WebviewWindow, auth: Auth<'_>, tenant: Uuid, link: Uuid) -> Result<bool, String> {
    main_window(&window)?;
    let credential = auth.credential(None).await?;
    credential.ism.saas_revoke_deploy_link(&credential.token, tenant, link).await.map_err(message)
}

/// This computer's registration, when the UI already knows it (works without a sign-in).
#[derive(serde::Deserialize)]
pub struct RegistrationRef {
    device: Uuid,
    connection_id: u32,
    generation: u64,
}

use mixel_engine::self_removal::{SelfRemovalStatus, deliver, save_pending};

fn pending_path(window: &tauri::WebviewWindow) -> Result<PathBuf, String> {
    let directory = window
        .app_handle()
        .path()
        .app_local_data_dir()
        .map_err(|_| "The app data folder is unavailable")?;
    std::fs::create_dir_all(&directory).map_err(|_| "The app data folder is unavailable")?;
    Ok(directory.join("pending-self-removal.json"))
}

/// Whether a self-removal of this computer is saved and not yet delivered: this computer must
/// not go online again until it is (`remote_register` refuses meanwhile).
pub fn removal_pending(window: &tauri::WebviewWindow) -> bool {
    pending_path(window).is_ok_and(|path| path.exists())
}

/// Removes this computer from its company (or personal account): signs a removal request with this
/// computer's own device key, saves it, and sends it. Offline it stays queued and is sent by
/// `device_self_remove_flush` (call at start-up). Without `registration`, the signed-in account
/// looks up this computer's current registration.
#[tauri::command]
pub async fn device_self_remove(
    window: tauri::WebviewWindow,
    auth: Auth<'_>,
    identity: tauri::State<'_, Arc<device_identity::IdentityState>>,
    remote: tauri::State<'_, remote_session::RemoteSession>,
    registration: Option<RegistrationRef>,
    confirmed: bool,
) -> Result<SelfRemovalStatus, String> {
    main_window(&window)?;
    if !confirmed {
        return Err("Confirm that this computer should be removed".into());
    }
    if !matches!(remote.status().await.phase.as_str(), "offline" | "error") {
        return Err("Go offline before removing this computer".into());
    }
    let saved = identity.inner().clone();
    tauri::async_runtime::spawn_blocking(move || saved.open(false))
        .await
        .map_err(|_| "Device identity storage is unavailable")??;
    let registration = match registration {
        Some(known) => known,
        None => {
            let credential = auth.credential(None).await?;
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
    let request = SelfRemoval::new(
        registration.device,
        registration.connection_id,
        registration.generation,
        issued_at,
        |text| identity.sign_self_removal(text),
    )?;
    if !request.verify(&identity.public_key()?) {
        return Err("The removal request could not be signed".into());
    }
    let path = pending_path(&window)?;
    let json = serde_json::to_vec(&request).map_err(|_| "The removal request could not be saved")?;
    save_pending(&path, &json).map_err(|_| "The removal request could not be saved")?;
    deliver(&path, &request).await
}

/// Sends a queued self-removal, if any. `None`: nothing was queued.
#[tauri::command]
pub async fn device_self_remove_flush(window: tauri::WebviewWindow) -> Result<Option<SelfRemovalStatus>, String> {
    main_window(&window)?;
    let path = pending_path(&window)?;
    let Ok(raw) = std::fs::read(&path) else {
        return Ok(None);
    };
    let Ok(request) = serde_json::from_slice::<SelfRemoval>(&raw) else {
        let _ = std::fs::remove_file(&path);
        return Ok(None);
    };
    deliver(&path, &request).await.map(Some)
}
