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
  # Preserve quarantine in the original outer finally even if this independent
  # diagnostic fails before the later mask12 registration/lifecycle starts.
  $script:namespaceRestorationKnown=$false
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
  $script:namespaceRestorationKnown=$true
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
  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",
  "test-support-launch-windows.ps1": "c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b",
  "test-support-lease-standard-windows.ps1": "c2897356a81f5410789fcebab4e845639a329da892b41cff4675aa08bc17f15e",
  "test-support-lease.py": "bdba6cc24c0b247de85a3bcda79744dd932f00bb6e97d038eba14a7e43d1db64",
  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",
  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",
  "test-windows-lease-observation.py": "53303228566698b88eb4f8207bba6c3255f560d15824d293b5f5b8addce45c45",
  "rust_toolchain.py": "2156c1452df874462a5e2a569f25a800a339cc2cb92f5732340aa12a633afa3c",
  "windows-standard-namespace-readonly.rs": "23e5fee444459cb68ed7c2bb2ac6c8696583697ce8874019101b16659334b705",
  "windows-standard-namespace-access.cs": "7f04727ae8161b8e4da1d2e4a74a0324549996da5af52a08c14538ccf53b2589",
  "windows-standard-namespace-diagnostics.cs": "980178cba4736f260d1365d3f19b3371158efcd0856d75fb12c036b0a093aad8"
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
$original=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'test-support-lease-standard-windows.ps1'),[Text.UTF8Encoding]::new($false,$true))
# Copy only the declared top-level functions; omit the wrapper's default execution.
$tokens=$null;$errors=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($PSCommandPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Diagnostic function prefix does not parse.' }
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if ($functions.Count -ne 4) { throw 'Exact diagnostic function family changed.' }
$functionText=($functions | ForEach-Object {$_.Extent.Text}) -join "`n"
$generated=New-SessionDirectoryAbaScript $original $functionText
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
