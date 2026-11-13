//! Remote SaaS tenant RPCs (next/sql 018-029) for the apps: sign-up, tenants, members and
//! invitations, devices, groups, deploy links, device self-removal, favorite and recent devices,
//! session reports and session notes. Every call uses the
//! caller's own bearer (or the public key for the anonymous self-removal); the database checks
//! membership, role, plan limits and status.
use super::{AuthError, IsmAuth};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use uuid::Uuid;

/// A refused or failed call, with the database's message for the person (bounded, no control
/// characters), e.g. "Device limit of your plan reached".
#[derive(Debug, Clone, Serialize, thiserror::Error)]
#[error("{message}")]
pub struct SaasError {
    /// HTTP status of the API (0: no answer).
    pub status: u16,
    /// PostgreSQL error code, e.g. "42501" (not allowed), "54000" (plan limit), "25006" (read-only).
    pub code: Option<String>,
    pub message: String,
}
impl From<AuthError> for SaasError {
    fn from(_: AuthError) -> Self {
        Self::unavailable()
    }
}
impl SaasError {
    fn unavailable() -> Self {
        Self { status: 0, code: None, message: "Mixel Remote is unavailable. Please try again.".into() }
    }
    fn invalid(message: &str) -> Self {
        Self { status: 400, code: Some("22023".into()), message: message.into() }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasTenant {
    pub tenant_id: Uuid,
    pub organization_id: Uuid,
    pub name: String,
    pub slug: String,
    pub role: String,
    pub plan: String,
    pub status: String,
    pub trial_ends_at: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasDevice {
    pub device_id: Uuid,
    pub name: String,
    pub os: String,
    pub group_id: Option<Uuid>,
    pub group_name: Option<String>,
    pub connection_id: Option<u32>,
    pub generation: Option<u64>,
    pub enrolled_at: Option<String>,
    pub revoked: Option<bool>,
    pub added_via: Option<String>,
}
/// Online status of a tenant device as the connection broker last reported it (SQL 025):
/// `online`, `offline` (with `last_seen`), `never`, `unknown` (broker silent) or `not_enrolled`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasDevicePresence {
    pub device_id: Uuid,
    pub connection_id: Option<u32>,
    pub status: String,
    pub last_seen: Option<String>,
    pub online_since: Option<String>,
    pub checked_at: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasGroup {
    pub group_id: Uuid,
    pub name: String,
    pub devices: i32,
    pub technicians: Vec<Uuid>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasDeployLink {
    pub id: Uuid,
    pub label: String,
    pub group_id: Option<Uuid>,
    pub created_at: String,
    pub expires_at: String,
    pub max_uses: i32,
    pub uses: i32,
    pub revoked_at: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasMember {
    pub user_id: Uuid,
    pub name: String,
    pub email: String,
    pub role: String,
    pub created_at: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasInvite {
    pub id: Uuid,
    pub email: String,
    pub role: String,
    pub created_at: String,
    pub expires_at: String,
    /// `pending` / `sending` (the e-mail is on its way), `sent`, `failed` (not delivered to the mail service).
    #[serde(default)]
    pub mail_state: Option<String>,
    #[serde(default)]
    pub mail_sent_at: Option<String>,
    #[serde(default)]
    pub mail_locale: Option<String>,
    #[serde(default)]
    pub mail_sends: Option<i32>,
}
/// A favorite device of the signed-in person (SQL 029; only devices they may still reach).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasFavorite {
    pub device_id: Uuid,
    pub created_at: String,
}
/// A device of the signed-in person's own recent sessions, newest first (SQL 029).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasRecentDevice {
    pub device_id: Uuid,
    pub last_connected_at: String,
}
/// One session of the session report (SQL 029). `technician_*` is empty when that person's
/// account was deleted; `ended_at` is empty while the session runs (`duration_seconds` counts up
/// to now). `device_name` is the current name, or the name at the start once the device is gone.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasSessionRow {
    pub session_id: Uuid,
    pub technician_id: Option<Uuid>,
    pub technician_name: Option<String>,
    pub technician_email: Option<String>,
    pub device_id: Uuid,
    pub device_name: String,
    /// The helped computer's Remote ID.
    pub connection_id: u32,
    pub started_at: String,
    pub ended_at: Option<String>,
    pub duration_seconds: i64,
    /// `attended` or `unattended`.
    pub kind: String,
    /// A colleague who joined another technician's running session.
    pub colleague: bool,
    pub note: Option<String>,
}
/// Longest session note (characters), as in the database.
pub const SESSION_NOTE_MAX: usize = 4000;
/// A session note as the database stores it: trimmed, CR LF as LF, at most 4000 characters, no
/// control characters other than newline and tab. Empty clears the note.
fn session_note(note: &str) -> Result<String, SaasError> {
    let note = note.replace("\r\n", "\n");
    let note = note.trim_matches([' ', '\n', '\t']);
    if note.chars().count() > SESSION_NOTE_MAX || note.chars().any(|c| c.is_control() && c != '\n' && c != '\t') {
        return Err(SaasError::invalid("Enter a note of at most 4000 characters"));
    }
    Ok(note.to_owned())
}
/// A report period bound as the app sends it (ISO 8601 date or date and time).
fn report_time(value: Option<String>) -> Result<Option<String>, SaasError> {
    let Some(value) = value.map(|text| text.trim().to_owned()).filter(|text| !text.is_empty()) else {
        return Ok(None);
    };
    if value.len() > 40
        || !value.as_bytes().first().is_some_and(u8::is_ascii_digit)
        || !value.bytes().all(|byte| byte.is_ascii_digit() || matches!(byte, b'-' | b':' | b'.' | b'+' | b'T' | b'Z' | b' '))
    {
        return Err(SaasError::invalid("Choose a valid period"));
    }
    Ok(Some(value))
}

/// Languages of the invitation e-mail (SQL 026).
pub const INVITE_LOCALES: [&str; 4] = ["en", "de", "fr", "it"];
fn invite_locale(locale: Option<&str>) -> Option<&'static str> {
    let wanted = locale?.trim().to_ascii_lowercase();
    let wanted = wanted.split(['-', '_']).next().unwrap_or_default().to_owned();
    INVITE_LOCALES.into_iter().find(|candidate| *candidate == wanted)
}
/// The result of a self-service sign-up: the new company (a 14-day Business trial).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasSignup {
    pub tenant_id: Uuid,
    pub organization_id: Uuid,
    pub slug: String,
    pub plan: String,
    pub status: String,
    pub trial_ends_at: Option<String>,
}
/// The registration of "add this computer" (same meaning as a rollout enrollment).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaasEnrollment {
    pub device_id: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub profile: Uuid,
    /// The tenant's ISM organization (the broker's tenant scope).
    pub tenant_org: Uuid,
}

