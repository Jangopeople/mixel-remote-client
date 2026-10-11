# Current-caller source observations only. No standard-account or customer-app qualification.
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

# The functions above are copied byte-for-byte from the exact shipped verifier
# and six-field result parser. Their reuse makes no account classification.
$script:CurrentCallerOwned=[Collections.Generic.List[object]]::new()
$script:CurrentCallerObserved=[Collections.Generic.List[object]]::new()
function New-MixelCurrentCallerProcess([string]$Executable) {
  if(-not [IO.Path]::IsPathRooted($Executable) -or [IO.Path]::GetFullPath($Executable) -cne $Executable -or $Executable.IndexOfAny([char[]]@('"',"`r","`n")) -ge 0) { throw 'Requires the exact verified owned executable path.' }
  $info=[Diagnostics.ProcessStartInfo]::new()
  $info.FileName=$Executable
  $info.Arguments=''
  $info.WorkingDirectory=[IO.Path]::GetDirectoryName($Executable)
  $info.UseShellExecute=$false
  $info.CreateNoWindow=$false
  $process=[Diagnostics.Process]::new()
  $process.StartInfo=$info
  return $process
}
function Start-MixelCurrentCallerOwned([string]$Executable) {
  $process=New-MixelCurrentCallerProcess $Executable
  $record=[pscustomobject]@{Process=$process;Started=$false;Closed=$false;ControlPid=0}
  $script:CurrentCallerOwned.Add($record)
  if(-not $process.Start()) { throw 'Owned current-caller process did not start.' }
  $record.Started=$true
  $number=$process.Id
  if($number -isnot [int] -or $number -le 0) { throw 'Owned current-caller process PID is invalid.' }
  $record.ControlPid=$number
  return $record
}
function Stop-MixelCurrentCallerOwned {
  $failures=[Collections.Generic.List[string]]::new()
  foreach($record in @($script:CurrentCallerOwned.ToArray())) {
    try {
      if($record.Started -and -not $record.Closed -and -not $record.Process.HasExited) {
        $record.Process.Kill()
        if(-not $record.Process.WaitForExit(10000) -or -not $record.Process.HasExited) { throw 'Owned current-caller termination was not confirmed.' }
      }
    } catch { $failures.Add('One exact owned current-caller process could not be stopped.') }
  }
  if($failures.Count) { throw ($failures -join ' ') }
}
function Close-MixelCurrentCallerOwned {
  $failures=[Collections.Generic.List[string]]::new()
  foreach($record in @($script:CurrentCallerOwned.ToArray())) {
    try {
      if($record.Closed) { throw 'Owned current-caller record was unexpectedly already closed.' }
      if($record.Started -and -not $record.Process.HasExited) { throw 'Refusing to lose a still-running owned process handle.' }
      $record.Process.Dispose()
      $record.Closed=$true
      [void]$script:CurrentCallerOwned.Remove($record)
    } catch { $failures.Add('One exact owned current-caller process could not be closed.') }
  }
  if($failures.Count) { throw ($failures -join ' ') }
}
function Read-MixelCurrentCallerResult([string]$Path) {
  $bytes=Read-PortableLeaseBytes $Path 4096
  if($bytes.Length -eq 0) { throw 'Owned current-caller source result is empty.' }
  return ([Text.UTF8Encoding]::new($false,$true).GetString($bytes)|ConvertFrom-Json)
}
function Invoke-MixelCurrentCallerControl([ValidateSet('probe','owner')][string]$Mode,[string]$ExpectedStatus,[int]$ExpectedError,[string]$ExpectedProof,[bool]$ExpectedRequired,[switch]$KeepAlive) {
  $resultPath=Join-Path $script:CurrentCallerWorkspace 'fixture-result.json'
  if(Test-Path -LiteralPath $resultPath) { [void](Read-PortableLeaseBytes $resultPath 4096); Remove-Item -LiteralPath $resultPath }
  [IO.File]::WriteAllText((Join-Path $script:CurrentCallerWorkspace 'fixture-mode.txt'),$Mode,[Text.UTF8Encoding]::new($false))
  $owned=Start-MixelCurrentCallerOwned $script:CurrentCallerExecutable
  $deadline=[DateTime]::UtcNow.AddSeconds(10)
  while(-not (Test-Path -LiteralPath $resultPath) -and [DateTime]::UtcNow -lt $deadline) {
    if($owned.Process.HasExited) { break }
    Start-Sleep -Milliseconds 50
  }
  if(-not (Test-Path -LiteralPath $resultPath)) { throw 'Exact owned current-caller process produced no bounded result.' }
  $result=Read-MixelCurrentCallerResult $resultPath
  Assert-StandardLeaseSafeResult $result $owned.ControlPid
  if($owned.Process.HasExited) { throw 'Exact owned current-caller process exited before result acknowledgment.' }
  $script:CurrentCallerObserved.Add([pscustomobject]@{mode=$Mode;result=$result})
  Write-Host ('OBSERVED current-caller only; genuineStandardQualified=false; fullEightQualified=false; customerAppQualified=false; '+($result|ConvertTo-Json -Compress))
  if($result.sourceStatus -cne $ExpectedStatus -or $result.rawOpenError -ne $ExpectedError -or $result.attestation -cne $ExpectedProof -or $result.requiresClick -ne $ExpectedRequired -or $result.ownerFailed) { throw 'Actual current-caller GUID lifecycle result differs from its strict expected phase.' }
  if(-not $KeepAlive) {
    [IO.File]::WriteAllText((Join-Path $script:CurrentCallerWorkspace ('fixture-release-'+$owned.ControlPid)),'verified',[Text.UTF8Encoding]::new($false))
    if(-not $owned.Process.WaitForExit(10000) -or -not $owned.Process.HasExited -or $owned.Process.ExitCode -ne 0) { throw 'Exact acknowledged current-caller source process did not exit successfully.' }
  }
  return $owned
}
function Invoke-MixelCurrentCallerFour {
  $absent='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
  $active='ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=false'
  $activeOwner='ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=true'
  [void](Invoke-MixelCurrentCallerControl probe $absent 2 '' $false)
  $owner=Invoke-MixelCurrentCallerControl owner $activeOwner 0 'attended-runtime-v2' $true -KeepAlive
  [void](Invoke-MixelCurrentCallerControl probe $active 0 'attended-runtime-v2' $true)
  if($owner.Process.HasExited) { throw 'Exact current-caller owner exited before explicit owned termination.' }
  Stop-MixelCurrentCallerOwned
  if(-not $owner.Process.HasExited) { throw 'Exact current-caller owner termination was not confirmed.' }
  # Process.Kill determines its own exit code. This scoped observer does not
  # claim the original native TerminateProcess(exitCode1) standard assertion.
  Write-Host ('OBSERVED exact current-caller owner terminated; exitCode=0x'+$owner.Process.ExitCode.ToString('x8')+'; standard/full-eight/customer-app qualification remains false.')
  Close-MixelCurrentCallerOwned
  [void](Invoke-MixelCurrentCallerControl probe $absent 2 '' $false)
}

