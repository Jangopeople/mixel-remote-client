#!/usr/bin/env python3
"""Use the signed macOS bundle identity for helper names, retaining preferences."""
import argparse
import os
from pathlib import Path
import re


ORIGINAL = '''#[cfg(target_os = "macos")]
pub fn get_full_name() -> String {
    format!(
        "{}.{}",
        hbb_common::config::ORG.read().unwrap(),
        hbb_common::config::APP_NAME.read().unwrap(),
    )
}'''


def replacement(bundle_id: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9-]*)+", bundle_id):
        raise ValueError("Invalid macOS bundle identifier")
    return '''#[cfg(target_os = "macos")]
pub fn get_full_name() -> String {
    // Helper templates use this bundle identity; ORG remains the preference namespace.
    "''' + bundle_id + '''".to_owned()
}'''


def patch(source: Path, bundle_id: str) -> None:
    data = source.read_bytes()
    if data.count(b"pub fn get_full_name() -> String {") != 1:
        raise RuntimeError("macOS helper identity function missing or duplicated")
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    old = ORIGINAL.encode().replace(b"\n", newline)
    new = replacement(bundle_id).encode().replace(b"\n", newline)
    if data.count(old) == 1 and data.count(new) == 0:
        source.write_bytes(data.replace(old, new, 1))
    elif data.count(old) == 0 and data.count(new) == 1:
        return
    else:
        raise RuntimeError("macOS helper identity anchor missing or duplicated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_id")
    args = parser.parse_args()
    patch(Path(os.environ.get("RDREPO", "rustdesk")) / "src/common.rs", args.bundle_id)
    print("PASS: macOS helper identity agrees with the signed bundle; preferences retained")
