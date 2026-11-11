//! Which addresses the app talks to and which pages it shows. Kept apart so it can be tested.

use tauri::Url;

/// Where the sign-in sends the browser back to. The app catches this address; it is never loaded.
pub const RETURN: &str = "https://ism.mixel.ch/remote-next/app-return";

#[derive(Debug, PartialEq, Eq)]
pub enum Return {
    Code(String),
    Failed,
    Cancelled,
}

/// The app's own pages: `tauri://localhost` on iPhone and Mac, `http://tauri.localhost` on Android and Windows.
pub fn is_app_page(url: &Url) -> bool {
    match (url.scheme(), url.host_str()) {
        ("tauri", Some("localhost")) => true,
        ("http" | "https", Some("tauri.localhost")) => url.port().is_none(),
        _ => false,
    }
}

fn https(url: &Url, host: &str) -> bool {
    url.scheme() == "https" && url.host_str() == Some(host) && url.port().is_none() && url.username().is_empty() && url.password().is_none()
}

/// The account service, and room creation of the meeting service.
pub fn api_allowed(url: &Url) -> bool {
    https(url, "ism-api.mixel.ch") || (https(url, "rs.mixel.ch") && url.path() == "/meet/api/rooms")
}

pub fn signaling_allowed(url: &Url) -> bool {
    url.scheme() == "wss" && url.host_str() == Some("rs.mixel.ch") && url.port() == Some(9444) && url.path() == "/v1/ws" && url.query().is_none()
}

pub fn meeting_link(url: &Url) -> bool {
    https(url, "rs.mixel.ch") && url.path() == "/meet/"
}

/// Pages that may be shown during sign-in: the account service, the Mixel ID sign-in pages and
/// the Microsoft sign-in they can hand over to.
pub fn sign_in_page_allowed(url: &Url) -> bool {
    // Mixel ID also offers sign-in through the company's Microsoft account.
    const MICROSOFT: [&str; 4] = ["login.microsoftonline.com", "login.microsoft.com", "login.live.com", "login.windows.net"];
    https(url, "ism-api.mixel.ch") || https(url, "auth.mixel.ch") || MICROSOFT.iter().any(|host| https(url, host))
}

/// Any page of the Mixel ISM website ends the sign-in: with a code, cancelled, or failed.
pub fn sign_in_return(url: &Url) -> Option<Return> {
    if !https(url, "ism.mixel.ch") {
        return None;
    }
    let value = |name: &str| url.query_pairs().find(|(key, _)| key == name).map(|(_, value)| value.into_owned());
    if value("cancel").is_some() {
        return Some(Return::Cancelled);
    }
    match value("code") {
        Some(code) if !code.is_empty() && code.len() <= 512 => Some(Return::Code(code)),
        _ => Some(Return::Failed),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn url(text: &str) -> Url {
        text.parse().unwrap()
    }

    #[test]
    fn only_the_apps_own_pages_count_as_the_app() {
        assert!(is_app_page(&url("tauri://localhost/index.html")));
        assert!(is_app_page(&url("http://tauri.localhost/index.html?x=1")));
        assert!(!is_app_page(&url("https://tauri.localhost.evil.example/")));
        assert!(!is_app_page(&url("http://tauri.localhost:8080/")));
        assert!(!is_app_page(&url("https://auth.mixel.ch/")));
        assert!(!is_app_page(&url("tauri://evil/")));
    }

    #[test]
    fn network_calls_are_limited_to_mixel_services() {
        assert!(api_allowed(&url("https://ism-api.mixel.ch/auth/v1/user")));
        assert!(api_allowed(&url("https://rs.mixel.ch/meet/api/rooms")));
        assert!(!api_allowed(&url("https://rs.mixel.ch/api/presence")));
        assert!(!api_allowed(&url("http://ism-api.mixel.ch/auth/v1/user")));
        assert!(!api_allowed(&url("https://ism-api.mixel.ch.evil.example/")));
        assert!(!api_allowed(&url("https://user@ism-api.mixel.ch/")));
        assert!(!api_allowed(&url("https://ism-api.mixel.ch:8443/")));
        assert!(signaling_allowed(&url("wss://rs.mixel.ch:9444/v1/ws")));
        assert!(!signaling_allowed(&url("ws://rs.mixel.ch:9444/v1/ws")));
        assert!(!signaling_allowed(&url("wss://rs.mixel.ch/v1/ws")));
        assert!(!signaling_allowed(&url("wss://example.com:9444/v1/ws")));
        assert!(meeting_link(&url("https://rs.mixel.ch/meet/#abcd-efgh-jkmn.0123")));
        assert!(!meeting_link(&url("https://rs.mixel.ch/")));
        assert!(!meeting_link(&url("https://example.com/meet/")));
    }

    #[test]
    fn sign_in_shows_only_mixel_pages_and_ends_on_the_ism_website() {
        assert!(sign_in_page_allowed(&url("https://auth.mixel.ch/application/o/mixel-ism-user/")));
        assert!(sign_in_page_allowed(&url("https://ism-api.mixel.ch/auth/v1/callback?code=x")));
        assert!(!sign_in_page_allowed(&url("https://accounts.example.com/")));
        assert!(sign_in_page_allowed(&url("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")));
        assert!(!sign_in_page_allowed(&url("https://login.microsoftonline.com.evil.example/")));
        assert!(!sign_in_page_allowed(&url("http://auth.mixel.ch/")));
        assert_eq!(sign_in_return(&url("https://auth.mixel.ch/?code=abc")), None);
        assert_eq!(sign_in_return(&url("https://ism.mixel.ch/remote-next/app-return?code=abc")), Some(Return::Code("abc".into())));
        assert_eq!(sign_in_return(&url("https://ism.mixel.ch/remote-next/app-return?cancel=1")), Some(Return::Cancelled));
        assert_eq!(sign_in_return(&url("https://ism.mixel.ch/remote-next/app-return?error=access_denied")), Some(Return::Failed));
        // The account service may fall back to the website's start page; the code is still ours.
        assert_eq!(sign_in_return(&url("https://ism.mixel.ch/?code=abc")), Some(Return::Code("abc".into())));
        assert_eq!(sign_in_return(&url("https://ism.mixel.ch/")), Some(Return::Failed));
    }
}
