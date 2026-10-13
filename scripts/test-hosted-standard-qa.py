"""Source/mock qualification only. Never runs Windows native setup or children."""
import hashlib
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
DRIVER = SCRIPTS / 'invoke-hosted-standard-qa-windows.ps1'
KNOWN = SCRIPTS / 'test-hosted-qa-session-directory.cs'
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
        (root / 'run.ps1').write_bytes((prefix + body).encode('utf-8-sig'))
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File', str(root / 'run.ps1')],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode:
            raise AssertionError(f'Actual source control exit{result.returncode}\n{result.stdout}\n{result.stderr}')
        return result.stdout


FUNCTIONS = r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__DRIVER__',[ref]$t,[ref]$e)
if($e.Count){throw 'Actual driver parse error'}
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if($functions.Count -ne 4){throw 'Exact four function family changed'}
foreach($fn in $functions){
 $text=$fn.Extent.Text.Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)').Replace("[MixelHostedQaNamespace+NativeApi]::new('StandardSource')",'[AbaMock+Platform]::new()')
 . ([scriptblock]::Create($text))
}
'''.replace('__DRIVER__', str(DRIVER).replace("'", "''"))


class HostedFixtureControls(unittest.TestCase):
    def test_actual_caller_activation_exports_final_pins_and_step_only_environment(self):
        source = (SCRIPTS / 'test-support-lease.py').read_text()
        tree = ast.parse(source)
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        caller = main.body[-2]
        self.assertIsInstance(caller, ast.If)
        self.assertEqual(ast.unparse(caller.test), "os.name == 'nt'")
        for toggle in (None, '', '1', '0', 'true', ' 1', '1 '):
            for native_failure in (False, True):
                calls = []
                original = subprocess.CalledProcessError(5, 'original-native5')
                def run(arguments, **kwargs):
                    calls.append((arguments, kwargs))
                    if native_failure:
                        raise original
                environment = {} if toggle is None else {'MIXEL_HOSTED_OWNED_QA_NAMESPACE': toggle}
                namespace = {'os': SimpleNamespace(name='nt', environ=environment), 'ROOT': ROOT,
                             'shutil': SimpleNamespace(which=lambda name: 'owned-pwsh'),
                             'subprocess': SimpleNamespace(run=run)}
                caught = None
                try:
                    exec(compile(ast.Module(body=[caller], type_ignores=[]), str(SCRIPTS / 'test-support-lease.py'), 'exec'), namespace)
                except Exception as error:
                    caught = error
                if toggle not in (None, '', '1'):
                    self.assertIsInstance(caught, RuntimeError)
                    self.assertEqual(str(caught), 'Invalid hosted owned QA namespace request')
                    self.assertEqual(calls, [])
                else:
                    driver = 'invoke-hosted-standard-qa-windows.ps1' if toggle == '1' else 'test-support-lease-standard-windows.ps1'
                    self.assertEqual(calls, [(['owned-pwsh', '-NoProfile', '-File', str(SCRIPTS / driver)], {'check': True, 'timeout': 180})])
                    self.assertIs(caught, original if native_failure else None)
        entry = tree.body[-1]
        self.assertIsInstance(entry, ast.If)
        for export in (None, Path('owned-export')):
            calls = []
            arguments = SimpleNamespace(export_standard_windows_fixture=export, event='isolated' if export else None,
                                        export_standard_windows_subsystem_pair=bool(export))
            parser = SimpleNamespace(add_argument=lambda *args, **kwargs: None, parse_args=lambda: arguments)
            namespace = {'__name__': '__main__', '__doc__': '', 'Path': Path,
                         'argparse': SimpleNamespace(ArgumentParser=lambda **kwargs: parser),
                         'export_standard_windows_fixture': lambda *args: calls.append(('export', args)),
                         'main': lambda: calls.append(('main', ()))}
            exec(compile(ast.Module(body=[entry], type_ignores=[]), str(SCRIPTS / 'test-support-lease.py'), 'exec'), namespace)
            self.assertEqual(calls, [('export', (export, 'isolated', True))] if export else [('main', ())])
        pins = json.loads(re.search(r"\$pins=ConvertFrom-Json @'\n(.*?)\n'@", DRIVER.read_text(), re.S).group(1))
        self.assertEqual(len(pins), 9)
        body = FUNCTIONS
        for name, digest in pins.items():
            body += "Read-PinnedHostedSource '" + str(SCRIPTS / name).replace("'", "''") + "' '" + digest + "' | Out-Null\n"
        body += "'PASS: actual canonical reader accepts all nine final-carrier source pins before native setup.'\n"
        print(invoke(body).strip())
        workflow_rows = {
            'preflight.yml': [('windows-packaging', 'Exercise complete Windows generated source, keep-awake and native branding')],
            'build.yml': [('windows', 'Preflight generated support source and reporter'),
                          ('windows', 'Exercise the signed QuickSupport download without an invite'),
                          ('runtime', 'Diagnose signed compiled ordinary-to-QuickSupport ownership'),
                          ('runtime', 'Prove actual signed customer QuickSupport95 without debugger')]}
        for filename, rows in workflow_rows.items():
            workflow = (ROOT / '.github/workflows' / filename).read_text()
            self.assertEqual(workflow.count('MIXEL_HOSTED_OWNED_QA_NAMESPACE:'), len(rows))
            for job, step in rows:
                start = workflow.index('\n  ' + job + ':\n')
                match = re.search(r'\n  [A-Za-z][A-Za-z0-9_-]*:\n', workflow[start + 1:])
                segment = workflow[start:] if match is None else workflow[start:start + 1 + match.start()]
                marker = '      - name: ' + step + '\n'
                self.assertEqual(segment.count(marker), 1)
                owned = segment.split(marker, 1)[1].split('\n      - ', 1)[0]
                self.assertIn('        env:\n', owned)
                self.assertIn('          MIXEL_HOSTED_OWNED_QA_NAMESPACE: "1"\n', owned)
        dispatcher = (SCRIPTS / 'test-windows-generated-source.sh').read_text()
        for name in ('test-hosted-standard-qa', 'test-packaged-owned-qa'):
            self.assertEqual(dispatcher.count('  ' + name + ' \\\n'), 1)
            self.assertLess(dispatcher.index('  ' + name + ' \\\n'), dispatcher.index('  test-support-lease \\\n'))
        print('PASS: literal native caller14 unset/empty/exact1/invalid and native5 cases; export paths preserved; timeout180/check=True unchanged; five exact step-only environments; both mandatory adapter controls before native gate.')

    def test_complete_generated_script_parses_before_any_execution(self):
            body = FUNCTIONS + r'''
    $original=Read-PinnedHostedSource '__ORIGINAL__' 'a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f'
    $generated=New-HostedStandardQaScript $original (($functions | ForEach-Object {$_.Extent.Text}) -join "`n")
    $path=Join-Path $PSScriptRoot 'generated.ps1'
    [IO.File]::WriteAllText($path,$generated,[Text.UTF8Encoding]::new($true))
    $t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
    if($e.Count -or -not $tree){throw 'Complete composed Standard script parser refused'}
    if($generated.IndexOf('Start-HostedStandardQaNamespace') -lt 0 -or $generated.IndexOf('Restore-HostedStandardQaNamespace') -lt 0){throw 'Actual setup/restore omitted'}
    'PASS: complete generated script plus all original functions parse before execution; no native or child launch.'
    '''.replace('__ORIGINAL__', str(ORIGINAL).replace("'", "''"))
            print(invoke(body).strip())

    def test_actual_lf_crlf_checkout_fixture_profiles(self):
        # Replay the three Windows failures from real independent whole-script
        # checkout profiles, using literal production reader/composer functions.
        cases = ['HostedFixtureControls.test_complete_generated_script_parses_before_any_execution',
                 'HostedFixtureControls.test_literal_original_eight_and_outer_finally_fault_order',
                 'HostedFixtureControls.test_source_normalization_original_eight_boundaries_and_default_refusal']
        for profile in ('lf', 'crlf'):
            with tempfile.TemporaryDirectory(prefix='mixel-hosted-fixture-profile-') as folder:
                scripts=Path(folder)/'scripts'
                shutil.copytree(SCRIPTS,scripts,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                for path in scripts.rglob('*'):
                    if not path.is_file() or path.suffix not in ('.py','.sh','.ps1','.rs','.dart','.swift','.h','.mm','.c','.cpp','.cs','.bat'):
                        continue
                    raw=path.read_bytes()
                    if b'\r' in raw:
                        remainder=raw.replace(b'\r\n',b'')
                        self.assertNotIn(b'\r',remainder,str(path))
                        self.assertNotIn(b'\n',remainder,str(path))
                        raw=raw.replace(b'\r\n',b'\n')
                    path.write_bytes(raw if profile=='lf' else raw.replace(b'\n',b'\r\n'))
                result=subprocess.run([os.sys.executable,'-B','-Werror',str(scripts/'test-hosted-standard-qa.py'),*cases,'-v'],
                                      capture_output=True,text=True,encoding='utf-8',timeout=90)
                self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
                self.assertIn('Ran 3 tests',result.stderr)
                self.assertIn('\nOK\n',result.stderr)
        print('PASS: actual three former Windows fixture failures replayed from complete independent LF and CRLF script checkouts; strict source pins/refusals and original8/finally preserved.')

    def test_actual_scope_partial_failures_and_unknown_cleanup(self):
            mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
            mock = mock.replace('MixelSessionDirectoryAba', 'MixelHostedQaNamespace').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
            body = FUNCTIONS + r'''
    function Get-LocalUser {return [pscustomobject]@{Name=$ownedUser}}
    function Get-Process {return [pscustomobject]@{SessionId=2}}
    $ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid'
    $runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
    $freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
    foreach($mode in @('normal','set-false','set-zero-false','set-throw','query-fails','open-denied','restore-false','restore-readback-drift','close-false','unload-throw')){
     [AbaMock+Platform]::Mode=$mode;$script:qaNamespaceScope=$null;$script:qaNamespaceCleanupOkay=$true
     $startFailure=$null;try{Start-HostedStandardQaNamespace}catch{$startFailure=$_}
     $restoreFailure=$null;try{Restore-HostedStandardQaNamespace}catch{$restoreFailure=$_}
     $unsafe=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw')
     if(($null -ne $restoreFailure) -ne $unsafe -or $script:qaNamespaceCleanupOkay -eq $unsafe){throw ('Cleanup attribution changed: '+$mode)}
     $platform=[AbaMock+Platform]::Last
     if($platform.unloads -ne 1){throw 'Independent module dispose skipped'}
     if($mode -in @('set-false','set-zero-false','set-throw') -and ($platform.sets -ne 2 -or -not $script:qaNamespaceScope.facts.restorationVerified)){throw 'Partial setter did not restore exact original'}
     if($unsafe -and $script:qaNamespaceScope.facts.cleanupOkay){throw 'Failed native cleanup falsely qualified'}
    }
    $script:qaNamespaceScope=$null;$freshFacts.integrityRid=12288
    $refused=$false;try{Start-HostedStandardQaNamespace}catch{$refused=$true}
    if(-not $refused -or $script:qaNamespaceScope){throw 'Foreign/high QA identity mutated namespace'}
    'PASS: literal setup/restore10 scenarios; failed partial writes restore, close/module independently attempted, unknown cleanup cannot delete account; source identity refusal before mutation.'
    '''
            print(invoke(body, KNOWN.read_text() + mock).splitlines()[-1])

    def test_literal_original_eight_and_outer_finally_fault_order(self):
            mock = PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture', 1)[0]
            mock = mock.replace('MixelSessionDirectoryAba', 'MixelHostedQaNamespace').replace('mask==8', 'mask==15').replace('ToUInt32(a.descriptor,96)==8', 'ToUInt32(a.descriptor,96)==15')
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
    $original=Read-PinnedHostedSource '__ORIGINAL__' 'a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f';$generated=New-HostedStandardQaScript $original 'PREFIX'
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
     $errorText=$null;try{& ([scriptblock]::Create("try {`n  Start-HostedStandardQaNamespace`n"+$eight+$tail))}catch{$errorText=$_.Exception.Message}
     $unsafe=$mode -in @('stop-failure','restore-false','original5-and-restore-false','close-false','unload-throw')
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

    def test_pure_exact_hosted_workflow_scope_and_unchanged_native_core(self):
        original=(SCRIPTS/'test-standard-qa-session-directory.cs').read_text()
        actual=KNOWN.read_text()
        predicate=actual[actual.index('  // Pure scope decision:'):actual.index('  public sealed class NativeApi : Api {')]
        a=original.index('    public NativeApi() {');b=original.index('    public uint CurrentSession()',a)
        x=actual.index('    public NativeApi(string context) {');y=actual.index('    public uint CurrentSession()',x)
        restored=actual.replace(predicate,'').replace(actual[x:y],original[a:b]).replace('MixelHostedQaNamespace','MixelSessionDirectoryFixture')
        self.assertEqual(restored,original)
        code=r'''
