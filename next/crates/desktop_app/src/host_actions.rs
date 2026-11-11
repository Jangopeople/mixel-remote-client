//! One-click actions a technician with keyboard and mouse control may run on the host:
//! Ctrl+Alt+Del (Windows) and restarting the computer. The host offers only what works
//! here; the controller shows only what the host offered.
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

pub const SAS: &str = "sas";
pub const RESTART: &str = "restart";

/// Actions this host can run now.
pub fn available() -> Vec<String> {
    #[allow(unused_mut)]
    let mut actions = Vec::new();
    #[cfg(target_os = "windows")]
    {
        if crate::windows_signin_host::can_send_sas() {
            actions.push(SAS.to_owned());
        }
        actions.push(RESTART.to_owned());
    }
    #[cfg(target_os = "linux")]
    actions.push(RESTART.to_owned());
    actions
}

/// Runs an action the controller asked for (only names from `available`).
pub fn run(name: &str) -> Result<(), String> {
    match name {
        #[cfg(target_os = "windows")]
        SAS => crate::windows_signin_host::send_sas(),
        #[cfg(target_os = "windows")]
        RESTART => {
            let ok = std::process::Command::new("shutdown")
                .args(["/r", "/t", "5", "/d", "p:0:0", "/c", "Mixel Remote: restart requested by your technician"])
                .creation_flags(0x0800_0000)
                .status()
                .is_ok_and(|status| status.success());
            if ok { Ok(()) } else { Err("Windows did not allow the restart".into()) }
        }
        #[cfg(target_os = "linux")]
        RESTART => {
            let ok = std::process::Command::new("systemctl")
                .arg("reboot")
                .status()
                .is_ok_and(|status| status.success());
            if ok { Ok(()) } else { Err("The system did not allow the restart".into()) }
        }
        _ => Err("This action is not available on the remote computer".into()),
    }
}

/// Parses the comma list a host announced; unknown names are dropped.
pub fn parse(list: &str) -> Vec<String> {
    list.split(',')
        .map(str::trim)
        .filter(|name| matches!(*name, SAS | RESTART))
        .map(str::to_owned)
        .take(8)
        .collect()
}

#[cfg(test)]
mod tests {
    #[test]
    fn only_known_actions_survive_parsing() {
        assert_eq!(super::parse("sas, restart,format-disk,"), vec!["sas", "restart"]);
        assert!(super::parse("").is_empty());
    }
}
