# Source QA only. Signed customer application qualification is separate.
param([Parameter(Mandatory)][string]$ExpectedManifestSha256,[string]$BundleRoot=$PSScriptRoot,[ValidateSet('console','windows')][string]$Subsystem='windows')
$ErrorActionPreference='Stop'
if([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess -or $PSVersionTable.PSVersion.Major -lt 5) { throw 'Requires actual x64 Windows PowerShell5 or later.' }
function Assert-PortableLeaseInteger($Value,[long]$Minimum,[long]$Maximum) {
  if(($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt $Minimum -or $Value -gt $Maximum) { throw 'Portable bundle integer type or bound changed.' }
}
function Read-PortableLeaseBytes([string]$Path,[long]$Maximum) {
  $item=Get-Item -LiteralPath $Path
  if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt $Maximum) { throw 'Portable bundle requires bounded ordinary files.' }
  $bytes=[IO.File]::ReadAllBytes($Path)
  if($bytes.Length -gt $Maximum) { throw 'Portable bundle member grew beyond its bound.' }
  return ,$bytes
}
function Read-PortableLeaseText([string]$Path,[long]$Maximum) {
  return [Text.UTF8Encoding]::new($false,$true).GetString((Read-PortableLeaseBytes $Path $Maximum))
}
function Assert-PortableLeasePe([byte[]]$Bytes,[int]$Subsystem) {
  if($Bytes.Length -lt 128 -or $Bytes.Length -gt 16777216 -or $Bytes[0] -ne 77 -or $Bytes[1] -ne 90) { throw 'Portable fixture PE bounds changed.' }
  $pe=[BitConverter]::ToUInt32($Bytes,60)
  if($pe -lt 64 -or $pe -gt $Bytes.Length-24 -or [BitConverter]::ToUInt32($Bytes,[int]$pe) -ne 17744 -or [BitConverter]::ToUInt16($Bytes,[int]$pe+4) -ne 34404) { throw 'Portable fixture requires bounded x64 PE.' }
  $size=[BitConverter]::ToUInt16($Bytes,[int]$pe+20); $optional=[long]$pe+24
  if($size -lt 70 -or $optional+$size -gt $Bytes.Length -or [BitConverter]::ToUInt16($Bytes,[int]$optional) -ne 523 -or [BitConverter]::ToUInt16($Bytes,[int]$optional+68) -ne $Subsystem) { throw 'Portable fixture subsystem or PE32+ contract changed.' }
}
function Assert-PortableLeaseBundle([string]$Root,[string]$ExpectedManifestSha256) {
  if($ExpectedManifestSha256 -cnotmatch '\A[0-9a-f]{64}\z') { throw 'A separately trusted canonical manifest SHA256 is required.' }
  $Root=[IO.Path]::GetFullPath($Root)
  $folder=Get-Item -LiteralPath $Root
  if(-not $folder.PSIsContainer -or ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Portable bundle root must be an ordinary directory.' }
  $manifestPath=Join-Path $Root 'manifest.json'
  $manifestBytes=Read-PortableLeaseBytes $manifestPath 65536
  $hash=[Security.Cryptography.SHA256]::Create()
  try { $actual=([BitConverter]::ToString($hash.ComputeHash($manifestBytes))).Replace('-','').ToLowerInvariant() } finally { $hash.Dispose() }
  if($actual -cne $ExpectedManifestSha256) { throw 'Portable bundle manifest differs from the separately trusted SHA256.' }
  $manifest=[Text.UTF8Encoding]::new($false,$true).GetString($manifestBytes)|ConvertFrom-Json
  if((@($manifest.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'args,buildSourceBindings,commit,compiler,eventName,files,jobName,portableEntryAssertion,purpose,runId,runtimeQualified,schemaVersion,tree' -or
     $manifest.purpose -isnot [string] -or $manifest.purpose -cne 'compile-only-standard-lease-qa' -or
     $manifest.runtimeQualified -isnot [bool] -or $manifest.runtimeQualified -or
     $manifest.eventName -isnot [string] -or $manifest.eventName -cnotmatch '\AGlobal\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z' -or
     $manifest.commit -isnot [string] -or $manifest.commit -cnotmatch '\A[0-9a-f]{40}\z' -or $manifest.tree -isnot [string] -or $manifest.tree -cnotmatch '\A[0-9a-f]{40}\z' -or
     $manifest.jobName -isnot [string] -or $manifest.jobName -cne 'portable-standard-lease-qa' -or
     $manifest.compiler -isnot [string] -or $manifest.compiler -cnotmatch '\Arustc 1\.75\.0 [^\r\n]{1,128}\z' -or
     $manifest.portableEntryAssertion -isnot [string] -or $manifest.portableEntryAssertion -cne 'assert!(std::env::args().len() == 1, "Portable source QA accepts no command-line arguments");') { throw 'Portable bundle identity, scope or schema changed.' }
  Assert-PortableLeaseInteger $manifest.schemaVersion 1 1
  Assert-PortableLeaseInteger $manifest.runId 1 ([long]::MaxValue)
  $arguments=@('--edition','2021','-D','warnings','-C','target-feature=+crt-static')
  if($manifest.args -isnot [array] -or $manifest.args.Count -ne $arguments.Count) { throw 'Portable fixture compiler arguments changed.' }
  for($i=0;$i -lt $arguments.Count;$i++) { if($manifest.args[$i] -isnot [string] -or $manifest.args[$i] -cne $arguments[$i]) { throw 'Portable fixture compiler arguments changed.' } }
  $allow=@('run-standard-lease-qa.ps1','run-admin-negative-lease-qa.ps1','scripts/support-invite-guard.rs','scripts/test-support-lease.py','scripts/test-support-lease-standard-windows.ps1','fixture/lease-standard.rs','fixture/lease-standard.exe','fixture/lease-standard-gui.rs','fixture/lease-standard-gui.exe','lease-standard-compile.log','lease-standard-gui-compile.log','hosted-loader-smoke.json')
  if($manifest.files -isnot [array] -or $manifest.files.Count -ne $allow.Count) { throw 'Portable bundle member count changed.' }
  $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach($member in $manifest.files) {
    if((@($member.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'bytes,path,sha256' -or $member.path -isnot [string] -or $member.path -cnotin $allow -or -not $seen.Add($member.path) -or $member.sha256 -isnot [string] -or $member.sha256 -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Portable bundle member schema or allowlist changed.' }
    $minimum=if($member.path.EndsWith('-compile.log',[StringComparison]::Ordinal)){0}else{1}
    Assert-PortableLeaseInteger $member.bytes $minimum 16777216
    $path=Join-Path $Root $member.path
    $bytes=Read-PortableLeaseBytes $path 16777216
    if($bytes.Length -ne $member.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $member.sha256) { throw 'Portable bundle member hash or byte count changed.' }
  }
  $sourceAllow=@('scripts/support-invite-guard.rs','scripts/test-support-lease.py','scripts/test-support-lease-standard-windows.ps1','scripts/test-support-lease-interactive-windows.ps1','scripts/test-support-lease-negative-windows.ps1','scripts/build-standard-lease-qa-bundle.py','.github/workflows/preflight.yml','scripts/activate-windows-msvc.ps1','scripts/test-standard-lease-qa-bundle.py','scripts/test-support-lease-interactive-source.ps1','scripts/test-support-launch-windows.ps1')
  if($manifest.buildSourceBindings -isnot [array] -or $manifest.buildSourceBindings.Count -ne $sourceAllow.Count) { throw 'Portable build source binding count changed.' }
  $sourceSeen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach($binding in $manifest.buildSourceBindings) {
    if((@($binding.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'bytes,path,sha256' -or $binding.path -isnot [string] -or $binding.path -cnotin $sourceAllow -or -not $sourceSeen.Add($binding.path) -or $binding.sha256 -isnot [string] -or $binding.sha256 -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Portable build source binding schema or allowlist changed.' }
    Assert-PortableLeaseInteger $binding.bytes 1 1048576
    $packagePath=if($binding.path -ceq 'scripts/test-support-lease-interactive-windows.ps1'){'run-standard-lease-qa.ps1'}elseif($binding.path -ceq 'scripts/test-support-lease-negative-windows.ps1'){'run-admin-negative-lease-qa.ps1'}elseif($binding.path -cin @('scripts/support-invite-guard.rs','scripts/test-support-lease.py','scripts/test-support-lease-standard-windows.ps1')){$binding.path}else{$null}
    if($packagePath) {
      $member=@($manifest.files|Where-Object {$_.path -ceq $packagePath})
      if($member.Count -ne 1 -or $member[0].bytes -ne $binding.bytes -or $member[0].sha256 -cne $binding.sha256) { throw 'Packaged source differs from the exact build source binding.' }
    }
  }
  $inventory=[Collections.Generic.List[string]]::new()
  $pending=[Collections.Generic.Queue[string]]::new(); $pending.Enqueue($Root)
  while($pending.Count) {
    foreach($item in Get-ChildItem -LiteralPath $pending.Dequeue() -Force) {
      if($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Portable bundle refuses reparse members.' }
      $relative=$item.FullName.Substring($Root.TrimEnd([IO.Path]::DirectorySeparatorChar).Length+1).Replace([IO.Path]::DirectorySeparatorChar,'/')
      if($item.PSIsContainer) { if($relative -cnotin @('scripts','fixture')) { throw 'Portable bundle directory allowlist changed.' }; $pending.Enqueue($item.FullName) }
      else { $inventory.Add($relative) }
    }
  }
  if((@($inventory|Sort-Object)-join ',') -cne (@(@($allow)+@('manifest.json')|Sort-Object)-join ',')) { throw 'Portable bundle contains missing or extra files.' }
  $expectedSource=@{
  'scripts/support-invite-guard.rs' = '4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29'
  'scripts/test-support-lease.py' = '4358b9452935bdc95a1209d50ca12dd22356a7c0fbe77192e4404b47386c44e6'
  'scripts/test-support-lease-standard-windows.ps1' = '75359cd04fc1b650f119af77eab55081db99c13eb0bf69242cd7c01e8491ba60'
  }
  foreach($name in $expectedSource.Keys) { if((Get-FileHash -LiteralPath (Join-Path $Root $name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expectedSource[$name]) { throw 'Portable source pin changed.' } }
  $console=Read-PortableLeaseText (Join-Path $Root 'fixture/lease-standard.rs') 1048576
  $gui=Read-PortableLeaseText (Join-Path $Root 'fixture/lease-standard-gui.rs') 1048576
  if($gui -cne ('#![windows_subsystem = "windows"]' + "`n" + $console)) { throw 'Portable GUI fixture differs beyond its exact subsystem header.' }
  $constant='const EVENT: &str = "'+$manifest.eventName.Replace('\','\\')+'";'
  if([regex]::Matches($console,'const EVENT: &str = ').Count -ne 1 -or -not $console.Contains($constant) -or -not $console.Contains(('fn main() {'+"`n    "+$manifest.portableEntryAssertion+"`n    // Keep the existing native negative-control branches compiled with -D warnings."))) { throw 'Portable fixture event or argument-refusal boundary changed.' }
  Assert-PortableLeasePe (Read-PortableLeaseBytes (Join-Path $Root 'fixture/lease-standard.exe') 16777216) 3
  Assert-PortableLeasePe (Read-PortableLeaseBytes (Join-Path $Root 'fixture/lease-standard-gui.exe') 16777216) 2
  return [pscustomobject]@{Root=$Root;Manifest=$manifest;ManifestSha256=$actual}
}

$bundle=Assert-PortableLeaseBundle $BundleRoot $ExpectedManifestSha256
Add-Type @'
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
public static class MixelInteractiveLeaseProcess {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct StartupInfo {
    public int cb; public string reserved, desktop, title;
    public int x,y,xSize,ySize,xCount,yCount,fill,flags;
    public short show,reservedSize; public IntPtr reservedPointer,input,output,error;
  }
  [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process,thread; public uint pid,tid; }
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcessW(string application,StringBuilder command,IntPtr processAttributes,IntPtr threadAttributes,bool inheritHandles,uint creation,IntPtr environment,string directory,ref StartupInfo startup,out ProcessInfo process);
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  [DllImport("kernel32.dll")] static extern uint GetCurrentProcessId();
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("kernel32.dll",SetLastError=true)] static extern uint GetProcessId(IntPtr process);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool ProcessIdToSessionId(uint pid,out uint session);
  [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr process,uint milliseconds);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
  [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
  [DllImport("advapi32.dll",SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr data,int size,out int required);
  [DllImport("user32.dll",SetLastError=true)] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
  [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
  [DllImport("user32.dll",SetLastError=true)] static extern IntPtr GetThreadDesktop(uint thread);
  [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool GetUserObjectInformationW(IntPtr handle,int kind,StringBuilder text,uint size,out uint needed);
  [DllImport("user32.dll",SetLastError=true)] static extern bool CloseDesktop(IntPtr handle);
  static readonly Dictionary<int,IntPtr> StartedProcesses=new Dictionary<int,IntPtr>();
  static readonly Dictionary<int,IntPtr> StartedThreads=new Dictionary<int,IntPtr>();
  static readonly List<IntPtr> PendingProcesses=new List<IntPtr>();
  static readonly List<IntPtr> PendingThreads=new List<IntPtr>();
  static readonly List<IntPtr> PendingTokens=new List<IntPtr>();
  enum ObservedTokenClass { Groups=2, Type=8, SessionId=12, ElevationType=18, LinkedToken=19, Elevation=20, HasRestrictions=21, IntegrityLevel=25, LogonSid=28, IsAppContainer=29 }
  [StructLayout(LayoutKind.Sequential)] struct ObservedSidAttributes { public IntPtr sid; public uint attributes; }
  [StructLayout(LayoutKind.Sequential)] struct ObservedGroupsHeader { public uint count; public ObservedSidAttributes first; }
  sealed class ObservedTokenQueryFailure : InvalidOperationException {
    public ObservedTokenClass kind;
    public string failure;
    public int? error, length;
    public ObservedTokenQueryFailure(ObservedTokenClass kind, string failure, int? error, int? length) {
      this.kind=kind; this.failure=failure; this.error=error; this.length=length;
    }
  }
  static uint ObservedTokenDword(IntPtr token, ObservedTokenClass kind) {
    IntPtr data=Marshal.AllocHGlobal(4);
    try {
      int required;
      bool success=GetTokenInformation(token,(int)kind,data,4,out required);
      int error=Marshal.GetLastWin32Error();
      if (!success) throw new ObservedTokenQueryFailure(kind,"api-error",error,required);
      if (required!=4) throw new ObservedTokenQueryFailure(kind,"returned-size",null,required);
      return unchecked((uint)Marshal.ReadInt32(data));
    } finally { Marshal.FreeHGlobal(data); }
  }
  static IntPtr ObservedTokenBuffer(IntPtr token, ObservedTokenClass kind, out int length) {
    bool initial=GetTokenInformation(token,(int)kind,IntPtr.Zero,0,out length);
    int error=Marshal.GetLastWin32Error();
    if(initial || error!=122 || length<=0 || length>65536) throw new ObservedTokenQueryFailure(kind,"size-contract",initial ? (int?)null : error,length);
    int allocated=length; IntPtr data=Marshal.AllocHGlobal(allocated);
    try {
      bool success=GetTokenInformation(token,(int)kind,data,allocated,out length);
      int outputError=Marshal.GetLastWin32Error();
      if(!success) throw new ObservedTokenQueryFailure(kind,"api-error",outputError,length);
      if(length<=0 || length>allocated) throw new ObservedTokenQueryFailure(kind,"size-contract",null,length);
      return data;
    } catch { Marshal.FreeHGlobal(data); throw; }
  }
  static string ObservedBufferedSid(IntPtr data, int length, IntPtr sid) {
    long offset=sid.ToInt64()-data.ToInt64();
    if(offset<0 || offset>length-8 || Marshal.ReadByte(sid)!=1) throw new InvalidOperationException("Read-only token SID header bounds failed");
    int subAuthorities=Marshal.ReadByte(sid,1);
    if(subAuthorities>15 || 8+subAuthorities*4>length-offset) throw new InvalidOperationException("Read-only token SID content bounds failed");
    return new SecurityIdentifier(sid).Value;
  }
  static byte[] ObservedLogonSidBytes(IntPtr data,int length,out uint count) {
    int offset=Marshal.OffsetOf(typeof(ObservedGroupsHeader),"first").ToInt32();
    int stride=Marshal.SizeOf(typeof(ObservedSidAttributes));
    if(IntPtr.Size!=8 || offset!=8 || stride!=16 || length<offset) throw new InvalidOperationException("Read-only logon SID header bounds failed");
    count=unchecked((uint)Marshal.ReadInt32(data));
    if(count>1 || count>(uint)((length-offset)/stride)) throw new InvalidOperationException("Read-only logon SID count bounds failed");
    if(count==0) return null;
    var group=(ObservedSidAttributes)Marshal.PtrToStructure(IntPtr.Add(data,offset),typeof(ObservedSidAttributes));
    long sidOffset=group.sid.ToInt64()-data.ToInt64();
    if((group.attributes & 0xc0000000)!=0xc0000000 || sidOffset<offset+stride || sidOffset>length-20) throw new InvalidOperationException("Read-only logon SID pointer/attribute bounds failed");
    // Canonical logon SID is S-1-5-5-X-Y. Its private X/Y bytes stay local;
    // only count and comparison equality are serialized, never the SID or LUID.
    if(Marshal.ReadByte(group.sid)!=1 || Marshal.ReadByte(group.sid,1)!=3 ||
       Marshal.ReadByte(group.sid,2)!=0 || Marshal.ReadByte(group.sid,3)!=0 ||
       Marshal.ReadByte(group.sid,4)!=0 || Marshal.ReadByte(group.sid,5)!=0 ||
       Marshal.ReadByte(group.sid,6)!=0 || Marshal.ReadByte(group.sid,7)!=5 ||
       Marshal.ReadInt32(group.sid,8)!=5) throw new InvalidOperationException("Read-only logon SID canonical shape failed");
    byte[] value=new byte[20]; Marshal.Copy(group.sid,value,0,value.Length); return value;
  }
  static byte[] ObservedLogonSid(IntPtr token,out uint count) {
    int length;IntPtr data=ObservedTokenBuffer(token,ObservedTokenClass.LogonSid,out length);
    try { return ObservedLogonSidBytes(data,length,out count); } finally { Marshal.FreeHGlobal(data); }
  }
  static bool ObservedLogonSidEqual(byte[] first,byte[] second) {
    if(first==null || second==null || first.Length!=20 || second.Length!=20) throw new InvalidOperationException("Read-only logon SID equality requires two bounded actual identities");
    for(int i=0;i<20;i++) if(first[i]!=second[i]) return false;
    return true;
  }

  static void CloseQueriedToken(IntPtr token) {
    PendingTokens.Add(token);
    if(!CloseHandle(token)) throw new Win32Exception(Marshal.GetLastWin32Error(),"Owned query-token handle close failed");
    PendingTokens.Remove(token);
  }
  static Dictionary<string,string> ReadGroups(IntPtr data,int length) {
    int offset=Marshal.OffsetOf(typeof(ObservedGroupsHeader),"first").ToInt32();
    int stride=Marshal.SizeOf(typeof(ObservedSidAttributes));
    if(IntPtr.Size!=8 || offset!=8 || stride!=16 || length<offset) throw new InvalidOperationException("Interactive group header bounds failed");
    uint count=unchecked((uint)Marshal.ReadInt32(data));
    if(count>4096 || count>(uint)((length-offset)/stride)) throw new InvalidOperationException("Interactive group count bounds failed");
    var groups=new Dictionary<string,string>();
    for(uint i=0;i<count;i++) {
      var group=(ObservedSidAttributes)Marshal.PtrToStructure(IntPtr.Add(data,offset+checked((int)i)*stride),typeof(ObservedSidAttributes));
      if(group.sid.ToInt64()-data.ToInt64()<offset+count*(long)stride) throw new InvalidOperationException("Interactive group SID overlaps its header/table");
      string sid=ObservedBufferedSid(data,length,group.sid);
      if(sid!="S-1-5-32-544" && sid!="S-1-5-32-545" && sid!="S-1-5-4") continue;
      if(groups.ContainsKey(sid) || (group.attributes & 0x14)==0x14) throw new InvalidOperationException("Interactive group attributes are inconsistent");
      groups.Add(sid,(group.attributes & 0x10)!=0 ? "deny-only" : (group.attributes & 4)!=0 ? "enabled" : "disabled");
    }
    return groups;
  }
  static void AssertStandardClassification(uint type,uint elevation,bool adminPresent,string users,string interactive,bool authenticated) {
    if(type!=1 || elevation!=0 || adminPresent || users!="enabled" || interactive!="enabled" || !authenticated)
      throw new InvalidOperationException("Current interactive account is not a verified genuine standard primary token");
  }
  static void AssertStandardToken(IntPtr token) {
    int length; IntPtr data=ObservedTokenBuffer(token,ObservedTokenClass.Groups,out length);
    Dictionary<string,string> groups;
    try { groups=ReadGroups(data,length); } finally { Marshal.FreeHGlobal(data); }
    string users,interactive;
    groups.TryGetValue("S-1-5-32-545",out users); groups.TryGetValue("S-1-5-4",out interactive);
    using(var identity=new WindowsIdentity(token)) {
      if(identity.IsSystem || identity.User==null) throw new InvalidOperationException("System or missing identity cannot qualify standard interactive QA");
      AssertStandardClassification(ObservedTokenDword(token,ObservedTokenClass.Type),ObservedTokenDword(token,ObservedTokenClass.Elevation),groups.ContainsKey("S-1-5-32-544"),users,interactive,identity.IsAuthenticated);
    }
  }
  static string ObjectName(IntPtr handle) {
    if(handle==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
    var text=new StringBuilder(256); uint needed;
    if(!GetUserObjectInformationW(handle,2,text,512,out needed)) throw new Win32Exception(Marshal.GetLastWin32Error());
    if(needed<2 || needed>512 || text.Length==0 || text.Length>=256) throw new InvalidOperationException("Interactive desktop name exceeds its bound");
    return text.ToString();
  }
  static void AssertInteractiveDesktop() {
    IntPtr input=OpenInputDesktop(0,false,1);
    if(input==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
    try {
      if(!String.Equals(ObjectName(GetProcessWindowStation()),"WinSta0",StringComparison.OrdinalIgnoreCase) ||
         !String.Equals(ObjectName(GetThreadDesktop(GetCurrentThreadId())),ObjectName(input),StringComparison.Ordinal))
        throw new InvalidOperationException("Current standard process is not on the actual interactive input desktop");
    } finally { if(!CloseDesktop(input)) throw new Win32Exception(Marshal.GetLastWin32Error()); }
  }
  static uint Session(uint pid) { uint session; if(!ProcessIdToSessionId(pid,out session)) throw new Win32Exception(Marshal.GetLastWin32Error()); return session; }
  public static string AssertParent() {
    if(IntPtr.Size!=8) throw new InvalidOperationException("Interactive standard QA requires x64");
    IntPtr token; if(!OpenProcessToken(GetCurrentProcess(),8,out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
    try {
      AssertStandardToken(token); AssertInteractiveDesktop();
      uint session=Session(GetCurrentProcessId());
      if(session==0 || ObservedTokenDword(token,ObservedTokenClass.SessionId)!=session) throw new InvalidOperationException("Current standard token/process session mismatch");
      uint count; if(ObservedLogonSid(token,out count)==null || count!=1) throw new InvalidOperationException("Current interactive logon identity is unavailable");
      return "primary=True;elevated=False;administratorsSidPresent=False;usersEnabled=True;interactiveEnabled=True;inputDesktopMatched=True;processSessionId="+session.ToString(System.Globalization.CultureInfo.InvariantCulture)+";tokenSessionMatched=True;logonSidCount=1";
    } finally { CloseQueriedToken(token); }
  }
  public static int StartOrdinary(string executable) {
    AssertParent();
    if(!Path.IsPathRooted(executable) || Path.GetFullPath(executable)!=executable || !File.Exists(executable) || executable.IndexOfAny(new char[]{'"','\r','\n'})>=0) throw new InvalidOperationException("Expected exact owned executable path");
    var startup=new StartupInfo(); startup.cb=Marshal.SizeOf(typeof(StartupInfo));
    ProcessInfo process;
    bool success=CreateProcessW(executable,new StringBuilder("\""+executable+"\""),IntPtr.Zero,IntPtr.Zero,false,0,IntPtr.Zero,Path.GetDirectoryName(executable),ref startup,out process);
    int error=Marshal.GetLastWin32Error();
    if(!success) throw new Win32Exception(error,"Ordinary inherited-context CreateProcessW failed");
    // Successful creation gives ownership of these exact returned handles even
    // if later attribution/close fails. Keep them for bounded outer cleanup.
    if(process.process!=IntPtr.Zero) PendingProcesses.Add(process.process);
    if(process.thread!=IntPtr.Zero) PendingThreads.Add(process.thread);
    if(process.process==IntPtr.Zero || process.thread==IntPtr.Zero || process.pid==0 || GetProcessId(process.process)!=process.pid) throw new InvalidOperationException("Ordinary child handle/PID contract failed");
    int pid=checked((int)process.pid);
    StartedProcesses.Add(pid,process.process); PendingProcesses.Remove(process.process);
    StartedThreads.Add(pid,process.thread); PendingThreads.Remove(process.thread);
    if(!CloseHandle(process.thread)) throw new Win32Exception(Marshal.GetLastWin32Error(),"Owned primary-thread handle close failed");
    StartedThreads.Remove(pid);
    return pid;
  }
  public static string AssertChild(int pid) {
    IntPtr process; if(!StartedProcesses.TryGetValue(pid,out process) || GetProcessId(process)!=(uint)pid) throw new InvalidOperationException("Child observation lacks the exact retained handle");
    IntPtr token; if(!OpenProcessToken(process,8,out token)) throw new Win32Exception(Marshal.GetLastWin32Error());
    try {
      AssertStandardToken(token); IntPtr caller;
      if(!OpenProcessToken(GetCurrentProcess(),8,out caller)) throw new Win32Exception(Marshal.GetLastWin32Error());
      try {
        AssertStandardToken(caller);
        using(var parent=new WindowsIdentity(caller)) using(var child=new WindowsIdentity(token)) {
          if(parent.User.Value!=child.User.Value) throw new InvalidOperationException("Ordinary child private user identity mismatch");
        }
        uint childSession=Session((uint)pid),parentSession=Session(GetCurrentProcessId());
        if(childSession!=parentSession || ObservedTokenDword(token,ObservedTokenClass.SessionId)!=childSession || ObservedTokenDword(caller,ObservedTokenClass.SessionId)!=parentSession) throw new InvalidOperationException("Ordinary child token/process session mismatch");
        uint parentCount,childCount;
        byte[] parentSid=ObservedLogonSid(caller,out parentCount),childSid=ObservedLogonSid(token,out childCount);
        if(parentCount!=1 || childCount!=1 || !ObservedLogonSidEqual(parentSid,childSid)) throw new InvalidOperationException("Ordinary child logon identity mismatch");
        return "ownedPid="+pid.ToString(System.Globalization.CultureInfo.InvariantCulture)+";userSidMatched=True;primary=True;elevated=False;administratorsSidPresent=False;processSessionId="+childSession.ToString(System.Globalization.CultureInfo.InvariantCulture)+";tokenSessionMatched=True;logonSidCount=1;logonSidMatchesCaller=True";
      } finally { CloseQueriedToken(caller); }
    } finally { CloseQueriedToken(token); }
  }
  public static string StartedStatus(int pid) {
    IntPtr handle; if(!StartedProcesses.TryGetValue(pid,out handle)) throw new InvalidOperationException("Process status lacks the exact retained handle");
    uint wait=WaitForSingleObject(handle,0);
    if(wait==0x102) return "still-running";
    if(wait!=0) throw new Win32Exception(Marshal.GetLastWin32Error());
    uint code; if(!GetExitCodeProcess(handle,out code)) throw new Win32Exception(Marshal.GetLastWin32Error());
    return "exited:0x"+code.ToString("x8",System.Globalization.CultureInfo.InvariantCulture);
  }
  static void StopOwnedHandle(IntPtr handle) {
    uint wait=WaitForSingleObject(handle,0);
    if(wait==0) return;
    if(wait!=0x102) throw new Win32Exception(Marshal.GetLastWin32Error());
    if(!TerminateProcess(handle,1)) {
      int error=Marshal.GetLastWin32Error();
      if(error!=5 || WaitForSingleObject(handle,10000)!=0) throw new Win32Exception(error);
    } else if(WaitForSingleObject(handle,10000)!=0) throw new InvalidOperationException("Owned ordinary process termination was not observed");
  }
  public static void StopOwnedStartedProcesses() {
    var failures=new List<Exception>();
    var handles=new List<IntPtr>(StartedProcesses.Values); handles.AddRange(PendingProcesses);
    foreach(IntPtr handle in handles) { try { StopOwnedHandle(handle); } catch(Exception error) { failures.Add(error); } }
    if(failures.Count!=0) throw new AggregateException("Owned ordinary process termination failed",failures);
  }
  public static void CloseStartedObservations() {
    var failures=new List<Exception>();
    foreach(IntPtr token in new List<IntPtr>(PendingTokens)) {
      try { if(!CloseHandle(token)) throw new Win32Exception(Marshal.GetLastWin32Error()); PendingTokens.Remove(token); }
      catch(Exception error) { failures.Add(error); }
    }
    foreach(int pid in new List<int>(StartedThreads.Keys)) {
      try {
        if(!CloseHandle(StartedThreads[pid])) throw new Win32Exception(Marshal.GetLastWin32Error(),"Owned primary-thread handle close failed");
        StartedThreads.Remove(pid);
      } catch(Exception error) { failures.Add(error); }
    }
    foreach(IntPtr thread in new List<IntPtr>(PendingThreads)) {
      try { if(!CloseHandle(thread)) throw new Win32Exception(Marshal.GetLastWin32Error()); PendingThreads.Remove(thread); }
      catch(Exception error) { failures.Add(error); }
    }
    foreach(int pid in new List<int>(StartedProcesses.Keys)) {
      try {
        IntPtr handle=StartedProcesses[pid];
        if(WaitForSingleObject(handle,0)!=0) throw new InvalidOperationException("Cannot release an unconfirmed owned process observation");
        if(!CloseHandle(handle)) throw new Win32Exception(Marshal.GetLastWin32Error());
        StartedProcesses.Remove(pid);
      } catch(Exception error) { failures.Add(error); }
    }
    foreach(IntPtr handle in new List<IntPtr>(PendingProcesses)) {
      try {
        if(WaitForSingleObject(handle,0)!=0) throw new InvalidOperationException("Cannot release an unconfirmed owned process observation");
        if(!CloseHandle(handle)) throw new Win32Exception(Marshal.GetLastWin32Error());
        PendingProcesses.Remove(handle);
      } catch(Exception error) { failures.Add(error); }
    }
    if(failures.Count!=0) throw new AggregateException("Owned native handle cleanup failed",failures);
  }
}

'@
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

$parentProof=$null
$fixtureRoot=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('MixelRemoteQA-lease-'+[Guid]::NewGuid().ToString('N'))
$eventName=$bundle.Manifest.eventName
$executable=Join-Path $fixtureRoot $(if($Subsystem -ceq 'windows'){'lease-standard-gui.exe'}else{'lease-standard.exe'})
$resultPath=Join-Path $fixtureRoot 'fixture-result.json'
$negativeRequest=$null
function Read-BoundedJson([string]$Path) {
  $item=Get-Item -LiteralPath $Path
  if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 4096) { throw 'Owned result is not a bounded ordinary file.' }
  $bytes=[IO.File]::ReadAllBytes($Path)
  if($bytes.Length -gt 4096) { throw 'Owned result grew beyond its bound.' }
  return ([Text.UTF8Encoding]::new($false,$true).GetString($bytes) | ConvertFrom-Json)
}
function Write-AtomicJson([string]$Path,$Record) {
  $next=$Path+'.new'
  [IO.File]::WriteAllText($next,($Record|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
  if([IO.File]::Exists($Path)) { [IO.File]::Replace($next,$Path,$null) } else { [IO.File]::Move($next,$Path) }
}
function Get-PortableLeaseAdminInstruction([string]$Workspace,[string]$Root,[string]$ManifestSha256) {
  $scriptPath=Join-Path $Root 'run-admin-negative-lease-qa.ps1'
  $command="& '"+$scriptPath.Replace("'","''")+"' -Workspace '"+$Workspace.Replace("'","''")+"' -BundleRoot '"+$Root.Replace("'","''")+"' -ExpectedManifestSha256 '"+$ManifestSha256.Replace("'","''")+"'"
  return ('LOCAL QA: after the confirmed standard baseline, run in the separately authorized admin session: '+$command)
}
function Assert-NegativeReady($Record,[string]$Request,[string]$Mode,[string]$EventName) {
  $pidNumber=$Record.pid -is [int] -or $Record.pid -is [long] -or $Record.pid -is [uint32] -or $Record.pid -is [uint64]
  if((@($Record.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'created,eventName,mode,pid,request' -or
      $Record.request -isnot [string] -or $Record.request -cne $Request -or
      $Record.mode -isnot [string] -or $Record.mode -cne $Mode -or
      $Record.eventName -isnot [string] -or $Record.eventName -cne $EventName -or
      $Record.created -isnot [bool] -or -not $Record.created -or -not $pidNumber -or
      $Record.pid -le 0 -or $Record.pid -gt [uint32]::MaxValue) { throw 'Owned admin negative fixture did not confirm its exact isolated object.' }
}
function Assert-NegativeClosed($Record,[string]$Request) {
  if((@($Record.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'closed,request' -or
      $Record.request -isnot [string] -or $Record.request -cne $Request -or
      $Record.closed -isnot [bool] -or -not $Record.closed) { throw 'Owned admin fixture closure was not confirmed.' }
}
function Invoke-StandardLeaseControl([ValidateSet('probe','owner','failed-owner')][string]$Mode,[string]$ExpectedStatus,[int]$ExpectedError,[string]$ExpectedProof,[bool]$ExpectedRequired,[bool]$ExpectedOwnerFailed,[switch]$KeepAlive) {
  if(Test-Path -LiteralPath $resultPath) { Remove-Item -LiteralPath $resultPath }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-mode.txt'),$Mode,[Text.UTF8Encoding]::new($false))
  $controlPid=[MixelInteractiveLeaseProcess]::StartOrdinary($executable)
  $identity=[MixelInteractiveLeaseProcess]::AssertChild($controlPid)
  $deadline=[DateTime]::UtcNow.AddSeconds(10)
  while(-not (Test-Path -LiteralPath $resultPath) -and [DateTime]::UtcNow -lt $deadline) {
    if([MixelInteractiveLeaseProcess]::StartedStatus($controlPid).StartsWith('exited:')) { break }
    Start-Sleep -Milliseconds 50
  }
  if(-not (Test-Path -LiteralPath $resultPath)) { throw 'Owned ordinary child did not produce its bounded native result.' }
  $record=Read-BoundedJson $resultPath
  Assert-StandardLeaseSafeResult $record $controlPid
  if($record.sourceStatus -cne $ExpectedStatus -or $record.rawOpenError -ne $ExpectedError -or $record.attestation -cne $ExpectedProof -or $record.requiresClick -ne $ExpectedRequired -or $record.ownerFailed -ne $ExpectedOwnerFailed) {
    throw ('Actual current-standard source lease classification mismatch: '+($record|ConvertTo-Json -Compress))
  }
  Write-Host "VERIFIED: inherited ordinary child; $identity; mode=$Mode; subsystem=$Subsystem; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
  if(-not $KeepAlive) {
    [IO.File]::WriteAllText((Join-Path $fixtureRoot "fixture-release-$controlPid"),'verified',[Text.UTF8Encoding]::new($false))
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while([MixelInteractiveLeaseProcess]::StartedStatus($controlPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if([MixelInteractiveLeaseProcess]::StartedStatus($controlPid) -cne 'exited:0x00000000') { throw 'Exact owned ordinary child did not exit successfully.' }
  } elseif([MixelInteractiveLeaseProcess]::StartedStatus($controlPid) -cne 'still-running') { throw 'Exact ordinary owner exited before independent observation.' }
  return $controlPid
}
function Request-NegativeFixture([ValidateSet('denied','wrong-object')][string]$Mode) {
  $script:negativeRequest=[Guid]::NewGuid().ToString('N')
  Write-AtomicJson (Join-Path $fixtureRoot 'negative-request.json') ([ordered]@{request=$negativeRequest;mode=$Mode;eventName=$eventName})
  $deadline=[DateTime]::UtcNow.AddSeconds(90)
  $readyPath=Join-Path $fixtureRoot 'negative-ready.json'
  while([DateTime]::UtcNow -lt $deadline) {
    if(Test-Path -LiteralPath $readyPath) {
      $ready=Read-BoundedJson $readyPath
      if($ready.request -is [string] -and $ready.request -ceq $negativeRequest) {
        Assert-NegativeReady $ready $negativeRequest $Mode $eventName
        return
      }
    }
    Start-Sleep -Milliseconds 100
  }
  throw 'Separate owned admin negative fixture was not ready within its bound.'
}
function Complete-NegativeFixture {
  if(-not $negativeRequest) { return }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot ('negative-release-'+$negativeRequest)),'verified',[Text.UTF8Encoding]::new($false))
  $deadline=[DateTime]::UtcNow.AddSeconds(10); $closedPath=Join-Path $fixtureRoot 'negative-closed.json'
  while([DateTime]::UtcNow -lt $deadline) {
    if(Test-Path -LiteralPath $closedPath) {
      $closed=Read-BoundedJson $closedPath
      if($closed.request -is [string] -and $closed.request -ceq $negativeRequest) {
        Assert-NegativeClosed $closed $negativeRequest
        $script:negativeRequest=$null; return
      }
    }
    Start-Sleep -Milliseconds 50
  }
  throw 'Owned isolated admin fixture handle closure was not confirmed.'
}
$failure=$null
try {
  $parentProof=[MixelInteractiveLeaseProcess]::AssertParent()
  Write-Host "VERIFIED: actual current standard interactive parent; $parentProof."
  if(Test-Path -LiteralPath $fixtureRoot) { throw 'Owned new fixture directory already exists.' }
  [void](New-Item -ItemType Directory -Path $fixtureRoot)
  foreach($name in @('lease-standard.rs','lease-standard.exe','lease-standard-gui.rs','lease-standard-gui.exe')) {
    $member=$bundle.Manifest.files|Where-Object {$_.path -ceq ('fixture/'+$name)}
    $target=Join-Path $fixtureRoot $name
    [IO.File]::Copy((Join-Path $bundle.Root ('fixture/'+$name)),$target,$false)
    if((Get-Item -LiteralPath $target).Length -ne $member.bytes -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -cne $member.sha256) { throw 'Owned copied fixture member differs from the trusted bundle.' }
  }
  $absent = 'ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
  $active = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=false'
  $activeOwner = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=true'
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: actual standard-account source probe safely observes absent GUID event, raw error2, empty attestation and saved authorization behavior.'
  $ownerPid = Invoke-StandardLeaseControl owner $activeOwner 0 'attended-runtime-v2' $true $false -KeepAlive
  [void](Invoke-StandardLeaseControl probe $active 0 'attended-runtime-v2' $true $false)
  Write-Host 'PASS: actual standard-account source creates the SYNCHRONIZE lease and a fresh ordinary process observes its live confirmed owner.'
  [MixelInteractiveLeaseProcess]::StopOwnedStartedProcesses()
  if ([MixelInteractiveLeaseProcess]::StartedStatus($ownerPid) -cne 'exited:0x00000001') { throw 'Exact retained standard-account owner handle did not observe its termination.' }
  [MixelInteractiveLeaseProcess]::CloseStartedObservations()
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: confirmed owned standard-account owner termination releases the GUID event; fresh actual source returns empty ordinary attestation.'
  Write-Host (Get-PortableLeaseAdminInstruction $fixtureRoot $bundle.Root $ExpectedManifestSha256)
  Request-NegativeFixture denied
  try {
    [void](Invoke-StandardLeaseControl probe 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false' 5 'guard-unavailable' $true $false)
    Write-Host 'PASS: actual standard-account inaccessible GUID event retains native error5 and unavailable attestation; authorization remains fail closed.'
  } finally { Complete-NegativeFixture }
  Request-NegativeFixture wrong-object
  try {
    [void](Invoke-StandardLeaseControl probe 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=6;memoryActive=false' 6 'guard-unavailable' $true $false)
    [void](Invoke-StandardLeaseControl failed-owner 'ownerFailed=true;ownerError=6;kernel=unavailable;probeError=6;memoryActive=true' 6 'guard-unavailable' $true $true)
    Write-Host 'PASS: actual standard-account wrong-object probe and failed owner acquisition retain native error6 and ownerFailed classification without false readiness.'
  } finally { Complete-NegativeFixture }
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: closed wrong-object control leaves no GUID lease or false readiness in a fresh actual standard-account process.'

  Write-AtomicJson (Join-Path $fixtureRoot 'negative-finished.json') ([ordered]@{finished=$true})
} catch { $failure=$_; throw } finally {
  $cleanup=[Collections.Generic.List[string]]::new()
  try { if($negativeRequest) { Complete-NegativeFixture } } catch { $cleanup.Add('Owned negative fixture closure failed.') }
  try { [MixelInteractiveLeaseProcess]::StopOwnedStartedProcesses() } catch { $cleanup.Add('Exact owned ordinary child termination failed.') }
  try { [MixelInteractiveLeaseProcess]::CloseStartedObservations() } catch { $cleanup.Add('Exact owned native handle closure failed.') }
  try { if(Test-Path -LiteralPath $fixtureRoot) { Write-AtomicJson (Join-Path $fixtureRoot 'negative-finished.json') ([ordered]@{finished=$true}) } } catch { $cleanup.Add('Owned completion marker publication failed.') }
  # Evidence remains in the owned local directory for collection; never delete
  # an account, profile, credential, desktop ACL or unrelated files.
  if($cleanup.Count) { if($failure) { Write-Host ('FAIL: '+($cleanup -join ' ')) } else { throw ($cleanup -join ' ') } }
  else { Write-Host 'PASS: exact inherited ordinary children and isolated negative handles are closed; local evidence retained.' }
}
