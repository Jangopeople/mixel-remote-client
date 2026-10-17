# A separate, required cold customer test. Never dot-source the launcher driver:
# only SHA-bound AST literals/functions are reused; its top-level app flow is inert.
param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [Parameter(Mandatory = $true)][string]$ExpectedPayload,
  [Parameter(Mandatory = $true)][string]$EvidenceDirectory
)
$ErrorActionPreference = 'Stop'

function Read-ColdPinnedText([string]$Path, [string]$Expected) {
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.PSIsContainer -or $item.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 1048576) { throw 'Cold source must be bounded regular text.' }
  $text = [Text.UTF8Encoding]::new($false, $true).GetString([IO.File]::ReadAllBytes($item.FullName))
  if ($text.Contains("`r")) {
    $rest = $text.Replace("`r`n", '')
    if ($rest.Contains("`r") -or $rest.Contains("`n")) { throw 'Mixed cold source newlines refused.' }
    $text = $text.Replace("`r`n", "`n")
  }
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $actual = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-', '').ToLowerInvariant() }
  finally { $sha.Dispose() }
  if ($actual -cne $Expected) { throw 'Exact cold source dependency changed.' }
  return $text
}

function Assert-ColdIdentity($Facts, [int]$ExpectedPid, [string]$ExpectedSid, [int]$Session, [string]$ExpectedPath) {
  if ($Facts.pid -ne $ExpectedPid -or $Facts.sid -cne $ExpectedSid -or $Facts.session -ne $Session -or
      $Facts.elevated -or $Facts.integrity -ne 8192 -or $Facts.birth -le 0 -or
      -not $Facts.path.Equals($ExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Cold product process is not the exact owned Standard/Medium image and session.'
  }
}

function Assert-ColdHealth($Health, [int]$ExpectedPid, [string]$ExpectedId) {
  if ($Health.incomingPid -ne $ExpectedPid -or -not $Health.attendedReady -or
      $Health.attendedProof -cne 'attended-runtime-v2' -or -not $Health.brandedRelay -or
      $Health.rendezvousState -le 0 -or -not $Health.keyConfirmed -or
      $Health.rendezvousServer -cne 'rs.mixel.ch:21116' -or
      ($ExpectedId -and $Health.registeredId -cne $ExpectedId)) {
    throw 'Cold incoming PID, stable ID, relay readiness or native v2 guard failed.'
  }
}

function Assert-ColdUnauthenticatedRemote([int]$ExpectedPid) {
  # ipc.rs answers from AUTHED_CONNS filtered to AuthConnType::Remote.
  $reply=[MixelSupportIpcProbe]::RequestWithServerPid('{"t":"VideoConnCount","c":null}')
  $record=$reply.Json | ConvertFrom-Json
  if ($reply.ServerPid -ne $ExpectedPid -or $record.t -isnot [string] -or $record.t -cne 'VideoConnCount' -or
      ($record.c -isnot [int] -and $record.c -isnot [long]) -or $record.c -ne 0) {
    throw 'Cold actual incoming endpoint has an authenticated Remote peer or invalid count.'
  }
}

function Assert-ColdStartWitnesses($Records, [int]$Outer, [int]$Incoming, [int]$Session) {
  $byPid=@{}
  foreach ($record in $Records) {
    if ($byPid.ContainsKey([int]$record.pid) -or $record.session -ne $Session -or
        $record.birth -le 0 -or $record.timeCreated -lt $record.birth -or
        $record.timeCreated - $record.birth -gt 100000000) { throw 'Cold process-start identity/time witness is incomplete or reused.' }
    $byPid.Add([int]$record.pid,$record)
  }
  if (-not $byPid.ContainsKey($Outer) -or -not $byPid.ContainsKey($Incoming) -or
      $byPid[$Outer].name -cne 'Mixel-Remote-QS.exe' -or $byPid[$Incoming].name -cne 'Mixel-Remote.exe') {
    throw 'Cold WMI delivery did not witness both the actual outer and incoming main.'
  }
  foreach ($record in $Records) {
    $current=[int]$record.pid; $visited=@{}
    for ($depth=0; $current -ne $Outer -and $depth -lt 16; $depth++) {
      if ($visited.ContainsKey($current) -or -not $byPid.ContainsKey($current)) { throw 'Cold product ancestry is foreign or cyclic.' }
      $visited.Add($current,$true); $current=[int]$byPid[$current].parent
    }
    if ($current -ne $Outer) { throw 'Cold product ancestry did not reach the owned outer.' }
  }
}

function Get-ColdMainWindow([int]$ProcessId) {
  $windows=[Collections.Generic.List[long]]::new()
  [MixelSupportWindowTest]::EnumWindows({
    param($hwnd,$unused)
    [uint32]$owner=0
    $thread=[MixelSupportWindowTest]::GetWindowThreadProcessId($hwnd,[ref]$owner)
    if ($thread -ne 0 -and $owner -eq $ProcessId) {
      $title=[Text.StringBuilder]::new(512)
      [void][MixelSupportWindowTest]::GetWindowText($hwnd,$title,$title.Capacity)
      if ($title.ToString() -ceq 'Mixel-Remote' -and [MixelSupportWindowTest]::IsWindowVisible($hwnd) -and -not [MixelSupportWindowTest]::IsIconic($hwnd)) { $windows.Add($hwnd.ToInt64()) }
    }
    return $true
  },[IntPtr]::Zero) | Out-Null
  if ($windows.Count -gt 1) { throw 'Cold incoming process has ambiguous branded main windows.' }
  if ($windows.Count -eq 0) { return [long]0 }
  return $windows[0]
}

function Save-ColdEvidence([string]$Name, $Value) {
  if ($Name -cnotmatch '\A[A-Z][A-Z0-9-]*\.json\z') { throw 'Invalid cold evidence name.' }
  $path = Join-Path $EvidenceDirectory $Name
  if (Test-Path -LiteralPath $path) { throw 'Cold evidence already exists.' }
  [IO.File]::WriteAllText($path, ($Value | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
}

function Read-ColdStarts {
  foreach ($event in @(Get-Event -SourceIdentifier $eventSource -ErrorAction SilentlyContinue)) {
    try {
      $entry = $event.SourceEventArgs.NewEvent
      if ($events.Count -ge 512) { throw 'Cold process-start evidence exceeded its bound.' }
      $name = [string]$entry.ProcessName
      $record=[ordered]@{name=$name;pid=[int]$entry.ProcessID;parent=[int]$entry.ParentProcessID;session=[int]$entry.SessionID;timeCreated=[long]$entry.TIME_CREATED;birth=0}
      $events.Add($record)
      if ($name -ieq 'consent.exe') { throw 'Cold customer launch requested a UAC consent or credential process.' }
      $child = [int]$entry.ProcessID
      if ($child -eq $outerPid) { $record.birth=$outerFacts.birth; continue }
      if ($name -ine 'Mixel-Remote.exe') { throw 'Unexpected product process in the isolated cold scenario.' }
      $facts = [MixelColdProcessObserver]::Capture($child)
      Assert-ColdIdentity $facts $child $ownedSid $callerSession $runtimePath
      $record.birth=$facts.birth
      if ((Get-FileHash -LiteralPath $facts.path -Algorithm SHA256).Hash -cne $expectedMainHash) { throw 'Cold child is not the signed compiled payload.' }
    } finally { Remove-Event -EventIdentifier $event.EventIdentifier -ErrorAction Stop }
  }
}

if (-not $IsWindows -or -not [Environment]::Is64BitProcess -or $env:GITHUB_ACTIONS -cne 'true' -or
    $env:GITHUB_REPOSITORY -cne 'Jangopeople/mixel-remote-client' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
    $env:GITHUB_JOB -cne 'windows' -or $env:MIXEL_HOSTED_OWNED_QA_NAMESPACE -cne '1') {
  throw 'Cold support test requires the isolated explicit hosted Windows shipping job.'
}
$archiveRoot = [IO.Path]::GetFullPath((Join-Path $env:RUNNER_TEMP 'mixel-owned-ordinary-qs-logs'))
$EvidenceDirectory = [IO.Path]::GetFullPath($EvidenceDirectory)
if (-not $EvidenceDirectory.StartsWith($archiveRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $EvidenceDirectory)) { throw 'Cold evidence must be a new child of the existing QA log artifact root.' }
foreach ($parent in @($archiveRoot, (Split-Path $EvidenceDirectory -Parent))) {
  if (Test-Path -LiteralPath $parent) {
    $item = Get-Item -LiteralPath $parent -Force
    if (-not $item.PSIsContainer -or $item.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe cold evidence parent.' }
  }
}
New-Item -ItemType Directory -Path $EvidenceDirectory -Force | Out-Null
$evidenceAcl = Get-Acl -LiteralPath $EvidenceDirectory
$evidenceAcl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)) {
  $evidenceAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
}
Set-Acl -LiteralPath $EvidenceDirectory -AclObject $evidenceAcl
$launcher = Read-ColdPinnedText (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') 'c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b'
$tokens = $null; $errors = $null
$tree = [Management.Automation.Language.Parser]::ParseInput($launcher, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Pinned actor source parse failed.' }
foreach ($type in @('MixelSupportWindowTest', 'MixelOrdinaryTokenFixture', 'MixelPackagedQaTokenFacts')) {
  $literals = @($tree.FindAll({param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] -and $node.Value.Contains('public static class ' + $type + ' {')}, $true))
  if ($literals.Count -ne 1) { throw 'Exact actor class literal missing or duplicated.' }
  Add-Type -TypeDefinition $literals[0].Value -CompilerOptions /warnaserror+ -ErrorAction Stop
}
foreach ($name in @('Start-OwnedPackagedQaNamespace', 'Restore-OwnedPackagedQaNamespace', 'Assert-OwnedPackagedQaCleanupBeforeDeletion')) {
  $functions = @($tree.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $name })
  if ($functions.Count -ne 1) { throw 'Exact actor namespace function missing or duplicated.' }
  . ([scriptblock]::Create($functions[0].Extent.Text))
}
. (Join-Path $PSScriptRoot 'support-runtime-probe-windows.ps1')

# Retain exact process handles before reading identities. Cleanup never resolves
# a replacement PID or kills by name; only these captured owned handles can stop.
Add-Type -TypeDefinition @'
using System; using System.Collections.Generic; using System.ComponentModel; using System.Runtime.InteropServices; using System.Security.Principal; using System.Text;
public static class MixelColdProcessObserver {
  [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll",SetLastError=true)] static extern uint GetProcessId(IntPtr handle);
  [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint timeout);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr handle,uint exit);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr handle,uint flags,StringBuilder path,ref uint size);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetProcessTimes(IntPtr handle,out long creation,out long exit,out long kernel,out long user);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr buffer,int size,out int required);
  public sealed class Facts { public int pid,session,integrity; public long birth; public bool elevated; public string sid,path; }
  sealed class Owned { public IntPtr handle; public Facts facts; }
  static readonly Dictionary<int,Owned> owned=new Dictionary<int,Owned>();
  static int Scalar(IntPtr token,int kind) { IntPtr data=Marshal.AllocHGlobal(4); try {int actual;if(!GetTokenInformation(token,kind,data,4,out actual)||actual!=4)throw new Win32Exception();return Marshal.ReadInt32(data);}finally{Marshal.FreeHGlobal(data);} }
  public static Facts Capture(int pid) {
    Owned existing;if(owned.TryGetValue(pid,out existing)) {if(GetProcessId(existing.handle)!=(uint)pid||WaitForSingleObject(existing.handle,0)!=0x102)throw new InvalidOperationException("Retained PID exited or changed");return existing.facts;}
    IntPtr process=OpenProcess(0x101001,false,pid),token=IntPtr.Zero,label=IntPtr.Zero;
    if(process==IntPtr.Zero)throw new Win32Exception();
    try {
      if(GetProcessId(process)!=(uint)pid||WaitForSingleObject(process,0)!=0x102)throw new InvalidOperationException("Cold process exited before token observation");
      if(!OpenProcessToken(process,8,out token))throw new Win32Exception();
      var f=new Facts{pid=pid,session=Scalar(token,12),elevated=Scalar(token,20)!=0};
      using(var identity=new WindowsIdentity(token)){f.sid=identity.User.Value;}
      int size;GetTokenInformation(token,25,IntPtr.Zero,0,out size);if(size<IntPtr.Size+4||size>65536)throw new InvalidOperationException("Invalid integrity size");
      label=Marshal.AllocHGlobal(size);int actual;if(!GetTokenInformation(token,25,label,size,out actual)||actual>size)throw new Win32Exception();
      var sid=new SecurityIdentifier(Marshal.ReadIntPtr(label)).Value;f.integrity=int.Parse(sid.Substring(sid.LastIndexOf('-')+1));
      uint length=32768;var path=new StringBuilder((int)length);if(!QueryFullProcessImageName(process,0,path,ref length))throw new Win32Exception();f.path=path.ToString();
      long exit,kernel,user;if(!GetProcessTimes(process,out f.birth,out exit,out kernel,out user))throw new Win32Exception();
      owned.Add(pid,new Owned{handle=process,facts=f});process=IntPtr.Zero;return f;
    } finally {if(label!=IntPtr.Zero)Marshal.FreeHGlobal(label);if(token!=IntPtr.Zero&&!CloseHandle(token))throw new Win32Exception();if(process!=IntPtr.Zero&&!CloseHandle(process))throw new Win32Exception();}
  }
  public static bool Live(int pid) {Owned value;if(!owned.TryGetValue(pid,out value))throw new InvalidOperationException("Unowned cold process");uint wait=WaitForSingleObject(value.handle,0);if(wait!=0&&wait!=0x102)throw new Win32Exception();return wait==0x102;}
  public static void StopAll(string sid,string root,string outer) {
    foreach(var value in owned.Values){var f=value.facts;if(f.sid!=sid||(!f.path.Equals(outer,StringComparison.OrdinalIgnoreCase)&&!f.path.StartsWith(root+System.IO.Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)))throw new InvalidOperationException("Foreign cleanup subject");
      uint wait=WaitForSingleObject(value.handle,0);if(wait==0x102){if(!TerminateProcess(value.handle,1)&&WaitForSingleObject(value.handle,10000)!=0)throw new Win32Exception();if(WaitForSingleObject(value.handle,10000)!=0)throw new InvalidOperationException("Owned child still live");}else if(wait!=0)throw new Win32Exception();}
  }
  public static void CloseAll(){foreach(var value in owned.Values){if(WaitForSingleObject(value.handle,0)!=0)throw new InvalidOperationException("Unconfirmed process exit");if(!CloseHandle(value.handle))throw new Win32Exception();}owned.Clear();}
}
'@ -CompilerOptions /warnaserror+ -ErrorAction Stop

