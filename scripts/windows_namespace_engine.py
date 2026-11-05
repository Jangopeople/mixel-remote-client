#!/usr/bin/env python3
"""Build a disposable getter-only extension for the retained source-only CDB."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import struct
import subprocess
import sys
from rust_toolchain import find_msvc_linker, read_windows_version_info, windows_environment_value

EXPORTS=('DebugExtensionInitialize','inspect')
MAX_FILE=16777216
PHASES=('platform','source','toolchain','sdk','compile','link','qualification')

class BuildFailure(RuntimeError):
    def __init__(self,phase,completed=None):
        if phase not in PHASES:raise ValueError('Unknown fixed native build phase')
        self.phase=phase
        self.facts={'buildFailed':True,'phase':phase,'nativeExitAvailable':False,'nativeExitCode':0,'codeFactsAvailable':False,'errorCodes':[],'warningCodes':[]}
        if completed is not None and type(completed.returncode) is int and -2147483648<=completed.returncode<=4294967295:
            self.facts['nativeExitAvailable']=True;self.facts['nativeExitCode']=completed.returncode
            raw=completed.stdout+completed.stderr
            if type(raw) is bytes and len(raw)<=1048576:
                self.facts['codeFactsAvailable']=True
                for kind,key in (('error','errorCodes'),('warning','warningCodes')):
                    codes=re.findall(rb'\b'+kind.encode()+rb' (?:C|LNK)([0-9]{4})\b',raw)
                    self.facts[key]=sorted({int(code) for code in codes})[:32]
        super().__init__('Private native extension build failed')

def qualify_dll(data):
    if type(data) is not bytes or not 512 <= len(data) <= MAX_FILE or data[:2]!=b'MZ':
        raise ValueError('Private engine DLL has invalid bounded PE bytes')
    def u16(at):
        if not 0 <= at <= len(data)-2:raise ValueError('Private engine DLL header exceeds file')
        return struct.unpack_from('<H',data,at)[0]
    def u32(at):
        if not 0 <= at <= len(data)-4:raise ValueError('Private engine DLL header exceeds file')
        return struct.unpack_from('<I',data,at)[0]
    pe=u32(60)
    if not 64 <= pe <= 65536 or data[pe:pe+4]!=b'PE\0\0' or u16(pe+4)!=0x8664:raise ValueError('Private engine DLL is not native AMD64')
    count,optional_size,flags=u16(pe+6),u16(pe+20),u16(pe+22);optional=pe+24
    if not 1 <= count <= 96 or not 240 <= optional_size <= 4096 or flags & 0x2102 != 0x2002 or u16(optional)!=0x20b:raise ValueError('Private engine DLL has invalid AMD64/DLL headers')
    image_size,headers,entry=u32(optional+56),u32(optional+60),u32(optional+16)
    if not 4096 <= image_size <= 268435456 or not optional+optional_size+40*count <= headers <= min(len(data),65536) or u32(optional+108)<1:raise ValueError('Private engine DLL image/header domain failed')
    export_rva,export_size=u32(optional+112),u32(optional+116)
    if not 40 <= export_size <= 65536 or export_rva+export_size>image_size:raise ValueError('Private engine DLL export domain failed')
    sections=[];raw_ranges=[]
    for index in range(count):
        at=optional+optional_size+40*index
        virtual,va,raw_size,raw,section_flags=(u32(at+i) for i in (8,12,16,20,36))
        span=max(virtual,raw_size)
        if not span or va<headers or va+span>image_size or raw_size and (raw<headers or raw+raw_size>len(data)):raise ValueError('Private engine DLL section domain failed')
        if any(va < right and left < va+span for left,right,_,_,_ in sections):raise ValueError('Private engine DLL virtual sections overlap')
        if raw_size and any(raw < right and left < raw+raw_size for left,right in raw_ranges):raise ValueError('Private engine DLL raw sections overlap')
        sections.append((va,va+span,raw,raw_size,section_flags))
        if raw_size:raw_ranges.append((raw,raw+raw_size))
    def mapped(rva,size):
        if type(rva) is not int or size<=0 or rva+size>image_size:raise ValueError('Private engine DLL RVA exceeds image')
        regions=[raw+rva-left for left,right,raw,raw_size,_ in sections if left<=rva and rva+size<=left+raw_size and rva+size<=right]
        if len(regions)!=1:raise ValueError('Private engine DLL RVA lacks unique raw backing')
        return regions[0]
    def executable(rva):
        mapped(rva,1)
        return sum(left<=rva<min(right,left+raw_size) and section_flags & 0x60000020==0x60000020 and not section_flags & 0x80000000 for left,right,_,raw_size,section_flags in sections)==1
    if not executable(entry):raise ValueError('Private engine DLL entry is not executable')
    at=mapped(export_rva,40)
    if u32(at+20)!=2 or u32(at+24)!=2:raise ValueError('Private engine DLL exports are not the exact two callbacks')
    addresses,names,ordinals=(u32(at+i) for i in (28,32,36))
    a,n,o=mapped(addresses,8),mapped(names,8),mapped(ordinals,4)
    ranges=[(export_rva,export_rva+40),(addresses,addresses+8),(names,names+8),(ordinals,ordinals+4)]
    def cstring(rva,cap):
        value=bytearray()
        for offset in range(cap):
            c=data[mapped(rva+offset,1)]
            if not c:return value.decode('ascii'),offset+1
            if not 32<=c<=126:raise ValueError('Private engine DLL export name is not ASCII')
            value.append(c)
        raise ValueError('Private engine DLL export string exceeds bound')
    dll_name,dll_size=cstring(u32(at+12),128)
    if dll_name!='mixelengine.dll':raise ValueError('Private engine DLL export name differs')
    ranges.append((u32(at+12),u32(at+12)+dll_size))
    found={};seen_ordinals=set()
    for index in range(2):
        name_rva=u32(n+4*index);name,size=cstring(name_rva,128);ordinal=u16(o+2*index)
        if name not in EXPORTS or name in found or ordinal not in (0,1) or ordinal in seen_ordinals:raise ValueError('Private engine DLL has duplicate/foreign callbacks')
        seen_ordinals.add(ordinal);rva=u32(a+4*ordinal)
        if export_rva<=rva<export_rva+export_size or not executable(rva):raise ValueError('Private engine DLL export is forwarded/non-executable')
        ranges.append((name_rva,name_rva+size));found[name]=rva
    if len(set(found.values()))!=2:raise ValueError('Private engine DLL callbacks alias')
    for index,(left,right) in enumerate(ranges):
        if any(left<other_right and other_left<right for other_left,other_right in ranges[index+1:]):raise ValueError('Private engine DLL export structures overlap')
        if any(left<=rva<right for rva in found.values()):raise ValueError('Private engine DLL export aliases metadata')
    return {'dllSHA256':hashlib.sha256(data).hexdigest(),'imageSize':image_size,'exportRvas':found}

def qualify_file(path,expected_sha=None):
    if not isinstance(path,Path) or path.name!='mixelengine.dll' or path.is_symlink() or not path.is_file() or path.resolve()!=path.absolute() or not 512<=path.stat().st_size<=MAX_FILE:raise ValueError('Private engine DLL is absent, indirect or unbounded')
    with path.open('rb') as stream:data=stream.read(MAX_FILE+1)
    result=qualify_dll(data)
    if expected_sha is not None and (type(expected_sha) is not str or len(expected_sha)!=64 or result['dllSHA256']!=expected_sha):raise ValueError('Private engine DLL identity differs')
    return result

def build(output):
    return _build(output,lambda value:None)

def _build(output,phase_callback):
    phase='platform'
    def stage(value):
        nonlocal phase
        phase=value;phase_callback(value)
    try:
        return _build_staged(output,stage)
    except BuildFailure:raise
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as error:
        raise BuildFailure(phase) from error

def _build_staged(output,stage):
    stage('platform')
    if os.name!='nt' or not isinstance(output,Path) or output.is_symlink() or not output.is_dir() or output.resolve()!=output.absolute():raise ValueError('Private engine build requires an owned native Windows directory')
    stage('source')
    source=Path(__file__).with_name('windows-namespace-engine-readonly.cpp')
    if source.is_symlink() or not source.is_file() or source.resolve()!=source.absolute() or not 1<=source.stat().st_size<=65536:raise ValueError('Private engine source is invalid')
    stage('toolchain')
    linker=find_msvc_linker(os.environ);compiler=linker.with_name('cl.exe')
    identity=read_windows_version_info(compiler)
    if identity.get('CompanyName')!='Microsoft Corporation' or identity.get('OriginalFilename','').casefold()!='cl.exe':raise ValueError('Private engine compiler is not native Microsoft CL')
    stage('sdk')
    sdk=windows_environment_value(os.environ,'WindowsSdkDir');version=windows_environment_value(os.environ,'WindowsSDKVersion').rstrip('\\/')
    program_files=windows_environment_value(os.environ,'ProgramFiles(x86)')
    candidates=[]
    if sdk and version and Path(sdk).is_absolute() and not any(c in sdk+version for c in '\r\n\0') and '/' not in version and '\\' not in version:
        candidates.append(Path(sdk)/'Include'/version/'um')
    if program_files and Path(program_files).is_absolute():candidates.append(Path(program_files)/'Windows Kits/10/Debuggers/x64/sdk/inc')
    headers=[path for path in candidates if not path.is_symlink() and path.resolve()==path.absolute() and (path/'dbgeng.h').is_file() and not (path/'dbgeng.h').is_symlink() and (path/'dbgeng.h').resolve()==(path/'dbgeng.h').absolute() and 1<=(path/'dbgeng.h').stat().st_size<=4194304]
    if not headers:raise ValueError('Native installed debugger SDK header is absent')
    dll=output/'mixelengine.dll'
    if any((output/name).exists() or (output/name).is_symlink() for name in ('mixelengine.dll','engine.obj','mixelengine.lib','mixelengine.exp')):raise ValueError('Private engine build may not replace existing owned files')
    stage('compile')
    command=[str(compiler),'/nologo','/c','/MT','/std:c++17','/EHsc','/W4','/WX','/I'+str(headers[0]),str(source),'/Fo'+str(output/'engine.obj')]
    completed=subprocess.run(command,cwd=output,capture_output=True,timeout=60)
    if completed.returncode:raise BuildFailure('compile',completed)
    obj=output/'engine.obj'
    if obj.is_symlink() or not obj.is_file() or obj.resolve()!=obj.absolute() or not 1<=obj.stat().st_size<=MAX_FILE:raise ValueError('Private compiler object is absent, indirect or unbounded')
    stage('link')
    command=[str(linker),'/NOLOGO','/DLL','/WX','/MACHINE:X64','/INCREMENTAL:NO','/DYNAMICBASE','/NXCOMPAT','/OUT:'+str(dll),'/IMPLIB:'+str(output/'mixelengine.lib'),str(obj)]
    completed=subprocess.run(command,cwd=output,capture_output=True,timeout=60)
    if completed.returncode:raise BuildFailure('link',completed)
    stage('qualification')
    result=qualify_file(dll);result['sourceSHA256']=hashlib.sha256(source.read_bytes()).hexdigest()
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(build(args.output),separators=(',',':')))
if __name__=='__main__':
    try:main()
    except BuildFailure as error:
        print(json.dumps(error.facts,separators=(',',':')))
        raise SystemExit(1)
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError):
        print('Private getter-only extension preparation failed; native diagnostics withheld',file=sys.stderr);raise SystemExit(1)
