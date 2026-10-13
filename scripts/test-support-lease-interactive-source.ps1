param([string]$MainPath=(Join-Path $PSScriptRoot 'test-support-lease-interactive-windows.ps1'),[string]$AdminPath=(Join-Path $PSScriptRoot 'test-support-lease-negative-windows.ps1'),[string]$StandardPath=(Join-Path $PSScriptRoot 'test-support-lease-standard-windows.ps1'),[string]$LaunchPath=(Join-Path $PSScriptRoot 'test-support-launch-windows.ps1'),[switch]$NativePipeControls)
$ErrorActionPreference='Stop'
$MainPath=[IO.Path]::GetFullPath($MainPath); $AdminPath=[IO.Path]::GetFullPath($AdminPath); $StandardPath=[IO.Path]::GetFullPath($StandardPath)
$candidate=$MainPath; $admin=$AdminPath; $native=$MainPath
$source=Get-Content -LiteralPath $MainPath -Raw -Encoding UTF8
$adminSource=Get-Content -LiteralPath $AdminPath -Raw -Encoding UTF8
$original=Get-Content -LiteralPath $StandardPath -Raw -Encoding UTF8
foreach($path in @($MainPath,$AdminPath)) {
  $tokens=$null; $errors=$null
  [void][Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
  if($errors.Count) { throw ($errors|Out-String) }
}
function Section([string]$Text,[string]$First,[string]$Last) {
  $start=$Text.IndexOf($First,[StringComparison]::Ordinal)
  if($start -lt 0 -or $start -ne $Text.LastIndexOf($First,[StringComparison]::Ordinal)) { throw 'Source extraction first boundary drifted.' }
  $end=$Text.IndexOf($Last,$start,[StringComparison]::Ordinal)
  if($end -le $start) { throw 'Source extraction last boundary drifted.' }
  return $Text.Substring($start,$end-$start)
}
$parser=Section $source 'function Assert-StandardLeaseSafeResult' '$parentProof='
$originalParser=Section $original 'function Assert-StandardLeaseSafeResult' 'function Read-StandardUnnamedEventResult'
if($parser -cne $originalParser) { throw 'Actual source six-field parser drifted.' }
. ([scriptblock]::Create($parser))
$core=Section $source "  `$absent = 'ownerFailed=false;" "  Write-AtomicJson (Join-Path `$fixtureRoot 'negative-finished.json')"
function Read-OriginalStandardTerminal([string]$Text) {
  $tokens=$null; $errors=$null
  $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
  if($errors.Count) { throw 'Original standard source does not parse.' }
  # The current-caller comparison has its own $absent variable. Select only
  # the original top-level terminal, never a function or nested observation.
  $marker="[void](Invoke-StandardLeaseControl probe `$absent 2 '' `$false `$false)"
  $terminals=@($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst] -and $_.Body.Extent.Text.Contains($marker)})
  if($terminals.Count -ne 1) { throw 'Original standard terminal is missing or duplicated.' }
  return $terminals[0].Body.Extent.Text
}
$originalCore=Read-OriginalStandardTerminal $original
foreach($changed in @($original.Replace("[void](Invoke-StandardLeaseControl probe `$absent 2 '' `$false `$false)",'changed original call'),($original+"`ntry { [void](Invoke-StandardLeaseControl probe `$absent 2 '' `$false `$false) } finally {}"))) {
  $refused=$false
  try { [void](Read-OriginalStandardTerminal $changed) } catch { $refused=$true }
  if(-not $refused) { throw 'Missing or duplicated original standard terminal was accepted.' }
}
$pattern='(?m)^\s*(?:\[void\]\(|\$ownerPid = )Invoke-StandardLeaseControl (?:probe|owner|failed-owner) [^\r\n]+'
$actualCalls=@([regex]::Matches($core,$pattern)|ForEach-Object{$_.Value.Trim()})
$originalCalls=@([regex]::Matches($originalCore,$pattern)|ForEach-Object{$_.Value.Trim()})
if($originalCalls.Count -ne 9 -or $originalCalls[8] -cne '[void](Invoke-StandardLeaseControl probe $beforeGrant.sourceStatus ([int]$beforeGrant.rawOpenError) $beforeGrant.attestation ([bool]$beforeGrant.requiresClick) ([bool]$beforeGrant.ownerFailed))') { throw 'Mandatory hosted original-descriptor restoration assertion changed.' }
if($actualCalls.Count -ne 8 -or ($actualCalls -join "`n") -cne ($originalCalls[0..7] -join "`n")) { throw 'Exact eight original grant-phase assertion calls changed.' }
$nativeStart="Add-Type @'"+"`n"
$nativeBlock=Section $source $nativeStart "`n'@`nfunction Assert-StandardLeaseSafeResult"
$nativeSource=$nativeBlock.Substring($nativeStart.Length)
foreach($forbidden in @('CreateProcessWith','LogonUser','Impersonate','OpenProcess(','AdjustTokenPrivileges','SetTokenInformation','SetUserObjectSecurity','NtQuery','Debugger','CREATE_SUSPENDED')) {
  if(($nativeSource.IndexOf($forbidden,[StringComparison]::OrdinalIgnoreCase) -ge 0)) { throw ('Forbidden launch/context mutation in local adapter: '+$forbidden) }
}
if(-not $nativeSource.Contains('CreateProcessW(executable,new StringBuilder("\""+executable+"\""),IntPtr.Zero,IntPtr.Zero,false,0,IntPtr.Zero,Path.GetDirectoryName(executable),ref startup,out process)')) { throw 'Normal launch arguments changed.' }
if($source.Contains('GITHUB_ACTIONS') -or $source.Contains('New-LocalUser') -or $source.Contains('Set-Acl') -or $source.Contains('Remove-LocalUser')) { throw 'Local standard runner changes hosted environment or accounts.' }
Add-Type -TypeDefinition $nativeSource
Write-Host 'PASS: both scripts parse; actual source parser/eight strict assertion calls and augmented C# compile; ordinary CreateProcessW flags0 only, no context mutation.'

$classification=[MixelInteractiveLeaseProcess].GetMethod('AssertStandardClassification',[Reflection.BindingFlags]'Static,NonPublic')
$positive=[object[]]@([uint32]1,[uint32]0,$false,'enabled','enabled',$true)
[void]$classification.Invoke($null,$positive)
$invalid=@()
foreach($mutation in @(@(0,[uint32]2),@(1,[uint32]1),@(2,$true),@(3,'absent'),@(3,'deny-only'),@(3,'disabled'),@(4,'absent'),@(4,'deny-only'),@(4,'disabled'),@(5,$false))) {
  $values=[object[]]$positive.Clone(); $values[$mutation[0]]=$mutation[1]; $invalid+=,$values
}
if($invalid.Count -ne 10) { throw 'Classification negative count changed.' }
foreach($values in $invalid) {
  $rejected=$false
  try { [void]$classification.Invoke($null,$values) } catch {
    if($_.Exception.InnerException.Message -cne 'Current interactive account is not a verified genuine standard primary token') { throw }
    $rejected=$true
  }
  if(-not $rejected) { throw 'Nonstandard token classification was accepted.' }
}
Write-Host 'PASS: actual source standard-token classifier accepts one genuine-standard shape and rejects ten primary/elevation/admin-SID/deny-only/interactive/authentication mutations.'

$groups=[MixelInteractiveLeaseProcess].GetMethod('ReadGroups',[Reflection.BindingFlags]'Static,NonPublic')
$buffer=[Runtime.InteropServices.Marshal]::AllocHGlobal(64)
try {
  for($i=0;$i -lt 64;$i++) { [Runtime.InteropServices.Marshal]::WriteByte($buffer,$i,0) }
  $empty=$groups.Invoke($null,[object[]]@($buffer,8))
  if($empty.Count -ne 0) { throw 'Empty bounded group parser changed.' }
  foreach($case in @(@(0,0),@(0,7),@(4097,64),@(4,64),@(1,8))) {
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer,[int]$case[0]); $rejected=$false
    try { [void]$groups.Invoke($null,[object[]]@($buffer,[int]$case[1])) } catch {
      if($_.Exception.InnerException.Message -cnotmatch '\AInteractive group (?:header|count) bounds failed\z') { throw }
      $rejected=$true
    }
    if(-not $rejected) { throw 'Malformed group header/count accepted.' }
  }
  foreach($offset in @(-1,0,63,64,4096)) {
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer,1)
    [Runtime.InteropServices.Marshal]::WriteIntPtr($buffer,8,[IntPtr]::Add($buffer,$offset))
    [Runtime.InteropServices.Marshal]::WriteInt32($buffer,16,4)
    $rejected=$false
    try { [void]$groups.Invoke($null,[object[]]@($buffer,64)) } catch {
      if($_.Exception.InnerException.Message -cnotin @('Read-only token SID header bounds failed','Interactive group SID overlaps its header/table')) { throw }
      $rejected=$true
    }
    if(-not $rejected) { throw 'Malformed group SID pointer/header accepted.' }
  }
} finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($buffer) }
Write-Host 'PASS: actual source group parser accepts bounded empty input and rejects ten short/count/pointer/header inputs before SID conversion or native APIs.'

