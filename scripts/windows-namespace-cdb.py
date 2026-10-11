#!/usr/bin/env python3
"""Bounded source-only CDB diagnostic; actual execution is separately qualified.
The parent must verify retained owned suspended PID/TID, SID/token/session first.
Only hardware execution breakpoints, debugger pseudo-registers and bounded reads.
"""
import argparse
import json
import re
from pathlib import Path

RECORD = re.compile(r'MIXEL_NAMESPACE_NATIVE seq=([0-9]+) api=([12]) access=0x([0-9a-fA-F]{8}) rootNonNull=([01]) pathKind=([0-9]+) ntstatus=0x([0-9a-fA-F]{8}) handleNonNull=([01]) scopeQualified=1')
WIN32 = re.compile(r'MIXEL_NAMESPACE_WIN32 handleNonNull=([01]) scopeQualified=1')
BINDINGS = 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1'
READY = 'MIXEL_NAMESPACE_TRACE_READY'
END = 'MIXEL_NAMESPACE_TRACE_END'

def windows_file(folder, leaf):
    if not re.fullmatch(r'[A-Z]:\\[A-Za-z0-9_\\.-]+', folder) or '..' in folder.split('\\') or folder.endswith('\\'):
        raise ValueError('Expected a bounded unambiguous owned Windows script folder')
    return folder + '\\' + leaf

