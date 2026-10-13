#!/usr/bin/env python3
"""Run the frozen tray fixture with additive, content-free privacy observations."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

MAX_OBSERVATIONS = 64
PRIVACY_CATEGORIES = {"synthetic-invite", "synthetic-api-key", "support-uri-prefix",
                      "invite-shape", "bearer-pattern", "credential-assignment"}


def privacy_categories(desktop, data):
    categories = []
    if desktop.unix.TOKEN.encode() in data or desktop.WARM_TOKEN.encode() in data:
        categories.append("synthetic-invite")
    if desktop.unix.API_KEY.encode() in data:
        categories.append("synthetic-api-key")
    if b"mixel-remote://support/?invite=" in data:
        categories.append("support-uri-prefix")
    if re.search(rb"(?i)inv_[0-9a-f-]{36}", data):
        categories.append("invite-shape")
    if re.search(rb"(?i)bearer\s+\S+", data):
        categories.append("bearer-pattern")
    if re.search(rb"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization)[\"']?\s*[=:]\s*[\"']?[^\s\"',}]+", data):
        categories.append("credential-assignment")
    return categories


def observe_privacy(desktop):
    original = desktop.privacy_issue
    facts = {"schema": 1, "originalResultPreserved": True, "matchedObservations": [],
             "observationLimitReached": False}
    seen = set()
    def wrapped(data):
        result = original(data)
        if result:
            value = {"categories": privacy_categories(desktop, data),
                     "freshBytesSha256": hashlib.sha256(data).hexdigest(), "freshBytes": len(data)}
            identity = (value["freshBytesSha256"], value["freshBytes"], tuple(value["categories"]))
            if identity not in seen:
                if len(facts["matchedObservations"]) < MAX_OBSERVATIONS:
                    facts["matchedObservations"].append(value)
                    seen.add(identity)
                else:
                    facts["observationLimitReached"] = True
        return result
    desktop.privacy_issue = wrapped
    return facts


def finite_positive(value):
    if type(value) not in (int, float):
        return False
    try:
        return value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def observe_launchservices(desktop):
    original = desktop.helper_request
    facts = {"schema": 1, "originalResultPreserved": True, "launchObservations": [],
             "observationLimitReached": False}
    def wrapped(helper, action, *args, **kwargs):
        result = original(helper, action, *args, **kwargs)
        if action in ("tray", "launch") and isinstance(result, dict):
            pid, launch = result.get("pid"), result.get("launchTime")
            if type(pid) is int and 0 < pid < 2**31 and finite_positive(launch):
                if len(facts["launchObservations"]) < 4:
                    facts["launchObservations"].append({"action": action, "pid": pid, "launchTime": launch})
                else:
                    facts["observationLimitReached"] = True
        return result
    desktop.helper_request = wrapped
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    args = parser.parse_args(argv)
    args.proofs = args.proofs.absolute()
    try:
        parent = args.proofs.parent.lstat()
        fresh = (args.proofs.parent.resolve(strict=True) == args.proofs.parent
                 and stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                 and not os.path.lexists(args.proofs))
    except OSError:
        fresh = False
    if not fresh:
        raise RuntimeError("Privacy proof destination must be new with an owned canonical parent")
    source = Path(__file__).resolve().parent / "test-support-launch-macos-tray.py"
    spec = importlib.util.spec_from_file_location("mixel_frozen_tray_diagnostic", source)
    tray = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tray)
    facts = observe_privacy(tray.desktop)
    launch_facts = observe_launchservices(tray.desktop)
    original_argv = sys.argv
    try:
        sys.argv = [str(source), "--app", str(args.app), "--proofs", str(args.proofs)]
        return tray.main()
    finally:
        sys.argv = original_argv
        original_proof = args.proofs / "tray-runtime-safe-facts.json"
        if args.proofs.exists() and os.path.lexists(original_proof):
            attributes = args.proofs.lstat()
            current_parent = args.proofs.parent.lstat()
            proof = original_proof.lstat()
            if (not stat.S_ISDIR(attributes.st_mode) or attributes.st_uid != os.getuid()
                    or (current_parent.st_dev, current_parent.st_ino) != (parent.st_dev, parent.st_ino)
                    or not stat.S_ISREG(proof.st_mode) or proof.st_uid != os.getuid() or proof.st_nlink != 1):
                raise RuntimeError("Privacy observation directory is not owned")
            for name, value in (("privacy-match-safe-facts.json", facts),
                                ("launch-observation-safe-facts.json", launch_facts)):
                with (args.proofs / name).open("x", encoding="utf-8") as output:
                    os.chmod(output.name, 0o600)
                    json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
                    output.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
