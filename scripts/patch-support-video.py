#!/usr/bin/env python3
"""Correct the pinned AV1 variadic tile-control argument type, and only that."""
import hashlib
import os
from pathlib import Path
import re

VERSION = "1.4.6"
SOURCE = "libs/scrap/src/common/aom.rs"
ORIGINAL_SHA256 = "f56cfeca5d904e5014b62ed5c3cdaa50ec9c70170879b25fe94a80cc8f5e5606"
OLD = "        call_ctl!(ctx, tile_set, (cfg.g_threads as f64 * 1.0f64).log2().ceil());"
NEW = """        // libaom's variadic tile controls require unsigned int, not double.
        call_ctl!(ctx, tile_set, (cfg.g_threads as f64 * 1.0f64).log2().ceil() as u32);"""


def patch_source(text: str) -> str:
    # Verify the entire pinned implementation, including an already-patched
    # copy. An upstream change must be reviewed rather than partially patched.
    if text.count(NEW) == 1:
        original = text.replace(NEW, OLD, 1)
    else:
        original = text
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != ORIGINAL_SHA256:
        raise RuntimeError("Pinned 1.4.6 AOM encoder source changed")
    if original.count(OLD) != 1:
        raise RuntimeError("Pinned AOM tile-control expression changed")
    return original.replace(OLD, NEW, 1)


def apply(repo: Path) -> None:
    manifest = (repo / "Cargo.toml").read_text(encoding="utf-8")
    package = re.search(r"(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)", manifest)
    versions = re.findall(r'^version\s*=\s*"([^"]+)"\s*$', package.group(1) if package else "", re.M)
    if versions != [VERSION]:
        raise RuntimeError("Pinned AOM fix requires upstream package version 1.4.6")
    target = repo / SOURCE
    patched = patch_source(target.read_text(encoding="utf-8"))
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(patched)
    print("   patched pinned AV1 tile row/column control: pass unsigned int through C varargs")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
