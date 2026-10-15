#!/usr/bin/env python3
"""Retain fresh raw current77 API facts for PRIVATE Standard95; no native actions."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('private_standard95_gate',HERE/'prepare-private-standard-foreground95.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
REPOSITORY='Jangopeople/mixel-remote-client'
ROOT_GATE='scripts/qa/current77-private-standard95-root-gate.json'
ENDPOINTS={'producer':'actions/runs/'+str(P.RUN),'windowsJob':'actions/jobs/'+str(P.WINDOWS),
           'armJob':'actions/jobs/'+str(P.ARM),'preflight':'actions/runs/'+str(P.PREFLIGHT),
           'comparison':'actions/runs/'+str(P.COMPARISON)}


def validate_request(environment):
    mode=environment.get('PRIVATE_STANDARD95','false')
    P.require(mode in ('true','false',''),'Typed private Standard95 request required')
    if mode!='true':return False
    exact={'GITHUB_EVENT_NAME':'workflow_dispatch','RUNTIME_RUN_ID':str(P.RUN),'PUBLISH_REQUESTED':'false',
           'WINDOWS_RELEASE_GATE':'false','COLLECT_WINDOWS_PREFLIGHT_FAILURE':'false'}
    P.require(all(environment.get(key)==value for key,value in exact.items()),'Exact isolated private current77 request required')
    P.require(all(not environment.get(key) for key in ('RUNTIME_LINUX_RUN','RUNTIME_MACOS_RUN','INPUT_ONLY','INPUT_UPSTREAM_VERSION')),
              'Private Windows request cannot build, publish or consume other platforms')
    return True


def restore_git_bytes(repository):
    """Materialize a new LF checkout prefix, then copy exact scoped Git bytes.

    An existing clean index entry may skip in-place EOL rewriting even with
    checkout --force. A new owned prefix has no such stat-cache shortcut.
    """
    parent=restore_context(repository)
    paths=subprocess.check_output(['git','-C',str(repository),'ls-files','-z','--','scripts','.github/workflows/build.yml'],timeout=30)
    P.require(0<len(paths)<=1_000_000 and paths.endswith(b'\0'),'Bounded tracked QA source inventory required')
    names=[part.decode('utf-8',errors='strict') for part in paths[:-1].split(b'\0')]
    P.require(len(names)==len(set(names)) and all((name.startswith('scripts/') or name=='.github/workflows/build.yml')
        and not any(part in ('','..','.') for part in name.split('/')) and '\\' not in name for name in names),
        'Exact scoped tracked QA source paths required')
    with tempfile.TemporaryDirectory(prefix='mixel-private95-git-',dir=parent) as directory:
        prefix=Path(directory)
        subprocess.run(['git','-C',str(repository),'-c','core.autocrlf=false','checkout-index','--force',
            '--prefix='+prefix.as_posix()+'/','-z','--stdin'],input=paths,check=True,timeout=60)
        for name in names:
            source=prefix/name;destination=repository/name
            P.require(source.is_file() and not source.is_symlink() and destination.is_file()
                and not destination.is_symlink(),'Regular exact QA source required for restoration')
            shutil.copyfile(source,destination)
    print('PASS: exact scoped private QA Git bytes restored; HEAD and Git configuration unchanged.')


def windows_directory_owner_matches_current_token(path):
    """Read the actual directory-handle owner; no ACL or policy mutation."""
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
        wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE];kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.CreateFileW(str(path),0x00020000,7,None,3,0x02000000|0x00200000,None)
    P.require(handle not in (None,ctypes.c_void_p(-1).value),'Owned runner temporary directory handle unavailable')
    try:return P.windows_owner_matches_current_sid(None,file_handle=handle)
    finally:P.require(bool(kernel.CloseHandle(handle)),'Owned runner directory handle cleanup failed')


def restore_context(repository):
    """Authorize every restoration write to this fresh hosted QA checkout only."""
    source=os.environ.get('GITHUB_SHA','')
    hosted_context(source)
    P.require(re.fullmatch('[0-9a-f]{40}',source),'Exact hosted QA source required before restoration')
    ref=os.environ.get('GITHUB_REF','')
    P.require(re.fullmatch(r'refs/(?:heads|tags)/[^\r\n]+',ref) and
        os.environ.get('GITHUB_WORKFLOW_REF')==REPOSITORY+'/.github/workflows/build.yml@'+ref,
        'Exact hosted private workflow/ref required before restoration')
    workspace=Path(os.environ.get('GITHUB_WORKSPACE',''))
    P.require(repository.is_absolute() and repository==repository.resolve(strict=True)
        and workspace.is_absolute() and workspace==workspace.resolve(strict=True) and repository==workspace
        and HERE==repository/'scripts','Restoration is restricted to its actual fresh GitHub workspace')
    parent=Path(os.environ.get('RUNNER_TEMP',''))
    P.require(parent.is_absolute() and parent==parent.resolve(strict=True) and parent.is_dir()
        and not parent.is_symlink() and not getattr(parent.stat(),'st_file_attributes',0)&0x400
        and windows_directory_owner_matches_current_token(parent),'Exact owned runner temporary parent required')
    head=subprocess.check_output(['git','-C',str(repository),'rev-parse','HEAD'],stderr=subprocess.PIPE,timeout=30)
    P.require(head.decode().strip()==source,'Restoration checkout HEAD differs from actual hosted source')
    dirty=subprocess.check_output(['git','-C',str(repository),'status','--porcelain','--untracked-files=no'],
        stderr=subprocess.PIPE,timeout=30)
    P.require(dirty==b'','Restoration refuses a dirty tracked checkout before mutation')
    return parent


def precheck(repository,gate_path,gate_digest,harness_source,harness_tree):
    P.require(gate_path==repository/ROOT_GATE,'Exact separately tracked private Root gate path required')
    P.require(gate_path.is_file(),'Private Root gate is absent; no collection or application work permitted')
    raw=P.owned_read(gate_path,1_000_000)
    P.require(P.sha(raw)==gate_digest,'Private Root gate hash differs')
    gate=P.unique_json(raw)
    P.require(gate.get('schema')=='root-current77-private-standard95-decision-v1' and
        gate.get('scope')==P.SCOPE and gate.get('authorizedBy')=='Root' and gate.get('privateDiagnosticGo') is True,
        'Private Root decision is unbound')
    for key in ('root_whole_green_verified','normal_qualified','release_qualified','publication'):
        P.require(gate.get(key) is False,'Private Root qualification must remain false')
    for key,value in {'source':P.SOURCE,'tree':P.TREE,'producerRun':P.RUN,'attempt':1,'windowsJob':P.WINDOWS,
                      'failedArmJob':P.ARM,'preflightRun':P.PREFLIGHT,'comparisonRun':P.COMPARISON}.items():
        P.require(type(gate.get(key)) is type(value) and gate[key]==value,'Private Root identity differs: '+key)
    blobs=gate.get('reviewed_harness_blobs')
    required={'scripts/capture-private-standard95-state.py','scripts/prepare-private-standard-foreground95.py',
        'scripts/test-private-standard-foreground95.py','scripts/prepare-standard-foreground95.py',
        'scripts/test-standard-foreground95-adapter.py','scripts/private_standard95_workflow_projection.py',
        '.github/workflows/build.yml'}
    P.require(isinstance(blobs,dict) and required<=set(blobs),'Reviewed private caller/helper blobs required')
    adapter=P.load_pinned('prepare-standard-foreground95.py')
    return adapter.verify_harness(repository,harness_source,harness_tree,blobs,gate_path,gate_digest)


def hosted_context(source):
    P.require(os.name=='nt' and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and os.environ.get('GITHUB_JOB')=='runtime'
        and os.environ.get('GITHUB_REPOSITORY')==REPOSITORY
        and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and os.environ.get('GITHUB_RUN_ATTEMPT')=='1'
        and os.environ.get('GITHUB_SHA')==source and os.environ.get('GITHUB_WORKFLOW_SHA')==source
        and os.environ.get('MIXEL_PRIVATE_STANDARD95_SCOPE')==P.SCOPE,
        'Actual fresh hosted private Windows runtime context required')


def api(path):
    result=subprocess.run(['gh','api','--hostname','github.com','repos/'+REPOSITORY+'/'+path],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    P.require(result.returncode==0,'Fresh read-only private provenance request failed')
    P.require(0<len(result.stdout)<=8*1024*1024,'Fresh private metadata size refused')
    P.unique_json(result.stdout)
    return result.stdout


def capture(repository,gate_path,gate_digest,harness_source,harness_tree,output,stage):
    harness=precheck(repository,gate_path,gate_digest,harness_source,harness_tree)
    hosted_context(harness_source)
    parent=Path(os.environ.get('RUNNER_TEMP',''))
    P.require(str(parent) not in ('','.','/') and parent.is_dir() and not parent.is_symlink()
        and output.parent.resolve()==parent.resolve() and stage in ('before','after')
        and re.fullmatch('mixel-private95-live-'+stage+'-[0-9a-f]{32}',output.name),
        'Fresh stage-specific owned metadata directory required')
    P.require(not output.exists() and not output.is_symlink(),'Existing fresh metadata output refused')
    output.mkdir(mode=0o700)
    started=datetime.now(timezone.utc).isoformat()
    for name,endpoint in ENDPOINTS.items():
        raw=api(endpoint)
        with (output/(name+'.json')).open('xb') as stream:stream.write(raw)
    hashes=P.check_live_files(output)
    record={'scope':P.SCOPE,'stage':stage,'startedUtc':started,'completedUtc':datetime.now(timezone.utc).isoformat(),
        'rawOriginalSha256':hashes,'harness':harness,'rootGateSha256':gate_digest,
        'wholeProducer':'completed/failure','armJob':'completed/failure','normal_qualified':False,
        'release_qualified':False,'publication':False}
    with (output/'capture.json').open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(record,stream,indent=2);stream.write('\n')
    print('PASS PRIVATE '+stage+': fresh raw current77 attempt1 state retained; producer and ARM failure unchanged.')
    return record


def main():
    if len(sys.argv)>1 and sys.argv[1]=='restore-git-bytes':
        parser=argparse.ArgumentParser();parser.add_argument('command');parser.add_argument('--repository',type=Path,required=True)
        args=parser.parse_args()
        try:restore_git_bytes(args.repository);return 0
        except (OSError,ValueError,subprocess.SubprocessError):
            print('FAIL: private QA Git byte restoration refused before mutation.',file=sys.stderr);return 1
    if sys.argv[1:]==['request']:
        try:
            enabled=validate_request(os.environ)
            print('PASS: private Standard95 request; no normal qualification.' if enabled else 'PASS: private Standard95 disabled.')
            return 0
        except ValueError:
            print('FAIL: private Standard95 request refused.',file=sys.stderr);return 1
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,required=True);parser.add_argument('--root-gate',type=Path,required=True)
    parser.add_argument('--root-gate-sha256',required=True);parser.add_argument('--harness-source',required=True)
    parser.add_argument('--harness-tree',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stage',choices=('before','after'),required=True)
    args=parser.parse_args()
    try:capture(args.repository,args.root_gate,args.root_gate_sha256,args.harness_source,args.harness_tree,args.output,args.stage)
    except (OSError,ValueError,subprocess.SubprocessError):
        print('FAIL: private Standard95 admission/capture refused; no normal qualification.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
