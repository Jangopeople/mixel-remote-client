# Normal native functional controls under the exact customer fixture's ordinary
# token. No debugger, registry observation, product event, or customer payload.
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') {
  throw 'Disposable standard-account lease qualification requires an isolated Windows Actions runner.'
}
$launchSource = Get-Content (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') -Raw
$class = $launchSource.IndexOf('public static class MixelOrdinaryTokenFixture {')
$first = $launchSource.LastIndexOf('using System;', $class)
$last = $launchSource.IndexOf("`n'@ }", $class)
if ($class -lt 0 -or $first -lt 0 -or $last -le $first) { throw 'Exact normal ordinary-token fixture is missing.' }
if (-not ('MixelOrdinaryTokenFixture' -as [type])) {
  $normalSource = $launchSource.Substring($first, $last - $first)
  $marker = '  public static int StartStandardUser(string executable, string username, string password) {'
  $position = $normalSource.IndexOf($marker)
  if ($position -lt 0 -or $position -ne $normalSource.LastIndexOf($marker)) { throw 'Exact normal standard-launch comparison boundary changed.' }
  # Add only a documented secondary-launch comparison and retained-token SID
  # check to the test class; preserve its original customer launch and cleanup.
  $comparison = @'
  public static int StartStandardUserWithToken(string executable, string username, string password, string expectedSid) {
    IntPtr token;
    if (!LogonUserW(username, ".", password, 2, 0, out token)) throw new Win32Exception();
    try {
      using (var identity = new WindowsIdentity(token)) {
        if (identity.User.Value != expectedSid || ElevatedToken(token)) throw new InvalidOperationException("Secondary launch did not log on the owned standard SID");
      }
      StartupInfo startup = Startup(); ProcessInfo process;
      if (!CreateProcessWithTokenW(token, 1, executable, new StringBuilder("\"" + executable + "\""), 0, IntPtr.Zero, System.IO.Path.GetDirectoryName(executable), ref startup, out process)) throw new Win32Exception();
      return Started(process);
    } finally { CloseHandle(token); }
  }
  public static bool StartedUserSidMatches(int pid, string expectedSid) {
    IntPtr process, token;
    if (!StartedProcesses.TryGetValue(pid, out process)) throw new InvalidOperationException("SID check does not own the retained process");
    if (!OpenProcessToken(process, 8, out token)) throw new Win32Exception();
    try {
      using (var identity = new WindowsIdentity(token)) { return identity.User.Value == expectedSid; }
    } finally { CloseHandle(token); }
  }
'@
  $bridge = @'
  public static System.Collections.Generic.Dictionary<string,string> ObserveOwnedNamespace(int pid) {
    IntPtr process;
    if (!StartedProcesses.TryGetValue(pid,out process) || WaitForSingleObject(process,0)!=0x102 || GetProcessId(process)!=checked((uint)pid)) throw new InvalidOperationException("Namespace observation requires an actual retained live owned process");
    return MixelCI.MixelStandardNamespaceDiagnostics.Observe(process);
  }
'@
  $normalSource = $normalSource.Insert($position,$comparison+"`n"+$bridge+"`n")
  $diagnostics = Get-Content (Join-Path $PSScriptRoot 'windows-standard-namespace-diagnostics.cs') -Raw
  Add-Type -TypeDefinition ($normalSource+"`n"+$diagnostics)
}
if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) { throw 'Owned standard-account qualification requires the elevated isolated runner.' }

