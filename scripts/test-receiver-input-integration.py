#!/usr/bin/env python3
"""Exercise exact terminal hook inverses and complete receiver prior admissions."""
import contextlib
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'mixel_receiver_integration_controls',
    ROOT / 'scripts/receiver_input_integration_projection.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class Controls(unittest.TestCase):
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

    def test_both_whole_profiles_inspect_before_any_private_copy(self):
        events = []
        class Owner:
            def __init__(self, name, final):
                self.name, self.final = name, final
            def is_current(self, source):
                events.append(('inspect', self.name))
                if isinstance(self.final, Exception):
                    raise self.final
                return self.final
            @contextlib.contextmanager
            def previous(self, source):
                events.append(('copy', self.name))
                yield Path(source) / self.name
        with tempfile.TemporaryDirectory(prefix='mixel-receiver-admission-') as temporary:
            source = Path(temporary)
            receiver, native = Owner('receiver', True), Owner('native', True)
            with mock.patch.object(P, 'owner', side_effect=[receiver, native]):
                with P.previous(source) as prior:
                    self.assertEqual(prior, source / 'receiver/native')
            self.assertEqual(events, [('inspect', 'receiver'), ('inspect', 'native'),
                                      ('copy', 'receiver'), ('copy', 'native')])
            for left, right in ((True, False), (False, True),
                                (RuntimeError('receiver drift'), True),
                                (True, RuntimeError('native drift'))):
                events.clear()
                with mock.patch.object(P, 'owner', side_effect=[Owner('receiver', left), Owner('native', right)]):
                    with self.assertRaises(RuntimeError):
                        with P.previous(source):
                            self.fail('Partial/drift source admitted')
                self.assertFalse(any(event[0] == 'copy' for event in events))
            events.clear()
            with mock.patch.object(P, 'owner', side_effect=[Owner('receiver', False), Owner('native', False)]):
                with P.previous(source) as prior:
                    self.assertEqual(prior, source)
            self.assertEqual(events, [('inspect', 'receiver'), ('inspect', 'native')])

    def test_private_restore_checks_both_before_writing_and_requires_original_after(self):
        events = []
        class Owner:
            def __init__(self, name): self.name, self.final = name, True
            def is_current(self, root): return self.final
            def apply(self, root, restore=False, check=False):
                self_outer.assertTrue(restore)
                events.append((self.name, check))
                if not check: self.final = False
        self_outer = self
        owners = [Owner('receiver'), Owner('native')]
        with mock.patch.object(P, 'owner', side_effect=owners):
            P.restore_private(Path('/unused-pure-control'))
        self.assertEqual(events, [('receiver', True), ('native', True),
                                  ('receiver', False), ('native', False)])

    def test_new_owner_hash_drift_refuses_before_import(self):
        original = P.digest
        with mock.patch.object(P, 'digest', side_effect=lambda raw: '0' * 64 if b'def apply(' in raw else original(raw)):
            with self.assertRaisesRegex(ValueError, 'Changed exact receiver integration owner'):
                P.owner(P.OWNERS[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
