//! ISM identity adapter: validate the bearer with GoTrue, then the active ISM
//! profile and organization using that same bearer (never a service-role key).
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use futures_util::StreamExt;
use reqwest::{Client, Url};
use serde::{Deserialize, Serialize};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use uuid::Uuid;

#[derive(Clone, Debug)]
pub struct Principal {
    pub user: Uuid,
    pub tenant: Option<Uuid>,
    pub staff: bool,
    pub expires_at: u64,
}
impl Principal {
    pub fn is_personal(&self) -> bool {
        !self.staff && self.tenant == Some(self.user)
    }

    pub fn can_request(&self, target: &Self) -> bool {
        self.staff || (self.tenant.is_some() && self.tenant == target.tenant)
    }
}
#[derive(Clone)]
pub struct EnrolledPrincipal {
    pub principal: Principal,
    pub device: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub public_key: ed25519_dalek::VerifyingKey,
    pub identity_ticket: Option<core_protocol::delegation::DeviceTicket>,
}
#[derive(Deserialize)]
pub struct PersonalAccount {
    pub profile_id: Option<Uuid>,
    pub display_name: Option<String>,
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PersonalDeviceOs {
    Macos,
    Windows,
    Linux,
}

impl PersonalDeviceOs {
    fn as_str(self) -> &'static str {
        match self {
            Self::Macos => "macos",
            Self::Windows => "windows",
            Self::Linux => "linux",
        }
    }
}

#[derive(Deserialize)]
struct PersonalDevice {
    #[serde(flatten)]
    enrollment: EnrollmentRow,
    name: String,
    os: String,
}

