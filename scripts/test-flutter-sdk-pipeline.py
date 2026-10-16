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
            ordered = ["scripts/test-support-flutter-analysis.py",
                       "scripts/test-macos-startup.py",
                       "scripts/test-support-macos-tray-url.py",
                       "scripts/test-support-launch-macos-tray-controls.py",
                       "scripts/test-support-macos-termination-support.py",
                       "scripts/test-support-flutter-sdk-compatibility.py"]
            for position, expected in enumerate((41, 43, 44, 45, 46, 42)):
                failure = ordered[position]
                with self.subTest(job=name, failure=failure), tempfile.TemporaryDirectory(prefix="mixel SDK pipeline préflight ") as directory:
                    root = Path(directory)
                    shim = root / "record.py"
                    shim.write_text("import json,os,sys\nfrom pathlib import Path\n"
                                    "with Path(os.environ['MIXEL_CALLS']).open('a',encoding='utf-8') as out: out.write(json.dumps(sys.argv[1:])+'\\n')\n"
                                    "if sys.argv[1]==os.environ['MIXEL_FAIL']: sys.exit(int(os.environ['MIXEL_CODE']))\n",
                                    encoding="utf-8")
                    prefix = 'python3() { "$MIXEL_PYTHON" "$MIXEL_SHIM" "$@"; }\n'
                    env = {**os.environ, "MIXEL_PYTHON": Path(sys.executable).resolve().as_posix(), "MIXEL_SHIM": shim.as_posix(),
                           "MIXEL_CALLS": str(root / "calls"), "MIXEL_FAIL": failure, "MIXEL_CODE": str(expected),
                           "RUNNER_TEMP": (root / "runner temporary étape 🚀").as_posix(),
                           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
                    result = subprocess.run([shell(), "-e", "-o", "pipefail", "-c", prefix + script], env=env,
                                            capture_output=True, text=True, encoding="utf-8", timeout=15)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    calls = [json.loads(line) for line in (root / "calls").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual([item[0] for item in calls], ordered[:position + 1],
                                     "Failure executed a later source/native command")
                    if failure == ordered[-1]:
                        self.assertEqual(calls[-1][1:3], ["--app", "rustdesk"] if workflow == self.build else
                                         ["--app", env["RUNNER_TEMP"] + "/mixel-sdk-source/pipeline-lf"])
                        self.assertEqual("--flutter" in calls[-1], workflow == self.build)

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
                                        ("test-support-macos-termination-support.py", 46, 2),
                                        ("test-support-flutter-sdk-compatibility.py", 42, 3)):
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


class FlutterSdkEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('mixel_sdk_evidence_controls',
            ROOT / 'scripts/test-support-flutter-sdk-compatibility.py')
        cls.suite = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.suite)

    def test_actual_successful_unicode_child_retains_exact_streams_and_command(self):
        with tempfile.TemporaryDirectory(prefix='mixel SDK evidence \u00e4 ') as directory:
            out = Path(directory)
            command = [sys.executable, '-c', "import sys; print('complete \\u2192'); print('stderr \\u00e4', file=sys.stderr)"]
            value = self.suite.run(command, out, out, 'success')
            self.assertEqual(value, 'complete \u2192\n')
            self.assertEqual((out / 'success.stdout.log').read_bytes(), ('complete \u2192' + os.linesep).encode())
            self.assertEqual((out / 'success.stderr.log').read_bytes(), ('stderr \u00e4' + os.linesep).encode())
            self.assertEqual((out / 'success.log').read_bytes(), ('complete \u2192' + os.linesep + 'stderr \u00e4' + os.linesep).encode())
            proof = json.loads((out / 'success.command.json').read_text())
            self.assertEqual(proof['command'], command)
            self.assertEqual(proof['cwd'], str(out.resolve()))
            self.assertEqual((proof['status'], proof['returncode']), ('completed', 0))

    def test_actual_nonzero_child_is_fatal_and_both_streams_survive(self):
        import contextlib
        import hashlib
        import io
        with tempfile.TemporaryDirectory(prefix='mixel SDK failed ') as directory:
            out = Path(directory)
            command = [sys.executable, '-c', "import sys; print('unqualified stdout \\u2192'); print('unqualified stderr \\u00e4', file=sys.stderr); sys.exit(17)"]
            visible = io.StringIO()
            with contextlib.redirect_stderr(visible), self.assertRaises(AssertionError):
                self.suite.run(command, out, out, 'nonzero')
            stdout, stderr = ('unqualified stdout \u2192' + os.linesep).encode(), ('unqualified stderr \u00e4' + os.linesep).encode()
            self.assertEqual((out / 'nonzero.stdout.log').read_bytes(), stdout)
            self.assertEqual((out / 'nonzero.stderr.log').read_bytes(), stderr)
            proof = json.loads((out / 'nonzero.command.json').read_text())
            self.assertEqual((proof['command'], proof['status'], proof['returncode']), (command, 'completed', 17))
            self.assertEqual(proof['stdout_sha256'], hashlib.sha256(stdout).hexdigest())
            self.assertEqual(proof['stderr_sha256'], hashlib.sha256(stderr).hexdigest())
            self.assertIn('UNQUALIFIED: nonzero', visible.getvalue())
            self.assertIn(stdout.decode().strip(), visible.getvalue())
            self.assertIn(stderr.decode().strip(), visible.getvalue())

    def test_actual_timeout_retains_partial_streams_and_remains_timeout(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory(prefix='mixel SDK timeout ') as directory:
            out = Path(directory)
            command = [sys.executable, '-c', "import sys,time; print('partial stdout \\u2192', flush=True); print('partial stderr \\u00e4', file=sys.stderr, flush=True); time.sleep(30)"]
            visible = io.StringIO()
            with contextlib.redirect_stderr(visible), self.assertRaises(subprocess.TimeoutExpired):
                self.suite.run(command, out, out, 'timeout', timeout=2)
            self.assertEqual((out / 'timeout.stdout.log').read_bytes(), ('partial stdout \u2192' + os.linesep).encode())
            self.assertEqual((out / 'timeout.stderr.log').read_bytes(), ('partial stderr \u00e4' + os.linesep).encode())
            proof = json.loads((out / 'timeout.command.json').read_text())
            self.assertEqual((proof['command'], proof['status'], proof['returncode'], proof['timeout_seconds']),
                             (command, 'timed-out', None, 2))
            self.assertIn('UNQUALIFIED: timeout; timed-out', visible.getvalue())

    def test_actual_missing_executable_retains_launch_failure_and_remains_fatal(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory(prefix='mixel SDK launch ') as directory:
            out = Path(directory)
            command = [str(out / 'nonexistent executable')]
            visible = io.StringIO()
            with contextlib.redirect_stderr(visible), self.assertRaises(OSError):
                self.suite.run(command, out, out, 'launch')
            proof = json.loads((out / 'launch.command.json').read_text())
            self.assertEqual((proof['command'], proof['status'], proof['returncode']), (command, 'launch-failed', None))
            self.assertEqual((out / 'launch.stdout.log').read_bytes(), b'')
            self.assertTrue((out / 'launch.stderr.log').read_bytes())
            self.assertIn('UNQUALIFIED: launch; launch-failed', visible.getvalue())

    def test_actual_raw_crlf_and_lone_cr_streams_preserve_old_text_return_api(self):
        with tempfile.TemporaryDirectory(prefix='mixel SDK newline ') as directory:
            out = Path(directory)
            stdout = b'first\r\nsecond\rthird\n'
            stderr = b'error\r\nsecond\rthird\n'
            command = [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(' + repr(stdout) + '); sys.stderr.buffer.write(' + repr(stderr) + ')']
            value = self.suite.run(command, out, out, 'newlines')
            self.assertEqual(value, 'first\nsecond\nthird\n')
            self.assertEqual((out / 'newlines.stdout.log').read_bytes(), stdout)
            self.assertEqual((out / 'newlines.stderr.log').read_bytes(), stderr)

    def test_actual_invalid_utf8_in_either_stream_retains_both_and_remains_fatal(self):
        import contextlib
        import hashlib
        import io
        with tempfile.TemporaryDirectory(prefix='mixel SDK decode ') as directory:
            out = Path(directory)
            for label, stdout, stderr in (('bad-stdout', b'out\xff', b'exact stderr'),
                                          ('bad-stderr', b'exact stdout', b'err\xff')):
                with self.subTest(stream=label):
                    command = [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(' + repr(stdout) + '); sys.stderr.buffer.write(' + repr(stderr) + ')']
                    visible = io.StringIO()
                    with contextlib.redirect_stderr(visible), self.assertRaises(UnicodeDecodeError):
                        self.suite.run(command, out, out, label)
                    self.assertEqual((out / (label + '.stdout.log')).read_bytes(), stdout)
                    self.assertEqual((out / (label + '.stderr.log')).read_bytes(), stderr)
                    self.assertEqual((out / (label + '.log')).read_bytes(), stdout + stderr)
                    proof = json.loads((out / (label + '.command.json')).read_text())
                    self.assertEqual((proof['command'], proof['status'], proof['returncode']), (command, 'decode-failed', 0))
                    self.assertEqual(proof['stdout_sha256'], hashlib.sha256(stdout).hexdigest())
                    self.assertEqual(proof['stderr_sha256'], hashlib.sha256(stderr).hexdigest())
                    self.assertIn('UNQUALIFIED: ' + label + '; decode-failed', visible.getvalue())

    def test_explicit_main_output_survives_fatal_child_unwind(self):
        from unittest import mock
        import contextlib
        import io
        with tempfile.TemporaryDirectory(prefix='mixel SDK main ') as directory:
            root = Path(directory)
            app, upstream, out = root / 'app', root / 'upstream', root / 'retained'
            app.mkdir(); upstream.mkdir()
            # These stand-ins isolate main's ownership/cleanup boundary. The
            # failure and retained output come from an actual child process.
            def fail(output, *unused):
                self.suite.run([sys.executable, '-c', "import sys; print('retained after unwind'); sys.exit(19)"], root, output, 'main-failure')
            argv = ['sdk-controls', '--app', str(app), '--source', str(upstream), '--output', str(out), '--flutter', str(root / 'fixture-sdk')]
            with mock.patch.object(sys, 'argv', argv), mock.patch.object(self.suite, 'source_snapshot', return_value={}), \
                 mock.patch.object(self.suite, 'archive_eol_controls'), mock.patch.object(self.suite, 'materialization'), \
                 mock.patch.object(self.suite, 'previous_helper_controls'), mock.patch.object(self.suite, 'host_portability_controls'), \
                 mock.patch.object(self.suite, 'flutter_controls', side_effect=fail), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(AssertionError):
                self.suite.main()
            self.assertEqual((out / 'main-failure.stdout.log').read_bytes(), ('retained after unwind' + os.linesep).encode())
            self.assertEqual(json.loads((out / 'main-failure.command.json').read_text())['returncode'], 19)


class FlutterSdkRetentionWorkflowTests(unittest.TestCase):
    def test_every_sdk_caller_has_explicit_owned_output_and_immediate_always_log_upload(self):
        cases = (("build.yml", "linux", "Preflight generated support source and reporter"),
                 ("build.yml", "macos", "Preflight generated support source and reporter"),
                 ("preflight.yml", "upstream-source", "Exercise actual source patches and support reporter"),
                 ("preflight.yml", "macos-runtime-harness", "Prove exact SDK source compatibility and composed branding"))
        wanted = ['${{ runner.temp }}/mixel-sdk-compatibility/*.log',
                  '${{ runner.temp }}/mixel-sdk-compatibility/*.command.json']
        for workflow, role, stage in cases:
            body = job((ROOT / '.github/workflows' / workflow).read_text(), role)
            gate = step(body, stage)
            self.assertEqual(gate.count('--output "$RUNNER_TEMP/mixel-sdk-compatibility"'), 1)
            self.assertNotIn('continue-on-error:', gate)
            upload = step(body, 'Preserve SDK compatibility diagnostics')
            ordered = steps(body)
            self.assertEqual(ordered.index(upload), ordered.index(gate) + 1)
            self.assertIn('        if: always()\n', upload)
            self.assertIn('uses: actions/upload-artifact@v6', upload)
            paths = [line.strip() for line in upload.splitlines() if line.strip().startswith('${{ runner.temp }}/')]
            self.assertEqual(paths, wanted)
            self.assertNotIn('actual-app', upload)
        dispatcher = (ROOT / 'scripts/test-windows-generated-source.sh').read_text()
        calls = [line for line in dispatcher.splitlines() if 'python scripts/test-support-flutter-sdk-compatibility.py --app ' in line]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call.count('--output "$RUNNER_TEMP/mixel-sdk-compatibility"'), 1)
            self.assertIn('--bash "$mixel_analyzer_bash"', call)
        self.assertIn('--app "$RDREPO" --flutter "$(command -v flutter)"', calls[0])
        for workflow, role, stage in (("build.yml", "windows", "Preflight generated support source and reporter"),
                                      ("preflight.yml", "windows-packaging", "Exercise complete Windows generated source, keep-awake and native branding")):
            body = job((ROOT / '.github/workflows' / workflow).read_text(), role)
            gate, upload = step(body, stage), step(body, 'Preserve Windows SDK source diagnostics')
            self.assertEqual(steps(body).index(upload), steps(body).index(gate) + 1)
            self.assertIn('        if: always()\n', upload)
            paths = [line.strip() for line in upload.splitlines() if line.strip().startswith('${{ runner.temp }}/')]
            self.assertEqual(paths, ['${{ runner.temp }}/mixel-sdk-source/*.log',
                                     '${{ runner.temp }}/mixel-sdk-source/*.timing.json'] + wanted)

    def test_exact_retention_inverse_refuses_missing_changed_duplicate_and_moved_hunks(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('sdk_retention_projection', ROOT / 'scripts/file_dialog_workflow_projection.py')
        projection = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(projection)
        for workflow in ('build.yml', 'preflight.yml'):
            text = (ROOT / '.github/workflows' / workflow).read_text()
            recovered = projection.inverse_sdk_compatibility_retention(text, workflow)
            self.assertNotIn('mixel-sdk-compatibility', recovered)
            for role, before, addition, after in projection.sdk_compatibility_workflow_projections(workflow):
                changed = addition.replace('mixel-sdk-compatibility', 'foreign-sdk-output', 1)
                exact = before + addition + after
                for kind, mutant in (('missing', text.replace(addition, '', 1)),
                                     ('changed', text.replace(addition, changed, 1)),
                                     ('duplicate', text.replace(addition, addition + addition, 1)),
                                     ('moved', text.replace(addition, '', 1) + addition),
                                     ('preceding', text.replace(exact, before + '# foreign\n' + addition + after, 1))):
                    with self.subTest(workflow=workflow, role=role, mutation=kind):
                        with self.assertRaises(AssertionError):
                            projection.inverse_sdk_compatibility_retention(mutant, workflow)
        with self.assertRaises(AssertionError):
            projection.inverse_sdk_compatibility_retention('', 'foreign.yml')


if __name__ == "__main__":
    unittest.main(verbosity=2)
