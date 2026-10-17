#!/usr/bin/env python3
"""Prepare an isolated real application cargo check; compilation requires --run in CI.

This is an early typecheck gate, not a replacement for complete platform builds,
Flutter analysis, signing, application execution or the existing release gates.
Only new, private output is written. The supplied branded application is copied,
including dependency Git metadata, before build.rs can rewrite src/version.rs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import threading
import time

SCHEMA = 1
VCPKG_COMMIT = "120deac3062162151622ca4860575a33844ba10b"
MATRIX = {
    "x86_64-unknown-linux-gnu": ("Linux", "1.75", "flutter", "x64-linux", None),
    "x86_64-pc-windows-msvc": ("Windows", "1.75", "flutter", "x64-windows-static", None),
    "aarch64-apple-darwin": ("Darwin", "1.81", "flutter,unix-file-copy-paste", "arm64-osx", "12.3"),
    "x86_64-apple-darwin": ("Darwin", "1.81", "flutter,unix-file-copy-paste", "x64-osx", "10.14"),
}
REQUIRED = (
    "Cargo.toml", "Cargo.lock", "build.rs", "src/lib.rs",
    "src/bridge_generated.rs", "src/bridge_generated.io.rs",
    "libs/hbb_common/Cargo.toml", "libs/enigo/Cargo.toml", "libs/scrap/Cargo.toml",
    "libs/scrap/build.rs",
)
# These are anchored application caches, not arbitrary directories named build or
# target. In particular libs/*/build source and .git/modules metadata are retained.
CACHES = {
    ".": {"build", "target", ".dart_tool", "__pycache__"},
    "flutter": {"build", ".dart_tool"},
    "flutter/macos/Flutter": {"ephemeral"},
    "flutter/windows/flutter": {"ephemeral"},
    "flutter/linux/flutter": {"ephemeral"},
}


class Refusal(RuntimeError):
    pass


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    # Every destination belongs to a just-reserved private output directory.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def ordinary_path(value, *, exists):
    if ".." in Path(value).parts:
        raise Refusal("parent traversal is not an explicit canonical path")
    path = Path(os.path.abspath(value))
    if any(part.casefold() == "worktrees" for part in path.parts):
        raise Refusal("worktrees paths are excluded")
    # Never follow a symlink/reparse-point ancestor into an unexpected output or
    # source. Callers on macOS should pass canonical /private/... temp paths.
    for ancestor in reversed((path, *path.parents)):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("indirect path component: " + str(ancestor))
        if ancestor != path and not stat.S_ISDIR(info.st_mode):
            raise Refusal("non-directory path ancestor")
    if exists and not path.is_dir():
        raise Refusal("expected an existing ordinary directory: " + str(path))
    if not exists and path.exists():
        raise Refusal("output must not already exist")
    return path


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def excluded(relative, name):
    return name.casefold() == "worktrees" or name in CACHES.get(relative, set())


def safe_destination(root, candidate, seen=()):
    """Resolve links/.. in filesystem order without touching excluded subtrees."""
    candidate = Path(candidate)
    if not candidate.is_absolute() or not inside(candidate, root):
        raise Refusal("source link escapes private copy")
    relative = candidate.relative_to(root)
    current = root
    for index, part in enumerate(relative.parts):
        if part == "..":
            if current == root:
                raise Refusal("source link escapes private copy")
            current = current.parent
            continue
        parent = current.relative_to(root).as_posix()
        if excluded(parent, part):
            raise Refusal("source link reaches an excluded subtree")
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            if current in seen or len(seen) >= 32:
                raise Refusal("source link cycle or excessive indirection")
            value = os.readlink(current)
            if Path(value).is_absolute():
                raise Refusal("absolute source symlink is not relocatable")
            remaining = relative.parts[index + 1:]
            return safe_destination(root, current.parent / value / Path(*remaining), (*seen, current))
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("source link reaches a reparse point")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise Refusal("source link traverses a non-directory")
    return current


def link_destination(root, path, target):
    if Path(target).is_absolute():
        raise Refusal("absolute source symlink is not relocatable: " + str(path.relative_to(root)))
    safe_destination(root, path.parent / target)
    return target


def git_pointer(root, path, raw):
    relative = path.relative_to(root).as_posix()
    if path.name == ".git":
        prefix = b"gitdir: "
        if not raw.startswith(prefix) or b"\n" in raw.rstrip(b"\r\n"):
            raise Refusal("invalid submodule Git pointer: " + relative)
        target = raw[len(prefix):].strip().decode("utf-8")
    elif path.name == "commondir" and ".git" in path.parts:
        target = raw.strip().decode("utf-8")
    elif path.name == "alternates" and ".git" in path.parts:
        if raw.strip():
            raise Refusal("external Git object alternates are not private")
        return
    else:
        return
    if Path(target).is_absolute():
        raise Refusal("absolute Git pointer is not relocatable: " + relative)
    candidate = safe_destination(root, path.parent / target)
    if not inside(candidate, root / ".git"):
        raise Refusal("Git pointer escapes copied ordinary checkout: " + relative)
    if any(part.casefold() == "worktrees" for part in candidate.parts):
        raise Refusal("Git worktree metadata is excluded")
    if not candidate.is_dir():
        raise Refusal("Git pointer is absent or indirect: " + relative)


def inventory(root):
    entries = []

    def visit(directory):
        relative = directory.relative_to(root).as_posix()
        # Decide exclusions by name before lstat/descent. A worktrees entry can be
        # a directory, symlink or inaccessible object: none of it is inspected.
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if excluded(relative, path.name):
                continue
            info = path.lstat()
            name = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                target = link_destination(root, path, os.readlink(path))
                entries.append({"path": name, "kind": "symlink", "target": target})
            elif getattr(info, "st_file_attributes", 0) & 0x400:
                raise Refusal("source contains a reparse point: " + name)
            elif stat.S_ISDIR(info.st_mode):
                entries.append({"path": name, "kind": "directory", "mode": mode})
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                digest = digest_file(path)
                after = path.lstat()
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode
                ):
                    raise Refusal("source changed while hashing: " + name)
                if path.name in (".git", "commondir", "alternates"):
                    git_pointer(root, path, path.read_bytes())
                entries.append({"path": name, "kind": "file", "mode": mode,
                                "size": info.st_size, "sha256": digest})
            else:
                raise Refusal("source contains a non-file object: " + name)

    visit(root)
    return entries


def binding(entries):
    raw = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def git_identity(root):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_OPTIONAL_LOCKS="0")
    result = {}
    for name, revision in (("head", "HEAD"), ("tree", "HEAD^{tree}")):
        run = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", revision],
                             env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             check=False)
        value = run.stdout.decode("ascii", errors="strict").strip()
        if run.returncode or len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise Refusal("source Git identity is unavailable; private diagnostics withheld")
        result[name] = value
    return result


def matrix(target, system=None, machine=None):
    system = platform.system() if system is None else system
    machine = platform.machine().casefold() if machine is None else machine.casefold()
    if target not in MATRIX or MATRIX[target][0] != system:
        raise Refusal("unsupported shipping host/target combination")
    if machine not in ({"x86_64", "amd64"} if system != "Darwin" else {"arm64", "aarch64", "x86_64"}):
        raise Refusal("unsupported shipping host architecture")
    host, toolchain, features, triplet, minimum = MATRIX[target]
    return {"host": host, "target": target, "toolchain": toolchain,
            "features": features, "triplet": triplet, "macosMinimum": minimum}


def command(config):
    return ["rustup", "run", config["toolchain"], "cargo", "check", "--locked", "--release",
            "--lib", "--features", config["features"], "--target", config["target"]]


def require_source(root):
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise Refusal("application source must be an ordinary Git checkout")
    for name in REQUIRED:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise Refusal("required real application/bridge input is absent: " + name)
        if not path.stat().st_size:
            raise Refusal("required application input is empty: " + name)


def run_environment(config, source, output, environment):
    # No local desktop compiler operation is permitted by --run. GitHub-hosted
    # runners are ephemeral and already own the shipping toolchain/dependencies.
    if (environment.get("GITHUB_ACTIONS") != "true" or
            environment.get("RUNNER_ENVIRONMENT") != "github-hosted" or
            environment.get("RUNNER_OS") != {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}[config["host"]]):
        raise Refusal("--run requires the matching GitHub-hosted CI runner")
    if environment.get("CARGO_BUILD_TARGET", config["target"]) != config["target"]:
        raise Refusal("CARGO_BUILD_TARGET conflicts with the explicit matrix target")
    # Rust wrappers/alternate compilers would turn the proposed whole-app check
    # into an unrelated executable. Shipping jobs do not use these overrides.
    for key in ("RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTDOC"):
        if environment.get(key):
            raise Refusal("unexpected compiler override: " + key)
    value = environment.get("VCPKG_ROOT", "")
    if not value:
        raise Refusal("shipping VCPKG_ROOT must already be installed")
    vcpkg = ordinary_path(value, exists=True)
    if inside(vcpkg, source) or inside(vcpkg, output):
        raise Refusal("vcpkg must be an external installed dependency")
    if git_identity(vcpkg)["head"] != VCPKG_COMMIT:
        raise Refusal("vcpkg revision differs from shipping pin")
    if not (vcpkg / "installed" / config["triplet"] / "lib").is_dir():
        raise Refusal("shipping vcpkg triplet libraries are absent")
    if config["host"] == "Darwin":
        if environment.get("MACOSX_DEPLOYMENT_TARGET") != config["macosMinimum"]:
            raise Refusal("macOS deployment target differs from shipping matrix")
        overlay = environment.get("VCPKG_OVERLAY_TRIPLETS", "")
        if not overlay or not ordinary_path(overlay, exists=True).joinpath(config["triplet"] + ".cmake").is_file():
            raise Refusal("shipping macOS overlay triplet is absent")
    runner_temp = environment.get("RUNNER_TEMP", "")
    if not runner_temp or not inside(output, ordinary_path(runner_temp, exists=True)):
        raise Refusal("CI output must be private to RUNNER_TEMP")
    cargo_home = environment.get("CARGO_HOME", "")
    if config["host"] == "Darwin" and not cargo_home:
        raise Refusal("macOS requires its already-patched owned shipping CARGO_HOME")
    if cargo_home:
        home = ordinary_path(cargo_home, exists=True)
        if inside(home, source) or inside(home, output):
            raise Refusal("Cargo registry must be an external installed dependency")
        if config["host"] == "Darwin":
            import importlib.util
            spec = importlib.util.spec_from_file_location("app_check_url_privacy", Path(__file__).with_name("patch-support-url-log-privacy.py"))
            privacy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(privacy)
            privacy.validate_cargo_home(home)
    result = dict(environment)
    result["CARGO_TARGET_DIR"] = str(output / "target")
    result["CARGO_BUILD_TARGET"] = config["target"]
    if config["host"] == "Windows":
        # Reuse the existing strict installed Microsoft LINK/PE identity checks.
        from rust_toolchain import find_msvc_linker
        result["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"] = str(find_msvc_linker(result))
    # Preserve the shipping patched Cargo registry and native compiler/SDK setup.
    # Never print the inherited environment (it can contain signing credentials).
    write_json(output / "check.environment.json", {
        "vcpkgRoot": str(vcpkg), "vcpkgCommit": VCPKG_COMMIT,
        "triplet": config["triplet"], "cargoHome": cargo_home or "inherited Cargo default",
        "cargoTargetDir": result["CARGO_TARGET_DIR"], "target": result["CARGO_BUILD_TARGET"],
        "macosMinimum": config["macosMinimum"],
        "msvcLinker": result.get("CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"),
    })
    return result


def capture(argv, cwd, environment, output, stem):
    plan = json.loads((output / "check.plan.json").read_text(encoding="utf-8"))
    selected = shutil.which(argv[0], path=environment.get("PATH"))
    write_json(output / (stem + ".command.json"), {
        "argv": argv, "cwd": str(cwd), "sourceInventorySha256": plan["sourceInventorySha256"],
        "driverSha256": plan["driverSha256"], "config": plan["config"],
        "resolvedExecutable": str(Path(selected).resolve(strict=True)) if selected else None,
        "executableSha256": digest_file(Path(selected)) if selected else None,
    })
    started = time.monotonic()
    result = {"status": "failed", "argv": argv, "cwd": str(cwd)}
    readers = []
    errors = []
    with (output / (stem + ".stdout.log")).open("xb") as stdout, (output / (stem + ".stderr.log")).open("xb") as stderr:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            result.update(error=str(error), elapsedSeconds=round(time.monotonic() - started, 3),
                          stdoutSha256=digest_file(output / (stem + ".stdout.log")),
                          stderrSha256=digest_file(output / (stem + ".stderr.log")))
            write_json(output / (stem + ".result.json"), result)
            raise Refusal(stem + " could not start; retained command/result are authoritative") from error

        def drain(pipe, destination, live):
            try:
                while block := pipe.read1(65536):
                    destination.write(block)
                    destination.flush()
                    try:
                        live.buffer.write(block)
                        live.flush()
                    except (AttributeError, BrokenPipeError, OSError):
                        pass
            except Exception as error:
                errors.append(type(error).__name__)
                # Popen retains this exact owned process handle; no PID search,
                # global process signal or retry of another compiler is used.
                process.kill()
            finally:
                pipe.close()

        for pipe, destination, live in ((process.stdout, stdout, sys.stdout), (process.stderr, stderr, sys.stderr)):
            thread = threading.Thread(target=drain, args=(pipe, destination, live), daemon=True)
            readers.append(thread)
            thread.start()
        code = process.wait()
        for thread in readers:
            thread.join()
        result.update(returncode=code, elapsedSeconds=round(time.monotonic() - started, 3),
                      status="passed" if code == 0 and not errors else "failed", captureErrors=errors)
    result.update(stdoutSha256=digest_file(output / (stem + ".stdout.log")),
                  stderrSha256=digest_file(output / (stem + ".stderr.log")))
    write_json(output / (stem + ".result.json"), result)
    if result["status"] != "passed":
        raise Refusal(stem + " failed; retained command/stdout/stderr are authoritative")
    return result


def prepare(source, output, target):
    source = ordinary_path(source, exists=True)
    output = ordinary_path(output, exists=False)
    if inside(output, source) or inside(source, output) or not output.parent.is_dir():
        raise Refusal("new output must have an existing parent and be separate from source")
    config = matrix(target)
    output.mkdir(mode=0o700)
    # Reserve evidence before source validation/copying so even an absent bridge
    # or preparation refusal leaves an owned diagnostic artifact, never a retry.
    write_json(output / "prepare.attempt.json", {"schema": SCHEMA, "source": str(source),
               "output": str(output), "config": config, "argv": command(config),
               "driverSha256": digest_file(Path(__file__)), "compiled": False})
    try:
        require_source(source)
        before = inventory(source)
        identity = git_identity(source)
        write_json(output / "source.input.json", {"schema": SCHEMA, "root": str(source),
                   "git": identity, "inventorySha256": binding(before), "entries": before})
        write_json(output / "check.plan.json", {"schema": SCHEMA, "kind": "entire-application-rust-typecheck",
                   "config": config, "argv": command(config), "cwd": str(output / "source"),
                   "cargoTargetDir": str(output / "target"), "sourceInventorySha256": binding(before),
                   "vcpkgCommit": VCPKG_COMMIT, "driverSha256": digest_file(Path(__file__)),
                   "excludedPolicy": {"everywhere": ["worktrees (case insensitive)"], "anchored": {key: sorted(value) for key, value in CACHES.items()}},
                   "limitations": "Typecheck only; full application builds/signing/runtime and all release gates remain mandatory."})
        def ignored(directory, names):
            relative = Path(directory).relative_to(source).as_posix()
            return [name for name in names if excluded(relative, name)]
        shutil.copytree(source, output / "source", symlinks=True, ignore=ignored)
        copied = inventory(output / "source")
        if before != copied or before != inventory(source):
            raise Refusal("source/copy changed during preparation")
        if git_identity(output / "source") != identity:
            raise Refusal("private ordinary source Git identity changed")
        write_json(output / "prepare.result.json", {"status": "prepared", "compiled": False,
                   "sourceUnchanged": True, "copyInventorySha256": binding(copied)})
    except Exception as error:
        write_json(output / "prepare.result.json", {"status": "failed", "compiled": False,
                   "error": str(error)})
        raise
    return source, output, config, before


def execute(source, output, config, before):
    result = {"status": "failed", "compiled": False}
    try:
        environment = run_environment(config, source, output, os.environ)
        private = output / "source"
        # These identities are real installed shipping tools, not a substituted
        # fixture. They run only after the explicit hosted-CI authorization gate.
        capture(["rustup", "run", config["toolchain"], "rustc", "-Vv"], private, environment, output, "rustc-identity")
        capture(["rustup", "run", config["toolchain"], "cargo", "-V"], private, environment, output, "cargo-identity")
        capture(command(config), private, environment, output, "cargo-check")
        result.update(status="passed", compiled=True)
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        try:
            after = inventory(source)
            unchanged = before == after
            result.update(sourceUnchanged=unchanged, sourceInventoryAfterSha256=binding(after))
            if not unchanged:
                result.update(status="failed", error="supplied application source changed during cargo check")
        except Exception as error:
            unchanged = False
            result.update(sourceUnchanged=False, status="failed", error=str(error))
        write_json(output / "check.result.json", result)
        if not unchanged:
            raise Refusal("supplied source immutability check failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="complete branded application with restored bridge")
    parser.add_argument("--output", type=Path, required=True, help="new private evidence/source/target directory")
    parser.add_argument("--target", required=True, choices=tuple(MATRIX))
    parser.add_argument("--run", action="store_true", help="run real cargo check, exclusively on matching hosted CI")
    args = parser.parse_args()
    try:
        source, output, config, before = prepare(args.source, args.output, args.target)
        if args.run:
            execute(source, output, config, before)
            print("PASS: real entire-application cargo check; supplied source unchanged", flush=True)
        else:
            print("PREPARED: private application source and exact shipping cargo command; compiler not run", flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        print("FAIL: " + str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
