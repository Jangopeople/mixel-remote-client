$ErrorActionPreference='Stop'
# Pure parser, generated C# compilation, and cleanup fault controls. No native
# Windows calls, account creation, ACL changes, or GUI observation are executed.
function Import-ExactSourceFunction([string]$Source,[string]$Name) {
  $tokens=$null; $errors=$null
  $ast=[Management.Automation.Language.Parser]::ParseInput($Source,[ref]$tokens,[ref]$errors)
  if($errors.Count) { throw 'GUI observation source does not parse.' }
  $functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name},$true))
  if($functions.Count -ne 1) { throw 'Exact GUI observation function is missing or duplicated.' }
  return [scriptblock]::Create($functions[0].Extent.Text)
}
function Assert-Rejected([scriptblock]$Action,[string]$Name) {
  $rejected=$false
  try { & $Action|Out-Null } catch { $rejected=$true }
  if(-not $rejected) { throw ('GUI observation failed to reject synthetic '+$Name+'.') }
}
function New-SyntheticObservation([string]$Stage,[uint32]$NativeError=5) {
  $state=if($NativeError -eq 2) { 'kernel=absent;probeError=none' } else { 'kernel=unavailable;probeError='+$NativeError }
  return [pscustomobject]@{stage=$Stage;pid=4242;sourceStatus=('ownerFailed=false;ownerError=none;'+$state+';memoryActive=false');rawOpenError=$NativeError;attestation=$(if($NativeError -eq 2){''}else{'guard-unavailable'});requiresClick=$true;ownerFailed=$false}
}
function New-SyntheticRecord([string]$Mode='B') {
  $record=[pscustomobject]@{schema='mixel-gui-connection-observation-v1';mode=$Mode;pid=4242;observations=@(New-SyntheticObservation 'before');gui=$null;standardQualified=$false;fullEightQualified=$false;customerAppQualified=$false;finalQualified=$false}
  if($Mode -ceq 'B') {
    $record.observations=@((New-SyntheticObservation 'before'),(New-SyntheticObservation 'after-load'),(New-SyntheticObservation 'after-call' 2))
    $record.gui=[pscustomobject]@{libraryLoaded=$true;loadError=0;callAttempted=$true;resolveError=0;desktopWindowPresent=$true;nameResolveError=0;station=[pscustomobject]@{name='WinSta0';error=0;length=16;bounded=$true};desktop=[pscustomobject]@{name='Default';error=0;length=16;bounded=$true};moduleClosed=$true;closeError=0}
  }
  return $record
}
$observer=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'observe-standard-gui-connection-windows.ps1') -Raw
. (Import-ExactSourceFunction $observer 'Read-GuiConnectionRecord')
. (Import-ExactSourceFunction $observer 'Invoke-GuiConnectionCleanup')
$standard=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'test-support-lease-standard-windows.ps1') -Raw
. (Import-ExactSourceFunction $standard 'Assert-StandardLeaseSafeResult')
$temporary=Join-Path ([IO.Path]::GetTempPath()) ('mixel-gui-source-'+[Guid]::NewGuid().ToString('N'))
try {
  $workspace=Join-Path $temporary 'workspace'; $evidence=Join-Path $temporary 'evidence'
  New-Item -ItemType Directory -Path $workspace,$evidence|Out-Null
  $python=if(Get-Command python3 -ErrorAction SilentlyContinue){'python3'}else{'python'}
  & $python (Join-Path $PSScriptRoot 'build-standard-gui-connection-fixture.py') --workspace $workspace --evidence $evidence --event ('Global\Mixel-Remote-Standard-Lease-'+('c'*32)) --source-only
  if($LASTEXITCODE -ne 0) { throw 'Pure generated fixture preparation failed.' }
  Add-Type -Path (Join-Path $evidence 'normal-fixture.cs')
  if([MixelOrdinaryTokenFixture].GetMethod('AssertOwnedContext').GetParameters().Count -ne 3 -or
     [MixelOrdinaryTokenFixture].GetMethods().Name -contains 'AttachOwnedDebugger' -or
     (Get-ChildItem -LiteralPath $workspace).Count -ne 0 -or (Test-Path -LiteralPath (Join-Path $evidence 'source-manifest.json'))) { throw 'Pure source compilation executed or exposed an unrelated native path.' }
  Write-Host 'PASS: exact extracted normal launcher, retained ownership and documented context C# compile; pure preparation creates no binary or runtime proof.'

  foreach($mode in @('A-before','B','A-after')) {
    $record=New-SyntheticRecord $mode
    $read=Read-GuiConnectionRecord ($record|ConvertTo-Json -Depth 8 -Compress) 4242 $mode
    if($read.observations[0].rawOpenError -ne 5 -or $read.standardQualified -or $read.fullEightQualified -or $read.customerAppQualified -or $read.finalQualified) { throw 'Synthetic native outcome or qualification flag changed.' }
  }
  foreach($fault in @('load','resolve','close','name')) {
    $record=New-SyntheticRecord
    switch($fault) {
      load { $record.gui.libraryLoaded=$false; $record.gui.loadError=126; $record.gui.callAttempted=$false; $record.gui.desktopWindowPresent=$false; $record.gui.station=$null; $record.gui.desktop=$null }
      resolve { $record.gui.callAttempted=$false; $record.gui.resolveError=127; $record.gui.desktopWindowPresent=$false }
      close { $record.gui.moduleClosed=$false; $record.gui.closeError=6 }
      name { $record.gui.station=[pscustomobject]@{name=$null;error=122;length=1024;bounded=$false} }
    }
    $read=Read-GuiConnectionRecord ($record|ConvertTo-Json -Depth 8 -Compress) 4242 'B'
    if(($read|ConvertTo-Json -Depth 8 -Compress) -cne ($record|ConvertTo-Json -Depth 8 -Compress)) { throw 'Literal synthetic documented-API fault was normalized.' }
  }
  Write-Host 'PASS: synthetic A/B/A and load126/resolve127/close6/name122 results retain exact native values; all qualification flags stay false.'

  $mutations=[ordered]@{
    pid={param($record) $record.pid=4243}
    mode={param($record) $record.mode='A-before'}
    schema={param($record) $record.schema='other'}
    extraField={param($record) $record|Add-Member NoteProperty extra $true}
    stageOrder={param($record) $record.observations[1].stage='after-call'}
    incompleteStage={param($record) $record.observations=@($record.observations[0])}
    owner={param($record) $record.observations[0].ownerFailed=$true}
    memory={param($record) $record.observations[0].sourceStatus=$record.observations[0].sourceStatus.Replace('memoryActive=false','memoryActive=true')}
    openErrorWidth={param($record) $record.observations[0].rawOpenError=4294967296L}
    openErrorType={param($record) $record.observations[0].rawOpenError='5'}
    guiErrorWidth={param($record) $record.gui.loadError=4294967296L}
    guiBoolean={param($record) $record.gui.callAttempted='true'}
    nameBound={param($record) $record.gui.station.length=512}
    nameExport={param($record) $record.gui.station.bounded=$false}
    closeConsistency={param($record) $record.gui.closeError=6}
    callConsistency={param($record) $record.gui.callAttempted=$false}
    stagePid={param($record) $record.observations[0].pid=4243}
    standardQualified={param($record) $record.standardQualified=$true}
    fullEightQualified={param($record) $record.fullEightQualified=$true}
    customerAppQualified={param($record) $record.customerAppQualified=$true}
    finalQualified={param($record) $record.finalQualified=$true}
  }
  foreach($entry in $mutations.GetEnumerator()) {
    $record=New-SyntheticRecord
    & $entry.Value $record
    Assert-Rejected {Read-GuiConnectionRecord ($record|ConvertTo-Json -Depth 8 -Compress) 4242 'B'} $entry.Key
  }
  $record=New-SyntheticRecord 'A-before';$record.gui=(New-SyntheticRecord).gui
  Assert-Rejected {Read-GuiConnectionRecord ($record|ConvertTo-Json -Depth 8 -Compress) 4242 'A-before'} 'A child GUI mutation'
  Assert-Rejected {Read-GuiConnectionRecord (' '*16385) 4242 'B'} 'record size'
  Assert-Rejected {Read-GuiConnectionRecord '{' 4242 'B'} 'malformed JSON'
  Write-Host ('PASS: '+($mutations.Count+3)+' synthetic attribution, shape, stage, native-width, ownership and qualification mutations are rejected by the actual observer reader.')

  $order=@('stop','close','desktop','hash','profile','account','files','secrets')
  foreach($fault in @('none')+$order+@('multiple','missing')) {
    $seen=[Collections.Generic.List[string]]::new(); $state=@{secret='synthetic'}
    $actions=@{}
    foreach($name in $order) {
      $actionName=$name; $actionFault=$fault
      $actions[$name]={
        $seen.Add($actionName)
        if($actionName -ceq 'secrets') { $state.secret=$null }
        if($actionName -ceq $actionFault -or ($actionFault -ceq 'multiple' -and $actionName -cin @('stop','hash','files'))) { throw 'Synthetic owned cleanup fault.' }
      }.GetNewClosure()
    }
    if($fault -ceq 'missing') { $actions.Remove('desktop') }
    $errors=Invoke-GuiConnectionCleanup $actions
    $expected=if($fault -ceq 'missing') { @($order|Where-Object{$_ -cne 'desktop'}) } else { $order }
    if(($seen -join ',') -cne ($expected -join ',') -or $null -ne $state.secret) { throw 'Actual cleanup stopped early, changed order, or skipped secret clearing.' }
    $count=if($fault -ceq 'none'){0}elseif($fault -ceq 'multiple'){3}else{1}
    if($errors.Count -ne $count) { throw 'Actual cleanup swallowed or duplicated its failure.' }
  }
  if(-not $observer.Contains("if(`$failure -or `$cleanupErrors.Count -gt 0) { throw") -or
     -not $observer.Contains('standardQualified=$false;fullEightQualified=$false;customerAppQualified=$false;finalQualified=$false') -or
     -not $observer.Contains('Copy-Item -LiteralPath $resultPath') -or
     $observer.IndexOf('Copy-Item -LiteralPath $resultPath') -gt $observer.IndexOf('$record=Read-GuiConnectionRecord')) { throw 'Native parent stopped preserving results or propagating cleanup failure.' }
  Write-Host 'PASS: actual cleanup executes all eight actions in order under each fault, concurrent faults and a missing action; secret clearing still runs and failures remain fatal.'
} finally {
  if(Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
}
Write-Host 'PASS: GUI-connection source controls complete; zero Windows API, GUI, account or ACL execution; synthetic controls confer no qualification.'
