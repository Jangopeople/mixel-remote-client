#!/usr/bin/env python3
"""Pinned loader regression and fail-closed compiled Mixel icon checks."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = runpy.run_path(str(ROOT / "scripts/patch-flutter-branding.py"))
VERIFY = runpy.run_path(str(ROOT / "scripts/verify-flutter-branding.py"))
SVG = ROOT / "scripts/fixtures/flutter-icon-upstream.svg"
ICON = ROOT / "branding/icon-512.png"


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mixel-flutter-branding-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "upstream"
        self.source = self.repo / "flutter/lib/common.dart"
        self.source.parent.mkdir(parents=True)
        self.assets = self.repo / "flutter/assets"
        self.assets.mkdir(parents=True)
        self.source.write_text("// unchanged prefix\n" + PATCH["ORIGINAL"] + "\n// unchanged suffix\n")
        self.svg = self.assets / "icon.svg"
        self.svg.write_bytes(SVG.read_bytes())
        self.compiled = self.root / "compiled/flutter_assets"
        (self.compiled / "assets").mkdir(parents=True)
        self.png = self.compiled / "assets/icon.png"
        self.manifest = self.compiled / "AssetManifest.json"
        self.png.write_bytes(ICON.read_bytes())
        self.manifest.write_text(json.dumps({"assets/icon.png": ["assets/icon.png"]}))

    def apply(self):
        PATCH["apply"](self.repo, ROOT / "branding")

    def verify(self):
        return VERIFY["verify"](self.compiled, ICON)

    def test_actual_pinned_svg_fixture_matches_known_original(self):
        self.assertEqual(hashlib.sha256(SVG.read_bytes()).hexdigest(), PATCH["UPSTREAM_ICON_SHA256"])

    def test_original_loader_has_no_png_and_uses_unbranded_svg_corrected_is_idempotent(self):
        self.assertFalse((self.assets / "icon.png").exists())
        self.assertIn("'assets/icon.svg'", self.source.read_text())
        before = self.source.read_bytes()
        self.apply()
        self.assertEqual((self.assets / "icon.png").read_bytes(), ICON.read_bytes())
        self.assertFalse(self.svg.exists())
        self.assertEqual(self.source.read_bytes(), before.replace(PATCH["ORIGINAL"].encode(), PATCH["BRANDED"].encode()))
        result = self.source.read_bytes()
        self.apply()
        self.assertEqual(self.source.read_bytes(), result)

    def test_windows_crlf_is_preserved_outside_exact_loader_and_idempotent(self):
        before = self.source.read_bytes().replace(b"\n", b"\r\n")
        self.source.write_bytes(before)
        self.apply()
        expected = before.replace(PATCH["ORIGINAL"].replace("\n", "\r\n").encode(), PATCH["BRANDED"].replace("\n", "\r\n").encode())
        self.assertEqual(self.source.read_bytes(), expected)
        self.apply()
        self.assertEqual(self.source.read_bytes(), expected)

    def test_source_drift_and_duplicate_loaders_fail_before_any_asset_mutation(self):
        for source in (PATCH["ORIGINAL"].replace("width: size", "width: size + 1"), PATCH["ORIGINAL"] * 2,
                       PATCH["ORIGINAL"] + PATCH["BRANDED"]):
            with self.subTest(source=source[:35]):
                self.source.write_text(source)
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.svg.read_bytes(), SVG.read_bytes())
                self.assertFalse((self.assets / "icon.png").exists())

    def test_missing_or_changed_original_svg_fails_before_loader_mutation(self):
        before = self.source.read_bytes()
        for value in (None, b"changed"):
            with self.subTest(value=value):
                if value is None:
                    self.svg.unlink()
                else:
                    self.svg.write_bytes(value)
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.source.read_bytes(), before)
                self.assertFalse((self.assets / "icon.png").exists())

    def test_unexpected_existing_png_and_symlink_are_refused(self):
        icon = self.assets / "icon.png"
        icon.write_bytes(b"unexpected")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertTrue(self.svg.exists())
        icon.unlink()
        try:
            icon.symlink_to(ICON)
        except OSError:
            self.skipTest("Symlink creation unavailable on this runner")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertTrue(self.svg.exists())

    def test_compiled_png_and_manifest_bind_exact_authoritative_bytes(self):
        self.assertEqual(self.verify()["icon_sha256"], hashlib.sha256(ICON.read_bytes()).hexdigest())

    def test_original_packaged_svg_without_png_is_rejected(self):
        self.png.unlink()
        (self.compiled / "assets/icon.svg").write_bytes(SVG.read_bytes())
        self.manifest.write_text(json.dumps({"assets/icon.svg": ["assets/icon.svg"]}))
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_png_and_unbranded_svg_file_are_rejected(self):
        self.png.write_bytes(b"wrong")
        with self.assertRaises(ValueError):
            self.verify()
        self.png.write_bytes(ICON.read_bytes())
        (self.compiled / "assets/icon.svg").write_bytes(SVG.read_bytes())
        with self.assertRaises(ValueError):
            self.verify()

    def test_manifest_missing_alias_svg_duplicate_key_or_non_object_is_rejected(self):
        for value in ({}, {"assets/icon.png": ["assets/other.png"]},
                      {"assets/icon.png": ["assets/icon.png"], "assets/icon.svg": []}, [],
                      '{"assets/icon.png":[],"assets/icon.png":["assets/icon.png"]}'):
            with self.subTest(value=value):
                self.manifest.write_text(value if isinstance(value, str) else json.dumps(value))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_compiled_asset_symlink_is_rejected(self):
        self.png.unlink()
        try:
            self.png.symlink_to(ICON)
        except OSError:
            self.skipTest("Symlink creation unavailable on this runner")
        with self.assertRaises(ValueError):
            self.verify()


if __name__ == "__main__":
    unittest.main(verbosity=2)
