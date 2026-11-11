//! Mixel Remote for iPhone, iPad and Android.
//!
//! The screen is the shared viewer (`frontends/web/viewer`). This shell adds what a web page on
//! another address cannot do: sign-in with Mixel ID inside the app, and the calls to the Mixel
//! account service and the connection service, which only accept the Mixel ISM website or a
//! native app. Sound and picture of a session still flow directly into the viewer.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use futures_util::{SinkExt, StreamExt};
use rand_core::RngCore;
use serde::Serialize;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, State, Url, Webview, WebviewUrl, WebviewWindowBuilder};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

pub mod rules;
use rules::{Return, api_allowed, is_app_page, meeting_link, signaling_allowed, sign_in_page_allowed, sign_in_return};

const IDENTITY: &str = "https://ism-api.mixel.ch";
/// Shown on every page that is not the app itself (the sign-in pages): a way back.
const BACK_BUTTON: &str = r#"(function(){
  if (location.protocol !== 'https:' || location.hostname === 'tauri.localhost') return;
  function add(){
    if (!document.body || document.getElementById('mixel-app-back')) return;
    var b = document.createElement('button');
    b.id = 'mixel-app-back'; b.type = 'button'; b.textContent = '✕'; b.setAttribute('aria-label', 'Cancel');
    var s = b.style; s.position = 'fixed'; s.top = 'max(10px, env(safe-area-inset-top))'; s.right = '10px'; s.zIndex = '2147483647';
    s.width = '40px'; s.height = '40px'; s.borderRadius = '20px'; s.border = '0'; s.background = 'rgba(23,32,51,.85)'; s.color = '#fff'; s.fontSize = '18px';
    b.onclick = function(){ location.href = 'https://ism.mixel.ch/remote-next/app-return?cancel=1'; };
    document.body.appendChild(b);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();"#;

#[derive(Clone, Serialize)]
struct Outcome {
    session: Option<serde_json::Value>,
    failed: bool,
}

#[derive(Default)]
struct Shared {
    /// PKCE verifier and public API key of the sign-in that is under way.
    pending: Option<(String, String)>,
    outcome: Option<Outcome>,
    home: Option<Url>,
    sockets: HashMap<u32, mpsc::UnboundedSender<Option<String>>>,
}
type Store = Mutex<Shared>;

fn lock(store: &Store) -> std::sync::MutexGuard<'_, Shared> {
    store.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Commands answer only to the app's own pages, never to a web page shown during sign-in.
fn own_page(webview: &Webview) -> Result<(), String> {
    match webview.url() {
        Ok(url) if is_app_page(&url) => Ok(()),
        _ => Err("Not available here".into()),
    }
}

fn client() -> &'static reqwest::Client {
    static CLIENT: std::sync::OnceLock<reqwest::Client> = std::sync::OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .https_only(!cfg!(feature = "lab-session"))
            .build()
            .unwrap_or_default()
    })
}

#[derive(Serialize)]
struct Reply {
    status: u16,
    body: String,
}

#[tauri::command]
async fn native_fetch(
    webview: Webview,
    url: String,
    method: String,
    headers: HashMap<String, String>,
    body: Option<String>,
) -> Result<Reply, String> {
    own_page(&webview)?;
    let url: Url = url.parse().map_err(|_| "Invalid address")?;
    if !api_allowed(&url) {
        return Err("Address not allowed".into());
    }
    let method = match method.as_str() {
        "GET" => reqwest::Method::GET,
        "POST" => reqwest::Method::POST,
        _ => return Err("Method not allowed".into()),
    };
    let mut request = client().request(method, url);
    for (name, value) in headers {
        if matches!(name.to_ascii_lowercase().as_str(), "apikey" | "authorization" | "content-type" | "prefer") {
            request = request.header(name, value);
        }
    }
    if let Some(body) = body {
        if body.len() > 262_144 {
            return Err("Request too large".into());
        }
        request = request.body(body);
    }
    let response = request.send().await.map_err(|_| "The service cannot be reached")?;
    let status = response.status().as_u16();
    let bytes = response.bytes().await.map_err(|_| "The answer could not be read")?;
    if bytes.len() > 4_194_304 {
        return Err("Answer too large".into());
    }
    Ok(Reply { status, body: String::from_utf8_lossy(&bytes).into_owned() })
}