if (-not ('MixelIsolatedLeaseControl' -as [type])) { Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MixelIsolatedLeaseControl {
  [StructLayout(LayoutKind.Sequential)] struct Attributes { public uint length; public IntPtr descriptor; public int inherit; }
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint version, out IntPtr descriptor, IntPtr size);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateEventExW(ref Attributes attributes, string name, uint flags, uint access);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateMutexW(ref Attributes attributes, bool owner, string name);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
  static void Isolated(string name) {
    if (!System.Text.RegularExpressions.Regex.IsMatch(name, @"\A(?:(?:Global|Local)\\)?Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z")) throw new InvalidOperationException("Expected GUID-isolated lease control name");
  }
  static IntPtr Event(string name, string sddl) {
    Isolated(name);
    IntPtr descriptor;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,out descriptor,IntPtr.Zero)) throw new Win32Exception();
    IntPtr handle; int error;
    try {
      Attributes attributes=new Attributes { length=(uint)Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=0 };
      handle=CreateEventExW(ref attributes,name,0,0x100000);
      error=handle==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    } finally { LocalFree(descriptor); }
    if(handle==IntPtr.Zero) throw new Win32Exception(error);
    return handle;
  }
  public static IntPtr DeniedEvent(string name) { return Event(name,"D:P(A;;GA;;;BA)(A;;GA;;;SY)"); }
  public static IntPtr WorldEvent(string name) { return Event(name,"D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)"); }
  public static IntPtr WrongObject(string name) {
    Isolated(name);
    // Grant the requested SYNCHRONIZE right to the ordinary account, so an
    // access denial cannot obscure the intended wrong-object type error.
    IntPtr descriptor;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW("D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)",1,out descriptor,IntPtr.Zero)) throw new Win32Exception();
    IntPtr handle; int error;
    try {
      Attributes attributes=new Attributes { length=(uint)Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=0 };
      handle=CreateMutexW(ref attributes,false,name);
      error=handle==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    } finally { LocalFree(descriptor); }
    if(handle==IntPtr.Zero) throw new Win32Exception(error);
    return handle;
  }
  public static void Close(IntPtr handle) { if(handle!=IntPtr.Zero && !CloseHandle(handle)) throw new Win32Exception(); }
}
'@ }

function Assert-StandardLeaseSafeResult($Record, [uint32]$ExpectedPid) {
  $names = @($Record.PSObject.Properties.Name | Sort-Object)
  $expectedNames = @('attestation','ownerFailed','pid','rawOpenError','requiresClick','sourceStatus')
  $pidIsNumber = $Record.pid -is [int] -or $Record.pid -is [long] -or $Record.pid -is [uint32] -or $Record.pid -is [uint64]
  $errorIsNumber = $Record.rawOpenError -is [int] -or $Record.rawOpenError -is [long] -or $Record.rawOpenError -is [uint32] -or $Record.rawOpenError -is [uint64]
  if (($names -join ',') -cne ($expectedNames -join ',') -or $ExpectedPid -eq 0 -or
      -not $pidIsNumber -or $Record.pid -ne $ExpectedPid -or -not $errorIsNumber -or
      $Record.rawOpenError -lt 0 -or $Record.rawOpenError -gt [uint32]::MaxValue -or
      $Record.sourceStatus -isnot [string] -or $Record.attestation -isnot [string] -or
      $Record.attestation -cnotin @('','attended-runtime-v2','guard-unavailable') -or
      $Record.requiresClick -isnot [bool] -or $Record.ownerFailed -isnot [bool]) {
    throw 'Owned standard-account result is not the exact PID or canonical safe source classification.'
  }
  $codePattern = '(?:none|0|-?[1-9][0-9]{0,9})'
  $fields = [regex]::Match($Record.sourceStatus, ('\AownerFailed=(?<owner>true|false);ownerError=(?<ownerError>' + $codePattern + ');kernel=(?<kernel>active|absent|unavailable);probeError=(?<probeError>' + $codePattern + ');memoryActive=(true|false)\z'))
  if (-not $fields.Success -or
      (($fields.Groups['owner'].Value -ceq 'true') -ne ($fields.Groups['ownerError'].Value -cne 'none')) -or
      (($fields.Groups['kernel'].Value -ceq 'unavailable') -ne ($fields.Groups['probeError'].Value -cne 'none')) -or
      (($fields.Groups['owner'].Value -ceq 'true') -ne $Record.ownerFailed)) {
    throw 'Owned standard-account result contains inconsistent read-only native lease classification.'
  }
  foreach ($field in @('ownerError','probeError')) {
    $value = $fields.Groups[$field].Value
    [int]$nativeCode = 0
    if ($value -cne 'none' -and -not [int]::TryParse($value, [Globalization.NumberStyles]::AllowLeadingSign, [Globalization.CultureInfo]::InvariantCulture, [ref]$nativeCode)) {
      throw 'Owned standard-account result contains an out-of-range native lease error.'
    }
  }
}

