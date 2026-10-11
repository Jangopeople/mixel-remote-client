#!/usr/bin/env python3
"""Pure generator/parser controls, not actual Windows/CDB qualification."""
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import tempfile

ROOT = Path(__file__).parent
sys.path.insert(0,str(ROOT))
from windows_namespace_probe import PROBE, MARKER, CAPTURE, diagnostic_source, qualify_pe, qualify_file
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
def synthetic_pe():
    data=bytearray(3072);data[:2]=b'MZ'
    def w(at,value):struct.pack_into('<H',data,at,value)
    def d(at,value):struct.pack_into('<I',data,at,value)
    d(0x3c,128);data[128:132]=b'PE\0\0';w(132,0x8664);w(134,2);w(148,240);w(150,0x22)
    opt=152;w(opt,0x20b);d(opt+16,0x1000);d(opt+56,0x3000);d(opt+60,1024);w(opt+68,3);d(opt+108,16);d(opt+112,0x2000);d(opt+116,0x180)
    for index,(va,raw,flags) in enumerate([(0x1000,1024,0x60000020),(0x2000,2048,0x40000040)]):
        at=opt+240+40*index
        for offset,value in [(8,1024),(12,va),(16,1024),(20,raw),(36,flags)]:d(at+offset,value)
    for offset,value in [(12,0x20b0),(16,1),(20,1),(24,1),(28,0x2040),(32,0x2044),(36,0x2048)]:d(2048+offset,value)
    d(2112,0x1100);d(2116,0x2060);w(2120,0)
    name=PROBE.encode()+b'\0';data[2144:2144+len(name)]=name
    data[2224:2243]=b'lease-standard.exe\0'
    return bytes(data)
pe_bytes=synthetic_pe();pe=qualify_pe(pe_bytes)
require(pe['entryRva']==0x1000 and pe['probeRva']==0x1100 and pe['imageSize']==0x3000,'Exact raw-backed private PE geometry changed')
def patched_pe(offset,value,width=4):
    data=bytearray(pe_bytes);struct.pack_into('<H' if width==2 else '<I',data,offset,value);return bytes(data)
for bad in [None,'MZ',bytearray(pe_bytes),b'',b'MZ'+b'\0'*126,pe_bytes[:1024],pe_bytes+b'\0'*16777216]:
    rejects(lambda bad=bad:qualify_pe(bad),'Absent/oversized/non-byte PE accepted')
for offset,value,width in [(0x3c,0xffffffff,4),(128,0,4),(132,0x14c,2),(134,0,2),(134,97,2),(148,119,2),(148,4097,2),(150,0x2022,2),(150,0x120,2),(152,0x10b,2),(220,2,2),(260,0,4),(260,17,4),(168,0,4),(168,0x3000,4),(208,1024,4),(212,471,4),(212,65537,4),(400,0x2c00,4),(404,0xffffffff,4),(408,0xffffffff,4),(444,0x1000,4),(452,1024,4),(428,0x40000020,4),(428,0xe0000020,4),(268,39,4),(268,65537,4),(2064,0,4),(2068,0,4),(2068,129,4),(2068,2,4),(2072,2,4),(2076,0x2000,4),(2080,0x2040,4),(2084,0x2044,4),(2116,0x2040,4),(2116,0x1100,4),(2120,1,2),(2112,0x2000,4),(2112,0x1000,4),(2112,0,4),(2112,0x3000,4),(2112,0x2400,4),(2060,0,4),(2060,0x2060,4)]:
    rejects(lambda offset=offset,value=value,width=width:qualify_pe(patched_pe(offset,value,width)),'Malformed/overlapping/forwarded/non-executable PE accepted')
