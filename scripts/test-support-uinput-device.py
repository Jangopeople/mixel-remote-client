#!/usr/bin/env python3
"""Compile literal setup/error code against a real C ABI mock; never create a device."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('device', HERE / 'patch-support-uinput-device.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

C = r'''
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
struct input_id { uint16_t bus, vendor, product, version; };
struct setup { struct input_id id; char name[80]; uint32_t ff_effects_max; };
struct absinfo { int32_t value, minimum, maximum, fuzz, flat, resolution; };
struct abssetup { uint16_t code; struct absinfo info; };
_Static_assert(sizeof(struct setup)==92, "Linux uinput_setup size");
_Static_assert(sizeof(struct abssetup)==28, "Linux abs_setup size");
static int count, failure, positive, fd_value, bad;
static const unsigned long requests[18] = {
 1074025828UL,1074025829UL,1074025829UL,1074025829UL,1074025829UL,1074025829UL,
 1074025828UL,1074025831UL,1075598596UL,1074025831UL,1075598596UL,
 1074025828UL,1074025830UL,1074025830UL,1074025830UL,1074025830UL,
 1079792899UL,21761UL};
static const int integers[18]={1,0x110,0x111,0x112,0x113,0x114,3,0,0,1,0,2,0,1,8,6,0,0};
void mock_reset(int fail_at,int return_positive) { count=bad=0;failure=fail_at;positive=return_positive;fd_value=-1;errno=0; }
int mock_count(void){return count;}
int mock_bad(void){return bad;}
int mock_fd(void){return fd_value;}
int mock_fd_closed(void){errno=0;return fcntl(fd_value,F_GETFD)==-1 && errno==EBADF;}
int mock_failure_errno(void){return EBUSY;}
int ioctl(int fd,unsigned long request,...) {
 if(fcntl(fd,F_GETFD)==-1)bad=1;
 if(fd_value==-1)fd_value=fd;
 if(fd_value!=fd)bad=1;
 if(request==21762UL){count++;errno=EDOM;return -1;}
 count++;
 if(count>18 || request!=requests[count-1]){bad=1;errno=EINVAL;return -1;}
 va_list args;va_start(args,request);
 if(request==1075598596UL) {
  const struct abssetup *p=va_arg(args,const struct abssetup *);
  int axis=count==9?0:1,max=count==9?1024:768;
  if(p->code!=axis || p->info.minimum!=0 || p->info.maximum!=max || p->info.value!=0 || p->info.fuzz!=0 || p->info.flat!=0 || p->info.resolution!=0)bad=1;
 } else if(request==1079792899UL) {
  const struct setup *p=va_arg(args,const struct setup *);
  if(p->id.bus!=3 || p->id.vendor!=0x2222 || p->id.product!=0x3333 || p->id.version!=0 || p->ff_effects_max!=0 || strcmp(p->name,"mouce-library-fake-mouse")!=0)bad=1;
 } else if(request!=21761UL) {
  if(va_arg(args,int)!=integers[count-1])bad=1;
 }
 va_end(args);
 if(count==failure){errno=EBUSY;return -1;}
 return positive?1:0;
}
'''

RUST = r'''
extern "C" {fn mock_reset(fail_at:i32,positive:i32);fn mock_count()->i32;fn mock_bad()->i32;fn mock_fd()->i32;fn mock_fd_closed()->i32;fn mock_failure_errno()->i32;}
fn anchors() {
 let _=mouce::UInputMouseManager::move_to as fn(&mouce::UInputMouseManager,usize,usize)->std::io::Result<()>;
 let _=mouce::UInputMouseManager::move_relative as fn(&mouce::UInputMouseManager,i32,i32)->std::io::Result<()>;
 let _=mouce::UInputMouseManager::press_button as fn(&mouce::UInputMouseManager,&mouce::MouseButton)->std::io::Result<()>;
 let _=mouce::UInputMouseManager::release_button as fn(&mouce::UInputMouseManager,&mouce::MouseButton)->std::io::Result<()>;
 let _=mouce::UInputMouseManager::click_button as fn(&mouce::UInputMouseManager,&mouce::MouseButton)->std::io::Result<()>;
 let _=mouce::UInputMouseManager::scroll_wheel_horizontal as fn(&mouce::UInputMouseManager,i32)->std::io::Result<()>;
 let _=mouce::UInputMouseManager::scroll_wheel_vertical as fn(&mouce::UInputMouseManager,i32)->std::io::Result<()>;
 let _=[mouce::MouseButton::Left,mouce::MouseButton::Middle,mouce::MouseButton::Right,mouce::MouseButton::Side,mouce::MouseButton::Extra,mouce::MouseButton::Back,mouce::MouseButton::Forward,mouce::MouseButton::Task];
}
fn main() {
 anchors();let mode=std::env::args().nth(1).unwrap();
 if mode=="abi" {assert_eq!(mouce::fixture_setup_abi(),(92,4,88,4));println!("PASS exact Linux uinput_setup C size/align/ff offset/width");return;}
 if mode=="old-failure" {
  unsafe{mock_reset(1,0)};
  let result=mouce::UInputMouseManager::new((0,1024),(0,768));
  assert!(result.is_err(),"first failed capability must propagate");return;
 }
 for failed in 1..=18 {
  unsafe{mock_reset(failed,0)};
  let result=mouce::UInputMouseManager::new((0,1024),(0,768));
  let error=match result {Err(e)=>e,Ok(_)=>panic!("failed ioctl ignored")};
  unsafe{assert_eq!(error.raw_os_error(),Some(mock_failure_errno()));assert_eq!(mock_count(),failed+1);assert_eq!(mock_bad(),0);assert!(mock_fd()>=0);assert_eq!(mock_fd_closed(),1);}
 }
 for positive in [0,1] {
  unsafe{mock_reset(0,positive)};
  let manager=mouce::UInputMouseManager::new((0,1024),(0,768)).unwrap();
  unsafe{assert_eq!(mock_count(),18);assert_eq!(mock_bad(),0);}
  drop(manager);
  unsafe{assert_eq!(mock_count(),19);assert_eq!(mock_fd_closed(),1);}
 }
 println!("PASS all18 setup ioctl failure positions preserve immediate EBUSY across failing Drop; same owned fd closed; exact successful arguments/order and nonnegative success");
}
'''

BUTTON_C = '\n#include <fcntl.h>\n#include <errno.h>\n#include <stdarg.h>\nstatic int buttons[5], count, fail_bit, observed_fd;\nvoid mock_reset(void){count=0;fail_bit=0;observed_fd=-1;for(int i=0;i<5;i++)buttons[i]=0;}\nvoid mock_fail(int bit){fail_bit=bit;}\nint mock_errno(void){return EBUSY;}\nint mock_closed(void){errno=0;return fcntl(observed_fd,F_GETFD)==-1 && errno==EBADF;}\nint mock_button(int n){return buttons[n];}\nint mock_count(void){return count;}\nint ioctl(int fd,unsigned long request,...){\n if(fcntl(fd,F_GETFD)==-1)return -1;\n observed_fd=fd;\n if(request==21762UL){errno=EDOM;return -1;}\n if(request==1074025829UL){\n  va_list args;va_start(args,request);int value=va_arg(args,int);va_end(args);\n  if(value<0x110||value>0x114)return -1;\n  buttons[value-0x110]++;count++;\n  if(value==fail_bit){errno=EBUSY;return -1;}\n }\n return 0;\n}\n'
BUTTON_RUST = '\nextern "C" {fn mock_reset();fn mock_button(n:i32)->i32;fn mock_count()->i32;fn mock_fail(bit:i32);fn mock_errno()->i32;fn mock_closed()->i32;}\nfn main(){\n let mode=std::env::args().nth(1).unwrap();let required=mode=="required";\n if mode=="errors"{for bit in [0x113,0x114]{unsafe{mock_reset();mock_fail(bit);}\n  let error=match mouce::UInputMouseManager::new((0,1),(0,1)){Err(e)=>e,Ok(_)=>panic!("new capability error ignored")};\n  unsafe{assert_eq!(error.raw_os_error(),Some(mock_errno()));assert_eq!(mock_closed(),1);}\n }println!("PASS new Side/Extra capability errors capture EBUSY before failing Drop and close exact ownedfd");return;}\n let file=std::env::var("MIXEL_BUTTON_FIXTURE_FILE").unwrap();\n let cases=[(enigo::MouseButton::Left,0x110),(enigo::MouseButton::Middle,0x112),\n  (enigo::MouseButton::Right,0x111),(enigo::MouseButton::Back,0x113),(enigo::MouseButton::Forward,0x114)];\n for (index,(button,code)) in cases.iter().enumerate(){\n  for action in 0..3{\n   std::fs::write(&file,[]).unwrap();unsafe{mock_reset()};\n   let mut mouse=mouce::UInputMouseManager::new((0,1024),(0,768)).unwrap();\n   let data=match action{0=>DataMouse::Down(*button),1=>DataMouse::Up(*button),_=>DataMouse::Click(*button)};\n   handle_mouse(&mut mouse,&data);\n   drop(mouse);\n   let raw=std::fs::read(&file).unwrap();assert_eq!(raw.len()%24,0);\n   let actual:Vec<_>=raw.chunks_exact(24).map(|r|(u16::from_ne_bytes([r[16],r[17]]),u16::from_ne_bytes([r[18],r[19]]),i32::from_ne_bytes([r[20],r[21],r[22],r[23]]))).collect();\n   let values=match action{0=>vec![1],1=>vec![0],_=>vec![1,0]};\n   let expected:Vec<_>=values.into_iter().flat_map(|v|[(1,*code,v),(0,0,0)]).collect();\n   if index<3||required{assert_eq!(actual,expected,"action {action} button {button:?}");}\n   else{assert!(actual.is_empty(),"old unsupported actions must drop unchanged");}\n   unsafe{assert_eq!(mock_count(),if required{5}else{3});for n in 0..5{assert_eq!(mock_button(n),if n<3||required{1}else{0});}}\n  }\n }\n // Preserve explicitly unsupported scroll-button enum variants, plus Refresh.\n for button in [enigo::MouseButton::ScrollUp,enigo::MouseButton::ScrollDown,enigo::MouseButton::ScrollLeft,enigo::MouseButton::ScrollRight]{\n  for data in [DataMouse::Down(button),DataMouse::Up(button),DataMouse::Click(button)]{\n   std::fs::write(&file,[]).unwrap();let mut mouse=mouce::UInputMouseManager::new((0,1),(0,1)).unwrap();handle_mouse(&mut mouse,&data);drop(mouse);assert!(std::fs::read(&file).unwrap().is_empty());\n  }\n }\n // Typed references preserve all original private enum values and methods.\n let _=[mouce::MouseButton::Side,mouce::MouseButton::Extra,mouce::MouseButton::Back,mouce::MouseButton::Forward,mouce::MouseButton::Task];\n let _=[DataMouse::MoveTo(0,0),DataMouse::MoveRelative(0,0),DataMouse::ScrollX(0),DataMouse::ScrollY(0),DataMouse::Refresh];\n println!("PASS literal actual enigo action routing, three ordinary buttons and Back/Forward down/up/click; exact signed EV_KEY/SYN owned-file records; five capability bits; unsupported variants conserved");\n}\n'

def run(args, out, name, env=None, expected=0):
    r = subprocess.run([str(x) for x in args], env=env, capture_output=True, timeout=60)
    (out / (name+'.stdout')).write_bytes(r.stdout)
    (out / (name+'.stderr')).write_bytes(r.stderr)
    (out / (name+'.json')).write_text(json.dumps({'arguments':[str(x) for x in args], 'returncode':r.returncode}, indent=2)+'\n')
    assert r.returncode == expected, r.stderr.decode(errors='replace')[-4000:]
    return r


def source_controls(sources, out):
    count = 0
    for number, original in enumerate(sources):
        for newline in [b'\n', b'\r\n']:
            data=original.replace(b'\n',newline)
            fixed=P.transform(data)
            assert P.transform(fixed)==fixed and P.transform(fixed,True)==data
            repo=out/('materialization-'+str(number)+'-'+str(len(newline)));target=repo/P.TARGET;target.parent.mkdir(parents=True);target.write_bytes(data)
            P.apply(repo,check=True);assert target.read_bytes()==data
            P.apply(repo);assert target.read_bytes()==fixed
            P.apply(repo);assert target.read_bytes()==fixed
            P.apply(repo,restore=True,check=True);assert target.read_bytes()==fixed
            P.apply(repo,restore=True);assert target.read_bytes()==data
            # A constructor-only or button-only layer is not a qualified combined
            # state. Every CLI mutation/check variant must refuse before write.
            device_only=fixed.replace(P.EXTRA_CAPABILITIES.encode().replace(b'\n',newline),b'',1).replace(P.EXTRA_MAP.encode().replace(b'\n',newline),b'')
            button_only=data.replace(b'                ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE);'+newline,b'                ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE);'+newline+P.EXTRA_CAPABILITIES.encode().replace(b'checked_ioctl!(',b'ioctl(').replace(b'\n',newline),1).replace(P.MAP.encode().replace(b'\n',newline),(P.MAP+P.EXTRA_MAP).encode().replace(b'\n',newline))
            for bad in [device_only,button_only,fixed.replace(P.EXTRA_CAPABILITIES.encode().replace(b'\n',newline),b'',1),fixed.replace(P.EXTRA_MAP.encode().replace(b'\n',newline),b'',1),data+b'// unrelated\n',fixed+b'// unrelated\n',fixed.replace(b'ff_effects_max: c_uint',b'ff_effects_max: u16'),fixed.replace(b'checked_ioctl!(fd, UI_DEV_CREATE)',b'ioctl(fd, UI_DEV_CREATE)'),fixed.replace(b'return Err(Error::last_os_error());',b'return Ok(manager);')]:
                target.write_bytes(bad)
                before=target.read_bytes()
                for restore,check in [(False,False),(False,True),(True,False),(True,True)]:
                    try:P.apply(repo,check=check,restore=restore)
                    except RuntimeError:pass
                    else:raise AssertionError('bad source accepted')
                    assert target.read_bytes()==before;count+=1
    print('PASS eight LF/CRLF whole-source apply/restore/check/idempotence; '+str(count)+' strict zero-write source refusals')


def ownership_controls(original, out):
    base=out/'ownership';base.mkdir()
    real=base/'real';target=real/P.TARGET;target.parent.mkdir(parents=True);target.write_bytes(original)
    external=base/'external';external.write_bytes(original)
    def refuse(root):
        before=external.read_bytes()
        try:P.apply(root)
        except RuntimeError:pass
        else:raise AssertionError('unsafe materialization accepted')
        assert external.read_bytes()==before
    root_link=base/'root-link';root_link.symlink_to(real,target_is_directory=True);refuse(root_link)
    target.unlink();target.symlink_to(external);refuse(real);target.unlink()
    os.link(external,target);refuse(real);assert external.stat().st_nlink==2;target.unlink()
    target.write_bytes(original)
    linked_server=base/'server-real';target.parent.rename(linked_server);target.parent.symlink_to(linked_server,target_is_directory=True);refuse(real)
    target.parent.unlink();linked_server.rename(target.parent)
    # Model the Windows public FILE_ATTRIBUTE_REPARSE_POINT for root, parent,
    # and target without pretending this host can create a Windows junction.
    actual_lstat=Path.lstat
    for flagged in [real,target.parent,target]:
        def modeled_lstat(path, flagged=flagged):
            info=actual_lstat(path)
            return SimpleNamespace(st_mode=info.st_mode,st_nlink=info.st_nlink,st_file_attributes=0x400 if path==flagged else 0)
        before=target.read_bytes()
        with patch.object(Path,'lstat',modeled_lstat):refuse(real)
        assert target.read_bytes()==before
    print('PASS four actual symlink/hardlink ownership refusals and three Windows-reparse models; all bytes unchanged')


def button_controls(args, original, corrected, out):
    def git(path):
        return subprocess.check_output(['git','-C',str(args.source),'show',P.PIN+':'+path]).decode()
    enum='#[derive(Debug,Clone,Copy)]\n'+P.block(git('libs/enigo/src/lib.rs'),'pub enum MouseButton {')
    data='#[derive(Debug)]\n'+P.block(git('src/ipc.rs'),'pub enum DataMouse {')
    baseline=corrected.replace(P.EXTRA_CAPABILITIES,'',1).replace(P.EXTRA_MAP,'')
    # Constructor-only baseline isolates the Back/Forward routing defect;
    # its 16 checked calls and __u32 field are preserved from the prior stage.
    assert P.block(baseline,'fn handle_mouse(')==P.block(original,'fn handle_mouse(')
    assert baseline.count('checked_ioctl!(')==16
    (out/'button-mock.c').write_text(BUTTON_C)
    run(['cc','-std=c11','-Wall','-Wextra','-Werror','-c',out/'button-mock.c','-o',out/'button-mock.o'],out,'button-c-compile')
    file=out/'owned-button-events';file.write_bytes(b'')
    env=dict(os.environ,MIXEL_BUTTON_FIXTURE_FILE=str(file))
    for label,text in [('baseline',baseline),('corrected',corrected)]:
        module=P.block(text,'mod mouce {')
        assert module.count('.open("/dev/uinput")?')==1
        module=module.replace('.open("/dev/uinput")?','.open(std::env::var("MIXEL_BUTTON_FIXTURE_FILE").unwrap())?',1)
        handler=P.block(text,'fn handle_mouse(')
        (out/(label+'-literal-handler.rs')).write_text(handler)
        assert handler.count('        log::trace!("handle_mouse {:?}", &data);\n')==1
        handler=handler.replace('        log::trace!("handle_mouse {:?}", &data);\n','',1)
        witness='mod enigo {\n'+enum+'\n}\n'+data+'\nmacro_rules! allow_err {($e:expr)=>{{let _=$e;}};}\n'+module+'\n'+handler+'\n'+BUTTON_RUST
        path=out/(label+'-buttons.rs');path.write_text(witness)
        for profile,flags in [('debug',[]),('release',['-O'])]:
            binary=out/(label+'-buttons-'+profile+'.bin')
            run([args.rustc,'--edition=2021','-D','warnings',path,*flags,'-C','link-arg='+str(out/'button-mock.o'),'-o',binary],out,label+'-buttons-'+profile+'-compile')
            run([binary,'required' if label=='corrected' else 'baseline'],out,label+'-buttons-'+profile+'-routing',env)
            if label=='baseline':
                run([binary,'required'],out,label+'-buttons-'+profile+'-dropped-back-negative',env,101)
            else:
                run([binary,'errors'],out,label+'-buttons-'+profile+'-capability-errors',env)
        run([args.rustc,'--edition=2021','--target','x86_64-unknown-linux-gnu','--sysroot',args.linux_sysroot,'--emit','metadata','-D','warnings',path,'-o',out/(label+'-buttons.rmeta')],out,label+'-buttons-linux-metadata')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--wheel-helper',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rustc',type=Path,required=True)
    parser.add_argument('--linux-sysroot',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True)
    upstream=subprocess.check_output(['git','-C',str(args.source),'show',P.PIN+':'+P.TARGET])
    spec=importlib.util.spec_from_file_location('wheel',args.wheel_helper);W=importlib.util.module_from_spec(spec);spec.loader.exec_module(W)
    branded=upstream.replace(b'RustDesk UInput Keyboard',b'Mixel Remote UInput Keyboard')
    inputs=[upstream,branded,W.transform(upstream),W.transform(branded)]
    assert tuple(P.sha(x) for x in inputs)==P.BASE_HASHES
    source_controls(inputs,out)
    ownership_controls(inputs[3],out)
    original=inputs[3].decode();corrected=P.transform(inputs[3]).decode()
    assert P.sha(corrected.encode())=='688e38d8f37bff1b1da3dbbd791034e4b12f7b9845bd848f48fc47ea2307f099'
    assert P.inverse(corrected)==original
    for marker in ['fn emit(&self','fn syncronize(&self','pub fn scroll_wheel(&self','pub fn scroll_wheel_horizontal(&self','pub fn scroll_wheel_vertical(&self','impl Drop for UInputMouseManager','fn map_btn(','pub fn press_button(','pub fn release_button(','pub fn click_button(']:
        assert P.block(original,marker)==P.block(corrected,marker),marker
    (out/'mock.c').write_text(C);run(['cc','-std=c11','-Wall','-Wextra','-Werror','-c',out/'mock.c','-o',out/'mock.o'],out,'c-mock-compile')
    fixture=out/'owned-regular-file';fixture.write_bytes(b'')
    env=dict(os.environ,MIXEL_IOCTL_FIXTURE_FILE=str(fixture))
    observation='''
    pub fn fixture_setup_abi()->(usize,usize,usize,usize){
      let raw=std::mem::MaybeUninit::<UInputSetup>::uninit();let base=raw.as_ptr() as usize;
      (std::mem::size_of::<UInputSetup>(),std::mem::align_of::<UInputSetup>(),unsafe{std::ptr::addr_of!((*raw.as_ptr()).ff_effects_max) as usize-base},std::mem::size_of::<c_uint>())
    }
'''
    for label,text in [('original',original),('corrected',corrected)]:
        module=P.block(text,'mod mouce {')
        assert module.count('.open("/dev/uinput")?')==1
        module=module.replace('.open("/dev/uinput")?','.open(std::env::var("MIXEL_IOCTL_FIXTURE_FILE").unwrap())?',1)
        if label=='original':observation_source=observation.replace('std::mem::size_of::<c_uint>()','std::mem::size_of::<c_ulong>()')
        else:observation_source=observation
        witness=module[:-1]+observation_source+'}\n'+RUST
        path=out/(label+'.rs');path.write_text(witness)
        run([args.rustc,'--edition=2021','-D','warnings',path,'-C','link-arg='+str(out/'mock.o'),'-o',out/(label+'.bin')],out,label+'-compile')
        run([args.rustc,'--edition=2021','--target','x86_64-unknown-linux-gnu','--sysroot',args.linux_sysroot,'--emit','metadata','-D','warnings',path,'-o',out/(label+'.rmeta')],out,label+'-linux-metadata')
    run([out/'original.bin','abi'],out,'original-ABI-negative',env,101)
    run([out/'original.bin','old-failure'],out,'original-ignored-ioctl-negative',env,101)
    run([out/'corrected.bin','abi'],out,'corrected-ABI',env)
    run([out/'corrected.bin','checks'],out,'corrected-all18-errors',env)
    # Same literal native calls and errno lifetime under an optimized profile.
    run([args.rustc,'--edition=2021','-D','warnings',out/'corrected.rs','-O','-C','link-arg='+str(out/'mock.o'),'-o',out/'corrected-release.bin'],out,'corrected-release-compile')
    run([out/'corrected-release.bin','checks'],out,'corrected-release-all18-errors',env)
    button_controls(args,original,corrected,out)
    (out/'corrected-full-uinput.rs').write_text(corrected)
    (out/'conservation.json').write_text(json.dumps({'setup_field':'Linux __u32 width/alignment','checked_setup_ioctls':18,'permissions_changed':False,'wheel_emit_syn_drop_and_button_emitters_exact':True,'Back_Forward_to_Side_Extra_three_matches':True,'new_invalid_coordinate_policy':False,'native_kernel_qualification':False,'native_ioctls_mocked_by_real_C_ABI':True},indent=2)+'\n')
    print('PASS pinned full constructor C ABI controls; original ABI/ignored failure101; corrected all18 errors; Linux metadata -Dwarnings; wheel/rights unchanged; two exact button routes and literal actual enums/handler debug+release records pass; kernel loopback is separate')

if __name__=='__main__':main()
