#!/usr/bin/env python3
"""Exact API adapter source/Git controls; no compiler or native input execution."""
import contextlib
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('uinput_dependencies', HERE/'patch-support-receiver-uinput-deps.py')
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)
PLAN = OWNER.load_plan()
UINPUT, LEAVES = OWNER.FILES.load_plan()


def source_owner(filename):
    spec = importlib.util.spec_from_file_location('pinned_'+filename.replace('-', '_'), HERE/filename)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    return owner


def input_prefix(root, newline):
    owner = source_owner('patch-support-input.py')
    for row in PLAN['files']:
        patch = owner.patch_manifest if row['name'] == 'Cargo.toml' else owner.patch_lock
        source = patch(row['freshSource']).encode()
        if OWNER.FILES.sha(source) != PLAN['priorProfiles']['input'][row['name']]['sha256']:
            raise AssertionError('actual input-only source differs from whole pinned prior')
        (root/row['name']).write_bytes(source.replace(b'\n', newline))


def inventory(root):
    result = {}
    for path in root.rglob('*'):
        info = path.lstat()
        result[path.relative_to(root).as_posix()] = (stat.S_IMODE(info.st_mode),
            ('link', os.readlink(path)) if path.is_symlink() else ('dir',) if path.is_dir() else ('file', path.read_bytes()))
    return result


@contextlib.contextmanager
def fixture(newline=b'\n', fresh=False, uinput=True, variant='macos'):
    with tempfile.TemporaryDirectory(prefix='mixel-uinput-dependency-test-') as temporary:
        root = Path(temporary).resolve()/'source'
        root.mkdir()
        for row in UINPUT['files']:
            raw = row['freshSource' if fresh else 'beforeSource']
            path = root/row['name']
            path.parent.mkdir(parents=True, exist_ok=True)
            if raw is not None:
                path.write_bytes(raw.encode().replace(b'\n', newline))
                path.chmod(row['inheritedMode'] or 0o640)
        if not fresh and uinput:
            OWNER.FILES.apply(root)
        for row in OWNER.root_variant(PLAN, variant)['files']:
            path = root/row['name']
            path.write_bytes(row['freshSource' if fresh else 'beforeSource'].encode().replace(b'\n', newline))
            path.chmod(0o640)
        retained = root/'.mixel-deps/rdev/.git/objects/retained'
        retained.parent.mkdir(parents=True)
        retained.write_bytes(b'Existing dependency metadata stays exact.')
        yield root


