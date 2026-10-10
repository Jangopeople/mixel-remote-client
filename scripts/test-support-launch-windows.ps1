param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [switch]$Portable,
  [switch]$QuickSupport,
  [switch]$OrdinaryThenQuickSupport,
  [switch]$CompiledQuickSupportDiagnostic,
  [switch]$NativeCrashDiagnostic,
  [string]$ExpectedPayload
)
$ErrorActionPreference = 'Stop'
if ($NativeCrashDiagnostic -and
    (-not $CompiledQuickSupportDiagnostic -or -not $OrdinaryThenQuickSupport -or $env:GITHUB_ACTIONS -cne 'true')) {
  throw 'Native crash capture is restricted to the explicit compiled diagnostic on an isolated GitHub Actions runner.'
}
if ($CompiledQuickSupportDiagnostic -and
    ($Portable -or -not $QuickSupport -or -not $OrdinaryThenQuickSupport -or -not $ExpectedPayload)) {
  throw 'Compiled QS diagnosis requires the ordinary-to-QS scenario and exact signed payload, without a portable outer launcher.'
}
if ($QuickSupport -and -not $Portable -and -not $CompiledQuickSupportDiagnostic) {
  throw 'Customer QuickSupport proof requires the actual portable launcher; compiled diagnosis must be explicitly selected.'
}
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
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
  [DllImport("kernel32.dll", SetLastError = true)] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
  [DllImport("kernel32.dll", SetLastError = true)] static extern uint ResumeThread(IntPtr thread);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool CheckRemoteDebuggerPresent(IntPtr process, out bool present);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool TerminateProcess(IntPtr process, uint code);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool DuplicateHandle(IntPtr source, IntPtr handle, IntPtr target, out IntPtr copy, uint access, bool inherit, uint options);
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr OpenEventW(uint access, bool inherit, string name);
  [DllImport("advapi32.dll", SetLastError = true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
  [DllImport("advapi32.dll", SetLastError = true)] static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr data, int size, out int required);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool LogonUserW(string user, string domain, string password, uint logonType, uint provider, out IntPtr token);
  [DllImport("advapi32.dll", SetLastError = true)] static extern bool ImpersonateLoggedOnUser(IntPtr token);
  [DllImport("advapi32.dll", SetLastError = true)] static extern bool RevertToSelf();
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessWithTokenW(IntPtr token, uint flags, string application, StringBuilder command, uint creation, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
  [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool CreateProcessWithLogonW(string user, string domain, string password, uint flags, string application, StringBuilder command, uint creation, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
  [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
  [DllImport("user32.dll", SetLastError = true)] static extern IntPtr GetThreadDesktop(uint thread);
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool GetUserObjectInformationW(IntPtr obj, int kind, StringBuilder text, uint size, out uint required);
  [DllImport("user32.dll", SetLastError = true)] static extern bool GetUserObjectSecurity(IntPtr obj, ref uint information, byte[] data, uint size, out uint required);
  [DllImport("user32.dll", SetLastError = true)] static extern bool SetUserObjectSecurity(IntPtr obj, ref uint information, byte[] data);
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr OpenWindowStationW(string name, bool inherit, uint access);
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr OpenDesktopW(string name, uint flags, bool inherit, uint access);
  [DllImport("user32.dll")] static extern bool CloseWindowStation(IntPtr station);
  [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
  [DllImport("userenv.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool DeleteProfileW(string sid, string path, string computer);
  [DllImport("userenv.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool GetUserProfileDirectoryW(IntPtr token, StringBuilder path, ref uint size);
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
  public static string ProfilePath(int pid) {
    IntPtr process = OpenProcess(0x1000, false, pid), token = IntPtr.Zero;
    if (process == IntPtr.Zero) throw new Win32Exception();
    try {
      if (!OpenProcessToken(process, 8, out token)) throw new Win32Exception();
      var path = new StringBuilder(512); uint size = checked((uint)path.Capacity);
      if (!GetUserProfileDirectoryW(token, path, ref size)) throw new Win32Exception();
      return path.ToString();
    } finally { if (token != IntPtr.Zero) CloseHandle(token); CloseHandle(process); }
  }
  static string UserObjectName(IntPtr obj) {
    var name = new StringBuilder(512); uint required;
    if (obj == IntPtr.Zero || !GetUserObjectInformationW(obj, 2, name, checked((uint)name.Capacity * 2), out required)) throw new Win32Exception();
    string value = name.ToString();
    if (value.Length == 0 || value.IndexOf('\\') >= 0) throw new InvalidOperationException("Invalid actual desktop object name");
    return value;
  }
  public static string CurrentDesktopPath() {
    return UserObjectName(GetProcessWindowStation()) + "\\" + UserObjectName(GetThreadDesktop(GetCurrentThreadId()));
  }
  public static string ThreadDesktopName(uint thread) { return UserObjectName(GetThreadDesktop(thread)); }
  public static System.Collections.Generic.Dictionary<string, int> ActualStandardDesktopAccess(string user, string password, string expectedSid) {
    string stationName = UserObjectName(GetProcessWindowStation()), desktopName = UserObjectName(GetThreadDesktop(GetCurrentThreadId()));
    IntPtr token;
    if (!LogonUserW(user, ".", password, 2, 0, out token)) throw new Win32Exception();
    bool impersonated = false;
    try {
      using (var identity = new WindowsIdentity(token)) {
        if (identity.User.Value != expectedSid) throw new InvalidOperationException("Desktop access probe token does not own the created SID");
      }
      if (!ImpersonateLoggedOnUser(token)) throw new Win32Exception();
      impersonated = true;
      var result = new System.Collections.Generic.Dictionary<string, int>();
      foreach (uint access in new uint[] { 0x327, 0x37f, 0x20000 }) {
        IntPtr station = OpenWindowStationW(stationName, false, access);
        result.Add("station:0x" + access.ToString("x"), station == IntPtr.Zero ? Marshal.GetLastWin32Error() : 0);
        if (station != IntPtr.Zero) CloseWindowStation(station);
      }
      foreach (uint access in new uint[] { 0xc7, 0x1ff, 0x20000 }) {
        IntPtr desktop = OpenDesktopW(desktopName, 0, false, access);
        result.Add("desktop:0x" + access.ToString("x"), desktop == IntPtr.Zero ? Marshal.GetLastWin32Error() : 0);
        if (desktop != IntPtr.Zero) CloseDesktop(desktop);
      }
      return result;
    } finally {
      if (impersonated && !RevertToSelf()) throw new Win32Exception();
      CloseHandle(token);
    }
  }
  static StartupInfo Startup() { return new StartupInfo { cb = Marshal.SizeOf(typeof(StartupInfo)), desktop = CurrentDesktopPath() }; }
  static readonly System.Collections.Generic.Dictionary<int, IntPtr> StartedProcesses = new System.Collections.Generic.Dictionary<int, IntPtr>();
  static readonly System.Collections.Generic.Dictionary<int, IntPtr> SuspendedThreads = new System.Collections.Generic.Dictionary<int, IntPtr>();
  static int Started(ProcessInfo process, bool suspended = false) {
    if (!suspended) CloseHandle(process.thread);
    int pid = checked((int)process.pid);
    if (StartedProcesses.ContainsKey(pid)) { if (suspended) CloseHandle(process.thread); CloseHandle(process.process); throw new InvalidOperationException("Owned process observation PID collision"); }
    StartedProcesses.Add(pid, process.process);
    if (suspended) SuspendedThreads.Add(pid, process.thread);
    return pid;
  }
  public static bool ActualOwnedDebuggerAttached(int pid) {
    IntPtr handle; bool attached;
    if (!StartedProcesses.TryGetValue(pid, out handle)) throw new InvalidOperationException("Debugger target is not owned");
    if (WaitForSingleObject(handle, 0) == 0) return false;
    if (!CheckRemoteDebuggerPresent(handle, out attached)) {
      int error = Marshal.GetLastWin32Error();
      if (WaitForSingleObject(handle, 0) == 0) return false;
      throw new Win32Exception(error);
    }
    return attached;
  }
  public static int ResumeOwnedPrimaryThread(int pid) {
    IntPtr thread, process;
    if (!SuspendedThreads.TryGetValue(pid, out thread) || !StartedProcesses.TryGetValue(pid, out process)) throw new InvalidOperationException("Primary thread and process observations are not owned");
    try {
      uint wait = WaitForSingleObject(process, 0);
      if (wait == 0) return -1; // The exact target can exit after capture/detach.
      if (wait != 0x102) throw new Win32Exception();
      uint previous = ResumeThread(thread);
      if (previous == 0xffffffff) {
        int error = Marshal.GetLastWin32Error();
        if (WaitForSingleObject(process, 0) == 0) return -1;
        throw new Win32Exception(error);
      }
      // A debugger may already have released CREATE_SUSPENDED. The documented
      // previous count 0 is then a harmless no-op; 1 releases our one count.
      // Never repeatedly resume an unexpected additional suspension.
      if (previous > 1) throw new InvalidOperationException("Unexpected owned primary thread suspension count");
      return checked((int)previous);
    } finally { SuspendedThreads.Remove(pid); CloseHandle(thread); }
  }
  public static void StopOwnedStartedProcesses() {
    foreach (IntPtr handle in StartedProcesses.Values) {
      uint wait = WaitForSingleObject(handle, 0);
      if (wait == 0) continue;
      if (wait != 0x102) throw new Win32Exception();
      if (!TerminateProcess(handle, 1)) {
        int error = Marshal.GetLastWin32Error();
        // An independently exiting process can signal this same retained
        // handle between the zero-time wait and TerminateProcess. Windows
        // documents ERROR_ACCESS_DENIED for termination after process exit.
        if (error != 5 || WaitForSingleObject(handle, 10000) != 0) throw new Win32Exception(error);
        continue;
      }
      if (WaitForSingleObject(handle, 10000) != 0) throw new InvalidOperationException("Owned native process termination not observed");
    }
  }
  public static string StartedStatus(int pid) {
    IntPtr handle;
    if (!StartedProcesses.TryGetValue(pid, out handle)) return "not-native-fixture-started";
    uint wait = WaitForSingleObject(handle, 0);
    if (wait == 0x102) return "still-running";
    if (wait != 0) throw new Win32Exception();
    // Read the exit code only after this retained process handle signals.
    // A code read before the wait can capture STILL_ACTIVE just before exit.
    uint code;
    if (!GetExitCodeProcess(handle, out code)) throw new Win32Exception();
    return "exited:0x" + code.ToString("x8");
  }
  public static void CloseStartedObservations() {
    foreach (IntPtr thread in SuspendedThreads.Values) CloseHandle(thread);
    SuspendedThreads.Clear();
    foreach (IntPtr handle in StartedProcesses.Values) CloseHandle(handle);
    StartedProcesses.Clear();
  }
  // The MiscInfo stream must independently attribute the private WER dump to
  // the actual owned PID; a filename alone is not sufficient evidence.
  public static int DumpProcessId(string path) {
    using (var file = System.IO.File.OpenRead(path)) using (var reader = new System.IO.BinaryReader(file)) {
      if (file.Length < 32 || reader.ReadUInt32() != 0x504d444d) throw new InvalidOperationException("Invalid minidump header");
      reader.ReadUInt32(); uint count = reader.ReadUInt32(), directory = reader.ReadUInt32();
      if (count > 1024 || (long)directory + count * 12L > file.Length) throw new InvalidOperationException("Invalid minidump directory");
      for (uint index = 0; index < count; index++) {
        file.Position = directory + index * 12L;
        uint kind = reader.ReadUInt32(), size = reader.ReadUInt32(), offset = reader.ReadUInt32();
        if (kind != 15) continue;
        if (size < 12 || (long)offset + size > file.Length) throw new InvalidOperationException("Invalid minidump process stream");
        file.Position = offset; uint declared = reader.ReadUInt32(), flags = reader.ReadUInt32();
        if (declared < 12 || declared > size || (flags & 1) == 0) throw new InvalidOperationException("Minidump has no actual process ID");
        return checked((int)reader.ReadUInt32());
      }
      throw new InvalidOperationException("Minidump process attribution missing");
    }
  }
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
    return StartStandardUser(executable, username, password, false);
  }
  public static int StartStandardUser(string executable, string username, string password, bool diagnosticSuspended) {
    StartupInfo startup = Startup(); ProcessInfo process;
    if (!CreateProcessWithLogonW(username, ".", password, 1, executable, new StringBuilder("\"" + executable + "\""), diagnosticSuspended ? 4U : 0U, IntPtr.Zero, System.IO.Path.GetDirectoryName(executable), ref startup, out process)) throw new Win32Exception();
    return Started(process, diagnosticSuspended);
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
  public static string DesktopAclHashes() {
    using (var hash = System.Security.Cryptography.SHA256.Create()) {
      return Convert.ToBase64String(hash.ComputeHash(Descriptor(GetProcessWindowStation()))) + ":" + Convert.ToBase64String(hash.ComputeHash(Descriptor(GetThreadDesktop(GetCurrentThreadId()))));
    }
  }
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
    public DesktopAccess(string sid) : this(sid, true) { }
    public DesktopAccess(string sid, bool canonical) {
      var identity = new SecurityIdentifier(sid);
      // The canonical Microsoft interactive-process fixture grants normal
      // interactive rights only to this disposable account, then restores the
      // original ACL. The narrow variant remains for the native A/B/A control.
      stationOriginal = Grant(station, identity, canonical ? 0xf037f : 0x327);
      try { desktopOriginal = Grant(desktop, identity, canonical ? 0xf01ff : 0xc7); } catch { Apply(station, stationOriginal); stationOriginal = null; throw; }
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
  public static bool LeasePresent(string name) {
    IntPtr handle = OpenEventW(0x100000, false, name);
    if (handle == IntPtr.Zero) {
      int error = Marshal.GetLastWin32Error();
      if (error == 2) return false;
      throw new Win32Exception(error);
    }
    try { return true; } finally { CloseHandle(handle); }
  }
  public static bool LeasePresent() {
    return LeasePresent("Global\\Mixel-Remote-Attended-Runtime-v2");
  }
}
'@ }

$executablePath = (Resolve-Path $Executable).Path
if ($CompiledQuickSupportDiagnostic) {
  $expectedCompiledEntry = Join-Path (Resolve-Path $ExpectedPayload).Path 'Mixel-Remote.exe'
  if ((Get-FileHash $executablePath -Algorithm SHA256).Hash -cne (Get-FileHash $expectedCompiledEntry -Algorithm SHA256).Hash) {
    throw 'Compiled diagnostic executable differs from the retained signed payload entry.'
  }
}
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
$ordinaryProfileRoot = $null
$ownedUser = $null
$ownedSid = $null
$desktopAccess = $null
$baselineIncomingPid = $null
$fixtureStage = 'customer launch'
$primaryFailure = $null
$nativeCrash = $null
$nativeCrashCleanupFailed = $false
$ordinaryStartedAt = $null
$launchScenario = if ($OrdinaryThenQuickSupport) { 'ordinary GUI to QuickSupport handoff' } elseif ($QuickSupport) { 'QuickSupport double-click' } else { 'support URI launch' }

function Start-CustomerApp {
  if ($QuickSupport) {
    if ($Portable) { return Start-Process -FilePath $executablePath -PassThru }
    # Artifact-only diagnosis uses the exact retained signed desktop payload
    # with the same native argument the customer QS portable launcher emits.
    # Final installer verification still executes the actual portable launcher.
    return Start-Process -FilePath $executablePath -ArgumentList '--quick_support' -PassThru
  }
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

function Write-OwnedWindowDiagnostic([int]$ProcessId, [string]$Phase) {
  try {
    Write-Host "FIXTURE: actual retained native process observation: phase=$Phase, pid=$ProcessId, status=$([MixelOrdinaryTokenFixture]::StartedStatus($ProcessId))."
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $windows = [System.Collections.Generic.List[object]]::new()
    [MixelSupportWindowTest]::EnumWindows({
      param($window, $parameter)
      [uint32]$ownerId = 0
      [void][MixelSupportWindowTest]::GetWindowThreadProcessId($window, [ref]$ownerId)
      if ($ownerId -eq $ProcessId) {
        $title = [System.Text.StringBuilder]::new(512)
        [void][MixelSupportWindowTest]::GetWindowText($window, $title, 512)
        $windows.Add([pscustomobject]@{
          hwnd = $window.ToInt64(); title = $title.ToString()
          visible = [MixelSupportWindowTest]::IsWindowVisible($window)
          iconic = [MixelSupportWindowTest]::IsIconic($window)
        })
      }
      return $true
    }, [IntPtr]::Zero) | Out-Null
    $modules = @()
    try { $modules = @($process.Modules | Select-Object -ExpandProperty ModuleName) }
    catch { $modules = @('unavailable') }
    $threads = @($process.Threads | ForEach-Object {
      $threadDesktop = $null
      try { $threadDesktop = [MixelOrdinaryTokenFixture]::ThreadDesktopName([uint32]$_.Id) }
      catch {
        $nativeError = $_.Exception
        while ($nativeError.InnerException) { $nativeError = $nativeError.InnerException }
        $threadDesktop = if ($nativeError -is [ComponentModel.Win32Exception]) { 'unavailable(win32=' + $nativeError.NativeErrorCode + ')' } else { 'unavailable' }
      }
      $threadState = 'unavailable'; $waitReason = $null; $startAddress = $null
      try {
        $threadState = $_.ThreadState.ToString()
        if ($_.ThreadState -eq [Diagnostics.ThreadState]::Wait) { $waitReason = $_.WaitReason.ToString() }
        $startAddress = $_.StartAddress.ToInt64()
      } catch { }
      [pscustomobject]@{ id = $_.Id; state = $threadState; waitReason = $waitReason; desktop = $threadDesktop; startAddress = $startAddress }
    })
    $state = [pscustomobject]@{
      phase = $Phase; foregroundPid = $ProcessId; executable = $process.Path
      exited = $process.HasExited; sessionId = $process.SessionId
      elevated = [MixelOrdinaryTokenFixture]::Elevated($ProcessId)
      runnerDesktop = [MixelOrdinaryTokenFixture]::CurrentDesktopPath()
      windows = $windows.ToArray()
      modules = $modules; threads = $threads
    }
    Write-Host ('FIXTURE: actual owned process/window snapshot: ' + ($state | ConvertTo-Json -Depth 4 -Compress))
  } catch {
    Write-Host "FIXTURE: owned process/window snapshot unavailable for PID $ProcessId; errorId=$($_.FullyQualifiedErrorId)."
  }
}

function Start-OwnedCrashObservation([string]$AccountSid, [string]$OwnedImageName='Mixel-Remote.exe') {
  if (-not $AccountSid) { throw 'Native crash capture requires the actual created standard-account SID.' }
  if ($OwnedImageName -cnotin @('Mixel-Remote.exe','Mixel-Native-Exception-Control.exe','Mixel-Genuine-Invalid-Control.exe')) { throw 'Native crash observation requires an exact owned executable name.' }
  $policy=@{}
  foreach ($source in @(
      @('machine','HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting'),
      @('policy','HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting'))) {
    if (-not (Test-Path $source[1])) { continue }
    $observed=Get-Item $source[1]
    foreach ($name in @('Disabled','DontShowUI','LoggingDisabled')) {
      if ($observed.GetValueNames().Contains($name) -and $observed.GetValueKind($name) -eq [Microsoft.Win32.RegistryValueKind]::DWord) {
        $policy[$source[0]+':'+$name]=$observed.GetValue($name)
      }
    }
  }
  Write-Host ('FIXTURE: read-only actual runner WER policy DWORD observations (absence is not an enabled assertion): '+($policy | ConvertTo-Json -Compress))
  $folder = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-native-crash-private-' + [Guid]::NewGuid().ToString('N'))
  $key = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\'+$OwnedImageName
  $state = [pscustomobject]@{ folder=$folder; key=$key; existed=(Test-Path $key); values=@{}; live=$null }
  if ($state.existed) {
    $original = Get-Item $key
    foreach ($name in @('DumpFolder','DumpType','DumpCount')) {
      if ($original.GetValueNames() -contains $name) {
        $state.values[$name] = @{ value=$original.GetValue($name,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames); kind=$original.GetValueKind($name) }
      }
    }
  }
  try {
    New-Item -ItemType Directory $folder | Out-Null
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($entry in @(@('S-1-5-18','FullControl'), @('S-1-5-32-544','FullControl'), @($AccountSid,'Modify'))) {
      $rule = [Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($entry[0]),$entry[1],'ContainerInherit,ObjectInherit','None','Allow')
      $acl.AddAccessRule($rule)
    }
    Set-Acl $folder $acl
    New-Item $key -Force | Out-Null
    New-ItemProperty $key -Name DumpFolder -Value $folder -PropertyType ExpandString -Force | Out-Null
    New-ItemProperty $key -Name DumpType -Value 2 -PropertyType DWord -Force | Out-Null
    New-ItemProperty $key -Name DumpCount -Value 1 -PropertyType DWord -Force | Out-Null
    return $state
  } catch {
    try { Stop-OwnedCrashObservation $state } catch { Write-Host 'FAIL: owned crash observation setup cleanup additionally failed.' }
    throw
  }
}

function Stop-OwnedCrashObservation($State) {
  # Restore only the three values changed by this observation and remove the
  # exact disposable folder. Raw dumps/debugger output never enter artifacts.
  $debuggerCleanupFailed=$false
  if ($State.live) { try { Stop-OwnedLiveDebugger $State.live } catch { $debuggerCleanupFailed=$true } }
  # The private synthetic control executable or a WER writer can still be in
  # use after qd. Observe debugger exit first, then terminate only retained
  # fixture-owned native processes before deleting their private directory.
  $processCleanupFailed=$false
  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $processCleanupFailed=$true }
  try {
    foreach ($name in @('DumpFolder','DumpType','DumpCount')) {
      if ($State.values.ContainsKey($name)) {
        New-ItemProperty $State.key -Name $name -Value $State.values[$name].value -PropertyType $State.values[$name].kind -Force | Out-Null
      } elseif ((Test-Path $State.key) -and (Get-Item $State.key).GetValueNames().Contains($name)) {
        Remove-ItemProperty $State.key -Name $name
      }
    }
    if (-not $State.existed -and (Test-Path $State.key)) { Remove-Item $State.key }
    if ($State.existed) {
      $restored=Get-Item $State.key
      foreach ($name in @('DumpFolder','DumpType','DumpCount')) {
        $present=$restored.GetValueNames().Contains($name)
        if ($present -ne $State.values.ContainsKey($name)) { throw 'Owned crash registry value presence was not restored.' }
        if ($present -and ($restored.GetValueKind($name) -ne $State.values[$name].kind -or
            $restored.GetValue($name,$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) -cne $State.values[$name].value)) {
          throw 'Owned crash registry value was not restored exactly.'
        }
      }
    } elseif (Test-Path $State.key) { throw 'Owned crash registry key remains.' }
  } finally {
    if (Test-Path $State.folder) { Remove-Item $State.folder -Recurse -Force }
  }
  if ($State.live -and $State.live.debugger.HasExited) { $State.live.debugger.Dispose() }
  if ($debuggerCleanupFailed -or $processCleanupFailed -or (Test-Path $State.folder)) { throw 'Owned native debugger/process/private file cleanup failed.' }
  Write-Host 'PASS: owned native crash observation restores its per-application registry values and deletes all private raw debugger/dump files.'
}

function Start-OwnedDebuggerProcess([string]$Executable, [string[]]$Arguments, [string]$Console, [string]$Errors) {
  $output=$null; $errorOutput=$null; $debugger=$null; $started=$false
  try {
    $output=[IO.File]::Create($Console)
    $errorOutput=[IO.File]::Create($Errors)
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Executable; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $debugger=[Diagnostics.Process]::new(); $debugger.StartInfo=$info
    if (-not $debugger.Start()) { throw 'Owned native debugger did not start.' }
    $started=$true
    # Keep our stdin writer open throughout capture. Drain both output pipes
    # concurrently into SID-private files so a full pipe cannot block CDB.
    $outputTask=$debugger.StandardOutput.BaseStream.CopyToAsync($output)
    $errorTask=$debugger.StandardError.BaseStream.CopyToAsync($errorOutput)
    return [pscustomobject]@{debugger=$debugger; input=$debugger.StandardInput; output=$output; errorOutput=$errorOutput; outputTask=$outputTask; errorTask=$errorTask; pipesClosed=$false}
  } catch {
    if ($started -and -not $debugger.HasExited) {
      try { $debugger.Kill(); if (-not $debugger.WaitForExit(10000)) { Write-Host 'FAIL: owned debugger startup cleanup did not observe exit.' } }
      catch { Write-Host 'FAIL: owned debugger startup cleanup additionally failed.' }
    }
    if ($output) { $output.Dispose() }; if ($errorOutput) { $errorOutput.Dispose() }
    if ($debugger) { $debugger.Dispose() }
    throw
  }
}

function Complete-OwnedDebuggerPipes($Live) {
  if ($Live.pipesClosed) { return }
  if (-not $Live.debugger.HasExited) { throw 'Owned native debugger must exit before its retained input pipe is closed.' }
  try {
    foreach ($task in @($Live.outputTask,$Live.errorTask)) {
      if (-not $task.Wait(10000)) { throw 'Owned native debugger output drain exceeded its deadline.' }
      [void]$task.GetAwaiter().GetResult()
    }
  } finally {
    $Live.input.Dispose(); $Live.output.Dispose(); $Live.errorOutput.Dispose()
    $Live.pipesClosed=$true
  }
}

function Stop-OwnedLiveDebugger($Live) {
  if (-not $Live.debugger.HasExited) {
    try { $Live.debugger.Kill() }
    catch { if (-not $Live.debugger.HasExited) { throw } }
    if (-not $Live.debugger.WaitForExit(10000) -or -not $Live.debugger.HasExited) { throw 'Owned native debugger termination was not observed within its deadline.' }
  }
  Complete-OwnedDebuggerPipes $Live
}

function Start-OwnedLiveCrashObservation([int]$ProcessId, $State, [switch]$GenuineInvalidControl) {
  $cdb=Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Debuggers/x64/cdb.exe'
  $commands=Join-Path $State.folder 'live-commands-private.txt'
  $raw=Join-Path $State.folder 'live-debugger-private.txt'
  $console=Join-Path $State.folder 'live-console-private.txt'
  $errors=Join-Path $State.folder 'live-errors-private.txt'
  $symbols=Join-Path $State.folder 'symbols'
  # .exr -1 supports live targets; .ecxr is documented for minidumps only.
  # qd detaches and leaves the actual owned application to handle/terminate
  # normally. No memory/argument display commands or child debugging are used.
  # Only the native first-chance CloseHandle notification observed on both
  # qualified runner images may be normalized. Genuine raised code8, drift,
  # other callers and second-chance events retain default Not Handled behavior.
  # Resolve addresses inside this same target/event; never copy ASLR addresses.
  $closeMappingCommand='.echo MIXEL_NATIVE_MAP_EVENTIP; ? @$eventip; .echo MIXEL_NATIVE_MAP_RETURN; ? @$ra; .echo MIXEL_NATIVE_MAP_CHANCE; ? @$exr_chance; .echo MIXEL_NATIVE_MAP_DISPATCHER_BASE; ? ntdll!KiRaiseUserExceptionDispatcher; .echo MIXEL_NATIVE_MAP_CLOSE_BASE; ? KERNELBASE!CloseHandle; .echo MIXEL_NATIVE_MAP_DISPATCHER; ? ntdll!KiRaiseUserExceptionDispatcher+0x3a; .echo MIXEL_NATIVE_MAP_CLOSE49; ? KERNELBASE!CloseHandle+0x49; .echo MIXEL_NATIVE_MAP_CLOSE4F; ? KERNELBASE!CloseHandle+0x4f'
  $closeNotificationGuard='((@$tpid == 0n'+$ProcessId+') and (@$exr_code == 0xc0000008) and (@$exr_chance == 0n1) and (ntdll!KiRaiseUserExceptionDispatcher > 0) and (KERNELBASE!CloseHandle > 0) and ((ntdll!KiRaiseUserExceptionDispatcher+0x3a) > ntdll!KiRaiseUserExceptionDispatcher) and ((KERNELBASE!CloseHandle+0x49) > KERNELBASE!CloseHandle) and ((KERNELBASE!CloseHandle+0x4f) > KERNELBASE!CloseHandle) and (@$eventip == ntdll!KiRaiseUserExceptionDispatcher+0x3a) and ((@$ra == KERNELBASE!CloseHandle+0x49) or (@$ra == KERNELBASE!CloseHandle+0x4f)))'
  $invalidHandleCommand='.if ((@$tpid == 0n'+$ProcessId+') and (@$exr_code == 0xc0000008)) { .if (@$t0 < 0n16) { r $t0 = @$t0 + 1; .echo MIXEL_NATIVE_INVALID_HANDLE; .lastevent; .exr -1; kn 40; lm; '+$closeMappingCommand+'; .if ('+$closeNotificationGuard+') { .echo MIXEL_NATIVE_CLOSE_NOTIFICATION_NORMALIZED; .echo MIXEL_NATIVE_INVALID_HANDLE_END; gh } .else { .echo MIXEL_NATIVE_INVALID_NOT_HANDLED; .echo MIXEL_NATIVE_INVALID_HANDLE_END; gn } } .else { .echo MIXEL_NATIVE_INVALID_HANDLE_LIMIT; qd } } .else { .echo MIXEL_NATIVE_OWNERSHIP_REJECTED; qd }'
  @(
    'sxe -c ".echo MIXEL_NATIVE_HEAP; .lastevent; .exr -1; kn 40; lm; .echo MIXEL_NATIVE_CAPTURE_END; qd" 0xc0000374',
    ('sxe -c "'+$invalidHandleCommand+'" -c2 "'+$invalidHandleCommand+'" ch'),
    'sxd av',
    '.echo MIXEL_NATIVE_READY',
    '.echo MIXEL_NATIVE_STARTUP_EVENT; .lastevent; .echo MIXEL_NATIVE_STARTUP_EVENT_END',
    ('.if (@$exr_code == 0xc0000374) { .echo MIXEL_NATIVE_HEAP; .lastevent; .exr -1; kn 40; lm; .echo MIXEL_NATIVE_CAPTURE_END; qd } .else { .if (@$exr_code == 0xc0000008) { '+$invalidHandleCommand+' } .else { .echo MIXEL_NATIVE_CONTINUE; g } }')
  ) | Set-Content $commands -Encoding ascii
  # Microsoft documents -pr for an already suspended target: resume occurs on
  # debugger attachment, permitting initial loader events and command startup.
  # Lowercase -g ignores only the debugger's initial breakpoint; uppercase -G
  # ignores its final breakpoint. The numeric heap break filter remains active.
  $State.live=Start-OwnedDebuggerProcess $cdb @('-p',[string]$ProcessId,'-pr','-g','-G','-pd','-hd','-nosqm','-noshell','-xe','0xc0000374','-y',('srv*'+$symbols+'*https://msdl.microsoft.com/download/symbols'),'-cf',$commands,'-logo',$raw) $console $errors
  $debugger=$State.live.debugger
  $State.live | Add-Member -NotePropertyMembers @{raw=$raw;console=$console;errors=$errors;pid=$ProcessId;attached=$false;captureAttributed=$false;invalidCaptureAttributed=$false;ready=$false;primaryThreadResume=$null;timeoutObservation=$null}
  $deadline=[DateTime]::UtcNow.AddSeconds(20)
  do {
    # Flush redirected output only after a confirmed debugger exit, before
    # parsing a rapid heap-capture-and-detach which polling may otherwise miss.
    if ($debugger.HasExited) { $debugger.WaitForExit(); Complete-OwnedDebuggerPipes $State.live }
    if ([MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ProcessId)) {
      $State.live.attached=$true
    }
    if (Test-Path $raw) {
      $lines=@(Get-Content $raw)
      $State.live.ready=@($lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_READY' }).Count -eq 1
      $State.live.captureAttributed=(Read-OwnedHeapCapture $lines $ProcessId).verified
      if ($GenuineInvalidControl -and -not $State.live.captureAttributed) {
        $negative=Read-OwnedInvalidHandleCaptures $lines $ProcessId
        $State.live.invalidCaptureAttributed=$negative.partialCapturePidVerified -and
          @($negative.captures | Where-Object { $_.chance -ceq 'first-chance' -and $_.handling -ceq 'not-handled' -and -not $_.closeNotificationQualified }).Count -eq 1
      }
      if ($State.live.ready -and ($State.live.attached -or $State.live.captureAttributed -or ($GenuineInvalidControl -and $State.live.invalidCaptureAttributed))) {
        $previous=[MixelOrdinaryTokenFixture]::ResumeOwnedPrimaryThread($ProcessId)
        $State.live.primaryThreadResume=[pscustomobject]@{ targetExited=($previous -eq -1); previousSuspendCount=$(if ($previous -ge 0) { $previous } else { $null }) }
        Write-Host ("FIXTURE: diagnostic-only configured-filter READY and exact target ownership qualified; foregroundPid=$ProcessId; retainedHandleAttachmentObserved=$($State.live.attached); exactNativeHeapCaptureObserved=$($State.live.captureAttributed); exactGenuineInvalidCaptureObserved=$($State.live.invalidCaptureAttributed); ownedPrimaryThreadResume="+($State.live.primaryThreadResume | ConvertTo-Json -Compress)+'.')
        return
      }
    }
    if ($debugger.HasExited) { break }
    Start-Sleep -Milliseconds 100
  } while ([DateTime]::UtcNow -lt $deadline)
  Write-Host ('FIXTURE: bounded live debugger startup observation: '+([pscustomobject]@{pid=$ProcessId; attached=$State.live.attached; captureAttributed=$State.live.captureAttributed; ready=$State.live.ready; debuggerExited=$debugger.HasExited; rawLogPresent=(Test-Path $raw)} | ConvertTo-Json -Compress))
  throw 'Owned live native debugger attachment/filter readiness did not qualify before exit or its deadline.'
}

function Read-OwnedHeapCapture([string[]]$Lines, [int]$ProcessId) {
  $frames=@(); $modules=@(); $active=$false; $completed=$false; $invalid=$false
  $captures=0; $events=0; $eventPid=$null; $code=$null
  foreach ($line in $Lines) {
    if ($line.Trim() -ceq 'MIXEL_NATIVE_HEAP') {
      $captures++; $active=$true; $frames=@(); $modules=@(); $events=0; $eventPid=$null; $code=$null
      if ($captures -ne 1) { $invalid=$true }
      continue
    }
    if (-not $active) { continue }
    if ($line.Trim() -ceq 'MIXEL_NATIVE_CAPTURE_END') { $active=$false; $completed=$true; continue }
    if ($line -match 'Last event:\s*([0-9a-fA-F]+)\.[0-9a-fA-F]+:.*(?:code|exception)\s+([0-9a-fA-F]{8})') {
      $events++; $eventPid=[Convert]::ToInt32($Matches[1],16); $code=$Matches[2]
      if ($events -ne 1 -or $eventPid -ne $ProcessId -or $code -ine 'c0000374') { $invalid=$true; $frames=@(); $modules=@() }
      continue
    }
    if ($line.Contains('Last event:')) { $invalid=$true; $frames=@(); $modules=@(); continue }
    if ($events -ne 1 -or $invalid) { continue }
    if ($line -match '^\s*([0-9a-fA-F]{1,3})\s+[0-9a-fA-F`]+\s+([0-9a-fA-F`]+)\s+((?:[A-Za-z0-9_.$?@:<>,~\[\]()+-]+![A-Za-z0-9_.$?@:<>,~\[\]()+-]+|[A-Za-z0-9_.-]+\+0x[0-9a-fA-F]+))\s*$') {
      $frames += [pscustomobject]@{ index=$Matches[1]; returnAddress=$Matches[2]; symbol=$Matches[3] }
    } elseif ($line -match '^\s*(?:[0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+(?:[0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+([A-Za-z0-9_.-]+)\s+') { $modules += $Matches[1] }
  }
  $verified=$captures -eq 1 -and $completed -and -not $active -and -not $invalid -and $events -eq 1 -and $eventPid -eq $ProcessId -and $code -ieq 'c0000374'
  if (-not $verified) { $frames=@(); $modules=@() }
  return [pscustomobject]@{ verified=$verified; pid=$eventPid; code=$code; frames=$frames; modules=$modules }
}

function Read-OwnedInvalidHandleCaptures([string[]]$Lines, [int]$ProcessId) {
  $captures=@(); $active=$false; $invalid=$false; $starts=0; $ends=0; $events=0
  $eventPid=$null; $chance=$null; $frames=@(); $modules=@(); $blockInvalid=$false; $exceptionAddress=$null; $addressRows=0; $recordCodeRows=0; $mapping=@{}; $pendingMapping=$null; $handling=$null; $handlingRows=0
  $observations=[pscustomobject]@{ captureStarts=0; captureEnds=0; eventRows=0; ownedPidRows=0; invalidHandleCodeRows=0; firstChanceRows=0; secondChanceRows=0; exceptionAddressRows=0; exceptionRecordCodeRows=0; nativeMappingRows=0; malformedMappingRows=0; handlingRows=0; malformedHandlingRows=0; malformedRecordCodeRows=0; malformedAddressRows=0; malformedEventRows=0; wrongPidRows=0; wrongCodeRows=0; unknownChanceRows=0; duplicateEventBlocks=0; malformedBlocks=0; noFrameBlocks=0; incompleteBlocks=0; ownershipRejected=0; limitReached=0; sizeBoundRejected=0 }
  if ($Lines.Count -gt 65536) { $observations.sizeBoundRejected=1; return [pscustomobject]@{ verified=$false; partialCapturePidVerified=$false; passedBlockCount=0; captures=@(); bounded=$false; observations=$observations } }
  foreach ($line in $Lines) {
    if ($line.Trim() -ceq 'MIXEL_NATIVE_INVALID_HANDLE_LIMIT') { $invalid=$true; if ($active) { $blockInvalid=$true }; $observations.limitReached++ }
    if ($line.Trim() -ceq 'MIXEL_NATIVE_OWNERSHIP_REJECTED') { $invalid=$true; if ($active) { $blockInvalid=$true }; $observations.ownershipRejected++ }
    if ($line.Trim() -ceq 'MIXEL_NATIVE_INVALID_HANDLE') {
      $starts++
      if ($active) { $invalid=$true; $observations.malformedBlocks++ }
      if ($starts -gt 16) { $invalid=$true; $observations.sizeBoundRejected++ }
      $blockInvalid=$active -or $starts -gt 16
      $active=$true; $events=0; $eventPid=$null; $chance=$null; $frames=@(); $modules=@(); $exceptionAddress=$null; $addressRows=0; $recordCodeRows=0; $mapping=@{}; $pendingMapping=$null; $handling=$null; $handlingRows=0
      continue
    }
    if ($line.Trim() -ceq 'MIXEL_NATIVE_INVALID_HANDLE_END') {
      $ends++
      if (-not $active -or $events -ne 1 -or $eventPid -ne $ProcessId -or -not $chance) { $invalid=$true; $blockInvalid=$true; $observations.malformedBlocks++ }
      if ($addressRows -ne 1 -or -not $exceptionAddress) { $invalid=$true; $blockInvalid=$true; $observations.malformedAddressRows++ }
      if ($recordCodeRows -ne 1) { $invalid=$true; $blockInvalid=$true; $observations.malformedRecordCodeRows++ }
      $zeroFrames=@($frames | Where-Object { [Convert]::ToInt32($_.index,16) -eq 0 })
      $mappingValid=$mapping.Count -eq 8 -and -not $pendingMapping -and $zeroFrames.Count -eq 1
      if ($mappingValid) {
        $dispatcherBase=[Convert]::ToUInt64($mapping.dispatcher_base,16)
        $closeBase=[Convert]::ToUInt64($mapping.close_base,16)
        $mappingValid=$dispatcherBase -gt 0 -and $closeBase -gt 0 -and
          $dispatcherBase -le ([UInt64]::MaxValue-[UInt64]0x3a) -and $closeBase -le ([UInt64]::MaxValue-[UInt64]0x4f)
        if ($mappingValid) {
          $mappingValid=[Convert]::ToUInt64($mapping.dispatcher,16) -eq ($dispatcherBase+[UInt64]0x3a) -and
            [Convert]::ToUInt64($mapping.close49,16) -eq ($closeBase+[UInt64]0x49) -and
            [Convert]::ToUInt64($mapping.close4f,16) -eq ($closeBase+[UInt64]0x4f)
        }
      }
      if ($mappingValid) {
        $mappingValid=$mapping.eventip -ceq $exceptionAddress -and $mapping.return -ceq $zeroFrames[0].returnAddress.Replace('`','').ToLowerInvariant().PadLeft(16,'0') -and
          (($chance -ceq 'first-chance' -and $mapping.chance -ceq '0000000000000001') -or
           ($chance -ceq 'second-chance' -and $mapping.chance -cin @('0000000000000000','0000000000000002')))
      }
      $closeQualified=$mappingValid -and $chance -ceq 'first-chance' -and $mapping.eventip -ceq $mapping.dispatcher -and
        ($mapping.return -ceq $mapping.close49 -or $mapping.return -ceq $mapping.close4f)
      if (-not $mappingValid) { $invalid=$true; $blockInvalid=$true; $observations.malformedMappingRows++ }
      if ($handlingRows -ne 1 -or ($handling -ceq 'normalized-close-notification') -ne $closeQualified) { $invalid=$true; $blockInvalid=$true; $observations.malformedHandlingRows++ }
      if ($frames.Count -eq 0) { $invalid=$true; $blockInvalid=$true; $observations.noFrameBlocks++ }
      if (-not $blockInvalid -and $captures.Count -lt 16) { $captures += [pscustomobject]@{ pid=$eventPid; code='c0000008'; chance=$chance; nativeExceptionAddress=$exceptionAddress; nativeMapping=[pscustomobject]$mapping; handling=$handling; closeNotificationQualified=$closeQualified; stackFrames=$frames; loadedModuleNames=$modules } }
      $active=$false; continue
    }
    if (-not $active) { continue }
    if ($line -match 'Last event:\s*([0-9a-fA-F]+)\.[0-9a-fA-F]+:\s*(.+)$') {
      $events++; $observations.eventRows++; $eventPid=[Convert]::ToInt32($Matches[1],16); $description=$Matches[2]
      if ($events -ne 1) { $invalid=$true; $blockInvalid=$true; $observations.duplicateEventBlocks++ }
      if ($eventPid -eq $ProcessId) { $observations.ownedPidRows++ } else { $invalid=$true; $blockInvalid=$true; $observations.wrongPidRows++ }
      if ($description -match '(?:code|exception)\s+c0000008(?:\s|$)') { $observations.invalidHandleCodeRows++ } else { $invalid=$true; $blockInvalid=$true; $observations.wrongCodeRows++ }
      if ($description -match '\((first|second) chance\)') {
        $chance=$Matches[1]+'-chance'
        if ($chance -ceq 'first-chance') { $observations.firstChanceRows++ } else { $observations.secondChanceRows++ }
      } else { $invalid=$true; $blockInvalid=$true; $observations.unknownChanceRows++ }
      continue
    }
    if ($line.Contains('Last event:')) { $invalid=$true; $blockInvalid=$true; $observations.malformedEventRows++; continue }
    if ($events -ne 1 -or $blockInvalid) { continue }
    if ($pendingMapping) {
      if ($line -match '^\s*Evaluate expression:\s*-?[0-9]+\s*=\s*([0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s*$') {
        $value=$Matches[1].Replace('`','').ToLowerInvariant().PadLeft(16,'0')
        if ($mapping.ContainsKey($pendingMapping) -or ($pendingMapping -cne 'chance' -and [Convert]::ToUInt64($value,16) -eq 0)) { $invalid=$true; $blockInvalid=$true; $observations.malformedMappingRows++ }
        else { $mapping[$pendingMapping]=$value; $observations.nativeMappingRows++ }
      } else { $invalid=$true; $blockInvalid=$true; $observations.malformedMappingRows++ }
      $pendingMapping=$null; continue
    }
    if ($line.Trim() -cmatch '^MIXEL_NATIVE_MAP_(EVENTIP|RETURN|CHANCE|DISPATCHER_BASE|CLOSE_BASE|DISPATCHER|CLOSE49|CLOSE4F)$') { $pendingMapping=$Matches[1].ToLowerInvariant(); continue }
    if ($line.Trim().StartsWith('MIXEL_NATIVE_MAP_')) { $invalid=$true; $blockInvalid=$true; $observations.malformedMappingRows++; continue }
    if ($line.Trim() -ceq 'MIXEL_NATIVE_CLOSE_NOTIFICATION_NORMALIZED' -or $line.Trim() -ceq 'MIXEL_NATIVE_INVALID_NOT_HANDLED') {
      $handlingRows++; $observations.handlingRows++
      $handling=if ($line.Trim() -ceq 'MIXEL_NATIVE_CLOSE_NOTIFICATION_NORMALIZED') { 'normalized-close-notification' } else { 'not-handled' }
      if ($handlingRows -ne 1) { $invalid=$true; $blockInvalid=$true; $observations.malformedHandlingRows++ }
      continue
    }
    if ($line -match '^\s*ExceptionAddress:\s*([0-9a-fA-F`]{8,17})(?:\s|$)') {
      $addressRows++; $observations.exceptionAddressRows++
      $hex=$Matches[1].Replace('`','')
      if ($addressRows -ne 1 -or $hex -notmatch '^[0-9a-fA-F]{8,16}$' -or [Convert]::ToUInt64($hex,16) -eq 0) { $invalid=$true; $blockInvalid=$true; $observations.malformedAddressRows++ }
      else { $exceptionAddress=$hex.ToLowerInvariant().PadLeft(16,'0') }
      continue
    }
    if ($line -match '^\s*ExceptionAddress:') { $invalid=$true; $blockInvalid=$true; $observations.malformedAddressRows++; continue }
    if ($line -match '^\s*ExceptionCode:\s*c0000008(?:\s|$)') {
      $recordCodeRows++; $observations.exceptionRecordCodeRows++
      if ($recordCodeRows -ne 1) { $invalid=$true; $blockInvalid=$true; $observations.malformedRecordCodeRows++ }
      continue
    }
    if ($line -match '^\s*ExceptionCode:') { $invalid=$true; $blockInvalid=$true; $observations.malformedRecordCodeRows++; continue }
    if ($line -match '^\s*([0-9a-fA-F]{1,3})\s+[0-9a-fA-F`]+\s+([0-9a-fA-F`]+)\s+((?:[A-Za-z0-9_.$?@:<>,~\[\]()+-]+![A-Za-z0-9_.$?@:<>,~\[\]()+-]+|[A-Za-z0-9_.-]+\+0x[0-9a-fA-F]+))\s*$') {
      $frames += [pscustomobject]@{ index=$Matches[1]; returnAddress=$Matches[2]; symbol=$Matches[3] }
      if ($frames.Count -gt 40) { $invalid=$true; $blockInvalid=$true; $observations.sizeBoundRejected++ }
    } elseif ($line -match '^\s*(?:[0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+(?:[0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+([A-Za-z0-9_.-]+)\s+') {
      $modules += $Matches[1]
      if ($modules.Count -gt 256) { $invalid=$true; $blockInvalid=$true; $observations.sizeBoundRejected++ }
    }
  }
  $verified=$starts -ge 1 -and $starts -le 16 -and $starts -eq $ends -and -not $active -and -not $invalid -and $captures.Count -eq $starts
  $observations.captureStarts=$starts; $observations.captureEnds=$ends
  if ($active -or $starts -ne $ends) { $observations.incompleteBlocks=1 }
  # A later exit-like or rejected block must not erase an earlier individually
  # complete owned-PID/code/chance stack. Partial evidence never passes the full
  # control or the independent heap parser.
  return [pscustomobject]@{ verified=$verified; partialCapturePidVerified=($captures.Count -gt 0); passedBlockCount=$captures.Count; captures=$captures; bounded=($observations.sizeBoundRejected -eq 0); observations=$observations }
}

function Read-OwnedExecutionMetadata([string[]]$Lines, [int]$ProcessId, [switch]$TimeoutEvent) {
  $startMarker=if ($TimeoutEvent) { 'MIXEL_NATIVE_TIMEOUT_EVENT' } else { 'MIXEL_NATIVE_STARTUP_EVENT' }
  $endMarker=$startMarker+'_END'
  $active=$false; $complete=$false; $starts=0; $ends=0; $events=0; $eventPid=$null; $eventKind='unobserved'; $eventCode=$null
  $lastPidVerified=$false; $lastKind='unobserved'; $lastCode=$null
  foreach ($line in $Lines) {
    if ($line.Trim() -ceq $startMarker) { $starts++; $active=$true; continue }
    if ($line.Trim() -ceq $endMarker) { $ends++; $active=$false; $complete=$true; continue }
    if ($line -notmatch 'Last event:\s*([0-9a-fA-F]+)\.[0-9a-fA-F]+:\s*(.+)$') { continue }
    $observedPid=[Convert]::ToInt32($Matches[1],16); $description=$Matches[2]
    $kind=if ($description -match '(?i)^create process') { 'process-create' }
      elseif ($description -match '(?i)^create thread') { 'thread-create' }
      elseif ($description -match '(?i)^load module') { 'module-load' }
      elseif ($description -match '(?i)^exit process') { 'process-exit' }
      elseif ($description -match '(?i)exception' -and $description -match '(?:code|exception)\s+c0000374') { 'heap-exception' }
      elseif ($description -match '(?i)exception' -and $description -match '(?:code|exception)\s+80000003') { 'initial-break-exception' }
      elseif ($description -match '(?:code|exception)\s+c0000008') { 'invalid-handle-exception' }
      elseif ($description -match '(?i)exception' -and $description -match '(?:code|exception)\s+[0-9a-fA-F]{8}') { 'other-exception' }
      else { 'other-native-event' }
    $code=if ($description -match '(?i)(?:code|exception)\s+([0-9a-fA-F]{8})(?:\s|$)') { $Matches[1].ToLowerInvariant() } else { $null }
    $lastPidVerified=$observedPid -eq $ProcessId
    $lastKind=if ($lastPidVerified) { $kind } else { 'unattributed' }
    $lastCode=if ($lastPidVerified) { $code } else { $null }
    if ($active) { $events++; $eventPid=$observedPid; $eventKind=$kind; $eventCode=$code }
  }
  $eventVerified=$starts -eq 1 -and $ends -eq 1 -and $events -eq 1 -and $complete -and -not $active -and $eventPid -eq $ProcessId
  if (-not $eventVerified) { $eventKind='unattributed-or-incomplete'; $eventCode=$null }
  return [pscustomobject]@{
    readyMarkers=@($Lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_READY' }).Count
    continueMarkers=@($Lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_CONTINUE' }).Count
    heapCaptureStarts=@($Lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_HEAP' }).Count
    heapCaptureEnds=@($Lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_CAPTURE_END' }).Count
    startupEventPidVerified=$eventVerified; startupEventKind=$eventKind; startupEventCode=$eventCode
    timeoutEventStarts=@($Lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_TIMEOUT_EVENT' }).Count
    timeoutEventEnds=@($Lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_TIMEOUT_EVENT_END' }).Count
    debuggerPromptLines=@($Lines | Where-Object { $_ -match '^\s*[0-9]+:[0-9a-fA-F]+>' }).Count
    lastObservedNativeEventPidVerified=$lastPidVerified; lastObservedNativeEventKind=$lastKind; lastObservedNativeEventCode=$lastCode
    syntaxErrorLines=@($Lines | Where-Object { $_ -match '(?i)syntax error' }).Count
    evaluationErrorLines=@($Lines | Where-Object { $_ -match "(?i)bad register error|could(?:n.t| not) (?:resolve|evaluate)|unable to (?:resolve|evaluate)" }).Count
    commandErrorLines=@($Lines | Where-Object { $_ -match '(?i)unknown command|unrecognized command|no runnable debuggees|command not supported' }).Count
  }
}

function Read-OwnedLiveDebuggerSnapshot([string]$Path) {
  $stream=$null; $memory=$null; $reader=$null
  try {
    $stream=[IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    # Read only the length observed when this shared handle opens; ongoing
    # appends cannot make a snapshot read continue indefinitely.
    $length=$stream.Length
    if ($length -gt 16777216) { throw 'Owned live debugger snapshot exceeded its size bound.' }
    $bytes=[byte[]]::new([int]$length); $offset=0
    while ($offset -lt $bytes.Length) {
      $read=$stream.Read($bytes,$offset,$bytes.Length-$offset)
      if ($read -eq 0) { return [pscustomobject]@{pending=$true;lines=$null} }
      $offset+=$read
    }
    $memory=[IO.MemoryStream]::new($bytes,$false)
    $reader=[IO.StreamReader]::new($memory,[Text.Encoding]::UTF8,$true)
    return [pscustomobject]@{pending=$false;lines=[string[]]($reader.ReadToEnd() -split '\r?\n')}
  } catch [IO.IOException] {
    $failure=$_.Exception
    while ($failure.InnerException) { $failure=$failure.InnerException }
    if (($failure.HResult -band 0xffff) -in @(32,33)) { return [pscustomobject]@{pending=$true;lines=$null} }
    throw
  } finally {
    if ($reader) { $reader.Dispose() }
    if ($memory) { $memory.Dispose() }
    if ($stream) { $stream.Dispose() }
  }
}

function Request-OwnedDebuggerTimeoutEvent([int]$ProcessId, $State) {
  $live=$State.live
  $observation=[pscustomobject]@{ requested=$false; writeCompleted=$false; flushCompleted=$false; requestError=$false; eventPidVerified=$false; eventKind='unobserved'; eventCode=$null }
  $live.timeoutObservation=$observation
  if ($live.debugger.HasExited) { return }
  $deadline=[DateTime]::UtcNow.AddSeconds(3)
  try {
    # Read-only event observation only. Never inject g/gh/gn or alter filters.
    $observation.requested=$true
    $write=$live.input.WriteLineAsync('.echo MIXEL_NATIVE_TIMEOUT_EVENT; .lastevent; .echo MIXEL_NATIVE_TIMEOUT_EVENT_END')
    if (-not $write.Wait([Math]::Max(0,[int]($deadline-[DateTime]::UtcNow).TotalMilliseconds))) { return }
    [void]$write.GetAwaiter().GetResult(); $observation.writeCompleted=$true
    $flush=$live.input.FlushAsync()
    if (-not $flush.Wait([Math]::Max(0,[int]($deadline-[DateTime]::UtcNow).TotalMilliseconds))) { return }
    [void]$flush.GetAwaiter().GetResult(); $observation.flushCompleted=$true
    do {
      if (Test-Path $live.raw) {
        $snapshot=Read-OwnedLiveDebuggerSnapshot $live.raw
        if (-not $snapshot.pending) {
          $metadata=Read-OwnedExecutionMetadata $snapshot.lines $ProcessId -TimeoutEvent
          if ($metadata.startupEventPidVerified) {
            $observation.eventPidVerified=$true; $observation.eventKind=$metadata.startupEventKind; $observation.eventCode=$metadata.startupEventCode
            return
          }
        }
      }
      if ($live.debugger.HasExited) { return }
      Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
  } catch { $observation.requestError=$true }
}

function Write-OwnedControlExecutionEvidence([int]$ProcessId, $State, [switch]$ProductTarget, [switch]$GenuineInvalidControl) {
  $entry=Join-Path $State.folder 'native-control-entered.txt'
  $entered=if ($ProductTarget) { $null } else { Test-Path $entry }
  $entryPidVerified=if ($ProductTarget) { $null } else { $entered -and [IO.File]::ReadAllText($entry) -ceq [string]$ProcessId }
  $closeResult=Join-Path $State.folder 'native-control-invalid-handle-return.txt'
  $invalidHandleReturnVerified=if ($ProductTarget -or $GenuineInvalidControl) { $null } else { (Test-Path $closeResult) -and [IO.File]::ReadAllText($closeResult) -ceq ($ProcessId.ToString()+';0;6') }
  $nonzeroCloseResult=Join-Path $State.folder 'native-control-nonzero-invalid-handle-return.txt'
  $nonzeroInvalidHandleReturnVerified=if ($ProductTarget -or $GenuineInvalidControl) { $null } else { (Test-Path $nonzeroCloseResult) -and [IO.File]::ReadAllText($nonzeroCloseResult) -ceq ($ProcessId.ToString()+';0;6') }
  $invalidHandles=[pscustomobject]@{ verified=$false; captures=@(); bounded=$true }
  if ($State.live -and (Test-Path $State.live.raw)) {
    $snapshot=Read-OwnedLiveDebuggerSnapshot $State.live.raw
    if ($snapshot.pending) { throw 'Stopped debugger invalid-handle snapshot is incomplete.' }
    $invalidHandles=Read-OwnedInvalidHandleCaptures $snapshot.lines $ProcessId
    $ownedStartup=$State.live.ready -and ($State.live.attached -or $State.live.captureAttributed -or ($GenuineInvalidControl -and $State.live.invalidCaptureAttributed))
    $invalidHandles.verified=$ownedStartup -and $invalidHandles.verified
    $invalidHandles.partialCapturePidVerified=$ownedStartup -and $invalidHandles.partialCapturePidVerified
    if (-not $ownedStartup) { $invalidHandles.captures=@(); $invalidHandles.passedBlockCount=0 }
  }
  $streams=@{}
  if ($State.live) {
    foreach ($name in @('raw','console','errors')) {
      $path=$State.live.$name
      $streams[$name]=$null
      if (Test-Path $path) {
        $snapshot=Read-OwnedLiveDebuggerSnapshot $path
        if ($snapshot.pending) { throw 'Stopped debugger execution snapshot is incomplete.' }
        $streams[$name]=Read-OwnedExecutionMetadata $snapshot.lines $ProcessId
      }
    }
  }
  $kind=if ($ProductTarget) { 'owned-product-native-execution' } elseif ($GenuineInvalidControl) { 'synthetic-genuine-invalid-handle-negative' } else { 'synthetic-native-exception-execution' }
  $evidence=[pscustomobject]@{ evidenceKind=$kind; observationPhase='after-bounded-debugger-stop-before-target-cleanup'; pid=$ProcessId; nativeStatus=[MixelOrdinaryTokenFixture]::StartedStatus($ProcessId); enteredMain=$entered; entryPidVerified=$entryPidVerified; syntheticInvalidHandleReturnFalseAndError6=$invalidHandleReturnVerified; syntheticNonzeroInvalidHandleReturnFalseAndError6=$nonzeroInvalidHandleReturnVerified; afterRaiseSentinelObserved=$(if ($GenuineInvalidControl) { Test-Path (Join-Path $State.folder 'native-genuine-after-raise.txt') } else { $null }); invalidHandleCaptures=$invalidHandles; primaryThreadResume=$(if ($State.live) { $State.live.primaryThreadResume } else { $null }); timeoutObservation=$(if ($State.live) { $State.live.timeoutObservation } else { $null }); debuggerExited=($State.live -and $State.live.debugger.HasExited); streams=$streams }
  Write-Host ('FIXTURE: bounded native execution evidence before cleanup: '+($evidence | ConvertTo-Json -Depth 5 -Compress))
  if ($env:RUNNER_TEMP) {
    $archive=Join-Path $env:RUNNER_TEMP 'mixel-owned-ordinary-qs-logs'
    New-Item -ItemType Directory $archive -Force | Out-Null
    $artifactName=if ($ProductTarget) { 'native-product-execution-sanitized.json' } elseif ($GenuineInvalidControl) { 'native-genuine-invalid-execution-sanitized.json' } else { 'native-exception-execution-sanitized.json' }
    $evidence | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $archive $artifactName)
  }
}

function Write-OwnedCrashDiagnostic([int]$ProcessId, [string]$OwnedExecutable, [DateTime]$StartedAt, $State, [switch]$SyntheticControl) {
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  $dump = $null
  if ($State -and -not $State.live) {
    do {
      foreach ($candidate in @(Get-ChildItem $State.folder -Filter '*.dmp' -File)) {
        # WER may still be writing a newly created dump. Retry bounded reads
        # instead of treating a partial header or sharing lock as attribution.
        try {
          if ([MixelOrdinaryTokenFixture]::DumpProcessId($candidate.FullName) -eq $ProcessId) { $dump=$candidate; break }
        } catch { }
      }
      if ($dump) { break }
      Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
  }
  $events = @()
  foreach ($event in @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=$StartedAt.AddSeconds(-1)} -ErrorAction SilentlyContinue)) {
    $data=@{}
    foreach ($entry in ([xml]$event.ToXml()).Event.EventData.Data) { $data[[string]$entry.Name]=[string]$entry.'#text' }
    if ($data.AppPath -cne $OwnedExecutable -or $data.ProcessId -notmatch '^(?:0x)?[0-9a-fA-F]+$') { continue }
    $eventPid = if ($data.ProcessId.StartsWith('0x')) { [Convert]::ToInt64($data.ProcessId.Substring(2),16) } else { [Convert]::ToInt64($data.ProcessId) }
    if ($eventPid -ne $ProcessId) { continue }
    $events += [pscustomobject]@{ eventId=1000; pid=$eventPid; module=$data.ModuleName; exception=$data.ExceptionCode; offset=$data.FaultingOffset }
  }
  $frames=@(); $modules=@(); $lastEvent=@(); $debuggerStatus='no-owned-dump'; $raw=$null; $liveVerified=$false
  if ($State -and $State.live) {
    $debugger=$State.live.debugger
    $waitMs=if ($State.live.ready) { 120000 } else { 0 }
    if (-not $debugger.WaitForExit($waitMs)) {
      if ($State.live.ready) { Request-OwnedDebuggerTimeoutEvent $ProcessId $State }
      Stop-OwnedLiveDebugger $State.live
      if ($State.live.ready) { throw 'Owned live stack extraction exceeded its deadline.' }
    }
    $debugger.WaitForExit(); Complete-OwnedDebuggerPipes $State.live; $debugger.Refresh()
    $debuggerStatus='live-exit:'+$debugger.ExitCode
    $raw=$State.live.raw
  }
  if ($dump) {
    $cdb = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Debuggers/x64/cdb.exe'
    if (-not (Test-Path $cdb)) { throw 'Native diagnostic CDB was not prepared.' }
    $raw = Join-Path $State.folder 'debugger-private.txt'
    $errors = Join-Path $State.folder 'debugger-private-errors.txt'
    $symbols = Join-Path $State.folder 'symbols'
    $debugger = Start-Process $cdb -ArgumentList @('-z',('"'+$dump.FullName+'"'),'-y',('"srv*'+$symbols+'*https://msdl.microsoft.com/download/symbols"'),'-c','".lastevent; .ecxr; kn 40; lm; q"') -RedirectStandardOutput $raw -RedirectStandardError $errors -PassThru -NoNewWindow
    if (-not $debugger.WaitForExit(120000)) { Stop-Process -Id $debugger.Id -Force; throw 'Owned dump stack extraction exceeded its deadline.' }
    $debugger.WaitForExit()
    $debugger.Refresh()
    $debuggerStatus='exit:'+$debugger.ExitCode
  }
  if ($raw -and (Test-Path $raw)) {
    # Bind one bounded heap-event block to both its actual native PID and code.
    # kn has no arguments/locals; unmarked startup rows are never retained.
    if ($State.live) {
      $capture=Read-OwnedHeapCapture @(Get-Content $raw) $ProcessId
      $liveVerified=($State.live.attached -or $State.live.captureAttributed) -and $State.live.ready -and $capture.verified
      if ($liveVerified) { $frames=$capture.frames; $modules=$capture.modules; $lastEvent=@($capture.code) }
    } else {
      foreach ($line in Get-Content $raw) {
        if ($line -match '^\s*([0-9a-fA-F]{1,3})\s+[0-9a-fA-F`]+\s+([0-9a-fA-F`]+)\s+((?:[A-Za-z0-9_.$?@:<>,~\[\]()+-]+![A-Za-z0-9_.$?@:<>,~\[\]()+-]+|[A-Za-z0-9_.-]+\+0x[0-9a-fA-F]+))\s*$') {
          $frames += [pscustomobject]@{ index=$Matches[1]; returnAddress=$Matches[2]; symbol=$Matches[3] }
        } elseif ($line -match '^\s*(?:[0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+(?:[0-9a-fA-F]{8,16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+([A-Za-z0-9_.-]+)\s+') { $modules += $Matches[1] }
        if ($line -match 'Last event:.*(?:code|exception)\s+([0-9a-fA-F]{8})') { $lastEvent += $Matches[1] }
      }
    }
  }
  if ($State -and $State.live -and -not $liveVerified) { $frames=@(); $modules=@(); $lastEvent=@() }
  $extraction = if (-not $dump -and -not $liveVerified) { 'no-owned-crash-attribution' } elseif ($frames.Count -eq 0) { 'no-stack-frames' } else { 'stack-extracted' }
  $kind=if ($SyntheticControl) { 'synthetic-native-exception-control' } else { 'owned-product-native-crash-diagnostic' }
  $selected=[pscustomobject]@{ evidenceKind=$kind; pid=$ProcessId; nativeStatus=[MixelOrdinaryTokenFixture]::StartedStatus($ProcessId); applicationEvents=$events; actualDumpPidVerified=[bool]$dump; actualLiveExceptionPidVerified=[bool]$liveVerified; debuggerStatus=$debuggerStatus; stackExtraction=$extraction; exceptionCodes=$lastEvent; stackFrames=$frames; loadedModuleNames=$modules }
  Write-Host ('FIXTURE: actual owned native crash evidence: '+($selected | ConvertTo-Json -Depth 5 -Compress))
  if ($env:RUNNER_TEMP) {
    $archive=Join-Path $env:RUNNER_TEMP 'mixel-owned-ordinary-qs-logs'
    New-Item -ItemType Directory $archive -Force | Out-Null
    $artifactName=if ($SyntheticControl) { 'native-exception-control-sanitized.json' } else { 'native-crash-sanitized.json' }
    $selected | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $archive $artifactName)
  }
  return $selected
}

function Invoke-OwnedGenuineInvalidControl([string]$AccountSid, [string]$AccountName, [string]$AccountPassword) {
  # A continuable genuine code8 must remain unhandled. Flags1 would not detect
  # blanket handling, because a noncontinuable exception can still terminate.
  $state=$null; $controlFailure=$null; $controlPid=$null
  try {
    $state=Start-OwnedCrashObservation $AccountSid 'Mixel-Genuine-Invalid-Control.exe'
    $source=Join-Path $state.folder 'genuine-invalid-control.cs'
    $executable=Join-Path $state.folder 'Mixel-Genuine-Invalid-Control.exe'
    @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
public static class MixelOwnedGenuineInvalidControl {
  [DllImport("kernel32.dll")] static extern void RaiseException(uint code, uint flags, uint count, IntPtr arguments);
  public static void Main() {
    File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-control-entered.txt"),Process.GetCurrentProcess().Id.ToString());
    RaiseException(0xc0000008, 0, 0, IntPtr.Zero);
    File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-genuine-after-raise.txt"),Process.GetCurrentProcess().Id.ToString());
  }
}
'@ | Set-Content $source -Encoding utf8
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler /nologo /target:winexe /platform:x64 "/out:$executable" $source
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $executable)) { throw 'Owned genuine invalid exception control compilation failed.' }
    $controlPid=[MixelOrdinaryTokenFixture]::StartStandardUser($executable,$AccountName,$AccountPassword,$true)
    $control=Get-Process -Id $controlPid
    if ([MixelOrdinaryTokenFixture]::Elevated($controlPid) -or $control.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'Genuine invalid control is not the actual non-elevated same-session owned target.' }
    Start-OwnedLiveCrashObservation $controlPid $state -GenuineInvalidControl
    if (-not $state.live.debugger.WaitForExit(120000)) {
      Request-OwnedDebuggerTimeoutEvent $controlPid $state
      throw 'Genuine invalid control debugger completion exceeded its deadline.'
    }
    $state.live.debugger.WaitForExit(); Complete-OwnedDebuggerPipes $state.live
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    do {
      $nativeStatus=[MixelOrdinaryTokenFixture]::StartedStatus($controlPid)
      if ($nativeStatus -cne 'still-running') { break }
      Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    $snapshot=Read-OwnedLiveDebuggerSnapshot $state.live.raw
    if ($snapshot.pending) { throw 'Stopped genuine invalid control snapshot is incomplete.' }
    $invalid=Read-OwnedInvalidHandleCaptures $snapshot.lines $controlPid
    $entry=Join-Path $state.folder 'native-control-entered.txt'
    $entryPidVerified=(Test-Path $entry) -and [IO.File]::ReadAllText($entry) -ceq [string]$controlPid
    if (-not $entryPidVerified -or $nativeStatus -cne 'exited:0xc0000008' -or
        (Test-Path (Join-Path $state.folder 'native-genuine-after-raise.txt')) -or
        -not $invalid.partialCapturePidVerified -or
        @($invalid.captures | Where-Object { $_.handling -cne 'not-handled' -or $_.closeNotificationQualified }).Count -gt 0 -or
        @($invalid.captures | Where-Object { $_.chance -ceq 'first-chance' }).Count -ne 1 -or
        @($snapshot.lines | Where-Object { $_.Trim() -ceq 'MIXEL_NATIVE_CLOSE_NOTIFICATION_NORMALIZED' }).Count -ne 0 -or
        (Read-OwnedHeapCapture $snapshot.lines $controlPid).verified) {
      throw 'Genuine continuable RaiseException8 did not qualify owned fatal code8, rejected normalization and absent after-raise sentinel.'
    }
    Write-Host "PASS: separate owned continuable RaiseException(0xc0000008, flags0) negative retains native fatal code8, exact first-chance PID/address mapping, no normalized marker and absent after-raise sentinel; controlPid=$controlPid; this is debugger guard qualification only."
  } catch { $controlFailure=$_; throw }
  finally {
    $errors=[System.Collections.Generic.List[string]]::new()
    if ($state -and $controlPid) {
      if ($state.live) { try { Stop-OwnedLiveDebugger $state.live } catch { $errors.Add('Genuine negative debugger exit/output observation failed.') } }
      try { Write-OwnedControlExecutionEvidence $controlPid $state -GenuineInvalidControl } catch { $errors.Add('Genuine negative bounded evidence capture failed.') }
    }
    if ($state) { try { Stop-OwnedCrashObservation $state } catch { $errors.Add('Genuine negative private/registry cleanup failed.') } }
    try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $errors.Add('Genuine negative retained native cleanup failed.') }
    [MixelOrdinaryTokenFixture]::CloseStartedObservations()
    if ($errors.Count -gt 0) {
      if ($controlFailure) { Write-Host ('FAIL: genuine negative cleanup additionally failed: '+($errors -join ' ')) }
      else { throw ($errors -join ' ') }
    } else { Write-Host 'PASS: genuine invalid negative cleans its exact process/thread handles, private raw files and per-executable registry values before positive qualification.' }
  }
}

function Invoke-OwnedLiveCrashControl([string]$AccountSid, [string]$AccountName, [string]$AccountPassword) {
  Invoke-OwnedGenuineInvalidControl $AccountSid $AccountName $AccountPassword
  # A managed entry stub invokes the actual native RaiseException API. This
  # qualifies debugger startup/capture only and is never product crash proof.
  $state=$null; $controlFailure=$null; $controlPid=$null
  try {
    $state=Start-OwnedCrashObservation $AccountSid 'Mixel-Native-Exception-Control.exe'
    $source=Join-Path $state.folder 'native-exception-control.cs'
    $executable=Join-Path $state.folder 'Mixel-Native-Exception-Control.exe'
    @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
public static class MixelOwnedNativeExceptionControl {
  [DllImport("kernel32.dll", SetLastError=true)] [return: MarshalAs(UnmanagedType.Bool)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll")] static extern void RaiseException(uint code, uint flags, uint count, IntPtr arguments);
  public static void Main() {
    File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-control-entered.txt"),Process.GetCurrentProcess().Id.ToString());
    bool closed=CloseHandle(IntPtr.Zero);
    int closeError=Marshal.GetLastWin32Error();
    File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-control-invalid-handle-return.txt"),Process.GetCurrentProcess().Id.ToString()+";"+(closed ? "1" : "0")+";"+closeError.ToString());
    // A distinct nonzero, non-pseudo invalid input qualifies the debugger
    // exception path; retain NULL as the separately observed API-return control.
    bool nonzeroClosed=CloseHandle(new IntPtr(1));
    int nonzeroError=Marshal.GetLastWin32Error();
    File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"native-control-nonzero-invalid-handle-return.txt"),Process.GetCurrentProcess().Id.ToString()+";"+(nonzeroClosed ? "1" : "0")+";"+nonzeroError.ToString());
    RaiseException(0xc0000374, 1, 0, IntPtr.Zero);
  }
}
'@ | Set-Content $source -Encoding utf8
    $compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler /nologo /target:winexe /platform:x64 "/out:$executable" $source
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $executable)) { throw 'Owned native exception control compilation failed.' }
    $startedAt=[DateTime]::UtcNow
    $controlPid=[MixelOrdinaryTokenFixture]::StartStandardUser($executable,$AccountName,$AccountPassword,$true)
    $control=Get-Process -Id $controlPid
    if ([MixelOrdinaryTokenFixture]::Elevated($controlPid) -or $control.SessionId -ne (Get-Process -Id $PID).SessionId) {
      throw 'Owned native exception control is not the actual suspended non-elevated same-session standard-user target.'
    }
    Start-OwnedLiveCrashObservation $controlPid $state
    $capture=Write-OwnedCrashDiagnostic $controlPid $executable $startedAt $state -SyntheticControl
    $entry=Join-Path $state.folder 'native-control-entered.txt'
    $entered=Test-Path $entry
    $entryPidVerified=$entered -and [IO.File]::ReadAllText($entry) -ceq [string]$controlPid
    $closeResult=Join-Path $state.folder 'native-control-invalid-handle-return.txt'
    $closeReturnVerified=(Test-Path $closeResult) -and [IO.File]::ReadAllText($closeResult) -ceq ($controlPid.ToString()+';0;6')
    $nonzeroCloseResult=Join-Path $state.folder 'native-control-nonzero-invalid-handle-return.txt'
    $nonzeroReturnVerified=(Test-Path $nonzeroCloseResult) -and [IO.File]::ReadAllText($nonzeroCloseResult) -ceq ($controlPid.ToString()+';0;6')
    $snapshot=Read-OwnedLiveDebuggerSnapshot $state.live.raw
    if ($snapshot.pending) { throw 'Synthetic stopped debugger invalid-handle snapshot is incomplete.' }
    $invalidHandles=Read-OwnedInvalidHandleCaptures $snapshot.lines $controlPid
    if (-not $closeReturnVerified -or -not $nonzeroReturnVerified -or -not $invalidHandles.verified -or
        @($invalidHandles.captures | Where-Object { $_.chance -cne 'first-chance' -or $_.handling -cne 'normalized-close-notification' -or -not $_.closeNotificationQualified }).Count -gt 0) {
      throw 'Synthetic invalid CloseHandle control did not qualify exact PID/code/native stack and normal FALSE/ERROR_INVALID_HANDLE return before its heap exception.'
    }
    if (-not $capture.actualLiveExceptionPidVerified -or $capture.pid -ne $controlPid -or
        $capture.exceptionCodes.Count -ne 1 -or $capture.exceptionCodes[0] -ine 'c0000374' -or $capture.stackFrames.Count -eq 0 -or -not $entryPidVerified) {
      throw 'Synthetic native exception control did not qualify exact owned PID/code and a sanitized native stack.'
    }
    Write-Host "PASS: synthetic native RaiseException(0xc0000374) control qualifies the exact suspended standard-user launch, owned primary-thread release after filter readiness and bounded PID/code-attributed native stack capture; controlPid=$controlPid; this is debugger qualification only."
    Write-Host "PASS: synthetic nonzero invalid CloseHandle control captures actual owned PID/c0000008/native frames and preserves normal FALSE/ERROR_INVALID_HANDLE(6), with a separate NULL return control, before the independently attributed heap exception; controlPid=$controlPid; this is debugger qualification only."
  } catch { $controlFailure=$_; throw }
  finally {
    $errors=[System.Collections.Generic.List[string]]::new()
    if ($state -and $controlPid) {
      if ($state.live) { try { Stop-OwnedLiveDebugger $state.live } catch { $errors.Add('Synthetic debugger exit/output observation failed.') } }
      try { Write-OwnedControlExecutionEvidence $controlPid $state } catch { $errors.Add('Synthetic bounded execution evidence capture failed.') }
      if ($controlFailure) { Write-OwnedWindowDiagnostic $controlPid 'synthetic native exception control after bounded debugger stop, before target cleanup' }
    }
    if ($state) { try { Stop-OwnedCrashObservation $state } catch { $errors.Add('Synthetic native exception private/registry cleanup failed.') } }
    try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $errors.Add('Synthetic retained native process cleanup failed.') }
    [MixelOrdinaryTokenFixture]::CloseStartedObservations()
    if ($errors.Count -gt 0) {
      if ($controlFailure) { Write-Host ('FAIL: synthetic native exception cleanup additionally failed: '+($errors -join ' ')) }
      else { throw ($errors -join ' ') }
    } else { Write-Host 'PASS: synthetic native exception control cleans its exact process/thread handles, private raw files and per-executable registry values before product diagnosis.' }
  }
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

function Stop-OwnedCustomerProcesses {
  # Observe termination through each exact process object's retained handle.
  # Stop-Process alone returns before exit and must not hide cleanup failures.
  $errors = [System.Collections.Generic.List[string]]::new()
  for ($round = 0; $round -le 3; $round++) {
    $ownedCount = 0
    foreach ($process in (Get-Process)) {
      $ownedPath = $null
      try { $ownedPath = $process.Path } catch { }
      if ($before -contains $process.Id -or -not $ownedPath) { $process.Dispose(); continue }
      $owned = $ownedPath.StartsWith((Split-Path $executablePath -Parent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        ($Portable -and $ownedPath.StartsWith((Split-Path $runtimePath -Parent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) -or
        ($ordinaryRoot -and $ownedPath.StartsWith($ordinaryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))
      if (-not $owned) { $process.Dispose(); continue }
      $ownedCount++
      # The last enumeration verifies the third termination round without
      # accepting a surviving owned process or manufacturing a false failure.
      if ($round -eq 3) { $process.Dispose(); continue }
      try {
        if ($process.HasExited) { continue }
        $null = $process.Handle
        try { $process.Kill() } catch { if (-not $process.HasExited) { throw } }
        if (-not $process.WaitForExit(10000)) { throw 'Owned customer process termination was not observed.' }
      } catch { $errors.Add("Owned customer process $($process.Id) cleanup failed.") }
      finally { $process.Dispose() }
    }
    if ($errors.Count -gt 0) { throw ($errors -join ' ') }
    if ($ownedCount -eq 0) { return }
  }
  throw 'Owned customer processes remain after bounded termination and fresh process enumeration.'
}

try {
  if ($OrdinaryThenQuickSupport) {
    if (-not $QuickSupport -or -not $ExpectedPayload -or (-not $Portable -and -not $CompiledQuickSupportDiagnostic)) {
      throw 'Ordinary-to-QuickSupport proof requires the actual portable QS launcher and exact signed payload, or explicit compiled-only diagnosis.'
    }
    $fixtureStage = 'verify absent prior foreground consent lease'
    if ([MixelOrdinaryTokenFixture]::LeasePresent()) {
      throw 'Ordinary-to-QS baseline found a prior global consent owner before starting the ordinary app.'
    }
    Write-Host 'PASS: ordinary-to-QS baseline has no prior global consent owner before actual ordinary startup.'
    # Pinned older clients detect '-qs-' anywhere in argv[0], including parent
    # directories. Use a neutral path so retained signed payloads can prove a
    # genuinely ordinary unguarded startup before the actual QS handoff.
    $ordinaryRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-ordinary-client-' + [Guid]::NewGuid().ToString('N'))
    $fixtureStage = 'copy signed ordinary payload'
    New-Item -ItemType Directory $ordinaryRoot | Out-Null
    Copy-Item (Join-Path (Resolve-Path $ExpectedPayload).Path '*') $ordinaryRoot -Recurse
    $ordinaryExecutable = Join-Path $ordinaryRoot 'Mixel-Remote.exe'
    & (Join-Path $PSScriptRoot 'verify-windows-payload.ps1') -Payload $ordinaryRoot
    Write-Host "FIXTURE: owned ordinary GUI uses the same actual runner desktop granted by DesktopAccess: $([MixelOrdinaryTokenFixture]::CurrentDesktopPath()); callerSessionId=$((Get-Process -Id $PID).SessionId)."
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
        # New-LocalUser validates Description at 48 characters. The previous
        # 51-character fixture comment failed before any account was created.
        $account = New-LocalUser -Name $ownedUser -Password (ConvertTo-SecureString $ownedPassword -AsPlainText -Force) -AccountNeverExpires -PasswordNeverExpires -Description 'Mixel owned QuickSupport runtime fixture'
        $ownedSid = $account.SID.Value
        $fixtureStage = 'add owned standard account to users'
        Add-LocalGroupMember -SID ([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')) -Member $ownedUser
        $fixtureStage = 'grant owned standard desktop access'
        $desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
        if ($NativeCrashDiagnostic) {
          $fixtureStage = 'qualify actual native debugger with synthetic exception control'
          Invoke-OwnedLiveCrashControl $ownedSid $ownedUser $ownedPassword
          $nativeCrash=Start-OwnedCrashObservation $ownedSid
        }
        $fixtureStage = 'start owned standard GUI'
        $ordinaryStartedAt=[DateTime]::UtcNow
        try {
          $ordinaryPid = [MixelOrdinaryTokenFixture]::StartStandardUser($ordinaryExecutable, $ownedUser, $ownedPassword, [bool]$NativeCrashDiagnostic)
          if ($NativeCrashDiagnostic) {
            $main=Get-Process -Id $ordinaryPid
            $ordinaryProfileRoot=[MixelOrdinaryTokenFixture]::ProfilePath($ordinaryPid)
            if ([MixelOrdinaryTokenFixture]::Elevated($ordinaryPid) -or $main.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'Owned suspended diagnostic target is not an ordinary same-session process.' }
            Start-OwnedLiveCrashObservation $ordinaryPid $nativeCrash
          }
        }
        finally { $ownedPassword = $null }
      }
      $main = Get-Process -Id $ordinaryPid
    }
    $fixtureStage = 'verify actual ordinary process token'
    if ([MixelOrdinaryTokenFixture]::Elevated($main.Id)) { throw 'Ordinary startup still uses an elevated token and could silently auto-enter QuickSupport.' }
    if ($main.SessionId -ne (Get-Process -Id $PID).SessionId) { throw 'Ordinary GUI was launched in a different session from the actual runner desktop.' }
    $ordinaryProfileRoot = [MixelOrdinaryTokenFixture]::ProfilePath($main.Id)
    Write-Host "FIXTURE: actual ordinary process is non-elevated in caller session; foregroundPid=$($main.Id), ordinarySessionId=$($main.SessionId)."
    Write-OwnedWindowDiagnostic $main.Id 'initial ordinary startup'
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
    $initialOwnsLease = [MixelOrdinaryTokenFixture]::OwnsLease($main.Id)
    $initialLeasePresent = [MixelOrdinaryTokenFixture]::LeasePresent()
    Write-Host "FIXTURE: read-only ordinary baseline; guardEmpty=$($initialHealth.attendedProof -ceq ''), guardConfirmed=$($initialHealth.attendedReady), foregroundOwnsLease=$initialOwnsLease, globalLeasePresent=$initialLeasePresent; foregroundPid=$($main.Id)."
    if ($initialHealth.attendedProof -cne '' -or $initialOwnsLease) {
      throw 'Ordinary-to-QS positive control is already guarded; refusing a manufactured transition proof.'
    }
    Write-Host "PASS: actual ordinary non-elevated signed GUI starts genuinely unguarded, with empty read-only IPC guard and no foreground lease; foregroundPid=$($main.Id), incomingPipePid=$($initialHealth.incomingPid), hwnd=$($window.ToInt64())."
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
    $handoffKind = if ($Portable) { 'actual customer QS portable launcher' } else { 'exact signed compiled QS argument (diagnostic)' }
    Write-Host "PASS: genuine ordinary GUI -> $handoffKind restores the same PID/HWND, and that foreground PID owns the kernel consent lease with v2 readiness after $([int]$started.Elapsed.TotalSeconds)s beyond the 90s memory deadline; transient launcher exited; foregroundPid=$($main.Id), incomingPipePid=$($finalHealth.incomingPid), hwnd=$($originalWindow.ToInt64())."
  }

  foreach ($logRoot in @(
      (Join-Path $env:APPDATA 'Mixel-Remote'),
      (Join-Path $env:LOCALAPPDATA 'Mixel-Remote'),
      (Join-Path $env:APPDATA 'MixelRemote'),
      $(if ($ordinaryProfileRoot) { Join-Path $ordinaryProfileRoot 'AppData/Roaming/Mixel-Remote' }),
      $(if ($ordinaryProfileRoot) { Join-Path $ordinaryProfileRoot 'AppData/Local/Mixel-Remote' }),
      $(if ($ordinaryProfileRoot) { Join-Path $ordinaryProfileRoot 'AppData/Roaming/MixelRemote' }))) {
    if (-not $logRoot) { continue }
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
  if ($main -and $OrdinaryThenQuickSupport) { Write-OwnedWindowDiagnostic $main.Id 'failed ordinary-to-QS scenario' }
  if ($main -and $ordinaryStartedAt) {
    try { [void](Write-OwnedCrashDiagnostic $main.Id $ordinaryExecutable $ordinaryStartedAt $nativeCrash) }
    catch { Write-Host "FIXTURE: owned native crash extraction additionally failed; errorId=$($_.FullyQualifiedErrorId)." }
  }
  throw
} finally {
  if ($nativeCrash) {
    if ($ordinaryPid) {
      if ($nativeCrash.live) {
        try { Stop-OwnedLiveDebugger $nativeCrash.live }
        catch { $nativeCrashCleanupFailed=$true; Write-Host 'FAIL: owned product debugger exit/output observation additionally failed.' }
      }
      try { Write-OwnedControlExecutionEvidence $ordinaryPid $nativeCrash -ProductTarget }
      catch { $nativeCrashCleanupFailed=$true; Write-Host 'FAIL: bounded owned product execution evidence capture additionally failed.' }
    }
    try { Stop-OwnedCrashObservation $nativeCrash }
    catch { $nativeCrashCleanupFailed=$true; Write-Host 'FAIL: owned native crash observation cleanup additionally failed.' }
  }
  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
  catch { $nativeCrashCleanupFailed=$true; Write-Host 'FAIL: actual retained owned native process termination additionally failed.' }
  $customerCleanupFailure = $null
  try { Stop-OwnedCustomerProcesses }
  catch { $customerCleanupFailure = $_ }
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  if ($ordinaryRoot -or $ownedUser -or $desktopAccess) {
    $cleanupErrors = [System.Collections.Generic.List[string]]::new()
    if ($nativeCrashCleanupFailed) { $cleanupErrors.Add('Owned native crash observation cleanup failed.') }
    Start-Sleep -Seconds 2
    # Preserve only logs from the actual owned runtime token's profile before
    # deleting it. No account credentials, configuration files or raw argv are
    # copied. CI can inspect a failed GUI launch without recreating its user.
    if ($ownedSid -and $ordinaryProfileRoot -and $env:RUNNER_TEMP) {
      try {
        $archive = Join-Path $env:RUNNER_TEMP 'mixel-owned-ordinary-qs-logs'
        foreach ($root in @('AppData/Roaming/Mixel-Remote', 'AppData/Local/Mixel-Remote', 'AppData/Roaming/MixelRemote')) {
          $sourceRoot = Join-Path $ordinaryProfileRoot $root
          if (-not (Test-Path $sourceRoot)) { continue }
          foreach ($file in (Get-ChildItem $sourceRoot -Recurse -Filter '*.log' -File)) {
            $relative = [IO.Path]::GetRelativePath($ordinaryProfileRoot, $file.FullName)
            $destination = Join-Path $archive $relative
            New-Item -ItemType Directory -Force (Split-Path $destination -Parent) | Out-Null
            Copy-Item $file.FullName $destination -Force
          }
        }
      } catch { $cleanupErrors.Add('Owned ordinary runtime log preservation failed.') }
    }
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
  if ($customerCleanupFailure) {
    if ($primaryFailure) { Write-Host 'FAIL: owned customer process cleanup additionally failed.' }
    else { throw $customerCleanupFailure }
  }
}

if ($QuickSupport -and -not $OrdinaryThenQuickSupport) {
  & $PSCommandPath -Executable $Executable -Portable -QuickSupport -ExpectedPayload $ExpectedPayload -OrdinaryThenQuickSupport
}