$valid=[pscustomobject]@{pid=123;sourceStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';rawOpenError=2;attestation='';requiresClick=$false;ownerFailed=$false}
Assert-StandardLeaseSafeResult $valid 123
$records=[Collections.Generic.List[object]]::new()
foreach($mutation in @(
  @('pid',0),@('pid',124),@('pid','123'),@('pid',123.0),
  @('rawOpenError',-1),@('rawOpenError',4294967296L),@('rawOpenError','2'),@('rawOpenError',2.5),
  @('attestation','ready'),@('requiresClick','false'),@('ownerFailed','false'),
  @('sourceStatus','ownerFailed=false;ownerError=6;kernel=absent;probeError=none;memoryActive=false'),
  @('sourceStatus','ownerFailed=false;ownerError=none;kernel=absent;probeError=5;memoryActive=false'),
  @('sourceStatus','ownerFailed=false;ownerError=none;kernel=unavailable;probeError=2147483648;memoryActive=false')
)) {
  $record=$valid|ConvertTo-Json -Compress|ConvertFrom-Json; $record.($mutation[0])=$mutation[1]; $records.Add($record)
}
$extra=$valid|ConvertTo-Json -Compress|ConvertFrom-Json; $extra|Add-Member noteproperty extra $true; $records.Add($extra)
$missing=$valid|ConvertTo-Json -Compress|ConvertFrom-Json; $missing.PSObject.Properties.Remove('pid'); $records.Add($missing)
if($records.Count -ne 16) { throw 'Schema negative control count changed.' }
foreach($record in $records) {
  $rejected=$false
  try { Assert-StandardLeaseSafeResult $record 123 } catch { $rejected=$true }
  if(-not $rejected) { throw 'Malformed six-field native result accepted.' }
}
Write-Host 'PASS: exact current six-field native result parser accepts canonical absence and rejects sixteen PID/type/width/pair/schema mutations.'

Add-Type @'
public static class MixelInteractiveSourceFlowMock {
 public static int Stops,Closes;
 public static void StopOwnedStartedProcesses(){Stops++;}
 public static string StartedStatus(int pid){if(pid!=102)throw new System.Exception("Owner PID flow drifted");return "exited:0x00000001";}
 public static void CloseStartedObservations(){Closes++;}
}
'@
$script:flowCalls=[Collections.Generic.List[string]]::new()
$script:requests=[Collections.Generic.List[string]]::new(); $script:released=0; $script:completed=0; $script:rejectFirst=$false
$script:acceptanceMessages=[Collections.Generic.List[string]]::new()
. ([scriptblock]::Create((Section $source 'function Get-PortableLeaseAdminInstruction' 'function Assert-NegativeReady')))
$fixtureRoot=Join-Path ([IO.Path]::GetTempPath()) "owned QA's ü !"; $bundle=[pscustomobject]@{Root=(Join-Path ([IO.Path]::GetTempPath()) "bundle QA's ü !")}; $ExpectedManifestSha256='a'*64
$instruction=Get-PortableLeaseAdminInstruction $fixtureRoot $bundle.Root $ExpectedManifestSha256
$command=$instruction.Substring($instruction.IndexOf("& '",[StringComparison]::Ordinal))
if(-not $instruction.Contains('administrator/SYSTEM-protected directory') -or -not $instruction.Contains('verify the trusted manifest SHA there') -or -not $instruction.Contains('Replace BOTH displayed bundle/script paths')) { throw 'Elevated helper instruction omits the protected-code prerequisite.' }
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($command,[ref]$tokens,[ref]$errors)
$values=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst]},$true)|ForEach-Object{$_.Value})
$expected=@((Join-Path $bundle.Root 'run-admin-negative-lease-qa.ps1'),$fixtureRoot,$bundle.Root,$ExpectedManifestSha256)
if($errors.Count -or $values.Count -ne 4 -or ($values -join "`0") -cne ($expected -join "`0")) { throw 'Actual admin instruction does not round-trip spaced Unicode/apostrophe paths as four exact string literals.' }
if(-not $command.EndsWith((' -StandardParentPid '+$PID),[StringComparison]::Ordinal)) { throw 'Admin pipe instruction is not bound to the exact standard parent PID.' }
Write-Host 'PASS: actual source admin instruction quotes verified script and spaced Unicode/apostrophe workspace/bundle paths after standard owner-release qualification.'
function Invoke-StandardLeaseControl($Mode,$Status,$ErrorCode,$Proof,$Click,$OwnerFailed,[switch]$KeepAlive) {
  if($script:rejectFirst) { throw 'Mandatory actual native error5 remains unavailable.' }
  $script:flowCalls.Add("$Mode|$Status|$ErrorCode|$Proof|$Click|$OwnerFailed|$KeepAlive")
  return 100+$script:flowCalls.Count
}
function Request-NegativeFixture($Mode) { $script:requests.Add($Mode) }
function Complete-NegativeFixture { $script:released++ }
function Complete-NegativeSession { $script:completed++ }
function Capture-SourceAcceptanceMessage($Message) { if($Message.StartsWith('LOCAL QA:') -and ($script:flowCalls.Count -ne 4 -or $script:requests.Count -ne 0)) { throw 'Admin setup instruction preceded confirmed owner release.' }; $script:acceptanceMessages.Add($Message) }
$mockCore=$core.Replace('[MixelInteractiveLeaseProcess]','[MixelInteractiveSourceFlowMock]').Replace('Write-Host','Capture-SourceAcceptanceMessage')
. ([scriptblock]::Create($mockCore))
if($flowCalls.Count -ne 8 -or ($requests -join ',') -cne 'denied,wrong-object' -or $released -ne 2 -or $completed -ne 1 -or $acceptanceMessages.Count -ne 7 -or [MixelInteractiveSourceFlowMock]::Stops -ne 1 -or [MixelInteractiveSourceFlowMock]::Closes -ne 1) { throw 'Exact core source flow changed.' }
if(($flowCalls|Where-Object{$_ -cmatch '\|5\|guard-unavailable\|True\|False\|False\z'}).Count -ne 1 -or ($flowCalls|Where-Object{$_ -cmatch '\|6\|guard-unavailable\|True\|(?:True|False)\|False\z'}).Count -ne 2) { throw 'Fail-closed error5/error6 assertions changed.' }
$script:rejectFirst=$true; $before=$flowCalls.Count; $rejected=$false
try { . ([scriptblock]::Create($mockCore)) } catch { if($_.Exception.Message -cne 'Mandatory actual native error5 remains unavailable.') { throw }; $rejected=$true }
if(-not $rejected -or $flowCalls.Count -ne $before -or $requests.Count -ne 2 -or $completed -ne 1 -or $acceptanceMessages.Count -ne 7) { throw 'Failed initial baseline progressed or printed acceptance.' }
Write-Host 'PASS: exact eight terminal source controls retain error2/owner/release/error5/error6 expectations; failed mandatory first probe cannot progress to owner or admin setup.'
$ownership=Section $nativeSource '  static void StopOwnedHandle(' "`n}`n"
$ownership=$ownership.Replace('Marshal.GetLastWin32Error()','LastError')
$start=Section $nativeSource '  public static int StartOrdinary(' '  public static string AssertChild('
$start=$start.Replace('Marshal.GetLastWin32Error()','LastError')
$tokenClose=Section $nativeSource '  static void CloseQueriedToken(' '  static Dictionary<string,string> ReadGroups('
$tokenClose=$tokenClose.Replace('Marshal.GetLastWin32Error()','LastError')
$ownershipMock=@'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class MixelInteractiveOwnershipSourceMock {
 struct StartupInfo {public int cb;}
 struct ProcessInfo {public IntPtr process,thread;public uint pid;}
 static readonly Dictionary<int,IntPtr> StartedProcesses=new Dictionary<int,IntPtr>();
 static readonly Dictionary<int,IntPtr> StartedThreads=new Dictionary<int,IntPtr>();
 static readonly List<IntPtr> PendingProcesses=new List<IntPtr>();
 static readonly List<IntPtr> PendingThreads=new List<IntPtr>();
 static readonly List<IntPtr> PendingTokens=new List<IntPtr>();
 static readonly Dictionary<long,int> Closed=new Dictionary<long,int>();
 static readonly HashSet<long> Exited=new HashSet<long>(),WaitFailure=new HashSet<long>(),TerminateFailure=new HashSet<long>(),CloseFailure=new HashSet<long>();
 static readonly List<long> Terminated=new List<long>();
 static int LastError=6,Kind;
 static void AssertParent(){}
 static uint GetProcessId(IntPtr process){return Kind==8?99U:1U;}
 static bool CreateProcessW(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string dir,ref StartupInfo si,out ProcessInfo pi){
  if(command.ToString()!="\""+app+"\"" || pa!=IntPtr.Zero || ta!=IntPtr.Zero || inherit || flags!=0 || env!=IntPtr.Zero || dir!=Path.GetDirectoryName(app))throw new Exception("CreateProcess arguments changed");
  pi=new ProcessInfo{process=new IntPtr(1),thread=new IntPtr(10),pid=Kind==9?0U:1U};return true;
 }
 static uint WaitForSingleObject(IntPtr handle,uint timeout){return WaitFailure.Contains(handle.ToInt64())?0xffffffffU:Exited.Contains(handle.ToInt64())?0U:0x102U;}
 static bool TerminateProcess(IntPtr handle,uint code){long key=handle.ToInt64();Terminated.Add(key);if(code!=1)throw new Exception("Termination code changed");if(!TerminateFailure.Contains(key) || Kind==3)Exited.Add(key);return !TerminateFailure.Contains(key);}
 static bool CloseHandle(IntPtr handle){long key=handle.ToInt64();int count;Closed.TryGetValue(key,out count);Closed[key]=count+1;return !CloseFailure.Contains(key);}
