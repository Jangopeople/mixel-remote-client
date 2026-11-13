//! Explicit attended compatibility with existing Mixel Remote Support IDs.
use std::{
    path::Path,
    process::{Command, Stdio},
};

const SUPPORT_APP: &str = "/Applications/Mixel-Remote.app";
const SUPPORT_REQUIREMENT: &str = r#"=anchor apple generic and identifier "ch.mixel.remote" and certificate leaf[subject.OU] = "5277F8NDH4" and certificate leaf[field.1.2.840.113635.100.6.1.13] exists"#;

fn support_id(id: &str) -> Result<String, &'static str> {
    if id.len() > 11 || !id.bytes().all(|b| b.is_ascii_digit() || b == b' ') {
        return Err("Enter the nine-digit ID shown in Mixel Remote Support.");
    }
    let digits: String = id.chars().filter(|&c| c != ' ').collect();
    if digits.len() != 9 || digits.starts_with('0') {
        return Err("Enter the nine-digit ID shown in Mixel Remote Support.");
    }
    Ok(digits)
}

pub fn connect(id: &str) -> Result<(), String> {
    let id = support_id(id)?;
    let path = Path::new(SUPPORT_APP);
    if !path.is_dir() {
        return Err("Install Mixel Remote Support in Applications from ism.mixel.ch/remote/support, then try again.".into());
    }
    if path.canonicalize().as_deref().ok() != Some(path) {
        return Err("The Mixel Remote Support installation is in an unexpected location.".into());
    }
    let valid = Command::new("/usr/bin/codesign")
        .args([
            "--verify",
            "--strict",
            "--deep",
            "-R",
            SUPPORT_REQUIREMENT,
            SUPPORT_APP,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !valid {
        return Err(
            "Mixel Remote Support could not be verified. Reinstall it from the Mixel support page."
                .into(),
        );
    }
    // Only a locally verified bundle and a canonical ID reach LaunchServices.
    // Never forward Mixel ID credentials, managed grants or session passwords.
    let opened = Command::new("/usr/bin/open")
        .args(["-n", "-a", SUPPORT_APP, "--args", "--connect", id.as_str()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());
    if !opened {
        return Err(
            "Could not open Mixel Remote Support. Open it from Applications and try again.".into(),
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::support_id;

    #[test]
    fn support_ids_cannot_add_launch_flags_or_address_another_server() {
        assert_eq!(support_id("304 619 778"), Ok("304619778".into()));
        assert_eq!(support_id("304619778"), Ok("304619778".into()));
        for invalid in [
            "--connect",
            "304619778@",
            "304619778/r",
            "004619778",
            "3046197781",
            "304\n619778",
            "３０４６１９７７８",
        ] {
            assert!(support_id(invalid).is_err(), "{invalid:?}");
        }
    }
}
