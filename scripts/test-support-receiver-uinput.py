#!/usr/bin/env python3
"""Closed source-owner controls. These do not qualify Linux native injection."""
import contextlib
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('uinput_owner', HERE/'patch-support-receiver-uinput.py')
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)
PLAN, TEMPLATES = OWNER.load_plan()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        result[relative] = (stat.S_IMODE(info.st_mode),
                            ('link', os.readlink(path)) if path.is_symlink() else
                            ('dir',) if path.is_dir() else ('file', path.read_bytes()))
    return result


@contextlib.contextmanager
def fixture(newline=b'\n', fresh=False, profile=None):
    with tempfile.TemporaryDirectory(prefix='mixel-uinput-control-') as temporary:
        root = Path(temporary).resolve()/'source'
        root.mkdir()
        for index, row in enumerate(PLAN['files']):
            raw = row[(profile or ('fresh' if fresh else 'before'))+'Source']
            path = root/row['name']
            path.parent.mkdir(parents=True, exist_ok=True)
            if raw is not None:
                path.write_bytes(raw.encode().replace(b'\n', newline))
                path.chmod(row['inheritedMode'] or (0o600 if index % 2 else 0o640))
        yield root


class OwnerControls(unittest.TestCase):
    def test_lf_and_crlf_check_roundtrip_and_modes(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), fixture(newline) as root:
                before = inventory(root)
                result = OWNER.apply(root, check=True)
                self.assertEqual(result['writes'], 0)
                self.assertEqual(result['plannedWrites'], 8)
                self.assertEqual(inventory(root), before)
                self.assertFalse(OWNER.is_current(root))
                self.assertEqual(OWNER.apply(root)['writes'], 8)
                self.assertTrue(OWNER.is_current(root))
                after = inventory(root)
                self.assertEqual(OWNER.apply(root)['writes'], 0)
                self.assertEqual(inventory(root), after)
                self.assertEqual(OWNER.apply(root, restore=True, check=True)['writes'], 0)
                self.assertEqual(inventory(root), after)
                for row in PLAN['owned']:
                    raw = (root/row['name']).read_bytes()
                    self.assertEqual(OWNER.canonical(raw)[1], newline)
                self.assertEqual(OWNER.apply(root, restore=True)['writes'], 8)
                self.assertEqual(inventory(root), before)
                self.assertEqual(OWNER.apply(root, restore=True)['writes'], 0)

    def test_fresh_restore_noop_and_apply_requires_prior(self):
        for newline in (b'\n', b'\r\n'):
            with fixture(newline, fresh=True) as root:
                before = inventory(root)
                self.assertFalse(OWNER.is_current(root))
                self.assertEqual(OWNER.apply(root, restore=True)['writes'], 0)
                with self.assertRaises(ValueError):
                    OWNER.apply(root, check=True)
                self.assertEqual(inventory(root), before)

    def test_exact_pre_native_receiver_prior_is_readonly(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), fixture(newline, profile='prior') as root:
                before = inventory(root)
                self.assertEqual(OWNER.inspect(root)[3], 'prior')
                self.assertFalse(OWNER.is_current(root))
                for check in (False, True):
                    self.assertEqual(OWNER.apply(root, restore=True, check=check)['writes'], 0)
                    with self.assertRaisesRegex(ValueError, 'requires full native\\+receiver'):
                        OWNER.apply(root, check=check)
                with OWNER.previous(root) as stage:
                    self.assertEqual(OWNER.inspect(stage)[3], 'prior')
                    self.assertEqual(inventory(stage), before)
                self.assertEqual(inventory(root), before)

    def test_every_mixed_prefix_profile_refuses_before_writes(self):
        # Include absent native/receiver leaves and absent terminal leaves in
        # the complete profiles; a plausible single-file prefix is no authority.
        for base in ('fresh', 'prior', 'before', 'after'):
            for donor in ('fresh', 'prior', 'before', 'after'):
                if base == donor:
                    continue
                with fixture(profile='before' if base == 'after' else base) as root:
                    if base == 'after':
                        OWNER.apply(root)
                    baseline = inventory(root)
                    for row in PLAN['files']+PLAN['owned']:
                        path = root/row['name']
                        original = path.read_bytes() if path.exists() else None
                        if row in PLAN['owned']:
                            replacement = TEMPLATES[row['name']] if donor == 'after' else None
                        elif donor == 'after':
                            replacement = OWNER.transform(row['beforeSource'].encode(), row)
                        else:
                            raw = row[donor+'Source']
                            replacement = raw.encode() if raw is not None else None
                        if original == replacement:
                            continue
                        with self.subTest(base=base, donor=donor, name=row['name']):
                            if replacement is None:
                                path.unlink()
                            else:
                                path.write_bytes(replacement)
                                path.chmod(0o644)
                            mixed = inventory(root)
                            for operation in (lambda: OWNER.is_current(root),
                                              lambda: OWNER.apply(root, check=True),
                                              lambda: OWNER.apply(root, restore=True)):
                                with self.assertRaises(ValueError):
                                    operation()
                            self.assertEqual(inventory(root), mixed)
                            if original is None:
                                path.unlink()
                            else:
                                path.write_bytes(original)
                                path.chmod(baseline[row['name']][0])
                    self.assertEqual(inventory(root), baseline)

    def test_each_prior_and_final_whole_source_drift_refuses(self):
        for after in (False, True):
            for row in PLAN['files']:
                with self.subTest(after=after, name=row['name']), fixture() as root:
                    if after:
                        OWNER.apply(root)
                    path = root/row['name']
                    path.write_bytes(path.read_bytes()+b'\n// unexpected source\n')
                    before = inventory(root)
                    for operation in (lambda: OWNER.is_current(root),
                                      lambda: OWNER.apply(root, check=True),
                                      lambda: OWNER.apply(root, restore=True)):
                        with self.assertRaises(ValueError):
                            operation()
                    self.assertEqual(inventory(root), before)

    def test_partial_profile_refuses(self):
        with fixture() as root:
            row = PLAN['files'][0]
            path = root/row['name']
            path.write_bytes(OWNER.transform(path.read_bytes(), row))
            before = inventory(root)
            with self.assertRaises(ValueError):
                OWNER.apply(root)
            self.assertEqual(inventory(root), before)

    def test_mixed_and_bare_cr_refuse_before_writes(self):
        for suffix in (b'\r\n', b'\r'):
            with fixture() as root:
                path = root/PLAN['files'][0]['name']
                path.write_bytes(path.read_bytes()+suffix)
                before = inventory(root)
                with self.assertRaises(ValueError):
                    OWNER.apply(root)
                self.assertEqual(inventory(root), before)

    def test_owned_leaf_byte_mode_and_partial_refusals(self):
        for alteration in ('bytes', 'mode', 'missing'):
            with fixture() as root:
                OWNER.apply(root)
                path = root/PLAN['owned'][0]['name']
                if alteration == 'bytes':
                    path.write_bytes(path.read_bytes()+b'\n')
                elif alteration == 'mode':
                    path.chmod(0o444 if os.name == 'nt' else 0o600)
                else:
                    path.unlink()
                before = inventory(root)
                with self.assertRaises(ValueError):
                    OWNER.apply(root, restore=True, check=True)
                self.assertEqual(inventory(root), before)
                if alteration == 'mode' and os.name == 'nt':
                    path.chmod(0o666)

    def test_hardlink_and_symlink_files_refuse(self):
        for link_kind in ('hard', 'symbolic'):
            with fixture() as root:
                path = root/PLAN['files'][0]['name']
                external = root/'original-copy.rs'
                path.rename(external)
                if link_kind == 'hard':
                    os.link(external, path)
                else:
                    path.symlink_to(external)
                before = inventory(root)
                with self.assertRaises(ValueError):
                    OWNER.apply(root)
                self.assertEqual(inventory(root), before)

    def test_linked_parent_and_root_refuse(self):
        with fixture() as root:
            original = root/'src'
            target = root/'saved-src'
            original.rename(target)
            original.symlink_to(target, target_is_directory=True)
            before = inventory(root)
            with self.assertRaises(ValueError):
                OWNER.apply(root)
            self.assertEqual(inventory(root), before)
        with fixture() as root:
            link = root.parent/'linked-source'
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                OWNER.apply(link)

    def test_owned_destination_link_refuses(self):
        with fixture() as root:
            path = root/PLAN['owned'][0]['name']
            path.symlink_to(root/PLAN['files'][0]['name'])
            before = inventory(root)
            with self.assertRaises(ValueError):
                OWNER.apply(root)
            self.assertEqual(inventory(root), before)

    def test_worktree_roots_are_never_read(self):
        with tempfile.TemporaryDirectory(prefix='mixel-uinput-forbidden-') as temporary:
            root = Path(temporary).resolve()/'worktrees'/'source'
            with self.assertRaises(ValueError):
                OWNER.apply(root)
            self.assertFalse(root.exists())

    def test_private_inverse_preserves_git_and_anchored_cache_policy(self):
        with fixture(b'\r\n') as root:
            additions = {
                '.mixel-deps/rdev/.git/HEAD': b'ref: refs/heads/pinned\n',
                '.mixel-deps/rdev/.git/objects/owned': b'owned git object',
                '.mixel-deps/rdev/build/retained': b'dependency build name is ordinary source',
                'elsewhere/target/retained': b'nested target is ordinary source',
                'build/ignored': b'app cache',
                'target/ignored': b'app cache',
                'flutter/build/ignored': b'app cache',
                'flutter/linux/flutter/ephemeral/ignored': b'app cache',
            }
            for name, raw in additions.items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o640)
            OWNER.apply(root)
            before = inventory(root)
            with OWNER.previous(root) as stage:
                self.assertEqual(stage, stage.resolve())
                self.assertFalse(OWNER.is_current(stage))
                for name, raw in additions.items():
                    path = stage/name
                    if name.endswith('/retained') or '/.git/' in name:
                        self.assertEqual(path.read_bytes(), raw)
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), before[name][0])
                    else:
                        self.assertFalse(path.exists())
            self.assertEqual(inventory(root), before)

    def test_private_inverse_rechecks_supplied_owner(self):
        with fixture() as root:
            OWNER.apply(root)
            with self.assertRaises(ValueError):
                with OWNER.previous(root):
                    path = root/PLAN['files'][0]['name']
                    path.write_bytes(path.read_bytes()+b'\n// concurrent change\n')

    def test_template_and_plan_drift_are_fatal(self):
        for target in ('support-receiver-uinput.rs', 'support-receiver-uinput-plan.json'):
            with tempfile.TemporaryDirectory(prefix='mixel-uinput-carrier-') as temporary:
                root = Path(temporary).resolve()
                for name in ('patch-support-receiver-uinput.py', 'support-receiver-uinput-plan.json',
                             'support-receiver-uinput.rs', 'support-receiver-uinput-core.rs',
                             'support-receiver-uinput-kernel.rs'):
                    shutil.copy2(HERE/name, root/name)
                path = root/target
                path.write_bytes(path.read_bytes()+b'\n')
                spec = importlib.util.spec_from_file_location('changed_owner', root/'patch-support-receiver-uinput.py')
                owner = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(owner)
                with self.assertRaises(ValueError):
                    owner.load_plan()

    def test_frontend_service_ledger_bypass_precedes_suppression(self):
        by_name = {row['name']: row for row in PLAN['files']}
        def final(name):
            row = by_name[name]
            return OWNER.transform(row['beforeSource'].encode(), row).decode()
        native = final('src/server/mixel_receiver_native.rs')
        acquire = native[native.index('fn acquire('):native.index('fn release(')]
        self.assertLess(acquire.index('receiver::key('), acquire.index('receipts().lock()'))
        release = native[native.index('fn release('):native.index('fn retire_matching(')]
        self.assertLess(release.index('receiver::key('), release.index('receipts().lock()'))
        service = final('src/server/input_service.rs')
        for function in ('owned_mouse_down', 'owned_mouse_up'):
            body = service[service.index('fn '+function+'('):]
            self.assertLess(body.index('receiver::mouse_button('), body.index('MOUSE_BUTTON_OWNERS.lock()'))

    def test_sync_deny_and_final_sink_observation_contract(self):
        by_name = {row['name']: row for row in PLAN['files']}
        row = by_name['src/server/mixel_receiver_input.rs']
        receiver = OWNER.transform(row['beforeSource'].encode(), row).decode()
        deny = receiver[receiver.index('    fn deny('):receiver.index('    fn revoke(')]
        self.assertIn('receiver::revoke(&self.token)', deny)
        self.assertNotIn('send(', deny)
        source = (HERE/'support-receiver-uinput.rs').read_text()
        kernel = (HERE/'support-receiver-uinput-kernel.rs').read_text()
        self.assertIn('libc::SO_PEERCRED', kernel)
        self.assertIn('libc::SYS_pidfd_open', kernel)
        self.assertIn('no PID-only fallback', kernel)
        self.assertIn('self.denied.store(true', kernel)
        self.assertIn('Publisher retains/maps only an O_RDONLY', kernel)
        self.assertIn('self.revoke.load(WORD)!=0 || !peer.alive()', kernel)
        self.assertIn('if !connected && state.drained()', source)
        self.assertIn('async fn send_observed', source)
        self.assertIn('while remaining!=0', source)
        self.assertIn('!self.pending_sync', source)
        row = by_name['src/server/uinput.rs']
        final = OWNER.transform(row['beforeSource'].encode(), row).decode()
        controller = final[final.index('    fn spawn_controller_handler('):final.index('    /// Start uinput service.')]
        self.assertIn('Ok(None) => break', controller)
        self.assertIn('if !peer.alive() { break; }', controller)
        self.assertNotIn('                            _ => {}', controller)
        request = source[source.index('pub(super) fn request('):source.index('pub(crate) fn uses_keyboard(')]
        self.assertIn('tokio::time::timeout', request)
        self.assertIn('uinput send deadline exceeded', request)
        self.assertIn('publisher.fail()', request)
        self.assertIn('core::reap_retired', source)
        self.assertIn('libc::F_SEAL_WRITE', kernel)
        self.assertIn('publisher_descriptors_match', kernel)
        self.assertIn('self.set(BIND_READY,kind)', kernel)
        self.assertIn('publisher.bind(kind,&reply.instance)', source)
        self.assertIn('cap.bound_to(kind,&self.instance)', source)
        self.assertIn('cap.bound_to(KEYBOARD,&state.instance)', source)
        self.assertIn('cap.bound_to(MOUSE,&state.instance)', source)
        self.assertIn('instance:uuid::Uuid::new_v4()', source)
        self.assertIn('CAP_SCAN.min(self.cap_scan.len())', source)
        self.assertIn('self.ledger.retire_some(KEY_SCAN', source)
        observer = source[source.index('async fn send_observed('):source.index('fn keyboard_state(')]
        self.assertLess(observer.index('_=&mut deadline'), observer.index('result=&mut send'))
        self.assertLess(observer.index('result=&mut send'), observer.index('_=timer.tick()'))
        self.assertIn('timer.reset_after', observer)
        self.assertEqual(source.count('set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay)'), 2)




if __name__ == '__main__':
    unittest.main()
