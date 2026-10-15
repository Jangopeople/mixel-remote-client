#!/usr/bin/env python3
"""Exercise the unpublished continuation request and mandatory source release gate."""
import ast
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/validate-windows-preflight-build.py'
SPEC = importlib.util.spec_from_file_location('windows_preflight_build', SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
SAFE = {'COLLECT_WINDOWS_PREFLIGHT_FAILURE': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'PUBLISH_REQUESTED': 'false', 'INPUT_ONLY': 'windows', 'WINDOWS_RELEASE_GATE': 'false',
        'RUNTIME_RUN_ID': '', 'RUNTIME_LINUX_RUN': '', 'RUNTIME_MACOS_RUN': ''}


def job(text, name):
    match = re.search(r'^  ' + re.escape(name) + r':\n(.*?)(?=^  [a-z][a-z0-9-]*:|\Z)', text, re.M | re.S)
    if not match:
        raise AssertionError('Required workflow job missing: ' + name)
    return match.group(1)


def expression_value(expression, context):
    """Evaluate only Boolean/comparison expressions from the actual workflow."""
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    expression = re.sub(r"'[^']*'|[A-Za-z_][A-Za-z0-9_.-]*",
                        lambda m: m[0] if m[0].startswith("'") else repr(context[m[0]]), expression)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', ' not ', expression).strip()
    tree = ast.parse(expression, mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
               ast.Compare, ast.Eq, ast.NotEq, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError('Unexpected conditional operation')
    # Actions compares strings without case sensitivity. These workflow
    # predicates use only Boolean logic and string-to-string comparisons.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = node.value.casefold()
    return bool(eval(compile(tree, '<actual-workflow-condition>', 'eval'), {'__builtins__': {}}, {}))


class WindowsPreflightBuildTests(unittest.TestCase):
    def run_cli(self, command, environment, output):
        system = {name: os.environ[name] for name in ('PATH', 'SYSTEMROOT', 'WINDIR') if name in os.environ}
        return subprocess.run([sys.executable, str(SCRIPT), command],
                              env={**system, **environment, 'GITHUB_OUTPUT': str(output)},
                              capture_output=True, text=True, timeout=10)

    def test_only_explicit_safe_request_enables_continuation(self):
        self.assertTrue(GATE.validate_request(SAFE))
        for disabled in ({}, {'COLLECT_WINDOWS_PREFLIGHT_FAILURE': ''},
                         {'COLLECT_WINDOWS_PREFLIGHT_FAILURE': 'false'}):
            self.assertFalse(GATE.validate_request(disabled))
        for value in ('TRUE', '1', 'yes', ' true', 'false ', None, True, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GATE.validate_request({**SAFE, 'COLLECT_WINDOWS_PREFLIGHT_FAILURE': value})

    def test_invalid_request_cli_rejects_before_writing_success_output(self):
        changes = [{'GITHUB_EVENT_NAME': value} for value in ('push', 'pull_request', '')]
        changes += [{'PUBLISH_REQUESTED': value} for value in ('true', '', 'FALSE')]
        changes += [{'INPUT_ONLY': value} for value in ('', 'linux', 'macos', 'windows,linux',
                                                       'windows,windows', ' windows', 'windows ', 'WINDOWS')]
        changes += [{'WINDOWS_RELEASE_GATE': value} for value in ('true', '', 'FALSE')]
        changes += [{name: '1234'} for name in ('RUNTIME_RUN_ID', 'RUNTIME_LINUX_RUN', 'RUNTIME_MACOS_RUN')]
        changes += [{'COLLECT_WINDOWS_PREFLIGHT_FAILURE': value} for value in ('TRUE', '1', ' true')]
        with tempfile.TemporaryDirectory(prefix='mixel-preflight-request-controls-') as folder:
            output = Path(folder) / 'owned-output'
            for change in changes:
                output.write_text('untouched\n', encoding='utf-8')
                result = self.run_cli('request', {**SAFE, **change}, output)
                with self.subTest(change=change):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')
                    self.assertTrue(result.stderr.startswith('ERROR: '))
                    self.assertEqual(output.read_text(encoding='utf-8'), 'untouched\n')

    def test_successful_request_cli_exports_only_validated_boolean(self):
        with tempfile.TemporaryDirectory(prefix='mixel-preflight-output-controls-') as folder:
            output = Path(folder) / 'owned-output'
            for enabled in ('true', 'false', ''):
                output.write_text('', encoding='utf-8')
                result = self.run_cli('request', {**SAFE, 'COLLECT_WINDOWS_PREFLIGHT_FAILURE': enabled}, output)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(encoding='utf-8'),
                                 'collect_windows_preflight_failure=' + str(enabled == 'true').lower() + '\n')

    def test_actual_outcome_cli_refuses_failure_skipped_unknown_and_invalid_request(self):
        with tempfile.TemporaryDirectory(prefix='mixel-preflight-outcome-controls-') as folder:
            output = Path(folder) / 'owned-output'
            output.write_text('untouched\n', encoding='utf-8')
            for enabled in ('true', 'false'):
                for outcome in ('failure', 'skipped', 'cancelled', '', 'unknown', 'SUCCESS'):
                    result = self.run_cli('outcome', {**SAFE, 'COLLECT_WINDOWS_PREFLIGHT_FAILURE': enabled,
                                                     'SOURCE_PREFLIGHT_OUTCOME': outcome}, output)
                    with self.subTest(enabled=enabled, outcome=outcome):
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')
                result = self.run_cli('outcome', {**SAFE, 'COLLECT_WINDOWS_PREFLIGHT_FAILURE': enabled,
                                                 'SOURCE_PREFLIGHT_OUTCOME': 'success'}, output)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli('outcome', {**SAFE, 'PUBLISH_REQUESTED': 'true',
                                             'SOURCE_PREFLIGHT_OUTCOME': 'success'}, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(encoding='utf-8'), 'untouched\n')

    def test_actual_config_and_runtime_conditions_reject_combined_opt_in(self):
        text = (ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        config = re.search(r'^    if: (.+)$', job(text, 'config'), re.M)[1]
        context = {'inputs.collect_windows_preflight_failure': False,
                   'inputs.runtime_windows_private_standard95': False,
                   'inputs.runtime_windows_release_gate': False, 'inputs.runtime_artifact_run': '',
                   'github.event.inputs.runtime_artifact_run': '',
                   'github.event.inputs.runtime_linux_artifact_run': '',
                   'github.event.inputs.runtime_macos_artifact_run': ''}
        self.assertTrue(expression_value(config, context))
        for name, field in (('runtime', 'runtime_artifact_run'), ('runtime-linux', 'runtime_linux_artifact_run'),
                            ('runtime-macos', 'runtime_macos_artifact_run')):
            condition = re.search(r'^    if: (.+)$', job(text, name), re.M)[1]
            artifact = {**context, 'github.event.inputs.' + field: '1234'}
            if field == 'runtime_artifact_run':
                artifact['inputs.runtime_artifact_run'] = '1234'
            self.assertFalse(expression_value(config, artifact))
            self.assertTrue(expression_value(condition, artifact))
            invalid = {**artifact, 'inputs.collect_windows_preflight_failure': True}
            self.assertTrue(expression_value(config, invalid))
            self.assertFalse(expression_value(condition, invalid))

    def test_only_windows_preflight_can_continue_and_final_gate_precedes_customer_upload(self):
        text = (ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertRegex(text, r'collect_windows_preflight_failure:\n[^\n]+\n        required: false\n        type: boolean\n        default: false')
        self.assertNotIn('continue-on-error:', job(text, 'linux'))
        self.assertNotIn('continue-on-error:', job(text, 'macos'))
        windows = job(text, 'windows')
        self.assertEqual(windows.count('continue-on-error:'), 1)
        preflight = re.search(r'      - name: Preflight generated support source and reporter\n(.*?)(?=\n      - )', windows, re.S)[1]
        self.assertIn('id: preflight\n', preflight)
        self.assertIn('run: bash scripts/test-windows-generated-source.sh', preflight)
        condition = re.search(r'continue-on-error: (.+)', preflight)[1]
        for value in ('', 'false', 'TRUE', 'true'):
            self.assertEqual(expression_value(condition, {'needs.config.outputs.collect_windows_preflight_failure': value}),
                             value.casefold() == 'true')
        final = re.search(r'      - name: Require the original Windows source preflight before customer artifact upload\n(.*?)(?=\n      - )', windows, re.S)[1]
        self.assertIn('if: ${{ always() && !cancelled() }}', final)
        self.assertIn('SOURCE_PREFLIGHT_OUTCOME: ${{ steps.preflight.outcome }}', final)
        self.assertNotIn('steps.preflight.conclusion', final)
        self.assertIn("python scripts/validate-windows-preflight-build.py outcome\n          if ($LASTEXITCODE -ne 0)", final)
        self.assertLess(windows.index('name: windows-msix-payload'), windows.index('Exercise the signed QuickSupport download'))
        self.assertLess(windows.index('Exercise the signed QuickSupport download'), windows.index('Require the original Windows source preflight'))
        self.assertLess(windows.index('Require the original Windows source preflight'), windows.index('name: windows\n'))
        self.assertIn('!inputs.collect_windows_preflight_failure', job(text, 'upload'))

    def test_windows_sdk_source_diagnostics_are_always_owned_log_files_only(self):
        module_names = ('test-support-launch-macos-concurrency-controls.py', 'test-standard-valid-qa.py')
        modules = []
        for filename in module_names:
            spec = importlib.util.spec_from_file_location('sdk_diagnostics_' + filename.replace('-', '_'), ROOT / 'scripts' / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
        upload = modules[0].WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD
        self.assertEqual(upload, modules[1].WINDOWS_SDK_SOURCE_DIAGNOSTICS_UPLOAD)
        self.assertIn('        if: always()\n', upload)
        self.assertIn('        uses: actions/upload-artifact@v6\n', upload)
        self.assertIn('          name: windows-sdk-source-diagnostics\n', upload)
        self.assertIn('          retention-days: 14\n', upload)
        self.assertIn('          if-no-files-found: ignore\n', upload)
        paths = [line.strip() for line in upload.splitlines() if line.startswith('            ')]
        self.assertEqual(paths, ['${{ runner.temp }}/mixel-sdk-source/*.log', '${{ runner.temp }}/mixel-sdk-source/*.timing.json'])
        for workflow, job_name in (('build.yml', 'windows'), ('preflight.yml', 'windows-packaging')):
            text = (ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8')
            self.assertEqual(text.count(upload), 1)
            self.assertEqual(job(text, job_name).count(upload), 1)
            self.assertIn('        run: bash scripts/test-windows-generated-source.sh\n' + upload, text)
            for module in modules:
                self.assertEqual(module.inverse_windows_sdk_source_diagnostics(text, workflow), text.replace(upload, '', 1))
                mutants = [text.replace(upload, '', 1), text + upload, text.replace(upload, '', 1) + upload]
                for before, after in (('if: always()', 'if: success()'), ('retention-days: 14', 'retention-days: 13'), ('if-no-files-found: ignore', 'if-no-files-found: error'), ('name: windows-sdk-source-diagnostics', 'name: other-diagnostics'), ('mixel-sdk-source/*.log', 'mixel-sdk-source/**/*.log'), ('mixel-sdk-source/*.log', 'mixel-sdk-source/*.zip'), ('mixel-sdk-source/*.timing.json', 'mixel-sdk-source/*.json'), ('${{ runner.temp }}/mixel-sdk-source/', '${{ github.workspace }}/')):
                    mutants.append(text.replace(upload, upload.replace(before, after), 1))
                for mutation in mutants:
                    with self.subTest(workflow=workflow, inverse=module.__name__), self.assertRaises(AssertionError):
                        module.inverse_windows_sdk_source_diagnostics(mutation, workflow)

    def test_existing_compiled_signed_customer_and_quicksupport_commands_remain_mandatory(self):
        windows = job((ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8'), 'windows')
        commands = ('python scripts/test-compiled-support-guard.py',
                    './scripts/verify-windows-payload.ps1 -Payload store-payload',
                    './scripts/test-support-launch-windows.ps1 -Executable store-payload/Mixel-Remote.exe',
                    './scripts/test-support-launch-windows.ps1 -Executable dist/Mixel-Remote-Support-Windows.exe -Portable -ExpectedPayload store-payload',
                    './scripts/test-support-launch-windows.ps1 -Executable dist/Mixel-Remote-QS.exe -Portable -QuickSupport -ExpectedPayload store-payload')
        for command in commands:
            matches = [step for step in re.split(r'(?m)^      - ', windows) if command in step]
            with self.subTest(command=command):
                self.assertEqual(len(matches), 1)
                self.assertNotIn('continue-on-error:', matches[0])
                self.assertNotIn('if:', matches[0])

    def test_diagnostic_installers_require_validated_opt_in_and_completed_signature_verification(self):
        text = (ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        windows = job(text, 'windows')
        verifier = re.search(r'      - name: Verify Windows Authenticode signatures\n(.*?)(?=\n      - )', windows, re.S)[1]
        self.assertIn('id: windows_authenticode\n', verifier)
        self.assertIn("if ($signature.Status -ne 'Valid')", verifier)
        self.assertNotIn('if:', verifier)
        self.assertNotIn('continue-on-error:', verifier)
        diagnostic = re.search(r'      - name: Retain verified signed Windows installers for unpublished diagnosis\n(.*?)(?=\n      - )', windows, re.S)[1]
        self.assertIn('name: windows-unpublished-diagnostic\n', diagnostic)
        self.assertIn('path: dist/\n', diagnostic)
        self.assertIn('if-no-files-found: error', diagnostic)
        condition = re.search(r'^        if: (.+)$', diagnostic, re.M)[1]
        self.assertIn('always()', condition)
        self.assertIn('!cancelled()', condition)
        condition = condition.replace('always()', 'observed_always').replace('cancelled()', 'observed_cancelled')
        for enabled in ('true', 'false', '', 'TRUE', '1', ' true', 'true '):
            for signature in ('success', 'failure', 'skipped', 'cancelled', '', 'SUCCESS'):
                for cancelled in (False, True):
                    context = {'observed_always': True, 'observed_cancelled': cancelled,
                               'needs.config.outputs.collect_windows_preflight_failure': enabled,
                               'steps.windows_authenticode.outcome': signature}
                    with self.subTest(enabled=enabled, signature=signature, cancelled=cancelled):
                        self.assertEqual(expression_value(condition, context),
                                         enabled.casefold() == 'true' and signature.casefold() == 'success' and not cancelled)
        self.assertLess(windows.index('Verify Windows Authenticode signatures'),
                        windows.index('Retain verified signed Windows installers'))
        self.assertLess(windows.index('Require the original Windows source preflight'),
                        windows.index('name: windows-unpublished-diagnostic'))
        self.assertLess(windows.index('name: windows-unpublished-diagnostic'), windows.index('name: windows\n'))
        self.assertIn('!inputs.collect_windows_preflight_failure', job(text, 'upload'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
