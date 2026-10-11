#!/usr/bin/env python3
"""Pure generator/parser controls, not actual Windows/CDB qualification."""
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('bounded_cdb_diagnostic', ROOT/'windows-namespace-cdb.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
checks = 0

def require(condition, message):
    global checks
    if not condition: raise AssertionError(message)
    checks += 1

def rejects(call, message):
    global checks
    try: call()
    except ValueError: checks += 1
    else: raise AssertionError(message)

leaf = 'Mixel-Remote-Standard-Lease-'+'0123456789abcdef'*2
files, paths = module.commands(101,202,2,leaf,r'D:\a\_temp\mixel-ns-trace-control')
require(len(files)==20 and len(paths)==12,'Bounded helper/program shape changed')
for name, data in files.items():
    require(len(data.replace('\n',';\n').encode('ascii'))<=4096, 'Condensed command block exceeds4096 characters')
    require(not re.search(r'(?i)%m[asu]|\b(?:du|db|dw|dq)\s|\.call\b|\.fnret\b|\bgu\b|(?<!\$)\bbp[0-9 ]|\bbu\b',data),'Raw strings/memory/software breakpoint/altered-return command')
    require(not re.search(r'(?m)^\s*r\s+(?!\$t[0-9]+\s*=)',data),'Native register write introduced')
    require(not re.search(r'(?m)^\s*e[bwdq]\s',data),'Target-memory write introduced')
for index in range(1,4):
    name=f'winmatch-{index:02d}.txt'
    require(name in files and ('$$><'+r'D:\a\_temp\mixel-ns-trace-control'+'\\'+name) in files['winentry.txt'],'Win32 comparison helper not consumed')
    require('r $t15 = 1' in files[name] and '$vvalid(@$t9,' in files[name] and f'r $t5 = @$t5 | 0n{1 << (index-1)}' in files[name],'Win32 helper lost bounded exact match/readability bit')
    require(not re.search(r'(?i)\b(?:g|gc|gh|gn|qd|ba|bc|bd|be)\b|MIXEL_NAMESPACE_',files[name]),'Win32 helper changed execution/marker scope')
for kind in range(1,13):
    name=f'pathmatch-{kind:02d}.txt'
    require(name in files and ('$$><'+r'D:\a\_temp\mixel-ns-trace-control'+'\\'+name) in files['entry.txt'],'Native path helper not consumed')
    require(f'r $t10 = 0n{kind}' in files[name] and '.if (@$t8 == 0n' in files[name],'Native helper lost exact byte-length match')
    require(not re.search(r'(?i)\b(?:g|gc|gh|gn|qd|ba|bc|bd|be)\b|MIXEL_NAMESPACE_',files[name]),'Native helper changed execution/marker scope')
rejects(lambda:module.commands(101,202,2,leaf,'D:\\'+'a'*4096),'Oversized nested script folder accepted')
maximum_files,_=module.commands(0xffffffff,0xffffffff,0xffffffff,leaf,r'D:\a\_temp\mixel-ns-trace-control')
require(all(len(text.replace('\n',';\n').encode('ascii'))<=4096 for text in maximum_files.values()),'Largest DWORD identity/path exceeds4096')
require('.if (@$t15 != 0) {' in files['winentry.txt'] and files['winentry.txt'].endswith('}\ngc\n'),'Nonmatching Win32 call can fall through after resume')
require('.if (@$t16 == 1) {' in files['entry.txt'] and files['entry.txt'].endswith('}\ngc\n'),'Pre-window native call can fall through after resume')
for name in ['winentry.txt','entry.txt']:
    require(len(re.findall(r'(?m)^gc$',files[name]))==1 and not re.search(r'\{\s*gc\s*\}',files[name]),'Resume exists before later side effects')
require('ba0 e 1 ntdll!NtOpenDirectoryObject' in files['start.txt'],'Directory hardware binding missing')
require('ba1 e 1 ntdll!NtOpenEvent' in files['start.txt'],'Event hardware binding missing')
require('ba2 e 1 KERNELBASE!OpenEventW' in files['start.txt'],'Exact Win32 window filter missing')
require(files['start.txt'].endswith('.echo MIXEL_NAMESPACE_TRACE_READY\n'),'Initial debugger script must finish at the verified readiness barrier')
require(not re.search(r'(?m)^\s*(?:g|gc|gh|gn)\s*$',files['start.txt']),'Initial script resumed before the owned parent removed external suspension')
require('ba3 e 1 @$t13' in files['entry.txt'],'Fourth and final hardware slot missing')
require('~~[0xca]ba3' in files['entry.txt'],'Native return lacks actual OS TID restriction')
require('@rsp != @$t12+0n8' in files['return.txt'],'Native return stack scope missing')
require('@rsp != @$t14+0n8' in files['winreturn.txt'],'Win32 return stack scope missing')
require('bc *\nqd' in files['winreturn.txt'],'Normal exact window completion fails to detach')
require('@$t0 >= 0n32' in files['entry.txt'],'Record bound missing')
require('r $t4 = 0' in files['start.txt'] and 'r $t5 = 0' in files['start.txt'],'Shared entry sequence/readability reset missing')
for name in ['winentry.txt','entry.txt']:
    require('@$t4 >= 0n32' in files[name] and 'MIXEL_NAMESPACE_TRACE_FAIL_ENTRY_LIMIT' in files[name],'Shared32-entry bound missing')
    require(files[name].count('r $t4 = @$t4 + 1')==1,'Entry sequence increment is not exact once')
    require(files[name].index('TRACE_FAIL_SCOPE') < files[name].index('r $t4 = @$t4 + 1'),'Entry marker precedes exact retained PID/TID scope')
require(files['winentry.txt'].index('MIXEL_NAMESPACE_WIN32_ENTRY') < files['winentry.txt'].index('.if (@$t15 != 0) {'),'Win32 entry diagnostic is after leaf filter')
require(files['entry.txt'].index('.if (@$t16 == 0) {') < files['entry.txt'].index('MIXEL_NAMESPACE_PREWINDOW_ENTRY') < files['entry.txt'].index('.if (@$t16 == 1) {'),'Native pre-window marker captures inside actual window')
require('@ecx != 0x00100000' in files['winentry.txt'] and '@edx != 0' in files['winentry.txt'],'Original interface not observed exactly')
require(files['return.txt'].count('\\n')==1, 'Native scalar output newline escaped incorrectly')
for value in [0,-1,0x100000000,None,True,'101']:
    for field in range(3):
        ids=[101,202,2];ids[field]=value
        rejects(lambda ids=ids:module.commands(*ids,leaf,r'D:\a\_temp\mixel-ns-trace-control'),'Malformed identity accepted')
for event in ['',leaf+'x',leaf+'\\secret','Global\\'+leaf,'Local\\'+leaf,leaf.upper(),'Other-'+('0'*32),leaf.replace('0','g',1)]:
    rejects(lambda event=event:module.commands(101,202,2,event,r'D:\a\_temp\mixel-ns-trace-control'),'Arbitrary event name accepted')
for folder in ['',r'\\host\share',r'C:\..\owned',r'C:\owned\..',r'C:\owned path',r'C:\owned%path',r'C:\owned;echo',r'C:\owned"path',r'C:\owned&path','C:\\owned\npath','C:\\owned\\']:
    rejects(lambda folder=folder:module.commands(101,202,2,leaf,folder),'Ambiguous command folder accepted')

def win_entry(sequence=1,mask=7,matched=1,access=1,inherit=1):
    return f'MIXEL_NAMESPACE_WIN32_ENTRY sequence={sequence} leafValidMask={mask} leafMatched={matched} accessMatched={access} inheritFalse={inherit}'
def pre_entry(sequence=1,api=1):
    return f'MIXEL_NAMESPACE_PREWINDOW_ENTRY sequence={sequence} api={api}'
ready_prefix='\n'.join([module.BINDINGS,module.READY])+'\n'
prefix = ready_prefix+win_entry()+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'
def record(sequence=1,api=1,access='0000000f',root=0,kind=4,status='c0000022',handle=0,qualified=1):
    return f'MIXEL_NAMESPACE_NATIVE seq={sequence} api={api} access=0x{access} rootNonNull={root} pathKind={kind} ntstatus=0x{status} handleNonNull={handle} scopeQualified={qualified}'
suffix='MIXEL_NAMESPACE_WIN32 handleNonNull=0 scopeQualified=1\n'+module.END+'\n'
valid=prefix+record()+'\n'+suffix
result=module.parse_trace(valid)
require(result['records']==[dict(sequence=1,api=1,desiredAccess=15,rootNonNull=False,pathKind=4,nativeStatus=0xc0000022,handleNonNull=False)],'Actual denial vector parsed incorrectly')
require(result['evidenceScope'].startswith('source-only debugger') and not result['win32HandleNonNull'],'Observation scope or actual handle boolean changed')
for kind in range(13):
    for api in [1,2]:
        for root in [0,1]:
            for handle in [0,1]:
                parsed=module.parse_trace(prefix+record(api=api,root=root,kind=kind,handle=handle)+'\n'+suffix)
                require(parsed['records'][0]['pathKind']==kind,'Allowed scalar domain changed')
for size in [0,1,2,31,32]:
    raw=prefix+''.join(record(sequence=i)+'\n' for i in range(1,size+1))+suffix
    require(module.parse_trace(raw)['nativeCallCount']==size,'Bounded record count rejected')
rejects(lambda:module.parse_trace(prefix+''.join(record(sequence=i)+'\n' for i in range(1,34))+suffix),'33records accepted')
for bad in [record(sequence=0),record(sequence=-1),record(sequence=2),record(api=0),record(api=3),record(api=11),record(access='f'),record(access='100000000'),record(access='0000000g'),record(root=2),record(root=-1),record(kind=13),record(kind=-1),record(status='c000002222'),record(status='bad'),record(handle=2),record(qualified=0),record()+' extra=secret',record().replace('pathKind=4 ','')]:
    rejects(lambda bad=bad:module.parse_trace(prefix+bad+'\n'+suffix),'Malformed native record accepted')
for marker in [module.BINDINGS,module.READY,'MIXEL_NAMESPACE_TRACE_WINDOW',module.END]:
    rejects(lambda marker=marker:module.parse_trace(valid.replace(marker+'\n','',1)),'Missing ordered marker accepted')
    rejects(lambda marker=marker:module.parse_trace(valid.replace(marker+'\n',marker+'\n'+marker+'\n',1)),'Duplicate marker accepted')
for marker in [module.BINDINGS.replace('directory=1','directory=0'),module.BINDINGS.replace('event=1','event=0'),module.BINDINGS.replace('win32=1','win32=0')]:
    rejects(lambda marker=marker:module.parse_trace(valid.replace(module.BINDINGS,marker)),'Unbound hardware probe qualified')
for injected in ['MIXEL_NAMESPACE_TRACE_FAIL_SCOPE','MIXEL_NAMESPACE_TRACE_FAIL_RETURN_SCOPE','MIXEL_NAMESPACE_TRACE_FAIL_RECORD_LIMIT','MIXEL_NAMESPACE_TRACE_READY_EXTRA','MIXEL_NAMESPACE_NATIVE secret=customer','Syntax error','Unable to insert breakpoint','Couldn\'t resolve ntdll!NtOpenEvent']:
    rejects(lambda injected=injected:module.parse_trace(valid+injected+'\n'),'Failure/unbounded malformed marker ignored')
rejects(lambda:module.parse_trace('x'*1048577),'Oversized raw trace accepted')
rejects(lambda:module.parse_trace(prefix+record()+'\n'+record(sequence=1)+'\n'+suffix),'Duplicate native sequence accepted')
require(module.parse_trace('.printf "MIXEL_NAMESPACE_NATIVE seq=99"\n'+valid)['nativeCallCount']==1,'Script command echo counted as actual output')
require(module.parse_trace('0:000> .echo MIXEL_NAMESPACE_TRACE_END\n'+valid)['nativeCallCount']==1,'Prompt/command echo counted as marker')
# Native status is observed as a DWORD without converting it by calling the target.
for status in ['00000000','00000001','7fffffff','80000000','c0000005','ffffffff']:
    require(module.parse_trace(prefix+record(status=status)+'\n'+suffix)['records'][0]['nativeStatus']==int(status,16),'Raw32-bit NTSTATUS changed')

# Read exact generated comparisons; simulate only the bounded target-read boundary.
# This checks actual UTF-16 literals, terminating-null offset, readability bits,
# and marker ordering. It does not execute CDB or assert native breakpoint hits.
spellings=['Global\\'+leaf,'Local\\'+leaf,leaf]
comparisons=[]
for index,spelling in enumerate(spellings,1):
    text=files[f'winmatch-{index:02d}.txt']
    span=int(re.search(r'\$vvalid\(@\$t9,0n([0-9]+)\)',text).group(1))
    bit=int(re.search(r'r \$t5 = @\$t5 \| 0n([0-9]+);',text).group(1))
    units=[(int(offset),int(value,16)) for offset,value in re.findall(r'\(wo\(@\$t9\+0n([0-9]+)\) == 0x([0-9a-f]{4})\)',text)]
    nul=int(re.search(r'\(wo\(@\$t9\+0n([0-9]+)\) == 0\)',text).group(1))
    require(span==len(spelling.encode('utf-16-le'))+2 and nul==span-2 and bit==1 << (index-1),'Generated comparison span/null/bit differs from exact spelling')
    require(units==[(2*i,ord(char)) for i,char in enumerate(spelling)],'Generated UTF-16 filter lost exact source GUID correlation')
    comparisons.append((span,bit,units,nul))
def observe_name(name,valid_bytes):
    data=(name+'\0').encode('utf-16-le');mask=0;matched=False
    for span,bit,units,nul in comparisons:
        if span>valid_bytes: continue
        mask |= bit
        # Readable backing beyond the string is simulated zero-filled, as a
        # valid allocation does not imply exact string length.
        buffer=data+b'\0'*max(0,span-len(data))
        matched |= all(int.from_bytes(buffer[offset:offset+2],'little')==value for offset,value in units) and buffer[nul:nul+2]==b'\0\0'
    return mask,bool(matched)
for spelling in spellings:
    require(observe_name(spelling,512)==(7,True),'An exact readable supported spelling misses the generated filter')
    require(observe_name(spelling,0)==(0,False),'Invalid target buffer is read/matched')
    for span in sorted(set(item[0] for item in comparisons)):
        mask,matched=observe_name(spelling,span-1)
        require(mask==sum(bit for size,bit,_,_ in comparisons if size<=span-1),'Unreadable comparison span contributes a mask bit')
        require(matched==(len((spelling+'\0').encode('utf-16-le'))<=span-1),'Bounded shorter memory matched an unreadable exact spelling')
for name in [leaf.upper(),spellings[0]+'x',spellings[0].replace('Global','global'),leaf[:-1],leaf.replace('0','1',1),'Other-'+leaf,spellings[0]+'\0x']:
    mask,matched=observe_name(name,512)
    require(mask==7 and matched==(name==spellings[0]+'\0x'),'Exact filter accepts a different GUID/prefix/case/termination')

for size in [1,2,31,32]:
    entries=[pre_entry(i,1+(i%2)) for i in range(1,size)] + [win_entry(size)]
    raw=ready_prefix+'\n'.join(entries)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+record()+'\n'+suffix
    require(module.parse_trace(raw)==result,'Bounded source-only diagnostics altered actual capture result schema/values')
for mask in range(8):
    for access in [0,1]:
        for inherit in [0,1]:
            raw=ready_prefix+win_entry(mask=mask,matched=0,access=access,inherit=inherit)+'\n'+win_entry(2)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+suffix
            require(module.parse_trace(raw)['completedQualifiedSourceWindow'],'Unmatched bounded entry wrongly changed later exact window')
for mask in range(1,8):
    require(module.parse_trace(valid.replace(win_entry(),win_entry(mask=mask)))==result,'Readable matched mask changed real native record')
for bad in [win_entry(sequence=0),win_entry(sequence=-1),win_entry(sequence=2),win_entry(sequence=33),win_entry(sequence='01'),win_entry(mask=8),win_entry(mask=-1),win_entry(mask=0),win_entry(matched=2),win_entry(access=2),win_entry(inherit=2),win_entry(access=0),win_entry(inherit=0),win_entry()+' extra=secret',pre_entry(api=0),pre_entry(api=3),pre_entry(sequence=0),pre_entry(sequence=33),'MIXEL_NAMESPACE_WIN32_ENTRY sequence=1 leafMatched=1']:
    rejects(lambda bad=bad:module.parse_trace(valid.replace(win_entry(),bad)),'Malformed/scalar/out-of-order diagnostic accepted')
rejects(lambda:module.parse_trace(valid.replace(win_entry()+'\n','')),'Window qualified without its exact entry marker')
rejects(lambda:module.parse_trace(ready_prefix+win_entry()+'\n'+pre_entry(2)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+suffix),'Matched entry was not immediately followed by its window')
rejects(lambda:module.parse_trace(ready_prefix+'\n'.join(pre_entry(i) for i in range(1,33))+'\n'+win_entry(33)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+suffix),'Shared diagnostic bound admits33total entries')
for entry in [win_entry(matched=0),pre_entry()]:
    rejects(lambda entry=entry:module.parse_trace(entry+'\n'+valid),'Diagnostic accepted before bindings/readiness')
    rejects(lambda entry=entry:module.parse_trace(valid+entry+'\n'),'Diagnostic accepted after END')
    rejects(lambda entry=entry:module.parse_trace(valid.replace(record(),entry+'\n'+record())),'Diagnostic accepted inside actual native window')
    rejects(lambda entry=entry:module.parse_trace(ready_prefix+entry+'\n'),'Partial/no-window diagnostics qualified source completion')
rejects(lambda:module.parse_trace(valid+'MIXEL_NAMESPACE_TRACE_FAIL_ENTRY_LIMIT\n'),'Overflow marker ignored')

print(f'PASS: {checks} pure generator/parser controls; 4 hardware slots, exact Win32 GUID window, same-thread/RSP-qualified returns, shared32-entry diagnostics and32-record bound, native values unchanged')
print('Scope: no actual Windows/CDB execution or signed/runtime/customer-Accept qualification')
