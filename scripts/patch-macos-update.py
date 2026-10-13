#!/usr/bin/env python3
"""Make the normal signed Mac updater transactional and propagate its actual result."""
import argparse
import os
import hashlib
import stat
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

PROFILES = [{'name': 'pristine', 'macosOriginal': '906437a17fa5d11a8c62a212a665362d5b880074f17d1bc0c3d0d8aedc433ee6', 'macosCorrected': '5fa906b581c5f0960fb81c4b00d8cbb4feb62e306c093a75ccae9fde1ca29e48', 'coreOriginal': '3bc241c59d547352e77bfa1b44d6ea0e1200b3dd703ed4a63fbb720b07187512', 'coreCorrected': '46e786a17717b5a975d0f6756847bff6f8a8ef759c6bc8f8c3c9f6874f22962a', 'scriptOriginal': 'de71fc36ab00ade08f4c6c4dd0a373a70d4973e4bc755d43ac611151e66c1a4a', 'scriptLower': False}, {'name': 'composed', 'macosOriginal': '0a00cbfaf4ce4ba813405ad7351fae891dcd92309080c3eeba9ea77782eafcf8', 'macosCorrected': 'ae56277bd67d1788d137d5d9a3dc2252c498db425eac4944ffaabdb8c791b7cd', 'coreOriginal': 'c2c7909b4c7c4ed7ca173ac614bb017266e3ff220fb19bedc0b8dbbf0f2ca00d', 'coreCorrected': '879f5bf280e4c638a74c5144975575df4cf1fc4c64b680ef616301c5ab35ee67', 'scriptOriginal': '3de85ca3785c9eaa2fffa7110372affc9d43a2a8128f090474fb34202433a0f7', 'scriptLower': True}, {'name': 'composed_fatal', 'macosOriginal': '0a00cbfaf4ce4ba813405ad7351fae891dcd92309080c3eeba9ea77782eafcf8', 'macosCorrected': 'ae56277bd67d1788d137d5d9a3dc2252c498db425eac4944ffaabdb8c791b7cd', 'coreOriginal': 'da22c16e10d6dcc8816dae300336cf5f3897834c65221a5a5f5344ec69901bd3', 'coreCorrected': '1f4a190de22571e014c0fca0130516b4e158457e4a16a27ff3423bfdbeabf8a4', 'scriptOriginal': '3de85ca3785c9eaa2fffa7110372affc9d43a2a8128f090474fb34202433a0f7', 'scriptLower': True}]
TEMPLATE_SHA = {'macos-update.scpt': 'b948c865226c50c4bcb061320fb2469e4ed2b4e648fb6f7a0c4919f98d641031', 'macos-update-bundle.sh': 'd0dd94eb484f86ca37f302a625d496c876e4cedd21c149749d51590702eff1cc'}

FILES = ['src/platform/macos.rs', 'src/core_main.rs', 'src/platform/privileges_scripts/update.scpt', 'src/platform/privileges_scripts/update.sh']
UPSTREAM = '1abc897c451c8b5bbff3792509a7fef9d12f2ce3'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(raw):
    if b'\r' in raw:
        remainder = raw.replace(b'\r\n', b'')
        if b'\r' in remainder or b'\n' in remainder:
            raise RuntimeError('Mixed macOS updater source newlines')
        return raw.replace(b'\r\n', b'\n'), b'\r\n'
    return raw, b'\n'


def safe_target(root, relative, optional=False):
    root = Path(root).absolute()
    target = root / relative
    for parent in reversed(target.parents):
        entry = parent.lstat()
        if (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode)
                or getattr(entry, 'st_file_attributes', 0) & 0x400):
            raise RuntimeError('Unsafe macOS updater source parent')
    try:
        entry = target.lstat()
    except FileNotFoundError:
        if optional:
            return target
        raise RuntimeError('Missing macOS updater source: ' + relative) from None
    if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
            or entry.st_size > 1000000
            or getattr(entry, 'st_file_attributes', 0) & 0x400):
        raise RuntimeError('Unsafe macOS updater source file: ' + relative)
    return target