class DependencyControls(unittest.TestCase):
    def test_roundtrip_lf_crlf_no_write_checks_real_git_identity(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), fixture(newline) as root:
                before = inventory(root)
                self.assertFalse(OWNER.is_current(root))
                self.assertEqual(OWNER.apply(root, check=True)['writes'], 0)
                self.assertEqual(inventory(root), before)
                OWNER.apply(root)
                self.assertTrue(OWNER.is_current(root))
                after = inventory(root)
                for dep in PLAN['dependencies']:
                    self.assertEqual(OWNER.git(root/dep['path'], 'rev-parse', 'HEAD').decode().strip(), dep['pin'])
                    self.assertEqual(OWNER.git(root/dep['path'], 'config', '--get', 'remote.origin.url').decode().strip(), dep['url'])
                    self.assertEqual(OWNER.git(root/dep['path'], 'fsck', '--full', '--no-reflogs'), b'')
                    self.assertEqual(OWNER.inventory(root, dep)[0], 'after')
                self.assertEqual(OWNER.apply(root)['writes'], 0)
                self.assertEqual(OWNER.apply(root, restore=True, check=True)['writes'], 0)
                with OWNER.previous(root) as stage:
                    self.assertEqual(stage, stage.resolve())
                    self.assertEqual(inventory(stage), before)
                self.assertEqual(inventory(root), after)
                OWNER.apply(root, restore=True)
                self.assertEqual(inventory(root), before)
                self.assertEqual(OWNER.apply(root, restore=True)['writes'], 0)

    def test_fresh_and_complete_uinput_predecessor_handling(self):
        with fixture(fresh=True) as root:
            before = inventory(root)
            self.assertEqual(OWNER.inspect(root)[2], 'fresh')
            self.assertEqual(OWNER.apply(root, restore=True)['writes'], 0)
            with self.assertRaises(ValueError):
                OWNER.apply(root, check=True)
            self.assertEqual(inventory(root), before)

        with fixture(uinput=False) as root:
            before = inventory(root)
            self.assertEqual(OWNER.inspect(root)[2], 'before')
            self.assertEqual(OWNER.apply(root, restore=True)['writes'], 0)
            with self.assertRaisesRegex(ValueError, 'complete own-uinput'):
                OWNER.apply(root, check=True)
            self.assertEqual(inventory(root), before)

    def test_complete_common_platform_profile_roundtrips_lf_and_crlf(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), fixture(newline, variant='common') as root:
                before = inventory(root)
                selected = OWNER.inspect(root)
                self.assertEqual(selected[2], 'before')
                lock = next(row for row in selected[1]['files'] if row['name'] == 'Cargo.lock')
                self.assertEqual(lock['beforeSha256'], PLAN['rootVariants']['common']['Cargo.lock']['beforeSha256'])
                self.assertEqual(OWNER.apply(root, check=True)['writes'], 0)
                self.assertEqual(inventory(root), before)
                OWNER.apply(root)
                self.assertTrue(OWNER.is_current(root))
                after = inventory(root)
                with OWNER.previous(root) as stage:
                    self.assertEqual(inventory(stage), before)
                self.assertEqual(inventory(root), after)
                OWNER.apply(root, restore=True)
                self.assertEqual(inventory(root), before)
                # An exact complete alternate is authority; a mixed stage is not.
                manifest = next(row for row in PLAN['files'] if row['name'] == 'Cargo.toml')
                (root/'Cargo.toml').write_bytes(manifest['afterSource'].encode().replace(b'\n', newline))
                mixed = inventory(root)
                with self.assertRaisesRegex(ValueError, 'mixed uinput dependency profile'):
                    OWNER.apply(root, restore=True, check=True)
                self.assertEqual(inventory(root), mixed)

    def test_input_only_prior_is_exact_noop_and_never_forward_authority(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), fixture(newline, fresh=True) as root:
                input_prefix(root, newline)
                before = inventory(root)
                self.assertEqual(OWNER.inspect(root)[2], 'prior')
                self.assertFalse(OWNER.is_current(root))
                for check in (False, True):
                    self.assertEqual(OWNER.apply(root, restore=True, check=check), {'profile': 'prior', 'writes': 0})
                    with self.assertRaisesRegex(ValueError, 'require complete branding'):
                        OWNER.apply(root, check=check)
                    self.assertEqual(inventory(root), before)
                with OWNER.previous(root) as stage:
                    self.assertEqual(inventory(stage), before)
                self.assertEqual(inventory(root), before)
                for row in PLAN['files']:
                    path = root/row['name']
                    prior = path.read_bytes()
                    for source in (row['freshSource'], row['beforeSource'], row['afterSource']):
                        path.write_bytes(source.encode().replace(b'\n', newline))
                        altered = inventory(root)
                        with self.assertRaisesRegex(ValueError, 'mixed uinput dependency profile'):
                            OWNER.apply(root, restore=True, check=True)
                        self.assertEqual(inventory(root), altered)
                    path.write_bytes(prior)

    def test_prior_cannot_adopt_any_owned_dependency_installation(self):
        for count in (1, 2):
            with self.subTest(count=count), fixture(fresh=True) as root:
                input_prefix(root, b'\n')
                for dep in PLAN['dependencies'][:count]:
                    OWNER.snapshot(root/dep['path'], dep)
                before = inventory(root)
                with self.assertRaisesRegex(ValueError, 'partial|mixed'):
                    OWNER.apply(root, restore=True, check=True)
                self.assertEqual(inventory(root), before)

    def test_full_common_and_macos_profiles_match_actual_pinned_root_helpers(self):
        capture = source_owner('patch-support-input.py')
        clipboard = source_owner('patch-support-clipboard.py')
        macos = source_owner('patch-support-macos-rdev.py')
        fresh = {row['name']: row['freshSource'] for row in PLAN['files']}
        manifest = capture.patch_manifest(fresh['Cargo.toml'])
        old = 'LegalCopyright = "Copyright © 2025 Purslane Ltd. All rights reserved."'
        new = 'LegalCopyright = "Copyright © 2026 Mixel IT and Corporate Services GmbH. All rights reserved."'
        self.assertEqual(manifest.count(old), 1)
        manifest = clipboard.patch_manifest(manifest.replace(old, new, 1))
        lock = capture.patch_lock(fresh['Cargo.lock'])
        for variant, candidate in (('common', lock), ('macos', macos.lock_pair(lock)[1])):
            with self.subTest(variant=variant):
                actual = {'Cargo.toml': manifest, 'Cargo.lock': clipboard.patch_lock(candidate)}
                for row in OWNER.root_variant(PLAN, variant)['files']:
                    self.assertEqual(actual[row['name']], row['beforeSource'])
                    self.assertEqual(OWNER.FILES.sha(actual[row['name']].encode()), row['beforeSha256'])

    def test_generated_git_identity_is_literal_lf_without_text_translation(self):
        original = Path.write_text
        def forbid_text_identity(path, *args, **kwargs):
            if path.parent.name == '.git' and path.name in ('HEAD', 'shallow', 'config'):
                self.fail('owned Git identity must use literal bytes on every host')
            return original(path, *args, **kwargs)
        with fixture() as root:
            with mock.patch.object(Path, 'write_text', forbid_text_identity):
                OWNER.apply(root)
            for dep in PLAN['dependencies']:
                for name in ('HEAD', 'shallow'):
                    self.assertEqual((root/dep['path']/'.git'/name).read_bytes(), (dep['pin']+'\n').encode('ascii'))
                self.assertEqual((root/dep['path']/'.git/config').read_bytes(), OWNER.config(dep))

    def test_each_root_source_byte_mode_and_mixed_profile_refuses(self):
        for after in (False, True):
            for row in PLAN['files']:
                with self.subTest(after=after, name=row['name']), fixture() as root:
                    if after:
                        OWNER.apply(root)
                    path = root/row['name']
                    path.write_bytes(path.read_bytes()+b'\n# unexpected change\n')
                    before = inventory(root)
                    with self.assertRaises(ValueError):
                        OWNER.apply(root, restore=True, check=True)
                    self.assertEqual(inventory(root), before)
        with fixture() as root:
            row = PLAN['files'][0]
            (root/row['name']).write_text(row['afterSource'])
            before = inventory(root)
            with self.assertRaises(ValueError):
                OWNER.apply(root)
            self.assertEqual(inventory(root), before)

    def test_dependency_source_extra_missing_and_metadata_refuse(self):
        for alteration in ('target', 'unmodified', 'extra', 'missing', 'head', 'origin', 'link', 'partial'):
            with self.subTest(alteration=alteration), fixture() as root:
                OWNER.apply(root)
                dep = PLAN['dependencies'][0]
                path = root/dep['path']
                if alteration == 'target':
                    target = path/dep['target']
                    target.write_bytes(target.read_bytes()+b'\n')
                elif alteration == 'unmodified':
                    target = path/next(row['path'] for row in dep['files'] if row['path'] != dep['target'])
                    target.write_bytes(target.read_bytes()+b'\n')
                elif alteration == 'extra':
                    (path/'unexpected-file').write_text('unexpected')
                elif alteration == 'missing':
                    (path/dep['target']).unlink()
                elif alteration == 'head':
                    (path/'.git/HEAD').write_text('0'*40+'\n')
                elif alteration == 'origin':
                    target = path/'.git/config'
                    target.write_text(target.read_text().replace(dep['url'], 'https://invalid.example/dependency'))
                elif alteration == 'link':
                    (path/'.git/objects/info').mkdir()
                    (path/'.git/objects/info/alternates').write_text('/external/objects')
                else:
                    shutil.rmtree(root/PLAN['dependencies'][1]['path'])
                before = inventory(root)
                for operation in (lambda: OWNER.is_current(root), lambda: OWNER.apply(root, restore=True, check=True), lambda: OWNER.apply(root)):
                    with self.assertRaises((ValueError, OWNER.subprocess.CalledProcessError)):
                        operation()
                self.assertEqual(inventory(root), before)

    def test_original_clone_and_owned_mode_refuse(self):
        with fixture() as root:
            OWNER.apply(root)
            dep = PLAN['dependencies'][0]
            path = root/dep['path']/dep['target']
            path.write_bytes(path.read_bytes()[:-len(dep['addition'].encode())])
            before = inventory(root)
            with self.assertRaisesRegex(ValueError, 'original uinput dependency'):
                OWNER.apply(root, restore=True)
            self.assertEqual(inventory(root), before)
        with fixture() as root:
            OWNER.apply(root)
            dep = PLAN['dependencies'][0]
            path = root/dep['path']/dep['target']
            path.chmod(0o444 if os.name == 'nt' else 0o600)
            before = inventory(root)
            with self.assertRaises(ValueError):
                OWNER.apply(root, restore=True)
            self.assertEqual(inventory(root), before)
            if os.name == 'nt':
                path.chmod(0o666)

    def test_private_inverse_rechecks_and_preserves_other_git_metadata(self):
        with fixture() as root:
            OWNER.apply(root)
            with self.assertRaises(ValueError):
                with OWNER.previous(root):
                    target = root/PLAN['dependencies'][0]['path']/PLAN['dependencies'][0]['target']
                    target.write_bytes(target.read_bytes()+b'\n')

    def test_partial_or_linked_destination_refuses(self):
        with fixture() as root:
            destination = root/PLAN['dependencies'][0]['path']
            destination.symlink_to(root/'.mixel-deps/rdev', target_is_directory=True)
            before = inventory(root)
            with self.assertRaises(ValueError):
                OWNER.apply(root, check=True)
            self.assertEqual(inventory(root), before)

    def test_owned_changes_only_expose_existing_handle(self):
        for dep in PLAN['dependencies']:
            changes = [row for row in dep['files'] if row['beforeSha256'] != row['afterSha256']]
            self.assertEqual([row['path'] for row in changes], [dep['target']])
            self.assertIn('impl std::os::unix::io::AsRawFd', dep['addition'])
            for forbidden in ('unsafe', 'write(', 'emit(', 'pid', 'connect(', 'ioctl'):
                self.assertNotIn(forbidden, dep['addition'])

    def test_pinned_dependency_plan_drift_refuses(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory(prefix='mixel-uinput-dependency-carrier-') as temporary:
                root = Path(temporary).resolve()
                for name in ('patch-support-receiver-uinput-deps.py', 'support-receiver-uinput-deps-plan.json',
                             'patch-support-receiver-uinput.py'):
                    shutil.copy2(HERE/name, root/name)
                target = root/'support-receiver-uinput-deps-plan.json'
                raw = OWNER.FILES.canonical(target.read_bytes())[0].replace(b'\n', newline)
                target.write_bytes(raw+OWNER.FILES.canonical(raw)[1])
                spec = importlib.util.spec_from_file_location('changed_uinput_dependency_owner', root/'patch-support-receiver-uinput-deps.py')
                owner = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(owner)
                with self.assertRaisesRegex(ValueError, 'plan drift'):
                    owner.load_plan()

    def test_worktrees_reject_before_scandir_or_read_below_child(self):
        for relative in ('.git/worktrees', '.git/objects/worktrees', 'worktrees', 'src/worktrees'):
            with self.subTest(relative=relative), fixture() as root:
                OWNER.apply(root)
                dep = PLAN['dependencies'][0]
                forbidden = root/dep['path']/relative
                forbidden.parent.mkdir(parents=True, exist_ok=True)
                try:
                    forbidden.symlink_to(root, target_is_directory=True)
                except OSError:
                    if os.name != 'nt':
                        raise
                    forbidden.mkdir()  # opaque empty fallback, no descendants
                scan, read, lstat = OWNER.os.scandir, Path.read_bytes, Path.lstat
                def guarded_scan(path):
                    if not isinstance(path, int) and 'worktrees' in Path(path).parts:
                        self.fail('scandir descended into a worktrees child')
                    return scan(path)
                def guarded_read(path):
                    if 'worktrees' in path.parts:
                        self.fail('read descended into a worktrees child')
                    return read(path)
                def guarded_lstat(path, *args, **kwargs):
                    if 'worktrees' in path.parts:
                        self.fail('lstat inspected an opaque worktrees entry')
                    return lstat(path, *args, **kwargs)
                try:
                    with mock.patch.object(OWNER.os, 'scandir', guarded_scan), mock.patch.object(Path, 'read_bytes', guarded_read), mock.patch.object(Path, 'lstat', guarded_lstat):
                        with self.assertRaisesRegex(ValueError, 'worktree'):
                            OWNER.inspect(root)
                finally:
                    if forbidden.is_symlink():
                        forbidden.unlink()
                    else:
                        forbidden.rmdir()

    def test_untrusted_owned_config_refuses_before_any_git_call(self):
        with fixture() as root:
            OWNER.apply(root)
            dep = PLAN['dependencies'][0]
            config = root/dep['path']/'.git/config'
            config.write_bytes(config.read_bytes()+b'\n[include]\n\tpath = /external/config\n')
            with mock.patch.object(OWNER, 'git', side_effect=AssertionError('untrusted local config reached Git')):
                with self.assertRaisesRegex(ValueError, 'owned Git config'):
                    OWNER.inspect(root)


if __name__ == '__main__':
    unittest.main()
