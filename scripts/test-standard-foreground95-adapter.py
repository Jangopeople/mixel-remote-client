#!/usr/bin/env python3
"""Pure/file/parse/compile/source-mock controls only. NEVER native Windows proof."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = next(parent for parent in (HERE,*HERE.parents) if (parent/'scripts/test-support-launch-windows.ps1').is_file())
SPEC = importlib.util.spec_from_file_location('adapter',HERE/'prepare-standard-foreground95.py')
A = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(A)


def gate():
    return {'schema':1,'scope':A.G.SCOPE,
            'root_whole_green_verified':True,'source':A.SOURCE,'tree':A.TREE,'producer_run':A.RUN,
            'producer_attempt':A.ATTEMPT,'producer_windows_job':A.WINDOWS_JOB,
            'preflight_run':A.PREFLIGHT,'comparison_run':A.COMPARISON,
            'checks':{name:'success' for name in ('all_required_producer_jobs','mandatory_preflight',
                     'actual2022_source_comparison','actual2025_source_comparison')},
            'original_evidence_sha256':{name:'a'*64 for name in A.G.ROLES},
            'reviewed_harness_blobs':{'scripts/prepare-standard-foreground95.py':'d'*64,
                                      'scripts/test-standard-foreground95-adapter.py':'f'*64,
                                      '.github/workflows/build.yml':'e'*64,
                                      'scripts/standard95_guard.py':'c'*64,
                                      'scripts/capture-standard95-state.py':'b'*64,
                                      'scripts/test-standard95-normal-guards.py':'1'*64,
                                      'scripts/standard_foreground95_workflow_projection.py':'2'*64},
            'artifacts':{name:{'id':index+1,'digest':'sha256:'+'b'*64}
                         for index,name in enumerate(('windows','windows-msix-payload'))}}


def collection():
    return {'evidence_kind':'debugger-free-windows-customer-release-gate-inputs',
            'repository':'Jangopeople/mixel-remote-client','publication':False,
            'build_run_id':str(A.RUN),'source_commit':A.SOURCE,'application_source_baseline':A.SOURCE,
            'producer_windows_job_id':A.WINDOWS_JOB,'producer_windows_job_conclusion':'success',
            'build_status':'completed','build_conclusion':'success',
            'collected_artifacts':{name:{'artifact_id':index+1,'artifact_digest':'sha256:'+'b'*64,
                'actual_archive_digest':'sha256:'+'b'*64,'files_sha256':{'artifacts/'+name+'/synthetic.exe':'c'*64}}
                for index,name in enumerate(('windows','windows-msix-payload'))}}


def pwsh(text):
    command = shutil.which('pwsh')
    if not command:
        raise AssertionError('Actual PowerShell source controls require pwsh')
    with tempfile.TemporaryDirectory(prefix='mixel-standard95-source-') as folder:
        script = Path(folder)/'control.ps1'; script.write_text(text)
        result = subprocess.run([command,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script)],
                                capture_output=True,text=True,timeout=90)
        if result.returncode:
            raise AssertionError(f'Pure/source control failed {result.returncode}\n{result.stdout}\n{result.stderr}')
        return result.stdout


class StagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = A.read_sources(ROOT)
        cls.original = cls.sources[A.DRIVER]
        cls.candidate,cls.edits = A.projected(cls.original,cls.sources[A.OBSERVER])

    def test_current_source1979_original_pins_and_canonical_lf_crlf(self):
        for name,text in self.sources.items():
            self.assertEqual(A.sha(text.encode()),A.PINS[name])
            self.assertEqual(A.canonical(text.replace('\n','\r\n').encode()),text)
        for bad in [b'a\r\nb\nc',b'a\rb',b'\xff',b'\xef\xbb\xbfa']:
            with self.subTest(bad=bad), self.assertRaises((ValueError,UnicodeError)): A.canonical(bad)

    def test_reversible_complete_projection_keeps_original_outer_95_and_finally(self):
        restored = self.candidate
        for old,new in reversed(self.edits):
            restored = A.replace_one(restored,new,old)
        self.assertEqual(restored,self.original)
        self.assertEqual(A.sha(restored.encode()),A.PINS[A.DRIVER])
        for first,last in [('function Start-CustomerApp {','function Find-MainWindow('),
                           ('} finally {\n  $ownedPassword=$null; $random=$null','\nif ($QuickSupport -and -not $OrdinaryThenQuickSupport)')]:
            start = self.original.index(first); end = self.original.index(last,start)
            self.assertIn(self.original[start:end],self.candidate)
        self.assertIn('while ($started.Elapsed.TotalSeconds -lt 95)',self.candidate)
        self.assertIn('StartStandardUser($ordinaryExecutable, $ownedUser, $ownedPassword, [bool]$NativeCrashDiagnostic)',self.candidate)
        self.assertEqual(self.candidate.count(A.INITIAL_ASSERT),1)
        self.assertEqual(self.candidate.count(A.FINAL_ASSERT),1)
        self.assertNotIn('Set-ExecutionPolicy',self.candidate)
        self.assertNotIn('Get-ExecutionPolicy',self.candidate)
        self.assertEqual(self.candidate.count("$ordinaryPid = [MixelOrdinaryTokenFixture]::StartLinkedToken($ordinaryExecutable)"),1)
        self.assertIn('standardOuterQualified=$false',self.candidate)

    def test_each_pinned_input_drift_refuses_before_output_or_native_seam(self):
        with tempfile.TemporaryDirectory(prefix='mixel-standard95-drift-') as folder:
            root = Path(folder).resolve(); (root/'scripts').mkdir()
            for name,text in self.sources.items(): (root/'scripts'/name).write_text(text)
            for name in A.PINS:
                path=root/'scripts'/name; original=path.read_bytes();path.write_bytes(original+b'\n# drift\n')
                with self.subTest(name=name), self.assertRaisesRegex(ValueError,'Pinned source drift'): A.read_sources(root)
                path.write_bytes(original)

    def test_gate_requires_current1979_full_success_identity_and_original_evidence(self):
        self.assertEqual(A.validate_gate(gate())['source'],A.SOURCE)
        for key,value in [('root_whole_green_verified',False),('producer_run',2),('producer_attempt',2),
                          ('producer_attempt',True),('source','2b'*20),('tree','0'*40),('schema',True),
                          ('preflight_run',2),('comparison_run',2),('producer_windows_job',2),
                          ('checks',{}),('original_evidence_sha256',{}),('reviewed_harness_blobs',{})]:
            candidate=gate();candidate[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): A.validate_gate(candidate)
        for name in gate()['checks']:
            candidate=gate();candidate['checks'][name]='skipped'
            with self.subTest(name=name), self.assertRaises(ValueError): A.validate_gate(candidate)
        with self.assertRaisesRegex(ValueError,'Duplicate'): A.unique_json('{"schema":1,"schema":1}')

    def test_collector_roles_source_actual_digest_and_root_bindings(self):
        self.assertEqual(A.validate_collection(collection(),gate())['source_commit'],A.SOURCE)
        for key,value in [('source_commit','2b'*20),('build_run_id','1'),('producer_windows_job_id',2),
                          ('publication',True),('build_conclusion','failure'),('application_source_baseline','0'*40)]:
            candidate=collection();candidate[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): A.validate_collection(candidate,gate())
        for change in ('id','digest','actual-digest','role','files'):
            candidate=collection()
            if change=='id':candidate['collected_artifacts']['windows']['artifact_id']=99
            elif change=='digest':candidate['collected_artifacts']['windows']['artifact_digest']='sha256:'+'c'*64
            elif change=='actual-digest':candidate['collected_artifacts']['windows']['actual_archive_digest']='sha256:'+'c'*64
            elif change=='role':candidate['collected_artifacts']['windows-native-symbols']={}
            else:candidate['collected_artifacts']['windows']['files_sha256']={}
            with self.subTest(change=change), self.assertRaises(ValueError): A.validate_collection(candidate,gate())
        candidate=gate();candidate['artifacts']['windows']['id']=True
        with self.assertRaisesRegex(ValueError,'actual positive integer'):A.validate_collection(collection(),candidate)

    def test_unbound_template_and_staged_untracked_harness_cannot_qualify(self):
        with tempfile.TemporaryDirectory(prefix='mixel-standard95-unbound-') as folder:
            root=Path(folder).resolve(); gp=root/'gate.json';cp=root/'collection.json';out=root/('mixel-standard-foreground95-'+'a'*32)
            candidate=gate();candidate['root_whole_green_verified']=False
            gp.write_text(json.dumps(candidate));cp.write_text(json.dumps(collection()))
            with (patch.object(A,'verify_harness',side_effect=AssertionError('Unexpected harness/native work')),
                  self.assertRaisesRegex(ValueError,'not verified')):
                A.prepare(ROOT,out,gp,A.sha(gp.read_bytes()),cp,'d'*40,'e'*40,root/'live')
            self.assertFalse(out.exists())
        with (patch.object(A,'__file__',str(ROOT/'artifacts/owned-untracked-adapter-fixture.py')),
              patch.object(A.subprocess,'check_output',side_effect=[b'd'*40+b'\n',b'e'*40+b'\n',b'']),
              self.assertRaisesRegex(ValueError,'reviewed tracked')):
            A.verify_harness(ROOT,'d'*40,'e'*40,gate()['reviewed_harness_blobs'],ROOT/'scripts/unbound.json','a'*64)

    def test_future_harness_is_distinct_tracked_clean_and_seals_gate_without_self_reference(self):
        with tempfile.TemporaryDirectory(prefix='mixel-standard95-harness-') as folder:
            root=Path(folder).resolve();(root/'scripts').mkdir();(root/'.github/workflows').mkdir(parents=True)
            adapter=root/'scripts/prepare-standard-foreground95.py';adapter.write_text('synthetic reviewed adapter fixture\n')
            caller=root/'.github/workflows/build.yml';caller.write_text('synthetic reviewed runtime caller fixture\n')
            gp=root/'scripts/current1979-root-gate.json';gp.write_text('synthetic tracked root seal fixture\n')
            blobs={adapter.relative_to(root).as_posix():A.sha(adapter.read_bytes()),caller.relative_to(root).as_posix():A.sha(caller.read_bytes())}
            dirty=False
            def git(argv,**kwargs):
                self.assertEqual(argv[:3],['git','-C',str(root)])
                args=argv[3:]
                if args==['rev-parse','HEAD']:return b'd'*40+b'\n'
                if args==['rev-parse','HEAD^{tree}']:return b'e'*40+b'\n'
                if args==['diff','--name-only','HEAD','--']:return b'scripts/dirty.py\n' if dirty else b''
                if args[0]=='show':
                    revision,name=args[1].split(':',1);self.assertEqual(revision,'d'*40)
                    return (root/name).read_bytes()
                if args[:2]==['status','--porcelain']:return b''
                raise AssertionError('Unexpected git/native/provider operation')
            with patch.object(A,'__file__',str(adapter)),patch.object(A.subprocess,'check_output',side_effect=git):
                result=A.verify_harness(root,'d'*40,'e'*40,blobs,gp,A.sha(gp.read_bytes()))
                self.assertNotEqual(result['source'],A.SOURCE)
                self.assertEqual(result['root_gate_sha256'],A.sha(gp.read_bytes()))
                self.assertNotIn(result['root_gate_path'],blobs)
                with self.assertRaisesRegex(ValueError,'checkout differs'):
                    A.verify_harness(root,'f'*40,'e'*40,blobs,gp,A.sha(gp.read_bytes()))
                changed=dict(blobs);changed['.github/workflows/build.yml']='0'*64
                with self.assertRaisesRegex(ValueError,'blob differs'):
                    A.verify_harness(root,'d'*40,'e'*40,changed,gp,A.sha(gp.read_bytes()))
                dirty=True
                with self.assertRaisesRegex(ValueError,'source is dirty'):
                    A.verify_harness(root,'d'*40,'e'*40,blobs,gp,A.sha(gp.read_bytes()))

    def test_prepare_complete_file_contract_with_explicit_synthetic_harness_facade_only(self):
        with tempfile.TemporaryDirectory(prefix='mixel-standard95-file-') as folder:
            root=Path(folder).resolve();gp=root/'gate.json';cp=root/'collection.json';out=root/('mixel-standard-foreground95-'+'a'*32)
            gp.write_text(json.dumps(gate()));cp.write_text(json.dumps(collection()))
            fake={'source':'d'*40,'tree':'e'*40,'adapter_sha256':'f'*64,'synthetic_source_control_only':True}
            with patch.dict(os.environ,{'RUNNER_TEMP':str(root)}),patch.object(A,'verify_harness',return_value=fake),\
                 patch.object(A.G,'originals',return_value={'syntheticSourceOnly':True}),\
                 patch.object(A.G,'live_state',return_value={'syntheticSourceOnly':'a'*64}):
                result=A.prepare(ROOT,out,gp,A.sha(gp.read_bytes()),cp,'d'*40,'e'*40,root/'live')
                self.assertEqual(result['harness'],fake)
                self.assertIs(result['native_runtime_qualified'],False)
                self.assertIs(result['standard_outer_qualified'],False)
                self.assertEqual((out/'test-standard-foreground95.generated.ps1').read_text(),self.candidate)
                for name in A.PINS:
                    if name!=A.DRIVER:self.assertEqual(A.sha((out/name).read_bytes()),A.PINS[name])
                with self.assertRaisesRegex(ValueError,'Existing output'):
                    A.prepare(ROOT,out,gp,A.sha(gp.read_bytes()),cp,'d'*40,'e'*40,root/'live')

    def test_actual_powershell_parser_original_function_conservation_and_existing_csharp_compile_only(self):
        with tempfile.TemporaryDirectory(prefix='mixel-standard95-parse-') as folder:
            folder=Path(folder).resolve();original=folder/'original.ps1';candidate=folder/'candidate.ps1'
            original.write_text(self.original);candidate.write_text(self.candidate)
            ps=r'''$ErrorActionPreference='Stop'
$t=$null;$e=$null;$old=[Management.Automation.Language.Parser]::ParseFile('__OLD__',[ref]$t,[ref]$e)
if($e.Count){throw 'Original complete parse failed'}
$t=$null;$e=$null;$new=[Management.Automation.Language.Parser]::ParseFile('__NEW__',[ref]$t,[ref]$e)
if($e.Count){throw ('Projected complete parse failed: '+$e)}
$before=@($old.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
$after=@($new.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
foreach($f in $before){$same=@($after|Where-Object{$_.Name -ceq $f.Name});if($same.Count-ne 1-or$same[0].Extent.Text-cne$f.Extent.Text){throw ('Changed original function: '+$f.Name)}}
if($after.Count-ne$before.Count+1){throw 'Unexpected added function family'}
$text=[IO.File]::ReadAllText('__NEW__')
$class=$text.IndexOf('public static class MixelOrdinaryTokenFixture {')
$first=$text.LastIndexOf('using System;',$class);$last=$text.IndexOf("`n'@ }",$class)
if($class-lt 0-or$first-lt 0-or$last-le$first){throw 'Existing full CSharp fixture boundary changed'}
Add-Type -TypeDefinition $text.Substring($first,$last-$first) -CompilerOptions /warnaserror+ -ErrorAction Stop
Add-Type -TypeDefinition ([IO.File]::ReadAllText('__HELPER__')) -CompilerOptions /warnaserror+ -ErrorAction Stop
$reference='refs/heads/codex/qa-standard-foreground95'
$workflow='Jangopeople/mixel-remote-client/.github/workflows/build.yml@'+$reference
if(-not[MixelHostedQaNamespace]::HostedScopeAllowed($true,$true,'true','Jangopeople/mixel-remote-client','github-hosted','runtime','workflow_dispatch',$reference,$workflow,'1','PackagedOrdinaryQa')){throw 'Legitimate future QA branch runtime scope refused'}
if([MixelHostedQaNamespace]::HostedScopeAllowed($true,$true,'true','Jangopeople/mixel-remote-client','github-hosted','new-runtime-job','workflow_dispatch',$reference,$workflow,'1','PackagedOrdinaryQa')){throw 'New job identity expanded native scope'}
'PASS: complete projected PowerShell parses; every original function is byte-exact; existing full native fixture plus existing observer compiles warning-fatal; no native API invoked.'
'''.replace('__OLD__',str(original).replace("'","''")).replace('__NEW__',str(candidate).replace("'","''")).replace('__HELPER__',str(ROOT/'scripts/test-hosted-qa-session-directory.cs').replace("'","''"))
            self.assertIn('PASS:',pwsh(ps))

    def test_actual_new_assertion_source_mock_refusals_never_native_acceptance(self):
        function=A.ASSERT_FUNCTION
        self.assertEqual(function.count('[Security.Principal.SecurityIdentifier]::new('),3)
        function=function.replace('[Security.Principal.SecurityIdentifier]::new(','[IdentityFixtureSid]::new(')
        ps=r'''$ErrorActionPreference='Stop'
Add-Type @'
public class IdentityFixtureSid{public string Value;public IdentityFixtureSid(string value){Value=value;}}
public static class MixelOrdinaryTokenFixture {
 public static string State="still-running";
 public static object Facts;
 public static string StartedStatus(int pid){if(pid!=403)throw new System.Exception("Foreign retained pid");return State;}
 public static object ObserveOwnedDesktopPrimary(int pid,string sid){if(pid!=403||sid!="private-created-SID")throw new System.Exception("Foreign retained identity");return Facts;}
}
'@
function Get-LocalUser {param($SID) if($SID.Value-cne'private-created-SID'){throw 'Foreign account query'};[pscustomobject]@{Name='mixelqs0123456789';Enabled=$true}}
function Get-LocalGroupMember {param($SID) if($SID.Value-ceq'S-1-5-32-544'){if($script:admin){[pscustomobject]@{SID=[pscustomobject]@{Value='private-created-SID'}}}}elseif($SID.Value-ceq'S-1-5-32-545'){if($script:users){[pscustomobject]@{SID=[pscustomobject]@{Value='private-created-SID'}}}}else{throw 'Foreign group query'}}
function Get-Process {param($Id) if($Id-ne$PID){throw 'Foreign runner query'};[pscustomobject]@{SessionId=2}}
__FUNCTION__
$ownedSid='private-created-SID';$ownedUser='mixelqs0123456789';$main=[pscustomobject]@{Id=403;SessionId=2}
foreach($mode in @('normal','admin','notUsers','elevated','wrongSid','wrongSession','closedToken','unavailable','unowned','wrongPid','closeError','exited')){
 $script:admin=$mode-ceq'admin';$script:users=$mode-cne'notUsers';[MixelOrdinaryTokenFixture]::State=if($mode-ceq'exited'){'exited:0x00000000'}else{'still-running'}
 $f=[pscustomobject]@{pid=403;retainedPidMatched=$true;status='available';operation='complete';failureType=$null;nativeError=$null;tokenCloseError=$null;ownedSidMatched=$true;elevated=$false;tokenSessionId=2;tokenOpened=$true;tokenCloseAttempted=$true;tokenClosed=$true}
 switch($mode){'elevated'{$f.elevated=$true}'wrongSid'{$f.ownedSidMatched=$false}'wrongSession'{$f.tokenSessionId=3}'closedToken'{$f.tokenClosed=$false}'unavailable'{$f.status='unavailable'}'unowned'{$f.retainedPidMatched=$false}'wrongPid'{$f.pid=404}'closeError'{$f.tokenCloseError=6}}
 [MixelOrdinaryTokenFixture]::Facts=$f
 $failed=$false;try{$record=@(Assert-MixelOwnedStandardForeground95 'source-mock-only' 6>&1)}catch{$failed=$true}
 if($mode-ceq'normal'){if($failed-or$record.Count-ne1-or$record[0].ToString().Contains($ownedSid)-or-not$record[0].ToString().Contains('"standardOuterQualified":false')){throw 'Source positive/redaction/qualification bound failed'}}elseif(-not$failed){throw ('Source refusal lost: '+$mode)}
}
'PASS: actual new retained-identity assertion rejects eleven mocked failures; private SID omitted; source controls only, no native/account/desktop APIs invoked.'
'''.replace('__FUNCTION__',function)
        self.assertIn('PASS:',pwsh(ps))

    def test_actual_reviewed_runtime_caller_parses_and_preserves_native_argument_interface(self):
        spec=importlib.util.spec_from_file_location('standard95_workflow',ROOT/'scripts/standard_foreground95_workflow_projection.py')
        workflow=importlib.util.module_from_spec(spec);spec.loader.exec_module(workflow)
        actual=(ROOT/'.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertEqual(A.sha(workflow.inverse_standard_foreground95_runtime_checks(actual,'build.yml').encode()),
                         workflow.APP_BUILD_WORKFLOW_SHA256)
        text='\n'.join(line[10:] for line in workflow.CALLER_CURRENT.splitlines())+'\n'
        with tempfile.TemporaryDirectory(prefix='mixel-standard95-caller-') as folder:
            path=Path(folder)/'actual-reviewed-caller.ps1';path.write_text(text)
            ps=r'''$ErrorActionPreference='Stop'
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('__CALLER__',[ref]$t,[ref]$e)
if($e.Count){throw ('Actual reviewed workflow caller parse failed: '+$e)}
$native=@($ast.FindAll({param($node)$node-is[Management.Automation.Language.CommandAst]-and$node.GetCommandName()-ceq'pwsh'},$true))
if($native.Count-ne1){throw 'Exactly one actual fresh native harness process required'}
$argv=@($native[0].CommandElements|ForEach-Object{$_.Extent.Text})
$expected=@('pwsh','-NoLogo','-NoProfile','-NonInteractive','-File','$generated','-Executable','(Resolve-Path release-client/Mixel-Remote-QS.exe).Path','-Portable','-QuickSupport','-ExpectedPayload','(Resolve-Path runtime-client).Path','-OrdinaryThenQuickSupport')
if($argv.Count-ne$expected.Count){throw 'Actual caller changed native driver argument count'}
for($i=0;$i-lt$argv.Count;$i++){if($argv[$i]-cne$expected[$i]){throw 'Actual caller changed native signed-outer interface'}}
$preparers=@($ast.FindAll({param($node)$node-is[Management.Automation.Language.CommandAst]-and$node.GetCommandName()-ceq'python'-and$node.Extent.Text.Contains('scripts/prepare-standard-foreground95.py')},$true))
if($preparers.Count-ne1-or-not$preparers[0].Extent.Text.Contains('--harness-source $env:GITHUB_SHA --harness-tree $tree --live-state $env:NORMAL95_BEFORE')){throw 'Actual future harness attribution lost'}
'PASS: actual reviewed runtime PowerShell caller parses; one fresh native driver preserves all13 exact signed-outer arguments and separate future harness attribution; nothing was executed.'
'''.replace('__CALLER__',str(path).replace("'","''"))
            self.assertIn('PASS:',pwsh(ps))


if __name__=='__main__':
    unittest.main(verbosity=2)
