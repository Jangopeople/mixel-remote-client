//! Mass rollout: the one-line install-and-enrol commands per operating system.
/// The PowerShell line (run as administrator) that installs the latest Windows build and enrols the
/// computer with a rollout token or SaaS deploy link, with always-on access.
pub async fn windows_rollout_command(token: &str) -> Result<String, String> {
    if !token.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte)) {
        return Err("The rollout token could not be created".into());
    }
    let installer = crate::self_update::latest_for("windows", "x64").await?;
    Ok(rollout_command_for(token, &crate::self_update::file_url(&installer)))
}
/// Where the portal serves the install scripts (`next/frontends/portal/public/install/`).
pub const INSTALL_SCRIPTS: &str = "https://remote.mixel.ch/install";
/// The one-line commands for Macs and Linux computers: download the install script, which installs
/// the released, signed app from the update channel and then asks for the deploy token without
/// showing it (deployment tools set MIXEL_DEPLOY_TOKEN instead). The token is never part of the
/// line, so it stays out of the shell history and the process list.
pub fn unix_rollout_commands(token: &str) -> Result<(String, String), String> {
    if !crate::deploy_token::valid(token) {
        return Err("The rollout token could not be created".into());
    }
    Ok((format!("curl -fsSL {INSTALL_SCRIPTS}/macos.sh | sudo sh"), format!("curl -fsSL {INSTALL_SCRIPTS}/linux.sh | sudo sh")))
}
pub fn rollout_command_for(token: &str, installer: &str) -> String {
    // Single quotes only, so the line also survives being wrapped in "…" by deployment tools. The
    // token reaches the enrol step in MIXEL_DEPLOY_TOKEN (inherited by Start-Process, set only for
    // that step), not as an argument other users could read in the process list.
    format!(
        "$t='{token}'; $f=Join-Path $env:TEMP 'Mixel-Remote-Setup.exe'; Invoke-WebRequest -UseBasicParsing '{installer}' -OutFile $f; Start-Process $f '/S' -Wait; $env:MIXEL_DEPLOY_TOKEN=$t; $p=Start-Process (Join-Path $env:LOCALAPPDATA 'Mixel Remote\\mixel-remote.exe') -ArgumentList '--signin-enroll' -Wait -PassThru; Remove-Item Env:MIXEL_DEPLOY_TOKEN -ErrorAction SilentlyContinue; exit $p.ExitCode"
    )
}

#[cfg(test)]
mod rollout_command_tests {
    #[test]
    fn deploy_command_carries_the_token_in_single_quotes_only() {
        let line = super::rollout_command_for("mrt_ab-c_D1", "https://download.mixel.ch/x.exe");
        assert!(line.starts_with("$t='mrt_ab-c_D1'; "));
        assert!(line.contains("'https://download.mixel.ch/x.exe'"));
        assert!(!line.contains('"'));
        // The token goes to the enrol step in the environment, never as an argument.
        assert!(line.contains("$env:MIXEL_DEPLOY_TOKEN=$t; $p=Start-Process "));
        assert!(line.contains("-ArgumentList '--signin-enroll' -Wait"));
        assert!(!line.contains("'--signin-enroll',$t"));
        assert_eq!(line.matches("$t").count(), 2, "{line}");
        assert!(line.find("Start-Process $f '/S' -Wait").unwrap() < line.find("$env:MIXEL_DEPLOY_TOKEN=$t").unwrap(), "the installer does not inherit it");
        assert!(line.contains("Remove-Item Env:MIXEL_DEPLOY_TOKEN"));
    }
    #[test]
    fn mac_and_linux_commands_pipe_the_install_script_to_root_without_the_token() {
        let token = "mrt_abcdefghijklmnopqrstuvwxyz-_0123";
        let (mac, linux) = super::unix_rollout_commands(token).unwrap();
        assert_eq!(mac, "curl -fsSL https://remote.mixel.ch/install/macos.sh | sudo sh");
        assert_eq!(linux, "curl -fsSL https://remote.mixel.ch/install/linux.sh | sudo sh");
        assert!(!mac.contains("mrt_") && !linux.contains("mrt_"));
        assert!(super::unix_rollout_commands("mrt_short").is_err());
        assert!(super::unix_rollout_commands("mrt_abcdefghijklmnopqrstuvwxyz';rm -rf /").is_err());
    }
}
