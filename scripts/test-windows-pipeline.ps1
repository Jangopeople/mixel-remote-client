$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'test-windows-customer-cleanup.ps1')
$repository = Split-Path $PSScriptRoot -Parent
foreach ($file in (Get-ChildItem $PSScriptRoot -Filter '*.ps1' -File)) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) { throw "PowerShell parse failed: $($file.Name): $errors" }
}
Write-Host 'PASS: every Windows pipeline script parses cleanly.'

function Read-OwnedDesktopControlSnapshot([string]$Path) {
  $stream=$null; $reader=$null
  try {
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    $reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true)
    $text=$reader.ReadToEnd()
  } catch [IO.IOException] {
    $failure=$_.Exception
    while ($failure.InnerException) { $failure=$failure.InnerException }
    if (($failure.HResult -band 0xffff) -in @(32,33)) { return [pscustomobject]@{pending=$true; values=$null} }
    throw
  } finally {
    if ($reader) { $reader.Dispose() }
    if ($stream) { $stream.Dispose() }
  }
  $values=@{}
  foreach ($line in ($text -split '\r?\n')) {
    if ($line -ceq '') { continue }
    $separator=$line.IndexOf('=')
    if ($separator -lt 1) { throw 'Malformed owned native desktop control snapshot.' }
    $name=$line.Substring(0,$separator)
    if ($values.ContainsKey($name)) { throw 'Duplicate owned native desktop control snapshot key.' }
    $values[$name]=$line.Substring($separator+1)
  }
  return [pscustomobject]@{pending=$false; values=$values}
}

