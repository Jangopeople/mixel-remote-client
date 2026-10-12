#!/usr/bin/env python3
"""Executable source controls; mocked Win32 APIs never qualify Windows runtime."""
import ast
import codecs
import importlib.util
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
        self.assertEqual(len(writers), 3)
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
        print("PASS: all three generated PowerShell source files retain exact UTF8 BOM/Unicode bytes with both actual LF and CRLF writer modes; original unmarked accented source changes under the explicit legacy cp1252 decode control. Actual Desktop5 execution remains required on Windows.", flush=True)

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

    def test_current_caller_four_actual_functions_strict_results_and_cleanup(self):
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
foreach($name in @('Assert-StandardLeaseSafeResult','Read-StandardCallingThreadTokens','Invoke-CurrentCallerLeaseControl','Invoke-CurrentCallerLeaseFour')) {
  $matches=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
  if($matches.Count -ne 1) { throw "Missing exact current function $name" }
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
    string close=Scenario=="token-close"?"failed":"closed";int closeError=Scenario=="token-close"?0:0;
    File.WriteAllText(Path.Combine(Root,"fixture-thread-token-initial.json"),"{\"schema\":1,\"pid\":"+pid+",\"records\":[{\"phase\":\"OpenEventW\",\"tid\":103,\"threadStatus\":\"absent\",\"threadError\":1008,\"threadType\":0,\"threadImpersonationLevel\":0,\"primaryStatus\":\"available\",\"primaryError\":0,\"primaryType\":1,\"threadLogonMatchesPrimary\":null,\"threadClose\":\"not-opened\",\"threadCloseError\":0,\"primaryClose\":\""+close+"\",\"primaryCloseError\":"+closeError+"}]}");
    return pid;
  }
  public static bool StartedCurrentCallerSidMatches(int pid){return Scenario!="sid";}
  public static bool Elevated(int pid){return pid==CallerPid || Scenario!="elevation";}
  public static ControlFacts StartedTokenFacts(int pid){var f=new ControlFacts();if(Scenario=="token-session")f.sessionId=3;if(Scenario=="unknown-session")f.sessionKnown=false;if(Scenario=="logon")f.logonComparison=f.logonComparison.Replace("MatchesCaller=true","MatchesCaller=false");return f;}
  public static string StartedStatus(int pid){if(!statuses.ContainsKey(pid))throw new InvalidOperationException("Unowned status");if(Scenario=="early-owner" && pid==Owner)return "exited:0x00000000";if(pid!=Owner && File.Exists(Path.Combine(Root,"fixture-release-"+pid)))statuses[pid]="exited:0x00000000";return statuses[pid];}
  public static void StopOwnedStartedProcesses(){Stops++;Calls.Add("stop");foreach(int pid in new List<int>(statuses.Keys))if(statuses[pid]=="still-running")statuses[pid]="exited:0x00000001";Owner=0;if(Scenario=="stop")throw new InvalidOperationException("Mock retained stop failure");}
  public static void CloseStartedObservations(){Closes++;Calls.Add("close");statuses.Clear();if(Scenario=="close")throw new InvalidOperationException("Mock retained close failure");}
}
'@
function Get-Process {param([int]$Id) $process=[ControlProcess]::new();if($Id -ne $PID -and [MixelOrdinaryTokenFixture]::Scenario -ceq 'process-session'){$process.SessionId=3};return $process}
$script:printed=[Collections.Generic.List[string]]::new()
function Write-Host {param($Object) $script:printed.Add([string]$Object)}
$scenarios=@('pass','error5','false-ready','owner-memory','owner-failed','wrong-click','token-close','sid','elevation','process-session','token-session','unknown-session','logon','early-owner','stop','close')
foreach($scenario in $scenarios) {
  $fixtureRoot=Join-Path $ControlRoot $scenario;[void][IO.Directory]::CreateDirectory($fixtureRoot)
  $executable=Join-Path $fixtureRoot 'lease-standard.exe';$resultPath=Join-Path $fixtureRoot 'fixture-result.json'
  [MixelOrdinaryTokenFixture]::Reset($fixtureRoot,$scenario,$PID)
  $script:printed.Clear()
  $message='';try { Invoke-CurrentCallerLeaseFour } catch { $message=$_.Exception.Message }
  if(($scenario -ceq 'pass') -ne ($message -ceq '')) {throw "Incorrect direct control scenario $scenario : $message"}
  if([MixelOrdinaryTokenFixture]::Stops -lt 1 -or [MixelOrdinaryTokenFixture]::Closes -lt 1) {throw "Independent retained cleanup missing $scenario"}
  if($scenario -ceq 'pass') {
    if(([MixelOrdinaryTokenFixture]::Calls -join ',') -cne 'probe:4001,owner:4002,probe:4003,stop,close,probe:4004,stop,close' -or [MixelOrdinaryTokenFixture]::Disposed -ne 8) {throw 'Four phase ownership/order or wrapper disposal changed'}
    if(@(Get-ChildItem -LiteralPath $fixtureRoot -Filter 'fixture-release-*').Count -ne 0) {throw 'Confirmed exited caller acknowledgment can leak into the unchanged standard terminal after PID reuse'}
    if($script:printed.Count -ne 9 -or @($script:printed | Where-Object {$_ -like 'OBSERVED: direct current Actions caller GUID functional control;*' -and $_.Contains('genuineStandardQualified=False; fullEightQualified=False; customerAppQualified=False;')}).Count -ne 4) {throw 'Diagnostic output silently became standard/app qualification or lost native records'}
  } else {
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
Microsoft.PowerShell.Utility\Write-Host 'PASS: exact current-caller PowerShell functions execute four strict same-executable phases, retained owner drop/fresh absence and independent cleanup; 15 identity/native-state/close/lifetime/cleanup mutations refuse; failure is retained while the old matrix runs and rethrown after the unchanged mandatory gate. APIs mocked only; no standard/app qualification.'
''', encoding="utf-8-sig")
            for index, (engine, edition) in enumerate(self.powershell_engines()):
                control_root = root / ("engine-" + str(index))
                control_root.mkdir()
                result = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(control_root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=35)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS: exact current-caller PowerShell functions", result.stdout)
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
            result = subprocess.run([bash, str(dispatcher)], cwd=root,
                                    env={**os.environ, "MIXEL_SOURCE_ORDER_PYTHON": Path(sys.executable).resolve().as_posix(),
                                         "MIXEL_SOURCE_ORDER_SHIM": python.as_posix()},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
            self.assertEqual((root / "actual-invocations.log").read_text().splitlines(), [
                "scripts/test-support-flutter-analysis.py",
                "scripts/test-support-invite.py", "scripts/test-generated-support-paths.py",
                "scripts/test-windows-lease-observation.py", "scripts/test-support-lease.py"])
            arguments = [json.loads(line) for line in (root / "actual-argv.jsonl").read_text(encoding="utf-8").splitlines()]
            expected_shell = subprocess.check_output([bash, "-c",
                'if [[ "${OS:-}" == Windows_NT ]]; then cygpath -w "$BASH"; else printf "%s\\n" "$BASH"; fi'],
                text=True, encoding="utf-8", timeout=15).strip()
            self.assertEqual(arguments[0][0:2], ["scripts/test-support-flutter-analysis.py", "--dart"])
            self.assertEqual(arguments[0][3:], ["--bash", expected_shell])
            self.assertTrue(Path(arguments[0][4]).is_absolute(), "Dispatcher lost the active full Bash path")
            self.assertEqual(arguments[1:], [[name] for name in [
                "scripts/test-support-invite.py", "scripts/test-generated-support-paths.py",
                "scripts/test-windows-lease-observation.py", "scripts/test-support-lease.py"]])
            self.assertIn("CONTROL: unchanged fatal native gate", result.stdout)
            print("PASS: exact generated-source dispatcher executes Flutter analyzer and calling-thread/reader controls before the unchanged fatal native lease gate; fatal result stops later tests.", flush=True)

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
        stripped = re.sub(r'        fixture_thread_tokens::observe\("[^"]+"\);\n', '', body).replace(lease.STANDARD_THREAD_BRIDGE, '', 1)
        marker = "\n}\n\npub fn hold_support_invite_attended_lease()"
        expected = lease.isolated_source(original, Path("owned-control"), "mixel-standard-control-", "Global\\Mixel-Remote-Standard-Lease-" + "a" * 32)
        expected = expected.replace(marker, "\n" + lease.standard_unnamed_bridge(original) + marker, 1)
        expected += lease.HARNESS.replace("fn main() {", "fn lease_main() {", 1) + lease.STANDARD_WINDOWS_HARNESS
        self.assertEqual(stripped, expected)
        main=lease.STANDARD_WINDOWS_HARNESS
        self.assertLess(main.index('fixture_enable_thread_tokens(&directory)'),main.index('hold_support_invite_attended_lease();'))
        self.assertLess(main.index('fixture_flush_thread_tokens(&directory,false)'),main.index('std::fs::rename(directory.join("fixture-result.new")'))
        self.assertLess(main.index('fixture_flush_thread_tokens(&directory,true)'),main.index('std::fs::rename(directory.join("fixture-unnamed-result.new")'))
        self.assertIn('.write(true).create_new(true)',lease.STANDARD_THREAD_BRIDGE)
        self.assertNotIn('std::fs::rename',lease.STANDARD_THREAD_BRIDGE)
        for forbidden in ("Impersonate", "RevertToSelf", "SetThreadToken", "SetTokenInformation", "AdjustToken", "NtOpen", "SetSecurity", "LogonUser"):
            self.assertNotIn(forbidden, lease.STANDARD_THREAD_BRIDGE)

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
