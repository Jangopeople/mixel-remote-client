//! LAB ONLY: one live check of the `remote_admin_*` commands against the public service, through the
//! engine's own command dispatch, as a throwaway QA login (never a real person's).
//!
//! ```text
//! MIXEL_DESKTOP_SERVICE_CONFIG=deployment/public-service-config.json \
//!   cargo run -p mixel_engine --example admin_live_check -- <admin-session.json> <company name> <device name> [<other-session.json>]
//! ```
//! The session files come from the lab script (`qa-linux.py mint`). The check lists the devices, renames the
//! named device of the named company, sets a note and a favourite, and puts everything back. With a second
//! session (a login that is not in that company) it shows that its calls are refused. It never removes
//! anything and touches no device outside the named company; no token or secret is printed.
use mixel_engine::native_commands::{Context, dispatch};
use serde_json::{Value, json};
use std::time::Duration;

async fn call(context: &Context, command: &str, args: Value) -> Result<Value, String> {
    let text = dispatch(context, command, &args.to_string()).await?;
    serde_json::from_str(&text).map_err(|_| "unreadable answer".to_owned())
}

async fn sign_in(context: &Context, file: &str) -> Result<(), String> {
    context.auth.begin_qa(file.into()).await?;
    for _ in 0..100 {
        let status = call(context, "account_status", json!({})).await?;
        match status["phase"].as_str() {
            Some("authenticated") => return Ok(()),
            // A fresh QA login: the personal profile step the apps show first.
            Some("profile_setup") => {
                call(context, "account_setup", json!({"name": "QA Outsider"})).await?;
            }
            Some("failed" | "signed_out") => return Err(format!("sign-in failed: {}", status["message"])),
            _ => tokio::time::sleep(Duration::from_millis(300)).await,
        }
    }
    Err("sign-in timed out".into())
}

fn show(step: &str, result: &Result<Value, String>) {
    match result {
        Ok(value) => println!("OK   {step}: {value}"),
        Err(text) => println!("FAIL {step}: {text}"),
    }
}