__EXACT_TOKEN_CLOSE__
__EXACT_START__
__EXACT_OWNERSHIP_BODY__
 static void Reset(int kind){Kind=kind;StartedProcesses.Clear();StartedThreads.Clear();PendingProcesses.Clear();PendingThreads.Clear();PendingTokens.Clear();Closed.Clear();Exited.Clear();WaitFailure.Clear();TerminateFailure.Clear();CloseFailure.Clear();Terminated.Clear();LastError=6;}
 static void ExpectFailure(Action action){bool failed=false;try{action();}catch(AggregateException){failed=true;}catch(InvalidOperationException){failed=true;}catch(Win32Exception){failed=true;}if(!failed)throw new Exception("Owned failure was accepted");}
 public static void Run(int kind,string path){
  Reset(kind);
  if(kind<=6){
   StartedProcesses.Add(1,new IntPtr(1));
   if(kind==1 || kind==6)Exited.Add(1);
   if(kind==3 || kind==4){LastError=5;TerminateFailure.Add(1);}
   if(kind==5){WaitFailure.Add(1);StartedProcesses.Add(2,new IntPtr(2));}
   if(kind==6){StartedProcesses.Add(2,new IntPtr(2));Exited.Add(2);CloseFailure.Add(1);}
   if(kind<=3){StopOwnedStartedProcesses();CloseStartedObservations();if(StartedProcesses.Count!=0 || Closed[1]!=1 || Terminated.Count!=(kind==1?0:1))throw new Exception("Successful cleanup changed");return;}
   if(kind==4 || kind==5){ExpectFailure(StopOwnedStartedProcesses);ExpectFailure(CloseStartedObservations);if(StartedProcesses.Count!=1 || !StartedProcesses.ContainsKey(1) || Closed.ContainsKey(1))throw new Exception("Unconfirmed process was released");if(kind==5 && (!Terminated.Contains(2) || Closed[2]!=1))throw new Exception("Later owned child was not stopped and closed after first wait failure");return;}
   StopOwnedStartedProcesses();ExpectFailure(CloseStartedObservations);
   if(StartedProcesses.Count!=1 || !StartedProcesses.ContainsKey(1) || Closed[1]!=1 || Closed[2]!=1)throw new Exception("Partial close erased failed handle or abandoned later handle");
   CloseFailure.Clear();CloseStartedObservations();if(StartedProcesses.Count!=0 || Closed[1]!=2 || Closed[2]!=1)throw new Exception("Retry closed a previously successful handle");return;
  }
  if(kind>=7 && kind<=9){
   if(kind==7)CloseFailure.Add(10);
   ExpectFailure(()=>StartOrdinary(path));
   if(kind==7 && (StartedProcesses.Count!=1 || StartedThreads.Count!=1 || PendingProcesses.Count!=0 || PendingThreads.Count!=0))throw new Exception("Thread-close failure lost returned ownership or accepted PID");
   if(kind!=7 && (PendingProcesses.Count!=1 || PendingThreads.Count!=1 || StartedProcesses.Count!=0))throw new Exception("Attribution failure lost successful returned handles");
   CloseFailure.Clear();StopOwnedStartedProcesses();CloseStartedObservations();
   if(StartedProcesses.Count+StartedThreads.Count+PendingProcesses.Count+PendingThreads.Count!=0 || Closed[1]!=1 || Closed[10]!=(kind==7?2:1))throw new Exception("Launch failure cleanup did not release exactly retained handles");return;
  }
  CloseFailure.Add(20);ExpectFailure(()=>CloseQueriedToken(new IntPtr(20)));
  if(PendingTokens.Count!=1 || Closed[20]!=1)throw new Exception("Failed token close was not retained");
  CloseFailure.Clear();CloseStartedObservations();if(PendingTokens.Count!=0 || Closed[20]!=2)throw new Exception("Retained query-token cleanup failed");
 }
}
'@.Replace('__EXACT_TOKEN_CLOSE__',$tokenClose).Replace('__EXACT_START__',$start).Replace('__EXACT_OWNERSHIP_BODY__',$ownership)
Add-Type -TypeDefinition $ownershipMock
foreach($case in 1..10) { [MixelInteractiveOwnershipSourceMock]::Run($case,$native) }
$cleanupMain=Section $source "  try { [MixelInteractiveLeaseProcess]::StopOwnedStartedProcesses() } catch" "  # Evidence remains"
if($cleanupMain -notmatch 'StopOwnedStartedProcesses\(\) \} catch[^\n]+\n  try \{ \[MixelInteractiveLeaseProcess\]::CloseStartedObservations\(\) \} catch') { throw 'Main cleanup does not independently attempt stop and close.' }
if(-not $source.Contains("try {`n  `$parentProof=[MixelInteractiveLeaseProcess]::AssertParent()`n")) { throw 'Parent assertion does not enter bounded cleanup on failure.' }
if(-not $adminSource.Contains('Receive-NegativeJson 60000') -or -not $adminSource.Contains('AddSeconds(240)') -or 60 -le 2*(10+10)+10) { throw 'Two-child negative fixture release timing is not bounded consistently.' }
foreach($text in @($source,$adminSource)) {
  if($text.Contains('$IsWindows') -or $text.Contains('PowerShell7') -or $text.Contains('$RepoRoot') -or $text.Contains('python ') -or -not $text.Contains('[PlatformID]::Win32NT')) { throw 'PS5/prebuilt-only publication/platform boundary changed.' }
}
foreach($forbidden in @('[IO.File]::Write','[IO.File]::Replace','[IO.File]::Move','Join-Path $Workspace','Get-Item -LiteralPath $Workspace','ReadAllText($release)','ReadAllBytes($Path)')) {
  # Bundle validation reads the separately SHA-bound package; the admin phase
  # must never read or write the standard-user-owned workspace by pathname.
  $adminPhase=$adminSource.Substring($adminSource.IndexOf('$Workspace=[IO.Path]::GetFullPath', [StringComparison]::Ordinal))
  if($adminPhase.Contains($forbidden)) { throw 'Elevated helper accesses user-controlled workspace files.' }
}
$channelStart='public sealed class MixelPortableLeaseChannel : IDisposable {'
$channel=Section $source $channelStart "`n'@"
$adminChannel=Section $adminSource $channelStart "`n'@"
if($channel -cne $adminChannel -or -not $channel.Contains('CreateNamedPipeW(@"\\.\pipe\"+name,0x40080003,8,1,4096,4096,0,ref attributes)') -or -not $channel.Contains('actual!=expectedServerPid') -or -not $channel.Contains('GetNamedPipeClientProcessId(server.SafePipeHandle') -or -not $channel.Contains('security.SetAccessRuleProtection(true,false)')) { throw 'Private first-instance/local-only/PID-bound pipe contract changed.' }
$adminBlock=Section $adminSource $nativeStart "`n'@`nfunction Receive-NegativeJson"
$adminNative=$adminBlock.Substring($nativeStart.Length).Replace('MixelPortableLeaseChannel','MixelPortableLeaseAdminNativeChannel')
Add-Type -TypeDefinition $adminNative
if(-not $adminSource.Contains('finally { if($channel) { $channel.Dispose() } }') -or -not $source.Contains("try { if(`$negativeChannel) { `$negativeChannel.Dispose() } } catch")) { throw 'Either pipe peer loses unconditional cleanup.' }
foreach($forbidden in @('WorldSid','AuthenticatedUserSid','Impersonate','SetTokenInformation','WriteAll','Get-Item','ReadAll')) { if($channel.Contains($forbidden)) { throw 'Pipe bridge widens caller/file/token authority.' } }
$frame=[MixelPortableLeaseChannel]::Frame('valid metadata')
if([MixelPortableLeaseChannel]::Decode($frame[0..3],$frame[4..($frame.Length-1)]) -cne 'valid metadata') { throw 'Actual bounded UTF8 frame round-trip failed.' }
$maximum=[MixelPortableLeaseChannel]::Frame('a'*4096)
if($maximum.Length -ne 4100) { throw 'Maximum bounded frame changed.' }
foreach($invalid in @('',('a'*4097),([string][char]0xd800))) {
  $refused=$false; try { [void][MixelPortableLeaseChannel]::Frame($invalid) } catch { $refused=$true }
  if(-not $refused) { throw 'Invalid or oversized outbound frame accepted.' }
}
foreach($invalid in @(@([byte[]](0,0,0,0),[byte[]]@()),@([BitConverter]::GetBytes(2),[byte[]]@(65)),@([BitConverter]::GetBytes(1),[byte[]]@(255)),@([byte[]]@(1),[byte[]]@(65)),@([BitConverter]::GetBytes(4097),[byte[]](@(65)*4097)))) {
  $refused=$false; try { [void][MixelPortableLeaseChannel]::Decode($invalid[0],$invalid[1]) } catch { $refused=$true }
  if(-not $refused) { throw 'Malformed inbound frame accepted.' }
}
. ([scriptblock]::Create((Section $source 'function Assert-NegativeReady' 'function Invoke-StandardLeaseControl')))
. ([scriptblock]::Create((Section $adminSource 'function Assert-NegativeRequest' '$seen=[Collections.Generic.HashSet[string]]::new();')))
$negativeChannel=[pscustomobject]@{PeerPid=123}
$eventName='Global\Mixel-Remote-Standard-Lease-'+('b'*32); $requestId='c'*32
$bundle=[pscustomobject]@{Manifest=[pscustomobject]@{eventName=$eventName}}
$ready=[pscustomobject]@{request=$requestId;mode='denied';eventName=$eventName;pid=123;created=$true}
Assert-NegativeReady $ready $requestId 'denied' $eventName
Assert-NegativeRequest ([pscustomobject]@{request=$requestId;mode='denied';eventName=$eventName})
foreach($change in @(@('pid',124),@('pid','123'),@('created',$false),@('request',('d'*32)),@('mode','wrong-object'),@('eventName','Global\unrelated'))) {
  $packet=$ready|ConvertTo-Json -Compress|ConvertFrom-Json; $packet.($change[0])=$change[1]
  $refused=$false; try { Assert-NegativeReady $packet $requestId 'denied' $eventName } catch { $refused=$true }
  if(-not $refused) { throw 'Wrong peer/request/mode/event or creation packet accepted.' }
}
foreach($change in @(@('request','invalid'),@('mode','other'),@('eventName','Global\unrelated'))) {
  $packet=[pscustomobject]@{request=$requestId;mode='denied';eventName=$eventName}; $packet.($change[0])=$change[1]
  $refused=$false; try { Assert-NegativeRequest $packet } catch { $refused=$true }
  if(-not $refused) { throw 'Noncanonical native-object request accepted.' }
}
Write-Host 'PASS: both exact server/client C# blocks compile; nine actual packet PID/request/mode/event/creation mutations rejected before object or readiness claims; both peers retain cleanup.'
Write-Host 'PASS: exact shared pipe bridge compiles; protected first-instance remote-refusing server and both kernel PID checks retained; actual UTF8 framing rejects empty, oversized, invalid, mismatched and short packets; admin phase makes zero workspace file calls.'
Write-Host 'PASS: exact native ownership source passes ten controls, including later-child cleanup after first wait failure, partial-close retries, successful creation/thread-close refusal, attribution failures and retained query-token close failure; main stop/close are independent.'
# Bind the ordinary hosted fixture's ownership methods to pure API providers.
# No P/Invoke declaration or native API is included in this mock assembly.
$normal=Get-Content -LiteralPath ([IO.Path]::GetFullPath($LaunchPath)) -Raw -Encoding UTF8
$normalStart=(Section $normal '  static readonly System.Collections.Generic.Dictionary<int, IntPtr> StartedProcesses' '  public static System.Collections.Generic.Dictionary<string, string> SuspendedOwnedIdentity') + (Section $normal '  static int Started(ProcessInfo process, bool suspended = false)' '  public static bool ActualOwnedDebuggerAttached')
$normalResume=Section $normal '  public static int ResumeOwnedPrimaryThread' '  static void StopOwnedProcessHandle'
$normalStop=Section $normal '  static void StopOwnedProcessHandle' '  public static string StartedStatus'
$normalClose=Section $normal '  public static void CloseStartedObservations()' '  // The MiscInfo stream'
if(-not $normal.Contains('[DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr handle);') -or
   -not $normal.Contains('[DllImport("kernel32.dll", SetLastError = true)] static extern uint GetProcessId(IntPtr process);')) { throw 'Exact owned handle API last-error/identity declarations changed.' }
