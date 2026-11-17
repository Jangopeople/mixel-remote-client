//! LAB only (`--features qa-session`; never in release packages): drives the running GTK app without a
//! pointer, like the Mac lab app's command file.
//! - `MIXEL_QA_STATUSLOG=<file>`: one line per second: engine phase and, per session, stable id, transport id, phase,
//!   reconnecting / failed flags, pictures received, pictures per second and picture age (for connect-time, fps,
//!   renewal and gap measurements).
//! - `MIXEL_QA_COMMANDS=<file>`: lines read about once a second, then the file is deleted:
//!   `connect <id> [attended]`, `end <id>`, `retry <id>` (id = Remote ID of the host), `burst <id> <id>...` (engine requests at once),
//!   `chat <id> <text>`, `call <engine command> <json args>`; answers go to `<statuslog>.cmd.log`.
use crate::app::App;
use gtk::glib;
use serde_json::{Value, json};
use std::{io::Write, rc::Rc, time::Duration};

fn append(path: &str, line: &str) {
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{line}");
    }
}

fn now_ms() -> u128 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|time| time.as_millis()).unwrap_or(0)
}

pub fn install(app: &Rc<App>) {
    let log = std::env::var("MIXEL_QA_STATUSLOG").ok();
    let commands = std::env::var("MIXEL_QA_COMMANDS").ok();
    let answers = format!("{}.cmd.log", log.clone().unwrap_or_else(|| "/tmp/mixel-qa".into()));
    if let Some(path) = log {
        let app = app.clone();
        glib::timeout_add_local(Duration::from_secs(1), move || {
            let state = app.state.borrow();
            let remote = &state.remote;
            let mut line = format!("{} phase={} msg={}", now_ms(), remote.phase, remote.message.chars().take(60).collect::<String>());
            for item in &remote.sessions {
                let fps = state.session_state(&item.id).map(|session| session.fps).unwrap_or(0);
                line += &format!(
                    " | {} t={} {} rc={} fail={} rx={} pps={:.1} age={} view_fps={} unatt={} refused={}",
                    item.id.chars().take(8).collect::<String>(),
                    item.session.clone().unwrap_or_else(|| "-".into()).chars().take(8).collect::<String>(),
                    item.phase,
                    item.reconnecting,
                    item.reconnect_failed,
                    item.received,
                    item.quality.pictures_per_second,
                    item.quality.picture_age_ms.map(|age| age.to_string()).unwrap_or_else(|| "-".into()),
                    fps,
                    item.unattended_session,
                    item.unattended_refused,
                );
            }
            append(&path, &line);
            glib::ControlFlow::Continue
        });
    }
    if let Some(path) = commands {
        let app = app.clone();
        glib::timeout_add_local(Duration::from_secs(1), move || {
            let Ok(text) = std::fs::read_to_string(&path) else { return glib::ControlFlow::Continue };
            let _ = std::fs::remove_file(&path);
            for line in text.lines() {
                let words: Vec<&str> = line.split_whitespace().collect();
                let Some(first) = words.first().copied() else { continue };
                let ids: Vec<u32> = words.iter().skip(1).filter_map(|word| word.replace('-', "").parse().ok()).collect();
                let answers = answers.clone();
                let note = move |text: String| append(&answers, &format!("{} {text}", now_ms()));
                match first {
                    "connect" if !ids.is_empty() => app.connect(ids[0], None, !words.contains(&"attended")),
                    "end" if !ids.is_empty() => app.end_session_peer(ids[0]),
                    "retry" if !ids.is_empty() => {
                        let id = app.state.borrow().session_of_peer(ids[0]).map(|item| item.id.clone());
                        if let Some(id) = id {
                            app.reconnect_session(&id);
                        }
                    }
                    "burst" => {
                        for id in ids {
                            let (app, note) = (app.clone(), note.clone());
                            app.clone().spawn(async move {
                                let result = app.perform("remote_request", json!({ "host": id, "password": null, "unattended": true })).await;
                                note(format!("burst {id}: {}", result.err().unwrap_or_else(|| "ok".into())));
                            });
                        }
                    }
                    "chat" if !ids.is_empty() => {
                        let stable = app.state.borrow().session_of_peer(ids[0]).map(|item| item.id.clone());
                        let text = words.iter().skip(2).copied().collect::<Vec<_>>().join(" ");
                        if let Some(stable) = stable {
                            let (app, note) = (app.clone(), note.clone());
                            app.clone().spawn(async move {
                                let sent = app.perform("remote_chat_send", json!({ "session": stable, "text": text })).await;
                                let list = app.engine.call("remote_chat_messages", json!({ "session": stable })).await;
                                note(format!("chat {:?} {}", sent, list.map(|value| value.to_string()).unwrap_or_default()));
                            });
                        }
                    }
                    "call" if words.len() >= 2 => {
                        let command = words[1].to_owned();
                        let args: Value = serde_json::from_str(&words[2..].join(" ")).unwrap_or(json!({}));
                        let (app, note) = (app.clone(), note.clone());
                        app.clone().spawn(async move {
                            let result = app.engine.call(&command, args).await;
                            note(format!("call {command}: {}", result.map(|value| value.to_string().chars().take(6000).collect::<String>()).unwrap_or_else(|error| format!("ERR {error}"))));
                        });
                    }
                    _ => {}
                }
            }
            glib::ControlFlow::Continue
        });
    }
}
