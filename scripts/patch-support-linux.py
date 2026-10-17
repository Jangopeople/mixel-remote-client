#!/usr/bin/env python3
"""Keep attended Linux consent working on desktops without logind seat data."""
import os
from pathlib import Path


ORIGINAL = """pub fn is_prelogin() -> bool {
    if is_flatpak() {
        return false;
    }
    let name = get_active_username();
    if let Ok(res) = run_cmds(&format!("getent passwd {}", name)) {
        return res.contains("/bin/false") || res.contains("/usr/sbin/nologin");
    }
    false
}"""

PATCHED = """pub fn is_prelogin() -> bool {
    if is_flatpak() {
        return false;
    }
    let name = get_active_username();
    let user = if name.is_empty() {
        // A valid nonroot X11 session can have no logind/seat0 entry. Resolve
        // its kernel identity through NSS, never USER/LOGNAME or an empty
        // shell command that enumerates every system account.
        let uid = hbb_common::users::get_effective_uid();
        if uid == 0 || hbb_common::users::get_current_uid() != uid {
            return true;
        }
        hbb_common::users::get_user_by_uid(uid).filter(|user| user.uid() == uid)
    } else {
        // Query NSS directly: a session name must never become shell syntax.
        get_user_by_name(&name)
    };
    match user {
        Some(user) => {
            let shell = user.shell();
            shell.ends_with("false") || shell.ends_with("nologin")
        }
        None => true,
    }
}"""

USERNAME_ORIGINAL = """pub fn get_active_username() -> String {
    get_values_of_seat0(&[2])[0].clone()
}"""

USERNAME_PATCHED = """pub fn get_active_username() -> String {
    let name = get_values_of_seat0(&[2]).first().cloned().unwrap_or_default();
    if !name.is_empty() {
        return name;
    }
    // logind is optional on a valid user desktop. This identity is also sent
    // in PeerInfo and used for file-transfer/home lookup, so the fallback must
    // resolve the actual process account rather than trusting environment text.
    let uid = hbb_common::users::get_effective_uid();
    if uid == 0 || hbb_common::users::get_current_uid() != uid {
        return String::new();
    }
    match hbb_common::users::get_user_by_uid(uid) {
        Some(user) if user.uid() == uid => {
            let shell = user.shell();
            if shell.ends_with("false") || shell.ends_with("nologin") {
                String::new()
            } else {
                user.name().to_str().unwrap_or_default().to_owned()
            }
        }
        _ => String::new(),
    }
}"""


def replace_once(source: str, old: str, new: str) -> str:
    if new in source:
        return source
    if source.count(old) != 1:
        raise RuntimeError("Pinned upstream Linux user/session detection changed")
    return source.replace(old, new, 1)


def patch(source: str) -> str:
    source = replace_once(source, ORIGINAL, PATCHED)
    return replace_once(source, USERNAME_ORIGINAL, USERNAME_PATCHED)


if __name__ == "__main__":
    path = Path(os.environ.get("RDREPO", "./rustdesk")) / "src/platform/linux.rs"
    path.write_text(patch(path.read_text(encoding="utf-8")), encoding="utf-8")
    print("   patched Linux prelogin and active-user lookup to use trusted nonroot Unix identity")