def commands(pid, tid, session, event, folder):
    for value in (pid, tid, session):
        if type(value) is not int or not 0 < value <= 0xffffffff:
            raise ValueError('Expected actual nonzero DWORD identity')
    if not re.fullmatch(r'Mixel-Remote-Standard-Lease-[0-9a-f]{32}', event):
        raise ValueError('Expected the exact isolated source-fixture GUID leaf')
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
                'r $t9 = @r8', 'r $t15 = 0']
    for index, spelling in enumerate(('Global\\' + event, 'Local\\' + event, event), 1):
        size = 2 * (len(spelling) + 1)
        # Nested validity guards ensure comparison cannot read invalid memory.
        name = f'winmatch-{index:02d}.txt'
        helpers[name] = f'.if ($vvalid(@$t9,0n{size}) != 0) {{ .if ({equals(spelling)} and (wo(@$t9+0n{size-2}) == 0)) {{ r $t15 = 1 }} }}\n'
        winentry.append(script(name))
    winentry.extend(['.if (@$t15 != 0) {',
                     f'.if ((@ecx != 0x00100000) or (@edx != 0)) {{ {fail("WIN32_INTERFACE")} }}',
                     'r $t16 = 1', 'r $t14 = @rsp',
                     f'.if ($vvalid(@$t14,0n8) == 0) {{ {fail("WIN32_STACK")} }}',
                     'r $t18 = poi(@$t14)',
                     f'.if ($vvalid(@$t18,0n1) == 0) {{ {fail("WIN32_RETURN")} }}',
                     'bc 2', f'{thread}ba2 e 1 @$t18 "{script("winreturn.txt")}"',
                     '.echo MIXEL_NAMESPACE_TRACE_WINDOW', '}','gc'])
    enter = [scope, '.if (@$t16 == 1) {',
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
    enter.extend(['bd 0 1', f'{thread}ba3 e 1 @$t13 "{script("return.txt")}"', '}','gc'])
    ret = [scope,
           f'.if ((@$t16 != 1) or (@$t17 != 1) or (@rsp != @$t12+0n8) or (@rip != @$t13)) {{ {fail("RETURN_SCOPE")} }}',
           f'.if ($vvalid(@$t11,0n8) == 0) {{ {fail("RETURN_HANDLE")} }}',
           '.printf "MIXEL_NAMESPACE_NATIVE seq=%u api=%u access=0x%08x rootNonNull=%u pathKind=%u ntstatus=0x%08x handleNonNull=%u scopeQualified=1\\n",@$t0,@$t1,@$t2,(@$t3 != 0),@$t10,@eax,(poi(@$t11) != 0)',
           'bc 3', 'r $t17 = 0', 'be 0 1', 'g']
    winret = [scope,
              f'.if ((@$t16 != 1) or (@$t17 != 0) or (@rsp != @$t14+0n8) or (@rip != @$t18)) {{ {fail("WIN32_RETURN_SCOPE")} }}',
              '.printf "MIXEL_NAMESPACE_WIN32 handleNonNull=%u scopeQualified=1\\n",(@rax != 0)',
              '.echo MIXEL_NAMESPACE_TRACE_END', 'bc *', 'qd']
    init = ['.expr /s masm', f'.if (@$tpid != 0n{pid}) {{ {fail("PID")} }}',
            f'.if (@$ptrsize != 0n8) {{ {fail("ARCH")} }}',
            'bc *', 'r $t0 = 0', 'r $t16 = 0', 'r $t17 = 0',
            f'{thread}ba0 e 1 ntdll!NtOpenDirectoryObject "r $t1 = 1; {script("entry.txt")}"',
            f'{thread}ba1 e 1 ntdll!NtOpenEvent "r $t1 = 2; {script("entry.txt")}"',
            f'{thread}ba2 e 1 KERNELBASE!OpenEventW "{script("winentry.txt")}"',
            '.printf "MIXEL_NAMESPACE_BINDINGS directory=%u event=%u win32=%u\\n",(@$bp0 == ntdll!NtOpenDirectoryObject),(@$bp1 == ntdll!NtOpenEvent),(@$bp2 == KERNELBASE!OpenEventW)',
            # Keep the initial debug event stopped. The retained parent removes
            # the source thread's external suspension after verifying READY,
            # then queues exactly one fixed continuation through owned stdin.
            '.echo MIXEL_NAMESPACE_TRACE_READY']
    files = {name: '\n'.join(lines) + '\n' for name, lines in
             [('start.txt', init), ('winentry.txt', winentry), ('entry.txt', enter),
              ('return.txt', ret), ('winreturn.txt', winret)]}
    files.update(helpers)
    if any(len(text.replace('\n',';\n').encode('ascii')) > 4096 for text in files.values()):
        raise ValueError('Condensed CDB command block exceeds4096 characters')
    return files, dict(enumerate(paths, 1))

def parse_trace(raw):
    if type(raw) is not str or len(raw.encode('utf-8')) > 1048576:
        raise ValueError('Bounded private trace exceeds1MiB')
    records, stage, window, winreturn = [], 0, False, None
    for line in raw.splitlines():
        line = line.strip()
        # Only exact marker lines count. Script command echoes remain ignored.
        if not line.startswith('MIXEL_NAMESPACE_'):
            if re.search(r'(?i)(syntax error|couldn.t resolve|unable to insert breakpoint|no hardware breakpoint|hardware breakpoint limit)', line):
                raise ValueError('CDB command/binding failure')
            continue
        if line.startswith('MIXEL_NAMESPACE_TRACE_FAIL_'):
            raise ValueError('CDB bounded trace failed')
        if line == BINDINGS:
            if stage != 0: raise ValueError('Duplicate/out-of-order bindings')
            stage = 1
        elif line == READY:
            if stage != 1: raise ValueError('Ready lacks exact bindings')
            stage = 2
        elif line == 'MIXEL_NAMESPACE_TRACE_WINDOW':
            if stage != 2 or window: raise ValueError('Window is not first qualified source call')
            window = True
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int); parser.add_argument('--tid', type=int)
    parser.add_argument('--session', type=int); parser.add_argument('--event')
    parser.add_argument('--windows-folder'); parser.add_argument('--output', type=Path)
    parser.add_argument('--parse', type=Path)
    args = parser.parse_args()
    if args.parse:
        if args.parse.stat().st_size > 1048576: raise ValueError('Private log exceeds1MiB')
        result = parse_trace(args.parse.read_text(encoding='utf-8'))
        print(json.dumps(result, separators=(',', ':')))
        return
    if None in (args.pid, args.tid, args.session, args.event, args.windows_folder, args.output):
        parser.error('All generator identity/folder/output fields are required')
    files, paths = commands(args.pid, args.tid, args.session, args.event, args.windows_folder)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, text in files.items():
        target = args.output / name; target.write_text(text); target.chmod(0o600)
    scope = {'scope': 'Source-only debugger evidence; never a signed release/readiness gate', 'maximumRecords': 32,
             'pathKind': paths, 'requires': 'retained owned suspended PID/TID/SID + non-elevated same-session identity first; actual bindings/READY before one owned ResumeThread then one fixed owned-stdin g continuation; bounded deadline/private1MiB raw log; exact Win32 result + qd/owned cleanup; no public artifact/upload'}
    target = args.output / 'DIAGNOSTIC-SCOPE.json'; target.write_text(json.dumps(scope, indent=2) + '\n'); target.chmod(0o600)
    print('PASS: bounded hardware diagnostic commands generated; actual CDB execution is a separate gate')

if __name__ == '__main__':
    main()
