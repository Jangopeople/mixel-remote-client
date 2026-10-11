# Source-only hardware trace. Never a signed product/customer-Accept release gate.
# Run as its own PowerShell process in the existing isolated x64 Windows CI job.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or [IntPtr]::Size -ne 8) {
  throw 'Source namespace tracing requires an isolated x64 Windows Actions runner.'
}
if ('MixelOrdinaryTokenFixture' -as [type]) { throw 'Source namespace tracing requires a fresh process with the exact extracted fixture class.' }
$launchSource = Get-Content (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') -Raw
$class = $launchSource.IndexOf('public static class MixelOrdinaryTokenFixture {')
$first = $launchSource.LastIndexOf('using System;', $class)
$last = $launchSource.IndexOf("`n'@", $class)
if ($class -lt 0 -or $first -lt 0 -or $last -le $first) { throw 'Exact retained ordinary-token fixture is missing.' }
$normalSource = $launchSource.Substring($first,$last-$first)
$marker = '  public static int StartStandardUser(string executable, string username, string password) {'
if ($normalSource.IndexOf($marker) -lt 0 -or $normalSource.IndexOf($marker) -ne $normalSource.LastIndexOf($marker)) { throw 'Exact retained fixture injection boundary changed.' }
$traceBridge = @'
  [DllImport("kernel32.dll", SetLastError=true)] static extern uint GetThreadId(IntPtr thread);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsWow64Process2(IntPtr process,out ushort machine,out ushort native);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageNameW(IntPtr process,uint flags,StringBuilder path,ref uint size);
  public static uint TraceOwnedPrimaryThreadId(int pid,string executable) {
    IntPtr process,thread;
    if(!StartedProcesses.TryGetValue(pid,out process) || !SuspendedThreads.TryGetValue(pid,out thread) || WaitForSingleObject(process,0)!=0x102 || GetProcessId(process)!=checked((uint)pid)) throw new InvalidOperationException("Trace requires the retained live suspended process and primary thread");
    ushort machine,native;
    if(!IsWow64Process2(process,out machine,out native)) throw new Win32Exception(Marshal.GetLastWin32Error());
    if(machine!=0 || native!=0x8664) throw new InvalidOperationException("Trace requires the actual native x64 process");
    var image=new StringBuilder(32768); uint size=(uint)image.Capacity;
    if(!QueryFullProcessImageNameW(process,0,image,ref size)) throw new Win32Exception(Marshal.GetLastWin32Error());
    if(!string.Equals(System.IO.Path.GetFullPath(image.ToString()),System.IO.Path.GetFullPath(executable),StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Trace image differs from the exact source executable");
    uint tid=GetThreadId(thread);
    if(tid==0) throw new Win32Exception(Marshal.GetLastWin32Error());
    return tid;
  }
'@
$normalSource=$normalSource.Insert($normalSource.IndexOf($marker),$traceBridge+"`n")
Add-Type -TypeDefinition $normalSource -CompilerOptions '/warnaserror'
if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) { throw 'Source namespace tracing requires the elevated isolated runner.' }

$boundedSource = @'
using System;
using System.IO;
using System.Threading.Tasks;
public static class MixelNamespaceTraceIO {
  public static async Task CopyBounded(Stream input,Stream output,int maximum) {
    if(maximum<1 || maximum>1048576) throw new ArgumentOutOfRangeException("maximum");
    byte[] buffer=new byte[4096]; int total=0;
    while(true) {
      int count=await input.ReadAsync(buffer,0,buffer.Length).ConfigureAwait(false);
      if(count==0) break;
      if(count>maximum-total) throw new InvalidOperationException("Private trace output exceeded its live byte bound");
      await output.WriteAsync(buffer,0,count).ConfigureAwait(false); total+=count;
      await output.FlushAsync().ConfigureAwait(false);
    }
  }
  public static string ReadBounded(string path,int maximum) {
    var info=new FileInfo(path);
    if(!info.Exists || (info.Attributes&FileAttributes.ReparsePoint)!=0 || info.Length>maximum) throw new InvalidOperationException("Private trace file is missing, redirected or oversized");
    using(var input=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite)) {
      if(input.Length>maximum) throw new InvalidOperationException("Private trace file exceeded its byte bound");
      byte[] bytes=new byte[maximum+1]; int used=0;
      while(used<bytes.Length) {int count=input.Read(bytes,used,bytes.Length-used);if(count==0)break;used+=count;}
      if(used>maximum) throw new InvalidOperationException("Private trace file grew beyond its byte bound");
      return new System.Text.UTF8Encoding(false,true).GetString(bytes,0,used);
    }
  }
}
'@
Add-Type -TypeDefinition $boundedSource -CompilerOptions '/warnaserror'
$aclNativeSource = @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MixelNamespaceTraceAcl {
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool GetFileSecurityW(string file,uint information,[Out] byte[] descriptor,uint length,out uint needed);
  [DllImport("advapi32.dll",CharSet=CharSet.Unicode)] static extern uint SetNamedSecurityInfoW(string file,uint objectType,uint information,IntPtr owner,IntPtr group,IntPtr dacl,IntPtr sacl);
  static int Position(byte[] bytes) {
    if(bytes==null || bytes.Length<20 || bytes.Length>65536 || bytes[0]!=1 || (BitConverter.ToUInt16(bytes,2)&0x8004)!=0x8004) throw new InvalidOperationException("Invalid bounded native file descriptor");
    uint offset=BitConverter.ToUInt32(bytes,16);
    if(offset<20 || offset>(uint)bytes.Length-8) throw new InvalidOperationException("Native file DACL offset is invalid");
    int position=checked((int)offset),size=BitConverter.ToUInt16(bytes,position+2);
    if((bytes[position]!=2 && bytes[position]!=4) || size<8 || size>bytes.Length-position) throw new InvalidOperationException("Native file DACL size or revision is invalid");
    return position;
  }
  public static byte[] Read(string path) {
    uint needed;
    bool first=GetFileSecurityW(path,7,null,0,out needed);
    int error=first?0:Marshal.GetLastWin32Error();
    if(first) throw new InvalidOperationException("Unexpected native file descriptor size query success");
    if(error!=122) throw new Win32Exception(error);
    if(needed<20 || needed>65536) throw new InvalidOperationException("Invalid bounded native file descriptor size query");
    byte[] bytes=new byte[needed];uint actual;
    if(!GetFileSecurityW(path,7,bytes,needed,out actual)) {error=Marshal.GetLastWin32Error();throw new Win32Exception(error);}
    if(actual<20 || actual>needed) throw new InvalidOperationException("Native file descriptor read exceeded its bound");
    if(actual!=needed) Array.Resize(ref bytes,checked((int)actual));
    Position(bytes);return bytes;
  }
  public static void RestoreDacl(string path,byte[] original) {
    int position=Position(original);
    uint information=4U|((BitConverter.ToUInt16(original,2)&0x1000)!=0?0x80000000U:0x20000000U);
    var pinned=GCHandle.Alloc(original,GCHandleType.Pinned);
    try {
      // Pass the saved native ACL, including padding. This API returns its own
      // error code and writes only the DACL plus its original protection state.
      uint error=SetNamedSecurityInfoW(path,1,information,IntPtr.Zero,IntPtr.Zero,IntPtr.Add(pinned.AddrOfPinnedObject(),position),IntPtr.Zero);
      if(error!=0) throw new Win32Exception(unchecked((int)error));
    } finally { pinned.Free(); }
  }
  public static ushort Control(byte[] bytes) {Position(bytes);return BitConverter.ToUInt16(bytes,2);}
  public static byte[] AutoInherited(byte[] bytes) {
    Position(bytes);
    byte[] result=(byte[])bytes.Clone();result[3]|=4;return result;
  }
  static byte[] Identity(byte[] bytes,int field) {
    Position(bytes);uint offset=BitConverter.ToUInt32(bytes,field);
    if(offset<20 || offset>(uint)bytes.Length-8)throw new InvalidOperationException("Native owner or group offset is invalid");
    int position=checked((int)offset),count=bytes[position+1],size=8+count*4;
    if(bytes[position]!=1 || count>15 || size>bytes.Length-position)throw new InvalidOperationException("Native owner or group SID is invalid");
    byte[] result=new byte[size];Buffer.BlockCopy(bytes,position,result,0,size);return result;
  }
  public static byte[] OwnerGroup(byte[] bytes) {
    byte[] owner=Identity(bytes,4),group=Identity(bytes,8),result=new byte[2+owner.Length+group.Length];
    result[0]=checked((byte)owner.Length);result[1]=checked((byte)group.Length);
    Buffer.BlockCopy(owner,0,result,2,owner.Length);Buffer.BlockCopy(group,0,result,2+owner.Length,group.Length);return result;
  }
  public static byte[] Dacl(byte[] bytes) {
    int position=Position(bytes),size=BitConverter.ToUInt16(bytes,position+2);
    ushort flags=(ushort)(BitConverter.ToUInt16(bytes,2)&0x150c);
    byte[] result=new byte[size+2];result[0]=(byte)flags;result[1]=(byte)(flags>>8);Buffer.BlockCopy(bytes,position,result,2,size);return result;
  }
  public static bool Equal(byte[] left,byte[] right) {
    if(left==null || right==null || left.Length!=right.Length)return false;
    int difference=0;for(int i=0;i<left.Length;i++)difference|=left[i]^right[i];return difference==0;
  }
  public static string Hash(byte[] bytes) {
    Position(bytes);
    using(var hash=System.Security.Cryptography.SHA256.Create()) {return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-","");}
  }
}
'@
Add-Type -TypeDefinition $aclNativeSource -CompilerOptions '/warnaserror'
$script:traceLiveProcesses=[Collections.Generic.List[object]]::new()

$leaseSource=Get-Content (Join-Path $PSScriptRoot 'test-support-lease-standard-windows.ps1') -Raw
$isolated=$leaseSource.IndexOf('public static class MixelIsolatedLeaseControl {')
$isolatedFirst=$leaseSource.LastIndexOf('using System;',$isolated)
$isolatedLast=$leaseSource.IndexOf("`n'@",$isolated)
if ($isolated -lt 0 -or $isolatedFirst -lt 0 -or $isolatedLast -le $isolatedFirst) { throw 'Exact isolated GUID event control is missing.' }
Add-Type -TypeDefinition $leaseSource.Substring($isolatedFirst,$isolatedLast-$isolatedFirst) -CompilerOptions '/warnaserror'
$leaseTokens=$null;$leaseParseErrors=$null
$leaseAst=[Management.Automation.Language.Parser]::ParseInput($leaseSource,[ref]$leaseTokens,[ref]$leaseParseErrors)
if ($leaseParseErrors.Count -ne 0) { throw 'Exact bounded source-result validator source does not parse.' }
$resultFunctions=@($leaseAst.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Assert-StandardLeaseSafeResult'},$true))
if ($resultFunctions.Count -ne 1) { throw 'Exact bounded source-result validator changed.' }
. ([scriptblock]::Create($resultFunctions[0].Extent.Text))
Add-Type -TypeDefinition (Get-Content (Join-Path $PSScriptRoot 'windows-standard-namespace-access.cs') -Raw) -CompilerOptions '/warnaserror'

function Assert-TracePlainPath([string]$Path,[switch]$AllowMissing) {
  $full=[IO.Path]::GetFullPath($Path)
  if ($full -cnotmatch '\A[A-Z]:\\' -or $full -cne $Path) { throw 'Owned trace path must be a canonical local Windows path.' }
  $current=$full;$first=$true
  while ($current) {
    if (Test-Path -LiteralPath $current) {
      if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Owned trace path traverses a reparse point.' }
    } elseif (-not $first -or -not $AllowMissing) { throw 'Owned trace path has a missing parent or file.' }
    $parent=[IO.Directory]::GetParent($current);$current=if ($parent) {$parent.FullName} else {$null};$first=$false
  }
}
function Remove-TracePlainContents([string]$Path) {
  Assert-TracePlainPath $Path
  $pending=[Collections.Generic.Stack[string]]::new();$pending.Push($Path);$count=0
  while ($pending.Count -gt 0) {
    foreach ($item in @(Get-ChildItem -LiteralPath $pending.Pop() -Force)) {
      $count++
      if ($count -gt 256 -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Owned trace cleanup tree is redirected or exceeds its item bound.' }
      if ($item.PSIsContainer) { $pending.Push($item.FullName) }
    }
  }
  Get-ChildItem -LiteralPath $Path -Force | Remove-Item -Recurse -Force
}
function Get-TraceNativeError([Exception]$Failure) {
  $current=$Failure
  for ($depth=0;$current -and $depth -lt 16;$depth++) {
    if ($current -is [ComponentModel.Win32Exception]) { return [pscustomobject]@{known=$true;code=[int]$current.NativeErrorCode;hresult=[int]$current.HResult} }
    if (-not $current.InnerException) { break };$current=$current.InnerException
  }
  return [pscustomobject]@{known=$false;code=0;hresult=$(if($current){[int]$current.HResult}else{0})}
}
function Write-TraceStageFailure([ValidateSet('create-user','verify-user','create-private-root','initialize-private-root','snapshot-private-root','restrict-private-root','create-source-root','initialize-source-root','snapshot-source-root','restrict-source-root','create-gate-root','initialize-gate-root','snapshot-gate-root','restrict-gate-root','grant-desktop','grant-namespace','trace-absent','trace-world','cleanup-private-root','cleanup-source-root','cleanup-gate-root')][string]$Stage,[Exception]$Failure) {
  $native=Get-TraceNativeError $Failure
  Write-Host ('OBSERVED: bounded source-only trace stage failure; '+([pscustomobject]@{stage=$Stage;nativeErrorKnown=$native.known;nativeError=$native.code;hresult=$native.hresult}|ConvertTo-Json -Compress))
}
function Write-TraceAclFacts([ValidateSet('initialize-root','set-directory','set-file','restore-root')][string]$Phase,$ExpectedAcl,$ActualAcl,[byte[]]$ExpectedNative,[byte[]]$ActualNative,[Exception]$Failure) {
  $native=Get-TraceNativeError $Failure
  $expectedAvailable=$null -ne $ExpectedNative;$actualAvailable=$null -ne $ActualNative
  $facts=[pscustomobject]@{
    phase=$Phase;nativeErrorKnown=$native.known;nativeError=$native.code;hresult=$native.hresult
    expectedAvailable=$expectedAvailable;actualAvailable=$actualAvailable
    expectedControl=$(if($expectedAvailable){[int][MixelNamespaceTraceAcl]::Control($ExpectedNative)}else{0})
    actualControl=$(if($actualAvailable){[int][MixelNamespaceTraceAcl]::Control($ActualNative)}else{0})
    expectedProtected=$(if($ExpectedAcl){[bool]$ExpectedAcl.AreAccessRulesProtected}else{$false})
    actualProtected=$(if($ActualAcl){[bool]$ActualAcl.AreAccessRulesProtected}else{$false})
    expectedCanonical=$(if($ExpectedAcl){[bool]$ExpectedAcl.AreAccessRulesCanonical}else{$false})
    actualCanonical=$(if($ActualAcl){[bool]$ActualAcl.AreAccessRulesCanonical}else{$false})
    expectedHash=$(if($expectedAvailable){[MixelNamespaceTraceAcl]::Hash($ExpectedNative)}else{''})
    actualHash=$(if($actualAvailable){[MixelNamespaceTraceAcl]::Hash($ActualNative)}else{''})
  }
  Write-Host ('OBSERVED: bounded owned trace ACL facts; '+($facts|ConvertTo-Json -Compress))
}
function Initialize-TraceCreatedRoot([string]$Path) {
  Assert-TracePlainPath $Path
  if (-not $script:createdRoots.Contains($Path) -or $script:fileSnapshots.ContainsKey($Path) -or
      -not (Get-Item -LiteralPath $Path -Force).PSIsContainer -or @(Get-ChildItem -LiteralPath $Path -Force).Count -ne 0) {
    throw 'ACL initialization requires an unused empty root created by this exact trace.'
  }
  $expected=$null;$actual=$null;$expectedNative=$null;$actualNative=$null
  try {
    $before=[MixelNamespaceTraceAcl]::Read($Path)
    $beforeIdentity=[MixelNamespaceTraceAcl]::OwnerGroup($before)
    # These are unused folders created by this trace. Establish their intended
    # private protected ACL before saving a canonical native baseline; never
    # treat creation defaults as an existing external object's baseline.
    $expected=New-TraceAclPlan (Get-Acl -LiteralPath $Path) '' $true $false
    Assert-TracePrivateRootAcl $expected
    $expectedNative=$expected.GetSecurityDescriptorBinaryForm()
    Set-Acl -LiteralPath $Path -AclObject $expected
    $actual=Get-Acl -LiteralPath $Path;$actualNative=[MixelNamespaceTraceAcl]::Read($Path)
    Assert-TracePrivateRootAcl $actual
    if ($actual.GetSecurityDescriptorSddlForm('Access') -cne $expected.GetSecurityDescriptorSddlForm('Access') -or
        -not [MixelNamespaceTraceAcl]::Equal([MixelNamespaceTraceAcl]::Dacl($expectedNative),[MixelNamespaceTraceAcl]::Dacl($actualNative)) -or
        -not [MixelNamespaceTraceAcl]::Equal($beforeIdentity,[MixelNamespaceTraceAcl]::OwnerGroup($actualNative))) {
      throw 'New trace root did not establish its exact private ACL with unchanged owner and group.'
    }
    return $actualNative
  } catch {
    Write-TraceAclFacts 'initialize-root' $expected $actual $expectedNative $actualNative $_.Exception
    throw
  }
}
function Assert-TracePrivateRootAcl($Acl) {
  $allowed=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  [void]$allowed.Add([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
  [void]$allowed.Add('S-1-5-18')
  $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  $rules=@($Acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
  if (-not $Acl.AreAccessRulesProtected -or -not $Acl.AreAccessRulesCanonical -or $rules.Count -ne $allowed.Count) {throw 'New trace private ACL must contain only the protected runner and SYSTEM allowances.'}
  foreach ($rule in $rules) {
    if ($rule.IsInherited -or $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        -not $allowed.Contains($rule.IdentityReference.Value) -or -not $seen.Add($rule.IdentityReference.Value) -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.InheritanceFlags -ne ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit) -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
      throw 'New trace private ACL has an unexpected principal, access mask or inheritance rule.'
    }
  }
}
function New-TraceAclPlan($Acl,[string]$ChildSid,[bool]$Directory,[bool]$ReadOnly) {
  # Derive from the current descriptor to preserve native inheritance metadata.
  # Then replace every ACE; this never retains a prior unrelated allowance.
  $Acl.SetAccessRuleProtection($true,$false)
  foreach ($rule in @($Acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))) { [void]$Acl.RemoveAccessRuleAll($rule) }
  $runner=[Security.Principal.WindowsIdentity]::GetCurrent().User
  foreach ($sid in @($runner,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $rule=if($Directory){[Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')}else{[Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow')}
    $Acl.AddAccessRule($rule)
  }
  if ($ChildSid) {
    $rights=if($ReadOnly){'ReadAndExecute'}else{'Modify'}
    $sid=[Security.Principal.SecurityIdentifier]::new($ChildSid)
    $rule=if($Directory){[Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,'ContainerInherit,ObjectInherit','None','Allow')}else{[Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,'Allow')}
    $Acl.AddAccessRule($rule)
  }
  # Plan the documented metadata written by the native inheritance engine.
  # Exact DACL flags, ACEs, padding and Access SDDL still must read back.
  $Acl.SetSecurityDescriptorBinaryForm([MixelNamespaceTraceAcl]::AutoInherited($Acl.GetSecurityDescriptorBinaryForm()),[Security.AccessControl.AccessControlSections]::Access)
  return $Acl
}
function Set-TraceAcl([string]$Path,[string]$ChildSid,[bool]$Directory,[bool]$ReadOnly) {
  Assert-TracePlainPath $Path
  $phase=if($Directory){'set-directory'}else{'set-file'}
  $expected=$null;$actual=$null;$expectedNative=$null;$actualNative=$null
  try {
    $expected=New-TraceAclPlan (Get-Acl -LiteralPath $Path) $ChildSid $Directory $ReadOnly
    $expectedNative=$expected.GetSecurityDescriptorBinaryForm()
    Set-Acl -LiteralPath $Path -AclObject $expected
    $actual=Get-Acl -LiteralPath $Path;$actualNative=[MixelNamespaceTraceAcl]::Read($Path)
    if ($actual.GetSecurityDescriptorSddlForm('Access') -cne $expected.GetSecurityDescriptorSddlForm('Access') -or
        -not [MixelNamespaceTraceAcl]::Equal([MixelNamespaceTraceAcl]::Dacl($expectedNative),[MixelNamespaceTraceAcl]::Dacl($actualNative))) {
      throw 'Owned trace ACL did not read back as the exact planned native DACL.'
    }
  } catch {
    Write-TraceAclFacts $phase $expected $actual $expectedNative $actualNative $_.Exception
    throw
  }
}
function Set-TraceDirectoryAcl([string]$Path,[string]$ChildSid,[switch]$ReadOnly) { Set-TraceAcl $Path $ChildSid $true ([bool]$ReadOnly) }
function Set-TraceReadOnlyFile([string]$Path,[string]$ChildSid) { Set-TraceAcl $Path $ChildSid $false $true }
function Restore-TraceRootAcl([string]$Path,[byte[]]$Original) {
  Assert-TracePlainPath $Path
  $expected=$null;$actual=$null;$actualNative=$null
  try {
    # Interpret the original access rules only for safe diagnostics. Restore
    # directly from the saved native DACL; never reserialize its padding.
    $expected=Get-Acl -LiteralPath $Path
    $expected.SetSecurityDescriptorBinaryForm($Original,[Security.AccessControl.AccessControlSections]::Access)
    [MixelNamespaceTraceAcl]::RestoreDacl($Path,$Original)
    $actual=Get-Acl -LiteralPath $Path;$actualNative=[MixelNamespaceTraceAcl]::Read($Path)
    if (-not [MixelNamespaceTraceAcl]::Equal($Original,$actualNative)) { throw 'Owned trace root did not restore its exact original native descriptor.' }
  } catch {
    Write-TraceAclFacts 'restore-root' $expected $actual $Original $actualNative $_.Exception
    throw
  }
}
function Start-TraceProcess([string]$File,[string[]]$Arguments,[string]$Folder,[string]$Label,[int]$ChildPid=0) {
  Assert-TracePlainPath $Folder
  $outPath=Join-Path $Folder ($Label+'-stdout.private');$errPath=Join-Path $Folder ($Label+'-stderr.private')
  Assert-TracePlainPath $outPath -AllowMissing;Assert-TracePlainPath $errPath -AllowMissing
  $output=$null;$errors=$null;$process=$null;$started=$false;$live=$null
  try {
    $output=[IO.FileStream]::new($outPath,'CreateNew','Write','ReadWrite')
    $errors=[IO.FileStream]::new($errPath,'CreateNew','Write','ReadWrite')
    $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName=$File;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    foreach ($argument in $Arguments) { [void]$info.ArgumentList.Add($argument) }
    $process=[Diagnostics.Process]::new();$process.StartInfo=$info
    $live=[pscustomobject]@{process=$process;input=$null;output=$output;errors=$errors;outPath=$outPath;errPath=$errPath;outTask=$null;errTask=$null;closed=$false;disposed=$false;childPid=$ChildPid}
    if (-not $process.Start()) { throw 'Owned trace subprocess failed to start.' };$started=$true
    # Register the exact retained object before accessing pipes or starting tasks.
    # A startup failure cannot discard an already-started process observation.
    $script:traceLiveProcesses.Add($live)
    $live.input=$process.StandardInput
    $live.outTask=[MixelNamespaceTraceIO]::CopyBounded($process.StandardOutput.BaseStream,$output,1048576)
    $live.errTask=[MixelNamespaceTraceIO]::CopyBounded($process.StandardError.BaseStream,$errors,1048576)
    return $live
  } catch {
    $originalFailure=$_.Exception
    if ($started) {
      try {Stop-TraceRetainedProcess $live} catch {throw [AggregateException]::new('Owned trace subprocess startup and retained cleanup failed.',[Exception[]]@($originalFailure,$_.Exception))}
    } else {if ($output) {$output.Dispose()};if ($errors) {$errors.Dispose()};if ($process) {$process.Dispose()}}
    throw
  }
}
function Assert-TraceStreams($Live) {
  if ($Live.outTask.IsFaulted -or $Live.errTask.IsFaulted -or $Live.output.Length -gt 1048576 -or $Live.errors.Length -gt 1048576) { throw 'Private trace output failed its live byte watchdog.' }
}
function Complete-TraceStreams($Live) {
  if ($Live.closed) { return }
  if (-not $Live.process.HasExited) { throw 'Trace output drain requires the retained subprocess exit.' }
  try {
    foreach ($task in @($Live.outTask,$Live.errTask)) {
      if (-not $task) {continue}
      if (-not $task.Wait(10000)) { throw 'Private trace output drain timed out.' }
      [void]$task.GetAwaiter().GetResult()
    }
  } finally { if($Live.input){$Live.input.Dispose()};$Live.output.Dispose();$Live.errors.Dispose();$Live.closed=$true }
}
function Stop-TraceDebugger($Live,[int]$ChildPid) {
  if (-not $Live.process.HasExited) {
    # Ask only the retained debugger to detach. No target/register/memory mutation.
    try { $Live.input.WriteLine('qd');$Live.input.Flush() } catch { }
    $deadline=[DateTime]::UtcNow.AddSeconds(2)
    while (-not $Live.process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if (-not $Live.process.HasExited) { $Live.process.Kill();if (-not $Live.process.WaitForExit(10000)) { throw 'Retained debugger termination was not observed.' } }
  }
  Complete-TraceStreams $Live
  if ($ChildPid -gt 0 -and [MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ChildPid)) { throw 'Retained child still has an attached debugger after debugger exit.' }
}
function Stop-TraceRetainedProcess($Live) {
  if ($Live.disposed) {return}
  if ($Live.childPid -gt 0) {Stop-TraceDebugger $Live $Live.childPid}
  else {
    if (-not $Live.process.HasExited) {$Live.process.Kill();if(-not $Live.process.WaitForExit(10000)){throw 'Retained trace helper termination was not observed.'}}
    Complete-TraceStreams $Live
  }
  if (-not $Live.process.HasExited) {throw 'Retained trace subprocess exit was not observed.'}
  $Live.process.Dispose();$Live.disposed=$true
}
function Invoke-TracePython([string[]]$Arguments,[string]$Folder,[string]$Label,[int]$Seconds=90) {
  $live=Start-TraceProcess $script:python $Arguments $Folder $Label
  $operationFailure=$null
  try {
    $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
    while (-not $live.process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Assert-TraceStreams $live;Start-Sleep -Milliseconds 50 }
    Assert-TraceStreams $live
    if (-not $live.process.HasExited) { throw 'Bounded trace Python operation timed out.' }
    Complete-TraceStreams $live
    if ($live.process.ExitCode -ne 0) { throw 'Private trace Python operation failed; raw output is never printed.' }
    return [MixelNamespaceTraceIO]::ReadBounded($live.outPath,1048576)
  } catch {$operationFailure=$_.Exception;throw
  } finally {
    try {Stop-TraceRetainedProcess $live} catch {
      if($operationFailure){throw [AggregateException]::new('Private trace Python operation and retained cleanup failed.',[Exception[]]@($operationFailure,$_.Exception))}
      throw
    }
  }
}
function Assert-TraceReady([string]$Text) {
  # Concurrent output may end in a partial line; only complete marker lines count.
  $end=$Text.LastIndexOf("`n")
  if ($end -lt 0) { return $false }
  $complete=$Text.Substring(0,$end+1)
  if ($complete -match '(?i)(syntax error|couldn.t resolve|unable to insert breakpoint|no hardware breakpoint|hardware breakpoint limit)') { throw 'Trace startup reported a debugger command or binding failure.' }
  $markers=@($complete -split '\r?\n' | ForEach-Object {$_.Trim()} | Where-Object {$_.StartsWith('MIXEL_NAMESPACE_')})
  if ($markers.Count -eq 0) { return $false }
  if ($markers.Count -gt 2 -or $markers[0] -cne 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1' -or
      ($markers.Count -eq 2 -and $markers[1] -cne 'MIXEL_NAMESPACE_TRACE_READY')) { throw 'Trace startup lacks the exact ordered bindings and one READY marker.' }
  return $markers.Count -eq 2
}
function Get-TraceCdbStopFacts([string]$Text) {
  $end=$Text.LastIndexOf("`n");$complete=if ($end -ge 0) {$Text.Substring(0,$end+1)} else {''}
  $kindOf={param([string]$InputText)
    $kind='none'
    foreach ($match in [regex]::Matches($InputText,'(?im)^\s*ntdll!(DbgBreakPoint|DbgUiRemoteBreakin|LdrpDoDebuggerBreak)(?:\+0x[0-9a-f]+)?:\s*$')) {
      $kind=switch ($match.Groups[1].Value.ToLowerInvariant()) {
        'dbgbreakpoint' {'debug-break'}
        'dbguiremotebreakin' {'remote-breakin'}
        'ldrpdodebuggerbreak' {'loader-break'}
      }
    }
    return $kind
  }
  $ready=[regex]::Matches($complete,'(?m)^MIXEL_NAMESPACE_TRACE_READY\r?\n')
  $afterReady=if($ready.Count -eq 1){$Text.Substring($ready[0].Index+$ready[0].Length)}else{''}
  $postEnd=$afterReady.LastIndexOf("`n");$postComplete=if($postEnd -ge 0){$afterReady.Substring(0,$postEnd+1)}else{''}
  # Only fixed categories and capped counts leave the private debugger output.
  return [pscustomobject]@{
    debuggerPromptCount=[Math]::Min(32,[regex]::Matches($complete,'(?m)^\s*[0-9]+:[0-9a-fA-F]+>').Count)
    firstChanceBreakpointCount=[Math]::Min(32,[regex]::Matches($complete,'(?im)^[^\r\n]*Break instruction exception - code 80000003 \(first chance\)\s*$').Count)
    firstChanceSingleStepCount=[Math]::Min(32,[regex]::Matches($complete,'(?im)^[^\r\n]* - code 80000004 \(first chance\)\s*$').Count)
    firstChanceAccessViolationCount=[Math]::Min(32,[regex]::Matches($complete,'(?im)^[^\r\n]* - code c0000005 \(first chance\)\s*$').Count)
    initialBreakpointBannerCount=[Math]::Min(32,[regex]::Matches($complete,'(?im)^\s*Initial breakpoint(?: hit)?\s*$').Count)
    hardwareBreakpointHitCount=[Math]::Min(32,[regex]::Matches($complete,'(?im)^\s*Breakpoint [0-3] hit\s*$').Count)
    lastRecognizedStopKind=& $kindOf $complete
    postReadyEvidenceAvailable=$ready.Count -eq 1
    # CDB's waiting command prompt may be the final, un-terminated output.
    postReadyPromptPresent=[bool]($afterReady -match '(?m)^\s*[0-9]+:[0-9a-fA-F]+>\s*$')
    postReadyFirstChanceBreakpointCount=[Math]::Min(32,[regex]::Matches($postComplete,'(?im)^[^\r\n]*Break instruction exception - code 80000003 \(first chance\)\s*$').Count)
    postReadyLastRecognizedStopKind=& $kindOf $postComplete
  }
}
function Get-TraceExecutionFacts([int]$ChildPid,[string]$AttemptRoot,[bool]$Resumed,[int]$Previous,[bool]$Gated) {
  $facts=[ordered]@{
    privateGate=$Gated;primaryResumeObserved=$Resumed;primaryResumePrevious=$(if($Resumed -and $Previous -in @(-1,0,1)){$Previous}else{-2})
    ownedChildState='unavailable';ownedChildExitCode=0L;ownedDebuggerStateAvailable=$false;ownedDebuggerAttached=$false
    sourceResultMetadataAvailable=$false;sourceResultExists=$false;sourceResultBounded=$false
    secondaryMetadataAvailable=$false;secondaryExists=$false;secondaryBounded=$false
  }
  if ($ChildPid -le 0) {return [pscustomobject]$facts}
  try {
    $status=[MixelOrdinaryTokenFixture]::StartedStatus($ChildPid)
    if ($status -ceq 'still-running') {$facts.ownedChildState='still-running'}
    elseif ($status -cmatch '\Aexited:0x([0-9a-f]{8})\z') {$facts.ownedChildState='exited';$facts.ownedChildExitCode=[Convert]::ToInt64($Matches[1],16)}
  } catch {}
  try {$facts.ownedDebuggerAttached=[MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ChildPid);$facts.ownedDebuggerStateAvailable=$true} catch {}
  foreach ($file in @(@('fixture-result.json','sourceResult'),@('fixture-namespace.json','secondary'))) {
    try {
      $path=Join-Path $AttemptRoot $file[0];Assert-TracePlainPath $path -AllowMissing
      $exists=Test-Path -LiteralPath $path
      $bounded=$false
      if ($exists) {$item=Get-Item -LiteralPath $path -Force;$bounded=(-not $item.PSIsContainer -and $item.Length -le 4096)}
      $facts[$file[1]+'MetadataAvailable']=$true;$facts[$file[1]+'Exists']=[bool]$exists;$facts[$file[1]+'Bounded']=[bool]$bounded
    } catch {}
  }
  return [pscustomobject]$facts
}
function Get-TraceFailureFacts($Live,[ValidateSet('export','identity','commands','startup','resume','trace','parse','result','ack','cleanup')][string]$Phase,[bool]$TimedOut,$Execution=$null) {
  $text='';$stderr='';$readFailed=$false;$stdoutBytes=0L;$stderrBytes=0L
  if ($Live) {
    try {
      $stdoutBytes=(Get-Item -LiteralPath $Live.outPath).Length;$stderrBytes=(Get-Item -LiteralPath $Live.errPath).Length
      $text=[MixelNamespaceTraceIO]::ReadBounded($Live.outPath,1048576)
      $stderr=[MixelNamespaceTraceIO]::ReadBounded($Live.errPath,1048576)
    } catch {$readFailed=$true}
  }
  $end=$text.LastIndexOf("`n");$complete=if ($end -ge 0) {$text.Substring(0,$end+1)} else {''}
  $lines=@($complete -split '\r?\n' | ForEach-Object {$_.Trim()})
  $combined=$complete+"`n"+$stderr
  return [pscustomobject]@{
    phase=$Phase;stdoutBytes=[long]$stdoutBytes;stderrBytes=[long]$stderrBytes;boundedReadFailed=$readFailed;timedOut=$TimedOut
    exactBindingsCount=@($lines | Where-Object {$_ -ceq 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1'}).Count
    exactReadyCount=@($lines | Where-Object {$_ -ceq 'MIXEL_NAMESPACE_TRACE_READY'}).Count
    exactWindowCount=@($lines | Where-Object {$_ -ceq 'MIXEL_NAMESPACE_TRACE_WINDOW'}).Count
    exactEndCount=@($lines | Where-Object {$_ -ceq 'MIXEL_NAMESPACE_TRACE_END'}).Count
    syntaxFailure=[bool]($combined -match '(?i)syntax error')
    bindingFailure=[bool]($combined -match '(?i)(couldn.t resolve|unable to insert breakpoint)')
    hardwareLimitFailure=[bool]($combined -match '(?i)(no hardware breakpoint|hardware breakpoint limit)')
    scopeFailure=[bool]($complete -match '(?m)^MIXEL_NAMESPACE_TRACE_FAIL_(?:SCOPE|PID|ARCH|WINDOW_NESTED|WIN32_INTERFACE)\r?$')
    returnFailure=[bool]($complete -match '(?m)^MIXEL_NAMESPACE_TRACE_FAIL_(?:RETURN_SCOPE|RETURN_HANDLE|WIN32_RETURN_SCOPE)\r?$')
    debuggerStop=Get-TraceCdbStopFacts $text
    execution=$Execution
  }
}
function New-TraceGatedSource([string]$Source,[string]$Gate,[string]$Nonce,[string]$Private) {
  $generator=Join-Path $Private 'gate-source.py'
  $code=@'
import sys,json,os,shutil,subprocess
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from rust_toolchain import rustc_command
source,gate,nonce=Path(sys.argv[2]),sys.argv[3],sys.argv[4]
assert len(nonce)==64 and all(c in '0123456789abcdef' for c in nonce)
text=source.read_text(encoding='utf-8')
marker='        let status = support_invite_lease_status();\n        let raw_error = mixel_support_lease::fixture_raw_open_error();'
assert text.count(marker)==1,'Exact pre-guard source probe boundary changed'
barrier='''        assert_eq!(mode, "probe", "Private trace barrier requires probe mode");
        let trace_gate = std::path::Path::new(GATE_LITERAL);
        let trace_deadline = std::time::Instant::now() + std::time::Duration::from_secs(45);
        loop {
            if let Ok(metadata) = std::fs::symlink_metadata(trace_gate) {
                assert!(metadata.is_file() && !metadata.file_type().is_symlink() && metadata.len() <= 64, "Invalid private trace gate");
                if std::fs::read_to_string(trace_gate).unwrap() == NONCE_LITERAL { break; }
            }
            assert!(std::time::Instant::now() < trace_deadline, "Private trace gate timed out");
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
'''.replace('GATE_LITERAL',json.dumps(gate)).replace('NONCE_LITERAL',json.dumps(nonce))
source.write_text(text.replace(marker,barrier+marker,1),encoding='utf-8')
rustc=os.environ.get('RUSTC_BIN') or shutil.which('rustc')
assert rustc,'Qualified Rust compiler is missing'
subprocess.run(rustc_command([rustc,'--edition','2021','-D','warnings',str(source),'-o',str(source.with_suffix('.exe'))]),check=True,timeout=90)
print('PASS: private source-only timing gate compiled with warnings as errors')
'@
  [IO.File]::WriteAllText($generator,$code,[Text.UTF8Encoding]::new($false))
  [void](Invoke-TracePython @($generator,$PSScriptRoot,$Source,$Gate,$Nonce) $Private 'gate-compile' 100)
}
function Assert-TraceParsedResult($Trace,$Record) {
  $names=@($Trace.PSObject.Properties.Name | Sort-Object)
  if (($names -join ',') -cne 'completedQualifiedSourceWindow,evidenceScope,nativeCallCount,records,win32HandleNonNull' -or
      $Trace.completedQualifiedSourceWindow -isnot [bool] -or -not $Trace.completedQualifiedSourceWindow -or
      $Trace.win32HandleNonNull -isnot [bool] -or ($Trace.nativeCallCount -isnot [int] -and $Trace.nativeCallCount -isnot [long]) -or
      $Trace.nativeCallCount -lt 0 -or $Trace.nativeCallCount -gt 32 -or @($Trace.records).Count -ne $Trace.nativeCallCount -or
      $Trace.evidenceScope -cne 'source-only debugger call observation; not signed runtime/customer-Accept qualification') { throw 'Private trace parser returned an invalid bounded result schema.' }
  if ($Trace.win32HandleNonNull -ne ($Record.rawOpenError -eq 0)) { throw 'Traced Win32 return disagrees with the unchanged source classification.' }
  $sequence=0
  foreach ($item in @($Trace.records)) {
    $sequence++
    if ((@($item.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'api,desiredAccess,handleNonNull,nativeStatus,pathKind,rootNonNull,sequence' -or
        ($item.sequence -isnot [int] -and $item.sequence -isnot [long]) -or $item.sequence -ne $sequence -or ($item.api -isnot [int] -and $item.api -isnot [long]) -or $item.api -notin @(1,2) -or
        ($item.pathKind -isnot [int] -and $item.pathKind -isnot [long]) -or $item.pathKind -lt 0 -or $item.pathKind -gt 12 -or
        $item.rootNonNull -isnot [bool] -or $item.handleNonNull -isnot [bool]) { throw 'Private native trace record is not the exact ordered scalar schema.' }
    foreach ($field in @('desiredAccess','nativeStatus')) {
      $value=$item.PSObject.Properties[$field].Value
      if (($value -isnot [int] -and $value -isnot [long]) -or $value -lt 0 -or $value -gt 4294967295) { throw 'Private native trace DWORD exceeded its scalar domain.' }
    }
  }
}
function Invoke-TraceAttempt([ValidateSet('absent','world')][string]$Scenario,[switch]$Gated) {
  $attemptRoot=Join-Path $script:fixtureRoot ([Guid]::NewGuid().ToString('N'))
  $private=Join-Path $script:privateRoot ([Guid]::NewGuid().ToString('N'))
  Assert-TracePlainPath $attemptRoot -AllowMissing;Assert-TracePlainPath $private -AllowMissing
  New-Item -ItemType Directory -Path $attemptRoot,$private | Out-Null
  $gateRoot=Join-Path $script:gateRoot ([Guid]::NewGuid().ToString('N'));Assert-TracePlainPath $gateRoot -AllowMissing;New-Item -ItemType Directory -Path $gateRoot | Out-Null
  Set-TraceDirectoryAcl $gateRoot $script:ownedSid -ReadOnly
  $gate=Join-Path $gateRoot 'ready';$nonce=[Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N')
  $event='Mixel-Remote-Standard-Lease-'+[Guid]::NewGuid().ToString('N')
  $source=Join-Path $attemptRoot 'lease-standard.rs';$exe=Join-Path $attemptRoot 'lease-standard.exe'
  $live=$null;$childPid=0;$eventHandle=[IntPtr]::Zero;$timeout=$false;$completed=$false;$phase='export';$attemptFailure=$null;$resumed=$false;$previous=-2
  try {
    [void](Invoke-TracePython @((Join-Path $PSScriptRoot 'test-support-lease.py'),'--export-standard-windows-fixture',$attemptRoot,'--event',('Global\'+$event)) $private 'source-export' 100)
    Assert-TracePlainPath $source;Assert-TracePlainPath $exe
    $originalHash=(Get-FileHash $source -Algorithm SHA256).Hash
    if ($Gated) { New-TraceGatedSource $source $gate $nonce $private }
    $diagnosticHash=(Get-FileHash $source -Algorithm SHA256).Hash;$exeHash=(Get-FileHash $exe -Algorithm SHA256).Hash
    if (($Gated -and $originalHash -ceq $diagnosticHash) -or (-not $Gated -and $originalHash -cne $diagnosticHash)) { throw 'Private timing-gate source hash binding is invalid.' }
    $mode=Join-Path $attemptRoot 'fixture-mode.txt';[IO.File]::WriteAllText($mode,'probe',[Text.UTF8Encoding]::new($false))
    Set-TraceDirectoryAcl $attemptRoot $script:ownedSid
    foreach ($file in @($source,$exe,$mode)) { Set-TraceReadOnlyFile $file $script:ownedSid }
    if ($Scenario -ceq 'world') { $eventHandle=[MixelIsolatedLeaseControl]::WorldEvent('Global\'+$event) }
    $script:profileStarted=$true
    $childPid=[MixelOrdinaryTokenFixture]::StartStandardUser($exe,$script:ownedUser,$script:ownedPassword,$true)
    $phase='identity'
    $identity=[MixelOrdinaryTokenFixture]::SuspendedOwnedIdentity($childPid)
    $thread=[MixelOrdinaryTokenFixture]::TraceOwnedPrimaryThreadId($childPid,$exe)
    if ($identity['kernelPid'] -cne [string]$childPid -or $identity['sid'] -cne $script:ownedSid -or $identity['elevated'] -cne 'False' -or
        $identity['sessionId'] -cne [string]$script:session -or $identity['integritySid'] -cne 'S-1-16-8192' -or
        (Get-FileHash $exe -Algorithm SHA256).Hash -cne $exeHash) { throw 'Retained trace PID/SID/session/medium/non-elevated/image qualification failed.' }
    $phase='commands'
    [void](Invoke-TracePython @((Join-Path $PSScriptRoot 'windows-namespace-cdb.py'),'--pid',[string]$childPid,'--tid',[string]$thread,'--session',[string]$script:session,'--event',$event,'--windows-folder',$private,'--output',$private) $private 'commands')
    $arguments=@('-p',[string]$childPid,'-pd','-noshell','-nosqm','-cf',(Join-Path $private 'start.txt'))
    if ($Gated) { $arguments=@('-p',[string]$childPid,'-pr','-pd','-noshell','-nosqm','-cf',(Join-Path $private 'start.txt')) }
    $live=Start-TraceProcess $script:cdb $arguments $private 'cdb' $childPid
    $phase='startup'
    $deadline=[DateTime]::UtcNow.AddSeconds(20);$ready=$false
    while ([DateTime]::UtcNow -lt $deadline) {
      Assert-TraceStreams $live
      if ($live.process.HasExited -or [MixelOrdinaryTokenFixture]::StartedStatus($childPid) -cne 'still-running') { throw 'Retained source/debugger exited before trace readiness.' }
      $ready=Assert-TraceReady ([MixelNamespaceTraceIO]::ReadBounded($live.outPath,1048576))
      if ($ready -and [MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($childPid)) { break }
      Start-Sleep -Milliseconds 50
    }
    if (-not $ready -or -not [MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($childPid)) {
      $timeout=$true
      if ($Gated) { throw 'Private gated trace did not reach exact debugger readiness.' }
      Write-Host ('OBSERVED: bounded source-only trace startup facts; '+((Get-TraceFailureFacts $live $phase $true (Get-TraceExecutionFacts $childPid $attemptRoot $resumed $previous ([bool]$Gated))) | ConvertTo-Json -Depth 4 -Compress))
      return [pscustomobject]@{StartupTimedOut=$true}
    }
    $phase='resume'
    $previous=[MixelOrdinaryTokenFixture]::ResumeOwnedPrimaryThread($childPid)
    $resumed=$true
    if ((-not $Gated -and $previous -ne 1) -or ($Gated -and $previous -notin @(0,1))) { throw 'Trace did not perform the one qualified owned primary resume.' }
    if ($Gated) {
      $temporary=Join-Path $gateRoot 'ready.new';[IO.File]::WriteAllText($temporary,$nonce,[Text.UTF8Encoding]::new($false));[IO.File]::Move($temporary,$gate)
    }
    $phase='trace'
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while (-not $live.process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Assert-TraceStreams $live;Start-Sleep -Milliseconds 50 }
    Assert-TraceStreams $live
    if (-not $live.process.HasExited) { $timeout=$true;throw 'Source trace did not reach END and detach within its trace deadline.' }
    Complete-TraceStreams $live
    if ($live.process.ExitCode -ne 0 -or [MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($childPid)) { throw 'Source trace did not detach and exit successfully.' }
    $phase='parse'
    $parsed=(Invoke-TracePython @((Join-Path $PSScriptRoot 'windows-namespace-cdb.py'),'--parse',$live.outPath) $private 'parse') | ConvertFrom-Json
    $phase='result'
    $result=Join-Path $attemptRoot 'fixture-result.json';$deadline=[DateTime]::UtcNow.AddSeconds(2)
    while (-not (Test-Path -LiteralPath $result) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    Assert-TracePlainPath $result
    $record=([MixelNamespaceTraceIO]::ReadBounded($result,4096)) | ConvertFrom-Json
    Assert-StandardLeaseSafeResult $record ([uint32]$childPid)
    Assert-TraceParsedResult $parsed $record
    Assert-TracePlainPath (Join-Path $attemptRoot 'fixture-namespace.json')
    $secondary=([MixelNamespaceTraceIO]::ReadBounded((Join-Path $attemptRoot 'fixture-namespace.json'),4096)) | ConvertFrom-Json
    if (($secondary.pid -isnot [int] -and $secondary.pid -isnot [long]) -or $secondary.pid -ne $childPid -or
        ($secondary.GlobalAbsoluteEventError -isnot [int] -and $secondary.GlobalAbsoluteEventError -isnot [long]) -or
        $secondary.GlobalAbsoluteEventError -ne $(if ($Scenario -ceq 'world') {0} else {2})) { throw 'Separate read-only native event control disagrees with its owned fixture scenario.' }
    $phase='ack'
    [IO.File]::WriteAllText((Join-Path $attemptRoot "fixture-release-$childPid"),'verified')
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while ([MixelOrdinaryTokenFixture]::StartedStatus($childPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if ([MixelOrdinaryTokenFixture]::StartedStatus($childPid) -cne 'exited:0x00000000') { throw 'Retained source trace child acknowledgement/exit failed.' }
    $completed=$true
    return [pscustomobject]@{StartupTimedOut=$false;Scenario=$Scenario;PrivateGate=[bool]$Gated;FirstCallQualified=$true;OriginalSourceSHA256=$originalHash;DiagnosticSourceSHA256=$diagnosticHash;OwnedIdentityQualified=$true;DebuggerDetached=$true;SingleResumeQualified=$true;rawOpenError=$record.rawOpenError;Trace=$parsed}
  } catch {
    $attemptFailure=$_.Exception
    Write-Host ('OBSERVED: bounded source-only trace failure facts; '+((Get-TraceFailureFacts $live $phase $timeout (Get-TraceExecutionFacts $childPid $attemptRoot $resumed $previous ([bool]$Gated))) | ConvertTo-Json -Depth 4 -Compress))
    throw
  } finally {
    $errors=[Collections.Generic.List[Exception]]::new()
    $subprocessesStopped=$true;$childrenStopped=$false
    foreach ($retained in $script:traceLiveProcesses) {
      try {Stop-TraceRetainedProcess $retained} catch {$subprocessesStopped=$false;$errors.Add([InvalidOperationException]::new('Retained trace subprocess cleanup failed.',$_.Exception))}
    }
    try {[MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses();$childrenStopped=$true} catch {$errors.Add([InvalidOperationException]::new('Retained source trace child cleanup failed.',$_.Exception))}
    # Preserve native ownership until every helper/debugger and child exit is
    # verified, including a process whose startup failed before it was returned.
    if ($subprocessesStopped -and $childrenStopped) {[MixelOrdinaryTokenFixture]::CloseStartedObservations()}
    if ($eventHandle -ne [IntPtr]::Zero) {try {[MixelIsolatedLeaseControl]::Close($eventHandle)} catch {$errors.Add([InvalidOperationException]::new('Owned GUID event handle cleanup failed.'))}}
    if ($errors.Count -gt 0) {if($attemptFailure){$errors.Insert(0,$attemptFailure)};throw [AggregateException]::new('Source trace attempt cleanup failed.',$errors)}
    if ($timeout -and $completed) { throw 'Trace timeout was inconsistently marked completed.' }
  }
}

$python=(Get-Command python -CommandType Application -ErrorAction Stop).Source
$cdb=Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/Debuggers/x64/cdb.exe'
$signature=Get-AuthenticodeSignature -FilePath $cdb
if ($signature.Status -cne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'Prepared CDB is not validly signed by Microsoft.' }
# The existing Program Files SDK is read-only to the disposable standard user.
$session=[uint32](Get-Process -Id $PID).SessionId
if ($session -eq 0) { throw 'Source trace requires the actual interactive runner session.' }
$ownedUser='mixelqs'+[Guid]::NewGuid().ToString('N').Substring(0,10)
$ownedSid=$null;$ownedPassword=$null;$random=$null;$profileStarted=$false;$namespaceAccess=$null;$desktopAccess=$null;$failure=$null
$privateRoot=Join-Path $env:RUNNER_TEMP ('mixel-trace-private-'+[Guid]::NewGuid().ToString('N'))
$fixtureRoot=Join-Path $env:RUNNER_TEMP ('mixel-trace-source-'+[Guid]::NewGuid().ToString('N'))
$gateRoot=Join-Path $env:RUNNER_TEMP ('mixel-trace-gates-'+[Guid]::NewGuid().ToString('N'))
if ($privateRoot -cnotmatch '\A[A-Z]:\\[A-Za-z0-9_\\.-]+\z' -or $privateRoot.Split('\') -ccontains '..') { throw 'Trace command folder is not a bounded unambiguous Windows path.' }
$originalDesktop=[MixelOrdinaryTokenFixture]::DesktopAclHashes()
$fileSnapshots=[Collections.Generic.Dictionary[string,byte[]]]::new()
$createdRoots=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$stage='create-user'
try {
  $accountStart=$launchSource.IndexOf('$random = [byte[]]::new(24)')
  $accountEnd=$launchSource.IndexOf("`$fixtureStage = 'grant owned standard desktop access'",$accountStart)
  if ($accountStart -lt 0 -or $accountEnd -le $accountStart) { throw 'Exact disposable customer account bootstrap changed.' }
  . ([scriptblock]::Create($launchSource.Substring($accountStart,$accountEnd-$accountStart)))
  $stage='verify-user'
  if (-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) { throw 'Exact owned trace account SID/name verification failed.' }
  foreach ($directory in @($privateRoot,$fixtureRoot,$gateRoot)) {
    $kind=if($directory -ceq $privateRoot){'private'}elseif($directory -ceq $fixtureRoot){'source'}else{'gate'}
    $stage='create-'+$kind+'-root'
    Assert-TracePlainPath $directory -AllowMissing
    New-Item -ItemType Directory -Path $directory | Out-Null
    [void]$createdRoots.Add($directory)
    $stage='initialize-'+$kind+'-root'
    $canonical=[byte[]](Initialize-TraceCreatedRoot $directory)
    $stage='snapshot-'+$kind+'-root'
    $fileSnapshots.Add($directory,$canonical)
    $stage='restrict-'+$kind+'-root'
    Set-TraceDirectoryAcl $directory $(if ($directory -ceq $privateRoot) {''} else {$ownedSid}) -ReadOnly
  }
  $stage='grant-desktop'
  $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
  $stage='grant-namespace'
  $namespaceAccess=[MixelCI.StandardNamespaceAccess]::new($ownedSid,$ownedUser,$session)
  foreach ($scenario in @('absent','world')) {
    $stage='trace-'+$scenario
    $result=Invoke-TraceAttempt $scenario
    if ($result.StartupTimedOut) { $result=Invoke-TraceAttempt $scenario -Gated }
    if ($result.StartupTimedOut -or -not $result.FirstCallQualified) { throw 'Source trace lacks first-call qualification.' }
    # Only validated numeric observations/booleans and source digests go public.
    Write-Host ('OBSERVED: source-only namespace hardware trace; '+($result | ConvertTo-Json -Depth 8 -Compress))
  }
} catch {
  Write-TraceStageFailure $stage $_.Exception
  $failure=[InvalidOperationException]::new(('Source-only namespace tracing failed at '+$stage+'. No raw debugger output was printed.'))
} finally {
  $cleanup=[Collections.Generic.List[Exception]]::new()
  $subprocessesStopped=$true;$childrenStopped=$false
  foreach ($retained in $script:traceLiveProcesses) {
    try {Stop-TraceRetainedProcess $retained} catch {$subprocessesStopped=$false;$cleanup.Add([InvalidOperationException]::new('Final retained trace subprocess cleanup failed.'))}
  }
  try {[MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses();$childrenStopped=$true} catch {$cleanup.Add([InvalidOperationException]::new('Final retained trace child cleanup failed.'))}
  $ownedProcessesStopped=$subprocessesStopped -and $childrenStopped
  if ($ownedProcessesStopped) {
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  if ($namespaceAccess) {try {$namespaceAccess.Dispose()} catch {$cleanup.Add([InvalidOperationException]::new('Exact trace namespace DACL restoration failed.'))}}
  if ($desktopAccess) {try {$desktopAccess.Dispose()} catch {$cleanup.Add([InvalidOperationException]::new('Exact trace desktop DACL restoration failed.'))}}
  if ([MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $originalDesktop) {$cleanup.Add([InvalidOperationException]::new('Trace desktop DACL hashes changed.'))}
  if ($profileStarted -and $ownedSid) {
    $removed=$false
    for ($attempt=0;$attempt -lt 5;$attempt++) {try {[MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid);$removed=$true;break} catch {if ($attempt -lt 4) {Start-Sleep -Seconds 2}}}
    if (-not $removed) {$cleanup.Add([InvalidOperationException]::new('Owned trace account profile cleanup failed.'))}
  }
  if ($ownedSid) {try {Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))} catch {$cleanup.Add([InvalidOperationException]::new('Owned trace account SID cleanup failed.'))}}
  $ownedPassword=$null;$random=$null
  foreach ($pair in $fileSnapshots.GetEnumerator()) {
    try {
      # Delete private contents while their runner-only ACL is still installed.
      Remove-TracePlainContents $pair.Key
      Restore-TraceRootAcl $pair.Key $pair.Value
      Remove-Item -LiteralPath $pair.Key -Recurse -Force
    } catch {
      $kind=if($pair.Key -ceq $privateRoot){'private'}elseif($pair.Key -ceq $fixtureRoot){'source'}else{'gate'}
      Write-TraceStageFailure ('cleanup-'+$kind+'-root') $_.Exception
      $cleanup.Add([InvalidOperationException]::new('Owned trace private/source/gate ACL restoration or file cleanup failed.'))
    }
  }
  foreach ($directory in $createdRoots) {
    if ($fileSnapshots.ContainsKey($directory)) {continue}
    try {
      # A root whose initialization failed was never used or snapshotted.
      # Delete only that owned empty creation, with no ACL restoration claim.
      Assert-TracePlainPath $directory
      if (@(Get-ChildItem -LiteralPath $directory -Force).Count -ne 0) {throw 'Unused new trace root is unexpectedly nonempty.'}
      Remove-Item -LiteralPath $directory -Force
    } catch {$cleanup.Add([InvalidOperationException]::new('Unused new trace root cleanup failed.'))}
  }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) {$cleanup.Add([InvalidOperationException]::new('Owned trace account remains.'))}
  } else {
    # Keep retained handles and restrictive ACLs for an unverified live process.
    # Never restore/delete its namespace, account or private files.
    $cleanup.Add([InvalidOperationException]::new('Owned process exit was not verified; ACL, account and private storage cleanup was withheld.'))
  }
  $ownedPassword=$null;$random=$null
  if ($failure) {$cleanup.Insert(0,$failure)}
  if ($cleanup.Count -gt 0) {throw [AggregateException]::new('Source-only namespace trace or exact owned cleanup failed.',$cleanup)}
}
Write-Host 'PASS: source-only hardware traces completed with retained identity, first-call qualification, bounded private output and exact owned cleanup; signed customer release gates remain required.'