bad=bytearray(pe_bytes);bad[2144]=ord('X');rejects(lambda:qualify_pe(bytes(bad)),'Different export name accepted')
bad=bytearray(pe_bytes);bad[2144+len(PROBE)]=ord('X');rejects(lambda:qualify_pe(bytes(bad)),'Unterminated exact export name accepted')
require(qualify_pe(pe_bytes)==pe,'PE qualification changed its input or result')
with tempfile.TemporaryDirectory(prefix='mixel-private-pe-control-') as temporary:
    root=Path(temporary);binary=root/'lease-standard.exe';binary.write_bytes(pe_bytes)
    require(qualify_file(binary,pe['exeSHA256'])==pe,'Actual bounded executable file/hash qualification failed')
    require(qualify_file(binary,pe['exeSHA256'].upper())==pe,'Canonical uppercase hash was rejected')
    for sha in ['', '0'*64, 'g'*64,None,1]:
        if sha is None:continue
        rejects(lambda sha=sha:qualify_file(binary,sha),'Wrong/malformed executable hash accepted')
    alias=root/'alias.exe';alias.symlink_to(binary)
    for path in [alias,root,root/'missing.exe',str(binary),root/'other.bin']:
        rejects(lambda path=path:qualify_file(path),'Indirect/non-file/wrong-type private executable accepted')
    binary.write_bytes(b'MZ')
    rejects(lambda:qualify_file(binary),'Small invalid binary accepted')
    binary.write_bytes(b'\0'*16777217)
    rejects(lambda:qualify_file(binary),'Oversized executable read accepted')
minimal=MARKER+'\n'+CAPTURE+'\n'
for gated in [False,True]:
    transformed=diagnostic_source(minimal,r'D:\owned\gate','0'*64,gated)
    require(transformed.count(f'let trace_probe_tid = {PROBE}();')==1 and transformed.count(f'pub extern "C" fn {PROBE}()')==1,'Source probe call/export is not unique')
    require(transformed.index('let trace_probe_tid')<transformed.index(MARKER)<transformed.index('fixture-hwcontrol.new')<transformed.index(CAPTURE),'Probe call/capture/supplemental order invalid')
    require(transformed.count(MARKER)==1 and transformed.count(CAPTURE)==1 and 'GetCurrentThreadId() -> u32' in transformed,'Original capture or native thread getter changed')
    require(('let trace_gate' in transformed)==gated,'No-pr/gated source distinction changed')
    if gated:require(transformed.index('let trace_gate')<transformed.index('let trace_probe_tid'),'Probe executed before nonce barrier')
for source in ['',MARKER, minimal+MARKER, minimal+CAPTURE,minimal+PROBE]:
    rejects(lambda source=source:diagnostic_source(source,'D:\\gate','0'*64,True),'Ambiguous/missing original source boundary accepted')
for nonce in ['', '0'*63,'0'*65,'G'+'0'*63]:
    rejects(lambda nonce=nonce:diagnostic_source(minimal,'D:\\gate',nonce,True),'Invalid private nonce accepted')
for gate in ['', 'D:\\gate\n', 'D:\\gate\0']:
    rejects(lambda gate=gate:diagnostic_source(minimal,gate,'0'*64,True),'Invalid private gate path accepted')
rejects(lambda:diagnostic_source(minimal,'D:\\gate','0'*64,1),'Nonboolean timing mode accepted')
def commands(*args):return module.commands(*args,pe)
files, paths = commands(101,202,2,leaf,r'D:\a\_temp\mixel-ns-trace-control')
require(len(files)==22 and len(paths)==12,'Bounded helper/program shape changed')
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
rejects(lambda:commands(101,202,2,leaf,'D:\\'+'a'*4096),'Oversized nested script folder accepted')
maximum_files,_=commands(0xffffffff,0xffffffff,0xffffffff,leaf,r'D:\a\_temp\mixel-ns-trace-control')
require(all(len(text.replace('\n',';\n').encode('ascii'))<=4096 for text in maximum_files.values()),'Largest DWORD identity/path exceeds4096')
require('.if (@$t15 != 0) {' in files['winentry.txt'] and files['winentry.txt'].endswith('}\ngc\n'),'Nonmatching Win32 call can fall through after resume')
require('.if (@$t16 == 1) {' in files['entry.txt'] and files['entry.txt'].endswith('}\ngc\n'),'Pre-window native call can fall through after resume')
for name in ['winentry.txt','entry.txt']:
    require(len(re.findall(r'(?m)^gc$',files[name]))==1 and not re.search(r'\{\s*gc\s*\}',files[name]),'Resume exists before later side effects')
