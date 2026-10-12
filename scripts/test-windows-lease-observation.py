#!/usr/bin/env python3
"""Executable source controls; mocked Win32 APIs never qualify Windows runtime."""
import ast
import codecs
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lease_observation", ROOT / "scripts/test-support-lease.py")
lease = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lease)

MOCKS = r'''
        #[allow(non_snake_case)]
        mod native {
            use super::{c_void, Handle, Luid, Statistics};
            use std::sync::atomic::{AtomicU32,Ordering};
            pub static VARIANT:AtomicU32=AtomicU32::new(0);
            pub static ERROR:AtomicU32=AtomicU32::new(0);
            pub static CLOSED:AtomicU32=AtomicU32::new(0);
            pub unsafe fn GetCurrentProcess()->Handle { 101usize as _ }
            pub unsafe fn GetCurrentThread()->Handle { 102usize as _ }
            pub unsafe fn GetCurrentThreadId()->u32 { 103 }
            pub unsafe fn GetLastError()->u32 { ERROR.load(Ordering::SeqCst) }
            pub unsafe fn OpenThreadToken(thread:Handle,access:u32,as_self:i32,out:*mut Handle)->i32 {
                assert_eq!(thread as usize,102);assert_eq!(access,8);assert_eq!(as_self,1);
                let variant=VARIANT.load(Ordering::SeqCst);
                if matches!(variant,0|3|4|8|9) { ERROR.store(if variant==3 {5} else if variant==9 {0} else {1008},Ordering::SeqCst);return 0; }
                *out=202usize as _;ERROR.store(0,Ordering::SeqCst);1
            }
            pub unsafe fn OpenProcessToken(process:Handle,access:u32,out:*mut Handle)->i32 {
                assert_eq!(process as usize,101);assert_eq!(access,8);
                let variant=VARIANT.load(Ordering::SeqCst);
                if matches!(variant,4|10) { ERROR.store(if variant==10 {0} else {5},Ordering::SeqCst);return 0; }
                *out=201usize as _;ERROR.store(0,Ordering::SeqCst);1
            }
            pub unsafe fn GetTokenInformation(token:Handle,kind:u32,data:*mut c_void,size:u32,returned:*mut u32)->i32 {
                assert_eq!(kind,10);assert_eq!(size,56);assert!(matches!(token as usize,201|202));
                let variant=VARIANT.load(Ordering::SeqCst);
                if (variant==5 && token as usize==202) || variant==8 {
                    *returned=56;ERROR.store(if variant==8 {0} else {87},Ordering::SeqCst);return 0;
                }
                let statistics=&mut *(data as *mut Statistics);
                statistics.token_type=if token as usize==201 {1} else {2};
                statistics.impersonation_level=if token as usize==201 {0} else {2};
                statistics.authentication_id=Luid{low:if variant==2 && token as usize==202 {12} else {11},high:0};
                *returned=if variant==6 && token as usize==202 {55} else {56};ERROR.store(0,Ordering::SeqCst);1
            }
            pub unsafe fn CloseHandle(token:Handle)->i32 {
                assert!(matches!(token as usize,201|202));
                CLOSED.fetch_add(1,Ordering::SeqCst);
                let variant=VARIANT.load(Ordering::SeqCst);
                if matches!(variant,7|11) && token as usize==202 { ERROR.store(if variant==11 {0} else {6},Ordering::SeqCst);0 }
                else { ERROR.store(0,Ordering::SeqCst);1 }
            }
        }
        use native::{GetCurrentProcess,GetCurrentThread,GetCurrentThreadId,GetLastError,CloseHandle,OpenProcessToken,OpenThreadToken,GetTokenInformation};
'''

RUST_TESTS = r'''
        #[test]
        fn actual_observer_with_mocked_read_only_token_apis() {
            use std::sync::atomic::Ordering;
            let directory=std::path::PathBuf::from(std::env::var("MIXEL_THREAD_TOKEN_CONTROL_DIR").unwrap()).join("observed");
            std::fs::create_dir(&directory).unwrap();
            RECORDS.lock().unwrap().clear();native::CLOSED.store(0,Ordering::SeqCst);
            enable(&directory);observe("OpenEventW");flush(&directory,false);
            assert!(RECORDS.lock().unwrap().is_empty());assert_eq!(native::CLOSED.load(Ordering::SeqCst),0);
            std::fs::write(directory.join("fixture-thread-token.request"),"observe").unwrap();enable(&directory);
            for variant in 0..12 {
                RECORDS.lock().unwrap().clear();native::VARIANT.store(variant,Ordering::SeqCst);native::CLOSED.store(0,Ordering::SeqCst);
                observe("OpenEventW");
                let record=RECORDS.lock().unwrap()[0].clone();
                assert!(record.contains("\"phase\":\"OpenEventW\",\"tid\":103"));
                assert!(!record.contains("authentication_id") && !record.contains("token_id") && !record.contains("S-1-"));
                match variant {
                    0=>assert!(record.contains("\"threadStatus\":\"absent\",\"threadError\":1008")),
                    1=>assert!(record.contains("\"threadLogonMatchesPrimary\":true")),
                    2=>assert!(record.contains("\"threadLogonMatchesPrimary\":false")),
                    3=>assert!(record.contains("\"threadStatus\":\"open-error\",\"threadError\":5")),
                    4=>assert!(record.contains("\"primaryStatus\":\"open-error\",\"primaryError\":5")),
                    5=>assert!(record.contains("\"threadStatus\":\"statistics-error\",\"threadError\":87")),
                    6=>assert!(record.contains("\"threadStatus\":\"statistics-size\",\"threadError\":0")),
                    7=>assert!(record.contains("\"threadClose\":\"failed\",\"threadCloseError\":6")),
                    8=>assert!(record.contains("\"primaryStatus\":\"statistics-error\",\"primaryError\":0")),
                    9=>assert!(record.contains("\"threadStatus\":\"open-error\",\"threadError\":0")),
                    10=>assert!(record.contains("\"primaryStatus\":\"open-error\",\"primaryError\":0")),
                    11=>assert!(record.contains("\"threadClose\":\"failed\",\"threadCloseError\":0")),
                    _=>unreachable!(),
                }
                assert_eq!(native::CLOSED.load(Ordering::SeqCst),if matches!(variant,0|3|8|9|10) {1} else if variant==4 {0} else {2});
            }
            RECORDS.lock().unwrap().clear();native::VARIANT.store(0,Ordering::SeqCst);
            observe("CreateEventExW");observe("OpenEventW");
            flush(&directory,false);
            let initial=directory.join("fixture-thread-token-initial.json");
            let initial_bytes=std::fs::read(&initial).unwrap();
            #[cfg(windows)]
            let reader={
                use std::os::windows::fs::OpenOptionsExt;
                // Exact FILE_SHARE_READ, excluding delete sharing, as used by
                // .NET ReadAllText. The second phase must not replace this file.
                std::fs::OpenOptions::new().read(true).share_mode(1).open(&initial).unwrap()
            };
            #[cfg(not(windows))]
            let reader=std::fs::File::open(&initial).unwrap();
            observe("CreateEventExW-unnamed");observe("OpenEventW");
            flush(&directory,true);
            assert!(directory.join("fixture-thread-token-unnamed.json").is_file());
            assert_eq!(std::fs::read(&initial).unwrap(),initial_bytes);
            assert!(!directory.join("fixture-thread-token.json").exists());
            assert!(!directory.join("fixture-thread-token.new").exists());
            drop(reader);
        }
'''


def token_record():
    return dict(phase="OpenEventW", tid=103, threadStatus="absent", threadError=1008,
                threadType=0, threadImpersonationLevel=0, primaryStatus="available", primaryError=0,
                primaryType=1, threadLogonMatchesPrimary=None, threadClose="not-opened", threadCloseError=0,
                primaryClose="closed", primaryCloseError=0)


class ObservationControls(unittest.TestCase):
    @staticmethod
    def powershell_engines():
        pwsh = shutil.which("pwsh")
        if not pwsh:
            raise AssertionError("PowerShell required for executable source controls")
        engines = [(pwsh, "Core")]
        if os.name == "nt":
            desktop = shutil.which("powershell.exe")
            if not desktop or Path(desktop).resolve() == Path(pwsh).resolve():
                raise AssertionError("Actual Windows source controls require distinct stock PowerShell5.1")
            engines.append((desktop, "Desktop"))
        return engines

    def test_generated_powershell_files_keep_unicode_with_desktop5_bom(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        writers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text"
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == "script"]
        self.assertEqual(len(writers), 6)
        csharp_writers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text"
                         and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "csharp"]
        self.assertEqual(len(csharp_writers), 1)
        self.assertEqual({item.arg: ast.literal_eval(item.value) for item in csharp_writers[0].keywords}, {"encoding": "utf-8"})
        with tempfile.TemporaryDirectory(prefix="mixel-powershell-encoding-") as temporary:
            for index, writer in enumerate(writers):
                encoding = next((item.value.value for item in writer.keywords
                                 if item.arg == "encoding" and isinstance(item.value, ast.Constant)), None)
                self.assertEqual(encoding, "utf-8-sig")
                self.assertIsInstance(writer.args[0], ast.Constant)
                original = writer.args[0].value
                self.assertNotIn("\r", original)
                path = Path(temporary) / (str(index) + ".ps1")
                # Path.write_text uses the host line ending by default. Both
                # Unix LF and Windows CRLF preserve this Unicode source; make
                # each writer mode explicit so both actually run on every OS.
                for newline in ("\n", "\r\n"):
                    path.write_text(original, encoding=encoding, newline=newline)
                    payload = path.read_bytes()
                    expected = original.replace("\n", newline)
                    self.assertEqual(payload, codecs.BOM_UTF8 + expected.encode("utf-8"))
                    self.assertEqual(payload.decode("utf-8-sig"), expected)
                if "préflight" in original:
                    # Windows PowerShell5 reads an unmarked source file with
                    # its legacy ANSI codepage; this is a bounded cp1252 decode
                    # control, not execution of that unavailable local engine.
                    self.assertNotEqual(original.encode("utf-8").decode("cp1252"), original)
                    self.assertNotIn("préflight", original.encode("utf-8").decode("cp1252"))
        print("PASS: all four generated PowerShell source files retain exact UTF8 BOM/Unicode bytes with both actual LF and CRLF writer modes; original unmarked accented source changes under the explicit legacy cp1252 decode control. Actual Desktop5 execution remains required on Windows.", flush=True)

    def test_current_caller_launch_exact_arguments_and_owned_handoff(self):
        source = (ROOT / "scripts/test-support-lease-standard-windows.ps1").read_text()
        launch = (ROOT / "scripts/test-support-launch-windows.ps1").read_text()
        method = re.search(r'  public static int StartCurrentCallerNative\(string executable\) \{.*?\n  \}', source, re.S)
        identity = re.search(r'  public static bool StartedCurrentCallerSidMatches\(int pid\) \{.*?\n  \}', source, re.S)
        self.assertIsNotNone(method)
        self.assertIsNotNone(identity)
        self.assertIn('int error=Marshal.GetLastWin32Error();\n    if(!success)', method[0])
        self.assertIn('return Started(process);', method[0])
        self.assertIn('OpenProcessToken(GetCurrentProcess(),8,out caller)', identity[0])
        self.assertNotIn('WindowsIdentity.GetCurrent(', identity[0])
        startup = re.search(r'  \[StructLayout\(LayoutKind.Sequential, CharSet = CharSet.Unicode\)\] struct StartupInfo \{.*?\n  \}', launch, re.S)
        process = re.search(r'  \[StructLayout\(LayoutKind.Sequential\)\] struct ProcessInfo \{[^\n]+', launch)
        self.assertIsNotNone(startup)
        self.assertIsNotNone(process)
        csharp = r'''
using System; using System.ComponentModel; using System.Runtime.InteropServices; using System.Text;
public static class DirectCallerLaunchControl {
''' + startup[0] + "\n" + process[0] + r'''
  public static bool Success=true; public static int Calls,Owned,Variant,Queries,Closed,IdentityDisposed,EffectiveThreadQueries;
  public static string QueryOrder="",CloseOrder="";
  static class Marshal {
    public static int NativeError;
    public static int SizeOf(Type value){return System.Runtime.InteropServices.Marshal.SizeOf(value);}
    public static int GetLastWin32Error(){return NativeError;}
  }
  static System.Collections.Generic.Dictionary<int,IntPtr> StartedProcesses=new System.Collections.Generic.Dictionary<int,IntPtr>();
  sealed class UserIdentity { public string Value; }
  sealed class WindowsIdentity:IDisposable {
    public UserIdentity User;
    WindowsIdentity(string value) {User=new UserIdentity {Value=value};}
    public WindowsIdentity(IntPtr token) {
      if(token!=new IntPtr(501) && token!=new IntPtr(502))throw new InvalidOperationException("Unowned identity token");
      if((Variant==7 && token==new IntPtr(502)) || ((Variant==8 || Variant==12) && token==new IntPtr(501)))throw new InvalidOperationException("Injected identity construction failure");
      User=new UserIdentity {Value=Variant==1 && token==new IntPtr(501)?"different-primary":"caller-primary"};
    }
    // The effective thread has a deliberately different identity. Querying it
    // would reproduce the reviewed GetCurrent/process-primary subject defect.
    public static WindowsIdentity GetCurrent(){EffectiveThreadQueries++;return new WindowsIdentity("different-effective-thread");}
    public void Dispose(){IdentityDisposed++;if((Variant==9 && User.Value=="caller-primary" && IdentityDisposed==2) || (Variant==10 && IdentityDisposed==1))throw new InvalidOperationException("Injected identity disposal failure");}
  }
  static IntPtr GetCurrentProcess(){return new IntPtr(101);}
  static bool OpenProcessToken(IntPtr process,uint access,out IntPtr token){
    if((process!=new IntPtr(401) && process!=new IntPtr(101)) || access!=8)throw new InvalidOperationException("Identity query is not exact retained child/current process handle TOKEN_QUERY");
    Queries++;bool child=process==new IntPtr(401);QueryOrder+=child?"child,":"caller,";token=new IntPtr(child?501:502);
    bool success=!((Variant==2 && child) || ((Variant==3 || Variant==11) && !child));
    Marshal.NativeError=success?0:(child?5:1300);return success;
  }
  static bool CloseHandle(IntPtr token){if(token!=new IntPtr(501) && token!=new IntPtr(502))throw new InvalidOperationException("Unowned identity token close");Closed++;bool child=token==new IntPtr(501);CloseOrder+=child?"child,":"caller,";bool success=!(Variant==6 || Variant==12 || ((Variant==4 || Variant==13) && !child) || ((Variant==5 || Variant==11) && child));Marshal.NativeError=success || Variant==13?0:(child?6:5);return success;}
  static bool CreateProcessW(string application,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string directory,ref StartupInfo startup,out ProcessInfo process) {
    Calls++;
    if(application!="owned path préflight/lease-standard.exe" || command.ToString()!="\""+application+"\"" || pa!=IntPtr.Zero || ta!=IntPtr.Zero || inherit || flags!=0 || env!=IntPtr.Zero || directory!=System.IO.Path.GetDirectoryName(application) || startup.desktop!=null || startup.cb!=Marshal.SizeOf(typeof(StartupInfo)) || startup.flags!=0) throw new InvalidOperationException("Direct launch changed context or arguments");
    process=new ProcessInfo {process=new IntPtr(401),thread=new IntPtr(402),pid=403,tid=404};Marshal.NativeError=Success?0:87;return Success;
  }
  static int Started(ProcessInfo process) { if(process.process!=new IntPtr(401) || process.thread!=new IntPtr(402) || process.pid!=403 || process.tid!=404) throw new InvalidOperationException("Wrong owned creation handles");Owned++;StartedProcesses.Add((int)process.pid,process.process);return (int)process.pid; }
''' + method[0] + "\n" + identity[0] + "\n}"
        with tempfile.TemporaryDirectory(prefix="mixel-direct-launch-source-") as temporary:
            root = Path(temporary)
            # ReadAllText below decodes the C# expectation as UTF8 on every
            # runner, regardless of Python's locale-dependent write default.
            (root / "control.cs").write_text(csharp, encoding="utf-8")
            script = root / "control.ps1"
            script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) { throw 'Actual required PowerShell edition/version missing' }
