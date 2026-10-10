#!/usr/bin/env python3
"""Package the signed desktop payload; fail if the portable Cargo build fails."""
import argparse
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path


def build_portable(repo: Path, payload: Path, output: Path, runner=subprocess.run) -> None:
    repo, payload, output = repo.resolve(), payload.resolve(), output.resolve()
    entry = payload / "Mixel-Remote.exe"
    if not entry.is_file():
        raise ValueError("Signed compiled Mixel-Remote.exe is required for portable packaging")
    portable = repo / "libs/portable"
    specification = importlib.util.spec_from_file_location("mixel_portable_generator", portable / "generate.py")
    generator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(generator)
    original_directory = Path.cwd()
    try:
        table = generator.generate_md5_table(str(payload), 11)
        generator.write_package_metadata(table, str(portable), "./Mixel-Remote.exe")
        generator.write_app_metadata(str(portable))
        # Upstream generate.py ignores the exit status from os.system(cargo).
        # Execute Cargo ourselves and never reuse a stale launcher on failure.
        binary = repo / "target/release/rustdesk-portable-packer.exe"
        binary.unlink(missing_ok=True)
        runner(["cargo", "build", "--locked", "--release", "--manifest-path", str(portable / "Cargo.toml")],
               cwd=repo, check=True)
        if not binary.is_file():
            raise RuntimeError("Portable Cargo build did not produce its executable")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(binary, output)
    finally:
        os.chdir(original_directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("rustdesk"))
    parser.add_argument("--payload", type=Path, default=Path("store-payload"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_portable(args.repo, args.payload, args.output)
    print("PASS: portable launcher packages the signed compiled application")
