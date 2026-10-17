#!/usr/bin/env python3
"""Exact terminal source controls; --native adds pure production Rust receipts.

The native fixture compiles the actual receipt core without any OS input sink.
It proves receipt transitions, not platform/API or target application delivery.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P = module('mixel_receiver_native_patch', SCRIPTS / 'patch-support-receiver-native.py')


def fixture(root, profile='before', newline=b'\n'):
    value, _ = P.plan()
    for row in value['files']:
        raw = row['freshSource' if profile == 'fresh' else 'beforeSource'].encode()
        if profile == 'after':
            raw = P.transform(raw, row)
        path = root / row['name']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw.replace(b'\n', newline))
        path.chmod(0o644)
    if profile == 'after':
        for owner in value['owned']:
            raw = (SCRIPTS / owner['template']).read_bytes()
            path = root / owner['name']
            path.write_bytes(raw.replace(b'\n', newline))
            path.chmod(0o644)
    return value


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


class NativeSourceControls(unittest.TestCase):
    def test_native_prospective_check_validates_without_writing_in_both_directions(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture(root, newline=newline)
                before = snapshot(root)
                result = P.apply(root, check=True)
                self.assertEqual(result['plannedProfile'], 'after')
                self.assertEqual(result['writes'], 0)
                self.assertEqual(result['plannedWrites'], 10)
                self.assertEqual(snapshot(root), before)
                P.apply(root)
                after = snapshot(root)
                result = P.apply(root, restore=True, check=True)
                self.assertEqual(result['plannedProfile'], 'before')
                self.assertEqual(result['writes'], 0)
                self.assertEqual(result['plannedWrites'], 10)
                self.assertEqual(snapshot(root), after)
                P.apply(root, restore=True)
                self.assertEqual(snapshot(root), before)

    def test_owned_mode_matches_actual_platform_representation(self):
        for platform, good, bad in (('nt', 0o666, (0o444, 0o644, 0o777)),
                                   ('posix', 0o644, (0o444, 0o666, 0o777))):
            with mock.patch.object(P.os, 'name', platform):
                self.assertTrue(P.owned_mode(good, 0o644))
                for mode in bad:
                    self.assertFalse(P.owned_mode(mode, 0o644))

    def test_private_copy_keeps_owned_dependency_metadata_and_nested_source_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            sentinels = {'.mixel-deps/rdev/.git/HEAD': b'pinned dependency identity\n',
                         '.mixel-deps/rdev/build/owned.rs': b'owned source, not a root cache\n',
                         '.mixel-deps/rdev/target/owned.rs': b'owned source, not a root cache\n'}
            for name, raw in sentinels.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            cached = root / 'target/cache-only'; cached.parent.mkdir(); cached.write_bytes(b'ignored root build cache')
            before = snapshot(root)
            with P.previous(root) as stage:
                self.assertEqual(stage, stage.resolve(strict=True))
                for name, raw in sentinels.items():
                    self.assertEqual((stage / name).read_bytes(), raw)
                self.assertFalse((stage / 'target').exists())
                (stage / '.mixel-deps/rdev/.git/HEAD').write_bytes(b'private mutation')
            self.assertEqual(snapshot(root), before)

    def test_complete_forward_inverse_and_idempotence_lf_crlf(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture(root, newline=newline)
                before = snapshot(root)
                self.assertFalse(P.is_current(root))
                self.assertEqual(P.apply(root)['writes'], 10)
                self.assertTrue(P.is_current(root))
                after = snapshot(root)
                self.assertEqual(P.apply(root)['writes'], 0)
                P.apply(root, check=True)
                with P.previous(root) as stage:
                    self.assertEqual(stage, stage.resolve(strict=True))
                    self.assertEqual(snapshot(stage), before)
                    self.assertEqual(snapshot(root), after)
                P.apply(root, restore=True)
                self.assertEqual(snapshot(root), before)
                P.apply(root, restore=True, check=True)

    def test_windows_checkout_plan_templates_and_fixtures_uniform_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'checkout'
            scripts = checkout / 'scripts'
            scripts.mkdir(parents=True)
            names = ('patch-support-receiver-native.py', 'support-receiver-native-plan.json',
                     'support-receiver-native.rs', 'support-receiver-receipts.rs',
                     'patch-support-receiver-native-enigo.py', 'support-receiver-native-enigo-plan.json',
                     'test-receiver-native-enigo.py')
            for name in names:
                raw = P.canonical((SCRIPTS / name).read_bytes())[0]
                (scripts / name).write_bytes(raw.replace(b'\n', b'\r\n'))
            for path in (SCRIPTS / 'fixtures/receiver-native-enigo').rglob('*.rs'):
                target = scripts / path.relative_to(SCRIPTS)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(P.canonical(path.read_bytes())[0].replace(b'\n', b'\r\n'))
            copied = module('mixel_native_crlf_checkout', scripts / names[0])
            value, _ = copied.plan()
            target = Path(directory) / 'target'
            target.mkdir()
            for row in value['files']:
                path = target / row['name']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(row['beforeSource'].encode().replace(b'\n', b'\r\n'))
            before = snapshot(target)
            copied.apply(target)
            self.assertTrue(copied.is_current(target))
            copied.apply(target, restore=True)
            self.assertEqual(snapshot(target), before)
            old = module('mixel_native_crlf_enigo_controls', scripts / 'test-receiver-native-enigo.py')
            result = unittest.TestResult()
            unittest.defaultTestLoader.loadTestsFromTestCase(old.NativeAcceptanceTests).run(result)
            self.assertTrue(result.wasSuccessful(), str(result.errors) + str(result.failures))

    def test_exact_fresh_only_restore_and_private_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root, 'fresh')
            before = snapshot(root)
            self.assertEqual(P.apply(root, restore=True, check=True)['profile'], 'fresh')
            self.assertFalse(P.is_current(root))
            with self.assertRaises(ValueError):
                P.apply(root)
            with P.previous(root) as stage:
                self.assertEqual(snapshot(stage), before)
            self.assertEqual(snapshot(root), before)

    def test_partial_drift_missing_and_owned_module_changes_refuse_before_writes(self):
        for mutation in ('partial', 'fresh-partial', 'drift', 'missing', 'missing-module', 'extra-module', 'module-drift', 'mixed'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                value = fixture(root, 'after' if mutation in ('missing-module', 'module-drift') else 'before')
                row = value['files'][0]
                target = root / row['name']
                if mutation == 'partial': target.write_bytes(P.transform(target.read_bytes(), row))
                elif mutation == 'fresh-partial':
                    different = next(item for item in value['files'] if item['freshSha256'] != item['beforeSha256'])
                    (root / different['name']).write_text(different['freshSource'])
                elif mutation == 'drift': target.write_bytes(target.read_bytes() + b'// drift\n')
                elif mutation == 'missing': target.unlink()
                elif mutation == 'missing-module': (root / value['owned'][0]['name']).unlink()
                elif mutation == 'extra-module': (root / value['owned'][0]['name']).write_bytes((SCRIPTS / value['owned'][0]['template']).read_bytes())
                elif mutation == 'module-drift': (root / value['owned'][0]['name']).write_bytes(b'// replaced\n')
                else: target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n', 1))
                before = snapshot(root)
                for restore in (False, True):
                    with self.assertRaises((ValueError, OSError)): P.apply(root, restore=restore)
                    self.assertEqual(snapshot(root), before)

    def test_linked_destination_and_parent_refuse(self):
        for kind in ('link', 'hardlink', 'parent'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                outer = Path(directory)
                root = outer / 'source'
                root.mkdir()
                value = fixture(root)
                target = root / value['files'][0]['name']
                if kind == 'parent':
                    moved = outer / 'foreign'
                    target.parent.rename(moved)
                    target.parent.symlink_to(moved, target_is_directory=True)
                else:
                    moved = outer / 'foreign.rs'
                    target.rename(moved)
                    if kind == 'link': target.symlink_to(moved)
                    else: os.link(moved, target)
                before = snapshot(outer)
                with self.assertRaises(ValueError): P.apply(root)
                self.assertEqual(snapshot(outer), before)

    def test_private_projection_preserves_supplied_source_even_when_caller_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root, 'after')
            (root / 'unrelated.txt').write_bytes(b'kept\n')
            before = snapshot(root)
            with self.assertRaisesRegex(RuntimeError, 'caller stopped'):
                with P.previous(root) as stage:
                    self.assertEqual((stage / 'unrelated.txt').read_bytes(), b'kept\n')
                    raise RuntimeError('caller stopped')
            self.assertEqual(snapshot(root), before)

    def test_no_global_protocol_ledger_or_unowned_key_sinks(self):
        value, _ = P.plan()
        row = next(row for row in value['files'] if row['name'] == 'src/server/input_service.rs')
        source = P.transform(row['beforeSource'].encode(), row).decode()
        self.assertNotIn('KEYS_DOWN', source)
        self.assertNotIn('record_pressed_key', source)
        for sink in ('en.key_down(', 'en.key_up(', 'en.key_click(', 'en.key_sequence(', 'rdev::simulate_key_unicode(', 'rdev::simulate_char('):
            self.assertNotIn(sink, source)
        for wrapper in ('handle_key_native(evt)', 'handle_mouse_native(evt, conn', 'handle_pointer_native(evt, conn)', 'handle_mouse_simulation_native(evt, conn)'):
            self.assertIn('receiver_native::with_default_scope(|| ' + wrapper, source)
        self.assertIn('receiver_native::mouse_owner(conn)', source)
        self.assertIn('whiteboard::get_key_cursor(conn)', source)
        self.assertNotIn('whiteboard::get_key_cursor(receiver_native', source)

    def test_deferred_effect_admission_and_remote_sas_refusal(self):
        value, modules = P.plan()
        row = next(row for row in value['files'] if row['name'] == 'src/server/input_service.rs')
        source = P.transform(row['beforeSource'].encode(), row).decode()
        self.assertEqual(source.count('receiver_native::deferred(|| {'), 4)
        self.assertEqual(source.count('QUEUE.exec_async(receiver_native::captured('), 3)
        sas = source[source.index('async fn send_sas()'):source.index('#[inline]', source.index('async fn send_sas()'))]
        self.assertLess(sas.index('remote_scope()'), sas.index('crate::ipc::connect('))
        self.assertIn('scoped secure-attention service is unavailable', sas)
        native = modules['src/server/mixel_receiver_native.rs'].decode()
        self.assertIn('type ScopedRunner = Arc<dyn Fn(Box<dyn FnOnce() + Send>) -> bool + Send + Sync>', native)
        self.assertIn('with_receiver_owner_scope_checked', native)
        self.assertIn('runner(Box::new(move || with_scope(', native)
        self.assertIn('saved_scopes()', native)

    def test_primitive_acceptance_and_pending_release_are_not_void_receipts(self):
        _, modules = P.plan()
        native = modules['src/server/mixel_receiver_native.rs'].decode()
        core = modules['src/server/mixel_receiver_receipts.rs'].decode()
        self.assertIn('pub(crate) fn release_receiver_owner(owner: &str) -> bool', native)
        self.assertIn('.mouse_up_checked(', native)
        self.assertIn('if !clear_mouse_owner(&mut en, mouse) { complete = false; }', native)
        self.assertIn('if complete {', native)
        self.assertIn('emit(held.route, false)?;', core)
        self.assertIn('errors.push(error); continue;', core)
        self.assertIn('.down(owner, alias, prepared.id, prepared.route', native)
        self.assertIn('.up(&owner, alias, fallback', native)
        self.assertNotIn('rdev::simulate_unicode(', native)
        self.assertIn('receiver_unicode_event(unit, down)', native)

    def test_numeric_boundaries_and_cancellation_chunks(self):
        _, modules = P.plan()
        native = modules['src/server/mixel_receiver_native.rs'].decode()
        self.assertIn('(-1024..=1024).contains(&value)', native)
        self.assertIn('value.is_finite() && (-1024.0..=1024.0).contains(&value)', native)
        self.assertIn('let chunk = value.clamp(-16, 16);', native)
        self.assertLess(native.index('if !admitted() { return; }', native.index('while value != 0')), native.index('let chunk =', native.index('while value != 0')))
        self.assertIn('chunk * 120', native)
        self.assertTrue(all(-1024 <= value <= 1024 for value in (0, 1, -1, 120, -120, 1024, -1024)))
        self.assertTrue(all(not -1024 <= value <= 1024 for value in (-2**31, 2**31-1, -1025, 1025)))
        self.assertTrue(all(not (math.isfinite(value) and -1024 <= value <= 1024) for value in (math.nan, math.inf, -math.inf)))
        for value in (1, -1, 120, -120, 1024, -1024):
            left = value
            chunks = []
            while left:
                chunk = max(-16, min(16, left))
                chunks.append(chunk)
                left -= chunk
            self.assertEqual(sum(chunks), value)
            self.assertLessEqual(len(chunks), 64)
            self.assertTrue(all(abs(chunk * 120) <= 1920 for chunk in chunks))

    def test_real_ids_and_scoped_relative_state_are_distinct(self):
        _, modules = P.plan()
        native = modules['src/server/mixel_receiver_native.rs'].decode()
        self.assertIn('scopes.insert(owner, peer)', native)
        self.assertIn('scopes.values().any(|value| *value == peer)', native)
        self.assertIn('scopes.remove(&owner)', native)
        self.assertIn('if !scopes.values().any(|value| *value == peer)', native)
        owners = {-1: 44, -2: 44, -3: 55}
        owners.pop(-1)
        self.assertIn(44, owners.values())
        owners.pop(-2)
        self.assertNotIn(44, owners.values())
        self.assertIn(55, owners.values())


def native_controls(proof):
    _, modules = P.plan()
    proof.mkdir(parents=True, exist_ok=False)
    core = modules['src/server/mixel_receiver_receipts.rs']
    (proof / 'mixel_receiver_receipts.rs').write_bytes(core)
    (proof / 'main.rs').write_text('#[path = "mixel_receiver_receipts.rs"]\nmod receipt_core;\n')
    compiler = shutil.which('rustc')
    if not compiler:
        raise RuntimeError('native receipt compiler unavailable; fixture did not run')
    executable = proof / ('receipt-controls.exe' if os.name == 'nt' else 'receipt-controls')
    commands = [[compiler, '--edition=2021', '--test', str(proof / 'main.rs'), '-o', str(executable)],
                [str(executable), '--test-threads=1', '--nocapture']]
    results = []
    for index, argv in enumerate(commands, 1):
        started = time.time()
        record = {'argv': argv, 'scope': 'actual pure production receipt transitions; no OS input sink', 'coreSha256': hashlib.sha256(core).hexdigest()}
        (proof / f'{index}-COMMAND.json').write_text(json.dumps(record, indent=2) + '\n')
        try:
            result = subprocess.run(argv, cwd=proof, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60 if index == 1 else 15, check=False)
        except subprocess.TimeoutExpired as error:
            (proof / f'{index}-stdout').write_bytes(error.stdout or b'')
            (proof / f'{index}-stderr').write_bytes(error.stderr or b'')
            (proof / f'{index}-RESULT.json').write_text(json.dumps({'timeout': True, 'elapsed': time.time()-started})+'\n')
            raise
        (proof / f'{index}-stdout').write_bytes(result.stdout)
        (proof / f'{index}-stderr').write_bytes(result.stderr)
        results.append({'exit': result.returncode, 'elapsed': time.time()-started})
        (proof / f'{index}-RESULT.json').write_text(json.dumps(results[-1])+'\n')
        print(result.stdout.decode(errors='replace'), end='')
        if result.returncode:
            raise RuntimeError('native receipt fixture failed; retained ' + str(proof))
    (proof / 'RESULT.json').write_text(json.dumps({'pass': True, 'coreSha256': hashlib.sha256(core).hexdigest(), 'results': results,
        'nativeApiInjection': False, 'targetApplicationConsumption': False})+'\n')
    print('PASS: actual pure production receipt core compiled and executed; no native OS input')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--proof-root', type=Path)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeSourceControls)
    old = module('mixel_native_acceptance_controls', SCRIPTS / 'test-receiver-native-enigo.py')
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(old.NativeAcceptanceTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    if args.native:
        if args.proof_root is None:
            parser.error('--native requires a fresh --proof-root for retained raw outcomes')
        native_controls(args.proof_root.absolute())
    else:
        print('PASS: source-only native receipt profiles and controls; compiler/native execution not run')


if __name__ == '__main__':
    main()
