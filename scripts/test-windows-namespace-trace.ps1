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
function Set-TraceDirectoryAcl([string]$Path,[string]$ChildSid,[switch]$ReadOnly) {
  Assert-TracePlainPath $Path
  $acl=[Security.AccessControl.DirectorySecurity]::new()
  $acl.SetAccessRuleProtection($true,$false)
  $runner=[Security.Principal.WindowsIdentity]::GetCurrent().User
  foreach ($sid in @($runner,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))
  }
  if ($ChildSid) {
    $rights=if ($ReadOnly) {'ReadAndExecute'} else {'Modify'}
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($ChildSid),$rights,'ContainerInherit,ObjectInherit','None','Allow'))
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
  $actual=Get-Acl -LiteralPath $Path
  if ($actual.GetSecurityDescriptorSddlForm('Access') -cne $acl.GetSecurityDescriptorSddlForm('Access')) { throw 'Owned trace directory ACL did not read back exactly.' }
}
function Set-TraceReadOnlyFile([string]$Path,[string]$ChildSid) {
  Assert-TracePlainPath $Path
  $acl=[Security.AccessControl.FileSecurity]::new();$acl.SetAccessRuleProtection($true,$false)
  foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'))
  }
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($ChildSid),'ReadAndExecute','Allow'))
  Set-Acl -LiteralPath $Path -AclObject $acl
  if ((Get-Acl -LiteralPath $Path).GetSecurityDescriptorSddlForm('Access') -cne $acl.GetSecurityDescriptorSddlForm('Access')) { throw 'Owned trace read-only file ACL did not read back exactly.' }
}
function Start-TraceProcess([string]$File,[string[]]$Arguments,[string]$Folder,[string]$Label) {
  Assert-TracePlainPath $Folder
  $outPath=Join-Path $Folder ($Label+'-stdout.private');$errPath=Join-Path $Folder ($Label+'-stderr.private')
  Assert-TracePlainPath $outPath -AllowMissing;Assert-TracePlainPath $errPath -AllowMissing
  $output=$null;$errors=$null;$process=$null;$started=$false
  try {
    $output=[IO.FileStream]::new($outPath,'CreateNew','Write','ReadWrite')
    $errors=[IO.FileStream]::new($errPath,'CreateNew','Write','ReadWrite')
    $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName=$File;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    foreach ($argument in $Arguments) { [void]$info.ArgumentList.Add($argument) }
    $process=[Diagnostics.Process]::new();$process.StartInfo=$info
    if (-not $process.Start()) { throw 'Owned trace subprocess failed to start.' };$started=$true
    return [pscustomobject]@{process=$process;input=$process.StandardInput;output=$output;errors=$errors;outPath=$outPath;errPath=$errPath;outTask=[MixelNamespaceTraceIO]::CopyBounded($process.StandardOutput.BaseStream,$output,1048576);errTask=[MixelNamespaceTraceIO]::CopyBounded($process.StandardError.BaseStream,$errors,1048576);closed=$false}
  } catch {
    if ($started -and -not $process.HasExited) { $process.Kill();if (-not $process.WaitForExit(10000)) { throw 'Owned trace subprocess startup cleanup failed.' } }
    if ($output) {$output.Dispose()};if ($errors) {$errors.Dispose()};if ($process) {$process.Dispose()};throw
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
      if (-not $task.Wait(10000)) { throw 'Private trace output drain timed out.' }
      [void]$task.GetAwaiter().GetResult()
    }
  } finally { $Live.input.Dispose();$Live.output.Dispose();$Live.errors.Dispose();$Live.closed=$true }
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
function Invoke-TracePython([string[]]$Arguments,[string]$Folder,[string]$Label,[int]$Seconds=90) {
  $live=Start-TraceProcess $script:python $Arguments $Folder $Label
  try {
    $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
    while (-not $live.process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Assert-TraceStreams $live;Start-Sleep -Milliseconds 50 }
    Assert-TraceStreams $live
    if (-not $live.process.HasExited) { throw 'Bounded trace Python operation timed out.' }
    Complete-TraceStreams $live
    if ($live.process.ExitCode -ne 0) { throw 'Private trace Python operation failed; raw output is never printed.' }
    return [MixelNamespaceTraceIO]::ReadBounded($live.outPath,1048576)
  } finally {
    try {
      if (-not $live.process.HasExited) { $live.process.Kill();if (-not $live.process.WaitForExit(10000)) { throw 'Retained trace Python cleanup failed.' } }
      Complete-TraceStreams $live
    } finally { $live.process.Dispose() }
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
function Get-TraceFailureFacts($Live,[ValidateSet('export','identity','commands','startup','resume','trace','parse','result','ack','cleanup')][string]$Phase,[bool]$TimedOut) {
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
  $live=$null;$childPid=0;$eventHandle=[IntPtr]::Zero;$timeout=$false;$completed=$false;$phase='export'
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
    $live=Start-TraceProcess $script:cdb $arguments $private 'cdb'
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
      Write-Host ('OBSERVED: bounded source-only trace startup facts; '+((Get-TraceFailureFacts $live $phase $true) | ConvertTo-Json -Compress))
      return [pscustomobject]@{StartupTimedOut=$true}
    }
    $phase='resume'
    $previous=[MixelOrdinaryTokenFixture]::ResumeOwnedPrimaryThread($childPid)
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
    Write-Host ('OBSERVED: bounded source-only trace failure facts; '+((Get-TraceFailureFacts $live $phase $timeout) | ConvertTo-Json -Compress))
    throw
  } finally {
    $errors=[Collections.Generic.List[Exception]]::new()
    if ($live) { try { Stop-TraceDebugger $live $childPid } catch {$errors.Add([InvalidOperationException]::new('Retained trace debugger detach/drain cleanup failed.'))};$live.process.Dispose() }
    try {[MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()} catch {$errors.Add([InvalidOperationException]::new('Retained source trace child cleanup failed.'))}
    [MixelOrdinaryTokenFixture]::CloseStartedObservations()
    if ($eventHandle -ne [IntPtr]::Zero) {try {[MixelIsolatedLeaseControl]::Close($eventHandle)} catch {$errors.Add([InvalidOperationException]::new('Owned GUID event handle cleanup failed.'))}}
    if ($errors.Count -gt 0) {throw [AggregateException]::new('Source trace attempt cleanup failed.',$errors)}
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
$fileSnapshots=[Collections.Generic.Dictionary[string,string]]::new()
$stage='create owned source trace account'
try {
  $accountStart=$launchSource.IndexOf('$random = [byte[]]::new(24)')
  $accountEnd=$launchSource.IndexOf("`$fixtureStage = 'grant owned standard desktop access'",$accountStart)
  if ($accountStart -lt 0 -or $accountEnd -le $accountStart) { throw 'Exact disposable customer account bootstrap changed.' }
  . ([scriptblock]::Create($launchSource.Substring($accountStart,$accountEnd-$accountStart)))
  if (-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) { throw 'Exact owned trace account SID/name verification failed.' }
  foreach ($directory in @($privateRoot,$fixtureRoot,$gateRoot)) {
    Assert-TracePlainPath $directory -AllowMissing
    New-Item -ItemType Directory -Path $directory | Out-Null
    $fileSnapshots.Add($directory,(Get-Acl -LiteralPath $directory).GetSecurityDescriptorSddlForm('Access'))
    Set-TraceDirectoryAcl $directory $(if ($directory -ceq $privateRoot) {''} else {$ownedSid}) -ReadOnly
  }
  $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
  $namespaceAccess=[MixelCI.StandardNamespaceAccess]::new($ownedSid,$ownedUser,$session)
  foreach ($scenario in @('absent','world')) {
    $stage='run '+$scenario+' source hardware trace'
    $result=Invoke-TraceAttempt $scenario
    if ($result.StartupTimedOut) { $result=Invoke-TraceAttempt $scenario -Gated }
    if ($result.StartupTimedOut -or -not $result.FirstCallQualified) { throw 'Source trace lacks first-call qualification.' }
    # Only validated numeric observations/booleans and source digests go public.
    Write-Host ('OBSERVED: source-only namespace hardware trace; '+($result | ConvertTo-Json -Depth 8 -Compress))
  }
} catch {
  $failure=[InvalidOperationException]::new(('Source-only namespace tracing failed at '+$stage+'. No raw debugger output was printed.'))
} finally {
  $cleanup=[Collections.Generic.List[Exception]]::new()
  try {[MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()} catch {$cleanup.Add([InvalidOperationException]::new('Final retained trace child cleanup failed.'))}
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
      $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetSecurityDescriptorSddlForm($pair.Value,'Access');Set-Acl -LiteralPath $pair.Key -AclObject $acl
      if ((Get-Acl -LiteralPath $pair.Key).GetSecurityDescriptorSddlForm('Access') -cne $pair.Value) {throw 'Trace file ACL restoration mismatch.'}
      Remove-Item -LiteralPath $pair.Key -Recurse -Force
    } catch {$cleanup.Add([InvalidOperationException]::new('Owned trace private/source/gate ACL restoration or file cleanup failed.'))}
  }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) {$cleanup.Add([InvalidOperationException]::new('Owned trace account remains.'))}
  if ($failure) {$cleanup.Insert(0,$failure)}
  if ($cleanup.Count -gt 0) {throw [AggregateException]::new('Source-only namespace trace or exact owned cleanup failed.',$cleanup)}
}
Write-Host 'PASS: source-only hardware traces completed with retained identity, first-call qualification, bounded private output and exact owned cleanup; signed customer release gates remain required.'
