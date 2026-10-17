#!/usr/bin/env python3
"""Exercise exact terminal hook inverses and complete receiver prior admissions."""
import contextlib
import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'mixel_receiver_integration_controls',
    ROOT / 'scripts/receiver_input_integration_projection.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def footer_guard():
    source = (ROOT / 'scripts/test-branding.py').read_text()
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == 'require_receiver_footer_ownership']
    if len(functions) != 1:
        raise AssertionError('Exact actual footer ownership guard required')
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'actual-footer-ownership', 'exec'), namespace)
    return namespace['require_receiver_footer_ownership'], tree


class Controls(unittest.TestCase):
    def test_footer_inverse_accepts_only_exact_dependency_directory_removals(self):
        guard, _ = footer_guard()
        directories = {'.mixel-deps/' + name + suffix
                       for name in ('evdev', 'parity-tokio-ipc')
                       for suffix in ('', '/examples', '/src')}
        before = {name: ('file', 'before') for name in P.owned_targets()}
        before.update({name: ('directory', '') for name in directories})
        after = {name: ('file', 'after') for name in P.owned_targets()}
        changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
        self.assertFalse(changed <= P.owned_targets(), 'Original file-only guard must reproduce the real refusal')
        guard(before, after, P)
        guard(after, after, P)

    def test_footer_inverse_still_refuses_foreign_files_directories_and_kind_changes(self):
        guard, _ = footer_guard()
        for name, old, new in (
                ('.mixel-deps', ('directory', ''), None),
                ('src/server', ('directory', ''), None),
                ('.mixel-deps/evdev/foreign', ('directory', ''), None),
                ('.mixel-deps/evdev/notes', ('file', 'old'), None),
                ('.mixel-deps/evdev', ('symlink', 'elsewhere'), None),
                ('.mixel-deps/evdev', ('directory', ''), ('file', 'new')),
                ('.mixel-deps/evdev', None, ('directory', '')),
                ('src/core_main.rs', ('file', 'old'), ('symlink', 'elsewhere'))):
            with self.subTest(name=name, old=old, new=new):
                before = {} if old is None else {name: old}
                after = {} if new is None else {name: new}
                with self.assertRaises(AssertionError):
                    guard(before, after, P)

    def test_actual_footer_inverse_calls_the_guard_and_keeps_plan_drift_fatal(self):
        guard, tree = footer_guard()
        caller = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == 'restore_footer_terminal_layers')
        calls = [node for node in ast.walk(caller) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'require_receiver_footer_ownership']
        self.assertEqual(len(calls), 1)
        with mock.patch.object(P, 'owner', side_effect=ValueError('exact owner drift')):
            with self.assertRaisesRegex(ValueError, 'exact owner drift'):
                guard({}, {}, P)

    def test_controls_are_required_by_both_release_entrypoints(self):
        for name, heading in (('build.yml', 'Verify release staging and build configuration'),
                              ('preflight.yml', 'Run release staging and configuration regression tests')):
            text = (ROOT / '.github/workflows' / name).read_text()
            step = text.split('      - name: ' + heading + '\n', 1)[1].split('      - ', 1)[0]
            command = '          python3 scripts/test-receiver-input-integration.py\n'
            self.assertEqual(step.count(command), 1)
            self.assertEqual(text.count(command), 1)
            self.assertNotIn('if:', step)
            self.assertNotIn('continue-on-error', step)
            self.assertNotIn('||', step)
        for name, job_name in (('build.yml', 'input-regressions'), ('preflight.yml', 'receiver-native')):
            text = (ROOT / '.github/workflows' / name).read_text()
            job = text.split('  ' + job_name + ':\n', 1)[1]
            job = re.split(r'\n  [a-zA-Z0-9_-]+:\n', job, maxsplit=1)[0]
            inert = job.split('      - name: Verify Windows custody source and inert component preparation\n', 1)[1].split('      - ', 1)[0]
            self.assertEqual(inert.count('python3 scripts/test-receiver-custodian.py\n'), 1)
            self.assertEqual(inert.count('python3 scripts/fixtures/receiver-custodian-native/prepare.py \\\n'), 1)
            self.assertIn('--output "$RUNNER_TEMP/mixel-receiver-custodian-prepared"\n', inert)
            self.assertNotIn('if:', inert)
            native = job.split('      - name: Prove actual Windows helper custody on the complete generated source\n', 1)[1].split('      - ', 1)[0]
            self.assertIn("if: runner.os == 'Windows'\n", native)
            self.assertIn('RDREPO: ./rustdesk\n', native)
            self.assertIn('source scripts/use-windows-msvc-bash.sh\n', native)
            self.assertEqual(native.count('python scripts/fixtures/receiver-custodian-native/prepare.py \\\n'), 1)
            self.assertIn('--output "$RUNNER_TEMP/mixel-receiver-custodian-native" --source-root "$RDREPO" --run\n', native)
            for step in (inert, native):
                self.assertNotIn('continue-on-error', step)
                self.assertNotIn('||', step)
            for pattern in ('mixel-receiver-custodian-prepared/SOURCE-BINDINGS.json',
                            'mixel-receiver-custodian-prepared/PREPARED-MANIFEST.json',
                            'mixel-receiver-custodian-native/*.json',
                            'mixel-receiver-custodian-native/*.jsonl',
                            'mixel-receiver-custodian-native/*.stdout',
                            'mixel-receiver-custodian-native/*.stderr',
                            '!${{ runner.temp }}/mixel-receiver-custodian-native/target/**'):
                self.assertIn(pattern, job)
            if name == 'preflight.yml':
                self.assertIn('timeout-minutes: 25\n', job)
                self.assertLess(job.index('Resolve the pinned complete Windows custody source'), job.index('Fetch pinned complete Windows custody source'))
                self.assertLess(job.index('Fetch pinned complete Windows custody source'), job.index('Apply complete reviewed Windows custody source ownership'))
                self.assertLess(job.index('Apply complete reviewed Windows custody source ownership'), job.index('Prove actual Windows helper custody'))
                self.assertIn('python scripts/release-artifacts.py config\n', job)
                self.assertIn('UPSTREAM_VERSION: ${{ steps.custody_config.outputs.upstream_version }}\n', job)
                self.assertIn('git -c core.autocrlf=false clone --depth 1 --branch "$UPSTREAM_VERSION" --recurse-submodules', job)

    def test_actual_whole_prior_files_recover_and_compose_without_other_changes(self):
        for name, row in P.plan()['files'].items():
            text, _ = P.canonical((ROOT / name).read_bytes())
            for newline in ('\n', '\r\n'):
                with self.subTest(name=name, newline=repr(newline)):
                    final = text.replace('\n', newline).encode()
                    previous = P.inverse(final, name)
                    self.assertEqual(P.digest(P.canonical(previous)[0].encode()), row['prior_sha256'])
                    self.assertEqual(P.compose(previous, name), final)
                    self.assertEqual(P.canonical(previous)[1], newline)

    def test_each_missing_duplicate_changed_moved_or_reordered_hunk_refuses(self):
        for name, row in P.plan()['files'].items():
            text, _ = P.canonical((ROOT / name).read_bytes())
            for hunk in row['changes']:
                old, new = hunk['old'], hunk['new']
                old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
                prefix = 0
                while prefix < min(len(old_lines), len(new_lines)) and old_lines[prefix] == new_lines[prefix]:
                    prefix += 1
                suffix = 0
                while (suffix < min(len(old_lines), len(new_lines)) - prefix
                       and old_lines[-suffix - 1] == new_lines[-suffix - 1]):
                    suffix += 1
                addition = ''.join(new_lines[prefix:len(new_lines) - suffix if suffix else None])
                self.assertTrue(addition)
                moved = text.replace(new, old, 1) + addition
                mutants = (text.replace(new, old, 1),
                    text.replace(new, new * 2, 1), moved,
                    text.replace(new, new.replace(addition,
                        addition.replace('\n', '\n# foreign receiver mutation\n', 1), 1), 1))
                for mutated in mutants:
                    for newline in ('\n', '\r\n'):
                        with self.subTest(name=name, hunk=P.digest(new.encode()), newline=repr(newline)):
                            self.assertNotEqual(mutated, text)
                            with self.assertRaises(ValueError):
                                P.inverse(mutated.replace('\n', newline).encode(), name)
            if len(row['changes']) > 1:
                first, last = row['changes'][0]['new'], row['changes'][-1]['new']
                reordered = text.replace(first, '\0receiver-first\0', 1).replace(last, first, 1).replace('\0receiver-first\0', last, 1)
                with self.assertRaises(ValueError):
                    P.inverse(reordered.encode(), name)

    def test_exact_historical_head_is_only_a_committed_carrier(self):
        for name in P.plan()['files']:
            final = (ROOT / name).read_bytes()
            previous = P.inverse(final, name)
            self.assertEqual(P.committed(previous, name), previous)
            self.assertEqual(P.committed(final, name), previous)
            with self.assertRaises(ValueError):
                P.inverse(previous, name)
            with self.assertRaises(ValueError):
                P.committed(previous + b'# unreviewed prior drift\n', name)

    def test_unknown_additional_invocations_refuse_and_other_drift_reaches_old_guards(self):
        for name in P.plan()['files']:
            final = (ROOT / name).read_bytes()
            for token in (*P.OWNERS, 'receiver_native_only', '  receiver-native:'):
                with self.assertRaises(ValueError):
                    P.inverse(final + ('\n# ' + token + '\n').encode(), name)
            unrelated = b'\n# separately reviewed prior-layer change\n'
            self.assertEqual(P.inverse(final + unrelated, name), P.inverse(final, name) + unrelated)
            self.assertNotEqual(P.digest(P.canonical(P.inverse(final + unrelated, name))[0].encode()),
                                P.plan()['files'][name]['prior_sha256'])

    def test_mixed_newlines_and_unknown_files_refuse(self):
        for name in P.plan()['files']:
            raw = (ROOT / name).read_bytes().replace(b'\r\n', b'\n')
            with self.assertRaises(ValueError):
                P.inverse(raw.replace(b'\n', b'\r\n', 1), name)
        with self.assertRaises(ValueError):
            P.inverse(b'', 'foreign.yml')

    def test_base_whole_profiles_inspect_before_private_copy(self):
        events = []
        class Base:
            def __init__(self, name, final): self.name, self.final = name, final
            def is_current(self, source):
                events.append(('inspect', self.name))
                if isinstance(self.final, Exception): raise self.final
                return self.final
            @contextlib.contextmanager
            def previous(self, source):
                events.append(('copy', self.name)); yield Path(source) / self.name
            def apply(self, source, restore=False, check=False):
                self_outer.assertTrue(restore); events.append(('inverse', self.name, check))
        class Outer:
            def __init__(self, profile): self.profile = profile
            def inspect(self, source):
                events.append(('inspect', 'uinput'))
                return None, None, None, self.profile
        self_outer = self
        source = Path('/unused-pure-control')
        for left, right in ((True, True), (True, False), (False, True),
                            (RuntimeError('receiver drift'), True),
                            (True, RuntimeError('native drift'))):
            events.clear()
            owners = {'patch-support-receiver-uinput.py': Outer('before'),
                      P.BASE_OWNERS[0]: Base('receiver', left), P.BASE_OWNERS[1]: Base('native', right)}
            with mock.patch.object(P, 'owner', side_effect=owners.__getitem__):
                if left is True and right is True:
                    with P._device_previous(source) as prior: self.assertEqual(prior, source / 'receiver')
                    self.assertEqual(events, [('inspect', 'uinput'), ('inspect', 'receiver'),
                        ('inspect', 'native'), ('copy', 'receiver'), ('inverse', 'native', False)])
                else:
                    with self.assertRaises(RuntimeError):
                        with P._device_previous(source): self.fail('Partial/drift source admitted')
                    self.assertFalse(any(event[0] == 'copy' for event in events))
        for profile in ('fresh', 'prior'):
            events.clear()
            owners = {'patch-support-receiver-uinput.py': Outer(profile),
                      P.BASE_OWNERS[0]: Base('receiver', False), P.BASE_OWNERS[1]: Base('native', False)}
            with mock.patch.object(P, 'owner', side_effect=owners.__getitem__):
                with P._device_previous(source) as prior: self.assertEqual(prior, source)
            self.assertEqual(events, [('inspect', 'uinput'), ('inspect', 'receiver'), ('inspect', 'native')])

    def test_latest_owner_inverses_first_in_one_private_snapshot(self):
        events = []
        source, private = Path('/unused-supplied'), Path('/unused-private')
        class Outer:
            def inspect(self, root):
                self_outer.assertEqual(root, source); events.append('outer-check')
                return None, None, None, 'after'
            @contextlib.contextmanager
            def previous(self, root):
                self_outer.assertEqual(root, source); events.append('copy+outer-inverse'); yield private
        class Base:
            def __init__(self, name): self.name = name
            def is_current(self, root):
                self_outer.assertEqual(root, private); events.append(self.name+'-check'); return True
            def apply(self, root, restore=False, check=False):
                self_outer.assertEqual(root, private); self_outer.assertTrue(restore)
                events.append(self.name+('-plan' if check else '-inverse'))
        self_outer = self
        owners = {'patch-support-receiver-uinput.py': Outer(),
                  P.BASE_OWNERS[0]: Base('receiver'), P.BASE_OWNERS[1]: Base('native')}
        with mock.patch.object(P, 'owner', side_effect=owners.__getitem__):
            with P._device_previous(source) as prior: self.assertEqual(prior, private)
        self.assertEqual(events, ['outer-check', 'copy+outer-inverse', 'receiver-check', 'native-check',
                                 'receiver-plan', 'native-plan', 'receiver-inverse', 'native-inverse'])

    def test_outer_drift_never_falls_back_to_an_earlier_prefix(self):
        outer = mock.Mock(); outer.inspect.side_effect = ValueError('exact device sink drift')
        with mock.patch.object(P, 'owner', return_value=outer) as load:
            with self.assertRaisesRegex(ValueError, 'device sink drift'):
                with P._device_previous(Path('/unused-pure-control')): self.fail('drift admitted')
        load.assert_called_once_with('patch-support-receiver-uinput.py')
        outer.previous.assert_not_called()

    def test_newer_owners_inverse_latest_first_without_a_second_copy(self):
        supplied, private = Path('/unused-supplied'), Path('/unused-private')
        events = []
        class Outer:
            def __init__(self, name): self.name = name
            def is_current(self, root):
                events.append(('inspect', self.name, root)); return True
            @contextlib.contextmanager
            def previous(self, root):
                events.append(('copy', self.name, root)); yield private
            def apply(self, root, restore=False, check=False):
                self_outer.assertEqual(root, private); self_outer.assertTrue(restore)
                events.append(('plan' if check else 'inverse', self.name, root))
        self_outer = self
        names = tuple(reversed(P.LATER_OWNERS))
        self.assertEqual(names[0], 'patch-support-receiver-custodian.py')
        self.assertTrue(names)
        owners = {name: Outer(name) for name in names}
        @contextlib.contextmanager
        def device(root, private=False):
            self.assertEqual(root, Path('/unused-private')); self.assertTrue(private)
            events.append(('earlier', 'device', root)); yield root
        with mock.patch.object(P, 'owner', side_effect=owners.__getitem__), \
                mock.patch.object(P, '_device_previous', device):
            with P.previous(supplied) as prior: self.assertEqual(prior, private)
        expected = [('inspect', names[0], supplied), ('copy', names[0], supplied)]
        for name in names[1:]:
            expected.extend((('inspect', name, private), ('plan', name, private), ('inverse', name, private)))
        expected.append(('earlier', 'device', private)); self.assertEqual(events, expected)
        failed = mock.Mock(); failed.is_current.side_effect = ValueError('outer drift')
        with mock.patch.object(P, 'owner', return_value=failed) as load, \
                mock.patch.object(P, '_device_previous') as inner:
            with self.assertRaisesRegex(ValueError, 'outer drift'):
                with P.previous(supplied): self.fail('drift admitted')
            load.assert_called_once_with(names[0]); inner.assert_not_called(); failed.previous.assert_not_called()

    def test_historical_failure_checks_private_source_before_supplied_inverse(self):
        with tempfile.TemporaryDirectory(prefix='mixel-receiver-prior-refusal-') as temporary:
            supplied = Path(temporary) / 'supplied'
            private = Path(temporary) / 'private'
            supplied.mkdir(); private.mkdir()
            marker = supplied / 'unchanged'
            marker.write_bytes(b'terminal receiver remains intact')
            failure = RuntimeError('exact historical refusal')
            historical = mock.Mock()
            historical.apply.side_effect = failure
            with mock.patch.object(P, 'previous', return_value=contextlib.nullcontext(private)), \
                    mock.patch.object(P, 'prior_owner', return_value=historical):
                with self.assertRaises(RuntimeError) as caught:
                    P.check_priors(supplied)
            self.assertIs(caught.exception, failure)
            historical.apply.assert_called_once_with(private, restore=True, check=True)
            self.assertEqual(marker.read_bytes(), b'terminal receiver remains intact')
        macro = (ROOT / 'scripts/apply-branding.sh').read_text()
        command = 'receiver_input_integration_projection.py" --root "$RDREPO" --check-priors\n'
        self.assertEqual(macro.count(command), 1)
        self.assertLess(macro.index(command), macro.index('patch-support-receiver-input.py" --root "$RDREPO" --restore\n'))
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        bridge = workflow.split('      - name: Generate bridge\n', 1)[1].split('      - ', 1)[0]
        self.assertEqual(bridge.count('receiver_input_integration_projection.py" --root . --check-priors\n'), 1)
        self.assertLess(bridge.index('--check-priors\n'), bridge.index('patch-support-receiver-input.py" --root . --restore\n'))

    def test_private_restore_checks_entire_chain_before_supplied_mutation(self):
        events = []
        class Base:
            def __init__(self, name): self.name, self.final = name, True
            def is_current(self, root): return self.final
            def apply(self, root, restore=False, check=False):
                self_outer.assertTrue(restore); events.append((self.name, check))
                if not check: self.final = False
        self_outer = self
        labels = {name: name.removeprefix('patch-support-receiver-').removesuffix('.py') for name in P.OWNERS}
        owners = {name: Base(labels[name]) for name in P.OWNERS}
        @contextlib.contextmanager
        def previous(root):
            events.append(('entire-chain-private-check', True)); yield Path('/unused-private')
        with mock.patch.object(P, 'owner', side_effect=owners.__getitem__), mock.patch.object(P, 'previous', previous):
            P.restore_private(Path('/unused-pure-control'))
        self.assertEqual(events, [('entire-chain-private-check', True)] +
            [(labels[name], check) for name in reversed(P.OWNERS[2:]) for check in (True, False)] +
            [('input', True), ('native', True), ('input', False), ('native', False)])
        events.clear()
        with mock.patch.object(P, 'previous', side_effect=ValueError('inner owner drift')), \
                mock.patch.object(P, 'owner') as owner:
            with self.assertRaises(ValueError): P.restore_private(Path('/unused-pure-control'))
            owner.assert_not_called()
        self.assertEqual(events, [])

    def test_new_owner_hash_drift_refuses_before_import(self):
        original = P.digest
        with mock.patch.object(P, 'digest', side_effect=lambda raw: '0' * 64 if b'def apply(' in raw else original(raw)):
            with self.assertRaisesRegex(ValueError, 'Changed exact receiver integration owner'):
                P.owner(P.OWNERS[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
