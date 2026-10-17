#!/usr/bin/env python3
"""Prepare whole-prefix Windows custody fixture; --run is explicit hosted-only.

Default performs finite source/hash construction only. No compiler, API, process,
account, pipe, input or namespace operation occurs without --run on Windows CI.
"""
from pathlib import Path
import argparse,hashlib,json,os,re,shutil,stat,subprocess,sys,time,uuid

HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]
def sha(raw):return hashlib.sha256(raw).hexdigest()
def require(value,message):
    if not value:raise ValueError(message)
def name_first(parts):
    require(all(p.casefold()!='worktrees' for p in parts),'held path entry')
    require('..' not in parts,'parent path component')
def absolute_name(path):
    # absolute() only anchors text to cwd. Do not resolve/stat/open before this
    # name-first boundary, including for a synthetic owned fixture entry.
    name_first(path.parts)
    path=path if path.is_absolute()else Path.cwd()/path
    name_first(path.parts);return path
def ordinary(path,exists=True):
    path=absolute_name(path);current=Path(path.anchor)
    if os.name=='nt':require(re.fullmatch(r'[A-Za-z]:[\\/]',path.anchor)is not None,'ordinary local drive required')
    parts=path.parts[1:]
    for index,part in enumerate(parts):
        current=current/part
        try:info=current.lstat()
        except FileNotFoundError:
            require(not exists and index==len(parts)-1,'ordinary existing ancestors required')
            return path
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'linked or reparse source/output ancestry')
        if index<len(parts)-1:require(stat.S_ISDIR(info.st_mode),'directory ancestor required')
    return path
def regular(path):
    path=ordinary(path)
    info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'regular single-link source required: '+str(path))
    return path.read_bytes()
def logical(raw):
    if b'\r' in raw:
        remainder=raw.replace(b'\r\n',b'')
        require(b'\r' not in remainder and b'\n' not in remainder,'mixed or bare-CR source newlines')
        return raw.replace(b'\r\n',b'\n')
    return raw
def byte_binding(raw):
    canonical=logical(raw)
    return dict(physicalBytes=len(raw),physicalSHA256=sha(raw),logicalBytes=len(canonical),logicalSHA256=sha(canonical),physicalNewlines='CRLF'if b'\r\n'in raw else'LF')
def save(path,raw):
    require(not path.exists(),'exclusive output: '+str(path));path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb')as out:out.write(raw);out.flush();os.fsync(out.fileno())
