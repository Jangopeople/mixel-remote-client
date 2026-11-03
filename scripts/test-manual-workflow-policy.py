#!/usr/bin/env python3
"""Source-only guard for GitHub's manual break-glass workflow policy.

Require the repository's canonical block-style trigger header. actionlint checks
the complete YAML separately; this guard uses only Python's standard library.
It never invokes a workflow, runner, provider, compiler, or network service.
"""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'manual_workflow_policy', ROOT / 'scripts/manual_workflow_policy_projection.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def manual_header(raw):
    logical = raw.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    if b'\r' in logical or logical.replace(b'\n', newline) != raw:
        raise ValueError('Mixed workflow source newlines')
    text = logical.decode('utf-8', errors='strict')
    keys = re.findall(r'''(?m)^(?:on|'on'|"on")[ ]*:.*$''', text)
    if keys != ['on:']:
        raise ValueError('Exactly one canonical block-style on header required')
    if re.search(r'(?m)^<<[ ]*:', text):
        raise ValueError('Merged workflow root cannot establish manual-only events')
    after = text.split('\non:\n', 1) if not text.startswith('on:\n') else ['', text[4:]]
    if len(after) != 2:
        raise ValueError('Canonical on header required')
    lines = []
    for line in after[1].splitlines(keepends=True):
        if line.strip() and not line.startswith((' ', '#')):
            break
        lines.append(line)
    header = ''.join(lines)
    if '\t' in header:
        raise ValueError('Trigger header must use spaces')
    children = [line for line in header.splitlines()
                if line.strip() and not line.lstrip().startswith('#')
                and len(line) - len(line.lstrip()) <= 2]
    if children != ['  workflow_dispatch:']:
        raise ValueError('workflow_dispatch must be the only trigger')
    if re.search(r'(?m)^ +<<[ ]*:', header):
        raise ValueError('Merged event configuration is not allowed')
    return 'on:\n' + header


class Controls(unittest.TestCase):
    def test_every_workflow_is_explicit_manual_break_glass_only(self):
        directory = ROOT / '.github/workflows'
        workflows = sorted([*directory.glob('*.yml'), *directory.glob('*.yaml')])
        self.assertTrue(workflows, 'Workflow policy inventory must not be empty')
        for path in workflows:
            with self.subTest(workflow=path.name):
                self.assertTrue(path.is_file() and not path.is_symlink())
                self.assertTrue(manual_header(path.read_bytes()).startswith('on:\n  workflow_dispatch:\n'))

    def test_plain_dispatch_and_nested_manual_inputs_are_allowed(self):
        for source in (
                'name: inert\non:\n  workflow_dispatch:\njobs:\n',
                'on:\n  workflow_dispatch:\n    inputs:\n      release:\n'
                '        type: boolean\n        default: false\njobs:\n'):
            for ending in ('\n', '\r\n'):
                with self.subTest(source=source, ending=ending):
                    manual_header(source.replace('\n', ending).encode())

    def test_all_automatic_and_reusable_trigger_additions_refuse(self):
        for event in ('push', 'pull_request', 'pull_request_target', 'schedule',
                      'workflow_run', 'workflow_call', 'repository_dispatch',
                      'release', 'issues', 'check_suite', 'merge_group', 'unknown_event'):
            for events in (f'  {event}:\n  workflow_dispatch:\n',
                           f'  workflow_dispatch:\n  {event}:\n'):
                with self.subTest(event=event, events=events):
                    with self.assertRaises(ValueError):
                        manual_header(('on:\n' + events + 'jobs:\n').encode())

    def test_missing_duplicate_flow_sequence_alias_and_merged_headers_refuse(self):
        sources = (
            'name: inert\njobs:\n',
            'on:\n  workflow_dispatch:\non:\n  workflow_dispatch:\n',
            'on: [workflow_dispatch, push]\n',
            'on: {workflow_dispatch: null, schedule: []}\n',
            '"on":\n  workflow_dispatch:\n',
            "'on':\n  workflow_dispatch:\n",
            'on:\n  - workflow_dispatch\n',
            'on: *events\n',
            'on:\n  <<: *events\n  workflow_dispatch:\n',
            'on:\n  workflow_dispatch: *manual\n',
            'on:\n  workflow_dispatch:\n    <<: *events\n',
            '<<: *defaults\non:\n  workflow_dispatch:\n',
        )
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    manual_header(source.encode())


