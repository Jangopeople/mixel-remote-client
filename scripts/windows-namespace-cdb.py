#!/usr/bin/env python3
"""Bounded source-only CDB diagnostic; actual execution is separately qualified.
The parent must verify retained owned suspended PID/TID, SID/token/session first.
Only hardware execution breakpoints, debugger pseudo-registers and bounded reads.
"""
import argparse
import json
import re
from pathlib import Path
from windows_namespace_probe import qualify_file
from windows_namespace_engine import qualify_file as qualify_engine_file

RECORD = re.compile(r'MIXEL_NAMESPACE_NATIVE seq=([0-9]+) api=([12]) access=0x([0-9a-fA-F]{8}) rootNonNull=([01]) pathKind=([0-9]+) ntstatus=0x([0-9a-fA-F]{8}) handleNonNull=([01]) scopeQualified=1')
WIN32 = re.compile(r'MIXEL_NAMESPACE_WIN32 handleNonNull=([01]) scopeQualified=1')
WIN32_ENTRY = re.compile(r'MIXEL_NAMESPACE_WIN32_ENTRY sequence=([1-9][0-9]?) leafValidMask=([0-7]) leafMatched=([01]) accessMatched=([01]) inheritFalse=([01])')
PREWINDOW_ENTRY = re.compile(r'MIXEL_NAMESPACE_PREWINDOW_ENTRY sequence=([1-9][0-9]?) api=([12])')
BINDINGS = 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1'
READY = 'MIXEL_NAMESPACE_TRACE_READY'
END = 'MIXEL_NAMESPACE_TRACE_END'
HWCONTROL = 'MIXEL_NAMESPACE_HWCONTROL scopeQualified=1'
CONTINUATION_ACK = 'MIXEL_NAMESPACE_CONTINUATION_ACK'
THREAD_SELECTION = re.compile(r'MIXEL_NAMESPACE_THREAD_SELECTION processMatched=([01]) threadMatched=([01])')
HW_CONTEXT = re.compile(r'MIXEL_NAMESPACE_HW_CONTEXT processMatched=([01]) threadMatched=([01]) addressPresentMask=([0-9]|1[0-5]) localEnableMask=([0-9]|1[0-5]) globalEnableMask=([0-9]|1[0-5]) executionByteMask=([0-9]|1[0-5])')

def windows_file(folder, leaf):
    if not re.fullmatch(r'[A-Z]:\\[A-Za-z0-9_\\.-]+', folder) or '..' in folder.split('\\') or folder.endswith('\\'):
        raise ValueError('Expected a bounded unambiguous owned Windows script folder')
    return folder + '\\' + leaf

def ba_command_string(body):
    # ba parses quoted CommandString with C-style escapes. Preserve the exact
    # decoded owned callback; unquoted script commands keep literal paths.
    if type(body) is not str or not body or len(body) > 1023 or any(ord(c) < 32 or ord(c) > 126 or c == '"' for c in body):
        raise ValueError('Expected a bounded unambiguous printable callback body')
    encoded = '"' + body.replace('\\', '\\\\') + '"'
    if len(encoded.encode('ascii')) > 2048:
        raise ValueError('Encoded callback exceeds its bounded command string')
    return encoded