require('ba0 e 1 ntdll!NtOpenDirectoryObject' in files['start.txt'],'Directory hardware binding missing')
require('ba1 e 1 ntdll!NtOpenEvent' in files['start.txt'],'Event hardware binding missing')
require('ba2 e 1 KERNELBASE!OpenEventW' in files['start.txt'],'Exact Win32 window filter missing')
snapshot=files['hardware-state.txt']
require(snapshot.startswith('.echo MIXEL_NAMESPACE_HW_LIST_BEGIN\nbl /L 0 1 2 3\n.echo MIXEL_NAMESPACE_HW_LIST_END\n'),'Bounded private breakpoint block/delimiters changed')
require(snapshot.count('MIXEL_NAMESPACE_HW_CONTEXT')==1 and '@$tpid == 0n101' in snapshot and '@$tid == 0n202' in snapshot,'Read-only context lacks unique retained-scope observation')
require(files['start.txt'].index('CONTROL_BINDING') < files['start.txt'].index('hardware-state.txt') < files['start.txt'].index('MIXEL_NAMESPACE_BINDINGS'),'Snapshot moved outside configured pre-READY stage')
require(not re.search(r'(?im)^r\s|\b(?:g|gc|gh|gn|ba|bc|bd|be)\b|\.apply_dbp|\b[ed][bwdq]\s',snapshot),'Read-only snapshot changes target/debugger execution state')
for logical,address in enumerate(('ntdll!NtOpenDirectoryObject','ntdll!NtOpenEvent','KERNELBASE!OpenEventW','@$t19')):
    expression='('+ ' or '.join(f'(@dr{slot} == {address})' for slot in range(4))+f')*0n{1 << logical}'
    require(expression in snapshot,'Address-presence assumes fixed logical/physical slot mapping')
for slot in range(4):
    require(f'((@dr7 & 0x{1 << (2*slot):x}) != 0)*0n{1 << slot}' in snapshot,'Local DR7 enable bit geometry changed')
    require(f'((@dr7 & 0x{2 << (2*slot):x}) != 0)*0n{1 << slot}' in snapshot,'Global DR7 enable bit geometry changed')
    require(f'((@dr7 & 0x{15 << (16+4*slot):x}) == 0)*0n{1 << slot}' in snapshot,'DR7 execute/one-byte nibble geometry changed')
observed_expressions=re.fullmatch(r'.*\n\.printf "[^"\n]+",\(@\$tpid == 0n101\),\(@\$tid == 0n202\),\((.*?)\),\((.*?)\),\((.*?)\),\((.*?)\)\n',snapshot,re.S).groups()
def snapshot_masks(registers,dr7):
    names={'ntdll!NtOpenDirectoryObject':'watch0','ntdll!NtOpenEvent':'watch1','KERNELBASE!OpenEventW':'watch2','@$t19':'watch3',**{f'@dr{index}':f'dr{index}' for index in range(4)},'@dr7':'dr7'}
    values={**{f'watch{index}':0x1000+index*0x100 for index in range(4)},**{f'dr{index}':value for index,value in enumerate(registers)},'dr7':dr7}
    output=[]
    for expression in observed_expressions:
        for original,name in names.items():expression=expression.replace(original,name)
        expression=re.sub(r'0n([0-9]+)',r'\1',expression)
        require(re.fullmatch(r'[a-z0-9_ ()+*!=&|]+',expression) is not None,'Unbounded state expression added')
        output.append(eval(expression,{'__builtins__':{}},values))
    return output
for mask in range(16):
    registers=[0x1000+index*0x100 if mask & (1<<index) else 0 for index in range(4)]
    expected=[mask,0,0,15]
    require(snapshot_masks(registers,0)==expected,'Actual read-only address expression lost bounded logical mask')
    require(snapshot_masks(registers[::-1],0)==expected,'Logical address mask assumes physical slot order')
    local=sum(1<<(2*slot) for slot in range(4) if mask & (1<<slot))
    global_bits=sum(2<<(2*slot) for slot in range(4) if mask & (1<<slot))
    require(snapshot_masks([0]*4,local)==[0,mask,0,15],'Actual local-enable expression lost DR7 bit domain')
    require(snapshot_masks([0]*4,global_bits)==[0,0,mask,15],'Actual global-enable expression lost DR7 bit domain')
for slot in range(4):
    for nibble in range(1,16):
        require(snapshot_masks([0]*4,nibble<<(16+4*slot))==[0,0,0,15 ^ (1<<slot)],'Actual execution/one-byte expression accepts a nonzero LEN/RW nibble')
