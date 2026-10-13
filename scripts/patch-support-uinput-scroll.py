#!/usr/bin/env python3
"""Forward Linux uinput wheel detents with bounded signed-count packets."""
import argparse
import hashlib
import os
from pathlib import Path
import stat

PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
TARGET = "src/server/uinput.rs"
CANONICAL_HASHES = (
    "ef9dfa4600f15d5ae05465560af8ca584e2b525108b32e270bd80f12072b1a57",
    "95e5cf0fd0f33cd3a4e81588d8a935734db1bff98ea67fb21ef3d9b3936e3563",
)
OLD = """            DataMouse::ScrollX(_length) => {
                // TODO: not supported for now
            }"""
NEW = """            DataMouse::ScrollX(length) => {
                allow_err!(mouse.scroll_wheel_horizontal(*length))
            }"""
WHEEL = """        pub fn scroll_wheel(&self, direction: &ScrollDirection) -> Result<()> {
            let (code, scroll_value) = match direction {
                ScrollDirection::Up => (REL_WHEEL, 1),
                ScrollDirection::Down => (REL_WHEEL, -1),
                ScrollDirection::Left => (REL_HWHEEL, -1),
                ScrollDirection::Right => (REL_HWHEEL, 1),
            };
            self.emit(EV_REL, code as c_int, scroll_value)?;
            self.syncronize()
        }"""
OLD_VERTICAL = """            DataMouse::ScrollY(length) => {
                let mut length = *length;

                let scroll = if length < 0 {
                    mouce::ScrollDirection::Up
                } else {
                    mouce::ScrollDirection::Down
                };

                if length < 0 {
                    length = -length;
                }

                for _ in 0..length {
                    allow_err!(mouse.scroll_wheel(&scroll))
                }
            }"""
NEW_VERTICAL = """            DataMouse::ScrollY(length) => {
                allow_err!(mouse.scroll_wheel_vertical(*length))
            }"""
HORIZONTAL = """        // Legacy wheel values are signed detent counts, not pixels. Keep
        // unit packets unchanged and bound larger requests without new rights.
        pub fn scroll_wheel_horizontal(&self, detents: i32) -> Result<()> {
            match detents {
                0 => Ok(()),
                1 => self.scroll_wheel(&ScrollDirection::Right),
                -1 => self.scroll_wheel(&ScrollDirection::Left),
                _ => {
                    self.emit(EV_REL, REL_HWHEEL as c_int, detents)?;
                    self.syncronize()
                }
            }
        }

"""
VERTICAL = """        pub fn scroll_wheel_vertical(&self, detents: i32) -> Result<()> {
            match detents {
                0 => Ok(()),
                1 => self.scroll_wheel(&ScrollDirection::Down),
                -1 => self.scroll_wheel(&ScrollDirection::Up),
                i32::MIN => {
                    // Up is positive REL_WHEEL. Its 2^31 detents need two
                    // representable counts, each with the existing SYN packet.
                    self.emit(EV_REL, REL_WHEEL as c_int, i32::MAX)?;
                    self.syncronize()?;
                    self.emit(EV_REL, REL_WHEEL as c_int, 1)?;
                    self.syncronize()
                }
                _ => {
                    self.emit(EV_REL, REL_WHEEL as c_int, -detents)?;
                    self.syncronize()
                }
            }
        }

"""


def transform(data: bytes, restore: bool = False) -> bytes:
    text = data.decode("utf-8")
    canonical = text.replace("\r\n", "\n")
    if "\r" in canonical or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise RuntimeError("Mixed uinput source newlines")
    original = canonical
    corrected_parts = (NEW, NEW_VERTICAL, HORIZONTAL, VERTICAL)
    if any(part in canonical for part in corrected_parts):
        if any(canonical.count(part) != 1 for part in corrected_parts) or OLD in canonical or OLD_VERTICAL in canonical:
            raise RuntimeError("Mixed uinput wheel source")
        original = canonical.replace(NEW, OLD, 1).replace(NEW_VERTICAL, OLD_VERTICAL, 1).replace(HORIZONTAL, "", 1).replace(VERTICAL, "", 1)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() not in CANONICAL_HASHES or original.count(OLD) != 1 or original.count(OLD_VERTICAL) != 1 or original.count(WHEEL) != 1:
        raise RuntimeError("Pinned uinput wheel source changed")
    result = original if restore else original.replace(OLD, NEW, 1).replace(OLD_VERTICAL, NEW_VERTICAL, 1).replace(WHEEL, HORIZONTAL + VERTICAL + WHEEL, 1)
    return result.replace("\n", "\r\n" if "\r\n" in text else "\n").encode("utf-8")


def apply(repo: Path, restore: bool = False, check: bool = False) -> None:
    repo = repo.absolute()
    target = repo / TARGET
    for path in (*reversed(repo.parents), repo, repo / "src", repo / "src/server", target):
        record = path.lstat()
        if stat.S_ISLNK(record.st_mode) or getattr(record, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Uinput source ownership changed")
        if path == target:
            if not stat.S_ISREG(record.st_mode) or record.st_nlink != 1:
                raise RuntimeError("Uinput requires a single-link regular source")
        elif not stat.S_ISDIR(record.st_mode):
            raise RuntimeError("Uinput source parent is not a directory")
    data = target.read_bytes()
    corrected = transform(data, restore)
    if not check and corrected != data:
        target.write_bytes(corrected)
    print("Uinput wheel verified: bounded signed detents; existing unit emitter, device setup and rights preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(os.environ.get("RDREPO", "./rustdesk")))
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    apply(args.repo, args.restore, args.check)
