#!/usr/bin/env python3
"""Exercise release selection, fail-closed staging, and portable build failures."""
import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import textwrap
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


class WorkflowPythonEncodingTests(unittest.TestCase):
    def test_actual_unicode_helper_status_and_fatal_result_with_workflow_environment(self):
        statements = []
        for helper_name, marker in (("patch-support-macos-window.py", "patched pinned Mac subwindow lifecycle"),
                                    ("patch-support-macos-cleanup.py", "patched bounded Mac page cleanup")):
            source = (ROOT / "scripts" / helper_name).read_text(encoding="utf-8")
            calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "print"
                     and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                     and isinstance(node.args[0].value, str) and marker in node.args[0].value]
            self.assertEqual(len(calls), 1, helper_name)
            self.assertIn("\u2192", calls[0].args[0].value)
            statements.append((helper_name, ast.get_source_segment(source, calls[0]), calls[0].args[0].value))
        payload = "pr\u00e9flight \U0001f680"
        with tempfile.TemporaryDirectory(prefix="mixel-python-workflow-") as directory:
            old_environment = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
            scripts = []
            for helper_name, statement, status in statements:
                code = statement + "\nimport sys\n" + \
                    "print(" + repr(payload) + ")\nprint(sys.stdout.encoding)\n" + \
                    "sys.exit(int(sys.argv[1]))\n"
                script = Path(directory) / helper_name
                script.write_text(code, encoding="utf-8")
                scripts.append((script, status))
                original = subprocess.run([sys.executable, str(script), "0"], env=old_environment,
                                          capture_output=True, timeout=15)
                self.assertNotEqual(original.returncode, 0, helper_name)
                self.assertIn(b"UnicodeEncodeError", original.stderr)
                self.assertIn(b"\\u2192", original.stderr)
            for workflow_name in ("preflight.yml", "build.yml"):
                workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
                blocks = re.findall(r"^env:\n((?:[ \t]+[^\n]*\n)+)", workflow, flags=re.M)
                self.assertEqual(len(blocks), 1, workflow_name + " requires one inherited environment")
                encoding_environment = {}
                for name, expected in (("PYTHONUTF8", "1"), ("PYTHONIOENCODING", "utf-8")):
                    values = re.findall(r"^  " + name + r': "([^"\n]+)"$', blocks[0], flags=re.M)
                    self.assertEqual(values, [expected], workflow_name + ": " + name)
                    encoding_environment[name] = values[0]
                environment = {**old_environment, **encoding_environment}
                for script, status in scripts:
                    for exit_code in (0, 23):
                        with self.subTest(workflow=workflow_name, helper=script.name, exit_code=exit_code):
                            result = subprocess.run([sys.executable, str(script), str(exit_code)], env=environment,
                                                    capture_output=True, timeout=15)
                            self.assertEqual(result.returncode, exit_code)
                            self.assertEqual(result.stdout.decode("utf-8"), status + "\n" + payload + "\nutf-8\n")
                            self.assertEqual(result.stderr, b"")
                self.assertEqual(old_environment["PYTHONIOENCODING"], "cp1252")


