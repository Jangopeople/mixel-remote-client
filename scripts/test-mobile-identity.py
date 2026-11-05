#!/usr/bin/env python3
"""Exercise the mobile projection against a real desktop-branded checkout."""
import argparse
import importlib.util
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("mobile_identity", Path(__file__).with_name("patch-mobile-identity.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MobileIdentity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ["flutter/android/app", "flutter/ios/Runner", "flutter/ios/Runner.xcodeproj"]:
            shutil.copytree(SOURCE / name, self.root / name)
        shutil.copyfile(SOURCE / "flutter/ios/Podfile", self.root / "flutter/ios/Podfile")
        shutil.copyfile(SOURCE / "flutter/android/gradle.properties", self.root / "flutter/android/gradle.properties")
        target = self.root / "flutter/lib/models/native_model.dart"
        target.parent.mkdir(parents=True)
        shutil.copyfile(SOURCE / "flutter/lib/models/native_model.dart", target)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_identity_and_jni_loader_chain(self):
        self.assertGreater(module.apply(self.root), 0)
        info = plistlib.loads((self.root / "flutter/ios/Runner/Info.plist").read_bytes())
        self.assertEqual(info["CFBundleDisplayName"], "Mixel Remote")
        self.assertEqual(info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["mixel-remote"])
        ffi = (self.root / "flutter/android/app/src/main/kotlin/ffi.kt").read_text()
        self.assertIn('System.loadLibrary("mixel-remote")', ffi)
        self.assertIn("package ffi", ffi)
        self.assertIn("import ch.mixel.remote.RdClipboardManager", ffi)
        manifest = (self.root / "flutter/android/app/src/main/AndroidManifest.xml").read_text()
        self.assertIn('package="ch.mixel.remote"', manifest)
        self.assertIn('android:label="Mixel Remote"', manifest)
        self.assertNotIn("com.carriez", manifest)
        gradle = (self.root / "flutter/android/app/build.gradle").read_text()
        self.assertIn("minSdkVersion 28", gradle)
        self.assertIn('ndkVersion "28.2.13676358"', gradle)
        self.assertIn("useLegacyPackaging true", gradle)
        project = (self.root / "flutter/ios/Runner.xcodeproj/project.pbxproj").read_text()
        self.assertEqual(project.count('"MIXEL_RUST_TARGET[sdk=iphonesimulator*]" = aarch64-apple-ios-sim;'), 3)
        self.assertNotIn("target/aarch64-apple-ios/", project)
        self.assertEqual(project.count("IPHONEOS_DEPLOYMENT_TARGET = 15.0;"), 3)
        self.assertEqual(project.count('"ARCHS[sdk=iphonesimulator*]" = arm64;'), 3)

    def test_repeat_and_check_are_byte_preserving(self):
        before = self.snapshot()
        self.assertGreater(module.apply(self.root, check=True), 0)
        self.assertEqual(before, self.snapshot())
        module.apply(self.root)
        after = self.snapshot()
        self.assertEqual(module.apply(self.root), 0)
        self.assertEqual(after, self.snapshot())

    def test_late_unexpected_source_refuses_all_writes(self):
        path = self.root / "flutter/ios/Runner.xcodeproj/project.pbxproj"
        path.write_bytes(path.read_bytes().replace(b"HZF9JMC8YN", b"UNKNOWN123"))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            module.apply(self.root)
        self.assertEqual(before, self.snapshot())

    def test_wrong_android_loader_refuses_all_writes(self):
        path = self.root / "flutter/lib/models/native_model.dart"
        path.write_bytes(path.read_bytes().replace(b"libmixel-remote.so", b"unexpected.so"))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            module.apply(self.root)
        self.assertEqual(before, self.snapshot())

    def test_previous_android_packaging_upgrades_without_duplicate_settings(self):
        module.apply(self.root)
        path = self.root / "flutter/android/app/build.gradle"
        path.write_bytes(path.read_bytes().replace(
            b"; keepDebugSymbols += ['**/libmixel-remote.so']", b""))
        self.assertEqual(module.apply(self.root), 1)
        text = path.read_text()
        self.assertEqual(text.count('ndkVersion "28.2.13676358"'), 1)
        self.assertEqual(text.count("keepDebugSymbols"), 1)
        self.assertEqual(module.apply(self.root), 0)

    def test_previous_mobile_profile_upgrades_without_duplicate_settings(self):
        module.apply(self.root)
        path = self.root / "flutter/ios/Runner.xcodeproj/project.pbxproj"
        path.write_bytes(path.read_bytes().replace(
            b'\t\t\t\t"ARCHS[sdk=iphonesimulator*]" = arm64;\n', b""))
        self.assertEqual(module.apply(self.root), 1)
        project = path.read_text()
        self.assertEqual(project.count("MIXEL_RUST_TARGET = aarch64-apple-ios;"), 3)
        self.assertEqual(project.count('"ARCHS[sdk=iphonesimulator*]" = arm64;'), 3)
        self.assertEqual(module.apply(self.root), 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    SOURCE = args.source
    unittest.main(argv=[__file__], verbosity=2)
