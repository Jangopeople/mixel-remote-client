//! ISM identity adapter: validate the bearer with GoTrue, then the active ISM
//! profile and organization using that same bearer (never a service-role key).
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use futures_util::StreamExt;
use reqwest::{Client, Url};
use serde::Deserialize;
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
    pub fn can_request(&self, target: &Self) -> bool {
        self.staff || (self.tenant.is_some() && self.tenant == target.tenant)
    }
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
    pub fn new(base: Url, api_key: String) -> Result<Self, AuthError> {
        if base.scheme() != "https"
            || base.host_str().is_none()
            || !base.username().is_empty()
            || base.password().is_some()
            || base.query().is_some()
            || base.fragment().is_some()
            || !matches!(base.path(), "" | "/")
            || api_key.is_empty()
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
    pub async fn authenticate(&self, token: &str) -> Result<Principal, AuthError> {
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
            "/rest/v1/users" => {
                assert!(
                    uri.query()
                        .unwrap()
                        .contains(&format!("auth_user_id=eq.{AUTH}"))
                );
                json!([{"id": USER, "auth_user_id": AUTH, "organization_id": if f.mode == "staff" { Value::Null } else { json!(ORG) }, "is_active": f.mode != "inactive_user", "role": "admin"}])
            }
            "/rest/v1/organizations" => json!([{"id": ORG, "is_active": f.mode != "inactive_org"}]),
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
            ["/auth/v1/user", "/rest/v1/users", "/rest/v1/organizations"]
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
}
