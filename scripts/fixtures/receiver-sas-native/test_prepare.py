#!/usr/bin/env python3
"""Inert preparation/fixture integrity only; no Cargo, compiler or Windows API."""
from pathlib import Path
import hashlib
import importlib.util
import contextlib
import io
import json
import tempfile
import sys
import tomllib
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sas_native_prepare', HERE / 'prepare.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparationControls(unittest.TestCase):
    def test_std_only_actor_compiles_independently_with_exact_target_and_warnings(self):
        root = Path('owned space \u2603')
        linker = str(Path('exact MSVC \u2603') / 'link.exe')
        argv = module.actor_compile_command(root, linker)
        self.assertEqual(argv[:2], ['rustc', '+1.75.0'])
        self.assertIn('--edition=2021', argv)
        self.assertEqual(argv[argv.index('--target') + 1], 'x86_64-pc-windows-msvc')
        self.assertIn('-Dwarnings', argv)
        self.assertEqual(argv[argv.index('-C') + 1], 'linker=' + linker)
        self.assertEqual(argv[argv.index('-o') + 1], str(root / 'kernel_actor.exe'))
        self.assertEqual(argv[-1], str(root / 'harness/src/bin/kernel_actor.rs'))
        self.assertNotIn('cargo', argv)
        actor = (HERE / 'kernel-actor.rs').read_text()
        self.assertNotIn('hbb_common', actor)
        self.assertNotIn('tokio', actor)

    def test_failed_compiler_diagnostic_is_bounded_and_keeps_actual_error(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module.report_compiler_failure('03-build-owned-kernel-actor', 1,
                b'old-prefix-not-in-tail' + b'x' * 13000 + b'\nerror[E0001]: controlled compile refusal\xff')
        output = buffer.getvalue()
        self.assertIn('compiler exit=1', output)
        self.assertIn('error[E0001]: controlled compile refusal', output)
        self.assertNotIn('old-prefix-not-in-tail', output)
        self.assertLess(len(output), 12200)

    def test_default_preparation_has_exact_entire_leaf_and_no_process_calls(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(module.subprocess, 'run', side_effect=AssertionError('no process')):
            output, result = module.prepare(Path(temporary).resolve() / 'prepared')
            raw = module.LEAF.read_bytes()
            self.assertTrue((output / 'harness/src/sas_leaf_under_test.rs').read_bytes().startswith(raw + b'\n'))
            self.assertEqual(result['entireLeafPrefixBytes'], len(raw))
            self.assertEqual(result['preparedComponentCases'], 14)
            self.assertFalse(result['compiled'])
            self.assertFalse(result['SendSASInvoked'])
            self.assertEqual(json.loads((output / 'PREPARATION.json').read_text()), result)
            self.assertEqual((output / 'harness/Cargo.lock').read_bytes(), (HERE / 'source-Cargo.lock').read_bytes())

    def test_physical_crlf_production_prefix_is_preserved_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw = module.logical(module.LEAF.read_bytes()).replace(b'\n', b'\r\n')
            leaf = root / 'production.rs'
            leaf.write_bytes(raw)
            with patch.object(module, 'LEAF', leaf):
                output, result = module.prepare(root / 'prepared')
            self.assertTrue((output / 'harness/src/sas_leaf_under_test.rs').read_bytes().startswith(raw + b'\n'))
            self.assertEqual(result['entireLeafLogicalSHA256'], module.LEAF_SHA256)
            self.assertEqual(result['entireLeafPhysicalSHA256'], hashlib.sha256(raw).hexdigest())

    def test_drifted_or_linked_leaf_is_refused_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            leaf = root / 'leaf.rs'
            logical = module.logical(module.LEAF.read_bytes())
            for index, newline in enumerate((b'\n', b'\r\n')):
                output = root / ('drift-output-' + str(index))
                leaf.write_bytes(logical.replace(b'\n', newline) + b'// drift' + newline)
                with patch.object(module, 'LEAF', leaf), self.assertRaisesRegex(ValueError, 'pin drift'):
                    module.prepare(output)
                self.assertFalse(output.exists())
            leaf.write_bytes(logical.replace(b'\n', b'\r\n', 1))
            with patch.object(module, 'LEAF', leaf), self.assertRaisesRegex(ValueError, 'Uniform fixture source newlines'):
                module.prepare(root / 'mixed-output')
            self.assertFalse((root / 'mixed-output').exists())
            linked = root / 'linked.rs'
            linked.symlink_to(module.LEAF)
            with patch.object(module, 'LEAF', linked), self.assertRaisesRegex(ValueError, 'single-link'):
                module.prepare(root / 'linked-output')
            self.assertFalse((root / 'linked-output').exists())

    def test_existing_output_refuses_and_non_windows_run_never_spawns(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(module.subprocess, 'run', side_effect=AssertionError('no process')):
            output = Path(temporary).resolve() / 'prepared'
            output.mkdir()
            sentinel = output / 'sentinel'
            sentinel.write_bytes(b'owned synthetic')
            with self.assertRaisesRegex(ValueError, 'Fresh ordinary'):
                module.prepare(output)
            self.assertEqual(sentinel.read_bytes(), b'owned synthetic')
            with patch.object(module.platform, 'system', return_value='Darwin'), self.assertRaisesRegex(ValueError, 'actual hosted Windows'):
                module.run_windows(output, {})
            self.assertEqual(sorted(p.name for p in output.iterdir()), ['sentinel'])

    def test_real_kernel_test_boundaries_do_not_invoke_dispatch_or_service(self):
        kernel = (HERE / 'kernel-tests.rs').read_text()
        pure = (HERE / 'pure-tests.rs').read_text()
        actor = (HERE / 'kernel-actor.rs').read_text()
        self.assertEqual(kernel.count('#[test]'), 7)
        self.assertEqual(pure.count('#[test]'), 7)
        self.assertNotIn('#[ignore]', kernel + pure)
        self.assertNotIn('SendSAS(', kernel + pure + actor)
        self.assertNotIn('.dispatch(', kernel)
        self.assertNotIn('request(', kernel)
        self.assertIn('let _dispatch = Captured::dispatch;', kernel)
        self.assertIn('Captured::capture(pipe.0', kernel)
        self.assertIn('Some(child.0.id())', kernel)
        self.assertIn('error==535 && pipe_pid(pipe.0,false)==Some(child.0.id())', kernel)
        self.assertNotIn('if ok!=0 || error==535 { break; }', kernel)
        self.assertIn('initial pipe availability is not an actual connected peer', kernel)
        self.assertIn('own pipe parent connect deadline', actor)
        self.assertIn('WaitForSingleObject(copy.0,WAIT_MS)', kernel)
        self.assertIn('TIMEOUT', kernel)
        self.assertIn('CloseHandle(handle)', actor)
        self.assertNotIn('std::thread::sleep', actor)
        self.assertIn('own pipe parent release deadline', actor)
        self.assertIn('WaitForSingleObject(handle,5000)', actor)
        for forbidden in ('SendSAS(', 'ImpersonateLoggedOnUser(', 'SetTokenInformation(', 'CreateService', 'RegSetValue', 'powershell', 'cmd.exe'):
            self.assertNotIn(forbidden, actor)

    def test_locked_dependency_graph_and_explicit_run_contract(self):
        lock = tomllib.loads((HERE / 'source-Cargo.lock').read_text())
        manifest = tomllib.loads((HERE / 'source-Cargo.toml').read_text())
        self.assertEqual(lock['version'], 3)
        self.assertEqual(manifest['package']['rust-version'], '1.75')
        self.assertEqual(manifest['dependencies']['winreg'], '=0.11.0')
        packages = {p['name']: p for p in lock['package']}
        self.assertEqual(packages['winreg']['version'], '0.11.0')
        self.assertEqual(packages['winreg']['dependencies'], ['cfg-if', 'winapi'])
        self.assertEqual(packages['winapi']['version'], '0.3.9')
        self.assertEqual(packages['winreg']['checksum'], '76a1a57ff50e9b408431e8f97d5456f2807f8eb2a2cd79b06068fc87f8ecf189')
        self.assertEqual(packages['tokio']['version'], '1.44.2')
        self.assertEqual(packages['anyhow']['version'], '1.0.98')
        driver = (HERE / 'prepare.py').read_text()
        self.assertNotIn('generate-lockfile', driver)
        self.assertIn("['--locked',", driver)
        self.assertIn("parser.add_argument('--run', action='store_true'", driver)
        for package in lock['package']:
            for dependency in package.get('dependencies', []):
                self.assertIn(dependency, packages)


if __name__ == '__main__':
    unittest.main(verbosity=2)
