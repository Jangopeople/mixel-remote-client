//! LAB only (`--features qa-session`; release packages contain none of this): the app's engine
//! against the live Mixel service with a throwaway QA account's session file (never a real person's).
//!
//! - `--live-check <session.json> <run>`: a QA company, this computer added with its own key, online,
//!   devices, presence, groups, rename/move, members, invitations (test domain, never e-mailed),
//!   deploy links, Mixel Meet, the update check; then this computer removes itself and the company is
//!   deleted again.
//! - `--live-session <session.json> <run> <dir>`: this computer as technician to a lab computer that
//!   enrols with the deploy link written to `<dir>/deploy-command.txt` (unattended, pictures, input,
//!   chat, end), then everything it created is removed.
//! - `--live-host <session.json> <run> <dir>`: this computer as technician to the computer whose
//!   Remote ID appears in `<dir>/host-id.txt` (the GUI app on the same account), attended: the person
//!   there allows it; pictures, input, chat, end. Writes `<dir>/tenant.txt` first for the host.
//! - `--live-watch <session.json> <run> <dir>`: a QA company with a deploy link (`<dir>/deploy-token.txt`)
//!   for lab computers (update tests); every 5 s `<dir>/devices.txt` lists the company's computers with
//!   their Remote ID and whether they are online. `<dir>/request.txt` with a Remote ID starts an
//!   unattended session to it (pictures, input, end); `<dir>/stop` ends the watch and removes everything.
//! - `--check-providers`: each sign-in button (Mixel ID, Google, Microsoft, Apple) leads the browser to
//!   that provider's own sign-in page; nothing is signed in (the attempt is cancelled).
use crate::engine::{Engine, RealEngine};
use serde_json::{Value, json};
use std::{path::PathBuf, time::Duration};

struct Check {
    engine: RealEngine,
    checks: u32,
    failures: u32,
}