long_folder=r'D:\a\_temp\mixel-ns-trace-'+('a'*32)+'\\'+('b'*32)+r'\commands'
long_files,_=commands(0xffffffff,0xffffffff,0xffffffff,leaf,long_folder)
require(all(len(text.replace('\n',';\n').encode('ascii'))<=4096 for text in long_files.values()),'Actual-shaped GUID command path exceeds4096')
selection_command='.printf "MIXEL_NAMESPACE_THREAD_SELECTION processMatched=%u threadMatched=%u\\n",(@$tpid == 0n101),(@$tid == 0n202)'
require(files['start.txt'].count(selection_command)==1,'Thread selection evidence is not exact/unique')
require(files['start.txt'].index('~~[0xca]s') < files['start.txt'].index(selection_command) < files['start.txt'].index('TRACE_FAIL_SCOPE') < files['start.txt'].index('MIXEL_NAMESPACE_BINDINGS'),'Selection evidence changed selector/scope/bindings order')
require('.if ((@$tpid != 0n101) or (@$tid != 0n202)) { .echo MIXEL_NAMESPACE_TRACE_FAIL_SCOPE; bc *; qd }' in files['start.txt'],'Original thread scope predicate changed')
require(files['start.txt'].index('TRACE_FAIL_PID')<files['start.txt'].index('TRACE_FAIL_ARCH')<files['start.txt'].index('~~[0xca]s'),'Original pre-selection PID/architecture gates changed')
require(files['start.txt'].endswith('.echo MIXEL_NAMESPACE_TRACE_READY\n'),'Initial debugger script must finish at the verified readiness barrier')
require(not re.search(r'(?m)^\s*(?:g|gc|gh|gn)\s*$',files['start.txt']),'Initial script resumed before the owned parent removed external suspension')
require('ba3 e 1 @$t13' in files['entry.txt'],'Fourth and final hardware slot missing')
require('ba3 e 1 /1 @$t19' in files['start.txt'] and '~~[0xca]s' in files['start.txt'],'Future export control or explicit OS-thread context missing')
for retained_tid in [1,10,0x1234,0x80000000,0xffffffff]:
    qualified_files,_=commands(101,retained_tid,2,leaf,r'D:\a\_temp\mixel-ns-trace-control')
    prefix=f'~~[0x{retained_tid:x}]'
    selectors=re.findall(r'(?m)^~~\[0x([0-9a-f]+)\]s$',qualified_files['start.txt'])
    require(len(selectors)==1 and int(selectors[0],16)==retained_tid,'Literal OS TID selector changed native retained DWORD')
    require(prefix+'ba0' in qualified_files['start.txt'] and prefix+'ba3' in qualified_files['entry.txt'],'Startup and hardware scopes use different native TIDs')
    require('~[0n' not in qualified_files['start.txt'],'Expression selector or additional selection introduced')
    require(f'(@$tpid != 0n101) or (@$tid != 0n{retained_tid})' in qualified_files['start.txt'],'Exact fatal scope was relaxed for literal selection')
require('r $t15 = @$exentry - 0n4096' in files['start.txt'] and 'r $t19 = @$t15 + 0n4352' in files['start.txt'],'Actual PE entry/export loaded-address correlation changed')
require('r $t19 = 0' in files['hwcontrol.txt'] and 'bc 3' not in files['hwcontrol.txt'] and files['hwcontrol.txt'].endswith('gc\n'),'One-shot control sentinel/removal/continuation changed')
require(files['hwcontrol.txt'].index('TRACE_FAIL_SCOPE')<files['hwcontrol.txt'].index('r $t19 = 0')<files['hwcontrol.txt'].index(module.HWCONTROL),'Control marker precedes exact thread/RIP qualification')
require('(@rip != @$t19)' in files['hwcontrol.txt'] and '(@$t19 == 0)' in files['hwcontrol.txt'],'Future control lacks RIP or duplicate guard')
require('@$t19 != 0' in files['entry.txt'] and '@$t19 != 0' in files['winentry.txt'],'Native window can reuse pending control slot')
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
        rejects(lambda ids=ids:commands(*ids,leaf,r'D:\a\_temp\mixel-ns-trace-control'),'Malformed identity accepted')
for event in ['',leaf+'x',leaf+'\\secret','Global\\'+leaf,'Local\\'+leaf,leaf.upper(),'Other-'+('0'*32),leaf.replace('0','g',1)]:
    rejects(lambda event=event:commands(101,202,2,event,r'D:\a\_temp\mixel-ns-trace-control'),'Arbitrary event name accepted')
for folder in ['',r'\\host\share',r'C:\..\owned',r'C:\owned\..',r'C:\owned path',r'C:\owned%path',r'C:\owned;echo',r'C:\owned"path',r'C:\owned&path','C:\\owned\npath','C:\\owned\\']:
    rejects(lambda folder=folder:commands(101,202,2,leaf,folder),'Ambiguous command folder accepted')

