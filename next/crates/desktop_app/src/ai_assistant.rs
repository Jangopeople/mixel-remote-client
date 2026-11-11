//! Session assistant backed by the company's own AI server (Ollama API). The technician's
//! app talks to it directly, so screenshots and notes never leave the company network.
use serde::{Deserialize, Serialize};
use std::time::Duration;

const MAX_IMAGE_BASE64: usize = 4 * 1024 * 1024;
const MAX_PROMPT: usize = 12_000;
/// Preferred models, best first; the first one installed on the server is used.
const VISION_MODELS: [&str; 4] = ["gemma3:12b", "gemma4", "llava", "gemma3"];

#[derive(Serialize)]
pub struct Server {
    pub address: String,
    pub models: Vec<String>,
    /// Model used for screenshots and notes.
    pub model: String,
}

fn base(address: &str) -> Result<reqwest::Url, String> {
    let address = address.trim().trim_end_matches('/');
    if address.len() > 200 {
        return Err("Enter the address of your AI server".into());
    }
    let url: reqwest::Url = format!("{address}/").parse().map_err(|_| "Enter the address of your AI server, e.g. http://10.0.0.5:11434")?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() || !url.username().is_empty() || url.query().is_some() {
        return Err("Enter the address of your AI server, e.g. http://10.0.0.5:11434".into());
    }
    Ok(url)
}
fn client(seconds: u64) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(seconds))
        .connect_timeout(Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "The AI assistant is unavailable".into())
}

async fn models(url: &reqwest::Url) -> Result<Vec<String>, String> {
    #[derive(Deserialize)]
    struct Tags {
        models: Vec<Tag>,
    }
    #[derive(Deserialize)]
    struct Tag {
        name: String,
    }
    let tags: Tags = client(6)?
        .get(url.join("api/tags").map_err(|_| "Invalid AI server address")?)
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "The AI server did not answer")?
        .json()
        .await
        .map_err(|_| "This address is not an AI server")?;
    Ok(tags.models.into_iter().map(|tag| tag.name).filter(|name| name.len() <= 80).take(100).collect())
}
fn pick(models: &[String], wanted: &str) -> String {
    if !wanted.is_empty() && models.iter().any(|name| name == wanted) {
        return wanted.to_owned();
    }
    VISION_MODELS
        .iter()
        .find_map(|prefix| models.iter().find(|name| name.starts_with(prefix)))
        .or(models.first())
        .cloned()
        .unwrap_or_default()
}

/// Connects to `address`; with an empty address tries this computer and the usual company server.
pub async fn connect(address: &str, wanted_model: &str) -> Result<Server, String> {
    let candidates: Vec<String> = if address.trim().is_empty() {
        vec!["http://127.0.0.1:11434".into(), "http://10.124.1.33:11434".into()]
    } else {
        vec![address.to_owned()]
    };
    let mut last = "No AI server was found. Enter its address.".to_owned();
    for candidate in candidates {
        match base(&candidate) {
            Ok(url) => match models(&url).await {
                Ok(list) if !list.is_empty() => {
                    let model = pick(&list, wanted_model);
                    return Ok(Server { address: candidate.trim().trim_end_matches('/').to_owned(), models: list, model });
                }
                Ok(_) => last = "The AI server has no models installed".into(),
                Err(error) => last = error,
            },
            Err(error) => last = error,
        }
    }
    Err(last)
}

/// Asks the model; `image` is an optional JPEG or PNG as base64 (a screenshot of the session).
pub async fn ask(address: &str, model: &str, system: &str, prompt: &str, image: Option<&str>) -> Result<String, String> {
    let url = base(address)?;
    if prompt.trim().is_empty() || prompt.len() > MAX_PROMPT || system.len() > 2000 || model.is_empty() || model.len() > 80 {
        return Err("The question is empty or too long".into());
    }
    if image.is_some_and(|data| data.len() > MAX_IMAGE_BASE64 || !data.bytes().all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b))) {
        return Err("The screenshot is too large".into());
    }
    let mut user = serde_json::json!({ "role": "user", "content": prompt });
    if let Some(image) = image {
        user["images"] = serde_json::json!([image]);
    }
    #[derive(Deserialize)]
    struct Reply {
        message: Message,
    }
    #[derive(Deserialize)]
    struct Message {
        content: String,
    }
    let reply: Reply = client(240)?
        .post(url.join("api/chat").map_err(|_| "Invalid AI server address")?)
        .json(&serde_json::json!({
            "model": model,
            "stream": false,
            "options": { "temperature": 0.2, "num_predict": 700 },
            "messages": [ { "role": "system", "content": system }, user ],
        }))
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|_| "The AI server did not answer in time")?
        .json()
        .await
        .map_err(|_| "The AI server sent an unreadable answer")?;
    let text = reply.message.content.trim().to_owned();
    if text.is_empty() { Err("The AI server sent an empty answer".into()) } else { Ok(text.chars().take(8000).collect()) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn addresses_and_model_choice_are_checked() {
        assert!(base("http://10.0.0.5:11434").is_ok());
        assert!(base("ftp://x").is_err());
        assert!(base("http://user@host").is_err());
        let list = vec!["qwen2.5:14b".to_owned(), "gemma3:12b".to_owned()];
        assert_eq!(pick(&list, ""), "gemma3:12b");
        assert_eq!(pick(&list, "qwen2.5:14b"), "qwen2.5:14b");
        assert_eq!(pick(&["x".to_owned()], "missing"), "x");
    }
}