$snapshotControl=Join-Path ([IO.Path]::GetTempPath()) ('mixel-owned-desktop-read-lock-' + [Guid]::NewGuid().ToString('N'))
$snapshotLock=$null
try {
  [IO.File]::WriteAllText($snapshotControl,"phase=Win32-calls-complete`npid=4321`n")
  $snapshotLock=[IO.FileStream]::new($snapshotControl,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
  $pending=Read-OwnedDesktopControlSnapshot $snapshotControl
  if (-not $pending.pending -or $null -ne $pending.values) { throw 'Locked owned result snapshot did not report pending.' }
  $snapshotLock.Dispose(); $snapshotLock=$null
  $released=Read-OwnedDesktopControlSnapshot $snapshotControl
  if ($released.pending -or $released.values.phase -cne 'Win32-calls-complete' -or $released.values.pid -cne '4321') { throw 'Released owned result snapshot did not decode completely.' }
  [IO.File]::WriteAllText($snapshotControl,'invalid-result')
  $malformedRejected=$false
  try { [void](Read-OwnedDesktopControlSnapshot $snapshotControl) } catch { $malformedRejected=$true }
  if (-not $malformedRejected) { throw 'Malformed owned result snapshot accepted.' }
} finally {
  if ($snapshotLock) { $snapshotLock.Dispose() }
  if (Test-Path $snapshotControl) { Remove-Item $snapshotControl }
}
Write-Host 'PASS: exact native desktop snapshot reader reports pending only for an actual exclusive file lock, reads a complete snapshot after release and rejects malformed data.'

# Compile the exact native OS fixture used by the real ordinary-to-QS runtime
# test; its policy checks are testable without launching or changing an app.
$launchSource = Get-Content (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') -Raw
$fixtureClass = $launchSource.IndexOf('public static class MixelOrdinaryTokenFixture {')
$fixtureStart = $launchSource.LastIndexOf('using System;', $fixtureClass)
$fixtureEnd = $launchSource.IndexOf("`n'@ }", $fixtureClass)
if ($fixtureClass -lt 0 -or $fixtureStart -lt 0 -or $fixtureEnd -le $fixtureStart) {
  throw 'Actual ordinary-token runtime fixture is missing.'
}
if (-not ('MixelOrdinaryTokenFixture' -as [type])) {
  Add-Type -TypeDefinition $launchSource.Substring($fixtureStart, $fixtureEnd - $fixtureStart)
}
$dumpControl = Join-Path ([IO.Path]::GetTempPath()) ('mixel-owned-dump-attribution-' + [Guid]::NewGuid().ToString('N'))
try {
  $validDump = [byte[]]::new(56)
  foreach ($field in @(@(0,0x504d444d),@(8,1),@(12,32),@(32,15),@(36,12),@(40,44),@(44,12),@(48,1),@(52,4321))) {
    [BitConverter]::GetBytes([uint32]$field[1]).CopyTo($validDump,$field[0])
  }
  [IO.File]::WriteAllBytes($dumpControl,$validDump)
  if ([MixelOrdinaryTokenFixture]::DumpProcessId($dumpControl) -ne 4321) { throw 'Actual minidump process stream was not decoded.' }
  foreach ($mutation in @(@(0,0),@(8,0),@(12,1000),@(36,4),@(40,1000),@(48,0))) {
    $invalidDump=$validDump.Clone()
    [BitConverter]::GetBytes([uint32]$mutation[1]).CopyTo($invalidDump,$mutation[0])
    [IO.File]::WriteAllBytes($dumpControl,$invalidDump)
    $rejected=$false
    try { [void][MixelOrdinaryTokenFixture]::DumpProcessId($dumpControl) } catch { $rejected=$true }
    if (-not $rejected) { throw 'Malformed or unattributed native dump accepted.' }
  }
} finally { if (Test-Path $dumpControl) { Remove-Item $dumpControl } }
Write-Host 'PASS: exact native dump attribution decodes the actual process stream and rejects six malformed or unattributed dump controls.'
$captureTokens=$null; $captureErrors=$null
$captureAst=[System.Management.Automation.Language.Parser]::ParseInput($launchSource,[ref]$captureTokens,[ref]$captureErrors)
$captureFunction=@($captureAst.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Read-OwnedHeapCapture'},$true))
if ($captureFunction.Count -ne 1) { throw 'Exact bounded native heap-capture parser missing.' }
. ([scriptblock]::Create($captureFunction[0].Extent.Text))
$ownedHeap='Last event: 18cc.1234: Exception - code c0000374 (first chance)'
$wrongHeap='Last event: 20cc.9999: Exception - code c0000374 (first chance)'
$ownedBreak='Last event: 18cc.1234: Break instruction exception - code 80000003 (first chance)'
$ownedFrame='00 000000ab`12340000 00007ffa`abcd1000 libmixel_remote+0x4a730'
$unrelatedFrame='01 000000ab`12340020 00007ffa`abcd2000 ntdll!UnrelatedInitialBreak+0x4'
$capture=Read-OwnedHeapCapture @($unrelatedFrame,'MIXEL_NATIVE_HEAP',$ownedHeap,$ownedFrame,'MIXEL_NATIVE_CAPTURE_END',$unrelatedFrame) 6348
if (-not $capture.verified -or $capture.frames.Count -ne 1 -or $capture.frames[0].symbol -cne 'libmixel_remote+0x4a730') {
  throw 'Exact bounded heap capture omitted the owned frame or accepted unmarked rows.'
}
foreach ($invalidLines in @(
    @('MIXEL_NATIVE_HEAP',$wrongHeap,$ownedFrame,$ownedBreak,$unrelatedFrame,'MIXEL_NATIVE_CAPTURE_END'),
    @('MIXEL_NATIVE_HEAP',$ownedHeap,$ownedFrame,$ownedHeap,$unrelatedFrame,'MIXEL_NATIVE_CAPTURE_END'),
    @('MIXEL_NATIVE_HEAP',$ownedBreak,$ownedFrame,'MIXEL_NATIVE_CAPTURE_END'),
    @($ownedHeap,$ownedFrame,'MIXEL_NATIVE_CAPTURE_END'),
    @('MIXEL_NATIVE_HEAP',$ownedHeap,$ownedFrame),
    @('MIXEL_NATIVE_HEAP',$ownedHeap,$ownedFrame,'MIXEL_NATIVE_CAPTURE_END','MIXEL_NATIVE_HEAP',$ownedHeap,$ownedFrame,'MIXEL_NATIVE_CAPTURE_END'))) {
  $rejected=Read-OwnedHeapCapture $invalidLines 6348
  if ($rejected.verified -or $rejected.frames.Count -ne 0 -or $rejected.modules.Count -ne 0) { throw 'Mixed, duplicated or incomplete native exception attribution accepted.' }
}
Write-Host 'PASS: exact bounded live heap capture accepts only the owned PID/code block, excludes unmarked rows and rejects six mixed-event, duplicate-event, DebugBreak, unmarked, incomplete and duplicate-capture controls.'
foreach ($name in @('Read-OwnedLiveDebuggerSnapshot','Read-OwnedExecutionMetadata')) {
  $function=@($captureAst.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true))
  if ($function.Count -ne 1) { throw 'Exact live debugger snapshot/event helper missing.' }
  . ([scriptblock]::Create($function[0].Extent.Text))
}
$liveSnapshotControl=Join-Path ([IO.Path]::GetTempPath()) ('mixel-owned-live-debugger-read-' + [Guid]::NewGuid().ToString('N'))
$liveWriter=$null; $liveLock=$null
try {
  $liveWriter=[IO.FileStream]::new($liveSnapshotControl,[IO.FileMode]::Create,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
  $partial=[Text.Encoding]::UTF8.GetBytes("MIXEL_NATIVE_TIMEOUT_EVENT`nLast event: 18cc.1234: Exception - code c0000005`n")
  $liveWriter.Write($partial,0,$partial.Length); $liveWriter.Flush()
  $snapshot=Read-OwnedLiveDebuggerSnapshot $liveSnapshotControl
  if ($snapshot.pending -or (Read-OwnedExecutionMetadata $snapshot.lines 6348 -TimeoutEvent).startupEventPidVerified) { throw 'Open-writer incomplete event snapshot was attributed.' }
  $completed=[Text.Encoding]::UTF8.GetBytes("MIXEL_NATIVE_TIMEOUT_EVENT_END`n")
  $liveWriter.Write($completed,0,$completed.Length); $liveWriter.Flush()
  $snapshot=Read-OwnedLiveDebuggerSnapshot $liveSnapshotControl
  $event=Read-OwnedExecutionMetadata $snapshot.lines 6348 -TimeoutEvent
  if ($snapshot.pending -or -not $event.startupEventPidVerified -or $event.startupEventCode -cne 'c0000005') { throw 'Retained concurrent writer prevented exact live shared snapshot attribution.' }
  $liveWriter.Dispose(); $liveWriter=$null
  if ($IsWindows) {
    $liveLock=[IO.FileStream]::new($liveSnapshotControl,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $locked=Read-OwnedLiveDebuggerSnapshot $liveSnapshotControl
    if (-not $locked.pending -or $null -ne $locked.lines) { throw 'Actual exclusive live debugger snapshot lock was accepted.' }
    $liveLock.Dispose(); $liveLock=$null
    $released=Read-OwnedLiveDebuggerSnapshot $liveSnapshotControl
    if ($released.pending -or -not (Read-OwnedExecutionMetadata $released.lines 6348 -TimeoutEvent).startupEventPidVerified) { throw 'Released live debugger lock did not decode completely.' }
    Write-Host 'PASS: actual Windows live debugger snapshot reader retries only a transient exclusive lock and attributes the completed exact event after release.'
  }
} finally {
  if ($liveWriter) { $liveWriter.Dispose() }
  if ($liveLock) { $liveLock.Dispose() }
  if (Test-Path $liveSnapshotControl) { Remove-Item $liveSnapshotControl }
}
Write-Host 'PASS: exact live debugger snapshot reader reads while the actual writer handle remains open, rejects its incomplete event and attributes only the completed marked PID/code snapshot.'
$actualDesktop = [MixelOrdinaryTokenFixture]::CurrentDesktopPath()
if ($actualDesktop -notmatch '^[^\\]+\\[^\\]+$' -or $launchSource.Contains('desktop = "winsta0\\default"')) {
  throw 'Actual native launch desktop is missing or reverted to a different hard-coded desktop.'
}
Write-Host "PASS: exact native fixture discovers the actual runner window station and thread desktop used for both permission grants and ordinary GUI launch: $actualDesktop."
foreach ($name in @('\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2', '\Sessions\0\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2')) {
  if (-not [MixelOrdinaryTokenFixture]::IsLeaseName($name)) { throw 'Exact runtime lease name rejected.' }
}
foreach ($name in @('', '\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v1', '\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2-other', '\Sessions\1\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2')) {
  if ([MixelOrdinaryTokenFixture]::IsLeaseName($name)) { throw 'Unrelated process event accepted as the global v2 lease.' }
}
if (-not ('MixelOwnedLeaseFixture' -as [type])) { Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MixelOwnedLeaseFixture {
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateEventExW(IntPtr attributes, string name, uint flags, uint access);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
  public static IntPtr Create() {
    IntPtr handle = CreateEventExW(IntPtr.Zero, "Global\\Mixel-Remote-Attended-Runtime-v2", 0, 0x100000);
    if (handle == IntPtr.Zero) throw new Win32Exception();
    return handle;
  }
}
'@ }
if ([MixelOrdinaryTokenFixture]::OwnsLease($PID) -or [MixelOrdinaryTokenFixture]::LeasePresent()) { throw 'Fixture already has a product event owner.' }
$fixtureHandle = [MixelOwnedLeaseFixture]::Create()
try {
  if (-not [MixelOrdinaryTokenFixture]::OwnsLease($PID)) { throw 'Actual SYNCHRONIZE event handle was not attributed to its process.' }
  if (-not [MixelOrdinaryTokenFixture]::LeasePresent()) { throw 'Live global consent event was not observed.' }
} finally {
  [void][MixelOwnedLeaseFixture]::CloseHandle($fixtureHandle)
}
if ([MixelOrdinaryTokenFixture]::OwnsLease($PID) -or [MixelOrdinaryTokenFixture]::LeasePresent()) { throw 'Closed event remains owned or globally present.' }
Write-Host 'PASS: exact native runtime fixture observes the current PID owning the real SYNCHRONIZE-only global v2 event, then observes its handle release; unrelated event names fail closed.'

# Exercise the exact disposable-account bootstrap before spending time on a
# full signed build. The original runtime failure occurred before its GUI wait
# and was hidden by deleting a name whose account creation had failed.
if ($env:GITHUB_ACTIONS -ceq 'true' -and [MixelOrdinaryTokenFixture]::Elevated($PID)) {
  $accountStart = $launchSource.IndexOf('$random = [byte[]]::new(24)')
  $accountEnd = $launchSource.IndexOf("`$fixtureStage = 'grant owned standard desktop access'", $accountStart)
  if ($accountStart -lt 0 -or $accountEnd -le $accountStart) { throw 'Actual owned account bootstrap is missing.' }
  $ownedUser = 'mixelqs' + [Guid]::NewGuid().ToString('N').Substring(0, 10)
  $ownedSid = $null
  $descriptionRejected = $false
  $negativeSid = $null
  try {
    $negativeAccount = New-LocalUser -Name $ownedUser -NoPassword -Description 'Owned disposable Mixel QuickSupport runtime fixture'
    $negativeSid = $negativeAccount.SID.Value
  } catch [System.Management.Automation.ParameterBindingException] {
    if (-not $_.Exception.Message.Contains('Description') -or -not $_.Exception.Message.Contains('48')) { throw }
    $descriptionRejected = $true
  } finally {
    if ($negativeSid) { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($negativeSid)) }
  }
  if (-not $descriptionRejected) { throw 'Native New-LocalUser did not reject the original invalid 51-character fixture description.' }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) { throw 'Invalid-description negative control unexpectedly created an account.' }
  Write-Host 'PASS: actual New-LocalUser rejects the original invalid 51-character description before creating any account.'
  try {
    . ([scriptblock]::Create($launchSource.Substring($accountStart, $accountEnd - $accountStart)))
    if (-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) {
      throw 'Actual owned standard account was not created with its recorded SID.'
    }
    Write-Host 'PASS: actual ordinary runtime bootstrap creates its isolated standard account and adds it to Users using memory-only random credentials.'
    # Qualify the exact cross-account desktop/launch fixture with an actual
    # Win32 GUI before waiting for the customer app. This control cannot satisfy
    # any customer HWND, incoming IPC, consent lease or 95-second runtime gate.
    $guiRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-ordinary-fixture-' + [Guid]::NewGuid().ToString('N'))
    $guiProcess = $null; $guiPid = $null; $guiAccess = $null; $guiProfile = $false; $guiFailure = $null
    try {
      New-Item -ItemType Directory $guiRoot | Out-Null
      $acl = Get-Acl $guiRoot
      $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($ownedSid), 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
      Set-Acl -Path $guiRoot -AclObject $acl
      $guiSource = Join-Path $guiRoot 'desktop-control.cs'
      $guiExecutable = Join-Path $guiRoot 'desktop-control.exe'
      $guiResult = Join-Path $guiRoot 'desktop-control-result.txt'
      Set-Content $guiSource -Encoding utf8 -Value @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.CompilerServices;
using System.Text;
public static class MixelOwnedDesktopControl {
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateWindowExW(uint extended, string cls, string title, uint style, int x, int y, int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr parameter);
  [DllImport("user32.dll", SetLastError=true)] static extern IntPtr CreateMenu();
  [DllImport("user32.dll")] static extern bool DestroyMenu(IntPtr menu);
  [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr window);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
  [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
  [DllImport("user32.dll", SetLastError=true)] static extern IntPtr GetThreadDesktop(uint thread);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool GetUserObjectInformationW(IntPtr obj, int kind, StringBuilder text, uint size, out uint required);
  static string Name(IntPtr obj) { var text=new StringBuilder(512); uint required; return GetUserObjectInformationW(obj,2,text,1024,out required) ? text.ToString() : "unavailable(win32="+Marshal.GetLastWin32Error()+")"; }
  static void WriteResult(string result, string[] values) {
    string temporary=result+".new";
    File.WriteAllLines(temporary,values);
    if(File.Exists(result)) File.Replace(temporary,result,null);
    else File.Move(temporary,result);
  }
  public static void Main() {
    string result=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"desktop-control-result.txt");
    WriteResult(result,new[] { "phase=entered-main", "pid="+Process.GetCurrentProcess().Id, "session="+Process.GetCurrentProcess().SessionId });
    try { Gui(result); }
    catch(Exception error) {
      WriteResult(result,new[] { "phase=managed-GUI-exception", "pid="+Process.GetCurrentProcess().Id, "exceptionType="+error.GetType().FullName, "exceptionHResult=0x"+error.HResult.ToString("x8") });
    }
  }
  [MethodImpl(MethodImplOptions.NoInlining)] static void Gui(string result) {
    IntPtr menu=CreateMenu(); int menuError=menu==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    IntPtr window=CreateWindowExW(0,"STATIC","Mixel owned ordinary desktop fixture",0x10cf0000,30,30,500,200,IntPtr.Zero,menu,IntPtr.Zero,IntPtr.Zero);
    int windowError=window==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    WriteResult(result,new[] {
      "phase=Win32-calls-complete",
      "pid="+Process.GetCurrentProcess().Id, "session="+Process.GetCurrentProcess().SessionId,
      "desktop="+Name(GetProcessWindowStation())+"\\"+Name(GetThreadDesktop(GetCurrentThreadId())),
      "window="+window.ToInt64(), "visible="+IsWindowVisible(window), "windowError="+windowError,
      "menu="+menu.ToInt64(), "menuError="+menuError, "profile="+Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)
    });
    System.Threading.Thread.Sleep(10000);
    if(window!=IntPtr.Zero) DestroyWindow(window);
    if(menu!=IntPtr.Zero) DestroyMenu(menu);
  }
}
'@
      $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
      & $compiler /nologo /target:winexe "/out:$guiExecutable" $guiSource
      if ($LASTEXITCODE -ne 0 -or -not (Test-Path $guiExecutable)) { throw 'Owned Win32 desktop fixture compilation failed.' }
      $windowClass = $launchSource.IndexOf('public static class MixelSupportWindowTest {')
      $windowFirst = $launchSource.LastIndexOf('using System;', $windowClass)
      $windowLast = $launchSource.IndexOf("`n'@", $windowClass)
      if ($windowClass -lt 0 -or $windowFirst -lt 0 -or $windowLast -le $windowFirst) { throw 'Exact runtime window fixture class missing.' }
      if (-not ('MixelSupportWindowTest' -as [type])) { Add-Type -TypeDefinition $launchSource.Substring($windowFirst,$windowLast-$windowFirst) }
      $launchAst = [System.Management.Automation.Language.Parser]::ParseInput($launchSource,[ref]$null,[ref]$null)
      $diagnostic = @($launchAst.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Write-OwnedWindowDiagnostic'},$true))
      if ($diagnostic.Count -ne 1) { throw 'Exact owned runtime diagnostic function is ambiguous.' }
      . ([scriptblock]::Create($diagnostic[0].Extent.Text))
      $baselineAcls = [MixelOrdinaryTokenFixture]::DesktopAclHashes()
      $desktopControls = [System.Collections.Generic.List[object]]::new()
      foreach ($mode in @('minimal-before', 'canonical', 'minimal-after')) {
      $canonical = $mode -ceq 'canonical'
      if (Test-Path $guiResult) { Remove-Item $guiResult }
      $guiProcess = $null; $guiPid = $null; $trialFailure = $null
      try {
      $guiAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid, $canonical)
      $effectiveDesktopAccess = [MixelOrdinaryTokenFixture]::ActualStandardDesktopAccess($ownedUser, $ownedPassword, $ownedSid)
      Write-Host ("FIXTURE: $mode actual owned standard SID Win32 desktop access errors (0=granted): " + ($effectiveDesktopAccess | ConvertTo-Json -Compress))
      if ($effectiveDesktopAccess['station:0x327'] -ne 0 -or $effectiveDesktopAccess['desktop:0xc7'] -ne 0) { throw 'Owned standard SID lacks its exact temporarily granted Win32 desktop rights.' }
      $guiProfile = $true
      $guiStartedAt = [DateTime]::UtcNow
      $guiPid = [MixelOrdinaryTokenFixture]::StartStandardUser($guiExecutable, $ownedUser, $ownedPassword)
      $guiProcess = Get-Process -Id $guiPid
      if ([MixelOrdinaryTokenFixture]::Elevated($guiPid) -or $guiProcess.SessionId -ne (Get-Process -Id $PID).SessionId) {
        throw 'Owned desktop control is not a genuine non-elevated process in the runner session.'
      }
      $deadline = [DateTime]::UtcNow.AddSeconds(8)
      $actualGui = @{}
      while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path $guiResult) {
          $snapshot=Read-OwnedDesktopControlSnapshot $guiResult
          if (-not $snapshot.pending) {
            $actualGui=$snapshot.values
            if ($actualGui.phase -ceq 'Win32-calls-complete') { break }
          }
        }
        if ([MixelOrdinaryTokenFixture]::StartedStatus($guiPid).StartsWith('exited:')) { break }
        Start-Sleep -Milliseconds 100
      }
      Write-Host ("FIXTURE: $mode actual native owned ordinary Win32 desktop control: " + ($actualGui | ConvertTo-Json -Compress))
      Write-OwnedWindowDiagnostic $guiPid "native Win32 desktop fixture qualification ($mode)"
      if ($actualGui.phase -cne 'Win32-calls-complete') {
        $nativeStatus = [MixelOrdinaryTokenFixture]::StartedStatus($guiPid)
        # Only emit selected fields from events attributable to this exact
        # owned executable and PID, never arbitrary event text or raw argv.
        $events = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=$guiStartedAt.AddSeconds(-1)} -ErrorAction SilentlyContinue)
        foreach ($event in $events) {
          $data = @{}
          foreach ($entry in ([xml]$event.ToXml()).Event.EventData.Data) { $data[[string]$entry.Name] = [string]$entry.'#text' }
          if ($data.AppPath -cne $guiExecutable -or $data.ProcessId -notmatch '^(?:0x)?[0-9a-fA-F]+$') { continue }
          $eventPid = if ($data.ProcessId.StartsWith('0x')) { [Convert]::ToInt64($data.ProcessId.Substring(2),16) } else { [Convert]::ToInt64($data.ProcessId) }
          if ($eventPid -ne $guiPid) { continue }
          $selected = [pscustomobject]@{ eventId=$event.Id; pid=$eventPid; appName=$data.AppName; module=$data.ModuleName; exception=$data.ExceptionCode; offset=$data.FaultingOffset }
          Write-Host ('FIXTURE: actual owned native Application Error event: ' + ($selected | ConvertTo-Json -Compress))
        }
        if ($canonical) { throw "Canonical owned ordinary desktop fixture did not complete its Win32 control; phase=$($actualGui.phase), nativeStatus=$nativeStatus." }
        $desktopControls.Add([pscustomobject]@{ mode=$mode; passed=$false; pid=$guiPid; nativeStatus=$nativeStatus; result=$actualGui })
        Write-Host "DIAGNOSTIC: $mode control did not complete Win32 calls; phase=$($actualGui.phase), nativeStatus=$nativeStatus."
        continue
      }
      if ($actualGui.pid -ne [string]$guiPid -or $actualGui.desktop -cne $actualDesktop -or
          $actualGui.window -eq '0' -or $actualGui.visible -cne 'True' -or $actualGui.menu -eq '0') {
        if ($canonical) { throw "Canonical owned ordinary Win32 GUI fixture failed; windowError=$($actualGui.windowError), menuError=$($actualGui.menuError), desktop=$($actualGui.desktop)." }
        $desktopControls.Add([pscustomobject]@{ mode=$mode; passed=$false; pid=$guiPid; nativeStatus=[MixelOrdinaryTokenFixture]::StartedStatus($guiPid); result=$actualGui })
        Write-Host "DIAGNOSTIC: $mode control failed GUI checks; windowError=$($actualGui.windowError), menuError=$($actualGui.menuError), desktop=$($actualGui.desktop)."
        continue
      }
      $desktopControls.Add([pscustomobject]@{ mode=$mode; passed=$true; pid=$guiPid; nativeStatus=[MixelOrdinaryTokenFixture]::StartedStatus($guiPid); result=$actualGui })
      Write-Host "PASS: $mode exact owned standard-account launch and temporary SID-scoped desktop grant create an actual visible Win32 window and menu on the runner desktop."
      } catch {
        $trialFailure = $_
        throw
      } finally {
        $trialCleanup = [System.Collections.Generic.List[string]]::new()
        try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $trialCleanup.Add('Owned retained desktop trial process cleanup failed.') }
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
        if ($guiAccess) { try { $guiAccess.Dispose(); $guiAccess=$null } catch { $trialCleanup.Add('Owned desktop trial ACL restoration failed.') } }
        if ([MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $baselineAcls) { $trialCleanup.Add('Owned desktop trial did not restore the exact original station/desktop ACL hashes.') }
        if ($trialCleanup.Count -gt 0) {
          if ($trialFailure) { Write-Host ('FAIL: owned desktop trial cleanup additionally failed: ' + ($trialCleanup -join ' ')) }
          else { throw ($trialCleanup -join ' ') }
        } else { Write-Host "PASS: $mode owned desktop control restores the exact original station/desktop ACL hashes." }
      }
      }
      Write-Host ('FIXTURE: native same-account/profile/executable desktop A/B/A results: ' + ($desktopControls | ConvertTo-Json -Depth 5 -Compress))
      if ($desktopControls.Count -ne 3 -or -not $desktopControls[1].passed) { throw 'Canonical owned interactive desktop qualification did not pass.' }
      if (-not $desktopControls[0].passed -and -not $desktopControls[2].passed) {
        Write-Host 'PASS: same account/profile/executable A/B/A reproduces minimal-rights failure before and after canonical visible-window/menu success, with exact original ACL restoration between all trials.'
      } else {
        Write-Host 'DIAGNOSTIC: canonical visible-window/menu qualification passes, but minimal rights also passed in at least one trial; no ACL-causality claim is made.'
      }
      # A diagnostic attach failure can leave the original primary thread
      # suspended before Process.Path is available. Qualify exact retained
      # native-handle cleanup on that real standard-user state as well.
      if (Test-Path $guiResult) { Remove-Item $guiResult }
      $guiAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
      $guiPid = [MixelOrdinaryTokenFixture]::StartStandardUser($guiExecutable, $ownedUser, $ownedPassword, $true)
      $guiProcess = Get-Process -Id $guiPid
      if ([MixelOrdinaryTokenFixture]::Elevated($guiPid) -or $guiProcess.SessionId -ne (Get-Process -Id $PID).SessionId -or
          [MixelOrdinaryTokenFixture]::StartedStatus($guiPid) -cne 'still-running' -or (Test-Path $guiResult)) {
        throw 'Owned retained-handle cleanup control did not start as a genuine suspended ordinary process before Main.'
      }
      [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
      if ([MixelOrdinaryTokenFixture]::StartedStatus($guiPid) -cne 'exited:0x00000001' -or (Test-Path $guiResult)) {
        throw 'Exact retained native process handle did not prove termination of its suspended owned target.'
      }
      # Already-exited handles are intentionally safe to revisit without PID
      # lookup or reliance on a loaded executable path.
      [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
      [MixelOrdinaryTokenFixture]::CloseStartedObservations()
      $guiAccess.Dispose(); $guiAccess=$null
      if ([MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $baselineAcls) { throw 'Suspended process cleanup control did not restore its original desktop ACLs.' }
      Write-Host "PASS: exact retained native process handle terminates the actual owned suspended non-elevated target before Main, observes native exit 0x00000001 and safely revisits its exited handle; pid=$guiPid; original desktop ACL hashes restored."
    } catch {
      $guiFailure = $_
      throw
    } finally {
      $guiCleanup = [System.Collections.Generic.List[string]]::new()
      try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $guiCleanup.Add('Owned retained native desktop-control process cleanup failed.') }
      [MixelOrdinaryTokenFixture]::CloseStartedObservations()
      if ($guiAccess) { try { $guiAccess.Dispose() } catch { $guiCleanup.Add('Owned native desktop-control ACL restoration failed.') } }
      if ($guiProfile) {
        $profileRemoved = $false
        for ($attempt=0; $attempt -lt 5; $attempt++) {
          try { [MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid); $profileRemoved=$true; break }
          catch { if ($attempt -lt 4) { Start-Sleep -Seconds 2 } }
        }
        if (-not $profileRemoved) { $guiCleanup.Add('Owned native desktop-control profile cleanup failed.') }
      }
      if (Test-Path $guiRoot) { try { Remove-Item $guiRoot -Recurse -Force } catch { $guiCleanup.Add('Owned native desktop-control files cleanup failed.') } }
      if ($guiCleanup.Count -gt 0) {
        if ($guiFailure) { Write-Host ('FAIL: owned native desktop-control cleanup additionally failed: ' + ($guiCleanup -join ' ')) }
        else { throw ($guiCleanup -join ' ') }
      }
    }
  } finally {
    $ownedPassword = $null
    $random = $null
    if ($ownedSid) { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) }
  }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) { throw 'Owned account remained after its actual SID was removed.' }
  Write-Host 'PASS: actual standard account cleanup removes only the created SID and leaves no test account.'
}