#[derive(Clone, Serialize)]
struct SocketEvent {
    id: u32,
    kind: &'static str,
    data: Option<String>,
}

#[tauri::command]
fn ws_open(webview: Webview, app: AppHandle, store: State<'_, Store>, id: u32, url: String) -> Result<(), String> {
    own_page(&webview)?;
    let url: Url = url.parse().map_err(|_| "Invalid address")?;
    if !signaling_allowed(&url) {
        return Err("Address not allowed".into());
    }
    let (sender, mut outgoing) = mpsc::unbounded_channel::<Option<String>>();
    {
        let mut shared = lock(&store);
        if shared.sockets.len() >= 4 || shared.sockets.contains_key(&id) {
            return Err("Too many connections".into());
        }
        shared.sockets.insert(id, sender);
    }
    tauri::async_runtime::spawn(async move {
        let emit = |kind: &'static str, data: Option<String>| {
            let _ = app.emit("ws", SocketEvent { id, kind, data });
        };
        let connected = tokio::time::timeout(Duration::from_secs(15), tokio_tungstenite::connect_async(url.as_str())).await;
        match connected {
            Ok(Ok((stream, _))) => {
                emit("open", None);
                let (mut write, mut read) = stream.split();
                loop {
                    tokio::select! {
                        incoming = read.next() => match incoming {
                            Some(Ok(Message::Text(text))) => emit("message", Some(text.to_string())),
                            Some(Ok(Message::Ping(_) | Message::Pong(_) | Message::Binary(_) | Message::Frame(_))) => {}
                            Some(Ok(Message::Close(_))) | Some(Err(_)) | None => break,
                        },
                        wanted = outgoing.recv() => match wanted {
                            Some(Some(text)) => { if write.send(Message::Text(text.into())).await.is_err() { break; } }
                            Some(None) | None => { let _ = write.close().await; break; }
                        },
                    }
                }
            }
            _ => emit("error", None),
        }
        lock(&app.state::<Store>()).sockets.remove(&id);
        emit("close", None);
    });
    Ok(())
}

// Not async on purpose: messages must leave in the order the page sent them.
#[tauri::command]
fn ws_send(webview: Webview, store: State<'_, Store>, id: u32, data: String) -> Result<(), String> {
    own_page(&webview)?;
    if data.len() > 262_144 {
        return Err("Message too large".into());
    }
    match lock(&store).sockets.get(&id) {
        Some(sender) => sender.send(Some(data)).map_err(|_| "Not connected".into()),
        None => Err("Not connected".into()),
    }
}

#[tauri::command]
fn ws_close(webview: Webview, store: State<'_, Store>, id: u32) -> Result<(), String> {
    own_page(&webview)?;
    if let Some(sender) = lock(&store).sockets.get(&id) {
        let _ = sender.send(None);
    }
    Ok(())
}

/// Starts the sign-in with Mixel ID and returns the page to open. The answer comes back to
/// `https://ism.mixel.ch/remote-next/app-return`, which this app catches instead of loading.
#[tauri::command]
fn sign_in_begin(webview: Webview, store: State<'_, Store>, api_key: String) -> Result<String, String> {
    own_page(&webview)?;
    if api_key.len() > 2048 || !api_key.is_ascii() {
        return Err("Invalid key".into());
    }
    let mut bytes = [0u8; 48];
    rand_core::OsRng.fill_bytes(&mut bytes);
    let verifier = URL_SAFE_NO_PAD.encode(bytes);
    let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
    let mut url: Url = format!("{IDENTITY}/auth/v1/authorize").parse().map_err(|_| "Invalid sign-in address")?;
    url.query_pairs_mut()
        .append_pair("provider", "keycloak")
        .append_pair("scopes", "openid profile email")
        .append_pair("redirect_to", rules::RETURN)
        .append_pair("code_challenge", &challenge)
        .append_pair("code_challenge_method", "s256");
    let mut shared = lock(&store);
    shared.pending = Some((verifier, api_key));
    shared.outcome = None;
    Ok(url.into())
}

