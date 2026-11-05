"""Private source-only hardware-control build and bounded PE qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

PROBE = 'mixel_namespace_trace_hw_probe'
MARKER = '        let status = support_invite_lease_status();\n        let raw_error = mixel_support_lease::fixture_raw_open_error();'
CAPTURE = '        let proof = support_invite_attestation();'

def diagnostic_source(source, gate, nonce, gated, attach_after_main_barrier=False):
    if type(source) is not str or len(source.encode('utf-8')) > 1048576 or source.count(MARKER) != 1 or source.count(CAPTURE) != 1 or PROBE in source:
        raise ValueError('Exact private source extraction boundary changed')
    if type(gated) is not bool or not re.fullmatch('[0-9a-f]{64}', nonce) or not gate or any(c in gate for c in '\r\n\0'):
        raise ValueError('Private timing gate identity is invalid')
    if type(attach_after_main_barrier) is not bool or (attach_after_main_barrier and not gated):
        raise ValueError('Post-main attachment requires the separate gated diagnostic mode')
    barrier = ''
    if gated:
        barrier = '''        assert_eq!(mode, "probe", "Private trace barrier requires probe mode");
        let trace_gate = std::path::Path::new(GATE_LITERAL);
        let trace_deadline = std::time::Instant::now() + std::time::Duration::from_secs(45);
        loop {
            if let Ok(metadata) = std::fs::symlink_metadata(trace_gate) {
                assert!(metadata.is_file() && !metadata.file_type().is_symlink() && metadata.len() <= 64, "Invalid private trace gate");
                if std::fs::read_to_string(trace_gate).unwrap() == NONCE_LITERAL { break; }
            }
            assert!(std::time::Instant::now() < trace_deadline, "Private trace gate timed out");
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
'''.replace('GATE_LITERAL', json.dumps(gate)).replace('NONCE_LITERAL', json.dumps(nonce))
        if attach_after_main_barrier:
            # Separate private witness only; the original guard/error capture stays exact.
            witness = '''        let trace_barrier_tid = {
            #[link(name = "kernel32")]
            extern "system" { fn GetCurrentThreadId() -> u32; }
            unsafe { GetCurrentThreadId() }
        };
        let trace_barrier_proof = format!("{{\\"pid\\":{},\\"tid\\":{},\\"barrierNonce\\":\\"{}\\"}}", std::process::id(), trace_barrier_tid, NONCE_LITERAL);
        let mut trace_barrier_file = std::fs::OpenOptions::new().write(true).create_new(true).open("fixture-main-barrier.new").unwrap();
        std::io::Write::write_all(&mut trace_barrier_file, trace_barrier_proof.as_bytes()).unwrap();
        drop(trace_barrier_file);
        std::fs::rename("fixture-main-barrier.new", "fixture-main-barrier.json").unwrap();
'''.replace('NONCE_LITERAL', json.dumps(nonce))
            barrier = barrier.replace('        loop {\n', witness + '        loop {\n', 1)
    source = source.replace(MARKER, barrier + f'        let trace_probe_fn: extern \"C\" fn() -> u32 = std::hint::black_box({PROBE} as extern \"C\" fn() -> u32);\n        let trace_probe_tid = trace_probe_fn();\n' + MARKER, 1)
    # Supplemental file is written only after the unchanged immediate raw-error
    # capture. It never changes either original result or namespace schema.
    supplemental = '''        let trace_probe_rva = {
            #[link(name = "kernel32")]
            extern "system" { fn GetModuleHandleW(name: *const u16) -> *mut std::ffi::c_void; }
            let main_base = unsafe { GetModuleHandleW(std::ptr::null()) } as usize;
            if main_base == 0 { 0 } else {
                (trace_probe_fn as usize).checked_sub(main_base)
                    .filter(|rva| *rva > 0 && *rva < 268435456)
                    .map(|rva| rva as u32).unwrap_or(0)
            }
        };
        let trace_probe_proof = format!("{{\\"pid\\":{},\\"tid\\":{},\\"probeRva\\":{}}}", std::process::id(), trace_probe_tid, trace_probe_rva);
        std::fs::write("fixture-hwcontrol.new", trace_probe_proof).unwrap();
        std::fs::rename("fixture-hwcontrol.new", "fixture-hwcontrol.json").unwrap();
'''
    source = source.replace(CAPTURE, supplemental + CAPTURE, 1)
    source += f'''
#[cfg(windows)]
#[no_mangle]
#[inline(never)]
pub extern "C" fn {PROBE}() -> u32 {{
    #[link(name = "kernel32")]
    extern "system" {{ fn GetCurrentThreadId() -> u32; }}
    unsafe {{ GetCurrentThreadId() }}
}}
'''
    return source

def qualify_pe(data):
    if type(data) is not bytes or not 128 <= len(data) <= 16777216 or data[:2] != b'MZ':
        raise ValueError('Expected a bounded private PE executable')
    def u16(at):
        if not 0 <= at <= len(data)-2: raise ValueError('PE WORD outside file')
        return struct.unpack_from('<H', data, at)[0]
    def u32(at):
        if not 0 <= at <= len(data)-4: raise ValueError('PE DWORD outside file')
        return struct.unpack_from('<I', data, at)[0]
    pe = u32(0x3c)
    if not 64 <= pe <= len(data)-144 or data[pe:pe+4] != b'PE\0\0' or pe % 4:
        raise ValueError('PE signature is outside its bounded header')
    count, optional_size, flags = u16(pe+6), u16(pe+20), u16(pe+22)
    optional = pe+24
    if u16(pe+4) != 0x8664 or not 1 <= count <= 96 or not 120 <= optional_size <= 4096 or optional+optional_size+40*count > len(data) or flags & 0x2102 != 2:
        raise ValueError('Expected a bounded AMD64 executable image')
    if u16(optional) != 0x20b or u16(optional+68) != 3 or not 1 <= u32(optional+108) <= 16 or optional_size < 112+8*u32(optional+108):
        raise ValueError('Expected exact console PE32+ directories')
    entry, image_size, headers = u32(optional+16), u32(optional+56), u32(optional+60)
    if not optional+optional_size+40*count <= headers <= min(len(data),65536) or not headers < image_size <= 268435456 or not 0 < entry < image_size:
        raise ValueError('PE image/header/entry bounds are invalid')
    sections=[]
    for i in range(count):
        at=optional+optional_size+40*i
        virtual_size, virtual, raw_size, raw, characteristics = (u32(at+n) for n in (8,12,16,20,36))
        span=max(virtual_size,raw_size)
        if not span or virtual < headers or virtual+span > image_size or (raw_size and (raw < headers or raw+raw_size > len(data))):
            raise ValueError('PE section is outside image/file bounds')
        for other in sections:
            if max(virtual,other['virtual']) < min(virtual+span,other['virtual']+other['span']) or (raw_size and other['rawSize'] and max(raw,other['raw']) < min(raw+raw_size,other['raw']+other['rawSize'])):
                raise ValueError('PE sections overlap')
        sections.append(dict(virtual=virtual,span=span,rawSize=raw_size,raw=raw,flags=characteristics,header=at))
    def mapped(rva,size):
        if not 0 < rva < image_size or not 0 < size <= 65536 or rva+size > image_size: raise ValueError('PE RVA span invalid')
        owners=[s for s in sections if s['virtual'] <= rva and rva+size <= s['virtual']+s['rawSize']]
        if len(owners) != 1: raise ValueError('PE RVA is not uniquely raw-backed')
        s=owners[0];return s['raw']+rva-s['virtual'],s
    def executable(rva):
        _,s=mapped(rva,1)
        if s['flags'] & 0xe0000020 != 0x60000020: raise ValueError('PE control is not readable non-writable executable code')
        return s
    entry_section=executable(entry)
    export, export_size = u32(optional+112), u32(optional+116)
    if not 40 <= export_size <= 65536: raise ValueError('PE export directory size invalid')
    exp,_=mapped(export,export_size)
    functions,names=u32(exp+20),u32(exp+24)
    if u32(exp) != 0 or u32(exp+16) != 1 or not 1 <= names <= functions <= 128: raise ValueError('PE export counts invalid')
    eat,_=mapped(u32(exp+28),4*functions);npt,_=mapped(u32(exp+32),4*names);ordinals,_=mapped(u32(exp+36),2*names)
    # Exactly one known named export and one address eliminate ordinal aliases,
    # duplicates and arbitrary symbols in this disposable diagnostic build.
    if names != 1 or functions != 1: raise ValueError('Private PE has unexpected/duplicate exports')
    name_at,_=mapped(u32(npt),len(PROBE)+1)
    if data[name_at:name_at+len(PROBE)+1] != PROBE.encode('ascii')+b'\0' or u16(ordinals) != 0:
        raise ValueError('Private PE lacks its exact ASCII export')
    module_rva=u32(exp+12);module_at,_=mapped(module_rva,1)
    module_end=data.find(b'\0',module_at,min(module_at+129,len(data)))
    if module_end <= module_at or any(c < 32 or c > 126 for c in data[module_at:module_end]): raise ValueError('PE module name is malformed/unbounded')
    metadata=[(export,40),(u32(exp+28),4),(u32(exp+32),4),(u32(exp+36),2),(u32(npt),len(PROBE)+1),(module_rva,module_end-module_at+1)]
    for i,(rva,size) in enumerate(metadata):
        mapped(rva,size)
        if not export <= rva < rva+size <= export+export_size: raise ValueError('PE export metadata escapes declared directory')
        if any(max(rva,other) < min(rva+size,other+length) for other,length in metadata[:i]): raise ValueError('PE export structures overlap')
    probe=u32(eat)
    if export <= probe < export+export_size or probe == entry: raise ValueError('Private probe is forwarded or aliases the image entry')
    probe_section=executable(probe)
    return {'entryRva':entry,'probeRva':probe,'imageSize':image_size,'peOffset':pe,'optionalSize':optional_size,'sectionCount':count,'coffFlags':flags,
            'exportRva':export,'exportSize':export_size,'headersSize':headers,'probeSectionVirtualSize':u32(probe_section['header']+8),'probeSectionRawSize':probe_section['rawSize'],
            'probeSectionHeader':probe_section['header'],'probeSectionRva':probe_section['virtual'],'probeSectionSpan':probe_section['span'],
            'probeSectionFlags':probe_section['flags'],'entrySectionRva':entry_section['virtual'],'exeSHA256':hashlib.sha256(data).hexdigest()}

def qualify_file(path, expected_sha=None):
    if not isinstance(path,Path) or not path.is_file() or path.is_symlink() or path.suffix.lower() != '.exe' or not 128 <= path.stat().st_size <= 16777216:
        raise ValueError('Private executable file type/size/path is invalid')
    pe=qualify_pe(path.read_bytes())
    if expected_sha is not None and (type(expected_sha) is not str or not re.fullmatch('[0-9a-fA-F]{64}',expected_sha) or pe['exeSHA256'] != expected_sha.lower()):
        raise ValueError('Qualified PE hash differs from retained executable')
    return pe

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--gate',required=True)
    parser.add_argument('--nonce',required=True);parser.add_argument('--gated',action='store_true')
    parser.add_argument('--attach-after-main-barrier',action='store_true')
    args=parser.parse_args()
    if os.name != 'nt': raise ValueError('Private probe compilation requires actual Windows')
    from rust_toolchain import rustc_command
    if not args.source.is_absolute() or not args.source.is_file() or args.source.is_symlink() or args.source.suffix != '.rs' or args.source.stat().st_size > 1048576:
        raise ValueError('Private source file type/size/path invalid')
    original=args.source.read_text(encoding='utf-8');result=diagnostic_source(original,args.gate,args.nonce,args.gated,args.attach_after_main_barrier)
    args.source.write_text(result,encoding='utf-8',newline='\n')
    compiler=os.environ.get('RUSTC_BIN') or shutil.which('rustc')
    if not compiler: raise ValueError('Qualified Rust compiler is missing')
    command=[compiler,'--edition','2021','-D','warnings','-C','opt-level=0','-C','lto=no','-C','link-arg=/OPT:NOICF','-C','link-arg=/EXPORT:'+PROBE,str(args.source),'-o',str(args.source.with_suffix('.exe'))]
    subprocess.run(rustc_command(command),check=True,timeout=90,cwd=args.source.parent)
    qualified = qualify_file(args.source.with_suffix('.exe'))
    # Private parent bridge; never print this metadata in the public CI facts.
    print(json.dumps({key: qualified[key] for key in ('probeRva','imageSize','exeSHA256')}, separators=(',', ':')))

if __name__=='__main__':main()
