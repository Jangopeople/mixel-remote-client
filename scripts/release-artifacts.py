#!/usr/bin/env python3
"""Validate build inputs and stage only the exact selected release artifacts."""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

PLATFORMS = ("linux", "macos", "windows")


def validate_version(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?", version):
        raise ValueError("Upstream version must be an explicit X.Y.Z release tag")
    return version


def selected_platforms(value: str) -> tuple[str, ...]:
    if not value.strip():
        return PLATFORMS
    values = tuple(part.strip() for part in value.split(","))
    if any(part not in PLATFORMS for part in values) or len(values) != len(set(values)):
        raise ValueError("Platforms must be a comma list of linux,macos,windows without duplicates")
    return tuple(part for part in PLATFORMS if part in values)


def validate_runtime_run_id(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("Runtime diagnostic run ID must contain only positive decimal digits")
    return value


def resolve_config(branding: Path, override: str, only: str) -> dict[str, str]:
    matches = re.findall(r"(?m)^UPSTREAM_VERSION=(.+)$", branding.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise ValueError("branding.env must contain exactly one UPSTREAM_VERSION")
    version = validate_version(override.strip() or matches[0].strip().strip('"'))
    platforms = selected_platforms(only)
    return {"upstream_version": version, "platforms": ",".join(platforms),
            **{platform: str(platform in platforms).lower() for platform in PLATFORMS}}


def require_publish_credentials(config: dict[str, str], environment: dict[str, str]) -> None:
    if environment.get("PUBLISH_REQUESTED") != "true":
        return
    required = ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"]
    if config["macos"] == "true":
        required += ["APPLE_CERT_P12_BASE64", "APPLE_CERT_P12_PASSWORD", "APPLE_NOTARY_USER",
                     "APPLE_NOTARY_PASSWORD", "APPLE_NOTARY_TEAM_ID"]
    if config["windows"] == "true":
        required += ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"]
    missing = [name for name in required if not environment.get(name)]
    if missing:
        raise ValueError("Publication requires configured signing/notarization/upload credentials: " + ", ".join(missing))


def stage_artifacts(source: Path, destination: Path, version: str, platforms: str) -> dict:
    version = validate_version(version)
    selected = selected_platforms(platforms)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Release staging directory must be empty")
    plans = []
    for platform in selected:
        targets = {
            "linux": [("linux", "x86_64.deb", ("Mixel-Remote-Support.deb",))],
            "macos": [("macos-aarch64", "aarch64.dmg", ("Mixel-Remote-Support-Apple-Silicon.dmg",)),
                      ("macos-x86_64", "x86_64.dmg", ("Mixel-Remote-Support-Intel.dmg",))],
            "windows": [("windows", "x86_64.exe", ("Mixel-Remote-Support-Windows.exe", "Mixel-Remote-Support.exe", "Mixel-Remote-QS.exe"))],
        }[platform]
        if platform == "windows" and (source / "windows" / f"mixel-remote-{version}-x86_64.msi").exists():
            targets.append(("windows", "x86_64.msi", ("Mixel-Remote-Support.msi",)))
        for artifact, suffix, aliases in targets:
            filename = f"mixel-remote-{version}-{suffix}"
            file = source / artifact / filename
            if file.is_symlink() or not file.is_file() or file.stat().st_size == 0:
                raise ValueError(f"Selected build is incomplete: {artifact}/{filename}")
            plans.append((file, aliases))
    # Validate every required file before creating any customer alias.
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"upstream_version": version, "platforms": list(selected), "files": []}
    for file, aliases in plans:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        for filename in (file.name, *aliases):
            shutil.copyfile(file, destination / filename)
            manifest["files"].append({"name": filename, "sha256": digest, "bytes": file.stat().st_size,
                                      "alias": filename in aliases})
    (destination / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config.add_argument("--branding", type=Path, default=Path("branding/branding.env"))
    staging = commands.add_parser("stage")
    staging.add_argument("--source", type=Path, default=Path("artifacts"))
    staging.add_argument("--destination", type=Path, default=Path("dist"))
    commands.add_parser("runtime")
    args = parser.parse_args()
    if args.command == "config":
        values = resolve_config(args.branding, os.environ.get("INPUT_UPSTREAM_VERSION", ""), os.environ.get("INPUT_ONLY", ""))
        require_publish_credentials(values, os.environ)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={value}\n")
        print(f"PASS: validated upstream {values['upstream_version']}; selected {values['platforms']}")
    elif args.command == "runtime":
        run_id = validate_runtime_run_id(os.environ["RUNTIME_RUN_ID"])
        if os.environ.get("PUBLISH_REQUESTED") == "true":
            raise ValueError("Artifact runtime diagnostics require publish_r2=false")
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"run_id={run_id}\n")
        print("PASS: validated artifact-only runtime diagnostic request")
    else:
        manifest = stage_artifacts(args.source, args.destination, os.environ["UPSTREAM_VERSION"], os.environ["SELECTED_PLATFORMS"])
        print(f"PASS: staged {len(manifest['files'])} exact release artifacts and SHA256 manifest")


if __name__ == "__main__":
    main()
