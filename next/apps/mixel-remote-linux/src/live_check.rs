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
//! - `--live-pair <session.json> <run> <dir>`: as `--live-host`, on a computer with an X display and
//!   `xclip`, plus: text copied on each side arrives on the other (`<dir>/clip-*.txt` hand-shake with
//!   the lab script, which reads and sets the host's clipboard), a second technician (`--live-colleague`
//!   in `<dir>/colleague`) joins and leaves, and the sides are switched and switched back.
//! - `--live-colleague <session.json> <run> <dir>`: the second technician: adds this computer to the
//!   company in `<dir>/../tenant.txt`, writes its Remote ID to `<dir>/colleague-id.txt`, joins when
//!   invited (`joined.txt`), watches a few seconds, leaves (`left.txt`) and removes itself.
//! - `--live-early <session.json> <run> <dir> [rounds]`: as `--live-host` (with `xclip`), `rounds`
//!   sessions (default 5; before each the lab script restarts the host app and writes `<dir>/go-<n>.txt`) in which a key release and a clipboard push go out the moment the session
//!   is active, before any picture (the host is still starting); each session must stay up, show
//!   pictures and take input afterwards, and is then ended.
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
        if attended {
            self.session_extras(&id, active, folder).await;
        }
        if let Some(consent) = active["consent"].as_str() {
            let stop = self.call("remote_stop_session", json!({ "consent": consent })).await;
            self.check("end session", stop.is_ok(), format!("{stop:?}"));
        }
        let back = self.wait(20, |status| status["phase"] == "ready").await;
        self.check("back to ready after the session", back.is_some(), "");
    }
}

