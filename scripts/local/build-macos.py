#!/usr/bin/env python3
"""Build an unsigned ARM Mixel Remote app locally; --run explicitly starts tools.

This driver never publishes, packages, installs, opens an app, or accesses signing
credentials. It builds private copies and retains commands, raw output and hashes.
Other required source/native/runtime acceptance gates remain separate.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

UPSTREAM_HEAD = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
HBB_HEAD = "48c37de3e6c4e399af6f51ca20e8e3e1fd037976"
VCPKG_HEAD = "120deac3062162151622ca4860575a33844ba10b"
FLUTTER_VERSION = "3.27.4"
FLUTTER_HEAD = "d8a9f9a52e5af486f80d932e838ee93861ffd863"
TOOLCHAIN = "1.81"
TARGET = "aarch64-apple-darwin"
MINIMUM = "12.3"
MAX_JOBS = 8
BRIDGE_FILES = (
    "src/bridge_generated.rs", "src/bridge_generated.io.rs",
    "flutter/lib/generated_bridge.dart", "flutter/lib/generated_bridge.freezed.dart",
    "flutter/macos/Runner/bridge_generated.h", "flutter/ios/Runner/bridge_generated.h",
)
# Exact current workflow order. Drift fails before any build rather than running
# newly introduced workflow shell or silently generating a different bridge.
BRIDGE_PATCHES = (
    ("receiver_input_integration_projection.py", "--root", ".", "--check-priors"),
    ("patch-support-receiver-custodian.py", "--root", ".", "--restore", "--check"),
    ("patch-support-receiver-custodian.py", "--root", ".", "--restore"),
    ("patch-support-receiver-sas.py", "--root", ".", "--restore", "--check"),
    ("patch-support-receiver-sas.py", "--root", ".", "--restore"),
    ("patch-support-receiver-uinput-deps.py", "--root", ".", "--restore", "--check"),
    ("patch-support-receiver-uinput-deps.py", "--root", ".", "--restore"),
    ("patch-support-receiver-uinput.py", "--root", ".", "--restore", "--check"),
    ("patch-support-receiver-uinput.py", "--root", ".", "--restore"),
    ("patch-support-receiver-input.py", "--root", ".", "--restore", "--check"),
    ("patch-support-receiver-native.py", "--root", ".", "--restore", "--check"),
    ("patch-support-receiver-input.py", "--root", ".", "--restore"),
    ("patch-support-receiver-native.py", "--root", ".", "--restore"),
    ("patch-support-input-lifecycle.py", "--root", ".", "--restore", "--check"),
    ("patch-support-input-lifecycle.py", "--root", ".", "--restore"),
    ("patch-support-uri-routing.py", "--root", ".", "--restore"),
    ("patch-support-linux-child-window-close.py", "--root", ".", "--restore"),
    ("patch-support-macos-texture.py",), ("patch-support-macos-window.py",),
    ("patch-support-linux-child-window-close.py", "--root", ".", "--after-branding"),
    ("patch-support-uri-routing.py", "--root", ".", "--after-branding"),
    ("patch-support-input-lifecycle.py", "--root", "."),
    ("patch-support-receiver-native.py", "--root", "."),
    ("patch-support-receiver-input.py", "--root", "."),
    ("patch-support-receiver-uinput.py", "--root", "."),
    ("patch-support-receiver-uinput-deps.py", "--root", "."),
    ("patch-support-receiver-sas.py", "--root", "."),
    ("patch-support-receiver-custodian.py", "--root", "."),
)
PUB_GET_LINE = "pushd flutter && sed -i -e 's/extended_text: 14.0.0/extended_text: 13.0.0/g' pubspec.yaml && flutter pub get && popd"
CODEGEN_LINE = "~/.cargo/bin/flutter_rust_bridge_codegen --rust-input ./src/flutter_ffi.rs --dart-output ./flutter/lib/generated_bridge.dart --c-output ./flutter/macos/Runner/bridge_generated.h"
COPY_LINE = "cp ./flutter/macos/Runner/bridge_generated.h ./flutter/ios/Runner/bridge_generated.h"
TOOLS = {
    "git": "install Xcode command-line tools", "bash": "install macOS command-line tools",
    "rustup": "install Rustup, then rustup toolchain install 1.81 --component rustfmt --target aarch64-apple-darwin,x86_64-apple-darwin",
    "autoconf": "brew install autoconf", "automake": "brew install automake",
    "glibtoolize": "brew install libtool", "pkg-config": "brew install pkg-config",
    "nasm": "brew install nasm", "cmake": "brew install cmake", "ninja": "brew install ninja",
    "magick": "brew install imagemagick", "pod": "brew install cocoapods",
    "xcrun": "select a complete Xcode installation", "xcodebuild": "select a complete Xcode installation",
    "clang": "select a complete Xcode installation", "iconutil": "install macOS command-line tools",
}


class Refusal(RuntimeError):
    pass


def job_count(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise argparse.ArgumentTypeError("jobs must be a positive integer from 1 to " + str(MAX_JOBS))
    if len(str(value)) > 2 or not 1 <= int(value) <= MAX_JOBS:
        raise argparse.ArgumentTypeError("jobs must be a positive integer from 1 to " + str(MAX_JOBS))
    return int(value)


def load_check(repo):
    spec = importlib.util.spec_from_file_location("local_build_app_check", repo / "scripts/check-app-rust.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def ordinary_directory(path):
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or any(part.casefold() == "worktrees" for part in path.parts) or
            any(character in str(path) for character in ("\r", "\n", "\0", ";", ","))):
        raise Refusal("use explicit canonical absolute paths outside protected worktrees")
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if (not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400):
            raise Refusal("symlink/reparse/non-directory path component: " + str(ancestor))
    return path


def owned_directory(path):
    path = ordinary_directory(path)
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise Refusal("directory must be current-user owned without group/other write access: " + str(path))
    return path


def regular_file(path, *, owned=True):
    ordinary_directory(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or
            (owned and (info.st_uid != os.getuid() or info.st_nlink != 1))):
        raise Refusal("expected an ordinary unaliased file: " + str(path))
    return path


def inside(path, root):
    return path.is_relative_to(root)


def git_environment(environment):
    result = {key: value for key, value in environment.items() if not key.upper().startswith("GIT_")}
    result.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    return result


def safe_environment(inherited):
    for key in ("RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTDOC"):
        if inherited.get(key):
            raise Refusal("unexpected compiler override: " + key)
    if inherited.get("CARGO_BUILD_TARGET", TARGET) != TARGET:
        raise Refusal("conflicting CARGO_BUILD_TARGET")
    # No signing, marketplace, provider tokens or caller's Cargo overrides reach
    # child tools. HOME is retained; private caches have their own actual vars.
    keep = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
            "DEVELOPER_DIR", "RUSTUP_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    return git_environment({key: value for key, value in inherited.items() if key in keep})


def extract_bridge_recipe(workflow):
    matches = re.findall(r"(?m)^      - name: Generate bridge\n        working-directory: rustdesk\n        run: \|\n((?:          .*\n)+)", workflow)
    if len(matches) != 1:
        raise Refusal("expected one exact Generate bridge workflow block")
    body = "\n".join(line[10:] for line in matches[0].splitlines()) + "\n"
    lines = body.replace("\\\n", " ").splitlines()
    # Join indentation left by YAML continuations before validating tokens.
    lines = [" ".join(shlex.split(line)) if index >= len(BRIDGE_PATCHES) + 1 else line
             for index, line in enumerate(lines)]
    if len(lines) != len(BRIDGE_PATCHES) + 3 or lines[-3] != PUB_GET_LINE:
        raise Refusal("bridge pub resolution or command count differs from the reviewed workflow")
    if shlex.split(lines[-2]) != shlex.split(CODEGEN_LINE) or shlex.split(lines[-1]) != shlex.split(COPY_LINE):
        raise Refusal("bridge generator or six-output copy command differs from the reviewed workflow")
    commands = []
    for line, expected in zip(lines, BRIDGE_PATCHES):
        tokens = shlex.split(line)
        assignments = {}
        if tokens and tokens[0] == "RDREPO=.":
            assignments["RDREPO"] = "."
            tokens = tokens[1:]
        if len(tokens) < 2 or tokens[0] != "python3":
            raise Refusal("unexpected executable in bridge patch preparation")
        path = tokens[1]
        prefix = "$GITHUB_WORKSPACE/scripts/" if path.startswith("$GITHUB_WORKSPACE/scripts/") else "../scripts/"
        if not path.startswith(prefix) or (path.removeprefix(prefix), *tokens[2:]) != expected:
            raise Refusal("ordered bridge restoration/recomposition differs from the reviewed workflow")
        if bool(assignments) != (expected[0] in ("patch-support-macos-texture.py", "patch-support-macos-window.py")):
            raise Refusal("bridge patch environment assignment changed")
        commands.append((expected, assignments))
    return body, commands


class Runner:
    def __init__(self, output, environment):
        self.output = output
        self.environment = environment
        self.commands = output / "commands"
        self.commands.mkdir(mode=0o700)
        self.count = 0

    def call(self, label, argv, cwd, *, extra=None, read=False, timeout=7200):
        self.count += 1
        stem = self.commands / (f"{self.count:03d}-" + label)
        argv = [str(value) for value in argv]
        environment = dict(self.environment)
        environment.update(extra or {})
        write_json(stem.with_suffix(".command.json"), {"argv": argv, "cwd": str(cwd),
                   "timeoutSeconds": timeout, "scope": "local unsigned ARM application build"})
        started = time.monotonic()
        result = {"status": "failed", "argv": argv, "cwd": str(cwd), "returncode": None}
        print("RUN: " + label, flush=True)
        stdout_path, stderr_path = stem.with_suffix(".stdout.log"), stem.with_suffix(".stderr.log")
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                process = subprocess.run(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                         stdout=stdout, stderr=stderr, check=False, timeout=timeout)
            result.update(returncode=process.returncode, status="passed" if process.returncode == 0 else "failed")
        except (OSError, subprocess.SubprocessError) as error:
            result["error"] = str(error)
        finally:
            result.update(elapsedSeconds=round(time.monotonic() - started, 3),
                          stdoutSha256=digest(stdout_path), stderrSha256=digest(stderr_path))
            write_json(stem.with_suffix(".result.json"), result)
        if result["status"] != "passed":
            raise Refusal(label + " failed; exact command, exit and raw output: " + str(stem))
        if read:
            if stdout_path.stat().st_size > 4_000_000:
                raise Refusal(label + " returned oversized identity output")
            return stdout_path.read_bytes()
        return result


def require_json(path, **expected):
    regular_file(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if any(value.get(key) != item for key, item in expected.items()):
        raise Refusal("required successful proof is absent or inconsistent: " + str(path))
    return value


def command_evidence(previous, label, *, successful=True):
    paths = list((owned_directory(previous / "commands")).glob("*-" + label + ".command.json"))
    if len(paths) != 1:
        raise Refusal("expected one closed previous command: " + label)
    command_path = regular_file(paths[0])
    stem = command_path.name.removesuffix(".command.json")
    result_path = regular_file(command_path.with_name(stem + ".result.json"))
    command = json.loads(command_path.read_text())
    result = json.loads(result_path.read_text())
    if (result.get("argv") != command.get("argv") or result.get("cwd") != command.get("cwd") or
            type(result.get("returncode")) is not int or result.get("status") not in ("passed", "failed")):
        raise Refusal("previous command is not closed with an actual exit: " + label)
    if successful and (result["status"] != "passed" or result["returncode"] != 0):
        raise Refusal("previous required command did not pass: " + label)
    raw = {}
    for stream in ("stdout", "stderr"):
        path = regular_file(command_path.with_name(stem + "." + stream + ".log"))
        if digest(path) != result.get(stream + "Sha256"):
            raise Refusal("previous raw command evidence changed: " + label)
        raw[stream] = path
    return command, result, raw


def cache_inventory(cache):
    entries = []
    def visit(directory):
        owned_directory(directory)
        for path in sorted(directory.iterdir()):
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                visit(path)
            else:
                regular_file(path)
                entries.append({"path": path.relative_to(cache).as_posix(), "sha256": digest(path)})
    visit(cache)
    if not entries:
        raise Refusal("previous codec cache is empty")
    return entries


def admit_reuse(previous, root, output):
    previous = owned_directory(previous)
    if previous == root or not inside(previous, root) or inside(output, previous) or inside(previous, output):
        raise Refusal("reuse input must be a separate closed descendant of the current run root")
    if stat.S_IMODE(previous.lstat().st_mode) != 0o700:
        raise Refusal("previous build output must retain private 0700 permissions")
    for parent in previous.parents:
        owned_directory(parent)
        if parent == root:
            break
    if (previous / "build-active.json").exists() or (previous / "build-active.json").is_symlink():
        raise Refusal("previous build is active or lacks a clean close")
    result_path = regular_file(previous / "build.result.json")
    result = require_json(result_path, suppliedSourceUnchanged=True, suppliedVcpkgUnchanged=True,
                          pipelineInputsUnchanged=True, target=TARGET, minimum=MINIMUM)
    if (type(result.get("buildQualified")) is not bool or any(result.get(key) is not True for key in
            ("suppliedSourceUnchanged", "suppliedVcpkgUnchanged", "pipelineInputsUnchanged"))):
        raise Refusal("previous result does not contain exact closed ownership outcomes")
    # Legacy closed outputs predate the active marker. Their final result exists
    # only after all commands returned; additionally require every original raw
    # command/result pair to be closed, including a genuine failed last command.
    commands = list((owned_directory(previous / "commands")).glob("*.command.json"))
    if not commands or len(commands) > 1000:
        raise Refusal("previous output has no bounded closed command inventory")
    for path in commands:
        label = path.name.split("-", 1)[1].removesuffix(".command.json")
        command_evidence(previous, label, successful=False)
    inputs = require_json(previous / "inputs.json", vcpkgHead=VCPKG_HEAD, flutterHead=FLUTTER_HEAD,
                          upstreamHead=UPSTREAM_HEAD, hbbHead=HBB_HEAD)
    require_json(previous / "native-codecs.json", status="passed", architecture="arm64", advertised_minimum=MINIMUM)
    codegen_install(previous)
    return {"root": previous, "closedResult": result, "closedResultSha256": digest(result_path),
            "inputs": inputs, "codecCache": previous / "codec-cache", "cacheInventory": cache_inventory(previous / "codec-cache")}


def triplet_hashes(repo):
    return {path.relative_to(repo).as_posix(): digest(regular_file(path))
            for path in sorted((repo / ".github/vcpkg-triplets").glob("*.cmake"))}


def reuse_profile(previous, current):
    old = previous["root"]
    pipeline = require_json(old / "pipeline-inputs.json")
    entries = {item["path"]: item["sha256"] for item in pipeline.get("entries", [])}
    triplets = {name: value for name, value in entries.items()
                if name.startswith(".github/vcpkg-triplets/") and name.endswith(".cmake")}
    if not triplets or any(value is None for value in triplets.values()):
        raise Refusal("previous current-triplet hashes are absent")
    historical = {"vcpkgHead": previous["inputs"]["vcpkgHead"], "target": TARGET, "minimum": MINIMUM,
                  "triplets": triplets,
                  "flutterIdentity": require_json(old / "flutter-identity.json"),
                  "flutterHashes": require_json(old / "flutter-input-hashes.json")}
    for key, label in (("rustcVersion", "rustc-version"), ("cargoVersion", "cargo-version"),
                       ("sdkPath", "macos-sdk"), ("clangPath", "macos-clang")):
        _, _, raw = command_evidence(old, label)
        value = raw["stdout"].read_text().strip()
        # Retain the exact version-bearing xcrun identity AND the canonical
        # SDKROOT actually used by tools; neither may drift between builds.
        if key == "sdkPath":
            historical["sdkCanonicalPath"] = str(ordinary_directory(Path(value).resolve(strict=True)))
        historical[key] = value
    if historical != current:
        raise Refusal("previous codec/codegen toolchain, SDK, Flutter, vcpkg or overlay profile differs")
    return historical


def codegen_install(old):
    command, result, _ = command_evidence(old, "install-bridge-codegen")
    expected = ["rustup", "run", TOOLCHAIN, "cargo", "install", "flutter_rust_bridge_codegen",
                "--version", "1.80.1", "--features", "uuid", "--locked", "--root", str(old / "codegen")]
    if command["argv"] != expected:
        raise Refusal("previous FRB install is not the exact locked uuid1.80.1 install")
    source = regular_file(old / "codegen/bin/flutter_rust_bridge_codegen")
    return source, result


def reuse_codegen(previous, destination, runner, repo):
    source, result = codegen_install(previous["root"])
    before = digest(source)
    destination.parent.mkdir(parents=True, mode=0o700)
    shutil.copy2(source, destination)
    regular_file(destination)
    if digest(destination) != before or digest(source) != before:
        raise Refusal("FRB executable changed during independent copying")
    version = runner.call("reuse-codegen-version", [destination, "--version"], repo, read=True).decode().strip()
    if not re.fullmatch(r"(?:flutter_rust_bridge_codegen\s+)?1\.80\.1", version):
        raise Refusal("reused FRB executable did not report actual version1.80.1")
    if digest(destination) != before or digest(source) != before:
        raise Refusal("FRB executable changed while verifying version")
    return {"oldExecutable": str(source), "copiedExecutable": str(destination), "sha256": before,
            "version": version, "successfulInstallResult": result}


def verify_lock(app, before, result, phase):
    after = digest(regular_file(app / "Cargo.lock"))
    result.update(cargoLockBeforeSha256=before, cargoLockAfterSha256=after, cargoLockUnchanged=before == after)
    result["cargoLockAfter" + phase + "Sha256"] = after
    if after != before:
        raise Refusal(phase + " changed the held private application Cargo.lock")


def owned_inventory(check, root):
    entries = check.inventory(root)
    for entry in entries:
        path = root / entry["path"]
        info = path.lstat()
        if entry["kind"] == "symlink" or info.st_uid != os.getuid() or (
                stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise Refusal("source must be current-user owned without links/hardlinks: " + str(path))
    return entries


def copy_ordinary(check, source, destination):
    before = owned_inventory(check, source)
    def ignored(directory, names):
        relative = Path(directory).relative_to(source).as_posix()
        return [name for name in names if check.excluded(relative, name)]
    shutil.copytree(source, destination, symlinks=True, ignore=ignored)
    if before != owned_inventory(check, destination) or before != owned_inventory(check, source):
        raise Refusal("ordinary source changed while copying")
    return before


def admit(args, repo, environment):
    if platform.system() != "Darwin" or platform.machine().casefold() not in ("arm64", "aarch64"):
        raise Refusal("this driver requires a native Apple Silicon Mac; no emulated or Windows qualification")
    if sys.version_info[:2] != (3, 11):
        raise Refusal("run this driver with python3.11 (the pinned source-control runtime)")
    repo = owned_directory(repo)
    if ordinary_directory(Path.cwd()) != repo:
        raise Refusal("start the driver from its exact repository root")
    root = owned_directory(args.run_root)
    if root == repo or not inside(root, repo):
        raise Refusal("run root must be strictly inside this repository")
    for path in (root, *root.parents):
        owned_directory(path)
        if path == repo:
            break
    relative = root.relative_to(repo).as_posix() + "/"
    ignored_argv = ["git", "-C", str(repo), "-c", "core.excludesFile=" + os.devnull,
                    "check-ignore", "--no-index", "--quiet", "--", relative]
    ignored = subprocess.run(ignored_argv, env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
    if ignored.returncode:
        raise Refusal("run root must be Git-ignored in this checkout: " + str(root))
    output = Path(args.output)
    if (not output.is_absolute() or ".." in output.parts or output == root or not inside(output, root) or
            any(character in str(output) for character in ("\r", "\n", "\0", ";", ","))):
        raise Refusal("fresh output must be an explicit absolute child of the admitted run root")
    owned_directory(output.parent)
    for path in (output.parent, *output.parent.parents):
        owned_directory(path)
        if path == root:
            break
    if output.exists() or output.is_symlink():
        raise Refusal("output already exists; choose a fresh output path")
    paths = [owned_directory(value) for value in (args.source, args.flutter, args.vcpkg)]
    for path in paths:
        if not inside(path, repo) or inside(output, path) or inside(path, output):
            raise Refusal("source/SDK/vcpkg must be checkout-local and separate from the fresh output")
        for parent in path.parents:
            owned_directory(parent)
            if parent == repo:
                break
    return root, output, paths, (ignored_argv, ignored)


def write_python_launcher(directory):
    directory.mkdir(mode=0o700)
    launcher = directory / "python3"
    with launcher.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    launcher.chmod(0o700)


def bundle_manifest(bundle):
    entries = []
    def visit(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(bundle).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if not path.resolve(strict=True).is_relative_to(bundle):
                    raise Refusal("bundle link escapes app root")
                entries.append({"path": name, "kind": "symlink", "target": target})
            elif stat.S_ISDIR(info.st_mode):
                entries.append({"path": name, "kind": "directory", "mode": stat.S_IMODE(info.st_mode)})
                visit(path)
            elif stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                entries.append({"path": name, "kind": "file", "mode": stat.S_IMODE(info.st_mode),
                                "size": info.st_size, "sha256": digest(path)})
            else:
                raise Refusal("unexpected bundle filesystem object")
    visit(owned_directory(bundle))
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"app": str(bundle), "treeSha256": hashlib.sha256(encoded).hexdigest(), "entries": entries}


def pipeline_inventory(repo, names):
    entries = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise Refusal("unsafe tracked pipeline input name")
        path = regular_file(repo / relative)
        entries.append({"path": relative.as_posix(), "mode": stat.S_IMODE(path.stat().st_mode), "sha256": digest(path)})
    return entries


def build(args, repo):
    try:
        jobs = job_count(args.jobs)
    except argparse.ArgumentTypeError as error:
        raise Refusal(str(error)) from error
    environment = safe_environment(os.environ)
    environment.update(CARGO_BUILD_JOBS=str(jobs), CMAKE_BUILD_PARALLEL_LEVEL=str(jobs), VCPKG_MAX_CONCURRENCY=str(jobs))
    root, output, (original, flutter, original_vcpkg), ignored = admit(args, repo, environment)
    check = load_check(repo)
    workflow = regular_file(repo / ".github/workflows/build.yml").read_text(encoding="utf-8")
    bridge_body, bridge_commands = extract_bridge_recipe(workflow)
    missing = [name + ": " + install for name, install in TOOLS.items() if not shutil.which(name, path=environment.get("PATH"))]
    if missing:
        raise Refusal("missing build prerequisites; " + "; ".join(missing))
    for name in ("bin/flutter", "bin/cache/dart-sdk/bin/dart"):
        regular_file(flutter / name)
    for name in ("buildtrees", "packages", "installed", "downloads", "vcpkg"):
        if (original_vcpkg / name).exists() or (original_vcpkg / name).is_symlink():
            raise Refusal("provide a source-only pinned vcpkg checkout; private build/cache output belongs to this driver: " + name)
    output.mkdir(mode=0o700)
    write_json(output / "build-active.json", {"pid": os.getpid(), "target": TARGET, "jobs": jobs})
    runner = Runner(output, environment)
    argv, ignored_result = ignored
    (output / "root-admission.stdout.log").write_bytes(ignored_result.stdout)
    (output / "root-admission.stderr.log").write_bytes(ignored_result.stderr)
    write_json(output / "root-admission.json", {"argv": argv, "returncode": ignored_result.returncode,
               "stdoutSha256": digest(output / "root-admission.stdout.log"),
               "stderrSha256": digest(output / "root-admission.stderr.log"), "runRoot": str(root)})
    result = {"buildQualified": False, "target": TARGET, "minimum": MINIMUM, "jobs": jobs,
              "reuseFrom": str(args.reuse_from) if args.reuse_from is not None else None,
              "limitations": "Unsigned app build only. Additional mandatory source/native tests and physical remote-support acceptance remain separate."}
    before = vcpkg_before = pipeline_before = previous = lock_before = None
    try:
        if args.reuse_from is not None:
            previous = admit_reuse(args.reuse_from, root, output)
            shutil.copyfile(previous["root"] / "build.result.json", output / "reuse-previous-build.result.json")
        def git(label, directory, *arguments):
            return runner.call(label, ["git", "-C", directory, *arguments], repo, read=True, timeout=30)
        source_head = git("pipeline-head", repo, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", source_head):
            raise Refusal("pipeline source HEAD is unavailable")
        pipeline_names = git("pipeline-inputs", repo, "ls-files", "-z", "--", "scripts", "branding",
                             ".github/vcpkg-triplets", ".github/workflows/build.yml").decode("utf-8").split("\0")
        pipeline_names = sorted(set(filter(None, pipeline_names)) | {"scripts/local/build-macos.py"})
        pipeline_before = pipeline_inventory(repo, pipeline_names)
        write_json(output / "pipeline-inputs.json", {"sourceHead": source_head, "entries": pipeline_before})
        for label, directory, expected in (("source", original, UPSTREAM_HEAD),
                                            ("hbb", original / "libs/hbb_common", HBB_HEAD),
                                            ("vcpkg", original_vcpkg, VCPKG_HEAD),
                                            ("flutter", flutter, FLUTTER_HEAD)):
            actual = git(label + "-head", directory, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
            if actual != expected:
                raise Refusal(label + " HEAD differs from the exact reviewed pin")
            if git(label + "-clean", directory, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"):
                raise Refusal(label + " source is dirty; an ordinary clean pinned input is required")
        modules = git("submodules", original, "submodule", "status", "--recursive").decode("ascii").splitlines()
        if len(modules) != 1 or not modules[0].startswith(" " + HBB_HEAD + " libs/hbb_common "):
            raise Refusal("exact initialized hbb_common submodule required")
        app, vcpkg = output / "rustdesk", output / "vcpkg"
        before = copy_ordinary(check, original, app)
        vcpkg_before = copy_ordinary(check, original_vcpkg, vcpkg)
        write_json(output / "inputs.json", {"sourceHead": source_head, "upstreamHead": UPSTREAM_HEAD, "hbbHead": HBB_HEAD,
                   "vcpkgHead": VCPKG_HEAD, "flutterHead": FLUTTER_HEAD, "sourceInventorySha256": check.binding(before),
                   "vcpkgInventorySha256": check.binding(vcpkg_before), "workflowSha256": digest(repo / ".github/workflows/build.yml"),
                   "bridgeRecipeSha256": hashlib.sha256(bridge_body.encode()).hexdigest(), "driverSha256": digest(Path(__file__))})
        (output / "bridge-recipe.sh.txt").write_text(bridge_body, encoding="utf-8")
        rust_paths = {}
        for tool in ("rustc", "cargo"):
            path = runner.call(tool + "-path", ["rustup", "which", "--toolchain", TOOLCHAIN, tool], repo, read=True).decode().strip()
            resolved = regular_file(Path(path), owned=False)
            if resolved.name != tool:
                raise Refusal("unexpected pinned Rust tool path")
            rust_paths[tool] = resolved
        if rust_paths["cargo"].parent != rust_paths["rustc"].parent:
            raise Refusal("Rust/Cargo must come from the same installed pinned toolchain")
        rust_version = runner.call("rustc-version", ["rustup", "run", TOOLCHAIN, "rustc", "-Vv"], repo, read=True).decode()
        cargo_version = runner.call("cargo-version", ["rustup", "run", TOOLCHAIN, "cargo", "-V"], repo, read=True).decode()
        if not rust_version.startswith("rustc 1.81.0 ") or "\nhost: aarch64-apple-darwin\n" not in rust_version or not cargo_version.startswith("cargo 1.81.0 "):
            raise Refusal("exact native Rust/Cargo1.81.0 required; install rustup toolchain 1.81")
        targets = runner.call("rust-targets", ["rustup", "target", "list", "--installed", "--toolchain", TOOLCHAIN], repo, read=True).decode().splitlines()
        if not {TARGET, "x86_64-apple-darwin"}.issubset(targets):
            raise Refusal("install both shipping targets: rustup target add --toolchain 1.81 aarch64-apple-darwin x86_64-apple-darwin")
        tools_bin = output / "tools-bin"
        write_python_launcher(tools_bin)
        home, codegen_root = output / "cargo-home", output / "codegen"
        (output / "codec-cache").mkdir(mode=0o700)
        (output / "pub-cache").mkdir(mode=0o700)
        environment.update({"PATH": os.pathsep.join((str(tools_bin), str(flutter / "bin"), str(rust_paths["cargo"].parent), environment.get("PATH", os.defpath))),
                            "CARGO_HOME": str(home), "RUSTUP_TOOLCHAIN": TOOLCHAIN, "CARGO_BUILD_TARGET": TARGET,
                            "VCPKG_ROOT": str(vcpkg), "VCPKG_DEFAULT_TRIPLET": "arm64-osx", "VCPKG_DEFAULT_HOST_TRIPLET": "arm64-osx",
                            "VCPKG_OVERLAY_TRIPLETS": str(repo / ".github/vcpkg-triplets"),
                            "VCPKG_BINARY_SOURCES": "clear;files," + str(output / "codec-cache") + ",readwrite",
                            "MACOSX_DEPLOYMENT_TARGET": MINIMUM, "MIXEL_MACOS_ARCH": "aarch64", "FLUTTER_XCODE_ARCHS": "arm64",
                            "RDREPO": str(app), "BRANDING": str(repo / "branding"), "GITHUB_WORKSPACE": str(repo),
                            "PUB_CACHE": str(output / "pub-cache"), "FLUTTER_SUPPRESS_ANALYTICS": "true", "DART_SUPPRESS_ANALYTICS": "true",
                            "CI": "true", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        runner.environment = environment
        python = sys.executable
        def script(label, name, *arguments, cwd=repo, extra=None):
            return runner.call(label, [python, repo / "scripts" / name, *arguments], cwd, extra=extra)
        script("reserve-cargo", "patch-support-url-log-privacy.py", "--reserve-cargo-home", home)
        sdk = runner.call("flutter-version", [flutter / "bin/flutter", "--version", "--machine"], repo, read=True)
        sdk_identity = json.loads(sdk)
        if sdk_identity.get("frameworkVersion") != FLUTTER_VERSION or sdk_identity.get("frameworkRevision") != FLUTTER_HEAD:
            raise Refusal("official complete Flutter3.27.4 SDK required at --flutter")
        write_json(output / "flutter-identity.json", sdk_identity)
        runner.call("flutter-precache", [flutter / "bin/flutter", "precache", "--macos"], repo)
        framework = flutter / "bin/cache/artifacts/engine/darwin-x64-release/FlutterMacOS.xcframework/macos-arm64_x86_64/FlutterMacOS.framework"
        header = (framework / "Headers/FlutterEngine.h").resolve(strict=True)
        if not inside(header, flutter):
            raise Refusal("Flutter framework header resolves outside the verified SDK")
        regular_file(header)
        framework_binary = (framework / "FlutterMacOS").resolve(strict=True)
        if not inside(framework_binary, flutter):
            raise Refusal("Flutter framework binary resolves outside the verified SDK")
        regular_file(framework_binary)
        write_json(output / "flutter-input-hashes.json", {"frameworkHead": FLUTTER_HEAD,
                   "flutterScriptSha256": digest(flutter / "bin/flutter"),
                   "dartBinarySha256": digest(flutter / "bin/cache/dart-sdk/bin/dart"),
                   "frameworkBinarySha256": digest(framework_binary), "frameworkHeaderSha256": digest(header)})
        sdk_reported = runner.call("macos-sdk", ["xcrun", "--sdk", "macosx", "--show-sdk-path"], repo, read=True).decode().strip()
        sdk_root = Path(sdk_reported).resolve(strict=True)
        ordinary_directory(sdk_root)
        clang_lookup = Path(runner.call("macos-clang", ["xcrun", "--find", "clang"], repo, read=True).decode().strip())
        clang = clang_lookup.resolve(strict=True)
        regular_file(clang, owned=False)
        libclang = regular_file((clang_lookup.parent.parent / "lib/libclang.dylib").resolve(strict=True), owned=False)
        environment.update(SDKROOT=str(sdk_root), LIBCLANG_PATH=str(libclang.parent))
        profile = {"vcpkgHead": VCPKG_HEAD, "target": TARGET, "minimum": MINIMUM, "triplets": triplet_hashes(repo),
                   "rustcVersion": rust_version.strip(), "cargoVersion": cargo_version.strip(),
                   "sdkPath": sdk_reported, "sdkCanonicalPath": str(sdk_root), "clangPath": str(clang_lookup), "flutterIdentity": sdk_identity,
                   "flutterHashes": require_json(output / "flutter-input-hashes.json")}
        write_json(output / "native-reuse-profile.json", profile)
        if previous is not None:
            matched = reuse_profile(previous, profile)
            environment["VCPKG_BINARY_SOURCES"] = ("clear;files," + str(previous["codecCache"]) + ",read;files," +
                                                    str(output / "codec-cache") + ",readwrite")
            write_json(output / "reuse-admission.json", {"previousOutput": str(previous["root"]),
                       "previousBuildQualified": previous["closedResult"]["buildQualified"],
                       "previousClosedResultSha256": previous["closedResultSha256"], "matchedProfile": matched,
                       "codecCacheAccess": "read", "codecCacheInventory": previous["cacheInventory"],
                       "cargoHomeReused": False, "applicationOutputsReused": False})
        runner.call("bootstrap-vcpkg", ["bash", vcpkg / "bootstrap-vcpkg.sh", "-disableMetrics"], vcpkg)
        runner.call("install-codecs", [vcpkg / "vcpkg", "install", "--overlay-triplets=" + environment["VCPKG_OVERLAY_TRIPLETS"],
                                      "--triplet", "arm64-osx", "libvpx", "libyuv", "opus", "aom", "libjpeg-turbo"], vcpkg)
        script("verify-codecs", "verify-macos-native-minimum.py", "--lib-dir", vcpkg / "installed/arm64-osx/lib",
               "--arch", "arm64", "--minimum", MINIMUM, "--output", output / "native-codecs.json")
        require_json(output / "native-codecs.json", status="passed", architecture="arm64", advertised_minimum=MINIMUM)
        runner.call("apply-branding", ["bash", repo / "scripts/apply-branding.sh"], repo)
        lock_before = digest(app / "Cargo.lock")
        result["cargoLockBeforeSha256"] = lock_before
        runner.call("fetch-locked", ["rustup", "run", TOOLCHAIN, "cargo", "fetch", "--locked", "--target", TARGET], app)
        if digest(app / "Cargo.lock") != lock_before:
            raise Refusal("locked application fetch changed Cargo.lock")
        script("patch-owned-tao", "patch-support-url-log-privacy.py", "--cargo-home", home)
        script("verify-owned-tao", "patch-support-url-log-privacy.py", "--cargo-home", home, "--check")
        check.verify_cargo_home(home, patched=True)
        if previous is None:
            runner.call("install-bridge-codegen", ["rustup", "run", TOOLCHAIN, "cargo", "install", "flutter_rust_bridge_codegen",
                                                   "--version", "1.80.1", "--features", "uuid", "--locked", "--root", codegen_root], repo)
        else:
            codegen_proof = reuse_codegen(previous, codegen_root / "bin/flutter_rust_bridge_codegen", runner, repo)
            write_json(output / "codegen-reuse.json", codegen_proof)
        codegen = regular_file(codegen_root / "bin/flutter_rust_bridge_codegen")
        for index, (command, extra) in enumerate(bridge_commands):
            script(f"bridge-patch-{index + 1:02d}", command[0], *command[1:], cwd=app, extra=extra)
        pubspec = regular_file(app / "flutter/pubspec.yaml")
        raw = pubspec.read_bytes()
        if raw.count(b"extended_text: 14.0.0") + raw.count(b"extended_text: 13.0.0") != 1:
            raise Refusal("reviewed extended_text bridge dependency changed")
        pubspec.write_bytes(raw.replace(b"extended_text: 14.0.0", b"extended_text: 13.0.0"))
        runner.call("bridge-pub-get", [flutter / "bin/flutter", "pub", "get"], app / "flutter")
        stale_bridge = {}
        for name in BRIDGE_FILES:
            path = app / name
            if path.exists() or path.is_symlink():
                stale_bridge[name] = digest(regular_file(path))
                path.unlink()
        write_json(output / "bridge-before-generation.json", {"removedOnlyFromPrivateCopy": stale_bridge})
        runner.call("generate-bridge", [codegen, "--rust-input", "./src/flutter_ffi.rs", "--dart-output", "./flutter/lib/generated_bridge.dart",
                                        "--c-output", "./flutter/macos/Runner/bridge_generated.h"], app)
        shutil.copyfile(app / "flutter/macos/Runner/bridge_generated.h", app / "flutter/ios/Runner/bridge_generated.h")
        bridge = {name: digest(regular_file(app / name)) for name in BRIDGE_FILES if (app / name).stat().st_size > 0}
        if set(bridge) != set(BRIDGE_FILES):
            raise Refusal("all six nonempty generated bridge outputs are mandatory")
        write_json(output / "bridge-files.json", bridge)
        if digest(app / "Cargo.lock") != lock_before:
            raise Refusal("bridge generation changed the held application Cargo.lock")
        script("check-entire-app", "check-app-rust.py", "--source", app, "--output", output / "rust-check",
               "--target", TARGET, "--run", "--local-run-root", root)
        require_json(output / "rust-check/check.result.json", status="passed", compiled=True, sourceUnchanged=True)
        runner.call("build-entire-app", [python, "build.py", "--flutter", "--unix-file-copy-paste"], app)
        verify_lock(app, lock_before, result, "Build")
        runner.call("analyze-entire-flutter", [flutter / "bin/flutter", "analyze", "--no-pub", "--no-fatal-infos"], app / "flutter")
        script("compiled-support-guard", "test-compiled-support-guard.py")
        verify_lock(app, lock_before, result, "Guard")
        bundle = app / "flutter/build/macos/Build/Products/Release/Mixel-Remote.app"
        owned_directory(bundle)
        script("compiled-branding", "verify-flutter-branding.py", "--assets",
               bundle / "Contents/Frameworks/App.framework/Resources/flutter_assets")
        script("verify-entire-bundle", "verify-macos-bundle-minimum.py", "--bundle", bundle,
               "--arch", "arm64", "--minimum", MINIMUM, "--output", output / "bundle-minimum.json")
        require_json(output / "bundle-minimum.json", status="passed", architecture="arm64", advertised_minimum=MINIMUM,
                     bundle_identifier="ch.mixel.remote", bundle_executable="Mixel-Remote")
        manifest = bundle_manifest(bundle)
        write_json(output / "app-manifest.json", manifest)
        result.update(app=str(bundle), appTreeSha256=manifest["treeSha256"], sourceHead=source_head, upstreamHead=UPSTREAM_HEAD,
                      hbbHead=HBB_HEAD, vcpkgHead=VCPKG_HEAD, commandsPassed=runner.count)
        result["buildQualified"] = True
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        try:
            if lock_before is not None:
                lock_after = digest(regular_file(app / "Cargo.lock"))
                result.update(cargoLockBeforeSha256=lock_before, cargoLockAfterSha256=lock_after,
                              cargoLockUnchanged=lock_before == lock_after)
                if lock_before != lock_after:
                    result.update(buildQualified=False, error="held private application Cargo.lock changed")
            if previous is not None:
                cache_unchanged = previous["cacheInventory"] == cache_inventory(previous["codecCache"])
                result["previousCodecCacheUnchanged"] = cache_unchanged
                if not cache_unchanged:
                    result.update(buildQualified=False, error="read-only previous codec cache changed")
            unchanged = before is not None and before == owned_inventory(check, original)
            vcpkg_unchanged = vcpkg_before is not None and vcpkg_before == owned_inventory(check, original_vcpkg)
            pipeline_unchanged = pipeline_before is not None and pipeline_before == pipeline_inventory(repo, pipeline_names)
            result.update(suppliedSourceUnchanged=unchanged, suppliedVcpkgUnchanged=vcpkg_unchanged, pipelineInputsUnchanged=pipeline_unchanged)
            if not unchanged or not vcpkg_unchanged or not pipeline_unchanged:
                result["buildQualified"] = False
                if before is not None:
                    result["error"] = "supplied source/vcpkg/pipeline immutability check failed"
        except Exception as error:
            result.update(buildQualified=False, error="input recheck failed: " + str(error))
        result["buildClosed"] = True
        write_json(output / "build.result.json", result)
        (output / "build-active.json").unlink()
    if not result["buildQualified"]:
        raise Refusal(result.get("error", "local build did not qualify"))
    print("PASS: unsigned ARM application built and verified; app tree SHA256=" + result["appTreeSha256"], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, help_text in (("source", "clean exact RustDesk1.4.6 checkout including hbb_common"),
                            ("flutter", "complete official Flutter3.27.4 SDK root"),
                            ("vcpkg", "source-only exact pinned vcpkg checkout"),
                            ("run-root", "existing checkout-local Git-ignored owned build root"),
                            ("output", "fresh absolute child of --run-root")):
        parser.add_argument("--" + name, type=Path, required=True, help=help_text)
    parser.add_argument("--run", action="store_true", help="explicitly run dependency fetches and native build tools")
    parser.add_argument("--jobs", type=job_count, default=1,
                        help="owned Cargo/CMake/vcpkg concurrency, 1–8 (default: 1)")
    parser.add_argument("--reuse-from", type=Path,
                        help="closed private output with matching codec profile and exact successful FRB install")
    args = parser.parse_args()
    if not args.run:
        print("PLAN ONLY: pass --run after the local native compiler reservation is granted; no commands or writes performed.")
        print(json.dumps({"target": TARGET, "minimum": MINIMUM, "jobs": args.jobs,
                          "reuseFrom": str(args.reuse_from) if args.reuse_from is not None else None,
                          "paths": {key: str(value) for key, value in vars(args).items() if key not in ("run", "jobs", "reuse_from")}}, sort_keys=True))
        return 0
    try:
        build(args, Path(__file__).absolute().parents[2])
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("FAIL: " + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
