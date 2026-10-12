#!/usr/bin/env python3
"""Correct the pinned build-local rdev Mac event-tap ABI and borrowed lifetime."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess

PIN = "f9b60b1dd0f3300a1b797d7a74c116683cd232c8"
UPSTREAM = "https://github.com/rustdesk-org/rdev"
DEPENDENCY = ".mixel-deps/rdev"
INVENTORY = "7c9eae7c461ae68462bfa0523c2947065aecefc95f5a468f1adedcfba9d5e924"
FILES = {
    "Cargo.toml": "efc78f723dc65c5d791b043d8387ecb492a403b7355ae02295ecaceb6906b99f",
    "src/macos/common.rs": "b01f64880ec9d8490e8d18b0452c81ceb46b4cdb81b1f885640dcdd006e17308",
    "src/macos/grab.rs": "7c0f71b9dcccb7b9c09b339c92408cc63fa9103b3b95bc661b97560b931a1ac8",
    "src/macos/listen.rs": "4753161aee5595421f54c518e9c60fd01c815b0db8bad9d52a248b6ec6bad6c5",
}
RDEV_ENTRY = "ef63b60dfa6ab1c8a08dd61424fdc51bcd10042cdffa2e405ecaf1a3ad86762e"
LOCKED = {
    ("core-graphics", "0.22.3"): "df8f67da2266d17dd5d461a3f8dcecd7210fcab2253f379499cf22d8438c8c60",
    ("foreign-types", "0.3.2"): "f1cd7d6f98f11b85dd5118ce6b4d85d4074df85e8131efb87b417be991166991",
}
MAC_DEPENDENCY = 'foreign-types = "=0.3.2"\n'
EDGE = ' "foreign-types 0.3.2",\n'

# CGEvent's public methods are on the owned wrapper in core-graphics 0.22.3.
# The facade never owns the caller's event: from_ptr is immediately protected
# by ManuallyDrop before conversion or user code can unwind.
BORROW = r'''
// The C ABI supplies an opaque borrowed pointer, never an owned Rust wrapper.
// Unknown types and disabled-tap notifications must not become invalid enums.
fn callback_event_type(value: u32) -> Option<CGEventType> {
    match value {
        0 => Some(CGEventType::Null),
        1 => Some(CGEventType::LeftMouseDown),
        2 => Some(CGEventType::LeftMouseUp),
        3 => Some(CGEventType::RightMouseDown),
        4 => Some(CGEventType::RightMouseUp),
        5 => Some(CGEventType::MouseMoved),
        6 => Some(CGEventType::LeftMouseDragged),
        7 => Some(CGEventType::RightMouseDragged),
        10 => Some(CGEventType::KeyDown),
        11 => Some(CGEventType::KeyUp),
        12 => Some(CGEventType::FlagsChanged),
        22 => Some(CGEventType::ScrollWheel),
        23 => Some(CGEventType::TabletPointer),
        24 => Some(CGEventType::TabletProximity),
        25 => Some(CGEventType::OtherMouseDown),
        26 => Some(CGEventType::OtherMouseUp),
        27 => Some(CGEventType::OtherMouseDragged),
        _ => None,
    }
}

fn report_callback_panic(payload: Box<dyn std::any::Any + Send>) {
    // A user-supplied panic payload can itself panic in Drop. No destructor
    // or diagnostic formatter may unwind across the C event-tap boundary.
    std::mem::forget(payload);
    let report = std::panic::catch_unwind(|| {
        let _ = std::io::Write::write_all(
            &mut std::io::stderr(),
            b"Mixel Remote: contained panic in Mac input callback; event passed through\n",
        );
    });
    if let Err(payload) = report {
        std::mem::forget(payload);
    }
}

pub unsafe fn with_borrowed_event<F>(kind: u32, event: CGEventRef, callback: F) -> CGEventRef
where
    F: FnOnce(CGEventType, &CGEvent, &mut Keyboard),
{
    let kind = match callback_event_type(kind) {
        Some(kind) if !event.is_null() => kind,
        _ => return event,
    };
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let borrowed = std::mem::ManuallyDrop::new(CGEvent::from_ptr(event));
        if let Ok(mut state) = KEYBOARD_STATE.lock() {
            if let Some(keyboard) = state.as_mut() {
                // Keep the mutex guard outside the unwinding scope so a
                // contained client panic does not permanently poison input.
                return std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    callback(kind, &borrowed, keyboard);
                })).err();
            }
        }
        None
    }));
    match result {
        Ok(Some(payload)) | Err(payload) => report_callback_panic(payload),
        Ok(None) => {}
    }
    event
}
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *arguments], timeout=45)


def normalized(data: bytes) -> tuple[str, str]:
    text = data.decode("utf-8")
    if "\r" in text.replace("\r\n", ""):
        raise RuntimeError("Mixed or unsupported dependency newlines")
    if "\r\n" in text and "\n" in text.replace("\r\n", ""):
        raise RuntimeError("Mixed or unsupported dependency newlines")
    return text.replace("\r\n", "\n"), "\r\n" if "\r\n" in text else "\n"


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError("Pinned Mac input source changed")
    return text.replace(old, new, 1)


def patch(relative: str, original: str) -> str:
    if relative == "Cargo.toml":
        return replace_once(original, 'dispatch = "0.2"\n', 'dispatch = "0.2"\n' + MAC_DEPENDENCY)
    if relative == "src/macos/common.rs":
        text = replace_once(original, "use lazy_static::lazy_static;", "use foreign_types::ForeignType;\nuse lazy_static::lazy_static;")
        text = replace_once(text, "pub type CGEventTapProxy = id;\npub type CGEventRef = CGEvent;", "pub type CGEventTapProxy = *mut c_void;\npub type CGEventRef = core_graphics::sys::CGEventRef;")
        text = replace_once(text, "    #[allow(improper_ctypes)]\n    pub fn CGEventTapCreate(", "    pub fn CGEventTapCreate(")
        text = replace_once(text, "    _type: CGEventType,\n    cg_event: CGEventRef,", "    _type: u32,\n    cg_event: CGEventRef,")
        return replace_once(text, "#[cfg(target_os = \"macos\")]\n#[inline]\nfn kb_get_layout_type()", BORROW + "\n#[cfg(target_os = \"macos\")]\n#[inline]\nfn kb_get_layout_type()")
    if relative not in ("src/macos/grab.rs", "src/macos/listen.rs"):
        raise RuntimeError("Unowned Mac input path")
    text = replace_once(original, "#![allow(improper_ctypes_definitions)]\n", "")
    start, end = text.index('unsafe extern "C" fn raw_callback('), text.index("\n}\n", text.index('unsafe extern "C" fn raw_callback(')) + 3
    body = """unsafe extern "C" fn raw_callback(
    _proxy: CGEventTapProxy,
    _type: u32,
    cg_event: CGEventRef,
    _user_info: *mut c_void,
) -> CGEventRef {
    with_borrowed_event(_type, cg_event, |kind, borrowed, keyboard| {
        if let Some(event) = convert(kind, borrowed, keyboard) {
            // The existing single-install callback is accessed only while
            // with_borrowed_event holds the keyboard serialization guard.
            let callbacks = std::ptr::addr_of_mut!(GLOBAL_CALLBACK);
            if let Some(callback) = (*callbacks).as_mut() {
__CALLBACK__
            }
        }
    })
}
"""
    action = """                if callback(event).is_none() {
                    borrowed.set_type(CGEventType::Null);
                }""" if relative.endswith("grab.rs") else "                callback(event);"
    return text[:start] + body.replace("__CALLBACK__", action) + text[end:]


def entries(text: str) -> list[re.Match]:
    return list(re.finditer(r"(?ms)^\[\[package\]\]\n.*?(?=^\[\[package\]\]|\Z)", text))


def lock_pair(text: str) -> tuple[str, str]:
    packages = entries(text)
    for (name, version), expected in LOCKED.items():
        found = [m.group() for m in packages if f'name = "{name}"\nversion = "{version}"\n' in m.group()]
        if len(found) != 1 or digest(found[0].encode()) != expected:
            raise RuntimeError("Locked Mac ABI dependency drift")
    found = [m for m in packages if '\nname = "rdev"\n' in m.group()]
    if len(found) != 1:
        raise RuntimeError("Pinned rdev lock entry missing or duplicated")
    entry = found[0]
    raw = entry.group().replace(EDGE, "", 1)
    source = f'source = "git+{UPSTREAM}#{PIN}"\n'
    if source not in raw:
        raw = replace_once(raw, 'version = "0.5.0-2"\n', 'version = "0.5.0-2"\n' + source)
    if digest(raw.encode()) != RDEV_ENTRY:
        raise RuntimeError("Pinned rdev dependency edges changed")
    old = raw.replace(source, "", 1)
    new = replace_once(old, ' "epoll",\n', ' "epoll",\n' + EDGE)
    if entry.group() not in (raw, old, new):
        raise RuntimeError("Unowned rdev lock state")
    return text[:entry.start()] + old + text[entry.end():], text[:entry.start()] + new + text[entry.end():]


def regular(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise RuntimeError("Symlink in owned Mac input dependency")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Nonregular owned Mac input file")
    if metadata.st_nlink != 1:
        raise RuntimeError("Hardlinked owned Mac input file")


def apply(repo: Path, restore: bool = False, check: bool = False) -> None:
    # macOS exposes /var temporary directories through the system /private
    # alias. Canonicalize the workspace's outside prefix, while refusing a
    # symlink workspace or anything linked inside the owned dependency.
    if repo.is_symlink():
        raise RuntimeError("Symlink workspace for owned Mac input dependency")
    repo = repo.resolve()
    dependency = repo / DEPENDENCY
    for path in (dependency, *dependency.parents):
        if path.is_symlink():
            raise RuntimeError("Symlink in owned Mac input dependency")
    if not dependency.exists():
        if dependency.is_symlink() or not restore:
            raise RuntimeError("Pinned build-local rdev override is required")
        # A fresh checkout has no local override. A missing owned checkout is
        # corruption, not a fresh state that may restore other overlays first.
        manifest, lock = repo / "Cargo.toml", repo / "Cargo.lock"
        regular(manifest)
        regular(lock)
        manifest_text, _ = normalized(manifest.read_bytes())
        lock_text, _ = normalized(lock.read_bytes())
        if (f'[patch."{UPSTREAM}"]' in manifest_text or DEPENDENCY in manifest_text or
                manifest_text.count(f'rdev = {{ git = "{UPSTREAM}" }}') != 1):
            raise RuntimeError("Missing owned rdev dependency checkout")
        lock_pair(lock_text)
        rdev = [entry.group() for entry in entries(lock_text) if '\nname = "rdev"\n' in entry.group()]
        if len(rdev) != 1 or digest(rdev[0].encode()) != RDEV_ENTRY:
            raise RuntimeError("Missing owned rdev dependency checkout")
        return
    for path in (dependency, dependency / ".git"):
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError("Unowned rdev checkout")
    if git(dependency, "rev-parse", "HEAD").decode().strip() != PIN or git(dependency, "remote", "get-url", "origin").decode().strip() != UPSTREAM:
        raise RuntimeError("Unowned rdev revision or origin")
    inventory = git(dependency, "ls-tree", "-r", "HEAD")
    if digest(inventory) != INVENTORY:
        raise RuntimeError("Pinned rdev inventory changed")
    if git(dependency, "diff", "--cached", "--name-only", "HEAD"):
        raise RuntimeError("Staged rdev dependency source changes")
    if git(dependency, "ls-files", "--others"):
        raise RuntimeError("Untracked rdev dependency files")
    spec = importlib.util.spec_from_file_location("mixel_rdev_input", Path(__file__).with_name("patch-support-input.py"))
    input_patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(input_patch)
    planned, states = [], []
    for row in inventory.decode().splitlines():
        mode, _kind, _blob, relative = re.split(r"[ \t]", row, maxsplit=3)
        target = dependency / relative
        regular(target)
        if mode not in ("100644", "100755"):
            raise RuntimeError("Unexpected rdev tracked file type")
        if os.name != "nt" and bool(target.stat().st_mode & 0o111) != (mode == "100755"):
            raise RuntimeError("Unexpected rdev tracked file permissions")
        original = git(dependency, "show", "HEAD:" + relative)
        actual = target.read_bytes()
        if relative in FILES:
            if digest(original) != FILES[relative]:
                raise RuntimeError("Pinned Mac callback bytes changed")
            old = original.decode("utf-8")
            new = patch(relative, old)
            canonical, newline = normalized(actual)
            if canonical not in (old, new):
                raise RuntimeError("Unowned Mac callback source changes")
            states.append("old" if canonical == old else "new")
            planned.append((target, (old if restore else new).replace("\n", newline).encode()))
        elif relative == "src/linux/grab.rs":
            canonical, _newline = normalized(actual)
            if canonical != input_patch.patch_grab(original.decode("utf-8")):
                raise RuntimeError("Pinned Linux input overlay is required")
        else:
            canonical, _newline = normalized(actual)
            if canonical != original.decode("utf-8"):
                raise RuntimeError("Unrelated rdev source drift")
    if len(set(states)) != 1:
        raise RuntimeError("Mixed owned Mac callback state")
    manifest, lock = repo / "Cargo.toml", repo / "Cargo.lock"
    regular(manifest)
    regular(lock)
    manifest_text, _ = normalized(manifest.read_bytes())
    if input_patch.PATCH_TABLE not in manifest_text or manifest_text.count(f'[patch."{UPSTREAM}"]') != 1:
        raise RuntimeError("Exact owned Cargo rdev override is required")
    lock_text, newline = normalized(lock.read_bytes())
    old_lock, new_lock = lock_pair(lock_text)
    if (states[0] == "new") != (lock_text == new_lock):
        raise RuntimeError("Mixed owned Mac callback lock state")
    planned.append((lock, (old_lock if restore else new_lock).replace("\n", newline).encode()))
    # Every dependency path, source state and locked package is validated
    # before the first write. Preserve the caller's homogeneous newlines.
    if check:
        return
    for path, data in planned:
        if path.read_bytes() != data:
            path.write_bytes(data)
    print("   restored exact Mac input callback baseline" if restore else "   corrected pinned Mac input callback ABI, borrowed lifetime and panic boundary")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true", help="Validate the complete owned state without writing")
    parser.add_argument("repo", nargs="?", type=Path, default=Path(os.environ.get("RDREPO", "./rustdesk")))
    arguments = parser.parse_args()
    apply(arguments.repo.absolute(), arguments.restore, arguments.check)