foreach ($required in @(
    "`$initialHealth.attendedProof -cne ''",
    '$initialOwnsLease',
    "'verify absent prior foreground consent lease'",
    'globalLeasePresent=$initialLeasePresent',
    'Stop-OwnedCustomerProcesses',
    '$process.WaitForExit(10000)',
    '[MixelOrdinaryTokenFixture]::Elevated($main.Id)',
    '[MixelOrdinaryTokenFixture]::OwnsLease($main.Id)',
    '$warmLaunch.WaitForExit(60000)',
    '$started.Elapsed.TotalSeconds -lt 95',
    '$window -ne $originalWindow',
    '$initialHealth.incomingPid -ne $main.Id',
    'Assert-OwnedIncomingHealth $warmHealth',
    'Assert-OwnedIncomingHealth $currentHealth',
    'Assert-OwnedIncomingHealth $finalHealth',
    "-ArgumentList '--quick_support'",
    'actual customer QS portable launcher',
    'exact signed compiled QS argument (diagnostic)',
    '(-not $Portable -and -not $CompiledQuickSupportDiagnostic)',
    'Compiled diagnostic executable differs from the retained signed payload entry.',
    'desktop = CurrentDesktopPath()',
    '$main.SessionId -ne (Get-Process -Id $PID).SessionId',
    '$ordinaryProfileRoot = [MixelOrdinaryTokenFixture]::ProfilePath($main.Id)',
    "Join-Path `$ordinaryProfileRoot 'AppData/Roaming/Mixel-Remote'",
    "('mixel-ordinary-client-' +",
    "Write-OwnedWindowDiagnostic `$main.Id 'initial ordinary startup'",
    'ThreadDesktopName([uint32]$_.Id)',
    '$process.Modules | Select-Object -ExpandProperty ModuleName',
    'StartedStatus($ProcessId)',
    'canonical ? 0xf01ff : 0xc7',
    'if ($ownedSid) { try { Remove-LocalUser -SID',
    '$primaryFailure = $_',
    'if ($primaryFailure) { Write-Host',
    '-OrdinaryThenQuickSupport')) {
  if (-not $launchSource.Contains($required)) { throw "Ordinary-to-QS runtime proof lost a required native assertion: $required" }
}