class ProjectionControls(unittest.TestCase):
    def test_complete_workflows_roundtrip_in_both_physical_newline_profiles(self):
        for name, row in P.plan().items():
            current = (ROOT / name).read_bytes()
            logical, _ = P.canonical(current)
            self.assertTrue(logical.startswith(row['prefix'] + row['manualHeader']))
            suffix = logical[len(row['prefix'] + row['manualHeader']):]
            for hunk in row['bodyChanges']:
                suffix = suffix.replace(hunk['new'], hunk['old'], 1)
            historical = row['prefix'] + row['historicalHeader'] + suffix
            for newline in (b'\n', b'\r\n'):
                with self.subTest(name=name, newline=newline):
                    raw = logical.encode().replace(b'\n', newline)
                    expected = historical.encode().replace(b'\n', newline)
                    self.assertEqual(P.project_to_historical(raw, name), expected)
                    self.assertEqual(P.project_to_historical(expected, name), expected)
                    self.assertEqual(P.compose_manual(expected, name), raw)
                    with self.assertRaises(ValueError):
                        P.compose_manual(raw, name)

    def test_non_header_drift_is_preserved_for_complete_historical_guards(self):
        for name, row in P.plan().items():
            current = (ROOT / name).read_bytes()
            suffix = b'\n# Foreign body drift must still reach the historical source guard.\n'
            historical = P.project_to_historical(current, name)
            if row['historicalSHA256'] is not None:
                for operation, raw in ((P.project_to_historical, current), (P.compose_manual, historical)):
                    with self.assertRaisesRegex(ValueError, 'complete frozen-controller'):
                        operation(raw + suffix, name)
                continue
            self.assertEqual(P.project_to_historical(current + suffix, name), historical + suffix)
            self.assertEqual(P.compose_manual(historical + suffix, name), current + suffix)

    def test_unknown_duplicate_moved_partial_and_changed_header_bytes_refuse(self):
        for name, row in P.plan().items():
            current = (ROOT / name).read_bytes()
            historical = P.project_to_historical(current, name)
            for raw, header in ((current, row['manualHeader'].encode()),
                                (historical, row['historicalHeader'].encode())):
                prefix = row['prefix'].encode()
                mutations = (
                    raw.replace(header, b'', 1),
                    raw.replace(header, header + header, 1),
                    raw.replace(header, b'', 1) + header,
                    b'# moved header prefix\n' + raw,
                    raw.replace(header, header.replace(b'workflow_dispatch:', b'workflow_dispatch: {}', 1), 1),
                    raw.replace(header, header.replace(b'workflow_dispatch:', b'workflow_call:', 1), 1),
                    raw.replace(prefix + header, prefix + header.rstrip(b'\n') + b'\n  push:\n\n', 1),
                    raw.replace(prefix + header, prefix + header.rstrip(b'\n') + b'\n  workflow_run:\n\n', 1),
                    raw.replace(prefix + header, prefix + header.rstrip(b'\n') + b'\n  schedule:\n\n', 1),
                    b'<<: *events\n' + raw,
                )
                for mutated in mutations:
                    for newline in (b'\n', b'\r\n'):
                        with self.subTest(name=name, header=header[:45], newline=newline):
                            source = mutated.replace(b'\n', newline)
                            for operation in (P.project_to_historical, P.compose_manual):
                                with self.assertRaises(ValueError):
                                    operation(source, name)
        with self.assertRaises(ValueError):
            P.project_to_historical(b'on:\n  workflow_dispatch:\n', 'unknown.yml')

    def test_mixed_newlines_bare_cr_invalid_encoding_and_bom_refuse(self):
        name = next(iter(P.plan()))
        raw = (ROOT / name).read_bytes()
        for malformed in (raw.replace(b'\n', b'\r\n', 1), raw + b'\r', raw + b'\xff',
                          b'\xef\xbb\xbf' + raw):
            for operation in (P.project_to_historical, P.compose_manual):
                with self.subTest(operation=operation.__name__), self.assertRaises((ValueError, UnicodeError)):
                    operation(malformed, name)

    def test_plan_drift_refuses_before_any_projection(self):
        name = next(iter(P.plan()))
        with tempfile.TemporaryDirectory(prefix='mixel manual policy plan ') as temporary:
            path = Path(temporary) / 'plan.json'
            path.write_bytes(P.PLAN.read_bytes() + b' ')
            with mock.patch.object(P, 'PLAN', path):
                with self.assertRaisesRegex(ValueError, 'Changed exact'):
                    P.project_to_historical((ROOT / name).read_bytes(), name)
        name = '.github/workflows/pc01-linux-controller.yml'
        row = P.plan()[name]
        current = (ROOT / name).read_bytes()
        for hunk in row['bodyChanges']:
            new, old = hunk['new'].encode(), hunk['old'].encode()
            mutants = (current.replace(new, old, 1), current.replace(new, b'', 1),
                       current.replace(new, new + new, 1),
                       current.replace(new, b'', 1) + new,
                       current.replace(new, new.replace(b'python3 ', b'python3 -I ', 1), 1))
            for mutated in mutants:
                for newline in (b'\n', b'\r\n'):
                    with self.assertRaises(ValueError):
                        P.project_to_historical(mutated.replace(b'\n', newline), name)

    def test_active_policy_requires_exact_manual_inventory_and_header(self):
        names = P.verify_active()
        self.assertEqual(len(names), 14)
        with tempfile.TemporaryDirectory(prefix='mixel manual policy active ') as temporary:
            root = Path(temporary)
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            self.assertEqual(P.verify_active(root), names)
            path = root / names[0]
            raw = path.read_bytes()
            path.write_bytes(P.project_to_historical(raw, names[0]))
            with self.assertRaisesRegex(ValueError, 'not active policy'):
                P.verify_active(root)
            path.write_bytes(raw)
            extra = path.with_name('unexpected.yaml')
            extra.write_bytes(b'on:\n  workflow_dispatch:\n')
            with self.assertRaisesRegex(ValueError, 'inventory'):
                P.verify_active(root)
            extra.unlink()
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'inventory'):
                P.verify_active(root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