$normalMock=@'
using System;
using System.ComponentModel;
using System.Collections.Generic;
public static class MixelNormalOwnershipSourceMock {
 struct ProcessInfo { public IntPtr process,thread; public uint pid,tid; }
 static HashSet<long> Exited=new HashSet<long>(),WaitFailure=new HashSet<long>(),TerminateFailure=new HashSet<long>(),CloseFailure=new HashSet<long>();
 static Dictionary<long,int> Closed=new Dictionary<long,int>();
 static Dictionary<long,uint> Identities=new Dictionary<long,uint>();
 static HashSet<long> Terminated=new HashSet<long>();
 static int Kind,LastError;
 static class Marshal { public static int GetLastWin32Error(){return LastError;} }
 static uint GetProcessId(IntPtr handle){uint pid;return Identities.TryGetValue(handle.ToInt64(),out pid)?pid:0;}
 static uint ResumeThread(IntPtr thread){return Kind==15?1U:Kind==17?2U:0U;}
 static uint WaitForSingleObject(IntPtr handle,uint timeout){long key=handle.ToInt64();if(WaitFailure.Contains(key))return 0xffffffff;if(Exited.Contains(key))return 0;return 0x102;}
 static bool TerminateProcess(IntPtr handle,uint code){long key=handle.ToInt64();Terminated.Add(key);if(TerminateFailure.Contains(key)){if(Kind==3)Exited.Add(key);return false;}Exited.Add(key);return true;}
 static bool CloseHandle(IntPtr handle){long key=handle.ToInt64();int count;Closed.TryGetValue(key,out count);Closed[key]=count+1;return !CloseFailure.Contains(key);}
__EXACT_START__
__EXACT_RESUME__
__EXACT_STOP__
__EXACT_CLOSE__
 static void Reset(int kind){Kind=kind;LastError=6;StartedProcesses.Clear();SuspendedThreads.Clear();PendingProcesses.Clear();PendingThreads.Clear();Exited.Clear();WaitFailure.Clear();TerminateFailure.Clear();CloseFailure.Clear();Closed.Clear();Identities.Clear();Terminated.Clear();}
 static void ExpectFailure(Action action){bool failed=false;try{action();}catch(AggregateException){failed=true;}catch(InvalidOperationException){failed=true;}catch(Win32Exception){failed=true;}catch(OverflowException){failed=true;}if(!failed)throw new Exception("Owned fixture failure was accepted");}
 static void Empty(){if(StartedProcesses.Count+SuspendedThreads.Count+PendingProcesses.Count+PendingThreads.Count!=0)throw new Exception("Unresolved handle ownership remains");}
 public static void Run(int kind){
  Reset(kind);
  if(kind<=6){
   StartedProcesses.Add(1,new IntPtr(1));
   if(kind==1||kind==6)Exited.Add(1);
   if(kind==3||kind==4){LastError=5;TerminateFailure.Add(1);}
   if(kind==5){WaitFailure.Add(1);StartedProcesses.Add(2,new IntPtr(2));}
   if(kind==6){StartedProcesses.Add(2,new IntPtr(2));Exited.Add(2);CloseFailure.Add(1);}
   if(kind<=3){StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||Terminated.Count!=(kind==1?0:1))throw new Exception("Positive stop/close behavior changed");return;}
   if(kind==4||kind==5){ExpectFailure(StopOwnedStartedProcesses);ExpectFailure(CloseStartedObservations);if(StartedProcesses.Count!=1||!StartedProcesses.ContainsKey(1)||Closed.ContainsKey(1))throw new Exception("Unconfirmed child ownership was discarded");if(kind==5&&(!Terminated.Contains(2)||Closed[2]!=1))throw new Exception("Later child cleanup was skipped");WaitFailure.Clear();TerminateFailure.Clear();StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||(kind==5&&Closed[2]!=1))throw new Exception("Successful close repeated during retry");return;}
   StopOwnedStartedProcesses();ExpectFailure(CloseStartedObservations);if(StartedProcesses.Count!=1||!StartedProcesses.ContainsKey(1)||Closed[1]!=1||Closed[2]!=1)throw new Exception("Partial close lost failure or skipped later handle");CloseFailure.Clear();CloseStartedObservations();Empty();if(Closed[1]!=2||Closed[2]!=1)throw new Exception("Retry repeated a successful close");return;
  }
  ProcessInfo info=new ProcessInfo{process=new IntPtr(1),thread=new IntPtr(10),pid=101,tid=11};Identities[1]=101;
  if(kind==7){CloseFailure.Add(10);ExpectFailure(()=>Started(info));if(StartedProcesses.Count!=1||PendingThreads.Count!=1||PendingProcesses.Count!=0)throw new Exception("Failed thread close accepted PID or lost ownership");CloseFailure.Clear();StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||Closed[10]!=2)throw new Exception("Thread-close failure cleanup differs");return;}
  if(kind==8)info.pid=0;
  if(kind==9)Identities[1]=102;
  if(kind==10){info.pid=uint.MaxValue;Identities[1]=uint.MaxValue;}
  if(kind>=8&&kind<=10){ExpectFailure(()=>Started(info));if(PendingProcesses.Count!=1||PendingThreads.Count!=1||StartedProcesses.Count!=0)throw new Exception("Attribution failure lost owned return handles");StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||Closed[10]!=1)throw new Exception("Attribution failure cleanup differs");return;}
  if(kind==11){StartedProcesses.Add(101,new IntPtr(2));ExpectFailure(()=>Started(info));if(StartedProcesses[101]!=new IntPtr(2)||PendingProcesses.Count!=1||PendingThreads.Count!=1)throw new Exception("PID collision changed existing owner");StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||Closed[2]!=1||Closed[10]!=1)throw new Exception("Collision cleanup lost a returned handle");return;}
  if(kind==12){int pid=Started(info,true);if(pid!=101||SuspendedThreads.Count!=1||Closed.Count!=0)throw new Exception("Held thread ownership changed");StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||Closed[10]!=1)throw new Exception("Held thread cleanup differs");return;}
  if(kind==13){info.process=IntPtr.Zero;ExpectFailure(()=>Started(info));if(PendingProcesses.Count!=0||PendingThreads.Count!=1)throw new Exception("Partial creation ownership was invented or lost");StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed.Count!=1||Closed[10]!=1)throw new Exception("Partial return close differs");return;}
  if(kind==14){PendingProcesses.Add(new IntPtr(1));PendingProcesses.Add(new IntPtr(2));PendingThreads.Add(new IntPtr(10));Exited.Add(1);Exited.Add(2);CloseFailure.Add(1);CloseFailure.Add(10);ExpectFailure(CloseStartedObservations);if(PendingProcesses.Count!=1||PendingThreads.Count!=1||Closed[2]!=1)throw new Exception("Pending partial close lost ownership or skipped later handle");CloseFailure.Clear();CloseStartedObservations();Empty();if(Closed[1]!=2||Closed[10]!=2||Closed[2]!=1)throw new Exception("Pending retry repeated a successful close");return;}
  if(kind>=15&&kind<=17){Started(info,true);if(kind==16)CloseFailure.Add(10);if(kind==15){if(ResumeOwnedPrimaryThread(101)!=1||SuspendedThreads.Count!=0||Closed[10]!=1)throw new Exception("Resume success semantics changed");}else{ExpectFailure(()=>ResumeOwnedPrimaryThread(101));if(kind==16&&(SuspendedThreads.Count!=1||Closed[10]!=1))throw new Exception("Resumed-thread close failure lost ownership");if(kind==17&&(SuspendedThreads.Count!=0||Closed[10]!=1))throw new Exception("Unexpected suspension-count refusal changed");}CloseFailure.Clear();StopOwnedStartedProcesses();CloseStartedObservations();Empty();if(Closed[1]!=1||Closed[10]!=(kind==16?2:1))throw new Exception("Resumed-thread retry repeated a successful close");return;}
  throw new Exception("Unknown source ownership control");
 }
}
'@.Replace('__EXACT_START__',$normalStart).Replace('__EXACT_RESUME__',$normalResume).Replace('__EXACT_STOP__',$normalStop).Replace('__EXACT_CLOSE__',$normalClose)
Add-Type -TypeDefinition $normalMock
foreach($case in 1..17){[MixelNormalOwnershipSourceMock]::Run($case)}
$normalFinally=Section $normal "  `$customerCleanupFailure = `$null" '  if ($ordinaryRoot -or $ownedUser -or $desktopAccess -or $namespaceAccess)'
if(-not $normalFinally.Contains("try { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }`n  catch")){throw 'Normal fixture close failure skips remaining outer cleanup.'}
$tokens=$null;$errors=$null
$standardOuterAst=[Management.Automation.Language.Parser]::ParseInput($original,[ref]$tokens,[ref]$errors)
$standardOuter=@($standardOuterAst.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.TryStatementAst]})
if($errors.Count -or $standardOuter.Count -ne 1 -or -not $standardOuter[0].Finally){throw 'Exact standard outer finally boundary changed.'}
$standardFinally=$standardOuter[0].Finally.Extent.Text
if(-not $standardFinally.Contains("try { [MixelOrdinaryTokenFixture]::CloseStartedObservations() } catch")){throw 'Standard source close failure skips remaining outer cleanup.'}
function Assert-OriginalObservationCleanup([string]$Text) {
  $tokens=$null; $errors=$null
  $ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$tokens,[ref]$errors)
  if($errors.Count) { throw 'Original observation source does not parse.' }
  foreach($name in @('Invoke-StandardLeaseSubsystemObservations','Invoke-StandardLeaseNamespaceObservations')) {
    $functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true))
    if($functions.Count -ne 1) { throw 'Source observation cleanup function is missing or duplicated.' }
    $text=$functions[0].Body.Extent.Text.Replace("`r`n","`n")
    $expected=if($name -ceq 'Invoke-StandardLeaseSubsystemObservations'){2}else{1}
    if([regex]::Matches($text,'(?m)^\s+finally \{ \[MixelOrdinaryTokenFixture\]::CloseStartedObservations\(\) \}$').Count -ne $expected) { throw 'Source observation stop failures skip exact handle cleanup.' }
    if($name -ceq 'Invoke-StandardLeaseNamespaceObservations') {
      $pending=@'
            try {
              Complete-LeaseNtPendingProtocols
              if ($matrixHandle -ne [IntPtr]::Zero -and -not (Test-LeaseNtWorldPending $matrixHandle)) { [MixelIsolatedLeaseControl]::Close($matrixHandle); $matrixHandle=[IntPtr]::Zero }
            } finally {
              if (@($script:ntPendingProtocols | Where-Object { -not $_.childExited }).Count -eq 0) {
                [MixelOrdinaryTokenFixture]::CloseStartedObservations()
              } else {
                # The original subsequent fixture reuse/eight controls close their
                # observation map. Do not enter them while this exact owned child
                # still needs that borrowed identity and the verified World handle.
                throw 'Exact pending NT child remains live after owned stop; retained process/World/protocol ownership prevents further fixture reuse.'
              }
            }
'@
      if([regex]::Matches($text,[regex]::Escape($pending.Replace("`r`n","`n"))).Count -ne 1){throw 'Exact pending-child drain/zero-live guard/close-or-fatal ownership branch changed.'}
    }
  }
}
Assert-OriginalObservationCleanup $original
foreach($changed in @($original.Replace('[MixelOrdinaryTokenFixture]::CloseStartedObservations()','changed-close'),$original.Replace('function Invoke-StandardLeaseNamespaceObservations(', 'function ChangedObservation('),$original.Replace('Complete-LeaseNtPendingProtocols','Changed-PendingDrain'),$original.Replace('Where-Object { -not $_.childExited }','Where-Object { $_.childExited }'),$original.Replace('Exact pending NT child remains live after owned stop;','Changed pending-child fatal;'))) {
  $refused=$false
  try { Assert-OriginalObservationCleanup $changed } catch { $refused=$true }
  if(-not $refused) { throw 'Missing original observation cleanup was accepted.' }
}
Write-Host 'PASS: exact normal fixture ownership source passes seventeen fault/success controls; failed waits and closes retain ownership, later children still clean, thread-close/PID/partial-return failures refuse launch, resumed-thread close failure stays owned and retry never closes a successful handle twice.'