def commands(pid, tid, session, event, folder, pe, engine=None):
    for value in (pid, tid, session):
        if type(value) is not int or not 0 < value <= 0xffffffff:
            raise ValueError('Expected actual nonzero DWORD identity')
    if not re.fullmatch(r'Mixel-Remote-Standard-Lease-[0-9a-f]{32}', event):
        raise ValueError('Expected the exact isolated source-fixture GUID leaf')
    # Only a freshly qualified complete executable supplies these fields.
    if type(pe) is not dict or any(type(pe.get(k)) is not int or not 0 < pe[k] <= 0xffffffff for k in ('entryRva','probeRva','imageSize','peOffset','headersSize')):
        raise ValueError('Expected bounded qualified private PE properties')
    def script(leaf):
        return '$$><' + windows_file(folder, leaf)
    def fail(tag):
        return f'.echo MIXEL_NAMESPACE_TRACE_FAIL_{tag}; bc *; qd'
    scope = f'.if ((@$tpid != 0n{pid}) or (@$tid != 0n{tid})) {{ {fail("SCOPE")} }}'
    thread = f'~~[0x{tid:x}]'
    paths = ['\\', '\\Sessions', f'\\Sessions\\{session}',
             f'\\Sessions\\{session}\\BaseNamedObjects', '\\BaseNamedObjects', event,
             '\\BaseNamedObjects\\' + event,
             f'\\Sessions\\{session}\\BaseNamedObjects\\' + event,
             'Global\\' + event, 'Local\\' + event,
             f'\\Sessions\\{session}\\BaseNamedObjects\\Global',
             f'\\Sessions\\{session}\\BaseNamedObjects\\Local']
    if not all(len(p) <= 128 for p in paths):
        raise ValueError('Allowlisted path exceeds128 UTF-16 units')
    def equals(path):
        return ' and '.join(f'(wo(@$t9+0n{i*2}) == 0x{ord(c):04x})' for i, c in enumerate(path))
    helpers = {}
    # Nested private helpers keep every condensed CDB command block within4096.
    # First exact-source OpenEventW only: three fixed spellings of the same GUID.
    # Hardware Win32 entry slot2 is reused for its return, leaving slot3 for NT.
    winentry = [scope,
                f'.if ((@$t16 != 0) or (@$t17 != 0)) {{ {fail("WINDOW_NESTED")} }}',
                f'.if (@$t4 >= 0n32) {{ {fail("ENTRY_LIMIT")} }}',
                'r $t4 = @$t4 + 1', 'r $t9 = @r8', 'r $t15 = 0', 'r $t5 = 0']
    for index, spelling in enumerate(('Global\\' + event, 'Local\\' + event, event), 1):
        size = 2 * (len(spelling) + 1)
        # Nested validity guards ensure comparison cannot read invalid memory.
        name = f'winmatch-{index:02d}.txt'
        helpers[name] = f'.if ($vvalid(@$t9,0n{size}) != 0) {{ r $t5 = @$t5 | 0n{1 << (index-1)}; .if ({equals(spelling)} and (wo(@$t9+0n{size-2}) == 0)) {{ r $t15 = 1 }} }}\n'
        winentry.append(script(name))
    winentry.extend(['.printf "MIXEL_NAMESPACE_WIN32_ENTRY sequence=%u leafValidMask=%u leafMatched=%u accessMatched=%u inheritFalse=%u\\n",@$t4,@$t5,(@$t15 != 0),(@ecx == 0x00100000),(@edx == 0)',
                     '.if (@$t15 != 0) {',
                     f'.if (@$t19 != 0) {{ {fail("HWCONTROL_MISSING")} }}',
                     f'.if ((@ecx != 0x00100000) or (@edx != 0)) {{ {fail("WIN32_INTERFACE")} }}',
                     'r $t16 = 1', 'r $t14 = @rsp',
                     f'.if ($vvalid(@$t14,0n8) == 0) {{ {fail("WIN32_STACK")} }}',
                     'r $t18 = poi(@$t14)',
                     f'.if ($vvalid(@$t18,0n1) == 0) {{ {fail("WIN32_RETURN")} }}',
                     'bc 2', f'{thread}ba2 e 1 @$t18 {ba_command_string(script("winreturn.txt"))}',
                     '.echo MIXEL_NAMESPACE_TRACE_WINDOW', '}','gc'])
    enter = [scope, '.if (@$t16 == 0) {',
             f'.if (@$t4 >= 0n32) {{ {fail("ENTRY_LIMIT")} }}',
             'r $t4 = @$t4 + 1',
             '.printf "MIXEL_NAMESPACE_PREWINDOW_ENTRY sequence=%u api=%u\\n",@$t4,@$t1',
             '}', '.if (@$t16 == 1) {',
             f'.if (@$t19 != 0) {{ {fail("HWCONTROL_MISSING")} }}',
             f'.if (@$t17 != 0) {{ {fail("NATIVE_NESTED")} }}',
             f'.if (@$t0 >= 0n32) {{ {fail("RECORD_LIMIT")} }}',
             'r $t0 = @$t0 + 1', 'r $t17 = 1', 'r $t2 = @edx',
             'r $t6 = @r8', 'r $t11 = @rcx', 'r $t12 = @rsp',
             f'.if ($vvalid(@$t12,0n8) == 0) {{ {fail("STACK")} }}',
             'r $t13 = poi(@$t12)',
             f'.if (($vvalid(@$t13,0n1) == 0) or ($vvalid(@$t6,0n48) == 0) or ($vvalid(@$t11,0n8) == 0)) {{ {fail("POINTER")} }}',
             f'.if (dwo(@$t6) != 0n48) {{ {fail("ATTR_SIZE")} }}',
             'r $t3 = poi(@$t6+0n8)', 'r $t7 = poi(@$t6+0n16)', 'r $t10 = 0',
             f'.if ($vvalid(@$t7,0n16) == 0) {{ {fail("NAME_HEADER")} }}',
             'r $t8 = wo(@$t7)',
             f'.if ((@$t8 > 0n256) or ((@$t8 & 1) != 0) or (wo(@$t7+0n2) < @$t8)) {{ {fail("NAME_LENGTH")} }}',
             'r $t9 = poi(@$t7+0n8)',
             f'.if ((@$t8 > 0) and ($vvalid(@$t9,@$t8) == 0)) {{ {fail("NAME_BUFFER")} }}']
    for kind, path in enumerate(paths, 1):
        # Even a known longer path is never read unless exact byte length matches.
        name = f'pathmatch-{kind:02d}.txt'
        helpers[name] = f'.if (@$t8 == 0n{len(path)*2}) {{ .if ({equals(path)}) {{ r $t10 = 0n{kind} }} }}\n'
        enter.append(script(name))
    enter.extend(['bd 0 1', f'{thread}ba3 e 1 @$t13 {ba_command_string(script("return.txt"))}', '}','gc'])
    ret = [scope,
           f'.if ((@$t19 != 0) or (@$t16 != 1) or (@$t17 != 1) or (@rsp != @$t12+0n8) or (@rip != @$t13)) {{ {fail("RETURN_SCOPE")} }}',
           f'.if ($vvalid(@$t11,0n8) == 0) {{ {fail("RETURN_HANDLE")} }}',
           '.printf "MIXEL_NAMESPACE_NATIVE seq=%u api=%u access=0x%08x rootNonNull=%u pathKind=%u ntstatus=0x%08x handleNonNull=%u scopeQualified=1\\n",@$t0,@$t1,@$t2,(@$t3 != 0),@$t10,@eax,(poi(@$t11) != 0)',
           'bc 3', 'r $t17 = 0', 'be 0 1', 'g']
    winret = [scope,
              f'.if ((@$t16 != 1) or (@$t17 != 0) or (@rsp != @$t14+0n8) or (@rip != @$t18)) {{ {fail("WIN32_RETURN_SCOPE")} }}',
              '.printf "MIXEL_NAMESPACE_WIN32 handleNonNull=%u scopeQualified=1\\n",(@rax != 0)',
              '.echo MIXEL_NAMESPACE_TRACE_END', 'bc *', 'qd']
    control = [scope,f'.if ((@$t19 == 0) or (@rip != @$t19) or (@$t16 != 0) or (@$t17 != 0)) {{ {fail("HWCONTROL_SCOPE")} }}',
               'r $t19 = 0',f'.echo {HWCONTROL}','gc']
    init = ['.expr /s masm', f'.if (@$tpid != 0n{pid}) {{ {fail("PID")} }}',
            f'.if (@$ptrsize != 0n8) {{ {fail("ARCH")} }}',
            f'{thread}s',
            f'.printf "MIXEL_NAMESPACE_THREAD_SELECTION processMatched=%u threadMatched=%u\\n",(@$tpid == 0n{pid}),(@$tid == 0n{tid})',scope,
            'bc *', 'r $t0 = 0', 'r $t4 = 0', 'r $t5 = 0', 'r $t16 = 0', 'r $t17 = 0',
            f'.if (@$exentry <= 0n{pe["entryRva"]}) {{ {fail("IMAGE")} }}',
            f'r $t15 = @$exentry - 0n{pe["entryRva"]}',
            f'.if (((@$t15 & 0xffff) != 0) or ($vvalid(@$t15,0n{pe["headersSize"]}) == 0)) {{ {fail("IMAGE")} }}']
    checks=[(0,2,0x5a4d),(0x3c,4,pe['peOffset']),(pe['peOffset'],4,0x4550),
            (pe['peOffset']+4,2,0x8664),(pe['peOffset']+6,2,pe['sectionCount']),
            (pe['peOffset']+20,2,pe['optionalSize']),(pe['peOffset']+22,2,pe['coffFlags'])]
    optional=pe['peOffset']+24
    checks.extend([(optional,2,0x20b),(optional+16,4,pe['entryRva']),(optional+56,4,pe['imageSize']),
                   (optional+60,4,pe['headersSize']),(optional+112,4,pe['exportRva']),(optional+116,4,pe['exportSize']),
                   (pe['probeSectionHeader']+8,4,pe['probeSectionVirtualSize']),(pe['probeSectionHeader']+12,4,pe['probeSectionRva']),
                   (pe['probeSectionHeader']+16,4,pe['probeSectionRawSize']),(pe['probeSectionHeader']+36,4,pe['probeSectionFlags'])])
    for offset,size,value in checks:
        read='wo' if size==2 else 'dwo'
        init.append(f'.if ({read}(@$t15+0n{offset}) != 0n{value}) {{ {fail("IMAGE")} }}')
    # Read-only pre-continuation context. The engine may deploy breakpoints on
    # continuation; these masks never prove activation or permit a trace window.
    addresses=('ntdll!NtOpenDirectoryObject','ntdll!NtOpenEvent','KERNELBASE!OpenEventW','@$t19')
    address_mask=' + '.join('('+ ' or '.join(f'(@dr{slot} == {address})' for slot in range(4))+f')*0n{1 << index}' for index,address in enumerate(addresses))
    local_mask=' + '.join(f'((@dr7 & 0x{1 << (2*slot):x}) != 0)*0n{1 << slot}' for slot in range(4))
    global_mask=' + '.join(f'((@dr7 & 0x{2 << (2*slot):x}) != 0)*0n{1 << slot}' for slot in range(4))
    execution_mask=' + '.join(f'((@dr7 & 0x{15 << (16+4*slot):x}) == 0)*0n{1 << slot}' for slot in range(4))
    hardware_snapshot=['.echo MIXEL_NAMESPACE_HW_LIST_BEGIN','bl /L 0 1 2 3','.echo MIXEL_NAMESPACE_HW_LIST_END',
                       f'.printf "MIXEL_NAMESPACE_HW_CONTEXT processMatched=%u threadMatched=%u addressPresentMask=%u localEnableMask=%u globalEnableMask=%u executionByteMask=%u\\n",(@$tpid == 0n{pid}),(@$tid == 0n{tid}),({address_mask}),({local_mask}),({global_mask}),({execution_mask})']
    # Fixed read-only engine metadata; outside the capture-marker namespace.
    hardware_snapshot.extend(['.echo MIXEL_CDB_THREAD_STATUS_BEGIN','~.','.echo MIXEL_CDB_THREAD_STATUS_END',
                              '.echo MIXEL_CDB_BREAKPOINT_COMMANDS_BEGIN','.bpcmds','.echo MIXEL_CDB_BREAKPOINT_COMMANDS_END'])
    init.extend([f'r $t19 = @$t15 + 0n{pe["probeRva"]}',
                 f'.if ((@$t19 <= @$t15) or (@$t19 >= @$t15+0n{pe["imageSize"]}) or ($vvalid(@$t19,0n1) == 0) or (@$t19 == ntdll!NtOpenDirectoryObject) or (@$t19 == ntdll!NtOpenEvent) or (@$t19 == KERNELBASE!OpenEventW)) {{ {fail("CONTROL_ADDRESS")} }}',
            f'{thread}ba0 e 1 ntdll!NtOpenDirectoryObject {ba_command_string("r $t1 = 1; " + script("entry.txt"))}',
            f'{thread}ba1 e 1 ntdll!NtOpenEvent {ba_command_string("r $t1 = 2; " + script("entry.txt"))}',
            f'{thread}ba2 e 1 KERNELBASE!OpenEventW {ba_command_string(script("winentry.txt"))}',
            f'{thread}ba3 e 1 /1 @$t19 {ba_command_string(script("hwcontrol.txt"))}',
            f'.if (@$bp3 != @$t19) {{ {fail("CONTROL_BINDING")} }}',
            script('hardware-state.txt'),
            '.printf "MIXEL_NAMESPACE_BINDINGS directory=%u event=%u win32=%u\\n",(@$bp0 == ntdll!NtOpenDirectoryObject),(@$bp1 == ntdll!NtOpenEvent),(@$bp2 == KERNELBASE!OpenEventW)',
            # Keep the initial debug event stopped. The retained parent removes
            # the source thread's external suspension after verifying READY,
            # then queues exactly one fixed continuation through owned stdin.
            '.echo MIXEL_NAMESPACE_TRACE_READY'])
    if engine is not None:
        if type(engine) is not dict or set(engine)!= {'dll','binary'}:raise ValueError('Expected exact private engine inputs')
        for key,leaf in (('dll','mixelengine.dll'),('binary','lease-standard.exe')):
            path=engine[key]
            if type(path) is not str or len(path)>512 or not path.endswith('\\'+leaf) or windows_file(path.rsplit('\\',1)[0],leaf)!=path or '..' in path:raise ValueError('Private engine input path is invalid')
        if engine['dll']!=windows_file(folder,'mixelengine.dll'):raise ValueError('Engine DLL is outside its exact private command folder')
        helpers['engine-query.txt']=f'.load {engine["dll"]}\n!mixelengine.inspect {pid} {tid} {pe["probeRva"]} {pe["imageSize"]} {engine["binary"]} {folder}\n'
        init.insert(init.index(script('hardware-state.txt'))+1,script('engine-query.txt'))
    files = {name: '\n'.join(lines) + '\n' for name, lines in
             [('start.txt', init), ('winentry.txt', winentry), ('entry.txt', enter),
              ('return.txt', ret), ('winreturn.txt', winret),('hwcontrol.txt',control),('hardware-state.txt',hardware_snapshot)]}
    files.update(helpers)
    if any(len(text.replace('\n',';\n').encode('ascii')) > 4096 for text in files.values()):
        raise ValueError('Condensed CDB command block exceeds4096 characters')
    return files, dict(enumerate(paths, 1))

