//! LAB builds only (feature `qa-session`; release builds contain none of this): one live session
//! as technician, the same on every system, run by the app's own engine through the command
//! `qa_live_technician` (Swift and Kotlin through `mixel_core_ffi`, Windows through `mixel_core.dll`,
//! the GTK app directly). A throwaway QA account's session file signs in; never a real person's.
//!
//! Arguments: `suffix` (run name), `folder` (hand-shake files with the lab script and `live.txt`,
//! the result), `session_file` (the QA session; without it the app's own lab sign-in is used),
//! `delete_company` (the last technician of a run deletes the QA company).
//!
//! The technician adds this device to the QA account's company (creating it when there is none and
//! writing `tenant.txt`), goes online, waits for the host's Remote ID in `host-id.txt`, asks for an
//! attended session and then, the moment the session is active and before any picture:
//! a key release and a clipboard push (the early case that used to end the session). The session
//! must survive; then pictures, input, the clipboard both ways (`clip-pushed.txt` out,
//! `clip-received.txt` and `clip-host.txt` in: the lab script reads and sets the host's clipboard),
//! chat, drawing, and the session is ended. Finally this device removes itself.
//!
//! `qa_live_host` is the other side on a computer that can be helped (never on a phone): it adds
//! this computer, goes online, writes `host-id.txt`, and when the technician asks it allows the
//! session as the person there would (whole display, control when this computer may inject input,
//! clipboard, chat). It then reports what its own clipboard received (`clip-received.txt`), copies
//! a text for the technician (`clip-host.txt`; the lab script carries both files over), answers in
//! the chat, and waits for the technician to end the session.
use crate::{
    native_commands::{Context, dispatch},
    remote_session::InputEvent,
    session_platform,
};
use serde_json::{Value, json};
use std::{
    io::Write,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

struct Check<'a> {
    context: &'a Context,
    folder: PathBuf,
    checks: u32,
    failures: u32,
}

impl Check<'_> {
    fn print(&self, line: &str) {
        if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(self.folder.join("live.txt")) {
            let _ = writeln!(file, "{line}");
        }
    }
    fn check(&mut self, label: &str, ok: bool, detail: impl std::fmt::Display) {
        self.checks += 1;
        if ok {
            self.print(&format!("PASS {label}"));
        } else {
            self.failures += 1;
            self.print(&format!("FAIL {label} {detail}"));
        }
    }
    async fn call(&self, command: &str, args: Value) -> Result<Value, String> {
        let text = dispatch(self.context, command, &args.to_string()).await?;
        serde_json::from_str(&text).map_err(|_| "The result is not JSON".to_owned())
    }
    async fn status(&self) -> Value {
        serde_json::to_value(self.context.remote.status().await).unwrap_or(Value::Null)
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
    async fn input(&self, session: uuid::Uuid, event: InputEvent) -> Result<bool, String> {
        self.context.remote.input(session, event).await
    }
    async fn picture(&self, session: uuid::Uuid) -> bool {
        self.context.remote.next_video(session, false).await.is_ok_and(|data| !data.is_empty())
    }
    /// Pixels close to the drawing red in the next picture ("MRF2": 32 bytes, then RGBA), if one waits.
    async fn red_pixels(&self, session: uuid::Uuid) -> Option<usize> {
        let data = self.context.remote.next_video(session, false).await.ok().filter(|data| data.len() > 32 && data.starts_with(b"MRF2"))?;
        Some(data[32..].as_chunks::<4>().0.iter().filter(|pixel| pixel[0] > 190 && pixel[1] < 90 && pixel[2] < 90).count())
    }
    /// A hand-shake file of the lab script, within `seconds`.
    async fn file(&self, name: &str, seconds: u64) -> Option<String> {
        file(&self.folder.join(name), seconds).await
    }
}