def data(path,value):save(path,(json.dumps(value,indent=2,sort_keys=True)+'\n').encode())
def prepare(output,source_root=None):
    # Validate BOTH raw roots before any output creation or source resolution.
    output=absolute_name(output)
    if source_root is not None:source_root=absolute_name(source_root)
    ordinary(output,exists=False)
    if source_root is not None:
        ordinary(source_root);require(stat.S_ISDIR(source_root.lstat().st_mode),'source root directory required')
    ordinary(HERE);ordinary(REPO)
    try:output.lstat()
    except FileNotFoundError:pass
    else:raise ValueError('new exclusive output required')
    output.mkdir(mode=0o700)
    pins=json.loads(regular(HERE/'SOURCE-PINS.json'))
    for name in pins:
        parts=name.split('/')
        name_first(parts)
        require(name.startswith('scripts/') and all(p and re.fullmatch(r'[A-Za-z0-9_.-]+',p)for p in parts) and not Path(name).is_absolute(),'closed relative source pin path')
    verified={}
    for name,pin in pins.items():
        raw=regular(REPO/name);canonical=logical(raw)
        require(sha(canonical)==pin['sha256'] and len(canonical)==pin['bytes'],'logical source pin drift: '+name)
        verified[name]=byte_binding(raw)
    plan=json.loads(regular(REPO/'scripts/support-receiver-custodian-plan.json'))
    rows={row['name']:row for row in plan['files']}
    expected={name:row['afterSha256'] for name,row in rows.items()}
    leaves={row['name']:row for row in plan['owned']}
    expected.update({name:row['sha256'] for name,row in leaves.items()})
    actual={}
    if source_root is not None:
        for name,digest in expected.items():
            raw=regular(source_root/name);require(sha(logical(raw))==digest,'actual final generated prefix drift: '+name)
            actual[name]=raw
    else:
        for name,row in rows.items():actual[name]=row['afterSource'].encode()
        for name,row in leaves.items():actual[name]=regular(REPO/'scripts'/row['template'])
    # Every actual compiled prefix is whole bytes, not extracted decisions or
    # pre-custody templates. Test code is appended only at an explicit EOF.
    files={
        'mixel_receiver_input.rs':actual['src/server/mixel_receiver_input.rs']+regular(HERE/'core-tests.rs')+b'\n#[cfg(test)] pub(crate) fn custody_fixture_main(){windows::custody_fixture_main();}\n',
        'mixel_receiver_input_windows.rs':actual['src/server/mixel_receiver_input_windows.rs'],
        'mixel_receiver_custodian_windows.rs':actual['src/server/mixel_receiver_custodian_windows.rs']+regular(HERE/'custodian-tests.rs'),
        'mixel_receiver_custodian_epoch_windows.rs':actual['src/server/mixel_receiver_custodian_epoch_windows.rs']+regular(HERE/'epoch-tests.rs'),
    }
    helpers=regular(REPO/'scripts/fixtures/receiver-input-native/tests/windows-cross-sid-tests.rs')
    cut=b'fn qa_parent_body(){';require(helpers.count(cut)==1,'closed existing memory-only account helper boundary')
    # Exact current bounded OVERLAPPED transport has one inert self-test whose
    # two helpers occur later in the source. Include those exact closed bodies,
    # never an arbitrary function/name fallback or the old NOWAIT transport.
    full_helpers=helpers;helpers=full_helpers[:full_helpers.index(cut)]
    for begin,end in [(b'fn qa_phase(',b'fn qa_api_result('),(b'fn qa_reply(',b'pub(crate) fn cross_sid_logon_child(')]:
        require(full_helpers.count(begin)==1 and full_helpers.count(end)==1,'closed current transport helper boundaries')
        a=full_helpers.index(begin);b=full_helpers.index(end,a);helpers+=full_helpers[a:b]
    native_tests=regular(HERE/'native-tests.rs');marker=b'// @EXACT_ACCOUNT_HELPERS@';require(native_tests.count(marker)==1,'closed account insertion')
    files['mixel_receiver_input_windows.rs']+=b'\n'+native_tests.replace(marker,helpers)
    for name in ('Cargo.toml','Cargo.lock','build.rs','native-main.rs'):files[name]=regular(HERE/name)
    original=rows['src/server/mixel_receiver_input.rs']['beforeSource'].encode()+regular(HERE/'original-tests.rs')
    files['original/mixel_receiver_input.rs']=original
    files['original/mixel_receiver_input_windows.rs']=rows['src/server/mixel_receiver_input_windows.rs']['beforeSource'].encode()
    files['native-main-original.rs']=files['native-main.rs'].replace(b'#[path="../mixel_receiver_input.rs"]',b'#[path="../original/mixel_receiver_input.rs"]').replace(b'fn main(){server::mixel_receiver_input::custody_fixture_main();}',b'fn main(){server::mixel_receiver_input::original_negative_main();}')
    files['server/mixel_receiver_receipts.rs']=regular(REPO/'scripts/support-receiver-receipts.rs')
    files['protos/message.proto']=regular(REPO/'scripts/fixtures/receiver-input-native/protos/message.proto')
    (output/'server/input_service').mkdir(parents=True)
    for name,raw in files.items():save(output/name,raw)
    bindings=dict(scope='whole final production prefixes; mock native input effects only',actualGeneratedSourceProvided=source_root is not None,
        sourceRoot=str(source_root)if source_root else None,sourcePins=pins,verifiedPhysicalAndLogicalSourceInputs=verified,
        compiledProductionPrefixes={name:byte_binding(raw)for name,raw in actual.items()},
        originalNegativePrefixes={name:row['beforeSha256']for name,row in rows.items()},
        rust='1.75.0',desktopInput=False,SendSAS=False,UAC=False,publication=False,applicationQualified=False,
        boundary='Custodian/OS death cannot prove native cleanup. Unknown dirty custody refuses regrant; only own cfg(test) mock namespace is removed after test actors exit.')
    data(output/'SOURCE-BINDINGS.json',bindings)
    data(output/'PREPARED-MANIFEST.json',{name:dict(bytes=len(raw),sha256=sha(raw))for name,raw in files.items()})
    return bindings