/// What the last sign-in brought back, handed out once.
#[tauri::command]
fn sign_in_result(webview: Webview, store: State<'_, Store>) -> Result<Option<Outcome>, String> {
    own_page(&webview)?;
    Ok(lock(&store).outcome.take())
}

#[tauri::command]
fn open_external(webview: Webview, app: AppHandle, url: String) -> Result<(), String> {
    own_page(&webview)?;
    let parsed: Url = url.parse().map_err(|_| "Invalid address")?;
    if !meeting_link(&parsed) {
        return Err("Address not allowed".into());
    }
    use tauri_plugin_opener::OpenerExt;
    app.opener().open_url(parsed.as_str(), None::<&str>).map_err(|_| "The link could not be opened".into())
}

/// Lab builds only: a test sign-in from the local test server (never part of a store build).
#[cfg(feature = "lab-session")]
#[tauri::command]
async fn lab_session(webview: Webview) -> Result<serde_json::Value, String> {
    own_page(&webview)?;
    let host = if cfg!(target_os = "android") { "10.0.2.2" } else { "127.0.0.1" };
    let response = client().get(format!("http://{host}:5173/dev-session.json")).send().await.map_err(|_| "No lab server")?;
    response.json().await.map_err(|_| "No lab session".into())
}
#[cfg(not(feature = "lab-session"))]
#[tauri::command]
fn lab_session() -> Result<serde_json::Value, String> {
    Err("Not available".into())
}

async fn exchange(code: String, verifier: String, api_key: String) -> Option<serde_json::Value> {
    let response = client()
        .post(format!("{IDENTITY}/auth/v1/token?grant_type=pkce"))
        .header("apikey", api_key)
        .header("content-type", "application/json")
        .body(serde_json::json!({ "auth_code": code, "code_verifier": verifier }).to_string())
        .send()
        .await
        .ok()?;
    if !response.status().is_success() {
        return None;
    }
    let session: serde_json::Value = response.json().await.ok()?;
    (session.get("access_token")?.is_string() && session.get("refresh_token")?.is_string()).then_some(session)
}

/// The sign-in came back (or was cancelled): finish it and show the app again.
fn finish(app: AppHandle, back: Return) {
    tauri::async_runtime::spawn(async move {
        let pending = lock(&app.state::<Store>()).pending.take();
        let outcome = match (back, pending) {
            (Return::Code(code), Some((verifier, api_key))) => {
                let session = exchange(code, verifier, api_key).await;
                Some(Outcome { failed: session.is_none(), session })
            }
            (Return::Cancelled, _) => None,
            _ => Some(Outcome { session: None, failed: true }),
        };
        let home = {
            let store = app.state::<Store>();
            let mut shared = lock(&store);
            shared.outcome = outcome;
            shared.home.clone()
        };
        if let (Some(window), Some(home)) = (app.get_webview_window("main"), home) {
            let _ = window.navigate(home);
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Store::default())
        .invoke_handler(tauri::generate_handler![
            native_fetch, ws_open, ws_send, ws_close, sign_in_begin, sign_in_result, open_external, lab_session
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .initialization_script(BACK_BUTTON)
                .on_navigation(move |url| {
                    if is_app_page(url) {
                        let store = handle.state::<Store>();
                        let mut shared = lock(&store);
                        if shared.home.is_none() {
                            let mut home = url.clone();
                            home.set_query(None);
                            home.set_fragment(None);
                            shared.home = Some(home);
                        }
                        return true;
                    }
                    if let Some(back) = sign_in_return(url) {
                        finish(handle.clone(), back);
                        return false;
                    }
                    sign_in_page_allowed(url)
                });
            #[cfg(desktop)]
            let builder = builder.title("Mixel Remote").inner_size(420.0, 820.0);
            let window = builder.build()?;
            if let Ok(url) = window.url()
                && is_app_page(&url)
            {
                lock(&app.state::<Store>()).home.get_or_insert(url);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Mixel Remote could not start");
}