class WindowsEarlyLeaseGateTests(unittest.TestCase):
    def test_early_private_gate_exact_context_and_closed_source_inverses(self):
        workflow = (ROOT / '.github/workflows/preflight.yml').read_text()
        before = "      - uses: dtolnay/rust-toolchain@v1\n        with:\n          toolchain: '1.75'\n"
        addition = "      - name: Prove early Windows acknowledgment publication\n        timeout-minutes: 3\n        shell: pwsh\n        run: |\n          ./scripts/activate-windows-msvc.ps1 -ExportToGitHubEnvironment\n          python scripts/test-support-lease-ack.py\n          if ($LASTEXITCODE -ne 0) { throw 'Actual Windows acknowledgment controls failed.' }\n      - name: Prove early actual standard-token lease lifecycle\n        timeout-minutes: 5\n        shell: pwsh\n        run: |\n          $env:MIXEL_HOSTED_OWNED_QA_NAMESPACE='1'\n          ./scripts/invoke-hosted-standard-qa-windows.ps1 -EvidenceDirectory (Join-Path $env:RUNNER_TEMP 'mixel-early-standard-qa')\n      - name: Preserve early Windows standard-token lifecycle evidence\n        if: always()\n        uses: actions/upload-artifact@v6\n        with:\n          name: windows-lease-ack-early\n          path: ${{ runner.temp }}/mixel-early-standard-qa/\n          if-no-files-found: ignore\n          retention-days: 14\n"
        after = '      - uses: subosito/flutter-action@v2\n'
        self.assertEqual(workflow.count(before + addition + after), 1)
        job = workflow.split('\n  windows-packaging:\n', 1)[1].split('\n  portable-standard-lease-qa:', 1)[0]
        self.assertLess(job.index('Prove early Windows acknowledgment'), job.index('subosito/flutter-action'))
        self.assertIn('timeout-minutes: 3', addition)
        self.assertIn('timeout-minutes: 5', addition)
        self.assertLess(job.index('Prove early actual standard-token lease'), job.index('subosito/flutter-action'))
        self.assertIn('invoke-hosted-standard-qa-windows.ps1 -EvidenceDirectory', addition)
        self.assertLess(job.index('Prove early Windows acknowledgment'), job.index('Fetch pinned complete Windows source'))
        self.assertIn('Qualify actual installed MSVC with a spaced Unicode temporary path', job)
        self.assertIn('bash scripts/test-windows-generated-source.sh', job)
        for filename in ('test-support-launch-macos-concurrency-controls.py', 'test-standard-valid-qa.py'):
            text = (ROOT / 'scripts' / filename).read_text()
            tree = ast.parse(text)
            nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'inverse_early_windows_lease_gate']
            self.assertEqual(len(nodes), 1)
            namespace = {}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)
            inverse = namespace['inverse_early_windows_lease_gate']
            restored = inverse(workflow)
            import hashlib
            self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(), 'c40f88b5c6c36e9c5fd1685e95c616a616360b1768a392f1c288b2e29d63c0b5')
            for changed in (workflow.replace(addition, '', 1), workflow.replace(addition, addition+addition, 1),
                            workflow.replace(addition, addition.replace('test-support-lease-ack.py','foreign.py'),1),
                            workflow.replace(before+addition+after, before+after,1)+addition,
                            workflow.replace(before+addition+after,before+'# disjoint\n'+addition+after,1)):
                with self.assertRaises(AssertionError):
                    inverse(changed)
        print('PASS: early native Windows gate is exact after Rust/before Flutter; both source-only inverses recover whole6c workflow and reject missing, moved, duplicate, drift and disjoint gates; existing later checks remain.')


class WindowsSourceBudgetTests(unittest.TestCase):
    def test_mandatory_source_budget_covers_the_shipping_source_budget(self):
        budgets = {}
        for workflow_name, job_name in (("preflight.yml", "windows-packaging"),
                                        ("build.yml", "windows")):
            workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
            jobs = re.findall(r"^  " + re.escape(job_name) + r":\n(.*?)(?=^  [\w-]+:|\Z)",
                              workflow, flags=re.M | re.S)
            self.assertEqual(len(jobs), 1, workflow_name)
            self.assertIn("        run: bash scripts/test-windows-generated-source.sh\n", jobs[0])
            limits = re.findall(r"^    timeout-minutes: ([0-9]+)$", jobs[0], flags=re.M)
            self.assertEqual(len(limits), 1, workflow_name)
            budgets[workflow_name] = int(limits[0])
        self.assertGreaterEqual(budgets["preflight.yml"], budgets["build.yml"],
                                "Mandatory Windows preflight must accommodate the shipping source checks")