function Invoke-StandardLeaseControl([ValidateSet('probe','owner','failed-owner')][string]$Mode, [string]$ExpectedStatus, [int]$ExpectedError,
    [string]$ExpectedProof, [bool]$ExpectedRequired, [bool]$ExpectedOwnerFailed, [switch]$KeepAlive,
    [switch]$ObserveOnly, [string]$ObservationLabel,
    [ValidateSet('with-logon','with-token')][string]$Launcher = 'with-logon') {
  if (Test-Path $resultPath) { Remove-Item -LiteralPath $resultPath }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-mode.txt'), $Mode, [Text.UTF8Encoding]::new($false))
  if ($Launcher -ceq 'with-token') {
    $controlPid = [MixelOrdinaryTokenFixture]::StartStandardUserWithToken($executable, $ownedUser, $ownedPassword, $ownedSid)
  } else {
    $controlPid = [MixelOrdinaryTokenFixture]::StartStandardUser($executable, $ownedUser, $ownedPassword)
  }
  $control = $null
  try {
    $control = Get-Process -Id $controlPid
    $sidMatches = [MixelOrdinaryTokenFixture]::StartedUserSidMatches($controlPid, $ownedSid)
    $elevated = [MixelOrdinaryTokenFixture]::Elevated($controlPid)
    $actualSession = $control.SessionId
    if (-not $sidMatches -or $elevated -or $actualSession -ne (Get-Process -Id $PID).SessionId) {
      throw 'Lease control is not the owned SID under the actual non-elevated same-session standard token.'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path $resultPath) -and [DateTime]::UtcNow -lt $deadline) {
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid).StartsWith('exited:')) { break }
      Start-Sleep -Milliseconds 50
    }
    if (-not (Test-Path $resultPath)) { throw 'Owned standard-account lease control did not produce its bounded native result.' }
    $text = [IO.File]::ReadAllText($resultPath)
    if ($text.Length -gt 4096) { throw 'Owned lease result exceeds its bound.' }
    $record = $text | ConvertFrom-Json
    Assert-StandardLeaseSafeResult $record $controlPid
    Write-Host "OBSERVED: primary source lease result before secondary token diagnosis; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2')."
    try {
      $tokenObservation = [MixelOrdinaryTokenFixture]::ObserveOwnedNamespace($controlPid)
      Write-Host ('OBSERVED: actual owned standard-token namespace access; ' + ($tokenObservation | ConvertTo-Json -Compress))
    } catch {
      $nativeFailure = $_.Exception
      while ($nativeFailure.InnerException) { $nativeFailure = $nativeFailure.InnerException }
      if ($nativeFailure -is [MixelCI.NamespaceRestoreException]) { throw }
      $nativeCode = if ($nativeFailure -is [ComponentModel.Win32Exception]) { $nativeFailure.NativeErrorCode } else { $nativeFailure.HResult }
      Write-Host "OBSERVED: secondary owned token namespace diagnosis unavailable; nativeCode=$nativeCode."
    }
    if (-not $ObserveOnly -and ($record.sourceStatus -cne $ExpectedStatus -or
        $record.rawOpenError -ne $ExpectedError -or $record.attestation -cne $ExpectedProof -or
        $record.requiresClick -isnot [bool] -or $record.requiresClick -ne $ExpectedRequired -or
        $record.ownerFailed -isnot [bool] -or $record.ownerFailed -ne $ExpectedOwnerFailed)) {
      throw ('Actual standard-account source lease classification mismatch: ' + ($record | ConvertTo-Json -Compress))
    }
    if ($ObserveOnly) {
      if ($ObservationLabel -cnotmatch '\A(?:bare|local|global)-[a-z]+(?:-[a-z]+)*\z') { throw 'Invalid safe namespace observation label.' }
      Write-Host "OBSERVED: standard-token GUID functional control; launcher=$Launcher; scenario=$ObservationLabel; mode=$Mode; controlPid=$controlPid; ordinarySessionId=$actualSession; ownedSidMatched=$sidMatches; elevated=$elevated; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
    } else {
      Write-Host "FIXTURE: actual source lease under non-elevated same-session token; mode=$Mode; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
    }
    if (-not $KeepAlive) {
      [IO.File]::WriteAllText((Join-Path $fixtureRoot "fixture-release-$controlPid"), 'verified')
      $deadline = [DateTime]::UtcNow.AddSeconds(10)
      while ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 50
      }
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'exited:0x00000000') { throw 'Exact owned functional lease process did not exit successfully.' }
    } elseif ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'still-running') {
      throw 'Actual standard-account lease owner exited before independent observation.'
    }
    return $controlPid
  } finally { if ($control) { $control.Dispose() } }
}

