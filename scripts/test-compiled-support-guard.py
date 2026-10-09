#!/usr/bin/env python3
"""Test the real compiled guard without unrelated optional WebRTC test dependencies."""
import os
import re
import subprocess
from pathlib import Path


def guard_test_manifest(source: str) -> str:
    # WebRTC is already an optional normal dependency, with a feature-gated
    # module. Its duplicate unconditional dev dependency unnecessarily compiles
    # WebRTC for every filtered unit test on the older product toolchain.
    if 'webrtc = { version = "0.14.0", optional = true }' not in source:
        raise RuntimeError("Pinned optional WebRTC dependency changed")
    match = re.search(r"(?ms)^\[dev-dependencies\]\r?\n(.*?)(?=^\[|\Z)", source)
    if not match:
        raise RuntimeError("Pinned common crate dev dependencies missing")
    dependencies, count = re.subn(r'^webrtc = "0\.14\.0"\r?\n', "", match.group(1), flags=re.M)
    if count != 1:
        raise RuntimeError("Pinned WebRTC dev dependency changed")
    return source[:match.start(1)] + dependencies + source[match.end(1):]


def run_guard_tests(repo: Path, runner=subprocess.run) -> int:
    manifest = repo / "libs/hbb_common/Cargo.toml"
    library = repo / "libs/hbb_common/src/lib.rs"
    if '#[cfg(feature = "webrtc")]\npub mod webrtc;' not in library.read_text(encoding="utf-8"):
        raise RuntimeError("WebRTC must remain feature-gated for narrow guard tests")
    original = manifest.read_bytes()
    modified = guard_test_manifest(original.decode("utf-8"))
    try:
        manifest.write_bytes(modified.encode("utf-8"))
        command = ["cargo", "test", "--locked", "--release", "--manifest-path", str(repo / "Cargo.toml"),
                   "-p", "hbb_common", "--no-default-features", "--lib", "mixel_support_"]
        return runner(command, check=False).returncode
    finally:
        # Byte-for-byte restore even if Cargo raises or its tests fail. The
        # signed product and future feature tests retain the upstream manifest.
        manifest.write_bytes(original)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(run_guard_tests(Path(os.environ.get("RDREPO", root / "rustdesk")).resolve()))