fn hint(result: &Result<Value, String>) -> String {
    match result {
        Err(text) => serde_json::from_str::<Value>(text).ok().and_then(|v| v["hint"].as_str().map(str::to_owned)).unwrap_or_else(|| text.clone()),
        Ok(_) => "NOT REFUSED".into(),
    }
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 3 {
        eprintln!("usage: admin_live_check <admin-session.json> <company name> <device name> [<other-session.json>]");
        std::process::exit(2);
    }
    let (company, device_name) = (args[1].as_str(), args[2].as_str());
    let folder = std::env::temp_dir().join(format!("admin-live-check-{}", std::process::id()));
    let mut failures = 0;
    let mut check = |name: &str, ok: bool| {
        println!("{} {name}", if ok { "PASS" } else { "FAIL" });
        failures += u32::from(!ok);
    };

    let context = Context::new(folder.clone()).expect("data folder");
    sign_in(&context, &args[0]).await.expect("admin sign-in");
    let rows = call(&context, "remote_admin_devices", json!({})).await.expect("list");
    let rows = rows.as_array().cloned().unwrap_or_default();
    println!("the admin login sees {} device(s)", rows.len());
    for row in &rows {
        println!("  \"{}\" of \"{}\" ({}), can_admin {}", row["name"], row["company_name"], row["company_kind"], row["can_admin"]);
    }
    let ours: Vec<&Value> = rows.iter().filter(|row| row["company_name"] == company).collect();
    println!("{} of them in \"{company}\"", ours.len());
    let Some(target) = ours.iter().find(|row| row["name"] == device_name) else {
        println!("FAIL no device \"{device_name}\" in \"{company}\"; nothing was changed");
        std::process::exit(1);
    };
    let id = target["device_id"].as_str().expect("id").to_owned();
    let original = target["name"].as_str().expect("name").to_owned();
    let original_note = target["note"].clone();
    let original_favorite = target["favorite"].as_bool().unwrap_or(false);
    println!(
        "device {}: connection {}, status {}, version {}, owner {}, can_admin {}, unattended {}/{}",
        target["name"], target["connection_id"], target["status"], target["app_version"], target["owner_name"],
        target["can_admin"], target["unattended_setting"], target["unattended_allowed"]
    );
    check("the list says the admin may administer the device", target["can_admin"] == true);
    check("the list carries a connection ID, last seen or status, and the unattended fields", target["connection_id"].is_u64() && target["unattended_setting"].is_string());

    let temp = format!("{original} (admin check)");
    let renamed = call(&context, "remote_admin_rename_device", json!({"device": id, "device_name": temp, "expected_update": target["updated_at"]})).await;
    show("rename", &renamed);
    check("rename returns the device row with the new name", renamed.as_ref().is_ok_and(|row| row["name"] == temp.as_str()));
    let stale = call(&context, "remote_admin_rename_device", json!({"device": id, "device_name": "stale", "expected_update": target["updated_at"]})).await;
    check("a rename with the old expected_update is refused as changed", hint(&stale) == "changed");

    let noted = call(&context, "remote_admin_set_device_note", json!({"device": id, "note": "admin check note"})).await;
    show("note", &noted);
    check("note is stored and returned", noted.as_ref().is_ok_and(|row| row["note"] == "admin check note"));
    let favourite = call(&context, "remote_admin_set_device_favorite", json!({"device": id, "favorite": !original_favorite})).await;
    show("favourite", &favourite);
    check("favourite flips and returns the new state", favourite.as_ref().is_ok_and(|value| *value == json!(!original_favorite)));
    let listed = call(&context, "remote_admin_devices", json!({"search": "admin check"})).await;
    let found = listed.as_ref().ok().and_then(Value::as_array).and_then(|rows| rows.iter().find(|row| row["device_id"] == id.as_str()).cloned());
    check(
        "a new list shows name, note and favourite and the search finds it",
        found.as_ref().is_some_and(|row| row["name"] == temp.as_str() && row["note"] == "admin check note" && row["favorite"] == json!(!original_favorite)),
    );
    let bad = call(&context, "remote_admin_rename_device", json!({"device": id, "device_name": "   "})).await;
    check("an empty name is refused before sending", hint(&bad) == "invalid_name");
    let wrong_confirm = call(&context, "remote_admin_remove_device", json!({"device": id, "confirm_connection_id": 42})).await;
    check("remove with a non 9-digit confirmation is refused before sending", hint(&wrong_confirm) == "confirm_mismatch");
    let missing = call(&context, "remote_admin_rename_device", json!({"device": "00000000-0000-4000-8000-0000000000aa", "device_name": "x"})).await;
    println!("rename of an unknown device: {}", hint(&missing));
    check("an unknown device is device_not_found", hint(&missing) == "device_not_found");

    // Put everything back.
    let back = call(&context, "remote_admin_rename_device", json!({"device": id, "device_name": original})).await;
    show("rename back", &back);
    let note_back = call(&context, "remote_admin_set_device_note", json!({"device": id, "note": original_note})).await;
    let favourite_back = call(&context, "remote_admin_set_device_favorite", json!({"device": id, "favorite": original_favorite})).await;
    show("note and favourite back", &note_back.and(favourite_back));
    let after = call(&context, "remote_admin_devices", json!({})).await.unwrap_or(Value::Null);
    let after = after.as_array().and_then(|rows| rows.iter().find(|row| row["device_id"] == id.as_str()).cloned()).unwrap_or(Value::Null);
    check(
        "name, note and favourite are as they were",
        after["name"] == original.as_str() && after["note"] == original_note && after["favorite"].as_bool().unwrap_or(false) == original_favorite,
    );

    // A login outside the company: it cannot see or change the device.
    if let Some(other) = args.get(3) {
        let outsider = Context::new(folder.join("other")).expect("data folder");
        match sign_in(&outsider, other).await {
            Err(text) => println!("outsider sign-in: {text}"),
            Ok(()) => {
                let list = call(&outsider, "remote_admin_devices", json!({})).await;
                let sees = list.as_ref().ok().and_then(Value::as_array).is_some_and(|rows| rows.iter().any(|row| row["device_id"] == id.as_str()));
                check("the outsider's list does not contain the device", !sees);
                for (command, body) in [
                    ("remote_admin_rename_device", json!({"device": id, "device_name": "not allowed"})),
                    ("remote_admin_set_device_note", json!({"device": id, "note": "not allowed"})),
                    ("remote_admin_set_device_unattended", json!({"device": id, "setting": "off"})),
                ] {
                    let refused = call(&outsider, command, body).await;
                    println!("outsider {command}: {}", hint(&refused));
                    check(&format!("outsider {command} is refused as device_not_found"), hint(&refused) == "device_not_found");
                }
                let confirm = call(&outsider, "remote_admin_remove_device", json!({"device": id, "confirm_connection_id": target["connection_id"]})).await;
                println!("outsider remove (with the right ID): {}", hint(&confirm));
                check("outsider remove is refused as device_not_found", hint(&confirm) == "device_not_found");
            }
        }
        let _ = outsider.shutdown().await;
    }
    let _ = context.shutdown().await;
    let _ = std::fs::remove_dir_all(&folder);
    println!("{}", if failures == 0 { "ALL PASSED" } else { "SOME FAILED" });
    std::process::exit(i32::from(failures > 0));
}