def parse_trace(raw):
    if type(raw) is not str or len(raw.encode('utf-8')) > 1048576:
        raise ValueError('Bounded private trace exceeds1MiB')
    records, stage, window, winreturn = [], 0, False, None
    entry_count, qualified_entry, control, selected = 0, False, False, False
    list_open, list_complete, context, acknowledged = False, False, False, False
    # A concurrent or incomplete final marker cannot supply evidence.
    for line in raw[:raw.rfind('\n')+1].splitlines():
        line = line.strip()
        # Only exact marker lines count. Script command echoes remain ignored.
        if not line.startswith('MIXEL_NAMESPACE_'):
            if re.search(r'(?i)(syntax error|couldn.t resolve|unable to insert breakpoint|no hardware breakpoint|hardware breakpoint limit)', line):
                raise ValueError('CDB command/binding failure')
            continue
        if line.startswith('MIXEL_NAMESPACE_TRACE_FAIL_'):
            raise ValueError('CDB bounded trace failed')
        if (match := THREAD_SELECTION.fullmatch(line)):
            if stage != 0 or selected or match.groups() != ('1','1'):
                raise ValueError('Thread selection is false/duplicate/out-of-order')
            selected = True
        elif line == 'MIXEL_NAMESPACE_HW_LIST_BEGIN':
            if stage != 0 or not selected or list_open or list_complete or context: raise ValueError('Hardware list begins outside its one bounded startup block')
            list_open = True
        elif line == 'MIXEL_NAMESPACE_HW_LIST_END':
            if not list_open or stage != 0: raise ValueError('Hardware list end lacks its bounded startup block')
            list_open = False; list_complete = True
        elif HW_CONTEXT.fullmatch(line):
            if stage != 0 or not list_complete or list_open or context: raise ValueError('Hardware context is duplicate/out-of-order')
            context = True
        elif line == BINDINGS:
            if stage != 0 or not selected or not context: raise ValueError('Bindings lack exact qualified thread selection/typed hardware observation')
            stage = 1
        elif line == READY:
            if stage != 1: raise ValueError('Ready lacks exact bindings')
            stage = 2
        elif line == CONTINUATION_ACK:
            if stage != 2 or acknowledged or control or entry_count or window:
                raise ValueError('Continuation acknowledgement is duplicate/out-of-order')
            acknowledged = True
        elif line == 'MIXEL_NAMESPACE_TRACE_WINDOW':
            if stage != 2 or window or not qualified_entry or not control: raise ValueError('Window lacks its qualified source entry/control')
            window = True
        elif line == HWCONTROL:
            if stage != 2 or window or qualified_entry or control: raise ValueError('Hardware control is duplicate/out-of-order')
            control = True
        elif (match := WIN32_ENTRY.fullmatch(line)):
            if stage != 2 or window or qualified_entry: raise ValueError('Win32 entry outside bounded pre-window stage')
            sequence, mask, matched, access, inherit = map(int, match.groups())
            if sequence != entry_count + 1 or sequence > 32 or (matched and not mask): raise ValueError('Win32 entry sequence/readability invalid')
            if matched and not (access and inherit and control): raise ValueError('Matched Win32 entry violates original interface/control')
            entry_count = sequence
            qualified_entry = bool(matched and access and inherit)
        elif (match := PREWINDOW_ENTRY.fullmatch(line)):
            if stage != 2 or window or qualified_entry: raise ValueError('Native pre-window entry outside bounded stage')
            sequence = int(match.group(1))
            if sequence != entry_count + 1 or sequence > 32: raise ValueError('Pre-window entry sequence invalid')
            entry_count = sequence
        elif line == END:
            if stage != 3 or winreturn is None: raise ValueError('End lacks qualified Win32 return')
            stage = 4
        elif (match := RECORD.fullmatch(line)):
            if stage != 2 or not window or len(records) >= 32: raise ValueError('Native record outside bounded window')
            seq, api, access, root, kind, status, handle = match.groups()
            seq, api, root, kind, handle = map(int, (seq, api, root, kind, handle))
            if seq != len(records) + 1 or kind > 12: raise ValueError('Native sequence/path enum invalid')
            records.append(dict(sequence=seq, api=api, desiredAccess=int(access, 16), rootNonNull=bool(root),
                                pathKind=kind, nativeStatus=int(status, 16), handleNonNull=bool(handle)))
        elif (match := WIN32.fullmatch(line)):
            if stage != 2 or not window or winreturn is not None: raise ValueError('Win32 return outside exact window')
            winreturn = bool(int(match.group(1))); stage = 3
        else:
            raise ValueError('Unknown/malformed namespace marker')
    if stage != 4:
        raise ValueError('Incomplete diagnostic; never qualify readiness from timeout/partial trace')
    return {'evidenceScope': 'source-only debugger call observation; not signed runtime/customer-Accept qualification',
            'nativeCallCount': len(records), 'records': records, 'win32HandleNonNull': winreturn,
            'completedQualifiedSourceWindow': True}