Write-Host "Actual mocked launch control PowerShell: $($PSVersionTable.PSVersion); $($PSVersionTable.PSEdition)"
# Compile the complete actual augmented class as well as executing the isolated
# launch body with mocks. No method in the real class is invoked here.
$standard=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1')).Replace("`r`n","`n")
$normal=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-launch-windows.ps1'))
$class=$normal.IndexOf('public static class MixelOrdinaryTokenFixture {');$first=$normal.LastIndexOf('using System;',$class);$last=$normal.IndexOf("`n'@ }",$class)
if($class -lt 0 -or $first -lt 0 -or $last -le $first) {throw 'Actual shared class extraction drift'}
$actual=$normal.Substring($first,$last-$first);$marker='  public static int StartStandardUser(string executable, string username, string password) {'
$position=$actual.IndexOf($marker);if($position -lt 0 -or $position -ne $actual.LastIndexOf($marker)) {throw 'Actual comparison insertion drift'}
$begin=$standard.IndexOf("  `$comparison = @'`n");$end=$standard.IndexOf("`n'@",$begin)
if($begin -lt 0 -or $end -le $begin) {throw 'Actual comparison extraction drift'}
$begin += "  `$comparison = @'`n".Length
Add-Type -TypeDefinition $actual.Insert($position,$standard.Substring($begin,$end-$begin)+"`n")
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $ControlRoot 'control.cs')))
if([DirectCallerLaunchControl]::StartCurrentCallerNative('owned path préflight/lease-standard.exe') -ne 403 -or [DirectCallerLaunchControl]::Owned -ne 1) { throw 'Exact retained handoff failed' }
foreach($variant in @(0,1,2,3,4,5,6,7,8,9,10,11,12,13)) {
  [DirectCallerLaunchControl]::Variant=$variant;[DirectCallerLaunchControl]::Queries=0;[DirectCallerLaunchControl]::Closed=0;[DirectCallerLaunchControl]::IdentityDisposed=0
  [DirectCallerLaunchControl]::EffectiveThreadQueries=0;[DirectCallerLaunchControl]::QueryOrder='';[DirectCallerLaunchControl]::CloseOrder=''
  $value=$null;$message='';$failure=$null;try {$value=[DirectCallerLaunchControl]::StartedCurrentCallerSidMatches(403)}catch{$failure=$_.Exception.InnerException;$message=$failure.Message}
  $queries=if($variant -eq 2){1}else{2}
  $closes=if($variant -eq 2){0}elseif($variant -cin @(3,11)){1}else{2}
  $disposed=if($variant -cin @(2,3,7,11)){0}elseif($variant -cin @(8,12)){1}else{2}
  $queryOrder=if($variant -eq 2){'child,'}else{'child,caller,'}
  $closeOrder=if($variant -eq 2){''}elseif($variant -cin @(3,11)){'child,'}else{'caller,child,'}
  if([DirectCallerLaunchControl]::Queries -ne $queries -or [DirectCallerLaunchControl]::Closed -ne $closes -or [DirectCallerLaunchControl]::IdentityDisposed -ne $disposed -or
    [DirectCallerLaunchControl]::EffectiveThreadQueries -ne 0 -or [DirectCallerLaunchControl]::QueryOrder -cne $queryOrder -or [DirectCallerLaunchControl]::CloseOrder -cne $closeOrder -or
    ($variant -eq 0 -and ($value -ne $true -or $message -cne '')) -or ($variant -eq 1 -and ($value -ne $false -or $message -cne '')) -or ($variant -ge 2 -and $message -ceq '')) {throw 'Actual same-caller identity/owned token failure or disposal contract changed'}
  if($variant -cin @(2,3)) {
    $code=if($variant -eq 2){5}else{1300}
    if($failure -isnot [ComponentModel.Win32Exception] -or $failure.NativeErrorCode -ne $code -or -not $message.Contains("nativeError=$code")){throw 'Actual primary query error was clobbered or omitted'}
  }
  if($variant -cin @(4,5,6,11,12,13)) {
    $count=if($variant -cin @(4,5,13)){1}elseif($variant -eq 12){3}else{2}
    if($failure -isnot [AggregateException] -or $failure.InnerExceptions.Count -ne $count) {throw 'Actual identity cleanup erased an earlier or simultaneous failure'}
    if(($variant -eq 11 -and -not $failure.InnerExceptions[0].Message.Contains('process primary TOKEN_QUERY failed')) -or
       ($variant -eq 12 -and $failure.InnerExceptions[0].Message -cne 'Injected identity construction failure') -or
       ($variant -cin @(6,12) -and (-not $failure.InnerExceptions[$count-2].Message.Contains('primary read-only identity token close failed') -or -not $failure.InnerExceptions[$count-1].Message.Contains('child read-only identity token close failed')))) {throw 'Actual source primary/query/close failure ordering or facts changed'}
    $codes=(@($failure.InnerExceptions | Where-Object {$_ -is [ComponentModel.Win32Exception]} | ForEach-Object {$_.NativeErrorCode}) -join ',')
    $expectedCodes=switch($variant){4{'5'}5{'6'}6{'5,6'}11{'1300,6'}12{'5,6'}13{'0'}}
    if($codes -cne $expectedCodes -or @($failure.InnerExceptions | Where-Object {$_ -is [ComponentModel.Win32Exception] -and -not $_.Message.Contains("nativeError=$($_.NativeErrorCode)")}).Count -ne 0){throw 'Actual original or simultaneous native query/close errors were clobbered or omitted; failed error0 remains fatal'}
  }
}
[DirectCallerLaunchControl]::Queries=0;$refused=$false;try{[void][DirectCallerLaunchControl]::StartedCurrentCallerSidMatches(999)}catch{$refused=$true}
if(-not $refused -or [DirectCallerLaunchControl]::Queries -ne 0) {throw 'Unowned identity PID reached token query'}
[DirectCallerLaunchControl]::Success=$false
$refused=$false;try { [void][DirectCallerLaunchControl]::StartCurrentCallerNative('owned path préflight/lease-standard.exe') } catch { $refused=$_.Exception.InnerException -is [ComponentModel.Win32Exception] -and $_.Exception.InnerException.NativeErrorCode -eq 87 -and $_.Exception.InnerException.Message.Contains('nativeError=87') }
if(-not $refused -or [DirectCallerLaunchControl]::Calls -ne 2 -or [DirectCallerLaunchControl]::Owned -ne 1) { throw 'Failed direct creation returned ownership or retried with another context' }
Write-Host 'PASS: actual extracted current-caller creation uses explicit Unicode/spaced path, quoted no-argument command, flags0/inheritfalse/environmentNULL/desktopNULL, exact owned handle handoff and fatal failure without retry; caller process and retained child primary TOKEN_QUERY identities match despite distinct effective thread identity; 14 identity/open/close/constructor/dispose combinations preserve both independent owned closes, exact immediate native error numbers, simultaneous close failures and primary-failure facts; failed close error0 remains fatal; unowned PID refused before queries. APIs mocked only.'
''', encoding="utf-8-sig")
            # Replay the reviewed unmarked-source ANSI decoding defect using
            # the actual launch body and unchanged Unicode C# expectation.
            # Core executes this deliberately decoded source; it is not PS5.
            original_ansi = root / "original-ansi-decoded.ps1"
            original_ansi.write_text(script.read_bytes()[len(codecs.BOM_UTF8):].decode("cp1252"), encoding="utf-8-sig")
            engine, edition = self.powershell_engines()[0]
            negative = subprocess.run([engine, "-NoProfile", "-File", str(original_ansi), "-Repository", str(ROOT), "-ControlRoot", str(root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(negative.returncode, 0)
            self.assertIn("Direct launch changed context or arguments", negative.stdout + negative.stderr)
            print("PASS: original explicit ANSI-decoded Unicode argument fails the actual extracted strict launch body before ownership; corrected BOM script is exercised next. This negative replay uses Core and mocked APIs, not native Desktop5.", flush=True)
            for engine, edition in self.powershell_engines():
                result = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout, flush=True)

    def test_fixture_result_sharing_retry_actual_lock_deadline_and_fatal_conservation(self):
        source = (ROOT / "scripts/test-support-lease-standard-windows.ps1").read_text(encoding="utf-8")
        start = source.index("function Read-StandardFixtureResult(")
        end = source.index("function Invoke-StandardLeaseControl(", start)
        helper = source[start:end]
        self.assertEqual(source.count("$text = Read-StandardFixtureResult $resultPath $deadline"), 2)
        prior = source[:start] + source[end:]
        prior = prior.replace("$text = Read-StandardFixtureResult $resultPath $deadline",
                              "$text = [IO.File]::ReadAllText($resultPath)")
        self.assertEqual(hashlib.sha256(prior.encode()).hexdigest(),
                         "103f8474c7a4b79f33c1ab64e98fce48cd380931607cb6c9b359e7eeccaf86d1")
        self.assertEqual(helper.count("[IO.File]::ReadAllText($Path)"), 1)
        self.assertIn('std::fs::write(directory.join("fixture-result.new"), result).unwrap();\n'
                      '        mixel_support_lease::fixture_flush_thread_tokens(&directory,false);\n'
                      '        std::fs::rename(directory.join("fixture-result.new"), directory.join("fixture-result.json")).unwrap();',
                      lease.STANDARD_WINDOWS_HARNESS)
        with tempfile.TemporaryDirectory(prefix="mixel-result-sharing-controls-") as temporary:
            root = Path(temporary)
            retry_script = root / "retry-control.ps1"
            retry_script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) {throw 'Required actual PowerShell edition/version missing'}
$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1')).Replace("`r`n","`n")
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count) {throw 'Actual retry helper does not parse'}
function ExactFunction([string]$Name) {
  $nodes=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name},$true)
  if($nodes.Count -ne 1) {throw "Missing exact function $Name"};return $nodes[0].Extent.Text
}
$helper=ExactFunction 'Read-StandardFixtureResult'
. ([scriptblock]::Create((ExactFunction 'Assert-StandardLeaseSafeResult')))
foreach($name in @('Invoke-StandardLeaseControl','Invoke-CurrentCallerLeaseControl')) {
  $body=ExactFunction $name
  if(-not $body.Contains('$deadline = [DateTime]::UtcNow.AddSeconds(10)') -or
     -not $body.Contains('$text = Read-StandardFixtureResult $resultPath $deadline') -or
     -not $body.Contains('if ($text.Length -gt 4096)') -or
     $body.IndexOf('$text = Read-StandardFixtureResult') -gt $body.IndexOf('$record = $text | ConvertFrom-Json')) {throw 'Original deadline, size bound or parse order changed'}
}
$standardAst=[Management.Automation.Language.Parser]::ParseInput((ExactFunction 'Invoke-StandardLeaseControl'),[ref]$tokens,[ref]$errors)
$sizeGate=$standardAst.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($text.Length -gt 4096)')},$true)
$nativeGate=$standardAst.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.Contains('Actual standard-account source lease classification mismatch:')},$true)
if($sizeGate.Count -ne 1 -or $nativeGate.Count -ne 1){throw 'Exact original size/native classification gate missing'}
Add-Type @'
using System;using System.IO;using System.Threading;
public sealed class FixtureReadIOException:IOException {public FixtureReadIOException(int code):base("Controlled exact IO status"){HResult=code;}}
public static class FixtureResultRead {
  static FileStream holder;static Timer timer;static readonly object gate=new object();static string ownedPath;
  public static int Calls,Releases,ForcedCode;static int releaseDelay;public static bool ForceIO,ForceOther;public static IOException FirstSharing;
  public static bool Windows {get{return Environment.OSVersion.Platform==PlatformID.Win32NT;}}
  public static void Reset(){Release();Calls=Releases=ForcedCode=0;ForceIO=ForceOther=false;FirstSharing=null;}
  public static void Hold(string path,int milliseconds){ownedPath=path;releaseDelay=milliseconds;holder=File.Open(path,FileMode.Open,FileAccess.Read,FileShare.None);}
  public static void Release(){lock(gate){if(holder!=null){holder.Dispose();holder=null;Releases++;}if(timer!=null){timer.Dispose();timer=null;}}}
  public static string ReadAllText(string path){Calls++;if(ForceOther)throw new InvalidOperationException("Controlled non-IO fatal");if(ForceIO)throw new FixtureReadIOException(ForcedCode);
    try{return File.ReadAllText(path);}catch(IOException error){
      lock(gate){if(releaseDelay>0){timer=new Timer(delegate(object value){Release();},null,releaseDelay,Timeout.Infinite);releaseDelay=0;}}
      // POSIX FileShare.None reports errno35. Map only the owned real-lock
      // boundary to the Windows HRESULT; Windows retains its actual status.
      if(!Windows && path==ownedPath && error.HResult==35){IOException mapped=new FixtureReadIOException(unchecked((int)0x80070020));if(FirstSharing==null)FirstSharing=mapped;throw mapped;}
      if(error.HResult==unchecked((int)0x80070020) && FirstSharing==null)FirstSharing=error;throw;
    }
  }
}
public static class FixtureReadClock {public static DateTime Now;public static DateTime UtcNow {get{return Now;}}}
'@
$path=Join-Path $ControlRoot 'fixture-result.json'
$native='{"pid":4321,"sourceStatus":"ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false","rawOpenError":5,"attestation":"guard-unavailable","requiresClick":true,"ownerFailed":false}'
[IO.File]::WriteAllText($path,$native)
# Real exclusive ownership demonstrates the original immediate-read failure.
[FixtureResultRead]::Reset();[FixtureResultRead]::Hold($path,0)
try {
  $failure=$null;try {[void][IO.File]::ReadAllText($path)}catch{$failure=$_.Exception;while($failure.InnerException){$failure=$failure.InnerException}}
  $expected=if([FixtureResultRead]::Windows){-2147024864}else{35}
  if($failure -isnot [IO.IOException] -or $failure.HResult -ne $expected){throw 'Original own exclusive-read sharing failure not reproduced'}
}finally{[FixtureResultRead]::Release()}
# Only the file-read API is an explicit dependency shim. Every lock/release and
# file byte is real; the exact extracted retry/exception/deadline body runs.
$readShim=$helper.Replace('[IO.File]::ReadAllText($Path)','[FixtureResultRead]::ReadAllText($Path)')
. ([scriptblock]::Create($readShim))
[FixtureResultRead]::Reset();[FixtureResultRead]::Hold($path,150)
try {
  $watch=[Diagnostics.Stopwatch]::StartNew();$text=Read-StandardFixtureResult $path ([DateTime]::UtcNow.AddSeconds(2));$watch.Stop()
  if($text -cne $native -or [FixtureResultRead]::Calls -lt 2 -or [FixtureResultRead]::Releases -ne 1 -or $watch.ElapsedMilliseconds -lt 80 -or $watch.ElapsedMilliseconds -gt 3000){throw 'Actual own exclusive release was not awaited under the bounded retry'}
}finally{[FixtureResultRead]::Release()}
foreach($expired in @($false,$true)) {
  [FixtureResultRead]::Reset();[FixtureResultRead]::Hold($path,0)
  try {
    $deadline=if($expired){[DateTime]::UtcNow.AddMilliseconds(-1)}else{[DateTime]::UtcNow.AddMilliseconds(120)}
    $watch=[Diagnostics.Stopwatch]::StartNew();$failure=$null
    try{[void](Read-StandardFixtureResult $path $deadline)}catch{$failure=$_.Exception;while($failure.InnerException){$failure=$failure.InnerException}};$watch.Stop()
    if($failure -isnot [IO.IOException] -or $failure.HResult -ne -2147024864 -or -not [Object]::ReferenceEquals($failure,[FixtureResultRead]::FirstSharing) -or
       ($expired -and [FixtureResultRead]::Calls -ne 1) -or (-not $expired -and [FixtureResultRead]::Calls -lt 2) -or
       $watch.ElapsedMilliseconds -gt 1500 -or (-not $expired -and $watch.ElapsedMilliseconds -lt 80)){throw 'Sharing timeout extended the original deadline, erased the original error or retried an expired read'}
  }finally{[FixtureResultRead]::Release()}
}
# Bind the first candidate's actual catch-only body, which let a post-sleep
# retry succeed at/after expiry. Only the clock and file-read APIs are stand-ins.
$catchOnly=$helper.Replace("  `$sharingFailure = `$null`n",'').Replace("    if (`$null -ne `$sharingFailure -and [DateTime]::UtcNow -ge `$Deadline) { throw `$sharingFailure }`n",'')
$catchOnly=$catchOnly.Replace('      if ($failure -isnot [IO.IOException] -or $failure.HResult -ne -2147024864) { throw }',"      if (`$failure -isnot [IO.IOException] -or `$failure.HResult -ne -2147024864 -or`n          [DateTime]::UtcNow -ge `$Deadline) { throw }")
$catchOnly=$catchOnly.Replace("      if (`$null -eq `$sharingFailure) { `$sharingFailure = `$_ }`n",'').Replace("      if ([DateTime]::UtcNow -ge `$Deadline) { throw `$sharingFailure }`n",'')
$sha=[Security.Cryptography.SHA256]::Create()
try{$hash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($catchOnly+"`n`n")))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
if($hash -cne '7cc127fd7196a075710a47bae912610ca419a374f0e955e694d8afd961d7786a'){throw 'Original catch-only candidate body drifted'}
function Start-Sleep {param([int]$Milliseconds)
  if($script:controlledClock){[FixtureReadClock]::Now=$script:clockResume;[FixtureResultRead]::Release()}
  else{Microsoft.PowerShell.Utility\Start-Sleep -Milliseconds $Milliseconds}
}
$script:controlledClock=$true
foreach($offset in @(0,1)) {
  foreach($original in @($true,$false)) {
    [FixtureResultRead]::Reset();[FixtureResultRead]::Hold($path,0)
    [FixtureReadClock]::Now=[DateTime]::UtcNow;$deadline=[FixtureReadClock]::Now.AddMilliseconds(100);$script:clockResume=$deadline.AddMilliseconds($offset)
    $body=if($original){$catchOnly}else{$helper}
    . ([scriptblock]::Create($body.Replace('[IO.File]::ReadAllText($Path)','[FixtureResultRead]::ReadAllText($Path)').Replace('[DateTime]::UtcNow','[FixtureReadClock]::UtcNow')))
    try {
      $failure=$null;$text='';try{$text=Read-StandardFixtureResult $path $deadline}catch{$failure=$_.Exception;while($failure.InnerException){$failure=$failure.InnerException}}
      if($original){if($failure -or $text -cne $native -or [FixtureResultRead]::Calls -ne 2){throw 'Actual original catch-only late-read defect not reproduced'}}
      elseif($null -eq $failure -or -not [Object]::ReferenceEquals($failure,[FixtureResultRead]::FirstSharing) -or [FixtureResultRead]::Calls -ne 1){throw 'At/after-deadline retry was allowed or original first sharing error replaced'}
    }finally{[FixtureResultRead]::Release()}
  }
}
$script:controlledClock=$false
. ([scriptblock]::Create($readShim))
foreach($code in @(0,-2147024894,-2147024891,-2147024863)) {
  [FixtureResultRead]::Reset();[FixtureResultRead]::ForceIO=$true;[FixtureResultRead]::ForcedCode=$code;$failure=$null
  try{[void](Read-StandardFixtureResult $path ([DateTime]::UtcNow.AddSeconds(1)))}catch{$failure=$_.Exception;while($failure.InnerException){$failure=$failure.InnerException}}
  if($failure -isnot [IO.IOException] -or $failure.HResult -ne $code -or [FixtureResultRead]::Calls -ne 1){throw 'Nonsharing IO/error0 was retried or replaced'}
}
[FixtureResultRead]::Reset();[FixtureResultRead]::ForceOther=$true;$message=''
try{[void](Read-StandardFixtureResult $path ([DateTime]::UtcNow.AddSeconds(1)))}catch{$message=$_.Exception.Message}
if(-not $message.Contains('Controlled non-IO fatal') -or [FixtureResultRead]::Calls -ne 1){throw 'Non-IO failure was retried or replaced'}
[FixtureResultRead]::Reset()
$ObserveOnly=$false;$ExpectedStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
$ExpectedError=2;$ExpectedProof='';$ExpectedRequired=$false;$ExpectedOwnerFailed=$false
foreach($payload in @('{malformed',(' ' * 4097),$native)) {
  [IO.File]::WriteAllText($path,$payload);$message=''
  try {
    $text=Read-StandardFixtureResult $path ([DateTime]::UtcNow.AddSeconds(1))
    & ([scriptblock]::Create($sizeGate[0].Extent.Text))
    $record=$text | ConvertFrom-Json;Assert-StandardLeaseSafeResult $record 4321
    & ([scriptblock]::Create($nativeGate[0].Extent.Text))
  }catch{$message=$_.Exception.Message}
  if($message -ceq '' -or ($payload -ceq $native -and (-not $message.Contains('classification mismatch') -or -not $message.Contains('"rawOpenError":5'))) -or
     ($payload.Length -gt 4096 -and -not $message.Contains('exceeds its bound'))){throw 'Malformed/oversize/native5 became a successful result after retry'}
}
Write-Host "PASS: exact result reader reproduces original real FileShare.None refusal, waits for own release, retries only sharing HRESULT within original deadline, preserves expiry/other IO/error0/non-IO/malformed/oversize/native5 fatal outcomes. SHA-bound original catch-only accepts at/after deadline; corrected exact-clock controls retain the first sharing exception and perform no late read. Real file/lock APIs; POSIX-only HRESULT35 and exact-clock mappings are explicit API stand-ins. Actual engine: $($PSVersionTable.PSVersion); $($PSVersionTable.PSEdition)."
''', encoding="utf-8-sig")
            for index, (engine, edition) in enumerate(self.powershell_engines()):
                control_root = root / ("engine-" + str(index))
                control_root.mkdir()
                result = subprocess.run([engine, "-NoProfile", "-File", str(retry_script), "-Repository", str(ROOT),
                                         "-ControlRoot", str(control_root), "-ExpectedEdition", edition],
                                        capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("preserves expiry/other IO/error0/non-IO/malformed/oversize/native5 fatal", result.stdout)
                print(result.stdout, flush=True)

    def test_current_caller_four_actual_functions_strict_results_and_cleanup(self):
        # The compiled fixture acknowledges its retained result by consuming the
        # exact release path before a successful exit. Model that real lifecycle,
        # as well as a residual acknowledgment that still requires parent cleanup.
        fixture_source = (ROOT / "scripts/test-support-lease.py").read_text(encoding="utf-8")
        self.assertIn('let release = directory.join(format!("fixture-release-{}", std::process::id()));', fixture_source)
        self.assertIn('assert!(release.is_file(), "Owned standard-account result was not acknowledged");\n            std::fs::remove_file(release).unwrap();', fixture_source)
        with tempfile.TemporaryDirectory(prefix="mixel-current-caller-controls-") as temporary:
            root = Path(temporary)
            script = root / "control.ps1"
            script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) { throw 'Actual required PowerShell edition/version missing' }
