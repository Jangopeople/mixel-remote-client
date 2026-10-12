#!/usr/bin/env python3
"""Patch locked rdev input capture and the Android physical grave-key mapping."""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

UPSTREAM_URL = "https://github.com/rustdesk-org/rdev"
PIN = "f9b60b1dd0f3300a1b797d7a74c116683cd232c8"
DEPENDENCY = ".mixel-deps/rdev"
PATCH_TABLE = f'\n[patch."{UPSTREAM_URL}"]\nrdev = {{ path = "{DEPENDENCY}" }}\n'
ANDROID_PATH = "src/keycodes/android.rs"
ANDROID_SHA256 = "461b84c7676baf5cc3356bfba9ead2c8ce32a52d8a403843978b1c626ded9c92"
OLD = """            Err(e) => {
                log::error!("Failed to poll event, {}", e);
                break;
            }"""
NEW = """            // Signals can interrupt mio without invalidating the X11
            // connection. Keep the same active keyboard grab and retry.
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => {
                log::error!("Failed to poll event, {}", e);
                break;
            }"""


def run(arguments: list[str], directory: Path) -> str:
    result = subprocess.run(["git", "-C", str(directory), *arguments],
                            check=True, capture_output=True, text=True, encoding="utf-8", timeout=45)
    return result.stdout.strip()


def patch_grab(source: str) -> str:
    if NEW in source:
        return source
    if source.count(OLD) != 1:
        raise RuntimeError("Pinned rdev X11 poll loop changed")
    return source.replace(OLD, NEW, 1)


def patch_android(source: str) -> str:
    # Android KEYCODE_GRAVE is 68; KEYCODE_APOSTROPHE remains 75.
    # https://developer.android.com/reference/android/view/KeyEvent#KEYCODE_GRAVE
    original = source.replace("    BackQuote, 68,\n", "    BackQuote, 75,\n")
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != ANDROID_SHA256 or original.count("    BackQuote, 75,\n") != 1:
        raise RuntimeError("Pinned rdev Android mapping table changed")
    return original.replace("    BackQuote, 75,\n", "    BackQuote, 68,\n", 1)


def android_bytes(source: bytes) -> bytes:
    text = source.decode("utf-8")
    canonical = text.replace("\r\n", "\n")
    if "\r" in canonical or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise RuntimeError("Mixed or unsupported Android mapping newlines")
    newline = "\r\n" if "\r\n" in text else "\n"
    return patch_android(canonical).replace("\n", newline).encode("utf-8")


def patch_manifest(source: str) -> str:
    if PATCH_TABLE in source:
        return source
    if f'[patch."{UPSTREAM_URL}"]' in source:
        raise RuntimeError("Conflicting rdev dependency override")
    if source.count(f'rdev = {{ git = "{UPSTREAM_URL}" }}') != 1:
        raise RuntimeError("Pinned root rdev dependency changed")
    return source.rstrip() + "\n" + PATCH_TABLE


def patch_lock(source: str) -> str:
    # Only rdev changes from the exact locked Git package to the patched local
    # package. Keep all versions and dependency edges for locked Cargo builds.
    packages = list(re.finditer(r'(?ms)^\[\[package\]\]\nname = "rdev"\n.*?(?=^\[\[package\]\]|\Z)', source))
    if len(packages) != 1:
        raise RuntimeError("Pinned rdev lock package missing or duplicated")
    entry = packages[0]
    text = entry.group()
    if '\nversion = "0.5.0-2"\n' not in text:
        raise RuntimeError("Pinned rdev version changed")
    expected_source = f'source = "git+{UPSTREAM_URL}#{PIN}"\n'
    actual_sources = re.findall(r'^source = .*\n', text, flags=re.M)
    if actual_sources and actual_sources != [expected_source]:
        raise RuntimeError("Pinned rdev source revision changed")
    text = text.replace(expected_source, "", 1)
    return source[:entry.start()] + text + source[entry.end():]


def apply(repo: Path) -> None:
    manifest, lock = repo / "Cargo.toml", repo / "Cargo.lock"
    manifest_text, lock_text = manifest.read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")
    # Check compatibility before fetching or writing anything.
    new_manifest, new_lock = patch_manifest(manifest_text), patch_lock(lock_text)
    dependency = repo / DEPENDENCY
    if not dependency.exists():
        dependency.parent.mkdir(parents=True, exist_ok=True)
        # A failed fetch leaves no half-created override for the next run.
        with tempfile.TemporaryDirectory(prefix="rdev-fetch-", dir=dependency.parent) as temporary:
            fetched = Path(temporary)
            run(["init", "--quiet"], fetched)
            run(["remote", "add", "origin", UPSTREAM_URL], fetched)
            run(["fetch", "--quiet", "--depth", "1", "origin", PIN], fetched)
            run(["checkout", "--quiet", "--detach", PIN], fetched)
            fetched.rename(dependency)
    if dependency.is_symlink() or run(["rev-parse", "HEAD"], dependency) != PIN:
        raise RuntimeError("Local rdev override must contain the exact locked revision")
    if run(["remote", "get-url", "origin"], dependency) != UPSTREAM_URL:
        raise RuntimeError("Local rdev override has an unexpected upstream")
    modified = run(["diff", "--name-only", "HEAD"], dependency).splitlines()
    if any(path not in ("src/linux/grab.rs", ANDROID_PATH) for path in modified):
        raise RuntimeError("Local rdev override contains unrelated source changes")
    # Cargo can implicitly execute an added build.rs, even if tracked files
    # are untouched. This checkout has no build outputs or generated files.
    if run(["ls-files", "--others"], dependency):
        raise RuntimeError("Local rdev override contains untracked files")
    original = subprocess.run(["git", "-C", str(dependency), "show", "HEAD:src/linux/grab.rs"],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout
    expected = patch_grab(original)
    target = dependency / "src/linux/grab.rs"
    if target.read_text(encoding="utf-8") not in (original, expected):
        raise RuntimeError("Local rdev X11 override contains unexpected changes")
    android = dependency / ANDROID_PATH
    try:
        record = android.lstat()
    except FileNotFoundError as error:
        raise RuntimeError("Missing pinned Android mapping source") from error
    if not stat.S_ISREG(record.st_mode) or record.st_nlink != 1 or getattr(record, "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Android mapping source must be an owned ordinary file")
    old_android = android.read_bytes()
    new_android = android_bytes(old_android)
    # Validate both input changes before the first source/manifest/lock write.
    target.write_text(expected, encoding="utf-8")
    if old_android != new_android:
        android.write_bytes(new_android)
    manifest.write_text(new_manifest, encoding="utf-8")
    lock.write_text(new_lock, encoding="utf-8")
    print("   installed pinned local rdev override: interrupted X11 polls retain keyboard capture; Android grave/apostrophe keys remain distinct")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