class PinnedDependencyRetryTests(unittest.TestCase):
    def test_actual_platform_steps_retry_transient_failure_and_preserve_fatal_exit(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        scripts = re.findall(
            r"      - name: Install vcpkg deps\n(?:        shell: bash\n)?        run: \|\n((?:          .*(?:\n|$))+)",
            workflow,
        )
        self.assertEqual(len(scripts), 3)
        for script, triplet in zip(scripts, ("x64-linux", "arm64-osx", "x64-windows-static")):
            script = textwrap.dedent(script)
            script = re.sub(r"\$\{\{ matrix\.job\.arch .*?\}\}", triplet, script)
            for failures in (0, 1, 2, 3):
                with self.subTest(triplet=triplet, failures=failures), tempfile.TemporaryDirectory(prefix="mixel-pinned-retry-") as directory:
                    root = Path(directory)
                    binary = root / "bin"
                    binary.mkdir()
                    vcpkg = binary / "vcpkg"
                    vcpkg.write_text("""#!/bin/bash
set -e
count=0
if [[ -f "$MIXEL_RETRY_COUNT" ]]; then count=$(cat "$MIXEL_RETRY_COUNT"); fi
count=$((count + 1))
printf '%s' "$count" >"$MIXEL_RETRY_COUNT"
printf '%s\\n' "$*" >>"$MIXEL_RETRY_CALLS"
if [[ "$count" -le "$MIXEL_RETRY_FAILURES" ]]; then exit 1; fi
""", encoding="utf-8")
                    vcpkg.chmod(0o755)
                    sleep = binary / "sleep"
                    sleep.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >>"$MIXEL_RETRY_DELAYS"\n', encoding="utf-8")
                    sleep.chmod(0o755)
                    environment = {**os.environ, "VCPKG_ROOT": str(binary),
                                   "VCPKG_OVERLAY_TRIPLETS": str(root / "triplet overlays"),
                                   "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                   "MIXEL_RETRY_COUNT": str(root / "count"), "MIXEL_RETRY_CALLS": str(root / "calls"),
                                   "MIXEL_RETRY_DELAYS": str(root / "delays"), "MIXEL_RETRY_FAILURES": str(failures)}
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], env=environment,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1 if failures == 3 else 0)
                    expected_calls = min(failures + 1, 3)
                    self.assertEqual(int((root / "count").read_text()), expected_calls)
                    overlay = " --overlay-triplets=" + environment["VCPKG_OVERLAY_TRIPLETS"] if triplet == "arm64-osx" else ""
                    self.assertEqual((root / "calls").read_text().splitlines(),
                                     ["install" + overlay + " --triplet " + triplet + " libvpx libyuv opus aom libjpeg-turbo"] * expected_calls)
                    delays = (root / "delays").read_text().splitlines() if (root / "delays").exists() else []
                    self.assertEqual(delays, ["10", "20"][:expected_calls - 1])


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


def original_mac_wrapper_calls(workflow):
    prefixes = [['python3 ../scripts/test-support-launch-macos.py --app', 'python3 ../scripts/test-support-launch-macos-concurrency.py --original-desktop --app'], ['python3 ../scripts/test-support-launch-macos-tray.py --app', 'python3 ../scripts/test-support-launch-macos-concurrency.py --original-tray --app'], ['python3 scripts/test-support-launch-macos.py --app', 'python3 scripts/test-support-launch-macos-concurrency.py --original-desktop --app'], ['python3 scripts/test-support-launch-macos-tray.py --app', 'python3 scripts/test-support-launch-macos-concurrency.py --original-tray --app']]
    counts = [workflow.count(current) for prior, current in prefixes]
    if counts == [0] * len(prefixes):
        if any(workflow.count(prior) != 1 for prior, current in prefixes):
            raise AssertionError("Historical Mac callback callers changed")
        return workflow
    if counts != [1] * len(prefixes):
        raise AssertionError("Partial or duplicate thread-safe Mac wrapper calls")
    for prior, current in prefixes:
        workflow = workflow.replace(current, prior, 1)
    return workflow


