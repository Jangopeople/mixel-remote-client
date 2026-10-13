param([ValidatePattern('\Agui-connection-proof(?:-[a-z0-9-]+)?\z')][string]$EvidenceDirectory='gui-connection-proof')
$ErrorActionPreference='Stop'

function Read-GuiConnectionRecord([string]$Text,[uint32]$ExpectedPid,[string]$ExpectedMode) {
  if($ExpectedPid -eq 0 -or $ExpectedMode -cnotin @('A-before','B','A-after') -or $Text.Length -gt 16384) { throw 'Observer attribution/input bound failed.' }
  $record=$Text|ConvertFrom-Json
  $names=@($record.PSObject.Properties.Name|Sort-Object)
  if(($names -join ',') -cne 'customerAppQualified,finalQualified,fullEightQualified,gui,mode,observations,pid,schema,standardQualified' -or
     $record.schema -isnot [string] -or $record.schema -cne 'mixel-gui-connection-observation-v1' -or
     $record.mode -isnot [string] -or $record.mode -cne $ExpectedMode -or
     ($record.pid -isnot [int] -and $record.pid -isnot [long]) -or $record.pid -ne $ExpectedPid -or
     $record.observations -isnot [array]) { throw 'Observer record has invalid schema or retained PID.' }
  foreach($flag in @('standardQualified','fullEightQualified','customerAppQualified','finalQualified')) {
    if($record.$flag -isnot [bool] -or $record.$flag) { throw 'Observation cannot confer qualification.' }
  }
  $stages=@(if($ExpectedMode -ceq 'B') { 'before','after-load','after-call' } else { 'before' })
  if($record.observations.Count -ne $stages.Count) { throw 'Observer stages are incomplete.' }
  for($index=0;$index -lt $stages.Count;$index++) {
    $observation=$record.observations[$index]
    $fields=@($observation.PSObject.Properties.Name|Sort-Object)
    if(($fields -join ',') -cne 'attestation,ownerFailed,pid,rawOpenError,requiresClick,sourceStatus,stage' -or
       $observation.stage -isnot [string] -or $observation.stage -cne $stages[$index]) { throw 'Observer stage order/schema changed.' }
    $safe=[pscustomobject]@{pid=$observation.pid;sourceStatus=$observation.sourceStatus;rawOpenError=$observation.rawOpenError;attestation=$observation.attestation;requiresClick=$observation.requiresClick;ownerFailed=$observation.ownerFailed}
    Assert-StandardLeaseSafeResult $safe $ExpectedPid
    if($safe.ownerFailed -or -not $safe.sourceStatus.StartsWith('ownerFailed=false;ownerError=none;') -or
       -not $safe.sourceStatus.EndsWith(';memoryActive=false')) { throw 'Read-only observer unexpectedly changed owner or memory state.' }
  }
  if($ExpectedMode -cne 'B') {
    if($null -ne $record.gui) { throw 'Untouched A child contains a GUI initialization observation.' }
  } else {
    $gui=$record.gui
    $fields=@($gui.PSObject.Properties.Name|Sort-Object)
    if(($fields -join ',') -cne 'callAttempted,closeError,desktop,desktopWindowPresent,libraryLoaded,loadError,moduleClosed,nameResolveError,resolveError,station') { throw 'GUI observation schema changed.' }
    foreach($flag in @('callAttempted','desktopWindowPresent','libraryLoaded','moduleClosed')) {
      if($gui.$flag -isnot [bool]) { throw 'GUI observation boolean type changed.' }
    }
    foreach($field in @('loadError','resolveError','nameResolveError','closeError')) {
      $value=$gui.$field
      if(($value -isnot [int] -and $value -isnot [long]) -or $value -lt 0 -or $value -gt [uint32]::MaxValue) { throw 'GUI native error is not a bounded integer.' }
    }
    if(($gui.libraryLoaded -and $gui.loadError -ne 0) -or ($gui.moduleClosed -and $gui.closeError -ne 0) -or
       (-not $gui.libraryLoaded -and ($gui.callAttempted -or -not $gui.moduleClosed)) -or
       (-not $gui.callAttempted -and $gui.desktopWindowPresent)) { throw 'GUI observation contains inconsistent library/call/close fields.' }
    foreach($field in @('station','desktop')) {
      $name=$gui.$field
      if($null -eq $name) { continue }
      $names=@($name.PSObject.Properties.Name|Sort-Object)
      if(($names -join ',') -cne 'bounded,error,length,name' -or $name.bounded -isnot [bool] -or
         ($name.error -isnot [int] -and $name.error -isnot [long]) -or $name.error -lt 0 -or $name.error -gt [uint32]::MaxValue -or
         ($name.length -isnot [int] -and $name.length -isnot [long]) -or $name.length -lt 0 -or $name.length -gt [uint32]::MaxValue) { throw 'GUI object name result schema/type changed.' }
      if($name.bounded) {
        if($name.name -isnot [string] -or $name.name.Length -lt 1 -or $name.name.Length -gt 255 -or
           $name.length -ne ($name.name.Length+1)*2 -or $name.error -ne 0) { throw 'GUI object name bound changed.' }
      } elseif($null -ne $name.name) { throw 'Unbounded GUI object name was exported.' }
    }
  }
  return $record
}

