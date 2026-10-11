#!/usr/bin/env python3
"""Compile a relocatable GUID-only Windows source fixture, never a release gate."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "compile-only-standard-lease-qa"
JOB = "portable-standard-lease-qa"
SOURCES = (
    "scripts/support-invite-guard.rs",
    "scripts/test-support-lease.py",
    "scripts/test-support-lease-standard-windows.ps1",
)
ADAPTERS = {
    "run-standard-lease-qa.ps1": "scripts/test-support-lease-interactive-windows.ps1",
    "run-admin-negative-lease-qa.ps1": "scripts/test-support-lease-negative-windows.ps1",
}
ENTRY = "fn main() {\n    // Keep the existing native negative-control branches compiled with -D warnings."
REFUSAL = '    assert!(std::env::args().len() == 1, "Portable source QA accepts no command-line arguments");\n'
STATIC_ARGS = ["--edition", "2021", "-D", "warnings", "-C", "target-feature=+crt-static"]
BUILD_SOURCE = "scripts/build-standard-lease-qa-bundle.py"
BUILD_CONTROLS = (
    ".github/workflows/preflight.yml",
    "scripts/activate-windows-msvc.ps1",
    "scripts/test-standard-lease-qa-bundle.py",
    "scripts/test-support-lease-interactive-source.ps1",
)
PACKAGE_MEMBERS = frozenset((*SOURCES, *ADAPTERS,
    "fixture/lease-standard.rs", "fixture/lease-standard.exe",
    "fixture/lease-standard-gui.rs", "fixture/lease-standard-gui.exe",
    "lease-standard-compile.log", "lease-standard-gui-compile.log", "hosted-loader-smoke.json"))


def remove_owned_compiler_sidecars(directory):
    """Exclude two exact compiler auxiliary names without reading their contents."""
    for name in ("lease-standard.pdb", "lease-standard-gui.pdb"):
        path = directory / name
        try:
            record = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(record.st_mode) or getattr(record, "st_file_attributes", 0) & 0x400 or record.st_size > 64 * 1024 * 1024:
            raise ValueError("Owned compiler auxiliary output is not a bounded ordinary file")
        path.unlink()


def bundle_members(directory):
    """Require the exact relocatable package inventory before writing its manifest."""
    members = []
    pending = [directory]
    while pending:
        for path in pending.pop().iterdir():
            record = path.lstat()
            name = path.relative_to(directory).as_posix()
            if stat.S_ISLNK(record.st_mode) or getattr(record, "st_file_attributes", 0) & 0x400:
                raise ValueError("QA package refuses reparse members")
            if stat.S_ISDIR(record.st_mode):
                if name not in ("fixture", "scripts"):
                    raise ValueError("QA package directory inventory differs")
                pending.append(path)
            elif stat.S_ISREG(record.st_mode) and name in PACKAGE_MEMBERS:
                members.append(path)
            else:
                raise ValueError("QA package file inventory differs")
    if {path.relative_to(directory).as_posix() for path in members} != PACKAGE_MEMBERS:
        raise ValueError("QA package is missing a required member")
    return sorted(members)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_source_bindings():
    names = (*SOURCES, *ADAPTERS.values(), BUILD_SOURCE, *BUILD_CONTROLS)
    bindings = []
    for name in names:
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 1048576:
            raise ValueError("QA source is missing or outside its regular-file bound")
        committed = subprocess.check_output(["git", "cat-file", "blob", "HEAD:" + name], cwd=ROOT, stderr=subprocess.PIPE)
        actual = path.read_bytes()
        if actual != committed:
            raise ValueError("QA build source differs from its exact committed blob")
        bindings.append({"path": name, "bytes": len(committed), "sha256": hashlib.sha256(committed).hexdigest()})
    return bindings


def portable_body(body):
    if body.count(ENTRY) != 1 or REFUSAL in body:
        raise ValueError("Exact portable fixture entry boundary changed")
    return body.replace(ENTRY, "fn main() {\n" + REFUSAL + ENTRY[len("fn main() {\n"):], 1)


def pe_imports(data):
    """Read only a bounded owned PE import-directory inventory, never instructions."""
    if not 128 <= len(data) <= 16 * 1024 * 1024 or data[:2] != b"MZ":
        raise ValueError("Expected bounded owned PE")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if not 64 <= pe <= len(data) - 24 or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Owned PE header bounds changed")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional, table = pe + 24, pe + 24 + optional_size
    if machine != 0x8664 or not 1 <= count <= 96 or optional_size < 128 or table + count * 40 > len(data):
        raise ValueError("Expected bounded x64 owned PE sections")
    directories = struct.unpack_from("<I", data, optional + 108)[0]
    if struct.unpack_from("<H", data, optional)[0] != 0x20B or not 2 <= directories <= 16 or directories > (optional_size - 112) // 8:
        raise ValueError("Owned PE32+ import-directory contract changed")
    sections = []
    for index in range(count):
        virtual_size, address, size, offset = struct.unpack_from("<IIII", data, table + index * 40 + 8)
        if offset + size > len(data):
            raise ValueError("Owned PE raw section exceeds file")
        sections.append((address, virtual_size, size, offset))

    def mapped(rva, size):
        matches = [offset + rva - address for address, _, raw, offset in sections
                   if address <= rva and rva - address <= raw and size <= raw - (rva - address)]
        if len(matches) != 1:
            raise ValueError("Owned PE import RVA is not uniquely file-backed")
        return matches[0]

    rva, size = struct.unpack_from("<II", data, optional + 120)
    if not rva or not 20 <= size <= 65536:
        raise ValueError("Owned PE import-directory bounds changed")
    imports = []
    for index in range(min(size // 20, 256)):
        offset = mapped(rva + index * 20, 20)
        descriptor = struct.unpack_from("<IIIII", data, offset)
        if not any(descriptor):
            if not imports:
                raise ValueError("Owned PE import inventory is empty")
            return sorted(imports)
        name_rva = descriptor[3]
        value = bytearray()
        for byte_index in range(260):
            byte = data[mapped(name_rva + byte_index, 1)]
            if byte == 0:
                break
            value.append(byte)
        else:
            raise ValueError("Owned PE import name exceeds bound")
        name = value.decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9_.-]+\.dll", name) or name in imports:
            raise ValueError("Owned PE import name is malformed or duplicated")
        if re.search(r"(?:vcruntime|msvcp|msvcr|concrt|ucrtbase)", name):
            raise ValueError("Portable QA unexpectedly depends on a separate C runtime")
        imports.append(name)
    raise ValueError("Owned PE import inventory lacks a bounded terminator")


def safe_result(record, pid):
    keys = {"pid", "sourceStatus", "rawOpenError", "attestation", "requiresClick", "ownerFailed"}
    if not isinstance(record, dict) or set(record) != keys:
        raise ValueError("Loader result fields differ")
    if type(record["pid"]) is not int or record["pid"] != pid or not 0 < pid <= 0xFFFFFFFF:
        raise ValueError("Loader result is not from the retained owned child")
    if type(record["rawOpenError"]) is not int or not 0 <= record["rawOpenError"] <= 0xFFFFFFFF:
        raise ValueError("Loader result error is not a bounded native integer")
    if type(record["requiresClick"]) is not bool or type(record["ownerFailed"]) is not bool:
        raise ValueError("Loader result Boolean types differ")
    if not isinstance(record["attestation"], str) or record["attestation"] not in ("", "attended-runtime-v2", "guard-unavailable") or not isinstance(record["sourceStatus"], str):
        raise ValueError("Loader result classification differs")
    code = r"(?:none|0|-?[1-9][0-9]{0,9})"
    fields = re.fullmatch(r"ownerFailed=(true|false);ownerError=(" + code + r");kernel=(absent|active|unavailable);probeError=(" + code + r");memoryActive=(true|false)", record["sourceStatus"])
    if not fields:
        raise ValueError("Loader result status is not canonical")
    owner, owner_error, kernel, probe_error, _ = fields.groups()
    if (owner == "true") != (owner_error != "none") or (kernel == "unavailable") != (probe_error != "none") or (owner == "true") != record["ownerFailed"]:
        raise ValueError("Loader result native error/status pairs differ")
    if any(value != "none" and not -0x80000000 <= int(value) <= 0x7FFFFFFF for value in (owner_error, probe_error)):
        raise ValueError("Loader result status error exceeds signed native bounds")


def loader_smoke(binary, directory):
    result = directory / "fixture-result.json"
    mode = directory / "fixture-mode.txt"
    if result.exists() or mode.exists():
        raise ValueError("Loader smoke requires new owned fixture records")
    refusal = subprocess.run([str(binary), "probe"], cwd=directory, capture_output=True, timeout=10)
    if refusal.returncode == 0 or result.exists() or b"Portable source QA accepts no command-line arguments" not in refusal.stderr:
        raise ValueError("Portable fixture did not refuse argv before native source execution")
    mode.write_text("probe", encoding="utf-8")
    child = subprocess.Popen([str(binary)], cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    release = directory / ("fixture-release-" + str(child.pid))
    try:
        deadline = time.monotonic() + 10
        while not result.is_file() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not result.is_file() or not 1 <= result.stat().st_size <= 4096:
            raise ValueError("Retained loader child did not write a bounded source result")
        record = json.loads(result.read_text(encoding="utf-8"))
        safe_result(record, child.pid)
        release.write_text("verified", encoding="utf-8")
        output, error = child.communicate(timeout=10)
        if child.returncode != 0:
            raise ValueError("Retained source loader child did not exit successfully")
        return {"scope": "hosted-loader-smoke-only", "standardUserQualified": False,
                "argvRefused": True, "result": record,
                "stdoutSha256": hashlib.sha256(output).hexdigest(),
                "stderrSha256": hashlib.sha256(error).hexdigest()}
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
        for path in (mode, result, release):
            if path.exists():
                path.unlink()


def build(output):
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_JOB") != JOB:
        raise ValueError("Compilation requires the explicitly scoped Windows QA job")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    run = os.environ.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or head != os.environ.get("GITHUB_SHA") or not re.fullmatch(r"[0-9a-f]{40}", tree) or not re.fullmatch(r"[1-9][0-9]{0,19}", run):
        raise ValueError("Exact checked-out QA source/run binding is absent")
    bindings = git_source_bindings()
    output = output.absolute()
    if output.exists() or output.is_symlink() or output.parent.is_symlink() or output.parent.resolve() != ROOT:
        raise ValueError("QA output must be a new direct workspace directory")
    rustc = shutil.which("rustc")
    if not rustc:
        raise ValueError("Installed native Rust compiler is absent")
    version = subprocess.check_output([rustc, "--version"], text=True, timeout=10).strip()
    if not version.startswith("rustc 1.75.0 "):
        raise ValueError("QA fixture requires pinned Rust1.75.0")
    output.mkdir()
    fixture = output / "fixture"
    fixture.mkdir()
    event = "Global\\Mixel-Remote-Standard-Lease-" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location("mixel_lease_export", ROOT / SOURCES[1])
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    exporter.export_standard_windows_fixture(fixture, event, True)
    body = portable_body((fixture / "lease-standard.rs").read_text(encoding="utf-8"))
    header = '#![windows_subsystem = "windows"]\n'
    smoke = {}
    for name, source in (("lease-standard", body), ("lease-standard-gui", header + body)):
        source_path, binary = fixture / (name + ".rs"), fixture / (name + ".exe")
        source_path.write_text(source, encoding="utf-8", newline="\n")
        compiled = subprocess.run([rustc, *STATIC_ARGS, str(source_path), "-o", str(binary)], capture_output=True, timeout=90)
        (output / (name + "-compile.log")).write_bytes(compiled.stdout + compiled.stderr)
        if compiled.returncode != 0 or compiled.stderr:
            raise ValueError("Portable static-CRT fixture compilation failed or warned")
        expected = 2 if name.endswith("-gui") else 3
        if exporter.read_pe_subsystem(binary) != expected:
            raise ValueError("Portable native subsystem differs")
        smoke[name] = {"subsystem": expected, "imports": pe_imports(binary.read_bytes()),
                       "loader": loader_smoke(binary, fixture)}
    for name in SOURCES:
        target = output / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    for target, source in ADAPTERS.items():
        (output / target).write_bytes((ROOT / source).read_bytes())
    (output / "hosted-loader-smoke.json").write_text(json.dumps(smoke, indent=2) + "\n", encoding="utf-8")
    remove_owned_compiler_sidecars(fixture)
    members = bundle_members(output)
    manifest = {"schemaVersion": 1, "purpose": PURPOSE, "runtimeQualified": False,
                "eventName": event, "commit": head, "tree": tree, "runId": int(run), "jobName": JOB,
                "compiler": version, "args": STATIC_ARGS, "portableEntryAssertion": REFUSAL.strip(),
                "buildSourceBindings": bindings,
                "files": [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                           "sha256": digest(path)} for path in members]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("PASS: exact GUID-only console/GUI fixtures compile with static CRT, reject argv, load and write owned results; this is compilation/loader proof only.")
    print("PASS: exact 12-member QA package inventory verified; two known compiler auxiliary outputs excluded without reading their contents.")
    print("NOT QUALIFIED: actual standard-user desktop consent and signed customer applications remain separate required gates.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