$bundle=Assert-PortableLeaseBundle $BundleRoot $ExpectedManifestSha256
if($bundle.Manifest.commit -cne 'c0a3d1ccb644a83df5615b2ac737cff10c94ed87' -or $bundle.Manifest.tree -cne 'f6e860ff5997cd41b0411a5de59fc3034a082025' -or $bundle.Manifest.runId -ne 38120029931 -or $ExpectedManifestSha256 -cne 'f0823ec8da4fe639307dc8e950e0bcb105863621b8a408b0dea64f52916b3367') { throw 'Current-caller observer requires the exact independently verified compilation package.' }
$script:CurrentCallerWorkspace=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('MixelRemoteQA-current-caller-'+[Guid]::NewGuid().ToString('N'))
$selected=if($Subsystem -ceq 'windows'){'lease-standard-gui.exe'}else{'lease-standard.exe'}
$script:CurrentCallerExecutable=Join-Path $script:CurrentCallerWorkspace $selected
$workspaceCreated=$false
$passed=$false
$failure=$null
$cleanup=[Collections.Generic.List[string]]::new()
try {
  if(Test-Path -LiteralPath $script:CurrentCallerWorkspace) { throw 'Requires a new owned current-caller workspace.' }
  [void](New-Item -ItemType Directory -Path $script:CurrentCallerWorkspace)
  $workspaceCreated=$true
  $member=@($bundle.Manifest.files|Where-Object {$_.path -ceq ('fixture/'+$selected)})
  if($member.Count -ne 1) { throw 'Verified selected fixture is ambiguous.' }
  [IO.File]::Copy((Join-Path $bundle.Root ('fixture/'+$selected)),$script:CurrentCallerExecutable,$false)
  $bytes=Read-PortableLeaseBytes $script:CurrentCallerExecutable 16777216
  if($bytes.Length -ne $member[0].bytes -or (Get-FileHash -LiteralPath $script:CurrentCallerExecutable -Algorithm SHA256).Hash.ToLowerInvariant() -cne $member[0].sha256) { throw 'Copied owned executable differs from the trusted package.' }
  Invoke-MixelCurrentCallerFour
  $passed=$true
} catch { $failure=$_ } finally {
  try { Stop-MixelCurrentCallerOwned } catch { $cleanup.Add('Owned current-caller termination failed.') }
  try { Close-MixelCurrentCallerOwned } catch { $cleanup.Add('Owned current-caller handle closure failed.') }
  if($workspaceCreated) {
    $summary=[ordered]@{scope='current-caller-four-guid-source-observations-only';applicationSource=$bundle.Manifest.commit;compileRun=$bundle.Manifest.runId;manifestSha256=$ExpectedManifestSha256;subsystem=$Subsystem;fourObservationsPassed=($passed -and $cleanup.Count -eq 0);genuineStandardQualified=$false;fullEightQualified=$false;customerAppQualified=$false;observations=@($script:CurrentCallerObserved.ToArray());ownedCleanupPassed=($cleanup.Count -eq 0);failure=if($failure -or $cleanup.Count){'current-caller-observation-or-cleanup-failed'}else{''}}
    [IO.File]::WriteAllText((Join-Path $script:CurrentCallerWorkspace 'current-caller-summary.json'),($summary|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    Write-Host ('Owned current-caller evidence: '+$script:CurrentCallerWorkspace)
  }
}
if($failure) { throw $failure }
if($cleanup.Count) { throw ($cleanup -join ' ') }
Write-Host 'PASS: four current-caller GUID source observations only; genuineStandardQualified=false; fullEightQualified=false; customerAppQualified=false.'
