#!/usr/bin/env python3
"""Select the local layout for eligible first-use native X11 Windows sessions."""
import argparse
import hashlib
import os
from pathlib import Path
import stat

CLIENT_SHA = "7ae3387a825a721c1f8a6fbe236c1810b2ff6e4aca7c09083f9e7f68c7283c47"
# The late branding hook also accepts the exact secure/network-corrected source.
# No partial/foreign earlier state can match either complete canonical hash.
COMPOSED_CLIENT_SHA = "f8cfbf83ab944d676f073d63d2d51cb2568f4328970b984405c6cca9ead7938d"
CANONICAL_CLIENT_HASHES = (CLIENT_SHA, COMPOSED_CLIENT_SHA)
OLD = """        if config.keyboard_mode.is_empty() {
            if is_keyboard_mode_supported(
                &KeyboardMode::Map,
                get_version_number(&pi.version),
                &pi.platform,
            ) {
                config.keyboard_mode = KeyboardMode::Map.to_string();
            } else {
                config.keyboard_mode = KeyboardMode::Legacy.to_string();
            }
        }"""
NEW = """        if config.keyboard_mode.is_empty() {
            // This first-use default follows the operator's local layout only
            // on the native X11 -> Windows path. Retain explicit peer choices.
            #[cfg(all(target_os = "linux", feature = "flutter"))]
            let first_use_translate = pi.platform == "Windows"
                && crate::platform::linux::is_x11()
                && crate::keyboard::input_source::get_cur_session_input_source()
                    == crate::keyboard::input_source::CONFIG_INPUT_SOURCE_1
                && is_keyboard_mode_supported(
                    &KeyboardMode::Translate,
                    get_version_number(&pi.version),
                    &pi.platform,
                );
            #[cfg(not(all(target_os = "linux", feature = "flutter")))]
            let first_use_translate = false;
            if first_use_translate {
                config.keyboard_mode = KeyboardMode::Translate.to_string();
            } else if is_keyboard_mode_supported(
                &KeyboardMode::Map,
                get_version_number(&pi.version),
                &pi.platform,
            ) {
                config.keyboard_mode = KeyboardMode::Map.to_string();
            } else {
                config.keyboard_mode = KeyboardMode::Legacy.to_string();
            }
        }"""


def transform(data: bytes, restore: bool = False) -> bytes:
    text = data.decode("utf-8")
    canonical = text.replace("\r\n", "\n")
    if "\r" in canonical or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise RuntimeError("Mixed keyboard default source newlines")
    original = canonical
    if NEW in canonical:
        if canonical.count(NEW) != 1 or OLD in canonical:
            raise RuntimeError("Mixed keyboard first-use source")
        original = canonical.replace(NEW, OLD, 1)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() not in CANONICAL_CLIENT_HASHES or original.count(OLD) != 1:
        raise RuntimeError("Pinned keyboard first-use source changed")
    result = original if restore else original.replace(OLD, NEW, 1)
    return result.replace("\n", "\r\n" if "\r\n" in text else "\n").encode("utf-8")


def apply(repo: Path, restore: bool = False, check: bool = False) -> None:
    repo = repo.absolute()
    target = repo / "src/client.rs"
    for path in (*reversed(repo.parents), repo, repo / "src", target):
        record = path.lstat()
        if stat.S_ISLNK(record.st_mode) or getattr(record, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Keyboard default source ownership changed")
        if path == target:
            if not stat.S_ISREG(record.st_mode) or record.st_nlink != 1:
                raise RuntimeError("Keyboard default requires a single-link regular source")
        elif not stat.S_ISDIR(record.st_mode):
            raise RuntimeError("Keyboard default source parent is not a directory")
    data = target.read_bytes()
    corrected = transform(data, restore)
    if not check and corrected != data:
        target.write_bytes(corrected)
    print("Keyboard first-use default verified: native Linux X11/source1 to supported Windows only; explicit choices preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--restore", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--repo", type=Path, default=Path(os.environ.get("RDREPO", "./rustdesk")))
    args = parser.parse_args()
    apply(args.repo, args.restore, args.check)
