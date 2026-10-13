#!/usr/bin/env python3
"""Keep a pinned X11 clipboard subscription alive across rapid owner changes."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import stat

UPSTREAM_URL = "https://github.com/rustdesk-org/clipboard-master"
PIN = "ddc39f00a6211959489ae683aa6ae6eedf03a809"
DEPENDENCY = ".mixel-deps/clipboard-master"
SOURCE = "src/master/x11.rs"
SOURCE_SHA256 = "50c223801ce8c791aa81f59edc5fd5dd5144416df364eed77f7c853e3e3232e2"
PATCH_TABLE = f'\n[patch."{UPSTREAM_URL}"]\nclipboard-master = {{ path = "{DEPENDENCY}" }}\n'
LIB_SOURCE = "src/lib.rs"
LIB_HASHES = {"bc385cb4cbfe35b1aacc471123c49f4077c92df849d521a410233f934c101afe", "71c82f00b19bdda4919e538764fe06365d66c98b622826893d715a6a0ba45a63"}
MARKER = "// Mixel: retain the Xfixes subscription and its initial sequence."


def run(arguments: list[str], directory: Path) -> str:
    return subprocess.run(["git", "-C", str(directory), *arguments], check=True,
                          capture_output=True, text=True, encoding="utf-8", timeout=45).stdout.strip()


def patch_x11(source: str) -> str:
    if MARKER in source:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Pinned clipboard-master X11 source changed")
    source = source.replace("        let mut result = Ok(());\n", "        let mut result = Ok(());\n        let mut subscribed_sequence = None;\n", 1)
    start = source.index("            // Clear selection sources...\n")
    end = source.index("            'poll: loop {", start)
    subscribe = source[start:end].rstrip()
    # Rearming each loop raises the sequence threshold past legitimate events
    # queued during the callback/sleep. Arm once and retain that first threshold.
    source = source[:start] + (
        "            " + MARKER + "\n"
        "            if subscribed_sequence.is_none() {\n"
        + "\n".join("    " + line if line else "" for line in subscribe.splitlines())
        + "\n                subscribed_sequence = Some(sequence_number);\n"
        "            }\n"
        "            let sequence_number = subscribed_sequence.unwrap();\n\n"
    ) + source[end:]
    old = "                    Ok(Some((_, seq))) if seq >= sequence_number => {"
    new = """                    Ok(Some((x11rb::protocol::Event::XfixesSelectionNotify(event), seq)))
                        if seq >= sequence_number && event.selection == selection => {"""
    if source.count(old) != 1:
        raise RuntimeError("Pinned clipboard-master event filter changed")
    return source.replace(old, new, 1)


def patch_manifest(source: str) -> str:
    if PATCH_TABLE in source:
        return source
    if f'[patch."{UPSTREAM_URL}"]' in source:
        raise RuntimeError("Conflicting clipboard-master dependency override")
    if source.count(f'clipboard-master = {{ git = "{UPSTREAM_URL}" }}') != 1:
        raise RuntimeError("Pinned root clipboard-master dependency changed")
    return source.rstrip() + "\n" + PATCH_TABLE


def patch_lock(source: str) -> str:
    packages = list(re.finditer(r'(?ms)^\[\[package\]\]\nname = "clipboard-master"\n.*?(?=^\[\[package\]\]|\Z)', source))
    if len(packages) != 1:
        raise RuntimeError("Pinned clipboard-master lock package missing or duplicated")
    entry = packages[0]
    text = entry.group()
    if '\nversion = "4.0.0-beta.6"\n' not in text:
        raise RuntimeError("Pinned clipboard-master version changed")
    expected_source = f'source = "git+{UPSTREAM_URL}#{PIN}"\n'
    actual_sources = re.findall(r'^source = .*\n', text, flags=re.M)
    if actual_sources and actual_sources != [expected_source]:
        raise RuntimeError("Pinned clipboard-master source revision changed")
    text = text.replace(expected_source, "", 1)
    return source[:entry.start()] + text + source[entry.end():]


def validate_lib(dependency: Path) -> None:
    path = dependency / LIB_SOURCE
    for parent in (dependency, path.parent):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError("Unsafe clipboard library source parent")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise RuntimeError("Unowned clipboard library source")
    raw = path.read_bytes()
    normalized = raw.replace(b'\r\n', b'\n')
    if b'\r' in normalized or (b'\r\n' in raw and b'\n' in raw.replace(b'\r\n', b'')):
        raise RuntimeError("Mixed clipboard library source newlines")
    if hashlib.sha256(normalized).hexdigest() not in LIB_HASHES:
        raise RuntimeError("Pinned clipboard library source changed")


def validate_dependency(dependency: Path):
    if dependency.is_symlink() or run(["rev-parse", "HEAD"], dependency) != PIN:
        raise RuntimeError("Local clipboard-master override must contain the exact locked revision")
    if run(["remote", "get-url", "origin"], dependency) != UPSTREAM_URL:
        raise RuntimeError("Local clipboard-master override has an unexpected upstream")
    if any(path not in (SOURCE, LIB_SOURCE) for path in run(["diff", "--name-only", "HEAD"], dependency).splitlines()):
        raise RuntimeError("Local clipboard-master override contains unrelated source changes")
    if run(["ls-files", "--others"], dependency):
        raise RuntimeError("Local clipboard-master override contains untracked files")
    validate_lib(dependency)
    original = subprocess.run(["git", "-C", str(dependency), "show", "HEAD:" + SOURCE],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout
    expected = patch_x11(original)
    target = dependency / SOURCE
    if target.read_text(encoding="utf-8") not in (original, expected):
        raise RuntimeError("Local clipboard-master X11 override contains unexpected changes")
    return target, expected


def check_existing(repo: Path) -> None:
    """Read-only refusal gate before any composed branding restore."""
    dependency = repo / DEPENDENCY
    manifest = (repo / "Cargo.toml").read_text(encoding="utf-8")
    lock = (repo / "Cargo.lock").read_text(encoding="utf-8")
    patch_manifest(manifest)
    patch_lock(lock)
    if not dependency.exists() and not dependency.is_symlink():
        if PATCH_TABLE in manifest:
            raise RuntimeError("Owned clipboard override checkout missing")
        return
    validate_dependency(dependency)


def apply(repo: Path) -> None:
    manifest, lock = repo / "Cargo.toml", repo / "Cargo.lock"
    new_manifest = patch_manifest(manifest.read_text(encoding="utf-8"))
    new_lock = patch_lock(lock.read_text(encoding="utf-8"))
    dependency = repo / DEPENDENCY
    if not dependency.exists():
        dependency.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="clipboard-master-fetch-", dir=dependency.parent) as temporary:
            fetched = Path(temporary)
            run(["init", "--quiet"], fetched)
            run(["remote", "add", "origin", UPSTREAM_URL], fetched)
            run(["fetch", "--quiet", "--depth", "1", "origin", PIN], fetched)
            run(["checkout", "--quiet", "--detach", PIN], fetched)
            fetched.rename(dependency)
    target, expected = validate_dependency(dependency)
    target.write_text(expected, encoding="utf-8")
    manifest.write_text(new_manifest, encoding="utf-8")
    lock.write_text(new_lock, encoding="utf-8")
    print("   installed pinned local clipboard-master override: rapid X11 changes remain observable")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
