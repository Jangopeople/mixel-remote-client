# Optional disposable-CI experiment; the original Standard gate remains fatal.
param([Parameter(Mandatory=$true)][string]$EvidenceDirectory)
$ErrorActionPreference='Stop'

function Invoke-SessionDirectoryAba {
  if ($ownedUser -cnotmatch '\Amixellease[0-9a-f]{10}\z' -or
      (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser -or
      $runnerFacts.unavailableKind -or $freshFacts.unavailableKind -or
      $runnerFacts.integrityRid -ne 12288 -or $freshFacts.integrityRid -ne 8192 -or
      $runnerFacts.hasRestrictions -or $runnerFacts.isAppContainer -or
      $freshFacts.hasRestrictions -or $freshFacts.isAppContainer -or
      $runnerFacts.sessionId -ne $freshFacts.sessionId -or
      $runnerFacts.sessionId -ne (Get-Process -Id $PID).SessionId) {
    throw 'Disposable owned QA identity and High/Medium same-session source facts required.'
  }
  $identity=[Security.Principal.SecurityIdentifier]::new($ownedSid)
  $sidBytes=[byte[]]::new($identity.BinaryLength); $identity.GetBinaryForm($sidBytes,0)
  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null
  $cleanupFailures=[Collections.Generic.List[Exception]]::new()
  $observations=[Collections.Generic.List[object]]::new()
  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
  try {
    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)
    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)
    $scope.Open()
    Invoke-SessionDirectoryAbaPhase before $sourceHash $observations
    $scope.Apply()
    Invoke-SessionDirectoryAbaPhase temporary $sourceHash $observations
  } catch { $bodyFailure=$_ }
  finally {
    # Stop all exactly retained children before the independently attempted restore.
    # This original stop implementation also drains partially launched children.
    try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
    catch { $cleanupFailures.Add($_.Exception) }
    try { if ($scope) { $scope.Restore() } }
    catch { $restoreFailure=$_ }
  }
  try {
    if (-not $bodyFailure -and -not $restoreFailure -and $cleanupFailures.Count -eq 0) {
      Invoke-SessionDirectoryAbaPhase restored $sourceHash $observations
    }
  } catch { $bodyFailure=$_ }
  finally {
    try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
    catch { $cleanupFailures.Add($_.Exception) }
    try { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }
    catch { $cleanupFailures.Add($_.Exception) }
    # If an owned child cannot be confirmed stopped, the original outer finally
    # retains responsibility for its process map and the original World registry.
    if ($cleanupFailures.Count -eq 0 -and $world -ne [IntPtr]::Zero) {
      try { [MixelIsolatedLeaseControl]::Close($world); $world=[IntPtr]::Zero }
      catch { $cleanupFailures.Add($_.Exception) }
    }
    if ($scope) { try { $scope.Dispose() } catch { $cleanupFailures.Add($_.Exception) } }
    $report=[ordered]@{schema=1;scope='DisposableHostedCiSessionDirectoryAba';sourceExeSha256=$sourceHash;
      observations=$observations.ToArray();directory=$(if($scope){$scope.facts}else{$null});
      bodyFailed=($null -ne $bodyFailure);restoreFailed=($null -ne $restoreFailure);
      bodyFailure=$(Get-SessionDirectoryAbaFailure $bodyFailure);restoreFailure=$(Get-SessionDirectoryAbaFailure $restoreFailure);
      cleanupFailureCount=$cleanupFailures.Count;worldHandleClosed=($world -eq [IntPtr]::Zero);
      processCleanupOkay=($cleanupFailures.Count -eq 0);actualWin32RouteMeasured=$false;causeEstablished=$false;
      productionWorkaroundQualified=$false;standardEightQualified=$false}
    $json=$report | ConvertTo-Json -Depth 12 -Compress
    Write-Host ('OBSERVED: CI-only session directory diagnostic; ' + $json)
    $bytes=[Text.UTF8Encoding]::new($false).GetBytes($json+"`n")
    try {
      $stream=[IO.File]::Open((Join-Path $script:abaEvidence 'session-directory-aba.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
      try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    } catch { $reportFailure=$_ }
  }
  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or
      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {
    throw 'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.'
  }
  if ($bodyFailure -or $reportFailure) {
    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})
    Write-Host 'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.'
  }
}

