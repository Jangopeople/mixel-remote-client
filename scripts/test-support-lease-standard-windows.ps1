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
  Add-Type -TypeDefinition $launchSource.Substring($first, $last - $first)
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
    if (!System.Text.RegularExpressions.Regex.IsMatch(name, @"\AGlobal\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z")) throw new InvalidOperationException("Expected GUID-isolated lease control name");
  }
  public static IntPtr DeniedEvent(string name) {
    Isolated(name);
    IntPtr descriptor;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW("D:P(A;;GA;;;BA)(A;;GA;;;SY)",1,out descriptor,IntPtr.Zero)) throw new Win32Exception();
    IntPtr handle; int error;
    try {
      Attributes attributes=new Attributes { length=(uint)Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=0 };
      handle=CreateEventExW(ref attributes,name,0,0x100000);
      error=handle==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    } finally { LocalFree(descriptor); }
    if(handle==IntPtr.Zero) throw new Win32Exception(error);
    return handle;
  }
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

function Invoke-StandardLeaseControl([string]$Mode, [string]$ExpectedStatus, [int]$ExpectedError,
    [string]$ExpectedProof, [bool]$ExpectedRequired, [bool]$ExpectedOwnerFailed, [switch]$KeepAlive) {
  if (Test-Path $resultPath) { Remove-Item -LiteralPath $resultPath }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-mode.txt'), $Mode, [Text.UTF8Encoding]::new($false))
  $controlPid = [MixelOrdinaryTokenFixture]::StartStandardUser($executable, $ownedUser, $ownedPassword)
  $control = $null
  try {
    $control = Get-Process -Id $controlPid
    if ([MixelOrdinaryTokenFixture]::Elevated($controlPid) -or $control.SessionId -ne (Get-Process -Id $PID).SessionId) {
      throw 'Lease control is not the actual non-elevated same-session standard token.'
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
    if ($record.pid -ne $controlPid -or $record.sourceStatus -cne $ExpectedStatus -or
        $record.rawOpenError -ne $ExpectedError -or $record.attestation -cne $ExpectedProof -or
        $record.requiresClick -isnot [bool] -or $record.requiresClick -ne $ExpectedRequired -or
        $record.ownerFailed -isnot [bool] -or $record.ownerFailed -ne $ExpectedOwnerFailed) {
      throw ('Actual standard-account source lease classification mismatch: ' + ($record | ConvertTo-Json -Compress))
    }
    Write-Host "FIXTURE: actual source lease under non-elevated same-session token; mode=$Mode; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
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
