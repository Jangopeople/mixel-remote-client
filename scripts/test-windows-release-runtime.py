#!/usr/bin/env python3
"""Negative qualification of the debugger-free actual customer release gate."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
import warnings
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release_runtime', Path(__file__).with_name('collect-windows-release-runtime.py'))
GATE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(GATE)
SOURCE = 'a' * 40
ENV = {'WINDOWS_RELEASE_GATE': 'true', 'NATIVE_SYMBOLS_REQUESTED': 'false', 'PUBLISH_REQUESTED': 'false',
       'GITHUB_EVENT_NAME': 'workflow_dispatch', 'RUNTIME_RUN_ID': '1234'}
RUN = {'id': 1234, 'repository': {'full_name': GATE.REPOSITORY}, 'event': 'workflow_dispatch',
       'path': '.github/workflows/build.yml', 'html_url': f'https://github.com/{GATE.REPOSITORY}/actions/runs/1234',
       'head_sha': SOURCE, 'status': 'completed', 'conclusion': 'success'}
JOB = {'id': 4321, 'run_id': 1234, 'head_sha': SOURCE, 'name': 'windows', 'status': 'completed', 'conclusion': 'success'}


def artifact(name, id=4321):
    return {'name': name, 'id': id, 'expired': False, 'digest': 'sha256:' + 'b' * 64, 'size_in_bytes': 1024,
            'workflow_run': {'id': 1234, 'head_sha': SOURCE}}


def archive_bytes(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive, warnings.catch_warnings():
        warnings.filterwarnings('ignore', message="Duplicate name:.*", category=UserWarning)
        for name, data in members:
            archive.writestr(name, data)
    return output.getvalue()


def windows_files():
    return [('Mixel-Remote-QS.exe', b'actual-synthetic-release'), ('Mixel-Remote-Support-Windows.exe', b'actual-synthetic-release'),
            ('mixel-remote-1.4.6-x86_64.exe', b'actual-synthetic-release')]


def payload_files():
    inner = b'synthetic-signed-payload-bytes'
    return [('Mixel-Remote-Store-Payload.zip', inner), ('Mixel-Remote-Store-Payload.sha256', hashlib.sha256(inner).hexdigest().encode() + b'\r\n')]


class WindowsReleaseGateTests(unittest.TestCase):
    def test_request_is_only_artifact_windows_no_publication_symbols_or_platform_mixing(self):
        self.assertTrue(GATE.validate_request(ENV))
        self.assertFalse(GATE.validate_request({'WINDOWS_RELEASE_GATE': 'false'}))
        for change in [{'GITHUB_EVENT_NAME': 'push'}, {'PUBLISH_REQUESTED': 'true'}, {'PUBLISH_REQUESTED': ''},
                       {'NATIVE_SYMBOLS_REQUESTED': 'true'}, {'RUNTIME_LINUX_RUN': '1234'}, {'RUNTIME_MACOS_RUN': '1234'},
                       {'RUNTIME_RUN_ID': ''}, {'RUNTIME_RUN_ID': '../1234'}, {'RUNTIME_RUN_ID': '01234'}, {'WINDOWS_RELEASE_GATE': 'TRUE'}]:
            with self.subTest(change=change), self.assertRaises(ValueError): GATE.validate_request({**ENV, **change})

    def test_run_and_exact_windows_job_must_complete_and_pass(self):
        self.assertEqual(GATE.validate_run(RUN, '1234'), SOURCE)
        self.assertEqual(GATE.validate_windows_job([JOB], '1234', SOURCE), JOB)
        for field, value in [('id', 1235), ('id', True), ('head_sha', '../bad'), ('status', 'in_progress'),
                             ('repository', {'full_name': 'unowned/repo'}), ('path', 'unowned.yml'), ('html_url', 'https://example.test')]:
            with self.subTest(field=field), self.assertRaises(ValueError): GATE.validate_run({**RUN, field: value}, '1234')
        for field, value in [('status', 'in_progress'), ('conclusion', 'failure'), ('conclusion', 'skipped'), ('run_id', 1235),
                             ('id', True), ('head_sha', 'b' * 40)]:
            with self.subTest(field=field), self.assertRaises(ValueError): GATE.validate_windows_job([{**JOB, field: value}], '1234', SOURCE)
        for jobs in [[], [JOB, JOB]]:
            with self.assertRaises(ValueError): GATE.validate_windows_job(jobs, '1234', SOURCE)

    def test_two_artifacts_require_same_source_digest_and_normal_profile_inventory(self):
        values = [artifact(name, index + 10) for index, name in enumerate(GATE.ARTIFACTS)]
        self.assertEqual(set(GATE.select_artifacts(values, '1234', SOURCE)), set(GATE.ARTIFACTS))
        for field, value in [('expired', True), ('id', True), ('digest', 'sha256:BAD'), ('size_in_bytes', GATE.MAX_ARCHIVE + 1),
                             ('workflow_run', {'id': 1235, 'head_sha': SOURCE}), ('workflow_run', {'id': 1234, 'head_sha': 'b' * 40})]:
            bad = copy.deepcopy(values); bad[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): GATE.select_artifacts(bad, '1234', SOURCE)
        for bad in [values[:1], values + [values[0]], values + [artifact('windows-native-symbols')]]:
            with self.assertRaises(ValueError): GATE.select_artifacts(bad, '1234', SOURCE)

    def test_archive_bytes_aliases_checksum_paths_duplicate_links_are_qualified(self):
        with tempfile.TemporaryDirectory(prefix='mixel-release-input-tests-') as folder:
            path = Path(folder) / 'owned.zip'
            for name, files in [('windows', windows_files()), ('windows-msix-payload', payload_files())]:
                data = archive_bytes(files); path.write_bytes(data)
                self.assertEqual(set(GATE.read_archive(path, name, 'sha256:' + hashlib.sha256(data).hexdigest())), {member for member, _ in files})
                with self.assertRaises(ValueError): GATE.read_archive(path, name, 'sha256:' + '0' * 64)
            cases = []
            altered = windows_files(); altered[0] = (altered[0][0], b'changed-alias'); cases.append(('windows', altered))
            cases.extend([('windows', windows_files()[:2]), ('windows', windows_files() + [windows_files()[0]]),
                          ('windows', windows_files() + [('../private.exe', b'bad')]),
                          ('windows', windows_files() + [('dir/evil.exe', b'bad')]),
                          ('windows', windows_files() + [('C:\\evil.exe', b'bad')]),
                          ('windows', windows_files() + [('extra.pdb', b'bad')]),
                          ('windows-msix-payload', payload_files()[:1]),
                          ('windows-msix-payload', [('Mixel-Remote-Store-Payload.zip', b'changed'), payload_files()[1]])])
            link = zipfile.ZipInfo('mixel-remote-1.4.6-x86_64.msi'); link.create_system = 3; link.external_attr = (stat.S_IFLNK | 0o777) << 16
            cases.append(('windows', windows_files() + [(link, b'private-link')]))
            for name, files in cases:
                with self.subTest(name=name, members=[str(member) for member, _ in files]):
                    data = archive_bytes(files); path.write_bytes(data)
                    with self.assertRaises(ValueError): GATE.read_archive(path, name, 'sha256:' + hashlib.sha256(data).hexdigest())

    def test_full_collection_writes_exact_provenance_and_members_from_verified_archives(self):
        archives = {name: archive_bytes(files) for name, files in [('windows', windows_files()), ('windows-msix-payload', payload_files())]}
        artifacts = []
        for index, (name, data) in enumerate(archives.items()):
            item = artifact(name, index + 10); item['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest(); item['size_in_bytes'] = len(data); artifacts.append(item)
        def download(arguments, stdout, stderr, timeout):
            identity = int(arguments[-1].split('/')[-2]); data = archives[artifacts[identity - 10]['name']]
            stdout.write(data)
            class Result:
                returncode = 0
            return Result()
        with tempfile.TemporaryDirectory(prefix='mixel-release-full-') as folder, patch.dict(os.environ, ENV, clear=True), \
             patch.object(GATE, 'api_json', return_value=RUN), patch.object(GATE, 'inventory', side_effect=[[JOB], artifacts]), \
             patch.object(GATE.subprocess, 'run', side_effect=download):
            output = Path(folder) / 'owned-collection'; result = GATE.collect('1234', output)
            self.assertEqual(result['source_commit'], SOURCE)
            self.assertEqual(result['producer_windows_job_id'], JOB['id'])
            self.assertEqual(result['producer_windows_job_conclusion'], 'success')
            self.assertIs(result['publication'], False)
            self.assertEqual(json.loads((output / 'artifact-provenance.json').read_text()), result)
            for mapping in result['collected_artifacts'].values():
                self.assertEqual(mapping['artifact_digest'], mapping['actual_archive_digest'])
                for relative, digest in mapping['files_sha256'].items():
                    self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), digest)

    def test_inventory_cannot_hide_duplicate_artifacts_or_stall_on_empty_page(self):
        with patch.object(GATE, 'api_json', side_effect=[{'total_count': 2, 'artifacts': [artifact('windows')]},
                                                       {'total_count': 2, 'artifacts': [artifact('windows-msix-payload')]}]):
            self.assertEqual(len(GATE.inventory('1234', 'artifacts')), 2)
        for response in [{'total_count': 2, 'artifacts': []}, {'total_count': 2001, 'artifacts': []},
                         {'total_count': 0, 'artifacts': [artifact('windows')]}]:
            with patch.object(GATE, 'api_json', return_value=response):
                with self.assertRaises(ValueError): GATE.inventory('1234', 'artifacts')


if __name__ == '__main__':
    unittest.main(verbosity=2)