impl Check {
    /// The toolbar and tools of an attended session the person allowed with everything: privacy
    /// screen, input block, pointer, sound, clipboard both ways, a file offer and a recording.
    async fn session_extras(&mut self, id: &str, active: &Value, folder: Option<&PathBuf>) {
        let offered: Vec<String> = active["host_actions"].as_array().map(|list| list.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect()).unwrap_or_default();
        for (name, actions) in [("privacy screen on/off", vec!["privacy:on", "privacy:off"]), ("block input on/off", vec!["block:on", "block:off"]), ("pointer shown/hidden", vec!["pointer:32000,24000", "pointer:off"]), ("sound off/on", vec!["sound:off", "sound:on"])] {
            let key = actions[0].split(':').next().unwrap_or_default().to_owned();
            if !offered.contains(&key) {
                println!("{name}: not offered by this computer");
                continue;
            }
            let mut ok = true;
            for action in actions {
                ok &= self.engine.input(id, json!({ "kind": "action", "name": action })).await.is_ok();
                tokio::time::sleep(Duration::from_millis(1500)).await;
            }
            self.check(name, ok, "");
        }
        if active["peer_features"].as_array().is_some_and(|features| features.iter().any(|feature| feature == "draw")) {
            let stroke = json!({ "kind": "stroke", "id": 1, "tool": "arrow", "color": 0, "width": 2, "points": [[12000, 12000], [30000, 26000]], "done": true });
            let mut shown = 0;
            for message in [stroke, json!({ "kind": "undo" }), json!({ "kind": "clear" })] {
                let pause = if message["kind"] == "stroke" { 5000 } else { 800 };
                if self.call("remote_draw", json!({ "session": id, "message": message })).await.is_ok_and(|value| value == true) {
                    shown += 1;
                }
                // The arrow stays a few seconds (it can be seen on the other computer's screen).
                tokio::time::sleep(Duration::from_millis(pause)).await;
            }
            self.check(&format!("drawing on the other computer's screen: stroke, undo, clear ({shown}/3)"), shown == 3, "");
        } else {
            println!("drawing: not offered by this computer");
        }
        if active["clipboard"] == true {
            // Something to send: this computer's clipboard (needs a display; the host test has one).
            let copied = self.call("copy_text", json!({ "text": "Mixel Remote for Linux clipboard check" })).await;
            println!("local clipboard set: {}", copied.is_ok());
            let push = self.call("remote_clipboard", json!({ "session": id, "push": true })).await;
            self.check("clipboard to the other computer", push.is_ok(), format!("{push:?}"));
            let pull = self.call("remote_clipboard", json!({ "session": id, "push": false })).await;
            self.check("clipboard from the other computer", pull.is_ok(), format!("{pull:?}"));
        }
        if active["files"] == true
            && let Some(consent) = active["consent"].as_str()
            && let Some(folder) = folder
        {
            let file = folder.join("mixel-linux-file-check.txt");
            let _ = std::fs::write(&file, "Mixel Remote for Linux file check\n");
            let mut connected = false;
            for _ in 0..20 {
                if self.call("remote_file_status", json!({ "consent": consent })).await.is_ok_and(|view| view["connected"] == true) {
                    connected = true;
                    break;
                }
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
            let offer = self.call("remote_file_send", json!({ "consent": consent, "path": file.to_string_lossy() })).await;
            tokio::time::sleep(Duration::from_secs(2)).await;
            let view = self.call("remote_file_status", json!({ "consent": consent })).await.unwrap_or(Value::Null);
            self.check(&format!("file offered to the other computer ({})", view["outgoing"]["phase"]), connected && offer.is_ok(), format!("{offer:?} {view}"));
        }
        if active["recording_permitted"] == true || self.call("remote_record_may_start", json!({ "session": id })).await.is_ok_and(|value| value == true) {
            if let Some(folder) = folder {
                let path = folder.join("session.mp4");
                let start = self.call("remote_record_start", json!({ "session": id, "path": path.to_string_lossy() })).await;
                // Pictures to record: the overview opens and closes (Super key) while it records.
                for _ in 0..2 {
                    let _ = self.engine.input(id, json!({ "kind": "key", "usage": 227, "down": true })).await;
                    let _ = self.engine.input(id, json!({ "kind": "key", "usage": 227, "down": false })).await;
                    let until = std::time::Instant::now() + Duration::from_secs(3);
                    while std::time::Instant::now() < until {
                        if !matches!(self.engine.next_video(id).await, Ok(data) if !data.is_empty()) {
                            tokio::time::sleep(Duration::from_millis(20)).await;
                        }
                    }
                }
                let before = self.status().await["recording"].clone();
                let stop = self.call("remote_record_stop", json!({ "session": id })).await;
                // The file is written after the stop (phase saving → saved).
                let saved = self.wait(20, |status| matches!(status["recording"]["phase"].as_str(), Some("saved" | "saved_partial" | "error")) || matches!(status["previous_recording"]["phase"].as_str(), Some("saved" | "saved_partial" | "error"))).await;
                println!("recording before stop: {before}; after: {}", saved.map(|status| format!("{} {}", status["recording"], status["previous_recording"])).unwrap_or_default());
                let size = std::fs::metadata(&path).map(|meta| meta.len()).unwrap_or(0);
                self.check(&format!("recording saved ({size} bytes)"), start.is_ok() && stop.is_ok() && size > 0, format!("{start:?} {stop:?}"));
            }
        } else {
            println!("recording: not allowed in this session");
        }
    }
}

/// The X display's clipboard through `xclip` (another program, as when a person copies there).
fn xclip_set(text: &str) -> bool {
    use std::io::Write;
    let Ok(mut child) = std::process::Command::new("xclip").env("LC_ALL", "C.UTF-8").args(["-selection", "clipboard", "-i"]).stdin(std::process::Stdio::piped()).spawn() else { return false };
    let written = child.stdin.take().is_some_and(|mut input| input.write_all(text.as_bytes()).is_ok());
    child.wait().is_ok_and(|status| status.success()) && written
}

fn xclip_get() -> String {
    std::process::Command::new("xclip")
        .env("LC_ALL", "C.UTF-8")
        .args(["-selection", "clipboard", "-o"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).into_owned())
        .unwrap_or_default()
}

/// Waits up to `seconds` for a hand-shake file of the lab script and returns its text.
async fn file(path: &std::path::Path, seconds: u64) -> Option<String> {
    for _ in 0..seconds * 2 {
        if let Ok(text) = std::fs::read_to_string(path) {
            return Some(text.trim_end_matches('\n').to_owned());
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    None
}

impl Check {
    /// Clipboard both ways with real clipboards, a second technician, switching sides. Returns the
    /// session's status afterwards (switching back starts a new session).
    async fn pair(&mut self, active: Value, suffix: &str, folder: &std::path::Path) -> Value {
        let id = active["session"].as_str().unwrap_or_default().to_owned();
        let features = active["peer_features"].to_string();
        let offered = |name: &str| active["peer_features"].as_array().is_some_and(|list| list.iter().any(|item| item == name));
        // As a person would: the picture is there first, then the clipboard buttons are used.
        let (mut frames, started) = (0, std::time::Instant::now());
        while frames < 3 && started.elapsed() < Duration::from_secs(20) {
            match self.engine.next_video(&id).await {
                Ok(data) if !data.is_empty() => frames += 1,
                _ => tokio::time::sleep(Duration::from_millis(30)).await,
            }
        }
        let timed = std::time::Instant::now();
        let first = self.engine.input(&id, json!({ "kind": "pointer", "x": 30000, "y": 30000 })).await;
        println!("first pictures after {} ms ({frames}); first input {:?} in {} ms", started.elapsed().as_millis() - timed.elapsed().as_millis(), first, timed.elapsed().as_millis());
        // 1. Clipboard: copied here, arrives there; copied there, arrives here.
        if active["clipboard"] == true {
            let ours = format!("Mixel Linux technician to host {suffix} äöü €");
            let set = xclip_set(&ours) && xclip_get() == ours;
            self.check("technician's clipboard set by another program (xclip)", set, xclip_get());
            let push = self.call("remote_clipboard", json!({ "session": id, "push": true })).await;
            self.check("send the clipboard to the host", push.is_ok(), format!("{push:?}"));
            let _ = std::fs::write(folder.join("clip-pushed.txt"), &ours);
            println!("WAITING for the host's clipboard (clip-received.txt, clip-host.txt)");
            let received = file(&folder.join("clip-received.txt"), 120).await.unwrap_or_default();
            self.check("text copied on the technician arrives in the host's clipboard", received == ours, format!("host has {received:?}"));
            if let Some(theirs) = file(&folder.join("clip-host.txt"), 120).await {
                let pull = self.call("remote_clipboard", json!({ "session": id, "push": false })).await;
                self.check("get the host's clipboard", pull.is_ok(), format!("{pull:?}"));
                tokio::time::sleep(Duration::from_millis(500)).await;
                let here = xclip_get();
                self.check("text copied on the host arrives in the technician's clipboard", here == theirs, format!("{here:?} / {theirs:?}"));
            } else {
                self.check("host copied a text", false, "no clip-host.txt");
            }
        } else {
            self.check("clipboard granted by the host", false, &active);
        }
        // 2. A second technician joins (the person at the host lets them in) and leaves again.
        if offered("join") {
            let colleague = folder.join("colleague");
            println!("WAITING for the second technician (colleague/colleague-id.txt)");
            let device = file(&colleague.join("colleague-id.txt"), 300).await.and_then(|text| text.trim().parse::<u32>().ok());
            if let Some(device) = device {
                let invite = self.call("remote_invite", json!({ "session": id, "user": null, "device": device, "control": false })).await;
                self.check("invite a second technician by Remote ID", invite.is_ok(), format!("{invite:?}"));
                println!("WAITING for the person at the host to let the colleague join (colleague/joined.txt)");
                let joined = file(&colleague.join("joined.txt"), 240).await;
                self.check("second technician joined the running session", joined.is_some(), self.status().await["invite_message"].clone());
                let left = file(&colleague.join("left.txt"), 90).await;
                let still = self.status().await;
                self.check("second technician left; the first stays connected", left.is_some() && still["phase"] == "active" && still["session"] == active["session"], &still);
            } else {
                self.check("second technician online", false, "no colleague-id.txt");
            }
        } else {
            println!("second technician: not offered by the host ({features})");
        }
        // 3. Switch sides (the host views this computer's screen), then back.
        if offered("switch") {
            let switch = self.call("remote_switch_sides", json!({ "session": id })).await;
            self.check("switch sides requested", switch.is_ok(), format!("{switch:?}"));
            let switched = self.wait(60, |status| status["phase"] == "active" && status["switched"] == true).await;
            self.check("sides switched: the host views this computer", switched.as_ref().is_some_and(|status| status["sharing"] == true), self.status().await);
            let _ = std::fs::write(folder.join("switched.txt"), "1");
            tokio::time::sleep(Duration::from_secs(12)).await;
            if let Some(switched) = switched {
                let back = self.call("remote_switch_sides", json!({ "session": switched["session"] })).await;
                self.check("switch back requested", back.is_ok(), format!("{back:?}"));
                let again = self.wait(60, |status| status["phase"] == "active" && status["switched"] == false && status["sharing"] == false && status["control"] == true).await;
                self.check("switched back: this computer views and controls the host again", again.is_some(), self.status().await);
                let _ = std::fs::write(folder.join("switched-back.txt"), "1");
                if let Some(again) = again {
                    return again;
                }
            }
            return self.status().await;
        }
        println!("switch sides: not offered by the host ({features})");
        active
    }
}

/// The second technician of `--live-pair`.
async fn colleague(check: &mut Check, suffix: &str, folder: &std::path::Path) {
    if !check.sign_in().await {
        return;
    }
    let Some(tenant) = file(&folder.join("../tenant.txt"), 600).await else {
        check.check("company of the first technician", false, "no tenant.txt");
        return;
    };
    // A new account is still being set up (profile) when this second app signs in at the same
    // time; once the first technician created the company it is complete: read the sign-in again.
    for _ in 0..20 {
        if check.call("account_status", json!({})).await.is_ok_and(|status| status["phase"] == "authenticated") {
            break;
        }
        let _ = check.call("account_sign_in", json!({})).await;
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
    let added = check.call("saas_add_this_device", json!({ "tenant": tenant, "name": format!("QA Linux colleague {suffix}"), "group": null })).await;
    let added = match added {
        Ok(value) => value,
        Err(error) => {
            check.check("second technician's computer added", false, error);
            return;
        }
    };
    let mine = added["connection_id"].as_u64().unwrap_or(0);
    check.check("second technician's computer added", mine > 0, &added);
    let _ = check.call("remote_register", json!({})).await;
    check.check("second technician online", check.wait(30, |status| status["phase"] == "ready").await.is_some(), "");
    let _ = std::fs::write(folder.join("colleague-id.txt"), mine.to_string());
    println!("WAITING for the invitation");
    let invited = check.wait(600, |status| status["invitations"].as_array().is_some_and(|list| !list.is_empty())).await;
    let invitation = invited.as_ref().and_then(|status| status["invitations"][0]["invitation"].as_str().map(str::to_owned));
    check.check("invitation from the first technician", invitation.is_some(), invited.unwrap_or(Value::Null));
    if let Some(invitation) = invitation {
        let join = check.call("remote_join", json!({ "invitation": invitation })).await;
        check.check("join requested", join.is_ok(), format!("{join:?}"));
        // Every change of the phase and message until the session is active (or gone), with the time.
        let started = std::time::Instant::now();
        let (mut last, mut active) = (String::new(), None);
        while started.elapsed() < Duration::from_secs(240) {
            let status = check.status().await;
            let now = format!("{} | {}", status["phase"].as_str().unwrap_or("?"), status["message"].as_str().unwrap_or(""));
            if now != last {
                println!("join trace +{} ms: {now}", started.elapsed().as_millis());
                last = now;
            }
            if status["phase"] == "active" {
                active = Some(status);
                break;
            }
            if status["phase"] == "ready" && started.elapsed() > Duration::from_secs(5) && status["message"].as_str().is_some_and(|text| text.starts_with("Session closed")) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        check.check("joined: viewing the host", active.as_ref().is_some_and(|status| status["joined_session"] == true), check.status().await);
        if let Some(active) = active {
            let id = active["session"].as_str().unwrap_or_default().to_owned();
            let mut frames = 0;
            let until = std::time::Instant::now() + Duration::from_secs(10);
            while std::time::Instant::now() < until && frames < 3 {
                match check.engine.next_video(&id).await {
                    Ok(data) if !data.is_empty() => frames += 1,
                    _ => tokio::time::sleep(Duration::from_millis(50)).await,
                }
            }
            check.check(&format!("second technician gets pictures ({frames})"), frames > 0, "");
            let _ = std::fs::write(folder.join("joined.txt"), &id);
            tokio::time::sleep(Duration::from_secs(8)).await;
            let close = check.call("remote_close", json!({ "session": id })).await;
            check.check("second technician leaves", close.is_ok(), format!("{close:?}"));
            check.check("second technician back to ready", check.wait(20, |status| status["phase"] == "ready").await.is_some(), "");
            let _ = std::fs::write(folder.join("left.txt"), "1");
        }
    }
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    if let Some(device) = added["device_id"].as_str() {
        let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": mine, "generation": added["generation"] } })).await;
        check.check("second technician's computer removes itself", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
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
    let offered = check.call("account_providers", json!({})).await.unwrap_or(Value::Null);
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
            "--live-session" => technician(&mut check, suffix, &folder, false, false).await,
            "--live-host" => technician(&mut check, suffix, &folder, true, false).await,
            "--live-pair" => technician(&mut check, suffix, &folder, true, true).await,
            "--live-early" => {
                let rounds = args.get(4).and_then(|text| text.parse().ok()).unwrap_or(5);
                early(&mut check, suffix, &folder, rounds).await;
            }
            "--live-colleague" => colleague(&mut check, suffix, &folder).await,
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

/// `--live-early`: input and clipboard in the first moment of a session, several sessions.
async fn early(check: &mut Check, suffix: &str, folder: &std::path::Path, rounds: u32) {
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
    check.check("this computer online", check.wait(30, |status| status["phase"] == "ready").await.is_some(), "");
    println!("WAITING for the host's Remote ID in {}/host-id.txt", folder.display());
    let mut host = 0u64;
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
    check.check("host computer online", host > 0, "");
    for round in 1..=rounds.max(1) {
        if host == 0 {
            break;
        }
        // The lab script restarts the app at the host (its first session is the slow one), then says go.
        if file(&folder.join(format!("go-{round}.txt")), 300).await.is_none() {
            check.check(&format!("round {round}: host restarted"), false, "no go file");
            break;
        }
        let mut online = false;
        for _ in 0..30 {
            let _ = check.call("remote_presence", json!({ "hosts": [host] })).await;
            tokio::time::sleep(Duration::from_secs(1)).await;
            if check.status().await["online"].as_array().is_some_and(|list| list.iter().any(|item| item.as_u64() == Some(host))) {
                online = true;
                break;
            }
        }
        check.check(&format!("round {round}: host app restarted and online"), online, "");
        let request = check.call("remote_request", json!({ "host": host, "password": null, "unattended": false })).await;
        check.check(&format!("round {round}: attended request"), request.is_ok(), format!("{request:?}"));
        println!("WAITING for the person at the host to allow the session (round {round})");
        let Some(active) = check.wait(240, |status| status["phase"] == "active").await else {
            check.check(&format!("round {round}: session active"), false, check.status().await);
            break;
        };
        let id = active["session"].as_str().unwrap_or_default().to_owned();
        // At once, before any picture: a key release, then the clipboard (which releases held keys first).
        let started = std::time::Instant::now();
        let release = check.engine.input(&id, json!({ "kind": "key", "usage": 225, "down": false })).await;
        let release_ms = started.elapsed().as_millis();
        let ours = format!("Mixel early clipboard {suffix} round {round} äöü");
        let copied = xclip_set(&ours);
        let push = check.call("remote_clipboard", json!({ "session": id, "push": true })).await;
        let push_ms = started.elapsed().as_millis();
        let pictures_before = check.engine.next_video(&id).await.is_ok_and(|data| !data.is_empty());
        println!("round {round}: key release {release:?} after {release_ms} ms; clipboard push {push:?} after {push_ms} ms (copied {copied}, picture already there: {pictures_before})");
        let setup = std::fs::read_to_string(folder.join("host-setup.txt")).unwrap_or_default();
        if !setup.is_empty() {
            println!("round {round}: host {setup}");
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
        let status = check.status().await;
        check.check(&format!("round {round}: the session survives the early input and clipboard"), status["phase"] == "active" && status["session"] == active["session"], &status);
        let (mut frames, waited) = (0, std::time::Instant::now());
        while frames < 3 && waited.elapsed() < Duration::from_secs(20) {
            match check.engine.next_video(&id).await {
                Ok(data) if !data.is_empty() => frames += 1,
                _ => tokio::time::sleep(Duration::from_millis(30)).await,
            }
        }
        check.check(&format!("round {round}: pictures ({frames})"), frames > 0, "");
        let mut posted = 0;
        for step in 0..6u32 {
            if check.engine.input(&id, json!({ "kind": "pointer", "x": 20000 + step * 3000, "y": 30000 })).await == Ok(true) {
                posted += 1;
            }
        }
        check.check(&format!("round {round}: input confirmed afterwards ({posted}/6)"), posted == 6, "");
        let again = check.call("remote_clipboard", json!({ "session": id, "push": true })).await;
        check.check(&format!("round {round}: clipboard push afterwards"), again.is_ok(), format!("{again:?}"));
        if let Some(consent) = active["consent"].as_str() {
            let stop = check.call("remote_stop_session", json!({ "consent": consent })).await;
            check.check(&format!("round {round}: end session"), stop.is_ok(), format!("{stop:?}"));
        }
        check.check(&format!("round {round}: back to ready"), check.wait(20, |status| status["phase"] == "ready").await.is_some(), "");
        let _ = std::fs::write(folder.join(format!("round-{round}.txt")), "1");
        tokio::time::sleep(Duration::from_secs(4)).await;
    }
    let _ = std::fs::write(folder.join("early-done.txt"), "1");
    let _ = check.call("remote_disconnect", json!({})).await;
    let _ = check.wait(15, |status| matches!(status["phase"].as_str(), Some("offline" | "error"))).await;
    if let Some(device) = added["device_id"].as_str() {
        let removal = check.call("device_self_remove", json!({ "confirmed": true, "registration": { "device": device, "connection_id": mine, "generation": added["generation"] } })).await;
        check.check("this computer removes itself", removal.as_ref().is_ok_and(|value| value == "removed"), format!("{removal:?}"));
    }
    let deleted = check.call("saas_delete_company", json!({ "tenant": tenant, "name": company })).await;
    check.check("QA company deleted", deleted.is_ok(), format!("{deleted:?}"));
}

async fn technician(check: &mut Check, suffix: &str, folder: &std::path::Path, attended: bool, pair: bool) {
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
            let active = if pair { check.pair(active, suffix, folder).await } else { active };
            check.session_tools(&active, Some(&folder.to_path_buf()), attended).await;
        }
    }
    if host > 0 {
        // The session report lists the session; a note goes on it (by the Remote ID); favorites.
        tokio::time::sleep(Duration::from_secs(3)).await;
        let report = check.call("saas_session_report", json!({ "tenant": tenant, "from": null, "to": null, "technician": null, "device": null })).await.unwrap_or(Value::Null);
        let listed = report.as_array().is_some_and(|rows| rows.iter().any(|row| row["connection_id"].as_u64() == Some(host)));
        check.check("session report lists the session", listed, &report);
        let note = check.call("saas_session_note", json!({ "tenant": tenant, "note": "Linux live check note", "session": null, "connection": host })).await;
        let report = check.call("saas_session_report", json!({ "tenant": tenant, "from": null, "to": null, "technician": null, "device": null })).await.unwrap_or(Value::Null);
        check.check("session note saved and reported", note.is_ok() && report.to_string().contains("Linux live check note"), format!("{note:?}"));
        let device = check.call("saas_devices", json!({ "tenant": tenant })).await.ok().and_then(|rows| rows.as_array().and_then(|rows| rows.iter().find(|row| row["connection_id"].as_u64() == Some(host)).map(|row| row["device_id"].clone())));
        if let Some(device) = device {
            let starred = check.call("saas_set_favorite", json!({ "tenant": tenant, "device": device, "favorite": true })).await;
            let favorites = check.call("saas_favorites", json!({ "tenant": tenant })).await.unwrap_or(Value::Null);
            let recent = check.call("saas_recent_devices", json!({ "tenant": tenant, "max_count": 5 })).await.unwrap_or(Value::Null);
            check.check("favorite and recent connections", starred.is_ok() && favorites.to_string().contains(device.as_str().unwrap_or("-")) && recent.to_string().contains(device.as_str().unwrap_or("-")), format!("{favorites} {recent}"));
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