def win_entry(sequence=1,mask=7,matched=1,access=1,inherit=1):
    return f'MIXEL_NAMESPACE_WIN32_ENTRY sequence={sequence} leafValidMask={mask} leafMatched={matched} accessMatched={access} inheritFalse={inherit}'
def pre_entry(sequence=1,api=1):
    return f'MIXEL_NAMESPACE_PREWINDOW_ENTRY sequence={sequence} api={api}'
selection='MIXEL_NAMESPACE_THREAD_SELECTION processMatched=1 threadMatched=1'
list_begin='MIXEL_NAMESPACE_HW_LIST_BEGIN';list_end='MIXEL_NAMESPACE_HW_LIST_END'
def context(process=1,thread=1,address=0,local=0,global_mask=0,execution=15):
    return f'MIXEL_NAMESPACE_HW_CONTEXT processMatched={process} threadMatched={thread} addressPresentMask={address} localEnableMask={local} globalEnableMask={global_mask} executionByteMask={execution}'
hardware_prefix='\n'.join([list_begin,list_end,context()])+'\n'
ready_prefix='\n'.join([selection,list_begin,list_end,context(),module.BINDINGS,module.READY])+'\n'
prefix = ready_prefix+module.HWCONTROL+'\n'+win_entry()+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'
def record(sequence=1,api=1,access='0000000f',root=0,kind=4,status='c0000022',handle=0,qualified=1):
    return f'MIXEL_NAMESPACE_NATIVE seq={sequence} api={api} access=0x{access} rootNonNull={root} pathKind={kind} ntstatus=0x{status} handleNonNull={handle} scopeQualified={qualified}'
suffix='MIXEL_NAMESPACE_WIN32 handleNonNull=0 scopeQualified=1\n'+module.END+'\n'
valid=prefix+record()+'\n'+suffix
result=module.parse_trace(valid)
for field in ['address','local','global_mask','execution']:
    for value in range(16):
        require(module.parse_trace(valid.replace(context(),context(**{field:value})))==result,'Read-only masks influenced actual hit/window qualification')
for process in [0,1]:
    for thread in [0,1]:
        require(module.parse_trace(valid.replace(context(),context(process=process,thread=thread)))==result,'Secondary context replaced mandatory actual hardware scope gate')
for marker in [list_begin,list_end,context()]:
    rejects(lambda marker=marker:module.parse_trace(valid.replace(marker+'\n','',1)),'Missing startup hardware metadata accepted')
    rejects(lambda marker=marker:module.parse_trace(valid.replace(marker,marker+'\n'+marker,1)),'Duplicate startup hardware metadata accepted')
    rejects(lambda marker=marker:module.parse_trace(marker+'\n'+valid),'Metadata accepted before selected startup context')
    rejects(lambda marker=marker:module.parse_trace(valid+marker+'\n'),'Metadata accepted after actual END')
    rejects(lambda marker=marker:module.parse_trace(valid.replace(marker+'\n','',1).replace(module.BINDINGS,module.BINDINGS+'\n'+marker)),'Metadata accepted after bindings')
for field in ['address','local','global_mask','execution']:
    for value in [-1,16,'01','true','0x1',4294967295]:
        rejects(lambda field=field,value=value:module.parse_trace(valid.replace(context(),context(**{field:value}))),'Malformed/unbounded context mask accepted')
for marker in [context(process=2),context(thread=-1),context(thread='true'),context()+' extra=PRIVATE_UNKNOWN',context().replace('addressPresentMask','AddressPresentMask'),'MIXEL_NAMESPACE_HW_CONTEXT','MIXEL_NAMESPACE_HW_LIST_BEGIN_EXTRA','MIXEL_NAMESPACE_HW_LIST_END_EXTRA']:
    rejects(lambda marker=marker:module.parse_trace(valid.replace(context(),marker)),'Unknown/malformed hardware observation accepted')
rejects(lambda:module.parse_trace(valid.replace(list_begin+'\n'+list_end,list_end+'\n'+list_begin)),'Hardware block end-before-begin accepted')
rejects(lambda:module.parse_trace(valid.replace(list_end+'\n'+context(),context()+'\n'+list_end)),'Context accepted inside private hardware list block')
require(module.parse_trace(valid.replace(list_begin+'\n',list_begin+'\n'+'0 e PRIVATE_RAW_ADDRESS_COMMAND\n'))==result,'Private bl rows changed fixed original actual result schema')
for marker in [list_begin,list_end,context()]:
    require(module.parse_trace(valid+marker[:-1])==result,'Partial final hardware metadata was treated as a complete marker')
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
    raw=ready_prefix+module.HWCONTROL+'\n'+'\n'.join(entries)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+record()+'\n'+suffix
    require(module.parse_trace(raw)==result,'Bounded source-only diagnostics altered actual capture result schema/values')
