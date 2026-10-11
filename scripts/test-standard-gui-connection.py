#!/usr/bin/env python3
"""Pure source, mocked documented-API, and scoped workflow controls; no Windows calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gui_builder", ROOT / "scripts/build-standard-gui-connection-fixture.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

MOCKS = r'''
#[allow(non_snake_case)]
mod mock_native {
    use super::c_void;
    use std::sync::atomic::{AtomicU32, Ordering};
    pub static ERROR: AtomicU32 = AtomicU32::new(0);
    pub static LOADS: AtomicU32 = AtomicU32::new(0);
    pub static FREES: AtomicU32 = AtomicU32::new(0);
    pub static CALLS: AtomicU32 = AtomicU32::new(0);
    pub static VARIANT: AtomicU32 = AtomicU32::new(0);
    pub fn reset(variant: u32) { for value in [&ERROR,&LOADS,&FREES,&CALLS] { value.store(0,Ordering::SeqCst); } VARIANT.store(variant,Ordering::SeqCst); }
    pub unsafe fn LoadLibraryExW(name: *const u16, file: *mut c_void, flags: u32) -> *mut c_void {
        assert!(file.is_null()); assert_eq!(flags,0x800);
        let expected: Vec<u16> = "user32.dll".encode_utf16().chain(Some(0)).collect();
        for (index, value) in expected.iter().enumerate() { assert_eq!(*name.add(index), *value); }
        LOADS.fetch_add(1,Ordering::SeqCst);
        if VARIANT.load(Ordering::SeqCst)==1 { ERROR.store(126,Ordering::SeqCst); std::ptr::null_mut() } else { 17usize as _ }
    }
    pub unsafe fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void {
        assert_eq!(module as usize,17);
        let mut length=0; while *name.add(length)!=0 { length+=1; assert!(length<64); }
        let name=std::str::from_utf8(std::slice::from_raw_parts(name,length)).unwrap();
        if VARIANT.load(Ordering::SeqCst)==2 && name=="GetDesktopWindow" { ERROR.store(127,Ordering::SeqCst); return std::ptr::null_mut(); }
        match name {
            "GetDesktopWindow"=>DesktopWindow as *const () as _,
            "GetProcessWindowStation"=>Station as *const () as _,
            "GetThreadDesktop"=>Desktop as *const () as _,
            "GetUserObjectInformationW"=>Information as *const () as _,
            _=>panic!("Unexpected documented function"),
        }
    }
    unsafe extern "system" fn DesktopWindow()->*mut c_void { CALLS.fetch_add(1,Ordering::SeqCst); 31usize as _ }
    unsafe extern "system" fn Station()->*mut c_void { 41usize as _ }
    unsafe extern "system" fn Desktop(thread:u32)->*mut c_void { assert_eq!(thread,51); 61usize as _ }
    unsafe extern "system" fn Information(handle:*mut c_void,kind:i32,buffer:*mut c_void,size:u32,needed:*mut u32)->i32 {
        assert_eq!(kind,2); assert_eq!(size,512);
        let variant=VARIANT.load(Ordering::SeqCst);
        if variant==3 { *needed=1024; ERROR.store(122,Ordering::SeqCst); return 0; }
        if variant==4 { *needed=4; *(buffer as *mut u16)=0xd800; *(buffer as *mut u16).add(1)=0; return 1; }
        let name=if handle as usize==41 { "WinSta0" } else { assert_eq!(handle as usize,61); "Default" };
        let value:Vec<u16>=name.encode_utf16().chain(Some(0)).collect();
        *needed=(value.len()*2) as u32;
        std::ptr::copy_nonoverlapping(value.as_ptr(),buffer as *mut u16,value.len());1
    }
    pub unsafe fn FreeLibrary(handle:*mut c_void)->i32 {
        assert_eq!(handle as usize,17);
        let call=FREES.fetch_add(1,Ordering::SeqCst);
        if VARIANT.load(Ordering::SeqCst)==5 && call==0 { ERROR.store(6,Ordering::SeqCst); 0 } else { 1 }
    }
    pub unsafe fn GetLastError()->u32 { ERROR.load(Ordering::SeqCst) }
    pub unsafe fn GetCurrentThreadId()->u32 { 51 }
}
use mock_native::{LoadLibraryExW,GetProcAddress,FreeLibrary,GetLastError,GetCurrentThreadId};
'''

RUST_TESTS = r'''
    #[cfg(test)]
    mod tests {
        use super::*;
        use std::sync::atomic::Ordering;
        #[test] fn exact_load_query_names_and_no_double_free() {
            mock_native::reset(0);
            let(mut module,error)=Module::load();assert_eq!(error,0);assert!(module.loaded());
            assert_eq!(module.desktop_window(),(true,0,true));
            assert_eq!(mock_native::CALLS.load(Ordering::SeqCst),1);
            let names=module.names();assert!(names.contains("\"name\":\"WinSta0\""));assert!(names.contains("\"name\":\"Default\""));
            assert_eq!(module.close(),(true,0));assert_eq!(module.close(),(true,0));drop(module);
            assert_eq!(mock_native::FREES.load(Ordering::SeqCst),1);
        }
        #[test] fn load_and_resolution_failures_are_literal_and_owned_only() {
            mock_native::reset(1);let(mut module,error)=Module::load();assert_eq!(error,126);assert!(!module.loaded());
            assert_eq!(module.desktop_window(),(false,0,false));assert_eq!(module.close(),(true,0));drop(module);
            assert_eq!(mock_native::FREES.load(Ordering::SeqCst),0);
            mock_native::reset(2);let(module,_)=Module::load();assert_eq!(module.desktop_window(),(false,127,false));drop(module);
            assert_eq!(mock_native::CALLS.load(Ordering::SeqCst),0);assert_eq!(mock_native::FREES.load(Ordering::SeqCst),1);
        }
        #[test] fn error_and_malformed_name_bounds_are_preserved() {
            mock_native::reset(3);let(module,_)=Module::load();let names=module.names();assert!(names.contains("\"error\":122"));assert!(names.contains("\"length\":1024"));assert!(!names.contains("\"bounded\":true"));drop(module);
            mock_native::reset(4);let(module,_)=Module::load();let names=module.names();assert!(names.contains("\"name\":null"));assert!(!names.contains("\"bounded\":true"));drop(module);
            assert_eq!(quote("a\"\\\n"),"\"a\\\"\\\\\\u000a\"");
        }
        #[test] fn failed_free_retains_ownership_and_drop_retries_exact_handle() {
            mock_native::reset(5);let(mut module,_)=Module::load();assert_eq!(module.close(),(false,6));assert!(module.loaded());drop(module);
            assert_eq!(mock_native::FREES.load(Ordering::SeqCst),2);
        }
    }
'''


class GuiConnectionSourceTests(unittest.TestCase):
    @staticmethod
    def compiler():
        rustc=os.environ.get('RUSTC_BIN') or shutil.which('rustc')
        if not rustc:
            candidate=Path.home()/'.rustup/toolchains/1.75.0-aarch64-apple-darwin/bin/rustc'
            if candidate.is_file():rustc=str(candidate)
        if not rustc:raise AssertionError('Rust compiler required; no source control skip')
        return rustc

    def test_original_probe_and_standard_fixture_files_remain_bound(self):
        with tempfile.TemporaryDirectory(prefix="mixel-gui-source-") as temporary:
            root=Path(temporary)
            event="Global\\Mixel-Remote-Standard-Lease-"+"a"*32
            actual=builder.generated_source(root,event)
            spec=importlib.util.spec_from_file_location("lease_fixture",ROOT/"scripts/test-support-lease.py")
            lease=importlib.util.module_from_spec(spec);spec.loader.exec_module(lease)
            guard=(ROOT/builder.SOURCES[0]).read_text(encoding="utf-8")
            baseline=lease.standard_windows_fixture_body(guard,root,"mixel-gui-observe-"+"a"*32+"-",event)
            self.assertEqual(actual.replace(builder.GUI_HELPER+builder.MAIN,lease.STANDARD_WINDOWS_HARNESS,1),baseline)
            self.assertEqual(builder.MAIN.count('observe("before")'),1)
            self.assertLess(builder.MAIN.index('module.desktop_window()'),builder.MAIN.index('observe("after-call")'))
            self.assertLess(builder.MAIN.index('observe("after-call")'),builder.MAIN.index('module.names()'))
            for forbidden in ("hold_support_invite", "renew_support_invite", "CreateEvent", "SetProcessWindowStation", "SetThreadDesktop", "NtQuery", "Debugger"):
                self.assertNotIn(forbidden,builder.MAIN+builder.GUI_HELPER)

    def test_normal_documented_launch_and_ownership_extraction_is_exact(self):
        launch=(ROOT/builder.SOURCES[2]).read_text(encoding="utf-8")
        actual=builder.normal_class(launch)
        self.assertIn('diagnosticSuspended ? 4U : 0U',actual)
        self.assertIn('return StartStandardUser(executable, username, password, false);',actual)
        self.assertIn('StartupInfo startup = Startup(); ProcessInfo process;',actual)
        self.assertIn('PendingProcesses.Add(process.process)',actual)
        self.assertIn('StopOwnedProcessHandle',actual)
        self.assertIn('CloseStartedObservations',actual)
        for forbidden in ('NtQuery','Debugger','DuplicateHandle','ResumeThread','CreateProcessWithToken','LogonUser'):
            self.assertNotIn(forbidden,actual)
        changed=launch.replace('  static StartupInfo Startup()', '  static StartupInfo Renamed()',1)
        with self.assertRaises(ValueError):builder.normal_class(changed)

    def test_event_names_and_entry_upgrade_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            for event in ("Global\\Mixel-Remote-Attended-Runtime-v2", "Local\\Mixel-Remote-Standard-Lease-"+"a"*32, "Global\\Mixel-Remote-Standard-Lease-"+"A"*32, "Global\\Mixel-Remote-Standard-Lease-"+"a"*31):
                with self.subTest(event=event),self.assertRaises(ValueError):builder.generated_source(Path(temporary),event)
            original=(ROOT/builder.SOURCES[0]).read_text(encoding="utf-8").replace('const EVENT: &str =','const EVENT_DRIFT: &str =',1)
            with self.assertRaises(AssertionError):builder.generated_source(Path(temporary),'Global\\Mixel-Remote-Standard-Lease-'+'a'*32,original)

    def test_prepare_is_source_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);workspace=root/'workspace';evidence=root/'proof';workspace.mkdir();evidence.mkdir()
            files=builder.prepare(workspace,evidence,'Global\\Mixel-Remote-Standard-Lease-'+'b'*32,False)
            self.assertEqual(set(files),{'normal-fixture.cs','normal-bootstrap.ps1','normal-result-parser.ps1','gui-connection.rs'})
            self.assertEqual(list(workspace.iterdir()),[])
            self.assertNotIn('source-manifest.json',{p.name for p in evidence.iterdir()})
            with self.assertRaises(FileExistsError):builder.prepare(workspace,evidence,'Global\\Mixel-Remote-Standard-Lease-'+'b'*32,False)

    def test_actual_documented_helper_with_mocked_api_faults(self):
        rustc=self.compiler()
        with tempfile.TemporaryDirectory(prefix='mixel-gui-rust-') as temporary:
            root=Path(temporary)
            (root/'gui-connection-native-mocks.rs').write_text(MOCKS,encoding='utf-8')
            helper=builder.GUI_HELPER
            position=helper.rfind('\n}')
            helper=helper[:position]+RUST_TESTS+helper[position:]
            source=root/'control.rs';source.write_text(helper,encoding='utf-8')
            binary=root/('control.exe' if os.name=='nt' else 'control')
            subprocess.run([rustc,'--edition','2021','-D','warnings','--test',str(source),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            result=subprocess.run([str(binary),'--test-threads=1'],check=True,capture_output=True,text=True,timeout=10)
            self.assertIn('4 passed; 0 failed',result.stdout)
            print(result.stdout,flush=True)

    def test_actual_child_entry_acknowledges_only_exact_pid_and_refuses_arguments(self):
        # Run the actual MAIN with the documented API/guard boundaries mocked.
        # This is executable source control, never Windows or consent evidence.
        stub=r'''
fn lease_main() { panic!("Legacy creation path must never execute"); }
fn support_invite_lease_status()->String {
    let state=if gui_connection::synthetic_error()==2 { "kernel=absent;probeError=none" } else { "kernel=unavailable;probeError=5" };
    format!("ownerFailed=false;ownerError=none;{state};memoryActive=false")
}
fn support_invite_attestation()->&'static str { if gui_connection::synthetic_error()==2 { "" } else { "guard-unavailable" } }
fn support_invite_requires_click()->bool { true }
fn support_invite_owner_lease_failed()->bool { false }
mod mixel_support_lease { pub fn fixture_raw_open_error()->u32 { super::gui_connection::synthetic_error() } }
'''
        helper=builder.GUI_HELPER
        position=helper.rfind('\n}')
        helper=helper[:position]+r'''
    pub(super) fn synthetic_error()->u32 {
        if mock_native::CALLS.load(std::sync::atomic::Ordering::SeqCst)>0 { 2 } else { 5 }
    }
'''+helper[position:]
        mocks=MOCKS.replace('    pub fn reset(', '    #[allow(dead_code)]\n    pub fn reset(',1)
        with tempfile.TemporaryDirectory(prefix='mixel-gui-entry-') as temporary:
            root=Path(temporary);source=root/'entry.rs';binary=root/('entry.exe' if os.name=='nt' else 'entry')
            (root/'gui-connection-native-mocks.rs').write_text(mocks,encoding='utf-8')
            source.write_text(helper+stub+builder.MAIN,encoding='utf-8')
            subprocess.run([self.compiler(),'--edition','2021','-D','warnings','--cfg','test',str(source),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            result_path=root/'gui-connection-result.json'
            for mode in ('A-before','B','A-after'):
                result_path.unlink(missing_ok=True)
                (root/'gui-connection-mode.txt').write_text(mode,encoding='utf-8')
                child=subprocess.Popen([str(binary)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    deadline=time.monotonic()+5
                    while not result_path.is_file() and time.monotonic()<deadline and child.poll() is None:time.sleep(0.02)
                    self.assertTrue(result_path.is_file(),'Actual entry did not atomically write its record')
                    record=json.loads(result_path.read_text(encoding='utf-8'))
                    self.assertEqual(record['pid'],child.pid)
                    self.assertEqual(record['mode'],mode)
                    self.assertEqual([o['rawOpenError'] for o in record['observations']], [5,5,2] if mode=='B' else [5])
                    self.assertTrue(all(o['requiresClick'] and not o['ownerFailed'] for o in record['observations']))
                    self.assertTrue(all(record[key] is False for key in ('standardQualified','fullEightQualified','customerAppQualified','finalQualified')))
                    if mode=='B':self.assertTrue(record['gui']['moduleClosed'])
                    else:self.assertIsNone(record['gui'])
                    # An unrelated PID acknowledgement must not release this child.
                    unrelated=root/f'fixture-release-{child.pid+1}';unrelated.write_text('unrelated',encoding='utf-8')
                    time.sleep(0.1);self.assertIsNone(child.poll())
                    release=root/f'fixture-release-{child.pid}';release.write_text('verified',encoding='utf-8')
                    stdout,stderr=child.communicate(timeout=5)
                    self.assertEqual(child.returncode,0,stderr.decode(errors='replace'))
                    self.assertFalse(release.exists());self.assertTrue(unrelated.is_file());unrelated.unlink()
                    self.assertFalse((root/'gui-connection-result.new').exists())
                finally:
                    if child.poll() is None:child.kill();child.wait(timeout=5)
            result_path.unlink()
            rejected=subprocess.run([str(binary),'--legacy'],capture_output=True,timeout=5)
            self.assertNotEqual(rejected.returncode,0);self.assertFalse(result_path.exists())
            # The same exact entry's deadline path uses a shortened test clock;
            # only this literal duration changes, and no acknowledgement is made.
            deadline_main=builder.MAIN.replace('std::time::Duration::from_secs(20)','std::time::Duration::from_millis(200)',1)
            self.assertEqual(deadline_main.replace('std::time::Duration::from_millis(200)','std::time::Duration::from_secs(20)',1),builder.MAIN)
            source.write_text(helper+stub+deadline_main,encoding='utf-8')
            subprocess.run([self.compiler(),'--edition','2021','-D','warnings','--cfg','test',str(source),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            expired=subprocess.run([str(binary)],capture_output=True,timeout=5)
            self.assertNotEqual(expired.returncode,0);self.assertTrue(result_path.is_file())
            self.assertIn(b'not acknowledged',expired.stderr)
            print('PASS: actual mocked child entry runs A/B/A, retains literal results, consumes only its PID acknowledgement, rejects arguments and fails an unacknowledged deadline; no Windows qualification.',flush=True)

    def test_pe_metadata_refuses_static_gui_delayload_and_gui_subsystem(self):
        def image(name='kernel32.dll'):
            data=bytearray(1024);data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,128);data[128:132]=b'PE\0\0'
            for offset,fmt,value in ((132,'<H',0x8664),(134,'<H',1),(148,'<H',240),(152,'<H',0x20b),(220,'<H',3),(260,'<I',16)):
                struct.pack_into(fmt,data,offset,value)
            struct.pack_into('<II',data,272,0x1000,40);struct.pack_into('<IIII',data,400,512,0x1000,512,512)
            struct.pack_into('<IIIII',data,512,0,0,0,0x1040,0);data[576:576+len(name)+1]=name.encode('ascii')+b'\0'
            return data
        self.assertEqual(builder.pe_observation_metadata(image()),{'subsystem':3,'imports':['kernel32.dll'],'delayImportsAbsent':True,'user32StaticImportAbsent':True})
        for name in ('user32.dll','gdi32.dll','unknown.dll'):
            with self.subTest(name=name),self.assertRaises(ValueError):builder.pe_observation_metadata(image(name))
        wrong=image();struct.pack_into('<H',wrong,220,2)
        delay=image();struct.pack_into('<II',delay,152+112+13*8,0x1000,32)
        for data in (wrong,delay,b''):
            with self.assertRaises(ValueError):builder.pe_observation_metadata(data)

    def test_workflow_is_manual_independent_observation_only(self):
        source=(ROOT/'.github/workflows/preflight.yml').read_text(encoding='utf-8')
        self.assertRegex(source,r'standard_gui_connection_observation:\n(?:[^\n]*\n){2}        default: false')
        block=builder.section(source,'  standard-gui-connection-observation:\n','  https-registration-gateway:\n')
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.standard_gui_connection_observation",block)
        self.assertIn("MIXEL_GUI_CONNECTION_OBSERVATION: '1'",block)
        self.assertIn('if: always()',block)
        for forbidden in ('continue-on-error','build.yml','publish','portable-standard-lease-qa-source-only','NativeCrashDiagnostic'):
            self.assertNotIn(forbidden,block)
        parent=(ROOT/'scripts/observe-standard-gui-connection-windows.ps1').read_text(encoding='utf-8')
        self.assertIn("$env:GITHUB_ACTIONS -cne 'true'",parent)
        self.assertIn("$env:GITHUB_JOB -cne 'standard-gui-connection-observation'",parent)
        self.assertIn("$env:MIXEL_GUI_CONNECTION_OBSERVATION -cne '1'",parent)
        self.assertIn("foreach($mode in @('A-before','B','A-after'))",parent)
        self.assertIn('::StartStandardUser($executable,$ownedUser,$ownedPassword)',parent)
        self.assertNotIn('StartStandardUserWithToken',parent)
        self.assertNotIn('::OwnsLease',parent)


if __name__=='__main__':
    unittest.main()
