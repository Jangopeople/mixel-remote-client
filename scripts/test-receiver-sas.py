#!/usr/bin/env python3
"""Source-only scoped SAS controls; --source adds exact real predecessor fixtures.

--prepare-component writes actual pure Rust decision functions without compiling.
No Windows API, application, service or secure-attention action is executed here.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).absolute().parent
SPEC = importlib.util.spec_from_file_location('mixel_scoped_sas_owner', HERE / 'patch-support-receiver-sas.py')
P = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(P)
SOURCE = None
UPSTREAM_SOURCE = None


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def fixture(root, ending=b'\n'):
    value, _ = P.plan()
    for row in value['files']:
        source = SOURCE / row['name']
        raw = source.read_bytes()
        logical = P.canonical(raw)[0].encode()
        if P.sha(logical) == row['afterSha256']:
            raw = P.transform(raw, row, restore=True)
            logical = P.canonical(raw)[0].encode()
        if P.sha(logical) != row['beforeSha256']:
            raise ValueError('Actual predecessor source fixture does not match')
        path = root / row['name']; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(logical.replace(b'\n', ending)); path.chmod(0o644)
    return value


def pure_item(source, marker):
    start = source.index(marker); opening = source.index('{', start); level = 1; index = opening + 1
    while level:
        if source[index] == '{': level += 1
        elif source[index] == '}': level -= 1
        index += 1
    return source[start:index]


class SourceControls(unittest.TestCase):
    def test_exact_closed_owner_plan_and_leaf(self):
        value, template = P.plan()
        self.assertEqual({row['name'] for row in value['files']}, {
            'src/platform/windows.rs', 'src/ipc.rs', 'src/server/input_service.rs',
            'src/server/mixel_receiver_native.rs'})
        self.assertEqual(value['owned']['name'], 'src/server/mixel_receiver_sas_windows.rs')
        self.assertEqual(hashlib.sha256(template).hexdigest(), value['owned']['sha256'])
        self.assertEqual(value['predecessorSource'], '4364200eb2aa3e4cdc88e13ea969be3926f714b7')

    def test_original_bare_sink_is_a_required_negative_and_candidate_refuses_it(self):
        value, _ = P.plan()
        row = next(row for row in value['files'] if row['name'] == 'src/platform/windows.rs')
        old, new = next((p['before'], p['after']) for p in row['changes'] if 'ipc::Data::SAS =>' in p['before'])
        self.assertIn('send_sas();', old)
        self.assertNotIn('receiver_sas::Captured::capture', old)
        bare = new[new.index('ipc::Data::SAS =>'):new.index('ipc::Data::SASScoped')]
        self.assertIn('request refused', bare)
        self.assertNotIn('send_sas(', bare)
        self.assertLess(new.index('Captured::capture'), new.index('Data::SASReady'))
        self.assertLess(new.index('Data::SASReady'), new.index('Data::SASCommit'))
        self.assertLess(new.index('Data::SASCommit'), new.index('captured.dispatch'))

    def test_unauthenticated_capture_never_waits_for_a_commit(self):
        value, _ = P.plan()
        row = next(row for row in value['files'] if row['name'] == 'src/platform/windows.rs')
        new = next(pair['after'] for pair in row['changes'] if 'ipc::Data::SAS =>' in pair['before'])
        self.assertIn('if let Ok(captured) = captured {', new)
        self.assertLess(new.index('if let Ok(captured) = captured {'), new.index('stream.next_timeout(1000)'))
        self.assertIn('if matches!(timeout(1000, stream.send(&ready)).await, Ok(Ok(())))', new)

    def test_no_temporary_policy_override_or_unscoped_direct_fallback(self):
        value, raw = P.plan(); text = raw.decode()
        for bad in ('KEY_WRITE', 'set_value(', 'delete_value(', 'Global\\', 'DUPLICATE_SAME_ACCESS', 'DUPLICATE_CLOSE_SOURCE'):
            self.assertNotIn(bad, text)
        self.assertIn('KEY_READ', text)
        self.assertIn('matches!(value,Some(1)|Some(3))', text)
        source = next(row for row in value['files'] if row['name'] == 'src/server/input_service.rs')
        new = source['changes'][0]['after']
        self.assertNotIn('unwrap_or(true)', new)
        self.assertNotIn('platform::send_sas', new)
        self.assertIn('secure_attention_owner()', new)

    def test_no_packet_pid_or_namespace_lookup_at_system_sink(self):
        value, raw = P.plan(); text = raw.decode()
        ipc = next(row for row in value['files'] if row['name'] == 'src/ipc.rs')
        message = next(p['after'] for p in ipc['changes'] if 'SASScoped' in p['after'])
        self.assertNotIn('pid:', message)
        self.assertNotIn('session:', message)
        capture = text[text.index('pub(crate) fn capture('):text.index('    fn active(&self)', text.index('pub(crate) fn capture('))]
        self.assertIn('pipe_pid:pipe_pid(pipe,false)', capture)
        self.assertIn('launched,SYNC|QUERY|DUP', capture)
        self.assertIn('duplicate(process.0,h as Handle,SYNC)', capture)
        self.assertNotIn('OpenEventW', capture)
        self.assertNotIn('OpenMutexW', capture)
        self.assertIn('authority_eligible(&authority)', capture)

    def test_readonly_capture_ready_commit_preserves_handle_lifetime(self):
        _, raw = P.plan(); text = raw.decode()
        request = text[text.index('pub(crate) async fn request'):text.index('struct Serial')]
        self.assertLess(request.index('Sender::open'), request.index('Data::SASScoped'))
        self.assertLess(request.index('Data::SASScoped'), request.index('Data::SASReady'))
        self.assertLess(request.index('Data::SASReady'), request.index('Data::SASCommit'))
        self.assertNotIn('WaitForSingleObject(self.effect', request)
        self.assertIn('outcome unknown, do not retry', request)
        self.assertIn('Queued only', request)
        self.assertIn('OpenEventW(SYNC,0', text)
        self.assertIn('OpenMutexW(SYNC,0', text)
        self.assertNotIn('SetEvent', text)
        self.assertNotIn('ResetEvent', text)

    def test_finite_same_thread_sink_with_lifetime_deny_session_and_impersonation(self):
        _, raw = P.plan(); text = raw.decode()
        dispatch = text[text.index('pub(crate) fn dispatch('):]
        self.assertIn('WaitForSingleObject(self.effect.0,WAIT_MS)', dispatch)
        self.assertIn('const WAIT_MS: u32 = 1000', text)
        self.assertLess(dispatch.index('WaitForSingleObject'), dispatch.index('Impersonation::enter'))
        self.assertLess(dispatch.index('Impersonation::enter'), dispatch.index('SAS generation denied at actual sink'))
        self.assertLess(dispatch.index('SAS generation denied at actual sink'), dispatch.index('SendSAS(0)'))
        self.assertIn('std::marker::PhantomData<std::rc::Rc<()>>', text)
        self.assertIn('WaitForSingleObject(self.revoked.0,0)==TIMEOUT', text)
        self.assertIn('alive(self.process.0)', text)
        self.assertIn('token_session(self.token.0)==Some(self.session)', text)
        self.assertIn('impl Drop for Impersonation', text)
        self.assertIn('RevertToSelf()', text)
        self.assertIn('std::process::exit(1)', text)
        self.assertIn('ABANDONED =>', dispatch)

    def test_scope_export_does_not_accept_trusted_default_or_revoked_owner(self):
        value, _ = P.plan()
        row = next(row for row in value['files'] if row['name'] == 'src/server/mixel_receiver_native.rs')
        new = row['changes'][0]['after']
        self.assertIn('scope.owner != "@trusted-native-input" && (scope.admit)()', new)
        self.assertNotIn('Some("@trusted-native-input"', new)

    def test_manifested_effect_is_not_a_delivery_acknowledgment(self):
        value, raw = P.plan()
        windows = next(row for row in value['files'] if row['name'] == 'src/platform/windows.rs')
        self.assertTrue(any('secure-screen delivery is not acknowledged' in p['after'] for p in windows['changes']))
        self.assertIn('extern "system" { fn SendSAS(as_user: i32); }', raw.decode())
        self.assertNotIn('SendSAS(as_user: i32) ->', raw.decode())


class ActualSourceControls(unittest.TestCase):
    def test_whole_actual_forward_reverse_idempotence_check_and_modes_lf_crlf(self):
        for ending in (b'\n', b'\r\n'):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); fixture(root, ending)
                before = snapshot(root)
                self.assertFalse(P.is_current(root))
                self.assertEqual(P.apply(root, check=True)['plannedWrites'], 5)
                self.assertEqual(snapshot(root), before)
                self.assertEqual(P.apply(root)['writes'], 5)
                after = snapshot(root); self.assertTrue(P.is_current(root))
                self.assertEqual(P.apply(root)['writes'], 0)
                P.apply(root, restore=True, check=True); self.assertEqual(snapshot(root), after)
                P.apply(root, restore=True); self.assertEqual(snapshot(root), before)
                self.assertFalse(P.is_current(root))

    def test_actual_mixed_partial_and_unrelated_drift_refuse_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); value = fixture(root); before = snapshot(root)
            for row in value['files']:
                path = root / row['name']; old = path.read_bytes()
                for mutant in (old + b'// unrelated drift\n', old.replace(b'\n', b'\r\n', 1), P.transform(old, row)):
                    path.write_bytes(mutant); changed = snapshot(root)
                    for restore in (False, True):
                        with self.assertRaises(ValueError): P.apply(root, restore=restore)
                        self.assertEqual(snapshot(root), changed)
                    path.write_bytes(old)
            self.assertEqual(snapshot(root), before)

    def test_actual_linked_old_and_new_targets_refuse_before_writes(self):
        for kind in ('hardlink', 'link', 'parent', 'owned-link'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                outer = Path(directory).resolve(); root = outer / 'source'; root.mkdir(); value = fixture(root)
                path = root / value['files'][0]['name']
                if kind == 'owned-link':
                    foreign = outer / 'foreign.rs'; foreign.write_bytes(b'unrelated sentinel')
                    (root / value['owned']['name']).symlink_to(foreign)
                elif kind == 'parent':
                    path.parent.rename(outer / 'outside'); path.parent.symlink_to(outer / 'outside', target_is_directory=True)
                else:
                    foreign = outer / 'foreign.rs'; path.rename(foreign)
                    if kind == 'hardlink': path.hardlink_to(foreign)
                    else: path.symlink_to(foreign)
                before = snapshot(outer)
                with self.assertRaises(ValueError): P.apply(root)
                self.assertEqual(snapshot(outer), before)

    def test_unknown_owned_module_drift_refuses_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); value = fixture(root); P.apply(root)
            (root / value['owned']['name']).write_bytes(b'// foreign module\n'); before = snapshot(root)
            with self.assertRaises(ValueError): P.apply(root, restore=True)
            self.assertEqual(snapshot(root), before)

    def test_real_upstream_fresh_only_restores_without_writes(self):
        value, _ = P.plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for row in value['files']:
                if row['freshSha256'] is None:
                    (root / row['name']).parent.mkdir(parents=True, exist_ok=True)
                    continue
                result = subprocess.run(['git', '-C', str(UPSTREAM_SOURCE), 'show', 'HEAD:' + row['name']], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(P.sha(P.canonical(result.stdout)[0].encode()), row['freshSha256'])
                path = root / row['name']; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(result.stdout)
            before = snapshot(root)
            self.assertEqual(P.apply(root, restore=True, check=True)['profile'], 'fresh')
            self.assertEqual(P.apply(root, restore=True)['writes'], 0)
            with self.assertRaises(ValueError): P.apply(root)
            self.assertEqual(snapshot(root), before)

    def test_actual_uniform_crlf_helper_plan_and_template_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); scripts = base / 'scripts'; scripts.mkdir()
            for name in ('patch-support-receiver-sas.py', 'support-receiver-sas-plan.json', 'support-receiver-sas-windows.rs'):
                raw = (HERE / name).read_bytes().replace(b'\r\n', b'\n')
                (scripts / name).write_bytes(raw.replace(b'\n', b'\r\n'))
            root = base / 'source'; root.mkdir(); fixture(root, b'\r\n'); before = snapshot(root)
            for flags in ([], ['--check'], ['--restore']):
                result = subprocess.run([sys.executable, str(scripts / 'patch-support-receiver-sas.py'), '--root', str(root), *flags], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(snapshot(root), before)

    def test_actual_python_cli_check_apply_restore_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); fixture(root); before = snapshot(root)
            for flags in (['--check'], [], ['--check'], ['--restore', '--check'], ['--restore']):
                result = subprocess.run([sys.executable, str(HERE/'patch-support-receiver-sas.py'), '--root', str(root), *flags], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn('profile', json.loads(result.stdout))
            self.assertEqual(snapshot(root), before)


def prepare_component(output):
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = (HERE/'support-receiver-sas-windows.rs').read_text()
    names = ('fn identity(', 'fn policy_eligible(', 'struct Authority', 'fn authority_eligible(', 'fn reserve(')
    bodies = [pure_item(source, name) for name in names]
    tail = r'''
fn valid() -> Authority { Authority { pipe_pid:Some(10),launched_pid:10,born:20,owner:Some((10,20)),publisher_session:Some(3),token_session:Some(3),target_session:3,service_session:Some(0),service_system:true,publisher_system:true,image_match:true,process_alive:true } }
#[test] fn current_authority() { assert!(authority_eligible(&valid())); }
#[test] fn wrong_peer_birth_or_owner() { for pair in [None,Some((11,20)),Some((10,21))] { let mut a=valid();a.owner=pair;assert!(!authority_eligible(&a)); } let mut a=valid();a.pipe_pid=Some(99);assert!(!authority_eligible(&a)); }
#[test] fn missing_kernel_queries_fail_closed() { let mut a=valid();a.pipe_pid=None;assert!(!authority_eligible(&a));let mut a=valid();a.publisher_session=None;assert!(!authority_eligible(&a));let mut a=valid();a.token_session=None;assert!(!authority_eligible(&a));let mut a=valid();a.service_session=None;assert!(!authority_eligible(&a)); }
#[test] fn wrong_session_or_authority() { let mut a=valid();a.target_session=0;assert!(!authority_eligible(&a));let mut a=valid();a.target_session=u32::MAX;assert!(!authority_eligible(&a));let mut a=valid();a.token_session=Some(4);assert!(!authority_eligible(&a));let mut a=valid();a.publisher_system=false;assert!(!authority_eligible(&a));let mut a=valid();a.service_system=false;assert!(!authority_eligible(&a));let mut a=valid();a.image_match=false;assert!(!authority_eligible(&a));let mut a=valid();a.process_alive=false;assert!(!authority_eligible(&a)); }
#[test] fn services_policy_only() { for value in [None,Some(0),Some(2),Some(4),Some(u32::MAX)] { assert!(!policy_eligible(value)); } assert!(policy_eligible(Some(1)));assert!(policy_eligible(Some(3))); }
#[test] fn exact_owner_identity() { assert_eq!(identity("10-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),Some((10,20)));for value in ["0-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","10-0-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","+10-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","010-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","10-020-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","10-20-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","10-20-aaaa","10-20-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-extra"] { assert_eq!(identity(value),None); } }
#[test] fn replays_and_sequence_exhaustion() { let mut seen=(0,0,0);assert!(reserve(&mut seen,10,20,1));assert!(!reserve(&mut seen,10,20,1));assert!(!reserve(&mut seen,10,20,0));assert!(reserve(&mut seen,10,20,2));assert!(!reserve(&mut seen,10,20,1));assert!(reserve(&mut seen,10,21,1));assert!(reserve(&mut seen,10,21,u64::MAX));assert!(!reserve(&mut seen,10,21,u64::MAX));let before=seen;assert!(!reserve(&mut seen,0,1,9));assert_eq!(seen,before); }
'''
    (output/'decision-core.rs').write_text('\n\n'.join(bodies) + '\n' + tail)
    (output/'SOURCE-BINDING.json').write_text(json.dumps({'scope':'Prepared exact pure production SAS decision bodies; NO compiler or OS APIs run', 'source':str(HERE/'support-receiver-sas-windows.rs'),'sourceSHA256':hashlib.sha256(source.encode()).hexdigest(),'functions':{name:hashlib.sha256(body.encode()).hexdigest() for name,body in zip(names,bodies)},'testCases':7,'nativeExecution':False,'compiled':False},indent=2)+'\n')
    print('PREPARED: seven exact production decision-core Rust cases; compile and execution pending')


def main():
    global SOURCE, UPSTREAM_SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--upstream-source', type=Path, help='Explicit source-only Git carrier for fresh-profile tests; defaults to --source')
    parser.add_argument('--prepare-component', type=Path)
    args = parser.parse_args(); SOURCE = args.source.resolve(strict=True) if args.source else None
    UPSTREAM_SOURCE = args.upstream_source.resolve(strict=True) if args.upstream_source else SOURCE
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SourceControls)
    if SOURCE is not None: suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ActualSourceControls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    print('PASS: scoped SAS source controls' + (' and exact predecessor LF/CRLF fixture path' if SOURCE else '') + '; native API/service delivery not run')
    if args.prepare_component: prepare_component(args.prepare_component)


if __name__ == '__main__': main()
