#!/usr/bin/env python3
"""Exercise release selection, fail-closed staging, and portable build failures."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


release = module("mixel_release", "release-artifacts.py")
portable = module("mixel_portable", "build-windows-portable.py")


class ReleasePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mixel-release-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.branding = self.root / "branding.env"
        self.branding.write_text('UPSTREAM_VERSION="1.4.6"\n', encoding="utf-8")
        self.artifacts = self.root / "artifacts"
        self.destination = self.root / "dist"

    def artifact(self, platform, suffix, content=b"signed-release-fixture"):
        path = self.artifacts / platform / f"mixel-remote-1.4.6-{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_config_default_and_exact_platform_selection(self):
        self.assertEqual(release.resolve_config(self.branding, "", "")["platforms"], "linux,macos,windows")
        config = release.resolve_config(self.branding, "1.4.7", " windows, linux ")
        self.assertEqual(config["upstream_version"], "1.4.7")
        self.assertEqual(config["platforms"], "linux,windows")
        self.assertEqual(config["macos"], "false")

    def test_unknown_duplicate_and_empty_platform_tokens_rejected(self):
        for value in ("win", "windowswindows", "windows,", "linux,linux", "linux,macos;windows"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.selected_platforms(value)

    def test_unsafe_versions_rejected(self):
        for value in ("main", "--help", "../1.4.6", "1.4.6\nwindows=true", '1.4.6"; touch /tmp/mixel;', "$(id)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.validate_version(value)

    def test_runtime_run_id_rejects_non_digits_and_zero(self):
        self.assertEqual(release.validate_runtime_run_id("37916407292"), "37916407292")
        for value in ("0", "", " 37916407292", "37916407292\n", "-1", "1;id", "1$(id)", "1" * 21):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.validate_runtime_run_id(value)

    def test_runtime_diagnostic_cannot_request_publication(self):
        output = self.root / "runtime-output"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/release-artifacts.py"), "runtime"],
                                env={**os.environ, "GITHUB_OUTPUT": str(output), "RUNTIME_RUN_ID": "37916407292", "PUBLISH_REQUESTED": "true"},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_config_cli_treats_override_as_data(self):
        output = self.root / "github-output"
        marker = self.root / "must-not-exist"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/release-artifacts.py"), "config", "--branding", str(self.branding)],
                                env={**os.environ, "GITHUB_OUTPUT": str(output), "INPUT_UPSTREAM_VERSION": f'1.4.6"; touch {marker}; #'},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertFalse(output.exists())

    def test_duplicate_branding_version_rejected(self):
        self.branding.write_text("UPSTREAM_VERSION=1.4.6\nUPSTREAM_VERSION=1.4.7\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            release.resolve_config(self.branding, "", "")

    def test_publication_requires_selected_platform_signing(self):
        config = release.resolve_config(self.branding, "", "macos")
        environment = {"PUBLISH_REQUESTED": "true", "CLOUDFLARE_API_TOKEN": "fixture", "CLOUDFLARE_ACCOUNT_ID": "fixture"}
        with self.assertRaisesRegex(ValueError, "APPLE_CERT_P12_BASE64"):
            release.require_publish_credentials(config, environment)
        for name in ("APPLE_CERT_P12_BASE64", "APPLE_CERT_P12_PASSWORD", "APPLE_NOTARY_USER", "APPLE_NOTARY_PASSWORD", "APPLE_NOTARY_TEAM_ID"):
            environment[name] = "fixture"
        release.require_publish_credentials(config, environment)
        release.require_publish_credentials(config, {"PUBLISH_REQUESTED": "false"})

    def test_partial_windows_release_has_exact_same_alias_bytes(self):
        self.artifact("windows", "x86_64.exe")
        foreign = self.artifacts / "windows" / "Mixel-Remote-Support-Windows.exe"
        foreign.write_bytes(b"stale-aliased-build")
        self.artifact("linux", "x86_64.deb", b"unselected")
        manifest = release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "windows")
        self.assertEqual(len(manifest["files"]), 4)
        for entry in manifest["files"]:
            self.assertEqual((self.destination / entry["name"]).read_bytes(), b"signed-release-fixture")
        self.assertFalse((self.destination / "Mixel-Remote-Support.deb").exists())

    def test_missing_selected_platform_fails_before_creating_aliases(self):
        self.artifact("windows", "x86_64.exe")
        with self.assertRaisesRegex(ValueError, "linux"):
            release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "linux,windows")
        self.assertFalse(self.destination.exists())

    def test_missing_intel_mac_fails_before_creating_aliases(self):
        self.artifact("macos-aarch64", "aarch64.dmg")
        with self.assertRaisesRegex(ValueError, "macos-x86_64"):
            release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "macos")
        self.assertFalse(self.destination.exists())

    def test_zero_bytes_and_symlinks_are_not_valid_releases(self):
        path = self.artifact("windows", "x86_64.exe", b"")
        with self.assertRaises(ValueError):
            release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "windows")
        path.unlink()
        target = self.root / "external.exe"
        target.write_bytes(b"outside selected artifact")
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "windows")

    def test_optional_msi_staged_only_when_built(self):
        self.artifact("windows", "x86_64.exe")
        self.artifact("windows", "x86_64.msi", b"signed-msi")
        manifest = release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "windows")
        self.assertEqual(len(manifest["files"]), 6)
        self.assertEqual((self.destination / "Mixel-Remote-Support.msi").read_bytes(), b"signed-msi")

    def test_existing_staging_directory_cannot_mix_release_versions(self):
        self.artifact("windows", "x86_64.exe")
        self.destination.mkdir()
        (self.destination / "stale.exe").write_bytes(b"stale")
        with self.assertRaises(ValueError):
            release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "windows")

    def test_all_selected_platforms_staged_with_distinct_mac_architectures(self):
        for platform, suffix in (("windows", "x86_64.exe"), ("linux", "x86_64.deb"), ("macos-aarch64", "aarch64.dmg"), ("macos-x86_64", "x86_64.dmg")):
            self.artifact(platform, suffix, platform.encode())
        manifest = release.stage_artifacts(self.artifacts, self.destination, "1.4.6", "")
        self.assertEqual(len(manifest["files"]), 10)
        self.assertEqual((self.destination / "Mixel-Remote-Support-Intel.dmg").read_bytes(), b"macos-x86_64")
        self.assertEqual((self.destination / "Mixel-Remote-Support-Apple-Silicon.dmg").read_bytes(), b"macos-aarch64")

    def test_portable_failure_cannot_reuse_stale_launcher_and_restores_cwd(self):
        repo = self.root / "repo"
        payload = self.root / "payload"
        payload.mkdir()
        (payload / "Mixel-Remote.exe").write_bytes(b"signed-main")
        generator = repo / "libs/portable/generate.py"
        generator.parent.mkdir(parents=True)
        generator.write_text("def generate_md5_table(folder, level):\n return {}\ndef write_package_metadata(table, output, exe):\n assert exe == './Mixel-Remote.exe'\ndef write_app_metadata(output):\n pass\n", encoding="utf-8")
        stale = repo / "target/release/rustdesk-portable-packer.exe"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale-old-launcher")
        previous = Path.cwd()
        def failing_runner(command, **kwargs):
            self.assertIn("--locked", command)
            self.assertTrue(kwargs["check"])
            os.chdir(repo)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            portable.build_portable(repo, payload, self.destination / "launcher.exe", failing_runner)
        self.assertEqual(Path.cwd(), previous)
        self.assertFalse(stale.exists())
        self.assertFalse((self.destination / "launcher.exe").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