/// A device's request to remove itself, signed with its enrolled Ed25519 key. It can be created
/// offline, kept, and sent later (the database accepts it for 30 days); it only removes the
/// enrollment generation it names.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SelfRemoval {
    pub device: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub issued_at: u64,
    #[serde(with = "signature_hex")]
    pub signature: [u8; 64],
}
impl SelfRemoval {
    /// The exact text the device signs (identical to remote_device_self_remove_message in SQL).
    pub fn message(device: Uuid, connection_id: u32, generation: u64, issued_at: u64) -> Vec<u8> {
        format!("mixel-remote/device-self-remove/v1\n{device}\n{connection_id}\n{generation}\n{issued_at}").into_bytes()
    }
    /// Builds the request; `sign` signs the message with the device key.
    pub fn new(
        device: Uuid,
        connection_id: u32,
        generation: u64,
        issued_at: u64,
        sign: impl FnOnce(&[u8]) -> Result<[u8; 64], String>,
    ) -> Result<Self, String> {
        if device.is_nil() || !(100_000_000..=999_999_999).contains(&connection_id) || generation == 0 {
            return Err("This computer has no Mixel Remote registration to remove".into());
        }
        let signature = sign(&Self::message(device, connection_id, generation, issued_at))?;
        Ok(Self { device, connection_id, generation, issued_at, signature })
    }
    /// Checks the signature against the device's public key.
    pub fn verify(&self, key: &ed25519_dalek::VerifyingKey) -> bool {
        key.verify_strict(
            &Self::message(self.device, self.connection_id, self.generation, self.issued_at),
            &ed25519_dalek::Signature::from_bytes(&self.signature),
        )
        .is_ok()
    }
}
mod signature_hex {
    use serde::{Deserialize, Deserializer, Serializer, de::Error};
    pub fn serialize<S: Serializer>(value: &[u8; 64], serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&value.iter().map(|byte| format!("{byte:02x}")).collect::<String>())
    }
    pub fn deserialize<'de, D: Deserializer<'de>>(deserializer: D) -> Result<[u8; 64], D::Error> {
        let text = String::deserialize(deserializer)?;
        if text.len() != 128 || !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(D::Error::custom("invalid signature"));
        }
        let mut bytes = [0; 64];
        for (index, byte) in bytes.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&text[index * 2..index * 2 + 2], 16).map_err(D::Error::custom)?;
        }
        Ok(bytes)
    }
}

fn bytea(bytes: &[u8]) -> String {
    format!("\\x{}", bytes.iter().map(|byte| format!("{byte:02x}")).collect::<String>())
}
fn clean_text(value: &str, max: usize) -> Result<&str, SaasError> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > max || value.chars().any(char::is_control) {
        return Err(SaasError::invalid("Enter a valid name"));
    }
    Ok(value)
}
fn member_role(role: &str, owner: bool) -> Result<&'static str, SaasError> {
    match role {
        "owner" if owner => Ok("owner"),
        "admin" => Ok("admin"),
        "technician" => Ok("technician"),
        "member" => Ok("member"),
        _ => Err(SaasError::invalid("Invalid role")),
    }
}
fn os_name(os: &str) -> Result<&'static str, SaasError> {
    match os {
        "macos" => Ok("macos"),
        "windows" => Ok("windows"),
        "linux" => Ok("linux"),
        _ => Err(SaasError::invalid("Unsupported operating system")),
    }
}

impl IsmAuth {
    /// One SaaS RPC. `token: None` calls anonymously with the public key.
    async fn saas_call<T: DeserializeOwned>(
        &self,
        token: Option<&str>,
        function: &'static str,
        body: Value,
    ) -> Result<T, SaasError> {
        self.saas_call_limited(token, function, body, 1 << 20).await
    }

