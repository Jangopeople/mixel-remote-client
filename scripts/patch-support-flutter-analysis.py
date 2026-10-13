#!/usr/bin/env python3
"""Correct six pinned Flutter 3.24 analyzer failures after complete branding."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
# common.dart is the exact completed Mixel branding output from the pinned
# source. Its upstream bytes have SHA256 95ffcc4fdc3d83564d19d5ad7c999adbe65cdc37829cf466d43d5a9684e7f8de.
# This helper belongs at the end of apply-branding, after every common patch.
FILES = {
    "flutter/lib/common.dart": (
        "66f58cc2606eba8a240e0f960c39c8ba18c14616352ef60b83f6202af5cf605a",
        "66f58cc2606eba8a240e0f960c39c8ba18c14616352ef60b83f6202af5cf605a",
        "import 'dart:math';\n", "", 1),
    "flutter/lib/models/ab_model.dart": (
        "e1c79b068b46042f5d7a1c3a4437b656a10e6b64cd67b671969faf1c2ac7c176",
        "ac8118640a408db95028cb077471daf694f71e2ead9c6a4c2ee38c81e3b111a4",
        "import 'dart:io';\n", "", 1),
    "flutter/lib/models/web_model.dart": (
        "87f793a0d4654491d65801c67fab85763f373b597e4abb89a8e593a606639289",
        "abe750eaf6e8c051da92864ff4c9a0214cb9976d940ad42ff97d6eb017d6a6d0",
        "import 'dart:js_interop';\n", "", 1),
    "flutter/lib/web/common.dart": (
        "85f9261fed73998c0aff10ef342f4cd7b3a04d4e088b68580234fec8ac67790f",
        "1b22d742f773e319c33b28cd2b923216179d463ea0afd18d227c7522c10b45da",
        "import 'dart:html' as html;\n", "", 1),
    "flutter/lib/mobile/pages/terminal_page.dart": (
        "1133d75f858d98fe7afd1a30ef0e57e1a4879c83d004821768fe1c83b69519b0",
        "4c898d8284b98012ccc0e57bea6e235c159621f9896540d381b1a74dc3dc481d",
        "    if (send != null) {\n      _terminalModel.sendVirtualKey(send);\n    }",
        "    _terminalModel.sendVirtualKey(send);", 1),
    "flutter/test/cm_test.dart": (
        "08be6092c8f3fce7969722e6a5b90524ba03aae1ea3ae6ab6c58a2a33039eb04",
        "c695a84d23e738c6de52cad732a0d16d605de08fd75f6a06d8422b255fb059f5",
        ", true, false, false, false)", ", true, false, false)", 4),
}
# The current URI/ACK profile uses Random.secure(), so its exact common.dart
# retains dart:math. Historical no-ACK source still removes the unused import.
# Keep the exact incoming-owner common.dart boundary as a historical variant.
# The composed current variant above additionally contains our URI/ACK layers.
HISTORICAL_COMMON = ("0d47b1dce88586fbebb2faaf390f210020a7dbbbf00bd782e7897944bcad556f",
                     "65af38effd117b36c614c729192c1c20a69e2ac6a7a0ade89b1890b2cc9a7138")
CLIENT = "flutter/lib/models/server_model.dart"
CLIENT_SHA256 = "febc11a1baac03dac0d84b8f85a2655784a7ceffb0789c42359b22f0aef330f9"
CLIENT_CONSTRUCTOR = """  Client(this.id, this.authorized, this.isFileTransfer, this.isViewCamera,
      this.name, this.peerId, this.keyboard, this.clipboard, this.audio);"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(repo, name):
    path = repo
    for part in Path(name).parts[:-1]:
        path /= part
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError("Flutter analysis source has an unsafe parent: " + name)
    path = repo / name
    attributes = path.lstat()
    if not stat.S_ISREG(attributes.st_mode) or attributes.st_nlink != 1:
        raise RuntimeError("Flutter analysis source must be a single-link regular file: " + name)
    return path.read_bytes()


def normalize(data):
    normalized = data.replace(b"\r\n", b"\n")
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    if data != normalized.replace(b"\n", newline):
        raise RuntimeError("Flutter analysis source has mixed line endings")
    return normalized, newline


def transform(name, data):
    normalized, newline = normalize(data)
    original, corrected, before, after, count = FILES[name]
    identity = digest(normalized)
    if name == "flutter/lib/common.dart" and identity in HISTORICAL_COMMON:
        original, corrected = HISTORICAL_COMMON
    if identity == corrected:
        return data
    if identity != original:
        raise RuntimeError("Pinned Flutter analysis input changed: " + name)
    source = normalized.decode("utf-8")
    if source.count(before) != count:
        raise RuntimeError("Pinned Flutter analysis replacement changed: " + name)
    result = source.replace(before, after).encode()
    if digest(result) != corrected:
        raise RuntimeError("Pinned Flutter analysis output changed: " + name)
    return result.replace(b"\n", newline)


def apply(repo):
    cargo, _ = normalize(regular(repo, "Cargo.toml"))
    packages = re.findall(rb'(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)', cargo)
    versions = re.findall(rb'(?m)^version\s*=\s*"([^"]+)"\s*$', packages[0]) if len(packages) == 1 else []
    if versions != [b"1.4.6"]:
        raise RuntimeError("Flutter analysis correction requires upstream 1.4.6")
    client, _ = normalize(regular(repo, CLIENT))
    if digest(client) != CLIENT_SHA256 or client.count(CLIENT_CONSTRUCTOR.encode()) != 1:
        raise RuntimeError("Pinned nine-field Client constructor changed")
    prepared = {}
    # Validate the complete inventory before writing any target.
    for name in FILES:
        before = regular(repo, name)
        prepared[name] = before, transform(name, before)
    for name, (before, after) in prepared.items():
        if before != after:
            (repo / name).write_bytes(after)
    print("   corrected pinned Flutter analyzer: obsolete Client arguments, profile-specific unused imports and redundant terminal null guard; current ACK retains dart:math")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