function Assert-Fails([scriptblock]$Action, [string]$Scenario) {
  $failed = $false
  try { & $Action } catch { $failed = $true }
  if (-not $failed) { throw "Expected failure was accepted: $Scenario" }
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('mixel-windows-pipeline-' + [Guid]::NewGuid())
try {
  $build = Join-Path $temporary 'build'
  $release = Join-Path $build 'x64/runner/Release'
  $payload = Join-Path $temporary 'payload'
  New-Item -ItemType Directory -Force (Join-Path $release 'data/flutter_assets') | Out-Null
  foreach ($file in @('mixel-remote.exe', 'libmixel-remote.dll', 'flutter_windows.dll', 'data/icudtl.dat', 'data/flutter_assets/AssetManifest.json')) {
    Set-Content (Join-Path $release $file) -Value 'compiled-test-fixture' -Encoding utf8
  }
  New-Item -ItemType Directory -Force $payload | Out-Null
  Set-Content (Join-Path $payload 'stale.dll') -Value 'must-be-removed'
  & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload
  if (-not (Test-Path (Join-Path $payload 'Mixel-Remote.exe')) -or (Test-Path (Join-Path $payload 'stale.dll'))) {
    throw 'Payload preparation failed to normalize the main name or remove stale bytes.'
  }
  Write-Host 'PASS: payload preparation starts clean and preserves the compiled application and assets.'
  Remove-Item (Join-Path $release 'data/flutter_assets/AssetManifest.json')
  Assert-Fails { & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload } 'empty Flutter assets'
  Set-Content (Join-Path $release 'data/flutter_assets/AssetManifest.json') -Value '{}'
  Remove-Item (Join-Path $release 'libmixel-remote.dll')
  Assert-Fails { & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload } 'missing core DLL'
  Set-Content (Join-Path $release 'libmixel-remote.dll') -Value 'compiled-test-fixture'
  $duplicate = Join-Path $build 'other/Release'
  New-Item -ItemType Directory -Force $duplicate | Out-Null
  Set-Content (Join-Path $duplicate 'mixel-remote.exe') -Value 'ambiguous-build'
  Assert-Fails { & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload } 'ambiguous compiled application'
  Write-Host 'PASS: incomplete and ambiguous Windows builds fail closed.'
} finally {
  if (Test-Path $temporary) { Remove-Item $temporary -Recurse -Force }
}

. (Join-Path $PSScriptRoot 'support-runtime-probe-windows.ps1')
if ([MixelSupportIpcProbe]::ConsistentServerPid([uint32[]]@(42, 42, 42, 42, 42)) -ne 42) {
  throw 'Consistent incoming server PID was rejected.'
}
foreach ($pids in @(@(0, 0, 0, 0, 0), @(42, 42, 43, 42, 42), @(42, 42, 42, 42))) {
  Assert-Fails { [MixelSupportIpcProbe]::ConsistentServerPid([uint32[]]$pids) } 'missing or inconsistent incoming server PID'
}
if (-not ('MixelSupportProbeFixture' -as [type])) { Add-Type @'
using System;
using System.Collections.Concurrent;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Threading.Tasks;
public static class MixelSupportProbeFixture {
  static readonly ConcurrentQueue<string> Requests = new ConcurrentQueue<string>();
  public static string[] Requested() { return Requests.ToArray(); }
  public static Task Serve(string[] responses) {
    string previous;
    while (Requests.TryDequeue(out previous)) { }
    return Task.Run(() => {
      foreach (string response in responses) {
        using (var pipe = new NamedPipeServerStream("Mixel-Remote\\query", PipeDirection.InOut, 1, PipeTransmissionMode.Byte)) {
          pipe.WaitForConnection();
          int first = pipe.ReadByte();
          int headerLength = (first & 3) + 1;
          int header = first;
          for (int i = 1; i < headerLength; i++) header |= pipe.ReadByte() << (8 * i);
          var request = new byte[header >> 2];
          int offset = 0;
          while (offset < request.Length) {
            int count = pipe.Read(request, offset, request.Length - offset);
            if (count == 0) throw new IOException("Fixture request was truncated");
            offset += count;
          }
          Requests.Enqueue(Encoding.UTF8.GetString(request));
          if (response == "oversized") {
            int largeHeader = (65537 << 2) | 2;
            for (int i = 0; i < 3; i++) pipe.WriteByte((byte)(largeHeader >> (8 * i)));
          } else if (response == "truncated") {
            pipe.WriteByte(5); // Two-byte response header, second byte missing.
          } else {
            var body = Encoding.UTF8.GetBytes(response);
            int length = body.Length <= 63 ? 1 : 2;
            int replyHeader = (body.Length << 2) | (length - 1);
            for (int i = 0; i < length; i++) pipe.WriteByte((byte)(replyHeader >> (8 * i)));
            // Exercise actual ReadExact framing with fragmented response bytes.
            foreach (byte value in body) pipe.WriteByte(value);
          }
          pipe.Flush();
        }
      }
    });
  }
}
'@ }

$guard = '{"t":"Config","c":["mixel-support-invite-attended","attended-runtime-v2"]}'
$online = '{"t":"OnlineStatus","c":[1,true]}'
$options = '{"t":"Options","c":{"custom-rendezvous-server":"rs.mixel.ch","relay-server":"rs.mixel.ch","key":"OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo="}}'
$rendezvous = '{"t":"Config","c":["rendezvous_server","rs.mixel.ch:21116,rs.mixel.ch:21116"]}'
$device = '{"t":"Config","c":["id","123456789"]}'
$fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $options, $rendezvous, $device))
$health = Get-MixelSupportRuntimeHealth
$fixture.GetAwaiter().GetResult()
$expectedRequests = @(
  '{"t":"Config","c":["mixel-support-invite-attended",null]}',
  '{"t":"OnlineStatus","c":null}',
  '{"t":"Options","c":null}',
  '{"t":"Config","c":["rendezvous_server",null]}',
  '{"t":"Config","c":["id",null]}')