def command(output,label,argv,env,bound):
    start=time.time();error=None;code=None;stdout=b'';stderr=b''
    try:
        result=subprocess.run(argv,cwd=output,env=env,capture_output=True,timeout=bound)
        code=result.returncode;stdout=result.stdout;stderr=result.stderr
    except subprocess.TimeoutExpired as failure:
        # subprocess.run has killed/waited its exact command child. Any fixture
        # actor cleanup remains unqualified on this path; retain all evidence.
        stdout=failure.stdout or b'';stderr=failure.stderr or b'';error='owned command timeout; actor/account cleanup not qualified'
    except OSError as failure:error=type(failure).__name__+': '+str(failure)
    save(output/(label+'.stdout'),stdout);save(output/(label+'.stderr'),stderr)
    data(output/(label+'.json'),dict(argv=argv,exit=code,error=error,startUnix=start,endUnix=time.time(),timeoutSeconds=bound))
    if code!=0 or error:
        for kind,raw in [('stdout',stdout),('stderr',stderr)]:
            tail=b'\n'.join(raw.splitlines()[-120:])[-24576:]
            print('BEGIN retained '+label+' '+kind+' bounded tail',flush=True)
            print(tail.decode('utf-8','replace'),flush=True)
            print('END retained '+label+' '+kind+' bounded tail',flush=True)
        raise ValueError('native command failed; complete raw evidence retained: '+label)
    return stdout
def run(output,bindings):
    require(sys.platform=='win32','--run requires actual Windows')
    require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='Windows','isolated hosted Windows only')
    require(bindings['actualGeneratedSourceProvided'],'--run requires the actual final generated source root')
    env=os.environ.copy();env['CARGO_TARGET_DIR']=str(output/'target');env['MIXEL_CUSTODY_QA_NAMESPACE']=uuid.uuid4().hex;env['MIXEL_CUSTODY_QA_OUTPUT']=str(output);env['MIXEL_CUSTODY_QA_CASE']='component-decisions'
    raw=command(output,'rust-version',['rustc','+1.75.0','--version'],env,15);require(raw.decode().startswith('rustc 1.75.0 '),'exact held Rust compiler')
    raw=command(output,'compile',['cargo','+1.75.0','test','--locked','--no-run','--test','custody','--test','original-negative','--message-format=json-render-diagnostics'],env,240)
    artifacts={}
    for line in raw.splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if value.get('reason')=='compiler-artifact' and value.get('target',{}).get('name')in('custody','original-negative') and value.get('executable'):
            name=value['target']['name'];require(name not in artifacts,'unique compiled fixture artifact');artifacts[name]=value['executable']
    require(set(artifacts)=={'custody','original-negative'},'both actual compiled target executables')
    for name in ('original-negative','custody'):
        executable=Path(artifacts[name]).resolve(strict=True);require(executable.is_relative_to(output.resolve()),'owned compiled fixture only')
        command(output,'run-'+name,[str(executable)],env,180)
    data(output/'NATIVE-RESULT.json',dict(scope='component only: mock effects and real Windows kernel/process/pipe/account lifecycle',allCommandsPassed=True,applicationQualified=False,releaseQualified=False,nativeDesktopInputExecuted=False,SendSASExecuted=False))
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source-root',type=Path);parser.add_argument('--run',action='store_true');args=parser.parse_args()
    require(args.output.is_absolute(),'absolute exclusive output required');bindings=prepare(args.output,args.source_root)
    if args.run:run(args.output,bindings)
    print('PASS: exact custody fixture source prepared'+('; actual hosted component commands passed'if args.run else '; no compiler/native/runtime invoked'))
if __name__=='__main__':main()
