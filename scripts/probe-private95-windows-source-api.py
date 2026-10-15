#!/usr/bin/env python3
"""Source/API-only hosted Windows probe. Never admits or starts an application."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

REPOSITORY = 'Jangopeople/mixel-remote-client'
REFERENCE = 'refs/heads/codex/current77-private95-source-probe'
WORKFLOW = '.github/workflows/windows-shell-boundary.yml'
PREPARER = 'prepare-private-standard-foreground95.py'
CAPTURE = 'capture-private-standard95-state.py'
PINS = {PREPARER: 'c7c6d448f8148fadbd4de8c6fbb35db49e1d2d71aa55278af3e884fbb8d00442',
        CAPTURE: '40d9c189921aebc387204925cc375830cb456d1e2abc0d18a22a4b4257d99c47'}
IMMUTABLE_PINS = {'scripts/' + name: value for name, value in PINS.items()}
IMMUTABLE_PINS.update({
    'scripts/test-private-standard-foreground95.py': '42e41713583fd1cec5ea5f284aa0b17c3bb3b3f3a9735bbbed81c05f18a510bf',
    'scripts/test-support-launch-windows.ps1': 'c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b',
    '.github/workflows/build.yml': 'fdd04d10a302ac80331d68e46c6dc57c0205297a6cfcd1288f06e835462f4262'})
SCOPED = ['scripts', '.github/workflows/build.yml', WORKFLOW]
PAYLOAD = 'Mixel private95 owned source fixture: préflight 🚀\n'.encode('utf-8')


def require(value):
    if not value:
        raise ValueError('Source/API-only probe refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def failure(record, error):
    record['failure_category'] = ('timeout' if isinstance(error, subprocess.TimeoutExpired) else
                                  'os_error' if isinstance(error, OSError) else
                                  'refusal' if isinstance(error, ValueError) else 'unexpected')
    for name in ('errno', 'winerror'):
        value = getattr(error, name, None)
        if type(value) is int and -(2 ** 31) <= value < 2 ** 32:
            record['failure_' + name] = value


def git(repository, *args, input=None):
    result = subprocess.run(['git', '-C', str(repository), *args], input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    require(result.returncode == 0)
    return result.stdout


def context(repository):
    env = os.environ
    source = env.get('GITHUB_SHA', '')
    require(os.name == 'nt' and sys.platform == 'win32' and
            env.get('GITHUB_ACTIONS') == 'true' and env.get('RUNNER_ENVIRONMENT') == 'github-hosted' and
            env.get('GITHUB_REPOSITORY') == REPOSITORY and env.get('GITHUB_JOB') == 'active-bash-boundary' and
            env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and env.get('GITHUB_RUN_ATTEMPT') == '1' and
            env.get('GITHUB_REF') == REFERENCE and re.fullmatch('[0-9a-f]{40}', source) and
            env.get('GITHUB_WORKFLOW_SHA') == source and
            env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/' + WORKFLOW + '@' + REFERENCE)
    workspace = Path(env.get('GITHUB_WORKSPACE', ''))
    require(repository.is_absolute() and repository == repository.resolve(strict=True) and
            workspace == repository and Path(__file__).resolve() == repository / 'scripts' / Path(__file__).name)
    require(git(repository, 'rev-parse', 'HEAD').decode().strip() == source and
            git(repository, 'status', '--porcelain', '--untracked-files=no') == b'')
    temporary = Path(env.get('RUNNER_TEMP', ''))
    require(temporary.is_absolute() and temporary == temporary.resolve(strict=True) and temporary.is_dir() and
            not temporary.is_symlink() and not getattr(temporary.stat(), 'st_file_attributes', 0) & 0x400)
    return temporary


def inventory(repository):
    names = git(repository, 'ls-files', '-z', '--', *SCOPED)
    require(names and names.endswith(b'\0'))
    decoded = [item.decode('utf-8', errors='strict') for item in names[:-1].split(b'\0')]
    require(len(decoded) == len(set(decoded)) and all(
        (name.startswith('scripts/') or name in SCOPED[1:]) and
        not any(item in ('', '.', '..') for item in name.split('/')) and '\\' not in name for name in decoded))
    values = {}
    for name in decoded:
        path = repository / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not path.is_symlink())
        values[name] = {'sha256': sha(path.read_bytes()), 'mode': stat.S_IMODE(info.st_mode)}
    return names, sha(json.dumps(values, sort_keys=True, separators=(',', ':')).encode())


def materialize(repository, mirror, names):
    require(not mirror.exists())
    mirror.mkdir()
    git(repository, '-c', 'core.autocrlf=false', 'checkout-index', '--force',
        '--prefix=' + mirror.as_posix() + '/', '-z', '--stdin', input=names)
    for name, expected in IMMUTABLE_PINS.items():
        require(sha((mirror / name).read_bytes()) == expected)


def load_sources(mirror):
    # Both immutable raw Git blobs must qualify before either module executes.
    for name, expected in IMMUTABLE_PINS.items():
        require(sha((mirror / name).read_bytes()) == expected)
    modules = []
    for name in (PREPARER, CAPTURE):
        spec = importlib.util.spec_from_file_location('source_api_' + name.replace('-', '_'), mirror / 'scripts' / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def native_controls(preparer, capture, fixture, record=None):
    progress = {} if record is None else record
    progress['stage'] = 'native_fixture'
    require(os.name == 'nt' and sys.platform == 'win32' and not fixture.exists())
    fixture.mkdir()
    path = (fixture / 'owned préflight 🚀.bin').resolve()
    path.write_bytes(PAYLOAD)
    result = {}
    progress['stage'] = 'native_owned_read'
    require(preparer.owned_read(path, len(PAYLOAD)) == PAYLOAD)
    result['actual_owned_read_exact_bytes'] = True
    progress['stage'] = 'native_file_owner'
    with path.open('rb') as stream:
        require(preparer.windows_owner_matches_current_sid(stream.fileno()) is True)
    result['actual_file_handle_owner_matches_token_user_or_owner'] = True
    progress['stage'] = 'native_directory_owner'
    require(capture.windows_directory_owner_matches_current_token(fixture.resolve()) is True)
    result['actual_directory_handle_owner_matches_token_user_or_owner'] = True
    result['fixture_payload_sha256'] = sha(PAYLOAD)
    return result


def source_controls(mirror, temporary):
    temporary.mkdir()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary))
    child = subprocess.run([sys.executable, '-B', 'scripts/test-private-standard-foreground95.py'],
                           cwd=mirror, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=150)
    passed = child.returncode == 0 and bool(re.search(rb'Ran 28 tests in [^\r\n]+\r?\n\r?\nOK\r?\n\Z', child.stderr))
    return {'unchanged_28_source_controls_pass': passed, 'source_controls_stdout_sha256': sha(child.stdout),
            'source_controls_stderr_sha256': sha(child.stderr)}


def run_probe(repository):
    temporary = context(repository)  # No writes or module import before hosted/ref authorization.
    proof = repository / 'shell-boundary-proof'
    require(proof.is_dir() and not proof.is_symlink())
    output = proof / 'private95-source-api-result.json'
    require(not output.exists())
    names, before = inventory(repository)
    record = {'scope': 'SOURCE_AND_OWNER_API_ONLY_NOT_INSTALLER_OR_NATIVE95_QUALIFICATION',
              'normal_qualified': False, 'release_qualified': False, 'publication': False,
              'native_application_started': False, 'account_acl_policy_or_credential_action': False,
              'provider_action': False, 'completed': False, 'stage': 'temporary_prefix', 'failure_category': 'none',
              'preparer_sha256': PINS[PREPARER],
              'capture_sha256': PINS[CAPTURE], 'canonical_before_inventory_sha256': before}
    try:
        with tempfile.TemporaryDirectory(prefix='mixel-private95-source-probe-', dir=temporary) as directory:
            owned = Path(directory).resolve()
            mirror = owned / 'source'
            record['stage'] = 'materialize'
            materialize(repository, mirror, names)
            record['exact_raw_git_mirror_pins_verified_before_import'] = True
            record['stage'] = 'import'
            preparer, capture = load_sources(mirror)
            record.update(native_controls(preparer, capture, owned / 'owned fixture préflight 🚀', record))
            record['stage'] = 'source_controls'
            record.update(source_controls(mirror, owned / 'temporary'))
            require(record['unchanged_28_source_controls_pass'])
            record['stage'] = 'final_immutable_pins'
            record['immutable_helper_pins_preserved'] = all(
                sha((mirror / name).read_bytes()) == expected for name, expected in IMMUTABLE_PINS.items())
            require(record['immutable_helper_pins_preserved'])
            record['completed'] = True
    except Exception as error:
        record['completed'] = False
        failure(record, error)
    finally:
        try:
            after_names, after = inventory(repository)
            record['canonical_after_inventory_sha256'] = after
            record['canonical_raw_sources_and_tracked_status_unchanged'] = (
                after_names == names and after == before and
                git(repository, 'status', '--porcelain', '--untracked-files=no') == b'')
        except Exception as error:
            record['canonical_raw_sources_and_tracked_status_unchanged'] = False
            if record['failure_category'] == 'none':
                record['stage'] = 'canonical_final_inventory'
                failure(record, error)
        if not record['canonical_raw_sources_and_tracked_status_unchanged'] and record['failure_category'] == 'none':
            record['stage'] = 'canonical_final_inventory'
            failure(record, ValueError())
        record['completed'] = record['completed'] and record['canonical_raw_sources_and_tracked_status_unchanged']
        if record['completed']:
            record['stage'] = 'complete'
        with output.open('xb') as stream:
            stream.write((json.dumps(record, sort_keys=True, indent=2) + '\n').encode())
    return record


def main():
    try:
        record = run_probe(Path(__file__).resolve().parent.parent)
    except Exception as error:
        record = {'stage': 'context_or_initial_inventory'}
        failure(record, error)
        print(json.dumps(record, sort_keys=True))
        print('FAIL: isolated private95 source/API probe refused before qualification.')
        return 1
    print('PASS: unchanged28 source controls and actual read-only Windows owner APIs; app/native95 remain unqualified.'
          if record['completed'] else 'FAIL: isolated private95 source/API controls; app/native95 remain unqualified.')
    return 0 if record['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
