#!/usr/bin/env python3
"""Injected Windows ownership/admission controls; no Windows or compiler proof.

All Windows security observations are explicit synthetic probe records over
temporary ordinary source fixtures. Git only reads inert fixture metadata.
No native API, account, ACL edit, provider, application, compiler or UI runs.
"""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name('check-app-rust.py')
SPEC = importlib.util.spec_from_file_location('windows_local_fixture_helpers', SCRIPT.with_name('test-app-rust-check.py'))
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
P = BASE.DRIVER
# Exact baseline function source from driver a3b7aa90d652af92; unlike ast.dump,
# these bindings are portable across the supported Python 3.11 and host 3.14.
EXPECTED_BASE_SOURCE = {'matrix': '8bc302c9bf17bb60f4dfab69c496ea727ae0ec4ed06d63437651a96e21e44176',
 'command': '54df1c1c4998cc195d06822f536ee258b5bdb73b1379fbec1ccbf9bcad2a4f8e',
 'ordinary_path': '936798581e3e5f7ef46f764b1bacdc2d778d4736c0fe422a0c5cfcfe6c249a3b',
 'inventory': '8d4ddf6137679cd6d6b155ec12b4b8945ea43cacae7b8f77e4de17091e829f7c',
 'git_identity': '0af1232168114b754ba507659834737b4972d30864d2fb3701b9678b058852ca',
 'require_source': '9f9b9b25bc3bdd7616e2b2a5e90cbcec897c8b4a7f3116aaa8e58f6f5affac06',
 'owned_directory': '159e54351b3594a5f2d3f7fcdbb2f02645731c927df6c9558bda48f07a1aa5c7',
 'verify_cargo_home': '5716dfa47bbc10896bb8906626e941e3486cef29297ebc027c9eeee71665e9a6',
 'execute': '464f99cc4184473f6b98fb39f4a52a234bad8c07c549942c95003635752bce5d',
 'capture': '89d01d160808b05ba465110e08490f9521a77db69f156694a51c99af94a4fcc7',
 'local_admission': 'b457db84e6b0df8df1bedb1c944ee8f4d56e8d960a030394a5566f9cd453e318',
 'run_environment': '5e2551a64478cb7f9d8dcd8206da68248628bdb3137d8f5bd18f5677c95aaf01'}


class SyntheticSecurityProbe:
    user_sid = 'S-1-5-21-101-202-303-1001'

    def __init__(self):
        self.overrides = {}
        self.calls = []

    def inspect(self, path):
        self.calls.append(path)
        info = path.lstat()
        value = dict(ownerSid=self.user_sid, attributes=0,
                     directory=stat.S_ISDIR(info.st_mode), links=info.st_nlink,
                     fileIndex=info.st_ino, volumeSerial=123, driveVolumeSerial=123, fileSystem='NTFS',
                     dacl=[dict(type=0, flags=0, mask=0x1f01ff, sid=sid)
                           for sid in (self.user_sid, 'S-1-5-18', 'S-1-5-32-544')])
        value.update(copy.deepcopy(self.overrides.get(path, {})))
        return value


class WindowsAdmissionControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='inert-windows-local-')
        self.outer = Path(self.temporary.name).resolve()
        self.repo = self.outer / 'repository'
        BASE.git_metadata(self.repo / '.git')
        BASE.put(self.repo, '.gitignore', b'_scratch/\n.cache/\n')
        self.script = BASE.put(self.repo, 'scripts/check-app-rust.py', SCRIPT.read_bytes())
        self.run_root = self.repo / '_scratch/windows-check'
        self.run_root.mkdir(parents=True)
        self.app = self.repo / 'rustdesk'
        BASE.source(self.app)
        self.output = self.run_root / 'new-check'
        self.target = 'x86_64-pc-windows-msvc'
        self.config = P.matrix(self.target, 'Windows', 'AMD64')
        self.probe = SyntheticSecurityProbe()
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(P, '__file__', str(self.script)))
        self.stack.enter_context(mock.patch.object(P.Path, 'cwd', return_value=self.repo))
        self.stack.enter_context(mock.patch.object(P.platform, 'system', return_value='Windows'))
        self.stack.enter_context(mock.patch.object(P.platform, 'machine', return_value='AMD64'))
        self.factory = self.stack.enter_context(mock.patch.object(P, 'WindowsSecurityProbe', return_value=self.probe))
        self.capture = self.stack.enter_context(mock.patch.object(P, 'capture', side_effect=AssertionError('compiler forbidden')))

    def tearDown(self):
        self.stack.close()
        self.temporary.cleanup()

    def prepare(self):
        return P.prepare(self.app, self.output, self.target, local_run_root=self.run_root)

    def environment(self):
        vcpkg = self.repo / '.cache/vcpkg'
        (vcpkg / 'installed/x64-windows-static/lib').mkdir(parents=True)
        linker = BASE.put(self.repo, '.cache/msvc/link.exe', b'inert; never executed')
        return {'VCPKG_ROOT': str(vcpkg), 'VCToolsInstallDir': str(linker.parent)}, linker

    def test_exact_local_prepare_and_revalidation_without_hosted_identity(self):
        before = P.inventory(self.app)
        self.prepare()
        self.assertEqual(before, P.inventory(self.app))
        self.assertEqual(before, P.inventory(self.output / 'source'))
        self.assertIn(self.output, self.probe.calls)
        env, linker = self.environment()
        import rust_toolchain
        with mock.patch.object(P, 'git_identity', return_value={'head': P.VCPKG_COMMIT}), \
                mock.patch.object(rust_toolchain, 'find_msvc_linker', return_value=linker) as qualified:
            result = P.run_environment(self.config, self.app, self.output, env, local_run_root=self.run_root)
        qualified.assert_called_once()
        self.assertEqual(result['CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER'], str(linker))
        self.assertEqual(P.command(self.config), ['rustup', 'run', '1.75', 'cargo', 'check', '--locked',
                         '--release', '--lib', '--features', 'flutter', '--target', self.target])
        for key in ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_TEMP'):
            self.assertNotIn(key, result)
        evidence = json.loads((self.output / 'check.environment.json').read_text())
        self.assertEqual(evidence['admission'], 'local-windows')
        self.assertEqual(evidence['localRunRoot'], str(self.run_root))
        self.assertIn(self.output / 'source/src/lib.rs', self.probe.calls)
        self.capture.assert_not_called()
        with self.assertRaises(P.Refusal):
            self.prepare()

    def test_foreign_owner_refuses_before_copy_or_output_creation(self):
        for path in (self.script, self.repo, self.run_root, self.app, self.app / 'src/lib.rs'):
            self.probe.overrides[path] = {'ownerSid': 'S-1-5-21-909-808-707-1002'}
            with self.subTest(path=path), mock.patch.object(P.shutil, 'copytree', side_effect=AssertionError('copy forbidden')):
                with self.assertRaisesRegex(P.Refusal, 'actual token SID'):
                    self.prepare()
            self.probe.overrides.clear()
            self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_public_null_empty_and_unreviewed_dacls_refuse(self):
        owner = dict(type=0, flags=0, mask=0x1f01ff, sid=self.probe.user_sid)
        cases = [None, [], [owner, dict(type=0, flags=0, mask=0x120089, sid='S-1-5-32-545')],
                 [owner, dict(type=0, flags=8, mask=1, sid='S-1-1-0')],
                 [dict(type=5, flags=0, mask=1, sid=self.probe.user_sid)],
                 [dict(type=0, flags=0, mask=0x1f01ff, sid='S-1-5-18')],
                 [owner, dict(type=0, flags=0, mask=1, sid='S-1-3-0')]]
        for dacl in cases:
            self.probe.overrides[self.run_root] = {'dacl': dacl}
            with self.subTest(dacl=dacl), self.assertRaises(P.Refusal):
                self.prepare()
            self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_trusted_inherited_entries_and_inherit_only_creator_owner_are_private(self):
        self.probe.overrides[self.run_root] = {'dacl': [
            dict(type=0, flags=0x10, mask=0x1f01ff, sid=self.probe.user_sid),
            dict(type=0, flags=0x18, mask=0x1f01ff, sid='S-1-3-0'),
            dict(type=1, flags=0, mask=1, sid='S-1-1-0')]}
        self.prepare()
        self.capture.assert_not_called()

    def test_source_symlink_and_hardlink_refuse(self):
        path = self.app / 'src/alias.rs'
        path.symlink_to('lib.rs')
        with self.assertRaises(P.Refusal):
            self.prepare()
        path.unlink()
        os.link(self.app / 'src/lib.rs', path)
        with self.assertRaises(P.Refusal):
            self.prepare()
        path.unlink()
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_native_reported_reparse_or_hardlink_refuses(self):
        path = self.app / 'src/lib.rs'
        for record in ({'attributes': 0x400}, {'links': 2}, {'directory': True},
                       {'fileIndex': 0}, {'fileIndex': 1}, {'volumeSerial': 456}, {'fileSystem': 'ReFS'}):
            self.probe.overrides[path] = record
            with self.subTest(record=record), self.assertRaises(P.Refusal):
                self.prepare()
            self.assertFalse(self.output.exists())

    def test_real_ignored_root_and_output_required_even_under_scratch(self):
        (self.repo / '.gitignore').write_bytes(b'.cache/\n')
        with self.assertRaisesRegex(P.Refusal, 'Git-ignored'):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.capture.assert_not_called()

    def test_wrong_cwd_relative_root_outside_output_and_wrong_matrix_refuse(self):
        with mock.patch.object(P.Path, 'cwd', return_value=self.app):
            with self.assertRaisesRegex(P.Refusal, 'repository root'):
                self.prepare()
        cases = ((Path('_scratch/windows-check'), self.output, self.config),
                 (self.run_root, self.outer / 'output', self.config),
                 (self.run_root, self.output, {**self.config, 'toolchain': '1.81'}))
        for root, output, config in cases:
            with self.subTest(root=root, output=output), self.assertRaises(P.Refusal):
                P.windows_local_admission(root, self.app, output, config, prepared=False)
        self.assertFalse(self.output.exists())

    def test_new_output_security_checked_before_any_evidence_or_copy(self):
        self.probe.overrides[self.output] = {'ownerSid': 'S-1-5-21-909-808-707-1002'}
        with mock.patch.object(P.shutil, 'copytree', side_effect=AssertionError('copy forbidden')):
            with self.assertRaises(P.Refusal):
                self.prepare()
        self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.output.iterdir()), [])
        self.capture.assert_not_called()

    def test_private_copy_security_revalidated_before_compiler(self):
        source, output, config, before = self.prepare()
        self.probe.overrides[output / 'source/src/lib.rs'] = {'ownerSid': 'S-1-5-21-909-808-707-1002'}
        with self.assertRaises(P.Refusal):
            P.execute(source, output, config, before, local_run_root=self.run_root)
        result = json.loads((output / 'check.result.json').read_text())
        self.assertFalse(result['compiled'])
        self.assertTrue(result['sourceUnchanged'])
        self.capture.assert_not_called()

    def test_pinned_vcpkg_and_qualified_msvc_remain_mandatory(self):
        self.prepare()
        env, linker = self.environment()
        import rust_toolchain
        with mock.patch.object(P, 'git_identity', return_value={'head': '0' * 40}):
            with self.assertRaisesRegex(P.Refusal, 'vcpkg revision'):
                P.run_environment(self.config, self.app, self.output, env, local_run_root=self.run_root)
        with mock.patch.object(P, 'git_identity', return_value={'head': P.VCPKG_COMMIT}), \
                mock.patch.object(rust_toolchain, 'find_msvc_linker', side_effect=RuntimeError('actual linker refuses')):
            with self.assertRaisesRegex(RuntimeError, 'actual linker refuses'):
                P.run_environment(self.config, self.app, self.output, env, local_run_root=self.run_root)
        self.assertFalse((self.output / 'check.environment.json').exists())
        self.capture.assert_not_called()