public static class HostedFenceCases {
 public static int checks;
 static bool Allowed(bool os,bool x64,string actions,string repo,string environment,string job,string evt,string reference,string workflow,string toggle,string context) {
  return MixelHostedQaNamespace.HostedScopeAllowed(os,x64,actions,repo,environment,job,evt,reference,workflow,toggle,context);
 }
 static void Check(bool value,string reason){checks++;if(!value)throw new Exception("Hosted fence "+reason);}
 public static void Run(){
  string repo="Jangopeople/mixel-remote-client";
  string[][] rows={
   new[]{"StandardSource","windows-packaging","workflow_dispatch","refs/heads/fix/test","preflight.yml"},
   new[]{"StandardSource","windows-packaging","pull_request","refs/pull/7/merge","preflight.yml"},
   new[]{"StandardSource","windows-packaging","push","refs/heads/main","preflight.yml"},
   new[]{"StandardSource","windows","workflow_dispatch","refs/heads/fix/test","build.yml"},
   new[]{"StandardSource","windows","push","refs/tags/v1.4.6","build.yml"},
   new[]{"PackagedOrdinaryQa","windows","workflow_dispatch","refs/heads/fix/test","build.yml"},
   new[]{"PackagedOrdinaryQa","windows","push","refs/tags/v1.4.6","build.yml"},
   new[]{"PackagedOrdinaryQa","runtime","workflow_dispatch","refs/heads/fix/test","build.yml"}
  };
  foreach(var row in rows){
   string flow=repo+"/.github/workflows/"+row[4]+"@"+row[3];
   Check(Allowed(true,true,"true",repo,"github-hosted",row[1],row[2],row[3],flow,"1",row[0]),"valid");
   Check(!Allowed(false,true,"true",repo,"github-hosted",row[1],row[2],row[3],flow,"1",row[0]),"nonWindows");
   Check(!Allowed(true,false,"true",repo,"github-hosted",row[1],row[2],row[3],flow,"1",row[0]),"x86");
   Check(!Allowed(true,true,"false",repo,"github-hosted",row[1],row[2],row[3],flow,"1",row[0]),"local");
   Check(!Allowed(true,true,"true","Other/repo","github-hosted",row[1],row[2],row[3],flow,"1",row[0]),"foreignRepo");
   Check(!Allowed(true,true,"true",repo,"self-hosted",row[1],row[2],row[3],flow,"1",row[0]),"selfHosted");
   Check(!Allowed(true,true,"true",repo,"github-hosted","unapproved",row[2],row[3],flow,"1",row[0]),"foreignJob");
   Check(!Allowed(true,true,"true",repo,"github-hosted",row[1],"schedule",row[3],flow,"1",row[0]),"foreignEvent");
   Check(!Allowed(true,true,"true",repo,"github-hosted",row[1],row[2],null,flow,"1",row[0]),"missingRef");
   Check(!Allowed(true,true,"true",repo,"github-hosted",row[1],row[2],row[3],flow+"changed","1",row[0]),"wrongWorkflow");
   Check(!Allowed(true,true,"true",repo,"github-hosted",row[1],row[2],row[3],repo+"/.github/workflows/unapproved.yml@"+row[3],"1",row[0]),"wrongWorkflowPath");
   Check(!Allowed(true,true,"true",repo,"github-hosted",row[1],row[2],row[3],flow,"0",row[0]),"noOptIn");
   Check(!Allowed(true,true,"true",repo,"github-hosted",row[1],row[2],row[3],flow,"1","Unknown"),"foreignContext");
  }
  Check(!Allowed(true,true,"true",repo,"github-hosted","windows-packaging","push","refs/heads/fix/test",repo+"/.github/workflows/preflight.yml@refs/heads/fix/test","1","StandardSource"),"nonMainPush");
  Check(!Allowed(true,true,"true",repo,"github-hosted","windows","pull_request","refs/pull/7/merge",repo+"/.github/workflows/build.yml@refs/pull/7/merge","1","PackagedOrdinaryQa"),"buildNotPR");
  Check(!Allowed(true,true,"true",repo,"github-hosted","runtime","push","refs/tags/v1",repo+"/.github/workflows/build.yml@refs/tags/v1","1","PackagedOrdinaryQa"),"runtimeNotPush");
  Check(!Allowed(true,true,"true",repo,"github-hosted","runtime","workflow_dispatch","refs/heads/fix/test",repo+"/.github/workflows/build.yml@refs/heads/fix/test","1","StandardSource"),"standardNotRuntime");
  Check(!Allowed(true,true,"true",repo,"github-hosted","windows-packaging","workflow_dispatch","refs/heads/fix/test",repo+"/.github/workflows/preflight.yml@refs/heads/fix/test","1","PackagedOrdinaryQa"),"packageNotSourceJob");
  Console.WriteLine("PASS: actual pure hosted workflow/job/event/ref/context fence "+checks+" controls; exact known15 native/Scope bodies; no native APIs called.");
 }
}
'''
        print(invoke('[HostedFenceCases]::Run()',actual+code).strip())

    def test_source_normalization_original_eight_boundaries_and_default_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=ORIGINAL.read_bytes()
            if b'\r' in raw:
                remaining=raw.replace(b'\r\n',b'')
                self.assertNotIn(b'\r',remaining)
                self.assertNotIn(b'\n',remaining)
                raw=raw.replace(b'\r\n',b'\n')
            self.assertEqual(hashlib.sha256(raw).hexdigest(),'a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f')
            lf=root/'lf.ps1';crlf=root/'crlf.ps1';mixed=root/'mixed.ps1';drift=root/'drift.ps1'
            lf.write_bytes(raw);crlf.write_bytes(raw.replace(b'\n',b'\r\n'));mixed.write_bytes(raw.replace(b'\n',b'\r\n',1));drift.write_bytes(raw+b'# changed\n')
            digest=hashlib.sha256(raw).hexdigest()
            body=FUNCTIONS+r'''