function Get-SessionDirectoryAbaFailure($Failure) {
  if (-not $Failure) { return $null }
  $error=$Failure.Exception
  for ($depth=0; $depth -lt 8 -and $error -is [Management.Automation.MethodInvocationException] -and $error.InnerException; $depth++) { $error=$error.InnerException }
  return [ordered]@{exceptionType=$error.GetType().Name;nativeError=$(if($error -is [ComponentModel.Win32Exception]){$error.NativeErrorCode}else{$null})}
}

function Invoke-SessionDirectoryAbaPhase([ValidateSet('before','temporary','restored')][string]$Phase,
    [string]$SourceHash,[Collections.Generic.List[object]]$Observations) {
  foreach ($launcher in @('with-logon','with-token')) {
    if ((Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant() -cne $SourceHash) {
      throw 'Original compiled fixture changed between A/B/A phases.'
    }
    $controlPid=Invoke-StandardLeaseControl -Mode probe -Launcher $launcher -ObserveOnly -ObservationLabel ('global-session-directory-' + $Phase)
    $record=([IO.File]::ReadAllText($resultPath) | ConvertFrom-Json)
    Assert-StandardLeaseSafeResult $record $controlPid
    if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'exited:0x00000000') {
      throw 'Exact original A/B/A child exit was not confirmed.'
    }
    $Observations.Add([ordered]@{phase=$Phase;launcher=$launcher;pid=$controlPid;
      sourceExeSha256=$SourceHash;rawOpenError=$record.rawOpenError;sourceStatus=$record.sourceStatus;
      attestation=$record.attestation;requiresClick=$record.requiresClick;ownerFailed=$record.ownerFailed;
      sameOwnedStandardIdentity=$true;exactRetainedChildExited=$true;actualWin32RouteMeasured=$false})
  }
}

function New-SessionDirectoryAbaScript([string]$Original,[string]$Prefix) {
  $anchor='  $currentCallerFailure = $null'
  if ($Original.IndexOf($anchor) -lt 0 -or $Original.IndexOf($anchor) -ne $Original.LastIndexOf($anchor)) {
    throw 'Exact original owned bootstrap insertion boundary changed.'
  }
  $insertion="  Invoke-SessionDirectoryAba`n"
  return $Prefix + "`n" + $Original.Replace($anchor,$insertion+$anchor) +
    "`nif (`$script:abaDeferredFailure) { throw `$script:abaDeferredFailure }`n"
}

