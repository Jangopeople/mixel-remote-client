#!/usr/bin/env python3
"""Make the normal signed Mac updater transactional and propagate its actual result."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ORIGINAL_SCRIPT = r"""on run {daemon_file, agent_file, user, cur_pid, source_dir}

  set agent_plist to "/Library/LaunchAgents/com.carriez.RustDesk_server.plist"
  set daemon_plist to "/Library/LaunchDaemons/com.carriez.RustDesk_service.plist"
  set app_bundle to "/Applications/RustDesk.app"

  set check_source to "test -d " & quoted form of source_dir & " || exit 1;"
  set resolve_uid to "uid=$(id -u " & quoted form of user & " 2>/dev/null || true);"
  set unload_agent to "if [ -n \"$uid\" ]; then launchctl bootout gui/$uid " & quoted form of agent_plist & " 2>/dev/null || launchctl bootout user/$uid " & quoted form of agent_plist & " 2>/dev/null || launchctl unload -w " & quoted form of agent_plist & " || true; else launchctl unload -w " & quoted form of agent_plist & " || true; fi;"
  set unload_service to "launchctl unload -w " & daemon_plist & " || true;"
  set kill_others to "pids=$(pgrep -x 'RustDesk' | grep -vx " & cur_pid & " || true); if [ -n \"$pids\" ]; then echo \"$pids\" | xargs kill -9 || true; fi;"

  set copy_files to "(rm -rf " & quoted form of app_bundle & " && ditto " & quoted form of source_dir & " " & quoted form of app_bundle & " && chown -R " & quoted form of user & ":staff " & quoted form of app_bundle & " && (xattr -r -d com.apple.quarantine " & quoted form of app_bundle & " || true)) || exit 1;"

  set write_daemon_plist to "echo " & quoted form of daemon_file & " > " & daemon_plist & " && chown root:wheel " & daemon_plist & ";"
  set write_agent_plist to "echo " & quoted form of agent_file & " > " & agent_plist & " && chown root:wheel " & agent_plist & ";"
  set load_service to "launchctl load -w " & daemon_plist & ";"
  set agent_label_cmd to "agent_label=$(basename " & quoted form of agent_plist & " .plist);"
  set bootstrap_agent to "if [ -n \"$uid\" ]; then launchctl bootstrap gui/$uid " & quoted form of agent_plist & " 2>/dev/null || launchctl bootstrap user/$uid " & quoted form of agent_plist & " 2>/dev/null || launchctl load -w " & quoted form of agent_plist & " || true; else launchctl load -w " & quoted form of agent_plist & " || true; fi;"
  set kickstart_agent to "if [ -n \"$uid\" ]; then launchctl kickstart -k gui/$uid/$agent_label 2>/dev/null || launchctl kickstart -k user/$uid/$agent_label 2>/dev/null || true; fi;"
  set load_agent to agent_label_cmd & bootstrap_agent & kickstart_agent

  set sh to "set -e;" & check_source & resolve_uid & unload_agent & unload_service & kill_others & copy_files & write_daemon_plist & write_agent_plist & load_service & load_agent

  do shell script sh with prompt "RustDesk wants to update itself" with administrator privileges
end run
"""

ORIGINAL_DAEMON = r"""fn update_daemon_agent(agent_plist_file: String, update_source_dir: String, sync: bool) {
    let update_script_file = "update.scpt";
    let Some(update_script) = PRIVILEGES_SCRIPTS_DIR.get_file(update_script_file) else {
        return;
    };
    let Some(update_script_body) = update_script.contents_utf8().map(correct_app_name) else {
        return;
    };

    let Some(daemon_plist) = PRIVILEGES_SCRIPTS_DIR.get_file("daemon.plist") else {
        return;
    };
    let Some(daemon_plist_body) = daemon_plist.contents_utf8().map(correct_app_name) else {
        return;
    };
    let Some(agent_plist) = PRIVILEGES_SCRIPTS_DIR.get_file("agent.plist") else {
        return;
    };
    let Some(agent_plist_body) = agent_plist.contents_utf8().map(correct_app_name) else {
        return;
    };

    let func = move || {
        let mut binding = std::process::Command::new("osascript");
        let cmd = binding
            .arg("-e")
            .arg(update_script_body)
            .arg(daemon_plist_body)
            .arg(agent_plist_body)
            .arg(&get_active_username())
            .arg(std::process::id().to_string())
            .arg(update_source_dir);
        match cmd.status() {
            Err(e) => {
                log::error!("run osascript failed: {}", e);
            }
            Ok(status) if !status.success() => {
                log::warn!("run osascript failed with status: {}", status);
            }
            _ => {
                let installed = std::path::Path::new(&agent_plist_file).exists();
                log::info!("Agent file {} installed: {}", &agent_plist_file, installed);
            }
        }
    };
    if sync {
        func();
    } else {
        std::thread::spawn(func);
    }
}

