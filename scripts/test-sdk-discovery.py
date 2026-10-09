#!/usr/bin/env python3
"""Regression coverage for Windows Dart analyzer's case-sensitive EXE check."""
import unittest

from sdk_discovery import find_dart, normalize_dart_executable


class DartSdkDiscoveryTests(unittest.TestCase):
    def test_uppercase_pathext_suffix_is_normalized(self):
        resolved = find_dart({}, windows=True, finder=lambda name: r"C:\Flutter SDK\dart-sdk\bin\dart.EXE")
        self.assertEqual(resolved, r"C:\Flutter SDK\dart-sdk\bin\dart.exe")
        # Dart's analyzer will now reuse the existing executable, rather than
        # appending a second .exe to its case-sensitive suffix check.
        self.assertTrue(resolved.endswith(".exe"))

    def test_mixed_case_and_lowercase_windows_suffixes_are_normalized(self):
        for suffix in (".eXe", ".exe", ".EXE"):
            self.assertEqual(normalize_dart_executable("D:/SDK/dart" + suffix, windows=True), "D:/SDK/dart.exe")

    def test_explicit_dart_bin_wins_and_is_normalized(self):
        def unexpected_search(name):
            raise AssertionError("An explicit SDK override must not search PATH")
        self.assertEqual(find_dart({"DART_BIN": r"D:\Custom SDK\dart.EXE"}, windows=True, finder=unexpected_search),
                         r"D:\Custom SDK\dart.exe")

    def test_empty_override_searches_path(self):
        self.assertEqual(find_dart({"DART_BIN": ""}, windows=True, finder=lambda name: "dart.EXE"), "dart.exe")

    def test_unix_case_sensitive_paths_remain_exact(self):
        self.assertEqual(normalize_dart_executable("/SDK/bin/dart.EXE", windows=False), "/SDK/bin/dart.EXE")
        self.assertEqual(find_dart({}, windows=False, finder=lambda name: "/SDK/bin/dart"), "/SDK/bin/dart")

    def test_missing_sdk_remains_missing(self):
        self.assertIsNone(find_dart({}, windows=True, finder=lambda name: None))

    def test_non_executable_suffixes_are_not_replaced(self):
        self.assertEqual(normalize_dart_executable("D:/Flutter/bin/dart.bat", windows=True), "D:/Flutter/bin/dart.bat")


if __name__ == "__main__":
    unittest.main(verbosity=2)