class CandidateConservationControls(unittest.TestCase):
    def test_native_architecture_decision_uses_kernel_facts_not_amd64_environment(self):
        P.require_windows_native_x64(0, 0x8664, 8)
        with mock.patch.dict(os.environ, {'PROCESSOR_ARCHITECTURE': 'AMD64', 'PROCESSOR_ARCHITEW6432': 'AMD64'}):
            for facts in ((0, 0xaa64, 8), (0x8664, 0xaa64, 8), (0x14c, 0x8664, 4),
                          (0, 0x8664, 4), (0, 0, 8)):
                with self.subTest(facts=facts), self.assertRaises(P.Refusal):
                    P.require_windows_native_x64(*facts)
        source = SCRIPT.read_text()
        self.assertIn('self.kernel.IsWow64Process2(self.kernel.GetCurrentProcess()', source)
        probe = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
                     and node.name == 'WindowsSecurityProbe')
        self.assertNotIn('platform.machine', ast.unparse(probe))

    def test_mac_hosted_compiler_and_source_conservation_functions_are_unchanged(self):
        source = SCRIPT.read_text()
        current = {node.name: ast.get_source_segment(source, node)
                   for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
        def digest(value):
            return hashlib.sha256(value.encode()).hexdigest()
        for name in ('matrix', 'command', 'ordinary_path', 'inventory', 'git_identity', 'require_source',
                     'owned_directory', 'verify_cargo_home', 'execute', 'capture'):
            self.assertEqual(digest(current[name]), EXPECTED_BASE_SOURCE[name], name)
        mac = current['macos_local_admission']
        self.assertEqual(mac.count('def macos_local_admission('), 1)
        mac = mac.replace('def macos_local_admission(', 'def local_admission(', 1)
        self.assertEqual(digest(mac), EXPECTED_BASE_SOURCE['local_admission'])
        environment = current['run_environment']
        new = '("local-windows" if config["host"] == "Windows" else "local-macos") if local_run_root is not None else "github-hosted"'
        old = '"local-macos" if local_run_root is not None else "github-hosted"'
        self.assertEqual(environment.count(new), 1)
        environment = environment.replace(new, old, 1)
        self.assertEqual(digest(environment), EXPECTED_BASE_SOURCE['run_environment'])

    def test_native_probe_is_read_only_and_real_host_guarded(self):
        text = SCRIPT.read_text()
        for forbidden in ('SetSecurityInfo', 'SetNamedSecurityInfo', 'SetFileSecurity', 'AdjustTokenPrivileges',
                          'NetUserAdd', 'New-LocalUser', 'Set-ExecutionPolicy'):
            self.assertNotIn(forbidden, text)
        for required in ('GetTokenInformation', 'GetSecurityInfo', 'GetFileInformationByHandle',
                         'GetVolumeInformationW', 'GetDriveTypeW',
                         'CreateFileW(str(path), 0x00020080, 3, None, 3, 0x02200000, None)'):
            self.assertIn(required, text)
        if os.name != 'nt':
            with self.assertRaisesRegex(P.Refusal, 'actual native Windows x64'):
                P.WindowsSecurityProbe()


if __name__ == '__main__':
    unittest.main(verbosity=2)
