//! Device administration RPCs `remote_admin_*` (docs/mixel-remote-device-admin-api.md, section 2),
//! called with the signed-in person's own ISM bearer. The database decides who may do what (owner and
//! admin of a company, Mixel staff with MFA); nothing here is a second check, only input hygiene so a
//! bad value never leaves the computer. Nothing is cached: every call carries the token it is given.
use super::IsmAuth;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use uuid::Uuid;

/// A refused or failed administration call in a form the apps can show and key on.
///
/// `hint` is the stable key (the server's own `hint` when it sent one): `not_admin`, `device_not_found`,
/// `confirm_mismatch`, `changed`, `mfa_required`, `plan_feature`, `read_only`, `invalid_*`, `too_many`,
/// or one of the engine's own: `offline` (no answer from the service), `session_expired` (401),
/// `invalid_argument` (rejected before sending), `refused` (anything else). The apps see this as the
/// failure text of the command: `{"hint": "...", "message": "...", "status": 403}`.
#[derive(Debug, Clone, Serialize, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct AdminError {
    /// HTTP status of the API (0: no answer).
    pub status: u16,
    pub hint: String,
    pub message: String,
}

impl AdminError {
    fn new(status: u16, hint: &str, message: &str) -> Self {
        Self { status, hint: hint.into(), message: message.into() }
    }
    fn offline() -> Self {
        Self::new(0, "offline", "Mixel Remote is unavailable. Please try again.")
    }
    /// The failure text handed to the apps (JSON, so they can read `hint`).
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| format!("{{\"hint\":\"{}\"}}", self.hint))
    }
}

/// One row of `remote_admin_devices` (section 2.1). Every field but the identity is optional: an older
/// server reports less and the apps show "—" rather than a guess.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct AdminDevice {
    pub device_id: Uuid,
    #[serde(default)]
    pub connection_id: Option<u32>,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub os: String,
    #[serde(default)]
    pub company_id: Option<Uuid>,
    #[serde(default)]
    pub company_name: Option<String>,
    /// `company` (ISM) or `saas`.
    #[serde(default)]
    pub company_kind: Option<String>,
    #[serde(default)]
    pub tenant_id: Option<Uuid>,
    #[serde(default)]
    pub group_id: Option<Uuid>,
    #[serde(default)]
    pub group_name: Option<String>,
    #[serde(default)]
    pub note: Option<String>,
    #[serde(default)]
    pub favorite: Option<bool>,
    /// `default`, `on` or `off`.
    #[serde(default)]
    pub unattended_setting: Option<String>,
    #[serde(default)]
    pub unattended_default: Option<bool>,
    #[serde(default)]
    pub unattended_allowed: Option<bool>,
    /// `online`, `offline`, `unknown` or `not_enrolled`.
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub last_seen: Option<String>,
    #[serde(default)]
    pub online_since: Option<String>,
    #[serde(default)]
    pub app_version: Option<String>,
    #[serde(default)]
    pub platform: Option<String>,
    #[serde(default)]
    pub version_seen_at: Option<String>,
    #[serde(default)]
    pub owner_id: Option<Uuid>,
    #[serde(default)]
    pub owner_name: Option<String>,
    #[serde(default)]
    pub enrolled_at: Option<String>,
    /// Send back as `expected_update` for a rename.
    #[serde(default)]
    pub updated_at: Option<String>,
    #[serde(default)]
    pub can_admin: Option<bool>,
}

/// Answer of `remote_admin_remove_device`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AdminRemoved {
    pub removed: bool,
    pub device_id: Uuid,
    #[serde(default)]
    pub connection_id: Option<u32>,
}

/// One person with a per-device unattended grant, or automatic access from the company setting (section 2.8).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AdminGrant {
    pub user_id: Uuid,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub granted_by: Option<Uuid>,
    #[serde(default)]
    pub granted_at: Option<String>,
    /// `company_setting` for automatic access (read-only); absent for a grant.
    #[serde(default)]
    pub source: Option<String>,
}

fn has_control(text: &str, allow_newline: bool) -> bool {
    text.chars().any(|c| c.is_control() && !(allow_newline && matches!(c, '\n' | '\t')))
}