function Start-StandardValidQaNamespace {
  if ($script:qaNamespaceScope -or $ownedUser -cnotmatch '\Amixellease[0-9a-f]{10}\z' -or
      (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser -or
      $runnerFacts.unavailableKind -or $freshFacts.unavailableKind -or
      $runnerFacts.integrityRid -ne 12288 -or $freshFacts.integrityRid -ne 8192 -or
      $runnerFacts.hasRestrictions -or $runnerFacts.isAppContainer -or
      $freshFacts.hasRestrictions -or $freshFacts.isAppContainer -or
      $runnerFacts.sessionId -ne $freshFacts.sessionId -or
      $runnerFacts.sessionId -ne (Get-Process -Id $PID).SessionId) {
    throw 'Exact disposable owned QA identity and High/Medium same-session source facts required.'
  }
  $identity=[Security.Principal.SecurityIdentifier]::new($ownedSid)
  $sidBytes=[byte[]]::new($identity.BinaryLength);$identity.GetBinaryForm($sidBytes,0)
  $script:qaNamespaceScope=[MixelSessionDirectoryFixture+Scope]::new([MixelSessionDirectoryFixture+NativeApi]::new(),$sidBytes)
  $script:qaNamespaceCleanupOkay=$false
  # Publish ownership before Open/Apply; failed queries or partially writing setters
  # are independently drained by the original outer finally.
  $script:qaNamespaceScope.Open()
  $script:qaNamespaceScope.Apply()
  Write-Host ('FIXTURE: isolated owned QA namespace bootstrap before exact original8; mask15 is measured sufficient, minimum unqualified; ' +
      ($script:qaNamespaceScope.facts | ConvertTo-Json -Depth 6 -Compress))
}

function Restore-StandardValidQaNamespace {
  if (-not $script:qaNamespaceScope) { $script:qaNamespaceCleanupOkay=$true;return }
  $restoreFailure=$null;$disposeFailure=$null
  try { $script:qaNamespaceScope.Restore() } catch { $restoreFailure=$_ }
  finally {
    try { $script:qaNamespaceScope.Dispose() } catch { $disposeFailure=$_ }
  }
  $facts=$script:qaNamespaceScope.facts
  $script:qaNamespaceCleanupOkay=($null -eq $restoreFailure -and $null -eq $disposeFailure -and
      $facts.cleanupOkay -and (-not $facts.writeAttempted -or $facts.restorationVerified))
  Write-Host ('OBSERVED: isolated owned QA namespace bootstrap cleanup; ' +
      ([ordered]@{directory=$facts;restoreFailed=($null -ne $restoreFailure);disposeFailed=($null -ne $disposeFailure);
        accountCleanupPermitted=$script:qaNamespaceCleanupOkay;standardEightQualified=$false;
        minimumMaskQualified=$false;actualWin32RouteMeasured=$false;productPermissionChanged=$false} |
        ConvertTo-Json -Depth 8 -Compress))
  if (-not $script:qaNamespaceCleanupOkay) {
    throw 'Owned namespace restoration or handle/module cleanup remains unknown; account/root deletion prohibited.'
  }
}

function New-StandardValidQaScript([string]$Original,[string]$Prefix) {
  $setupAnchor="`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"
  $drainAnchor="`n  Complete-LeaseNtPendingProtocols`n"
  $deleteAnchor="`n  if (`$profileStarted -and `$ownedSid) {`n"
  foreach ($anchor in @($setupAnchor,$drainAnchor,$deleteAnchor)) {
    if ($Original.IndexOf($anchor) -lt 0 -or $Original.IndexOf($anchor) -ne $Original.LastIndexOf($anchor)) {
      throw 'Exact original fixture setup/outer cleanup boundary changed.'
    }
  }
  $setup=$setupAnchor+"  Start-StandardValidQaNamespace`n"
  $drain="`n"+@'
  try { Restore-StandardValidQaNamespace }
  catch { $cleanup.Add('Owned QA namespace restoration/cleanup failed; no false success.') }
  try { Complete-LeaseNtPendingProtocols }
  catch { $cleanup.Add('Exact pending NT protocol drain failed; other owned restoration remains independent.') }
'@ + "`n"
  $deletion="`n"+@'
  if (-not $script:qaNamespaceCleanupOkay) {
    Write-Host 'FAIL: owned QA namespace remains unknown; actual account/profile/root retained. Original Standard failure remains fatal.'
    if ($failure) { throw $failure }
    throw 'Owned QA namespace did not restore/close before account cleanup.'
  }
'@ + "`n" + $deleteAnchor
  $modified=$Original.Replace($setupAnchor,$setup).Replace($drainAnchor,$drain).Replace($deleteAnchor,$deletion)
  $state="`$script:qaNamespaceScope=`$null;`$script:qaNamespaceCleanupOkay=`$true`n"
  return New-SessionDirectoryAbaScript $modified ($Prefix+"`n"+$state)
}

if ($env:GITHUB_ACTIONS -cne 'true' -or $env:GITHUB_EVENT_NAME -cne 'workflow_dispatch' -or
    $env:GITHUB_JOB -cne 'session-directory-access-observation' -or
    $env:GITHUB_REPOSITORY -cne 'Jangopeople/mixel-remote-client' -or
    $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or $env:MIXEL_SESSION_DIRECTORY_ABA -cne '1' -or
    -not $IsWindows -or -not [Environment]::Is64BitProcess) {
  throw 'Explicit optional disposable hosted Windows CI scope required; no host or VM mutation.'
}
$script:abaEvidence=[IO.Path]::GetFullPath($EvidenceDirectory)
if (Test-Path -LiteralPath $script:abaEvidence) { throw 'Diagnostic evidence destination already exists.' }
[void][IO.Directory]::CreateDirectory($script:abaEvidence)
$script:abaDeferredFailure=$null
$pins=ConvertFrom-Json @'
{
  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",
  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",
  "test-standard-qa-session-directory.cs": "310b613adc779fed71c2d2bc689041b6cfd07750dd7b9dff5f786c9c446c51a8",
  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",
  "test-support-launch-windows.ps1": "012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec",
  "test-support-lease-standard-windows.ps1": "a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f",
  "test-support-lease.py": "d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604",
  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",
  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",
  "test-windows-lease-observation.py": "bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e"
}
'@
foreach ($pin in $pins.PSObject.Properties) {
  $path=Join-Path $PSScriptRoot $pin.Name
  if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
      (Get-Item -LiteralPath $path).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
      (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $pin.Value) {
    throw 'Pinned unchanged source family differs; experiment must not start.'
  }
}
$helperPath=Join-Path $PSScriptRoot 'test-session-directory-access.cs'
if (-not ('MixelSessionDirectoryAba' -as [type])) {
  Add-Type -Path $helperPath -CompilerOptions /warnaserror+ -ErrorAction Stop
}
$knownFixturePath=Join-Path $PSScriptRoot 'test-standard-qa-session-directory.cs'
if (-not ('MixelSessionDirectoryFixture' -as [type])) {
  Add-Type -Path $knownFixturePath -CompilerOptions /warnaserror+ -ErrorAction Stop
}
$original=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'test-support-lease-standard-windows.ps1'),[Text.UTF8Encoding]::new($false,$true))
# Copy only the declared top-level functions; omit the wrapper's default execution.
$tokens=$null;$errors=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($PSCommandPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Diagnostic function prefix does not parse.' }
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if ($functions.Count -ne 7) { throw 'Exact diagnostic function family changed.' }
$functionText=($functions | ForEach-Object {$_.Extent.Text}) -join "`n"
$generated=New-StandardValidQaScript $original $functionText
$temporary=Join-Path $PSScriptRoot ('mixel-session-directory-aba-' + [Guid]::NewGuid().ToString('N') + '.ps1')
$owned=$false; $wrapperFailure=$null
try {
  $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);$owned=$true
  try { $bytes=[Text.UTF8Encoding]::new($true).GetPreamble()+[Text.UTF8Encoding]::new($false).GetBytes($generated);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true) }
  finally { $stream.Dispose() }
  $script:abaGeneratedHash=(Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
  . $temporary
} catch { $wrapperFailure=$_; throw }
finally {
  if ($owned) {
    try {
      if (-not $script:abaGeneratedHash) {
        $partial=[IO.File]::ReadAllBytes($temporary)
        if ($partial.Length -gt $bytes.Length) { throw 'Owned temporary write changed.' }
        for ($index=0; $index -lt $partial.Length; $index++) { if ($partial[$index] -ne $bytes[$index]) { throw 'Owned temporary write changed.' } }
        $script:abaGeneratedHash=(Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
      }
      if ((Get-Item -LiteralPath $temporary).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
          (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -cne $script:abaGeneratedHash) {
        throw 'Owned generated script changed; foreign replacement preserved.'
      }
      Remove-Item -LiteralPath $temporary -ErrorAction Stop
    } catch {
      if ($wrapperFailure) { Write-Host 'FAIL: owned generated diagnostic script additionally failed cleanup; original failure retained.' }
      else { throw }
    }
  }
}