"""

ORIGINAL_UPDATER = r"""pub fn update_me() -> ResultType<()> {
    let is_installed_daemon = is_installed_daemon(false);
    let option_stop_service = "stop-service";
    let is_service_stopped = hbb_common::config::option2bool(
        option_stop_service,
        &crate::ui_interface::get_option(option_stop_service),
    );

    let cmd = std::env::current_exe()?;
    // RustDesk.app/Contents/MacOS/RustDesk
    let app_dir = cmd
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .map(|d| d.to_string_lossy().to_string());
    let Some(app_dir) = app_dir else {
        bail!("Unknown app directory of current exe file: {:?}", cmd);
    };

    let app_name = crate::get_app_name();
    if is_installed_daemon && !is_service_stopped {
        let agent = format!("{}_server.plist", crate::get_full_name());
        let agent_plist_file = format!("/Library/LaunchAgents/{}", agent);
        update_daemon_agent(agent_plist_file, app_dir, true);
    } else {
        // `kill -9` may not work without "administrator privileges"
        let update_body = r#"
on run {app_name, cur_pid, app_dir, user_name}
    set app_bundle to "/Applications/" & app_name & ".app"
    set app_bundle_q to quoted form of app_bundle
    set app_dir_q to quoted form of app_dir
    set user_name_q to quoted form of user_name

    set check_source to "test -d " & app_dir_q & " || exit 1;"
    set kill_others to "pids=$(pgrep -x '" & app_name & "' | grep -vx " & cur_pid & " || true); if [ -n \"$pids\" ]; then echo \"$pids\" | xargs kill -9 || true; fi;"
    set copy_files to "rm -rf " & app_bundle_q & " && ditto " & app_dir_q & " " & app_bundle_q & " && chown -R " & user_name_q & ":staff " & app_bundle_q & " && (xattr -r -d com.apple.quarantine " & app_bundle_q & " || true);"
    set sh to "set -e;" & check_source & kill_others & copy_files

    do shell script sh with prompt app_name & " wants to update itself" with administrator privileges
end run
        "#;
        let active_user = get_active_username();
        let status = Command::new("osascript")
            .arg("-e")
            .arg(update_body)
            .arg(app_name.to_string())
            .arg(std::process::id().to_string())
            .arg(app_dir)
            .arg(active_user)
            .status();
        match status {
            Ok(status) if !status.success() => {
                log::error!("osascript execution failed with status: {}", status);
            }
            Err(e) => {
                log::error!("run osascript failed: {}", e);
            }
            _ => {}
        }
    }
    std::process::Command::new("open")
        .arg("-n")
        .arg(&format!("/Applications/{}.app", app_name))
        .spawn()
        .ok();
    // leave open a little time
    std::thread::sleep(std::time::Duration::from_millis(300));
    Ok(())
}

pub fn update_from_dmg(dmg_path: &str) -> ResultType<()> {
    let update_temp_dir = get_update_temp_dir_string();
    println!("Starting update from DMG: {}", dmg_path);
    extract_dmg(dmg_path, &update_temp_dir)?;
    println!("DMG extracted");
    update_extracted(&update_temp_dir)?;
    println!("Update process started");
    Ok(())
}