#[derive(Deserialize)]
struct EnrollmentRow {
    device_id: Uuid,
    connection_id: u32,
    generation: u64,
    public_key: String,
    revoked_at: Option<String>,
}
#[derive(Deserialize)]
struct DirectoryRow {
    device_id: Uuid,
    connection_id: u32,
    generation: u64,
    devices: DirectoryDevice,
}
#[derive(Deserialize)]
struct DirectoryDevice {
    id: Uuid,
    name: String,
    os: String,
}
#[derive(Serialize)]
pub struct InventoryPage {
    pub devices: Vec<InventoryDevice>,
    pub next: Option<Uuid>,
}
#[derive(Serialize, Deserialize)]
pub struct InventoryDevice {
    pub id: Uuid,
    pub name: String,
    pub os: String,
}
#[derive(Serialize, Deserialize)]
pub struct EnrollmentCompany {
    pub client_id: Uuid,
    pub client_name: String,
}
#[derive(Serialize, Deserialize)]
pub struct RolloutToken {
    pub id: Uuid,
    pub label: String,
    pub created_at: String,
    pub expires_at: String,
    pub max_uses: i32,
    pub uses: i32,
    pub revoked_at: Option<String>,
}
#[derive(Deserialize)]
pub struct RolloutEnrollment {
    pub device_id: Uuid,
    pub connection_id: u32,
    pub generation: u64,
    pub profile: Uuid,
    pub tenant: Uuid,
}
#[derive(Serialize)]
pub struct CurrentEnrollment {
    pub device: Uuid,
    pub connection_id: u32,
    pub name: String,
}
#[derive(Serialize)]
pub struct RegisteredDevice {
    pub device: Uuid,
    pub connection_id: u32,
    pub name: String,
    pub os: String,
}
#[derive(Serialize)]
pub struct DevicePage {
    pub devices: Vec<RegisteredDevice>,
    pub next: Option<u32>,
}
#[derive(Clone)]
pub struct IsmAuth {
    client: Client,
    base: Url,
    api_key: String,
}
#[derive(Debug, thiserror::Error)]
#[error("identity unavailable or unauthorized")]
pub struct AuthError;
#[derive(Deserialize)]
struct Claims {
    sub: Uuid,
    role: String,
    exp: u64,
    aal: Option<String>,
}
#[derive(Deserialize)]
struct User {
    id: Uuid,
}
#[derive(Deserialize)]
struct Profile {
    id: Uuid,
    auth_user_id: Uuid,
    organization_id: Option<Uuid>,
    is_active: bool,
    role: String,
}
#[derive(Deserialize)]
struct Organization {
    id: Uuid,
    is_active: bool,
}
impl IsmAuth {
    /// Enrollment is an explicit MFA-staff operation on an existing ISM device.
    /// The database RPC is add-only/idempotent for the same key; this API never
    /// reactivates or rotates a conflicting enrollment.
    pub async fn enroll(
        &self,
        token: &str,
        device: Uuid,
        key: ed25519_dalek::VerifyingKey,
        cancelled: &std::sync::atomic::AtomicBool,
    ) -> Result<EnrolledPrincipal, AuthError> {
        if device.is_nil() || key.is_weak() || cancelled.load(std::sync::atomic::Ordering::Acquire)
        {
            return Err(AuthError);
        }
        if !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let hex: String = key
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        if cancelled.load(std::sync::atomic::Ordering::Acquire) {
            return Err(AuthError);
        }
        let response = self
            .client
            .post(
                self.base
                    .join("/rest/v1/rpc/remote_enroll_device")
                    .map_err(|_| AuthError)?,
            )
            .header("apikey", &self.api_key)
            .header("accept", "application/vnd.pgrst.object+json")
            .bearer_auth(token)
            .json(&serde_json::json!({"target":device,"key_material":format!("\\x{hex}")}))
            .send()
            .await
            .map_err(|_| AuthError)?;
        let written: EnrollmentRow = Self::decode_response(response).await?;
        if written.device_id != device
            || written.revoked_at.is_some()
            || written.public_key != format!("\\x{hex}")
        {
            return Err(AuthError);
        }
        let checked = self.authenticate_device(token, device).await?;
        if checked.public_key != key
            || checked.connection_id != written.connection_id
            || checked.generation != written.generation
        {
            return Err(AuthError);
        }
        Ok(checked)
    }
    /// Companies an MFA-verified team member may add a computer to. Only
    /// companies linked to a Mixel admin company are returned by the RPC.
    pub async fn enrollment_companies(
        &self,
        token: &str,
    ) -> Result<Vec<EnrollmentCompany>, AuthError> {
        if !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let companies: Vec<EnrollmentCompany> = self
            .get("/rest/v1/rpc/remote_enrollment_companies", token)
            .await?;
        if companies.len() > 200
            || companies.iter().any(|company| {
                company.client_id.is_nil()
                    || company.client_name.is_empty()
                    || company.client_name.len() > 1024
                    || company.client_name.chars().any(char::is_control)
            })
        {
            return Err(AuthError);
        }
        Ok(companies)
    }
    /// Adds (or reuses a not yet enrolled) device record for the computer
    /// being set up. Enrollment itself stays a separate, key-bound step.
    pub async fn add_device(
        &self,
        token: &str,
        client: Uuid,
        name: &str,
        os: &str,
    ) -> Result<Uuid, AuthError> {
        let name = name.trim();
        if client.is_nil()
            || name.is_empty()
            || name.chars().count() > 80
            || name.chars().any(char::is_control)
            || !matches!(os, "macos" | "windows" | "linux")
            || !self.authenticate_scope(token, false).await?.staff
        {
            return Err(AuthError);
        }
        let response = self
            .client
            .post(
                self.base
                    .join("/rest/v1/rpc/remote_add_device")
                    .map_err(|_| AuthError)?,
            )
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .json(&serde_json::json!({"target_client":client,"device_name":name,"device_os":os}))
            .send()
            .await
            .map_err(|_| AuthError)?;
        let device: Uuid = Self::decode_response(response).await?;
        if device.is_nil() {
            return Err(AuthError);
        }
        Ok(device)
    }
    /// Creates a company rollout token (team members only); returns the secret once.
    pub async fn create_rollout_token(
        &self,
        token: &str,
        client: Uuid,
        label: &str,
        valid_days: u16,
        computers: u16,
    ) -> Result<String, AuthError> {
        let label = label.trim();
        if client.is_nil()
            || label.is_empty()
            || label.chars().count() > 80
            || label.chars().any(char::is_control)
            || !(1..=365).contains(&valid_days)
            || !(1..=10000).contains(&computers)
            || !self.authenticate_scope(token, false).await?.staff
        {
            return Err(AuthError);
        }
        let response = self
            .client
            .post(self.base.join("/rest/v1/rpc/remote_create_rollout_token").map_err(|_| AuthError)?)
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .json(&serde_json::json!({"target_client": client, "token_label": label, "valid_days": valid_days, "computers": computers}))
            .send()
            .await
            .map_err(|_| AuthError)?;
        let secret: String = Self::decode_response(response).await?;
        if !secret.starts_with("mrt_") || secret.len() > 100 {
            return Err(AuthError);
        }
        Ok(secret)
    }
    /// A company's rollout tokens (no secrets).
    pub async fn rollout_tokens(&self, token: &str, client: Uuid) -> Result<Vec<RolloutToken>, AuthError> {
        if client.is_nil() || !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let response = self
            .client
            .post(self.base.join("/rest/v1/rpc/remote_rollout_tokens").map_err(|_| AuthError)?)
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .json(&serde_json::json!({"target_client": client}))
            .send()
            .await
            .map_err(|_| AuthError)?;
        let tokens: Vec<RolloutToken> = Self::decode_response(response).await?;
        if tokens.len() > 100 {
            return Err(AuthError);
        }
        Ok(tokens)
    }
    pub async fn revoke_rollout_token(&self, token: &str, id: Uuid) -> Result<bool, AuthError> {
        if id.is_nil() || !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let response = self
            .client
            .post(self.base.join("/rest/v1/rpc/remote_revoke_rollout_token").map_err(|_| AuthError)?)
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .json(&serde_json::json!({"token_id": id}))
            .send()
            .await
            .map_err(|_| AuthError)?;
        Self::decode_response(response).await
    }
    /// A computer enrols itself with a rollout token (no user sign-in).
    pub async fn enroll_with_rollout_token(
        &self,
        secret: &str,
        name: &str,
        os: &str,
        key: &ed25519_dalek::VerifyingKey,
    ) -> Result<RolloutEnrollment, AuthError> {
        let name = name.trim();
        if !secret.starts_with("mrt_")
            || secret.len() > 100
            || name.is_empty()
            || name.chars().count() > 60
            || name.chars().any(char::is_control)
            || !matches!(os, "macos" | "windows" | "linux")
        {
            return Err(AuthError);
        }
        let hex: String = key.as_bytes().iter().map(|byte| format!("{byte:02x}")).collect();
        let response = self
            .client
            .post(self.base.join("/rest/v1/rpc/remote_enroll_with_token").map_err(|_| AuthError)?)
            .header("apikey", &self.api_key)
            .header("accept", "application/vnd.pgrst.object+json")
            .bearer_auth(&self.api_key)
            .json(&serde_json::json!({"token": secret, "device_name": name, "device_os": os, "key_material": format!("\\x{hex}")}))
            .send()
            .await
            .map_err(|_| AuthError)?;
        let enrolled: RolloutEnrollment = Self::decode_response(response).await?;
        if enrolled.device_id.is_nil()
            || enrolled.profile.is_nil()
            || enrolled.tenant.is_nil()
            || !(100_000_000..=999_999_999).contains(&enrolled.connection_id)
            || enrolled.generation < 1
        {
            return Err(AuthError);
        }
        Ok(enrolled)
    }
    /// The ISM organization (tenant) of a device a team member may enrol.
    pub async fn device_tenant(&self, token: &str, device: Uuid) -> Result<Uuid, AuthError> {
        if device.is_nil() || !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let response = self
            .client
            .post(
                self.base
                    .join("/rest/v1/rpc/remote_device_tenant")
                    .map_err(|_| AuthError)?,
            )
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .json(&serde_json::json!({ "target": device }))
            .send()
            .await
            .map_err(|_| AuthError)?;
        let tenant: Uuid = Self::decode_response(response).await?;
        if tenant.is_nil() {
            return Err(AuthError);
        }
        Ok(tenant)
    }
    /// The active enrollment that holds this computer's key, if any.
    pub async fn enrollment_of_key(
        &self,
        token: &str,
        key: &ed25519_dalek::VerifyingKey,
    ) -> Result<Option<CurrentEnrollment>, AuthError> {
        #[derive(Deserialize)]
        struct Named {
            name: String,
        }
        #[derive(Deserialize)]
        struct Row {
            device_id: Uuid,
            connection_id: u32,
            devices: Named,
        }
        if !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let hex: String = key
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let rows: Vec<Row> = self
            .get(
                &format!("/rest/v1/remote_device_enrollments?public_key=eq.%5Cx{hex}&revoked_at=is.null&select=device_id,connection_id,devices(name)&limit=2"),
                token,
            )
            .await?;
        let [row] = rows.as_slice() else {
            return if rows.is_empty() { Ok(None) } else { Err(AuthError) };
        };
        if !(100_000_000..=999_999_999).contains(&row.connection_id)
            || row.devices.name.is_empty()
            || row.devices.name.len() > 1024
            || row.devices.name.chars().any(char::is_control)
        {
            return Err(AuthError);
        }
        Ok(Some(CurrentEnrollment {
            device: row.device_id,
            connection_id: row.connection_id,
            name: row.devices.name.clone(),
        }))
    }
    pub async fn enrollment_devices(
        &self,
        token: &str,
        after: Option<Uuid>,
        search: &str,
    ) -> Result<InventoryPage, AuthError> {
        if after.is_some_and(|id| id.is_nil())
            || search.len() > 160
            || search.chars().any(char::is_control)
        {
            return Err(AuthError);
        }
        if !self.authenticate_scope(token, false).await?.staff {
            return Err(AuthError);
        }
        let mut path = self.base.join("/rest/v1/devices").map_err(|_| AuthError)?;
        {
            let mut query = path.query_pairs_mut();
            query
                .append_pair("select", "id,name,os")
                .append_pair("order", "id.asc")
                .append_pair("limit", "51");
            if let Some(after) = after {
                query.append_pair("id", &format!("gt.{after}"));
            }
            if !search.is_empty() {
                let literal = search
                    .replace('\\', "\\\\")
                    .replace('%', "\\%")
                    .replace('_', "\\_")
                    .replace('*', "\\*");
                query.append_pair("name", &format!("ilike.*{literal}*"));
            }
        }
        let mut devices: Vec<InventoryDevice> = self.get(path.as_str(), token).await?;
        if devices.len() > 51 {
            return Err(AuthError);
        }
        let mut previous = after.unwrap_or(Uuid::nil());
        for device in &devices {
            if device.id <= previous
                || device.name.is_empty()
                || device.name.len() > 1024
                || device.name.chars().any(char::is_control)
                || device.os.is_empty()
                || device.os.len() > 80
                || device.os.chars().any(char::is_control)
            {
                return Err(AuthError);
            }
            previous = device.id;
        }
        let next = if devices.len() == 51 {
            devices.truncate(50);
            devices.last().map(|device| device.id)
        } else {
            None
        };
        Ok(InventoryPage { devices, next })
    }
    pub async fn personal_account(
        &self,
        token: &str,
    ) -> Result<Option<PersonalAccount>, AuthError> {
        let response = self
            .client
            .get(
                self.base
                    .join("/rest/v1/rpc/remote_personal_account")
                    .map_err(|_| AuthError)?,
            )
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .send()
            .await
            .map_err(|_| AuthError)?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            // The account migration can roll out after existing clients.
            return Ok(None);
        }
        let mut rows: Vec<PersonalAccount> = Self::decode_response(response).await?;
        if rows.len() > 1
            || rows
                .iter()
                .any(|row| match (&row.profile_id, &row.display_name) {
                    (None, None) => false,
                    (Some(id), Some(name)) => id.is_nil() || !valid_personal_name(name),
                    _ => true,
                })
        {
            return Err(AuthError);
        }
        Ok(rows.pop())
    }

    async fn personal_devices(&self, token: &str) -> Result<Vec<PersonalDevice>, AuthError> {
        let rows: Vec<PersonalDevice> = self
            .get("/rest/v1/rpc/remote_personal_devices", token)
            .await?;
        let mut previous = 0;
        for row in &rows {
            if row.enrollment.connection_id <= previous
                || !valid_personal_name(&row.name)
                || !matches!(row.os.as_str(), "macos" | "linux" | "windows")
            {
                return Err(AuthError);
            }
            row.enrollment.key()?;
            previous = row.enrollment.connection_id;
        }
        if rows.len() > 32 {
            return Err(AuthError);
        }
        Ok(rows)
    }

    pub async fn register_personal_device(
        &self,
        token: &str,
        name: &str,
        os: PersonalDeviceOs,
        key: &ed25519_dalek::VerifyingKey,
    ) -> Result<u32, AuthError> {
        let name = name.trim();
        if !valid_personal_name(name)
            || key.is_weak()
            || !self.authenticate(token).await?.is_personal()
        {
            return Err(AuthError);
        }
        let hex: String = key
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let response = self.client.post(self.base.join("/rest/v1/rpc/remote_register_personal_device").map_err(|_| AuthError)?)
            .header("apikey", &self.api_key).bearer_auth(token)
            .json(&serde_json::json!({"device_name": name, "device_os": os, "key_material": format!("\\x{hex}")}))
            .send().await.map_err(|_| AuthError)?;
        let id: u32 = Self::decode_response(response).await?;
        let devices = self.personal_devices(token).await?;
        if !devices.iter().any(|row| {
            row.enrollment.connection_id == id
                && row.os == os.as_str()
                && row
                    .enrollment
                    .key()
                    .as_ref()
                    .is_ok_and(|saved| saved == key)
        }) {
            return Err(AuthError);
        }
        Ok(id)
    }

    /// Current authenticated RLS directory. Inventory is not a claim of broker
    /// presence and never replaces independent key pinning at connection time.
    pub async fn directory(
        &self,
        token: &str,
        after: Option<u32>,
        search: &str,
    ) -> Result<DevicePage, AuthError> {
        let personal = self.authenticate(token).await?.is_personal();
        self.directory_scoped(token, personal, after, search).await
    }
    /// Device directory of one workspace: the personal space, or the
    /// company scope of the same Mixel ID.
    pub async fn directory_scoped(
        &self,
        token: &str,
        personal: bool,
        after: Option<u32>,
        search: &str,
    ) -> Result<DevicePage, AuthError> {
        if after.is_some_and(|value| !(100_000_000..=999_999_999).contains(&value))
            || search.len() > 160
            || search.chars().any(char::is_control)
        {
            return Err(AuthError);
        }
        if personal {
            let query = search.to_lowercase();
            let devices = self
                .personal_devices(token)
                .await?
                .into_iter()
                .filter(|row| {
                    row.enrollment.connection_id > after.unwrap_or(0)
                        && row.name.to_lowercase().contains(&query)
                })
                .map(|row| RegisteredDevice {
                    device: row.enrollment.device_id,
                    connection_id: row.enrollment.connection_id,
                    name: row.name,
                    os: row.os,
                })
                .collect();
            return Ok(DevicePage {
                devices,
                next: None,
            });
        }
        let mut path = self
            .base
            .join("/rest/v1/remote_device_enrollments")
            .map_err(|_| AuthError)?;
        {
            let mut query = path.query_pairs_mut();
            query
                .append_pair(
                    "select",
                    "device_id,connection_id,generation,devices!inner(id,name,os)",
                )
                .append_pair("revoked_at", "is.null")
                .append_pair("order", "connection_id.asc")
                .append_pair("limit", "51");
            if let Some(after) = after {
                query.append_pair("connection_id", &format!("gt.{after}"));
            }
            if !search.is_empty() {
                let literal = search
                    .replace('\\', "\\\\")
                    .replace('%', "\\%")
                    .replace('_', "\\_")
                    .replace('*', "\\*");
                query.append_pair("devices.name", &format!("ilike.*{literal}*"));
            }
        }
        let mut rows: Vec<DirectoryRow> = self.get(path.as_str(), token).await?;
        if rows.len() > 51 {
            return Err(AuthError);
        }
        let mut previous = after.unwrap_or(0);
        for row in &rows {
            if row.device_id.is_nil()
                || row.device_id != row.devices.id
                || row.generation == 0
                || !(100_000_000..=999_999_999).contains(&row.connection_id)
                || row.connection_id <= previous
                || row.devices.name.is_empty()
                || row.devices.name.len() > 1024
                || row.devices.name.chars().any(char::is_control)
                || row.devices.os.is_empty()
                || row.devices.os.len() > 80
                || row.devices.os.chars().any(char::is_control)
            {
                return Err(AuthError);
            }
            previous = row.connection_id;
        }
        let next = if rows.len() == 51 {
            rows.truncate(50);
            rows.last().map(|row| row.connection_id)
        } else {
            None
        };
        Ok(DevicePage {
            devices: rows
                .into_iter()
                .map(|row| RegisteredDevice {
                    device: row.device_id,
                    connection_id: row.connection_id,
                    name: row.devices.name,
                    os: row.devices.os,
                })
                .collect(),
            next,
        })
    }
    pub async fn device_for_key(
        &self,
        token: &str,
        key: &ed25519_dalek::VerifyingKey,
    ) -> Result<Uuid, AuthError> {
        // A Mixel ID with both a personal space and a company scope keeps a
        // separate key per scope; look in the personal devices first and fall
        // back to the company enrollment of this key.
        if self.authenticate(token).await?.is_personal()
            && let Some(device) = self
                .personal_devices(token)
                .await?
                .into_iter()
                .find(|row| {
                    row.enrollment
                        .key()
                        .as_ref()
                        .is_ok_and(|saved| saved == key)
                })
                .map(|row| row.enrollment.device_id)
        {
            return Ok(device);
        }
        let hex: String = key
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let rows:Vec<EnrollmentRow>=self.get(&format!("/rest/v1/remote_device_enrollments?public_key=eq.%5Cx{hex}&select=device_id,connection_id,generation,public_key,revoked_at&limit=2"),token).await?;
        let [row] = rows.as_slice() else {
            return Err(AuthError);
        };
        if row.device_id.is_nil()
            || row.revoked_at.is_some()
            || row.generation == 0
            || row.public_key != format!("\\x{hex}")
        {
            return Err(AuthError);
        }
        Ok(row.device_id)
    }
    /// Resolve the peer's enrolled identity independently of signaling-supplied
    /// Hello messages. The verified caller's bearer remains subject to RLS.
    pub async fn peer_key(
        &self,
        token: &str,
        connection: u32,
    ) -> Result<ed25519_dalek::VerifyingKey, AuthError> {
        if !(100_000_000..=999_999_999).contains(&connection) {
            return Err(AuthError);
        }
        if self.authenticate(token).await?.is_personal()
            && let Some(row) = self
                .personal_devices(token)
                .await?
                .into_iter()
                .find(|row| row.enrollment.connection_id == connection)
        {
            return row.enrollment.key();
        }
        let rows: Vec<EnrollmentRow> = self.get(&format!("/rest/v1/remote_device_enrollments?connection_id=eq.{connection}&select=device_id,connection_id,generation,public_key,revoked_at&limit=2"), token).await?;
        let [row] = rows.as_slice() else {
            return Err(AuthError);
        };
        if row.connection_id != connection
            || row.revoked_at.is_some()
            || row.generation == 0
            || row.device_id.is_nil()
        {
            return Err(AuthError);
        }
        let hex = row.public_key.strip_prefix("\\x").ok_or(AuthError)?;
        if hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(AuthError);
        }
        let mut bytes = [0; 32];
        for (index, byte) in bytes.iter_mut().enumerate() {
            *byte =
                u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16).map_err(|_| AuthError)?;
        }
        let key = ed25519_dalek::VerifyingKey::from_bytes(&bytes).map_err(|_| AuthError)?;
        if key.is_weak() {
            return Err(AuthError);
        }
        Ok(key)
    }
    pub fn new(base: Url, api_key: String) -> Result<Self, AuthError> {
        if base.scheme() != "https"
            || base.host_str().is_none()
            || !base.username().is_empty()
            || base.password().is_some()
            || base.query().is_some()
            || base.fragment().is_some()
            || !matches!(base.path(), "" | "/")
            || api_key.is_empty()
            || api_key.len() > 16384
            || api_key.starts_with("sb_secret_")
            || api_key.bytes().any(|byte| byte.is_ascii_control())
        {
            return Err(AuthError);
        }
        // The app-facing API key must be public. Never distribute an elevated
        // legacy service-role JWT or a new-style secret key as configuration.
        if let Some(payload) = api_key.split('.').nth(1)
            && let Ok(decoded) = URL_SAFE_NO_PAD.decode(payload)
            && let Ok(claims) = serde_json::from_slice::<serde_json::Value>(&decoded)
            && claims
                .get("role")
                .and_then(serde_json::Value::as_str)
                .is_some_and(|role| role != "anon")
        {
            return Err(AuthError);
        }
        let client = Client::builder()
            .timeout(Duration::from_secs(4))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| AuthError)?;
        Ok(Self {
            client,
            base,
            api_key,
        })
    }
    async fn get<T: serde::de::DeserializeOwned>(
        &self,
        path: &str,
        token: &str,
    ) -> Result<T, AuthError> {
        let response = self
            .client
            .get(self.base.join(path).map_err(|_| AuthError)?)
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .send()
            .await
            .map_err(|_| AuthError)?;
        Self::decode_response(response).await
    }
    async fn decode_response<T: serde::de::DeserializeOwned>(
        response: reqwest::Response,
    ) -> Result<T, AuthError> {
        if !response.status().is_success() {
            return Err(AuthError);
        }
        let mut body = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| AuthError)?;
            if body.len() + chunk.len() > 65536 {
                return Err(AuthError);
            }
            body.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&body).map_err(|_| AuthError)
    }
    pub async fn authenticate_device(
        &self,
        token: &str,
        device: Uuid,
    ) -> Result<EnrolledPrincipal, AuthError> {
        let mut principal = self.authenticate(token).await?;
        if principal.is_personal() {
            if let Some(row) = self
                .personal_devices(token)
                .await?
                .into_iter()
                .find(|row| row.enrollment.device_id == device)
                .map(|row| row.enrollment)
            {
                return Ok(EnrolledPrincipal {
                    principal,
                    device,
                    connection_id: row.connection_id,
                    generation: row.generation,
                    public_key: row.key()?,
                    identity_ticket: None,
                });
            }
            // Existing callers may still use their separately enrolled ISM
            // device. Its original role, MFA and RLS checks still apply.
            principal = self.authenticate_scope(token, false).await?;
        }
        // The same verified bearer is subject to enrollment RLS and the live
        // ISM profile/device/client/organization binding, not a service-role key.
        let rows: Vec<EnrollmentRow> = self.get(&format!("/rest/v1/remote_device_enrollments?device_id=eq.{device}&select=device_id,connection_id,generation,public_key,revoked_at&limit=2"), token).await?;
        let [row] = rows.as_slice() else {
            return Err(AuthError);
        };
        if row.device_id != device
            || row.revoked_at.is_some()
            || row.generation == 0
            || !(100_000_000..=999_999_999).contains(&row.connection_id)
        {
            return Err(AuthError);
        }
        let hex = row.public_key.strip_prefix("\\x").ok_or(AuthError)?;
        if hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(AuthError);
        }
        let mut bytes = [0; 32];
        for (index, byte) in bytes.iter_mut().enumerate() {
            *byte =
                u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16).map_err(|_| AuthError)?;
        }
        let public_key = ed25519_dalek::VerifyingKey::from_bytes(&bytes).map_err(|_| AuthError)?;
        if public_key.is_weak() {
            return Err(AuthError);
        }
        Ok(EnrolledPrincipal {
            principal,
            device,
            connection_id: row.connection_id,
            generation: row.generation,
            public_key,
            identity_ticket: None,
        })
    }
    pub async fn authenticate(&self, token: &str) -> Result<Principal, AuthError> {
        self.authenticate_scope(token, true).await
    }
    /// The company (team or organization) scope of this bearer, ignoring any
    /// personal Remote account. Used when one Mixel ID has both.
    pub async fn authenticate_organization(&self, token: &str) -> Result<Principal, AuthError> {
        self.authenticate_scope(token, false).await
    }

    async fn authenticate_scope(
        &self,
        token: &str,
        prefer_personal: bool,
    ) -> Result<Principal, AuthError> {
        if token.len() > 16000
            || token.is_empty()
            || !token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
        {
            return Err(AuthError);
        }
        let parts: Vec<_> = token.split('.').collect();
        if parts.len() != 3 || parts.iter().any(|s| s.is_empty()) {
            return Err(AuthError);
        }
        let claims: Claims =
            serde_json::from_slice(&URL_SAFE_NO_PAD.decode(parts[1]).map_err(|_| AuthError)?)
                .map_err(|_| AuthError)?;
        // Decoding claims is NOT verification. The same bearer must be accepted
        // by GoTrue below, and its subject must match GoTrue's verified user.
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| AuthError)?
            .as_secs();
        if claims.role != "authenticated" || claims.exp <= now {
            return Err(AuthError);
        }
        let user: User = self.get("/auth/v1/user", token).await?;
        if user.id != claims.sub {
            return Err(AuthError);
        }
        if prefer_personal
            && let Some(id) = self
                .personal_account(token)
                .await?
                .and_then(|account| account.profile_id)
        {
            return Ok(Principal {
                user: id,
                tenant: Some(id),
                staff: false,
                expires_at: claims.exp,
            });
        }
        let profiles: Vec<Profile> = self.get(&format!("/rest/v1/users?auth_user_id=eq.{}&is_active=eq.true&select=id,auth_user_id,organization_id,is_active,role&limit=2", user.id), token).await?;
        let [profile] = profiles.as_slice() else {
            return Err(AuthError);
        };
        if profile.auth_user_id != user.id
            || !profile.is_active
            || !matches!(profile.role.as_str(), "admin" | "agent" | "user")
        {
            return Err(AuthError);
        }
        let staff =
            profile.organization_id.is_none() && matches!(profile.role.as_str(), "admin" | "agent");
        if profile.organization_id.is_none() && !staff {
            return Err(AuthError);
        }
        if staff && claims.aal.as_deref() != Some("aal2") {
            return Err(AuthError);
        }
        if let Some(tenant) = profile.organization_id {
            let orgs: Vec<Organization> = self
                .get(
                    &format!("/rest/v1/organizations?id=eq.{tenant}&select=id,is_active&limit=2"),
                    token,
                )
                .await?;
            if !matches!(orgs.as_slice(), [org] if org.id == tenant && org.is_active) {
                return Err(AuthError);
            }
        }
        Ok(Principal {
            user: profile.id,
            tenant: profile.organization_id,
            staff,
            expires_at: claims.exp,
        })
    }
}

