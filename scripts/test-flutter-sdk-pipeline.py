#!/usr/bin/env python3
"""Run bounded SDK workflow ordering controls; stand-ins do not qualify apps."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = "3.27.4"
ENGINE = "82bd5b7209295a5b7ff8cae0df96e7870171e3a5"
PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"


def job(text, name):
    match = re.search(r"^  " + re.escape(name) + r":\n(.*?)(?=^  [a-z][a-z0-9-]*:|\Z)", text, re.M | re.S)
    if not match:
        raise AssertionError("Required workflow job missing: " + name)
    return match[1]


def steps(text):
    return re.findall(r"^      - (.*?)(?=^      - |\Z)", text, re.M | re.S)


def step(text, name):
    matches = [item for item in steps(text) if item.startswith("name: " + name + "\n")]
    if len(matches) != 1:
        raise AssertionError("Required unique workflow step missing: " + name)
    return matches[0]


def run_text(text):
    match = re.search(r"^        run: (.*)$", text, re.M)
    if not match:
        raise AssertionError("Required workflow run missing")
    if match[1] != "|":
        return match[1] + "\n"
    body = re.match(r"((?:          [^\n]*(?:\n|$))*)", text[match.end() + 1:])[1]
    return textwrap.dedent(body)


def shell():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
    value = shutil.which("bash")
    if not value:
        raise AssertionError("Bash is required for actual workflow script controls")
    return value


class FlutterSdkPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        cls.preflight = (ROOT / ".github/workflows/preflight.yml").read_text(encoding="utf-8")

    def test_sdk_identity_and_all_installer_and_preflight_pins(self):
        source = (ROOT / "scripts/patch-support-flutter-sdk-compatibility.py").read_text(encoding="utf-8")
        constants = {node.targets[0].id: node.value.value for node in ast.parse(source).body
                     if isinstance(node, ast.Assign) and len(node.targets) == 1
                     and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
        self.assertEqual({name: constants[name] for name in ("SDK", "ENGINE", "PIN")},
                         {"SDK": SDK, "ENGINE": ENGINE, "PIN": PIN})
        self.assertEqual(re.findall(r'^  FLUTTER_VERSION: "([^"]+)"$', self.build, re.M), [SDK])
        for name in ("bridge", "linux", "macos", "windows"):
            flutter = [item for item in steps(job(self.build, name)) if "uses: subosito/flutter-action@" in item]
            self.assertEqual(len(flutter), 1, name)
            self.assertIn("flutter-version: ${{ env.FLUTTER_VERSION }}", flutter[0])
        for name in ("windows-packaging", "upstream-source", "macos-runtime-harness"):
            flutter = [item for item in steps(job(self.preflight, name)) if "uses: subosito/flutter-action@" in item]
            self.assertEqual(len(flutter), 1, name)
            self.assertIn("flutter-version: '" + SDK + "'", flutter[0])
        suite = (ROOT / "scripts/test-support-flutter-sdk-compatibility.py").read_text(encoding="utf-8")
        self.assertIn("version['frameworkVersion'] == S.SDK", suite)
        self.assertIn("version['engineRevision'] == S.ENGINE", suite)
        analyses = [node for node in ast.walk(ast.parse(suite)) if isinstance(node, ast.List)
                    and len(node.elts) == 4 and isinstance(node.elts[0], ast.Name)
                    and node.elts[0].id == "flutter"
                    and all(isinstance(item, ast.Constant) for item in node.elts[1:])
                    and [item.value for item in node.elts[1:]] == ["analyze", "--no-pub", "--no-fatal-infos"]]
        self.assertEqual(len(analyses), 1, "Actual full-app SDK analysis lost fatal warning/error checks")
        for workflow, name in ((self.build, "config"), (self.preflight, "release-pipeline")):
            required = "python3 scripts/test-flutter-sdk-pipeline.py"
            body = job(workflow, name)
            self.assertEqual(body.count(required), 1, "SDK integration gate missing or duplicated")
            self.assertLess(body.index("python3 scripts/test-release-pipeline.py"), body.index(required))
            matched = [item for item in steps(body) if required in item]
            self.assertEqual(len(matched), 1)
            self.assertNotIn("continue-on-error:", matched[0])
            self.assertNotIn("        if:", matched[0])

    def test_full_app_gate_precedes_build_and_uses_owned_source_in_pr(self):
        for name in ("linux", "macos"):
            body = job(self.build, name)
            gate = step(body, "Preflight generated support source and reporter")
            self.assertIn('scripts/test-support-flutter-sdk-compatibility.py --app rustdesk --flutter "$(command -v flutter)"', gate)
            self.assertNotIn("continue-on-error:", gate)
            self.assertLess(body.index("Apply Mixel-Remote branding"), body.index("Restore bridge files"))
            self.assertLess(body.index("Restore bridge files"), body.index("Preflight generated support source and reporter"))
            self.assertLess(body.index("Preflight generated support source and reporter"), body.index("Build (cargo + flutter"))
        windows = job(self.build, "windows")
        gate = step(windows, "Preflight generated support source and reporter")
        self.assertIn('MIXEL_SDK_FULL_APP_PREFLIGHT: "true"', gate)
        self.assertIn("run: bash scripts/test-windows-generated-source.sh", gate)
        self.assertLess(windows.index("Restore bridge files"), windows.index("Preflight generated support source and reporter"))
        self.assertLess(windows.index("Preflight generated support source and reporter"), windows.index("Build (cargo + flutter"))
        pr_windows = job(self.preflight, "windows-packaging")
        self.assertIn("bash scripts/test-windows-generated-source.sh", pr_windows)
        self.assertNotIn("MIXEL_SDK_FULL_APP_PREFLIGHT:", pr_windows)
        for name in ("upstream-source", "macos-runtime-harness"):
            body = job(self.preflight, name)
            analyzer = body.index('test-support-flutter-analysis.py --dart "$(command -v dart)" --evidence-dir "$RUNNER_TEMP/mixel-sdk-source"')
            gate = body.index('test-support-flutter-sdk-compatibility.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"')
            self.assertLess(analyzer, gate)

    def test_actual_unix_preflight_scripts_preserve_first_analyzer_and_sdk_failure(self):
        for workflow, name, gate_name in ((self.build, "linux", "Preflight generated support source and reporter"),
                                         (self.build, "macos", "Preflight generated support source and reporter"),
                                         (self.preflight, "upstream-source", "Exercise actual source patches and support reporter"),
                                         (self.preflight, "macos-runtime-harness", "Prove exact SDK source compatibility and composed branding")):
            script = run_text(step(job(workflow, name), gate_name))
            for failure, expected in (("analysis", 41), ("sdk", 42)):
                with self.subTest(job=name, failure=failure), tempfile.TemporaryDirectory(prefix="mixel SDK pipeline préflight ") as directory:
                    root = Path(directory)
                    shim = root / "record.py"
                    shim.write_text("import json,os,sys\nfrom pathlib import Path\n"
                                    "with Path(os.environ['MIXEL_CALLS']).open('a',encoding='utf-8') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n"
                                    "name=sys.argv[1]\n"
                                    "if name.endswith('test-support-flutter-analysis.py') and os.environ['MIXEL_FAIL']=='analysis': sys.exit(41)\n"
                                    "if name.endswith('test-support-flutter-sdk-compatibility.py') and os.environ['MIXEL_FAIL']=='sdk': sys.exit(42)\n",
                                    encoding="utf-8")
                    prefix = 'python3() { "$MIXEL_PYTHON" "$MIXEL_SHIM" "$@"; }\n'
                    env = {**os.environ, "MIXEL_PYTHON": Path(sys.executable).resolve().as_posix(), "MIXEL_SHIM": shim.as_posix(),
                           "MIXEL_CALLS": str(root / "calls"), "MIXEL_FAIL": failure,
                           "RUNNER_TEMP": (root / "runner temporary étape 🚀").as_posix(),
                           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
                    result = subprocess.run([shell(), "-e", "-o", "pipefail", "-c", prefix + script], env=env,
                                            capture_output=True, text=True, encoding="utf-8", timeout=15)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    calls = [json.loads(line) for line in (root / "calls").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual([item[0] for item in calls], ["scripts/test-support-flutter-analysis.py"] +
                                     (["scripts/test-support-flutter-sdk-compatibility.py"] if failure == "sdk" else []))
                    if failure == "sdk":
                        self.assertEqual(calls[1][1:3], ["--app", "rustdesk"] if workflow == self.build else
                                         ["--app", env["RUNNER_TEMP"] + "/mixel-sdk-source/pipeline-lf"])
                        self.assertEqual("--flutter" in calls[1], workflow == self.build)

    def test_actual_framework_discovery_resolves_sdk_and_refuses_missing_header(self):
        name = "Prepare the exact release Flutter framework for native regression checks"
        script = run_text(step(job(self.build, "macos"), name))
        self.assertEqual(script, run_text(step(job(self.preflight, "macos-runtime-harness"), name)))
        with tempfile.TemporaryDirectory(prefix="mixel Flutter SDK étape ") as directory:
            root = Path(directory)
            sdk = root / "official SDK préflight 🚀"
            flutter = sdk / "bin/flutter"
            flutter.parent.mkdir(parents=True)
            flutter.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            flutter.chmod(0o755)
            launcher = root / "selected flutter"
            launcher.symlink_to(flutter)
            framework = sdk / "bin/cache/artifacts/engine/darwin-x64-release/FlutterMacOS.xcframework/macos-arm64_x86_64/FlutterMacOS.framework"
            header = framework / "Headers/FlutterEngine.h"
            header.parent.mkdir(parents=True)
            header.write_text("// controlled release framework identity\n", encoding="utf-8")
            shim = root / "public-query.py"
            shim.write_text("import pathlib,shutil\nshutil.which=lambda name: " + repr(str(launcher)) + "\n" +
                            "exec(__import__('sys').argv[2])\n", encoding="utf-8")
            prefix = 'flutter() { [[ "$*" == "precache --macos" ]]; }\npython3() { "$MIXEL_PYTHON" "$MIXEL_QUERY" "$@"; }\n'
            output = root / "github environment"
            env = {**os.environ, "GITHUB_ENV": str(output), "MIXEL_PYTHON": Path(sys.executable).resolve().as_posix(), "MIXEL_QUERY": shim.as_posix(),
                   "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run([shell(), "-e", "-o", "pipefail", "-c", prefix + script], env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "MIXEL_FLUTTER_FRAMEWORK=" + str(framework.resolve()) + "\n")
            output.unlink()
            header.unlink()
            result = subprocess.run([shell(), "-e", "-o", "pipefail", "-c", prefix + script], env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists(), "Missing exact SDK framework exported success")
        for workflow, name in ((self.build, "macos"), (self.preflight, "macos-runtime-harness")):
            body = job(workflow, name)
            window_arguments = "--native " if workflow == self.preflight else ""
            self.assertIn('test-support-macos-window.py ' + window_arguments + '--flutter-framework "$MIXEL_FLUTTER_FRAMEWORK"', body)
            self.assertIn('test-support-macos-termination.py --flutter "$(command -v flutter)" --framework "$MIXEL_FLUTTER_FRAMEWORK"', body)
            self.assertLess(body.index("Prepare the exact release Flutter framework"), body.index("--flutter-framework"))

    def test_actual_windows_dispatcher_stops_at_analyzer_or_sdk_failure(self):
        for full in ("false", "true"):
            for target, code, count in (("test-support-flutter-analysis.py", 41, 1),
                                        ("test-support-flutter-sdk-compatibility.py", 42, 2)):
                with self.subTest(full_app_request=full, failure=target), tempfile.TemporaryDirectory(prefix="mixel SDK dispatch étape ") as directory:
                    root = Path(directory)
                    scripts = root / "scripts"
                    scripts.mkdir()
                    dispatcher = scripts / "test-windows-generated-source.sh"
                    dispatcher.write_bytes((ROOT / "scripts/test-windows-generated-source.sh").read_bytes())
                    (scripts / "use-windows-msvc-bash.sh").write_text(
                        'python() { "$MIXEL_PYTHON" "$MIXEL_SHIM" "$@"; }\n', encoding="utf-8", newline="\n")
                    shim = root / "record.py"
                    shim.write_text("import json,os,sys\nfrom pathlib import Path\n"
                                    "with Path('calls.jsonl').open('a',encoding='utf-8') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n"
                                    "if sys.argv[1]=='scripts/'+os.environ['MIXEL_FAIL']: sys.exit(int(os.environ['MIXEL_CODE']))\n",
                                    encoding="utf-8")
                    env = {**os.environ, "MIXEL_PYTHON": Path(sys.executable).resolve().as_posix(), "MIXEL_SHIM": shim.as_posix(),
                           "MIXEL_FAIL": target, "MIXEL_CODE": str(code), "MIXEL_SDK_FULL_APP_PREFLIGHT": full,
                           "RUNNER_TEMP": (root / "runner temporary préflight 🚀").as_posix(),
                           "RDREPO": (root / "full application préflight 🚀").as_posix(),
                           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
                    result = subprocess.run([shell(), str(dispatcher)], cwd=root, env=env,
                                            capture_output=True, text=True, encoding="utf-8", timeout=15)
                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(len(calls), count, "Failure executed a later source/native command")
                    self.assertEqual(calls[-1][0], "scripts/" + target)

    def test_bridge_generation_preserves_owned_overlay_and_only_six_generated_members(self):
        bridge = job(self.build, "bridge")
        self.assertLess(bridge.index("Fetch upstream RustDesk"), bridge.index("Apply exact SDK compatibility before bridge generation"))
        self.assertLess(bridge.index("Apply exact SDK compatibility before bridge generation"), bridge.index("Install flutter_rust_bridge_codegen"))
        self.assertLess(bridge.index("Apply exact SDK compatibility before bridge generation"), bridge.index("flutter pub get"))
        self.assertIn("run: ./scripts/apply-branding.sh", bridge)
        generated = step(bridge, "Generate bridge")
        self.assertLess(generated.index("patch-support-macos-texture.py"), generated.index("flutter pub get"))
        self.assertLess(generated.index("patch-support-macos-window.py"), generated.index("flutter pub get"))
        self.assertIn("--version 1.80.1 --features uuid --locked", bridge)
        artifact = [item for item in steps(bridge) if "name: bridge-artifact\n" in item]
        self.assertEqual(len(artifact), 1)
        members = re.search(r"          path: \|\n((?:            [^\n]+\n)+)", artifact[0])[1]
        self.assertEqual(textwrap.dedent(members).splitlines(), [
            "rustdesk/src/bridge_generated.rs", "rustdesk/src/bridge_generated.io.rs",
            "rustdesk/flutter/lib/generated_bridge.dart", "rustdesk/flutter/lib/generated_bridge.freezed.dart",
            "rustdesk/flutter/macos/Runner/bridge_generated.h", "rustdesk/flutter/ios/Runner/bridge_generated.h"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