function Invoke-StandardLeaseNamespaceObservations([ValidateSet('with-logon','with-token')][string]$Launcher) {
  # These are observations only. They cannot satisfy the unchanged original
  # Global absence/error2 or consent gate that follows this isolated matrix.
  $savedRoot=$script:fixtureRoot; $savedExecutable=$script:executable; $savedResult=$script:resultPath
  try {
    foreach ($namespace in @('bare','local','global')) {
      $script:fixtureRoot=Join-Path $savedRoot "$Launcher-$namespace"
      New-Item -ItemType Directory -Path $script:fixtureRoot | Out-Null
      $script:executable=Join-Path $script:fixtureRoot 'lease-standard.exe'
      $script:resultPath=Join-Path $script:fixtureRoot 'fixture-result.json'
      $name='Mixel-Remote-Standard-Lease-' + [Guid]::NewGuid().ToString('N')
      $endpoint=switch ($namespace) { 'bare' { $name }; 'local' { 'Local\'+$name }; 'global' { 'Global\'+$name } }
      python (Join-Path $PSScriptRoot 'test-support-lease.py') --export-standard-windows-fixture $script:fixtureRoot --event $endpoint
      if ($LASTEXITCODE -ne 0) { throw 'Normal namespace source control compilation failed.' }
      $matrixHandle=[IntPtr]::Zero
      try {
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-initial-untouched-absent")
        [void](Invoke-StandardLeaseControl -Mode owner -Launcher $Launcher -KeepAlive -ObserveOnly -ObservationLabel "$namespace-explicit-source-owner")
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-fresh-probe-after-owner-request")
        [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-after-confirmed-owner-request-process-termination")
        $matrixHandle=[MixelIsolatedLeaseControl]::WorldEvent($endpoint)
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-existing-world-synchronize-event")
        [MixelIsolatedLeaseControl]::Close($matrixHandle)
        $matrixHandle=[IntPtr]::Zero
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-after-confirmed-world-event-close")
      } finally {
        if ($matrixHandle -ne [IntPtr]::Zero) { [MixelIsolatedLeaseControl]::Close($matrixHandle) }
        [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
      }
    }
  } finally {
    $script:fixtureRoot=$savedRoot; $script:executable=$savedExecutable; $script:resultPath=$savedResult
  }
}

$ownedUser = 'mixellease' + [Guid]::NewGuid().ToString('N').Substring(0,10)
$ownedSid = $null; $ownedPassword = $null; $random = $null
$fixtureRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-standard-lease-' + [Guid]::NewGuid().ToString('N'))
$eventName = 'Global\Mixel-Remote-Standard-Lease-' + [Guid]::NewGuid().ToString('N')
$executable = Join-Path $fixtureRoot 'lease-standard.exe'
$resultPath = Join-Path $fixtureRoot 'fixture-result.json'
$desktopAccess = $null; $profileStarted = $false; $eventHandle = [IntPtr]::Zero; $failure = $null
$originalDesktop = [MixelOrdinaryTokenFixture]::DesktopAclHashes()
try {
  # Use the exact customer account bootstrap; its random password stays in memory.
  $accountStart = $launchSource.IndexOf('$random = [byte[]]::new(24)')
  $accountEnd = $launchSource.IndexOf("`$fixtureStage = 'grant owned standard desktop access'", $accountStart)
  if ($accountStart -lt 0 -or $accountEnd -le $accountStart) { throw 'Exact owned standard-account bootstrap is missing.' }
  . ([scriptblock]::Create($launchSource.Substring($accountStart,$accountEnd-$accountStart)))
  if (-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) {
    throw 'Owned standard-account SID was not recorded exactly.'
  }
  New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
  $acl = Get-Acl $fixtureRoot
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new($ownedSid),'Modify','ContainerInherit,ObjectInherit','None','Allow'))
  Set-Acl -Path $fixtureRoot -AclObject $acl
  python (Join-Path $PSScriptRoot 'test-support-lease.py') --export-standard-windows-fixture $fixtureRoot --event $eventName
  if ($LASTEXITCODE -ne 0) { throw 'Actual standard-account lease source compilation failed.' }
  $desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
  $profileStarted = $true
  Invoke-StandardLeaseNamespaceObservations with-logon
  Invoke-StandardLeaseNamespaceObservations with-token
  $absent = 'ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
  $active = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=false'
  $activeOwner = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=true'
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: actual standard-account source probe safely observes absent GUID event, raw error2, empty attestation and saved authorization behavior.'
  $ownerPid = Invoke-StandardLeaseControl owner $activeOwner 0 'attended-runtime-v2' $true $false -KeepAlive
  [void](Invoke-StandardLeaseControl probe $active 0 'attended-runtime-v2' $true $false)
  Write-Host 'PASS: actual standard-account source creates the SYNCHRONIZE lease and a fresh ordinary process observes its live confirmed owner.'
  [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
  if ([MixelOrdinaryTokenFixture]::StartedStatus($ownerPid) -cne 'exited:0x00000001') { throw 'Exact retained standard-account owner handle did not observe its termination.' }
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: confirmed owned standard-account owner termination releases the GUID event; fresh actual source returns empty ordinary attestation.'
  $eventHandle = [MixelIsolatedLeaseControl]::DeniedEvent($eventName)
  try {
    [void](Invoke-StandardLeaseControl probe 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false' 5 'guard-unavailable' $true $false)
    Write-Host 'PASS: actual standard-account inaccessible GUID event retains native error5 and unavailable attestation; authorization remains fail closed.'
  } finally { [MixelIsolatedLeaseControl]::Close($eventHandle); $eventHandle = [IntPtr]::Zero }
  $eventHandle = [MixelIsolatedLeaseControl]::WrongObject($eventName)
  try {
    [void](Invoke-StandardLeaseControl probe 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=6;memoryActive=false' 6 'guard-unavailable' $true $false)
    [void](Invoke-StandardLeaseControl failed-owner 'ownerFailed=true;ownerError=6;kernel=unavailable;probeError=6;memoryActive=true' 6 'guard-unavailable' $true $true)
    Write-Host 'PASS: actual standard-account wrong-object probe and failed owner acquisition retain native error6 and ownerFailed classification without false readiness.'
  } finally { [MixelIsolatedLeaseControl]::Close($eventHandle); $eventHandle = [IntPtr]::Zero }
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: closed wrong-object control leaves no GUID lease or false readiness in a fresh actual standard-account process.'
} catch {
  $failure = $_
  throw
} finally {
  $cleanup = [System.Collections.Generic.List[string]]::new()
  if ($eventHandle -ne [IntPtr]::Zero) { try { [MixelIsolatedLeaseControl]::Close($eventHandle) } catch { $cleanup.Add('Owned isolated event handle cleanup failed.') } }
  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $cleanup.Add('Exact owned standard lease process cleanup failed.') }
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  if ($desktopAccess) { try { $desktopAccess.Dispose() } catch { $cleanup.Add('Owned standard lease desktop ACL restoration failed.') } }
  if ([MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $originalDesktop) { $cleanup.Add('Owned standard lease desktop ACL hashes changed.') }
  if ($profileStarted -and $ownedSid) {
    $removed = $false
    for ($attempt=0; $attempt -lt 5; $attempt++) {
      try { [MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid); $removed=$true; break }
      catch { if ($attempt -lt 4) { Start-Sleep -Seconds 2 } }
    }
    if (-not $removed) { $cleanup.Add('Owned standard lease profile cleanup failed.') }
  }
  if ($ownedSid) { try { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) } catch { $cleanup.Add('Owned standard lease SID cleanup failed.') } }
  $ownedPassword = $null; $random = $null
  if (Test-Path $fixtureRoot) { try { Remove-Item -LiteralPath $fixtureRoot -Recurse -Force } catch { $cleanup.Add('Owned standard lease files cleanup failed.') } }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) { $cleanup.Add('Owned standard lease account remains.') }
  if ($cleanup.Count -gt 0) {
    if ($failure) { Write-Host ('FAIL: standard lease cleanup additionally failed: ' + ($cleanup -join ' ')) }
    else { throw ($cleanup -join ' ') }
  } else { Write-Host 'PASS: standard-account lease controls remove only owned processes/profile/SID/files and restore exact original desktop ACLs.' }
}