"""

ORIGINAL_CLI = r"""        #[cfg(target_os = "macos")]
        {
            use crate::platform;
            if args[0] == "--update" {
                if args.len() > 1 && args[1].ends_with(".dmg") {
                    // Version check is unnecessary unless downgrading to an older version
                    // that lacks "update dmg" support. This is a special case since we cannot
                    // detect the version before extracting the DMG, so we skip the check.
                    let dmg_path = &args[1];
                    println!("Updating from DMG: {}", dmg_path);
                    match platform::update_from_dmg(dmg_path) {
                        Ok(_) => {
                            println!("Update process from DMG started successfully.");
                            // The new process will handle the rest. We can exit.
                        }
                        Err(err) => {
                            eprintln!("Failed to start update from DMG: {}", err);
                        }
                    }
                } else {
                    println!("Starting update process...");
                    log::info!("Starting update process...");
                    let _text = match platform::update_me() {
                        Ok(_) => {
                            println!("{}", translate("Update successfully!".to_string()));
                            log::info!("Update successfully!");
                        }
                        Err(err) => {
                            eprintln!("Update failed with error: {}", err);
                            log::error!("Update failed with error: {err}");
                        }
                    };
                }
                return None;
            }
        }
"""

FIXED_DAEMON = r"""// MIXEL_MACOS_TRANSACTIONAL_UPDATE_V1
fn run_update_script(update_source_dir: &str, with_service: bool) -> ResultType<()> {
    let text = |name: &str| -> ResultType<String> {
        PRIVILEGES_SCRIPTS_DIR
            .get_file(name)
            .and_then(|file| file.contents_utf8())
            // Only upstream plist templates contain branding placeholders. The
            // checked-in updater scripts must stay byte-exact, including the
            // real liblibrustdesk.dylib filename used by the signed payload.
            .map(|body| match name {
                "daemon.plist" | "agent.plist" => correct_app_name(body),
                _ => body.to_owned(),
            })
            .ok_or_else(|| anyhow!("Embedded macOS updater resource missing: {}", name))
    };
    let status = Command::new("/usr/bin/osascript")
        .arg("-e")
        .arg(text("update.scpt")?)
        .arg(text("daemon.plist")?)
        .arg(text("agent.plist")?)
        .arg(get_active_username())
        .arg(std::process::id().to_string())
        .arg(update_source_dir)
        .arg(text("update.sh")?)
        .arg(crate::get_app_name())
        .arg("ch.mixel.remote")
        .arg(if with_service { "1" } else { "0" })
        .status()?;
    if !status.success() {
        bail!("macOS updater authorization or bundle replacement failed: {}", status);
    }
    Ok(())
}

fn update_daemon_agent(agent_plist_file: String, update_source_dir: String) -> ResultType<()> {
    run_update_script(&update_source_dir, true)?;
    if !Path::new(&agent_plist_file).is_file() {
        bail!("Canonical macOS launch agent is missing after update");
    }
    Ok(())
}

"""

FIXED_UPDATER = r"""pub fn update_me() -> ResultType<()> {
    let cmd = std::env::current_exe()?;
    let app_dir = cmd
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .ok_or_else(|| anyhow!("Unknown updater application directory"))?;
    update_source_app(app_dir)
}

fn update_source_app(source: &Path) -> ResultType<()> {
    // Reject an installed/source alias before authorization, process shutdown or replacement.
    let source = source.canonicalize()?;
    let target = PathBuf::from(format!("/Applications/{}.app", crate::get_app_name()));
    let resolved_target = if target.exists() { target.canonicalize()? } else { target.clone() };
    if source.starts_with(&resolved_target) || resolved_target.starts_with(&source) {
        bail!("Updater source overlaps the installed application");
    }
    let source = source.to_str().ok_or_else(|| anyhow!("Updater source is not UTF-8"))?;
    let daemon_installed = is_installed_daemon(false);
    let option_stop_service = "stop-service";
    let is_service_stopped = hbb_common::config::option2bool(
        option_stop_service,
        &crate::ui_interface::get_option(option_stop_service),
    );
    if daemon_installed && !is_service_stopped {
        let agent = format!("{}_server.plist", crate::get_full_name());
        let agent_plist_file = format!("/Library/LaunchAgents/{}", agent);
        update_daemon_agent(agent_plist_file, source.to_owned())?;
    } else {
        run_update_script(source, false)?;
    }
    let status = Command::new("/usr/bin/open")
        .arg("-n")
        .arg(&target)
        .status()?;
    if !status.success() {
        bail!("Updated application could not be opened: {}", status);
    }
    Ok(())
}

