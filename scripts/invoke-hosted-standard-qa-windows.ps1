# Source-only hosted CI owned-account fixture. No application permission fallback.
param([string]$EvidenceDirectory)
$ErrorActionPreference='Stop'
if (-not $IsWindows -or -not [Environment]::Is64BitProcess -or $env:GITHUB_ACTIONS -cne 'true' -or
    $env:GITHUB_REPOSITORY -cne 'Jangopeople/mixel-remote-client' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
    $env:MIXEL_HOSTED_OWNED_QA_NAMESPACE -cne '1') {
  throw 'Explicit hosted Windows owned QA source fixture required; no local or VM permission mutation.'
}

function Read-PinnedHostedSource([string]$Path,[string]$Expected) {
  $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if ($item.PSIsContainer -or $item.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Pinned fixture source must be a regular nonreparse file.' }
  $raw=[IO.File]::ReadAllBytes($Path)
  $text=[Text.UTF8Encoding]::new($false,$true).GetString($raw)
  if ($text.Contains("`r")) {
    $withoutPairs=$text.Replace("`r`n",'')
    if ($withoutPairs.Contains("`r") -or $withoutPairs.Contains("`n")) { throw 'Mixed fixture source newlines refused.' }
    $text=$text.Replace("`r`n","`n")
  }
  $bytes=[Text.UTF8Encoding]::new($false,$true).GetBytes($text)
  $hash=[Security.Cryptography.SHA256]::Create()
  try { $actual=([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
  finally { $hash.Dispose() }
  if ($actual -cne $Expected) { throw 'Pinned original fixture source changed.' }
  return $text
}
function Start-HostedStandardQaNamespace {
  if ($script:qaNamespaceRegistered -or $ownedUser -cnotmatch '\Amixellease[0-9a-f]{10}\z' -or
      (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser -or
      $runnerFacts.unavailableKind -or $freshFacts.unavailableKind -or
      $runnerFacts.integrityRid -ne 12288 -or $freshFacts.integrityRid -ne 8192 -or
      $runnerFacts.hasRestrictions -or $runnerFacts.isAppContainer -or
      $freshFacts.hasRestrictions -or $freshFacts.isAppContainer -or
      $runnerFacts.sessionId -ne $freshFacts.sessionId -or
      $runnerFacts.sessionId -ne (Get-Process -Id $PID).SessionId) {
    throw 'Exact disposable owned QA identity and High/Medium same-session source facts required.'
  }
  if (-not [MixelHostedQaNamespace]::HostedScopeAllowed($IsWindows,[Environment]::Is64BitProcess,$env:GITHUB_ACTIONS,
      $env:GITHUB_REPOSITORY,$env:RUNNER_ENVIRONMENT,$env:GITHUB_JOB,$env:GITHUB_EVENT_NAME,$env:GITHUB_REF,
      $env:GITHUB_WORKFLOW_REF,$env:MIXEL_HOSTED_OWNED_QA_NAMESPACE,'StandardSource')) {
    throw 'Source fixture workflow/job/event/ref differs; no grant permitted.'
  }
  # Register only. The original script observes A before its sole mask12 grant.
  $script:qaNamespaceRegistered=$true
  $script:qaNamespaceCleanupOkay=$false
  Write-Host 'FIXTURE: hosted identity and workflow validated before original A; original script owns the single mask12 grant, exact8 and restoration probe.'
}

function Restore-HostedStandardQaNamespace {
  if (-not $script:qaNamespaceRegistered) { $script:qaNamespaceCleanupOkay=$true;return }
  # The original finally restores independently of child termination. Observe
  # its managed result here; never create a second directory grant or handle.
  $facts=$script:namespaceFacts
  $factsKnown=($null -eq $facts -or ($facts.cleanupOkay -and (-not $facts.writeAttempted -or $facts.restorationVerified)))
  $script:qaNamespaceCleanupOkay=([bool]$script:namespaceRestorationKnown -and $factsKnown)
  Write-Host ('OBSERVED: isolated owned QA namespace bootstrap cleanup; ' +
      ([ordered]@{directory=$script:namespaceFacts;namespaceRestorationKnown=$script:namespaceRestorationKnown;
        accountCleanupPermitted=$script:qaNamespaceCleanupOkay;standardEightQualified=$false;
        minimumMaskQualified=$false;actualWin32RouteMeasured=$false;productPermissionChanged=$false} |
        ConvertTo-Json -Depth 8 -Compress))
  if (-not $script:qaNamespaceCleanupOkay) {
    throw 'Owned namespace restoration or handle/module cleanup remains unknown; account/root deletion prohibited.'
  }
}

function New-HostedStandardQaScript([string]$Original,[string]$Prefix) {
  $setupAnchor="`n  Invoke-OptionalCallerLinkedLeaseSubsystemObservations`n"
  $drainAnchor="`n  try { Complete-LeaseNtPendingProtocols } catch { `$cleanup.Add('Exact pending NT protocol drain failed.') }`n"
  $deleteAnchor="`n  if (`$ownedCleanupPermitted -and `$profileStarted -and `$ownedSid) {`n"
  foreach ($anchor in @($setupAnchor,$drainAnchor,$deleteAnchor)) {
    if ($Original.IndexOf($anchor) -lt 0 -or $Original.IndexOf($anchor) -ne $Original.LastIndexOf($anchor)) {
      throw 'Exact original fixture setup/outer cleanup boundary changed.'
    }
  }
  $stopAnchor="  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses(); `$ownedProcessesExited=`$true } catch { `$cleanup.Add('Exact owned standard lease process cleanup failed.') }"
  if ($Original.IndexOf($stopAnchor) -lt 0 -or $Original.IndexOf($stopAnchor) -ne $Original.LastIndexOf($stopAnchor)) { throw 'Exact original owned process cleanup boundary changed.' }
  $setup=$setupAnchor+"  Start-HostedStandardQaNamespace`n"
  $drain="`n"+@'
  try { Complete-LeaseNtPendingProtocols }
  catch { $cleanup.Add('Exact pending NT protocol drain failed; other owned restoration remains independent.') }
'@ + "`n"
  $deletion="`n"+@'
  $script:qaProcessesKnownStopped=[bool]$ownedProcessesExited
  try { Restore-HostedStandardQaNamespace }
  catch { $cleanup.Add('Owned QA namespace restoration/cleanup failed; no false success.') }
  if (-not $script:qaNamespaceCleanupOkay -or -not $script:qaProcessesKnownStopped -or
      $script:ntPendingProtocols.Count -gt 0 -or $script:ntPreservedRoots.Count -gt 0) {
    $ownedCleanupPermitted=$false
    $cleanup.Add('Owned QA namespace did not restore/close before account cleanup.')
    try { Write-Host 'FAIL: owned QA namespace remains unknown; actual account/profile/root retained. Original Standard failure remains fatal.' } catch {}
  }
'@ + "`n" + $deleteAnchor
  $modified=$Original.Replace($setupAnchor,$setup).Replace($drainAnchor,$drain).Replace($deleteAnchor,$deletion)
  $state="`$script:qaNamespaceRegistered=`$false;`$script:qaNamespaceCleanupOkay=`$true;`$script:qaProcessesKnownStopped=`$false`n"
  # Keep the pinned UTF-8 signature at the standalone source boundary only.
  # A signature embedded after function declarations is a PowerShell token.
  return $Prefix+"`n"+$state+$modified.TrimStart([char]0xFEFF)
}

$pins=ConvertFrom-Json @'
{
  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",
  "test-hosted-qa-session-directory.cs": "b7f7886e2e6de9f7d76ef371473a436d8845b1396c12b63517331894d4611d4e",
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
$canonical=[Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
foreach ($pin in $pins.PSObject.Properties) { $canonical.Add($pin.Name,(Read-PinnedHostedSource (Join-Path $PSScriptRoot $pin.Name) $pin.Value)) }
if (-not ('MixelHostedQaNamespace' -as [type])) { Add-Type -TypeDefinition $canonical['test-hosted-qa-session-directory.cs'] -CompilerOptions /warnaserror+ -ErrorAction Stop }
if (-not [MixelHostedQaNamespace]::HostedScopeAllowed($IsWindows,[Environment]::Is64BitProcess,$env:GITHUB_ACTIONS,
    $env:GITHUB_REPOSITORY,$env:RUNNER_ENVIRONMENT,$env:GITHUB_JOB,$env:GITHUB_EVENT_NAME,$env:GITHUB_REF,
    $env:GITHUB_WORKFLOW_REF,$env:MIXEL_HOSTED_OWNED_QA_NAMESPACE,'StandardSource')) { throw 'Source fixture workflow/job/event/ref differs; no grant permitted.' }
if (-not $env:RUNNER_TEMP) { throw 'Owned hosted runner temporary parent required.' }
$parent=[IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd([IO.Path]::DirectorySeparatorChar)
if (-not $EvidenceDirectory) { $EvidenceDirectory=Join-Path $parent ('mixel-hosted-standard-qa-'+[Guid]::NewGuid().ToString('N')) }
$destination=[IO.Path]::GetFullPath($EvidenceDirectory)
if (-not $destination.StartsWith($parent+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $destination)) { throw 'Fresh evidence destination inside exact runner temporary parent required.' }
[void][IO.Directory]::CreateDirectory($destination)
$original=$canonical['test-support-lease-standard-windows.ps1']
$tokens=$null;$errors=$null;$tree=[Management.Automation.Language.Parser]::ParseFile($PSCommandPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Hosted fixture prefix parser refused.' }
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if ($functions.Count -ne 4) { throw 'Exact four hosted fixture functions changed.' }
$prefix=($functions | ForEach-Object {$_.Extent.Text}) -join "`n"
$generated=New-HostedStandardQaScript $original $prefix
$temporary=Join-Path $PSScriptRoot ('mixel-hosted-standard-qa-'+[Guid]::NewGuid().ToString('N')+'.ps1')
$owned=$false;$wrapperFailure=$null;$script:hostedGeneratedHash=$null
try {
  $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);$owned=$true
  try { $bytes=[Text.UTF8Encoding]::new($true).GetPreamble()+[Text.UTF8Encoding]::new($false).GetBytes($generated);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true) }
  finally { $stream.Dispose() }
  $script:hostedGeneratedHash=(Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
  . $temporary
  Write-Host 'PASS: unchanged original Standard8 and ninth restored-A probe passed in source-only hosted fixture with a single mask12 scope; namespace restored before account cleanup.'
} catch { $wrapperFailure=$_;throw }
finally {
  $reportFailure=$null;$generatedCleanupFailure=$null
  if ($script:qaNamespaceRegistered) {
    $report=[ordered]@{schema=1;scope='HostedOwnedStandardSourceFixture';directory=$script:namespaceFacts;
      namespaceCleanupKnown=$script:qaNamespaceCleanupOkay;ownedProcessesKnownStopped=$script:qaProcessesKnownStopped;
      nativeOriginalFailure=($null -ne $wrapperFailure);productPermissionChanged=$false;minimumMaskQualified=$false;actualWin32RouteMeasured=$false}
    try {
      $reportBytes=[Text.UTF8Encoding]::new($false).GetBytes(($report | ConvertTo-Json -Depth 8)+"`n")
      $reportStream=[IO.File]::Open((Join-Path $destination 'fixture-namespace.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
      try { $reportStream.Write($reportBytes,0,$reportBytes.Length);$reportStream.Flush($true) } finally { $reportStream.Dispose() }
    } catch { $reportFailure=$_ }
  }
  if ($owned) {
    try {
      if ((Get-Item -LiteralPath $temporary).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Foreign generated source replacement preserved.' }
      if (-not $script:hostedGeneratedHash) {
        $partial=[IO.File]::ReadAllBytes($temporary)
        if ($partial.Length -gt $bytes.Length) { throw 'Owned partial generated source changed.' }
        for ($i=0;$i -lt $partial.Length;$i++) { if ($partial[$i] -ne $bytes[$i]) { throw 'Owned partial generated source changed.' } }
        $script:hostedGeneratedHash=(Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
      }
      if ((Get-Item -LiteralPath $temporary).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
          (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -cne $script:hostedGeneratedHash) { throw 'Foreign generated source replacement preserved.' }
      Remove-Item -LiteralPath $temporary -ErrorAction Stop
    } catch { $generatedCleanupFailure=$_ }
  }
  if ($reportFailure -or $generatedCleanupFailure) {
    if ($wrapperFailure) { Write-Host 'FAIL: owned source report or temporary cleanup additionally failed; original native failure retained.' }
    elseif ($reportFailure) { throw $reportFailure }
    else { throw $generatedCleanupFailure }
  }
}