impl Check {
    fn check(&mut self, label: &str, ok: bool, detail: impl std::fmt::Display) {
        self.checks += 1;
        if ok {
            println!("PASS {label}");
        } else {
            self.failures += 1;
            println!("FAIL {label} {detail}");
        }
    }
    async fn call(&self, command: &str, args: Value) -> Result<Value, String> {
        self.engine.call(command, args).await
    }
    async fn status(&self) -> Value {
        self.call("remote_status", json!({})).await.unwrap_or(Value::Null)
    }
    async fn wait(&self, seconds: u64, until: impl Fn(&Value) -> bool) -> Option<Value> {
        for _ in 0..seconds * 4 {
            let status = self.status().await;
            if until(&status) {
                return Some(status);
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        None
    }
    async fn sign_in(&mut self) -> bool {
        let _ = self.call("account_sign_in", json!({})).await;
        for _ in 0..60 {
            if let Ok(status) = self.call("account_status", json!({})).await {
                let phase = status["phase"].as_str().unwrap_or_default().to_owned();
                if matches!(phase.as_str(), "authenticated" | "profile_setup" | "error") {
                    let ok = phase != "error";
                    self.check("sign-in with the QA session", ok, status);
                    return ok;
                }
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        self.check("sign-in with the QA session", false, "timeout");
        false
    }
    /// Pictures, harmless input (pointer moves, Shift), chat, then end; `session` is active.
    async fn session_tools(&mut self, active: &Value, folder: Option<&PathBuf>, attended: bool) {
        let id = active["session"].as_str().unwrap_or_default().to_owned();
        let (mut frames, mut sequence, mut size) = (0, 0, String::new());
        let start = std::time::Instant::now();
        let mut step = 0u32;
        while start.elapsed() < Duration::from_secs(20) && frames < 40 {
            step += 1;
            if step.is_multiple_of(10) {
                let event = json!({ "kind": "pointer", "x": (step * 997) % 60000 + 2000, "y": (step * 431) % 50000 + 5000 });
                let _ = self.engine.input(&id, event).await;
            }
            let Ok(data) = self.engine.next_video(&id).await else { break };
            if data.is_empty() {
                tokio::time::sleep(Duration::from_millis(20)).await;
                continue;
            }
            if let Ok(frame) = crate::video::decode(data, sequence) {
                sequence = frame.sequence;
                frames += 1;
                size = format!("{}×{}", frame.width, frame.height);
                if (frames == 1 || frames == 10)
                    && let Some(folder) = folder
                {
                    let _ = std::fs::write(folder.join("remote-screen.ppm"), ppm(&frame));
                }
            }
        }
        // A still screen sends a picture only when it changes: show something (the Super key opens the
        // desktop's overview, the second press closes it again), then expect new pictures.
        let mut changed = 0;
        if frames < 2 {
            for _ in 0..2 {
                let _ = self.engine.input(&id, json!({ "kind": "key", "usage": 227, "down": true })).await;
                let _ = self.engine.input(&id, json!({ "kind": "key", "usage": 227, "down": false })).await;
                let until = std::time::Instant::now() + Duration::from_secs(4);
                while std::time::Instant::now() < until {
                    match self.engine.next_video(&id).await {
                        Ok(data) if !data.is_empty() => {
                            if let Ok(frame) = crate::video::decode(data, sequence) {
                                sequence = frame.sequence;
                                changed += 1;
                            }
                        }
                        _ => tokio::time::sleep(Duration::from_millis(20)).await,
                    }
                }
            }
        }
        self.check(
            &format!("pictures from the other computer ({frames} in {} s, {size}; {changed} more after a visible change)", start.elapsed().as_secs()),
            frames >= 2 || (frames >= 1 && changed >= 1),
            "",
        );
        let mut posted = 0;
        for step in 0..10 {
            if self.engine.input(&id, json!({ "kind": "pointer", "x": 20000 + step * 2000, "y": 30000 })).await == Ok(true) {
                posted += 1;
            }
        }
        for down in [true, false] {
            if self.engine.input(&id, json!({ "kind": "key", "usage": 225, "down": down })).await == Ok(true) {
                posted += 1;
            }
        }
        self.check(&format!("input applied on the other computer ({posted}/12)"), posted == 12, "");
        // Chat is part of an attended session only (the person there allows it); unattended has none.
        if !attended {
            println!("chat: not part of an unattended session (by design)");
        } else {
            let sent = self.call("remote_chat_send", json!({ "session": id, "text": "Mixel Remote for Linux live check" })).await;
            self.check("chat message sent", sent.is_ok(), format!("{sent:?}"));
        }
        if let Some(consent) = active["consent"].as_str() {
            let stop = self.call("remote_stop_session", json!({ "consent": consent })).await;
            self.check("end session", stop.is_ok(), format!("{stop:?}"));
        }
        let back = self.wait(20, |status| status["phase"] == "ready").await;
        self.check("back to ready after the session", back.is_some(), "");
    }
}

/// The picture as a PPM file (no image library needed).
fn ppm(frame: &crate::video::Frame) -> Vec<u8> {
    let mut out = format!("P6\n{} {}\n255\n", frame.width, frame.height).into_bytes();
    for row in frame.pixels.chunks(frame.stride) {
        for pixel in row[..frame.width * 4].as_chunks::<4>().0 {
            match frame.layout {
                crate::video::Layout::Rgba => out.extend_from_slice(&pixel[..3]),
                crate::video::Layout::Bgra => out.extend_from_slice(&[pixel[2], pixel[1], pixel[0]]),
            }
        }
    }
    out
}

async fn providers(check: &mut Check) {
    let offered = check.call("sign_in_providers", json!({})).await.unwrap_or(Value::Null);
    println!("sign-in service offers: {offered}");
    let http = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).timeout(Duration::from_secs(15)).build().expect("http");
    for (provider, host) in [("keycloak", "auth.mixel.ch"), ("google", "accounts.google.com"), ("azure", "login.microsoftonline.com"), ("apple", "appleid.apple.com")] {
        let listed = offered.as_array().is_some_and(|list| list.iter().any(|item| item == provider));
        let url = check.call("account_sign_in", json!({ "open_browser": false, "provider": provider })).await;
        let mut location = String::new();
        if let Ok(Value::String(url)) = &url
            && let Ok(response) = http.get(url).send().await
        {
            location = response.headers().get("location").and_then(|value| value.to_str().ok()).unwrap_or_default().to_owned();
        }
        let reached = location.strip_prefix("https://").and_then(|rest| rest.split('/').next()).unwrap_or_default().to_owned();
        check.check(&format!("{provider}: offered, the button leads to {reached}"), listed && reached == host, format!("{url:?} → {location}"));
        let _ = check.call("account_cancel", json!({})).await;
    }
}

pub fn run(args: &[String]) -> i32 {
    if args.first().map(String::as_str) == Some("--check-providers") {
        let folder = std::env::temp_dir().join("mixel-linux-providers");
        let engine = match RealEngine::new(folder.join("engine")) {
            Ok(engine) => engine,
            Err(error) => {
                eprintln!("{error}");
                return 2;
            }
        };
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("runtime");
        let mut check = Check { engine, checks: 0, failures: 0 };
        runtime.block_on(providers(&mut check));
        let _ = std::fs::remove_dir_all(&folder);
        println!("RESULT {}/{} passed", check.checks - check.failures, check.checks);
        return i32::from(check.failures > 0);
    }
    let (Some(mode), Some(session), Some(suffix)) = (args.first(), args.get(1), args.get(2)) else {
        eprintln!("usage: --live-check|--live-session|--live-host <qa-session.json> <run> [dir]");
        return 2;
    };
    // SAFETY of the setting: done before any thread exists; the engine reads it at sign-in.
    unsafe_env(session);
    let folder = args.get(3).map(PathBuf::from).unwrap_or_else(|| std::env::temp_dir().join(format!("mixel-linux-live-{suffix}")));
    let _ = std::fs::create_dir_all(&folder);
    let engine = match RealEngine::new(folder.join("engine")) {
        Ok(engine) => engine,
        Err(error) => {
            eprintln!("{error}");
            return 2;
        }
    };
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().expect("runtime");
    let mut check = Check { engine, checks: 0, failures: 0 };
    runtime.block_on(async {
        match mode.as_str() {
            "--live-session" => technician(&mut check, suffix, &folder, false).await,
            "--live-host" => technician(&mut check, suffix, &folder, true).await,
            "--live-watch" => watch(&mut check, suffix, &folder).await,
            _ => checks(&mut check, suffix).await,
        }
        let _ = check.engine.shutdown().await;
    });
    println!("RESULT {}/{} passed", check.checks - check.failures, check.checks);
    i32::from(check.failures > 0)
}

#[allow(unsafe_code)]
fn unsafe_env(session: &str) {
    // SAFETY: called first thing in a single-threaded process (no other thread reads the environment).
    unsafe { std::env::set_var("MIXEL_QA_SESSION_FILE", session) };
}

async fn technician(check: &mut Check, suffix: &str, folder: &std::path::Path, attended: bool) {
    if !check.sign_in().await {
        return;
    }
    let company = format!("QA Linux {suffix}");
    let tenant = check.call("saas_signup", json!({ "company": company, "terms": true })).await.ok().and_then(|value| value["tenant_id"].as_str().map(str::to_owned)).unwrap_or_default();
    check.check("QA company", !tenant.is_empty(), "");
    if tenant.is_empty() {
        return;
    }
    let _ = std::fs::write(folder.join("tenant.txt"), &tenant);
    let added = check.call("saas_add_this_device", json!({ "tenant": tenant, "name": format!("QA Linux technician {suffix}"), "group": null })).await.unwrap_or(Value::Null);
    let mine = added["connection_id"].as_u64().unwrap_or(0);
    check.check("this computer added", mine > 0, &added);
    let _ = check.call("remote_register", json!({})).await;
    let ready = check.wait(30, |status| status["phase"] == "ready").await;
    check.check("this computer online", ready.is_some(), "");
    let mut host = 0u64;
    if attended {
        println!("WAITING for the host's Remote ID in {}/host-id.txt", folder.display());
        for _ in 0..450 {
            if let Some(id) = std::fs::read_to_string(folder.join("host-id.txt")).ok().and_then(|text| text.trim().parse::<u64>().ok()) {
                let _ = check.call("remote_presence", json!({ "hosts": [id] })).await;
                tokio::time::sleep(Duration::from_secs(1)).await;
                if check.status().await["online"].as_array().is_some_and(|online| online.iter().any(|item| item.as_u64() == Some(id))) {
                    host = id;
                    break;
                }
            }
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
        check.check("host computer (the Linux app on the same account) online", host > 0, "");
    } else {
        let link = check.call("saas_create_deploy_link", json!({ "tenant": tenant, "label": "QA lab", "days": 1, "computers": 1, "group": null })).await.unwrap_or(Value::Null);
        let command = link["windows_command"].as_str().unwrap_or_default().to_owned();
        check.check("deploy link for the lab computer", !command.is_empty(), "");
        let _ = std::fs::write(folder.join("deploy-command.txt"), &command);
        println!("WAITING for the lab computer (deploy-command.txt)");
        for _ in 0..180 {
            if let Ok(rows) = check.call("saas_devices", json!({ "tenant": tenant })).await
                && let Some(id) = rows.as_array().and_then(|rows| rows.iter().find(|row| row["os"] == "windows")).and_then(|row| row["connection_id"].as_u64())
            {
                let _ = check.call("remote_presence", json!({ "hosts": [id] })).await;
                tokio::time::sleep(Duration::from_secs(1)).await;
                if check.status().await["online"].as_array().is_some_and(|online| online.iter().any(|item| item.as_u64() == Some(id))) {
                    host = id;
                    break;
                }
            }
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
        check.check("lab computer enrolled with the deploy link and online", host > 0, "");
    }
    if host > 0 {
        let request = check.call("remote_request", json!({ "host": host, "password": null, "unattended": !attended })).await;
        check.check(if attended { "attended request" } else { "unattended request" }, request.is_ok(), format!("{request:?}"));
        if attended {
            println!("WAITING for the person at the host to allow the session");
        }
        let active = check.wait(if attended { 240 } else { 60 }, |status| status["phase"] == "active").await;
        check.check("session active", active.is_some(), check.status().await);
        if let Some(active) = active {
            check.check("view and control granted", active["control"] == true, &active);
            println!("host actions: {}  displays: {}  features: {}", active["host_actions"], active["displays"], active["peer_features"]);
            check.session_tools(&active, Some(&folder.to_path_buf()), attended).await;
        }
    }
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    if let Some(device) = added["device_id"].as_str() {
        let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": mine, "generation": added["generation"] } })).await;
        check.check("this computer removes itself", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
    }
    let deleted = check.call("saas_delete_company", json!({ "tenant": tenant, "name": company })).await;
    check.check("QA company deleted (the other computer's enrolment with it)", deleted.is_ok(), format!("{deleted:?}"));
}

async fn checks(check: &mut Check, suffix: &str) {
    let info = check.engine.info();
    check.check(&format!("engine starts ({}, {})", info.version, info.platform), info.platform == "linux", "");
    if !check.sign_in().await {
        return;
    }
    let company = format!("QA Linux {suffix}");
    let signup = check.call("saas_signup", json!({ "company": company, "terms": true })).await;
    let tenant = signup.as_ref().ok().and_then(|value| value["tenant_id"].as_str().map(str::to_owned)).unwrap_or_default();
    check.check("create a company (14-day trial, terms accepted)", !tenant.is_empty(), format!("{signup:?}"));
    if tenant.is_empty() {
        return;
    }
    let tenants = check.call("saas_my_tenants", json!({})).await.unwrap_or(Value::Null);
    check.check("my companies lists it as owner", tenants.as_array().is_some_and(|rows| rows.iter().any(|row| row["tenant_id"] == tenant.as_str() && row["role"] == "owner")), &tenants);
    let overview = check.call("saas_tenant_overview", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
    check.check("overview: trial status and limits", overview["status"] == "trial" && !overview["limits"].is_null(), &overview);
    let terms = check.call("saas_terms_status", json!({ "tenant": tenant })).await;
    check.check("terms status recorded", terms.is_ok(), format!("{terms:?}"));
    let added = check.call("saas_add_this_device", json!({ "tenant": tenant, "name": format!("QA Linux {suffix}"), "group": null })).await.unwrap_or(Value::Null);
    let connection = added["connection_id"].as_u64().unwrap_or(0);
    check.check("add this computer (its own key in the Secret Service)", connection > 100_000_000, &added);
    let registered = check.call("remote_register", json!({})).await;
    check.check("go online", registered.is_ok(), format!("{registered:?}"));
    let ready = check.wait(30, |status| status["phase"] == "ready").await;
    check.check("connection service: ready with this computer's ID", ready.as_ref().is_some_and(|status| status["host"].as_u64() == Some(connection)), format!("{ready:?}"));
    let devices = check.call("saas_devices", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
    check.check("company devices list this computer", devices.as_array().is_some_and(|rows| rows.iter().any(|row| row["connection_id"].as_u64() == Some(connection))), &devices);
    let directory = check.call("remote_devices", json!({ "search": "" })).await;
    check.check("device directory answers", directory.is_ok(), format!("{directory:?}"));
    let _ = check.call("remote_presence", json!({ "hosts": [connection] })).await;
    let presence = check.call("saas_device_presence", json!({ "tenant": tenant })).await;
    check.check("connection service record per device", presence.is_ok(), format!("{presence:?}"));
    let group = check.call("saas_create_group", json!({ "tenant": tenant, "name": "QA group" })).await.ok().and_then(|value| value.as_str().map(str::to_owned)).unwrap_or_default();
    check.check("create a group", !group.is_empty(), "");
    let device = added["device_id"].as_str().unwrap_or_default().to_owned();
    let renamed = check.call("saas_rename_device", json!({ "tenant": tenant, "device": device, "name": "QA Linux renamed" })).await;
    check.check("rename the device", renamed.as_ref().is_ok_and(|value| value == "QA Linux renamed"), format!("{renamed:?}"));
    let moved = check.call("saas_move_device", json!({ "tenant": tenant, "device": device, "group": group })).await;
    check.check("move the device to the group", moved.as_ref().is_ok_and(|value| value == true), format!("{moved:?}"));
    let invite = check.call("saas_invite_member", json!({ "tenant": tenant, "email": format!("qa-linux-{suffix}@example.com"), "role": "technician", "locale": "de" })).await;
    let invite_id = invite.as_ref().ok().and_then(|value| value.as_str().map(str::to_owned)).unwrap_or_default();
    check.check("invite a technician", !invite_id.is_empty(), format!("{invite:?}"));
    let invites = check.call("saas_invites", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
    check.check("open invitations list it", invites.as_array().is_some_and(|rows| rows.iter().any(|row| row["id"] == invite_id.as_str())), &invites);
    // The service lets an invitation be sent again after a minute.
    tokio::time::sleep(Duration::from_secs(62)).await;
    let resent = check.call("saas_resend_invite", json!({ "tenant": tenant, "invite": invite_id, "locale": "fr" })).await;
    check.check("send the invitation again", resent.is_ok(), format!("{resent:?}"));
    let invites = check.call("saas_invites", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
    let current = invites.as_array().and_then(|rows| rows.first()).and_then(|row| row["id"].as_str()).unwrap_or(&invite_id).to_owned();
    let revoked = check.call("saas_revoke_invite", json!({ "tenant": tenant, "invite": current })).await;
    check.check("revoke the invitation", revoked.as_ref().is_ok_and(|value| value == true), format!("{revoked:?}"));
    let members = check.call("saas_members", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
    check.check("members: the owner", members.as_array().is_some_and(|rows| rows.iter().any(|row| row["role"] == "owner")), &members);
    let renamed_company = check.call("saas_rename_tenant", json!({ "tenant": tenant, "name": format!("{company} renamed") })).await;
    check.check("rename the company", renamed_company.is_ok(), format!("{renamed_company:?}"));
    let link = check.call("saas_create_deploy_link", json!({ "tenant": tenant, "label": "QA", "days": 1, "computers": 1, "group": null })).await.unwrap_or(Value::Null);
    check.check(
        "deploy link with Windows, Mac and Linux commands",
        link["linux_command"].as_str().is_some_and(|text| text.contains("remote.mixel.ch/install/linux.sh")) && link["windows_command"].as_str().is_some_and(|text| text.contains("--signin-enroll")),
        &link,
    );
    let links = check.call("saas_deploy_links", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
    if let Some(id) = links.as_array().and_then(|rows| rows.first()).and_then(|row| row["id"].as_str()) {
        let revoke = check.call("saas_revoke_deploy_link", json!({ "tenant": tenant, "link": id })).await;
        check.check("revoke the deploy link", revoke.as_ref().is_ok_and(|value| value == true), format!("{revoke:?}"));
    } else {
        check.check("deploy links", false, &links);
    }
    let room = check.call("meeting_create", json!({})).await;
    check.check("Mixel Meet room for a session", room.as_ref().is_ok_and(|value| value["invite"].is_string()), format!("{room:?}"));
    let code = check.call("remote_support_code", json!({ "required": true })).await;
    let shown = check.wait(10, |status| !status["support_code"].is_null()).await;
    check.check("one-time password for this computer", code.is_ok() && shown.is_some(), format!("{code:?}"));
    let _ = check.call("remote_support_code", json!({ "required": false })).await;
    let printing = check.call("remote_printing_status", json!({})).await;
    check.check("remote printing status (CUPS)", printing.is_ok(), format!("{printing:?}"));
    let legal = check.call("legal_url", json!({ "page": "privacy", "lang": "it" })).await;
    check.check("legal page address", legal.as_ref().is_ok_and(|value| value == "https://remote.mixel.ch/legal/privacy?lang=it"), format!("{legal:?}"));
    let update = check.call("update_check", json!({})).await;
    println!("update check: {update:?}");
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": connection, "generation": added["generation"] } })).await;
    check.check("this computer removes itself (signed with its key)", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
    let deleted = check.call("saas_delete_company", json!({ "tenant": tenant, "name": format!("{company} renamed") })).await;
    check.check("delete the QA company", deleted.is_ok(), format!("{deleted:?}"));
    let left = check.call("saas_my_tenants", json!({})).await.unwrap_or(Value::Null);
    check.check("no company left for the QA account", left.as_array().is_none_or(Vec::is_empty), &left);
}

async fn watch(check: &mut Check, suffix: &str, folder: &std::path::Path) {
    if !check.sign_in().await {
        return;
    }
    let company = format!("QA Linux {suffix}");
    let tenant = check.call("saas_signup", json!({ "company": company, "terms": true })).await.ok().and_then(|value| value["tenant_id"].as_str().map(str::to_owned)).unwrap_or_default();
    check.check("QA company", !tenant.is_empty(), "");
    if tenant.is_empty() {
        return;
    }
    let added = check.call("saas_add_this_device", json!({ "tenant": tenant, "name": format!("QA Linux watcher {suffix}"), "group": null })).await.unwrap_or(Value::Null);
    let mine = added["connection_id"].as_u64().unwrap_or(0);
    let _ = check.call("remote_register", json!({})).await;
    check.check("watcher online", check.wait(30, |status| status["phase"] == "ready").await.is_some(), "");
    let link = check.call("saas_create_deploy_link", json!({ "tenant": tenant, "label": "QA update test", "days": 1, "computers": 5, "group": null })).await.unwrap_or(Value::Null);
    let token = link["token"].as_str().unwrap_or_default().to_owned();
    check.check("deploy link for the lab computers", !token.is_empty(), "");
    let _ = std::fs::write(folder.join("deploy-token.txt"), &token);
    let _ = std::fs::write(folder.join("deploy-command.txt"), link["windows_command"].as_str().unwrap_or_default());
    println!("WATCHING (deploy-token.txt, devices.txt, request.txt, stop)");
    for _ in 0..1800 {
        if folder.join("stop").exists() {
            break;
        }
        let rows = check.call("saas_devices", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
        let ids: Vec<u64> = rows.as_array().map(|rows| rows.iter().filter_map(|row| row["connection_id"].as_u64()).filter(|id| *id != mine).collect()).unwrap_or_default();
        if !ids.is_empty() {
            let _ = check.call("remote_presence", json!({ "hosts": ids })).await;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
        let online: Vec<u64> = check.status().await["online"].as_array().map(|list| list.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
        let lines: Vec<String> = rows
            .as_array()
            .map(|rows| rows.iter().filter(|row| row["connection_id"].as_u64() != Some(mine)).map(|row| format!("{} {} {} {}", row["connection_id"], row["os"].as_str().unwrap_or("?"), if row["connection_id"].as_u64().is_some_and(|id| online.contains(&id)) { "online" } else { "offline" }, row["name"].as_str().unwrap_or(""))).collect())
            .unwrap_or_default();
        let _ = std::fs::write(folder.join("devices.txt"), lines.join("\n") + "\n");
        if let Some(id) = std::fs::read_to_string(folder.join("request.txt")).ok().and_then(|text| text.trim().parse::<u64>().ok()) {
            let _ = std::fs::remove_file(folder.join("request.txt"));
            let request = check.call("remote_request", json!({ "host": id, "password": null, "unattended": true })).await;
            check.check(&format!("unattended request to {id}"), request.is_ok(), format!("{request:?}"));
            if let Some(active) = check.wait(60, |status| status["phase"] == "active").await {
                check.check("session active", true, "");
                println!("host actions: {}  features: {}", active["host_actions"], active["peer_features"]);
                check.session_tools(&active, Some(&folder.to_path_buf()), false).await;
            } else {
                check.check("session active", false, check.status().await);
            }
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    if let Some(device) = added["device_id"].as_str() {
        let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": mine, "generation": added["generation"] } })).await;
        check.check("watcher removes itself", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
    }
    let deleted = check.call("saas_delete_company", json!({ "tenant": tenant, "name": company })).await;
    check.check("QA company deleted (the lab computers' enrolments with it)", deleted.is_ok(), format!("{deleted:?}"));
}
