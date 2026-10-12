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


if __name__ == "__main__":
    unittest.main(verbosity=2)
