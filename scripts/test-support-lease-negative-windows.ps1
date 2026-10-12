# PREPARED LOCAL QA ONLY. Run separately in an already authorized admin session.
param([Parameter(Mandatory)][string]$Workspace,[Parameter(Mandatory)][string]$ExpectedManifestSha256,[string]$BundleRoot=$PSScriptRoot)
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
  $allow=@('run-standard-lease-qa.ps1','run-admin-negative-lease-qa.ps1','scripts/support-invite-guard.rs','scripts/test-support-lease.py','scripts/test-support-lease-standard-windows.ps1','scripts/test-support-directory-security.cs','scripts/test-support-nt-event-child.rs','scripts/test-support-nt-event-binding.cs','fixture/lease-standard.rs','fixture/lease-standard.exe','fixture/lease-standard-gui.rs','fixture/lease-standard-gui.exe','lease-standard-compile.log','lease-standard-gui-compile.log','hosted-loader-smoke.json')
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
  $sourceAllow=@('scripts/support-invite-guard.rs','scripts/test-support-lease.py','scripts/test-support-lease-standard-windows.ps1','scripts/test-support-directory-security.cs','scripts/test-support-nt-event-child.rs','scripts/test-support-nt-event-binding.cs','scripts/test-support-lease-interactive-windows.ps1','scripts/test-support-lease-negative-windows.ps1','scripts/build-standard-lease-qa-bundle.py','.github/workflows/preflight.yml','scripts/activate-windows-msvc.ps1','scripts/test-standard-lease-qa-bundle.py','scripts/test-support-lease-interactive-source.ps1','scripts/test-support-launch-windows.ps1')
  if($manifest.buildSourceBindings -isnot [array] -or $manifest.buildSourceBindings.Count -ne $sourceAllow.Count) { throw 'Portable build source binding count changed.' }
  $sourceSeen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach($binding in $manifest.buildSourceBindings) {
    if((@($binding.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'bytes,path,sha256' -or $binding.path -isnot [string] -or $binding.path -cnotin $sourceAllow -or -not $sourceSeen.Add($binding.path) -or $binding.sha256 -isnot [string] -or $binding.sha256 -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Portable build source binding schema or allowlist changed.' }
    Assert-PortableLeaseInteger $binding.bytes 1 1048576
    $packagePath=if($binding.path -ceq 'scripts/test-support-lease-interactive-windows.ps1'){'run-standard-lease-qa.ps1'}elseif($binding.path -ceq 'scripts/test-support-lease-negative-windows.ps1'){'run-admin-negative-lease-qa.ps1'}elseif($binding.path -cin @('scripts/support-invite-guard.rs','scripts/test-support-lease.py','scripts/test-support-lease-standard-windows.ps1','scripts/test-support-directory-security.cs','scripts/test-support-nt-event-child.rs','scripts/test-support-nt-event-binding.cs')){$binding.path}else{$null}
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
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
try {
  $principal=[Security.Principal.WindowsPrincipal]::new($identity)
  if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Negative fixture requires its separately authorized admin setup process.' }
} finally { $identity.Dispose() }
$Workspace=[IO.Path]::GetFullPath($Workspace)
$folder=Get-Item -LiteralPath $Workspace
if(-not $folder.PSIsContainer -or ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $folder.Name -cnotmatch '\AMixelRemoteQA-lease-[0-9a-f]{32}\z') { throw 'Requires the exact owned local GUID fixture directory.' }
Add-Type @'
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
'@
function Read-BoundedJson([string]$Path) {
  $item=Get-Item -LiteralPath $Path
  if($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 4096) { throw 'Negative request is not an ordinary bounded file.' }
  $bytes=[IO.File]::ReadAllBytes($Path)
  if($bytes.Length -gt 4096) { throw 'Negative request grew beyond its bound.' }
  return ([Text.UTF8Encoding]::new($false,$true).GetString($bytes) | ConvertFrom-Json)
}
function Write-AtomicJson([string]$Path,$Record) {
  [IO.File]::WriteAllText(($Path+'.new'),($Record|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
  if([IO.File]::Exists($Path)) { [IO.File]::Replace(($Path+'.new'),$Path,$null) } else { [IO.File]::Move(($Path+'.new'),$Path) }
}
function Assert-NegativeRequest($Record) {
  if((@($Record.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'eventName,mode,request' -or
      $Record.request -isnot [string] -or $Record.request -cnotmatch '\A[0-9a-f]{32}\z' -or
      $Record.eventName -isnot [string] -or $Record.eventName -cnotmatch '\AGlobal\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z' -or $Record.eventName -cne $bundle.Manifest.eventName -or
      $Record.mode -isnot [string] -or $Record.mode -cnotin @('denied','wrong-object')) { throw 'Negative helper refuses any noncanonical GUID-only request.' }
}
$deadline=[DateTime]::UtcNow.AddSeconds(240); $seen=[Collections.Generic.HashSet[string]]::new(); $handle=[IntPtr]::Zero
try {
  while([DateTime]::UtcNow -lt $deadline) {
    $finished=Join-Path $Workspace 'negative-finished.json'
    if(Test-Path -LiteralPath $finished) {
      $record=Read-BoundedJson $finished
      if((@($record.PSObject.Properties.Name)-join ',') -cne 'finished' -or $record.finished -isnot [bool] -or -not $record.finished) { throw 'Invalid owned completion marker.' }
      break
    }
    $requestPath=Join-Path $Workspace 'negative-request.json'
    if(-not (Test-Path -LiteralPath $requestPath)) { Start-Sleep -Milliseconds 100; continue }
    $request=Read-BoundedJson $requestPath
    Assert-NegativeRequest $request
    if($seen.Contains($request.request)) { Start-Sleep -Milliseconds 100; continue }
    if($seen.Count -ge 2) { throw 'Negative helper exceeds its two owned fixture phases.' }
    [void]$seen.Add($request.request)
    $handle=if($request.mode -ceq 'denied') { [MixelIsolatedLeaseControl]::DeniedEvent($request.eventName) } else { [MixelIsolatedLeaseControl]::WrongObject($request.eventName) }
    try {
      Write-AtomicJson (Join-Path $Workspace 'negative-ready.json') ([ordered]@{request=$request.request;mode=$request.mode;eventName=$request.eventName;pid=$PID;created=$true})
      $release=Join-Path $Workspace ('negative-release-'+$request.request); $releaseDeadline=[DateTime]::UtcNow.AddSeconds(60)
      while(-not (Test-Path -LiteralPath $release) -and [DateTime]::UtcNow -lt $releaseDeadline) { Start-Sleep -Milliseconds 50 }
      if(-not (Test-Path -LiteralPath $release) -or [IO.File]::ReadAllText($release) -cne 'verified') { throw 'Owned admin negative fixture release was not acknowledged.' }
    } finally { [MixelIsolatedLeaseControl]::Close($handle); $handle=[IntPtr]::Zero }
    Write-AtomicJson (Join-Path $Workspace 'negative-closed.json') ([ordered]@{request=$request.request;closed=$true})
  }
  if([DateTime]::UtcNow -ge $deadline) { throw 'Owned admin negative fixture session exceeded its bound.' }
  Write-Host 'PASS: separate owned admin helper closed only its isolated GUID negative-fixture handles; no account/desktop/namespace grants were made.'
} finally { if($handle -ne [IntPtr]::Zero) { [MixelIsolatedLeaseControl]::Close($handle) } }