impl IsmAuth {
    async fn admin_call<T: DeserializeOwned>(
        &self,
        token: &str,
        function: &'static str,
        body: Value,
    ) -> Result<T, AdminError> {
        debug_assert!(function.starts_with("remote_admin_"));
        let url = self.base.join(&format!("/rest/v1/rpc/{function}")).map_err(|_| AdminError::offline())?;
        let response = self
            .client
            .post(url)
            .header("apikey", &self.api_key)
            .bearer_auth(token)
            .json(&body)
            .send()
            .await
            .map_err(|_| AdminError::offline())?;
        let status = response.status().as_u16();
        // 2000 rows of section 2.1 are well under this; anything bigger is not our server.
        let max_bytes = 4 << 20;
        let mut raw = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| AdminError::offline())?;
            if raw.len() + chunk.len() > max_bytes {
                return Err(AdminError::offline());
            }
            raw.extend_from_slice(&chunk);
        }
        if (200..300).contains(&status) {
            return serde_json::from_slice(&raw).map_err(|_| AdminError::new(status, "refused", "Unexpected answer from the service"));
        }
        #[derive(Deserialize)]
        struct ApiError {
            message: Option<String>,
            hint: Option<String>,
        }
        let parsed: Option<ApiError> = serde_json::from_slice(&raw).ok();
        let hint = parsed
            .as_ref()
            .and_then(|error| error.hint.clone())
            .filter(|hint| !hint.is_empty() && hint.len() <= 40 && hint.bytes().all(|b| b.is_ascii_lowercase() || b == b'_'))
            .unwrap_or_else(|| {
                match status {
                    401 => "session_expired",
                    403 => "not_admin",
                    404 => "device_not_found",
                    409 => "changed",
                    405 => "read_only",
                    _ => "refused",
                }
                .into()
            });
        let message = parsed
            .and_then(|error| error.message)
            .map(|text| text.chars().filter(|c| !c.is_control()).take(300).collect::<String>())
            .filter(|text| !text.trim().is_empty())
            .unwrap_or_else(|| "The request was refused".into());
        Err(AdminError { status, hint, message })
    }

    /// The Devices list with administration fields. `company`: one company or tenant, none = all the caller may see.
    pub async fn admin_devices(&self, token: &str, company: Option<Uuid>, search: Option<&str>) -> Result<Vec<AdminDevice>, AdminError> {
        let search = search.map(str::trim).filter(|text| !text.is_empty());
        if search.is_some_and(|text| text.chars().count() > 80 || has_control(text, false)) {
            return Err(AdminError::new(400, "invalid_search", "Search text too long"));
        }
        self.admin_call(token, "remote_admin_devices", json!({"company": company, "search": search})).await
    }

    pub async fn admin_rename_device(
        &self,
        token: &str,
        device: Uuid,
        name: &str,
        expected_update: Option<&str>,
    ) -> Result<AdminDevice, AdminError> {
        let name = name.trim();
        if name.is_empty() || name.chars().count() > 60 || has_control(name, false) {
            return Err(AdminError::new(400, "invalid_name", "Enter a device name of 1 to 60 characters"));
        }
        self.admin_call(
            token,
            "remote_admin_rename_device",
            json!({"device": device, "device_name": name, "expected_update": expected_update}),
        )
        .await
    }

    /// `group_id` for a Remote SaaS tenant, `group_name` for an ISM company; both empty = no group.
    pub async fn admin_move_device(
        &self,
        token: &str,
        device: Uuid,
        group_id: Option<Uuid>,
        group_name: Option<&str>,
    ) -> Result<AdminDevice, AdminError> {
        let group_name = group_name.map(str::trim).filter(|text| !text.is_empty());
        if group_id.is_some() && group_name.is_some() {
            return Err(AdminError::new(400, "invalid_group", "Choose a group or a group name, not both"));
        }
        if group_name.is_some_and(|text| text.chars().count() > 60 || has_control(text, false)) {
            return Err(AdminError::new(400, "invalid_group", "Enter a group name of 1 to 60 characters"));
        }
        self.admin_call(token, "remote_admin_move_device", json!({"device": device, "group_id": group_id, "group_name": group_name}))
            .await
    }

    /// `None` (or only blanks) clears the note.
    pub async fn admin_set_device_note(&self, token: &str, device: Uuid, note: Option<&str>) -> Result<AdminDevice, AdminError> {
        let note = note.map(str::trim).filter(|text| !text.is_empty());
        if note.is_some_and(|text| text.chars().count() > 500 || has_control(text, true)) {
            return Err(AdminError::new(400, "invalid_note", "A note has at most 500 characters"));
        }
        self.admin_call(token, "remote_admin_set_device_note", json!({"device": device, "note": note})).await
    }

    /// Personal to the caller. Returns the new state.
    pub async fn admin_set_device_favorite(&self, token: &str, device: Uuid, favorite: bool) -> Result<bool, AdminError> {
        self.admin_call(token, "remote_admin_set_device_favorite", json!({"device": device, "favorite": favorite})).await
    }

    /// `setting`: `default`, `on` or `off`.
    pub async fn admin_set_device_unattended(&self, token: &str, device: Uuid, setting: &str) -> Result<AdminDevice, AdminError> {
        if !matches!(setting, "default" | "on" | "off") {
            return Err(AdminError::new(400, "invalid_setting", "The setting is default, on or off"));
        }
        self.admin_call(token, "remote_admin_set_device_unattended", json!({"device": device, "setting": setting})).await
    }

    /// `confirm_connection_id` must be the device's own 9-digit ID: the server refuses anything else
    /// (`confirm_mismatch`). The apps ask the person to confirm it before calling.
    pub async fn admin_remove_device(&self, token: &str, device: Uuid, confirm_connection_id: u32) -> Result<AdminRemoved, AdminError> {
        if !(100_000_000..=999_999_999).contains(&confirm_connection_id) {
            return Err(AdminError::new(400, "confirm_mismatch", "Enter the device's 9-digit ID to confirm"));
        }
        self.admin_call(
            token,
            "remote_admin_remove_device",
            json!({"device": device, "confirm_connection_id": confirm_connection_id}),
        )
        .await
    }

    /// Remote SaaS devices: who may connect unattended (grants and automatic access).
    pub async fn admin_unattended_grants(&self, token: &str, device: Uuid) -> Result<Vec<AdminGrant>, AdminError> {
        self.admin_call(token, "remote_admin_unattended_grants", json!({"device": device})).await
    }

    pub async fn admin_set_unattended_grant(&self, token: &str, device: Uuid, user: Uuid, granted: bool) -> Result<bool, AdminError> {
        self.admin_call(token, "remote_admin_set_unattended_grant", json!({"device": device, "user": user, "granted": granted}))
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{Json, http::StatusCode, response::IntoResponse};

    const DEVICE: u128 = 0xd1;
    const TOKEN: &str = "user-token";

    fn row(name: &str) -> Value {
        json!({"device_id": Uuid::from_u128(DEVICE), "connection_id": 440207203, "name": name, "os": "macos",
               "company_id": Uuid::from_u128(1), "company_name": "QA AG", "company_kind": "saas", "tenant_id": Uuid::from_u128(2),
               "group_id": null, "group_name": null, "note": "Desk 3", "favorite": true, "unattended_setting": "default",
               "unattended_default": false, "unattended_allowed": true, "status": "online", "last_seen": "2026-11-17T09:12:30Z",
               "online_since": null, "app_version": "2.0.19", "platform": "macos-arm64", "version_seen_at": null,
               "owner_id": Uuid::from_u128(3), "owner_name": "Pat", "enrolled_at": "2026-11-11T16:02:00Z",
               "updated_at": "2026-11-17T08:00:00Z", "can_admin": true, "future_field": 1})
    }
    fn refuse(status: StatusCode, code: &str, hint: Option<&str>, message: &str) -> axum::response::Response {
        (status, Json(json!({"code": code, "message": message, "details": null, "hint": hint}))).into_response()
    }

    async fn api(
        axum::extract::Path(function): axum::extract::Path<String>,
        headers: axum::http::HeaderMap,
        Json(body): Json<Value>,
    ) -> axum::response::Response {
        let bearer = headers.get("authorization").and_then(|v| v.to_str().ok()).unwrap_or_default().to_owned();
        assert_eq!(bearer, format!("Bearer {TOKEN}"), "every call carries the signed-in person's token");
        assert_eq!(headers.get("apikey").and_then(|v| v.to_str().ok()), Some("public"));
        let device_is = |n: u128| body["device"] == json!(Uuid::from_u128(n));
        match function.as_str() {
            "remote_admin_devices" => {
                if body["search"] == "boom" {
                    return refuse(StatusCode::BAD_REQUEST, "22023", Some("invalid_search"), "bad search");
                }
                if body["company"] == json!(Uuid::from_u128(9)) {
                    return Json(json!([])).into_response();
                }
                Json(json!([row("Office"), {"device_id": Uuid::from_u128(0xd2), "name": "Bare", "os": "linux"}])).into_response()
            }
            "remote_admin_rename_device" => {
                if device_is(0xe1) {
                    return refuse(StatusCode::FORBIDDEN, "42501", Some("not_admin"), "Only an owner or administrator can change devices");
                }
                if device_is(0xe2) {
                    return refuse(StatusCode::NOT_FOUND, "PT404", Some("device_not_found"), "Device not found");
                }
                if body["expected_update"] == "stale" {
                    return refuse(StatusCode::CONFLICT, "PT409", Some("changed"), "The device changed");
                }
                Json(row(body["device_name"].as_str().unwrap())).into_response()
            }
            "remote_admin_move_device" => {
                if device_is(0xe3) {
                    return refuse(StatusCode::FORBIDDEN, "42501", Some("plan_feature"), "Groups need the Team plan");
                }
                Json(row("Moved")).into_response()
            }
            "remote_admin_set_device_note" => {
                assert!(body["note"].is_null() || body["note"].is_string());
                Json(row("Noted")).into_response()
            }
            "remote_admin_set_device_favorite" => {
                if body["favorite"] == true && device_is(0xe4) {
                    return refuse(StatusCode::BAD_REQUEST, "54000", Some("too_many"), "At most 200 favourites");
                }
                Json(body["favorite"].clone()).into_response()
            }
            "remote_admin_set_device_unattended" => {
                if device_is(0xe5) {
                    return refuse(StatusCode::FORBIDDEN, "42501", Some("mfa_required"), "Second step needed");
                }
                let mut value = row("Office");
                value["unattended_setting"] = body["setting"].clone();
                Json(value).into_response()
            }
            "remote_admin_remove_device" => {
                if body["confirm_connection_id"] != 440207203 {
                    return refuse(StatusCode::BAD_REQUEST, "22023", Some("confirm_mismatch"), "The ID does not match");
                }
                if device_is(0xe6) {
                    return refuse(StatusCode::NOT_FOUND, "PT404", Some("device_not_found"), "Device not found");
                }
                if device_is(0xe7) {
                    return refuse(StatusCode::METHOD_NOT_ALLOWED, "25006", Some("read_only"), "Read-only");
                }
                Json(json!({"removed": true, "device_id": body["device"], "connection_id": 440207203})).into_response()
            }
            "remote_admin_unattended_grants" => {
                if device_is(0xe8) {
                    return refuse(StatusCode::BAD_REQUEST, "22023", Some("not_saas"), "Grants of company devices are kept in the console");
                }
                Json(json!([{"user_id": Uuid::from_u128(5), "name": "Tech", "role": "technician",
                              "granted_by": Uuid::from_u128(3), "granted_at": "2026-11-17T09:00:00Z"},
                             {"user_id": Uuid::from_u128(3), "name": "Owner", "role": "owner", "source": "company_setting"}]))
                .into_response()
            }
            "remote_admin_set_unattended_grant" => {
                if body["user"] == json!(Uuid::from_u128(0xbad)) {
                    return refuse(StatusCode::BAD_REQUEST, "22023", Some("invalid_user"), "Not a member");
                }
                Json(json!(true)).into_response()
            }
            "remote_admin_expired" => refuse(StatusCode::UNAUTHORIZED, "PGRST301", None, "JWT expired\u{7}"),
            _ => StatusCode::NOT_FOUND.into_response(),
        }
    }

    async fn serve() -> (IsmAuth, tokio::task::JoinHandle<()>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, axum::Router::new().route("/rest/v1/rpc/{function}", axum::routing::post(api))).await.unwrap()
        });
        // The production constructor requires HTTPS; tests reach the loopback fixture directly.
        let ism = IsmAuth { client: reqwest::Client::new(), base: format!("http://{address}").parse().unwrap(), api_key: "public".into() };
        (ism, server)
    }

    fn id(n: u128) -> Uuid {
        Uuid::from_u128(n)
    }
    fn hint(error: AdminError) -> (u16, String) {
        (error.status, error.hint)
    }

    #[tokio::test]
    async fn list_returns_admin_fields_and_tolerates_missing_ones() {
        let (ism, server) = serve().await;
        let rows = ism.admin_devices(TOKEN, None, Some("  office ")).await.unwrap();
        assert_eq!(rows.len(), 2);
        let first = &rows[0];
        assert_eq!((first.connection_id, first.app_version.as_deref(), first.owner_name.as_deref()), (Some(440207203), Some("2.0.19"), Some("Pat")));
        assert_eq!((first.note.as_deref(), first.favorite, first.unattended_setting.as_deref(), first.can_admin), (Some("Desk 3"), Some(true), Some("default"), Some(true)));
        assert_eq!((first.status.as_deref(), first.last_seen.as_deref()), (Some("online"), Some("2026-11-17T09:12:30Z")));
        let bare = &rows[1];
        assert_eq!((bare.name.as_str(), bare.app_version.clone(), bare.can_admin), ("Bare", None, None));
        assert!(ism.admin_devices(TOKEN, Some(id(9)), None).await.unwrap().is_empty());
        // Rows serialise with the section 2.1 names the apps read.
        let text = serde_json::to_value(first).unwrap();
        assert_eq!(text["unattended_allowed"], true);
        assert_eq!(text["updated_at"], "2026-11-17T08:00:00Z");
        server.abort();
    }

    #[tokio::test]
    async fn list_rejects_bad_search_before_sending_and_maps_server_refusal() {
        let (ism, server) = serve().await;
        assert_eq!(hint(ism.admin_devices(TOKEN, None, Some(&"x".repeat(81))).await.unwrap_err()), (400, "invalid_search".into()));
        assert_eq!(hint(ism.admin_devices(TOKEN, None, Some("a\u{7}b")).await.unwrap_err()), (400, "invalid_search".into()));
        let refused = ism.admin_devices(TOKEN, None, Some("boom")).await.unwrap_err();
        assert_eq!((refused.status, refused.hint.as_str(), refused.message.as_str()), (400, "invalid_search", "bad search"));
        server.abort();
    }

    #[tokio::test]
    async fn rename_maps_not_admin_not_found_changed_and_validates_the_name() {
        let (ism, server) = serve().await;
        let renamed = ism.admin_rename_device(TOKEN, id(DEVICE), "  Reception PC ", Some("2026-11-17T08:00:00Z")).await.unwrap();
        assert_eq!(renamed.name, "Reception PC");
        assert_eq!(hint(ism.admin_rename_device(TOKEN, id(0xe1), "X", None).await.unwrap_err()), (403, "not_admin".into()));
        assert_eq!(hint(ism.admin_rename_device(TOKEN, id(0xe2), "X", None).await.unwrap_err()), (404, "device_not_found".into()));
        assert_eq!(hint(ism.admin_rename_device(TOKEN, id(DEVICE), "X", Some("stale")).await.unwrap_err()), (409, "changed".into()));
        for bad in ["", "   ", "tab\there", &"n".repeat(61)] {
            assert_eq!(hint(ism.admin_rename_device(TOKEN, id(DEVICE), bad, None).await.unwrap_err()), (400, "invalid_name".into()), "{bad:?}");
        }
        server.abort();
    }

    #[tokio::test]
    async fn move_and_note_validate_then_call() {
        let (ism, server) = serve().await;
        assert_eq!(ism.admin_move_device(TOKEN, id(DEVICE), None, Some(" Office ")).await.unwrap().name, "Moved");
        assert_eq!(ism.admin_move_device(TOKEN, id(DEVICE), Some(id(7)), None).await.unwrap().name, "Moved");
        assert_eq!(ism.admin_move_device(TOKEN, id(DEVICE), None, None).await.unwrap().name, "Moved");
        assert_eq!(hint(ism.admin_move_device(TOKEN, id(0xe3), Some(id(7)), None).await.unwrap_err()), (403, "plan_feature".into()));
        assert_eq!(hint(ism.admin_move_device(TOKEN, id(DEVICE), Some(id(7)), Some("A")).await.unwrap_err()), (400, "invalid_group".into()));
        assert_eq!(hint(ism.admin_move_device(TOKEN, id(DEVICE), None, Some(&"g".repeat(61))).await.unwrap_err()), (400, "invalid_group".into()));
        assert_eq!(ism.admin_set_device_note(TOKEN, id(DEVICE), Some("line 1\nline 2")).await.unwrap().name, "Noted");
        assert_eq!(ism.admin_set_device_note(TOKEN, id(DEVICE), Some("   ")).await.unwrap().name, "Noted");
        assert_eq!(ism.admin_set_device_note(TOKEN, id(DEVICE), None).await.unwrap().name, "Noted");
        assert_eq!(hint(ism.admin_set_device_note(TOKEN, id(DEVICE), Some(&"n".repeat(501))).await.unwrap_err()), (400, "invalid_note".into()));
        assert_eq!(hint(ism.admin_set_device_note(TOKEN, id(DEVICE), Some("a\u{0}b")).await.unwrap_err()), (400, "invalid_note".into()));
        server.abort();
    }

    #[tokio::test]
    async fn favourite_and_unattended() {
        let (ism, server) = serve().await;
        assert!(ism.admin_set_device_favorite(TOKEN, id(DEVICE), true).await.unwrap());
        assert!(!ism.admin_set_device_favorite(TOKEN, id(DEVICE), false).await.unwrap());
        assert_eq!(hint(ism.admin_set_device_favorite(TOKEN, id(0xe4), true).await.unwrap_err()), (400, "too_many".into()));
        for setting in ["default", "on", "off"] {
            let row = ism.admin_set_device_unattended(TOKEN, id(DEVICE), setting).await.unwrap();
            assert_eq!(row.unattended_setting.as_deref(), Some(setting));
        }
        assert_eq!(hint(ism.admin_set_device_unattended(TOKEN, id(DEVICE), "always").await.unwrap_err()), (400, "invalid_setting".into()));
        assert_eq!(hint(ism.admin_set_device_unattended(TOKEN, id(0xe5), "off").await.unwrap_err()), (403, "mfa_required".into()));
        server.abort();
    }

    #[tokio::test]
    async fn remove_needs_the_id_confirmation() {
        let (ism, server) = serve().await;
        let done = ism.admin_remove_device(TOKEN, id(DEVICE), 440_207_203).await.unwrap();
        assert_eq!((done.removed, done.device_id, done.connection_id), (true, id(DEVICE), Some(440_207_203)));
        // Not a 9-digit ID: never sent.
        assert_eq!(hint(ism.admin_remove_device(TOKEN, id(DEVICE), 12).await.unwrap_err()), (400, "confirm_mismatch".into()));
        // A 9-digit ID that is not the device's: the server says no.
        let wrong = ism.admin_remove_device(TOKEN, id(DEVICE), 123_456_789).await.unwrap_err();
        assert_eq!((wrong.status, wrong.hint.as_str(), wrong.message.as_str()), (400, "confirm_mismatch", "The ID does not match"));
        assert_eq!(hint(ism.admin_remove_device(TOKEN, id(0xe6), 440_207_203).await.unwrap_err()), (404, "device_not_found".into()));
        assert_eq!(hint(ism.admin_remove_device(TOKEN, id(0xe7), 440_207_203).await.unwrap_err()), (405, "read_only".into()));
        server.abort();
    }

    #[tokio::test]
    async fn grants() {
        let (ism, server) = serve().await;
        let grants = ism.admin_unattended_grants(TOKEN, id(DEVICE)).await.unwrap();
        assert_eq!(grants.len(), 2);
        assert_eq!((grants[0].role.as_deref(), grants[0].source.clone()), (Some("technician"), None));
        assert_eq!(grants[1].source.as_deref(), Some("company_setting"));
        assert_eq!(hint(ism.admin_unattended_grants(TOKEN, id(0xe8)).await.unwrap_err()), (400, "not_saas".into()));
        assert!(ism.admin_set_unattended_grant(TOKEN, id(DEVICE), id(5), true).await.unwrap());
        assert_eq!(hint(ism.admin_set_unattended_grant(TOKEN, id(DEVICE), id(0xbad), true).await.unwrap_err()), (400, "invalid_user".into()));
        server.abort();
    }

    #[tokio::test]
    async fn transport_and_session_failures_are_typed() {
        let (ism, server) = serve().await;
        // 401 without a hint: the person must sign in again.
        let expired: Result<Value, _> = ism.admin_call(TOKEN, "remote_admin_expired", json!({})).await;
        let expired = expired.unwrap_err();
        assert_eq!((expired.status, expired.hint.as_str(), expired.message.as_str()), (401, "session_expired", "JWT expired"));
        // An unknown function (404 without a body).
        let unknown: Result<Value, _> = ism.admin_call(TOKEN, "remote_admin_nothing", json!({})).await;
        assert_eq!(hint(unknown.unwrap_err()), (404, "device_not_found".into()));
        server.abort();
        // Nothing listening: offline, never a panic.
        let down = IsmAuth { client: reqwest::Client::new(), base: "http://127.0.0.1:9".parse().unwrap(), api_key: "public".into() };
        assert_eq!(hint(down.admin_devices(TOKEN, None, None).await.unwrap_err()), (0, "offline".into()));
        // The failure text for the apps is JSON with the hint.
        let json: Value = serde_json::from_str(&expired.to_json()).unwrap();
        assert_eq!((json["hint"].as_str(), json["status"].as_u64()), (Some("session_expired"), Some(401)));
    }
}
