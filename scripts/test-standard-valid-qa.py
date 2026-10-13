"""Source/mock qualification only. Never runs Windows native setup or children."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
DRIVER = SCRIPTS / 'observe-standard-valid-qa-windows.ps1'
KNOWN = SCRIPTS / 'test-standard-qa-session-directory.cs'
ORIGINAL = SCRIPTS / 'test-support-lease-standard-windows.ps1'
SPEC = importlib.util.spec_from_file_location('previous_controls', SCRIPTS / 'test-session-directory-access.py')
PREVIOUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREVIOUS)


def invoke(body, csharp=None):
    with tempfile.TemporaryDirectory(prefix='mixel-valid-qa-source-') as folder:
        root = Path(folder)
        prefix = "$ErrorActionPreference='Stop'\n"
        if csharp:
            (root / 'helper.cs').write_text(csharp, encoding='utf-8')
            prefix += "Add-Type -Path (Join-Path $PSScriptRoot 'helper.cs') -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
        (root / 'run.ps1').write_text(prefix + body, encoding='utf-8-sig')
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File', str(root / 'run.ps1')],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise AssertionError(f'Actual source control exit{result.returncode}\n{result.stdout}\n{result.stderr}')
        return result.stdout


FUNCTIONS = r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__DRIVER__',[ref]$t,[ref]$e)
if($e.Count){throw 'Actual driver parse error'}
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if($functions.Count -ne 7){throw 'Exact seven function family changed'}
foreach($fn in $functions){
 $text=$fn.Extent.Text.Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)').Replace('[MixelSessionDirectoryFixture+NativeApi]::new()','[AbaMock+Platform]::new()')
 . ([scriptblock]::Create($text))
}
'''.replace('__DRIVER__', str(DRIVER).replace("'", "''"))


class IgnoredFixtureControls(unittest.TestCase):
    def test_known_fifteen_only_class_rename_and_native_fence(self):
        raw = KNOWN.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '310b613adc779fed71c2d2bc689041b6cfd07750dd7b9dff5f786c9c446c51a8')
        old = raw.replace(b'MixelSessionDirectoryFixture', b'MixelSessionDirectoryAba')
        self.assertEqual(hashlib.sha256(old).hexdigest(), 'bf291d27fcdb4aec3cae1b19b29f70491c74159dafba5e49642746cbf6233379')
        text = raw.decode()
        for value in ('addedMask=15', 'Put(value,end+4,15);', 'AddAccessAllowedAceEx(target,revision,0,15,sid)',
                      '"workflow_dispatch"', '"session-directory-access-observation"', '"MIXEL_SESSION_DIRECTORY_ABA"'):
            self.assertIn(value, text)
        # Compile both distinct real classes, complete native declarations included.
        body = "Add-Type -Path '" + str(SCRIPTS / 'test-session-directory-access.cs') + "' -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
        body += "Add-Type -Path '" + str(KNOWN) + "' -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
        body += "'PASS: complete real mask8 and known15 classes compile warning-fatal; no native constructor called.'\n"
        print(invoke(body).strip())

    def test_default_refusal_precedes_files_and_native(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'absent'
            env = dict(os.environ, GITHUB_ACTIONS='false')
            result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-File', str(DRIVER), '-EvidenceDirectory', str(destination)],
                                    capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Explicit optional disposable hosted Windows CI scope required', result.stderr)
            self.assertFalse(destination.exists())

    def test_source_composition_original_eight_functions_and_cleanup_order(self):
        body = FUNCTIONS + r'''
$original=[IO.File]::ReadAllText('__ORIGINAL__')
$generated=New-StandardValidQaScript $original 'PREFIX'
$a=$original.IndexOf("  `$absent = 'ownerFailed=false;", $original.IndexOf("`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"))
$b=$original.IndexOf('  if ($currentCallerFailure)', $a)
$eight=$original.Substring($a,$b-$a)
if($generated.IndexOf($eight) -lt 0 -or $generated.IndexOf($eight) -ne $generated.LastIndexOf($eight)){throw 'Original whole eight block changed'}
if($generated.IndexOf('  Invoke-SessionDirectoryAba') -gt $generated.IndexOf('  Start-StandardValidQaNamespace')){throw 'Unmodified A/B/A must precede later full8 setup'}
if($generated.IndexOf('  Start-StandardValidQaNamespace') -lt $generated.IndexOf('  Invoke-OptionalCallerLinkedLeaseSubsystemObservations')){throw 'Full8 bootstrap started before original supplemental controls'}
if($generated.IndexOf('  try { Restore-StandardValidQaNamespace }') -gt $generated.IndexOf('  try { Complete-LeaseNtPendingProtocols }')){throw 'Restore must independently precede protocol drain'}
if($generated.IndexOf('  if (-not $script:qaNamespaceCleanupOkay)') -gt $generated.IndexOf('  if ($profileStarted -and $ownedSid)')){throw 'Unknown environment reached account cleanup'}
foreach($bad in @($original.Replace("`n  Complete-LeaseNtPendingProtocols`n",''),($original+$original))){
 $refused=$false;try{New-StandardValidQaScript $bad 'PREFIX'|Out-Null}catch{$refused=$true}
 if(-not $refused){throw 'Missing/duplicate strict original anchor accepted'}
}
'PASS: literal entire original8 conserved; same original A/B/A before setup; restore before NT drain/account deletion; strict anchors refuse.'
'''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
        print(invoke(body).strip())
        old = (SCRIPTS / 'observe-session-directory-aba-windows.ps1').read_text()
        new = DRIVER.read_text()
        # Four original orchestration functions are byte-for-byte present.
        for first, last in [('function Invoke-SessionDirectoryAba {', '\nfunction Get-SessionDirectoryAbaFailure'),
                            ('function Get-SessionDirectoryAbaFailure(', '\nfunction Invoke-SessionDirectoryAbaPhase'),
                            ('function Invoke-SessionDirectoryAbaPhase(', '\nfunction New-SessionDirectoryAbaScript'),
                            ('function New-SessionDirectoryAbaScript(', "\nif ($env:GITHUB_ACTIONS")]:
            fragment = old[old.index(first):old.index(last, old.index(first))]
            self.assertIn(fragment, new)
        pins = json.loads(re.search(r"\$pins=ConvertFrom-Json @'\n(.*?)\n'@", new, re.S).group(1))
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((SCRIPTS / name).read_bytes()).hexdigest(), digest)

    def test_complete_generated_script_parses_before_any_execution(self):
        body = FUNCTIONS + r'''
$original=[IO.File]::ReadAllText('__ORIGINAL__')
$generated=New-StandardValidQaScript $original (($functions | ForEach-Object {$_.Extent.Text}) -join "`n")
$path=Join-Path $PSScriptRoot 'generated.ps1'
[IO.File]::WriteAllText($path,$generated,[Text.UTF8Encoding]::new($true))
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
if($e.Count -or -not $tree){throw 'Complete composed Standard script parser refused'}
if($generated.IndexOf('Start-StandardValidQaNamespace') -lt 0 -or $generated.IndexOf('Restore-StandardValidQaNamespace') -lt 0){throw 'Actual setup/restore omitted'}
'PASS: complete generated script plus all original functions parse before execution; no native or child launch.'
'''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
        print(invoke(body).strip())

    def test_current_eight_projection_and_exact_optional_workflow(self):
        # Durable cold-CI conservation: only the already-reviewed helper pin changes
        # in the stopped full8 wrapper. Runtime setup/functions remain identical.
        driver = DRIVER.read_bytes()
        # Restore only exact final-carrier pins before the historical whole-file proof.
        for actual, original in [(b'012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', b'765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), (b'd11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', b'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), (b'bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', b'6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:
            self.assertEqual(driver.count(actual), 1)
            driver = driver.replace(actual, original, 1)
        current = b'c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76'
        previous = b'65fc503abd02da1fc50f0e2abe73a747fd21468512298376cea73062422b0a64'
        self.assertEqual(driver.count(current), 1)
        self.assertEqual(hashlib.sha256(driver.replace(current, previous)).hexdigest(),
                         'e0b93f5d3b519877650e5cca61c37102c5f8897510d76fecfe8fd29867692126')
        self.assertEqual(hashlib.sha256((SCRIPTS / 'test-session-directory-access.cs').read_bytes()).hexdigest(), current.decode())
        workflow = (ROOT / '.github/workflows/preflight.yml').read_text()
        flag = '          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n'
        self.assertEqual(workflow.count(flag), 1)
        workflow = workflow.replace(flag, '', 1)
        suffix = "          python scripts/test-standard-valid-qa.py\n          if ($LASTEXITCODE -ne 0) { throw 'Valid owned QA original eight source and cleanup controls failed.' }\n"
        self.assertEqual(workflow.count(suffix), 1)
        restored = workflow.replace(suffix, '').replace(
            '- name: Observe original World children then run exact eight in an independently restored owned QA fixture',
            '- name: Observe six original World children around an exactly restored temporary directory DACL').replace(
            'run: ./scripts/observe-standard-valid-qa-windows.ps1 -EvidenceDirectory session-directory-aba-proof',
            'run: ./scripts/observe-session-directory-aba-windows.ps1 -EvidenceDirectory session-directory-aba-proof')
        # Reverse only the exact hosted Windows source-job budget before its historical pin.
        current_budget = "  windows-packaging:\n    runs-on: ${{ github.event_name == 'workflow_dispatch' && inputs.windows_source_runner == 'windows-2025' && 'windows-2025' || 'windows-2022' }}\n    timeout-minutes: 45\n"
        historical_budget = current_budget.replace('    timeout-minutes: 45\n', '    timeout-minutes: 20\n', 1)
        self.assertEqual(restored.count(current_budget), 1)
        restored = restored.replace(current_budget, historical_budget, 1)
        # Reverse only the exact approved control additions after the historical
        # optional-wrapper baseline; the original whole-workflow pin stays intact.
        later_controls = (
            '          python3 scripts/test-pinned-source-archives.py\n',
            '          targets: x86_64-unknown-linux-gnu,x86_64-pc-windows-msvc,aarch64-apple-darwin\n',
            '          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only\n          python3 scripts/test-support-file-transfer-window.py --app "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --output "$RUNNER_TEMP/mixel-file-transfer-source" --flutter "$(command -v flutter)"\n',
            '          python3 scripts/test-support-keyboard-default.py --source rustdesk --source-root "$PWD" --evidence-dir "$RUNNER_TEMP/mixel-keyboard-default-source" --linux-sysroot "$(rustc --print sysroot)"\n          python3 scripts/test-support-uinput-scroll.py --source rustdesk --evidence-dir "$RUNNER_TEMP/mixel-uinput-wheel-source" --linux-sysroot "$(rustc --print sysroot)"\n          python3 scripts/test-support-uinput-device.py --source rustdesk --wheel-helper scripts/patch-support-uinput-scroll.py --output "$RUNNER_TEMP/mixel-uinput-device-source" --rustc "$(command -v rustc)" --linux-sysroot "$(rustc --print sysroot)"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-support-fingerprint.py --source rustdesk --evidence-dir "$RUNNER_TEMP/mixel-fingerprint-source"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-support-fatal-crash.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --upstream-repo rustdesk --evidence-dir "$RUNNER_TEMP/mixel-fatal-crash-source" --linux-sysroot "$(rustc --print sysroot)"\n',
            '          python3 scripts/test-support-native-warnings.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-native-warning-source"\n          RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-macos-update.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf"\n      - name: Prove native fatal-crash handling with isolated owned children\n        run: RUSTC_BIN="$(rustup which rustc)" python3 scripts/test-support-fatal-crash.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --upstream-repo rustdesk --evidence-dir "$RUNNER_TEMP/mixel-fatal-crash-source"\n',
        )
        for control in later_controls:
            self.assertEqual(restored.count(control), 1)
            restored = restored.replace(control, '', 1)
        self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(),
                         'e8c4e5fd3f7ee7126867122dca7e5e86b6e4e12eff80aec9f1307b3abd4654e4')
        job = workflow.split('\n  session-directory-access-observation:', 1)[1]
        self.assertLess(job.index('test-session-directory-access.py'), job.index('test-standard-valid-qa.py'))
        self.assertLess(job.index('test-standard-valid-qa.py'), job.index('observe-standard-valid-qa-windows.ps1'))
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.session_directory_access_observation", job)
        self.assertNotIn('continue-on-error', job)
        original_test = (SCRIPTS / 'test-session-directory-access.py').read_bytes().replace(
            b"job.index('observe-standard-valid-qa-windows.ps1')", b"job.index('observe-session-directory-aba-windows.ps1')")
        projection = "        # Reverse only the exact final-carrier source-family pin rebindings.\n        for current, original in [('012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', '765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), ('d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', 'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), ('bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', '6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:\n            self.assertEqual(wrapper.count(current), 1)\n            wrapper = wrapper.replace(current, original, 1)\n"
        self.assertEqual(original_test.count(projection.encode()), 1)
        original_test = original_test.replace(projection.encode(), b'', 1)
        self.assertEqual(hashlib.sha256(original_test).hexdigest(),
                         '9b185f245cd60ae1d053712e36b56a3399cbbfc3092edb172a22ab98da9ca1a5')

    def test_actual_scope_partial_failures_and_unknown_cleanup(self):
        mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
        mock = mock.replace('MixelSessionDirectoryAba', 'MixelSessionDirectoryFixture').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
        body = FUNCTIONS + r'''
function Get-LocalUser {return [pscustomobject]@{Name=$ownedUser}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
$ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid'
$runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
foreach($mode in @('normal','set-false','set-zero-false','set-throw','query-fails','open-denied','restore-false','restore-readback-drift','close-false','unload-throw')){
 [AbaMock+Platform]::Mode=$mode;$script:qaNamespaceScope=$null;$script:qaNamespaceCleanupOkay=$true
 $startFailure=$null;try{Start-StandardValidQaNamespace}catch{$startFailure=$_}
 $restoreFailure=$null;try{Restore-StandardValidQaNamespace}catch{$restoreFailure=$_}
 $unsafe=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw')
 if(($null -ne $restoreFailure) -ne $unsafe -or $script:qaNamespaceCleanupOkay -eq $unsafe){throw ('Cleanup attribution changed: '+$mode)}
 $platform=[AbaMock+Platform]::Last
 if($platform.unloads -ne 1){throw 'Independent module dispose skipped'}
 if($mode -in @('set-false','set-zero-false','set-throw') -and ($platform.sets -ne 2 -or -not $script:qaNamespaceScope.facts.restorationVerified)){throw 'Partial setter did not restore exact original'}
 if($unsafe -and $script:qaNamespaceScope.facts.cleanupOkay){throw 'Failed native cleanup falsely qualified'}
}
$script:qaNamespaceScope=$null;$freshFacts.integrityRid=12288
$refused=$false;try{Start-StandardValidQaNamespace}catch{$refused=$true}
if(-not $refused -or $script:qaNamespaceScope){throw 'Foreign/high QA identity mutated namespace'}
'PASS: literal setup/restore10 scenarios; failed partial writes restore, close/module independently attempted, unknown cleanup cannot delete account; source identity refusal before mutation.'
'''
        print(invoke(body, KNOWN.read_text() + mock).splitlines()[-1])

    def test_literal_original_eight_and_outer_finally_fault_order(self):
        mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
        mock = mock.replace('MixelSessionDirectoryAba', 'MixelSessionDirectoryFixture').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
        fixture = r'''
public static class MixelOrdinaryTokenFixture {
 public static bool stopFailure; public static int stops,profiles,handleCloses,desktopRestores;
 public static void StopOwnedStartedProcesses(){stops++;if(stopFailure)throw new Exception("Injected stop failure");}
 public static void CloseStartedObservations(){handleCloses++;}
 public static string StartedStatus(int pid){if(pid!=512)throw new Exception("Foreign owner PID");return "exited:0x00000001";}
 public static string DesktopAclHashes(){return "original-desktop";}
 public static void RemoveProfile(string sid){if(sid!="owned-local-qa-sid")throw new Exception("Foreign profile");profiles++;}
 public sealed class DesktopAccess {public void Dispose(){desktopRestores++;}}
}
public static class MixelIsolatedLeaseControl {
 public static int closed;
 public static IntPtr DeniedEvent(string name){return new IntPtr(77);}
 public static IntPtr WrongObject(string name){return new IntPtr(88);}
 public static void Close(IntPtr handle){if(handle.ToInt64()!=77&&handle.ToInt64()!=88)throw new Exception("Foreign event close");closed++;}
 public static void ClosePending(){}
}
'''
        body = FUNCTIONS + r'''
function Get-LocalUser {param($SID,$Name,$ErrorAction);if($Name -and $script:userRemoved){return $null};return [pscustomobject]@{Name=$ownedUser}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
function Remove-LocalUser {param($SID);$script:userRemoved=$true}
function Complete-LeaseNtPendingProtocols {$script:drains++;if($script:drainFailure){throw 'Injected protocol drain failure'}}
function Start-Sleep {param($Seconds)}
function Invoke-StandardLeaseControl {
 param($Mode,$ExpectedStatus,[int]$ExpectedNativeError,$ExpectedAttestation,[bool]$ExpectedRequiresClick,[bool]$ExpectedOwnerFailure,[switch]$KeepAlive)
 $script:eightCalls.Add([pscustomobject]@{mode=$Mode;status=$ExpectedStatus;error=$ExpectedNativeError;attestation=$ExpectedAttestation;click=$ExpectedRequiresClick;failedOwner=$ExpectedOwnerFailure;kept=[bool]$KeepAlive})
 if($script:originalFailure -and $script:eightCalls.Count -eq 1){throw 'ORIGINAL_NATIVE5'}
 return 512
}
$original=[IO.File]::ReadAllText('__ORIGINAL__');$generated=New-StandardValidQaScript $original 'PREFIX'
$a=$original.IndexOf("  `$absent = 'ownerFailed=false;",$original.IndexOf("`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"))
$b=$original.IndexOf('  if ($currentCallerFailure)',$a);$eight=$original.Substring($a,$b-$a)
$tailAnchor="} catch {`n  `$failure = `$_`n  throw`n} finally {`n  `$cleanup = [System.Collections.Generic.List[string]]::new()"
$tailAt=$generated.LastIndexOf($tailAnchor);if($tailAt -lt 0){throw 'Exact original outer finally absent'}
$tail=$generated.Substring($tailAt).Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)')
$runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
foreach($mode in @('normal','original-native5','stop-failure','protocol-failure','restore-false','original5-and-restore-false','close-false','unload-throw')){
 $ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid';$profileStarted=$true;$eventHandle=[IntPtr]::Zero;$failure=$null
 $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new();$originalDesktop='original-desktop';$eventName='Global\Mixel-Remote-Standard-Lease-'+('d'*32)
 $script:ntPendingProtocols=[Collections.Generic.List[object]]::new();$script:ntPreservedRoots=[Collections.Generic.HashSet[string]]::new()
 $fixtureRoot=Join-Path $PSScriptRoot $mode;[void][IO.Directory]::CreateDirectory($fixtureRoot)
 $script:qaNamespaceScope=$null;$script:qaNamespaceCleanupOkay=$true;$script:abaDeferredFailure=$null;$script:userRemoved=$false;$script:drains=0
 $script:eightCalls=[Collections.Generic.List[object]]::new();$script:originalFailure=$mode -in @('original-native5','original5-and-restore-false')
 $script:drainFailure=$mode -eq 'protocol-failure';[MixelOrdinaryTokenFixture]::stopFailure=$mode -eq 'stop-failure'
 [MixelOrdinaryTokenFixture]::stops=0;[MixelOrdinaryTokenFixture]::profiles=0;[MixelOrdinaryTokenFixture]::desktopRestores=0
 [AbaMock+Platform]::Mode=$(if($mode -eq 'original5-and-restore-false'){'restore-false'}elseif($mode -in @('restore-false','close-false','unload-throw')){$mode}else{'normal'})
 $errorText=$null;try{& ([scriptblock]::Create("try {`n  Start-StandardValidQaNamespace`n"+$eight+$tail))}catch{$errorText=$_.Exception.Message}
 $unsafe=$mode -in @('restore-false','original5-and-restore-false','close-false','unload-throw')
 if($script:drains -ne 1 -or [MixelOrdinaryTokenFixture]::desktopRestores -ne 1){throw ('Independent pending drain/desktop restore skipped: '+$mode)}
 if($unsafe){
  if($script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 0 -or -not [IO.Directory]::Exists($fixtureRoot)){throw 'Unknown namespace deleted owned account/profile/root'}
  if(-not $errorText){throw 'Unknown namespace silently passed'}
 }elseif(-not $script:userRemoved -or [MixelOrdinaryTokenFixture]::profiles -ne 1 -or [IO.Directory]::Exists($fixtureRoot)){throw ('Verified restoration did not preserve original cleanup: '+$mode)}
 if($script:originalFailure -and $errorText -cne 'ORIGINAL_NATIVE5'){throw 'Original native5 was replaced by cleanup failure'}
 if($mode -eq 'normal'){
  if($errorText -or $script:eightCalls.Count -ne 8 -or ($script:eightCalls.error -join ',') -cne '2,0,0,2,5,6,6,2'){throw 'Exact original8 assertion modes/codes changed'}
  if($script:eightCalls[4].attestation -cne 'guard-unavailable' -or -not $script:eightCalls[6].failedOwner -or -not $script:eightCalls[1].kept){throw 'Original negative/ownership semantics changed'}
 }
 if($mode -eq 'protocol-failure' -and -not $errorText){throw 'Deferred protocol failure lost fatal semantics'}
}
'PASS: literal original8 and full outer finally8 fault scenarios; codes2,0,0,2,5,6,6,2; restore/dispose before protocol drain; desktop independent; unknown account/profile/root preserved; original5 firstfatal.'
'''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
        print(invoke(body, KNOWN.read_text() + mock + fixture).splitlines()[-1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