for mask in range(8):
    for access in [0,1]:
        for inherit in [0,1]:
            raw=ready_prefix+module.HWCONTROL+'\n'+win_entry(mask=mask,matched=0,access=access,inherit=inherit)+'\n'+win_entry(2)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+suffix
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
for process in [0,1]:
    for thread in [0,1]:
        alternate=f'MIXEL_NAMESPACE_THREAD_SELECTION processMatched={process} threadMatched={thread}'
        if process and thread:require(module.parse_trace(valid)==result,'Exact thread selection no longer accepts qualified source')
        else:rejects(lambda alternate=alternate:module.parse_trace(valid.replace(selection,alternate)),'False thread selection qualifies source trace')
for marker in [selection+' extra=PRIVATE_UNKNOWN',selection.replace('processMatched=1','processMatched=true'),selection.replace('threadMatched=1','threadMatched=2'),selection.replace('threadMatched=1','threadMatched=01'),selection.replace('processMatched','ProcessMatched'),'MIXEL_NAMESPACE_THREAD_SELECTION','MIXEL_NAMESPACE_THREAD_SELECTION_EXTRA']:
    rejects(lambda marker=marker:module.parse_trace(valid.replace(selection,marker)),'Malformed/unknown thread selection qualifies source trace')
rejects(lambda:module.parse_trace(valid.replace(selection+'\n','')),'Missing thread selection qualifies source trace')
rejects(lambda:module.parse_trace(valid.replace(selection,selection+'\n'+selection)),'Duplicate thread selection qualifies source trace')
rejects(lambda:module.parse_trace(valid.replace(selection+'\n','').replace(module.BINDINGS,module.BINDINGS+'\n'+selection)),'Late thread selection qualifies source trace')
rejects(lambda:module.parse_trace(valid.replace(selection+'\n','').replace(module.READY,module.READY+'\n'+selection)),'Post-READY thread selection qualifies source trace')
rejects(lambda:module.parse_trace(selection),'Partial thread selection qualifies source trace')
rejects(lambda:module.parse_trace(valid.rstrip('\n')),'Partial final END qualifies source trace')
require(module.parse_trace(valid+'MIXEL_NAMESPACE_THREAD_SELECTION processMatched=')==result,'Incomplete final diagnostic tail was treated as a completed marker')
rejects(lambda:module.parse_trace(valid.replace(module.HWCONTROL+'\n','')),'Missing future hardware control qualifies source window')
rejects(lambda:module.parse_trace(valid.replace(module.HWCONTROL,module.HWCONTROL+'\n'+module.HWCONTROL)),'Duplicate hardware control accepted')
rejects(lambda:module.parse_trace(module.HWCONTROL+'\n'+valid),'Control accepted before readiness')
rejects(lambda:module.parse_trace(valid+module.HWCONTROL+'\n'),'Control accepted after completion')
rejects(lambda:module.parse_trace(valid.replace(module.HWCONTROL,module.HWCONTROL.replace('=1','=0'))),'Unqualified control accepted')
for entries in [pre_entry()+'\n',win_entry(matched=0)+'\n',pre_entry()+'\n'+win_entry(2,matched=0)+'\n']:
    size=len(entries.splitlines())
    raw=ready_prefix+entries+module.HWCONTROL+'\n'+win_entry(size+1)+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+suffix
    require(module.parse_trace(raw)['completedQualifiedSourceWindow'],'Safe unmatched/source-IO calls before future probe were rejected')
rejects(lambda:module.parse_trace(ready_prefix+win_entry()+'\n'+module.HWCONTROL+'\nMIXEL_NAMESPACE_TRACE_WINDOW\n'+suffix),'Control can follow first matched source entry')

print(f'PASS: {checks} pure generator/parser controls; 4 hardware slots, exact Win32 GUID window, same-thread/RSP-qualified returns, shared32-entry diagnostics and32-record bound, native values unchanged')
print('Scope: no actual Windows/CDB execution or signed/runtime/customer-Accept qualification')
