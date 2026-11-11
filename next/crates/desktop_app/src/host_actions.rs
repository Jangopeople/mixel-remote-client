//! One-click actions a technician with keyboard and mouse control may run on the host:
//! Ctrl+Alt+Del (Windows) and restarting the computer. The host offers only what works
//! here; the controller shows only what the host offered.
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

pub const SAS: &str = "sas";
pub const RESTART: &str = "restart";
/// Wake another computer on this host's network: sent as "wol:<12 hex digits>".
pub const WOL: &str = "wol";
/// Not run here: `sound:on` / `sound:off` switch the sound of the running session.
pub const SOUND: &str = "sound";

fn mac_bytes(text: &str) -> Option<[u8; 6]> {
    let hex: String = text.chars().filter(char::is_ascii_hexdigit).collect();
    if hex.len() != 12 || text.len() > 17 {
        return None;
    }
    let mut mac = [0u8; 6];
    for (index, byte) in mac.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16).ok()?;
    }
    (mac != [0; 6] && mac != [0xff; 6]).then_some(mac)
}

/// Sends a Wake-on-LAN magic packet for `mac` on the local networks of this computer.
pub fn wake(mac: &str) -> Result<(), String> {
    let mac = mac_bytes(mac).ok_or("Invalid network address")?;
    let mut packet = vec![0xffu8; 6];
    for _ in 0..16 {
        packet.extend_from_slice(&mac);
    }
    let socket = std::net::UdpSocket::bind(("0.0.0.0", 0)).map_err(|_| "Cannot open the network")?;
    socket.set_broadcast(true).map_err(|_| "Cannot send on the network")?;
    let mut sent = false;
    for port in [9u16, 7] {
        sent |= socket.send_to(&packet, ("255.255.255.255", port)).is_ok();
    }
    if sent { Ok(()) } else { Err("The wake signal could not be sent".into()) }
}

/// Hardware addresses of this computer's network adapters (12 hex digits each), for a later wake.
pub fn mac_addresses() -> Vec<String> {
    let mut command;
    #[cfg(target_os = "windows")]
    {
        command = std::process::Command::new("getmac");
        command.args(["/fo", "csv", "/nh"]).creation_flags(0x0800_0000);
    }
    #[cfg(target_os = "macos")]
    {
        command = std::process::Command::new("/sbin/ifconfig");
        command.arg("-a");
    }
    #[cfg(target_os = "linux")]
    {
        command = std::process::Command::new("sh");
        command.args(["-c", "cat /sys/class/net/*/address"]);
    }
    let Ok(output) = command.output() else { return Vec::new() };
    let text = String::from_utf8_lossy(&output.stdout);
    let mut found: Vec<String> = Vec::new();
    for token in text.split(|c: char| !(c.is_ascii_hexdigit() || c == ':' || c == '-')) {
        if token.len() == 17
            && let Some(mac) = mac_bytes(token)
            && mac[0] & 2 == 0
        {
            let hex: String = mac.iter().map(|byte| format!("{byte:02x}")).collect();
            if !found.contains(&hex) {
                found.push(hex);
            }
        }
    }
    found.truncate(4);
    found
}

/// Actions this host can run now.
#[allow(clippy::vec_init_then_push)]
pub fn available() -> Vec<String> {
    let mut actions = Vec::new();
    #[cfg(target_os = "windows")]
    {
        if crate::windows_signin_host::can_send_sas() {
            actions.push(SAS.to_owned());
        }
        actions.push(RESTART.to_owned());
        // The technician can hear this computer's sound and switch it on or off.
        actions.push(SOUND.to_owned());
    }
    #[cfg(target_os = "linux")]
    actions.push(RESTART.to_owned());
    actions.push(WOL.to_owned());
    actions
}

/// Runs an action the controller asked for (only names from `available`).
pub fn run(name: &str) -> Result<(), String> {
    if let Some(mac) = name.strip_prefix("wol:") {
        return wake(mac);
    }
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
        .filter(|name| matches!(*name, SAS | RESTART | WOL | SOUND))
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
        assert!(super::wake("zz").is_err());
        assert!(super::wake("ff:ff:ff:ff:ff:ff").is_err());
        assert!(super::mac_bytes("52:54:00:12:34:56").is_some());
    }
}
