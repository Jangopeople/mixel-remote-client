#!/usr/bin/env python3
"""Actual extracted diagnostic/predicate controls; native APIs are mocked only."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lease_observation_controls", ROOT / "scripts/test-windows-lease-observation.py")
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)

MOCK = r'''
using System; using System.ComponentModel; using System.Runtime.InteropServices;
public static class MixelOrdinaryTokenFixture {
  public static int Variant,Error,Queries,Closes,Allocations,Frees,EffectiveThreadQueries,ElevationCalls;
  public static bool PredicateElevated;
  static System.Collections.Generic.Dictionary<int,IntPtr> StartedProcesses=new System.Collections.Generic.Dictionary<int,IntPtr>{{403,new IntPtr(401)}};
  public static void Reset(int variant){Variant=variant;Error=Queries=Closes=Allocations=Frees=EffectiveThreadQueries=0;}
  public static bool Elevated(int pid){if(pid!=403)throw new Exception("Wrong predicate PID");ElevationCalls++;return PredicateElevated;}
  public static string StartedStatus(int pid){if(pid!=403)throw new Exception("Wrong retained status PID");if(Variant==16)throw new InvalidOperationException("Injected retained status query failure");return "still-running";}
  static uint GetProcessId(IntPtr process){if(process!=new IntPtr(401))throw new Exception("Unowned process handle");Error=6;return Variant==14?0U:403U;}
  static bool OpenProcessToken(IntPtr process,uint access,out IntPtr token){if(process!=new IntPtr(401)||access!=8)throw new Exception("Wrong primary token subject/access");Queries++;token=new IntPtr(501);Error=5;return Variant!=4;}
  static bool GetTokenInformation(IntPtr token,int kind,IntPtr data,int size,out int required){
    if(token!=new IntPtr(501)||size!=4||(kind!=20&&kind!=12))throw new Exception("Wrong token information arguments");
    required=(Variant==7&&kind==20)||(Variant==8&&kind==12)?3:4;
    bool success=!((Variant==5||Variant==15)&&kind==20)&&!(Variant==6&&kind==12);
    Error=Variant==15?0:kind==20?87:6;
    System.Runtime.InteropServices.Marshal.WriteInt32(data,kind==20?(Variant==1?1:0):(Variant==2?3:2));return success;
  }
  static bool CloseHandle(IntPtr token){if(token!=new IntPtr(501))throw new Exception("Wrong close handle");Closes++;Error=Variant==12?0:6;return Variant!=11&&Variant!=12;}
  static class Marshal {
    public static int GetLastWin32Error(){return Error;}
    public static IntPtr AllocHGlobal(int size){if(size!=4)throw new Exception("Wrong allocation");Allocations++;return System.Runtime.InteropServices.Marshal.AllocHGlobal(size);}
    public static void FreeHGlobal(IntPtr data){Frees++;Error=999;System.Runtime.InteropServices.Marshal.FreeHGlobal(data);}
    public static int ReadInt32(IntPtr data){return System.Runtime.InteropServices.Marshal.ReadInt32(data);}
  }
  sealed class UserIdentity {public string Value;}
  sealed class WindowsIdentity:IDisposable {
    public UserIdentity User;
    public WindowsIdentity(IntPtr token){if(token!=new IntPtr(501))throw new Exception("Wrong identity handle");if(Variant==9)throw new InvalidOperationException("Injected constructor failure");User=new UserIdentity{Value=Variant==3?"different-primary-SID":"private-owned-SID"};}
    public static WindowsIdentity GetCurrent(){EffectiveThreadQueries++;throw new Exception("Wrong thread identity subject");}
    public void Dispose(){if(Variant==10)throw new InvalidOperationException("Injected disposal failure");}
  }
'''

SCRIPT = r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) {throw 'Actual required PowerShell edition/version missing'}
$pipeline=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-windows-pipeline.ps1'))
$launch=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-launch-windows.ps1'))
$class=$launch.IndexOf('public static class MixelOrdinaryTokenFixture {');$first=$launch.LastIndexOf('using System;',$class);$last=$launch.IndexOf("`n'@ }",$class)
if($class -lt 0 -or $first -lt 0 -or $last -le $first) {throw 'Actual complete class extraction drift'}
$actual=$launch.Substring($first,$last-$first).Replace('public static class MixelOrdinaryTokenFixture {','public static class MixelOrdinaryTokenFixtureCompileOnly {')
$fragment=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/support-windows-desktop-identity-diagnostic.cs'))
$marker='  public static int StartStandardUser(string executable, string username, string password) {'
$position=$actual.IndexOf($marker);if($position -lt 0 -or $position -ne $actual.LastIndexOf($marker)) {throw 'Actual insertion drift'}
Add-Type -TypeDefinition $actual.Insert($position,$fragment+"`n")
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $ControlRoot 'mock.cs')))
foreach($variant in 0..15) {
  [MixelOrdinaryTokenFixture]::Reset($variant)
  $facts=[MixelOrdinaryTokenFixture]::ObserveOwnedDesktopPrimary($(if($variant -eq 13){999}else{403}),'private-owned-SID')
  $opened=$variant -cnotin @(4,13,14)
  $queries=if($variant -cin @(13,14)){0}else{1}
  if([MixelOrdinaryTokenFixture]::Queries -ne $queries -or [MixelOrdinaryTokenFixture]::Closes -ne [int]$opened -or
     [MixelOrdinaryTokenFixture]::Allocations -ne [int]$opened -or [MixelOrdinaryTokenFixture]::Frees -ne [int]$opened -or
     [MixelOrdinaryTokenFixture]::EffectiveThreadQueries -ne 0 -or $facts.tokenOpened -ne $opened -or $facts.tokenCloseAttempted -ne $opened) {throw 'Primary query ownership/allocation/close contract changed'}
  if(($variant -le 3 -and ($facts.status -cne 'available' -or $facts.ownedSidMatched -ne ($variant -ne 3) -or $facts.elevated -ne ($variant -eq 1) -or $facts.tokenSessionId -ne $(if($variant -eq 2){3}else{2}))) -or
     ($variant -eq 4 -and ($facts.operation -cne 'OpenProcessToken-TOKEN_QUERY' -or $facts.nativeError -ne 5)) -or
     ($variant -eq 5 -and ($facts.operation -cne 'TokenElevation' -or $facts.nativeError -ne 87)) -or
     ($variant -eq 6 -and ($facts.operation -cne 'TokenSessionId' -or $facts.nativeError -ne 6)) -or
     ($variant -cin @(7,8) -and $facts.status -cne 'returned-size') -or
     ($variant -cin @(9,10) -and $facts.failureType -cne 'System.InvalidOperationException') -or
     ($variant -cin @(11,12) -and ($facts.tokenClosed -ne $false -or $facts.tokenCloseError -ne $(if($variant -eq 12){0}else{6}))) -or
     ($variant -eq 13 -and $facts.status -cne 'unowned') -or
     ($variant -eq 14 -and ($facts.status -cne 'retained-pid-mismatch' -or $facts.nativeError -ne 6)) -or
     ($variant -eq 15 -and ($facts.operation -cne 'TokenElevation' -or $facts.nativeError -ne 0))) {throw 'Diagnostic original read-only facts/errors changed'}
  if(($facts | ConvertTo-Json -Compress).Contains('private-owned-SID')) {throw 'Raw owned SID leaked'}
}
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($pipeline,[ref]$tokens,[ref]$errors)
if($errors.Count) {throw 'Pipeline does not parse'}
$writer=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Write-OwnedDesktopIdentityDiagnostic'},$true)
$gate=$ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($guiElevated -or ($predicateChildSession=')},$true)
if($writer.Count -ne 1 -or $gate.Count -ne 1) {throw 'Exact diagnostic/predicate missing'}
. ([scriptblock]::Create($writer[0].Extent.Text))
$failureLiteral='Owned desktop control is not a genuine non-elevated process in the runner session.'
# Original condition/body bound to preserved committed49a1 source and job failure.
$original='if ([MixelOrdinaryTokenFixture]::Elevated($guiPid) -or $guiProcess.SessionId -ne (Get-Process -Id $PID).SessionId) { throw ''Owned desktop control is not a genuine non-elevated process in the runner session.'' }'
function Get-Process {param([int]$Id) if($Id -ne $PID){throw 'Wrong caller process subject'};$script:callerReads++;[pscustomobject]@{SessionId=2}}
$guiPid=403;$ownedSid='private-owned-SID';$mode='minimal-before'
$guiProcess=New-Object psobject
$guiProcess | Add-Member ScriptProperty SessionId {$script:childReads++;$script:childSession}
foreach($case in @(@($false,2,$false),@($false,3,$true),@($true,2,$true))) {
  [MixelOrdinaryTokenFixture]::PredicateElevated=$case[0];$script:childSession=$case[1]
  foreach($corrected in @($false,$true)) {
    [MixelOrdinaryTokenFixture]::Reset(0);[MixelOrdinaryTokenFixture]::ElevationCalls=0;$script:childReads=0;$script:callerReads=0
    $predicateChildSession=$null;$predicateCallerSession=$null;$message='';$captured=@()
    try {
      $captured=@(& {
        if($corrected) {$guiElevated=[MixelOrdinaryTokenFixture]::Elevated($guiPid);& ([scriptblock]::Create($gate[0].Extent.Text))}
        else {& ([scriptblock]::Create($original))}
      } 6>&1)
    } catch {$message=$_.Exception.Message}
    $diagnosticQueries=if($corrected -and $case[2]){1}else{0}
    if(($case[2] -and $message -cne $failureLiteral) -or (-not $case[2] -and $message -cne '') -or
       [MixelOrdinaryTokenFixture]::ElevationCalls -ne 1 -or $script:childReads -ne [int](-not $case[0]) -or $script:callerReads -ne [int](-not $case[0]) -or
       [MixelOrdinaryTokenFixture]::Queries -ne $diagnosticQueries -or [MixelOrdinaryTokenFixture]::Closes -ne $diagnosticQueries) {throw 'Original fatal gate/calls/short-circuit behavior changed, or diagnostic token query escaped its failure-only branch'}
    # Invoke the exact writer separately because a terminating throw discards
    # captured pipeline output. No native behavior is inferred from this mock.
    if($corrected -and $case[2]) {
      $line=@(Write-OwnedDesktopIdentityDiagnostic 403 $ownedSid $mode $case[0] $(if($case[0]){$null}else{$case[1]}) $(if($case[0]){$null}else{2}) 6>&1)
      if($line.Count -ne 1 -or -not $line[0].ToString().StartsWith('FIXTURE: fatal owned desktop identity observation; ')) {throw 'Structured diagnostic missing'}
      $json=$line[0].ToString().Substring('FIXTURE: fatal owned desktop identity observation; '.Length)
      $record=$json | ConvertFrom-Json
      if($record.ownedPid -ne 403 -or $record.predicateElevated -ne $case[0] -or $record.sessionPredicateEvaluated -ne (-not $case[0]) -or
         $record.primaryToken.ownedSidMatched -ne $true -or $record.primaryToken.tokenSessionId -ne 2 -or $json.Contains($ownedSid)) {throw 'Decisive facts, primary token subject or redaction changed'}
    }
  }
}
$script:childSession=3;[MixelOrdinaryTokenFixture]::PredicateElevated=$false
foreach($variant in 0..16) {
  [MixelOrdinaryTokenFixture]::Reset($variant);$guiElevated=[MixelOrdinaryTokenFixture]::Elevated($guiPid)
  $predicateChildSession=$null;$predicateCallerSession=$null;$message=''
  try { & ([scriptblock]::Create($gate[0].Extent.Text)) 6>&1 | Out-Null } catch {$message=$_.Exception.Message}
  if($message -cne $failureLiteral) {throw 'Read-only primary/status/cleanup observation changed original fatal failure'}
}
function Write-OwnedDesktopIdentityDiagnostic { throw 'Injected diagnostic emitter failure' }
$message='';try { & ([scriptblock]::Create($gate[0].Extent.Text)) 6>&1 | Out-Null } catch {$message=$_.Exception.Message}
if($message -cne $failureLiteral) {throw 'Diagnostic emitter failure replaced original fatal failure'}
function Write-Host { throw 'Injected output failure' }
$message='';try { & ([scriptblock]::Create($gate[0].Extent.Text)) 6>&1 | Out-Null } catch {$message=$_.Exception.Message}
if($message -cne $failureLiteral) {throw 'Diagnostic fallback output failure replaced original fatal failure'}
Remove-Item Function:Write-Host
Write-Host "PASS: exact original/corrected desktop predicate retains same fatal failures, native elevation call and session short-circuit; structured facts preserve actual evaluated operands and primary owned-SID/session without raw identity. 16 mocked primary API/size/constructor/dispose/close/error0/unowned controls pass; complete actual augmented class compiles. PowerShell=$($PSVersionTable.PSVersion); edition=$($PSVersionTable.PSEdition). No native/app qualification."
Write-Host 'PASS: all 17 primary/status observation scenarios and both diagnostic-emitter/output failures preserve the original fatal desktop failure, including failed read-only handle cleanup/error0; native process/ACL cleanup remains unchanged.'
'''

EXIT_SCRIPT = r'''
param([string]$Repository,[string]$ControlRoot,[string]$ExpectedEdition)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition) {throw 'Required PowerShell edition missing'}
$pipeline=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-windows-pipeline.ps1'))
$original=[IO.File]::ReadAllText((Join-Path $ControlRoot 'original.ps1'))
$ast=[Management.Automation.Language.Parser]::ParseInput($pipeline,[ref]$null,[ref]$null)
$helper=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Complete-OwnedNoncanonicalExitedDesktopControl'},$true))
$writer=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Write-OwnedDesktopIdentityDiagnostic'},$true))
$cleanup=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.TryStatementAst] -and $node.Finally -and $node.Finally.Extent.Text.Contains('$trialCleanup =')},$true))
if($helper.Count -ne 1 -or $writer.Count -ne 1 -or $cleanup.Count -ne 1) {throw 'Exact helper/diagnostic/trial-finally source extraction changed'}
. ([scriptblock]::Create($helper[0].Extent.Text))
. ([scriptblock]::Create($writer[0].Extent.Text))
Add-Type -TypeDefinition ([IO.File]::ReadAllText((Join-Path $ControlRoot 'exit-mock.cs')))
function Get-Process {
  [CmdletBinding()]param([int]$Id)
  if($Id -eq $PID) {return [pscustomobject]@{SessionId=$script:callerSession}}
  if($Id -ne 403) {throw 'Unexpected process lookup subject'}
  $script:lookups++
  if($script:stage -ceq 'foreign-error') {throw 'Unrelated process lookup failure'}
  if($script:stage -ceq 'lookup' -or [MixelOrdinaryTokenFixture]::CurrentStatus.StartsWith('exited:')) {
    [MixelOrdinaryTokenFixture]::CurrentStatus='exited:0x8007045a'
    throw [Management.Automation.ErrorRecord]::new([InvalidOperationException]::new('No process at lookup'),
      'NoProcessFoundForGivenId,Microsoft.PowerShell.Commands.GetProcessCommand',[Management.Automation.ErrorCategory]::ObjectNotFound,$Id)
  }
  $process=New-Object psobject
  $process | Add-Member ScriptProperty SessionId {
    if($script:stage -ceq 'property') {[MixelOrdinaryTokenFixture]::CurrentStatus='exited:0x8007045a';return $null}
    return $script:session
  }
  return $process
}
function Reset-Control([int]$variant=0) {
  [MixelOrdinaryTokenFixture]::Reset($variant)
  [MixelOrdinaryTokenFixture]::CurrentStatus='exited:0x8007045a'
  [MixelOrdinaryTokenFixture]::StatusCalls=0
  [MixelOrdinaryTokenFixture]::ChangeAfterPrimary=$false
  [MixelOrdinaryTokenFixture]::Stops=0;[MixelOrdinaryTokenFixture]::ObservationCloses=0
  [MixelOrdinaryTokenFixture]::Restores=0
  [MixelOrdinaryTokenFixture]::PredicateElevated=$false
  $script:stage='none';$script:session=2;$script:callerSession=2;$script:lookups=0
}
foreach($mode in @('minimal-before','minimal-after')) {
  Reset-Control
  $list=[Collections.Generic.List[object]]::new()
  $output=@(Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' $mode $list 6>&1)
  if($output[-1] -ne $true -or $list.Count -ne 1 -or $list[0].passed -ne $false -or
    $list[0].nativeStatus -cne 'exited:0x8007045a' -or [MixelOrdinaryTokenFixture]::Queries -ne 1 -or
    [MixelOrdinaryTokenFixture]::Closes -ne 1 -or [MixelOrdinaryTokenFixture]::StatusCalls -ne 2) {throw 'Exact verified owned noncanonical exit not retained as failed diagnostic'}
  $line=$output[0].ToString();$prefix='FIXTURE: noncanonical owned desktop exit observation; '
  if(-not $line.StartsWith($prefix)) {throw 'Raw failure observation missing'}
  $record=$line.Substring($prefix.Length) | ConvertFrom-Json
  if(-not $record.failedDiagnostic -or -not $record.identityVerified -or $record.canonicalQualified -or
    $record.nativeStatus -cne $list[0].nativeStatus -or $line.Contains('private-owned-SID')) {throw 'Failure/identity/qualification/redaction changed'}
}
foreach($mode in @('canonical','unrecognized')) {
  Reset-Control;$list=[Collections.Generic.List[object]]::new()
  if((Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' $mode $list) -ne $false -or
     [MixelOrdinaryTokenFixture]::Queries -ne 0 -or [MixelOrdinaryTokenFixture]::StatusCalls -ne 0 -or $list.Count) {throw 'Canonical or foreign mode queried/bypassed'}
}
Reset-Control;[MixelOrdinaryTokenFixture]::CurrentStatus='still-running';$list=[Collections.Generic.List[object]]::new()
if((Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' 'minimal-before' $list) -ne $false -or
  [MixelOrdinaryTokenFixture]::Queries -ne 0 -or $list.Count) {throw 'Live child bypassed its original identity gate'}
foreach($variant in 1..15) {
  Reset-Control $variant;$list=[Collections.Generic.List[object]]::new();$message=''
  try {Complete-OwnedNoncanonicalExitedDesktopControl $(if($variant -eq 13){999}else{403}) 'private-owned-SID' 'minimal-before' $list 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
  if(-not $message -or $list.Count -ne 0 -or [MixelOrdinaryTokenFixture]::Queries -gt 1) {throw 'Wrong/elevated/unowned/query/error0/failed-close identity accepted or retried'}
}
foreach($status in @('not-native-fixture-started','exited:0x0','exited:0x8007045A')) {
  Reset-Control;[MixelOrdinaryTokenFixture]::CurrentStatus=$status;$list=[Collections.Generic.List[object]]::new();$message=''
  try {Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' 'minimal-before' $list 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
  if(-not $message -or $list.Count -ne 0 -or [MixelOrdinaryTokenFixture]::Queries -ne 0) {throw 'Unverified retained status accepted'}
}
Reset-Control;[MixelOrdinaryTokenFixture]::ChangeAfterPrimary=$true;$list=[Collections.Generic.List[object]]::new();$message=''
try {Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' 'minimal-before' $list 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
if(-not $message -or $list.Count -ne 0 -or [MixelOrdinaryTokenFixture]::Queries -ne 1) {throw 'Changed retained status accepted/retried'}
Reset-Control 16;$list=[Collections.Generic.List[object]]::new();$message=''
try {Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' 'minimal-before' $list 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
if(-not $message -or $list.Count -ne 0 -or [MixelOrdinaryTokenFixture]::Queries -ne 0) {throw 'Failed retained-status query accepted/retried'}
Reset-Control;$script:callerSession=$null;$list=[Collections.Generic.List[object]]::new();$message=''
try {Complete-OwnedNoncanonicalExitedDesktopControl 403 'private-owned-SID' 'minimal-before' $list 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
if(-not $message -or $list.Count -ne 0 -or [MixelOrdinaryTokenFixture]::Queries -ne 1) {throw 'Unavailable caller session accepted/retried'}
function Trial-Block([string]$source) {
  $start=$source.IndexOf('      $guiPid = [MixelOrdinaryTokenFixture]::StartStandardUser($guiExecutable, $ownedUser, $ownedPassword)')
  $start=$source.IndexOf("`n",$start)+1
  $end=$source.IndexOf('      $deadline =',$start)
  if($start -le 0 -or $end -le $start) {throw 'Exact original trial ordering missing'}
  return $source.Substring($start,$end-$start)
}
$oldBlock=Trial-Block $original;$newBlock=Trial-Block $pipeline
$failedOriginal=0;$passedCorrected=0
foreach($timing in @('before','lookup','property')) {
 foreach($corrected in @($false,$true)) {
  Reset-Control;$script:stage=$timing
  if($timing -cne 'before') {[MixelOrdinaryTokenFixture]::CurrentStatus='still-running'}
  $mode='minimal-before';$canonical=$false;$guiPid=403;$ownedSid='private-owned-SID'
  $desktopControls=[Collections.Generic.List[object]]::new();$guiProcess=$null;$guiAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new()
  $trialFailure=$null;$baselineAcls='original';$script:Reached=$false;$message=''
  $block=if($corrected){$newBlock}else{$oldBlock}
  $wrapper='foreach($iteration in 0) { try {'+$block+'; $script:Reached=$true } finally '+$cleanup[0].Finally.Extent.Text+' }'
  try {& ([scriptblock]::Create($wrapper)) 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
  if([MixelOrdinaryTokenFixture]::Stops -ne 1 -or [MixelOrdinaryTokenFixture]::ObservationCloses -ne 1 -or
     [MixelOrdinaryTokenFixture]::Restores -ne 1 -or $script:Reached) {throw 'Actual original trial cleanup/order changed'}
  if($corrected) {
    if($message -or $desktopControls.Count -ne 1 -or $desktopControls[0].passed -ne $false) {throw ('Corrected verified exit did not continue after original cleanup: '+$message)}
    $passedCorrected++
  } else {
    if(-not $message -or $desktopControls.Count) {throw 'Original lifecycle ordering negative did not fail'}
    $failedOriginal++
  }
 }
}
foreach($case in @(@('canonical','property',$false,2),@('minimal-before','none',$true,2),@('minimal-before','none',$false,3),@('minimal-before','foreign-error',$false,2))) {
 Reset-Control;[MixelOrdinaryTokenFixture]::CurrentStatus='still-running';$mode=$case[0];$canonical=$mode -ceq 'canonical'
 $script:stage=$case[1];[MixelOrdinaryTokenFixture]::PredicateElevated=$case[2];$script:session=$case[3]
 $guiPid=403;$ownedSid='private-owned-SID';$desktopControls=[Collections.Generic.List[object]]::new()
 $guiAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new();$trialFailure=$null;$baselineAcls='original';$message=''
 $wrapper='foreach($iteration in 0) { try {'+$newBlock+'; throw ''Unexpected gate success'' } finally '+$cleanup[0].Finally.Extent.Text+' }'
 try {& ([scriptblock]::Create($wrapper)) 6>&1 | Out-Null} catch {$message=$_.Exception.Message}
 $expected=if($case[1] -ceq 'foreign-error'){'Unrelated process lookup failure'}else{'Owned desktop control is not a genuine non-elevated process in the runner session.'}
 if($message -cne $expected -or $desktopControls.Count -ne 0 -or
   [MixelOrdinaryTokenFixture]::Stops -ne 1 -or [MixelOrdinaryTokenFixture]::ObservationCloses -ne 1 -or [MixelOrdinaryTokenFixture]::Restores -ne 1) {throw 'Canonical/live elevated/wrong-session fatal or original cleanup changed'}
}
if($failedOriginal -ne 3 -or $passedCorrected -ne 3) {throw 'Historical ordering replay incomplete'}
Write-Host 'PASS: exact three original exit-timing failures pair with corrected failed diagnostics plus unchanged cleanup; canonical/live elevated/wrong-session gates remain fatal. 2 positive, 2 inactive, live, 15 identity failures, 3 malformed statuses, changed/failed status, unavailable caller and unrelated lookup controls preserve raw errors and no failed-query retry. Native APIs mocked only.'
'''


class DesktopIdentityControls(unittest.TestCase):
    def test_actual_fragment_and_original_fatal_predicate(self):
        fragment = (ROOT / "scripts/support-windows-desktop-identity-diagnostic.cs").read_text(encoding="utf-8")
        self.assertNotIn("WindowsIdentity.GetCurrent", fragment)
        self.assertNotIn("OpenThreadToken", fragment)
        with tempfile.TemporaryDirectory(prefix="mixel-desktop-primary-controls-") as temporary:
            root = Path(temporary)
            (root / "mock.cs").write_text(MOCK + fragment + "\n}", encoding="utf-8")
            script = root / "control.ps1"
            script.write_text(SCRIPT, encoding="utf-8-sig")
            for engine, edition in controls.ObservationControls.powershell_engines():
                result = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout, flush=True)


    def test_owned_noncanonical_exit_timing_and_original_cleanup(self):
        fragment = (ROOT / "scripts/support-windows-desktop-identity-diagnostic.cs").read_text(encoding="utf-8")
        # Keep the existing primary-token API mock intact; only its controlled
        # process lifecycle and owned cleanup are added for this ordering test.
        mock = MOCK.replace("public static bool PredicateElevated;", """public static bool PredicateElevated;
  public static string CurrentStatus="exited:0x8007045a";
  public static int StatusCalls,Stops,ObservationCloses,Restores;
  public static bool ChangeAfterPrimary;
  public static void StopOwnedStartedProcesses(){Stops++;}
  public static void CloseStartedObservations(){ObservationCloses++;}
  public static string DesktopAclHashes(){return "original";}
  public sealed class DesktopAccess:IDisposable {public void Dispose(){Restores++;}}
