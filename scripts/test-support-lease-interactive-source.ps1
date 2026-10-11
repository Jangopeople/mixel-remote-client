param([string]$MainPath=(Join-Path $PSScriptRoot 'test-support-lease-interactive-windows.ps1'),[string]$AdminPath=(Join-Path $PSScriptRoot 'test-support-lease-negative-windows.ps1'),[string]$StandardPath=(Join-Path $PSScriptRoot 'test-support-lease-standard-windows.ps1'),[switch]$NativePipeControls)
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
$originalCore=Section $original "  `$absent = 'ownerFailed=false;" "} catch {`n  `$failure = `$_`n  throw`n} finally {"
$pattern='(?m)^\s*(?:\[void\]\(|\$ownerPid = )Invoke-StandardLeaseControl[^\r\n]+'
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
$nativeControlSource=@'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
public static class MixelPortableNativePipeControls {
  public static void Run() {
    string name="MixelRemoteQA-lease-"+Guid.NewGuid().ToString("N");
    uint pid=(uint)System.Diagnostics.Process.GetCurrentProcess().Id;
    using(var server=new MixelPortableLeaseNativeChannel(name,true,0)) {
      bool refused=false;
      try { using(var collision=new MixelPortableLeaseNativeChannel(name,true,0)) {} } catch(System.ComponentModel.Win32Exception error) { if(error.NativeErrorCode!=5)throw; refused=true; }
      if(!refused)throw new Exception("First-instance pipe collision accepted");
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
Add-Type -TypeDefinition $nativeControlSource
if($NativePipeControls) {
  if([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess) { throw 'Native transport controls require actual x64 Windows.' }
  [MixelPortableNativePipeControls]::Run()
  Write-Host 'PASS: actual Windows private first-instance pipe refuses name collision; bidirectional two-phase/release/finish exchange and kernel server/client PID checks pass; retained task and both handles close. Transport only; no standard-account consent or signed-app qualification.'
} else {
  Write-Host 'Result: exact interactive source controls pass; zero Windows API calls and no standard-account/runtime qualification.'
}
