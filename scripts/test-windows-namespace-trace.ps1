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
$resumeReject='if (previous > 1) throw new InvalidOperationException("Unexpected owned primary thread suspension count");'
if ($normalSource.IndexOf($resumeReject) -lt 0 -or $normalSource.IndexOf($resumeReject) -ne $normalSource.LastIndexOf($resumeReject)) {throw 'Exact retained suspension rejection changed.'}
# Preserve the customer's helper. Only its private extracted diagnostic clone
# carries the same rejected count; native call, predicate and finally stay exact.
$normalSource=$normalSource.Replace($resumeReject,'if (previous > 1) throw new TraceAdditionalSuspensionException(previous);')
$marker = '  public static int StartStandardUser(string executable, string username, string password) {'
if ($normalSource.IndexOf($marker) -lt 0 -or $normalSource.IndexOf($marker) -ne $normalSource.LastIndexOf($marker)) { throw 'Exact retained fixture injection boundary changed.' }
$traceBridge = @'
  public sealed class TraceAdditionalSuspensionException : InvalidOperationException {
    public uint PreviousCount {get;}
    public TraceAdditionalSuspensionException(uint previous) : base("Additional owned source-trace suspension rejected") {
      if(previous<=1 || previous==UInt32.MaxValue)throw new ArgumentOutOfRangeException("previous");
      PreviousCount=previous;
    }
  }
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
  if ($ChildPid -gt 0 -and $File -cne $script:cdb) {throw 'Only the verified retained CDB may receive trace command input.'}
  Assert-TracePlainPath $Folder
  $outPath=Join-Path $Folder ($Label+'-stdout.private');$errPath=Join-Path $Folder ($Label+'-stderr.private')
  Assert-TracePlainPath $outPath -AllowMissing;Assert-TracePlainPath $errPath -AllowMissing
  $output=$null;$errors=$null;$process=$null;$started=$false;$live=$null
  try {
    $output=[IO.FileStream]::new($outPath,'CreateNew','Write','ReadWrite')
    $errors=[IO.FileStream]::new($errPath,'CreateNew','Write','ReadWrite')
    $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName=$File;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$ChildPid -gt 0;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    foreach ($argument in $Arguments) { [void]$info.ArgumentList.Add($argument) }
    $process=[Diagnostics.Process]::new();$process.StartInfo=$info
    $live=[pscustomobject]@{process=$process;input=$null;output=$output;errors=$errors;outPath=$outPath;errPath=$errPath;outTask=$null;errTask=$null;closed=$false;disposed=$false;childPid=$ChildPid;continuationAttempted=$false;continuationWritten=$false;continuationQueued=$false}
    if (-not $process.Start()) { throw 'Owned trace subprocess failed to start.' };$started=$true
    # Register the exact retained object before accessing pipes or starting tasks.
    # A startup failure cannot discard an already-started process observation.
    $script:traceLiveProcesses.Add($live)
    if ($ChildPid -gt 0) {$live.input=$process.StandardInput}
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
  if ($markers.Count -gt 6 -or $markers[0] -cne 'MIXEL_NAMESPACE_THREAD_SELECTION processMatched=1 threadMatched=1' -or
      ($markers.Count -ge 2 -and $markers[1] -cne 'MIXEL_NAMESPACE_HW_LIST_BEGIN') -or
      ($markers.Count -ge 3 -and $markers[2] -cne 'MIXEL_NAMESPACE_HW_LIST_END') -or
      ($markers.Count -ge 4 -and $markers[3] -cnotmatch '\AMIXEL_NAMESPACE_HW_CONTEXT processMatched=[01] threadMatched=[01] addressPresentMask=([0-9]|1[0-5]) localEnableMask=([0-9]|1[0-5]) globalEnableMask=([0-9]|1[0-5]) executionByteMask=([0-9]|1[0-5])\z') -or
      ($markers.Count -ge 5 -and $markers[4] -cne 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1') -or
      ($markers.Count -eq 6 -and $markers[5] -cne 'MIXEL_NAMESPACE_TRACE_READY')) { throw 'Trace startup lacks exact ordered thread selection, typed hardware observations, bindings and one READY marker.' }
  return $markers.Count -eq 6
}
function Send-TraceContinuation($Live,[int]$ChildPid,[bool]$Ready,[bool]$Resumed,[int]$Previous,[bool]$Gated) {
  $retained=@($script:traceLiveProcesses | Where-Object {[object]::ReferenceEquals($_,$Live)})
  if ($retained.Count -ne 1 -or $ChildPid -le 0 -or $Live.childPid -ne $ChildPid -or
      $Live.process.StartInfo.FileName -cne $script:cdb -or -not $Ready -or -not $Resumed -or
      (-not $Gated -and $Previous -ne 1) -or ($Gated -and $Previous -notin @(0,1)) -or
      $Live.closed -or $Live.disposed -or $Live.continuationAttempted -or $Live.continuationWritten -or $Live.continuationQueued -or $Live.process.HasExited -or -not $Live.input -or
      [MixelOrdinaryTokenFixture]::StartedStatus($ChildPid) -cne 'still-running' -or
      -not [MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ChildPid)) {
    throw 'Trace continuation requires the same retained live debugger, exact readiness and one qualified owned resume.'
  }
  # Set the attempt flag before any write. A partial write or failed flush is
  # fatal and cannot authorize a second continuation command.
  $Live.continuationAttempted=$true
  $Live.input.Write(".echo MIXEL_NAMESPACE_CONTINUATION_ACK; g`r`n");$Live.continuationWritten=$true
  $Live.input.Flush();$Live.continuationQueued=$true
}
function Get-TraceAdditionalSuspensionCount([Exception]$Failure) {
  $current=$Failure
  for($depth=0;$current -and $depth -lt 16;$depth++) {
    if($current -is [MixelOrdinaryTokenFixture+TraceAdditionalSuspensionException]) {return [long]$current.PreviousCount}
    $current=$current.InnerException
  }
  return -1L
}
function Test-TraceAdditionalSuspensionFallback($Live,[int]$ChildPid,[string]$AttemptRoot,[long]$Count,[bool]$Ready,[bool]$ResumeAttempted,[bool]$Resumed,[bool]$Gated) {
  try {
    if($Gated -or -not $Ready -or -not $ResumeAttempted -or $Resumed -or $Count -lt 2 -or $Count -gt 4294967294 -or
        @($script:traceLiveProcesses | Where-Object {[object]::ReferenceEquals($_,$Live)}).Count -ne 1 -or
        $ChildPid -le 0 -or $Live.childPid -ne $ChildPid -or $Live.closed -or $Live.disposed -or $Live.process.HasExited -or
        $Live.process.StartInfo.FileName -cne $script:cdb -or $Live.continuationAttempted -or $Live.continuationWritten -or $Live.continuationQueued -or
        [MixelOrdinaryTokenFixture]::StartedStatus($ChildPid) -cne 'still-running' -or
        -not [MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ChildPid)) {return $false}
    $text=[MixelNamespaceTraceIO]::ReadBounded($Live.outPath,1048576)
    if(-not (Assert-TraceReady $text) -or $text.Contains('MIXEL_NAMESPACE_TRACE_WINDOW') -or $text.Contains('MIXEL_NAMESPACE_TRACE_END')) {return $false}
    foreach($file in @('fixture-result.json','fixture-namespace.json','fixture-hwcontrol.json')) {
      $path=Join-Path $AttemptRoot $file;Assert-TracePlainPath $path -AllowMissing
      if(Test-Path -LiteralPath $path) {return $false}
    }
    return $true
  } catch {return $false}
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
function Get-TraceExecutionFacts([int]$ChildPid,[string]$AttemptRoot,[bool]$Resumed,[int]$Previous,[bool]$Gated,$Continuation=$null,[bool]$ResumeAttempted=$false,[long]$AdditionalCount=-1,[uint32]$Thread=0,[uint32]$ExpectedProbeRva=0,[uint32]$ImageSize=0) {
  $facts=[ordered]@{
    privateGate=$Gated;primaryResumeObserved=$Resumed;primaryResumePrevious=$(if($Resumed -and $Previous -in @(-1,0,1)){$Previous}else{-2})
    ownedChildState='unavailable';ownedChildExitCode=0L;ownedDebuggerStateAvailable=$false;ownedDebuggerAttached=$false
    sourceResultMetadataAvailable=$false;sourceResultExists=$false;sourceResultBounded=$false
    secondaryMetadataAvailable=$false;secondaryExists=$false;secondaryBounded=$false
    hwcontrolMetadataAvailable=$false;hwcontrolExists=$false;hwcontrolBounded=$false;hwcontrolSchemaValid=$false;sourceProcessMatched=$false;sourceThreadMatched=$false;sourceProbeRvaAvailable=$false;sourceProbeRvaMatched=$false
    continuationStateAvailable=$false;continuationAttempted=$false;continuationWritten=$false;continuationQueued=$false
    primaryResumeAttempted=$ResumeAttempted -or $Resumed;additionalSuspensionCountKnown=$AdditionalCount -ge 2 -and $AdditionalCount -le 4294967294
    additionalPreviousSuspendCount=$(if($AdditionalCount -ge 2 -and $AdditionalCount -le 4294967294){$AdditionalCount}else{0L})
  }
  if ($ChildPid -le 0) {return [pscustomobject]$facts}
  try {
    if ($Continuation -and @($script:traceLiveProcesses | Where-Object {[object]::ReferenceEquals($_,$Continuation)}).Count -eq 1 -and
        $Continuation.childPid -eq $ChildPid -and $Continuation.continuationAttempted -is [bool] -and
        $Continuation.continuationWritten -is [bool] -and $Continuation.continuationQueued -is [bool]) {
      $facts.continuationStateAvailable=$true;$facts.continuationAttempted=$Continuation.continuationAttempted
      $facts.continuationWritten=$Continuation.continuationWritten;$facts.continuationQueued=$Continuation.continuationQueued
    }
  } catch {}
  try {
    $status=[MixelOrdinaryTokenFixture]::StartedStatus($ChildPid)
    if ($status -ceq 'still-running') {$facts.ownedChildState='still-running'}
    elseif ($status -cmatch '\Aexited:0x([0-9a-f]{8})\z') {$facts.ownedChildState='exited';$facts.ownedChildExitCode=[Convert]::ToInt64($Matches[1],16)}
  } catch {}
  try {$facts.ownedDebuggerAttached=[MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ChildPid);$facts.ownedDebuggerStateAvailable=$true} catch {}
  foreach ($file in @(@('fixture-result.json','sourceResult',4096),@('fixture-namespace.json','secondary',4096),@('fixture-hwcontrol.json','hwcontrol',128))) {
    try {
      $path=Join-Path $AttemptRoot $file[0];Assert-TracePlainPath $path -AllowMissing
      $exists=Test-Path -LiteralPath $path
      $bounded=$false
      if ($exists) {$item=Get-Item -LiteralPath $path -Force;$bounded=(-not $item.PSIsContainer -and $item.Length -le $file[2])}
      $facts[$file[1]+'MetadataAvailable']=$true;$facts[$file[1]+'Exists']=[bool]$exists;$facts[$file[1]+'Bounded']=[bool]$bounded
      if($file[1] -ceq 'hwcontrol' -and $bounded){
        $proof=Read-TraceProbeProof $path
        if((@($proof.PSObject.Properties.Name|Sort-Object)-join ',') -ceq 'pid,probeRva,tid'){
          $valid=$true
          foreach($field in @('pid','tid')){$value=$proof.PSObject.Properties[$field].Value;if(($value -isnot [int] -and $value -isnot [long]) -or $value -le 0 -or $value -gt 4294967295){$valid=$false}}
          $rva=$proof.probeRva
          if(($rva -isnot [int] -and $rva -isnot [long]) -or $rva -lt 0 -or $rva -ge 268435456){$valid=$false}
          if($valid){
            $facts.hwcontrolSchemaValid=$true;$facts.sourceProcessMatched=$proof.pid -eq $ChildPid;$facts.sourceThreadMatched=$Thread -ne 0 -and $proof.tid -eq $Thread
            $facts.sourceProbeRvaAvailable=$rva -gt 0
            $facts.sourceProbeRvaMatched=$rva -gt 0 -and $ExpectedProbeRva -gt 0 -and $ExpectedProbeRva -lt $ImageSize -and $ImageSize -le 268435456 -and $rva -eq $ExpectedProbeRva
          }
        }
      }
    } catch {}
  }
  return [pscustomobject]$facts
}
function Get-TraceEntryFacts([string]$Text) {
  $facts=[ordered]@{markerSchemaValid=$true;hardwareControlCount=0;completeMarkerCount=0;win32EntryCount=0;nativeDirectoryPreWindowCount=0;nativeEventPreWindowCount=0;entryLimitFailure=$false;entries=@()}
  if ([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576) {$facts.markerSchemaValid=$false;return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");$complete=if($end -ge 0){$Text.Substring(0,$end+1)}else{''}
  $ready=$false;$window=$false;$qualified=$false;$qualifiedInterface=$false;$items=[Collections.Generic.List[object]]::new()
  foreach ($line in @($complete -split '\r?\n' | ForEach-Object {$_.Trim()})) {
    if ($line -ceq 'MIXEL_NAMESPACE_TRACE_READY') {if($ready){$facts.markerSchemaValid=$false;break};$ready=$true;continue}
    if ($line -ceq 'MIXEL_NAMESPACE_TRACE_WINDOW') {if(-not $ready -or $window -or -not $qualifiedInterface -or $facts.hardwareControlCount -ne 1){$facts.markerSchemaValid=$false;break};$window=$true;continue}
    if ($line -ceq 'MIXEL_NAMESPACE_HWCONTROL scopeQualified=1') {if(-not $ready -or $window -or $qualified -or $facts.hardwareControlCount -ne 0){$facts.markerSchemaValid=$false;break};$facts.hardwareControlCount=1;continue}
    if ($line -cmatch '\AMIXEL_NAMESPACE_HWCONTROL') {$facts.markerSchemaValid=$false;break}
    if ($line -ceq 'MIXEL_NAMESPACE_TRACE_FAIL_ENTRY_LIMIT') {$facts.entryLimitFailure=$true;continue}
    if ($line -cnotmatch '\AMIXEL_NAMESPACE_(?:WIN32_ENTRY|PREWINDOW_ENTRY)') {continue}
    if (-not $ready -or $window -or $qualified -or $items.Count -ge 32) {$facts.markerSchemaValid=$false;break}
    if ($line -cmatch '\AMIXEL_NAMESPACE_WIN32_ENTRY sequence=([1-9][0-9]?) leafValidMask=([0-7]) leafMatched=([01]) accessMatched=([01]) inheritFalse=([01])\z') {
      $sequence=[int]$Matches[1];$mask=[int]$Matches[2];$matched=$Matches[3] -ceq '1';$access=$Matches[4] -ceq '1';$inherit=$Matches[5] -ceq '1'
      if ($sequence -ne $items.Count+1 -or $sequence -gt 32 -or ($matched -and ($mask -eq 0 -or $facts.hardwareControlCount -ne 1))) {$facts.markerSchemaValid=$false;break}
      $items.Add([pscustomobject]@{kind='win32';sequence=$sequence;leafValidMask=$mask;leafMatched=$matched;accessMatched=$access;inheritFalse=$inherit})
      $facts.win32EntryCount++;$qualified=$matched;$qualifiedInterface=$matched -and $access -and $inherit
    } elseif ($line -cmatch '\AMIXEL_NAMESPACE_PREWINDOW_ENTRY sequence=([1-9][0-9]?) api=([12])\z') {
      $sequence=[int]$Matches[1];$api=[int]$Matches[2]
      if ($sequence -ne $items.Count+1 -or $sequence -gt 32) {$facts.markerSchemaValid=$false;break}
      $items.Add([pscustomobject]@{kind='native-prewindow';sequence=$sequence;api=$api})
      if($api -eq 1){$facts.nativeDirectoryPreWindowCount++}else{$facts.nativeEventPreWindowCount++}
    } else {$facts.markerSchemaValid=$false;break}
  }
  # Malformed diagnostics never leak a partially accepted or arbitrary marker.
  if ($facts.markerSchemaValid) {$facts.completeMarkerCount=$items.Count;$facts.entries=$items.ToArray()}
  else {$facts.hardwareControlCount=0;$facts.win32EntryCount=0;$facts.nativeDirectoryPreWindowCount=0;$facts.nativeEventPreWindowCount=0}
  return [pscustomobject]$facts
}
function Get-TraceStartupFacts([string]$Text) {
  $facts=[ordered]@{selectionMarkerCount=0;selectionSchemaValid=$true;selectionAvailable=$false;processMatched=$false;threadMatched=$false;failureTagCount=0;failureCategories=@()}
  if([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576){$facts.selectionSchemaValid=$false;return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");$complete=if($end -ge 0){$Text.Substring(0,$end+1)}else{''}
  $afterSelection=$false;$bindingOrReady=$false;$process=$false;$thread=$false
  $allowed=@('PID','ARCH','SCOPE','WINDOW_NESTED','WIN32_INTERFACE','WIN32_STACK','WIN32_RETURN','WIN32_RETURN_SCOPE','ENTRY_LIMIT','HWCONTROL_MISSING','HWCONTROL_SCOPE','NATIVE_NESTED','RECORD_LIMIT','STACK','POINTER','ATTR_SIZE','NAME_HEADER','NAME_LENGTH','NAME_BUFFER','RETURN_SCOPE','RETURN_HANDLE','IMAGE','CONTROL_ADDRESS','CONTROL_BINDING')
  $categories=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach($line in @($complete -split '\r?\n'|ForEach-Object {$_.Trim()})){
    if($line -ceq 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1' -or $line -ceq 'MIXEL_NAMESPACE_TRACE_READY'){$bindingOrReady=$true}
    if($line -cmatch '\AMIXEL_NAMESPACE_THREAD_SELECTION processMatched=([01]) threadMatched=([01])\z'){
      $facts.selectionMarkerCount=[Math]::Min(32,$facts.selectionMarkerCount+1)
      if($afterSelection -or $bindingOrReady){$facts.selectionSchemaValid=$false}
      $afterSelection=$true;$process=$Matches[1] -ceq '1';$thread=$Matches[2] -ceq '1'
    }elseif($line -cmatch '\AMIXEL_NAMESPACE_THREAD_SELECTION'){$facts.selectionSchemaValid=$false}
    if($line -cmatch '\AMIXEL_NAMESPACE_TRACE_FAIL_([A-Z0-9_]+)\z' -and $allowed -ccontains $Matches[1]){
      $facts.failureTagCount=[Math]::Min(32,$facts.failureTagCount+1)
      [void]$categories.Add($Matches[1].ToLowerInvariant().Replace('_','-'))
    }
  }
  if($facts.selectionSchemaValid -and $facts.selectionMarkerCount -eq 1){$facts.selectionAvailable=$true;$facts.processMatched=$process;$facts.threadMatched=$thread}
  $facts.failureCategories=@($categories|Sort-Object)
  return [pscustomobject]$facts
}
function Get-TraceRowLexemes([string]$Row) {
  $Row=$Row.Trim()
  $facts=[ordered]@{lexemesAvailable=$false;length=[Math]::Min(4096,$Row.Length);lengthBounded=$Row.Length -le 4096;tokenCount=0;tokensBounded=$false;slotTokenKind='unavailable';statusTokenKind='unavailable';addressShape='unavailable';addressTokenCount=0;typeSizeShape='unavailable';passPairPresent=$false;postPassAssociationShape='unavailable';colonShape='unavailable';promptPresent=$false;quotedCommandPresent=$false;controlCharacterPresent=$false;escapePresent=$false;angleBracketPresent=$false;blWordPresent=$false;slashLWordPresent=$false;beginWordPresent=$false;endWordPresent=$false;echoWordPresent=$false}
  if($Row.Length -gt 4096){return [pscustomobject]$facts}
  $tokens=@($Row -split '\s+'|Where-Object {$_ -ne ''})
  $facts.tokenCount=[Math]::Min(32,$tokens.Count);$facts.tokensBounded=$tokens.Count -le 32
  if(-not $facts.tokensBounded){return [pscustomobject]$facts}
  $facts.lexemesAvailable=$true;$facts.slotTokenKind='missing';$facts.statusTokenKind='missing';$facts.addressShape='none';$facts.typeSizeShape='absent';$facts.colonShape='absent';$facts.postPassAssociationShape='absent'
  if($tokens.Count -gt 0){$facts.slotTokenKind=if($tokens[0] -cmatch '\A[0-3]\z'){'expected-slot'}elseif($tokens[0] -cmatch '\A[0-9]+\z'){'numeric-other'}else{'other'}}
  if($tokens.Count -gt 1){$facts.statusTokenKind=switch -CaseSensitive ($tokens[1]){'e'{'enabled'}'d'{'disabled'}'u'{'unresolved'}default{'other'}}}
  $addressKinds=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  for($index=0;$index -lt $tokens.Count;$index++){
    $token=$tokens[$index]
    $address=if($token -cmatch '\A[0-9a-fA-F]{8}\z'){'hex8'}elseif($token -cmatch '\A[0-9a-fA-F]{16}\z'){'hex16'}elseif($token -cmatch '\A[0-9a-fA-F]{8}`[0-9a-fA-F]{8}\z'){'split16'}elseif($token -cmatch '\A\((?:[0-9a-fA-F]{8}|[0-9a-fA-F]{16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\)\z'){'parenthesized'}else{''}
    if($address){[void]$addressKinds.Add($address);$facts.addressTokenCount++}
    if($facts.typeSizeShape -ceq 'absent'){
      if($token -cmatch '\A[erwi][1248]\z'){$facts.typeSizeShape=if($token -ceq 'e1'){'packed-e1'}else{'packed-other'}}
      elseif($index+1 -lt $tokens.Count -and $token -cmatch '\A[erwi]\z' -and $tokens[$index+1] -cmatch '\A[1248]\z'){$facts.typeSizeShape=if($token -ceq 'e' -and $tokens[$index+1] -ceq '1'){'separate-e1'}else{'separate-other'}}
    }
    if(-not $facts.passPairPresent -and $index+1 -lt $tokens.Count -and $token -cmatch '\A[0-9a-fA-F]{1,4}\z' -and $tokens[$index+1] -cmatch '\A\([0-9a-fA-F]{1,4}\)\z'){
      $facts.passPairPresent=$true
      $association=@($tokens|Select-Object -Skip ($index+2) -First 3)
      $facts.postPassAssociationShape=if($association.Count -eq 0){'absent'}
        elseif($association[0] -cmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,8}\z'){'packed-pair'}
        elseif($association[0] -cmatch '\A[0-9]{1,10}:\*{3,4}\z'){'packed-wildcard'}
        elseif($association.Count -ge 3 -and $association[0] -cmatch '\A[0-9]{1,10}\z' -and $association[1] -ceq ':' -and $association[2] -cmatch '\A[0-9a-fA-F]{1,8}\z'){'spaced-colon-pair'}
        elseif($association.Count -ge 2 -and $association[0] -cmatch '\A[0-9]{1,10}:\z' -and $association[1] -cmatch '\A[0-9a-fA-F]{1,8}\z'){'index-colon-space'}
        elseif($association.Count -ge 2 -and $association[0] -cmatch '\A[0-9]{1,10}\z' -and $association[1] -cmatch '\A[0-9a-fA-F]{1,8}\z'){'index-whitespace'}
        elseif($association[0] -cmatch '\A[0-9]{1,10}:\z'){'index-colon-only'}
        elseif($association[0] -ceq ':'){'colon-only'}else{'other'}
    }
    if($token -cmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,8}\z'){$facts.colonShape='index-pair'}
    elseif($token -cmatch '\A[0-9]{1,10}:\*{3,4}\z'){$facts.colonShape='index-wildcard'}
    elseif($token.Contains(':') -and $facts.colonShape -ceq 'absent'){$facts.colonShape='other-colon'}
  }
  if($addressKinds.Count -eq 1){$facts.addressShape=@($addressKinds)[0]}elseif($addressKinds.Count -gt 1){$facts.addressShape='multiple'}
  $facts.promptPresent=[bool]($Row -cmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,8}>')
  $facts.quotedCommandPresent=$Row.Contains('"')
  $facts.controlCharacterPresent=[bool]($Row -cmatch '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]');$facts.escapePresent=$Row.Contains([char]27);$facts.angleBracketPresent=$Row.Contains('<') -or $Row.Contains('>')
  $facts.blWordPresent=$tokens -ccontains 'bl';$facts.slashLWordPresent=$tokens -ccontains '/L'
  $facts.beginWordPresent=$Row.Contains('MIXEL_NAMESPACE_HW_LIST_BEGIN');$facts.endWordPresent=$Row.Contains('MIXEL_NAMESPACE_HW_LIST_END');$facts.echoWordPresent=$tokens -ccontains '.echo'
  return [pscustomobject]$facts
}
function Get-TraceAssociationTokenFacts([string]$Row,[bool]$FromRowStart=$false) {
  $facts=[ordered]@{available=$false;observationKind=if($FromRowStart){'row'}else{'post-pass'};rowBounded=$false;tokensBounded=$false;passPairPresent=$false;remainingTokenCount=0;observedCount=0;observationTruncated=$false;shapes=@();lengths=@();punctuationMasks=@();asciiDigitPresent=@();asciiLetterPresent=@()}
  if($Row.Length -gt 4096){return [pscustomobject]$facts};$facts.rowBounded=$true
  $tokens=@($Row.Trim() -split '\s+'|Where-Object {$_ -ne ''});$facts.tokensBounded=$tokens.Count -le 32
  if(-not $facts.tokensBounded){return [pscustomobject]$facts}
  $start=if($FromRowStart){0}else{-1}
  if(-not $FromRowStart){for($index=0;$index+1 -lt $tokens.Count;$index++){
    if($tokens[$index] -cmatch '\A[0-9a-fA-F]{1,4}\z' -and $tokens[$index+1] -cmatch '\A\([0-9a-fA-F]{1,4}\)\z'){$start=$index+2;$facts.passPairPresent=$true;break}
  }
  }
  if($start -lt 0){return [pscustomobject]$facts}
  $post=@($tokens|Select-Object -Skip $start);$facts.remainingTokenCount=$post.Count;$facts.observationTruncated=$post.Count -gt 8
  $observed=@($post|Select-Object -First 8)
  # A token bound rejects the entire observation; no truncated token is parsed.
  if(@($observed|Where-Object {$_.Length -gt 256}).Count){return [pscustomobject]$facts}
  $punctuation=@(':','~','[',']','<','>','"',"'",'.','*','=','/','\','!','(',')')
  foreach($token in $observed){
    $shape=if($token -cmatch '\A[0-9]{1,10}\z'){'decimal'}elseif($token -cmatch '\A[0-9a-fA-F]{1,16}\z'){'hex'}
      elseif($token -cmatch '\A[0-9]{1,10}:\z'){'decimal-colon'}elseif($token -cmatch '\A:[0-9a-fA-F]{1,16}\z'){'colon-hex'}elseif($token -ceq ':'){'colon-only'}
      elseif($token -cmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,16}\z'){'packed-pair'}elseif($token -cmatch '\A\*{1,4}\z'){'wildcard'}elseif($token -cmatch '\A[A-Za-z_][A-Za-z_0-9-]*\z'){'word'}else{'other'}
    $mask=0;for($bit=0;$bit -lt $punctuation.Count;$bit++){if($token.Contains($punctuation[$bit])){$mask=$mask -bor (1 -shl $bit)}}
    $facts.shapes+=,$shape;$facts.lengths+=,$token.Length;$facts.punctuationMasks+=,$mask
    $facts.asciiDigitPresent+=,[bool]($token -cmatch '[0-9]');$facts.asciiLetterPresent+=,[bool]($token -cmatch '[A-Za-z]')
  }
  $facts.available=$true;$facts.observedCount=$observed.Count
  return [pscustomobject]$facts
}
function Get-TraceEngineMetadataFacts([string]$Text,[uint32]$ChildPid=0,[uint32]$Thread=0,[string]$ExpectedControlCommand='') {
  $facts=[ordered]@{textBounded=$false;threadBlockComplete=$false;threadRowCount=0;threadMappingAvailable=$false;retainedProcessMatched=$false;retainedThreadMatched=$false;threadLexemeRows=@();commandBlockComplete=$false;commandRowCount=0;commandRowsBounded=$false;commandSchemaAvailable=$false;commandSlotMask=0;commandExecuteByteMask=0;commandCallbackComparisonAvailable=$false;commandCallbackMatchedMask=0;callbackTextOccurrenceMask=0;callbackTextOccurrenceCount=0;callbackTextOccurrencesBounded=$true;commandOsScopeLiteralMask=0;commandOsScopeMatchedMask=0;commandIndexScopeLiteralMask=0;commandUnknownRowCount=0;commandEchoRowCount=0;commandLexemeRows=@();associationBlockComplete=$false;associationRowsBounded=$false;associationRows=@()}
  if([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576){return [pscustomobject]$facts};$facts.textBounded=$true
  $end=$Text.LastIndexOf("`n");$complete=if($end -ge 0){$Text.Substring(0,$end+1)}else{''}
  $lines=@($complete -split '\r?\n'|ForEach-Object {$_.Trim()})
  $blocks=@{}
  foreach($kind in @('THREAD_STATUS','BREAKPOINT_COMMANDS','ASSOCIATION')){
    $prefix=if($kind -ceq 'ASSOCIATION'){'MIXEL_NAMESPACE_HW_LIST'}else{'MIXEL_CDB_'+$kind}
    $begin=-1;$finish=-1;$invalid=$false;$boundary=-1
    for($index=0;$index -lt $lines.Count;$index++){
      $line=$lines[$index]
      if($line -ceq ($prefix+'_BEGIN')){if($begin -ge 0 -or $boundary -ge 0){$invalid=$true};$begin=$index}
      elseif($line -ceq ($prefix+'_END')){if($begin -lt 0 -or $finish -ge 0 -or $boundary -ge 0){$invalid=$true};$finish=$index}
      elseif($line.StartsWith($prefix,[StringComparison]::Ordinal)){$invalid=$true}
      if($line -ceq 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1' -or $line -ceq 'MIXEL_NAMESPACE_TRACE_READY'){$boundary=$index}
    }
    $valid=-not $invalid -and $begin -ge 0 -and $finish -gt $begin
    $rows=@();if($valid -and $finish -gt $begin+1){$rows=@($lines[($begin+1)..($finish-1)])}
    $blocks[$kind]=[pscustomobject]@{valid=$valid;begin=$begin;finish=$finish;rows=$rows}
  }
  # Reject interleaved, reversed or overlapping fixed metadata blocks.
  if($blocks.THREAD_STATUS.valid -and $blocks.BREAKPOINT_COMMANDS.valid -and $blocks.THREAD_STATUS.finish -ge $blocks.BREAKPOINT_COMMANDS.begin){$blocks.THREAD_STATUS.valid=$false;$blocks.BREAKPOINT_COMMANDS.valid=$false}
  foreach($kind in @('THREAD_STATUS','BREAKPOINT_COMMANDS')){
    if($blocks[$kind].valid -and $blocks.ASSOCIATION.valid -and $blocks.ASSOCIATION.finish -ge $blocks[$kind].begin){$blocks[$kind].valid=$false}
  }
  $threadRows=@($blocks.THREAD_STATUS.rows);$facts.threadBlockComplete=$blocks.THREAD_STATUS.valid;$facts.threadRowCount=[Math]::Min(32,$threadRows.Count)
  if($facts.threadBlockComplete -and $threadRows.Count -le 32){$facts.threadLexemeRows=@($threadRows|ForEach-Object {Get-TraceAssociationTokenFacts $_ $true})}
  if($facts.threadBlockComplete -and $threadRows.Count -le 32 -and @($threadRows|Where-Object {$_.Length -gt 4096}).Count -eq 0){
    $current=@($threadRows|Where-Object {$_ -ne '' -and $_ -cnotmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,8}>\s+~\.\z'})
    if($current.Count -eq 1 -and $current[0] -cmatch '\A\.\s+([0-9]{1,10})\s+id:\s+([0-9a-fA-F]{1,8})\.([0-9a-fA-F]{1,8})\s+Suspend:\s+[0-9]{1,10}\s+Teb:?\s+(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{16}|[0-9a-fA-F]{8}`[0-9a-fA-F]{8})\s+(?:Unfrozen|Frozen)\z' -and [long]$Matches[1] -le 4294967295){
      $mappedPid=[Convert]::ToUInt32($Matches[2],16);$mappedTid=[Convert]::ToUInt32($Matches[3],16)
      if($mappedPid -gt 0 -and $mappedTid -gt 0 -and $ChildPid -gt 0 -and $Thread -gt 0){$facts.threadMappingAvailable=$true;$facts.retainedProcessMatched=$mappedPid -eq $ChildPid;$facts.retainedThreadMatched=$mappedTid -eq $Thread}
    }
  }
  $commandRows=@($blocks.BREAKPOINT_COMMANDS.rows);$facts.commandBlockComplete=$blocks.BREAKPOINT_COMMANDS.valid;$facts.commandRowCount=[Math]::Min(32,$commandRows.Count);$facts.commandRowsBounded=$commandRows.Count -le 32
  $expectedValid=$ExpectedControlCommand.Length -le 4096 -and $ExpectedControlCommand -cmatch '\A"\$\$><[A-Z]:\\[A-Za-z0-9_\\.-]+\\hwcontrol\.txt"\z' -and -not $ExpectedControlCommand.Contains('..')
  $facts.commandCallbackComparisonAvailable=$facts.commandBlockComplete -and $expectedValid -and $facts.commandRowsBounded -and @($commandRows|Where-Object {$_.Length -gt 4096}).Count -eq 0
  $callbacks=@();if($expectedValid){$folder=$ExpectedControlCommand.Substring(5,$ExpectedControlCommand.Length-6);$folder=$folder.Substring(0,$folder.Length-'hwcontrol.txt'.Length);$callbacks=@(('"r $t1 = 1; $$><'+$folder+'entry.txt"'),('"r $t1 = 2; $$><'+$folder+'entry.txt"'),('"$$><'+$folder+'winentry.txt"'),$ExpectedControlCommand)}
  if($facts.commandBlockComplete -and $facts.commandRowsBounded){
    $known=0;$duplicate=$false;$callbackTextOccurrences=0
    $facts.commandLexemeRows=@($commandRows|ForEach-Object {
      $lex=Get-TraceAssociationTokenFacts $_ $true
      $leading=if($_.Length -gt 4096){'unavailable'}elseif($_ -cmatch '\Aba[0-3](?:\s|\z)'){'ba'}elseif($_ -cmatch '\Abp[0-3](?:\s|\z)'){'bp'}elseif($_ -cmatch '\Abu[0-3](?:\s|\z)'){'bu'}elseif($_ -cmatch '\A~~?\[' -or $_ -cmatch '\A~[0-9]'){'thread-prefix'}elseif($_ -cmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,8}>'){'prompt'}else{'other'}
      $lex|Add-Member -NotePropertyName leadingCommandKind -NotePropertyValue $leading;$lex
    })
    foreach($row in $commandRows){
      if($row -ceq ''){continue}
      if($facts.commandCallbackComparisonAvailable){for($callbackIndex=0;$callbackIndex -lt $callbacks.Count;$callbackIndex++){
        $occurrences=[regex]::Matches($row,[regex]::Escape($callbacks[$callbackIndex])).Count
        if($occurrences){$facts.callbackTextOccurrenceMask=$facts.callbackTextOccurrenceMask -bor (1 -shl $callbackIndex);$callbackTextOccurrences+=$occurrences}
      }}
      if($row.Length -le 4096 -and $row -cmatch '\A[0-9]{1,10}:[0-9a-fA-F]{1,8}>\s+\.bpcmds\z'){$facts.commandEchoRowCount++;continue}
      if($row.Length -gt 4096 -or $row -cnotmatch '\A(?:(~~\[(?:0x)?[0-9a-fA-F]{1,8}\]|~[0-9]{1,10})\s*)?ba([0-3])\s+(?:e\s+1|e1)\s+(?:/1\s+)?[^\s"]{1,256}\s+("[^\r\n]{1,4090}")\z'){$facts.commandUnknownRowCount++;continue}
      $scope=$Matches[1];$slotIndex=[int]$Matches[2];$slot=1 -shl $slotIndex;$callback=$Matches[3]
      if($scope -cmatch '\A~([0-9]{1,10})\z' -and [long]$Matches[1] -gt 4294967295){$facts.commandUnknownRowCount++;continue};$known++
      if(($facts.commandSlotMask -band $slot) -ne 0){$duplicate=$true};$facts.commandSlotMask=$facts.commandSlotMask -bor $slot;$facts.commandExecuteByteMask=$facts.commandExecuteByteMask -bor $slot
      if($facts.commandCallbackComparisonAvailable -and $callback -ceq $callbacks[$slotIndex]){$facts.commandCallbackMatchedMask=$facts.commandCallbackMatchedMask -bor $slot}
      if($scope -cmatch '\A~~\[(?:0x)?([0-9a-fA-F]{1,8})\]\z'){$facts.commandOsScopeLiteralMask=$facts.commandOsScopeLiteralMask -bor $slot;if($Thread -gt 0 -and [Convert]::ToUInt32($Matches[1],16) -eq $Thread){$facts.commandOsScopeMatchedMask=$facts.commandOsScopeMatchedMask -bor $slot}}
      elseif($scope -cmatch '\A~([0-9]{1,10})\z' -and [long]$Matches[1] -le 4294967295){$facts.commandIndexScopeLiteralMask=$facts.commandIndexScopeLiteralMask -bor $slot}
    }
    $facts.callbackTextOccurrenceCount=[Math]::Min(32,$callbackTextOccurrences);$facts.callbackTextOccurrencesBounded=$callbackTextOccurrences -le 32
    $facts.commandSchemaAvailable=$known -eq 4 -and -not $duplicate -and $facts.commandUnknownRowCount -eq 0 -and $facts.commandSlotMask -eq 15
  }
  $facts.associationBlockComplete=$blocks.ASSOCIATION.valid;$facts.associationRowsBounded=$blocks.ASSOCIATION.rows.Count -le 32
  if($facts.associationBlockComplete -and $facts.associationRowsBounded){$facts.associationRows=@($blocks.ASSOCIATION.rows|ForEach-Object {Get-TraceAssociationTokenFacts $_})}
  return [pscustomobject]$facts
}
function Get-TraceHardwareFacts([string]$Text,[string]$ExpectedControlCommand='') {
  $facts=[ordered]@{engineBlockCount=0;engineBlockComplete=$false;engineRowCount=0;engineSchemaValid=$false;engineIndexMetadataAvailable=$false;engineOsThreadCorrelationAvailable=$false;engineOsThreadMatched=$false;rowKindsAvailable=$false;rowKindsBounded=$false;rowKindsCount=0;knownRowCount=0;commandEchoRowCount=0;blankRowCount=0;oversizedRowCount=0;unknownRowCount=0;duplicateSlotCount=0;lexemeRows=@();controlCommandComparisonAvailable=$false;controlCommandMatchCount=0;slotMask=0;enabledMask=0;resolvedMask=0;executeByteMask=0;passOneMask=0;threadSpecificMask=0;contextMarkerCount=0;contextSchemaValid=$true;contextAvailable=$false;processMatched=$false;threadMatched=$false;addressPresentMask=0;localEnableMask=0;globalEnableMask=0;executionByteMask=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576){$facts.contextSchemaValid=$false;return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");$complete=if($end -ge 0){$Text.Substring(0,$end+1)}else{''}
  $lines=@($complete -split '\r?\n'|ForEach-Object {$_.Trim()})
  $begin=-1;$finish=-1;$contextIndex=-1;$blockInvalid=$false;$contextValues=@()
  for($index=0;$index -lt $lines.Count;$index++){
    $line=$lines[$index]
    if($line -ceq 'MIXEL_NAMESPACE_HW_LIST_BEGIN'){$facts.engineBlockCount=[Math]::Min(32,$facts.engineBlockCount+1);if($begin -ge 0){$blockInvalid=$true};$begin=$index}
    elseif($line -ceq 'MIXEL_NAMESPACE_HW_LIST_END'){if($begin -lt 0 -or $finish -ge 0){$blockInvalid=$true};$finish=$index}
    elseif($line -cmatch '\AMIXEL_NAMESPACE_HW_LIST'){$blockInvalid=$true}
    if($line -cmatch '\AMIXEL_NAMESPACE_HW_CONTEXT processMatched=([01]) threadMatched=([01]) addressPresentMask=([0-9]|1[0-5]) localEnableMask=([0-9]|1[0-5]) globalEnableMask=([0-9]|1[0-5]) executionByteMask=([0-9]|1[0-5])\z'){
      $facts.contextMarkerCount=[Math]::Min(32,$facts.contextMarkerCount+1)
      if($contextIndex -ge 0 -or $finish -lt 0 -or $blockInvalid){$facts.contextSchemaValid=$false}
      $contextIndex=$index;$contextValues=@(1..6|ForEach-Object {[int]$Matches[$_]})
    }elseif($line -cmatch '\AMIXEL_NAMESPACE_HW_CONTEXT'){$facts.contextSchemaValid=$false}
    if(($line -ceq 'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1' -or $line -ceq 'MIXEL_NAMESPACE_TRACE_READY') -and $contextIndex -lt 0){$facts.contextSchemaValid=$false}
  }
  $facts.engineBlockComplete=-not $blockInvalid -and $facts.engineBlockCount -eq 1 -and $finish -gt $begin
  if($facts.contextSchemaValid -and $facts.contextMarkerCount -eq 1 -and $facts.engineBlockComplete){
    $facts.contextAvailable=$true;$facts.processMatched=[bool]$contextValues[0];$facts.threadMatched=[bool]$contextValues[1]
    $facts.addressPresentMask=$contextValues[2];$facts.localEnableMask=$contextValues[3];$facts.globalEnableMask=$contextValues[4];$facts.executionByteMask=$contextValues[5]
  }
  if($facts.engineBlockComplete){
    $blockRows=@($lines[($begin+1)..($finish-1)])
    # Empty bl output is unavailable, not a four-slot engine configuration.
    if($finish -eq $begin+1){$blockRows=@()}
    $facts.rowKindsAvailable=$true;$facts.rowKindsBounded=$blockRows.Count -le 32;$facts.rowKindsCount=[Math]::Min(32,$blockRows.Count)
    $rowSlots=0
    $facts.controlCommandComparisonAvailable=$ExpectedControlCommand.Length -le 4096 -and $ExpectedControlCommand -cmatch '\A"\$\$><[A-Z]:\\[A-Za-z0-9_\\.-]+\\hwcontrol\.txt"\z' -and -not $ExpectedControlCommand.Contains('..')
    if($facts.controlCommandComparisonAvailable){
      $facts.controlCommandMatchCount=[Math]::Min(32,@($blockRows|Where-Object { $_.Length -le 4096 -and $_ -ceq $ExpectedControlCommand }).Count)
    }
    $facts.lexemeRows=@($blockRows|Select-Object -First 32|ForEach-Object {Get-TraceRowLexemes $_})
    foreach($row in @($blockRows|Select-Object -First 32)){
      if($row -ceq ''){$facts.blankRowCount++;continue}
      if($row.Length -gt 4096){$facts.oversizedRowCount++;continue}
      if($row -cmatch '\A([0-9]{1,10}):[0-9a-fA-F]{1,8}>\s+bl /L 0 1 2 3\z' -and [long]$Matches[1] -le 4294967295){$facts.commandEchoRowCount++;continue}
      if($row -cmatch '\A([0-3])\s+([ed])\s+(u\s+)?([0-9a-fA-F]{8}(?:`[0-9a-fA-F]{8})?|[0-9a-fA-F]{16})\s+([erwi])\s+([1248])\s+([0-9a-fA-F]{1,4})\s+\(([0-9a-fA-F]{1,4})\)\s+([0-9]{1,10}):(\*{3,4}|[0-9a-fA-F]{1,8})(?:\s+[^\r\n]*)?\z' -and
          [Convert]::ToUInt64($Matches[4].Replace('`',''),16) -ne 0 -and [long]$Matches[9] -le 4294967295){
        $facts.knownRowCount++;$slot=1 -shl [int]$Matches[1]
        if(($rowSlots -band $slot) -ne 0){$facts.duplicateSlotCount++};$rowSlots=$rowSlots -bor $slot
      }else{$facts.unknownRowCount++}
    }
    $rows=@($blockRows|Where-Object {$_ -ne ''})
    $facts.engineRowCount=[Math]::Min(32,$rows.Count)
    $masks=[ordered]@{slotMask=0;enabledMask=0;resolvedMask=0;executeByteMask=0;passOneMask=0;threadSpecificMask=0};$valid=$rows.Count -eq 4
    foreach($row in $rows){
      if($row.Length -gt 4096 -or $row -cnotmatch '\A([0-3])\s+([ed])\s+(u\s+)?([0-9a-fA-F]{8}(?:`[0-9a-fA-F]{8})?|[0-9a-fA-F]{16})\s+([erwi])\s+([1248])\s+([0-9a-fA-F]{1,4})\s+\(([0-9a-fA-F]{1,4})\)\s+([0-9]{1,10}):(\*{3,4}|[0-9a-fA-F]{1,8})(?:\s+[^\r\n]*)?\z'){$valid=$false;continue}
      $slot=1 -shl [int]$Matches[1]
      if(($masks.slotMask -band $slot) -ne 0 -or [Convert]::ToUInt64($Matches[4].Replace('`',''),16) -eq 0 -or [long]$Matches[9] -gt 4294967295){$valid=$false;continue}
      $masks.slotMask=$masks.slotMask -bor $slot
      if($Matches[2] -ceq 'e'){$masks.enabledMask=$masks.enabledMask -bor $slot}
      if(-not $Matches[3]){$masks.resolvedMask=$masks.resolvedMask -bor $slot}
      if($Matches[5] -ceq 'e' -and $Matches[6] -ceq '1'){$masks.executeByteMask=$masks.executeByteMask -bor $slot}
      if([Convert]::ToInt32($Matches[7],16) -eq 1 -and [Convert]::ToInt32($Matches[8],16) -eq 1){$masks.passOneMask=$masks.passOneMask -bor $slot}
      if($Matches[10] -notmatch '\*'){$masks.threadSpecificMask=$masks.threadSpecificMask -bor $slot}
    }
    if($valid -and $masks.slotMask -eq 15){$facts.engineSchemaValid=$true;$facts.engineIndexMetadataAvailable=$true;foreach($key in $masks.Keys){$facts[$key]=$masks[$key]}}
  }
  return [pscustomobject]$facts
}
function Get-TraceContinuationFacts([string]$Text) {
  $facts=[ordered]@{acknowledgementCount=0;acknowledgementSchemaValid=$true;acknowledgementObserved=$false}
  if([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576){$facts.acknowledgementSchemaValid=$false;return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");$complete=if($end -ge 0){$Text.Substring(0,$end+1)}else{''}
  $ready=$false;$afterEntry=$false
  foreach($line in @($complete -split '\r?\n'|ForEach-Object {$_.Trim()})){
    if($line -ceq 'MIXEL_NAMESPACE_TRACE_READY'){if($ready){$facts.acknowledgementSchemaValid=$false};$ready=$true}
    if($line -cmatch '\AMIXEL_NAMESPACE_(?:HWCONTROL|WIN32_ENTRY|PREWINDOW_ENTRY|NATIVE|TRACE_WINDOW|TRACE_END)(?:\s|\z)'){$afterEntry=$true}
    if($line -ceq 'MIXEL_NAMESPACE_CONTINUATION_ACK'){
      $facts.acknowledgementCount=[Math]::Min(32,$facts.acknowledgementCount+1)
      if(-not $ready -or $afterEntry -or $facts.acknowledgementCount -ne 1){$facts.acknowledgementSchemaValid=$false}
    }elseif($line -cmatch '\AMIXEL_NAMESPACE_CONTINUATION_ACK'){$facts.acknowledgementSchemaValid=$false}
  }
  $facts.acknowledgementObserved=$facts.acknowledgementSchemaValid -and $facts.acknowledgementCount -eq 1
  return [pscustomobject]$facts
}
function Get-TraceMarkerFormattingFacts([string]$Text) {
  $facts=[ordered]@{available=$false;markers=@()}
  if([Text.Encoding]::UTF8.GetByteCount($Text) -gt 1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");$complete=if($end -ge 0){$Text.Substring(0,$end+1)}else{''};$tail=$Text.Substring($end+1)
  $lines=@($complete -split '\r?\n'|ForEach-Object {$_.Trim()})
  $tokens=[ordered]@{ack='MIXEL_NAMESPACE_CONTINUATION_ACK';'hardware-control'='MIXEL_NAMESPACE_HWCONTROL';'win32-entry'='MIXEL_NAMESPACE_WIN32_ENTRY';'prewindow-entry'='MIXEL_NAMESPACE_PREWINDOW_ENTRY';native='MIXEL_NAMESPACE_NATIVE';window='MIXEL_NAMESPACE_TRACE_WINDOW';end='MIXEL_NAMESPACE_TRACE_END'}
  $items=[Collections.Generic.List[object]]::new()
  foreach($key in $tokens.Keys){
    $literal=[regex]::Escape($tokens[$key]);$occurrence='(?<!\w)'+$literal+'(?=\s|\z|[";<>]|\\[rn])'
    $allCount=[regex]::Matches($Text,$occurrence).Count;$tailCount=[regex]::Matches($tail,$occurrence).Count
    $matching=@($lines|Where-Object {$_ -cmatch $occurrence})
    $item=[ordered]@{kind=$key;bounded=$allCount -le 32 -and $matching.Count -le 32;tokenOccurrenceCount=[Math]::Min(32,$allCount);completeLineCount=[Math]::Min(32,$matching.Count);tailOccurrenceCount=[Math]::Min(32,$tailCount);standalonePrefixLineCount=0;promptPrefixLineCount=0;commandEchoLineCount=0;otherLineCount=0;oversizedLineCount=0}
    foreach($line in @($matching|Select-Object -First 32)){
      if($line.Length -gt 4096){$item.oversizedLineCount++;continue}
      $prompt=$false;$payload=$line
      if($line -cmatch '\A([0-9]{1,10}):[0-9a-fA-F]{1,8}>\s*(.*)\z' -and [long]$Matches[1] -le 4294967295){$prompt=$true;$payload=$Matches[2]}
      if($key -ceq 'ack'){
        if($line -ceq $tokens[$key]){$item.standalonePrefixLineCount++}
        elseif($prompt -and $payload -ceq $tokens[$key]){$item.promptPrefixLineCount++}
        elseif($payload -cmatch '\A\.echo MIXEL_NAMESPACE_CONTINUATION_ACK; g\z'){$item.commandEchoLineCount++}
        else{$item.otherLineCount++}
      }else{
        if($line -cmatch ('\A'+$literal+'(?=\s|\z)')){$item.standalonePrefixLineCount++}
        elseif($prompt -and $payload -cmatch ('\A'+$literal+'(?=\s|\z)')){$item.promptPrefixLineCount++}
        elseif($payload -cmatch ('\A(?:\.echo '+$literal+'(?=\s|\z|;)|\.printf "'+$literal+'(?=\s|"|\\[rn]))')){$item.commandEchoLineCount++}
        else{$item.otherLineCount++}
      }
    }
    $items.Add([pscustomobject]$item)
  }
  $facts.available=$true;$facts.markers=$items.ToArray()
  return [pscustomobject]$facts
}
function Get-TraceEngineQueryFacts([string]$Text) {
  $keys=@('argumentsValid','contextAvailable','processMatched','threadMatched','mappingAvailable','currentEngineMatched','moduleAvailable','presentMask','idMatchedMask','flagsAvailableMask','enabledMask','deferredMask','oneShotMask','adderOnlyMask','goOnlyMask','unknownFlagsMask','typeAvailableMask','dataAmd64Mask','dataAvailableMask','executeByteMask','passAvailableMask','passOneMask','currentPassAvailableMask','currentPassOneMask','matchAvailableMask','specificThreadMask','anyThreadMask','expectedEngineMatchedMask','commandAvailableMask','callbackMatchedMask','offsetAvailableMask','expectedOffsetAvailableMask','offsetMatchedMask')
  $facts=[ordered]@{recordCount=0;schemaAvailable=$false}
  foreach($key in $keys){$facts[$key]=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()})
  $records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_QUERY(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt4096){return [pscustomobject]$facts}
  $position=[Array]::IndexOf($lines,$records[0]);$binding=[Array]::IndexOf($lines,'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1')
  if($binding-ge0-and$position-ge$binding){return [pscustomobject]$facts}
  if($records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_QUERY (\{[^\r\n]+\})\z'){return [pscustomobject]$facts}
  $json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    for($index=0;$index-lt$keys.Count;$index++){
      $key=$keys[$index];$number=$value.PSObject.Properties[$key].Value;$max=if($index-lt7){1}else{15}
      if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt$max){return [pscustomobject]$facts}
      $canonical[$key]=[int]$number
    }
    if(($canonical|ConvertTo-Json -Compress)-cne$json){return [pscustomobject]$facts}
    if(($value.idMatchedMask-band(-bnot$value.presentMask))-ne0){return [pscustomobject]$facts}
    foreach($key in @('flagsAvailableMask','typeAvailableMask','dataAvailableMask','passAvailableMask','currentPassAvailableMask','matchAvailableMask','commandAvailableMask','offsetAvailableMask')){
      if(($value.PSObject.Properties[$key].Value-band(-bnot$value.idMatchedMask))-ne0){return [pscustomobject]$facts}
    }
    $derived=@{enabledMask='flagsAvailableMask';deferredMask='flagsAvailableMask';oneShotMask='flagsAvailableMask';adderOnlyMask='flagsAvailableMask';goOnlyMask='flagsAvailableMask';unknownFlagsMask='flagsAvailableMask';dataAmd64Mask='typeAvailableMask';executeByteMask='dataAvailableMask';passOneMask='passAvailableMask';currentPassOneMask='currentPassAvailableMask';specificThreadMask='matchAvailableMask';anyThreadMask='matchAvailableMask';expectedEngineMatchedMask='specificThreadMask';callbackMatchedMask='commandAvailableMask';offsetMatchedMask='offsetAvailableMask'}
    foreach($key in $derived.Keys){if(($value.PSObject.Properties[$key].Value-band(-bnot$value.PSObject.Properties[$derived[$key]].Value))-ne0){return [pscustomobject]$facts}}
    if(($value.specificThreadMask-band$value.anyThreadMask)-ne0-or($value.specificThreadMask-bor$value.anyThreadMask)-ne$value.matchAvailableMask-or($value.offsetMatchedMask-band(-bnot$value.expectedOffsetAvailableMask))-ne0){return [pscustomobject]$facts}
    if(-not$value.argumentsValid-and(@($keys|Select-Object -Skip 1|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    if(-not$value.contextAvailable-and($value.processMatched-or$value.threadMatched)){return [pscustomobject]$facts}
    if(($value.mappingAvailable-or$value.currentEngineMatched)-and-not($value.contextAvailable-and$value.processMatched-and$value.threadMatched)){return [pscustomobject]$facts}
    if($value.currentEngineMatched-and-not$value.mappingAvailable){return [pscustomobject]$facts}
    if((-not$value.mappingAvailable-or-not$value.currentEngineMatched)-and(@($keys|Select-Object -Skip 7|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0-or$value.moduleAvailable-ne0)){return [pscustomobject]$facts}
    if([bool]($value.expectedOffsetAvailableMask-band8)-ne[bool]$value.moduleAvailable){return [pscustomobject]$facts}
    foreach($key in $keys){$facts[$key]=$canonical[$key]}
    $facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function Get-TraceEngineShapeFacts([string]$Text) {
  $keys=@('commandAvailableMask','commandLength0','commandLength1','commandLength2','commandLength3','outerQuoteMask','edgeWhitespaceMask','controlCharacterMask','quotedExpectedMask','trimmedExpectedMask','expectedBodyOccurrenceMask','expectedScriptOccurrenceMask','backslashDeletedExpectedMask','moduleEnumerationAvailable','moduleCount','scannedCount','moduleNamesSuccessCount','moduleNamesBoundedCount','moduleExactPathCount','moduleCasePathCount','moduleBasenameOnlyCount','moduleScanCompleted')
  $facts=[ordered]@{recordCount=0;schemaAvailable=$false};foreach($key in $keys){$facts[$key]=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()});$records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_SHAPES(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt4096){return [pscustomobject]$facts}
  $position=[Array]::IndexOf($lines,$records[0]);$binding=[Array]::IndexOf($lines,'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1');$queryPosition=-1
  for($index=0;$index-lt$lines.Count;$index++){if($lines[$index]-cmatch'\AMIXEL_CDB_ENGINE_QUERY '){$queryPosition=$index;break}}
  $query=Get-TraceEngineQueryFacts $Text
  if(-not$query.schemaAvailable-or$queryPosition-lt0-or$position-le$queryPosition-or($binding-ge0-and$position-ge$binding)){return [pscustomobject]$facts}
  if($records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_SHAPES (\{[^\r\n]+\})\z'){return [pscustomobject]$facts};$json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    foreach($key in $keys){
      $number=$value.PSObject.Properties[$key].Value
      $max=if($key-in@('moduleEnumerationAvailable','moduleScanCompleted')){1}elseif($key.StartsWith('commandLength')){4095}elseif($key.EndsWith('Mask')){15}else{128}
      if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt$max){return [pscustomobject]$facts};$canonical[$key]=[int]$number
    }
    if(($canonical|ConvertTo-Json -Compress)-cne$json-or$value.commandAvailableMask-ne$query.commandAvailableMask){return [pscustomobject]$facts}
    foreach($key in @('outerQuoteMask','edgeWhitespaceMask','controlCharacterMask','quotedExpectedMask','trimmedExpectedMask','expectedBodyOccurrenceMask','expectedScriptOccurrenceMask','backslashDeletedExpectedMask')){
      if(($value.PSObject.Properties[$key].Value-band(-bnot$value.commandAvailableMask))-ne0){return [pscustomobject]$facts}
    }
    for($slot=0;$slot-lt4;$slot++){
      $bit=1-shl$slot;$length=$value.PSObject.Properties['commandLength'+$slot].Value
      if(($value.commandAvailableMask-band$bit)-eq0-and$length-ne0){return [pscustomobject]$facts}
      if(($value.outerQuoteMask-band$bit)-ne0-and$length-lt2){return [pscustomobject]$facts}
      if((($value.edgeWhitespaceMask-bor$value.controlCharacterMask-bor$value.trimmedExpectedMask-bor$value.expectedBodyOccurrenceMask-bor$value.expectedScriptOccurrenceMask-bor$value.backslashDeletedExpectedMask)-band$bit)-ne0-and$length-eq0){return [pscustomobject]$facts}
    }
    if(($value.quotedExpectedMask-band(-bnot$value.outerQuoteMask))-ne0-or(($value.quotedExpectedMask-bor$value.trimmedExpectedMask-bor$query.callbackMatchedMask)-band(-bnot($value.expectedBodyOccurrenceMask-band$value.expectedScriptOccurrenceMask)))-ne0-or($query.callbackMatchedMask-band(-bnot$value.trimmedExpectedMask))-ne0){return [pscustomobject]$facts}
    if(($value.backslashDeletedExpectedMask-band($value.outerQuoteMask-bor$value.edgeWhitespaceMask-bor$value.controlCharacterMask-bor$value.quotedExpectedMask-bor$value.trimmedExpectedMask-bor$value.expectedBodyOccurrenceMask-bor$value.expectedScriptOccurrenceMask-bor$query.callbackMatchedMask))-ne0){return [pscustomobject]$facts}
    if($value.scannedCount-gt$value.moduleCount-or$value.moduleNamesSuccessCount-gt$value.scannedCount-or$value.moduleNamesBoundedCount-gt$value.moduleNamesSuccessCount-or$value.moduleExactPathCount-gt$value.moduleCasePathCount-or$value.moduleCasePathCount+$value.moduleBasenameOnlyCount-gt$value.moduleNamesBoundedCount){return [pscustomobject]$facts}
    if(-not$value.moduleEnumerationAvailable-and(@($keys|Where-Object{($_.StartsWith('module')-or$_-ceq'scannedCount')-and$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    if($value.moduleEnumerationAvailable-and$value.moduleCount-eq0){return [pscustomobject]$facts}
    if($value.moduleScanCompleted-and($value.scannedCount-ne$value.moduleCount-or$value.moduleNamesBoundedCount-ne$value.moduleCount)){return [pscustomobject]$facts}
    if($query.moduleAvailable-and(-not$value.moduleScanCompleted-or$value.moduleCasePathCount-ne1)){return [pscustomobject]$facts}
    foreach($key in $keys){$facts[$key]=$canonical[$key]};$facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function Get-TraceEngineLoadFacts([string]$Text) {
  $keys=@('interfaceAvailable','cacheAvailable','moduleCount','scannedCount','namesSuccessCount','namesBoundedCount','exactPathCount','casePathCount','basenameOnlyCount','scanCompleted','uniqueFullPathMatched','probeOffsetAvailable','probeOffsetMatched')
  $facts=[ordered]@{recordCount=0;schemaAvailable=$false};foreach($key in $keys){$facts[$key]=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()});$records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_LOAD(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt2048){return [pscustomobject]$facts}
  $position=[Array]::IndexOf($lines,$records[0]);$binding=[Array]::IndexOf($lines,'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1');$shapePosition=-1
  for($index=0;$index-lt$lines.Count;$index++){if($lines[$index]-cmatch'\AMIXEL_CDB_ENGINE_SHAPES '){$shapePosition=$index;break}}
  $query=Get-TraceEngineQueryFacts $Text;$shape=Get-TraceEngineShapeFacts $Text
  if(-not$query.schemaAvailable-or-not$shape.schemaAvailable-or$shapePosition-lt0-or$position-le$shapePosition-or($binding-ge0-and$position-ge$binding)-or$records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_LOAD (\{[^\r\n]+\})\z'){return [pscustomobject]$facts};$json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    foreach($key in $keys){
      $number=$value.PSObject.Properties[$key].Value;$max=if($key.EndsWith('Count')){128}else{1}
      if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt$max){return [pscustomobject]$facts};$canonical[$key]=[int]$number
    }
    if(($canonical|ConvertTo-Json -Compress)-cne$json){return [pscustomobject]$facts}
    if(-not$value.cacheAvailable-and(@($keys|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    if($value.cacheAvailable-and(-not$query.currentEngineMatched-or-not$shape.moduleScanCompleted-or$value.moduleCount-eq0-or$value.moduleCount-ne$shape.moduleCount)){return [pscustomobject]$facts}
    if(-not$value.interfaceAvailable-and(@($keys|Select-Object -Skip 3|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    if($value.scannedCount-gt$value.moduleCount-or$value.namesSuccessCount-gt$value.scannedCount-or$value.namesBoundedCount-gt$value.namesSuccessCount-or$value.exactPathCount-gt$value.casePathCount-or$value.casePathCount+$value.basenameOnlyCount-gt$value.namesBoundedCount){return [pscustomobject]$facts}
    if($value.scanCompleted-and($value.scannedCount-ne$value.moduleCount-or$value.namesBoundedCount-ne$value.moduleCount)){return [pscustomobject]$facts}
    if([bool]$value.uniqueFullPathMatched-ne[bool]($value.scanCompleted-and$value.casePathCount-eq1)){return [pscustomobject]$facts}
    if($value.probeOffsetAvailable-and-not$value.uniqueFullPathMatched){return [pscustomobject]$facts}
    if($value.probeOffsetMatched-and(-not$value.probeOffsetAvailable-or($query.offsetAvailableMask-band8)-eq0)){return [pscustomobject]$facts}
    foreach($key in $keys){$facts[$key]=$canonical[$key]};$facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function Get-TraceEngineProbeFacts([string]$Text) {
  $keys=@('cacheAvailable','actualProbeOffsetAvailable','interfaceAvailable','moduleLookupAvailable','cachedBaseMatched','cachedBaseUnique','loadedNameAvailable','loadedPathMatched','imageRangeMatched','probeRvaMatched')
  $facts=[ordered]@{recordCount=0;schemaAvailable=$false};foreach($key in $keys){$facts[$key]=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()});$records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_PROBE(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt2048){return [pscustomobject]$facts}
  $position=[Array]::IndexOf($lines,$records[0]);$binding=[Array]::IndexOf($lines,'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1');$loadPosition=-1
  for($index=0;$index-lt$lines.Count;$index++){if($lines[$index]-cmatch'\AMIXEL_CDB_ENGINE_LOAD '){$loadPosition=$index;break}}
  $query=Get-TraceEngineQueryFacts $Text;$shape=Get-TraceEngineShapeFacts $Text;$load=Get-TraceEngineLoadFacts $Text
  if(-not$query.schemaAvailable-or-not$shape.schemaAvailable-or-not$load.schemaAvailable-or$loadPosition-lt0-or$position-le$loadPosition-or($binding-ge0-and$position-ge$binding)-or$records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_PROBE (\{[^\r\n]+\})\z'){return [pscustomobject]$facts};$json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    foreach($key in $keys){$number=$value.PSObject.Properties[$key].Value;if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt1){return [pscustomobject]$facts};$canonical[$key]=[int]$number}
    if(($canonical|ConvertTo-Json -Compress)-cne$json){return [pscustomobject]$facts}
    if(-not$value.cacheAvailable-and(@($keys|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    if($value.cacheAvailable-and(-not$query.currentEngineMatched-or-not$shape.moduleScanCompleted-or$shape.moduleCount-eq0)){return [pscustomobject]$facts}
    if($value.actualProbeOffsetAvailable-and(-not$value.cacheAvailable-or($query.offsetAvailableMask-band8)-eq0)){return [pscustomobject]$facts}
    foreach($pair in @(@('moduleLookupAvailable','actualProbeOffsetAvailable'),@('cachedBaseMatched','moduleLookupAvailable'),@('cachedBaseUnique','cachedBaseMatched'),@('interfaceAvailable','cachedBaseUnique'),@('loadedNameAvailable','interfaceAvailable'),@('loadedPathMatched','loadedNameAvailable'),@('imageRangeMatched','loadedPathMatched'),@('probeRvaMatched','imageRangeMatched'))){if($value.PSObject.Properties[$pair[0]].Value-and-not$value.PSObject.Properties[$pair[1]].Value){return [pscustomobject]$facts}}
    foreach($key in $keys){$facts[$key]=$canonical[$key]};$facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function Get-TraceEngineExecutionFacts([string]$Text) {
  $keys=@('statusAvailable','statusKind');$facts=[ordered]@{recordCount=0;schemaAvailable=$false;statusAvailable=0;statusKind=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()});$records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_EXECUTION(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt2048){return [pscustomobject]$facts}
  $position=[Array]::IndexOf($lines,$records[0]);$binding=[Array]::IndexOf($lines,'MIXEL_NAMESPACE_BINDINGS directory=1 event=1 win32=1');$probePosition=-1
  for($index=0;$index-lt$lines.Count;$index++){if($lines[$index]-cmatch'\AMIXEL_CDB_ENGINE_PROBE '){$probePosition=$index;break}}
  $query=Get-TraceEngineQueryFacts $Text;$probe=Get-TraceEngineProbeFacts $Text
  if(-not$query.schemaAvailable-or-not$probe.schemaAvailable-or$probePosition-lt0-or$position-le$probePosition-or($binding-ge0-and$position-ge$binding)-or$records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_EXECUTION (\{[^\r\n]+\})\z'){return [pscustomobject]$facts};$json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    foreach($key in $keys){$number=$value.PSObject.Properties[$key].Value;$max=if($key-ceq'statusKind'){7}else{1};if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt$max){return [pscustomobject]$facts};$canonical[$key]=[int]$number}
    if(($canonical|ConvertTo-Json -Compress)-cne$json-or[bool]$value.statusAvailable-ne[bool]$value.statusKind-or($value.statusAvailable-and-not$query.currentEngineMatched)){return [pscustomobject]$facts}
    foreach($key in $keys){$facts[$key]=$canonical[$key]};$facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function Get-TraceEngineEventFacts([string]$Text) {
  $keys=@('argumentsValid','statusAvailable','engineStopped','contextAvailable','processMatched','mappingAvailable','eventAvailable','eventProcessMatched','eventThreadMatched','eventIsException','exceptionAvailable','exceptionKind','firstChance','locationAvailableMask','locationKind')
  $facts=[ordered]@{recordCount=0;schemaAvailable=$false};foreach($key in $keys){$facts[$key]=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()});$records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_EVENT(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt2048-or@($lines|Where-Object{$_-ceq'MIXEL_NAMESPACE_TRACE_READY'}).Count-ne1){return [pscustomobject]$facts}
  if([Array]::IndexOf($lines,$records[0])-le[Array]::IndexOf($lines,'MIXEL_NAMESPACE_TRACE_READY')-or$records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_EVENT (\{[^\r\n]+\})\z'){return [pscustomobject]$facts};$json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    foreach($key in $keys){
      $number=$value.PSObject.Properties[$key].Value;$max=if($key-ceq'exceptionKind'){4}elseif($key-ceq'locationKind'){3}elseif($key-ceq'locationAvailableMask'){7}else{1}
      if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt$max){return [pscustomobject]$facts};$canonical[$key]=[int]$number
    }
    if(($canonical|ConvertTo-Json -Compress)-cne$json){return [pscustomobject]$facts}
    $zeroAfter=@{argumentsValid=1;statusAvailable=2;engineStopped=3;processMatched=5;mappingAvailable=6;eventAvailable=7;eventProcessMatched=8;eventIsException=10;exceptionAvailable=11}
    foreach($parent in $zeroAfter.Keys){if($value.PSObject.Properties[$parent].Value-eq0){for($index=$zeroAfter[$parent];$index-lt$keys.Count;$index++){if($value.PSObject.Properties[$keys[$index]].Value-ne0){return [pscustomobject]$facts}}}}
    if(-not$value.contextAvailable-and$value.processMatched){return [pscustomobject]$facts}
    if($value.exceptionAvailable-and$value.exceptionKind-eq0){return [pscustomobject]$facts}
    if($value.locationKind-gt0-and($value.locationAvailableMask-band(1-shl($value.locationKind-1)))-eq0){return [pscustomobject]$facts}
    foreach($key in $keys){$facts[$key]=$canonical[$key]};$facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function Get-TraceEngineStackFacts([string]$Text) {
  $keys=@('currentThreadAvailable','currentEventThreadMatched','stackAvailable','frameCount','frame0InstructionMatched','symbolAvailableMask','displacementZeroMask','dbgBreakPointMask','remoteBreakinMask','loaderBreakMask')
  $facts=[ordered]@{recordCount=0;schemaAvailable=$false};foreach($key in $keys){$facts[$key]=0}
  if([Text.Encoding]::UTF8.GetByteCount($Text)-gt1048576){return [pscustomobject]$facts}
  $end=$Text.LastIndexOf("`n");if($end-lt0){return [pscustomobject]$facts}
  $lines=@($Text.Substring(0,$end+1)-split'\r?\n'|ForEach-Object{$_.Trim()});$records=@($lines|Where-Object{$_-cmatch'\AMIXEL_CDB_ENGINE_STACK(?:\s|\z)'})
  $facts.recordCount=[Math]::Min(32,$records.Count)
  if($records.Count-ne1-or$records[0].Length-gt2048){return [pscustomobject]$facts}
  $event=Get-TraceEngineEventFacts $Text;$eventPosition=-1
  for($index=0;$index-lt$lines.Count;$index++){if($lines[$index]-cmatch'\AMIXEL_CDB_ENGINE_EVENT '){$eventPosition=$index;break}}
  if(-not$event.schemaAvailable-or$eventPosition-lt0-or[Array]::IndexOf($lines,$records[0])-le$eventPosition-or$records[0]-cnotmatch'\AMIXEL_CDB_ENGINE_STACK (\{[^\r\n]+\})\z'){return [pscustomobject]$facts};$json=$Matches[1]
  try {
    $value=ConvertFrom-Json -InputObject $json -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne($keys-join',')){return [pscustomobject]$facts}
    $canonical=[ordered]@{}
    foreach($key in $keys){
      $number=$value.PSObject.Properties[$key].Value;$max=if($key-ceq'frameCount'){8}elseif($key.EndsWith('Mask')){255}else{1}
      if(($number-isnot[int]-and$number-isnot[long])-or$number-lt0-or$number-gt$max){return [pscustomobject]$facts};$canonical[$key]=[int]$number
    }
    if(($canonical|ConvertTo-Json -Compress)-cne$json){return [pscustomobject]$facts}
    if(-not$event.exceptionAvailable-and(@($keys|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    foreach($parent in @('currentThreadAvailable','currentEventThreadMatched','stackAvailable')){
      $position=[Array]::IndexOf($keys,$parent)
      if(-not$value.PSObject.Properties[$parent].Value-and(@($keys|Select-Object -Skip($position+1)|Where-Object{$value.PSObject.Properties[$_].Value-ne0}).Count-ne0)){return [pscustomobject]$facts}
    }
    if($value.stackAvailable-and$value.frameCount-eq0){return [pscustomobject]$facts}
    $frameMask=(1-shl$value.frameCount)-1
    if(($value.symbolAvailableMask-band(-bnot$frameMask))-ne0){return [pscustomobject]$facts}
    foreach($key in @('displacementZeroMask','dbgBreakPointMask','remoteBreakinMask','loaderBreakMask')){if(($value.PSObject.Properties[$key].Value-band(-bnot$value.symbolAvailableMask))-ne0){return [pscustomobject]$facts}}
    if(-not$value.frame0InstructionMatched-and($value.symbolAvailableMask-ne0)){return [pscustomobject]$facts}
    if(($value.dbgBreakPointMask-band$value.remoteBreakinMask)-ne0-or($value.dbgBreakPointMask-band$value.loaderBreakMask)-ne0-or($value.remoteBreakinMask-band$value.loaderBreakMask)-ne0){return [pscustomobject]$facts}
    foreach($key in $keys){$facts[$key]=$canonical[$key]};$facts.schemaAvailable=$true
  } catch { }
  return [pscustomobject]$facts
}
function New-TraceEventQueryCommand([uint32]$ChildPid,[uint32]$Thread,[uint32]$ProbeRva,[uint32]$ImageSize,[string]$Image,[string]$Folder) {
  if(-not$ChildPid-or-not$Thread-or-not$ProbeRva-or$ProbeRva-ge$ImageSize-or$ImageSize-gt268435456){throw 'Stopped event query requires the exact bounded source identities.'}
  foreach($path in @($Image,$Folder)){
    if($path.Length-gt512-or$path-cnotmatch'\A[A-Z]:\\[A-Za-z0-9_\\.-]+\z'-or$path.Contains('..')){throw 'Stopped event query path is not a private bounded literal.'}
  }
  if(-not$Image.EndsWith('.exe',[StringComparison]::Ordinal)){throw 'Stopped event query image is not the exact executable type.'}
  return '!mixelengine.inspect event '+$ChildPid+' '+$Thread+' '+$ProbeRva+' '+$ImageSize+' '+$Image+' '+$Folder
}
function Collect-TraceStoppedEvent($Live,[int]$ChildPid) {
  try {
    $retained=@($script:traceLiveProcesses|Where-Object{[object]::ReferenceEquals($_,$Live)})
    if($retained.Count-ne1-or$ChildPid-le0-or$Live.childPid-ne$ChildPid-or$Live.process.StartInfo.FileName-cne$script:cdb-or$Live.closed-or$Live.disposed-or$Live.process.HasExited-or-not$Live.input-or-not$Live.continuationQueued-or$Live.eventQueryAttempted-or$Live.eventQueryWritten-or$Live.eventQueryQueued-or[MixelOrdinaryTokenFixture]::StartedStatus($ChildPid)-cne'still-running'-or-not[MixelOrdinaryTokenFixture]::ActualOwnedDebuggerAttached($ChildPid)){return}
    $text=[MixelNamespaceTraceIO]::ReadBounded($Live.outPath,1048576);$stop=Get-TraceCdbStopFacts $text
    # A prompt only schedules this read-only query; the getter itself requires
    # DEBUG_STATUS_BREAK before interpreting an event from the owned target.
    if(-not$stop.postReadyEvidenceAvailable-or-not$stop.postReadyPromptPresent-or$stop.postReadyFirstChanceBreakpointCount-ne1-or$stop.postReadyLastRecognizedStopKind-notin@('debug-break','remote-breakin','loader-break')){return}
    $expected=New-TraceEventQueryCommand ([uint32]$ChildPid) $Live.expectedThreadId $Live.expectedProbeRva $Live.expectedImageSize $Live.expectedImage $Live.expectedFolder
    if($Live.eventQueryCommand-cne$expected){return}
    $Live.eventQueryAttempted=$true
    $Live.input.Write($expected+"`r`n");$Live.eventQueryWritten=$true
    $Live.input.Flush();$Live.eventQueryQueued=$true
    $deadline=[DateTime]::UtcNow.AddSeconds(2)
    while(-not$Live.process.HasExited-and[DateTime]::UtcNow-lt$deadline){
      Assert-TraceStreams $Live
      $snapshot=[MixelNamespaceTraceIO]::ReadBounded($Live.outPath,1048576)
      $event=Get-TraceEngineEventFacts $snapshot;$stack=Get-TraceEngineStackFacts $snapshot
      if($event.schemaAvailable-and$stack.schemaAvailable){break}
      Start-Sleep -Milliseconds 50
    }
  } catch { }
}
function Get-TraceEngineBuildFacts([string]$Text) {
  $facts=[ordered]@{available=$false;phase='unavailable';nativeExitAvailable=$false;nativeExitCode=0L;codeFactsAvailable=$false;errorCodes=@();warningCodes=@()}
  if($Text.Length-gt4096){return [pscustomobject]$facts}
  try {
    $value=ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    if((@($value.PSObject.Properties.Name)-join',')-cne'buildFailed,phase,nativeExitAvailable,nativeExitCode,codeFactsAvailable,errorCodes,warningCodes'-or$value.buildFailed-isnot[bool]-or-not$value.buildFailed-or$value.phase-isnot[string]-or$value.phase-cnotin@('platform','source','toolchain','sdk','compile','link','qualification')-or$value.nativeExitAvailable-isnot[bool]-or$value.codeFactsAvailable-isnot[bool]){return [pscustomobject]$facts}
    $exit=$value.nativeExitCode
    if(($exit-isnot[int]-and$exit-isnot[long])-or$exit-lt-2147483648-or$exit-gt4294967295-or(-not$value.nativeExitAvailable-and$exit-ne0)){return [pscustomobject]$facts}
    foreach($key in @('errorCodes','warningCodes')){
      $codes=$value.PSObject.Properties[$key].Value
      if($codes-isnot[Array]-or$codes.Count-gt32-or(-not$value.codeFactsAvailable-and$codes.Count-ne0)){return [pscustomobject]$facts}
      $previous=-1
      foreach($code in $codes){if(($code-isnot[int]-and$code-isnot[long])-or$code-lt0-or$code-gt9999-or$code-le$previous){return [pscustomobject]$facts};$previous=$code}
    }
    if(($value|ConvertTo-Json -Depth 3 -Compress)-cne$Text){return [pscustomobject]$facts}
    $facts.available=$true;$facts.phase=$value.phase;$facts.nativeExitAvailable=$value.nativeExitAvailable;$facts.nativeExitCode=[long]$exit;$facts.codeFactsAvailable=$value.codeFactsAvailable;$facts.errorCodes=@($value.errorCodes);$facts.warningCodes=@($value.warningCodes)
  } catch { }
  return [pscustomobject]$facts
}
function New-TraceEngineDll([string]$Private) {
  $helper=Join-Path $PSScriptRoot 'windows_namespace_engine.py';$source=Join-Path $PSScriptRoot 'windows-namespace-engine-readonly.cpp'
  Assert-TracePlainPath $helper;Assert-TracePlainPath $source;Assert-TracePlainPath $Private
  try {$text=(Invoke-TracePython @($helper,'--output',$Private) $Private 'engine-build' 140).Trim()} catch {
    try {
      $path=Join-Path $Private 'engine-build-stdout.private';Assert-TracePlainPath $path
      $failure=Get-TraceEngineBuildFacts ([MixelNamespaceTraceIO]::ReadBounded($path,4096).Trim())
      if($failure.available){Write-Host ('OBSERVED: bounded native SDK build failure; '+($failure|ConvertTo-Json -Depth 3 -Compress))}
    } catch { }
    throw
  }
  if($text.Length-gt1024-or$text-cnotmatch'\A\{"dllSHA256":"([0-9a-f]{64})","imageSize":([1-9][0-9]{0,8}),"exportRvas":\{"DebugExtensionInitialize":([1-9][0-9]{0,8}),"inspect":([1-9][0-9]{0,8})\},"sourceSHA256":"([0-9a-f]{64})"\}\z'){throw 'Private native SDK qualification bridge is invalid.'}
  $dllHash=$Matches[1];$image=[long]$Matches[2];$init=[long]$Matches[3];$inspect=[long]$Matches[4];$sourceHash=$Matches[5]
  if($image-gt268435456-or$init-ge$image-or$inspect-ge$image-or$init-eq$inspect-or(Get-FileHash $source -Algorithm SHA256).Hash.ToLowerInvariant()-cne$sourceHash){throw 'Private native SDK source/export identity differs.'}
  $dll=Join-Path $Private 'mixelengine.dll';Assert-TracePlainPath $dll
  $info=Get-Item -LiteralPath $dll
  if($info.Length-lt512-or$info.Length-gt16777216-or(Get-FileHash $dll -Algorithm SHA256).Hash.ToLowerInvariant()-cne$dllHash){throw 'Private native SDK DLL identity differs.'}
  return [pscustomobject]@{path=$dll;dllSHA256=$dllHash}
}
function Get-TraceFailureFacts($Live,[ValidateSet('export','identity','commands','startup','resume','trace','parse','result','ack','cleanup')][string]$Phase,[bool]$TimedOut,$Execution=$null) {
  $text='';$stderr='';$readFailed=$false;$stdoutBytes=0L;$stderrBytes=0L;$expectedControlCommand=''
  $expectedChildPid=0u;$expectedThreadId=0u
  if ($Live) {
    if($Live.PSObject.Properties['expectedControlCommand'] -and $Live.expectedControlCommand -is [string]){$expectedControlCommand=$Live.expectedControlCommand}
    if($Live.PSObject.Properties['childPid'] -and ($Live.childPid -is [int] -or $Live.childPid -is [uint32]) -and $Live.childPid -gt 0){$expectedChildPid=[uint32]$Live.childPid}
    if($Live.PSObject.Properties['expectedThreadId'] -and $Live.expectedThreadId -is [uint32] -and $Live.expectedThreadId -gt 0){$expectedThreadId=$Live.expectedThreadId}
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
    hardwareControlFailure=[bool]($complete -match '(?m)^MIXEL_NAMESPACE_TRACE_FAIL_(?:IMAGE|CONTROL_ADDRESS|CONTROL_BINDING|HWCONTROL_SCOPE|HWCONTROL_MISSING)\r?$')
    scopeFailure=[bool]($complete -match '(?m)^MIXEL_NAMESPACE_TRACE_FAIL_(?:SCOPE|PID|ARCH|WINDOW_NESTED|WIN32_INTERFACE)\r?$')
    returnFailure=[bool]($complete -match '(?m)^MIXEL_NAMESPACE_TRACE_FAIL_(?:RETURN_SCOPE|RETURN_HANDLE|WIN32_RETURN_SCOPE)\r?$')
    startupDiagnostics=Get-TraceStartupFacts $text
    hardwareDiagnostics=Get-TraceHardwareFacts $text $expectedControlCommand
    engineMetadataDiagnostics=Get-TraceEngineMetadataFacts $text $expectedChildPid $expectedThreadId $expectedControlCommand
    engineQueryDiagnostics=Get-TraceEngineQueryFacts $text
    engineShapeDiagnostics=Get-TraceEngineShapeFacts $text
    engineLoadDiagnostics=Get-TraceEngineLoadFacts $text
    engineProbeDiagnostics=Get-TraceEngineProbeFacts $text
    engineExecutionDiagnostics=Get-TraceEngineExecutionFacts $text
    engineEventDiagnostics=Get-TraceEngineEventFacts $text
    engineStackDiagnostics=Get-TraceEngineStackFacts $text
    eventQueryAttempted=[bool]($Live-and$Live.PSObject.Properties['eventQueryAttempted']-and$Live.eventQueryAttempted-is[bool]-and$Live.eventQueryAttempted)
    eventQueryWritten=[bool]($Live-and$Live.PSObject.Properties['eventQueryWritten']-and$Live.eventQueryWritten-is[bool]-and$Live.eventQueryWritten)
    eventQueryQueued=[bool]($Live-and$Live.PSObject.Properties['eventQueryQueued']-and$Live.eventQueryQueued-is[bool]-and$Live.eventQueryQueued)
    continuationDiagnostics=Get-TraceContinuationFacts $text
    markerFormattingDiagnostics=Get-TraceMarkerFormattingFacts $text
    entryDiagnostics=Get-TraceEntryFacts $text
    debuggerStop=Get-TraceCdbStopFacts $text
    execution=$Execution
  }
}
function New-TraceGatedSource([string]$Source,[string]$Gate,[string]$Nonce,[string]$Private,[bool]$Gated=$true) {
  Assert-TracePlainPath $Source
  Assert-TracePlainPath $Gate -AllowMissing
  $arguments=@((Join-Path $PSScriptRoot 'windows_namespace_probe.py'),'--source',$Source,'--gate',$Gate,'--nonce',$Nonce)
  if($Gated){$arguments+='--gated'}
  $qualified=(Invoke-TracePython $arguments $Private 'probe-compile' 100).Trim()
  if([Text.Encoding]::UTF8.GetByteCount($qualified) -gt 256 -or $qualified -cnotmatch '\A\{"probeRva":([1-9][0-9]{0,8}),"imageSize":([1-9][0-9]{0,8}),"exeSHA256":"([0-9a-f]{64})"\}\z'){throw 'Private PE qualification bridge is invalid.'}
  $rva=[long]$Matches[1];$image=[long]$Matches[2];$hash=$Matches[3]
  if($rva -ge $image -or $image -gt 268435456){throw 'Private PE qualification bridge exceeded its exact image bounds.'}
  return [pscustomobject]@{probeRva=[uint32]$rva;imageSize=[uint32]$image;exeSHA256=$hash}
}
function Assert-TraceProbeProof($Proof,[uint32]$ChildPid,[uint32]$Thread,[uint32]$ExpectedProbeRva,[uint32]$ImageSize) {
  if((@($Proof.PSObject.Properties.Name|Sort-Object)-join ',') -cne 'pid,probeRva,tid'){throw 'Private source probe proof schema is invalid.'}
  foreach($field in @('pid','tid')){
    $value=$Proof.PSObject.Properties[$field].Value
    if(($value -isnot [int] -and $value -isnot [long]) -or $value -le 0 -or $value -gt 4294967295){throw 'Private source probe proof exceeded its DWORD domain.'}
  }
  $rva=$Proof.probeRva
  if(($rva -isnot [int] -and $rva -isnot [long]) -or $rva -le 0 -or $ExpectedProbeRva -eq 0 -or $rva -ne $ExpectedProbeRva -or $ExpectedProbeRva -ge $ImageSize -or $ImageSize -gt 268435456){throw 'Private source probe pointer disagrees with the exact qualified PE export.'}
  if($Proof.pid -ne $ChildPid -or $Proof.tid -ne $Thread){throw 'Private source probe PID/thread disagrees with retained native ownership.'}
}
function Read-TraceProbeProof([string]$Path) {
  Assert-TracePlainPath $Path
  $text=[MixelNamespaceTraceIO]::ReadBounded($Path,128)
  if($text -cnotmatch '\A\{"pid":([1-9][0-9]{0,9}),"tid":([1-9][0-9]{0,9}),"probeRva":(0|[1-9][0-9]{0,8})\}\z'){throw 'Private source probe bytes are not the exact bounded schema.'}
  $pidValue=[long]$Matches[1];$tidValue=[long]$Matches[2];$rva=[long]$Matches[3]
  if($pidValue -gt 4294967295 -or $tidValue -gt 4294967295 -or $rva -ge 268435456){throw 'Private source probe bytes exceeded their exact scalar domain.'}
  return [pscustomobject]@{pid=$pidValue;tid=$tidValue;probeRva=$rva}
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
  $live=$null;$childPid=0;$eventHandle=[IntPtr]::Zero;$timeout=$false;$completed=$false;$phase='export';$attemptFailure=$null;$resumed=$false;$previous=-2;$resumeAttempted=$false;$additionalCount=-1L;$fallbackResult=$null;$thread=0;$expectedProbeRva=0;$expectedImageSize=0
  try {
    [void](Invoke-TracePython @((Join-Path $PSScriptRoot 'test-support-lease.py'),'--export-standard-windows-fixture',$attemptRoot,'--event',('Global\'+$event)) $private 'source-export' 100)
    Assert-TracePlainPath $source;Assert-TracePlainPath $exe
    $originalHash=(Get-FileHash $source -Algorithm SHA256).Hash
    $qualifiedPe=New-TraceGatedSource $source $gate $nonce $private ([bool]$Gated)
    $expectedProbeRva=$qualifiedPe.probeRva;$expectedImageSize=$qualifiedPe.imageSize
    $diagnosticHash=(Get-FileHash $source -Algorithm SHA256).Hash;$exeHash=(Get-FileHash $exe -Algorithm SHA256).Hash
    if ($originalHash -ceq $diagnosticHash) { throw 'Private timing-gate source hash binding is invalid.' }
    if($qualifiedPe.exeSHA256 -cne $exeHash.ToLowerInvariant()){throw 'Private qualified PE identity differs from the exact retained executable.'}
    $engine=New-TraceEngineDll $private
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
    [void](Invoke-TracePython @((Join-Path $PSScriptRoot 'windows-namespace-cdb.py'),'--pid',[string]$childPid,'--tid',[string]$thread,'--session',[string]$script:session,'--event',$event,'--windows-folder',$private,'--output',$private,'--binary',$exe,'--exe-sha256',$exeHash,'--engine-dll',$engine.path,'--engine-sha256',$engine.dllSHA256) $private 'commands')
    $arguments=@('-p',[string]$childPid,'-pd','-noshell','-nosqm','-cf',(Join-Path $private 'start.txt'))
    if ($Gated) { $arguments=@('-p',[string]$childPid,'-pr','-pb','-pd','-noshell','-nosqm','-cf',(Join-Path $private 'start.txt')) }
    $live=Start-TraceProcess $script:cdb $arguments $private 'cdb' $childPid
    $live|Add-Member -NotePropertyName expectedControlCommand -NotePropertyValue ('"$$><'+(Join-Path $private 'hwcontrol.txt')+'"')
    $live|Add-Member -NotePropertyName expectedThreadId -NotePropertyValue ([uint32]$thread)
    $live|Add-Member -NotePropertyName expectedProbeRva -NotePropertyValue ([uint32]$expectedProbeRva)
    $live|Add-Member -NotePropertyName expectedImageSize -NotePropertyValue ([uint32]$expectedImageSize)
    $live|Add-Member -NotePropertyName expectedImage -NotePropertyValue $exe
    $live|Add-Member -NotePropertyName expectedFolder -NotePropertyValue $private
    $live|Add-Member -NotePropertyName eventQueryCommand -NotePropertyValue (New-TraceEventQueryCommand ([uint32]$childPid) ([uint32]$thread) ([uint32]$expectedProbeRva) ([uint32]$expectedImageSize) $exe $private)
    $live|Add-Member -NotePropertyName eventQueryAttempted -NotePropertyValue $false
    $live|Add-Member -NotePropertyName eventQueryWritten -NotePropertyValue $false
    $live|Add-Member -NotePropertyName eventQueryQueued -NotePropertyValue $false
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
      Write-Host ('OBSERVED: bounded source-only trace startup facts; '+((Get-TraceFailureFacts $live $phase $true (Get-TraceExecutionFacts $childPid $attemptRoot $resumed $previous ([bool]$Gated) $live $resumeAttempted $additionalCount ([uint32]$thread) $expectedProbeRva $expectedImageSize)) | ConvertTo-Json -Depth 4 -Compress))
      $fallbackResult=[pscustomobject]@{StartupTimedOut=$true;GatedFallbackRequired=$true;FallbackReason='startup-timeout';AdditionalSuspensionDetected=$false;AdditionalPreviousSuspendCount=0L;OwnedCleanupVerified=$false}
      return $fallbackResult
    }
    $phase='resume'
    $resumeAttempted=$true
    try {$previous=[MixelOrdinaryTokenFixture]::ResumeOwnedPrimaryThread($childPid);$resumed=$true} catch {
      $additionalCount=Get-TraceAdditionalSuspensionCount $_.Exception
      if(Test-TraceAdditionalSuspensionFallback $live $childPid $attemptRoot $additionalCount $ready $resumeAttempted $resumed ([bool]$Gated)) {
        Write-Host ('OBSERVED: bounded source-only trace additional suspension facts; '+((Get-TraceFailureFacts $live $phase $false (Get-TraceExecutionFacts $childPid $attemptRoot $resumed $previous ([bool]$Gated) $live $resumeAttempted $additionalCount ([uint32]$thread) $expectedProbeRva $expectedImageSize)) | ConvertTo-Json -Depth 4 -Compress))
        $fallbackResult=[pscustomobject]@{StartupTimedOut=$false;GatedFallbackRequired=$true;FallbackReason='additional-suspension';AdditionalSuspensionDetected=$true;AdditionalPreviousSuspendCount=$additionalCount;OwnedCleanupVerified=$false}
        return $fallbackResult
      }
      throw
    }
    if ((-not $Gated -and $previous -ne 1) -or ($Gated -and $previous -notin @(0,1))) { throw 'Trace did not perform the one qualified owned primary resume.' }
    Send-TraceContinuation $live $childPid $ready $resumed $previous ([bool]$Gated)
    Write-Host ('OBSERVED: bounded source-only trace continuation queued; '+([pscustomobject]@{privateGate=[bool]$Gated;primaryResumePrevious=$previous;continuationAttempted=$live.continuationAttempted;continuationWritten=$live.continuationWritten;continuationQueued=$live.continuationQueued}|ConvertTo-Json -Compress))
    if ($Gated) {
      $temporary=Join-Path $gateRoot 'ready.new';[IO.File]::WriteAllText($temporary,$nonce,[Text.UTF8Encoding]::new($false));[IO.File]::Move($temporary,$gate)
    }
    $phase='trace'
    $deadline=[DateTime]::UtcNow.AddSeconds(15)
    while (-not $live.process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Assert-TraceStreams $live;Start-Sleep -Milliseconds 50 }
    Assert-TraceStreams $live
    if (-not $live.process.HasExited) { $timeout=$true;Collect-TraceStoppedEvent $live $childPid;throw 'Source trace did not reach END and detach within its trace deadline.' }
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
    $probePath=Join-Path $attemptRoot 'fixture-hwcontrol.json';Assert-TracePlainPath $probePath
    $probeProof=Read-TraceProbeProof $probePath
    Assert-TraceProbeProof $probeProof ([uint32]$childPid) ([uint32]$thread) $expectedProbeRva $expectedImageSize
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
    return [pscustomobject]@{StartupTimedOut=$false;GatedFallbackRequired=$false;Scenario=$Scenario;PrivateGate=[bool]$Gated;FirstCallQualified=$true;OriginalSourceSHA256=$originalHash;DiagnosticSourceSHA256=$diagnosticHash;DiagnosticExecutableSHA256=$exeHash;HardwareControlQualified=$true;SourceProbeThreadQualified=$true;OwnedIdentityQualified=$true;DebuggerDetached=$true;SingleResumeQualified=$true;SingleContinuationQualified=$live.continuationQueued;rawOpenError=$record.rawOpenError;Trace=$parsed}
  } catch {
    $attemptFailure=$_.Exception
    Write-Host ('OBSERVED: bounded source-only trace failure facts; '+((Get-TraceFailureFacts $live $phase $timeout (Get-TraceExecutionFacts $childPid $attemptRoot $resumed $previous ([bool]$Gated) $live $resumeAttempted $additionalCount ([uint32]$thread) $expectedProbeRva $expectedImageSize)) | ConvertTo-Json -Depth 4 -Compress))
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
    if ($fallbackResult) {$fallbackResult.OwnedCleanupVerified=$true;Write-Host ('OBSERVED: bounded source-only trace clean fallback; '+($fallbackResult|ConvertTo-Json -Compress))}
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
    if ($result.GatedFallbackRequired) {
      if(-not $result.OwnedCleanupVerified) {throw 'Fresh gated trace requires verified cleanup of the rejected original attempt.'}
      $result=Invoke-TraceAttempt $scenario -Gated
    }
    if ($result.GatedFallbackRequired -or $result.StartupTimedOut -or -not $result.FirstCallQualified) { throw 'Source trace lacks first-call qualification.' }
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