$actualRequests = [MixelSupportProbeFixture]::Requested()
if ($actualRequests.Count -ne $expectedRequests.Count) { throw 'IPC health probe issued an unexpected number of requests.' }
for ($index = 0; $index -lt $expectedRequests.Count; $index++) {
  if ($actualRequests[$index] -cne $expectedRequests[$index]) {
    throw 'IPC health probe must send only the exact framed read-only runtime queries.'
  }
}
if ($health.incomingPid -ne $PID -or -not $health.attendedReady -or -not $health.keyConfirmed -or $health.rendezvousState -ne 1 -or
    -not $health.brandedRelay -or $health.registeredId -ne '123456789' -or $health.rendezvousServer -ne 'rs.mixel.ch:21116') {
  throw 'Runtime IPC probe failed to parse a valid framed response.'
}
Write-Host 'PASS: actual named-pipe IPC decodes fragmented attended, online, branded relay/key, rendezvous and registered-ID responses.'
Write-Host 'PASS: actual framed health requests are read-only and never arm consent or change options.'
Write-Host 'PASS: all five actual named-pipe responses belong to the fixture server PID; missing or inconsistent PID attribution fails closed.'

foreach ($invalid in @('{"t":"OnlineStatus","c":[1,"false"]}', '{"t":"OnlineStatus","c":["1",true]}', '{"t":"OnlineStatus","c":[1]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'malformed online proof'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @(
    '{"t":"Options","c":[]}',
    $options.Replace('rs.mixel.ch', 'other.example'),
    $options.Replace('OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo=', 'wrong-public-key'),
    $options.Replace('"key":', '"mixel-support-invite-attended":"Y","key":'))) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'wrong/malformed relay defaults or persisted attended guard'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('{"t":"Config","c":["rendezvous_server","other.example:21116"]}', '{"t":"Config","c":["id","rs.mixel.ch:21116"]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $options, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'wrong/malformed rendezvous proof'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('{"t":"Config","c":["id",""]}', '{"t":"Config","c":["id",123456789]}', '{"t":"Config","c":["id","invalid id!"]}', '{"t":"Config","c":["id","123456789\n"]}', '{"t":"Config","c":["temporary-password","123456789"]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $options, $rendezvous, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'wrong/malformed registered ID'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('oversized', 'truncated')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($invalid))
  Assert-Fails { [MixelSupportIpcProbe]::Request('{"t":"OnlineStatus","c":null}') } 'invalid IPC frame'
  $fixture.GetAwaiter().GetResult()
}
Write-Host 'PASS: malformed online/relay/key/rendezvous/ID proofs, persisted consent, oversized frames and truncated frames fail closed.'
