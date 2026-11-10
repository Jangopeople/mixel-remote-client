#!/usr/bin/env python3
"""Install or inspect the managed host in the approved user's Aqua session."""
import argparse
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile


APPROVED_BUNDLES = {"ch.mixel.remote", "ch.mixel.remote.next.qa"}
MIXEL_SIGNATURE = 'anchor apple generic and certificate leaf[subject.OU] = "5277F8NDH4"'


def agent_configuration(app_path, settings_path):
    app = app_path.resolve(strict=True)
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    bundle = info["CFBundleIdentifier"]
    if bundle not in APPROVED_BUNDLES or info["CFBundleExecutable"] != "Mixel-Remote":
        raise ValueError("The startup agent requires an approved Mixel Remote host bundle")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "-R", "=" + MIXEL_SIGNATURE, str(app)],
        check=True,
    )
    details = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=2", str(app)],
        check=True, capture_output=True, text=True,
    ).stderr
    if not any(line.startswith("Authority=Developer ID Application: ") for line in details.splitlines()):
        raise ValueError("The startup agent requires a Developer ID signed host")
    settings_path = settings_path.absolute()
    settings = settings_path.lstat()
    if not stat.S_ISREG(settings.st_mode) or settings.st_uid != os.geteuid() or settings.st_mode & 0o077:
        raise ValueError("Use a private startup configuration file owned by the graphical user")
    return {
        "Label": f"{bundle}.startup-host",
        "ProgramArguments": [
            str(app / "Contents/MacOS/Mixel-Remote"), "--startup-host", str(settings_path),
        ],
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
    }


def write_new_agent(path, configuration):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".mixel-startup-") as stream:
        plistlib.dump(configuration, stream)
        stream.flush()
        os.fsync(stream.fileno())
        os.link(stream.name, path)


def matches_installer(configuration, label):
    if not isinstance(configuration, dict):
        return False
    args = configuration.get("ProgramArguments")
    return (
        set(configuration) == {"Label", "ProgramArguments", "LimitLoadToSessionType", "RunAtLoad"}
        and configuration["Label"] == label
        and configuration["LimitLoadToSessionType"] == "Aqua"
        and configuration["RunAtLoad"] is True
        and isinstance(args, list)
        and len(args) == 3
        and all(isinstance(value, str) for value in args)
        and Path(args[0]).is_absolute()
        and Path(args[0]).parts[-3:] == ("Contents", "MacOS", "Mixel-Remote")
        and args[1] == "--startup-host"
        and Path(args[2]).is_absolute()
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "install", "remove"))
    parser.add_argument("--app", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--bundle-id", choices=sorted(APPROVED_BUNDLES))
    args = parser.parse_args()
    uid = os.geteuid()
    if sys.platform != "darwin" or uid == 0:
        parser.error("Run this on macOS as the graphical user")
    if args.action == "remove":
        if not args.bundle_id or args.app or args.settings:
            parser.error("Removal requires only --bundle-id")
        label = f"{args.bundle_id}.startup-host"
    else:
        if not args.app or not args.settings or args.bundle_id:
            parser.error("Rendering and installing require --app and --settings")
        configuration = agent_configuration(args.app, args.settings)
        label = configuration["Label"]
    if args.action == "render":
        plistlib.dump(configuration, sys.stdout.buffer)
        return
    domain = f"gui/{uid}"
    service = f"{domain}/{label}"
    directory = Path.home() / "Library/LaunchAgents"
    if args.action == "install":
        directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != uid:
        raise ValueError("The LaunchAgents folder must belong to the graphical user")
    path = directory / f"{label}.plist"
    launchctl = "/bin/launchctl"
    if args.action == "install":
        write_new_agent(path, configuration)
        try:
            subprocess.run([launchctl, "bootstrap", domain, str(path)], check=True)
        except BaseException:
            path.unlink()
            raise
        print(f"Installed {service}")
        return
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != uid or metadata.st_mode & 0o077:
        raise ValueError("The launch item is outside the managed host scope")
    with path.open("rb") as stream:
        if not matches_installer(plistlib.load(stream), label):
            raise ValueError("The saved launch item does not match this host")
    active = subprocess.run([launchctl, "print", service], capture_output=True).returncode == 0
    if active:
        subprocess.run([launchctl, "bootout", service], check=True)
    path.unlink()
    print(f"Removed {service}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(2)
