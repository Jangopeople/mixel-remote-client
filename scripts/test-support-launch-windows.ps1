param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [switch]$Portable,
  [switch]$QuickSupport,
  [switch]$OrdinaryThenQuickSupport,
  [string]$ExpectedPayload
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'support-runtime-probe-windows.ps1')

if (-not ('MixelSupportWindowTest' -as [type])) { Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MixelSupportWindowTest {
  public delegate bool EnumCallback(IntPtr hwnd, IntPtr parameter);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback callback, IntPtr parameter);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int command);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder title, int maximum);
}
'@
}

if (-not ('MixelOrdinaryTokenFixture' -as [type])) { Add-Type @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
public static class MixelOrdinaryTokenFixture {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct StartupInfo {
    public int cb; public string reserved, desktop, title;
    public int x, y, xSize, ySize, xCount, yCount, fill, flags;
    public short show, reservedSize; public IntPtr reservedPointer, input, output, error;
  }
  [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process, thread; public uint pid, tid; }
  [StructLayout(LayoutKind.Sequential)] struct UnicodeString { public ushort length, maximum; public IntPtr buffer; }
  [StructLayout(LayoutKind.Sequential)] struct SystemHandle {
    public IntPtr obj, pid, handle; public uint access; public ushort creator, type;
    public uint attributes, reserved;
  }
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool DuplicateHandle(IntPtr source, IntPtr handle, IntPtr target, out IntPtr copy, uint access, bool inherit, uint options);
  [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
  [DllImport("advapi32.dll", SetLastError = true)] static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr data, int size, out int required);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessWithTokenW(IntPtr token, uint flags, string application, StringBuilder command, uint creation, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessWithLogonW(string user, string domain, string password, uint flags, string application, StringBuilder command, uint creation, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
  [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
  [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint thread);
  [DllImport("user32.dll", SetLastError = true)] static extern bool GetUserObjectSecurity(IntPtr obj, ref uint information, byte[] data, uint size, out uint required);
  [DllImport("user32.dll", SetLastError = true)] static extern bool SetUserObjectSecurity(IntPtr obj, ref uint information, byte[] data);
  [DllImport("userenv.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool DeleteProfileW(string sid, string path, string computer);
  [DllImport("ntdll.dll")] static extern int NtQuerySystemInformation(int kind, IntPtr data, int length, out int required);
  [DllImport("ntdll.dll")] static extern int NtQueryObject(IntPtr handle, int kind, IntPtr data, int length, out int required);

  static bool ElevatedToken(IntPtr token) {
    IntPtr data = Marshal.AllocHGlobal(4);
    try { int required; if (!GetTokenInformation(token, 20, data, 4, out required)) throw new Win32Exception(); return Marshal.ReadInt32(data) != 0; }
    finally { Marshal.FreeHGlobal(data); }
  }
  public static bool Elevated(int pid) {
    IntPtr process = OpenProcess(0x1000, false, pid), token = IntPtr.Zero;
    if (process == IntPtr.Zero) throw new Win32Exception();
    try { if (!OpenProcessToken(process, 8, out token)) throw new Win32Exception(); return ElevatedToken(token); }
    finally { if (token != IntPtr.Zero) CloseHandle(token); CloseHandle(process); }
  }
  static StartupInfo Startup() { return new StartupInfo { cb = Marshal.SizeOf(typeof(StartupInfo)), desktop = "winsta0\\default" }; }
  static int Started(ProcessInfo process) { CloseHandle(process.thread); CloseHandle(process.process); return checked((int)process.pid); }
  public static int StartLinkedToken(string executable) {
    IntPtr token = IntPtr.Zero, linked = IntPtr.Zero, data = Marshal.AllocHGlobal(IntPtr.Size);
    try {
      if (!OpenProcessToken(GetCurrentProcess(), 10, out token)) throw new Win32Exception();
      int required;
      if (!GetTokenInformation(token, 19, data, IntPtr.Size, out required)) return 0;
      linked = Marshal.ReadIntPtr(data);
      if (ElevatedToken(linked)) return 0;
      StartupInfo startup = Startup(); ProcessInfo process;
      if (!CreateProcessWithTokenW(linked, 1, executable, new StringBuilder("\"" + executable + "\""), 0, IntPtr.Zero, System.IO.Path.GetDirectoryName(executable), ref startup, out process)) return 0;
      return Started(process);
    } finally { if (linked != IntPtr.Zero) CloseHandle(linked); if (token != IntPtr.Zero) CloseHandle(token); Marshal.FreeHGlobal(data); }
  }
  public static int StartStandardUser(string executable, string username, string password) {
    StartupInfo startup = Startup(); ProcessInfo process;
    if (!CreateProcessWithLogonW(username, ".", password, 1, executable, new StringBuilder("\"" + executable + "\""), 0, IntPtr.Zero, System.IO.Path.GetDirectoryName(executable), ref startup, out process)) throw new Win32Exception();
    return Started(process);
  }
  static byte[] Descriptor(IntPtr obj) {
    uint information = 4, required;
    GetUserObjectSecurity(obj, ref information, null, 0, out required);
    if (required == 0 || required > 65536) throw new Win32Exception();
    byte[] result = new byte[required];
    if (!GetUserObjectSecurity(obj, ref information, result, required, out required)) throw new Win32Exception();
    return result;
  }
  static void Apply(IntPtr obj, byte[] descriptor) { uint information = 4; if (!SetUserObjectSecurity(obj, ref information, descriptor)) throw new Win32Exception(); }
  static byte[] Grant(IntPtr obj, SecurityIdentifier sid, int access) {
    byte[] original = Descriptor(obj); var descriptor = new RawSecurityDescriptor(original, 0);
    if (descriptor.DiscretionaryAcl != null) {
      descriptor.DiscretionaryAcl.InsertAce(descriptor.DiscretionaryAcl.Count, new CommonAce(AceFlags.None, AceQualifier.AccessAllowed, access, sid, false, null));
      byte[] updated = new byte[descriptor.BinaryLength]; descriptor.GetBinaryForm(updated, 0); Apply(obj, updated);
    }
    return original;
  }
  public sealed class DesktopAccess : IDisposable {
    readonly IntPtr station = GetProcessWindowStation(), desktop = GetThreadDesktop(GetCurrentThreadId());
    byte[] stationOriginal, desktopOriginal;
    public DesktopAccess(string sid) {
      var identity = new SecurityIdentifier(sid);
      stationOriginal = Grant(station, identity, 0x327);
      try { desktopOriginal = Grant(desktop, identity, 0xc3); } catch { Apply(station, stationOriginal); stationOriginal = null; throw; }
    }
    public void Dispose() {
      Exception failure = null;
      if (desktopOriginal != null) { try { Apply(desktop, desktopOriginal); } catch (Exception error) { failure = error; } desktopOriginal = null; }
      if (stationOriginal != null) { try { Apply(station, stationOriginal); } catch (Exception error) { failure = error; } stationOriginal = null; }
      if (failure != null) throw failure;
    }
  }
  public static void RemoveProfile(string sid) { if (!DeleteProfileW(sid, null, null) && Marshal.GetLastWin32Error() != 2) throw new Win32Exception(); }
  public static bool IsLeaseName(string name) {
    return name == "\\BaseNamedObjects\\Mixel-Remote-Attended-Runtime-v2" || name == "\\Sessions\\0\\BaseNamedObjects\\Mixel-Remote-Attended-Runtime-v2";
  }
  static string ObjectText(IntPtr handle, int kind) {
    IntPtr data = Marshal.AllocHGlobal(65536);
    try { int required; if (NtQueryObject(handle, kind, data, 65536, out required) != 0) return null; var text = (UnicodeString)Marshal.PtrToStructure(data, typeof(UnicodeString)); return text.buffer == IntPtr.Zero ? null : Marshal.PtrToStringUni(text.buffer, text.length / 2); }
    finally { Marshal.FreeHGlobal(data); }
  }
  public static bool OwnsLease(int pid) {
    IntPtr process = OpenProcess(0x40, false, pid);
    if (process == IntPtr.Zero) throw new Win32Exception();
    IntPtr data = IntPtr.Zero;
    try {
      int size = 65536, required, status;
      do {
        if (data != IntPtr.Zero) Marshal.FreeHGlobal(data);
        data = Marshal.AllocHGlobal(size); status = NtQuerySystemInformation(64, data, size, out required);
        if (status == unchecked((int)0xc0000004)) size = Math.Max(size * 2, required + 4096);
        else if (status != 0) throw new InvalidOperationException("Cannot inspect actual process lease handles");
        if (size > 67108864) throw new InvalidOperationException("Process handle snapshot exceeds bound");
      } while (status != 0);
      long count = Marshal.ReadIntPtr(data).ToInt64(); int entrySize = Marshal.SizeOf(typeof(SystemHandle));
      if (count < 0 || count > (size - 2 * IntPtr.Size) / entrySize) throw new InvalidOperationException("Malformed process handle snapshot");
      for (long index = 0; index < count; index++) {
        var entry = (SystemHandle)Marshal.PtrToStructure(IntPtr.Add(data, checked(2 * IntPtr.Size + (int)index * entrySize)), typeof(SystemHandle));
        if (entry.pid.ToInt64() != pid || entry.access != 0x100000) continue;
        IntPtr copy;
        if (!DuplicateHandle(process, entry.handle, GetCurrentProcess(), out copy, 0, false, 2)) continue;
        try { if (ObjectText(copy, 2) == "Event" && IsLeaseName(ObjectText(copy, 1))) return true; }
        finally { CloseHandle(copy); }
      }
      return false;
    } finally { if (data != IntPtr.Zero) Marshal.FreeHGlobal(data); CloseHandle(process); }
  }
}
'@ }

$executablePath = (Resolve-Path $Executable).Path
$runtimePath = $executablePath
if ($Portable) {
  if (-not $ExpectedPayload) { throw 'Portable runtime verification requires the expected signed payload.' }
  $runtimePath = Join-Path $env:LOCALAPPDATA 'mixel-remote/Mixel-Remote.exe'
}
$token = 'inv_00000000-0000-0000-0000-000000000002'
# Windows protocol activation can canonicalize an authority-only URI by adding
# this slash. Exercise the same semantic support URI the Dart parser accepts.
$uri = "mixel-remote://support/?invite=$token&apikey=synthetic-invalid-public-key-000000000000"
$before = @(Get-Process | Select-Object -ExpandProperty Id)
$main = $null
$ordinaryRoot = $null
$ownedUser = $null
$ownedSid = $null
$desktopAccess = $null
$baselineIncomingPid = $null
$fixtureStage = 'customer launch'
$primaryFailure = $null
$launchScenario = if ($QuickSupport) { 'QuickSupport double-click' } else { 'support URI launch' }

function Start-CustomerApp {
  if ($QuickSupport) { return Start-Process -FilePath $executablePath -PassThru }
  return Start-Process -FilePath $executablePath -ArgumentList $uri -PassThru
}

function Find-MainWindow([int]$ProcessId) {
  $found = [System.Collections.Generic.List[IntPtr]]::new()
  [MixelSupportWindowTest]::EnumWindows({
    param($window, $parameter)
    [uint32]$ownerId = 0
    [void][MixelSupportWindowTest]::GetWindowThreadProcessId($window, [ref]$ownerId)
    if ($ownerId -eq $ProcessId) {
      $title = [System.Text.StringBuilder]::new(512)
      [void][MixelSupportWindowTest]::GetWindowText($window, $title, 512)
      if ($title.ToString() -eq 'Mixel-Remote') { $found.Add($window) }
    }
    return $true
  }, [IntPtr]::Zero) | Out-Null
  return $found.ToArray()
}

function Wait-VisibleMain([int]$ProcessId, [string]$Scenario) {
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  while ([DateTime]::UtcNow -lt $deadline) {
    foreach ($window in (Find-MainWindow $ProcessId)) {
      if ([MixelSupportWindowTest]::IsWindowVisible($window) -and
          -not [MixelSupportWindowTest]::IsIconic($window)) {
        return $window
      }
    }
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
      throw "$Scenario failed: main app exited before showing customer UI."
    }
    Start-Sleep -Milliseconds 250
  }
  throw "$Scenario failed: customer app window did not become visible on the Windows runner desktop."
}

function Wait-PortableMain {
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  while ([DateTime]::UtcNow -lt $deadline) {
    foreach ($candidate in (Get-Process | Where-Object {
      $before -notcontains $_.Id -and $_.Path -and
      $_.Path.Equals($runtimePath, [StringComparison]::OrdinalIgnoreCase)
    })) {
      if (@(Find-MainWindow $candidate.Id).Count -gt 0) { return $candidate }
    }
    Start-Sleep -Milliseconds 250
  }
  throw 'Portable customer launcher did not extract and start the customer app.'
}

function Assert-OwnedIncomingHealth($Health) {
  if ($before -contains $Health.incomingPid) { throw 'Runtime proof reached an incoming endpoint that predates this owned scenario.' }
  $incoming = Get-Process -Id $Health.incomingPid
  $allowedPaths = @($runtimePath)
  if ($ordinaryRoot) { $allowedPaths += (Join-Path $ordinaryRoot 'Mixel-Remote.exe') }
  $matched = $false
  foreach ($path in $allowedPaths) {
    if ($incoming.Path -and $incoming.Path.Equals($path, [StringComparison]::OrdinalIgnoreCase)) { $matched = $true; break }
  }
  if (-not $matched) { throw 'Actual named-pipe server PID is not running the exact signed payload owned by this scenario.' }
  if ($null -ne $baselineIncomingPid -and $Health.incomingPid -ne $baselineIncomingPid) {
    throw 'Warm launch changed the incoming server PID rather than preserving the original owned endpoint.'
  }
}

try {
  if ($OrdinaryThenQuickSupport) {
    if (-not $QuickSupport -or -not $Portable -or -not $ExpectedPayload) {
      throw 'Ordinary-to-QuickSupport proof requires the actual portable QS launcher and exact signed payload.'
    }
    $ordinaryRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-ordinary-qs-' + [Guid]::NewGuid().ToString('N'))
    $fixtureStage = 'copy signed ordinary payload'
    New-Item -ItemType Directory $ordinaryRoot | Out-Null
    Copy-Item (Join-Path (Resolve-Path $ExpectedPayload).Path '*') $ordinaryRoot -Recurse
    $ordinaryExecutable = Join-Path $ordinaryRoot 'Mixel-Remote.exe'
    & (Join-Path $PSScriptRoot 'verify-windows-payload.ps1') -Payload $ordinaryRoot
    if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) {
      $fixtureStage = 'start current ordinary token'
      $main = Start-Process -FilePath $ordinaryExecutable -PassThru
    } else {
      $fixtureStage = 'start linked ordinary token'
      $ordinaryPid = [MixelOrdinaryTokenFixture]::StartLinkedToken($ordinaryExecutable)
      if ($ordinaryPid -eq 0) {
        if ($env:GITHUB_ACTIONS -cne 'true') { throw 'Disposable standard-user fallback is restricted to an isolated GitHub Actions runner.' }
        $ownedUser = 'mixelqs' + [Guid]::NewGuid().ToString('N').Substring(0, 10)
        $random = [byte[]]::new(24)
        [Security.Cryptography.RandomNumberGenerator]::Fill($random)
        $ownedPassword = 'aA!9' + [Convert]::ToBase64String($random)
        $fixtureStage = 'create owned standard account'
        $account = New-LocalUser -Name $ownedUser -Password (ConvertTo-SecureString $ownedPassword -AsPlainText -Force) -AccountNeverExpires -PasswordNeverExpires -Description 'Owned disposable Mixel QuickSupport runtime fixture'
        $ownedSid = $account.SID.Value
        $fixtureStage = 'add owned standard account to users'
        Add-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')) -Member $ownedUser
        $fixtureStage = 'grant owned standard desktop access'
        $desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
        $fixtureStage = 'start owned standard GUI'
        try { $ordinaryPid = [MixelOrdinaryTokenFixture]::StartStandardUser($ordinaryExecutable, $ownedUser, $ownedPassword) }
        finally { $ownedPassword = $null }
      }
      $main = Get-Process -Id $ordinaryPid
    }
    $fixtureStage = 'verify actual ordinary process token'
    if ([MixelOrdinaryTokenFixture]::Elevated($main.Id)) { throw 'Ordinary startup still uses an elevated token and could silently auto-enter QuickSupport.' }
  } else {
    $main = Start-CustomerApp
  }
  # Let FFI initialization and the cold URI handler finish before checking the
  # stable window state; the original bug hid an initially-created main window.
  Start-Sleep -Seconds 15
  if ($Portable -and -not $OrdinaryThenQuickSupport) {
    $main = Wait-PortableMain
    $expectedRoot = (Resolve-Path $ExpectedPayload).Path
    $extractedRoot = Split-Path $runtimePath -Parent
    foreach ($file in (Get-ChildItem $expectedRoot -Recurse -File)) {
      $relative = [IO.Path]::GetRelativePath($expectedRoot, $file.FullName)
      $extracted = Join-Path $extractedRoot $relative
      if (-not (Test-Path $extracted) -or
          (Get-FileHash $file.FullName -Algorithm SHA256).Hash -ne (Get-FileHash $extracted -Algorithm SHA256).Hash) {
        throw "Portable customer launcher extracted different bytes: $relative"
      }
    }
    & (Join-Path $PSScriptRoot 'verify-windows-payload.ps1') -Payload $extractedRoot
    Write-Host 'PASS: portable customer launcher extracts the exact signed application and assets.'
  }
  $window = Wait-VisibleMain $main.Id "Cold $launchScenario"
  Start-Sleep -Seconds 3
  if (-not [MixelSupportWindowTest]::IsWindowVisible($window)) {
    throw "Cold $launchScenario failed: main app became hidden after initialization."
  }
  Write-Host "PASS: cold $launchScenario shows customer app."
  if ($OrdinaryThenQuickSupport) {
    $initialHealth = Get-MixelSupportRuntimeHealth
    Assert-OwnedIncomingHealth $initialHealth
    if ($initialHealth.incomingPid -ne $main.Id) { throw 'Ordinary positive control is not querying its actual in-process native incoming server.' }
    $baselineIncomingPid = $initialHealth.incomingPid
    if ($initialHealth.attendedProof -cne '' -or [MixelOrdinaryTokenFixture]::OwnsLease($main.Id)) {
      throw 'Ordinary-to-QS positive control is already guarded; refusing a manufactured transition proof.'
    }
    Write-Host 'PASS: actual ordinary non-elevated signed GUI starts genuinely unguarded, with empty read-only IPC guard and no foreground lease.'
  } else {
    $coldHealth = Wait-MixelSupportRuntimeHealth "Cold $launchScenario" -RequireOnline
    Assert-OwnedIncomingHealth $coldHealth
    $baselineIncomingPid = $coldHealth.incomingPid
  }

  [void][MixelSupportWindowTest]::ShowWindow($window, 6)
  Start-Sleep -Seconds 1
  if (-not [MixelSupportWindowTest]::IsIconic($window)) {
    throw 'Warm-launch setup failed: runner could not minimize the customer app.'
  }
  $originalWindow = $window
  $warmLaunch = Start-CustomerApp
  $window = Wait-VisibleMain $main.Id "Warm $launchScenario"
  if ($window -ne $originalWindow) { throw 'Warm launch restored a different HWND.' }
  Write-Host "PASS: warm $launchScenario restores visible customer app."
  $warmHealth = Wait-MixelSupportRuntimeHealth "Warm $launchScenario" -RequireOnline
  Assert-OwnedIncomingHealth $warmHealth
  if ($OrdinaryThenQuickSupport) {
    if (-not $warmLaunch.WaitForExit(60000)) { throw 'Transient QS launcher did not exit after dispatching to the existing ordinary GUI.' }
    if (-not [MixelOrdinaryTokenFixture]::OwnsLease($main.Id)) { throw 'The original foreground GUI does not own the actual kernel consent lease.' }
    $started = [Diagnostics.Stopwatch]::StartNew()
    while ($started.Elapsed.TotalSeconds -lt 95) {
      $currentHealth = Get-MixelSupportRuntimeHealth
      Assert-OwnedIncomingHealth $currentHealth
      if (-not $currentHealth.attendedReady -or -not [MixelOrdinaryTokenFixture]::OwnsLease($main.Id) -or
          -not [MixelSupportWindowTest]::IsWindowVisible($originalWindow) -or
          [MixelSupportWindowTest]::IsIconic($originalWindow)) {
        throw 'Ordinary-to-QS lost the same foreground HWND, actual owned kernel lease or v2 consent after the launch process exited.'
      }
      Start-Sleep -Seconds 5
    }
    $finalHealth = Get-MixelSupportRuntimeHealth
    Assert-OwnedIncomingHealth $finalHealth
    if (-not $finalHealth.attendedReady -or -not [MixelOrdinaryTokenFixture]::OwnsLease($main.Id)) { throw 'Foreground consent did not outlive the 90s memory deadline.' }
    Write-Host "PASS: genuine ordinary GUI -> actual QS restores the same PID/HWND, and that foreground PID owns the kernel consent lease with v2 readiness after $([int]$started.Elapsed.TotalSeconds)s beyond the 90s memory deadline; transient launcher exited."
  }

  foreach ($logRoot in @(
      (Join-Path $env:APPDATA 'Mixel-Remote'),
      (Join-Path $env:LOCALAPPDATA 'Mixel-Remote'),
      (Join-Path $env:APPDATA 'MixelRemote'))) {
    if (Test-Path $logRoot) {
      $leaks = Get-ChildItem $logRoot -Recurse -Filter '*.log' -File |
        Select-String -SimpleMatch $token
      if ($leaks) { throw 'Support invite bearer appeared in app logs.' }
    }
  }
  Write-Host 'PASS: synthetic support invite bearer absent from app log files.'
} catch {
  $primaryFailure = $_
  # Report fixture attribution without copying the memory-only password or
  # arbitrary app arguments. Rethrow the original error after owned cleanup.
  Write-Host "FAIL: owned runtime stage '$fixtureStage'; errorId=$($_.FullyQualifiedErrorId); exceptionType=$($_.Exception.GetType().FullName)."
  throw
} finally {
  # Only stop processes newly started from this runner-owned build directory.
  Get-Process | Where-Object {
    $before -notcontains $_.Id -and $_.Path -and
    ($_.Path.StartsWith((Split-Path $executablePath -Parent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
     ($Portable -and $_.Path.StartsWith((Split-Path $runtimePath -Parent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) -or
     ($ordinaryRoot -and $_.Path.StartsWith($ordinaryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)))
  } | Stop-Process -Force -ErrorAction SilentlyContinue
  if ($ordinaryRoot -or $ownedUser -or $desktopAccess) {
    $cleanupErrors = [System.Collections.Generic.List[string]]::new()
    Start-Sleep -Seconds 2
    if ($desktopAccess) { try { $desktopAccess.Dispose() } catch { $cleanupErrors.Add('Owned desktop ACL restoration failed.') } }
    if ($ownedSid) {
      $profileRemoved = $false
      for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try { [MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid); $profileRemoved = $true; break }
        catch { if ($attempt -lt 4) { Start-Sleep -Seconds 2 } }
      }
      if (-not $profileRemoved) { $cleanupErrors.Add('Owned test-user profile cleanup failed.') }
    }
    # A requested account name does not prove creation succeeded. Only delete
    # the actual account whose returned SID this scenario recorded.
    if ($ownedSid) { try { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) } catch { $cleanupErrors.Add('Owned standard local account cleanup failed.') } }
    if ($ordinaryRoot) { try { Remove-Item $ordinaryRoot -Recurse -Force } catch { $cleanupErrors.Add('Owned ordinary payload cleanup failed.') } }
    if ($cleanupErrors.Count -gt 0) {
      if ($primaryFailure) { Write-Host ('FAIL: owned cleanup additionally failed: ' + ($cleanupErrors -join ' ')) }
      else { throw ($cleanupErrors -join ' ') }
    }
  }
}

if ($QuickSupport -and -not $OrdinaryThenQuickSupport) {
  & $PSCommandPath -Executable $Executable -Portable -QuickSupport -ExpectedPayload $ExpectedPayload -OrdinaryThenQuickSupport
}
