#!/usr/bin/env python3
"""Build an unsigned native Windows x64 payload; no install/sign/client launch/publish.

Default is inert. --export-bridge creates a source-only capsule from qualified
Mac R4 evidence; --run consumes preinstalled Windows tools/codecs and that capsule.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import time

REPO=Path(__file__).absolute().parents[2]
sys.path.insert(0,str(REPO/'scripts'))

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

B=load(REPO/'scripts/local/build-macos.py','windows_common_build')
C=B.load_check(REPO)
TARGET='x86_64-pc-windows-msvc'
TOOLCHAIN='1.75'
TRIPLET='x64-windows-static'
MIN_FREE_GIB=80
EPOCH='src/server/mixel_receiver_custodian_epoch_windows.rs'
EPOCH_BEFORE='b662ed35b2bea75c7f02214424186b983096565ab79bfbcb5625b5a165e6aeb7'
EPOCH_AFTER='f362530835fd3a45cb98d8616dc7517cc077189d700a1790e7700a434085c9a2'
SHIPPING_PUBSPEC='cfa3c757fdd04e8a293b0f5bfcea286ec31e904786a4467d66867dbdd2ef5358'
CODECS=('vpx','yuv','opus','aom','jpeg','turbojpeg')
FLUTTER_PLUGIN_REGISTRANTS=frozenset({
    'flutter/ios/Runner/GeneratedPluginRegistrant.h',
    'flutter/ios/Runner/GeneratedPluginRegistrant.m',
    'flutter/linux/flutter/generated_plugin_registrant.cc',
    'flutter/linux/flutter/generated_plugin_registrant.h',
    'flutter/windows/flutter/generated_plugin_registrant.cc',
    'flutter/windows/flutter/generated_plugin_registrant.h',
})
Refusal=C.Refusal


def file(path, *, independent=True):
    path=Path(path);C.ordinary_path(path.parent,exists=True)
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or (independent and info.st_nlink!=1) or getattr(info,'st_file_attributes',0)&0x400):
        raise Refusal('ordinary unaliased file required: '+str(path))
    return path


def read_json(path,**required):
    value=json.loads(file(path).read_text(encoding='utf-8'))
    if any(value.get(k)!=v for k,v in required.items()):raise Refusal('required proof changed: '+str(path))
    return value


def inventory(root,probe=None):
    entries=C.inventory(root)
    for item in entries:
        if item['kind']=='symlink':raise Refusal('indirect source/cache entry: '+item['path'])
        if probe is not None:C.windows_private_entry(root/item['path'],probe,directory=item['kind']=='directory')
        elif (root/item['path']).stat().st_uid!=os.getuid() or (item['kind']=='file' and (root/item['path']).stat().st_nlink!=1):
            raise Refusal('export input is not current-user owned and independent')
    return entries


def logical_file(path):
    raw=file(path).read_bytes()
    try:raw.decode('utf-8')
    except UnicodeDecodeError:return {'logicalSha256':hashlib.sha256(raw).hexdigest(),'logicalSize':len(raw),'newlineView':'binary-exact'}
    if b'\0' in raw:return {'logicalSha256':hashlib.sha256(raw).hexdigest(),'logicalSize':len(raw),'newlineView':'binary-exact'}
    logical=raw.replace(b'\r\n',b'\n')
    if b'\r' in logical or (b'\r\n' in raw and b'\n' in raw.replace(b'\r\n',b'')):
        raise Refusal('mixed/bare-CR source newlines: '+str(path))
    return {'logicalSha256':hashlib.sha256(logical).hexdigest(),'logicalSize':len(logical),'newlineView':'CRLF' if b'\r\n' in raw else 'LF'}


def bridge_inputs(entries,root):
    """Authored Rust/native inputs; Flutter regenerates its plugin registrants."""
    result=[]
    for item in entries:
        name=item['path'];parts=Path(name).parts
        if item['kind']!='file' or '.git' in parts or name in B.BRIDGE_FILES or name=='src/version.rs' or name in FLUTTER_PLUGIN_REGISTRANTS:continue
        if (parts[0] in ('src','libs','.mixel-deps','.cargo') or
                Path(name).name in ('Cargo.toml','Cargo.lock','build.rs') or
                Path(name).suffix in ('.rs','.c','.h','.cpp','.hpp','.cc','.m','.mm','.S')):
            result.append({'path':name,'sha256':item['sha256'],'size':item['size'],**logical_file(root/name)})
    if not result:raise Refusal('empty bridge input closure')
    return result


def pipeline_files(repo,names):
    if any(Path(name).is_absolute() or '..' in Path(name).parts or any(c in name for c in '\r\n\0') for name in names):
        raise Refusal('unsafe relative pipeline input')
    return [{'path':name,'sha256':B.digest(file(repo/name))} for name in sorted(set(names))]


def export_bridge(mac_build,destination,repo=REPO):
    if platform.system()!='Darwin' or Path.cwd()!=repo:raise Refusal('bridge export requires exact local Mac checkout')
    mac_build=B.owned_directory(mac_build);destination=Path(destination)
    root=B.owned_directory(repo/'_scratch')
    if not destination.is_absolute() or destination.parent!=root or destination.exists() or destination.is_symlink():
        raise Refusal('fresh bridge capsule must be directly inside owned _scratch')
    result=read_json(mac_build/'build.result.json',buildQualified=True,buildClosed=True,cargoLockUnchanged=True)
    if any(result.get(k) is not True for k in ('buildQualified','buildClosed','cargoLockUnchanged')):
        raise Refusal('qualified closed Mac result with strict lock proof required')
    if (mac_build/'build-active.json').exists():raise Refusal('Mac input is active')
    app=mac_build/'rustdesk'
    # Inspect generator inputs only: generated platform caches are not source.
    for relative,names in {'flutter/macos':{'Pods','.symlinks'},'flutter':{'.flutter-plugins','.flutter-plugins-dependencies'}}.items():
        C.CACHES.setdefault(relative,set()).update(names)
    before=inventory(app)
    inputs=bridge_inputs(before,app)
    epoch=next((x for x in inputs if x['path']==EPOCH),None)
    if not epoch or epoch['sha256']!=EPOCH_BEFORE:raise Refusal('Mac bridge epoch predecessor is not the exact reviewed leaf')
    if B.digest(file(repo/'scripts/support-receiver-custodian-epoch-windows.rs'))!=EPOCH_AFTER:
        raise Refusal('current reviewed Windows leaf differs from pinned successor')
    if B.digest(file(app/'Cargo.lock'))!=result.get('cargoLockAfterFinalSha256'):
        raise Refusal('Mac held lock changed')
    if logical_file(app/'flutter/pubspec.yaml')['logicalSha256']!=SHIPPING_PUBSPEC:raise Refusal('shipping14 profile changed')
    bound=read_json(mac_build/'continuation-admission.json')['bridgeFiles']
    bridge={name:B.digest(file(app/name)) for name in B.BRIDGE_FILES}
    if set(bound)!=set(B.BRIDGE_FILES) or bridge!=bound:raise Refusal('Mac six bridge outputs differ from actual generation proof')
    generated=B.owned_directory(Path(result['reuseFrom']))
    generation,_,_=B.command_evidence(generated,'generate-bridge')
    version,_,version_raw=B.command_evidence(generated,'reuse-codegen-version')
    if (generation['argv']!=[str(generated/'codegen/bin/flutter_rust_bridge_codegen'),'--rust-input','./src/flutter_ffi.rs',
                            '--dart-output','./flutter/lib/generated_bridge.dart','--c-output','./flutter/macos/Runner/bridge_generated.h'] or
            version['argv']!=[str(generated/'codegen/bin/flutter_rust_bridge_codegen'),'--version'] or
            version_raw['stdout'].read_text().strip()!='flutter_rust_bridge_codegen 1.80.1'):
        raise Refusal('actual pinned FRB generation/version receipt changed')
    git=subprocess.run(['git','-C',str(repo),'ls-files','-z','--','scripts','branding','.github/workflows/build.yml'],
                       env=B.git_environment(dict(os.environ)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if git.returncode:raise Refusal('cannot inventory current source pipeline')
    names=list(filter(None,git.stdout.decode().split('\0')))+['scripts/local/build-windows.py']
    pipeline=pipeline_files(repo,names)
    destination.mkdir(mode=0o700)
    for name in B.BRIDGE_FILES:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(app/name,target)
    proof={'schema':1,'upstreamHead':B.UPSTREAM_HEAD,'hbbHead':B.HBB_HEAD,'flutterHead':B.FLUTTER_HEAD,
           'flutterVersion':B.FLUTTER_VERSION,'frbVersion':'1.80.1','frbFeatures':['uuid'],'macBuildSourceHead':result['sourceHead'],
           'macBuildResultSha256':B.digest(mac_build/'build.result.json'),'macBuildQualified':True,'bridgeFiles':bridge,
           'inputs':inputs,'inputsSha256':C.binding(inputs),'pipeline':pipeline,'shippingPubspecSha256':SHIPPING_PUBSPEC,
           'exactWindowsLeafDelta':{'path':EPOCH,'before':EPOCH_BEFORE,'after':EPOCH_AFTER}}
    B.write_json(destination/'bridge-proof.json',proof)
    if before!=inventory(app) or pipeline!=pipeline_files(repo,names):raise Refusal('source changed while exporting bridge')
    print('PASS: six-file source-only bridge capsule; no compiler/provider/native execution')
    return proof


def admit_bridge(capsule,app,probe,repo):
    proof=read_json(capsule/'bridge-proof.json',schema=1,upstreamHead=B.UPSTREAM_HEAD,hbbHead=B.HBB_HEAD,
                    flutterHead=B.FLUTTER_HEAD,flutterVersion=B.FLUTTER_VERSION,frbVersion='1.80.1',frbFeatures=['uuid'],macBuildQualified=True)
    if proof.get('macBuildQualified') is not True or C.binding(proof['inputs'])!=proof.get('inputsSha256'):
        raise Refusal('bridge whole-input proof is not bound')
    if proof.get('exactWindowsLeafDelta')!={'path':EPOCH,'before':EPOCH_BEFORE,'after':EPOCH_AFTER}:
        raise Refusal('unreviewed bridge source delta')
    if proof['pipeline']!=pipeline_files(repo,[x['path'] for x in proof['pipeline']]):
        raise Refusal('current source pipeline differs from exported bridge inputs')
    actual=bridge_inputs(inventory(app,probe),app);expected=proof['inputs']
    original=next((x for x in expected if x['path']==EPOCH),None)
    current=next((x for x in actual if x['path']==EPOCH),None)
    if not original or original['logicalSha256']!=EPOCH_BEFORE or not current or current['logicalSha256']!=EPOCH_AFTER:
        raise Refusal('exact full old/new Windows epoch pair required')
    projected=[original if x['path']==EPOCH else x for x in actual]
    logical=lambda rows:[{k:x[k] for k in ('path','logicalSha256','logicalSize')} for x in rows]
    if logical(projected)!=logical(expected):raise Refusal('Rust/native/Cargo/local-dependency bridge inputs differ beyond reviewed exact leaf')
    if logical_file(app/'flutter/pubspec.yaml')['logicalSha256']!=SHIPPING_PUBSPEC:raise Refusal('shipping extended_text14 profile required')
    admitted={**proof,'actualWindowsPhysicalInputs':actual,'actualShippingPubspecSha256':B.digest(app/'flutter/pubspec.yaml')}
    if set(proof['bridgeFiles'])!=set(B.BRIDGE_FILES):raise Refusal('exact six bridge output set required')
    for name,digest in proof['bridgeFiles'].items():
        if B.digest(file(capsule/name))!=digest:raise Refusal('exported bridge file changed: '+name)
        dest=app/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(capsule/name,dest)
        if B.digest(file(dest))!=digest:raise Refusal('bridge copy changed')
    C.require_source(app)
    return admitted


def safe_environment(inherited):
    process_policy=[value for key,value in inherited.items() if key.casefold()=='psexecutionpolicypreference']
    if process_policy and (any(value.casefold()!='remotesigned' for value in process_policy)):
        raise Refusal('only an existing explicit RemoteSigned Process execution policy is admitted')
    for name in ('RUSTC','RUSTC_WRAPPER','RUSTC_WORKSPACE_WRAPPER','RUSTDOC','CARGO_TARGET_DIR','CARGO_BUILD_TARGET'):
        if inherited.get(name):raise Refusal('unexpected compiler/output override: '+name)
    # CPython's Windows platform.machine() reads these inherited OS values.
    # Preserve them for child admission; WindowsSecurityProbe independently
    # verifies the actual process/native machine via IsWow64Process2.
    keep={'path','systemroot','windir','comspec','temp','tmp','userprofile','home','appdata','localappdata','programfiles','programfiles(x86)',
          'processor_architecture','processor_architew6432',
          # Visual Studio's setup query resolves its installed-instance catalog
          # through these OS directories, including when Flutter invokes it.
          'programdata','systemdrive',
          'vctoolsinstalldir','vcinstalldir','vsinstalldir','visualstudioversion','vscmd_arg_tgt_arch','vscmd_arg_host_arch',
          'windowssdkdir','windowssdkversion','windowssdkbinpath','windowssdkverbinpath','universalcrtsdkdir','ucrtversion',
          'include','lib','libpath','rustup_home','http_proxy','https_proxy','no_proxy','ssl_cert_file','ssl_cert_dir'}
    selected={}
    for key,value in inherited.items():
        if key.casefold() not in keep:continue
        if key.upper() in selected and selected[key.upper()]!=value:raise Refusal('conflicting Windows environment aliases: '+key)
        selected[key.upper()]=value
    result=B.git_environment(selected)
    if process_policy:result['PSExecutionPolicyPreference']='RemoteSigned'
    result.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1',PYTHONNOUSERSITE='1',CI='true',FLUTTER_SUPPRESS_ANALYTICS='true',DART_SUPPRESS_ANALYTICS='true')
    return result


POLICY_QUERY="$scopes=@{}; foreach($scope in @('MachinePolicy','UserPolicy','Process','CurrentUser','LocalMachine')) { $scopes[$scope]=[string](Get-ExecutionPolicy -Scope $scope) }; [ordered]@{effective=[string](Get-ExecutionPolicy);scopes=$scopes;processEnvironment=$env:PSExecutionPolicyPreference} | ConvertTo-Json -Depth 3 -Compress"


def execution_policy(raw):
    proof=json.loads(raw)
    scopes=('MachinePolicy','UserPolicy','Process','CurrentUser','LocalMachine')
    if (set(proof)!= {'effective','scopes','processEnvironment'} or
            not isinstance(proof['scopes'],dict) or set(proof['scopes'])!=set(scopes)):
        raise Refusal('complete actual PowerShell execution-policy metadata required')
    if any(proof['scopes'][scope] not in ('Undefined','RemoteSigned') for scope in ('MachinePolicy','UserPolicy')):
        raise Refusal('Group Policy does not permit the explicit RemoteSigned process context')
    if (proof['effective']!='RemoteSigned' or proof['scopes']['Process']!='RemoteSigned' or
            proof['processEnvironment']!='RemoteSigned'):
        raise Refusal('actual effective and explicit Process execution policy must both be RemoteSigned')
    return proof


def dependency_inventory(root,probe):
    """Hold every LLVM dependency byte; application cache exclusions do not apply."""
    entries=[]
    def visit(directory):
        C.windows_private_entry(directory,probe,directory=True)
        for path in sorted(directory.iterdir()):
            info=path.lstat();name=path.relative_to(root).as_posix()
            directory_entry=stat.S_ISDIR(info.st_mode)
            C.windows_private_entry(path,probe,directory=directory_entry)
            if directory_entry:
                entries.append({'path':name,'kind':'directory'});visit(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                entries.append({'path':name,'kind':'file','size':info.st_size,'sha256':B.digest(file(path))})
            else:raise Refusal('ordinary independent LLVM dependency entries required')
    visit(root)
    return entries


def libclang_version(path):
    """Query the actual admitted DLL, without parsing or compiling source."""
    if os.name!='nt':raise Refusal('libclang identity query requires native Windows')
    import ctypes
    class CXString(ctypes.Structure):
        _fields_=[('data',ctypes.c_void_p),('private_flags',ctypes.c_uint)]
    try:
        # LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32:
        # resolve private adjacent dependencies and OS runtimes, never cwd/PATH.
        library=ctypes.CDLL(str(path),winmode=0x900)
        for name in ('clang_createIndex','clang_disposeIndex','clang_parseTranslationUnit2'):
            getattr(library,name)
        library.clang_getClangVersion.argtypes=[];library.clang_getClangVersion.restype=CXString
        library.clang_getCString.argtypes=[CXString];library.clang_getCString.restype=ctypes.c_char_p
        library.clang_disposeString.argtypes=[CXString];library.clang_disposeString.restype=None
        value=library.clang_getClangVersion()
        try:
            raw=library.clang_getCString(value)
            if not raw or len(raw)>4096:raise Refusal('missing or oversized actual libclang version')
            version=raw.decode('utf-8')
        finally:library.clang_disposeString(value)
    except (OSError,AttributeError,UnicodeError) as error:
        raise Refusal('admitted native libclang DLL could not expose its actual identity') from error
    if re.search(r'clang version \d+',version) is None:raise Refusal('unexpected actual libclang version')
    return version


def admit_libclang(path,repo,source,output,probe,dependencies=()):
    path=Path(path)
    if not path.is_absolute() or path.name.casefold()!='libclang.dll' or path.parent.name.casefold()!='bin':
        raise Refusal('--libclang must be an absolute preinstalled bin/libclang.dll path')
    C.ordinary_path(path.parent,exists=True);path=file(path);root=path.parent.parent
    if (root==repo or not root.is_relative_to(repo) or any(root.is_relative_to(other) or other.is_relative_to(root)
            for other in (source,output,*dependencies))):
        raise Refusal('LLVM must be a separate owned dependency inside the repository')
    for parent in (root,*root.parents):
        C.windows_private_entry(parent,probe,directory=True)
        if parent==repo:break
    before=dependency_inventory(root,probe);pe_x64(path)
    headers=sorted(root.glob('lib/clang/*/include/stddef.h'))
    if not headers:raise Refusal('preinstalled LLVM resource headers are missing')
    version=libclang_version(path)
    if before!=dependency_inventory(root,probe):raise Refusal('LLVM dependency changed during admission')
    return {'dll':str(path),'root':str(root),'directory':str(path.parent),'dllSha256':B.digest(path),
            'version':version,'resourceHeaders':[p.relative_to(root).as_posix() for p in headers],
            'inputs':before,'inputsSha256':C.binding(before)}


def pe_x64(path):
    # Windows system tools can legitimately share WinSxS hardlinks. Source,
    # cache and output objects retain their independent-file admission above.
    with file(path,independent=False).open('rb') as stream:
        header=stream.read(64)
        if len(header)!=64 or header[:2]!=b'MZ':raise Refusal('native PE executable required: '+str(path))
        offset=struct.unpack_from('<I',header,60)[0]
        if offset>16*1024*1024:raise Refusal('invalid PE offset')
        stream.seek(offset);signature=stream.read(6)
    if signature!=b'PE\0\0\x64\x86':raise Refusal('native AMD64 PE required: '+str(path))


def codec_archives(vcpkg):
    report={}
    status=file(vcpkg/'installed/vcpkg/status').read_text()
    for package in ('libvpx','libyuv','opus','aom','libjpeg-turbo'):
        if not any('Package: '+package+'\n' in '\n'+part+'\n' and 'Architecture: '+TRIPLET+'\n' in '\n'+part+'\n' for part in status.split('\n\n')):
            raise Refusal('preinstalled pinned vcpkg codec absent: '+package+':'+TRIPLET)
    for name in CODECS:
        path=file(vcpkg/'installed'/TRIPLET/'lib'/(name+'.lib'));data=path.read_bytes()
        if not data.startswith(b'!<arch>\n'):raise Refusal('ordinary static codec archive required: '+name)
        offset=8;count=0
        while offset<len(data):
            header=data[offset:offset+60]
            if len(header)!=60 or header[58:]!=b'`\n':raise Refusal('invalid static archive member')
            length_text=header[48:58].decode().strip()
            if not length_text.isdecimal():raise Refusal('invalid static archive member length')
            length=int(length_text);start=offset+60;end=start+length
            if end>len(data):raise Refusal('truncated static codec archive')
            if header[:16].decode().strip() not in ('/','//'):
                member=data[start:end]
                if len(member)<20:raise Refusal('truncated COFF codec object')
                machine=struct.unpack_from('<H',member)[0]
                if member[:4]==b'\0\0\xff\xff':machine=struct.unpack_from('<H',member,6)[0]
                if machine!=0x8664:raise Refusal('non-AMD64 codec object: '+name)
                count+=1
            offset=end+(length&1)
            if offset>len(data) or (length&1 and data[end:offset]!=b'\n'):raise Refusal('invalid static archive member padding')
        if not count:raise Refusal('empty static codec archive')
        report[path.name]={'sha256':B.digest(path),'amd64Members':count}
    return report


def copy_source(source,destination,probe):
    before=inventory(source,probe)
    def ignored(directory,names):return [n for n in names if C.excluded(Path(directory).relative_to(source).as_posix(),n)]
    shutil.copytree(source,destination,symlinks=True,ignore=ignored)
    if before!=inventory(destination,probe) or before!=inventory(source,probe):raise Refusal('input changed while copying')
    return before


def batch(cmd,binary,*args):
    values=[str(binary),*[str(x) for x in args]]
    if any(any(c in value for c in '%!&^|<>\r\n\0"') for value in values):raise Refusal('unsafe batch argument')
    return [cmd,'/d','/c','call',*values]


class Runner(B.Runner):
    """Same bounded raw capture as Mac, with an actual Windows scope receipt."""
    def call(self,label,argv,cwd,*,read=False,timeout=7200):
        self.count+=1;stem=self.commands/(f'{self.count:03d}-'+label);argv=[str(x) for x in argv]
        B.write_json(stem.with_suffix('.command.json'),{'argv':argv,'cwd':str(cwd),'timeoutSeconds':timeout,
                     'scope':'local unsigned native Windows x64 application build'})
        result={'argv':argv,'cwd':str(cwd),'status':'failed','returncode':None};started=time.monotonic()
        stdout,stderr=stem.with_suffix('.stdout.log'),stem.with_suffix('.stderr.log')
        print('RUN: '+label,flush=True)
        try:
            with stdout.open('xb') as out,stderr.open('xb') as err:
                process=subprocess.run(argv,cwd=cwd,env=self.environment,stdin=subprocess.DEVNULL,stdout=out,stderr=err,check=False,timeout=timeout)
            result.update(returncode=process.returncode,status='passed' if process.returncode==0 else 'failed')
        except (OSError,subprocess.SubprocessError) as error:result['error']=str(error)
        finally:
            result.update(elapsedSeconds=round(time.monotonic()-started,3),stdoutSha256=B.digest(stdout),stderrSha256=B.digest(stderr))
            B.write_json(stem.with_suffix('.result.json'),result)
        if result['status']!='passed':raise Refusal(label+' failed; exact raw evidence: '+str(stem))
        if read:
            if stdout.stat().st_size>4_000_000:raise Refusal('oversized tool identity output')
            return stdout.read_bytes()
        return result


def build(args,repo=REPO):
    jobs=B.job_count(args.jobs);env=safe_environment(os.environ)
    if env.get('PSExecutionPolicyPreference')!='RemoteSigned':
        raise Refusal('caller must explicitly authorize an existing RemoteSigned Process context; driver never changes policy')
    probe=C.WindowsSecurityProbe() # Actual IsWow64Process2/token SID, never OS/env spoofing.
    if sys.version_info[:2]!=(3,11):raise Refusal('preinstalled native Python3.11 required')
    pe_x64(Path(sys.executable))
    C.windows_private_entry(Path(__file__),probe,directory=False)
    root=C.windows_local_admission(args.run_root,args.source,args.output,C.matrix(TARGET),prepared=False)
    output=C.ordinary_path(args.output,exists=False)
    for value in (args.vcpkg,args.bridge):
        C.ordinary_path(value,exists=True);C.windows_private_entry(value,probe,directory=True)
        if not value.is_relative_to(repo) or value.is_relative_to(output) or output.is_relative_to(value):raise Refusal('dependency/capsule must be separate inside repository')
    if shutil.disk_usage(root).free<MIN_FREE_GIB*1024**3:raise Refusal('at least80GiB free on owned output volume required; no disk/VM change is performed')
    flutter=C.ordinary_path(args.flutter,exists=True)
    libclang=admit_libclang(args.libclang,repo,args.source,output,probe,(args.vcpkg,args.bridge,flutter))
    from rust_toolchain import find_msvc_linker,windows_environment_value
    linker=find_msvc_linker(env);pe_x64(linker);pe_x64(linker.with_name('cl.exe'))
    if any(windows_environment_value(env,name)!='x64' for name in ('VSCMD_ARG_TGT_ARCH','VSCMD_ARG_HOST_ARCH')):
        raise Refusal('pre-activate installed native x64 MSVC environment')
    for name in ('INCLUDE','LIB','WindowsSdkDir','WindowsSDKVersion'):
        if not windows_environment_value(env,name):raise Refusal('installed native Windows SDK developer environment missing: '+name)
    sdk=Path(windows_environment_value(env,'WindowsSdkDir'));sdk_version=windows_environment_value(env,'WindowsSDKVersion').rstrip('\\/')
    sdk_inputs=[file(sdk/'Include'/sdk_version/'um/Windows.h',independent=False),
                file(sdk/'Lib'/sdk_version/'um/x64/kernel32.lib',independent=False),
                file(sdk/'Lib'/sdk_version/'ucrt/x64/ucrt.lib',independent=False)]
    tools={name:shutil.which(name,path=env.get('PATH',env.get('Path',''))) for name in ('git.exe','bash.exe','rustup.exe','cmake.exe','ninja.exe','magick.exe','pwsh.exe')}
    if any(not path for path in tools.values()):raise Refusal('preinstall required tools on PATH: '+', '.join(k for k,v in tools.items() if not v))
    for tool in tools.values():pe_x64(Path(tool))
    bash_parent=Path(tools['bash.exe']).parent
    if not any(path.is_file() for path in (bash_parent/'msys-2.0.dll',bash_parent.parent/'usr/bin/msys-2.0.dll')):
        raise Refusal('actual installed Git Bash/MSYS runtime required; WSL is not the native Windows build shell')
    flutter_bat=file(flutter/'bin/flutter.bat');dart=file(flutter/'bin/cache/dart-sdk/bin/dart.exe');pe_x64(dart)
    engine=file(flutter/'bin/cache/artifacts/engine/windows-x64-release/flutter_windows.dll');pe_x64(engine)
    file(flutter/'bin/cache/artifacts/engine/windows-x64-release/flutter_windows.dll.lib')
    # Windows services this OS binary through WinSxS hard links, like SDK inputs.
    # Build-owned files remain single-link; hold the system command bytes below.
    command=file(Path(windows_environment_value(env,'SystemRoot'))/'System32/cmd.exe',independent=False);pe_x64(command)
    try:import brotli
    except ImportError as error:raise Refusal('preinstall Python Brotli from pinned portable requirements; no pip runs in this driver') from error
    output.mkdir(mode=0o700);C.windows_private_entry(output,probe,directory=True)
    B.write_json(output/'build-active.json',{'pid':os.getpid(),'target':TARGET,'jobs':jobs})
    runner=Runner(output,env)
    B.write_json(output/'libclang-admission.json',libclang)
    result={'buildQualified':False,'target':TARGET,'jobs':jobs,'unsignedOnly':True,'nativeRuntimeQualified':False,'signingQualified':False,'publish':False,'executionPolicyQualified':False}
    before=vcpkg_before=vcpkg_metadata_before=capsule_before=pipeline=tool_inputs=None;lock=None;app=output/'rustdesk'
    try:
        def call(label,argv,cwd=repo,read=False):return runner.call(label,argv,cwd,read=read)
        def git(label,path,*args):return call(label,[tools['git.exe'],'-C',path,*args],read=True)
        policy=json.loads(call('powershell-execution-policy',[tools['pwsh.exe'],'-NoLogo','-NoProfile','-NonInteractive','-Command',POLICY_QUERY],read=True))
        B.write_json(output/'execution-policy.json',policy);execution_policy(json.dumps(policy));result['executionPolicyQualified']=True
        head=git('pipeline-head',repo,'rev-parse','HEAD').decode().strip()
        names=git('pipeline-files',repo,'ls-files','-z','--','scripts','branding','.github/workflows/build.yml').decode().split('\0')
        names=list(filter(None,names))+['scripts/local/build-windows.py'];pipeline=pipeline_files(repo,names)
        B.write_json(output/'pipeline-inputs.json',{'sourceHead':head,'entries':pipeline})
        for label,path,expected in (('source',args.source,B.UPSTREAM_HEAD),('hbb',args.source/'libs/hbb_common',B.HBB_HEAD),('vcpkg',args.vcpkg,B.VCPKG_HEAD),('flutter',flutter,B.FLUTTER_HEAD)):
            if git(label+'-head',path,'rev-parse','HEAD').decode().strip()!=expected or git(label+'-clean',path,'status','--porcelain=v1','--untracked-files=all','--ignore-submodules=none'):
                raise Refusal('exact clean pinned source required: '+label)
        before=copy_source(args.source,app,probe)
        capsule_before=inventory(args.bridge,probe)
        vcpkg_before=inventory(args.vcpkg/'installed'/TRIPLET,probe)
        vcpkg_metadata_before=inventory(args.vcpkg/'installed/vcpkg',probe)
        codec_proof=codec_archives(args.vcpkg);B.write_json(output/'native-codecs.json',codec_proof)
        rust_paths={}
        for tool in ('rustc','cargo'):
            path=Path(call(tool+'-path',[tools['rustup.exe'],'which','--toolchain',TOOLCHAIN,tool],read=True).decode().strip());pe_x64(path);rust_paths[tool]=path
        if rust_paths['cargo'].parent!=rust_paths['rustc'].parent:raise Refusal('Rust/Cargo pinned toolchain paths differ')
        rust=call('rustc-version',[tools['rustup.exe'],'run',TOOLCHAIN,'rustc','-Vv'],read=True).decode()
        cargo=call('cargo-version',[tools['rustup.exe'],'run',TOOLCHAIN,'cargo','-V'],read=True).decode()
        if not rust.startswith('rustc 1.75.0 ') or '\nhost: '+TARGET+'\n' not in rust or not cargo.startswith('cargo 1.75.0 '):raise Refusal('native Rust/Cargo1.75.0 required')
        targets=call('installed-rust-targets',[tools['rustup.exe'],'target','list','--installed','--toolchain',TOOLCHAIN],read=True).decode().splitlines()
        if TARGET not in targets:raise Refusal('preinstall pinned native Windows Rust target')
        identity=json.loads(call('flutter-version',batch(command,flutter_bat,'--version','--machine'),read=True))
        if identity.get('frameworkVersion')!=B.FLUTTER_VERSION or identity.get('frameworkRevision')!=B.FLUTTER_HEAD:raise Refusal('official Flutter3.27.4 required')
        for tool in ('git.exe','bash.exe','cmake.exe','ninja.exe','magick.exe','pwsh.exe'):
            argsv=['-NoLogo','-NoProfile','-NonInteractive','-Command','$PSVersionTable.PSVersion.ToString()'] if tool=='pwsh.exe' else ['-version' if tool=='magick.exe' else '--version']
            actual=call('tool-'+tool, [tools[tool],*argsv],read=True)
            if tool=='pwsh.exe' and not re.match(rb'7\.',actual.strip()):raise Refusal('preinstalled PowerShell7 required')
        tool_inputs={str(path):B.digest(path) for path in (*rust_paths.values(),linker,linker.with_name('cl.exe'),*sdk_inputs,command,flutter_bat,dart,engine,Path(libclang['dll']))}
        owned_bin=output/'tools-bin';owned_bin.mkdir()
        (owned_bin/'python3').write_bytes(b'#!/usr/bin/env bash\nexec "$MIXEL_LOCAL_PYTHON" "$@"\n')
        home=output/'cargo-home';home.mkdir();pub=output/'pub-cache';pub.mkdir();temporary=output/'tmp';temporary.mkdir()
        # Cargo1.75's embedded libgit2 also reads the XDG config. Keep this
        # option private to the build; never edit account/system Git or HOME.
        git_xdg=home/'xdg';git_xdg.mkdir();git_directory=git_xdg/'git';git_directory.mkdir()
        git_config=git_directory/'config';git_config.write_bytes(b'[core]\n\tlongpaths = true\n')
        for directory in (owned_bin,home,pub,temporary,git_xdg,git_directory):C.windows_private_entry(directory,probe,directory=True)
        C.windows_private_entry(owned_bin/'python3',probe,directory=False)
        C.windows_private_entry(git_config,probe,directory=False)
        tool_inputs[str(git_config)]=B.digest(file(git_config))
        B.write_json(output/'toolchains.json',{'rustcVersion':rust.strip(),'cargoVersion':cargo.strip(),'sdkVersion':sdk_version,
                     'flutterIdentity':identity,'actualInputHashes':tool_inputs,'libclangVersion':libclang['version'],
                     'privateGitConfig':str(git_config),'privateGitConfigScope':'child environment only; no account/system changes'})
        env.update(PATH=os.pathsep.join((str(linker.parent),str(rust_paths['cargo'].parent),str(owned_bin),str(flutter/'bin'),env.get('PATH',env.get('Path','')))),
                   CARGO_HOME=str(home),PUB_CACHE=str(pub),RUSTUP_TOOLCHAIN=TOOLCHAIN,VCPKG_ROOT=str(args.vcpkg),VCPKG_DEFAULT_TRIPLET=TRIPLET,
                   VCPKG_DEFAULT_HOST_TRIPLET=TRIPLET,CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER=str(linker),
                   CARGO_BUILD_JOBS=str(jobs),CMAKE_BUILD_PARALLEL_LEVEL=str(jobs),VCPKG_MAX_CONCURRENCY=str(jobs),
                   COMSPEC=str(command),OS='Windows_NT',
                   TEMP=str(temporary),TMP=str(temporary),
                   LIBCLANG_PATH=libclang['directory'],
                   XDG_CONFIG_HOME=str(git_xdg),GIT_CONFIG_GLOBAL=str(git_config),
                   RDREPO=str(app).replace('\\','/'),BRANDING=str(repo/'branding').replace('\\','/'),MIXEL_LOCAL_PYTHON=sys.executable.replace('\\','/'))
        # Windows hardcodes target/release in build.py; native host target was proved above.
        for key in list(env):
            if key.casefold()=='path' and key!='PATH':del env[key]
        runner.environment=env
        call('apply-branding',[tools['bash.exe'],repo/'scripts/apply-branding.sh'])
        # This existing owner validates the complete known profile before writes.
        # Only cfg(macos) dependency/callback source and its exact lock edge change;
        # matching the bridge's source physically avoids a platform-guessing inverse.
        call('shared-bridge-rdev-profile',[sys.executable,repo/'scripts/patch-support-macos-rdev.py',app])
        bridge=admit_bridge(args.bridge,app,probe,repo);B.write_json(output/'bridge-admission.json',bridge)
        # The shared Mac bridge predates these private Windows compiler-only
        # corrections. Admit its complete original input closure first, then
        # apply one exact ten-file profile; generated API/wire types are unchanged.
        call('windows-compiler-integration',[sys.executable,repo/'scripts/patch-windows-compiler-integration.py','--root',app,'--apply'])
        lock=B.digest(file(app/'Cargo.lock'))
        def hold(phase):
            current=B.digest(file(app/'Cargo.lock'));result.update(cargoLockBeforeSha256=lock,cargoLockAfterSha256=current,cargoLockUnchanged=current==lock)
            result['cargoLockAfter'+phase+'Sha256']=current
            if current!=lock:raise Refusal('held application Cargo.lock changed: '+phase)
        call('fetch-locked',[tools['rustup.exe'],'run',TOOLCHAIN,'cargo','fetch','--locked','--target',TARGET],app);hold('Fetch')
        call('shipping-pub-get',batch(command,flutter_bat,'pub','get'),app/'flutter')
        if logical_file(app/'flutter/pubspec.yaml')['logicalSha256']!=SHIPPING_PUBSPEC:raise Refusal('shipping14 manifest changed')
        call('check-entire-app',[sys.executable,repo/'scripts/check-app-rust.py','--source',app,'--output',output/'rust-check','--target',TARGET,'--run','--local-run-root',root]);hold('Check')
        read_json(output/'rust-check/check.result.json',status='passed',compiled=True,sourceUnchanged=True)
        call('build-entire-app',[sys.executable,'build.py','--flutter','--skip-portable-pack'],app);hold('Build')
        call('analyze-entire-flutter',batch(command,flutter_bat,'analyze','--no-pub','--no-fatal-infos'),app/'flutter')
        call('compiled-support-guard',[sys.executable,repo/'scripts/test-compiled-support-guard.py']);hold('Guard')
        release=app/'flutter/build/windows/x64/runner/Release'
        call('compiled-branding',[sys.executable,repo/'scripts/verify-flutter-branding.py','--assets',release/'data/flutter_assets'])
        payload=output/'unsigned-payload'
        ps=[tools['pwsh.exe'],'-NoLogo','-NoProfile','-NonInteractive','-File']
        call('prepare-unsigned-payload',[*ps,repo/'scripts/prepare-windows-store-payload.ps1','-BuildRoot',app/'flutter/build/windows','-Destination',payload])
        payload_manifest=inventory(payload,probe)
        for item in payload_manifest:
            if item['kind']=='file' and Path(item['path']).suffix.casefold() in ('.exe','.dll'):pe_x64(payload/item['path'])
        B.write_json(output/'payload-manifest.json',{'entries':payload_manifest,'treeSha256':C.binding(payload_manifest),'unsigned':True})
        dist=output/'dist';dist.mkdir();outer=dist/'Mixel-Remote-UNSIGNED-local-QA.exe'
        call('package-unsigned-outer',[sys.executable,repo/'scripts/build-windows-portable.py','--repo',app,'--payload',payload,'--output',outer]);hold('Package')
        pe_x64(outer)
        call('verify-outer-manifest',[sys.executable,repo/'scripts/build-windows-portable.py','--verify-manifest',outer])
        if payload_manifest!=inventory(payload,probe):raise Refusal('payload changed during outer packaging')
        result.update(buildQualified=True,sourceHead=head,payload=str(payload),payloadTreeSha256=C.binding(payload_manifest),outer=str(outer),outerSha256=B.digest(file(outer)),commandsPassed=runner.count)
    except Exception as error:result['error']=str(error);raise
    finally:
        try:
            result.update(suppliedSourceUnchanged=before is not None and before==inventory(args.source,probe),
                suppliedVcpkgUnchanged=vcpkg_before is not None and vcpkg_before==inventory(args.vcpkg/'installed'/TRIPLET,probe)
                    and vcpkg_metadata_before==inventory(args.vcpkg/'installed/vcpkg',probe),
                bridgeInputUnchanged=capsule_before is not None and capsule_before==inventory(args.bridge,probe),
                suppliedLibclangUnchanged=libclang['inputs']==dependency_inventory(Path(libclang['root']),probe),
                pipelineInputsUnchanged=pipeline is not None and pipeline==pipeline_files(repo,names),
                toolchainInputsUnchanged=tool_inputs is not None and all(B.digest(file(Path(p),independent=False))==sha for p,sha in tool_inputs.items()))
            if lock is not None:
                current=B.digest(file(app/'Cargo.lock'));result.update(cargoLockBeforeSha256=lock,cargoLockAfterSha256=current,cargoLockUnchanged=current==lock,cargoLockAfterFinalSha256=current)
            if not all(result.get(k) is True for k in ('suppliedSourceUnchanged','suppliedVcpkgUnchanged','suppliedLibclangUnchanged','bridgeInputUnchanged','pipelineInputsUnchanged','toolchainInputsUnchanged','cargoLockUnchanged')):
                result['buildQualified']=False
                result.setdefault('error','held lock or supplied source/dependency/capsule/pipeline changed or was not admitted')
        except Exception as error:result.update(buildQualified=False,error=str(error))
        result['buildClosed']=True;B.write_json(output/'build.result.json',result);(output/'build-active.json').unlink()
    if not result['buildQualified']:raise Refusal(result.get('error','Windows build did not qualify'))
    print('PASS: unsigned Windows x64 payload/outer built and verified; signing, cold execution and customer acceptance remain separate')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','flutter','vcpkg','bridge','libclang','run-root','output','mac-build','export-bridge'):parser.add_argument('--'+name,type=Path)
    parser.add_argument('--jobs',type=B.job_count,default=1);parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    try:
        if args.export_bridge:
            if args.run or not args.mac_build:parser.error('--export-bridge requires --mac-build and cannot compile')
            export_bridge(args.mac_build,args.export_bridge)
        elif args.run:
            if not all(getattr(args,n) for n in ('source','flutter','vcpkg','bridge','libclang','run_root','output')):parser.error('--run requires source/flutter/vcpkg/bridge/libclang/run-root/output')
            build(args)
        else:print('PLAN ONLY: native Windows x64, preinstalled pinned tools/codecs, private NTFS ignored output, exact six-file Mac bridge capsule; unsigned build/package only; jobs='+str(args.jobs)+'. No commands/writes.')
    except (OSError,ValueError,RuntimeError) as error:print('FAIL: '+str(error),file=sys.stderr);return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