class MacTrayProofPathTests(unittest.TestCase):
    """Run the two actual caller snippets and real main path guards, no native app."""

    def callers(self):
        workflow = original_mac_wrapper_calls((ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8"))
        producer = (
            'TRAY_PROOF_ROOT="$(cd ../runtime-diagnostics && pwd -P)"\n'
            'python3 ../scripts/test-support-launch-macos-tray.py --app "$APP_PATH" \\\n'
            '  --proofs "$TRAY_PROOF_ROOT/macos-${{ matrix.job.arch }}-tray"\n'
        )
        consumer = (
            'TRAY_PROOF_ROOT="$(cd runtime-diagnostics && pwd -P)"\n'
            'python3 scripts/test-support-launch-macos-tray.py --app "$APP_PATH" \\\n'
            '  --proofs "$TRAY_PROOF_ROOT/macos-${EXPECTED_ARCH}-tray"\n'
        )
        for snippet, indentation in ((producer, 12), (consumer, 10)):
            self.assertEqual(workflow.count(textwrap.indent(snippet, " " * indentation)), 1,
                             "Exact canonical Mac tray caller missing or duplicated")
        return (("producer", producer), ("consumer", consumer))

    def shell_call(self, snippet, cwd, app, shim, arch):
        import json
        import shutil
        self.assertEqual(os.name, "posix", "Mac proof path controls require the POSIX source gate")
        bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Actual Bash is required; path controls cannot be skipped")
        snippet = snippet.replace("${{ matrix.job.arch }}", arch)
        prefix = 'python3() { "$MIXEL_PROOF_PATH_PYTHON" "$MIXEL_PROOF_PATH_SHIM" "$@"; }\n'
        env = {**os.environ, "PWD": str(cwd), "APP_PATH": str(app), "EXPECTED_ARCH": arch,
               "MIXEL_PROOF_PATH_PYTHON": sys.executable, "MIXEL_PROOF_PATH_SHIM": str(shim)}
        result = subprocess.run([bash, "-e", "-o", "pipefail", "-c", prefix + snippet],
                                cwd=cwd, env=env, capture_output=True, text=True,
                                encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        rows = result.stdout.splitlines()
        self.assertEqual(len(rows), 1, "Caller must invoke the single tray helper exactly once")
        row = json.loads(rows[0])
        arguments = row["arguments"]
        self.assertEqual(len(arguments), 5)
        self.assertIn(arguments[0], ("../scripts/test-support-launch-macos-tray.py",
                                     "scripts/test-support-launch-macos-tray.py"))
        self.assertEqual(arguments[1:4], ["--app", str(app), "--proofs"])
        self.assertEqual(row["bash_pwd"], str(cwd), "Logical symlink cwd must reach the actual Bash")
        return Path(arguments[4])

    def guard(self, app, output, cwd):
        import json
        from unittest import mock
        controls = module("mixel_safe_tray_path_controls", "test-support-launch-macos-tray-controls.py")
        namespace = controls.extracted_namespace(ROOT / "scripts/test-support-launch-macos-tray.py")
        calls = []
        namespace["isolated_ci"] = lambda: None
        namespace["desktop"].SWIFT = "source-only helper placeholder"
        namespace["tray_swift"] = lambda value: value
        namespace["desktop"].compile_helper = lambda _: (calls.append("compile seam"), Path("/never-executed/helper"))[1]
        namespace["desktop"].safe_error = lambda error: str(error)
        def runtime(_app, _helper, facts):
            calls.append("runtime seam")
            facts["ownedProcessesStopped"] = True
        namespace["runtime"] = runtime
        previous = Path.cwd()
        try:
            os.chdir(cwd)
            with mock.patch.object(sys, "argv", ["source-only-path-control", "--app", str(app), "--proofs", str(output)]):
                status = namespace["main"]()
            absolute = output.absolute()
        finally:
            os.chdir(previous)
        facts = None
        if status == 0:
            facts = json.loads((absolute / "tray-runtime-safe-facts.json").read_bytes())
            self.assertTrue(facts["passed"])
            for name in ("customerAcceptTested", "remoteDesktopConnected",
                         "actualTrayDispatchObserved", "visibleGuiAndAttendedV2"):
                self.assertFalse(facts[name], "Source-only path controls must not claim native/consent success")
        return status, calls, namespace["sys"].stderr.getvalue(), absolute, facts

    def fixture(self, base):
        physical = base / "physical workspace with spaces"
        (physical / "rustdesk").mkdir(parents=True)
        (physical / "runtime-diagnostics").mkdir()
        app = physical / "empty source-only app"
        app.mkdir()
        linked = base / "logical workspace with spaces"
        linked.symlink_to(physical, target_is_directory=True)
        shim = base / "record-only-python.py"
        shim.write_text("import json, os, sys\nprint(json.dumps({'arguments': sys.argv[1:], 'bash_pwd': os.environ.get('PWD')}))\n",
                        encoding="utf-8")
        return physical, linked, app, shim

    def test_actual_both_callers_pass_real_path_guard_with_physical_and_symlinked_cwd(self):
        completed = 0
        for caller, snippet in self.callers():
            for symlinked in (False, True):
                for arch in ("x86_64", "aarch64"):
                    with self.subTest(caller=caller, symlinked_cwd=symlinked, arch=arch), tempfile.TemporaryDirectory(prefix="mixel-tray-caller-path-") as directory:
                        base = Path(directory).resolve()
                        physical, linked, app, shim = self.fixture(base)
                        cwd = linked if symlinked else physical
                        if caller == "producer":
                            cwd /= "rustdesk"
                        output = self.shell_call(snippet, cwd, app, shim, arch)
                        self.assertEqual(output, physical / "runtime-diagnostics" / ("macos-" + arch + "-tray"))
                        self.assertEqual(output.parent, output.parent.resolve())
                        status, calls, error, absolute, _ = self.guard(app, output, cwd)
                        self.assertEqual((status, calls, error), (0, ["compile seam", "runtime seam"], ""))
                        self.assertTrue(absolute.is_dir())
                        completed += 1
        self.assertEqual(completed, 8, "All eight caller/cwd/architecture cases must finish")
        print("PASS: both actual Bash Mac tray callers canonicalize physical/symlinked cwd for both architectures; real main accepts only new owned output, no native app/Accept claim", flush=True)

    def test_historical_producer_dotdot_is_rejected_before_owned_output_or_native_seams(self):
        completed = 0
        producer = dict(self.callers())["producer"]
        assignment = 'TRAY_PROOF_ROOT="$(cd ../runtime-diagnostics && pwd -P)"\n'
        current = '--proofs "$TRAY_PROOF_ROOT/macos-${{ matrix.job.arch }}-tray"'
        previous = '--proofs "../runtime-diagnostics/macos-${{ matrix.job.arch }}-tray"'
        self.assertEqual(producer.count(assignment), 1)
        self.assertEqual(producer.count(current), 1)
        historical = producer.replace(assignment, "", 1).replace(current, previous, 1)
        for symlinked in (False, True):
            with self.subTest(symlinked_cwd=symlinked), tempfile.TemporaryDirectory(prefix="mixel-tray-old-path-") as directory:
                physical, linked, app, shim = self.fixture(Path(directory).resolve())
                cwd = (linked if symlinked else physical) / "rustdesk"
                output = self.shell_call(historical, cwd, app, shim, "x86_64")
                self.assertEqual(str(output), "../runtime-diagnostics/macos-x86_64-tray")
                status, calls, error, absolute, facts = self.guard(app, output, cwd)
                self.assertEqual(status, 1)
                self.assertEqual(calls, [])
                self.assertIn("Tray proof parent is not a canonical directory", error)
                self.assertFalse(absolute.exists())
                self.assertIsNone(facts)
                self.assertEqual(list((physical / "runtime-diagnostics").iterdir()), [])
                completed += 1
        self.assertEqual(completed, 2, "Both historical caller cwd cases must finish")
        print("PASS: literal prior producer ../ path fails the real canonical-parent guard before output ownership, helper compilation or runtime", flush=True)

    def test_existing_output_and_direct_symlink_parent_still_refuse_without_writes(self):
        completed = 0
        for fault in ("collision", "symlink-parent", "symlink-leaf"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(prefix="mixel-tray-proof-refusal-") as directory:
                base = Path(directory).resolve()
                physical, _, app, _ = self.fixture(base)
                parent = physical / "runtime-diagnostics"
                output = parent / "owned-proof"
                if fault == "symlink-parent":
                    alias = base / "direct linked parent"
                    alias.symlink_to(parent, target_is_directory=True)
                    output = alias / "unowned-proof"
                else:
                    retained = parent / "retained" if fault == "symlink-leaf" else output
                    retained.mkdir()
                    sentinel = retained / "tray-runtime-safe-facts.json"
                    sentinel.write_bytes(b"already-owned facts must remain exact")
                    if fault == "symlink-leaf":
                        output.symlink_to(retained, target_is_directory=True)
                before = {str(path.relative_to(base)): path.read_bytes()
                          for path in base.rglob("*") if path.is_file() and not path.is_symlink()}
                status, calls, error, _, facts = self.guard(app, output, physical)
                self.assertEqual(status, 1)
                self.assertEqual(calls, [])
                self.assertIsNone(facts)
                if fault == "symlink-parent":
                    self.assertIn("Tray proof parent is not a canonical directory", error)
                    self.assertFalse((parent / "unowned-proof").exists())
                else:
                    self.assertEqual(sentinel.read_bytes(), b"already-owned facts must remain exact")
                after = {str(path.relative_to(base)): path.read_bytes()
                         for path in base.rglob("*") if path.is_file() and not path.is_symlink()}
                self.assertEqual(after, before)
                completed += 1
        self.assertEqual(completed, 3, "All collision/symlink refusals must finish")
        print("PASS: real tray main still refuses existing/symlink output and noncanonical direct symlink parents before writes or native seams", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
