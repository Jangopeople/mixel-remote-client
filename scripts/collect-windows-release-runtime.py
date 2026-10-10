#!/usr/bin/env python3
"""Collect an exact successful Windows release for debugger-free outer-launcher QA."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import zipfile

REPOSITORY = 'Jangopeople/mixel-remote-client'
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_MEMBER = 512 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
ARTIFACTS = ('windows', 'windows-msix-payload')


def require(value, message):
    if not value:
        raise ValueError(message)


def run_id(value):
    require(re.fullmatch(r'[1-9][0-9]{0,19}', str(value)) is not None, 'Expected a canonical positive bounded run ID')
    return int(value)


def validate_request(environment):
    enabled = environment.get('WINDOWS_RELEASE_GATE', 'false')
    require(enabled in ('', 'true', 'false'), 'Invalid Windows release gate request')
    if enabled != 'true':
        return False
    require(environment.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'Windows release gate requires workflow_dispatch')
    require(environment.get('PUBLISH_REQUESTED') == 'false', 'Windows release gate requires publish_r2=false')
    require(environment.get('NATIVE_SYMBOLS_REQUESTED') in ('', 'false', None), 'Release gate and native symbol diagnostics are mutually exclusive')
    require(not environment.get('RUNTIME_LINUX_RUN') and not environment.get('RUNTIME_MACOS_RUN'), 'Windows release gate requires only the Windows artifact runtime')
    run_id(environment.get('RUNTIME_RUN_ID', ''))
    return True


def validate_run(run, expected_run):
    expected_run = run_id(expected_run)
    require(type(run.get('id')) is int and run['id'] == expected_run, 'Producer run ID mismatch')
    require(run.get('repository', {}).get('full_name') == REPOSITORY, 'Producer repository mismatch')
    require(run.get('event') in ('workflow_dispatch', 'push') and run.get('path') == '.github/workflows/build.yml', 'Expected the client build workflow')
    require(run.get('html_url') == f'https://github.com/{REPOSITORY}/actions/runs/{expected_run}' and
            re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', '')) is not None, 'Producer URL/source attribution mismatch')
    require(run.get('status') == 'completed', 'Release producer run must be completed')
    return run['head_sha']


def validate_windows_job(jobs, expected_run, source):
    selected = [job for job in jobs if job.get('name') == 'windows']
    require(len(selected) == 1, 'Expected one producer Windows build job')
    job = selected[0]
    require(type(job.get('id')) is int and job['id'] > 0 and type(job.get('run_id')) is int and
            job['run_id'] == run_id(expected_run) and job.get('head_sha') == source, 'Windows job run/source attribution mismatch')
    require(job.get('status') == 'completed' and job.get('conclusion') == 'success', 'Producer Windows build/customer launch gates must be successful')
    return job


def select_artifacts(artifacts, expected_run, source):
    require(not any(artifact.get('name') == 'windows-native-symbols' for artifact in artifacts),
            'Diagnostic native-symbol producer cannot qualify as a normal release')
    selected = {}
    for name in ARTIFACTS:
        matches = [artifact for artifact in artifacts if artifact.get('name') == name]
        require(len(matches) == 1, 'Expected one exact customer artifact: ' + name)
        artifact = matches[0]
        require(artifact.get('expired') is False and type(artifact.get('id')) is int and artifact['id'] > 0, 'Invalid/expired customer artifact')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('digest', '')) is not None, 'Missing exact GitHub archive digest')
        require(type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= MAX_ARCHIVE, 'Customer artifact exceeds its size bound')
        origin = artifact.get('workflow_run', {})
        require(type(origin.get('id')) is int and origin['id'] == run_id(expected_run) and origin.get('head_sha') == source,
                'Customer artifact run/source mismatch')
        selected[name] = artifact
    return selected


def read_archive(path, name, digest):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_ARCHIVE, 'Expected a bounded regular artifact archive')
    data = path.read_bytes()
    require('sha256:' + hashlib.sha256(data).hexdigest() == digest, 'Actual artifact ZIP digest differs from GitHub')
    files = {}; expanded = 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(0 < len(entries) <= 64, 'Artifact file inventory exceeds its bound')
        for entry in entries:
            require(not entry.is_dir() and '/' not in entry.filename and '\\' not in entry.filename and ':' not in entry.filename,
                    'Customer artifact must contain only canonical root files')
            require(entry.filename not in files, 'Duplicate artifact member')
            mode = entry.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFREG) and not entry.flag_bits & 1, 'Artifact member is not an unencrypted regular file')
            require(0 < entry.file_size <= MAX_MEMBER, 'Artifact member exceeds its size bound')
            expanded += entry.file_size
            require(expanded <= MAX_EXPANDED, 'Artifact expanded size exceeds its bound')
            if name == 'windows-msix-payload':
                require(entry.filename in ('Mixel-Remote-Store-Payload.zip', 'Mixel-Remote-Store-Payload.sha256'), 'Unexpected Store payload archive member')
            else:
                require(entry.filename in ('Mixel-Remote-QS.exe', 'Mixel-Remote-Support-Windows.exe') or
                        re.fullmatch(r'mixel-remote-[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?-x86_64\.(?:exe|msi)', entry.filename),
                        'Unexpected customer Windows artifact member')
            files[entry.filename] = archive.read(entry)
    if name == 'windows-msix-payload':
        require(set(files) == {'Mixel-Remote-Store-Payload.zip', 'Mixel-Remote-Store-Payload.sha256'}, 'Incomplete Store payload artifact')
        expected = files['Mixel-Remote-Store-Payload.sha256'].decode('ascii').strip()
        require(re.fullmatch(r'[0-9a-f]{64}', expected) is not None and
                hashlib.sha256(files['Mixel-Remote-Store-Payload.zip']).hexdigest() == expected, 'Actual inner signed payload checksum mismatch')
    else:
        versions = [member for member in files if member.endswith('.exe') and member.startswith('mixel-remote-')]
        require(len(versions) == 1 and 'Mixel-Remote-QS.exe' in files and 'Mixel-Remote-Support-Windows.exe' in files,
                'Missing actual signed customer launcher/version aliases')
        require(files[versions[0]] == files['Mixel-Remote-QS.exe'] == files['Mixel-Remote-Support-Windows.exe'],
                'Customer launcher aliases are not byte-identical to the signed release')
    return files


def api_json(path):
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    require(result.returncode == 0, 'Read-only GitHub provenance request failed')
    require(len(result.stdout) <= 8 * 1024 * 1024, 'GitHub metadata response exceeds its bound')
    return json.loads(result.stdout)


def inventory(build_run, category):
    values = []; page = 1
    while True:
        response = api_json(f'repos/{REPOSITORY}/actions/runs/{build_run}/{category}?per_page=100&page={page}')
        batch = response[category]
        require(type(response['total_count']) is int and 0 <= response['total_count'] <= 2000, 'GitHub inventory exceeds its bound')
        values.extend(batch)
        if len(values) == response['total_count']:
            return values
        require(len(values) < response['total_count'] and batch and page < 20, 'Incomplete or contradictory GitHub inventory')
        page += 1


def collect(build_run, output):
    require(validate_request(os.environ), 'Windows release collection requires explicit artifact-only gate mode')
    build_run = run_id(build_run)
    require(build_run == run_id(os.environ.get('RUNTIME_RUN_ID', '')), 'Collection differs from the requested run')
    output = output.resolve(); output.mkdir(parents=True, mode=0o700, exist_ok=False)
    run = api_json(f'repos/{REPOSITORY}/actions/runs/{build_run}')
    source = validate_run(run, build_run)
    job = validate_windows_job(inventory(build_run, 'jobs'), build_run, source)
    artifacts = select_artifacts(inventory(build_run, 'artifacts'), build_run, source)
    mappings = {}
    for name, artifact in artifacts.items():
        archive = output / (name + '.zip')
        with archive.open('xb') as stream:
            result = subprocess.run(['gh', 'api', '--hostname', 'github.com', f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],
                                    stdout=stream, stderr=subprocess.PIPE, timeout=120)
        require(result.returncode == 0, 'Read-only exact customer artifact download failed')
        files = read_archive(archive, name, artifact['digest'])
        relative = Path('artifacts') / name
        (output / relative).mkdir(parents=True)
        hashes = {}
        for member, data in files.items():
            path = relative / member; (output / path).write_bytes(data); hashes[path.as_posix()] = hashlib.sha256(data).hexdigest()
        mappings[name] = {'artifact_id': artifact['id'], 'artifact_digest': artifact['digest'], 'actual_archive_digest': artifact['digest'], 'files_sha256': hashes}
    (output / 'run-metadata.json').write_text(json.dumps(run, indent=2) + '\n')
    provenance = {'evidence_kind': 'debugger-free-windows-customer-release-gate-inputs', 'repository': REPOSITORY,
                  'build_run_id': build_run, 'build_url': run['html_url'], 'source_commit': source, 'application_source_baseline': source,
                  'build_status': run['status'], 'build_conclusion': run.get('conclusion'), 'producer_windows_job_id': job['id'],
                  'producer_windows_job_conclusion': 'success', 'publication': False, 'collected_artifacts': mappings}
    (output / 'artifact-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(f'PASS: exact successful Windows build {build_run}/{source}; both GitHub ZIP digests, signed customer alias bytes and inner payload checksum verified.')
    print('Native signatures and actual debugger-free95 customer ownership remain mandatory in the following runtime steps.')
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('request')
    collection = commands.add_parser('collect')
    collection.add_argument('--run-id', required=True)
    collection.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'request':
        enabled = validate_request(os.environ)
        print('PASS: artifact-only Windows release gate request qualified.' if enabled else 'PASS: Windows release gate disabled.')
    else:
        collect(args.run_id, args.output)


if __name__ == '__main__':
    main()