# Keep the exact isolated event/mutex ownership bodies, substituting only their
# API providers. No imported Windows declaration is compiled or invoked here.
$isolatedPrefix="if (-not ('MixelIsolatedLeaseControl' -as [type])) { Add-Type @'"+"`n"
$isolatedBlock=Section $original $isolatedPrefix "`n'@ }"
$isolated=$isolatedBlock.Substring($isolatedPrefix.Length)
$providers=@{
  '  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint version, out IntPtr descriptor, IntPtr size);'='  static bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text,uint version,out IntPtr descriptor,IntPtr size){if(version!=1||size!=IntPtr.Zero)throw new Exception("Descriptor arguments changed");LastSddl=text;descriptor=new IntPtr(9);return true;}'
  '  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateEventExW(ref Attributes attributes, string name, uint flags, uint access);'='  static IntPtr CreateEventExW(ref Attributes attributes,string name,uint flags,uint access){if(flags!=0||access!=0x100000||attributes.inherit!=0||attributes.descriptor!=new IntPtr(9))throw new Exception("Event arguments changed");return Created();}'
  '  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateMutexW(ref Attributes attributes, bool owner, string name);'='  static IntPtr CreateMutexW(ref Attributes attributes,bool owner,string name){if(owner||attributes.inherit!=0||attributes.descriptor!=new IntPtr(9))throw new Exception("Mutex arguments changed");return Created();}'
  '  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);'='  static bool CloseHandle(IntPtr handle){long value=handle.ToInt64();int count;Closed.TryGetValue(value,out count);Closed[value]=count+1;SourceLastError=6;return !CloseFailure.Contains(value);}'
  '  [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);'='  static IntPtr LocalFree(IntPtr memory){if(memory!=new IntPtr(9))throw new Exception("Descriptor ownership changed");Freed++;SourceLastError=0;return IntPtr.Zero;}'
  # These read-only diagnostics are outside the original creation/ownership
  # control. Throw if any is reached; compile the entire current class.
  ('  [DllImport("kernel32.dll", EntryPoint="OpenEventW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]'+"`n"+'  static extern IntPtr ReadOnlyOpenEvent(uint access, bool inherit, string name);')='  static IntPtr ReadOnlyOpenEvent(uint access,bool inherit,string name){throw new InvalidOperationException("Out-of-scope descriptor query");}'
  ('  [DllImport("advapi32.dll", EntryPoint="GetSecurityInfo", ExactSpelling=true)]'+"`n"+'  static extern uint ReadOnlyGetSecurityInfo(IntPtr handle, uint objectType, uint information, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);')='  static uint ReadOnlyGetSecurityInfo(IntPtr handle,uint objectType,uint information,out IntPtr owner,out IntPtr group,out IntPtr dacl,out IntPtr sacl,out IntPtr descriptor){throw new InvalidOperationException("Out-of-scope descriptor query");}'
  ('  [DllImport("advapi32.dll", EntryPoint="GetSecurityDescriptorDacl", ExactSpelling=true, SetLastError=true)]'+"`n"+'  static extern bool ReadOnlyGetDacl(IntPtr descriptor, out bool present, out IntPtr acl, out bool defaulted);')='  static bool ReadOnlyGetDacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted){throw new InvalidOperationException("Out-of-scope descriptor query");}'
  ('  [DllImport("advapi32.dll", EntryPoint="GetSecurityDescriptorSacl", ExactSpelling=true, SetLastError=true)]'+"`n"+'  static extern bool ReadOnlyGetSacl(IntPtr descriptor, out bool present, out IntPtr acl, out bool defaulted);')='  static bool ReadOnlyGetSacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted){throw new InvalidOperationException("Out-of-scope descriptor query");}'
  ('  [DllImport("kernel32.dll", EntryPoint="LocalFree", ExactSpelling=true, SetLastError=true)]'+"`n"+'  static extern IntPtr ReadOnlyLocalFree(IntPtr memory);')='  static IntPtr ReadOnlyLocalFree(IntPtr memory){throw new InvalidOperationException("Out-of-scope descriptor query");}'
}
foreach($declaration in $providers.Keys){
  if([regex]::Matches($isolated,[regex]::Escape($declaration)).Count -ne 1){throw 'Isolated ownership API provider boundary drifted.'}
  $isolated=$isolated.Replace($declaration,$providers[$declaration])
}
$isolated=$isolated.Replace('public static class MixelIsolatedLeaseControl {','public static class MixelIsolatedOwnershipSourceMock {').Replace('Marshal.GetLastWin32Error()','SourceLastError')
if($isolated.Contains('DllImport') -or -not $isolated.TrimEnd().EndsWith('}')){throw 'Isolated ownership mock contains a native declaration or malformed boundary.'}
$isolatedTests=@'
  static int SourceLastError,Freed,Next;static bool CreateFailure;static string LastSddl;
  static readonly System.Collections.Generic.HashSet<long> CloseFailure=new System.Collections.Generic.HashSet<long>();
  static readonly System.Collections.Generic.Dictionary<long,int> Closed=new System.Collections.Generic.Dictionary<long,int>();
  static IntPtr Created(){SourceLastError=5;return CreateFailure?IntPtr.Zero:new IntPtr(++Next);}
  static void Reset(){Owned.Clear();CloseFailure.Clear();Closed.Clear();SourceLastError=0;Freed=0;Next=0;CreateFailure=false;LastSddl=null;}
  static void Reject(Action action){bool refused=false;try{action();}catch(InvalidOperationException){refused=true;}catch(Win32Exception){refused=true;}catch(AggregateException){refused=true;}if(!refused)throw new Exception("Invalid isolated ownership state was accepted");}
  public static void Run(){
    const string name="Global\\Mixel-Remote-Standard-Lease-0123456789abcdef0123456789abcdef";
    Reset();Close(IntPtr.Zero);Reject(()=>Close(new IntPtr(99)));Reject(()=>WorldEvent("Global\\Mixel-Remote-Attended-Runtime-v2"));if(Closed.Count!=0||Freed!=0||Owned.Count!=0)throw new Exception("Unowned close/name refusal made an API call");
    Reset();IntPtr first=WorldEvent(name),second=WrongObject(name);if(Owned.Count!=2||Freed!=2||LastSddl!="D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)")throw new Exception("Successful creation ownership or SDDL changed");ClosePending();if(Owned.Count!=0||Closed[1]!=1||Closed[2]!=1)throw new Exception("Successful created handles not closed once");
    Reset();first=DeniedEvent(name);second=WrongObject(name);CloseFailure.Add(first.ToInt64());Reject(ClosePending);if(Owned.Count!=1||!Owned.Contains(first)||Closed[1]!=1||Closed[2]!=1)throw new Exception("First isolated close failure skipped later handle or lost ownership");CloseFailure.Clear();ClosePending();if(Owned.Count!=0||Closed[1]!=2||Closed[2]!=1)throw new Exception("Retry repeated a successful isolated close");
    Reset();CreateFailure=true;try{WorldEvent(name);throw new Exception("Failed event creation accepted");}catch(Win32Exception error){if(error.NativeErrorCode!=5)throw new Exception("Create error was not retained before descriptor free");}try{WrongObject(name);throw new Exception("Failed mutex creation accepted");}catch(Win32Exception error){if(error.NativeErrorCode!=5)throw new Exception("Mutex error was not retained before descriptor free");}ClosePending();if(Owned.Count!=0||Closed.Count!=0||Freed!=2)throw new Exception("Failed creation invented handle ownership");
  }