$ownedUser=$null; $ownedSid=$null; $ownedPassword=$null; $profile=$null; $ownedRoot=$null; $desktopAccess=$null
$script:namespaceAccess=$null; $script:namespaceRestorationKnown=$true; $script:namespaceFacts=$null; $script:qaNamespaceCleanupOkay=$true
$events=[Collections.Generic.List[object]]::new(); $samples=[Collections.Generic.List[object]]::new()
$eventSource='Mixel-Cold-'+[Guid]::NewGuid().ToString('N'); $subscription=$null; $outerPid=0; $mainPid=0
$primaryFailure=$null; $stage='qualify signed inputs'; $clean=$false; $accepted=$false
try {
  if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) { throw 'Hosted cold fixture requires its administrative runner for disposable account setup.' }
  $policy=Get-ItemProperty -LiteralPath 'HKLM:/SOFTWARE/Microsoft/Windows/CurrentVersion/Policies/System'
  $uac=[ordered]@{}
  foreach ($name in @('EnableLUA','ConsentPromptBehaviorUser','ConsentPromptBehaviorAdmin','PromptOnSecureDesktop')) {
    if ($null -eq $policy.PSObject.Properties[$name] -or $policy.$name -isnot [int]) { throw 'Actual UAC policy observation is incomplete.' }
    $uac.Add($name,[int]$policy.$name)
  }
  Save-ColdEvidence 'UAC-POLICY.json' $uac
  if ($uac.EnableLUA -ne 1 -or $uac.ConsentPromptBehaviorUser -notin @(1,3)) { throw 'Host UAC disabled or auto-deny policy cannot prove absence of a Standard credential request.' }
  if (@(Get-Process | Where-Object { $_.ProcessName -like 'Mixel-Remote*' -or $_.ProcessName -ieq 'consent' }).Count -ne 0 -or [MixelOrdinaryTokenFixture]::LeasePresent()) { throw 'Cold scenario requires no existing product, consent process or attended owner.' }
  $Executable=(Resolve-Path -LiteralPath $Executable).Path; $ExpectedPayload=(Resolve-Path -LiteralPath $ExpectedPayload).Path
  if ([IO.Path]::GetFileName($Executable) -cne 'Mixel-Remote-QS.exe') { throw 'Cold test requires the actual customer QuickSupport outer alias.' }
  & python (Join-Path $PSScriptRoot 'build-windows-portable.py') --verify-manifest $Executable
  if ($LASTEXITCODE -ne 0) { throw 'Signed outer asInvoker manifest verification failed.' }
  if ((Get-AuthenticodeSignature -LiteralPath $Executable).Status -ne 'Valid') { throw 'Cold outer signature is invalid.' }
  & (Join-Path $PSScriptRoot 'verify-windows-payload.ps1') -Payload $ExpectedPayload
  $outerHash=(Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash
  $expectedMainHash=(Get-FileHash -LiteralPath (Join-Path $ExpectedPayload 'Mixel-Remote.exe') -Algorithm SHA256).Hash
  $callerSession=(Get-Process -Id $PID).SessionId
  $stage='create fresh Standard profile'
  $ownedUser='mixelqs'+[Guid]::NewGuid().ToString('N').Substring(0,10)
  $random=[byte[]]::new(24); [Security.Cryptography.RandomNumberGenerator]::Fill($random)
  $ownedPassword='aA!9'+[Convert]::ToBase64String($random); $random=$null
  $account=New-LocalUser -Name $ownedUser -Password (ConvertTo-SecureString $ownedPassword -AsPlainText -Force) -AccountNeverExpires -PasswordNeverExpires -Description 'Mixel isolated cold support QA'
  $ownedSid=$account.SID.Value
  Add-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')) -Member $ownedUser
  if (@(Get-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')) | Where-Object { $_.SID.Value -ceq $ownedSid }).Count) { throw 'Disposable user unexpectedly belongs to Administrators.' }
  $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
  Start-OwnedPackagedQaNamespace
  $ownedRoot=Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-cold-'+[Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $ownedRoot | Out-Null
  $outer=Join-Path $ownedRoot 'Mixel-Remote-QS.exe'; Copy-Item -LiteralPath $Executable -Destination $outer
  if ((Get-FileHash -LiteralPath $outer -Algorithm SHA256).Hash -cne $outerHash) { throw 'Cold copied outer bytes changed.' }
  $stage='observe actual cold outer and children'
  $subscription=Register-CimIndicationEvent -Namespace root/cimv2 -Query "SELECT * FROM Win32_ProcessStartTrace WHERE ProcessName='consent.exe' OR ProcessName='Mixel-Remote.exe' OR ProcessName='Mixel-Remote-QS.exe'" -SourceIdentifier $eventSource
  $outerPid=[MixelOrdinaryTokenFixture]::StartStandardUser($outer,$ownedUser,$ownedPassword,$true)
  $identity=[MixelOrdinaryTokenFixture]::SuspendedOwnedIdentity($outerPid)
  if ($identity['kernelPid'] -cne [string]$outerPid -or $identity['sid'] -cne $ownedSid -or $identity['elevated'] -cne 'False' -or $identity['integritySid'] -cne 'S-1-16-8192' -or $identity['sessionId'] -cne [string]$callerSession) { throw 'Actual suspended outer is not fresh Standard/Medium in the runner session.' }
  $profile=[MixelOrdinaryTokenFixture]::ProfilePath($outerPid)
  $runtimeRoot=Join-Path $profile 'AppData/Local/mixel-remote'; $runtimePath=Join-Path $runtimeRoot 'Mixel-Remote.exe'
  if (Test-Path -LiteralPath $runtimeRoot) { throw 'Fresh profile already contains a product extraction.' }
  $outerFacts=[MixelColdProcessObserver]::Capture($outerPid); Assert-ColdIdentity $outerFacts $outerPid $ownedSid $callerSession $outer
  if ([MixelOrdinaryTokenFixture]::ResumeOwnedPrimaryThread($outerPid) -ne 1) { throw 'Exact initial outer suspension was not released once.' }
  $ownedPassword=$null
  $deadline=[DateTime]::UtcNow.AddSeconds(90); $health=$null; $last='No owned incoming endpoint.'
  while ([DateTime]::UtcNow -lt $deadline) {
    Read-ColdStarts
    try {
      $candidate=Get-MixelSupportRuntimeHealth
      $facts=[MixelColdProcessObserver]::Capture($candidate.incomingPid)
      Assert-ColdIdentity $facts $candidate.incomingPid $ownedSid $callerSession $runtimePath
      Assert-ColdHealth $candidate $candidate.incomingPid ''
      $health=$candidate; $mainPid=[int]$candidate.incomingPid; break
    } catch { $last=$_.Exception.GetType().Name }
    Start-Sleep -Milliseconds 200
  }
  if (-not $health) { throw "Cold Standard launch failed to establish a guarded online incoming endpoint: $last" }
  foreach ($file in @(Get-ChildItem -LiteralPath $ExpectedPayload -Recurse -File -Force)) {
    $relative=[IO.Path]::GetRelativePath($ExpectedPayload,$file.FullName); $actual=Join-Path $runtimeRoot $relative
    if (-not (Test-Path -LiteralPath $actual -PathType Leaf) -or (Get-FileHash -LiteralPath $actual -Algorithm SHA256).Hash -cne (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash) { throw 'Cold outer extracted different signed payload bytes.' }
  }
  & (Join-Path $PSScriptRoot 'verify-windows-payload.ps1') -Payload $runtimeRoot
  $senderDeadline=[DateTime]::UtcNow.AddSeconds(30)
  while ([MixelOrdinaryTokenFixture]::StartedStatus($outerPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $senderDeadline) { Read-ColdStarts; Start-Sleep -Milliseconds 200 }
  if ([MixelOrdinaryTokenFixture]::StartedStatus($outerPid) -cne 'exited:0x00000000') { throw 'Cold outer did not exit successfully after starting its owned application.' }
  $witnessDeadline=[DateTime]::UtcNow.AddSeconds(10)
  while ((@($events | Where-Object {$_.pid -eq $outerPid}).Count -eq 0 -or @($events | Where-Object {$_.pid -eq $mainPid}).Count -eq 0) -and [DateTime]::UtcNow -lt $witnessDeadline) { Read-ColdStarts; Start-Sleep -Milliseconds 200 }
  Assert-ColdStartWitnesses $events.ToArray() $outerPid $mainPid $callerSession
  $stage='guard, same PID and relay survive sender exit beyond 90 seconds'
  [long]$originalWindow=0; $windowDeadline=[DateTime]::UtcNow.AddSeconds(30)
  while ($originalWindow -eq 0 -and [DateTime]::UtcNow -lt $windowDeadline) {
    Read-ColdStarts
    if (-not [MixelColdProcessObserver]::Live($mainPid)) { throw 'Cold incoming process exited before its branded main window.' }
    $originalWindow=Get-ColdMainWindow $mainPid
    if ($originalWindow -eq 0) { Start-Sleep -Milliseconds 200 }
  }
  if ($originalWindow -eq 0) { throw 'Cold incoming process did not show its branded main window.' }
  $stableId=$health.registeredId; $clock=[Diagnostics.Stopwatch]::StartNew()
  do {
    Read-ColdStarts
    $current=Get-MixelSupportRuntimeHealth; Assert-ColdHealth $current $mainPid $stableId
    Assert-ColdUnauthenticatedRemote $mainPid
    $facts=[MixelColdProcessObserver]::Capture($mainPid); Assert-ColdIdentity $facts $mainPid $ownedSid $callerSession $runtimePath
    if (-not [MixelColdProcessObserver]::Live($mainPid) -or -not [MixelOrdinaryTokenFixture]::OwnsLease($mainPid)) { throw 'Cold foreground incoming process lost its owned kernel consent lease.' }
    $lease=Get-MixelSupportLeaseDiagnostic $mainPid
    if ($lease -cnotmatch '\AownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=(true|false)\z') { throw 'Cold native lease state is not kernel-active and healthy.' }
    if ((Get-ColdMainWindow $mainPid) -ne $originalWindow) { throw 'Cold incoming process lost the same visible branded main HWND.' }
    $elapsedSeconds=$clock.Elapsed.TotalSeconds
    $samples.Add([ordered]@{seconds=$elapsedSeconds;incomingPid=$mainPid;birth=$facts.birth;medium=$true;attendedReady=$current.attendedReady;relayReady=$true;stableId=$true;kernelLease=$true;remoteAuthenticated=0;senderExited=$true;hwnd=$originalWindow})
    if ($elapsedSeconds -ge 95) { break }
    Start-Sleep -Seconds 3
  } while ($true)
  Read-ColdStarts
  Assert-ColdStartWitnesses $events.ToArray() $outerPid $mainPid $callerSession
  Save-ColdEvidence 'PROCESS-STARTS.json' $events.ToArray()
  Save-ColdEvidence 'SAMPLES.json' $samples.ToArray()
  $accepted=$true
} catch {
  $primaryFailure=$_
  try { Save-ColdEvidence 'PROCESS-STARTS.json' $events.ToArray(); Save-ColdEvidence 'SAMPLES.json' $samples.ToArray() } catch { Write-Host 'Cold partial evidence additionally failed; original failure preserved.' }
  try { Save-ColdEvidence 'FAILED.json' ([ordered]@{stage=$stage;exceptionType=$_.Exception.GetType().FullName;accepted=$false}) } catch { Write-Host 'Cold failure receipt additionally failed; original failure preserved.' }
  throw
} finally {
  $ownedPassword=$null
  $cleanup=[Collections.Generic.List[string]]::new(); $processesExited=$false
  try {
    if ($ownedSid -and $runtimeRoot -and $outer) { [MixelColdProcessObserver]::StopAll($ownedSid,$runtimeRoot,$outer) }
    [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses(); $processesExited=$true
  } catch { $cleanup.Add('Owned exact process cleanup failed.') }
  try { Restore-OwnedPackagedQaNamespace $processesExited } catch { $cleanup.Add('Owned namespace restoration failed.') }
  try { [MixelColdProcessObserver]::CloseAll(); [MixelOrdinaryTokenFixture]::CloseStartedObservations() } catch { $cleanup.Add('Owned process handle close failed.') }
  if ($subscription) {
    try { Unregister-Event -SourceIdentifier $eventSource -ErrorAction Stop; Get-Event -SourceIdentifier $eventSource -ErrorAction SilentlyContinue | Remove-Event -ErrorAction Stop } catch { $cleanup.Add('Owned process-start subscription cleanup failed.') }
  }
  if ($desktopAccess) { try { $desktopAccess.Dispose() } catch { $cleanup.Add('Owned desktop ACL restoration failed.') } }
  if ($processesExited -and $script:qaNamespaceCleanupOkay -and $cleanup.Count -eq 0) {
    if ($ownedSid) {
      try { [MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid); Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) } catch { $cleanup.Add('Owned profile/account cleanup failed.') }
    }
    if ($ownedRoot -and $cleanup.Count -eq 0) { try { Remove-Item -LiteralPath $ownedRoot -Recurse -Force } catch { $cleanup.Add('Owned outer directory cleanup failed.') } }
  }
  $clean=($cleanup.Count -eq 0 -and $processesExited -and $script:qaNamespaceCleanupOkay)
  try { Save-ColdEvidence 'RESULT.json' ([ordered]@{schema=1;scope='actual-signed-cold-standard-quicksupport';accepted=($accepted -and $clean);cleanupOkay=$clean;outerPid=$outerPid;incomingPid=$mainPid;outerSHA256=$outerHash;mainSHA256=$expectedMainHash;observedStarts=$events.Count;samples=$samples.Count;senderExited=($accepted);consentObserved=@($events | Where-Object {$_.name -ieq 'consent.exe'}).Count;globalReleaseQualified=$false}) }
  catch { if (-not $primaryFailure) { throw }; Write-Host 'Cold final receipt additionally failed; original failure preserved.' }
  if (-not $clean -and -not $primaryFailure) { throw 'Cold support owned cleanup failed; no acceptance.' }
}
if (-not $accepted -or -not $clean) { throw 'Cold signed Standard support gate did not pass.' }
Write-Host "PASS: actual signed asInvoker customer outer starts fresh Standard/Medium cold QuickSupport without observed UAC/elevated product children; same incoming PID, registered ID, branded relay, kernel lease and v2 readiness persist 95s after sender exit; owned cleanup verified."