""")
        old_status = 'return "still-running";'
        self.assertEqual(mock.count(old_status), 1)
        mock = mock.replace(old_status, 'StatusCalls++;return ChangeAfterPrimary && StatusCalls>1 ? "still-running" : CurrentStatus;', 1)
        pipeline = (ROOT / "scripts/test-windows-pipeline.ps1").read_text(encoding="utf-8")
        # Exact inverse defines the pre-correction source without historical
        # Git availability in shallow Windows CI.
        helper_start = pipeline.index("function Complete-OwnedNoncanonicalExitedDesktopControl(")
        helper_end = pipeline.index("# Compile the exact native OS fixture", helper_start)
        original = pipeline[:helper_start] + pipeline[helper_end:]
        lookup = """      if (Complete-OwnedNoncanonicalExitedDesktopControl $guiPid $ownedSid $mode $desktopControls) { continue }
      try { $guiProcess = Get-Process -Id $guiPid }
      catch {
        if ($_.FullyQualifiedErrorId -ceq 'NoProcessFoundForGivenId,Microsoft.PowerShell.Commands.GetProcessCommand' -and
            (Complete-OwnedNoncanonicalExitedDesktopControl $guiPid $ownedSid $mode $desktopControls)) { continue }
        throw
      }
"""
        self.assertEqual(original.count(lookup), 1)
        original = original.replace(lookup, "      $guiProcess = Get-Process -Id $guiPid\n", 1)
        late = """        if (-not $guiElevated -and $null -eq $predicateChildSession -and
            (Complete-OwnedNoncanonicalExitedDesktopControl $guiPid $ownedSid $mode $desktopControls)) { continue }
"""
        self.assertEqual(original.count(late), 1)
        original = original.replace(late, "", 1)
        import hashlib
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), "159f90310d01c2f6efe26e556cd3b69d61d74a5b98050c84b4224ad9c1d73348")
        with tempfile.TemporaryDirectory(prefix="mixel-desktop-exit-ordering-") as temporary:
            root = Path(temporary)
            (root / "exit-mock.cs").write_bytes((mock + fragment + "\n}").encode())
            (root / "original.ps1").write_bytes(original.encode())
            script = root / "control.ps1"
            script.write_bytes(b"\xef\xbb\xbf" + EXIT_SCRIPT.encode())
            for engine, edition in controls.ObservationControls.powershell_engines():
                result = subprocess.run([engine, "-NoProfile", "-File", str(script), "-Repository", str(ROOT), "-ControlRoot", str(root), "-ExpectedEdition", edition], capture_output=True, text=True, timeout=40)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout, flush=True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
