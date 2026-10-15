"""Normal same-HEAD Standard95 file/source guards; never launches an app."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import hashlib

SOURCE = '1979d9432c316f0b10d79fb170407cf6859b1127'
TREE = '4d36a5cf8a22ce4c473a8e270cbc1325d21a0cbe'
RUN = 38799293531
WINDOWS = 116645947382
PREFLIGHT = 38799071390
COMPARISON = 38799071930
REPOSITORY = 'Jangopeople/mixel-remote-client'
SCOPE = 'root-current1979-whole-green-before-standard-foreground95'
ROOT_GATE = 'scripts/qa/current1979-standard-foreground95-root-gate.json'
EVIDENCE = 'scripts/qa/current1979-standard95-evidence/'
ROLES = ('producer','producerJobs','preflight','preflightJobs','comparison','comparisonJobs')
ENDPOINTS = {name: 'actions/runs/' + str(run) + suffix for name,run,suffix in (
    ('producer',RUN,''),('producerJobs',RUN,'/attempts/1/jobs?per_page=100'),
    ('preflight',PREFLIGHT,''),('preflightJobs',PREFLIGHT,'/attempts/1/jobs?per_page=100'),
    ('comparison',COMPARISON,''),('comparisonJobs',COMPARISON,'/attempts/1/jobs?per_page=100'))}
PRODUCER = {'config','bridge','linux','windows','macos (aarch64, aarch64-apple-darwin)',
            'macos (x86_64, x86_64-apple-darwin)','runtime','runtime-macos','runtime-linux','upload'}
PRODUCER_SKIP = {'runtime','runtime-macos','runtime-linux','upload'}
PREF = {'https-registration-gateway','windows-packaging','release-pipeline','macos-runtime-harness',
        'upstream-source','portable-standard-lease-qa','session-directory-access-observation',
        'standard-gui-connection-observation'}
PREF_SKIP = {'portable-standard-lease-qa','session-directory-access-observation','standard-gui-connection-observation'}
COMPARE = {'Complete branding byte identity (windows-2022)','Complete branding byte identity (windows-2025)'}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_json(raw):
    def pairs(items):
        result = {}
        for key,value in items:
            require(key not in result,'Duplicate JSON key')
            result[key]=value
        return result
    def constant(value):
        raise ValueError('Nonfinite JSON constant refused: '+value)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def owner_matches_token_sids(owner,user,default_owner,equal_sid):
    # TokenOwner is the actual current token's default owner, which can be
    # BUILTIN\\Administrators for an elevated runner. No group enumeration or
    # arbitrary Administrators exception substitutes for that token fact.
    require(bool(owner) and bool(user) and bool(default_owner),'Windows owner/token SID missing')
    return bool(equal_sid(owner,user) or equal_sid(owner,default_owner))


def windows_owner_matches_current_sid(descriptor, *, file_handle=None):
    """Read file owner and actual current TokenUser + TokenOwner from handles.

    No account, ACL, privilege or policy is changed. Missing APIs or inability to
    inspect either SID is a hard refusal, never a Unix-UID fallback.
    """
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    advapi=ctypes.WinDLL('advapi32',use_last_error=True)
    pointer=ctypes.c_void_p
    kernel.GetCurrentProcess.argtypes=[];kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[pointer];kernel.LocalFree.restype=pointer
    advapi.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype=wintypes.BOOL
    advapi.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,pointer,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype=wintypes.BOOL
    advapi.GetSecurityInfo.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.DWORD,
        ctypes.POINTER(pointer),pointer,pointer,pointer,ctypes.POINTER(pointer)]
    advapi.GetSecurityInfo.restype=wintypes.DWORD
    advapi.EqualSid.argtypes=[pointer,pointer];advapi.EqualSid.restype=wintypes.BOOL
    token=wintypes.HANDLE();security=pointer();owner=pointer();result=False;cleanup_error=False
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(),0x0008,ctypes.byref(token)):
        raise OSError(ctypes.get_last_error(),'Current Windows token owner inspection refused')
    try:
        def token_sid(info_class):
            size=wintypes.DWORD()
            advapi.GetTokenInformation(token,info_class,None,0,ctypes.byref(size))
            require(0<size.value<=65536,'Current Windows token SID size refused')
            storage=ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token,info_class,storage,size.value,ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(),'Current Windows token SID inspection refused')
            return storage,ctypes.cast(storage,ctypes.POINTER(pointer))[0]
        # Keep both backing buffers alive until EqualSid has completed.
        user_storage,current_sid=token_sid(1)  # TokenUser
        owner_storage,default_owner_sid=token_sid(4)  # TokenOwner
        actual_handle=msvcrt.get_osfhandle(descriptor) if file_handle is None else file_handle
        status=advapi.GetSecurityInfo(wintypes.HANDLE(actual_handle),1,1,
            ctypes.byref(owner),None,None,None,ctypes.byref(security))
        if status:raise OSError(status,'Windows file-handle owner inspection refused')
        result=owner_matches_token_sids(owner.value,current_sid,default_owner_sid,advapi.EqualSid)
    finally:
        if security.value and kernel.LocalFree(security):cleanup_error=True
        if not kernel.CloseHandle(token):cleanup_error=True
        require(not cleanup_error,'Windows owner inspection handle cleanup failed')
    return result


def owned_read(path, limit):
    path=Path(path)
    require(path.is_absolute() and path==path.resolve(strict=True),'Input must be absolute canonical')
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode),'Input parent must be a directory')
    before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
            and not getattr(before,'st_file_attributes',0)&0x400
            and 0<before.st_size<=limit,'Input must be owned bounded single-link regular file')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor,'rb') as stream:
        first=os.fstat(stream.fileno())
        owned=(windows_owner_matches_current_sid(stream.fileno()) if os.name=='nt'
               else first.st_uid==os.getuid())
        require(owned,'Input owner differs from the actual current account')
        raw=stream.read(limit+1);after=os.fstat(stream.fileno())
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_nlink,s.st_size,s.st_mtime_ns)
    require(identity(before)==identity(first)==identity(after) and len(raw)==before.st_size,
            'Input changed during read')
    return raw


def windows_directory_owner_matches_current_token(path):
    """Read the actual directory-handle owner; no ACL or policy mutation."""
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
        wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.CreateFileW(str(path),0x00020000,7,None,3,0x02000000|0x00200000,None)
    require(handle not in (None,ctypes.c_void_p(-1).value),'Owned runner temporary directory handle unavailable')
    try:return windows_owner_matches_current_sid(None,file_handle=handle)
    finally:require(bool(kernel.CloseHandle(handle)),'Owned runner directory handle cleanup failed')


def run_identity(value, run, workflow, event):
    require(isinstance(value, dict) and type(value.get('id')) is int and value['id'] == run and
            type(value.get('run_attempt')) is int and value['run_attempt'] == 1 and
            value.get('head_sha') == SOURCE and value.get('status') == 'completed' and
            value.get('conclusion') == 'success' and value.get('event') == event and
            value.get('path') == '.github/workflows/' + workflow and
            value.get('repository', {}).get('full_name') == REPOSITORY and
            value.get('html_url') == 'https://github.com/' + REPOSITORY + '/actions/runs/' + str(run),
            'Actual same-HEAD attempt1 whole-run success required')


def jobs(value, names, skipped, run):
    require(isinstance(value, dict) and type(value.get('total_count')) is int and
            isinstance(value.get('jobs'), list) and value['total_count'] == len(value['jobs']) == len(names),
            'Complete exact normal job inventory required')
    result = {}
    for job in value['jobs']:
        name = job.get('name')
        require(name in names and name not in result and type(job.get('id')) is int and job['id'] > 0 and
                job.get('run_id') == run and type(job.get('run_id')) is int and
                type(job.get('run_attempt')) is int and job['run_attempt'] == 1 and job.get('head_sha') == SOURCE and
                job.get('status') == 'completed' and job.get('conclusion') == ('skipped' if name in skipped else 'success'),
                'Every required normal job must actually pass on the same source')
        steps = job.get('steps')
        require(isinstance(steps, list) and (steps or name in skipped) and
                len({step.get('number') for step in steps}) == len(steps) and
                all(type(step.get('number')) is int and isinstance(step.get('name'), str) and step['name'] and
                    step.get('status') == 'completed' and step.get('conclusion') in ('success', 'skipped') for step in steps),
                'Actual complete normal step inventory required')
        result[name] = job
    require(set(result) == names and len({item['id'] for item in result.values()}) == len(names),
            'Exact unique normal job identities required')
    return result


def facts(data, gate):
    run_identity(data['producer'], RUN, 'build.yml', 'workflow_dispatch')
    run_identity(data['preflight'], PREFLIGHT, 'preflight.yml', 'pull_request')
    run_identity(data['comparison'], COMPARISON, 'windows-branding-idempotence.yml', 'pull_request')
    producer = jobs(data['producerJobs'], PRODUCER, PRODUCER_SKIP, RUN)
    preflight = jobs(data['preflightJobs'], PREF, PREF_SKIP, PREFLIGHT)
    comparison = jobs(data['comparisonJobs'], COMPARE, set(), COMPARISON)
    require(producer['windows']['id'] == gate['producer_windows_job'] == WINDOWS, 'Actual Windows job differs from Root seal')
    required = {'Prove file-dialog lifecycle before complete Windows source checks',
                'Preflight generated support source and reporter', 'Build (cargo + flutter via build.py)',
                'Require the complete generated Flutter app to analyze without errors or warnings',
                'Test compiled attended support guard', 'Exercise the signed portable customer download',
                'Exercise the signed QuickSupport download without an invite',
                'Require the original Windows source preflight before customer artifact upload'}
    for name in required:
        matched = [step for step in producer['windows']['steps'] if step['name'] == name]
        require(len(matched) == 1 and matched[0]['conclusion'] == 'success', 'Mandatory Windows customer/source gate must pass')
    return {key: {name: item['id'] for name, item in value.items()}
            for key, value in [('producer', producer), ('preflight', preflight), ('comparison', comparison)]}


def originals(repository, gate):
    refs = gate.get('references')
    require(isinstance(refs, dict) and set(refs) == set(ROLES), 'Six sealed original normal run/job inventories required')
    data = {}; hashes = {}
    for name in ROLES:
        item = refs[name]
        require(isinstance(item, dict) and set(item) == {'path', 'bytes', 'sha256'} and
                item['path'] == EVIDENCE + name + '.json' and type(item['bytes']) is int and
                isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                'Exact typed normal original reference required')
        raw = owned_read(repository / item['path'], 8 * 1024 * 1024)
        require(len(raw) == item['bytes'] and sha(raw) == item['sha256'], 'Original normal byte binding differs')
        hashes[name] = sha(raw); data[name] = unique_json(raw)
    require(gate.get('original_evidence_sha256') == hashes, 'Original normal evidence hashes differ')
    return facts(data, gate)


def live_state(directory, gate, expected):
    require(directory.is_absolute() and directory == directory.resolve(strict=True) and directory.is_dir(),
            'Canonical fresh normal state directory required')
    raw = {name: owned_read(directory / (name + '.json'), 8 * 1024 * 1024) for name in ROLES}
    require(facts({name: unique_json(value) for name, value in raw.items()}, gate) == expected,
            'Fresh normal job identities changed')
    return {name: sha(value) for name, value in raw.items()}


def hosted_context(repository, here):
    env = os.environ; source = env.get('GITHUB_SHA', '')
    require(os.name == 'nt' and env.get('GITHUB_ACTIONS') == 'true' and
            env.get('RUNNER_ENVIRONMENT') == 'github-hosted' and env.get('GITHUB_JOB') == 'runtime' and
            env.get('GITHUB_REPOSITORY') == REPOSITORY and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
            env.get('GITHUB_RUN_ATTEMPT') == '1' and env.get('GITHUB_WORKFLOW_SHA') == source and
            re.fullmatch('[0-9a-f]{40}', source) and env.get('MIXEL_STANDARD95_SOURCE_SCOPE') == SCOPE and
            env.get('WINDOWS_RELEASE_GATE') == 'true' and env.get('RUNTIME_RUN_ID') == str(RUN) and
            env.get('PUBLISH_REQUESTED') == 'false', 'Actual normal hosted runtime context required before mutation')
    reference = env.get('GITHUB_REF', '')
    require(re.fullmatch(r'refs/heads/codex/[^\r\n]+', reference) and
            env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/.github/workflows/build.yml@' + reference,
            'Exact reviewed normal workflow/ref required')
    workspace = Path(env.get('GITHUB_WORKSPACE', '')); parent = Path(env.get('RUNNER_TEMP', ''))
    require(repository.is_absolute() and repository == repository.resolve(strict=True) and workspace == repository and
            here == repository / 'scripts' and parent.is_absolute() and parent == parent.resolve(strict=True) and
            parent.is_dir() and not parent.is_symlink() and not getattr(parent.stat(), 'st_file_attributes', 0) & 0x400 and
            windows_directory_owner_matches_current_token(parent), 'Actual owned normal workspace/temp required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repository), *args], stderr=subprocess.PIPE, timeout=30)
    require(git('rev-parse', 'HEAD').decode().strip() == source and
            git('status', '--porcelain', '--untracked-files=no') == b'', 'Normal restoration requires a clean exact checkout')
    return parent


def restore_git_bytes(repository, here):
    parent = hosted_context(repository, here)
    names = subprocess.check_output(['git', '-C', str(repository), 'ls-files', '-z', '--', 'scripts',
                                     '.github/workflows/build.yml'], timeout=30)
    require(0 < len(names) <= 1_000_000 and names.endswith(b'\0'), 'Bounded normal source inventory required')
    paths = [value.decode('utf-8', errors='strict') for value in names[:-1].split(b'\0')]
    require(len(paths) == len(set(paths)) and all((name.startswith('scripts/') or name == '.github/workflows/build.yml')
            and '\\' not in name and not any(part in ('', '.', '..') for part in name.split('/')) for name in paths),
            'Exact scoped normal source paths required')
    with tempfile.TemporaryDirectory(prefix='mixel-normal95-git-', dir=parent) as folder:
        prefix = Path(folder)
        subprocess.run(['git', '-C', str(repository), '-c', 'core.autocrlf=false', 'checkout-index', '--force',
                        '--prefix=' + prefix.as_posix() + '/', '-z', '--stdin'], input=names, check=True, timeout=60)
        # Validate the whole scoped inventory before the first destination write.
        for name in paths:
            source = prefix / name; destination = repository / name
            require(source.is_file() and not source.is_symlink() and destination.is_file() and
                    not destination.is_symlink(), 'Regular scoped normal source required')
        for name in paths:
            shutil.copyfile(prefix / name, repository / name)
