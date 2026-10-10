#!/usr/bin/env python3
"""Execute generated Linux prelogin detection with controlled NSS/UID boundaries."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError("Incomplete generated Linux prelogin function")


with tempfile.TemporaryDirectory(prefix="mixel-linux-tests-") as temporary:
    repo = Path(temporary)
    file = repo / "src/platform/linux.rs"
    file.parent.mkdir(parents=True)
    original = subprocess.run(["git", "-C", str(UPSTREAM), "show", "HEAD:src/platform/linux.rs"],
                              check=True, capture_output=True).stdout
    file.write_bytes(original)
    environment = {**os.environ, "RDREPO": str(repo)}
    command = ["python3", str(ROOT / "scripts/patch-support-linux.py")]
    subprocess.run(command, env=environment, check=True)
    patched = file.read_bytes()
    subprocess.run(command, env=environment, check=True)
    assert file.read_bytes() == patched, "Linux patch must be byte-identical when repeated"
    detection = function(patched.decode(), "pub fn is_prelogin()")
    active_user = function(patched.decode(), "pub fn get_active_username()")
    assert "run_cmds" not in detection and "getent passwd" not in detection
    file.write_bytes(original.replace(b"pub fn is_prelogin()", b"pub fn changed_prelogin()", 1))
    rejected = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert rejected.returncode != 0 and "Pinned upstream Linux user/session detection changed" in rejected.stderr

    # The production function above is compiled unchanged. Only the OS/NSS
    # boundaries are supplied by deterministic fixtures; no account, session,
    # loginctl state or installed service is changed by this regression test.
    harness = r'''
use std::cell::RefCell;
use std::path::{Path, PathBuf};
#[derive(Clone)] struct User { id:u32, shell:PathBuf }
impl User { fn uid(&self)->u32 {self.id} }
trait UserExt {fn shell(&self)->&Path;}
impl UserExt for User {fn shell(&self)->&Path {&self.shell}}
#[derive(Default)] struct State {
 flatpak:bool, name:String, uid:u32, real_uid:u32,
 uid_record:Option<User>, name_record:Option<User>, queries:Vec<String>,
}
thread_local! {static STATE:RefCell<State> = RefCell::new(State::default());}
mod hbb_common {pub mod users {
 use crate::{STATE, User};
 pub fn get_effective_uid()->u32 {STATE.with(|s|s.borrow().uid)}
 pub fn get_current_uid()->u32 {STATE.with(|s|s.borrow().real_uid)}
 pub fn get_user_by_uid(uid:u32)->Option<User> {
  STATE.with(|s| {let mut s=s.borrow_mut();s.queries.push(format!("uid:{uid}"));s.uid_record.clone()})
 }
}}
fn is_flatpak()->bool {STATE.with(|s|s.borrow().flatpak)}
fn get_active_username()->String {STATE.with(|s|s.borrow().name.clone())}
fn get_user_by_name(name:&str)->Option<User> {
 STATE.with(|s| {let mut s=s.borrow_mut();s.queries.push(format!("name:{name}"));s.name_record.clone()})
}
__DETECTION__
fn user(id:u32,shell:&str)->User {User {id,shell:shell.into()}}
fn reset() {STATE.with(|s|*s.borrow_mut()=State {uid:1000,real_uid:1000,..State::default()});}
fn queries()->Vec<String> {STATE.with(|s|s.borrow().queries.clone())}

#[test] fn actual_nonroot_desktop_without_logind_uses_kernel_uid() {
 reset();STATE.with(|s|s.borrow_mut().uid_record=Some(user(1000,"/bin/bash")));
 assert!(!is_prelogin());assert_eq!(queries(),vec!["uid:1000"]);
}
#[test] fn actual_no_seat_root_and_elevated_processes_are_conservative() {
 for (uid,real_uid) in [(0,0),(0,1000),(1000,0),(1000,2000)] {
  reset();STATE.with(|s|{let mut s=s.borrow_mut();s.uid=uid;s.real_uid=real_uid;
   s.uid_record=Some(user(uid,"/bin/bash"));});
  assert!(is_prelogin());assert!(queries().is_empty());
 }
}
#[test] fn actual_uid_account_must_exist_and_match() {
 reset();assert!(is_prelogin());
 reset();STATE.with(|s|s.borrow_mut().uid_record=Some(user(2000,"/bin/bash")));
 assert!(is_prelogin());
}
#[test] fn actual_login_shell_is_checked_from_only_the_selected_account() {
 for shell in ["/bin/false","/usr/bin/false","/usr/sbin/nologin","/sbin/nologin"] {
  reset();STATE.with(|s|s.borrow_mut().uid_record=Some(user(1000,shell)));
  assert!(is_prelogin(),"{shell}");
 }
 for shell in ["/bin/bash","/usr/bin/zsh","/bin/sh","","/bin/notfalse"] {
  reset();STATE.with(|s|s.borrow_mut().uid_record=Some(user(1000,shell)));
  assert!(!is_prelogin(),"{shell}");
 }
}
#[test] fn actual_identified_active_session_preserves_login_and_prelogin() {
 for (shell,prelogin) in [("/bin/bash",false),("/usr/sbin/nologin",true)] {
  reset();STATE.with(|s|{let mut s=s.borrow_mut();s.uid=0;s.real_uid=0;
   s.name="selected-session".into();s.name_record=Some(user(120,shell));});
  assert_eq!(is_prelogin(),prelogin);assert_eq!(queries(),vec!["name:selected-session"]);
 }
 reset();STATE.with(|s|s.borrow_mut().name="missing-session".into());assert!(is_prelogin());
}
#[test] fn actual_session_name_cannot_execute_shell_or_use_environment_identity() {
 reset();let name="fixture; $(touch /should-never-exist)";
 STATE.with(|s|{let mut s=s.borrow_mut();s.name=name.into();s.name_record=Some(user(1000,"/bin/bash"));});
 assert!(!is_prelogin());assert_eq!(queries(),vec![format!("name:{name}")]);
 reset();std::env::set_var("USER","root");std::env::set_var("LOGNAME","root");
 STATE.with(|s|s.borrow_mut().uid_record=Some(user(1000,"/bin/bash")));
 assert!(!is_prelogin());assert_eq!(queries(),vec!["uid:1000"]);
 std::env::remove_var("USER");std::env::remove_var("LOGNAME");
}
#[test] fn actual_flatpak_behavior_is_preserved() {
 reset();STATE.with(|s|s.borrow_mut().flatpak=true);
 assert!(!is_prelogin());assert!(queries().is_empty());
}
'''.replace("__DETECTION__", detection)
    source = repo / "linux-prelogin-tests.rs"
    source.write_text(harness, encoding="utf-8")
    binary = repo / ("linux-prelogin-tests.exe" if os.name == "nt" else "linux-prelogin-tests")
    subprocess.run(["rustc", "--edition=2021", "--deny", "warnings", "--test", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)

    # Execute the actual source used to populate PeerInfo.username and resolve
    # the customer's home directory; testing is_prelogin alone misses the file
    # transfer rejection on an otherwise valid desktop without a seat0 record.
    account_harness = r'''
use std::cell::RefCell;
use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
#[derive(Clone)] struct User { id:u32, name:OsString, shell:PathBuf }
impl User {fn uid(&self)->u32 {self.id} fn name(&self)->&OsStr {&self.name}}
trait UserExt {fn shell(&self)->&Path;}
impl UserExt for User {fn shell(&self)->&Path {&self.shell}}
#[derive(Default)] struct State {seat:Vec<String>,uid:u32,real_uid:u32,record:Option<User>,queries:Vec<u32>}
thread_local! {static STATE:RefCell<State> = RefCell::new(State::default());}
mod hbb_common {pub mod users {
 use crate::{STATE,User};
 pub fn get_effective_uid()->u32 {STATE.with(|s|s.borrow().uid)}
 pub fn get_current_uid()->u32 {STATE.with(|s|s.borrow().real_uid)}
 pub fn get_user_by_uid(uid:u32)->Option<User> {STATE.with(|s|{let mut s=s.borrow_mut();s.queries.push(uid);s.record.clone()})}
}}
fn get_values_of_seat0(indices:&[usize])->Vec<String> {assert_eq!(indices,&[2]);STATE.with(|s|s.borrow().seat.clone())}
__ACTIVE_USER__
fn reset() {STATE.with(|s|*s.borrow_mut()=State {uid:1000,real_uid:1000,..State::default()});}
fn account(id:u32,name:OsString,shell:&str)->User {User {id,name,shell:shell.into()}}
fn select(shell:&str) {STATE.with(|s|s.borrow_mut().record=Some(account(1000,"guest".into(),shell)));}
#[test] fn actual_no_logind_identity_populates_peer_info_and_home_lookup() {
 reset();select("/bin/bash");assert_eq!(get_active_username(),"guest");
 STATE.with(|s|assert_eq!(s.borrow().queries,vec![1000]));
 reset();STATE.with(|s|s.borrow_mut().seat=vec![String::new()]);select("/bin/bash");
 assert_eq!(get_active_username(),"guest");
}
#[test] fn actual_active_logind_session_takes_priority_even_for_root_service() {
 reset();STATE.with(|s|{let mut s=s.borrow_mut();s.uid=0;s.real_uid=0;s.seat=vec!["actual-session".into()];});
 assert_eq!(get_active_username(),"actual-session");STATE.with(|s|assert!(s.borrow().queries.is_empty()));
}
#[test] fn actual_fallback_never_selects_root_or_mismatched_effective_identity() {
 for (uid,real_uid) in [(0,0),(0,1000),(1000,0),(1000,2000)] {
  reset();STATE.with(|s|{let mut s=s.borrow_mut();s.uid=uid;s.real_uid=real_uid;s.record=Some(account(uid,"root".into(),"/bin/bash"));});
  assert!(get_active_username().is_empty());STATE.with(|s|assert!(s.borrow().queries.is_empty()));
 }
}
#[test] fn actual_fallback_rejects_unknown_wrong_uid_and_login_disabled_accounts() {
 reset();assert!(get_active_username().is_empty());
 reset();STATE.with(|s|s.borrow_mut().record=Some(account(2000,"other".into(),"/bin/bash")));
 assert!(get_active_username().is_empty());
 for shell in ["/bin/false","/usr/bin/false","/sbin/nologin","/usr/sbin/nologin"] {
  reset();select(shell);assert!(get_active_username().is_empty());
 }
}
#[test] fn actual_fallback_never_uses_untrusted_environment_or_lossy_names() {
 reset();select("/bin/bash");std::env::set_var("USER","forged");std::env::set_var("LOGNAME","forged");
 assert_eq!(get_active_username(),"guest");std::env::remove_var("USER");std::env::remove_var("LOGNAME");
 #[cfg(unix)] {
  use std::os::unix::ffi::OsStringExt;
  reset();STATE.with(|s|s.borrow_mut().record=Some(account(1000,OsString::from_vec(vec![255]),"/bin/bash")));
  assert!(get_active_username().is_empty());
 }
}
'''.replace("__ACTIVE_USER__", active_user)
    source.write_text(account_harness, encoding="utf-8")
    subprocess.run(["rustc", "--edition=2021", "--deny", "warnings", "--test", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)

print("PASS: actual generated Linux prelogin and PeerInfo/home identity support nonroot desktops without logind and keep privileged cases conservative")
