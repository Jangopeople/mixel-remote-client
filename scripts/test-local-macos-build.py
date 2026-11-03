#!/usr/bin/env python3
"""Inert local build-driver controls: every process is mocked; no native tools run."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/local/build-macos.py"
SPEC = importlib.util.spec_from_file_location("local_macos_build", SCRIPT)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
CHECK = DRIVER.load_check(ROOT)


def put(root, name, raw=b"inert fixture\n", mode=0o644):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(mode)
    return path


@unittest.skipUnless(os.name == "posix", "Mac ownership controls use POSIX metadata")
class LocalMacBuildControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mixel-local-build-controls-")
        self.root = Path(self.temporary.name).resolve(strict=True)
        self.repo = self.root / "repository"
        self.run_root = self.repo / ".cache/builds"
        self.run_root.mkdir(parents=True, mode=0o700)
        self.output = self.run_root / "fresh-arm-build"
        self.source = self.repo / "rustdesk"
        for name in CHECK.REQUIRED:
            put(self.source, name)
        put(self.source, ".git/HEAD", (DRIVER.UPSTREAM_HEAD + "\n").encode())
        put(self.source, "libs/hbb_common/.git/HEAD", (DRIVER.HBB_HEAD + "\n").encode())
        put(self.source, "flutter/pubspec.yaml", b"dependencies:\n  extended_text: 14.0.0\n")
        (self.source / "flutter/ios/Runner").mkdir(parents=True)
        self.vcpkg = self.run_root / "vcpkg-source"
        put(self.vcpkg, ".git/HEAD", (DRIVER.VCPKG_HEAD + "\n").encode())
        put(self.vcpkg, "bootstrap-vcpkg.sh")
        self.flutter = self.run_root / "flutter-sdk"
        put(self.flutter, "bin/flutter", mode=0o755)
        put(self.flutter, "bin/cache/dart-sdk/bin/dart", mode=0o755)
        put(self.flutter, "bin/cache/artifacts/engine/darwin-x64-release/FlutterMacOS.xcframework/macos-arm64_x86_64/FlutterMacOS.framework/Headers/FlutterEngine.h")
        put(self.flutter, "bin/cache/artifacts/engine/darwin-x64-release/FlutterMacOS.xcframework/macos-arm64_x86_64/FlutterMacOS.framework/FlutterMacOS")
        self.rust_bin = self.root / "toolchain/bin"
        put(self.rust_bin, "rustc", mode=0o755)
        put(self.rust_bin, "cargo", mode=0o755)
        self.sdk = self.root / "Xcode/MacOSX.sdk"
        self.sdk.mkdir(parents=True)
        self.sdk_reported = self.sdk
        self.clang = put(self.root, "Xcode/Toolchains/usr/bin/clang", mode=0o755)
        put(self.clang.parent.parent, "lib/libclang.dylib")
        put(self.repo, ".github/workflows/build.yml", (ROOT / ".github/workflows/build.yml").read_bytes())
        put(self.repo, "scripts/local/build-macos.py", SCRIPT.read_bytes())
        for name in ("apply-branding.sh", "check-app-rust.py", "patch-support-url-log-privacy.py",
                     "verify-macos-native-minimum.py", "verify-macos-bundle-minimum.py",
                     "verify-flutter-branding.py", "test-compiled-support-guard.py"):
            put(self.repo, "scripts/" + name)
        put(self.repo, ".github/vcpkg-triplets/arm64-osx.cmake")
        self.args = argparse.Namespace(source=self.source, flutter=self.flutter, vcpkg=self.vcpkg,
                                       run_root=self.run_root, output=self.output, run=True, jobs=1, reuse_from=None)
        self.processes = []
        self.labels = []
        self.fail_label = None
        self.dirty = False
        self.sdk_version = DRIVER.FLUTTER_VERSION
        self.omit_bridge = False
        self.omit_proof = False
        self.corrupt_bundle_proof = False
        self.mutate_original = False
        self.mutate_lock_at = None
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(DRIVER.platform, "system", return_value="Darwin"))
        self.stack.enter_context(mock.patch.object(DRIVER.platform, "machine", return_value="arm64"))
        self.stack.enter_context(mock.patch.object(DRIVER.Path, "cwd", return_value=self.repo))
        self.stack.enter_context(mock.patch.object(DRIVER, "load_check", return_value=CHECK))
        self.stack.enter_context(mock.patch.object(DRIVER.shutil, "which", side_effect=lambda name, **kw: "/inert-tools/" + name))
        self.stack.enter_context(mock.patch.object(DRIVER.subprocess, "run", side_effect=self.fake_process))
        self.stack.enter_context(mock.patch.object(CHECK, "verify_cargo_home"))
        self.stack.enter_context(mock.patch.dict(os.environ, {"PATH": "/inert-tools", "HOME": str(self.root)}, clear=True))
        original_call = DRIVER.Runner.call
        def recording(runner, label, *args, **kwargs):
            self.labels.append(label)
            return original_call(runner, label, *args, **kwargs)
        self.stack.enter_context(mock.patch.object(DRIVER.Runner, "call", recording))

    def tearDown(self):
        self.stack.close()
        self.temporary.cleanup()

    def fake_process(self, argv, **kwargs):
        argv = [str(value) for value in argv]
        self.processes.append((argv, kwargs))
        output = b"inert process output; not a native build\n"
        current_label = self.labels[-1] if self.labels else "root-admission"
        if self.fail_label == current_label:
            code = 23
        else:
            code = 0
        if argv[0] == "git":
            if "check-ignore" in argv:
                output = b""
            elif "ls-files" in argv:
                output = b".github/workflows/build.yml\0scripts/apply-branding.sh\0scripts/check-app-rust.py\0.github/vcpkg-triplets/arm64-osx.cmake\0"
            elif "rev-parse" in argv:
                directory = Path(argv[2])
                head = (DRIVER.HBB_HEAD if directory.name == "hbb_common" else DRIVER.VCPKG_HEAD if directory == self.vcpkg
                        else DRIVER.FLUTTER_HEAD if directory == self.flutter else DRIVER.UPSTREAM_HEAD)
                output = (head + "\n").encode()
            elif "status" in argv and "submodule" in argv:
                output = (" " + DRIVER.HBB_HEAD + " libs/hbb_common (inert fixture)\n").encode()
            elif "status" in argv:
                output = b" M src/lib.rs\n" if self.dirty else b""
        elif argv[:2] == ["rustup", "which"]:
            output = (str(self.rust_bin / argv[-1]) + "\n").encode()
        elif argv[-2:] == ["rustc", "-Vv"]:
            output = b"rustc 1.81.0 (inert)\nhost: aarch64-apple-darwin\n"
        elif argv[-2:] == ["cargo", "-V"]:
            output = b"cargo 1.81.0 (inert)\n"
        elif argv[:3] == ["rustup", "target", "list"]:
            output = b"aarch64-apple-darwin\nx86_64-apple-darwin\n"
        elif argv[-2:] == ["--version", "--machine"]:
            output = json.dumps({"frameworkVersion": self.sdk_version, "frameworkRevision": DRIVER.FLUTTER_HEAD}).encode()
        elif argv[0] == "xcrun":
            output = (str(self.clang if argv[1] == "--find" else self.sdk_reported) + "\n").encode()
        elif Path(argv[0]).name == "flutter_rust_bridge_codegen" and argv[-1:] == ["--version"]:
            output = b"flutter_rust_bridge_codegen 1.80.1\n"
        if code == 0:
            if "--reserve-cargo-home" in argv:
                Path(argv[-1]).mkdir(mode=0o700)
            elif "--root" in argv and "install" in argv and "flutter_rust_bridge_codegen" in argv:
                put(Path(argv[argv.index("--root") + 1]), "bin/flutter_rust_bridge_codegen", mode=0o755)
            elif "verify-macos-native-minimum.py" in [Path(item).name for item in argv] and not self.omit_proof:
                put(self.output, "native-codecs.json", json.dumps({"status": "passed", "architecture": "arm64", "advertised_minimum": "12.3"}).encode())
            elif Path(argv[0]).name == "flutter_rust_bridge_codegen" and "--version" not in argv:
                for name in DRIVER.BRIDGE_FILES:
                    if name == "flutter/ios/Runner/bridge_generated.h":
                        continue
                    if self.omit_bridge and name.endswith(".freezed.dart"):
                        continue
                    put(self.output / "rustdesk", name, b"inert generated bridge\n")
            elif "check-app-rust.py" in [Path(item).name for item in argv]:
                put(self.output, "rust-check/check.result.json", b'{"status":"passed","compiled":true,"sourceUnchanged":true}')
            elif argv[1:2] == ["build.py"]:
                put(self.output / "rustdesk", "flutter/build/macos/Build/Products/Release/Mixel-Remote.app/Contents/Info.plist")
                put(self.output / "rustdesk", "flutter/build/macos/Build/Products/Release/Mixel-Remote.app/Contents/MacOS/Mixel-Remote", mode=0o755)
            elif "verify-macos-bundle-minimum.py" in [Path(item).name for item in argv] and not self.omit_proof:
                put(self.output, "bundle-minimum.json", json.dumps({"status": "passed", "architecture": "x86_64" if self.corrupt_bundle_proof else "arm64", "advertised_minimum": "12.3",
                    "bundle_identifier": "ch.mixel.remote", "bundle_executable": "Mixel-Remote"}).encode())
            if self.mutate_original and current_label == "compiled-support-guard":
                (self.source / "src/lib.rs").write_bytes(b"unexpected original-source change\n")
            if current_label == "install-codecs":
                put(self.output, "codec-cache/inert-codec.zip", b"inert owned codec cache\n")
            if self.mutate_lock_at == current_label:
                (self.output / "rustdesk/Cargo.lock").write_bytes(b"unexpected unlocked resolution\n")
        if kwargs.get("stdout") == subprocess.PIPE:
            return subprocess.CompletedProcess(argv, code, output, b"")
        kwargs["stdout"].write(output)
        kwargs["stderr"].write(b"inert expected failure\n" if code else b"")
        return subprocess.CompletedProcess(argv, code)

    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return DRIVER.build(self.args, self.repo)

    def result(self):
        return json.loads((self.output / "build.result.json").read_text())

    def previous_failure(self):
        self.fail_label = "check-entire-app"
        with self.assertRaises(DRIVER.Refusal):
            self.build()
        old = self.output
        self.output = self.run_root / "fresh-arm-r2"
        self.args.output = self.output
        self.args.reuse_from = old
        self.fail_label = None
        self.labels.clear()
        self.processes.clear()
        return old

    def test_mocked_end_to_end_preserves_supplied_source_and_binds_app_hash(self):
        before = CHECK.inventory(self.source)
        vcpkg_before = CHECK.inventory(self.vcpkg)
        result = self.build()
        self.assertTrue(result["buildQualified"])
        self.assertTrue(result["suppliedSourceUnchanged"])
        self.assertTrue(result["suppliedVcpkgUnchanged"])
        self.assertTrue(result["pipelineInputsUnchanged"])
        self.assertEqual(CHECK.inventory(self.source), before)
        self.assertEqual(CHECK.inventory(self.vcpkg), vcpkg_before)
        manifest = json.loads((self.output / "app-manifest.json").read_text())
        self.assertEqual(result["appTreeSha256"], manifest["treeSha256"])
        self.assertEqual(result["sourceHead"], DRIVER.UPSTREAM_HEAD)
        self.assertEqual(set(json.loads((self.output / "bridge-files.json").read_text())), set(DRIVER.BRIDGE_FILES))
        self.assertEqual((self.output / "rustdesk/flutter/ios/Runner/bridge_generated.h").read_bytes(),
                         (self.output / "rustdesk/flutter/macos/Runner/bridge_generated.h").read_bytes())
        self.assertNotIn("nativeQualified", result)
        self.assertNotIn("releaseQualified", result)

    def test_exact_command_order_retains_full_application_and_bundle_gates(self):
        self.build()
        sequence = ["reserve-cargo", "flutter-version", "flutter-precache", "bootstrap-vcpkg", "install-codecs", "verify-codecs",
                    "apply-branding", "fetch-locked", "patch-owned-tao", "verify-owned-tao", "install-bridge-codegen",
                    *[f"bridge-patch-{index:02d}" for index in range(1, len(DRIVER.BRIDGE_PATCHES) + 1)],
                    "bridge-pub-get", "generate-bridge", "check-entire-app", "build-entire-app", "analyze-entire-flutter",
                    "compiled-support-guard", "compiled-branding", "verify-entire-bundle"]
        positions = [self.labels.index(label) for label in sequence]
        self.assertEqual(positions, sorted(positions))
        records = [json.loads(path.read_text()) for path in sorted((self.output / "commands").glob("*.command.json"))]
        build = next(item for item in records if "build.py" in item["argv"])
        self.assertEqual(build["argv"][1:], ["build.py", "--flutter", "--unix-file-copy-paste"])
        analysis = next(item for item in records if "analyze" in item["argv"])
        self.assertEqual(analysis["argv"][-3:], ["analyze", "--no-pub", "--no-fatal-infos"])
        check = next(item for item in records if any(value.endswith("/check-app-rust.py") for value in item["argv"]))
        self.assertIn("--local-run-root", check["argv"])
        self.assertIn("--run", check["argv"])
        install = next(item for item in records if "install" in item["argv"] and "flutter_rust_bridge_codegen" in item["argv"])
        for flag in ("--locked", "--root", "uuid", "1.80.1"):
            self.assertIn(flag, install["argv"])

    def test_failed_command_keeps_actual_exit_raw_logs_and_cannot_qualify(self):
        self.fail_label = "compiled-support-guard"
        with self.assertRaisesRegex(DRIVER.Refusal, "compiled-support-guard failed"):
            self.build()
        result = self.result()
        self.assertFalse(result["buildQualified"])
        self.assertNotIn("appTreeSha256", result)
        evidence = next((self.output / "commands").glob("*-compiled-support-guard.result.json"))
        command_result = json.loads(evidence.read_text())
        self.assertEqual(command_result["returncode"], 23)
        self.assertEqual(command_result["status"], "failed")
        raw = next((self.output / "commands").glob("*-compiled-support-guard.stderr.log"))
        self.assertEqual(raw.read_bytes(), b"inert expected failure\n")
        self.assertNotIn("verify-entire-bundle", self.labels)

    def test_dirty_input_refuses_before_branding_or_native_tools(self):
        self.dirty = True
        with self.assertRaisesRegex(DRIVER.Refusal, "source is dirty"):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertNotIn("rustc-path", self.labels)
        self.assertNotIn("apply-branding", self.labels)
        self.assertFalse((self.output / "rustdesk").exists())

    def test_existing_output_and_unsafe_roots_refuse_before_native_tools(self):
        self.output.mkdir(mode=0o700)
        with self.assertRaisesRegex(DRIVER.Refusal, "already exists"):
            self.build()
        self.assertFalse(self.labels)
        self.output.rmdir()
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        self.args.run_root = outside
        with self.assertRaisesRegex(DRIVER.Refusal, "inside this repository"):
            self.build()
        alias = self.repo / ".cache/alias"
        alias.symlink_to(outside, target_is_directory=True)
        self.args.run_root = alias
        with self.assertRaisesRegex(DRIVER.Refusal, "symlink/reparse"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.labels)

    def test_run_root_must_be_ignored_in_actual_checkout_git_context(self):
        original = self.fake_process
        def not_ignored(argv, **kwargs):
            if "check-ignore" in argv:
                self.assertEqual(argv[argv.index("-C") + 1], str(self.repo))
                self.assertIn("--no-index", argv)
                self.assertEqual(argv[-1], ".cache/builds/")
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return original(argv, **kwargs)
        with mock.patch.object(DRIVER.subprocess, "run", side_effect=not_ignored):
            with self.assertRaisesRegex(DRIVER.Refusal, "Git-ignored"):
                self.build()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.labels)

    def test_wrong_cwd_foreign_arch_and_missing_tools_do_not_start_build(self):
        with mock.patch.object(DRIVER.Path, "cwd", return_value=self.source):
            with self.assertRaisesRegex(DRIVER.Refusal, "exact repository root"):
                self.build()
        with mock.patch.object(DRIVER.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(DRIVER.Refusal, "native Apple Silicon"):
                self.build()
        with mock.patch.object(DRIVER.shutil, "which", return_value=None):
            with self.assertRaisesRegex(DRIVER.Refusal, "brew install nasm"):
                self.build()
        self.assertFalse(self.output.exists())

    def test_source_links_hardlinks_refuse_before_compilers(self):
        link = self.source / "alias"
        link.symlink_to("Cargo.toml")
        with self.assertRaisesRegex(DRIVER.Refusal, "without links/hardlinks"):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertNotIn("rustc-path", self.labels)

    def test_source_hardlink_refuses_before_compilers(self):
        os.link(self.source / "Cargo.toml", self.source / "hardlink")
        with self.assertRaisesRegex(DRIVER.Refusal, "without links/hardlinks"):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertNotIn("rustc-path", self.labels)

    def test_successful_child_exit_cannot_replace_required_bundle_proof(self):
        self.corrupt_bundle_proof = True
        with self.assertRaisesRegex(DRIVER.Refusal, "proof is absent or inconsistent"):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertFalse((self.output / "app-manifest.json").exists())

    def test_original_source_change_cannot_report_build_qualified(self):
        self.mutate_original = True
        with self.assertRaisesRegex(DRIVER.Refusal, "immutability check failed"):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertFalse(self.result()["suppliedSourceUnchanged"])

    def test_successful_build_or_guard_cannot_change_the_held_cargo_lock(self):
        self.mutate_lock_at = "build-entire-app"
        with self.assertRaisesRegex(DRIVER.Refusal, "Cargo.lock"):
            self.build()
        result = self.result()
        self.assertFalse(result["buildQualified"])
        self.assertFalse(result["cargoLockUnchanged"])
        self.assertNotEqual(result["cargoLockBeforeSha256"], result["cargoLockAfterSha256"])
        self.assertNotIn("compiled-support-guard", self.labels)

    def test_successful_guard_cannot_change_the_held_cargo_lock(self):
        self.mutate_lock_at = "compiled-support-guard"
        with self.assertRaisesRegex(DRIVER.Refusal, "Cargo.lock"):
            self.build()
        result = self.result()
        self.assertFalse(result["buildQualified"])
        self.assertFalse(result["cargoLockUnchanged"])
        self.assertNotIn("compiled-branding", self.labels)

    def test_closed_failed_build_reuses_only_readonly_codec_cache_and_copied_codegen(self):
        old = self.previous_failure()
        closed = (old / "build.result.json").read_bytes()
        before_cache = DRIVER.cache_inventory(old / "codec-cache")
        result = self.build()
        self.assertTrue(result["buildQualified"])
        self.assertTrue(result["cargoLockUnchanged"])
        self.assertTrue(result["previousCodecCacheUnchanged"])
        self.assertNotIn("install-bridge-codegen", self.labels)
        self.assertIn("reuse-codegen-version", self.labels)
        self.assertIn("generate-bridge", self.labels)
        self.assertIn("build-entire-app", self.labels)
        self.assertEqual((old / "build.result.json").read_bytes(), closed)
        self.assertEqual(DRIVER.cache_inventory(old / "codec-cache"), before_cache)
        admission = json.loads((self.output / "reuse-admission.json").read_text())
        self.assertFalse(admission["previousBuildQualified"])
        self.assertFalse(admission["cargoHomeReused"])
        self.assertFalse(admission["applicationOutputsReused"])
        self.assertEqual((self.output / "reuse-previous-build.result.json").read_bytes(), closed)
        self.assertNotEqual((old / "codegen/bin/flutter_rust_bridge_codegen").stat().st_ino,
                            (self.output / "codegen/bin/flutter_rust_bridge_codegen").stat().st_ino)
        install = next(kwargs for argv, kwargs in self.processes if Path(argv[0]).name == "vcpkg")
        self.assertEqual(install["env"]["VCPKG_BINARY_SOURCES"], "clear;files," + str(old / "codec-cache") +
                         ",read;files," + str(self.output / "codec-cache") + ",readwrite")
        self.assertEqual(install["env"]["CARGO_HOME"], str(self.output / "cargo-home"))

    def test_active_previous_build_refuses_without_native_commands(self):
        old = self.previous_failure()
        put(old, "build-active.json", b"{}")
        with self.assertRaisesRegex(DRIVER.Refusal, "active"):
            self.build()
        self.assertFalse(self.labels)
        self.assertFalse(self.result()["buildQualified"])

    def test_legacy_closed_output_and_versioned_sdk_alias_match_canonical_sdk(self):
        old = self.previous_failure()
        (old / "native-reuse-profile.json").unlink()
        result_path = old / "build.result.json"
        result = json.loads(result_path.read_text())
        result.pop("buildClosed")
        result_path.write_text(json.dumps(result))
        alias = self.sdk.parent / "MacOSX27.2.sdk"
        alias.symlink_to(self.sdk, target_is_directory=True)
        self.sdk_reported = alias
        stdout = next((old / "commands").glob("*-macos-sdk.stdout.log"))
        receipt = (str(alias) + "\n").encode()
        stdout.write_bytes(receipt)
        command_result = next((old / "commands").glob("*-macos-sdk.result.json"))
        data = json.loads(command_result.read_text())
        data["stdoutSha256"] = DRIVER.digest(stdout)
        command_result.write_text(json.dumps(data))
        self.assertTrue(self.build()["buildQualified"])
        self.assertEqual(stdout.read_bytes(), receipt)
        admission = json.loads((self.output / "reuse-admission.json").read_text())
        self.assertEqual(admission["matchedProfile"]["sdkPath"], str(alias))
        self.assertEqual(admission["matchedProfile"]["sdkCanonicalPath"], str(self.sdk))

    def test_changed_overlay_profile_refuses_before_vcpkg_or_codegen(self):
        self.previous_failure()
        (self.repo / ".github/vcpkg-triplets/arm64-osx.cmake").write_bytes(b"different overlay")
        with self.assertRaisesRegex(DRIVER.Refusal, "profile differs"):
            self.build()
        self.assertNotIn("bootstrap-vcpkg", self.labels)
        self.assertNotIn("reuse-codegen-version", self.labels)
        self.assertFalse(self.result()["buildQualified"])

    def test_reuse_unsafe_cache_alias_and_codegen_hardlink_refuse(self):
        old = self.previous_failure()
        os.link(old / "codegen/bin/flutter_rust_bridge_codegen", old / "codegen/extra-link")
        with self.assertRaisesRegex(DRIVER.Refusal, "unaliased"):
            self.build()
        self.assertFalse(self.labels)

    def test_wrong_flutter_and_missing_required_proofs_cannot_qualify(self):
        self.sdk_version = "3.99.0"
        with self.assertRaisesRegex(DRIVER.Refusal, "Flutter3.27.4"):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertNotIn("apply-branding", self.labels)

    def test_missing_bridge_output_and_missing_native_proof_cannot_qualify(self):
        self.omit_proof = True
        with self.assertRaises(OSError):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertNotIn("apply-branding", self.labels)

    def test_missing_generated_freezed_file_cannot_qualify(self):
        self.omit_bridge = True
        # A fresh fixture starts without generated files, including the freezed leaf.
        with self.assertRaises(OSError):
            self.build()
        self.assertFalse(self.result()["buildQualified"])
        self.assertNotIn("build-entire-app", self.labels)

    def test_no_secrets_provider_publication_or_gui_commands(self):
        with mock.patch.dict(os.environ, {"APPLE_CERT_P12_PASSWORD": "never-forward", "CLOUDFLARE_API_TOKEN": "never-forward"}):
            self.build()
        forbidden = {"wrangler", "codesign", "notarytool", "stapler", "open", "osascript", "security", "softwareupdate", "gh", "hdiutil"}
        for argv, kwargs in self.processes:
            self.assertFalse(forbidden.intersection(Path(value).name for value in argv))
            self.assertNotIn("shell", kwargs)
            self.assertNotIn("GITHUB_ACTIONS", kwargs["env"])
            self.assertNotIn("RUNNER_ENVIRONMENT", kwargs["env"])
            self.assertNotIn("APPLE_CERT_P12_PASSWORD", kwargs["env"])
            self.assertNotIn("CLOUDFLARE_API_TOKEN", kwargs["env"])
            self.assertEqual(kwargs["env"].get("HOME"), str(self.root))

    def test_jobs_are_bounded_and_invalid_values_refuse_before_any_process(self):
        for value in (0, -1, True, False, 999999999999999999999999, "true", "False", 1.0):
            self.args.jobs = value
            with self.subTest(jobs=value), self.assertRaisesRegex(DRIVER.Refusal, "positive integer"):
                self.build()
        self.assertFalse(self.processes)
        self.assertFalse(self.output.exists())
        self.assertEqual(DRIVER.job_count(DRIVER.MAX_JOBS), DRIVER.MAX_JOBS)

    def test_owned_jobs_override_inherited_cargo_cmake_and_vcpkg_values(self):
        self.args.jobs = 2
        names = ("CARGO_BUILD_JOBS", "CMAKE_BUILD_PARALLEL_LEVEL", "VCPKG_MAX_CONCURRENCY")
        with mock.patch.dict(os.environ, {name: "999" for name in names}):
            result = self.build()
        self.assertEqual(result["jobs"], 2)
        for argv, kwargs in self.processes:
            for name in names:
                self.assertEqual(kwargs["env"][name], "2", (argv, name))

    def test_cli_invalid_jobs_refuse_before_plan_or_processes(self):
        base = [str(SCRIPT), "--source", str(self.source), "--flutter", str(self.flutter), "--vcpkg", str(self.vcpkg),
                "--run-root", str(self.run_root), "--output", str(self.output)]
        for value in ("0", "-1", "true", "999999999999999999999999"):
            with mock.patch("sys.argv", [*base, "--jobs", value]), contextlib.redirect_stderr(io.StringIO()):
                with self.subTest(jobs=value), self.assertRaises(SystemExit) as refusal:
                    DRIVER.main()
            self.assertEqual(refusal.exception.code, 2)
        self.assertFalse(self.processes)
        self.assertFalse(self.output.exists())

    def test_plan_default_starts_no_process_and_writes_nothing(self):
        args = [str(SCRIPT), "--source", str(self.source), "--flutter", str(self.flutter), "--vcpkg", str(self.vcpkg),
                "--run-root", str(self.run_root), "--output", str(self.output)]
        with mock.patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(DRIVER.main(), 0)
        self.assertIn("PLAN ONLY", stdout.getvalue())
        plan = json.loads(stdout.getvalue().splitlines()[-1])
        self.assertEqual(plan["jobs"], 1)
        self.assertFalse(self.processes)
        self.assertFalse(self.output.exists())


class BridgeRecipeControls(unittest.TestCase):
    def test_current_workflow_is_exactly_represented_and_unknown_shell_refuses(self):
        text = (ROOT / ".github/workflows/build.yml").read_text()
        body, commands = DRIVER.extract_bridge_recipe(text)
        self.assertEqual(tuple(command for command, _ in commands), DRIVER.BRIDGE_PATCHES)
        self.assertIn(DRIVER.PUB_GET_LINE, body)
        for malformed in (text.replace("--check-priors", "--check-priors; unexpected", 1),
                          text.replace("      - name: Generate bridge", "      - name: Altered bridge", 1),
                          text.replace("--rust-input ./src/flutter_ffi.rs", "--rust-input ./different.rs", 1)):
            with self.assertRaises(DRIVER.Refusal):
                DRIVER.extract_bridge_recipe(malformed)

    def test_owned_bundle_manifest_changes_with_bytes_and_refuses_external_links(self):
        with tempfile.TemporaryDirectory(prefix="mixel-app-hash-control-") as temporary:
            root = Path(temporary).resolve()
            bundle = root / "Mixel-Remote.app"
            leaf = put(bundle, "Contents/file", b"first")
            first = DRIVER.bundle_manifest(bundle)
            leaf.write_bytes(b"second")
            self.assertNotEqual(first["treeSha256"], DRIVER.bundle_manifest(bundle)["treeSha256"])
            (bundle / "external").symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(DRIVER.Refusal, "escapes app root"):
                DRIVER.bundle_manifest(bundle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
