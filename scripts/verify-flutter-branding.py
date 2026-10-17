#!/usr/bin/env python3
"""Reject a compiled Flutter bundle missing its exact Mixel UI icon."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def unique_object(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Compiled Flutter asset manifest contains duplicate keys")
        values[key] = value
    return values


def verify(assets: Path, expected: Path) -> dict:
    icon = assets / "assets/icon.png"
    manifest = assets / "AssetManifest.json"
    if assets.is_symlink() or (assets / "assets").is_symlink() or icon.is_symlink() or manifest.is_symlink():
        raise ValueError("Compiled Flutter branding requires regular owned assets")
    if not icon.is_file() or icon.read_bytes() != expected.read_bytes():
        raise ValueError("Compiled Flutter icon differs from the authoritative Mixel PNG")
    if (assets / "assets/icon.svg").exists() or (assets / "assets/icon.svg").is_symlink():
        raise ValueError("Compiled Flutter bundle still contains the upstream icon fallback")
    values = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(values, dict) or values.get("assets/icon.png") != ["assets/icon.png"] or "assets/icon.svg" in values:
        raise ValueError("Compiled Flutter asset manifest does not bind the Mixel icon")
    return {"result": "passed", "icon_sha256": hashlib.sha256(icon.read_bytes()).hexdigest(),
            "asset_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--expected", type=Path, default=ROOT / "branding/icon-512.png")
    args = parser.parse_args()
    result = verify(args.assets, args.expected)
    print("PASS: compiled Flutter asset manifest and exact Mixel UI icon verified; SHA256=" + result["icon_sha256"])


if __name__ == "__main__":
    main()