$lf=Read-PinnedHostedSource '__LF__' '__HASH__'
$crlf=Read-PinnedHostedSource '__CRLF__' '__HASH__'
if($lf -cne $crlf){throw 'Homogeneous original script identity changed'}
$generated=New-HostedStandardQaScript $lf 'PREFIX'
$a=$lf.IndexOf("  `$absent = 'ownerFailed=false;",$lf.IndexOf("`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"));$b=$lf.IndexOf('  if ($currentCallerFailure)',$a)
$eight=$lf.Substring($a,$b-$a)
if($generated.IndexOf($eight) -lt 0 -or $generated.IndexOf($eight) -ne $generated.LastIndexOf($eight)){throw 'Original eight body changed'}
if($generated.Contains('Invoke-SessionDirectoryAba')){throw 'Main fixture silently adds optional A/B/A'}
foreach($path in @('__MIXED__','__DRIFT__')){
 $refused=$false;try{Read-PinnedHostedSource $path '__HASH__'|Out-Null}catch{$refused=$true};if(-not $refused){throw 'Mixed/drift source accepted'}
}
foreach($bad in @(($lf.Replace("`n  Complete-LeaseNtPendingProtocols`n",'')),($lf+$lf),($lf.Replace("  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { `$cleanup.Add('Exact owned standard lease process cleanup failed.') }",'')))){
 $refused=$false;try{New-HostedStandardQaScript $bad 'PREFIX'|Out-Null}catch{$refused=$true};if(-not $refused){throw 'Missing/duplicate original boundary accepted'}
}
'PASS: actual canonical LF/CRLF identity, mixed/drift/missing/duplicate refusals; whole original8 conserved; no optional A/B/A.'
'''
            for key,path in [('__LF__',lf),('__CRLF__',crlf),('__MIXED__',mixed),('__DRIFT__',drift)]:body=body.replace(key,str(path).replace("'","''"))
            print(invoke(body.replace('__HASH__',digest)).strip())
            destination=root/'evidence-absent';env=dict(os.environ,GITHUB_ACTIONS='false')
            result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-File',str(DRIVER),'-EvidenceDirectory',str(destination)],capture_output=True,text=True,encoding='utf-8',env=env,timeout=30)
            self.assertNotEqual(result.returncode,0);self.assertIn('Explicit hosted Windows owned QA source fixture required',result.stderr);self.assertFalse(destination.exists())


    def test_actual_reporter_and_generated_file_cleanup_are_independent(self):
        body=r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__DRIVER__',[ref]$t,[ref]$e)
if($e.Count){throw 'Actual wrapper parse changed'}
$outer=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})
if($outer.Count -ne 1 -or -not $outer[0].Finally){throw 'Exact outer cleanup source absent'}
$actualBody=($outer[0].Finally.Statements | ForEach-Object {$_.Extent.Text}) -join "`n"
foreach($case in @('normal','partial','report-failed','foreign-source','original5-report-failed','original5-foreign-source','unowned-source')){
 $destination=Join-Path $PSScriptRoot $case;[void][IO.Directory]::CreateDirectory($destination)
 $temporary=Join-Path $destination 'owned.ps1';$bytes=[Text.UTF8Encoding]::new($false).GetBytes('known source bytes')
 [IO.File]::WriteAllBytes($temporary,$bytes)
 $owned=$case -ne 'unowned-source';$wrapperFailure=$null
 if($case.StartsWith('original5-')){try{throw 'ORIGINAL_NATIVE5'}catch{$wrapperFailure=$_}}
 $script:hostedGeneratedHash=(Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
 if($case -eq 'partial'){$script:hostedGeneratedHash=$null;[IO.File]::WriteAllBytes($temporary,[byte[]]$bytes[0..4])}
 $foreign=$case -in @('foreign-source','original5-foreign-source','unowned-source')
 if($foreign){[IO.File]::WriteAllText($temporary,'foreign content')}
 $reportPath=Join-Path $destination 'fixture-namespace.json'
 $reportExists=$case -in @('report-failed','original5-report-failed')
 if($reportExists){[IO.File]::WriteAllText($reportPath,'foreign report')}
 $script:qaNamespaceScope=[pscustomobject]@{facts=[pscustomobject]@{cleanupOkay=$true}}
 $script:qaNamespaceCleanupOkay=$true;$script:qaProcessesKnownStopped=$true
 $failure=$null;try{& ([scriptblock]::Create($actualBody))}catch{$failure=$_}
 if($foreign){if(-not [IO.File]::Exists($temporary) -or [IO.File]::ReadAllText($temporary) -cne 'foreign content'){throw 'Foreign/unowned source deleted'}}
 elseif([IO.File]::Exists($temporary)){throw 'Owned source not independently removed'}
 if($reportExists -and [IO.File]::ReadAllText($reportPath) -cne 'foreign report'){throw 'Foreign report overwritten'}
 $shouldFail=$case -in @('report-failed','foreign-source')
 if(($null -ne $failure) -ne $shouldFail){throw ('Report/temp fatal attribution changed: '+$case)}
 if($wrapperFailure -and $wrapperFailure.Exception.Message -cne 'ORIGINAL_NATIVE5'){throw 'Original native5 replaced'}
}
'PASS: literal actual reporter/temp-finally seven real filesystem cases; report failure still cleans owned source; partial owned prefix removed; foreign/unowned preserved; original5 retained.'
'''.replace('__DRIVER__',str(DRIVER).replace("'","''"))
        print(invoke(body).splitlines()[-1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