Write-Host "Actual mocked four-phase control PowerShell: $($PSVersionTable.PSVersion); $($PSVersionTable.PSEdition)"
$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1'))
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count) { throw 'Current helper does not parse' }
foreach($name in @('Write-LeaseContextFailure','Invoke-LeaseJobObservation','Read-StandardChildJobLimits','Invoke-LeaseChildJobLimitsObservation','Read-StandardChildDirectoryAccess','Invoke-LeaseChildDirectoryAccessObservation','Assert-StandardLeaseSafeResult','Read-StandardCallingThreadTokens','Read-StandardFixtureResult','Invoke-CurrentCallerLeaseControl','Invoke-CurrentCallerLeaseFour')) {
  $matches=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
  if($matches.Count -ne 1) { throw "Missing exact current function $name" }
  if($name -ceq 'Invoke-CurrentCallerLeaseControl') { $correctedControl=$matches[0].Extent.Text }
  . ([scriptblock]::Create($matches[0].Extent.Text))
}
Add-Type @'
using System;using System.IO;using System.Collections.Generic;
public sealed class ControlProcess : IDisposable { public int SessionId=2;public void Dispose(){MixelOrdinaryTokenFixture.Disposed++;} }
public sealed class ControlFacts {public bool sessionKnown=true;public uint sessionId=2;public string logonComparison="tokenLogonSidCount=1;tokenLogonSidMatchesCaller=true;tokenLogonSidQuerySubject=none;tokenLogonSidQueryFailure=none;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none";}
public static class MixelOrdinaryTokenFixture {
  public static string Root,Scenario;public static int CallerPid,Next,Owner,Disposed,Stops,Closes;
  public static List<string> Calls=new List<string>();static Dictionary<int,string> statuses=new Dictionary<int,string>();
  public static void Reset(string root,string scenario,int caller) {Root=root;Scenario=scenario;CallerPid=caller;Next=4000;Owner=0;Disposed=Stops=Closes=0;Calls.Clear();statuses.Clear();}
  public static int StartCurrentCallerNative(string executable) {
    if(executable!=Path.Combine(Root,"lease-standard.exe") || File.ReadAllText(Path.Combine(Root,"fixture-thread-token.request"))!="observe") throw new InvalidOperationException("Wrong source executable/request");
    int pid=++Next;string mode=File.ReadAllText(Path.Combine(Root,"fixture-mode.txt"));Calls.Add(mode+":"+pid);statuses.Add(pid,"still-running");
    bool active=Owner!=0;bool memory=mode=="owner";if(memory){Owner=pid;active=true;}
    string kernel=active?"active":"absent",probe="none",proof=active?"attended-runtime-v2":"";int error=active?0:2;bool click=active,failed=false;
    if(Scenario=="error5") {kernel="unavailable";probe="5";error=5;proof="guard-unavailable";click=true;}
    if(Scenario=="false-ready" && !active){proof="attended-runtime-v2";click=true;}
    if(Scenario=="owner-memory" && memory)memory=false;
    if(Scenario=="owner-failed") {failed=true;proof="guard-unavailable";click=true;}
    if(Scenario=="wrong-click")click=!click;
    string status="ownerFailed="+(failed?"true":"false")+";ownerError="+(failed?"6":"none")+";kernel="+kernel+";probeError="+probe+";memoryActive="+(memory?"true":"false");
    string json="{\"pid\":"+pid+",\"sourceStatus\":\""+status+"\",\"rawOpenError\":"+error+",\"attestation\":\""+proof+"\",\"requiresClick\":"+(click?"true":"false")+",\"ownerFailed\":"+(failed?"true":"false")+"}";
    File.WriteAllText(Path.Combine(Root,"fixture-result.json"),json);
    if(File.ReadAllText(Path.Combine(Root,"fixture-job-limits.request"))!="observe")throw new InvalidOperationException("Missing child-self job observation request");
    File.WriteAllText(Path.Combine(Root,"fixture-job-limits.json"),"{\"schema\":1,\"pid\":"+pid+",\"inAnyJob\":true,\"membershipStatus\":\"available\",\"membershipError\":0,\"uiStatus\":\"available\",\"uiError\":0,\"uiReturned\":4,\"uiFlags\":0,\"extendedStatus\":\"available\",\"extendedError\":0,\"extendedReturned\":144,\"extendedFlags\":8192}");
    if(File.ReadAllText(Path.Combine(Root,"fixture-directory-access.request"))!="observe")throw new InvalidOperationException("Missing child-self explicit directory request");
    string rows="";for(int i=0;i<6;i++){if(i!=0)rows+=",";bool global=i<2||i==4;string kind=global?"global":"session",path=global?"\\\\BaseNamedObjects":"\\\\Sessions\\\\2\\\\BaseNamedObjects";rows+="{\"directory\":\""+kind+"\",\"path\":\""+path+"\",\"access\":"+(i<4?1+i%2:4)+",\"ntStatus\":0,\"status\":\"opened\",\"handleReturned\":true,\"closeStatus\":\"closed\",\"closeError\":0}";}
    File.WriteAllText(Path.Combine(Root,"fixture-directory-access.json"),"{\"schema\":1,\"pid\":"+pid+",\"scope\":\"ExplicitDirectoryAccessOnly\",\"route\":\"ActualEventRouteUnmeasured\",\"nameCreationAccessMeasured\":true,\"objectsCreated\":false,\"sessionStatus\":\"available\",\"sessionError\":0,\"sessionId\":2,\"loaderStatus\":\"available\",\"loaderError\":0,\"unloadStatus\":\"closed\",\"unloadError\":0,\"records\":["+rows+"]}");
    string close=Scenario=="token-close"?"failed":"closed";int closeError=Scenario=="token-close"?0:0;
    File.WriteAllText(Path.Combine(Root,"fixture-thread-token-initial.json"),"{\"schema\":1,\"pid\":"+pid+",\"records\":[{\"phase\":\"OpenEventW\",\"tid\":103,\"threadStatus\":\"absent\",\"threadError\":1008,\"threadType\":0,\"threadImpersonationLevel\":0,\"primaryStatus\":\"available\",\"primaryError\":0,\"primaryType\":1,\"threadLogonMatchesPrimary\":null,\"threadClose\":\"not-opened\",\"threadCloseError\":0,\"primaryClose\":\""+close+"\",\"primaryCloseError\":"+closeError+"}]}");
    return pid;
  }
  public static bool StartedCurrentCallerSidMatches(int pid){return Scenario!="sid";}
  public static bool Elevated(int pid){return pid==CallerPid || Scenario!="elevation";}
  public static ControlFacts StartedTokenFacts(int pid){var f=new ControlFacts();if(Scenario=="token-session")f.sessionId=3;if(Scenario=="unknown-session")f.sessionKnown=false;if(Scenario=="logon")f.logonComparison=f.logonComparison.Replace("MatchesCaller=true","MatchesCaller=false");return f;}
  public static bool StartedJobMembership(int pid){if(!statuses.ContainsKey(pid))throw new InvalidOperationException("Unowned job observation");return true;}
  public static string StartedStatus(int pid){if(!statuses.ContainsKey(pid))throw new InvalidOperationException("Unowned status");if(Scenario=="early-owner" && pid==Owner)return "exited:0x00000000";string release=Path.Combine(Root,"fixture-release-"+pid);if(pid!=Owner && File.Exists(release)){if(Scenario!="parent-release" && Scenario!="remove-denied")File.Delete(release);statuses[pid]="exited:0x00000000";}return statuses[pid];}
  public static void StopOwnedStartedProcesses(){Stops++;Calls.Add("stop");foreach(int pid in new List<int>(statuses.Keys))if(statuses[pid]=="still-running")statuses[pid]="exited:0x00000001";Owner=0;if(Scenario=="stop")throw new InvalidOperationException("Mock retained stop failure");}
  public static void CloseStartedObservations(){Closes++;Calls.Add("close");statuses.Clear();if(Scenario=="close")throw new InvalidOperationException("Mock retained close failure");}
}
'@
function Get-Process {param([int]$Id) $process=[ControlProcess]::new();if($Id -ne $PID -and [MixelOrdinaryTokenFixture]::Scenario -ceq 'process-session'){$process.SessionId=3};return $process}
function Remove-Item {
  param([string]$LiteralPath)
  if([MixelOrdinaryTokenFixture]::Scenario -ceq 'remove-denied' -and [IO.Path]::GetFileName($LiteralPath).StartsWith('fixture-release-')) {throw 'Mock existing acknowledgment cleanup denied'}
  Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath
}
$script:printed=[Collections.Generic.List[string]]::new()
$leaseContextFailures=[Collections.Generic.List[Exception]]::new()
function Write-Host {param($Object) $script:printed.Add([string]$Object)}
$cleanupGuard='if (Test-Path -LiteralPath $releasePath) { Remove-Item -LiteralPath $releasePath }'
if(-not $correctedControl.Contains($cleanupGuard)) {throw 'Exact guarded release cleanup missing'}
# Restore the one original failing statement inside the actual extracted body;
# the child model still consumes its acknowledgment before retained exit0.
$originalControl=$correctedControl.Replace($cleanupGuard,'Remove-Item -LiteralPath $releasePath')
. ([scriptblock]::Create($originalControl))
$fixtureRoot=Join-Path $ControlRoot 'original-consumed-release';[void][IO.Directory]::CreateDirectory($fixtureRoot)
$executable=Join-Path $fixtureRoot 'lease-standard.exe';$resultPath=Join-Path $fixtureRoot 'fixture-result.json'
[MixelOrdinaryTokenFixture]::Reset($fixtureRoot,'pass',$PID);$script:printed.Clear()
$message='';try {Invoke-CurrentCallerLeaseFour} catch {$message=$_.Exception.Message}
if(-not $message.Contains('fixture-release-4001') -or -not $message.Contains('does not exist') -or
   ([MixelOrdinaryTokenFixture]::Calls -join ',') -cne 'probe:4001,stop,close' -or
   [MixelOrdinaryTokenFixture]::Disposed -ne 2 -or [MixelOrdinaryTokenFixture]::Stops -ne 1 -or [MixelOrdinaryTokenFixture]::Closes -ne 1 -or
   (Test-Path -LiteralPath (Join-Path $fixtureRoot 'fixture-release-4001'))) {throw "Original consumed acknowledgment did not fail the exact first-probe cleanup: $message"}