def templates():
    result = []
    for name in ['macos-update.scpt', 'macos-update-bundle.sh']:
        data = canonical(safe_target(ROOT, name).read_bytes())[0]
        if digest(data) != TEMPLATE_SHA[name]:
            raise RuntimeError('Pinned macOS updater template changed: ' + name)
        # Embed canonical LF shell bytes even when Git materialized CRLF templates.
        result.append(data)
    return result


def transform(data, changes, restore=False):
    for before, after in reversed(changes) if restore else changes:
        old, new = (after, before) if restore else (before, after)
        old, new = old.encode(), new.encode()
        if data.count(old) != 1 or data.count(new) != 0:
            raise RuntimeError('macOS updater source anchor changed')
        data = data.replace(old, new, 1)
    return data


def project(macos, core, script, shell, restore=False):
    embedded_script, embedded_shell = templates()
    for profile in PROFILES:
        if digest(macos) == profile['macosOriginal'] and digest(core) == profile['coreOriginal']:
            if digest(script) != profile['scriptOriginal'] or shell is not None:
                raise RuntimeError('macOS updater original resource changed or foreign shell exists')
            m = transform(macos, [(ORIGINAL_DAEMON, FIXED_DAEMON), (ORIGINAL_UPDATER, FIXED_UPDATER)])
            c = transform(core, [(ORIGINAL_CLI, FIXED_CLI)])
            if digest(m) != profile['macosCorrected'] or digest(c) != profile['coreCorrected']:
                raise RuntimeError('macOS updater corrected source mismatch')
            return (macos, core, script, None) if restore else (m, c, embedded_script, embedded_shell)
        if digest(macos) == profile['macosCorrected'] and digest(core) == profile['coreCorrected']:
            if script != embedded_script or shell != embedded_shell:
                raise RuntimeError('macOS updater owned resource changed or missing')
            m = transform(macos, [(ORIGINAL_DAEMON, FIXED_DAEMON), (ORIGINAL_UPDATER, FIXED_UPDATER)], True)
            c = transform(core, [(ORIGINAL_CLI, FIXED_CLI)], True)
            old_script = ORIGINAL_SCRIPT.replace('com.carriez.RustDesk', 'com.carriez.rustdesk') if profile['scriptLower'] else ORIGINAL_SCRIPT
            if (digest(m) != profile['macosOriginal'] or digest(c) != profile['coreOriginal']
                    or digest(old_script.encode()) != profile['scriptOriginal']):
                raise RuntimeError('macOS updater inverse source mismatch')
            return (m, c, old_script.encode(), None) if restore else (macos, core, script, shell)
    raise RuntimeError('Pinned macOS updater whole source profile changed or partially applied')


def apply(root, restore=False, check=False):
    paths = [safe_target(root, relative, optional=index == 3) for index, relative in enumerate(FILES)]
    before = [path.read_bytes() if path.exists() else None for path in paths]
    normalized = [canonical(data) if data is not None else (None, b'\n') for data in before]
    desired = project(*(data for data, newline in normalized), restore=restore)
    after = [data.replace(b'\n', normalized[index][1]) if data is not None else None for index, data in enumerate(desired)]
    # Every source, resource and ownership check is complete before the first write.
    if not check:
        for path, old, new in zip(paths, before, after):
            if old == new:
                continue
            if new is None:
                path.unlink()
            elif old is None:
                with path.open('xb') as stream:
                    stream.write(new)
            else:
                path.write_bytes(new)
    return {relative: digest(data) if data is not None else None for relative, data in zip(FILES, after)}


def patch(repo):
    return apply(repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(os.environ.get('RDREPO', 'rustdesk')))
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.source, restore=args.restore, check=args.check)
    print('PASS exact transactional Mac updater source, resources, failure propagation and inverse')


if __name__ == '__main__':
    main()