fn valid_personal_name(name: &str) -> bool {
    let length = name.chars().count();
    (1..=80).contains(&length) && !name.chars().any(char::is_control) && !name.trim().is_empty()
}

impl EnrollmentRow {
    fn key(&self) -> Result<ed25519_dalek::VerifyingKey, AuthError> {
        if self.device_id.is_nil()
            || self.revoked_at.is_some()
            || self.generation == 0
            || !(100_000_000..=999_999_999).contains(&self.connection_id)
        {
            return Err(AuthError);
        }
        let hex = self.public_key.strip_prefix("\\x").ok_or(AuthError)?;
        if hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err(AuthError);
        }
        let mut bytes = [0; 32];
        for (index, byte) in bytes.iter_mut().enumerate() {
            *byte =
                u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16).map_err(|_| AuthError)?;
        }
        let key = ed25519_dalek::VerifyingKey::from_bytes(&bytes).map_err(|_| AuthError)?;
        if key.is_weak() {
            return Err(AuthError);
        }
        Ok(key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        Router,
        extract::State,
        http::{HeaderMap, Uri},
        response::IntoResponse,
        routing::get,
    };
    use serde_json::{Value, json};
    use std::sync::{Arc, Mutex};
    #[derive(Clone)]
    struct Fixture {
        mode: &'static str,
        paths: Arc<Mutex<Vec<String>>>,
    }
    const AUTH: &str = "00000000-0000-0000-0000-000000000001";
    const USER: &str = "00000000-0000-0000-0000-000000000002";
    const ORG: &str = "00000000-0000-0000-0000-000000000003";
    async fn reply(
        State(f): State<Fixture>,
        uri: Uri,
        headers: HeaderMap,
    ) -> axum::response::Response {
        assert_eq!(headers["apikey"], "public-key");
        assert!(
            headers["authorization"]
                .to_str()
                .unwrap()
                .starts_with("Bearer ")
        );
        f.paths.lock().unwrap().push(uri.path().into());
        if f.mode == "oversized" {
            return "x".repeat(65537).into_response();
        }
        if f.mode == "denied" {
            return axum::http::StatusCode::UNAUTHORIZED.into_response();
        }
        let body: Value = match uri.path() {
            "/auth/v1/user" => json!({"id": if f.mode == "wrong_subject" { USER } else { AUTH }}),
            "/rest/v1/rpc/remote_personal_account" => {
                if f.mode == "personal" {
                    json!([{"profile_id": USER, "display_name": "Remote Customer"}])
                } else {
                    return axum::http::StatusCode::NOT_FOUND.into_response();
                }
            }
            "/rest/v1/users" => {
                assert!(
                    uri.query()
                        .unwrap()
                        .contains(&format!("auth_user_id=eq.{AUTH}"))
                );
                json!([{"id": USER, "auth_user_id": AUTH, "organization_id": if f.mode.starts_with("staff") { Value::Null } else { json!(ORG) }, "is_active": f.mode != "inactive_user", "role": "admin"}])
            }
            "/rest/v1/organizations" => json!([{"id": ORG, "is_active": f.mode != "inactive_org"}]),
            "/rest/v1/remote_device_enrollments" => {
                if f.mode.starts_with("directory_") {
                    let url: Url = format!("http://fixture{uri}").parse().unwrap();
                    let query: std::collections::HashMap<_, _> = url.query_pairs().collect();
                    assert_eq!(query.get("revoked_at").unwrap(), "is.null");
                    assert_eq!(query.get("limit").unwrap(), "51");
                    assert_eq!(query.get("order").unwrap(), "connection_id.asc");
                    assert_eq!(query.get("connection_id").unwrap(), "gt.123456789");
                    assert_eq!(query.get("devices.name").unwrap(), "ilike.*Mixel\\_\\%*");
                    assert!(!query.get("select").unwrap().contains("public_key"));
                    if f.mode == "directory_denied" {
                        return axum::http::StatusCode::FORBIDDEN.into_response();
                    }
                    let mut rows:Vec<Value>=(0..if f.mode=="directory_oversized"{52}else{51}).map(|index|json!({"device_id":Uuid::from_u128(1000+index),"connection_id":123456790+index,"generation":1,"devices":{"id":Uuid::from_u128(1000+index),"name":format!("Mixel device{index}"),"os":"macos"}})).collect();
                    match f.mode {
                        "directory_duplicate" => {
                            rows[1]["connection_id"] = rows[0]["connection_id"].clone()
                        }
                        "directory_wrong_binding" => rows[0]["devices"]["id"] = json!(Uuid::nil()),
                        "directory_control_name" => {
                            rows[0]["devices"]["name"] = json!("bad\u{0}name")
                        }
                        "directory_unreadable_device" => rows[0]["devices"] = Value::Null,
                        _ => {}
                    }
                    return axum::Json(rows).into_response();
                }
                assert!(
                    uri.query()
                        .unwrap()
                        .contains(&format!("device_id=eq.{}", Uuid::from_u128(21)))
                );
                if f.mode == "enrollment_denied" {
                    return axum::http::StatusCode::FORBIDDEN.into_response();
                }
                if f.mode == "enrollment_missing" {
                    return axum::Json(json!([])).into_response();
                }
                let key = ed25519_dalek::SigningKey::from_bytes(&[7; 32])
                    .verifying_key()
                    .to_bytes();
                let encoded: String = key.iter().map(|b| format!("{b:02x}")).collect();
                let row = json!({
                    "device_id":Uuid::from_u128(if f.mode=="enrollment_wrong_device" {22} else {21}),
                    "connection_id":if f.mode=="enrollment_bad_id" {999} else {123456789},
                    "generation":if f.mode=="enrollment_bad_generation" {0} else {1},
                    "revoked_at":if f.mode=="enrollment_revoked" {json!("2026-11-06T00:00:00Z")} else {Value::Null},
                    "public_key":if f.mode=="enrollment_weak_key" {format!("\\x{}","00".repeat(32))}
                        else if f.mode=="enrollment_bad_key" {"\\xZZ".to_owned()} else {format!("\\x{encoded}")},
                });
                if f.mode == "enrollment_duplicate" {
                    json!([row.clone(), row])
                } else {
                    json!([row])
                }
            }
            _ => panic!("unexpected identity endpoint"),
        };
        axum::Json(body).into_response()
    }
    fn bearer(aal: &str, expiry: u64) -> String {
        format!(
            "header.{}.signature",
            URL_SAFE_NO_PAD.encode(
                serde_json::to_vec(
                    &json!({"sub": AUTH,"role":"authenticated","exp":expiry,"aal":aal})
                )
                .unwrap()
            )
        )
    }
    async fn fixture(mode: &'static str, aal: &str) -> (Result<Principal, AuthError>, Vec<String>) {
        let paths = Arc::new(Mutex::new(Vec::new()));
        let state = Fixture {
            mode,
            paths: paths.clone(),
        };
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}/", listener.local_addr().unwrap())
            .parse()
            .unwrap();
        let task = tokio::spawn(async {
            axum::serve(
                listener,
                Router::new().fallback(get(reply)).with_state(state),
            )
            .await
            .unwrap();
        });
        // Private construction only inside this test module permits HTTP fixtures.
        // The public production constructor always requires HTTPS.
        let auth = IsmAuth {
            client: Client::new(),
            base,
            api_key: "public-key".into(),
        };
        let result = auth
            .authenticate(&bearer(
                aal,
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + 600,
            ))
            .await;
        task.abort();
        let paths = paths.lock().unwrap().clone();
        (result, paths)
    }
    #[tokio::test]
    async fn ism_adapter_uses_verified_subject_active_profile_and_active_organization() {
        let (result, paths) = fixture("valid", "aal1").await;
        let principal = result.unwrap();
        assert_eq!(principal.user.to_string(), USER);
        assert_eq!(principal.tenant.unwrap().to_string(), ORG);
        assert!(!principal.staff);
        assert_eq!(
            paths,
            [
                "/auth/v1/user",
                "/rest/v1/rpc/remote_personal_account",
                "/rest/v1/users",
                "/rest/v1/organizations"
            ]
        );
        for mode in [
            "wrong_subject",
            "inactive_user",
            "inactive_org",
            "oversized",
            "denied",
        ] {
            assert!(fixture(mode, "aal1").await.0.is_err(), "{mode}");
        }
        assert!(fixture("staff", "aal1").await.0.is_err());
        assert!(fixture("staff", "aal2").await.0.unwrap().staff);
    }
    #[tokio::test]
    async fn verified_personal_account_signs_in_without_an_ism_staff_profile_or_aal2() {
        let (result, paths) = fixture("personal", "aal1").await;
        let principal = result.unwrap();
        assert!(principal.is_personal());
        assert!(!principal.staff);
        assert_eq!(
            paths,
            ["/auth/v1/user", "/rest/v1/rpc/remote_personal_account"]
        );
    }

    #[tokio::test]
    async fn enrolled_identity_is_resolved_through_same_verified_bearer_and_fails_closed() {
        for mode in [
            "enrollment_valid",
            "enrollment_denied",
            "enrollment_missing",
            "enrollment_wrong_device",
            "enrollment_bad_id",
            "enrollment_bad_generation",
            "enrollment_revoked",
            "enrollment_weak_key",
            "enrollment_bad_key",
            "enrollment_duplicate",
        ] {
            let paths = Arc::new(Mutex::new(Vec::new()));
            let state = Fixture {
                mode,
                paths: paths.clone(),
            };
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let base = format!("http://{}/", listener.local_addr().unwrap())
                .parse()
                .unwrap();
            let task = tokio::spawn(async move {
                axum::serve(
                    listener,
                    Router::new().fallback(get(reply)).with_state(state),
                )
                .await
                .unwrap();
            });
            let auth = IsmAuth {
                client: Client::new(),
                base,
                api_key: "public-key".into(),
            };
            let token = bearer(
                "aal1",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + 600,
            );
            let result = auth.authenticate_device(&token, Uuid::from_u128(21)).await;
            if mode == "enrollment_valid" {
                let enrolled = result.unwrap();
                assert_eq!(enrolled.connection_id, 123456789);
                assert_eq!(
                    enrolled.public_key,
                    ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key()
                );
            } else {
                assert!(result.is_err(), "{mode}");
            }
            assert_eq!(
                paths.lock().unwrap().as_slice(),
                [
                    "/auth/v1/user",
                    "/rest/v1/rpc/remote_personal_account",
                    "/rest/v1/users",
                    "/rest/v1/organizations",
                    "/rest/v1/remote_device_enrollments"
                ]
            );
            task.abort();
            let _ = task.await;
        }
    }
    #[test]
    fn production_identity_url_rejects_insecure_or_ambiguous_endpoints() {
        for url in [
            "http://ism.example",
            "https://user@ism.example",
            "https://ism.example/other",
            "https://ism.example?key=secret",
        ] {
            assert!(IsmAuth::new(url.parse().unwrap(), "public".into()).is_err());
        }
        assert!(IsmAuth::new("https://ism.example/".parse().unwrap(), "public".into()).is_ok());
    }
    #[tokio::test]
    async fn device_directory_is_bearer_scoped_bounded_and_rejects_malformed_bindings() {
        for mode in [
            "directory_valid",
            "directory_denied",
            "directory_oversized",
            "directory_duplicate",
            "directory_wrong_binding",
            "directory_control_name",
            "directory_unreadable_device",
        ] {
            let paths = Arc::new(Mutex::new(Vec::new()));
            let state = Fixture {
                mode,
                paths: paths.clone(),
            };
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let base = format!("http://{}/", listener.local_addr().unwrap())
                .parse()
                .unwrap();
            let task = tokio::spawn(async move {
                axum::serve(
                    listener,
                    Router::new().fallback(get(reply)).with_state(state),
                )
                .await
                .unwrap();
            });
            let auth = IsmAuth {
                client: Client::new(),
                base,
                api_key: "public-key".into(),
            };
            let token = bearer(
                "aal1",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + 600,
            );
            let result = auth.directory(&token, Some(123456789), "Mixel_%").await;
            if mode == "directory_valid" {
                let page = result.unwrap();
                assert_eq!(page.devices.len(), 50);
                assert_eq!(page.next, Some(123456839));
                assert_eq!(page.devices[0].device, Uuid::from_u128(1000));
            } else {
                assert!(result.is_err(), "{mode}");
            }
            assert_eq!(
                paths.lock().unwrap().as_slice(),
                [
                    "/auth/v1/user",
                    "/rest/v1/rpc/remote_personal_account",
                    "/rest/v1/users",
                    "/rest/v1/organizations",
                    "/rest/v1/remote_device_enrollments"
                ]
            );
            task.abort();
            let _ = task.await;
        }
    }
    async fn enrollment_write_reply(
        State(f): State<Fixture>,
        headers: HeaderMap,
        axum::Json(body): axum::Json<Value>,
    ) -> axum::response::Response {
        assert_eq!(headers["apikey"], "public-key");
        assert_eq!(headers["accept"], "application/vnd.pgrst.object+json");
        assert!(
            headers["authorization"]
                .to_str()
                .unwrap()
                .starts_with("Bearer ")
        );
        f.paths.lock().unwrap().push("POST enrollment".into());
        assert_eq!(body["target"], json!(Uuid::from_u128(21)));
        let key = ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key();
        let hex: String = key
            .as_bytes()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(body["key_material"], json!(format!("\\x{hex}")));
        if f.mode == "staff_write_denied" {
            return axum::http::StatusCode::CONFLICT.into_response();
        }
        axum::Json(json!({"device_id":Uuid::from_u128(21),"connection_id":123456789,"generation":if f.mode=="staff_write_stale"{2}else{1},"revoked_at":Value::Null,"public_key":if f.mode=="staff_write_wrong_key"{"\\x00".to_owned()}else{format!("\\x{hex}")}})).into_response()
    }
    #[tokio::test]
    async fn enrollment_requires_staff_and_cancellation_guard_and_reads_back_exact_persisted_key() {
        for mode in [
            "valid",
            "staff",
            "staff_write_denied",
            "staff_write_wrong_key",
            "staff_write_stale",
            "staff_cancelled",
        ] {
            let paths = Arc::new(Mutex::new(Vec::new()));
            let state = Fixture {
                mode,
                paths: paths.clone(),
            };
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let base = format!("http://{}/", listener.local_addr().unwrap())
                .parse()
                .unwrap();
            let task = tokio::spawn(async move {
                axum::serve(
                    listener,
                    Router::new()
                        .route(
                            "/rest/v1/rpc/remote_enroll_device",
                            axum::routing::post(enrollment_write_reply),
                        )
                        .fallback(get(reply))
                        .with_state(state),
                )
                .await
                .unwrap();
            });
            let auth = IsmAuth {
                client: Client::new(),
                base,
                api_key: "public-key".into(),
            };
            let token = bearer(
                "aal2",
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + 600,
            );
            let cancelled = std::sync::atomic::AtomicBool::new(mode == "staff_cancelled");
            let result = auth
                .enroll(
                    &token,
                    Uuid::from_u128(21),
                    ed25519_dalek::SigningKey::from_bytes(&[7; 32]).verifying_key(),
                    &cancelled,
                )
                .await;
            if mode == "staff" {
                assert_eq!(result.unwrap().connection_id, 123456789);
            } else {
                assert!(result.is_err(), "{mode}");
            }
            let paths = paths.lock().unwrap().clone();
            if matches!(mode, "valid" | "staff_cancelled") {
                assert!(!paths.iter().any(|path| path == "POST enrollment"));
            } else {
                assert!(paths.iter().any(|path| path == "POST enrollment"));
            }
            if mode == "staff" {
                assert!(
                    paths
                        .iter()
                        .any(|path| path == "/rest/v1/remote_device_enrollments")
                );
            }
            task.abort();
            let _ = task.await;
        }
    }
}