async fn file(path: &Path, seconds: u64) -> Option<String> {
    for _ in 0..seconds * 2 {
        if let Ok(text) = std::fs::read_to_string(path) {
            return Some(text.trim_end_matches(['\r', '\n']).to_owned());
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    None
}

fn clipboard_now() -> String {
    session_platform::read_text().map(|text| text.to_string()).unwrap_or_default()
}

/// Runs the session; the result lists what passed (`live.txt` in `folder` has every line).
pub async fn technician(context: &Context, suffix: String, folder: PathBuf, session_file: Option<PathBuf>, delete_company: bool) -> Result<Value, String> {
    std::fs::create_dir_all(&folder).map_err(|_| "The lab folder is unavailable")?;
    let mut check = Check { context, folder, checks: 0, failures: 0 };
    check.print(&format!("engine {} on {}", crate::agent::APP_VERSION, crate::native_commands::PLATFORM));
    run(&mut check, &suffix, session_file, delete_company).await;
    let line = format!("RESULT {}/{} passed", check.checks - check.failures, check.checks);
    check.print(&line);
    Ok(json!({ "passed": check.checks - check.failures, "checks": check.checks, "result": line }))
}

async fn sign_in(check: &mut Check<'_>, session_file: Option<PathBuf>) -> bool {
    let begun = match session_file {
        Some(path) => check.context.auth.begin_qa(path.into_os_string()).await,
        None => check.call("account_sign_in", json!({})).await.map(|_| ()),
    };
    if let Err(error) = begun {
        check.check("sign-in with the QA session", false, error);
        return false;
    }
    for _ in 0..80 {
        if let Ok(status) = check.call("account_status", json!({})).await {
            let phase = status["phase"].as_str().unwrap_or_default().to_owned();
            if matches!(phase.as_str(), "authenticated" | "profile_setup" | "error") {
                check.check("sign-in with the QA session", phase != "error", &status["message"]);
                return phase != "error";
            }
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    check.check("sign-in with the QA session", false, "timeout");
    false
}

async fn run(check: &mut Check<'_>, suffix: &str, session_file: Option<PathBuf>, delete_company: bool) {
    let platform = crate::native_commands::PLATFORM;
    if !sign_in(check, session_file.clone()).await {
        return;
    }
    // The QA account's company: the first technician of a run creates it, the others find it.
    let tenants = check.call("saas_my_tenants", json!({})).await.unwrap_or(Value::Null);
    let (mut tenant, mut company) = tenants
        .as_array()
        .and_then(|rows| rows.first())
        .map(|row| (row["tenant_id"].as_str().unwrap_or_default().to_owned(), row["name"].as_str().unwrap_or_default().to_owned()))
        .unwrap_or_default();
    if tenant.is_empty() {
        company = format!("QA Live {suffix}");
        tenant = check.call("saas_signup", json!({ "company": company, "terms": true })).await.ok().and_then(|value| value["tenant_id"].as_str().map(str::to_owned)).unwrap_or_default();
    }
    check.check("QA company", !tenant.is_empty(), &tenants);
    if tenant.is_empty() {
        return;
    }
    let _ = std::fs::write(check.folder.join("tenant.txt"), &tenant);
    // A new account's profile is complete once it has a company: read the sign-in again.
    for _ in 0..20 {
        if check.call("account_status", json!({})).await.is_ok_and(|status| status["phase"] == "authenticated") {
            break;
        }
        let _ = match &session_file {
            Some(path) => check.context.auth.begin_qa(path.clone().into_os_string()).await,
            None => check.call("account_sign_in", json!({})).await.map(|_| ()),
        };
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
    let added = check.call("saas_add_this_device", json!({ "tenant": tenant, "name": format!("QA {platform} technician {suffix}"), "group": null })).await;
    let mine = added.as_ref().ok().and_then(|value| value["connection_id"].as_u64()).unwrap_or(0);
    check.check("this device added to the company (its own new key)", mine > 0, format!("{added:?}"));
    let added = added.unwrap_or(Value::Null);
    if mine > 0 {
        session(check, suffix, &tenant, mine).await;
    }
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    if let Some(device) = added["device_id"].as_str() {
        let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": mine, "generation": added["generation"] } })).await;
        check.check("this device removes itself (signed with its key)", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
    }
    if delete_company {
        let deleted = check.call("saas_delete_company", json!({ "tenant": tenant, "name": company })).await;
        check.check("QA company deleted (the host's enrolment with it)", deleted.is_ok(), format!("{deleted:?}"));
    }
}

async fn session(check: &mut Check<'_>, suffix: &str, tenant: &str, mine: u64) {
    // The apps ask to go online twice (by themselves and after "Add this computer"): both succeed.
    let first = check.call("remote_register", json!({})).await;
    let second = check.call("remote_register", json!({})).await;
    check.check("go online, and asking again while connecting is not an error", first.is_ok() && second.is_ok(), format!("{first:?} {second:?}"));
    let ready = check.wait(40, |status| status["phase"] == "ready").await;
    check.check("this device online", ready.is_some(), check.status().await["message"].clone());
    let again = check.call("remote_register", json!({})).await;
    check.check("asking to go online while online is not an error", again.is_ok(), format!("{again:?}"));
    // The host: the Remote ID in `host-id.txt`, or else the company's only other computer that is online.
    check.print("WAITING for the host (host-id.txt, or the company's other computer online)");
    let mut host = 0u64;
    for _ in 0..450 {
        let given = std::fs::read_to_string(check.folder.join("host-id.txt")).ok().and_then(|text| text.trim().parse::<u64>().ok());
        let candidates: Vec<u64> = match given {
            Some(id) => vec![id],
            None => check
                .call("saas_devices", json!({ "tenant": tenant }))
                .await
                .ok()
                .and_then(|rows| rows.as_array().map(|rows| rows.iter().filter_map(|row| row["connection_id"].as_u64()).filter(|id| *id != mine).collect()))
                .unwrap_or_default(),
        };
        if !candidates.is_empty() {
            let _ = check.call("remote_presence", json!({ "hosts": candidates })).await;
            tokio::time::sleep(Duration::from_secs(1)).await;
            let online: Vec<u64> = check.status().await["online"].as_array().map(|list| list.iter().filter_map(Value::as_u64).filter(|id| candidates.contains(id)).collect()).unwrap_or_default();
            if online.len() == 1 {
                host = online[0];
                break;
            }
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
    check.check(&format!("host computer online ({host})"), host > 0, "");
    if host == 0 {
        return;
    }
    let request = check.call("remote_request", json!({ "host": host, "password": null, "unattended": false })).await;
    check.check("attended request", request.is_ok(), format!("{request:?}"));
    check.print("WAITING for the person at the host to allow the session");
    let Some(active) = check.wait(300, |status| status["phase"] == "active").await else {
        check.check("session active", false, check.status().await);
        return;
    };
    let text = active["session"].as_str().unwrap_or_default().to_owned();
    let Ok(id) = text.parse::<uuid::Uuid>() else {
        check.check("session active", false, &active);
        return;
    };
    // 1. At once, before any picture: a key release, then the clipboard (the host is still starting).
    // (A host that may not inject input allows viewing only: the input steps are then left out.)
    let control = active["control"] == true;
    let started = Instant::now();
    let release = if control { check.input(id, InputEvent::Key { usage: 225, down: false }).await } else { Err("view only".into()) };
    let release_ms = started.elapsed().as_millis();
    let early = format!("Mixel early clipboard {suffix} äöü");
    let copied = session_platform::write_text(&early);
    let push = check.call("remote_clipboard", json!({ "session": text, "push": true })).await;
    let push_ms = started.elapsed().as_millis();
    let picture_before = check.picture(id).await;
    check.check("session active", true, "");
    check.print(&format!("early: key release {release:?} after {release_ms} ms; clipboard push {push:?} after {push_ms} ms (copied {copied:?}, picture already there: {picture_before})"));
    check.print(&format!("granted: control {} clipboard {} files {}; host actions {}; features {}", active["control"], active["clipboard"], active["files"], active["host_actions"], active["peer_features"]));
    tokio::time::sleep(Duration::from_secs(3)).await;
    let status = check.status().await;
    check.check("the session survives the early key release and clipboard push", status["phase"] == "active" && status["session"] == active["session"], format!("{} {}", status["phase"], status["message"]));
    if active["clipboard"] == true {
        check.check("the early clipboard push itself succeeds", push.is_ok(), format!("{push:?}"));
    }
    // 2. Pictures.
    let (mut frames, waited) = (0, Instant::now());
    while frames < 3 && waited.elapsed() < Duration::from_secs(25) {
        if check.picture(id).await {
            frames += 1;
        } else {
            if waited.elapsed().as_millis() % 2000 < 40 {
                let _ = check.input(id, InputEvent::Pointer { x: 20000 + (waited.elapsed().as_millis() % 20000) as u16, y: 24000 }).await;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    }
    check.check(&format!("pictures from the host ({frames}, first after {} ms)", waited.elapsed().as_millis()), frames > 0, "");
    let now = check.status().await;
    if now["phase"] != "active" || now["session"] != active["session"] {
        check.check("the session is still running", false, format!("{} {}", now["phase"], now["message"]));
        return;
    }
    // 3. Input: pointer moves and Shift, each confirmed by the host.
    let mut posted = if control { 0 } else { 12 };
    if !control {
        check.print("input: the host allowed viewing only (it may not inject input); not sent");
    }
    // Every input that is not confirmed is listed with what came back and after how long.
    let mut unconfirmed = Vec::new();
    for step in 0..10u16 {
        let sent = Instant::now();
        let result = if control { check.input(id, InputEvent::Pointer { x: 20000 + step * 2000, y: 30000 }).await } else { Ok(false) };
        if result == Ok(true) {
            posted += 1;
        } else if control {
            unconfirmed.push(format!("pointer move {}: {result:?} after {} ms, sent at clock {}", step + 1, sent.elapsed().as_millis(), crate::remote_control::qa_wall_clock().saturating_sub(sent.elapsed().as_millis())));
        }
    }
    for down in [true, false] {
        let sent = Instant::now();
        let result = if control { check.input(id, InputEvent::Key { usage: 225, down }).await } else { Ok(false) };
        if result == Ok(true) {
            posted += 1;
        } else if control {
            unconfirmed.push(format!("Shift {}: {result:?} after {} ms", if down { "down" } else { "up" }, sent.elapsed().as_millis()));
        }
    }
    if !unconfirmed.is_empty() {
        check.print(&format!("inputs not confirmed: {}", unconfirmed.join("; ")));
    }
    if control {
        check.check(&format!("input applied on the host ({posted}/12)"), posted == 12, "");
    }
    // 4. The clipboard both ways, checked in each side's real clipboard.
    if active["clipboard"] == true {
        let ours = format!("Mixel {} technician to host {suffix} äöü €", crate::native_commands::PLATFORM);
        let set = session_platform::write_text(&ours).is_ok() && clipboard_now() == ours;
        check.check("text copied on the technician", set, clipboard_now());
        let push = check.call("remote_clipboard", json!({ "session": text, "push": true })).await;
        check.check("send the clipboard to the host", push.is_ok(), format!("{push:?}"));
        let _ = std::fs::write(check.folder.join("clip-pushed.txt"), &ours);
        check.print("WAITING for the host's clipboard (clip-received.txt, clip-host.txt)");
        let received = check.file("clip-received.txt", 180).await.unwrap_or_default();
        check.check("the technician's text arrives in the host's clipboard", received == ours, format!("host has {received:?}"));
        if let Some(theirs) = check.file("clip-host.txt", 180).await {
            let pull = check.call("remote_clipboard", json!({ "session": text, "push": false })).await;
            check.check("get the host's clipboard", pull.is_ok(), format!("{pull:?}"));
            tokio::time::sleep(Duration::from_millis(700)).await;
            let here = clipboard_now();
            check.check("text copied on the host arrives in the technician's clipboard", here == theirs, format!("{here:?} / {theirs:?}"));
        } else {
            check.check("host copied a text", false, "no clip-host.txt");
        }
    } else {
        check.check("clipboard granted by the host", false, &active["clipboard"]);
    }
    // 5. Chat.
    let message = format!("Mixel live check {suffix} from {}", crate::native_commands::PLATFORM);
    let sent = check.call("remote_chat_send", json!({ "session": text, "text": message })).await;
    let listed = check.call("remote_chat_messages", json!({ "session": text })).await.unwrap_or(Value::Null);
    check.check("chat message sent and in the conversation", sent.is_ok() && listed.to_string().contains(&message), format!("{sent:?} {listed}"));
    // The person at the host answers (the lab script types it there): `chat-expected.txt` has the text.
    if let Some(answer) = check.file("chat-expected.txt", 5).await {
        let mut seen = false;
        for _ in 0..120 {
            if check.call("remote_chat_messages", json!({ "session": text })).await.is_ok_and(|list| list.to_string().contains(&answer)) {
                seen = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        check.check("chat answer from the host arrives", seen, "");
    }
    // 6. Drawing on the host's screen.
    if active["peer_features"].as_array().is_some_and(|features| features.iter().any(|feature| feature == "draw")) {
        let stroke = json!({ "kind": "stroke", "id": 1, "tool": "arrow", "color": 0, "width": 2, "points": [[12000, 12000], [30000, 26000]], "done": true });
        // The red in the host's picture before the arrow, while it is shown, and after Clear.
        let mut before = 0;
        let settle = Instant::now();
        while settle.elapsed() < Duration::from_millis(1500) {
            match check.red_pixels(id).await {
                Some(count) => before = count,
                None => tokio::time::sleep(Duration::from_millis(30)).await,
            }
        }
        let (mut shown, mut with_arrow, mut after) = (0, before, None);
        for message in [stroke, json!({ "kind": "undo" }), json!({ "kind": "clear" })] {
            let arrow = message["kind"] == "stroke";
            if check.call("remote_draw", json!({ "session": text, "message": message })).await.is_ok_and(|value| value == true) {
                shown += 1;
            }
            if arrow {
                let _ = std::fs::write(check.folder.join("drawing.txt"), "1");
            }
            let pause = Instant::now();
            while pause.elapsed() < Duration::from_millis(if arrow { 5000 } else { 1200 }) {
                match check.red_pixels(id).await {
                    Some(count) if arrow => with_arrow = with_arrow.max(count),
                    Some(count) => after = Some(count),
                    None => tokio::time::sleep(Duration::from_millis(30)).await,
                }
            }
        }
        check.check(&format!("drawing on the host's screen: stroke, undo, clear ({shown}/3)"), shown == 3, "");
        let in_picture = active["peer_features"].as_array().is_some_and(|features| features.iter().any(|feature| feature == "drawpic"));
        check.print(&format!("drawing red in the host's picture: {before} pixels before, {with_arrow} with the arrow, {} after Clear", after.map_or("no new picture".to_owned(), |count| count.to_string())));
        // A host that puts the drawing into its picture (Linux X11) must show it there; the others
        // draw on their screen, which their picture shows as well unless the system keeps it out.
        if in_picture {
            check.check("the arrow appears in the host's picture and is gone after Clear", with_arrow > before + 200 && after.is_some_and(|count| count + 200 < with_arrow), "");
        }
    } else {
        check.check("drawing offered by the host", false, &active["peer_features"]);
    }
    let still = check.status().await;
    check.check("still the same session after all of it", still["phase"] == "active" && still["session"] == active["session"], format!("{} {}", still["phase"], still["message"]));
    // 7. End.
    if let Some(consent) = active["consent"].as_str() {
        let stop = check.call("remote_stop_session", json!({ "consent": consent })).await;
        check.check("end session", stop.is_ok(), format!("{stop:?}"));
    }
    check.check("back to ready after the session", check.wait(20, |status| status["phase"] == "ready").await.is_some(), check.status().await["message"].clone());
    check.print(&format!("shown after the session: {}", check.status().await["message"]));
}

/// The computer being helped; see the module text. `folder/live.txt` has every line.
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
pub async fn host(context: &Context, suffix: String, folder: PathBuf, session_file: Option<PathBuf>) -> Result<Value, String> {
    std::fs::create_dir_all(&folder).map_err(|_| "The lab folder is unavailable")?;
    let mut check = Check { context, folder, checks: 0, failures: 0 };
    let platform = crate::native_commands::PLATFORM;
    check.print(&format!("engine {} on {platform} (host)", crate::agent::APP_VERSION));
    host_run(&mut check, &suffix, session_file).await;
    let line = format!("RESULT {}/{} passed", check.checks - check.failures, check.checks);
    check.print(&line);
    Ok(json!({ "passed": check.checks - check.failures, "checks": check.checks, "result": line }))
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
async fn host_run(check: &mut Check<'_>, suffix: &str, session_file: Option<PathBuf>) {
    let platform = crate::native_commands::PLATFORM;
    if !sign_in(check, session_file).await {
        return;
    }
    // The technician of the run made the company.
    let tenant = check
        .call("saas_my_tenants", json!({}))
        .await
        .ok()
        .and_then(|rows| rows.as_array().and_then(|rows| rows.first()).and_then(|row| row["tenant_id"].as_str().map(str::to_owned)))
        .unwrap_or_default();
    check.check("QA company of the run", !tenant.is_empty(), "");
    if tenant.is_empty() {
        return;
    }
    let screen = check.call("screen_access", json!({})).await;
    check.check("this computer may show its screen (permission already there, never asked)", screen.as_ref().is_ok_and(|value| value == true), format!("{screen:?}"));
    if !screen.is_ok_and(|value| value == true) {
        return;
    }
    let added = check.call("saas_add_this_device", json!({ "tenant": tenant, "name": format!("QA {platform} host {suffix}"), "group": null })).await;
    let mine = added.as_ref().ok().and_then(|value| value["connection_id"].as_u64()).unwrap_or(0);
    check.check("this computer added to the company (its own key)", mine > 0, format!("{added:?}"));
    let added = added.unwrap_or(Value::Null);
    if mine > 0 {
        hosted(check, suffix, mine).await;
    }
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    if let Some(device) = added["device_id"].as_str() {
        let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": mine, "generation": added["generation"] } })).await;
        check.check("this computer removes itself (signed with its key)", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
    }
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
async fn hosted(check: &mut Check<'_>, suffix: &str, mine: u64) {
    let platform = crate::native_commands::PLATFORM;
    let first = check.call("remote_register", json!({})).await;
    let second = check.call("remote_register", json!({})).await;
    check.check("go online, and asking again while connecting is not an error", first.is_ok() && second.is_ok(), format!("{first:?} {second:?}"));
    let ready = check.wait(40, |status| status["phase"] == "ready").await;
    check.check(&format!("this computer online ({mine})"), ready.is_some(), check.status().await["message"].clone());
    let _ = std::fs::write(check.folder.join("host-id.txt"), mine.to_string());
    check.print("WAITING for a technician (host-id.txt)");
    let Some(incoming) = check.wait(900, |status| status["phase"] == "incoming").await else {
        check.check("a technician asks to connect", false, check.status().await["phase"].clone());
        return;
    };
    check.check("a technician asks to connect", true, "");
    let targets = check.call("capture_targets", json!({})).await.unwrap_or(Value::Null);
    let source = targets.as_array().and_then(|list| list.iter().find(|target| target["full_display"] == true)).and_then(|target| target["id"].as_str()).unwrap_or_default().to_owned();
    let control = incoming["input_access"] == true;
    check.print(&format!("screen to share: {source}; keyboard and mouse may be allowed here: {control}"));
    let decision = json!({ "session": incoming["session"], "accept": true, "source": source, "confirm": true, "control": control, "clipboard": true, "files": false, "chat": true, "recording": false });
    let decided = check.call("remote_decide", json!({ "decision": decision })).await;
    let active = check.wait(60, |status| status["phase"] == "active" && status["sharing"] == true).await;
    check.check("allowed: this computer's screen is shared", decided.is_ok() && active.is_some(), format!("{decided:?} {}", check.status().await["message"]));
    let Some(active) = active else { return };
    let session = active["session"].as_str().unwrap_or_default().to_owned();
    // The technician's text arrives in this computer's clipboard; then a text is copied here.
    let (mut received, waited) = (String::new(), Instant::now());
    while waited.elapsed() < Duration::from_secs(150) && check.status().await["phase"] == "active" {
        let now = clipboard_now();
        if now.contains("technician to host") {
            received = now;
            break;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    check.check("the technician's text arrives in this computer's clipboard", !received.is_empty(), clipboard_now());
    let _ = std::fs::write(check.folder.join("clip-received.txt"), &received);
    let ours = format!("Mixel {platform} host to technician {suffix} ß Ω");
    let copied = session_platform::write_text(&ours).is_ok() && clipboard_now() == ours;
    check.check("a text copied on this computer", copied, clipboard_now());
    let _ = std::fs::write(check.folder.join("clip-host.txt"), &ours);
    // Chat: the technician's message is here, and an answer goes back.
    let mut seen = false;
    for _ in 0..180 {
        if check.call("remote_chat_messages", json!({ "session": session })).await.is_ok_and(|list| list.to_string().contains("Mixel live check")) {
            seen = true;
            break;
        }
        if check.status().await["phase"] != "active" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    check.check("the technician's chat message arrives", seen, "");
    let answer = check.call("remote_chat_send", json!({ "session": session, "text": format!("Answer from the {platform} host {suffix}") })).await;
    check.check("chat answer sent", answer.is_ok(), format!("{answer:?}"));
    check.print("WAITING for the technician to end the session");
    let ended = check.wait(300, |status| status["phase"] != "active").await;
    check.check("the technician ends the session; this computer is ready again", ended.is_some() && check.wait(20, |status| status["phase"] == "ready").await.is_some(), check.status().await["message"].clone());
    check.print(&format!("shown after the session: {}", check.status().await["message"]));
}
