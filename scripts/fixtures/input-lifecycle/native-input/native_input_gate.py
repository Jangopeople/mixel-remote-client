#!/usr/bin/env python3
"""Run pinned production C components from a just-branded app, without OS input.
The output must not exist. Offline by default; --online permits Cargo downloads.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,time,platform
from source_io import read_inputs
from extract_session import extract as extract_session
from extract_receiver_components import extract as extract_receivers
P=Path(__file__).resolve().parent

def sha(data): return hashlib.sha256(data).hexdigest()
def method(text,signature):
    start=text.index(signature);begin=text.index('{',start);i=begin+1;depth=1;mode=None;escape=False
    while depth:
        c=text[i];n=text[i:i+2]
        if mode=='line':
            if c=='\n':mode=None
        elif mode=='string':
            if escape:escape=False
            elif c=='\\':escape=True
            elif c=='"':mode=None
        elif mode=='block':
            if n=='*/':mode=None;i+=1
        elif n=='//':mode='line';i+=1
        elif n=='/*':mode='block';i+=1
        elif c=='"':mode='string'
        elif c=='{':depth+=1
        elif c=='}':depth-=1
        i+=1
    return text[start:i]
def function(text,name):
    index=text.index('fn '+name);return method(text,text[text.rfind('\n',0,index)+1:index+len('fn '+name)])
def write(out,name,value): (out/'harness/src'/name).write_bytes(value.encode('utf-8'))
def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--source',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--cargo',default='cargo');a.add_argument('--rustc');a.add_argument('--online',action='store_true');a.add_argument('--prepare-only',action='store_true');args=a.parse_args()
    source=args.source.resolve();out=args.output.resolve();assert not out.exists(),'fresh output required'
    pins=json.loads((P/'SOURCE-PINS.json').read_text(encoding='utf-8'))
    inputs,raw_witnesses=read_inputs(source,pins)
    shutil.copytree(P/'template',out/'harness')
    for name,b in inputs.items():
        f=out/'candidate'/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b)
    (out/'harness/protos/message.proto').write_bytes(inputs['libs/hbb_common/protos/message.proto'])
    session_names=extract_session(out,inputs,method)
    keyboard=inputs['src/keyboard.rs'].decode();ui=inputs['src/ui_session_interface.rs'].decode();ffi=inputs['src/flutter_ffi.rs'].decode()
    names=['is_numpad_rdev_key','is_numpad_key','map_keyboard_mode','_map_keyboard_mode','try_fill_unicode','translate_keyboard_mode','convert_numpad_keys','legacy_keyboard_mode_for_peer','native_modifier_snapshot']
    write(out,'native_conversion_selected.rs','\n\n'.join(function(keyboard,n)for n in names)+'\n')
    write(out,'native_legacy_client_selected.rs','\n\n'.join(function(keyboard,n)for n in ['get_modifiers_state','legacy_modifiers','event_lock_screen'])+'\n')
    write(out,'native_cleanup_selected.rs','\n\n'.join(function(keyboard,n)for n in ['release_remote_keys','process_native_event_with_session'])+'\n')
    write(out,'native_cleanup_client_selected.rs','\n\n'.join(function(keyboard,n)for n in ['process_release_events','process_release_events_with_session'])+'\n')
    write(out,'source_change_selected.rs',function(keyboard,'change_input_source')+'\n')
    write(out,'native_admission_selected.rs',function(keyboard,'process_event'))
    write(out,'native_ui_selected.rs',function(keyboard,'process_event_with_session')+'\n')
    cache_names=['is_long_press','update_modifiers_state','event_to_key_events','event_to_key_events_with_modifiers','is_letter_rdev_key','is_letter_rdev_key_ex','is_letter_key_4_lock_modes','parse_add_lock_modes_modifiers','add_lock_modes_modifiers','clear_input_source_state']
    write(out,'native_cache_selected.rs','\n\n'.join(function(keyboard,n)for n in cache_names)+'\n')
    methods=['session_handle_flutter_key_event','session_handle_flutter_raw_key_event','session_input_key','session_input_string','session_send_mouse','session_send_pointer','main_set_input_source']
    prefix='#[allow(unused_imports)]\nuse super::{sessions,SessionID,HashMap,SyncReturn};\nuse super::input::*;\nuse super::{change_input_source,try_sync_peer_option};\n'
    write(out,'entrypoints_candidate.rs',prefix+'\n\n'.join(function(ffi,n)for n in methods)+'\n')
    swap=method(ui,'    pub fn swap_modifier_key(')+'\n\n'+method(ui,'    fn swap_modifier_mouse(')+'\n';write(out,'modifier_swap_selected.rs',swap)
    text=(out/'harness/src/modifier_swap.template.rs').read_text(encoding='utf-8').replace('    include!("modifier_swap_selected.rs");',swap).replace('    include!("modifier_swap_original.rs");',(out/'harness/src/modifier_swap_original.rs').read_text(encoding='utf-8'))
    text+=(P/'template/src/modifier_swap.test-tail.rs').read_text(encoding='utf-8')
    write(out,'modifier_swap.rs',text)
    receiver_names=extract_receivers(out,inputs,method,function)
    extract_names=['session_methods.rs','remote_input_arms.rs','remote_permission_body.rs','remote_methods.rs','session_component.rs','native_conversion_selected.rs','native_legacy_client_selected.rs','native_cleanup_selected.rs','native_cleanup_client_selected.rs','source_change_selected.rs','native_admission_selected.rs','native_ui_selected.rs','native_cache_selected.rs','entrypoints_candidate.rs','modifier_swap_selected.rs','modifier_swap.rs']
    extract_names=sorted(set(extract_names+session_names+receiver_names))
    extraction={n:sha((out/'harness/src'/n).read_bytes())for n in extract_names}
    expected=json.loads((P/'CURRENT-EXTRACTIONS.json').read_text(encoding='utf-8'))
    assert extraction==expected,'extracted production components differ from reviewed final fixture'
    (out/'EXTRACTION.json').write_bytes((json.dumps({'productionCanonicalLFPins':pins,'productionRawWitnesses':raw_witnesses,'extractedSHA256':extraction,'mockSeams':'Session/registry/config/current-session/physical-key/layout/transport and server OS-effect recorders; actual protobuf, rdev pure tables and FRB1.80.1 executor; no OS input/network app session','negativeFixtures':'Pinned original/rejected production snippets; original persistent Scale receiver is a compatibility fixture, current balanced receiver is freshly extracted','portableAdapter':'Cargo.lock v4→v3; mock LazyLock→OnceLock mutex; macOS-only monitoring/helper fixture cfg; production bodies unchanged'},indent=2)+'\n').encode('utf-8'))
    if args.prepare_only:print('PASS: exact production pins and component extraction; no tests requested.');return
    env=os.environ.copy();env['RUSTFLAGS']='-D warnings';env['CARGO_TARGET_DIR']=str(out/'target')
    if args.rustc:
        env['RUSTC']=str(Path(args.rustc).resolve());env['RUSTDOC']=str(Path(args.rustc).resolve().with_name('rustdoc'))
    common=[args.cargo,'test','--locked','--manifest-path',str(out/'harness/Cargo.toml')]
    if not args.online:common.insert(2,'--offline')
    # Mac cases use exact macOS rdev native conversion; other desktop hosts run
    # common Session/Remote/FRB/source ledger tests, never pretend native coverage.
    counts=json.loads((P/'EXPECTED-COUNTS.json').read_text(encoding='utf-8'))[platform.system()]
    positive=counts['current'];negative=counts['requiredNegative']
    cases=[('current',common+['--','--test-threads=1'],0,positive,0),('original-normal',common+['--features','original-requirements','original_transport::normal_permitted_key_path','--','--test-threads=1'],0,1,0),('required-negatives',common+['--features','original-requirements','original_requirement','--','--test-threads=1'],101,0,negative),('mixed-controls',common+['--features','original-requirements','--','--test-threads=1'],101,positive+1,negative)]
    results=[];failed=False
    for label,argv,exitcode,passed,failed_count in cases:
        start=time.monotonic();r=subprocess.run(argv,env=env,capture_output=True)
        for stream,data in [('stdout',r.stdout),('stderr',r.stderr)]:(out/(label+'.'+stream)).write_bytes(data)
        expected=f'{passed} passed; {failed_count} failed; 0 ignored';ok=r.returncode==exitcode and expected in r.stdout.decode()
        result={'case':label,'argv':argv,'exit':r.returncode,'expectedExit':exitcode,'expectedSummary':expected,'pass':ok,'seconds':round(time.monotonic()-start,3),'stdoutSHA256':sha(r.stdout),'stderrSHA256':sha(r.stderr)};results.append(result);print(json.dumps(result),flush=True)
        if not ok:print(r.stdout.decode()[-3500:]+r.stderr.decode()[-3500:],flush=True);failed=True;break
    version=subprocess.check_output([env.get('RUSTC','rustc'),'--version']).decode().strip()
    result={'productionPinsSHA256':sha((P/'SOURCE-PINS.json').read_bytes()),'extractionSHA256':sha((out/'EXTRACTION.json').read_bytes()),'cargoVersion':subprocess.check_output([args.cargo,'--version']).decode().strip(),'rustcVersion':version,'platform':platform.system(),'rustflags':env['RUSTFLAGS'],'runs':results,'pass':not failed}
    (out/'RESULT.json').write_bytes((json.dumps(result,indent=2)+'\n').encode('utf-8'));assert not failed,'gate failed; see exact stdout/stderr'
    print(f'PASS: {positive} production component tests, original balanced control, {negative} required negative reproductions, mixed-controls; warnings denied.')
if __name__=='__main__':main()