'@
$isolated=$isolated.TrimEnd();$isolated=$isolated.Substring(0,$isolated.Length-1)+$isolatedTests+"`n}"
Add-Type -TypeDefinition $isolated
[MixelIsolatedOwnershipSourceMock]::Run()
Write-Host 'PASS: exact isolated event/mutex ownership bodies reject unowned/product names, retain failed closes, attempt later handles, retry only unresolved handles and retain creation errors before descriptor release.'

# Execute the actual outer finally blocks with pure providers, including the
# failure branches that used to abandon remaining owned resources.
$tokens=$null;$errors=$null
$standardAst=[Management.Automation.Language.Parser]::ParseFile($StandardPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Standard cleanup source does not parse.'}
$namespace=@($standardAst.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Invoke-StandardLeaseNamespaceObservations'},$true))
if($namespace.Count -ne 1){throw 'Exact namespace cleanup function boundary changed.'}
$matrix=@($namespace[0].FindAll({param($node)$node -is [Management.Automation.Language.TryStatementAst] -and $node.Finally -and @($node.Body.Statements|Where-Object{$_.Extent.Text -ceq '$matrixHandle=[MixelIsolatedLeaseControl]::WorldEvent($endpoint)'}).Count -eq 1},$true))
$main=@($standardAst.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.TryStatementAst]})
if($matrix.Count -ne 1 -or $main.Count -ne 1){throw 'Exact outer cleanup finally boundaries changed.'}
Add-Type @'
using System;
using System.Collections.Generic;
public static class MixelStandardOuterSourceMock {
  public static bool EventFails,StopFails,CloseFails,PendingFails,HashFails,DesktopFails,HashDrift;
  public static readonly List<string> Calls=new List<string>();
  public static void Reset(){EventFails=StopFails=CloseFails=PendingFails=HashFails=DesktopFails=HashDrift=false;Calls.Clear();}
  public static void Close(IntPtr handle){Calls.Add("eventClose");if(EventFails)throw new InvalidOperationException("Synthetic owned event close failure");}
  public static void ClosePending(){Calls.Add("pendingClose");if(PendingFails)throw new InvalidOperationException("Synthetic pending event close failure");}
  public static void StopOwnedStartedProcesses(){Calls.Add("stopOwned");if(StopFails)throw new InvalidOperationException("Synthetic owned stop failure");}
  public static void CloseStartedObservations(){Calls.Add("closeOwned");if(CloseFails)throw new InvalidOperationException("Synthetic owned process close failure");}
  public static string DesktopAclHashes(){Calls.Add("hashRead");if(HashFails)throw new InvalidOperationException("Synthetic hash read failure");return HashDrift?"foreign":"original";}
  public sealed class DesktopAccess {public void Dispose(){Calls.Add("desktopDispose");if(DesktopFails)throw new InvalidOperationException("Synthetic desktop restoration failure");}}
  public static void RemoveProfile(string sid){Calls.Add("profileCleanup");}
}
public sealed class MixelStandardOuterSidSourceMock {public MixelStandardOuterSidSourceMock(string value){if(value!="synthetic-owned-sid")throw new Exception("Mock SID attribution differs");}}
'@
function Get-ActualStandardCleanup($Block){
  $text=$Block.Extent.Text
  if(-not $text.StartsWith('{') -or -not $text.EndsWith('}')){throw 'Actual finally delimiters changed.'}
  $text=$text.Substring(1,$text.Length-2).Replace('[MixelOrdinaryTokenFixture]','[MixelStandardOuterSourceMock]').Replace('[MixelIsolatedLeaseControl]','[MixelStandardOuterSourceMock]').Replace('[Security.Principal.SecurityIdentifier]','[MixelStandardOuterSidSourceMock]')
  return [scriptblock]::Create($text)
}
$matrixCleanup=Get-ActualStandardCleanup $matrix[0].Finally
$mainCleanup=Get-ActualStandardCleanup $main[0].Finally
function Test-ActualStandardCleanup {
  function Remove-Item {param($LiteralPath,[switch]$Recurse,[switch]$Force) [MixelStandardOuterSourceMock]::Calls.Add('filesCleanup')}
  function Remove-LocalUser {param($SID) [MixelStandardOuterSourceMock]::Calls.Add('sidCleanup')}
  function Get-LocalUser {param($Name,$SID,$ErrorAction) return $null}
  function Start-Sleep {param($Seconds,$Milliseconds)}
  function Write-Host {param($Object)}
  function Test-LeaseNtWorldPending([IntPtr]$Handle){return @($script:ntPendingProtocols|Where-Object{-not $_.childExited}).Count -gt 0}
  function Complete-LeaseNtPendingProtocols {if($script:syntheticConfirmNtExit){foreach($context in $script:ntPendingProtocols){$context.childExited=$true}}}
  $script:ntPendingProtocols=[Collections.Generic.List[object]]::new()
  $script:ntPreservedRoots=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  $script:syntheticConfirmNtExit=$false
  foreach($case in 0..3){
    [MixelStandardOuterSourceMock]::Reset()
    [MixelStandardOuterSourceMock]::EventFails=$case -eq 1
    [MixelStandardOuterSourceMock]::StopFails=$case -eq 2
    [MixelStandardOuterSourceMock]::CloseFails=$case -eq 3
    $matrixHandle=[IntPtr]555;$caught=$false
    try{. $matrixCleanup}catch{$caught=$true}
    $expectedCalls=if($case -eq 1){'eventClose,stopOwned,eventClose,closeOwned'}else{'eventClose,stopOwned,closeOwned'}
    if($caught -ne ($case -ne 0) -or ([MixelStandardOuterSourceMock]::Calls -join ',') -cne $expectedCalls -or ($matrixHandle -ne [IntPtr]::Zero) -ne ($case -eq 1)){throw 'Actual matrix finally abandons cleanup or discards a failed event close.'}
  }
  foreach($case in 0..3){
    [MixelStandardOuterSourceMock]::Reset()
    [MixelStandardOuterSourceMock]::StopFails=$case -eq 1
    [MixelStandardOuterSourceMock]::EventFails=$case -eq 3
    $script:ntPendingProtocols.Clear();$script:ntPendingProtocols.Add([pscustomobject]@{childExited=$false})
    $script:syntheticConfirmNtExit=$case -ge 2
    $matrixHandle=[IntPtr]555;$caught=$false
    try{. $matrixCleanup}catch{$caught=$true}
    $expectedCalls=if($case -lt 2){'stopOwned'}else{'stopOwned,eventClose,closeOwned'}
    if($caught -ne ($case -ne 2) -or ([MixelStandardOuterSourceMock]::Calls -join ',') -cne $expectedCalls -or ($matrixHandle -ne [IntPtr]::Zero) -ne ($case -ne 2)){throw 'Actual pending-live/late-exit matrix cleanup loses ownership or skips an independent process close.'}
  }
  $script:ntPendingProtocols.Clear();$script:syntheticConfirmNtExit=$false
  foreach($case in 0..8){
    [MixelStandardOuterSourceMock]::Reset()
    [MixelStandardOuterSourceMock]::EventFails=$case -eq 1
    [MixelStandardOuterSourceMock]::StopFails=$case -eq 2
    [MixelStandardOuterSourceMock]::CloseFails=$case -eq 3
    [MixelStandardOuterSourceMock]::PendingFails=$case -eq 4
    [MixelStandardOuterSourceMock]::HashFails=$case -in @(5,8)
    [MixelStandardOuterSourceMock]::DesktopFails=$case -in @(6,8)
    [MixelStandardOuterSourceMock]::HashDrift=$case -eq 7
    $eventHandle=[IntPtr]555;$desktopAccess=$null;$namespaceAccess=$null;$originalDesktop='original'
    if($case -ge 6){$desktopAccess=[MixelStandardOuterSourceMock+DesktopAccess]::new()}
    $script:namespaceRestorationKnown=$true
    $profileStarted=$true;$ownedSid='synthetic-owned-sid';$ownedUser='synthetic-owned-user'
    $ownedPassword='synthetic-nonsecret-marker';$random='synthetic-nonsecret-marker'
    $fixtureRoot=$temporaryCleanup;$failure='synthetic-prior-failure'
    . $mainCleanup
    $expectedCalls='eventClose,stopOwned,closeOwned,pendingClose,'+$(if($case -ge 6){'desktopDispose,'}else{''})+'hashRead'
    if($case -notin @(2,5,6,7,8)){$expectedCalls+=',profileCleanup,sidCleanup,filesCleanup'}
    $expectedFailures=if($case -eq 8){3}elseif($case -in @(2,5,6,7)){2}else{[int]($case -ne 0)}
    if(([MixelStandardOuterSourceMock]::Calls -join ',') -cne $expectedCalls -or $null -ne $ownedPassword -or $null -ne $random -or $cleanup.Count -ne $expectedFailures){throw 'Actual standard outer finally skips a later owned cleanup attempt, deletes ownership before known exit/restoration, or conceals a failure.'}
  }
}
$temporaryCleanup=Join-Path ([IO.Path]::GetTempPath()) ('mixel-source-cleanup-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($temporaryCleanup)
try{Test-ActualStandardCleanup}finally{[IO.Directory]::Delete($temporaryCleanup,$true)}
Write-Host 'PASS: exact matrix/main finally blocks retain ten original and four pending-live/late-exit controls plus three desktop Dispose/hash/drift controls; independent hash verification follows failed Dispose, unknown exit or desktop restoration quarantines profile/account/root, and memory-only markers clear.'
$nativeControlSource=@'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
public static class MixelPortableNativePipeControls {
  public static int NativeStage { get; private set; }
  public static void Run() {
    string capacityName="MixelRemoteQA-lease-"+Guid.NewGuid().ToString("N");
    NativeStage=4;
    using(var first=new MixelPortableLeaseAdditionalInstanceChannel(capacityName,true,0)) {
      NativeStage=5;
      // Prove spare capacity independently of the FIRST_PIPE_INSTANCE flag.
      using(var additional=new MixelPortableLeaseAdditionalInstanceChannel(capacityName,true,0)) {}
      NativeStage=6;
      bool firstRefused=false;
      try { using(var collision=new MixelPortableLeaseFirstInstanceChannel(capacityName,true,0)) {} }
      catch(System.ComponentModel.Win32Exception error) { if(error.NativeErrorCode!=5)throw; firstRefused=true; }
      if(!firstRefused)throw new Exception("First-instance flag accepted a second server with available capacity");
    }
    string name="MixelRemoteQA-lease-"+Guid.NewGuid().ToString("N");
    uint pid=(uint)System.Diagnostics.Process.GetCurrentProcess().Id;
    NativeStage=1;
    using(var server=new MixelPortableLeaseNativeChannel(name,true,0)) {
      bool refused=false;
      NativeStage=2;
      try { using(var collision=new MixelPortableLeaseNativeChannel(name,true,0)) {} } catch(System.ComponentModel.Win32Exception error) { if(error.NativeErrorCode!=5 && error.NativeErrorCode!=231)throw; refused=true; }
      if(!refused)throw new Exception("Single-instance pipe collision accepted");
      NativeStage=3;
      var client=Task.Run(()=> {
        using(var channel=new MixelPortableLeaseNativeChannel(name,false,pid)) {
          if(channel.PeerPid!=pid)throw new Exception("Actual server PID changed");
          foreach(string request in new[]{"denied","wrong-object"}) {
            channel.Send(request,1000);
            if(channel.Receive(2000)!=request+"-ready")throw new Exception("Phase ready reply changed");
            channel.Send(request+"-release",1000);
            if(channel.Receive(2000)!=request+"-closed")throw new Exception("Phase closed reply changed");
          }
          channel.Send("finished",1000);
        }
      });
      try {
        foreach(string request in new[]{"denied","wrong-object"}) {
          if(server.Receive(2000)!=request || server.PeerPid!=pid)throw new Exception("Actual client PID or phase changed");
          server.Send(request+"-ready",1000);
          if(server.Receive(2000)!=request+"-release")throw new Exception("Phase release changed");
          server.Send(request+"-closed",1000);
        }
        if(server.Receive(2000)!="finished")throw new Exception("Completion changed");
        if(!client.Wait(2000))throw new Exception("Retained transport task exceeded bound");
      } finally {
        server.Dispose();
        if(!client.Wait(10000))throw new Exception("Retained transport task did not finish on cleanup");
      }
    }
  }
}
'@
$nativeControlSource+=$channel.Replace('MixelPortableLeaseChannel','MixelPortableLeaseNativeChannel')
$createCall='CreateNamedPipeW(@"\\.\pipe\"+name,0x40080003,8,1,4096,4096,0,ref attributes)'
if([regex]::Matches($channel,[regex]::Escape($createCall)).Count -ne 1) { throw 'Exact native create call changed before isolated flag controls.' }
$firstInstanceSource=$channel.Replace('MixelPortableLeaseChannel','MixelPortableLeaseFirstInstanceChannel').Replace($createCall,$createCall.Replace(',8,1,4096,',',8,2,4096,'))
$additionalInstanceSource=$firstInstanceSource.Replace('MixelPortableLeaseFirstInstanceChannel','MixelPortableLeaseAdditionalInstanceChannel').Replace('0x40080003,8,2,4096,','0x40000003,8,2,4096,')
$nativeControlSource+=$firstInstanceSource+$additionalInstanceSource
Add-Type -TypeDefinition $nativeControlSource
if($NativePipeControls) {
  if([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess) { throw 'Native transport controls require actual x64 Windows.' }
  try { [MixelPortableNativePipeControls]::Run() } catch {
    $nativeException=$_.Exception
    for($depth=0;$depth -lt 8 -and $null -ne $nativeException.InnerException;$depth++) { $nativeException=$nativeException.InnerException }
    $nativeCodeAvailable=$nativeException -is [ComponentModel.Win32Exception]
    $nativeCode=0
    if($nativeCodeAvailable) { $nativeCode=$nativeException.NativeErrorCode }
    Write-Host ('Native transport failure: stage={0}; nativeCodeAvailable={1}; nativeCode={2}' -f [MixelPortableNativePipeControls]::NativeStage,$nativeCodeAvailable,$nativeCode)
    throw
  }
  Write-Host 'PASS: actual Windows first-instance flag rejects a second server after spare capacity is independently proven; unchanged single-instance pipe refuses collision; bidirectional two-phase/release/finish exchange and kernel server/client PID checks pass; retained task and all handles close. Transport only; no standard-account consent or signed-app qualification.'
} else {
  Write-Host 'Result: exact interactive source controls pass; zero Windows API calls and no standard-account/runtime qualification.'
}
