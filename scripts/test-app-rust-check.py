#!/usr/bin/env python3
"""Source-only controls for the real application Rust typecheck driver.

These tests do not run Cargo, rustc, an application, native injection or a provider.
Tiny Git objects are written directly in Python; Git is used only to read identity.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import platform
import stat
import tempfile
import unittest
from unittest import mock
import zlib

SCRIPT = Path(__file__).with_name("check-app-rust.py")
spec = importlib.util.spec_from_file_location("app_rust_check", SCRIPT)
DRIVER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DRIVER)


def put(root, name, raw=b"real source input\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def git_object(directory, kind, raw):
    body = kind.encode("ascii") + b" " + str(len(raw)).encode("ascii") + b"\0" + raw
    identity = hashlib.sha1(body).hexdigest()
    put(directory, "objects/" + identity[:2] + "/" + identity[2:], zlib.compress(body))
    return identity


def git_metadata(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "refs/heads").mkdir(parents=True)
    tree = git_object(directory, "tree", b"")
    commit = git_object(directory, "commit", (
        "tree " + tree + "\nauthor Fixture <qa@example.invalid> 1 +0000\n"
        "committer Fixture <qa@example.invalid> 1 +0000\n\nsource-only fixture\n").encode("ascii"))
    put(directory, "HEAD", b"ref: refs/heads/main\n")
    put(directory, "refs/heads/main", (commit + "\n").encode("ascii"))
    put(directory, "config", b"[core]\n\trepositoryformatversion = 0\n\tbare = false\n")
    return {"head": commit, "tree": tree}


def source(root):
    root.mkdir()
    identity = git_metadata(root / ".git")
    for name in DRIVER.REQUIRED:
        put(root, name)
    put(root, "src/version.rs", b'pub const VERSION: &str = "fixture";\n')
    put(root, "libs/rdev/.git", b"gitdir: ../../.git/modules/libs/rdev\n")
    (root / ".git/modules/libs/rdev").mkdir(parents=True)
    put(root, ".git/modules/libs/rdev/config", b"[remote \"origin\"]\n\turl = https://github.com/rustdesk-org/rdev\n")
    # Legitimate nested source folders must survive anchored cache exclusions.
    put(root, "libs/example/build/source.rs")
    put(root, "libs/example/target/source.rs")
    put(root, "flutter/lib/kept.dart")
    return identity


def native_target():
    if platform.system() == "Darwin":
        return "aarch64-apple-darwin"
    if platform.system() == "Windows":
        return "x86_64-pc-windows-msvc"
    return "x86_64-unknown-linux-gnu"


class Controls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mixel-app-check-source-")
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.app = self.root / "app"
        self.identity = source(self.app)
        self.output = self.root / "proof"

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        return DRIVER.prepare(self.app, self.output, native_target())

    def test_exact_shipping_matrix_and_real_command(self):
        for target, (host, version, features, triplet, minimum) in DRIVER.MATRIX.items():
            config = DRIVER.matrix(target, host, "arm64" if host == "Darwin" else "AMD64")
            self.assertEqual(DRIVER.command(config), ["rustup", "run", version, "cargo", "check",
                "--locked", "--release", "--lib", "--features", features, "--target", target])
            self.assertEqual(config["triplet"], triplet)
            self.assertEqual(config["macosMinimum"], minimum)
            self.assertNotIn("--no-default-features", DRIVER.command(config))
        for target, host, machine in (("aarch64-unknown-linux-gnu", "Linux", "aarch64"),
                ("x86_64-unknown-linux-gnu", "Darwin", "x86_64"),
                ("x86_64-pc-windows-msvc", "Windows", "arm64"),
                ("x86_64-pc-windows-msvc", "Unknown", "AMD64")):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.matrix(target, host, machine)

    def test_prepare_copies_real_source_and_git_without_running_compiler(self):
        with mock.patch.object(DRIVER, "capture", side_effect=AssertionError("compiler forbidden")):
            _, _, config, before = self.prepare()
        self.assertEqual(DRIVER.git_identity(self.output / "source"), self.identity)
        self.assertEqual(before, DRIVER.inventory(self.output / "source"))
        plan = json.loads((self.output / "check.plan.json").read_text())
        self.assertEqual(plan["argv"], DRIVER.command(config))
        self.assertEqual(plan["sourceInventorySha256"], DRIVER.binding(before))
        self.assertEqual(plan["driverSha256"], DRIVER.digest_file(SCRIPT))
        self.assertFalse(json.loads((self.output / "prepare.result.json").read_text())["compiled"])
        self.assertFalse((self.output / "cargo-check.stdout.log").exists())
        self.assertEqual((self.output / "source/libs/rdev/.git").read_bytes(), (self.app / "libs/rdev/.git").read_bytes())
        self.assertEqual((self.output / "source/.git/modules/libs/rdev/config").read_bytes(),
                         (self.app / ".git/modules/libs/rdev/config").read_bytes())

    def test_build_version_write_is_isolated_no_hardlinks(self):
        _, _, _, before = self.prepare()
        copy = self.output / "source/src/version.rs"
        original = self.app / "src/version.rs"
        self.assertFalse(os.path.samefile(copy, original))
        copy.write_text('pub const VERSION: &str = "written by build.rs";\n')
        self.assertIn(b'"fixture"', original.read_bytes())
        self.assertEqual(before, DRIVER.inventory(self.app))
        self.assertEqual(json.loads((self.output / "check.plan.json").read_text())["cargoTargetDir"], str(self.output / "target"))

    def test_all_worktrees_pruned_before_lstat_and_only_anchored_caches(self):
        # An excluded name is tested as a link to an external directory. Its
        # contents are never traversed, read or modified by these controls.
        for parent in (self.app, self.app / ".git", self.app / "libs/example"):
            try:
                (parent / "worktrees").symlink_to(self.root, target_is_directory=True)
            except OSError:
                (parent / "worktrees").mkdir()
        for parent, names in DRIVER.CACHES.items():
            directory = self.app if parent == "." else self.app / parent
            directory.mkdir(parents=True, exist_ok=True)
            for name in names:
                (directory / name).mkdir(exist_ok=True)
        lstat = Path.lstat
        def guard(path, *args, **kwargs):
            if path.name.casefold() == "worktrees":
                raise AssertionError("excluded entry was inspected")
            return lstat(path, *args, **kwargs)
        with mock.patch.object(Path, "lstat", guard):
            entries = DRIVER.inventory(self.app)
        self.prepare()
        paths = {entry["path"] for entry in entries}
        self.assertIn("libs/example/build/source.rs", paths)
        self.assertIn("libs/example/target/source.rs", paths)
        self.assertFalse(any("worktrees" in name.split("/") for name in paths))
        self.assertFalse((self.output / "source/target").exists())
        self.assertTrue((self.output / "source/libs/example/target/source.rs").is_file())

    def test_output_refuses_existing_nested_alias_and_unsafe_ancestor(self):
        self.output.mkdir()
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        self.output.rmdir()
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.prepare(self.app, self.app / "new-output", native_target())
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.prepare(self.app, self.root / "worktrees" / "new", native_target())
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.prepare(self.app, self.root / "missing/new", native_target())
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.app, target_is_directory=True)
        except OSError:
            return
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.prepare(alias, self.output, native_target())
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.prepare(self.app, alias / "new", native_target())

    def test_source_git_worktree_external_pointer_and_alternates_refused(self):
        pointer = self.app / "libs/rdev/.git"
        pointer.write_text("gitdir: " + str(self.root) + "\n")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        __import__("shutil").rmtree(self.output)
        pointer.write_text("gitdir: ../../.git/worktrees/forbidden\n")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        __import__("shutil").rmtree(self.output)
        pointer.unlink()
        put(self.app, ".git/objects/info/alternates", b"/external/objects\n")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        __import__("shutil").rmtree(self.output)
        (self.app / ".git/objects/info/alternates").unlink()
        shutil = __import__("shutil")
        shutil.rmtree(self.app / ".git")
        put(self.app, ".git", b"gitdir: /external/git\n")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()

    def test_missing_bridge_and_empty_lock_refused(self):
        bridge = self.app / "src/bridge_generated.io.rs"
        bridge.unlink()
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        self.assertEqual(json.loads((self.output / "prepare.result.json").read_text())["status"], "failed")
        self.assertTrue((self.output / "prepare.attempt.json").is_file())
        __import__("shutil").rmtree(self.output)
        put(self.app, "src/bridge_generated.io.rs")
        (self.app / "Cargo.lock").write_bytes(b"")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()

    def test_internal_relative_links_preserved_external_and_excluded_links_refused(self):
        link = self.app / "src/internal-link.rs"
        try:
            link.symlink_to("lib.rs")
        except OSError:
            self.skipTest("symlink creation privilege unavailable")
        self.prepare()
        self.assertTrue((self.output / "source/src/internal-link.rs").is_symlink())
        self.assertEqual(os.readlink(self.output / "source/src/internal-link.rs"), "lib.rs")
        __import__("shutil").rmtree(self.output)
        for target in (str(self.app / "src/lib.rs"), "../../outside", "../target/cache"):
            link.unlink()
            link.symlink_to(target)
            with self.assertRaises((DRIVER.Refusal, FileNotFoundError)):
                self.prepare()
            __import__("shutil").rmtree(self.output)
        link.unlink()
        (self.app / "src/to-excluded").symlink_to("../worktrees")
        link.symlink_to("to-excluded/no-read")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()

    def test_link_parent_components_cannot_hide_an_escape_or_excluded_traversal(self):
        # A lexical normpath would wrongly erase portal/.. and accept this. The
        # kernel follows portal first, making .. leave the copied source.
        try:
            (self.app / "portal").symlink_to(".", target_is_directory=True)
            (self.app / "src/escape").symlink_to("../portal/../../outside")
        except OSError:
            self.skipTest("symlink creation privilege unavailable")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        __import__("shutil").rmtree(self.output)
        (self.app / "src/escape").unlink()
        (self.app / "src/escape").symlink_to("../worktrees/../src/lib.rs")
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()

    def test_missing_generated_version_allowed_and_manifest_modes_retained(self):
        (self.app / "src/version.rs").unlink()
        executable = put(self.app, "scripts/helper.sh", b"#!/bin/sh\n")
        executable.chmod(0o755)
        readonly = put(self.app, "res/read-only", b"immutable input\n")
        readonly.chmod(0o444)
        self.prepare()
        self.assertFalse((self.output / "source/src/version.rs").exists())
        self.assertEqual(stat.S_IMODE((self.output / "source/scripts/helper.sh").stat().st_mode), stat.S_IMODE(executable.stat().st_mode))
        self.assertEqual(stat.S_IMODE((self.output / "source/res/read-only").stat().st_mode), stat.S_IMODE(readonly.stat().st_mode))
        # Windows deletion respects read-only attributes; restore only our files.
        readonly.chmod(0o644)
        (self.output / "source/res/read-only").chmod(0o644)

    def test_manifest_detects_generated_source_changes_beyond_git_tree(self):
        before = DRIVER.inventory(self.app)
        (self.app / "src/lib.rs").write_bytes(b"changed generated product source\n")
        self.assertNotEqual(DRIVER.binding(before), DRIVER.binding(DRIVER.inventory(self.app)))
        self.assertEqual(DRIVER.git_identity(self.app), self.identity)

    def test_preparation_change_and_copy_failure_leave_evidence(self):
        original = DRIVER.shutil.copytree
        def changed(*args, **kwargs):
            original(*args, **kwargs)
            (self.app / "src/lib.rs").write_bytes(b"source changed during copy\n")
        with mock.patch.object(DRIVER.shutil, "copytree", changed):
            with self.assertRaises(DRIVER.Refusal):
                self.prepare()
        result = json.loads((self.output / "prepare.result.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertTrue((self.output / "source.input.json").is_file())
        self.assertTrue((self.output / "check.plan.json").is_file())

    def test_run_refuses_local_desktop_before_compiler_and_retains_failure(self):
        source_path, output, config, before = self.prepare()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(DRIVER, "capture", side_effect=AssertionError("compiler forbidden")):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.execute(source_path, output, config, before)
        result = json.loads((self.output / "check.result.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["sourceUnchanged"])
        self.assertFalse(result["compiled"])
        self.assertFalse((self.output / "cargo-check.command.json").exists())

    def test_run_refuses_ci_host_target_wrapper_and_unpinned_dependency(self):
        config = DRIVER.matrix("x86_64-unknown-linux-gnu", "Linux", "x86_64")
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux"}
        for key, value in (("RUNNER_ENVIRONMENT", "self-hosted"), ("RUNNER_OS", "macOS"),
                ("CARGO_BUILD_TARGET", "aarch64-apple-darwin"), ("RUSTC_WRAPPER", "unexpected")):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.run_environment(config, self.app, self.output, {**env, key: value})
        vcpkg = self.root / "vcpkg"
        git_metadata(vcpkg / ".git")
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.run_environment(config, self.app, self.output, {**env, "VCPKG_ROOT": str(vcpkg)})

    def test_env_preserves_shipping_dependencies_isolates_target_and_withholds_secrets(self):
        config = DRIVER.matrix("x86_64-unknown-linux-gnu", "Linux", "x86_64")
        vcpkg = self.root / "vcpkg"
        (vcpkg / "installed/x64-linux/lib").mkdir(parents=True)
        self.output.mkdir()
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
            "RUNNER_TEMP": str(self.root), "VCPKG_ROOT": str(vcpkg),
            "CARGO_TARGET_DIR": str(self.app / "target"), "APPLE_CERT_P12_PASSWORD": "not-in-evidence",
            "VCPKG_BINARY_SOURCES": "clear;files,shipping-cache,readwrite", "PATH": "shipping-native-tools"}
        with mock.patch.object(DRIVER, "git_identity", return_value={"head": DRIVER.VCPKG_COMMIT, "tree": "0" * 40}):
            result = DRIVER.run_environment(config, self.app, self.output, env)
        self.assertEqual(result["CARGO_TARGET_DIR"], str(self.output / "target"))
        self.assertEqual(result["VCPKG_BINARY_SOURCES"], env["VCPKG_BINARY_SOURCES"])
        self.assertEqual(result["PATH"], env["PATH"])
        self.assertEqual(result["APPLE_CERT_P12_PASSWORD"], env["APPLE_CERT_P12_PASSWORD"])
        evidence = (self.output / "check.environment.json").read_text()
        self.assertNotIn("not-in-evidence", evidence)
        self.assertNotIn("APPLE_CERT", evidence)
        self.assertEqual(json.loads(evidence)["vcpkgCommit"], DRIVER.VCPKG_COMMIT)

    def test_execute_failure_always_rechecks_original_source_and_no_retry(self):
        source_path, output, config, before = self.prepare()
        with mock.patch.object(DRIVER, "run_environment", return_value={}), mock.patch.object(DRIVER, "capture", side_effect=DRIVER.Refusal("real compiler rejected source")) as capture:
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.execute(source_path, output, config, before)
        self.assertEqual(capture.call_count, 1)
        result = json.loads((self.output / "check.result.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["sourceUnchanged"])
        self.assertEqual(result["sourceInventoryAfterSha256"], DRIVER.binding(before))

    def test_execute_source_change_cannot_report_pass(self):
        source_path, output, config, before = self.prepare()
        def fake_capture(*args):
            (self.app / "src/version.rs").write_bytes(b"unexpected source change\n")
        with mock.patch.object(DRIVER, "run_environment", return_value={}), mock.patch.object(DRIVER, "capture", side_effect=fake_capture):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.execute(source_path, output, config, before)
        result = json.loads((self.output / "check.result.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["sourceUnchanged"])

    def test_macos_owned_registry_overlay_and_deployment_are_required(self):
        if os.name != "posix":
            self.skipTest("POSIX owned Cargo home control")
        config = DRIVER.matrix("aarch64-apple-darwin", "Darwin", "arm64")
        vcpkg = self.root / "vcpkg"
        (vcpkg / "installed/arm64-osx/lib").mkdir(parents=True)
        overlay = self.root / "overlay"
        overlay.mkdir()
        put(overlay, "arm64-osx.cmake")
        home = self.root / "owned-cargo"
        home.mkdir(mode=0o700)
        put(home, ".mixel-owned-cargo-home.json", b"wrong marker name is insufficient\n")
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "macOS",
            "RUNNER_TEMP": str(self.root), "VCPKG_ROOT": str(vcpkg),
            "MACOSX_DEPLOYMENT_TARGET": "12.3", "VCPKG_OVERLAY_TRIPLETS": str(overlay)}
        with mock.patch.object(DRIVER, "git_identity", return_value={"head": DRIVER.VCPKG_COMMIT, "tree": "0" * 40}):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.run_environment(config, self.app, self.output, env)
            with self.assertRaises((OSError, RuntimeError)):
                DRIVER.run_environment(config, self.app, self.output, {**env, "CARGO_HOME": str(home)})
            for key, value in (("MACOSX_DEPLOYMENT_TARGET", "10.14"), ("VCPKG_OVERLAY_TRIPLETS", "")):
                with self.assertRaises(DRIVER.Refusal):
                    DRIVER.run_environment(config, self.app, self.output, {**env, key: value})

    def test_windows_uses_existing_strict_native_linker_selection(self):
        import rust_toolchain
        config = DRIVER.matrix("x86_64-pc-windows-msvc", "Windows", "AMD64")
        vcpkg = self.root / "vcpkg"
        (vcpkg / "installed/x64-windows-static/lib").mkdir(parents=True)
        self.output.mkdir()
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Windows",
            "RUNNER_TEMP": str(self.root), "VCPKG_ROOT": str(vcpkg)}
        linker = self.root / "explicit-native-msvc-link.exe"
        with mock.patch.object(DRIVER, "git_identity", return_value={"head": DRIVER.VCPKG_COMMIT, "tree": "0" * 40}), mock.patch.object(rust_toolchain, "find_msvc_linker", return_value=linker) as find:
            result = DRIVER.run_environment(config, self.app, self.output, env)
        find.assert_called_once()
        self.assertEqual(result["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"], str(linker))

    def test_cli_prepare_default_never_launches_the_compiler(self):
        args = [str(SCRIPT), "--source", str(self.app), "--output", str(self.output), "--target", native_target()]
        stdout = io.StringIO()
        with mock.patch("sys.argv", args), mock.patch.object(DRIVER, "capture", side_effect=AssertionError("compiler forbidden")), contextlib.redirect_stdout(stdout):
            self.assertEqual(DRIVER.main(), 0)
        self.assertIn("PREPARED:", stdout.getvalue())
        self.assertIn("compiler not run", stdout.getvalue())

    def test_capture_stream_evidence_is_bound_and_nonzero_remains_failure(self):
        self.prepare()
        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.stdout = io.BytesIO(b"source-control process plumbing only\n")
                self.stderr = io.BytesIO(b"source-control expected failure\n")
            def wait(self):
                return 101
        with mock.patch.object(DRIVER.subprocess, "Popen", FakeProcess), mock.patch.object(DRIVER.shutil, "which", return_value=None):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.capture(["not-a-compiler"], self.output / "source", {}, self.output, "source-control")
        result = json.loads((self.output / "source-control.result.json").read_text())
        self.assertEqual(result["returncode"], 101)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stdoutSha256"], DRIVER.digest_file(self.output / "source-control.stdout.log"))
        command = json.loads((self.output / "source-control.command.json").read_text())
        self.assertEqual(command["sourceInventorySha256"], json.loads((self.output / "source.input.json").read_text())["inventorySha256"])
        self.assertEqual(command["driverSha256"], DRIVER.digest_file(SCRIPT))

    def test_capture_start_failure_has_saved_diagnostics(self):
        self.prepare()
        with mock.patch.object(DRIVER.subprocess, "Popen", side_effect=FileNotFoundError("source-control absent tool")), mock.patch.object(DRIVER.shutil, "which", return_value=None):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.capture(["not-a-compiler"], self.output / "source", {}, self.output, "start-control")
        self.assertTrue((self.output / "start-control.command.json").is_file())
        self.assertTrue((self.output / "start-control.stdout.log").is_file())
        self.assertTrue((self.output / "start-control.stderr.log").is_file())
        self.assertEqual(json.loads((self.output / "start-control.result.json").read_text())["status"], "failed")

    def test_pins_match_existing_shipping_workflow(self):
        workflow = SCRIPT.parents[1].joinpath(".github/workflows/build.yml").read_text()
        self.assertIn('RUST_VERSION: "1.75"', workflow)
        self.assertIn('MAC_RUST_VERSION: "1.81"', workflow)
        self.assertIn('VCPKG_COMMIT_ID: "' + DRIVER.VCPKG_COMMIT + '"', workflow)
        for target in DRIVER.MATRIX:
            self.assertIn(target, workflow)
        self.assertIn('python3 build.py --flutter --unix-file-copy-paste', workflow)
        self.assertIn('python build.py --flutter --skip-portable-pack', workflow)
        self.assertIn('python3 build.py --flutter', workflow)

    def test_driver_ast_default_prepare_no_shell_or_retry_or_feature_reduction(self):
        text = SCRIPT.read_text()
        ast.parse(text)
        self.assertNotIn("shell=True", text)
        self.assertNotIn("--no-default-features", text)
        self.assertNotIn("build.py", text)
        self.assertNotIn("cargo clean", text)
        self.assertNotIn("--target-cache", text)
        self.assertIn('if args.run:', text)
        self.assertIn('sourceInventoryAfterSha256', text)
        self.assertIn('source.input.json', text)
        self.assertIn('stdout.log', text)
        self.assertIn('stderr.log', text)
        self.assertIn('run_environment(config, source, output, os.environ, local_run_root=local_run_root)', text)


@unittest.skipUnless(os.name == "posix", "local Mac admission uses POSIX ownership")
class LocalAdmissionControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mixel-app-local-admission-")
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.repo = self.root / "repository"
        git_metadata(self.repo / ".git")
        put(self.repo, ".gitignore", b".cache/\n")
        self.script = put(self.repo, "scripts/check-app-rust.py", SCRIPT.read_bytes())
        put(self.repo, "scripts/patch-support-url-log-privacy.py",
            SCRIPT.with_name("patch-support-url-log-privacy.py").read_bytes())
        self.run_root = self.repo / ".cache/qualification"
        self.run_root.mkdir(parents=True, mode=0o700)
        self.app = self.repo / "rustdesk"
        source(self.app)
        self.output = self.run_root / "fresh-check"
        self.target = "aarch64-apple-darwin"
        self.config = DRIVER.matrix(self.target, "Darwin", "arm64")
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(DRIVER, "__file__", str(self.script)))
        self.stack.enter_context(mock.patch.object(DRIVER.Path, "cwd", return_value=self.repo))
        # These are inert admission controls on every source host, not Mac proof.
        self.stack.enter_context(mock.patch.object(DRIVER.platform, "system", return_value="Darwin"))
        self.stack.enter_context(mock.patch.object(DRIVER.platform, "machine", return_value="arm64"))
        self.capture = self.stack.enter_context(mock.patch.object(DRIVER, "capture", side_effect=AssertionError("compiler forbidden")))

    def tearDown(self):
        self.stack.close()
        self.temporary.cleanup()

    def prepare(self):
        return DRIVER.prepare(self.app, self.output, self.target, local_run_root=self.run_root)

    def environment(self):
        vcpkg = self.repo / ".cache/vcpkg"
        (vcpkg / "installed/arm64-osx/lib").mkdir(parents=True)
        overlay = self.repo / ".github/vcpkg-triplets"
        put(overlay, "arm64-osx.cmake")
        home = self.repo / ".cache/owned-cargo"
        home.mkdir(mode=0o700)
        return {"VCPKG_ROOT": str(vcpkg), "MACOSX_DEPLOYMENT_TARGET": "12.3",
                "VCPKG_OVERLAY_TRIPLETS": str(overlay), "CARGO_HOME": str(home)}

    def test_local_root_is_explicit_and_private_output_is_fresh(self):
        self.prepare()
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertTrue((self.output / "source/src/bridge_generated.rs").is_file())
        with self.assertRaises(DRIVER.Refusal):
            self.prepare()
        self.capture.assert_not_called()

    def test_scratch_is_allowed_without_git_ignore_and_nonignored_root_refuses(self):
        self.run_root = self.repo / "_scratch/native"
        self.run_root.mkdir(parents=True, mode=0o700)
        self.output = self.run_root / "fresh-check"
        self.prepare()
        other = self.repo / "untracked-build"
        other.mkdir(mode=0o700)
        with self.assertRaisesRegex(DRIVER.Refusal, "Git-ignored"):
            DRIVER.prepare(self.app, other / "proof", self.target, local_run_root=other)
        self.assertFalse((other / "proof").exists())
        self.capture.assert_not_called()

    def test_local_wrong_cwd_outside_root_and_relative_root_refuse_before_copy_or_command(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        cases = ((outside, outside / "proof"), (self.run_root, outside / "proof"),
                 (Path(".cache/qualification"), self.output))
        with mock.patch.object(DRIVER.shutil, "copytree", side_effect=AssertionError("copy forbidden")):
            for root, output in cases:
                with self.subTest(root=root, output=output), self.assertRaises(DRIVER.Refusal):
                    DRIVER.prepare(self.app, output, self.target, local_run_root=root)
            with mock.patch.object(DRIVER.Path, "cwd", return_value=self.app):
                with self.assertRaisesRegex(DRIVER.Refusal, "repository root"):
                    self.prepare()
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_local_root_symlink_and_permissions_refuse_before_command(self):
        alias = self.repo / ".cache/alias"
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.prepare(self.app, alias / "proof", self.target, local_run_root=alias)
        self.run_root.chmod(0o777)
        with self.assertRaisesRegex(DRIVER.Refusal, "group/other write"):
            self.prepare()
        self.run_root.chmod(0o700)
        with mock.patch.object(DRIVER.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(DRIVER.Refusal, "current user"):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_local_driver_alias_cannot_change_the_repository_binding(self):
        original = self.root / "external-driver.py"
        self.script.rename(original)
        self.script.symlink_to(original)
        with self.assertRaisesRegex(DRIVER.Refusal, "ordinary current-user-owned driver"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_local_source_symlink_hardlink_and_reparse_refuse(self):
        alias = self.app / "src/alias.rs"
        alias.symlink_to("lib.rs")
        with self.assertRaisesRegex(DRIVER.Refusal, "indirect entry"):
            self.prepare()
        alias.unlink()
        os.link(self.app / "src/lib.rs", alias)
        with self.assertRaisesRegex(DRIVER.Refusal, "hardlinked"):
            self.prepare()
        alias.unlink()
        original = Path.lstat
        def reparse(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.app / "src/lib.rs":
                return mock.Mock(st_mode=value.st_mode, st_file_attributes=0x400)
            return value
        with mock.patch.object(Path, "lstat", reparse):
            with self.assertRaisesRegex(DRIVER.Refusal, "reparse point"):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_local_mode_never_admits_windows_or_a_foreign_host(self):
        for config, host in ((DRIVER.matrix("x86_64-pc-windows-msvc", "Windows", "AMD64"), "Darwin"),
                             (self.config, "Linux")):
            with mock.patch.object(DRIVER.platform, "system", return_value=host):
                with self.assertRaisesRegex(DRIVER.Refusal, "native macOS"):
                    DRIVER.local_admission(self.run_root, self.app, self.output, config, prepared=False)
        self.capture.assert_not_called()

    def test_local_environment_needs_no_runner_identity_and_preserves_exact_command(self):
        self.prepare()
        env = self.environment()
        with mock.patch.object(DRIVER, "git_identity", return_value={"head": DRIVER.VCPKG_COMMIT}), \
                mock.patch.object(DRIVER, "verify_cargo_home") as verify:
            result = DRIVER.run_environment(self.config, self.app, self.output, env, local_run_root=self.run_root)
        verify.assert_called_once_with(Path(env["CARGO_HOME"]), patched=True)
        for key in ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_TEMP"):
            self.assertNotIn(key, result)
        self.assertEqual(DRIVER.command(self.config), ["rustup", "run", "1.81", "cargo", "check", "--locked",
                         "--release", "--lib", "--features", "flutter,unix-file-copy-paste", "--target", self.target])
        evidence = json.loads((self.output / "check.environment.json").read_text())
        self.assertEqual(evidence["admission"], "local-macos")
        self.assertEqual(evidence["localRunRoot"], str(self.run_root))
        self.capture.assert_not_called()

    def test_local_dependencies_wrappers_and_owned_cargo_are_not_bypassed(self):
        self.prepare()
        env = self.environment()
        failures = (("CARGO_HOME", ""), ("CARGO_HOME", str(self.output)),
                    ("CARGO_HOME", str(self.repo)), ("RUSTC_WRAPPER", "unexpected"),
                    ("CARGO_BUILD_TARGET", "x86_64-apple-darwin"),
                    ("MACOSX_DEPLOYMENT_TARGET", "10.14"), ("VCPKG_OVERLAY_TRIPLETS", ""))
        with mock.patch.object(DRIVER, "git_identity", return_value={"head": DRIVER.VCPKG_COMMIT}):
            for key, value in failures:
                with self.subTest(key=key, value=value), self.assertRaises((OSError, RuntimeError)):
                    DRIVER.run_environment(self.config, self.app, self.output, {**env, key: value}, local_run_root=self.run_root)
            # A merely existing directory is not a reserved/patched Cargo home.
            with self.assertRaises((OSError, RuntimeError)):
                DRIVER.run_environment(self.config, self.app, self.output, env, local_run_root=self.run_root)
        self.assertFalse((self.output / "check.environment.json").exists())
        self.capture.assert_not_called()

    def test_local_cargo_verifier_requires_actual_pinned_patch_after_ownership_check(self):
        home = self.repo / ".cache/owned-cargo"
        home.mkdir(mode=0o700)
        checkout = home / "fixture-tao"
        leaf = put(checkout, "logger.rs", b"original")
        privacy = mock.Mock(SOURCES={"tao": {"path": "logger.rs"}})
        privacy.tao_checkout.return_value = checkout
        privacy.safe_file.return_value = (leaf, leaf.stat())
        privacy.transform.return_value = b"patched"
        with mock.patch.object(DRIVER, "privacy_module", return_value=privacy):
            with self.assertRaisesRegex(DRIVER.Refusal, "already contain"):
                DRIVER.verify_cargo_home(home, patched=True)
            leaf.write_bytes(b"patched")
            DRIVER.verify_cargo_home(home, patched=True)
        self.assertEqual(privacy.validate_cargo_home.call_count, 2)
        privacy.tao_checkout.assert_called_with(home)
        privacy.safe_file.assert_called_with(checkout, "logger.rs")
        self.capture.assert_not_called()

    def test_local_revalidation_refuses_changed_root_before_compiler_and_records_failure(self):
        source_path, output, config, before = self.prepare()
        self.run_root.chmod(0o777)
        with self.assertRaises(DRIVER.Refusal):
            DRIVER.execute(source_path, output, config, before, local_run_root=self.run_root)
        result = json.loads((output / "check.result.json").read_text())
        self.assertFalse(result["compiled"])
        self.assertTrue(result["sourceUnchanged"])
        self.run_root.chmod(0o700)
        self.capture.assert_not_called()

    def test_cli_invalid_local_root_cannot_prepare_or_invoke_compiler(self):
        args = [str(self.script), "--source", str(self.app), "--output", str(self.output),
                "--target", self.target, "--run", "--local-run-root", str(self.root)]
        with mock.patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(DRIVER.main(), 1)
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_cli_local_root_cannot_change_prepare_only_semantics(self):
        args = [str(self.script), "--source", str(self.app), "--output", str(self.output),
                "--target", self.target, "--local-run-root", str(self.run_root)]
        with mock.patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as refusal:
                DRIVER.main()
        self.assertEqual(refusal.exception.code, 2)
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_mocked_local_cli_reaches_all_actual_compile_gates_without_hosted_variables(self):
        env = self.environment()
        env["PATH"] = os.environ.get("PATH", os.defpath)
        identity = DRIVER.git_identity
        def dependency_identity(path):
            if path == Path(env["VCPKG_ROOT"]):
                return {"head": DRIVER.VCPKG_COMMIT}
            return identity(path)
        args = [str(self.script), "--source", str(self.app), "--output", str(self.output),
                "--target", self.target, "--run", "--local-run-root", str(self.run_root)]
        self.capture.side_effect = None
        with mock.patch("sys.argv", args), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(DRIVER, "git_identity", side_effect=dependency_identity), \
                mock.patch.object(DRIVER, "verify_cargo_home") as verify, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(DRIVER.main(), 0)
        self.assertEqual(self.capture.call_count, 3)
        self.assertEqual([call.args[-1] for call in self.capture.call_args_list],
                         ["rustc-identity", "cargo-identity", "cargo-check"])
        self.assertEqual(self.capture.call_args_list[-1].args[0], DRIVER.command(self.config))
        verify.assert_called_once_with(Path(env["CARGO_HOME"]), patched=True)
        for call in self.capture.call_args_list:
            self.assertNotIn("GITHUB_ACTIONS", call.args[2])
            self.assertNotIn("RUNNER_TEMP", call.args[2])


class ShippingWorkflowControls(unittest.TestCase):
    def test_each_real_shipping_job_requires_early_app_check_and_retains_evidence(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / '.github/workflows/build.yml').read_text()
        for job, following, target, label in (
                ('linux', 'macos', 'x86_64-unknown-linux-gnu', 'linux'),
                ('macos', 'windows', '${{ matrix.job.target }}', '${{ matrix.job.target }}'),
                ('windows', 'upload', 'x86_64-pc-windows-msvc', 'windows')):
            section = text.split('  '+job+':\n', 1)[1].split('  '+following+':\n', 1)[0]
            early = section.split('      - name: Typecheck the complete application with shipping Rust and native dependencies\n',1)[1].split('      - ',1)[0]
            self.assertIn('--target "'+target+'" --run', early)
            self.assertIn('--output "$RUNNER_TEMP/mixel-app-rust-'+label+'"', early)
            self.assertIn('timeout-minutes: 40', early)
            for bypass in ('if:', 'continue-on-error', '||', 'source-only'):
                self.assertNotIn(bypass, early)
            self.assertLess(section.index('Typecheck the complete application'), section.index('Preflight generated support source and reporter'))
            self.assertLess(section.index('Preflight generated support source and reporter'), section.index('Build (cargo + flutter via build.py)'))
            evidence = section.split('      - name: Preserve early complete application compiler evidence\n',1)[1].split('      - ',1)[0]
            self.assertIn('if: always()', evidence)
            self.assertIn('mixel-app-rust-'+label+'/*.json', evidence)
            self.assertIn('mixel-app-rust-'+label+'/*.log', evidence)
            self.assertNotIn('/source', evidence); self.assertNotIn('/target', evidence)
        for workflow in ('build.yml', 'preflight.yml'):
            self.assertEqual((root / '.github/workflows' / workflow).read_text().count('          python3 scripts/test-app-rust-check.py\n'), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