function Invoke-GuiConnectionCleanup([hashtable]$Actions) {
  $errors=[Collections.Generic.List[string]]::new()
  foreach($name in @('stop','close','desktop','hash','profile','account','files','secrets')) {
    try {
      if(-not $Actions.ContainsKey($name) -or $Actions[$name] -isnot [scriptblock]) { throw 'Owned cleanup action is missing.' }
      & $Actions[$name]
    } catch { $errors.Add('Owned GUI observation cleanup failed: '+$name+'.') }
  }
  return ,$errors.ToArray()
}

# Native entry is isolated and opt-in. Pure source tests extract only functions.
if(-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or $env:GITHUB_JOB -cne 'standard-gui-connection-observation' -or $env:MIXEL_GUI_CONNECTION_OBSERVATION -cne '1') {
  throw 'GUI-connection native observation requires the explicit isolated Windows Actions job.'
}
$evidenceRoot=Join-Path (Get-Location).Path $EvidenceDirectory
if(Test-Path -LiteralPath $evidenceRoot) { throw 'GUI observation evidence directory already exists.' }
New-Item -ItemType Directory -Path $evidenceRoot|Out-Null
$fixtureRoot=Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-standard-lease-'+[Guid]::NewGuid().ToString('N'))
$eventName='Global\Mixel-Remote-Standard-Lease-'+[Guid]::NewGuid().ToString('N')
$ownedUser=$null; $ownedSid=$null; $ownedPassword=$null; $random=$null
$desktopAccess=$null; $profileStarted=$false; $rootCreated=$false; $originalDesktop=$null
$failure=$null; $stage='prepare'; $records=[Collections.Generic.List[object]]::new()
$cleanupErrors=@(); $fixtureLoaded=$false
try {
  New-Item -ItemType Directory -Path $fixtureRoot|Out-Null
  $rootCreated=$true
  python (Join-Path $PSScriptRoot 'build-standard-gui-connection-fixture.py') --workspace $fixtureRoot --evidence $evidenceRoot --event $eventName
  if($LASTEXITCODE -ne 0) { throw 'Separate source-bound GUI observer compilation failed.' }
  Add-Type -Path (Join-Path $evidenceRoot 'normal-fixture.cs')
  $fixtureLoaded=$true
  . (Join-Path $evidenceRoot 'normal-result-parser.ps1')
  $originalDesktop=[MixelOrdinaryTokenFixture]::DesktopAclHashes()
  $stage='owned normal account bootstrap'
  . (Join-Path $evidenceRoot 'normal-bootstrap.ps1')
  if(-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) { throw 'Exact owned normal account SID was not recorded.' }
  $acl=Get-Acl $fixtureRoot
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($ownedSid),'Modify','ContainerInherit,ObjectInherit','None','Allow'))
  Set-Acl -Path $fixtureRoot -AclObject $acl
  $desktopAccess=[MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
  $executable=Join-Path $fixtureRoot 'gui-connection.exe'
  $session=[uint32](Get-Process -Id $PID).SessionId
  foreach($mode in @('A-before','B','A-after')) {
    $stage=$mode
    $resultPath=Join-Path $fixtureRoot 'gui-connection-result.json'
    if(Test-Path -LiteralPath $resultPath) { Remove-Item -LiteralPath $resultPath }
    [IO.File]::WriteAllText((Join-Path $fixtureRoot 'gui-connection-mode.txt'),$mode,[Text.UTF8Encoding]::new($false))
    $profileStarted=$true
    $childPid=[MixelOrdinaryTokenFixture]::StartStandardUser($executable,$ownedUser,$ownedPassword)
    [MixelOrdinaryTokenFixture]::AssertOwnedContext($childPid,$ownedSid,$session)
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while(-not (Test-Path -LiteralPath $resultPath -PathType Leaf) -and [DateTime]::UtcNow -lt $deadline) {
      if([MixelOrdinaryTokenFixture]::StartedStatus($childPid).StartsWith('exited:')) { break }
      Start-Sleep -Milliseconds 50
    }
    if(-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw 'Owned GUI observer did not produce its bounded result.' }
    if((Get-Item -LiteralPath $resultPath).Length -gt 16384) { throw 'Owned GUI result exceeds its byte bound.' }
    # Preserve exact child bytes before structural validation or cleanup failure.
    Copy-Item -LiteralPath $resultPath -Destination (Join-Path $evidenceRoot ($mode+'.json'))
    $record=Read-GuiConnectionRecord ([IO.File]::ReadAllText($resultPath)) $childPid $mode
    $records.Add($record)
    Write-Host ('OBSERVED: GUI connection '+$mode+'; exact owned PID='+$childPid+'; '+($record|ConvertTo-Json -Depth 8 -Compress))
    [IO.File]::WriteAllText((Join-Path $fixtureRoot ('fixture-release-'+$childPid)),'verified',[Text.UTF8Encoding]::new($false))
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while([MixelOrdinaryTokenFixture]::StartedStatus($childPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
    if([MixelOrdinaryTokenFixture]::StartedStatus($childPid) -cne 'exited:0x00000000') { throw 'Exact owned GUI observer did not acknowledge successful exit.' }
    [MixelOrdinaryTokenFixture]::CloseStartedObservations()
    if($mode -ceq 'B' -and -not $record.gui.moduleClosed) { throw 'Owned USER32 module close failed; exact native observation retained.' }
  }
  Write-Host 'PASS: three bounded GUI-connection observations completed and acknowledged; literal native outcomes retained; no consent or application qualification.'
} catch {
  $failure='GUI observation failed at '+$stage+'; type='+$_.Exception.GetType().Name+'.'
  Write-Host ('FAIL: '+$failure)
} finally {
  $cleanupErrors=Invoke-GuiConnectionCleanup @{
    stop={ if($fixtureLoaded) { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } }
    close={ if($fixtureLoaded) { [MixelOrdinaryTokenFixture]::CloseStartedObservations() } }
    desktop={ if($desktopAccess) { $desktopAccess.Dispose() } }
    hash={ if($null -ne $originalDesktop -and [MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $originalDesktop) { throw 'Original desktop ACL hashes changed.' } }
    profile={
      if($profileStarted -and $ownedSid) {
        $removed=$false
        for($attempt=0;$attempt -lt 5;$attempt++) {
          try { [MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid); $removed=$true; break }
          catch { if($attempt -lt 4) { Start-Sleep -Seconds 2 } }
        }
        if(-not $removed) { throw 'Owned profile cleanup failed.' }
      }
    }
    account={ if($ownedSid) { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)); if(Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) -ErrorAction SilentlyContinue) { throw 'Owned SID remains.' } } }
    files={ if($rootCreated -and (Test-Path -LiteralPath $fixtureRoot)) { Remove-Item -LiteralPath $fixtureRoot -Recurse -Force } }
    secrets={ $script:ownedPassword=$null; $script:random=$null }
  }
  $summary=[ordered]@{schema='mixel-gui-connection-observation-summary-v1';completedObservations=$records.Count;observations=@($records.ToArray());ownedCleanupPassed=($cleanupErrors.Count -eq 0);cleanupErrors=@($cleanupErrors);failure=$failure;standardQualified=$false;fullEightQualified=$false;customerAppQualified=$false;finalQualified=$false}
  $summary|ConvertTo-Json -Depth 10|Set-Content -LiteralPath (Join-Path $evidenceRoot 'summary.json') -Encoding utf8
}
if($failure -or $cleanupErrors.Count -gt 0) { throw 'GUI connection observation or owned cleanup failed; preserved proof remains unqualified.' }
Write-Host 'PASS: only exact owned process handles, normal desktop/profile/SID/files cleaned; original desktop ACL hashes restored; all qualification flags remain false.'
