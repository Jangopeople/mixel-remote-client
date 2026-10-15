#!/usr/bin/env python3
"""File-only PRIVATE current77 Windows95 admission; never runs apps or collects inputs.

This separate diagnostic profile preserves the reviewed Standard adapter and
original signed-outer/runtime95/cleanup projection. Whole producer failure is
mandatory and stays failure; it cannot qualify a release or normal workflow.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parent
SOURCE = '77a0acf1b0af2070c7d5d2d1fb172a343882dfc0'
TREE = 'fdd3807757b4c0e6085cf4def3f5eb338ba2b04f'
RUN = 38774820528
WINDOWS = 116562140563
ARM = 116562140099
PREFLIGHT = 38774157443
COMPARISON = 38774157532
SCOPE = 'OWNED_CURRENT77_WINDOWS_STANDARD_FOREGROUND95_DIAGNOSTIC_ONLY'
PINS = {'prepare-standard-foreground95.py':'934287410aca6e2e8720041768498af02b3e7d2f969b17d84a95c1f4fd0ef300',
        'collect-windows-release-runtime.py':'5bd875229f3311ff6c9bb87d35be8422fe380bd60324104784626d1a6f64429a'}
PREF_JOBS = {'https-registration-gateway':116557333724,'windows-packaging':116557334151,
             'release-pipeline':116557334166,'macos-runtime-harness':116557334238,
             'upstream-source':116557334406,'portable-standard-lease-qa':116557335838,
             'session-directory-access-observation':116557335904,
             'standard-gui-connection-observation':116557335935}
PREF_SKIP = {'portable-standard-lease-qa','session-directory-access-observation','standard-gui-connection-observation'}
COMPARISON_JOBS = {'Complete branding byte identity (windows-2025)':116557333543,
                   'Complete branding byte identity (windows-2022)':116557334369}
PRODUCER_JOBS = {'config':116559628730,'bridge':116559715360,'windows':WINDOWS,
                 'linux':116562140309,'macos (aarch64, aarch64-apple-darwin)':ARM,
                 'macos (x86_64, x86_64-apple-darwin)':116562140491,
                 'runtime':116559630782,'runtime-macos':116559630809,'runtime-linux':116559631350}
PRODUCER_SKIP = {'runtime','runtime-macos','runtime-linux','upload'}
WINDOWS_STEPS = {1: 'Set up job',
 2: 'Run actions/checkout@v5',
 3: 'Install Rust',
 4: 'Install Flutter',
 5: 'Resolve canonical Windows Dart executable',
 6: 'Restore compiled codec cache',
 7: 'Setup pinned vcpkg',
 8: 'Install vcpkg deps',
 9: 'Install ImageMagick (for branding .ico)',
 10: 'Fetch upstream RustDesk',
 11: 'Apply Mixel-Remote branding',
 12: 'Restore bridge files',
 13: 'Run Swatinem/rust-cache@v2',
 14: 'Activate installed native MSVC clipboard regression toolchain',
 15: 'Prove explicit MSVC selection under actual Git Bash coreutils shadow',
 16: 'Prove native Windows clipboard ownership and Rust error cleanup',
 17: 'Run actions/upload-artifact@v6',
 18: 'Preflight generated support source and reporter',
 19: 'Preserve Windows SDK source diagnostics',
 20: 'Build (cargo + flutter via build.py)',
 21: 'Require the complete generated Flutter app to analyze without errors or warnings',
 22: 'Test compiled attended support guard',
 23: 'Verify compiled Flutter UI branding',
 24: 'Prepare full application directory for Store packaging',
 25: 'Sign compiled Store application',
 26: 'Verify and exercise the signed compiled application',
 27: 'Build portable launcher from the signed compiled application',
 28: 'Stage optional MSI',
 29: 'Sign with Azure Trusted Signing',
 30: 'Prepare byte-identical signed customer aliases',
 31: 'Verify Windows Authenticode signatures',
 32: 'Package signed Store payload',
 33: 'Run actions/upload-artifact@v6',
 34: 'Exercise the signed portable customer download',
 35: 'Exercise the signed QuickSupport download without an invite',
 36: 'Retain owned ordinary customer application logs',
 37: 'Require the original Windows source preflight before customer artifact upload',
 39: 'Run actions/upload-artifact@v6',
 40: 'Preserve Windows file-dialog lifecycle evidence'}
WINDOWS_DIAGNOSTIC_SKIP = {38:'Retain verified signed Windows installers for unpublished diagnosis'}
JSON_INPUTS = {'producer','producerJobs','windowsJob','armJob','preflight','preflightJobs',
               'comparison','comparisonJobs','commit','request','sourceInputs','provenance',
               'windowsArtifact','payloadArtifact','checkoutEvidence','rootCollectorProvenance'}
REFERENCES = JSON_INPUTS | {'windowsArchive','payloadArchive','windowsJobLog','armJobLog'}
EVIDENCE_PREFIX = 'scripts/qa/current77-private-standard95-evidence/'
REFERENCE_PATHS = {name:EVIDENCE_PREFIX+name+('.log' if name.endswith('Log') else '.json')
                   for name in REFERENCES-{'windowsArchive','payloadArchive','provenance'}}
REFERENCE_PATHS.update({'windowsArchive':'collector/windows.zip','payloadArchive':'collector/windows-msix-payload.zip',
                        'provenance':'collector/artifact-provenance.json'})
HEX = re.compile('[0-9a-f]{64}')


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


def load_pinned(name):
    raw=owned_read(ROOT/name,1_000_000)
    require(sha(raw)==PINS[name],'Frozen normal helper changed: '+name)
    spec=importlib.util.spec_from_file_location('mixel_private95_'+name.replace('-','_'),ROOT/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def completed(record, conclusion, label):
    require(record.get('status')=='completed' and record.get('conclusion')==conclusion,
            label+' must retain exact completed/'+conclusion)


def run_identity(record, run, conclusion):
    require(type(record.get('id')) is int and record['id']==run and record.get('head_sha')==SOURCE
            and type(record.get('run_attempt')) is int and record['run_attempt']==1,
            'Exact current77 run/source/attempt required')
    require(record.get('repository',{}).get('full_name')=='Jangopeople/mixel-remote-client',
            'Exact producer repository required')
    path={RUN:'build.yml',PREFLIGHT:'preflight.yml',COMPARISON:'windows-branding-idempotence.yml'}[run]
    require(record.get('path')=='.github/workflows/'+path
            and record.get('html_url')=='https://github.com/Jangopeople/mixel-remote-client/actions/runs/'+str(run)
            and record.get('event')==('workflow_dispatch' if run==RUN else 'pull_request'),
            'Exact current workflow URL/path/event required')
    completed(record,conclusion,'Run '+str(run))


def inventory(record, expected, run, skipped=()):
    require(isinstance(record,dict) and type(record.get('total_count')) is int
            and isinstance(record.get('jobs'),list) and record['total_count']==len(record['jobs'])==len(expected),
            'Complete exact job inventory required')
    jobs={}
    for job in record['jobs']:
        name=job.get('name')
        require(name in expected and name not in jobs and type(job.get('id')) is int
                and job['id']==expected[name] and type(job.get('run_id')) is int and job['run_id']==run
                and type(job.get('run_attempt')) is int and job['run_attempt']==1 and job.get('head_sha')==SOURCE,
                'Exact job name/ID/run/attempt/source required')
        completed(job,'skipped' if name in skipped else 'failure' if job['id']==ARM else 'success',name)
        steps=job.get('steps')
        require(isinstance(steps,list) and (steps or name in skipped),'Actual job step inventory required')
        require(len({s.get('number') for s in steps})==len(steps) and all(type(s.get('number')) is int
                and isinstance(s.get('name'),str) and s['name'] for s in steps),'Unique typed step inventory required')
        if job['id']!=ARM:
            require(all(s.get('status')=='completed' and s.get('conclusion') in ('success','skipped') for s in steps),
                    'No failed, cancelled, timed out or pending admitted job steps')
        jobs[name]=job
    return jobs


def check_live_files(directory):
    """Caller supplies fresh original API responses before collect and after95.

    No provider request occurs here. A retry, rerun, source/status drift or
    concealed ARM failure is fatal; these records never rewrite Root's gate.
    """
    require(directory.is_absolute() and directory==directory.resolve(strict=True)
            and directory.is_dir(),'Canonical live-state evidence directory required')
    raw={name:owned_read(directory/(name+'.json'),8*1024*1024)
         for name in ('producer','windowsJob','armJob','preflight','comparison')}
    actual={name:unique_json(value) for name,value in raw.items()}
    for name,run,conclusion in [('producer',RUN,'failure'),('preflight',PREFLIGHT,'success'),
                                ('comparison',COMPARISON,'success')]:
        run_identity(actual[name],run,conclusion)
    for name,job,conclusion in [('windowsJob',WINDOWS,'success'),('armJob',ARM,'failure')]:
        record=actual[name]
        require(type(record.get('id')) is int and record['id']==job and record.get('run_id')==RUN
                and type(record.get('run_attempt')) is int and record['run_attempt']==1
                and record.get('head_sha')==SOURCE,'Fresh exact Windows/ARM source/job/attempt required')
        completed(record,conclusion,'Fresh '+name)
    return {name:sha(value) for name,value in raw.items()}


def validate_evidence(binding, data, raw):
    require(binding.get('schema')=='root-current77-private-standard95-decision-v1'
            and binding.get('scope')==SCOPE and binding.get('authorizedBy')=='Root'
            and binding.get('privateDiagnosticGo') is True,'Exact genuine Root private decision required')
    for name in ('root_whole_green_verified','normal_qualified','release_qualified','publication'):
        require(binding.get(name) is False,'Private qualification must remain false: '+name)
    for name,value in {'source':SOURCE,'tree':TREE,'producerRun':RUN,'attempt':1,
                       'windowsJob':WINDOWS,'failedArmJob':ARM,'preflightRun':PREFLIGHT,
                       'comparisonRun':COMPARISON}.items():
        require(type(binding.get(name)) is type(value) and binding[name]==value,'Private identity differs: '+name)
    run_identity(data['producer'],RUN,'failure')
    run_identity(data['preflight'],PREFLIGHT,'success')
    run_identity(data['comparison'],COMPARISON,'success')
    p=data['producerJobs'];expected=dict(PRODUCER_JOBS)
    upload=[j for j in p.get('jobs',[]) if j.get('name')=='upload']
    require(len(upload)==1 and type(upload[0].get('id')) is int and upload[0]['id']>0,'Actual terminal upload job required')
    expected['upload']=upload[0]['id']
    jobs=inventory(p,expected,RUN,PRODUCER_SKIP)
    inventory(data['preflightJobs'],PREF_JOBS,PREFLIGHT,PREF_SKIP)
    inventory(data['comparisonJobs'],COMPARISON_JOBS,COMPARISON)
    require(data['windowsJob']==jobs['windows'] and data['armJob']==jobs['macos (aarch64, aarch64-apple-darwin)'],
            'Original per-job objects must equal whole inventory facts')
    steps={s['number']:s for s in data['windowsJob']['steps']}
    for number,name in WINDOWS_STEPS.items():
        require(number in steps and steps[number]['name']==name,'Mandatory Windows step inventory differs')
        completed(steps[number],'success','Windows step '+str(number))
    for number,name in WINDOWS_DIAGNOSTIC_SKIP.items():
        require(number in steps and steps[number]['name']==name,'Exact disabled diagnostic step required')
        completed(steps[number],'skipped','Disabled diagnostic Windows step '+str(number))
    failed=[s for s in data['armJob']['steps'] if s.get('conclusion')=='failure']
    require(len(failed)==1 and failed[0].get('number')==25
            and failed[0].get('name')=='Package + sign + notarize (.app then .dmg)',
            'Actual ARM warm-package failure must remain visible')
    require(all(s.get('status')=='completed' and s.get('conclusion') in ('success','failure','skipped')
                for s in data['armJob']['steps']),'ARM step facts cannot be pending or cancelled')
    require(sha(raw['armJobLog'])=='e6b02871a2ba72c7e9dbc23d4e5a40faf8cfd56f6b8a88ef374210e1c334ec61'
            and b'FAIL: Warm support URL customer window: customer app exited' in raw['armJobLog'],
            'Exact retained original ARM warm-exit failure log required')
    commit=data['commit']
    require(commit.get('sha')==SOURCE and commit.get('commit',{}).get('tree',{}).get('sha')==TREE,
            'Actual producer source/tree commit required')
    require(sha(raw['request'])=='de56aeef9725505437670740dbe0452eb850570bd504f1e8b0bf88c24fe9ae0f'
            and sha(raw['sourceInputs'])=='48fdcca6372a60e7d86b1195517b7118c9a214150f6eaf873c87311371cd237b',
            'Exact retained producer request/source inputs required')
    checkout=data['checkoutEvidence']
    require(checkout.get('schema')=='root-observed-current77-windows-checkout-v1'
            and checkout.get('source')==SOURCE and checkout.get('tree')==TREE
            and checkout.get('job')==WINDOWS and checkout.get('rootOriginalCheckoutVerified') is True
            and checkout.get('originalLogSha256')==sha(raw['windowsJobLog']),
            'Root original Windows checkout observation required')
    lines=raw['windowsJobLog'].decode('utf-8',errors='strict').splitlines()
    number=checkout.get('lineNumber');line=checkout.get('checkoutSourceLine')
    require(type(number) is int and 1<=number<=len(lines) and lines[number-1]==line
            and isinstance(line,str) and SOURCE in line,'Exact retained Windows checkout source line required')
    return jobs


def validate_artifacts(binding,data,raw,paths,collector):
    run=data['producer'];collector.validate_run(run,RUN)
    job=collector.validate_windows_job(data['producerJobs']['jobs'],RUN,SOURCE)
    artifacts=collector.select_artifacts([data['windowsArtifact'],data['payloadArtifact']],RUN,SOURCE)
    require(set(binding.get('artifacts',{}))==set(artifacts),'Root exact two artifact bindings required')
    mappings={}
    for name,artifact in artifacts.items():
        archive='windowsArchive' if name=='windows' else 'payloadArchive'
        require(len(raw[archive])==artifact.get('size_in_bytes') and
                binding['artifacts'][name]=={'id':artifact['id'],'digest':artifact['digest']},
                'Actual artifact bytes/ID/digest differ')
        files=collector.read_archive(paths[archive],name,artifact['digest'])
        mappings[name]={'artifact_id':artifact['id'],'artifact_digest':artifact['digest'],
            'actual_archive_digest':artifact['digest'],
            'files_sha256':{'artifacts/'+name+'/'+member:sha(value) for member,value in files.items()}}
    expected={'evidence_kind':'debugger-free-windows-customer-release-gate-inputs',
        'repository':'Jangopeople/mixel-remote-client','build_run_id':RUN,'build_url':run['html_url'],
        'source_commit':SOURCE,'application_source_baseline':SOURCE,'build_status':'completed',
        'build_conclusion':'failure','producer_windows_job_id':job['id'],
        'producer_windows_job_conclusion':'success','publication':False,'collected_artifacts':mappings}
    require(data['provenance']==data['rootCollectorProvenance']==expected,
            'Unchanged collector provenance must preserve whole producer failure and exact files')
    return mappings


def qualify(binding_path,binding_digest,repository,collection):
    require(repository.is_absolute() and repository==repository.resolve(strict=True)
            and collection==repository/'release-runtime-collection' and collection.is_dir()
            and collection==collection.resolve(strict=True),'Exact canonical private collector directory required')
    require(isinstance(binding_digest,str) and HEX.fullmatch(binding_digest),'Explicit Root binding SHA required')
    binding_raw=owned_read(binding_path,1_000_000)
    require(sha(binding_raw)==binding_digest,'Root binding byte digest differs')
    binding=unique_json(binding_raw)
    refs=binding.get('references')
    require(isinstance(refs,dict) and set(refs)==REFERENCES,'Complete original evidence references required')
    raw={};paths={}
    for name,item in refs.items():
        require(isinstance(item,dict) and set(item)=={'path','bytes','sha256'}
                and type(item['bytes']) is int and isinstance(item['sha256'],str)
                and HEX.fullmatch(item['sha256']),'Typed exact original reference required')
        limit=512*1024*1024 if name.endswith('Archive') else 64*1024*1024 if name.endswith('Log') else 8*1024*1024
        require(item['path']==REFERENCE_PATHS[name],'Exact fixed private evidence role path required')
        is_collection=item['path'].startswith('collector/')
        parent=collection if is_collection else repository
        relative=item['path'][len('collector/'):] if is_collection else item['path']
        paths[name]=parent/relative;value=owned_read(paths[name],limit)
        if name!='provenance':
            require(len(value)==item['bytes'] and sha(value)==item['sha256'],'Original evidence byte binding differs: '+name)
        raw[name]=value
    root_provenance=raw['rootCollectorProvenance']
    root_dictionary=unique_json(root_provenance)
    lf=(json.dumps(root_dictionary,indent=2)+'\n').encode('utf-8')
    require(root_provenance==lf,'Frozen Root collector derivative must retain its exact LF serialization')
    variants={'lf':lf,'crlf':lf.replace(b'\n',b'\r\n')}
    require(binding.get('derived_provenance_serializations')=={
        key:{'bytes':len(value),'sha256':sha(value)} for key,value in variants.items()},
        'Explicit Root LF/CRLF collector derivative byte pairs required')
    require(refs['provenance']['bytes']==len(lf) and refs['provenance']['sha256']==sha(lf)
            and raw['provenance'] in variants.values(),'Actual native collector derivative bytes differ')
    data={name:unique_json(raw[name]) for name in JSON_INPUTS}
    validate_evidence(binding,data,raw)
    collector=load_pinned('collect-windows-release-runtime.py')
    artifacts=validate_artifacts(binding,data,raw,paths,collector)
    return binding,data,raw,artifacts


def prepare(repository,output,binding_path,binding_digest,collection,harness_source,harness_tree,live_state):
    live_hashes=check_live_files(live_state)
    binding,data,raw,artifacts=qualify(binding_path,binding_digest,repository,collection)
    adapter=load_pinned('prepare-standard-foreground95.py')
    sources=adapter.read_sources(repository)
    candidate,edits=adapter.projected(sources[adapter.DRIVER],sources[adapter.OBSERVER])
    blobs=binding.get('reviewed_harness_blobs',{})
    required={'scripts/prepare-private-standard-foreground95.py','scripts/test-private-standard-foreground95.py',
              'scripts/prepare-standard-foreground95.py','scripts/test-standard-foreground95-adapter.py',
              'scripts/collect-windows-release-runtime.py','scripts/capture-private-standard95-state.py',
              'scripts/private_standard95_workflow_projection.py','.github/workflows/build.yml'}
    require(isinstance(blobs,dict) and required<=set(blobs) and all(
        isinstance(name,str) and re.fullmatch(r'(?:scripts|\.github/workflows)/[A-Za-z0-9_./-]+',name)
        and '..' not in name.split('/') and isinstance(value,str) and HEX.fullmatch(value)
        for name,value in blobs.items()),'Reviewed private caller/helper source blobs required')
    harness=adapter.verify_harness(repository,harness_source,harness_tree,blobs,binding_path,binding_digest)
    parent=Path(os.environ.get('RUNNER_TEMP',''))
    require(str(parent) not in ('','.','/') and parent.is_dir() and not parent.is_symlink(),'Actual owned runner temporary parent required')
    require(output.parent.resolve()==parent.resolve() and re.fullmatch('mixel-standard-foreground95-[0-9a-f]{32}',output.name),
            'Fresh direct owned runner temporary child required')
    require(not output.exists() and not output.is_symlink(),'Existing output refused without mutation')
    output.mkdir(mode=0o700)
    for name,text in sources.items():
        if name!=adapter.DRIVER:(output/name).write_bytes(text.encode())
    (output/'test-standard-foreground95.generated.ps1').write_bytes(candidate.encode())
    record={'schema':1,'scope':SCOPE,'prepared_only':True,'normal_qualified':False,'release_qualified':False,
        'publication':False,'root_whole_green_verified':False,'artifact_source':SOURCE,'artifact_tree':TREE,
        'producer_run':RUN,'producer_attempt':1,'producer_windows_job':WINDOWS,'failed_arm_job':ARM,
        'actual_whole_facts':{name:{'status':data[name]['status'],'conclusion':data[name]['conclusion']}
                              for name in ('producer','preflight','comparison')},
        'actual_arm_failure':{'id':ARM,'status':data['armJob']['status'],'conclusion':data['armJob']['conclusion']},
        'harness':harness,'root_gate_sha256':binding_digest,
        'fresh_preparation_state_sha256':live_hashes,
        'original_evidence_sha256':{name:sha(value) for name,value in raw.items()},'actual_collected_artifacts':artifacts,
        'native_collector_derivative':{'rawSha256':sha(raw['provenance']),
            'rootFrozenSha256':sha(raw['rootCollectorProvenance']),
            'serialization':'crlf' if b'\r\n' in raw['provenance'] else 'lf',
            'providerOriginal':False,'originalRootBytesChanged':False},
        'source_pins':adapter.PINS,'generated_driver_sha256':sha(candidate.encode()),'original_driver_sha256':adapter.PINS[adapter.DRIVER],
        'native_runtime_qualified':False,'outer_token':'runner-original-unchanged','standard_outer_qualified':False,
        'original_cleanup_and_95_assertions_conserved':True,'policy_mutation':False}
    (output/'preparation-manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print('PREPARED PRIVATE DIAGNOSTIC ONLY: whole producer failure retained; actual Standard foreground95 still mandatory.')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    live=commands.add_parser('check-live');live.add_argument('--live-state',type=Path,required=True)
    ready=commands.add_parser('prepare')
    ready.add_argument('--repository',type=Path,required=True);ready.add_argument('--output',type=Path,required=True)
    ready.add_argument('--root-gate',type=Path,required=True);ready.add_argument('--root-gate-sha256',required=True)
    ready.add_argument('--collection',type=Path,required=True);ready.add_argument('--live-state',type=Path,required=True)
    ready.add_argument('--harness-source',required=True);ready.add_argument('--harness-tree',required=True)
    args=parser.parse_args()
    if args.command=='check-live':
        print(json.dumps({'scope':SCOPE,'publication':False,'fresh_original_sha256':check_live_files(args.live_state)},sort_keys=True))
    else:
        prepare(args.repository,args.output,args.root_gate,args.root_gate_sha256,args.collection,args.harness_source,args.harness_tree,args.live_state)


if __name__=='__main__':main()
