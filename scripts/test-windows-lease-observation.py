#!/usr/bin/env python3
"""Executable source controls; mocked Win32 APIs never qualify Windows runtime."""
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
import sys
from pathlib import Path
with Path('actual-invocations.log').open('a') as output:
    output.write(sys.argv[1]+'\n')
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
                "scripts/test-support-invite.py", "scripts/test-generated-support-paths.py",
                "scripts/test-windows-lease-observation.py", "scripts/test-support-lease.py"])
            self.assertIn("CONTROL: unchanged fatal native gate", result.stdout)
            print("PASS: exact generated-source dispatcher executes calling-thread/reader controls before the unchanged fatal native lease gate; fatal result stops later tests.", flush=True)

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
Write-Host "Actual PowerShell version: $($PSVersionTable.PSVersion)"
''')
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