def observe_failure_capture(raw):
    """Secondary safe metadata only; never replace the original full reader/gate.

    Preserve every complete namespace marker, including unknown/failure markers,
    and validate that stream with the unchanged reader. Non-marker debugger
    failures remain explicit. No target values other than the reader's bounded
    native enums/masks/status/booleans are returned.
    """
    result = dict(metadataOnly=True, readinessQualified=False,
                  inputBounded=False, fullReaderAccepted=False,
                  commandFailure=False, markerProjectionAccepted=False,
                  nativeCallCount=0, records=[], win32HandleNonNull=False)
    if type(raw) is not str:
        return result
    try:
        bounded = len(raw.encode('utf-8')) <= 1048576
    except UnicodeEncodeError:
        return result
    if not bounded:
        return result
    result['inputBounded'] = True
    complete = raw[:raw.rfind('\n') + 1]
    result['commandFailure'] = bool(re.search(
        r'(?i)(syntax error|couldn.t resolve|unable to insert breakpoint|no hardware breakpoint|hardware breakpoint limit)', complete))
    try:
        parse_trace(raw)
        result['fullReaderAccepted'] = True
    except ValueError:
        pass
    markers = [line.strip() for line in complete.splitlines()
               if line.strip().startswith('MIXEL_NAMESPACE_')]
    try:
        validated = parse_trace('\n'.join(markers) + '\n')
    except ValueError:
        return result
    result.update(markerProjectionAccepted=True,
                  nativeCallCount=validated['nativeCallCount'],
                  records=validated['records'],
                  win32HandleNonNull=validated['win32HandleNonNull'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int); parser.add_argument('--tid', type=int)
    parser.add_argument('--session', type=int); parser.add_argument('--event')
    parser.add_argument('--windows-folder'); parser.add_argument('--output', type=Path)
    parser.add_argument('--binary',type=Path);parser.add_argument('--exe-sha256')
    parser.add_argument('--engine-dll',type=Path);parser.add_argument('--engine-sha256')
    parser.add_argument('--parse', type=Path)
    parser.add_argument('--failure-observe', type=Path)
    args = parser.parse_args()
    if args.failure_observe:
        if args.parse:
            parser.error('Full parsing and secondary failure observation are exclusive')
        path = args.failure_observe
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576:
            raise ValueError('Secondary private capture must be a bounded plain file')
        with path.open('rb') as stream:
            data = stream.read(1048577)
        if len(data) > 1048576:
            raise ValueError('Secondary private capture exceeded its byte bound')
        print(json.dumps(observe_failure_capture(data.decode('utf-8')), separators=(',', ':')))
        return
    if args.parse:
        if args.parse.stat().st_size > 1048576: raise ValueError('Private log exceeds1MiB')
        result = parse_trace(args.parse.read_text(encoding='utf-8'))
        print(json.dumps(result, separators=(',', ':')))
        return
    if None in (args.pid, args.tid, args.session, args.event, args.windows_folder, args.output,args.binary,args.exe_sha256,args.engine_dll,args.engine_sha256):
        parser.error('All generator identity/folder/output fields are required')
    pe=qualify_file(args.binary,args.exe_sha256)
    qualify_engine_file(args.engine_dll,args.engine_sha256)
    files, paths = commands(args.pid, args.tid, args.session, args.event, args.windows_folder,pe,{'dll':str(args.engine_dll),'binary':str(args.binary)})
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, text in files.items():
        target = args.output / name; target.write_text(text); target.chmod(0o600)
    scope = {'scope': 'Source-only debugger evidence; never a signed release/readiness gate', 'maximumRecords': 32,
             'pathKind': paths, 'requires': 'retained owned suspended PID/TID/SID + non-elevated same-session identity first; actual bindings/READY before one owned ResumeThread then one fixed owned-stdin g continuation; bounded deadline/private1MiB raw log; exact Win32 result + qd/owned cleanup; no public artifact/upload'}
    target = args.output / 'DIAGNOSTIC-SCOPE.json'; target.write_text(json.dumps(scope, indent=2) + '\n'); target.chmod(0o600)
    print('PASS: bounded hardware diagnostic commands generated; actual CDB execution is a separate gate')

if __name__ == '__main__':
    main()