    /// Like `saas_call`, with an answer of at most `max_bytes`.
    async fn saas_call_limited<T: DeserializeOwned>(
        &self,
        token: Option<&str>,
        function: &'static str,
        body: Value,
        max_bytes: usize,
    ) -> Result<T, SaasError> {
        debug_assert!(function.starts_with("remote_saas_") || function == "remote_device_self_remove");
        let url = self.base.join(&format!("/rest/v1/rpc/{function}")).map_err(|_| SaasError::unavailable())?;
        let response = self
            .client
            .post(url)
            .header("apikey", &self.api_key)
            .bearer_auth(token.unwrap_or(&self.api_key))
            .json(&body)
            .send()
            .await
            .map_err(|_| SaasError::unavailable())?;
        let status = response.status().as_u16();
        let mut raw = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| SaasError::unavailable())?;
            if raw.len() + chunk.len() > max_bytes {
                return Err(SaasError::unavailable());
            }
            raw.extend_from_slice(&chunk);
        }
        if (200..300).contains(&status) {
            return serde_json::from_slice(&raw).map_err(|_| SaasError::unavailable());
        }
        #[derive(Deserialize)]
        struct ApiError {
            code: Option<String>,
            message: Option<String>,
        }
        let parsed: Option<ApiError> = serde_json::from_slice(&raw).ok();
        let code = parsed.as_ref().and_then(|error| error.code.clone()).filter(|code| {
            code.len() <= 8 && code.bytes().all(|byte| byte.is_ascii_alphanumeric())
        });
        let message = parsed
            .and_then(|error| error.message)
            .map(|text| text.chars().filter(|c| !c.is_control()).take(300).collect::<String>())
            .filter(|text| !text.trim().is_empty())
            .unwrap_or_else(|| "The request was refused".into());
        Err(SaasError { status, code, message })
    }

    pub async fn saas_my_tenants(&self, token: &str) -> Result<Vec<SaasTenant>, SaasError> {
        self.saas_call(Some(token), "remote_saas_my_tenants", json!({})).await
    }
    pub async fn saas_tenant_overview(&self, token: &str, tenant: Uuid) -> Result<Value, SaasError> {
        self.saas_call(Some(token), "remote_saas_tenant_overview", json!({"tenant": tenant})).await
    }
    pub async fn saas_devices(
        &self,
        token: &str,
        tenant: Uuid,
        group: Option<Uuid>,
        search: Option<&str>,
    ) -> Result<Vec<SaasDevice>, SaasError> {
        let search = search.map(str::trim).filter(|text| !text.is_empty());
        if search.is_some_and(|text| text.chars().count() > 80) {
            return Err(SaasError::invalid("Search text too long"));
        }
        self.saas_call(Some(token), "remote_saas_devices", json!({"tenant": tenant, "in_group": group, "search": search}))
            .await
    }
    pub async fn saas_device_presence(&self, token: &str, tenant: Uuid) -> Result<Vec<SaasDevicePresence>, SaasError> {
        self.saas_call(Some(token), "remote_saas_device_presence", json!({"tenant": tenant})).await
    }
    /// Owners and admins: rename the company (the slug stays).
    pub async fn saas_rename_tenant(&self, token: &str, tenant: Uuid, name: &str) -> Result<String, SaasError> {
        let name = name.trim();
        if !(2..=80).contains(&name.chars().count()) || name.chars().any(char::is_control) {
            return Err(SaasError::invalid("Enter a company name of 2 to 80 characters"));
        }
        self.saas_call(Some(token), "remote_saas_rename_tenant", json!({"tenant": tenant, "company_name": name})).await
    }
    pub async fn saas_rename_device(&self, token: &str, tenant: Uuid, device: Uuid, name: &str) -> Result<String, SaasError> {
        let name = clean_text(name, 60)?;
        self.saas_call(Some(token), "remote_saas_rename_device", json!({"tenant": tenant, "device": device, "device_name": name}))
            .await
    }
    pub async fn saas_move_device(&self, token: &str, tenant: Uuid, device: Uuid, group: Option<Uuid>) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_move_device", json!({"tenant": tenant, "device": device, "to_group": group}))
            .await
    }
    pub async fn saas_remove_device(&self, token: &str, tenant: Uuid, device: Uuid) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_remove_device", json!({"tenant": tenant, "device": device})).await
    }
    pub async fn saas_add_this_device(
        &self,
        token: &str,
        tenant: Uuid,
        name: &str,
        os: &str,
        key: &ed25519_dalek::VerifyingKey,
        group: Option<Uuid>,
    ) -> Result<SaasEnrollment, SaasError> {
        let name = clean_text(name, 60)?;
        let os = os_name(os)?;
        if key.is_weak() {
            return Err(SaasError::invalid("Invalid device key"));
        }
        let rows: Vec<SaasEnrollment> = self
            .saas_call(
                Some(token),
                "remote_saas_add_this_device",
                json!({"tenant": tenant, "device_name": name, "device_os": os,
                       "key_material": bytea(key.as_bytes()), "in_group": group}),
            )
            .await?;
        match rows.as_slice() {
            [row] if !row.device_id.is_nil() && !row.tenant_org.is_nil() && row.generation >= 1
                && (100_000_000..=999_999_999).contains(&row.connection_id) => Ok(row.clone()),
            _ => Err(SaasError::unavailable()),
        }
    }
    pub async fn saas_groups(&self, token: &str, tenant: Uuid) -> Result<Vec<SaasGroup>, SaasError> {
        self.saas_call(Some(token), "remote_saas_groups", json!({"tenant": tenant})).await
    }
    pub async fn saas_create_group(&self, token: &str, tenant: Uuid, name: &str) -> Result<Uuid, SaasError> {
        let name = clean_text(name, 60)?;
        self.saas_call(Some(token), "remote_saas_create_group", json!({"tenant": tenant, "group_name": name})).await
    }
    pub async fn saas_rename_group(&self, token: &str, tenant: Uuid, group: Uuid, name: &str) -> Result<bool, SaasError> {
        let name = clean_text(name, 60)?;
        self.saas_call(Some(token), "remote_saas_rename_group", json!({"tenant": tenant, "target": group, "group_name": name}))
            .await
    }
    pub async fn saas_delete_group(&self, token: &str, tenant: Uuid, group: Uuid) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_delete_group", json!({"tenant": tenant, "target": group})).await
    }
    pub async fn saas_set_group_access(&self, token: &str, tenant: Uuid, group: Uuid, technicians: &[Uuid]) -> Result<i32, SaasError> {
        if technicians.len() > 500 {
            return Err(SaasError::invalid("Too many technicians"));
        }
        self.saas_call(
            Some(token),
            "remote_saas_set_group_access",
            json!({"tenant": tenant, "target": group, "technicians": technicians}),
        )
        .await
    }
    pub async fn saas_create_deploy_link(
        &self,
        token: &str,
        tenant: Uuid,
        label: &str,
        valid_days: u16,
        computers: u16,
        group: Option<Uuid>,
    ) -> Result<String, SaasError> {
        let label = clean_text(label, 80)?;
        if !(1..=365).contains(&valid_days) || !(1..=10000).contains(&computers) {
            return Err(SaasError::invalid("Invalid deploy link"));
        }
        let secret: String = self
            .saas_call(
                Some(token),
                "remote_saas_create_deploy_link",
                json!({"tenant": tenant, "link_label": label, "valid_days": valid_days, "computers": computers, "in_group": group}),
            )
            .await?;
        if !secret.starts_with("mrt_") || secret.len() > 100 {
            return Err(SaasError::unavailable());
        }
        Ok(secret)
    }
    pub async fn saas_deploy_links(&self, token: &str, tenant: Uuid) -> Result<Vec<SaasDeployLink>, SaasError> {
        self.saas_call(Some(token), "remote_saas_deploy_links", json!({"tenant": tenant})).await
    }
    pub async fn saas_revoke_deploy_link(&self, token: &str, tenant: Uuid, link: Uuid) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_revoke_deploy_link", json!({"tenant": tenant, "link": link})).await
    }
    /// Self-service sign-up: the signed-in account becomes the owner of a new company. The person
    /// must have accepted the Terms of Service and the Data Processing Agreement (SQL 027 stores the
    /// current version and the time; the app links to the current texts on remote.mixel.ch/legal).
    pub async fn saas_signup(&self, token: &str, company: &str, accepted_terms: bool) -> Result<SaasSignup, SaasError> {
        let company = company.trim();
        if !(2..=80).contains(&company.chars().count()) || company.chars().any(char::is_control) {
            return Err(SaasError::invalid("Enter a company name of 2 to 80 characters"));
        }
        if !accepted_terms {
            return Err(SaasError::invalid("Accept the Terms of Service and the Data Processing Agreement to create a company"));
        }
        let created: SaasSignup = self
            .saas_call(Some(token), "remote_saas_signup", json!({"company_name": company, "accept_terms": true, "source": "app"}))
            .await?;
        if created.tenant_id.is_nil() || created.organization_id.is_nil() {
            return Err(SaasError::unavailable());
        }
        Ok(created)
    }
    pub async fn saas_members(&self, token: &str, tenant: Uuid) -> Result<Vec<SaasMember>, SaasError> {
        self.saas_call(Some(token), "remote_saas_members", json!({"tenant": tenant})).await
    }
    /// Owners/admins: invites a person. The mail service e-mails the invitation link (SQL 026); the
    /// secret is never returned to the inviter. Returns the invitation id.
    pub async fn saas_invite_member(&self, token: &str, tenant: Uuid, email: &str, role: &str, locale: Option<&str>) -> Result<Uuid, SaasError> {
        let email = email.trim();
        if !(3..=254).contains(&email.chars().count())
            || email.chars().any(|c| c.is_control() || c.is_whitespace())
            || email.matches('@').count() != 1
        {
            return Err(SaasError::invalid("Invalid email address"));
        }
        let role = member_role(role, false)?;
        self.saas_call(
            Some(token),
            "remote_saas_invite_member",
            json!({"tenant": tenant, "invitee_email": email, "member_role": role, "locale": invite_locale(locale)}),
        )
        .await
    }
    /// Owners/admins: sends the invitation e-mail again with a new link (the old one stops working).
    pub async fn saas_resend_invite(&self, token: &str, tenant: Uuid, invite: Uuid, locale: Option<&str>) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_resend_invite", json!({"tenant": tenant, "invite": invite, "locale": invite_locale(locale)}))
            .await
    }
    pub async fn saas_invites(&self, token: &str, tenant: Uuid) -> Result<Vec<SaasInvite>, SaasError> {
        self.saas_call(Some(token), "remote_saas_invites", json!({"tenant": tenant})).await
    }
    pub async fn saas_revoke_invite(&self, token: &str, tenant: Uuid, invite: Uuid) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_revoke_invite", json!({"tenant": tenant, "invite": invite})).await
    }
    pub async fn saas_set_member_role(&self, token: &str, tenant: Uuid, member: Uuid, role: &str) -> Result<bool, SaasError> {
        let role = member_role(role, true)?;
        self.saas_call(Some(token), "remote_saas_set_member_role", json!({"tenant": tenant, "member": member, "member_role": role}))
            .await
    }
    /// Removes a member (or leaves, for the caller's own profile).
    pub async fn saas_remove_member(&self, token: &str, tenant: Uuid, member: Uuid) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_remove_member", json!({"tenant": tenant, "member": member})).await
    }
    /// Any member: the current versions of the legal documents and what the company accepted.
    pub async fn saas_terms_status(&self, token: &str, tenant: Uuid) -> Result<Value, SaasError> {
        self.saas_call(Some(token), "remote_saas_terms_status", json!({"tenant": tenant})).await
    }
    /// Owner: everything about the company (company, members, devices, groups, audit log) as JSON.
    pub async fn saas_export(&self, token: &str, tenant: Uuid) -> Result<Value, SaasError> {
        self.saas_call(Some(token), "remote_saas_export", json!({"tenant": tenant})).await
    }
    /// Owner: deletes the company at once: devices revoked and removed, invitations revoked,
    /// members and their accounts deleted; the billing service ends the Stripe subscription.
    /// `confirm_name` must be the company's name (as typed by the owner).
    pub async fn saas_delete_company(&self, token: &str, tenant: Uuid, confirm_name: &str) -> Result<Value, SaasError> {
        let confirm = confirm_name.trim();
        if confirm.is_empty() || confirm.chars().count() > 80 {
            return Err(SaasError::invalid("Type the company name exactly to confirm"));
        }
        self.saas_call(Some(token), "remote_saas_delete_company", json!({"tenant": tenant, "confirm_name": confirm})).await
    }
    /// The signed-in person deletes their own account (not the last owner of a company).
    pub async fn saas_delete_my_account(&self, token: &str, confirm_email: &str) -> Result<Value, SaasError> {
        let confirm = confirm_email.trim();
        if confirm.is_empty() || confirm.chars().count() > 254 {
            return Err(SaasError::invalid("Type your email address exactly to confirm"));
        }
        self.saas_call(Some(token), "remote_saas_delete_my_account", json!({"confirm_email": confirm})).await
    }
    /// Any member: marks a device of the company as a favorite (or not). Returns the new state.
    pub async fn saas_set_favorite(&self, token: &str, tenant: Uuid, device: Uuid, favorite: bool) -> Result<bool, SaasError> {
        self.saas_call(Some(token), "remote_saas_set_favorite", json!({"tenant": tenant, "device": device, "favorite": favorite}))
            .await
    }
    /// The signed-in person's favorite devices (only those they may still reach), newest first.
    pub async fn saas_favorites(&self, token: &str, tenant: Uuid) -> Result<Vec<SaasFavorite>, SaasError> {
        self.saas_call(Some(token), "remote_saas_favorites", json!({"tenant": tenant})).await
    }
    /// The devices of the signed-in person's own recent sessions, newest first (`max_count` 1 to
    /// 50, default 10).
    pub async fn saas_recent_devices(&self, token: &str, tenant: Uuid, max_count: Option<u32>) -> Result<Vec<SaasRecentDevice>, SaasError> {
        if max_count.is_some_and(|count| !(1..=50).contains(&count)) {
            return Err(SaasError::invalid("Choose 1 to 50 devices"));
        }
        self.saas_call(Some(token), "remote_saas_recent_devices", json!({"tenant": tenant, "max_count": max_count.unwrap_or(10)}))
            .await
    }
    /// Session report: sessions started in [`from`, `to`) (ISO 8601; default the last 30 days, at
    /// most 366 days), newest first, at most 5000. Owners/admins see every session of the company
    /// (optionally of one `technician`); technicians and members their own. Needs a paid plan.
    pub async fn saas_session_report(
        &self,
        token: &str,
        tenant: Uuid,
        from: Option<String>,
        to: Option<String>,
        technician: Option<Uuid>,
        device: Option<Uuid>,
    ) -> Result<Vec<SaasSessionRow>, SaasError> {
        let (from, to) = (report_time(from)?, report_time(to)?);
        self.saas_call_limited(
            Some(token),
            "remote_saas_session_report",
            json!({"tenant": tenant, "from_at": from, "to_at": to, "technician": technician, "device": device}),
            32 << 20,
        )
        .await
    }
    /// Note on a session: by its id, or by the helped computer's Remote ID (`connection`: the
    /// caller's newest session to it in the last 24 hours). Exactly one of the two. An empty note
    /// clears it. Returns the session id.
    pub async fn saas_session_note(
        &self,
        token: &str,
        tenant: Uuid,
        note: &str,
        session: Option<Uuid>,
        connection: Option<u32>,
    ) -> Result<Uuid, SaasError> {
        let note = session_note(note)?;
        if session.is_some() == connection.is_some()
            || connection.is_some_and(|id| !(100_000_000..=999_999_999).contains(&id))
        {
            return Err(SaasError::invalid("Choose one session"));
        }
        self.saas_call(
            Some(token),
            "remote_saas_session_note",
            json!({"tenant": tenant, "note": note, "session": session, "connection": connection}),
        )
        .await
    }
    /// Sends a device's signed self-removal (no account needed). `Ok(true)`: removed (or already
    /// removed); `Ok(false)`: the request names an older or newer registration and changed nothing.
    pub async fn device_self_remove(&self, request: &SelfRemoval) -> Result<bool, SaasError> {
        self.saas_call(
            None,
            "remote_device_self_remove",
            json!({"device": request.device, "connection": request.connection_id, "key_generation": request.generation,
                   "issued_at": request.issued_at, "signature": bytea(&request.signature)}),
        )
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    #[test]
    fn self_removal_signs_the_exact_database_text_and_round_trips() {
        let device = Uuid::from_u128(0x1234);
        assert_eq!(
            SelfRemoval::message(device, 123_456_789, 4, 1_763_000_000),
            b"mixel-remote/device-self-remove/v1\n00000000-0000-0000-0000-000000001234\n123456789\n4\n1763000000".to_vec()
        );
        let key = SigningKey::from_bytes(&[5; 32]);
        let request = SelfRemoval::new(device, 123_456_789, 4, 1_763_000_000, |message| Ok(key.sign(message).to_bytes())).unwrap();
        assert!(request.verify(&key.verifying_key()));
        assert!(!request.verify(&SigningKey::from_bytes(&[6; 32]).verifying_key()));
        let mut changed = request.clone();
        changed.generation = 5;
        assert!(!changed.verify(&key.verifying_key()));
        let text = serde_json::to_string(&request).unwrap();
        assert_eq!(serde_json::from_str::<SelfRemoval>(&text).unwrap(), request);
        assert!(serde_json::from_str::<SelfRemoval>(&text.replace("\"signature\":\"", "\"signature\":\"zz")).is_err());
        assert!(SelfRemoval::new(Uuid::nil(), 123_456_789, 4, 0, |_| Ok([0; 64])).is_err());
        assert!(SelfRemoval::new(device, 12, 4, 0, |_| Ok([0; 64])).is_err());
        assert!(SelfRemoval::new(device, 123_456_789, 0, 0, |_| Ok([0; 64])).is_err());
    }

    async fn api(
        axum::extract::Path(function): axum::extract::Path<String>,
        headers: axum::http::HeaderMap,
        axum::Json(body): axum::Json<Value>,
    ) -> axum::response::Response {
        use axum::{http::StatusCode, response::IntoResponse};
        let bearer = headers["authorization"].to_str().unwrap().to_owned();
        match function.as_str() {
            "remote_saas_add_this_device" => {
                assert_eq!(bearer, "Bearer user-token");
                assert_eq!(body["device_os"], "linux");
                assert!(body["key_material"].as_str().unwrap().starts_with("\\x"));
                if body["device_name"] == "Full" {
                    return (StatusCode::BAD_REQUEST, axum::Json(json!({"code":"54000","message":"Device limit of your plan reached\u{7}","details":null}))).into_response();
                }
                axum::Json(json!([{"device_id": Uuid::from_u128(1), "connection_id": 123456789, "generation": 1,
                                   "profile": Uuid::from_u128(2), "tenant_org": Uuid::from_u128(3)}])).into_response()
            }
            "remote_saas_invite_member" => {
                assert_eq!(body["member_role"], "technician");
                if body["invitee_email"] == "full@example.com" {
                    return (StatusCode::BAD_REQUEST, axum::Json(json!({"code":"54000","message":"Technician limit of your plan reached"}))).into_response();
                }
                // The language of the e-mail: a supported one, else none (the database then uses the inviter's).
                let expected = if body["invitee_email"] == "tech@example.com" { json!("de") } else { Value::Null };
                assert_eq!(body["locale"], expected);
                axum::Json(json!(Uuid::from_u128(77))).into_response()
            }
            "remote_saas_resend_invite" => {
                assert_eq!(body["locale"], "fr");
                axum::Json(json!(true)).into_response()
            }
            "remote_saas_signup" => {
                assert_eq!(body["company_name"], "Acme AG");
                assert_eq!((body["accept_terms"].as_bool(), body["source"].as_str()), (Some(true), Some("app")));
                axum::Json(json!({"tenant_id": Uuid::from_u128(4), "organization_id": Uuid::from_u128(5), "client_id": Uuid::from_u128(6),
                                  "slug": "acme-ag", "plan": "business", "status": "trial", "trial_ends_at": "2026-11-27T10:00:00Z"})).into_response()
            }
            "remote_saas_delete_company" => {
                assert_eq!(bearer, "Bearer user-token");
                if body["confirm_name"] != "Acme AG" {
                    return (StatusCode::BAD_REQUEST, axum::Json(json!({"code":"22023","message":"Type the company name exactly to confirm"}))).into_response();
                }
                axum::Json(json!({"deleted": true, "devices_removed": 2, "members_removed": 3, "billing": "pending"})).into_response()
            }
            "remote_saas_delete_my_account" => {
                if body["confirm_email"] == "owner@example.com" {
                    return (StatusCode::BAD_REQUEST, axum::Json(json!({"code":"55000","message":"You are the only owner. Delete the company, or make another member owner first."}))).into_response();
                }
                axum::Json(json!({"deleted": true, "tenant_id": null, "invites_revoked": 0})).into_response()
            }
            "remote_saas_set_member_role" => {
                if body["member_role"] == "owner" {
                    return (StatusCode::FORBIDDEN, axum::Json(json!({"code":"42501","message":"Only an owner can change owners"}))).into_response();
                }
                axum::Json(json!(true)).into_response()
            }
            "remote_saas_set_favorite" => {
                assert_eq!(bearer, "Bearer user-token");
                axum::Json(body["favorite"].clone()).into_response()
            }
            "remote_saas_favorites" => {
                axum::Json(json!([{"device_id": Uuid::from_u128(5), "created_at": "2026-11-13T10:00:00+00:00"}])).into_response()
            }
            "remote_saas_recent_devices" => {
                assert!(body["max_count"] == 10 || body["max_count"] == 3, "{body}");
                axum::Json(json!([{"device_id": Uuid::from_u128(6), "last_connected_at": "2026-11-13T11:00:00+00:00"}])).into_response()
            }
            "remote_saas_session_report" => {
                assert_eq!((body["from_at"].as_str(), body["to_at"].clone()), (Some("2026-11-01"), Value::Null));
                assert_eq!(body["technician"], json!(Uuid::from_u128(7)));
                if body["device"] == json!(Uuid::from_u128(8)) {
                    return (StatusCode::FORBIDDEN, axum::Json(json!({"code":"42501","message":"Session reports need a paid plan"}))).into_response();
                }
                axum::Json(json!([{"session_id": Uuid::from_u128(1), "technician_id": null, "technician_name": null,
                    "technician_email": null, "device_id": Uuid::from_u128(6), "device_name": "Office PC",
                    "connection_id": 123456789, "started_at": "2026-11-13T09:00:00+00:00", "ended_at": null,
                    "duration_seconds": 120, "kind": "unattended", "colleague": true, "note": "Printer"}])).into_response()
            }
            "remote_saas_session_note" => {
                assert_eq!(body["note"], "Fixed\nprinter");
                axum::Json(json!(body["session"].as_str().map_or(Uuid::from_u128(2), |id| id.parse().unwrap()))).into_response()
            }
            "remote_device_self_remove" => {
                assert_eq!(bearer, "Bearer public");
                assert_eq!(body["signature"].as_str().unwrap().len(), 130);
                axum::Json(json!(true)).into_response()
            }
            _ => StatusCode::NOT_FOUND.into_response(),
        }
    }

    #[tokio::test]
    async fn calls_carry_the_right_bearer_and_surface_database_messages() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, axum::Router::new().route("/rest/v1/rpc/{function}", axum::routing::post(api)))
                .await
                .unwrap()
        });
        // The production constructor requires HTTPS; tests reach the loopback fixture directly.
        let ism = IsmAuth {
            client: reqwest::Client::new(),
            base: format!("http://{address}").parse().unwrap(),
            api_key: "public".into(),
        };
        let key = SigningKey::from_bytes(&[5; 32]);
        let enrolled = ism
            .saas_add_this_device("user-token", Uuid::from_u128(9), "Office PC", "linux", &key.verifying_key(), None)
            .await
            .unwrap();
        assert_eq!(enrolled.connection_id, 123_456_789);
        let refused = ism
            .saas_add_this_device("user-token", Uuid::from_u128(9), "Full", "linux", &key.verifying_key(), None)
            .await
            .unwrap_err();
        assert_eq!((refused.status, refused.code.as_deref(), refused.message.as_str()), (400, Some("54000"), "Device limit of your plan reached"));
        assert!(ism.saas_add_this_device("user-token", Uuid::from_u128(9), " ", "linux", &key.verifying_key(), None).await.is_err());
        assert!(ism.saas_add_this_device("user-token", Uuid::from_u128(9), "PC", "beos", &key.verifying_key(), None).await.is_err());
        let tenant = Uuid::from_u128(9);
        let invite = ism.saas_invite_member("user-token", tenant, " tech@example.com ", "technician", Some("de-CH")).await.unwrap();
        assert_eq!(invite, Uuid::from_u128(77));
        let full = ism.saas_invite_member("user-token", tenant, "full@example.com", "technician", Some("es")).await.unwrap_err();
        assert_eq!((full.code.as_deref(), full.message.as_str()), (Some("54000"), "Technician limit of your plan reached"));
        assert!(ism.saas_invite_member("user-token", tenant, "no-at-sign", "technician", None).await.is_err());
        assert!(ism.saas_invite_member("user-token", tenant, "a@b.ch", "owner", None).await.is_err(), "owners are not invited");
        assert!(ism.saas_invite_member("user-token", tenant, "a@b.ch", "boss", None).await.is_err());
        assert!(ism.saas_resend_invite("user-token", tenant, invite, Some("FR")).await.unwrap());
        let created = ism.saas_signup("user-token", " Acme AG ", true).await.unwrap();
        assert_eq!((created.tenant_id, created.status.as_str()), (Uuid::from_u128(4), "trial"));
        assert!(ism.saas_signup("user-token", "A", true).await.is_err());
        let unaccepted = ism.saas_signup("user-token", "Acme AG", false).await.unwrap_err();
        assert!(unaccepted.message.contains("Terms of Service"), "no sign-up without the terms");
        let deleted = ism.saas_delete_company("user-token", tenant, " Acme AG ").await.unwrap();
        assert_eq!((deleted["deleted"].as_bool(), deleted["devices_removed"].as_i64()), (Some(true), Some(2)));
        let wrong = ism.saas_delete_company("user-token", tenant, "Acme").await.unwrap_err();
        assert_eq!(wrong.code.as_deref(), Some("22023"));
        assert!(ism.saas_delete_company("user-token", tenant, "  ").await.is_err());
        assert_eq!(ism.saas_delete_my_account("user-token", "tech@example.com").await.unwrap()["deleted"], true);
        let owner = ism.saas_delete_my_account("user-token", "owner@example.com").await.unwrap_err();
        assert_eq!((owner.code.as_deref(), owner.message.starts_with("You are the only owner")), (Some("55000"), true));
        assert!(ism.saas_set_member_role("user-token", tenant, Uuid::from_u128(7), "admin").await.unwrap());
        let refused = ism.saas_set_member_role("user-token", tenant, Uuid::from_u128(7), "owner").await.unwrap_err();
        assert_eq!((refused.status, refused.code.as_deref()), (403, Some("42501")));
        assert!(ism.saas_set_member_role("user-token", tenant, Uuid::from_u128(7), "root").await.is_err());
        let request = SelfRemoval::new(Uuid::from_u128(1), 123_456_789, 1, 1, |message| Ok(key.sign(message).to_bytes())).unwrap();
        assert!(ism.device_self_remove(&request).await.unwrap());
        // Favorites, recent devices, session report and notes.
        assert!(ism.saas_set_favorite("user-token", tenant, Uuid::from_u128(5), true).await.unwrap());
        assert!(!ism.saas_set_favorite("user-token", tenant, Uuid::from_u128(5), false).await.unwrap());
        assert_eq!(ism.saas_favorites("user-token", tenant).await.unwrap()[0].device_id, Uuid::from_u128(5));
        assert_eq!(ism.saas_recent_devices("user-token", tenant, None).await.unwrap()[0].device_id, Uuid::from_u128(6));
        assert_eq!(ism.saas_recent_devices("user-token", tenant, Some(3)).await.unwrap().len(), 1);
        assert!(ism.saas_recent_devices("user-token", tenant, Some(0)).await.is_err());
        assert!(ism.saas_recent_devices("user-token", tenant, Some(51)).await.is_err());
        let rows = ism
            .saas_session_report("user-token", tenant, Some(" 2026-11-01 ".into()), Some(" ".into()), Some(Uuid::from_u128(7)), None)
            .await
            .unwrap();
        assert_eq!(
            (rows[0].connection_id, rows[0].ended_at.as_deref(), rows[0].duration_seconds, rows[0].colleague, rows[0].note.as_deref()),
            (123_456_789, None, 120, true, Some("Printer"))
        );
        let gated = ism
            .saas_session_report("user-token", tenant, Some("2026-11-01".into()), None, Some(Uuid::from_u128(7)), Some(Uuid::from_u128(8)))
            .await
            .unwrap_err();
        assert_eq!((gated.status, gated.code.as_deref(), gated.message.as_str()), (403, Some("42501"), "Session reports need a paid plan"));
        assert!(ism.saas_session_report("user-token", tenant, Some("now; drop".into()), None, None, None).await.is_err());
        assert!(ism.saas_session_report("user-token", tenant, None, Some("x".repeat(41)), None, None).await.is_err());
        let session = Uuid::from_u128(3);
        assert_eq!(ism.saas_session_note("user-token", tenant, " Fixed\r\nprinter\n", Some(session), None).await.unwrap(), session);
        assert_eq!(ism.saas_session_note("user-token", tenant, "Fixed\nprinter", None, Some(123_456_789)).await.unwrap(), Uuid::from_u128(2));
        assert!(ism.saas_session_note("user-token", tenant, "x", None, None).await.is_err(), "one of session or connection");
        assert!(ism.saas_session_note("user-token", tenant, "x", Some(session), Some(123_456_789)).await.is_err());
        assert!(ism.saas_session_note("user-token", tenant, "x", None, Some(12)).await.is_err());
        assert!(ism.saas_session_note("user-token", tenant, &"x".repeat(4001), Some(session), None).await.is_err());
        assert!(ism.saas_session_note("user-token", tenant, "a\u{1}b", Some(session), None).await.is_err());
        server.abort();
    }
}