pub fn update_from_dmg(dmg_path: &str) -> ResultType<()> {
    let update_temp_dir = get_update_temp_dir_string();
    extract_dmg(dmg_path, &update_temp_dir)?;
    // The normal CLI owns the complete result; do not detach a successor and call its launch an update.
    let source = Path::new(&update_temp_dir).join(format!("{}.app", crate::get_app_name()));
    update_source_app(&source)
}

"""

FIXED_CLI = r"""        #[cfg(target_os = "macos")]
        {
            use crate::platform;
            if args[0] == "--update" {
                let result = if args.len() > 1 && args[1].ends_with(".dmg") {
                    platform::update_from_dmg(&args[1])
                } else {
                    platform::update_me()
                };
                match result {
                    Ok(_) => println!("{}", translate("Update successfully!".to_string())),
                    Err(err) => {
                        eprintln!("Update failed with error: {}", err);
                        log::error!("Update failed with error: {err}");
                        // core_main None is otherwise converted to exit0 by the macOS Flutter entry.
                        std::process::exit(1);
                    }
                }
                return None;
            }
        }
"""

def replace(data: bytes, old: str, new: str) -> bytes:
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    before = old.encode().replace(b"\n", newline)
    after = new.encode().replace(b"\n", newline)
    if data.count(before) == 1 and data.count(after) == 0:
        return data.replace(before, after, 1)
    if data.count(before) == 0 and data.count(after) == 1:
        return data
    raise RuntimeError("macOS normal updater anchor missing, duplicated or partially modified")


def patch(repo: Path) -> None:
    macos = repo / "src/platform/macos.rs"
    core = repo / "src/core_main.rs"
    if macos.is_symlink() or core.is_symlink():
        raise RuntimeError("macOS updater source must be ordinary files")
    updated_macos = replace(macos.read_bytes(), ORIGINAL_DAEMON, FIXED_DAEMON)
    updated_macos = replace(updated_macos, ORIGINAL_UPDATER, FIXED_UPDATER)
    updated_core = replace(core.read_bytes(), ORIGINAL_CLI, FIXED_CLI)
    resources = repo / "src/platform/privileges_scripts"
    embedded = {"update.scpt": (ROOT / "macos-update.scpt").read_bytes(),
                "update.sh": (ROOT / "macos-update-bundle.sh").read_bytes()}
    if not resources.is_dir() or resources.is_symlink():
        raise RuntimeError("macOS privilege resource directory missing")
    script = resources / "update.scpt"
    accepted_scripts = (
            ORIGINAL_SCRIPT.encode(), ORIGINAL_SCRIPT.replace("com.carriez.RustDesk", "com.carriez.rustdesk").encode(),
            embedded["update.scpt"])
    accepted_scripts += tuple(data.replace(b"\n", b"\r\n") for data in accepted_scripts)
    if not script.is_file() or script.is_symlink() or script.read_bytes() not in accepted_scripts:
        raise RuntimeError("macOS privilege script missing or changed upstream")
    shell = resources / "update.sh"
    if shell.is_symlink() or (shell.exists() and (not shell.is_file() or shell.read_bytes() != embedded["update.sh"])):
        raise RuntimeError("macOS transactional updater resource collision")
    # Validate every source anchor before writing any generated file.
    macos.write_bytes(updated_macos)
    core.write_bytes(updated_core)
    for name, content in embedded.items():
        (resources / name).write_bytes(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    patch(Path(os.environ.get("RDREPO", "rustdesk")))
    print("PASS: normal Mac updater validates staging/rollback and propagates actual failure")