. ([scriptblock]::Create($correctedControl))
$scenarios=@('pass','parent-release','remove-denied','error5','false-ready','owner-memory','owner-failed','wrong-click','token-close','sid','elevation','process-session','token-session','unknown-session','logon','early-owner','stop','close')
foreach($scenario in $scenarios) {
  $fixtureRoot=Join-Path $ControlRoot $scenario;[void][IO.Directory]::CreateDirectory($fixtureRoot)
  $executable=Join-Path $fixtureRoot 'lease-standard.exe';$resultPath=Join-Path $fixtureRoot 'fixture-result.json'
  [MixelOrdinaryTokenFixture]::Reset($fixtureRoot,$scenario,$PID)
  $script:printed.Clear()
  $message='';try { Invoke-CurrentCallerLeaseFour } catch { $message=$_.Exception.Message }
  $positive=$scenario -ceq 'pass' -or $scenario -ceq 'parent-release'
  if($positive -ne ($message -ceq '')) {throw "Incorrect direct control scenario $scenario : $message"}
  if([MixelOrdinaryTokenFixture]::Stops -lt 1 -or [MixelOrdinaryTokenFixture]::Closes -lt 1) {throw "Independent retained cleanup missing $scenario"}
  if($positive) {
    if(([MixelOrdinaryTokenFixture]::Calls -join ',') -cne 'probe:4001,owner:4002,probe:4003,stop,close,probe:4004,stop,close' -or [MixelOrdinaryTokenFixture]::Disposed -ne 8) {throw 'Four phase ownership/order or wrapper disposal changed'}
    if(@(Get-ChildItem -LiteralPath $fixtureRoot -Filter 'fixture-release-*').Count -ne 0) {throw 'Confirmed exited caller acknowledgment can leak into the unchanged standard terminal after PID reuse'}
    if($script:printed.Count -ne 21 -or @($script:printed | Where-Object {$_ -like 'OBSERVED: direct current Actions caller GUID functional control;*' -and $_.Contains('genuineStandardQualified=False; fullEightQualified=False; customerAppQualified=False;')}).Count -ne 4 -or
       @($script:printed | Where-Object {$_ -like 'OBSERVED: retained owned source child job membership;*' -and $_.Contains('childJobLimitsMeasured=False; namedDirectoryMeasured=False.')}).Count -ne 4 -or
       @($script:printed | Where-Object {$_ -like 'OBSERVED: owned source child immediate job limits;*' -and $_.Contains('immediateJobOnly=True; completeLimitsMeasured=True; namedDirectoryMeasured=False; causeEstablished=False;')}).Count -ne 4 -or
       @($script:printed | Where-Object {$_ -like 'OBSERVED: owned source child explicit object directory access;*' -and $_.Contains('ExplicitDirectoryAccessOnly=True; ActualEventRouteUnmeasured=True; nameCreationAccessMeasured=True; objectsCreated=False; causeEstablished=False;')}).Count -ne 4 -or $leaseContextFailures.Count -ne 0) {throw 'Diagnostic output silently became standard/app qualification or lost native records'}
  } else {
    if($scenario -ceq 'remove-denied' -and ($message -cne 'Mock existing acknowledgment cleanup denied' -or
       -not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'fixture-release-4001')) -or
       ([MixelOrdinaryTokenFixture]::Calls -join ',') -cne 'probe:4001,stop,close' -or [MixelOrdinaryTokenFixture]::Disposed -ne 2)) {throw 'Existing acknowledgment cleanup failure was erased or retained process/wrapper cleanup changed'}
    if($scenario -ceq 'error5' -and (-not $message.Contains('classification mismatch') -or -not ($script:printed -join '').Contains('"rawOpenError":5'))) {throw 'Native5 did not remain fatal or was lost before failure'}
    if($scenario -ceq 'token-close' -and -not $message.Contains('token handle cleanup failed')) {throw 'Unknown failed close0 did not remain fatal'}
  }
}
$start=$source.IndexOf('  $currentCallerFailure = $null')
$end=$source.IndexOf('  Invoke-StandardLeaseSubsystemObservations with-logon',$start)
if($start -lt 0 -or $end -le $start) {throw 'Direct comparison not before unchanged matrix'}
$entry=$source.Substring($start,$end-$start)
$final=$ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text -ceq 'if ($currentCallerFailure) { throw $currentCallerFailure }'},$true)
if($final.Count -ne 1 -or $final[0].Extent.StartOffset -lt $source.IndexOf("PASS: closed wrong-object control leaves no GUID lease")) {throw 'Deferred direct failure replaced or escaped the original mandatory gate'}
function Invoke-CurrentCallerLeaseFour {throw 'Exact retained direct source failure'}
. ([scriptblock]::Create($entry))
if(-not $currentCallerFailure -or $currentCallerFailure.Exception.Message -cne 'Exact retained direct source failure') {throw 'Direct failure erased before old matrix'}
$oldMatrixReached=$true
$message='';try {& ([scriptblock]::Create($final[0].Extent.Text))} catch {$message=$_.Exception.Message}
if(-not $oldMatrixReached -or $message -cne 'Exact retained direct source failure') {throw 'Deferred direct failure no longer fatal'}
Microsoft.PowerShell.Utility\Write-Host 'PASS: exact original consumed-acknowledgment cleanup fails after its first native-result model; corrected current-caller PowerShell functions execute four strict same-executable phases with consumed and residual acknowledgments, retained owner drop/fresh absence and independent cleanup; 16 identity/native-state/close/lifetime/cleanup mutations refuse including existing-file unlink failure; failure is retained while the old matrix runs and rethrown after the unchanged mandatory gate. APIs mocked only; no standard/app qualification.'
''', encoding="utf-8-sig")
            for index, (engine, edition) in enumerate(self.powershell_engines()):
                control_root = root / ("engine-" + str(index))
                control_root.mkdir()
                result = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(control_root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=35)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("corrected current-caller PowerShell functions execute four strict same-executable phases", result.stdout)
                print(result.stdout, flush=True)

    def test_owned_world_descriptor_and_retained_job_read_only_controls(self):
        source = (ROOT / "scripts/test-support-lease-standard-windows.ps1").read_text(encoding="utf-8").replace("\r\n", "\n")
        start = source.index("public static class MixelIsolatedLeaseControl {")
        first = source.rindex("using System;", 0, start)
        end = source.index("\n'@ }", start)
        actual = source[first:end]
        pattern = r'  \[DllImport\([^\n]*\)\](?:[ \t]*\n)?[ \t]*static extern [^\n]*;\n'
        mocked, count = re.subn(pattern, "", actual)
        self.assertEqual(count, 10, "Exact diagnostic and original native declarations changed")
        self.assertIn("ReadOnlyOpenEvent(0x20000,false,name)", mocked)
        self.assertIn("ReadOnlyGetSecurityInfo(handle,6,0x14", mocked)
        self.assertNotIn("AccessCheck", mocked)
        self.assertNotIn("DuplicateToken", source)
        self.assertNotIn("QueryInformationJobObject", actual)
        self.assertNotRegex(source, r"static extern [^\n]*QueryInformationJobObject")
        mocked = mocked.replace("Marshal.GetLastWin32Error()", "MockLastError()")
        mocks = r'''
  public static string Scenario="pass";
  public static int OpenCalls, QueryCalls, ReadCloses, QueryFrees, NativeError;
  static IntPtr queryAllocation=IntPtr.Zero;
  static IntPtr queryDacl,querySacl;
  static int MockLastError(){return NativeError;}
  static void Check(bool value,string reason){if(!value)throw new InvalidOperationException(reason);}
  static bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text,uint version,out IntPtr descriptor,IntPtr size){
    Check(text=="D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)" && version==1 && size==IntPtr.Zero,"Original event descriptor changed");
    descriptor=Marshal.AllocHGlobal(20);return true;
  }
  static IntPtr CreateEventExW(ref Attributes attributes,string name,uint flags,uint access){Check(attributes.descriptor!=IntPtr.Zero && attributes.length==24 && attributes.inherit==0 && name==ControlName && flags==0 && access==0x100000,"Original event API/ABI/access changed");return new IntPtr(101);}
  static IntPtr CreateMutexW(ref Attributes attributes,bool owner,string name){throw new InvalidOperationException("Unexpected wrong-object control execution");}
  static IntPtr LocalFree(IntPtr memory){Marshal.FreeHGlobal(memory);NativeError=87;return IntPtr.Zero;}
  static IntPtr ReadOnlyOpenEvent(uint access,bool inherit,string name){
    OpenCalls++;Check(access==0x20000 && !inherit && name==ControlName,"Diagnostic reopen changed READ_CONTROL/inheritance/owned name");
    if(Scenario=="open5" || Scenario=="open0"){NativeError=Scenario=="open0"?0:5;return IntPtr.Zero;}
    NativeError=0;return new IntPtr(102);
  }
  static byte[] Sid(ulong authority,params uint[] parts){
    byte[] data=new byte[8+parts.Length*4];data[0]=1;data[1]=(byte)parts.Length;
    for(int i=7;i>=2;i--){data[i]=(byte)authority;authority>>=8;}
    for(int i=0;i<parts.Length;i++)Array.Copy(BitConverter.GetBytes(parts[i]),0,data,8+i*4,4);return data;
  }
  static byte[] Ace(byte type,uint mask,byte[] sid){
    byte[] data=new byte[8+sid.Length];data[0]=type;Array.Copy(BitConverter.GetBytes((ushort)data.Length),0,data,2,2);Array.Copy(BitConverter.GetBytes(mask),0,data,4,4);Array.Copy(sid,0,data,8,sid.Length);return data;
  }
  static byte[] Acl(params byte[][] aces){
    int size=8;foreach(byte[] ace in aces)size+=ace.Length;byte[] data=new byte[size];data[0]=2;Array.Copy(BitConverter.GetBytes((ushort)size),0,data,2,2);Array.Copy(BitConverter.GetBytes((ushort)aces.Length),0,data,4,2);
    int offset=8;foreach(byte[] ace in aces){Array.Copy(ace,0,data,offset,ace.Length);offset+=ace.Length;}return data;
  }
  static uint ReadOnlyGetSecurityInfo(IntPtr handle,uint objectType,uint information,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor){
    QueryCalls++;Check(handle==new IntPtr(102) && objectType==6 && information==0x14,"Query changed kernel object or DACL+LABEL-only information");
    owner=group=dacl=sacl=descriptor=IntPtr.Zero;NativeError=87;
    if(Scenario=="query5")return 5;
    if(Scenario=="missing-descriptor")return 0;
    byte[] access=Acl(Ace(0,0x100000,Sid(1,0)),Ace(0,0x10000000,Sid(5,32,544)),Ace(0,0x10000000,Sid(5,18)));
    if(Scenario=="other-ace")access=Acl(Ace(0,0x100000,Sid(5,21,123,456,789,1001)));
    if(Scenario=="bad-acl" || Scenario=="no-acl-poison" || Scenario=="absent-dacl-poison"){access[2]=7;access[3]=0;}
    if(Scenario=="bad-ace"){access[10]=255;access[11]=255;}
    if(Scenario=="bad-sid")access[17]=15;
    byte[] labels=Acl(Ace(Scenario=="label-other"?(byte)2:(byte)0x11,1,Sid(16,12288)));
    if(Scenario=="no-acl-poison" || Scenario=="absent-label-poison"){labels[2]=7;labels[3]=0;}
    descriptor=Marshal.AllocHGlobal(20+access.Length+labels.Length);queryAllocation=descriptor;
    dacl=IntPtr.Add(descriptor,20);sacl=IntPtr.Add(dacl,access.Length);
    Marshal.Copy(access,0,dacl,access.Length);Marshal.Copy(labels,0,sacl,labels.Length);
    if(Scenario=="no-acl")dacl=sacl=IntPtr.Zero;
    if(Scenario=="null-dacl")dacl=IntPtr.Zero;
    queryDacl=dacl;querySacl=sacl;
    // Optional GetSecurityInfo ACL pointers are deliberately absent. The exact
    // production getters must read the requested subsets from its descriptor.
    dacl=sacl=IntPtr.Zero;
    return Scenario=="query5-allocation" || Scenario=="query5-free-close"?5u:0u;
  }
  static bool ReadOnlyGetDacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted){Check(descriptor==queryAllocation && descriptor!=IntPtr.Zero,"Unowned DACL descriptor getter");present=Scenario!="no-acl" && Scenario!="no-acl-poison" && Scenario!="absent-dacl-poison";acl=queryDacl;defaulted=false;NativeError=5;return Scenario!="dacl-query5";}
  static bool ReadOnlyGetSacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted){Check(descriptor==queryAllocation && descriptor!=IntPtr.Zero,"Unowned LABEL descriptor getter");present=Scenario!="no-acl" && Scenario!="no-acl-poison" && Scenario!="absent-label-poison";acl=querySacl;defaulted=false;NativeError=0;return Scenario!="sacl-query0";}
  static IntPtr ReadOnlyLocalFree(IntPtr descriptor){
    QueryFrees++;Check(descriptor==queryAllocation && descriptor!=IntPtr.Zero,"Unowned descriptor free");
    if(Scenario=="free6" || Scenario=="query5-free-close"){NativeError=6;return descriptor;}
    Marshal.FreeHGlobal(descriptor);queryAllocation=IntPtr.Zero;NativeError=87;return IntPtr.Zero;
  }
  static bool CloseHandle(IntPtr handle){
    Check(handle==new IntPtr(101) || handle==new IntPtr(102),"Foreign native handle close");
    if(handle==new IntPtr(102)){ReadCloses++;if(Scenario=="close6" || Scenario=="close0" || Scenario=="query5-free-close"){NativeError=Scenario=="close0"?0:6;return false;}}
    NativeError=87;return true;
  }
  public const string ControlName="Global\\Mixel-Remote-Standard-Lease-0123456789abcdef0123456789abcdef";
  public static IntPtr Begin(string scenario){
    Check(Owned.Count==0 && DiagnosticDescriptors.Count==0 && queryAllocation==IntPtr.Zero,"Previous diagnostic leaked ownership");
    Scenario="pass";OpenCalls=QueryCalls=ReadCloses=QueryFrees=0;IntPtr original=WorldEvent(ControlName);Scenario=scenario;return original;
  }
  public static void Drain(){Scenario="pass";ClosePending();Check(Owned.Count==0 && WorldNames.Count==0 && DiagnosticDescriptors.Count==0 && queryAllocation==IntPtr.Zero,"Owned read-only cleanup incomplete");}
  public static void ControlRun(){
    foreach(string scenario in new[]{"pass","no-acl","null-dacl","other-ace","label-other","no-acl-poison","absent-dacl-poison","absent-label-poison"}){
      IntPtr original=Begin(scenario);SafeDescriptorFacts facts=ObserveWorldDescriptor(ControlName,original);
      Check(Owned.Count==1 && Owned.Contains(original) && WorldNames.ContainsKey(original) && DiagnosticDescriptors.Count==0,"Observation replaced or closed original owner");
      Check(OpenCalls==1 && QueryCalls==1 && ReadCloses==1 && QueryFrees==1,"Successful independent query/free/close lifecycle changed");
      if(scenario=="pass")Check(facts.daclAces.Length==3 && facts.daclAces[0].trustee=="World" && facts.daclAces[0].mask==0x100000 && facts.daclAces[1].trustee=="Administrators" && facts.daclAces[2].trustee=="SYSTEM" && facts.labels.Length==1 && facts.labels[0].classification=="mandatory-integrity" && facts.labels[0].integrityRid==12288 && facts.labels[0].policy==1,"Actual bounded ACL/SID/label parser changed");
      if(scenario=="no-acl")Check(!facts.daclPresent && !facts.labelAclPresent && facts.daclAces.Length==0 && facts.labels.Length==0,"Null ACL classified as measured World/label");
      if(scenario=="no-acl-poison")Check(!facts.daclPresent && !facts.labelAclPresent && facts.daclNull && facts.labelAclNull && facts.daclAces.Length==0 && facts.labels.Length==0,"Invalid absent ACL outputs were inspected or classified as present");
      if(scenario=="absent-dacl-poison")Check(!facts.daclPresent && facts.daclNull && facts.daclAces.Length==0 && facts.labelAclPresent && facts.labels.Length==1,"Absent DACL output inspected or valid present label discarded");
      if(scenario=="absent-label-poison")Check(facts.daclPresent && facts.daclAces.Length==3 && !facts.labelAclPresent && facts.labelAclNull && facts.labels.Length==0,"Absent label output inspected or valid present DACL discarded");
      if(scenario=="null-dacl")Check(facts.daclPresent && facts.daclNull && facts.daclAces.Length==0,"Present NULL DACL conflated with absent DACL");
      if(scenario=="other-ace")Check(facts.daclAces.Length==1 && facts.daclAces[0].trustee=="other","Other SID not redacted");
      if(scenario=="label-other")Check(facts.labels[0].classification=="other" && !facts.labels[0].integrityRid.HasValue,"Unknown label invented a mandatory RID");
      Drain();
    }
    foreach(string scenario in new[]{"open5","open0","query5","query5-allocation","missing-descriptor","dacl-query5","sacl-query0","bad-acl","bad-ace","bad-sid","free6","close6","close0","query5-free-close"}){
      IntPtr original=Begin(scenario);Exception failure=null;try{ObserveWorldDescriptor(ControlName,original);}catch(Exception error){failure=error;}
      Check(failure!=null && Owned.Contains(original) && WorldNames.ContainsKey(original),"Failed read-only query was accepted or destroyed original owner");
      bool openFailure=scenario.StartsWith("open");Check(QueryCalls==(openFailure?0:1) && ReadCloses==(openFailure?0:1),"Failure skipped independent read handle close or queried after failed open");
      if(openFailure || scenario.StartsWith("query5") || scenario=="close0" || scenario=="dacl-query5" || scenario=="sacl-query0"){
        var errors=failure is AggregateException?((AggregateException)failure).InnerExceptions:new System.Collections.ObjectModel.ReadOnlyCollection<Exception>(new[]{failure});
        int firstCode=scenario=="open0" || scenario=="close0" || scenario=="sacl-query0"?0:5;
        Check(errors[0] is Win32Exception && ((Win32Exception)errors[0]).NativeErrorCode==firstCode,"Direct API or immediate last error clobbered; unknown native0 accepted");
        if(scenario=="query5-free-close")Check(errors.Count==3 && ((Win32Exception)errors[1]).NativeErrorCode==6 && ((Win32Exception)errors[2]).NativeErrorCode==6,"Original query and both cleanup failures lost/order changed");
      }
      Drain();
    }
    IntPtr retained=Begin("pass");int previous=OpenCalls;bool refused=false;
    try{ObserveWorldDescriptor(ControlName,IntPtr.Zero);}catch(InvalidOperationException){refused=true;}
    Check(refused && OpenCalls==previous,"Unowned handle reached native reopen");refused=false;
    try{ObserveWorldDescriptor(ControlName.Replace("0123456789abcdef0123456789abcdef","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),retained);}catch(InvalidOperationException){refused=true;}
    Check(refused && OpenCalls==previous,"Wrong isolated GUID reached native reopen");Drain();refused=false;
    try{ObserveWorldDescriptor(ControlName,retained);}catch(InvalidOperationException){refused=true;}
    Check(refused && OpenCalls==previous,"Closed owner reached native reopen");
  }
'''
        absent_guard = "    if(!daclPresent) dacl=IntPtr.Zero;\n    if(!saclPresent) sacl=IntPtr.Zero;\n"
        self.assertEqual(mocked.count(absent_guard), 1)
        descriptor_reader = re.search(r'  static SafeDescriptorFacts ReadOnlyDescriptorFacts\(.*?\n  \}', mocked, re.S)
        self.assertIsNotNone(descriptor_reader)
        original_reader = descriptor_reader[0].replace(absent_guard, "").replace("ReadOnlyDescriptorFacts(", "OriginalReadOnlyDescriptorFacts(")
        original_control = r'''
  public static void OriginalAbsentOutputsControl(){
    foreach(string scenario in new[]{"no-acl-poison","absent-dacl-poison","absent-label-poison"}){
      Begin(scenario);IntPtr handle=ReadOnlyOpenEvent(0x20000,false,ControlName);Owned.Add(handle);
      IntPtr owner,group,dacl,sacl,descriptor;Check(ReadOnlyGetSecurityInfo(handle,6,0x14,out owner,out group,out dacl,out sacl,out descriptor)==0,"Original absent-output setup failed");DiagnosticDescriptors.Add(descriptor);
      bool daclPresent,saclPresent,defaulted;Check(ReadOnlyGetDacl(descriptor,out daclPresent,out dacl,out defaulted),"Original absent-output DACL getter failed");Check(ReadOnlyGetSacl(descriptor,out saclPresent,out sacl,out defaulted),"Original absent-output LABEL getter failed");
      bool reproduced=false;try{OriginalReadOnlyDescriptorFacts(daclPresent,dacl,saclPresent,sacl);}catch(InvalidOperationException error){reproduced=error.Message=="Owned descriptor ACL size is invalid";}
      Check(reproduced,"Original ignored Present flag no longer reproduces the allocated poisoned ACL read");
      FreeDiagnosticDescriptor(descriptor);Close(handle);Drain();
    }
  }
'''
        mocked = mocked[:mocked.rindex("}")] + mocks + original_reader + original_control + "\n}"
        job = re.search(r'  public static bool StartedJobMembership\(int pid\) \{.*?\n  \}', source, re.S)
        self.assertIsNotNone(job)
        job_control = r'''
public static class MixelOrdinaryTokenFixture {
  static readonly System.Collections.Generic.Dictionary<int,IntPtr> StartedProcesses=new System.Collections.Generic.Dictionary<int,IntPtr>{{403,new IntPtr(401)}};
  public static int Variant,Calls,Error;
  static int MockLastError(){return Error;}
  static bool IsProcessInJob(IntPtr process,IntPtr job,out bool present){
    Calls++;if(process!=new IntPtr(401) || job!=IntPtr.Zero)throw new InvalidOperationException("Job query changed exact retained process or NULL any-job subject");
    present=Variant==1;Error=Variant==3?0:5;return Variant<2;
  }
''' + job[0].replace("Marshal.GetLastWin32Error()", "MockLastError()") + "\n}"
        with tempfile.TemporaryDirectory(prefix="mixel-owned-context-read-only-") as temporary:
            root = Path(temporary)
            (root / "actual.cs").write_text(actual.replace("class MixelIsolatedLeaseControl", "class CompiledActualWorldDescriptorControl"), encoding="utf-8")
            (root / "mocked.cs").write_text(mocked + "\n" + job_control, encoding="utf-8")
            script = root / "control.ps1"
            script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) {throw 'Required actual PowerShell edition missing'}
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $ControlRoot 'actual.cs')))
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $ControlRoot 'mocked.cs')))
[MixelIsolatedLeaseControl]::OriginalAbsentOutputsControl()
[MixelIsolatedLeaseControl]::ControlRun()
foreach($variant in @(0,1,2,3)) {
  [MixelOrdinaryTokenFixture]::Variant=$variant;[MixelOrdinaryTokenFixture]::Calls=0;$value=$null;$failure=$null
  try{$value=[MixelOrdinaryTokenFixture]::StartedJobMembership(403)}catch{$failure=$_.Exception.InnerException}
  if([MixelOrdinaryTokenFixture]::Calls -ne 1 -or ($variant -lt 2 -and ($failure -or $value -ne ($variant -eq 1))) -or
     ($variant -ge 2 -and ($failure -isnot [ComponentModel.Win32Exception] -or $failure.NativeErrorCode -ne [MixelOrdinaryTokenFixture]::Error))) {throw 'Retained job membership native success/error facts changed'}
}
[MixelOrdinaryTokenFixture]::Calls=0;$failed=$false;try{[void][MixelOrdinaryTokenFixture]::StartedJobMembership(999)}catch{$failed=$true}
if(-not $failed -or [MixelOrdinaryTokenFixture]::Calls -ne 0){throw 'Unowned PID reached native job query'}
$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1'))
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Current source helper does not parse'}
foreach($name in @('Write-LeaseContextFailure','Invoke-LeaseJobObservation','Invoke-LeaseWorldDescriptorObservation')) {
  $items=$ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
  if($items.Count -ne 1){throw 'Exact read-only observation function missing'}
  . ([scriptblock]::Create($items[0].Extent.Text))
}
$script:printed=[Collections.Generic.List[string]]::new();function Write-Host {param($Object)$script:printed.Add([string]$Object)}
$leaseContextFailures=[Collections.Generic.List[Exception]]::new()
[MixelOrdinaryTokenFixture]::Variant=1;Invoke-LeaseJobObservation 403
$owner=[MixelIsolatedLeaseControl]::Begin('pass');Invoke-LeaseWorldDescriptorObservation ([MixelIsolatedLeaseControl]::ControlName) $owner global
[MixelIsolatedLeaseControl]::Drain()
if($leaseContextFailures.Count -ne 0 -or $script:printed.Count -ne 2 -or -not $script:printed[0].Contains('childJobLimitsMeasured=False; namedDirectoryMeasured=False.') -or
   -not $script:printed[1].Contains('information=DACL+LABEL; namedDirectoryMeasured=False;') -or ($script:printed -join '').Contains('S-1-')) {throw 'Observation invented child limits/directory cause, lost successful facts or exported a SID'}
[MixelOrdinaryTokenFixture]::Variant=3;Invoke-LeaseJobObservation 403
$owner=[MixelIsolatedLeaseControl]::Begin('query5-free-close');Invoke-LeaseWorldDescriptorObservation ([MixelIsolatedLeaseControl]::ControlName) $owner local
[MixelIsolatedLeaseControl]::Drain()
if($leaseContextFailures.Count -ne 2 -or -not $script:printed[2].Contains('nativeErrors=0; fatalAfterOriginalGate=True;') -or
   -not $script:printed[3].Contains('nativeErrors=5,6,6; fatalAfterOriginalGate=True;')) {throw 'Deferred fatal failures lost primary/cleanup errors including native0'}
$gate=$ast.FindAll({param($node)$node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($leaseContextFailures.Count -gt 0)')},$true)
if($gate.Count -ne 1 -or $gate[0].Extent.StartOffset -lt $source.IndexOf('PASS: closed wrong-object control leaves no GUID lease') -or
   $gate[0].Extent.StartOffset -lt $source.IndexOf('if ($currentCallerFailure) { throw $currentCallerFailure }')) {throw 'Diagnostic replaced or preceded unchanged original terminal'}
$caught=$null;try{& ([scriptblock]::Create($gate[0].Extent.Text))}catch{$caught=$_.Exception}
if($caught -isnot [AggregateException] -or $caught.InnerExceptions.Count -ne 2){throw 'Deferred query errors accepted after original terminal'}
$leaseContextFailures.Clear();& ([scriptblock]::Create($gate[0].Extent.Text))
$original=$ast.FindAll({param($node)$node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if (-not $ObserveOnly -and ($record.sourceStatus')},$true)
if($original.Count -ne 1){throw 'Original mandatory native failure branch changed'}
$ObserveOnly=$false;$record=[pscustomobject]@{sourceStatus='ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false';rawOpenError=5;attestation='guard-unavailable';requiresClick=$true;ownerFailed=$false}
$ExpectedStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';$ExpectedError=2;$ExpectedProof='';$ExpectedRequired=$false;$ExpectedOwnerFailed=$false
$message='';try{& ([scriptblock]::Create($original[0].Extent.Text))}catch{$message=$_.Exception.Message}
if(-not $message.Contains('classification mismatch') -or -not $message.Contains('"rawOpenError":5')){throw 'Original fatal native5 was relaxed/replaced'}
Microsoft.PowerShell.Utility\Write-Host "Actual read-only context control PowerShell: $($PSVersionTable.PSVersion); $($PSVersionTable.PSEdition)"
Microsoft.PowerShell.Utility\Write-Host 'PASS: exact original WorldEvent API/ACL retained; separate owned READ_CONTROL reopen and direct GetSecurityInfo DACL+LABEL error preserved; three original poisoned absent-ACL failures and eight corrected parsed positive states, fourteen query/descriptor-getter/parse/free/close failures, three ownership refusals and independent cleanup; retained child IsProcessInJob true/false/native5/native0 controls; redacted observations defer and rethrow all errors after unchanged fatal native5 gate. APIs mocked only; no directory cause, Windows/app qualification or token duplication.'
''', encoding="utf-8-sig")
            for engine, edition in self.powershell_engines():
                result = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(root), "-ExpectedEdition", edition], capture_output=True, text=True, encoding="utf-8", timeout=35)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("separate owned READ_CONTROL reopen", result.stdout)
                print(result.stdout, flush=True)

    def test_actual_source_dispatch_runs_reader_before_fatal_native_gate(self):
        bash = shutil.which("bash")
        if os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            if candidate.is_file():
                bash = str(candidate)
        self.assertTrue(bash, "Bash required for executable source ordering controls")
        with tempfile.TemporaryDirectory(prefix="mixel source dispatch préflight ") as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            runner_temp = root / "runner temporary préflight 🚀"
            app = root / "full application préflight 🚀"
            runner_temp.mkdir()
            app.mkdir()
            dispatcher = scripts / "test-windows-generated-source.sh"
            dispatcher.write_bytes((ROOT / "scripts/test-windows-generated-source.sh").read_bytes())
            # Only the native environment and individual test bodies are
            # stand-ins; run the exact dispatcher and its real fatal ordering.
            (scripts / "use-windows-msvc-bash.sh").write_text(r'''
python() {
  "$MIXEL_SOURCE_ORDER_PYTHON" "$MIXEL_SOURCE_ORDER_SHIM" "$@"
}
''', encoding="utf-8", newline="\n")
            python = root / "python-ordering-control.py"
            python.write_text(r'''
import json
import sys
from pathlib import Path
with Path('actual-invocations.log').open('a') as output:
    output.write(sys.argv[1]+'\n')
with Path('actual-argv.jsonl').open('a',encoding='utf-8') as output:
    output.write(json.dumps(sys.argv[1:])+'\n')
if sys.argv[1]=='scripts/test-support-lease.py':
    print('CONTROL: unchanged fatal native gate',flush=True)
    sys.exit(23)
''', encoding="utf-8", newline="\n")
            environment = {**os.environ, "MIXEL_SOURCE_ORDER_PYTHON": Path(sys.executable).resolve().as_posix(),
                           "MIXEL_SOURCE_ORDER_SHIM": python.as_posix(),
                           "RUNNER_TEMP": runner_temp.as_posix(), "RDREPO": app.as_posix(),
                           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            environment.pop("MIXEL_SDK_FULL_APP_PREFLIGHT", None)
            expected_shell = subprocess.check_output([bash, "-c",
                'if [[ "${OS:-}" == Windows_NT ]]; then cygpath -w "$BASH"; else printf "%s\\n" "$BASH"; fi'],
                text=True, encoding="utf-8", timeout=15).strip()
            preceding = [
                "scripts/test-support-invite.py", "scripts/test-generated-support-paths.py",
                "scripts/test-windows-lease-observation.py", "scripts/test-support-lease.py"]
            for full in (None, "", "false", "true", "TRUE", " true"):
                with self.subTest(full_app_request=full):
                    for name in ("actual-invocations.log", "actual-argv.jsonl"):
                        (root / name).unlink(missing_ok=True)
                    case_environment = dict(environment)
                    if full is not None:
                        case_environment["MIXEL_SDK_FULL_APP_PREFLIGHT"] = full
                    result = subprocess.run([bash, str(dispatcher)], cwd=root, env=case_environment,
                                            capture_output=True, text=True, encoding="utf-8", timeout=15)
                    arguments = [json.loads(line) for line in (root / "actual-argv.jsonl").read_text(encoding="utf-8").splitlines()]
                    self.assertEqual(arguments[0][0:2], ["scripts/test-support-flutter-analysis.py", "--dart"])
                    self.assertEqual(arguments[0][3:], ["--bash", expected_shell, "--evidence-dir",
                                                       runner_temp.as_posix() + "/mixel-sdk-source"])
                    self.assertTrue(Path(arguments[0][4]).is_absolute(), "Dispatcher lost the active full Bash path")
                    if full in ("TRUE", " true"):
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertIn("Invalid complete SDK application preflight request", result.stderr)
                        self.assertEqual(len(arguments), 1, "Invalid request reached SDK or native tests")
                        continue
                    self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                    expected_sdk = ["scripts/test-support-flutter-sdk-compatibility.py", "--app"]
                    if full == "true":
                        self.assertEqual(arguments[1][0:4], expected_sdk + [app.as_posix(), "--flutter"])
                        self.assertEqual(arguments[1][5:], ["--bash", expected_shell])
                    else:
                        self.assertEqual(arguments[1], expected_sdk + [runner_temp.as_posix() + "/mixel-sdk-source/pipeline-lf",
                                                                     "--bash", expected_shell])
                    self.assertEqual(arguments[2][0:2], ["scripts/test-support-macos-termination.py", "--flutter"])
                    self.assertEqual(arguments[2][3:], ["--bash", expected_shell])
                    self.assertEqual(arguments[3:], [[name] for name in preceding])
                    self.assertEqual((root / "actual-invocations.log").read_text(encoding="utf-8").splitlines(),
                                     [row[0] for row in arguments])
                    self.assertIn("CONTROL: unchanged fatal native gate", result.stdout)
            print("PASS: exact generated-source dispatcher preserves spaced Unicode runner/application paths, default/source and explicit full-app SDK gates, invalid-request refusal, then Flutter analyzer, SDK, Mac Quit and calling-thread/reader controls before unchanged fatal native23; later tests remain unrun.", flush=True)

    @staticmethod
    def mocked_bridge():
        bridge = lease.STANDARD_THREAD_BRIDGE.replace("    #[cfg(windows)]\n", "")
        pattern = r'        #\[link\(name="(?:kernel32|advapi32)"\)\]\n        extern "system" \{.*?\n        \}\n'
        bridge, count = re.subn(pattern, "", bridge, flags=re.S)
        if count != 2:
            raise AssertionError("Exact read-only Windows token declarations changed")
        return bridge.replace('        struct Facts {', MOCKS + '        struct Facts {', 1)

    def test_complete_windows_cfg_source_compiles_metadata(self):
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        self.assertTrue(rustc, "Rust compiler required for executable source controls")
        target = "x86_64-pc-windows-msvc"
        library = subprocess.run([rustc, "--print", "target-libdir", "--target", target], check=True, capture_output=True, text=True, timeout=15)
        if not list(Path(library.stdout.strip()).glob("libstd-*.rlib")):
            self.skipTest("Windows Rust standard library is unavailable; native Windows CI compiles the fixture")
        with tempfile.TemporaryDirectory(prefix="mixel-thread-token-windows-cfg-") as temporary:
            root = Path(temporary)
            body = lease.standard_windows_fixture_body((ROOT / "scripts/support-invite-guard.rs").read_text(), root, "mixel-standard-control-", "Global\\Mixel-Remote-Standard-Lease-" + "a" * 32)
            for name, header in (("console", ""), ("gui", '#![windows_subsystem = "windows"]\n')):
                source = root / (name + ".rs")
                source.write_text(header + body)
                subprocess.run([rustc, "--edition", "2021", "--target", target, "--emit", "metadata", "-D", "warnings", str(source), "-o", str(root / (name + ".rmeta"))], check=True, capture_output=True, timeout=60)
            print("PASS: complete console/GUI x64 Windows source fixtures compile metadata with -D warnings; no native Windows qualification.", flush=True)

    def test_generated_instrumentation_preserves_calls_and_immediate_error_capture(self):
        original = (ROOT / "scripts/support-invite-guard.rs").read_text()
        body = lease.standard_windows_fixture_body(original, Path("owned-control"), "mixel-standard-control-", "Global\\Mixel-Remote-Standard-Lease-" + "a" * 32)
        self.assertEqual(body.count('fixture_thread_tokens::observe("OpenEventW");'), 2)
        self.assertEqual(body.count('fixture_thread_tokens::observe("CreateEventExW");'), 1)
        self.assertEqual(body.count('fixture_thread_tokens::observe("CreateEventExW-unnamed");'), 1)
        for call in ('CreateEventExW(&attributes, name.as_ptr(), 0, 0x00100000)', 'OpenEventW(0x00100000, 0, name.as_ptr())'):
            self.assertIn(call, body)
        stripped = re.sub(r'        fixture_thread_tokens::observe\("[^"]+"\);\n', '', body).replace(lease.STANDARD_THREAD_BRIDGE, '', 1).replace(lease.STANDARD_JOB_BRIDGE, '', 1).replace(lease.STANDARD_DIRECTORY_BRIDGE, '', 1)
        marker = "\n}\n\npub fn hold_support_invite_attended_lease()"
        expected = lease.isolated_source(original, Path("owned-control"), "mixel-standard-control-", "Global\\Mixel-Remote-Standard-Lease-" + "a" * 32)
        expected = expected.replace(marker, "\n" + lease.standard_unnamed_bridge(original) + marker, 1)
        expected += lease.HARNESS.replace("fn main() {", "fn lease_main() {", 1) + lease.STANDARD_WINDOWS_HARNESS
        self.assertEqual(stripped, expected)
        main=lease.STANDARD_WINDOWS_HARNESS
        self.assertLess(main.index('fixture_enable_thread_tokens(&directory)'),main.index('hold_support_invite_attended_lease();'))
        self.assertLess(main.index('fixture_flush_thread_tokens(&directory,false)'),main.index('std::fs::rename(directory.join("fixture-result.new")'))
        self.assertLess(main.index('fixture_flush_thread_tokens(&directory,true)'),main.index('std::fs::rename(directory.join("fixture-unnamed-result.new")'))
        self.assertLess(main.index('fixture_observe_job_limits(&directory)'),main.index('hold_support_invite_attended_lease();'))
        self.assertLess(main.index('fixture_observe_directory_access(&directory)'),main.index('hold_support_invite_attended_lease();'))
        self.assertIn('.write(true).create_new(true)',lease.STANDARD_DIRECTORY_BRIDGE)
        for forbidden in ("CreateDirectory", "NtQuery", "Bno", "OpenProcess", "OpenThread", "Token", "Impersonate", "SetSecurity", "SetInformation", "Adjust", "DIRECTORY_CREATE", "NtClose"):
            self.assertNotIn(forbidden,lease.STANDARD_DIRECTORY_BRIDGE)
        self.assertIn('.write(true).create_new(true)',lease.STANDARD_JOB_BRIDGE)
        for forbidden in ("AssignProcessToJobObject", "SetInformationJobObject", "CreateJobObject", "OpenJobObject", "AdjustToken", "Impersonate", "SetThreadToken"):
            self.assertNotIn(forbidden,lease.STANDARD_JOB_BRIDGE)
        self.assertIn('.write(true).create_new(true)',lease.STANDARD_THREAD_BRIDGE)
        self.assertNotIn('std::fs::rename',lease.STANDARD_THREAD_BRIDGE)
        for forbidden in ("Impersonate", "RevertToSelf", "SetThreadToken", "SetTokenInformation", "AdjustToken", "NtOpen", "SetSecurity", "LogonUser"):
            self.assertNotIn(forbidden, lease.STANDARD_THREAD_BRIDGE)

    @staticmethod
    def mocked_directory_bridge(prior_creation=False):
        bridge=lease.STANDARD_DIRECTORY_BRIDGE
        if prior_creation:
            delta=r'''                        // Request name-creation access separately on these
                        // existing directories. This only opens handles; no
                        // directory or event object is created or modified.
                        records.push(attempt(open,"global",r"\BaseNamedObjects",4));
                        if session_status=="available" {
                            let path=format!(r"\Sessions\{}\BaseNamedObjects",session);
                            records.push(attempt(open,"session",&path,4));
                        }
'''
            assert bridge.count(delta)==1
            bridge=bridge.replace(delta,'',1)
            field='                "\\\"nameCreationAccessMeasured\\\":{},\\\"objectsCreated\\\":false,",\n'
            assert bridge.count(field)==1
            bridge=bridge.replace(field,'',1).replace('std::process::id(),loader_status=="available",session_status,session_error,','std::process::id(),session_status,session_error,',1).replace('records.len()<=6','records.len()<=4',1).replace('matches!(access,1 | 2 | 4)','matches!(access,1 | 2)',1)
            assert hashlib.sha256(bridge.encode()).hexdigest()=='ef0dbc29cf4b66c621a60f209884ba9e1815130737e37197bf795977e6220974'
        bridge=bridge.replace('    #[cfg(windows)]\n','')
        start=bridge.index('        #[link(name="kernel32")]')
        end=bridge.index('\n        fn layout_valid()',start)
        mocks=r'''
        #[allow(non_snake_case)]
        mod native {
            use super::{c_void,Handle,ObjectAttributes};
            use std::sync::atomic::{AtomicU32,Ordering};
            pub static VARIANT:AtomicU32=AtomicU32::new(0);
            pub static CALLS:AtomicU32=AtomicU32::new(0);
            pub static OPENS:AtomicU32=AtomicU32::new(0);
            pub static CLOSES:AtomicU32=AtomicU32::new(0);
            pub static FREES:AtomicU32=AtomicU32::new(0);
            pub static ERROR:AtomicU32=AtomicU32::new(0);
            pub static ERROR_READS:AtomicU32=AtomicU32::new(0);
            pub unsafe fn GetCurrentProcessId()->u32 { CALLS.fetch_add(1,Ordering::SeqCst);std::process::id() }
            pub unsafe fn ProcessIdToSessionId(pid:u32,out:*mut u32)->i32 {
                CALLS.fetch_add(1,Ordering::SeqCst);assert_eq!(pid,std::process::id());assert_eq!(*out,0);
                let v=VARIANT.load(Ordering::SeqCst);
                if v==9 || v==10 {ERROR.store(if v==10 {0} else {87},Ordering::SeqCst);return 0;}
                *out=if v==16 {u32::MAX} else if v==17 {0} else {2};2
            }
            pub unsafe fn LoadLibraryExW(name:*const u16,file:Handle,flags:u32)->Handle {
                CALLS.fetch_add(1,Ordering::SeqCst);assert!(file.is_null());assert_eq!(flags,0x800);
                let mut len=0;while *name.add(len)!=0 {len+=1;assert!(len<32);}
                assert_eq!(String::from_utf16(std::slice::from_raw_parts(name,len)).unwrap(),"ntdll.dll");
                let v=VARIANT.load(Ordering::SeqCst);
                if v==5 || v==6 {ERROR.store(if v==6 {0} else {126},Ordering::SeqCst);return std::ptr::null_mut();}
                701usize as _
            }
            pub unsafe fn GetProcAddress(module:Handle,name:*const u8)->*mut c_void {
                CALLS.fetch_add(1,Ordering::SeqCst);assert_eq!(module as usize,701);
                assert_eq!(std::ffi::CStr::from_ptr(name.cast()).to_bytes(),b"NtOpenDirectoryObject");
                let v=VARIANT.load(Ordering::SeqCst);
                if v==7 || v==8 {ERROR.store(if v==8 {0} else {127},Ordering::SeqCst);return std::ptr::null_mut();}
                NtOpenDirectoryObject as *mut c_void
            }
            unsafe extern "system" fn NtOpenDirectoryObject(out:*mut Handle,access:u32,oa:*mut ObjectAttributes)->i32 {
                let index=OPENS.fetch_add(1,Ordering::SeqCst);assert!(index<6);assert!(FREES.load(Ordering::SeqCst)==0);
                let v=VARIANT.load(Ordering::SeqCst);let creation_start=if v==9 || v==10 {2} else {4};
                assert!(out.read().is_null());assert_eq!(access,if index<creation_start {1+index%2} else {4});
                let object=&*oa;assert_eq!(object.length,48);assert!(object.root.is_null());assert_eq!(object.attributes,0x40);
                assert!(object.security.is_null() && object.quality.is_null());assert!(!object.name.is_null());
                let bytes=std::slice::from_raw_parts(oa.cast::<u8>(),48);assert_eq!(&bytes[4..8],[0;4]);assert_eq!(&bytes[28..32],[0;4]);
                let name=&*object.name;assert_eq!(name.maximum_length,name.length+2);assert!(name.length>0 && name.length<254 && name.length%2==0);
                assert_eq!(&std::slice::from_raw_parts(object.name.cast::<u8>(),16)[4..8],[0;4]);
                let wide=std::slice::from_raw_parts(name.buffer,name.maximum_length as usize/2);assert_eq!(wide.last(),Some(&0));
                let path=String::from_utf16(&wide[..wide.len()-1]).unwrap();
                let session=if v==16 {u32::MAX} else if v==17 {0} else {2};
                assert_eq!(path,if index<2 || index==creation_start {r"\BaseNamedObjects".to_owned()} else {format!(r"\Sessions\{}\BaseNamedObjects",session)});
                ERROR.store(777,Ordering::SeqCst); // NTSTATUS must not read this stale Win32 error.
                if v==2 || (v==1 && index==0) {return if v==2 {0xc0000034u32 as i32} else {0xc0000022u32 as i32};}
                if v==13 && index==0 {return 0;}
                if v==14 && index==0 {*out=999usize as _;return 0xc000000du32 as i32;}
                if v==15 && index==0 {return 1;}
                if (v==20 && index==4) || (v==21 && index>=4) {return 0xc0000022u32 as i32;}
                if v==23 && index>=4 {return 0xc0000034u32 as i32;}
                if v==24 && index==5 {*out=999usize as _;return 0xc000000du32 as i32;}
                *out=(801+index as usize) as _;0
            }
            pub unsafe fn CloseHandle(handle:Handle)->i32 {
                let h=handle as usize;assert!((801..=806).contains(&h));assert_eq!(FREES.load(Ordering::SeqCst),0);
                CLOSES.fetch_add(1,Ordering::SeqCst);let v=VARIANT.load(Ordering::SeqCst);
                if h==801 && matches!(v,3|4|18) {ERROR.store(if v==3 {5} else if v==18 {u32::MAX} else {0},Ordering::SeqCst);return 0;}
                if h==805 && v==22 {ERROR.store(0,Ordering::SeqCst);return 0;}
                2
            }
            pub unsafe fn FreeLibrary(module:Handle)->i32 {
                assert_eq!(module as usize,701);assert_eq!(FREES.fetch_add(1,Ordering::SeqCst),0);
                let v=VARIANT.load(Ordering::SeqCst);if v==11 || v==12 {ERROR.store(if v==12 {0} else {5},Ordering::SeqCst);return 0;}2
            }
            pub unsafe fn GetLastError()->u32 {ERROR_READS.fetch_add(1,Ordering::SeqCst);ERROR.load(Ordering::SeqCst)}
        }
        use native::{GetCurrentProcessId,ProcessIdToSessionId,LoadLibraryExW,GetProcAddress,FreeLibrary,CloseHandle,GetLastError};
'''
        bridge=bridge[:start]+mocks+bridge[end:]
        # The real layout function remains byte-exact and executes. This sole
        # fixture-only branch control exercises its unavailable-layout result.
        bridge=bridge.replace('if layout_valid() {','if layout_valid() && native::VARIANT.load(std::sync::atomic::Ordering::SeqCst)!=19 {',1)
        boundary='\n    }\n    pub(super) fn fixture_observe_directory_access'
        controls=r'''
        pub(super) fn set_variant(value:u32) {native::VARIANT.store(value,std::sync::atomic::Ordering::SeqCst);}
        pub(super) fn counts()->(u32,u32,u32,u32,u32) {
            use std::sync::atomic::Ordering;
            (native::CALLS.load(Ordering::SeqCst),native::OPENS.load(Ordering::SeqCst),native::CLOSES.load(Ordering::SeqCst),native::FREES.load(Ordering::SeqCst),native::ERROR_READS.load(Ordering::SeqCst))
        }
'''
        assert bridge.count(boundary)==1
        return bridge.replace(boundary,controls+boundary,1)

    def test_explicit_directory_actual_exported_main_layout_status_and_ownership(self):
        rustc=os.environ.get('RUSTC_BIN') or shutil.which('rustc');self.assertTrue(rustc)
        bridge=self.mocked_directory_bridge()
        prelude=r'''
mod mixel_support_lease {
'''+bridge+r'''
    pub(super) fn fixture_enable_thread_tokens(_directory:&std::path::Path) {}
    pub(super) fn fixture_flush_thread_tokens(_directory:&std::path::Path,_unnamed:bool) {}
    pub(super) fn fixture_observe_job_limits(_directory:&std::path::Path) {}
    pub(super) fn fixture_raw_open_error()->u32 {2}
    pub(super) fn fixture_unnamed_create()->(&'static str,bool,u32,bool,u32) {panic!("Not requested")}
    pub(super) fn variant(value:u32) {fixture_directory_access::set_variant(value);}
    pub(super) fn counts()->(u32,u32,u32,u32,u32) {fixture_directory_access::counts()}
}
fn lease_main() {panic!("Not requested")}
fn hold_support_invite_attended_lease()->bool {false}
fn support_invite_lease_status()->&'static str {"ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false"}
fn support_invite_attestation()->&'static str {""}
fn support_invite_requires_click()->bool {false}
fn support_invite_owner_lease_failed()->bool {false}
fn main() {
    let directory=std::env::current_exe().unwrap().parent().unwrap().to_owned();
    let variant=std::env::var("MIXEL_DIRECTORY_VARIANT").unwrap().parse().unwrap();
    mixel_support_lease::variant(variant);
    std::fs::write(directory.join(format!("fixture-release-{}",std::process::id())),"verified").unwrap();
    if std::env::var_os("MIXEL_DIRECTORY_PRIOR").is_some() {prior_fixture_main();} else {fixture_main();}
    println!("counts={:?}",mixel_support_lease::counts());
}
'''
        harness=lease.STANDARD_WINDOWS_HARNESS.replace('fn main() {','fn fixture_main() {',1).replace('    #[cfg(windows)] ','    ')
        refusal='    #[cfg(not(windows))]\n    panic!("This standard-account functional control requires actual Windows");\n'
        self.assertEqual(harness.count(refusal),1);harness=harness.replace(refusal,'',1)
        call='        mixel_support_lease::fixture_observe_directory_access(&directory);\n'
        self.assertEqual(harness.count(call),1)
        prior=harness.replace(call,'',1).replace('fn fixture_main() {','fn prior_fixture_main() {',1)
        with tempfile.TemporaryDirectory(prefix='mixel-explicit-directory-main-') as temporary:
            root=Path(temporary);source=root/'source.rs';source.write_text(prelude+harness+prior)
            binary=root/('control.exe' if os.name=='nt' else 'control')
            subprocess.run([rustc,'--edition','2021','-D','warnings',str(source),'-o',str(binary)],check=True,capture_output=True,timeout=60)
            # Execute the SHA-bound actual f894 observer through the same
            # exported main: its four old rights cannot measure access4.
            prior_source=root/'prior-creation.rs'
            prior_source.write_text(prelude.replace(bridge,self.mocked_directory_bridge(prior_creation=True),1)+harness+prior)
            prior_binary=root/('prior-creation.exe' if os.name=='nt' else 'prior-creation')
            subprocess.run([rustc,'--edition','2021','-D','warnings',str(prior_source),'-o',str(prior_binary)],check=True,capture_output=True,timeout=60)
            def run(path,variant=0,prior=False):
                path.mkdir();owned=path/binary.name;shutil.copy2(binary,owned)
                (path/'fixture-mode.txt').write_text('probe')
                env={**os.environ,'MIXEL_DIRECTORY_VARIANT':str(variant)}
                if prior:env['MIXEL_DIRECTORY_PRIOR']='1'
                return owned,env
            owned,env=run(root/'absent');result=subprocess.run([str(owned)],env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('counts=(0, 0, 0, 0, 0)',result.stdout)
            self.assertFalse((owned.parent/'fixture-directory-access.json').exists())
            owned,env=run(root/'prior-request',prior=True);(owned.parent/'fixture-directory-access.request').write_text('observe')
            result=subprocess.run([str(owned)],env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('counts=(0, 0, 0, 0, 0)',result.stdout)
            self.assertFalse((owned.parent/'fixture-directory-access.json').exists());self.assertTrue((owned.parent/'fixture-result.json').exists())
            original=root/'actual-f894';original.mkdir();old=original/prior_binary.name;shutil.copy2(prior_binary,old)
            (original/'fixture-mode.txt').write_text('probe');(original/'fixture-directory-access.request').write_text('observe')
            result=subprocess.run([str(old)],env={**os.environ,'MIXEL_DIRECTORY_VARIANT':'0'},capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('counts=(4, 4, 4, 1, 0)',result.stdout)
            old_record=json.loads((original/'fixture-directory-access.json').read_text())
            self.assertEqual([row['access'] for row in old_record['records']],[1,2,1,2])
            self.assertNotIn('nameCreationAccessMeasured',old_record);self.assertFalse(any(row['access']==4 for row in old_record['records']))
            values=[]
            for variant in range(25):
                owned,env=run(root/str(variant),variant);(owned.parent/'fixture-directory-access.request').write_text('observe')
                result=subprocess.run([str(owned)],env=env,capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                value=json.loads((owned.parent/'fixture-directory-access.json').read_text());values.append(value)
                actual=json.loads((owned.parent/'fixture-result.json').read_text());self.assertEqual(value['pid'],actual['pid'])
                self.assertEqual(actual['rawOpenError'],2);self.assertEqual(actual['attestation'],'')
                expected_open=0 if variant in (5,6,7,8,19) else 3 if variant in (9,10) else 6
                expected_close=0 if variant in (2,5,6,7,8,19) else expected_open-1 if variant in (1,13,14,15,20,24) else expected_open-2 if variant in (21,23) else expected_open
                expected_free=0 if variant in (5,6,19) else 1
                expected_errors=1 if variant in (3,4,5,6,7,8,9,10,11,12,18,22) else 0
                counts=re.search(r'counts=\((\d+), (\d+), (\d+), (\d+), (\d+)\)',result.stdout);self.assertIsNotNone(counts)
                self.assertEqual(tuple(map(int,counts.groups()))[1:],(expected_open,expected_close,expected_free,expected_errors))
                self.assertEqual(value['nameCreationAccessMeasured'],expected_open>0);self.assertIs(value['objectsCreated'],False)
                if expected_open==6:
                    self.assertEqual([(row['directory'],row['access']) for row in value['records']],[('global',1),('global',2),('session',1),('session',2),('global',4),('session',4)])
                elif expected_open==3:self.assertEqual([row['access'] for row in value['records']],[1,2,4])
                if variant==19:self.assertEqual(value['loaderStatus'],'layout-error');self.assertEqual(value['sessionStatus'],'unqueried')
                if variant in (1,2):self.assertTrue(all(row['ntStatus']==-1073741772 for row in value['records']) if variant==2 else value['records'][0]['ntStatus']==-1073741790)
                if variant in (3,4,18):self.assertEqual(value['records'][0]['closeError'],5 if variant==3 else 4294967295 if variant==18 else 0)
                if variant in (13,14,15):self.assertEqual(value['records'][0]['status'],'contract-error');self.assertEqual(value['records'][0]['closeStatus'],'not-opened')
                if variant in (16,17):self.assertEqual(value['sessionId'],4294967295 if variant==16 else 0)
                if variant in (20,21,22,23,24):
                    self.assertEqual(value['records'][:4],values[0]['records'][:4])
                    if variant in (20,21):self.assertEqual(value['records'][4]['ntStatus'],-1073741790)
                    if variant==21:self.assertEqual(value['records'][5]['ntStatus'],-1073741790)
                    if variant==22:self.assertEqual(value['records'][4]['closeStatus'],'failed');self.assertEqual(value['records'][4]['closeError'],0);self.assertEqual(value['records'][5]['closeStatus'],'closed')
                    if variant==23:self.assertTrue(all(row['ntStatus']==-1073741772 for row in value['records'][4:]))
                    if variant==24:self.assertEqual(value['records'][5]['status'],'contract-error');self.assertEqual(value['records'][5]['closeStatus'],'not-opened')
                saved=(owned.parent/'fixture-directory-access.json').read_bytes();(owned.parent/'fixture-result.json').unlink()
                duplicate=subprocess.run([str(owned)],env=env,capture_output=True,text=True,timeout=15)
                self.assertNotEqual(duplicate.returncode,0);self.assertIn('AlreadyExists',duplicate.stderr)
                self.assertEqual((owned.parent/'fixture-directory-access.json').read_bytes(),saved);self.assertFalse((owned.parent/'fixture-result.json').exists())
            owned,env=run(root/'invalid-request');(owned.parent/'fixture-directory-access.request').write_text('unexpected')
            result=subprocess.run([str(owned)],env=env,capture_output=True,text=True,timeout=15)
            self.assertNotEqual(result.returncode,0);self.assertIn('Invalid owned explicit-directory',result.stderr)
            self.assertFalse((owned.parent/'fixture-result.json').exists())
        print('PASS: exact exported source main and SHA-bound f894 observer reproduce unmeasured access4; original four query/traverse tuples conserved; absent request makes zero calls; corrected25 native API/layout/status/error0/ownership cases add two independent name-creation access requests without object creation, preserve original event result and immutable publication. APIs mocked, no actual Win32 directory route/cause qualification.',flush=True)

    def test_explicit_directory_actual_reader_strict_scope_and_deferred_failures(self):
        def row(index):
            global_directory=index<2 or index==4
            return dict(directory='global' if global_directory else 'session',path='\\BaseNamedObjects' if global_directory else '\\Sessions\\2\\BaseNamedObjects',access=1+index%2 if index<4 else 4,ntStatus=0,status='opened',handleReturned=True,closeStatus='closed',closeError=0)
        good=dict(schema=1,pid=4321,scope='ExplicitDirectoryAccessOnly',route='ActualEventRouteUnmeasured',nameCreationAccessMeasured=True,objectsCreated=False,sessionStatus='available',sessionError=0,sessionId=2,loaderStatus='available',loaderError=0,unloadStatus='closed',unloadError=0,records=[row(i) for i in range(6)])
        cases=[dict(record=good,accept=True,failure=False)]
        def mutated(key,value,accept=False,failure=False,index=None):
            record=json.loads(json.dumps(good))
            if index is None:record[key]=value
            else:record['records'][index][key]=value
            cases.append(dict(record=record,accept=accept,failure=failure))
        for key,value in [('schema',True),('pid',4322),('scope','ActualEventRoute'),('route',['ActualEventRouteUnmeasured']),('nameCreationAccessMeasured',False),('nameCreationAccessMeasured',1),('objectsCreated',True),('objectsCreated',0),('sessionId',-1),('sessionId',True),('sessionStatus',['available']),('sessionError',5),('loaderError',1),('unloadError',1),('records',{}),('records',good['records'][:4]),('records',good['records'][:5]),('extra',0)]:mutated(key,value)
        for key,value in [('access',4),('access',True),('access',2),('path','\\Sessions\\99\\BaseNamedObjects'),('directory',['global']),('ntStatus',4294967295),('ntStatus',-2147483649),('ntStatus',True),('ntStatus',1),('handleReturned',1),('status',['opened']),('closeStatus','not-opened'),('closeError',True),('closeError',4294967296),('extra',0)]:mutated(key,value,index=0)
        for error in [0,5,4294967295]:
            record=json.loads(json.dumps(good));record['records'][0].update(closeStatus='failed',closeError=error);cases.append(dict(record=record,accept=True,failure=True))
            record=json.loads(json.dumps(good));record['records'][4].update(closeStatus='failed',closeError=error);cases.append(dict(record=record,accept=True,failure=True))
        for index in [4,5]:
            for key,value in [('access',1),('access',2),('access',8),('access',True),('path','\\BaseNamedObjects' if index==5 else '\\Sessions\\2\\BaseNamedObjects')]:mutated(key,value,index=index)
            record=json.loads(json.dumps(good));record['records'][index].update(ntStatus=-1073741790,status='nt-result',handleReturned=False,closeStatus='not-opened');cases.append(dict(record=record,accept=True,failure=False))
        for nt,returned in [(-1073741790,False),(-1073741772,False),(-2147483648,False),(-1073741811,True),(0,False),(1,False),(2147483647,False)]:
            record=json.loads(json.dumps(good));record['records'][0].update(ntStatus=nt,handleReturned=returned,status='contract-error' if nt>=0 or returned else 'nt-result',closeStatus='not-opened');cases.append(dict(record=record,accept=True,failure=nt>=0 or returned))
        for kind in ['session','loader','unload']:
            for error in [0,5,4294967295]:
                record=json.loads(json.dumps(good));record[kind+'Error']=error
                if kind=='session':record.update(sessionStatus='error',sessionId=None,records=record['records'][:2]+[record['records'][4]])
                elif kind=='loader':record.update(loaderStatus='load-error',unloadStatus='not-loaded',nameCreationAccessMeasured=False,records=[])
                else:record.update(unloadStatus='failed')
                cases.append(dict(record=record,accept=True,failure=True))
        record=json.loads(json.dumps(good));record.update(loaderStatus='export-error',loaderError=127,nameCreationAccessMeasured=False,records=[]);cases.append(dict(record=record,accept=True,failure=True))
        record=json.loads(json.dumps(good));record.update(loaderStatus='layout-error',sessionStatus='unqueried',sessionId=None,unloadStatus='not-loaded',nameCreationAccessMeasured=False,records=[]);cases.append(dict(record=record,accept=True,failure=True))
        for session in [0,4294967295]:
            record=json.loads(json.dumps(good));record['sessionId']=session
            for item in record['records']:
                if item['directory']=='session':item['path']=f'\\Sessions\\{session}\\BaseNamedObjects'
            cases.append(dict(record=record,accept=True,failure=False))
        with tempfile.TemporaryDirectory(prefix='mixel-explicit-directory-reader-') as temporary:
            root=Path(temporary);(root/'cases.json').write_text(json.dumps(cases));script=root/'control.ps1'
            script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))){throw 'Actual required PowerShell edition missing'}
$fixtureRoot=$ControlRoot;$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1'))
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors);if($errors.Count){throw 'Current helper does not parse'}
foreach($name in @('Write-LeaseContextFailure','Read-StandardChildDirectoryAccess','Invoke-LeaseChildDirectoryAccessObservation')) {
 $items=$ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
 if($items.Count -ne 1){throw 'Exact directory reader/observer missing'}
 . ([scriptblock]::Create($items[0].Extent.Text))
}
$script:printed=[Collections.Generic.List[string]]::new();function Write-Host {param($Object)$script:printed.Add([string]$Object)}
$leaseContextFailures=[Collections.Generic.List[Exception]]::new();$path=Join-Path $fixtureRoot 'fixture-directory-access.json'
$cases=[IO.File]::ReadAllText((Join-Path $fixtureRoot 'cases.json')) | ConvertFrom-Json
foreach($case in $cases) {
 [IO.File]::WriteAllText($path,($case.record | ConvertTo-Json -Depth 7 -Compress))
 $message='';try{[void](Read-StandardChildDirectoryAccess 4321)}catch{$message=$_.Exception.Message}
 if(($message -ceq '') -ne $case.accept){throw "Directory strict acceptance changed: $message"}
 if($case.accept) {
  $leaseContextFailures.Clear();$script:printed.Clear();Invoke-LeaseChildDirectoryAccessObservation 4321
  $expectedFlag=[string][bool]$case.record.nameCreationAccessMeasured
  if(($leaseContextFailures.Count -ne 0) -ne $case.failure -or -not $script:printed[0].Contains("ExplicitDirectoryAccessOnly=True; ActualEventRouteUnmeasured=True; nameCreationAccessMeasured=$expectedFlag; objectsCreated=False; causeEstablished=False;")){throw 'Directory measurement failure was hidden or event route was inferred'}
  if($case.failure -and (-not $script:printed[1].Contains('fatalAfterOriginalGate=True;') -or -not $script:printed[1].Contains('operation=explicit-directory-access;'))){throw 'Directory error replaced original native gate'}
  if($case.record.records.Count -and $case.record.records[0].closeStatus -ceq 'failed' -and $case.record.records[0].closeError -eq 0 -and -not $script:printed[1].Contains('nativeErrors=0;')){throw 'Directory close error0 was discarded'}
  if($case.record.unloadStatus -ceq 'failed' -and $case.record.unloadError -eq [uint32]::MaxValue -and -not $script:printed[1].Contains('nativeErrors=-1;')){throw 'Raw DWORD module error bits changed'}
 }
}
$object=$cases[0].record | ConvertTo-Json -Depth 7 -Compress
foreach($invalid in @(('['+$object+']'),('[['+$object+']]'))) {
 [IO.File]::WriteAllText($path,$invalid);$message='';try{[void](Read-StandardChildDirectoryAccess 4321)}catch{$message=$_.Exception.Message}
 if($message -cne 'Owned explicit-directory root must be an object.'){throw 'Singleton/nested directory envelope reached object conversion'}
}
[IO.File]::WriteAllText($path,(' '*4097));$message='';try{[void](Read-StandardChildDirectoryAccess 4321)}catch{$message=$_.Exception.Message};if(-not $message){throw 'Oversize directory result accepted'}
Remove-Item -LiteralPath $path;$message='';try{[void](Read-StandardChildDirectoryAccess 4321)}catch{$message=$_.Exception.Message};if(-not $message){throw 'Missing directory result accepted'}
$original=$ast.FindAll({param($node)$node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if (-not $ObserveOnly -and ($record.sourceStatus')},$true)
if($original.Count -ne 1){throw 'Original mandatory native gate changed'}
$ObserveOnly=$false;$record=[pscustomobject]@{sourceStatus='ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false';rawOpenError=5;attestation='guard-unavailable';requiresClick=$true;ownerFailed=$false}
$ExpectedStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';$ExpectedError=2;$ExpectedProof='';$ExpectedRequired=$false;$ExpectedOwnerFailed=$false
$message='';try{& ([scriptblock]::Create($original[0].Extent.Text))}catch{$message=$_.Exception.Message}
if(-not $message.Contains('classification mismatch') -or -not $message.Contains('"rawOpenError":5')){throw 'Original fatal native5 was replaced'}
Microsoft.PowerShell.Utility\Write-Host 'PASS: literal explicit-directory reader retains exact query1/traverse2 paths and adds independent name-creation access4/self-session/signed NTSTATUS/ownership; rejects missing/duplicate/changed rights and false creation claims, malformed/root-array/missing/oversize facts; preserves close0 and deferred errors behind unchanged fatal native5. Existing directory opens only, no objects created; actual event route unmeasured.'
''',encoding='utf-8-sig')
            for engine,edition in self.powershell_engines():
                result=subprocess.run([engine,'-NoProfile','-File',str(script),'-Repository',str(ROOT),'-ControlRoot',str(root),'-ExpectedEdition',edition],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr);print(result.stdout,flush=True)

    def test_child_self_job_queries_exact_layout_errors_and_immutable_publication(self):
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        self.assertTrue(rustc)
        bridge = lease.STANDARD_JOB_BRIDGE.replace("    #[cfg(windows)]\n", "")
        start = bridge.index('        #[link(name="kernel32")]')
        end = bridge.index('\n        fn query(', start)
        mocked = r'''
        #[allow(non_snake_case)]
        mod native {
            use super::{c_void,Handle,ExtendedLimit};
            use std::sync::atomic::{AtomicU32,Ordering};
            pub static VARIANT:AtomicU32=AtomicU32::new(0);
            pub static ERROR:AtomicU32=AtomicU32::new(0);
            pub static CALLS:AtomicU32=AtomicU32::new(0);
            pub unsafe fn GetCurrentProcess()->Handle {101usize as _}
            pub unsafe fn GetLastError()->u32 {ERROR.load(Ordering::SeqCst)}
            pub unsafe fn IsProcessInJob(process:Handle,job:Handle,present:*mut i32)->i32 {
                assert_eq!(process as usize,101);assert!(job.is_null());assert_eq!(*present,0);
                let v=VARIANT.load(Ordering::SeqCst);*present=if v==1 {0} else {1};
                ERROR.store(if v==3 {0} else {5},Ordering::SeqCst);
                if v==2 || v==3 {0} else {1}
            }
            pub unsafe fn QueryInformationJobObject(job:Handle,kind:i32,data:*mut c_void,size:u32,returned:*mut u32)->i32 {
                assert!(job.is_null());assert_eq!(*returned,0);
                assert!(matches!((kind,size),(4,4)|(9,144)));
                assert!(std::slice::from_raw_parts(data.cast::<u8>(),size as usize).iter().all(|x|*x==0));
                let order=CALLS.fetch_add(1,Ordering::SeqCst);assert_eq!(kind,if order%2==0 {4} else {9});
                let v=VARIANT.load(Ordering::SeqCst);
                if kind==4 {*(data as *mut u32)=if v==12 {u32::MAX} else {64};}
                else {(*(data as *mut ExtendedLimit)).basic.flags=if v==12 {u32::MAX} else {8192};}
                *returned=if kind==4 && v==8 {3} else if kind==9 && v==9 {143}
                    else if kind==4 && v==10 {5} else if kind==9 && v==11 {145} else {size};
                let fail=(kind==4 && matches!(v,4|5)) || (kind==9 && matches!(v,6|7|13));
                ERROR.store(if !fail || matches!(v,5|7) {0} else if v==13 {u32::MAX} else if kind==4 {5} else {87},Ordering::SeqCst);
                if fail {0} else {1}
            }
        }
        use native::{GetCurrentProcess,GetLastError,IsProcessInJob,QueryInformationJobObject};
'''
        bridge = bridge[:start] + mocked + bridge[end:]
        tests = r'''
        #[test]
        fn actual_job_observer() {
            use std::sync::atomic::Ordering;
            let root=std::path::PathBuf::from(std::env::var("MIXEL_JOB_CONTROL_DIR").unwrap());
            observe(&root);assert_eq!(native::CALLS.load(Ordering::SeqCst),0);
            assert!(!root.join("fixture-job-limits.json").exists());
            for variant in 0..14 {
                let dir=root.join(variant.to_string());std::fs::create_dir(&dir).unwrap();
                std::fs::write(dir.join("fixture-job-limits.request"),"observe").unwrap();
                native::VARIANT.store(variant,Ordering::SeqCst);native::CALLS.store(0,Ordering::SeqCst);
                observe(&dir);
                let text=std::fs::read_to_string(dir.join("fixture-job-limits.json")).unwrap();
                assert_eq!(native::CALLS.load(Ordering::SeqCst),if matches!(variant,1|2|3) {0} else {2});
                assert!(text.contains(&format!("\"pid\":{}",std::process::id())));
                match variant {
                    0=>{assert!(text.contains("\"uiFlags\":64"));assert!(text.contains("\"extendedFlags\":8192"));},
                    1=>{assert!(text.contains("\"inAnyJob\":false"));assert!(text.contains("\"uiStatus\":\"not-in-job\""));},
                    2=>assert!(text.contains("\"membershipStatus\":\"error\",\"membershipError\":5")),
                    3=>assert!(text.contains("\"membershipStatus\":\"error\",\"membershipError\":0")),
                    4=>assert!(text.contains("\"uiStatus\":\"error\",\"uiError\":5,\"uiReturned\":4,\"uiFlags\":null")),
                    5=>assert!(text.contains("\"uiStatus\":\"error\",\"uiError\":0,\"uiReturned\":4,\"uiFlags\":null")),
                    6=>assert!(text.contains("\"extendedStatus\":\"error\",\"extendedError\":87")),
                    7=>assert!(text.contains("\"extendedStatus\":\"error\",\"extendedError\":0")),
                    8=>assert!(text.contains("\"uiStatus\":\"size\",\"uiError\":0,\"uiReturned\":3,\"uiFlags\":null")),
                    9=>assert!(text.contains("\"extendedStatus\":\"size\",\"extendedError\":0,\"extendedReturned\":143,\"extendedFlags\":null")),
                    10=>assert!(text.contains("\"uiReturned\":5,\"uiFlags\":null")),
                    11=>assert!(text.contains("\"extendedReturned\":145,\"extendedFlags\":null")),
                    12=>{assert!(text.contains("\"uiFlags\":4294967295"));assert!(text.contains("\"extendedFlags\":4294967295"));},
                    13=>assert!(text.contains("\"extendedError\":4294967295")),
                    _=>unreachable!(),
                }
                if matches!(variant,2|3) {assert!(text.contains("\"inAnyJob\":null"));assert!(text.contains("\"uiFlags\":null"));}
                let before=std::fs::read(dir.join("fixture-job-limits.json")).unwrap();
                assert!(std::panic::catch_unwind(||observe(&dir)).is_err());
                assert_eq!(std::fs::read(dir.join("fixture-job-limits.json")).unwrap(),before);
            }
        }
'''
        boundary = '\n    }\n    pub(super) fn fixture_observe_job_limits'
        self.assertEqual(bridge.count(boundary), 1)
        bridge = bridge.replace(boundary, tests + boundary, 1)
        with tempfile.TemporaryDirectory(prefix="mixel-job-self-source-") as temporary:
            root = Path(temporary)
            source = root / "control.rs"
            source.write_text("mod fixture {\n" + bridge + '\n#[test] fn exported_absence() {let root=std::path::PathBuf::from(std::env::var("MIXEL_JOB_CONTROL_DIR").unwrap());fixture_observe_job_limits(&root);}\n}\n')
            binary = root / ("control.exe" if os.name == "nt" else "control")
            subprocess.run([rustc, "--edition", "2021", "-D", "warnings", "--test", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=60)
            output = subprocess.run([str(binary), "--test-threads=1"], check=True, capture_output=True, text=True, timeout=15, env={**os.environ, "MIXEL_JOB_CONTROL_DIR": str(root)})
            self.assertIn("2 passed; 0 failed", output.stdout)
            for variant in range(14):
                value = json.loads((root / str(variant) / "fixture-job-limits.json").read_text())
                self.assertEqual(value["schema"], 1)
                self.assertGreater(value["pid"], 0)
            print("PASS: literal child-self observer exact zeroed x64 ABI/NULL queries, fourteen membership/flags/error0/error5/size/native-DWORD cases, both query attempts, request absence and immutable duplicate refusal; mocked APIs only.", flush=True)

    def test_actual_child_job_reader_and_deferred_error_facts(self):
        good = dict(schema=1,pid=4321,inAnyJob=True,membershipStatus="available",membershipError=0,
                    uiStatus="available",uiError=0,uiReturned=4,uiFlags=64,
                    extendedStatus="available",extendedError=0,extendedReturned=144,extendedFlags=8192)
        cases = [(good, True)]
        for key,value in (("schema",True),("pid","4321"),("pid",4322),("extra","refused"),
                          ("membershipStatus",["available"]),("inAnyJob",1),("membershipError",5),
                          ("uiStatus",["available"]),("extendedStatus",["available"]),
                          ("uiFlags",-1),("extendedFlags",4294967296),("uiFlags",True),
                          ("uiReturned",3),("extendedReturned",143),("uiError",5)):
            cases.append(({**good,key:value},False))
        for kind in ("ui","extended"):
            cases.append(({**good,kind+"Status":"invented"},False))
            cases.append(({**good,kind+"Status":"size",kind+"Flags":None},False))
            cases.append(({**good,kind+"Status":"error"},False))
        positives = [good, {**good,"uiFlags":4294967295,"extendedFlags":4294967295}]
        for kind,length in (("ui",4),("extended",144)):
            for error in (0,5,4294967295):
                positives.append({**good,kind+"Status":"error",kind+"Error":error,kind+"Flags":None})
            positives.append({**good,kind+"Status":"size",kind+"Returned":length-1,kind+"Flags":None})
        for failed,error in ((False,0),(True,0),(True,5)):
            positives.append({**good,"membershipStatus":"error" if failed else "available","membershipError":error,
                              "inAnyJob":None if failed else False,
                              **{kind+field:value for kind in ("ui","extended") for field,value in (("Status","membership-error" if failed else "not-in-job"),("Error",0),("Returned",0),("Flags",None))}})
        cases.extend((value,True) for value in positives[1:])
        with tempfile.TemporaryDirectory(prefix="mixel-job-reader-") as temporary:
            root=Path(temporary)
            (root/"cases.json").write_text(json.dumps([{"record":v,"accept":a} for v,a in cases]))
            script=root/"control.ps1"
            script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) {throw 'Required actual PowerShell edition missing'}
$fixtureRoot=$ControlRoot
$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1'))
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Current helper does not parse'}
foreach($name in @('Write-LeaseContextFailure','Read-StandardChildJobLimits','Invoke-LeaseChildJobLimitsObservation')) {
 $items=$ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
 if($items.Count -ne 1){throw 'Exact child job reader/observer missing'}
 . ([scriptblock]::Create($items[0].Extent.Text))
 if($name -ceq 'Read-StandardChildJobLimits') {
  $rootGuard=@'
  if (-not $text.TrimStart().StartsWith('{') -or -not $text.TrimEnd().EndsWith('}')) { throw 'Owned child job-limit root must be an object.' }
'@
  $body=$items[0].Extent.Text
  if(-not $body.Contains($rootGuard) -or $body.IndexOf($rootGuard) -ne $body.LastIndexOf($rootGuard)){throw 'Exact root envelope guard changed'}
  $originalBody=$body.Replace($rootGuard,'').Replace('function Read-StandardChildJobLimits(','function Read-OriginalChildJobLimits(')
  . ([scriptblock]::Create($originalBody))
 }
}
$path=Join-Path $fixtureRoot 'fixture-job-limits.json'
$script:printed=[Collections.Generic.List[string]]::new();function Write-Host {param($Object)$script:printed.Add([string]$Object)}
$leaseContextFailures=[Collections.Generic.List[Exception]]::new()
$cases=[IO.File]::ReadAllText((Join-Path $ControlRoot 'cases.json')) | ConvertFrom-Json
foreach($case in $cases) {
 [IO.File]::WriteAllText($path,($case.record | ConvertTo-Json -Depth 5 -Compress))
 $failed=$false;try{[void](Read-StandardChildJobLimits 4321)}catch{$failed=$true}
 if($failed -eq $case.accept){throw 'Child job reader acceptance changed'}
 if($case.accept) {
  $leaseContextFailures.Clear();$script:printed.Clear();Invoke-LeaseChildJobLimitsObservation 4321
  $failureExpected=$case.record.membershipStatus -ceq 'error' -or $case.record.uiStatus -cin @('error','size') -or $case.record.extendedStatus -cin @('error','size')
  if(($leaseContextFailures.Count -ne 0) -ne $failureExpected -or -not $script:printed[0].Contains('immediateJobOnly=True;') -or
     -not $script:printed[0].Contains('causeEstablished=False;')){throw 'Partial query was silently successful or inferred an event cause'}
  if($failureExpected -and (-not $script:printed[1].Contains('fatalAfterOriginalGate=True;') -or -not $script:printed[1].Contains('operation=job-limits;'))) {throw 'Read-only query failure replaced original native gate'}
  if($case.record.uiStatus -ceq 'error' -and $case.record.uiError -eq 0 -and -not $script:printed[1].Contains('nativeErrors=0;')){throw 'Failed native0 query was discarded'}
  if($case.record.extendedStatus -ceq 'error' -and $case.record.extendedError -eq [uint32]::MaxValue -and
     (-not $script:printed[0].Contains('"extendedError":4294967295') -or -not $script:printed[1].Contains('nativeErrors=-1;'))) {throw 'Raw native DWORD bits were changed by .NET signed exception conversion'}
 }
}
Microsoft.PowerShell.Utility\Write-Host "CONTROL: actual $ExpectedEdition positive/malformed job-reader cases completed before original envelope reproduction."
$object=$cases[0].record | ConvertTo-Json -Depth 5 -Compress
$arrayRoots=@(('['+$object+']'),('[['+$object+']]'))
if($arrayRoots.Count -ne 2){throw 'Both separate malformed root envelopes are required'}
[IO.File]::WriteAllText($path,$arrayRoots[0])
# Desktop5 preserves a JSON root array as one pipeline object; modern Core
# enumerates it by default. The original defect is therefore Core-specific.
# Do not demand that a strict original Desktop reader accept a malformed root.
$converted=$arrayRoots[0] | ConvertFrom-Json
if($ExpectedEdition -ceq 'Core') {
 if($converted -is [array]){throw 'Actual Core singleton conversion contract changed'}
 $prior=Read-OriginalChildJobLimits 4321
 if($prior.pid -ne 4321 -or $prior.uiFlags -ne 64 -or $prior.extendedFlags -ne 8192){throw 'Original Core singleton envelope defect did not reproduce'}
 # Execute the same literal reader under a controlled non-enumerating output
 # boundary. This models WriteObject(result), the published old cmdlet path;
 # actual stock Desktop5 is separately mandatory on Windows below.
 function ConvertFrom-Json {
  param([Parameter(ValueFromPipeline=$true)][string]$InputObject)
  process {
   $parsed=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject -NoEnumerate
   Write-Output -NoEnumerate $parsed
  }
 }
 $preserved=$arrayRoots[0] | ConvertFrom-Json
 if($preserved -isnot [array] -or $preserved.Count -ne 1 -or $preserved[0].pid -ne 4321){throw 'Controlled old array-output boundary is not exact'}
 $message='';try{[void](Read-OriginalChildJobLimits 4321)}catch{$message=$_.Exception.Message}
 if($message -cne 'Owned child job-limit fields changed.'){throw 'Original preserved-array field refusal changed'}
 foreach($invalid in $arrayRoots) {
  [IO.File]::WriteAllText($path,$invalid)
  $message='';try{[void](Read-StandardChildJobLimits 4321)}catch{$message=$_.Exception.Message}
  if($message -cne 'Owned child job-limit root must be an object.'){throw 'Preserved-array mode bypassed the corrected root guard'}
 }
 Remove-Item -LiteralPath Function:\ConvertFrom-Json
} else {
 if($converted -isnot [array] -or $converted.Count -ne 1 -or $converted[0].pid -ne 4321){throw 'Actual Desktop5 singleton conversion contract changed'}
 $message='';try{[void](Read-OriginalChildJobLimits 4321)}catch{$message=$_.Exception.Message}
 if($message -cne 'Owned child job-limit fields changed.'){throw 'Original Desktop5 malformed root refusal changed'}
}
Microsoft.PowerShell.Utility\Write-Host "CONTROL: actual $ExpectedEdition original singleton conversion bound; corrected root refusals remain mandatory."
foreach($invalid in $arrayRoots) {
 [IO.File]::WriteAllText($path,$invalid)
 $message='';try{[void](Read-StandardChildJobLimits 4321)}catch{$message=$_.Exception.Message}
 if($message -cne 'Owned child job-limit root must be an object.'){throw 'Singleton or nested array envelope reached object conversion'}
}
[IO.File]::WriteAllText($path,(' ' * 4097))
$failed=$false;try{[void](Read-StandardChildJobLimits 4321)}catch{$failed=$true};if(-not $failed){throw 'Oversize job observation accepted'}
Remove-Item -LiteralPath $path
$failed=$false;try{[void](Read-StandardChildJobLimits 4321)}catch{$failed=$true};if(-not $failed){throw 'Missing job observation accepted'}
$original=$ast.FindAll({param($node)$node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if (-not $ObserveOnly -and ($record.sourceStatus')},$true)
if($original.Count -ne 1){throw 'Original mandatory native gate changed'}
$ObserveOnly=$false;$record=[pscustomobject]@{sourceStatus='ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false';rawOpenError=5;attestation='guard-unavailable';requiresClick=$true;ownerFailed=$false}
$ExpectedStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';$ExpectedError=2;$ExpectedProof='';$ExpectedRequired=$false;$ExpectedOwnerFailed=$false
$message='';try{& ([scriptblock]::Create($original[0].Extent.Text))}catch{$message=$_.Exception.Message}
if(-not $message.Contains('classification mismatch') -or -not $message.Contains('"rawOpenError":5')){throw 'Original fatal native5 changed after job diagnostics'}
Microsoft.PowerShell.Utility\Write-Host 'PASS: literal child-self job reader preserves complete/absent/partial/error0/size/rawDWORD facts, rejects malformed/singleton/nested/missing/oversize records, defers query failures and preserves original fatal native5; no native Windows or event-cause qualification.'
''',encoding="utf-8-sig")
            for engine,edition in self.powershell_engines():
                result=subprocess.run([engine,"-NoProfile","-File",str(script),"-Repository",str(ROOT),"-ControlRoot",str(root),"-ExpectedEdition",edition],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                print(result.stdout,flush=True)

    def test_actual_rust_observer_with_mocked_api_errors_and_cleanup(self):
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        self.assertTrue(rustc, "Rust compiler required for executable source controls")
        bridge = self.mocked_bridge()
        boundary = '\n    }\n    pub(super) fn fixture_enable_thread_tokens'
        self.assertEqual(bridge.count(boundary), 1)
        bridge = bridge.replace(boundary, RUST_TESTS + boundary, 1)
        with tempfile.TemporaryDirectory(prefix="mixel-thread-token-source-") as temporary:
            root = Path(temporary)
            source = root / "control.rs"
            # Exercise the actual exported flush as well as its actual helper.
            source.write_text("mod fixture {\n" + bridge + '\n#[test] fn exported_flush() { let directory=std::path::PathBuf::from(std::env::var("MIXEL_THREAD_TOKEN_CONTROL_DIR").unwrap()).join("exported"); std::fs::create_dir(&directory).unwrap(); std::fs::write(directory.join("fixture-thread-token.request"),"observe").unwrap(); fixture_enable_thread_tokens(&directory); fixture_thread_tokens::observe("OpenEventW"); fixture_flush_thread_tokens(&directory,false); }\n}\n')
            binary = root / ("control.exe" if os.name == "nt" else "control")
            subprocess.run([rustc, "--edition", "2021", "-D", "warnings", "--test", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=60)
            output = subprocess.run([str(binary), "--test-threads=1"], check=True, capture_output=True, text=True, timeout=15, env={**os.environ, "MIXEL_THREAD_TOKEN_CONTROL_DIR": str(root)})
            self.assertIn("2 passed; 0 failed", output.stdout)
            result = json.loads((root / "observed/fixture-thread-token-initial.json").read_text())
            self.assertEqual(result["schema"], 1)
            self.assertGreater(result["pid"], 0)
            self.assertTrue(all(row["tid"] == 103 for row in result["records"]))
            second = json.loads((root / "observed/fixture-thread-token-unnamed.json").read_text())
            self.assertEqual(second["records"][:len(result["records"])],result["records"])
            self.assertEqual(sum(row["phase"]=="CreateEventExW-unnamed" for row in second["records"]),1)
            print(output.stdout, flush=True)

    def test_actual_harness_preserves_repeated_portable_children_without_request(self):
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        self.assertTrue(rustc, "Rust compiler required for executable source controls")
        facade = r'''
        pub(super) fn fixture_observe_directory_access(directory:&std::path::Path) { assert!(!directory.join("fixture-directory-access.request").exists()); }
        pub(super) fn fixture_observe_job_limits(directory:&std::path::Path) { assert!(!directory.join("fixture-job-limits.request").exists()); }
        pub(super) fn fixture_raw_open_error()->u32 { fixture_thread_tokens::observe("OpenEventW"); 2 }
        pub(super) fn fixture_unnamed_create()->(&'static str,bool,u32,bool,u32) {
            fixture_thread_tokens::observe("CreateEventExW-unnamed"); ("created",true,0,true,0)
        }
        pub(super) fn status()->&'static str { fixture_thread_tokens::observe("OpenEventW"); "ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false" }
'''
        prelude = r'''
fn lease_main() { panic!("No portable arguments in this control"); }
fn hold_support_invite_attended_lease()->bool { false }
fn support_invite_lease_status()->&'static str { mixel_support_lease::status() }
fn support_invite_attestation()->&'static str { "" }
fn support_invite_requires_click()->bool { false }
fn support_invite_owner_lease_failed()->bool { false }
fn main() {
    let directory=std::env::current_exe().unwrap().parent().unwrap().to_owned();
    std::fs::write(directory.join(format!("fixture-release-{}",std::process::id())),"verified").unwrap();
    fixture_main();
}
'''
        harness = lease.STANDARD_WINDOWS_HARNESS.replace("fn main() {", "fn fixture_main() {", 1).replace("    #[cfg(windows)] ", "    ")
        refusal = '    #[cfg(not(windows))]\n    panic!("This standard-account functional control requires actual Windows");\n'
        self.assertEqual(harness.count(refusal), 1)
        harness = harness.replace(refusal, "", 1)
        with tempfile.TemporaryDirectory(prefix="mixel-thread-token-portable-harness-") as temporary:
            root = Path(temporary)
            source = root / "control.rs"
            source.write_text("mod mixel_support_lease {\n" + self.mocked_bridge() + facade + "}\n" + prelude + harness)
            binary = root / ("control.exe" if os.name == "nt" else "control")
            subprocess.run([rustc, "--edition", "2021", "-D", "warnings", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=60)
            (root / "fixture-mode.txt").write_text("probe")
            for _ in range(3):
                (root / "fixture-result.json").unlink(missing_ok=True)
                output = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
                result = json.loads((root / "fixture-result.json").read_text())
                self.assertEqual(result["rawOpenError"], 2)
                self.assertEqual(result["attestation"], "")
                self.assertFalse(list(root.glob("fixture-thread-token-*.json")))
            (root / "fixture-thread-token.request").write_text("observe")
            (root / "fixture-result.json").unlink()
            output = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
            initial = root / "fixture-thread-token-initial.json"
            result = json.loads((root / "fixture-result.json").read_text())
            observed = json.loads(initial.read_text())
            self.assertEqual(observed["pid"], result["pid"])
            self.assertEqual(len(observed["records"]), 2)
            saved = initial.read_bytes()
            (root / "fixture-result.json").unlink()
            duplicate = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("AlreadyExists", duplicate.stderr)
            self.assertEqual(initial.read_bytes(), saved)
            self.assertFalse((root / "fixture-result.json").exists())
            print("PASS: actual standard harness with mocked APIs runs three portable children in the same directory without token requests; explicit request enables same-PID evidence and repeated immutable publication fails before result readiness.", flush=True)

    def test_actual_powershell_reader_and_native_failure_reporting(self):
        pwsh = shutil.which("pwsh")
        self.assertTrue(pwsh, "PowerShell required for executable source controls")
        engines = [pwsh]
        windows_powershell = shutil.which("powershell.exe") if os.name == "nt" else None
        if os.name == "nt":
            self.assertTrue(windows_powershell, "Actual Windows source controls require stock Windows PowerShell5.1")
            self.assertNotEqual(Path(windows_powershell).resolve(), Path(pwsh).resolve(),
                                "Stock Windows PowerShell5.1 must be distinct from PowerShell7")
        if windows_powershell and Path(windows_powershell).resolve() != Path(pwsh).resolve():
            engines.append(windows_powershell)
        with tempfile.TemporaryDirectory(prefix="mixel-thread-token-reader-") as temporary:
            root = Path(temporary)
            good = dict(schema=1, pid=4321, records=[token_record()])
            cases = [good]
            for key, value in (("pid", "4321"), ("schema", True), ("records", [])):
                cases.append({**good, key: value})
            for key, value in (("tid", 0), ("tid", "103"), ("threadStatus", "invented"), ("threadError", 0), ("primaryType", 2), ("threadLogonMatchesPrimary", True), ("primaryCloseError", 6), ("secret", "refuse unknown field")):
                cases.append({**good, "records": [{**token_record(), key: value}]})
            for key in ("phase", "threadStatus", "primaryStatus", "threadClose", "primaryClose"):
                cases.append({**good, "records": [{**token_record(), key: [token_record()[key]]}]})
            cases.append({**good, "records": [token_record(), {**token_record(), "tid": 104}]})
            cases.append({**good, "records": [token_record()] * 33})
            present = {**token_record(), "threadStatus": "available", "threadError": 0, "threadType": 2,
                       "threadImpersonationLevel": 2, "threadLogonMatchesPrimary": False, "threadClose": "closed"}
            for subject in ("thread", "primary"):
                for status in ("available", "statistics-error", "statistics-size", "open-error"):
                    row = {**present, "threadLogonMatchesPrimary": None if status != "available" else False,
                           subject + "Status": status, subject + "Error": 0,
                           subject + "Type": (2 if subject == "thread" else 1) if status == "available" else 0,
                           subject + "Close": "closed" if status == "open-error" else "not-opened"}
                    if subject == "thread" and status != "available":
                        row["threadImpersonationLevel"] = 0
                    cases.append({**good, "records": [row]})
            malformed_count = len(cases) - 1
            cases = [{"record": case, "accept": index == 0} for index, case in enumerate(cases)]
            positive_rows = [present]
            for subject in ("thread", "primary"):
                for status in ("statistics-error", "statistics-size", "open-error"):
                    row = {**present, "threadLogonMatchesPrimary": None, subject + "Status": status,
                           subject + "Error": 0, subject + "Type": 0,
                           subject + "Close": "not-opened" if status == "open-error" else "closed"}
                    if subject == "thread":
                        row["threadImpersonationLevel"] = 0
                    positive_rows.append(row)
                for error in (0, 6):
                    positive_rows.append({**present, subject + "Close": "failed", subject + "CloseError": error})
            cases.extend({"record": {**good, "records": [row]}, "accept": True} for row in positive_rows)
            self.assertEqual(malformed_count, 26)
            self.assertEqual(len(positive_rows) + 1, 12)
            encoded_cases = json.dumps(cases)
            script = root / "control.ps1"
            script.write_text(r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if ($PSVersionTable.PSEdition -cne $ExpectedEdition -or
    ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) {
  throw 'Calling-thread reader did not execute with the required actual PowerShell edition/version'
}
$fixtureRoot=$ControlRoot
$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1'))
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count) { throw 'Standard helper does not parse' }
$reader=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Read-StandardCallingThreadTokens'},$true)
if($reader.Count -ne 1) { throw 'Exact reader missing' }
. ([scriptblock]::Create($reader[0].Extent.Text))
$native=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Invoke-StandardLeaseControl'},$true)
if($native.Count -ne 1) { throw 'Exact native control missing' }
$nativeText=$native[0].Extent.Text
$requestIndex=$nativeText.IndexOf("[IO.File]::WriteAllText((Join-Path `$fixtureRoot 'fixture-thread-token.request'), 'observe', [Text.UTF8Encoding]::new(`$false))")
if($requestIndex -lt 0 -or $requestIndex -gt $nativeText.IndexOf('$controlPid =')) { throw 'CI standard child can start without its explicit token observation request' }
$cases=[IO.File]::ReadAllText((Join-Path $ControlRoot 'cases.json')) | ConvertFrom-Json
for($i=0;$i -lt $cases.Count;$i++) {
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-thread-token-initial.json'),($cases[$i].record | ConvertTo-Json -Depth 5 -Compress))
  $failed=$false;try { [void](Read-StandardCallingThreadTokens 4321) } catch { $failed=$true }
  if($failed -eq $cases[$i].accept) { throw "Reader result incorrect case$i" }
}
[IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-thread-token-initial.json'),(' ' * 32769))
$oversizeRefused=$false;try { [void](Read-StandardCallingThreadTokens 4321) } catch { $oversizeRefused=$true }
if(-not $oversizeRefused) { throw 'Oversize calling-thread record accepted' }
$initialPath=Join-Path $fixtureRoot 'fixture-thread-token-initial.json'
$unnamedPath=Join-Path $fixtureRoot 'fixture-thread-token-unnamed.json'
[IO.File]::WriteAllText($initialPath,($cases[0].record | ConvertTo-Json -Depth 5 -Compress))
$missingStageRefused=$false;try { [void](Read-StandardCallingThreadTokens 4321 -Unnamed) } catch { $missingStageRefused=$true }
if(-not $missingStageRefused) { throw 'Unnamed reader silently used initial file' }
$bytes=[IO.File]::ReadAllBytes($initialPath)
$locked=[IO.File]::Open($initialPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try {
  $unnamed=$cases[0].record | ConvertTo-Json -Depth 5 -Compress | ConvertFrom-Json
  $unnamed.records[0].phase='CreateEventExW-unnamed'
  [IO.File]::WriteAllText($unnamedPath,($unnamed | ConvertTo-Json -Depth 5 -Compress))
  [void](Read-StandardCallingThreadTokens 4321)
  [void](Read-StandardCallingThreadTokens 4321 -Unnamed)
  if([Convert]::ToBase64String([IO.File]::ReadAllBytes($initialPath)) -cne [Convert]::ToBase64String($bytes)) { throw 'Second phase changed first bytes' }
} finally { $locked.Dispose() }
[IO.File]::WriteAllText($initialPath,($unnamed | ConvertTo-Json -Depth 5 -Compress))
$wrongStageRefused=$false;try { [void](Read-StandardCallingThreadTokens 4321) } catch { $wrongStageRefused=$true }
if(-not $wrongStageRefused) { throw 'Initial reader accepted unnamed phase' }
[IO.File]::WriteAllText($unnamedPath,($cases[0].record | ConvertTo-Json -Depth 5 -Compress))
$wrongStageRefused=$false;try { [void](Read-StandardCallingThreadTokens 4321 -Unnamed) } catch { $wrongStageRefused=$true }
if(-not $wrongStageRefused) { throw 'Unnamed reader accepted initial phase' }
$cleanup=$ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if (@($threadTokens.records | Where-Object { $_.threadClose')},$true)
if($cleanup.Count -ne 2) { throw 'Exact initial/unnamed cleanup gates missing' }
foreach($item in $cases) {
  if(-not $item.accept) { continue }
  $threadTokens=$item.record
  $mustFail=@($threadTokens.records | Where-Object { $_.threadClose -ceq 'failed' -or $_.primaryClose -ceq 'failed' }).Count -gt 0
  foreach($gate in $cleanup) {
    $message='';try { & ([scriptblock]::Create($gate.Extent.Text)) } catch { $message=$_.Exception.Message }
    if(($mustFail -and -not $message.Contains('handle cleanup failed')) -or (-not $mustFail -and $message -cne '')) { throw 'Cleanup failure including native error0 is no longer fatal' }
  }
}
if($source.IndexOf('Actual standard-account source lease classification mismatch:') -gt $source.IndexOf('Owned read-only calling-thread token handle cleanup failed;')) { throw 'Cleanup error replaced the original native classification failure' }
$launch=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-launch-windows.ps1'))
$ast=[Management.Automation.Language.Parser]::ParseInput($launch,[ref]$tokens,[ref]$errors)
if($errors.Count) { throw 'Customer launch helper does not parse' }
$baseline=$ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Condition -eq $null -and $node.Extent.Text.StartsWith("if (`$initialHealth.attendedProof -cne '' -or `$initialOwnsLease) {")},$true)
if($baseline.Count -ne 1) { throw 'Exact fatal baseline branch missing' }
function Get-MixelSupportLeaseDiagnostic { param($ExpectedPid) if($ExpectedPid -ne 4321) { throw 'Wrong IPC PID' };'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false' }
foreach($case in @(@('', $false, ''), @('guard-unavailable',$false,'cannot safely inspect'), @('attended-runtime-v2',$false,'already guarded'), @('',$true,'already guarded'), @('service-update-required',$false,'already guarded'))) {
  $initialHealth=[pscustomobject]@{attendedProof=$case[0];incomingPid=4321};$initialOwnsLease=$case[1]
  $message='';try { & ([scriptblock]::Create($baseline[0].Extent.Text)) } catch { $message=$_.Exception.Message }
  if(($case[2] -ceq '' -and $message -cne '') -or ($case[2] -cne '' -and -not $message.Contains($case[2]))) { throw 'Native baseline fatal behavior/reporting changed' }
}
if($launch.IndexOf("`$fixtureStage = 'verify ordinary native consent baseline'") -gt $launch.IndexOf('$initialHealth = Get-MixelSupportRuntimeHealth')) { throw 'Baseline stage attribution follows the native observation' }
Write-Host 'PASS: actual PowerShell reader accepts 12 valid observations including unknown native error0, refuses 26 malformed records including all five singleton string arrays and eight impossible cleanup states plus an oversize file, selects immutable initial/unnamed files with a retained FileShare.Read reader, and preserves five native baseline outcomes including fatal unavailable/error5 and both fatal cleanup gates.'
Write-Host "Actual PowerShell version: $($PSVersionTable.PSVersion); $($PSVersionTable.PSEdition)"
''', encoding="utf-8-sig")
            for index, engine in enumerate(engines):
                control_root = root / ("engine-" + str(index))
                control_root.mkdir()
                (control_root / "cases.json").write_text(encoded_cases)
                edition = "Desktop" if engine == windows_powershell else "Core"
                output = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(control_root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=30)
                self.assertEqual(output.returncode, 0, output.stdout + output.stderr)
                self.assertIn("PASS: actual PowerShell reader", output.stdout)
                print(output.stdout, flush=True)
            if windows_powershell is None:
                print("Windows PowerShell5.1 is unavailable here; actual PS5 compatibility has not been executed.", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
