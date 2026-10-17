#!/usr/bin/env python3
"""Regression coverage for Windows Dart analyzer's case-sensitive EXE check."""
import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
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


class DartConsumerTests(unittest.TestCase):
    """Execute the real consumer bodies without launching a native process."""

    scripts = Path(__file__).resolve().parent

    def texture_function(self, *, original=False):
        source = (self.scripts / "test-support-macos-texture.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        function = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef) and node.name == "dart_controls")
        literal = ast.get_source_segment(source, function)
        self.assertEqual(literal.count("    dart = find_dart()"), 1)
        if original:
            literal = literal.replace("    dart = find_dart()",
                                      '    dart = os.environ.get("DART_BIN") or shutil.which("dart")', 1)
        constants = {}
        for node in module.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                name = getattr(node.targets[0], "id", None)
                if name in ("DART_STUBS", "DART_TEST"):
                    constants[name] = ast.literal_eval(node.value)
        self.assertEqual(set(constants), {"DART_STUBS", "DART_TEST"})
        return literal, constants

    def texture_commands(self, environment, path, *, windows, original=False,
                         reject_analyzer_suffix=False):
        literal, constants = self.texture_function(original=original)
        calls, searches = [], []

        def finder(name):
            self.assertEqual(name, "dart")
            searches.append(name)
            return path

        def run(arguments, output, name):
            calls.append(arguments)
            if reject_analyzer_suffix and arguments[1] == "analyze":
                executable = arguments[0]
                analyzer = executable if executable.endswith(".exe") else executable + ".exe"
                if analyzer != executable:
                    raise RuntimeError("historical Dart analyzer executable: " + analyzer)
            return "controlled child output"

        namespace = {**constants, "os": SimpleNamespace(environ=environment),
                     "shutil": SimpleNamespace(which=finder), "run": run,
                     "find_dart": lambda: find_dart(environment, windows=windows, finder=finder)}
        exec(compile(literal, "actual-texture-dart-consumer", "exec"), namespace)
        with tempfile.TemporaryDirectory(prefix="mixel-dart-consumer-") as temporary:
            output = Path(temporary)
            model = output / "flutter/lib/models/desktop_render_texture.dart"
            model.parent.mkdir(parents=True)
            data = b"class _PixelbufferTexture {}\nclass _GpuTexture {}\n"
            model.write_bytes(data)
            with redirect_stdout(io.StringIO()):
                namespace["dart_controls"](output, output,
                                           {"flutter/lib/models/desktop_render_texture.dart": data})
        return calls, searches

    def test_actual_texture_consumer_uses_selected_origin_for_all_three_commands(self):
        for suffix in (".EXE", ".eXe", ".exe"):
            with self.subTest(origin="PATH", suffix=suffix):
                calls, searches = self.texture_commands({}, "C:/Flutter SDK/Ünicode/dart" + suffix,
                                                       windows=True)
                self.assertEqual(searches, ["dart"])
                self.assertEqual([call[0] for call in calls], ["C:/Flutter SDK/Ünicode/dart.exe"] * 3)
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[0][1], "analyze")
                self.assertTrue(calls[1][1].endswith("actual-pixelbuffer-lifecycle.dart"))
                self.assertTrue(calls[2][1].endswith("original-zero-registration.dart"))
        calls, searches = self.texture_commands({"DART_BIN": "D:/Explicit SDK/dart.EXE"},
                                               "C:/Wrong SDK/dart.EXE", windows=True)
        self.assertEqual(searches, [])
        self.assertEqual([call[0] for call in calls], ["D:/Explicit SDK/dart.exe"] * 3)
        calls, searches = self.texture_commands({"DART_BIN": ""}, "C:/PATH SDK/dart.EXE", windows=True)
        self.assertEqual(searches, ["dart"])
        self.assertEqual([call[0] for call in calls], ["C:/PATH SDK/dart.exe"] * 3)

    def test_actual_texture_consumer_preserves_unix_paths_and_missing_sdk_fatal(self):
        for path in ("/SDK with spaces/Ünicode/dart", "/SDK/bin/dart.EXE", "/SDK/bin/dart.bat"):
            with self.subTest(path=path):
                calls, _ = self.texture_commands({"DART_BIN": path}, "/wrong/dart", windows=False)
                self.assertEqual([call[0] for call in calls], [path] * 3)
        with self.assertRaisesRegex(RuntimeError, "Dart SDK required for async texture controls"):
            self.texture_commands({}, None, windows=True)

    def test_literal_previous_texture_consumer_reproduces_double_suffix(self):
        with self.assertRaisesRegex(RuntimeError, r"dart\.EXE\.exe$"):
            self.texture_commands({}, "C:/Flutter SDK/dart.EXE", windows=True, original=True,
                                  reject_analyzer_suffix=True)
        calls, searches = self.texture_commands({}, "C:/Flutter SDK/dart.EXE", windows=True,
                                               reject_analyzer_suffix=True)
        self.assertEqual(searches, ["dart"])
        self.assertEqual([call[0] for call in calls], ["C:/Flutter SDK/dart.exe"] * 3)

    def test_actual_mac_availability_keeps_native_only_boundary(self):
        source = (self.scripts / "test-support-macos.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        condition = next(node for node in module.body if isinstance(node, ast.If)
                         and "texture_arguments.append('--native-only')" in ast.get_source_segment(source, node))
        self.assertIn("find_dart()", ast.get_source_segment(source, condition))
        for platform, available, expected in (("Darwin", None, ["--native-only"]),
                                               ("Darwin", "/SDK/dart", []),
                                               ("Darwin", "/SDK/dart.EXE", []),
                                               ("Windows", None, []), ("Linux", None, [])):
            with self.subTest(platform=platform, available=available):
                namespace = {"platform": SimpleNamespace(system=lambda: platform),
                             "find_dart": lambda: available, "texture_arguments": []}
                exec(compile(ast.Module(body=[condition], type_ignores=[]),
                             "actual-macos-dart-availability", "exec"), namespace)
                self.assertEqual(namespace["texture_arguments"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
