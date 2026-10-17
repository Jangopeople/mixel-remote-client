#!/usr/bin/env python3
"""Install the exact pinned repo-local URI plugin with bounded Mac gap replay."""
import os
from pathlib import Path
import re
import shutil


SCRIPTS = Path(__file__).resolve().parent
VENDOR = SCRIPTS / "vendor/uni_links_desktop"
ROOT = Path(os.environ.get("RDREPO", SCRIPTS.parent / "rustdesk"))
PUBSPEC = ROOT / "flutter/pubspec.yaml"
OVERRIDE = "  uni_links_desktop:\n    path: local_plugins/uni_links_desktop\n"


def main():
    text = PUBSPEC.read_text(encoding="utf-8")
    if len(re.findall(r"^  uni_links_desktop: \^0\.1\.6(?:[^\n]*)$", text, re.M)) != 1:
        raise SystemExit("Pinned uni_links_desktop dependency changed; no files written")
    if text.count("dependency_overrides:\n") != 1:
        raise SystemExit("Pinned dependency override section changed; no files written")
    section = text.split("dependency_overrides:\n", 1)[1].split("\n\n", 1)[0]
    if OVERRIDE in text and (text.count(OVERRIDE) != 1 or len(re.findall(r"^  uni_links_desktop:", section, re.M)) != 1):
        raise SystemExit("Duplicate URI plugin override; no files written")
    if OVERRIDE not in text:
        if re.search(r"^  uni_links_desktop:", section, re.M):
            raise SystemExit("Unexpected URI plugin override; no files written")
        text = text.replace("dependency_overrides:\n", "dependency_overrides:\n" + OVERRIDE, 1)
    lock = (ROOT / "flutter/pubspec.lock").read_text(encoding="utf-8")
    blocks = re.findall(r"^  uni_links_desktop:\n(.*?)(?=^  [a-zA-Z_]|\Z)", lock, re.M | re.S)
    if len(blocks) != 1 or 'version: "0.1.7"' not in blocks[0]:
        raise SystemExit("Pinned URI plugin version changed; no files written")
    hosted = ('sha256: "692de81efc32ef72df56d428902afb5216d5f9e43d71c7b315d360acd7a1e115"' in blocks[0]
              and "source: hosted" in blocks[0])
    local = (re.search(r'^      path: "?local_plugins/uni_links_desktop"?$', blocks[0], re.M)
             and "source: path" in blocks[0] and "relative: true" in blocks[0])
    if not hosted and not local:
        raise SystemExit("Pinned URI plugin source changed; no files written")
    if 'version: 0.1.7' not in (VENDOR / "pubspec.yaml").read_text(encoding="utf-8"):
        raise SystemExit("Vendored URI plugin version differs; no files written")
    shutil.copytree(VENDOR, ROOT / "flutter/local_plugins/uni_links_desktop", dirs_exist_ok=True)
    PUBSPEC.write_text(text, encoding="utf-8")
    print("Installed exact repo-local URI plugin with latest-only validated Mac support replay")


if __name__ == "__main__":
    main()
