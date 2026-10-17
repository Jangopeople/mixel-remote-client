# Pure ownership/source controls. Never start a native child or call Windows APIs.
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot 'observe-current-caller-four.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count) { throw 'Current-caller source does not parse.' }
$functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true))
foreach($function in $functions) { . ([scriptblock]::Create($function.Extent.Text)) }
function Assert-Review($Value,[string]$Message) { if(-not $Value) { throw $Message } }
function Refuse-Review([scriptblock]$Call) {
  $caught=$false
  try { & $Call } catch { $caught=$true }
  if(-not $caught) { throw 'Actual malformed ownership/source path was accepted.' }
}
function Reset-Review {
  $script:CurrentCallerOwned=[Collections.Generic.List[object]]::new()
  $script:CurrentCallerObserved=[Collections.Generic.List[object]]::new()
  $script:ReviewOperations=[Collections.Generic.List[string]]::new()
  $script:ReviewNext=$null
  $script:ReviewEvent=$false
  $script:ReviewSerial=400
  $script:ReviewSourceMode=$true
  $script:ReviewForcedError=0
}
function New-ReviewProcess([int]$Number) {
  $value=[pscustomobject]@{Id=$Number;HasExited=$false;ExitCode=0;FailStart=$false;ThrowStart=$false;FailKill=$false;FailWait=$false;FailDispose=$false;SourceMode=$script:ReviewSourceMode;WasOwner=$false}
  $value|Add-Member ScriptMethod Start {
    $script:ReviewOperations.Add('start:'+ $this.Id)
    if($this.ThrowStart) { throw 'Synthetic start failure.' }
    if($this.FailStart) { return $false }
    if($this.SourceMode) {
      $mode=[IO.File]::ReadAllText((Join-Path $script:CurrentCallerWorkspace 'fixture-mode.txt'))
      if($mode -ceq 'owner') { $script:ReviewEvent=$true; $this.WasOwner=$true }
      $active=$script:ReviewEvent
      $record=[ordered]@{pid=$this.Id;sourceStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';rawOpenError=2;attestation='';requiresClick=$false;ownerFailed=$false}
      if($active) { $record.sourceStatus='ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive='+ $(if($mode -ceq 'owner'){'true'}else{'false'}); $record.rawOpenError=0; $record.attestation='attended-runtime-v2'; $record.requiresClick=$true }
      if($script:ReviewForcedError) { $record.sourceStatus='ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false'; $record.rawOpenError=5; $record.attestation='guard-unavailable'; $record.requiresClick=$true }
      [IO.File]::WriteAllText((Join-Path $script:CurrentCallerWorkspace 'fixture-result.json'),($record|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    }
    return $true
  }
  $value|Add-Member ScriptMethod Kill {
    $script:ReviewOperations.Add('kill:'+ $this.Id)
    if($this.FailKill) { throw 'Synthetic stop failure.' }
    $this.HasExited=$true; $this.ExitCode=-1
    if($this.WasOwner) { $script:ReviewEvent=$false }
  }
  $value|Add-Member ScriptMethod WaitForExit {
    param($Milliseconds)
    $script:ReviewOperations.Add('wait:'+ $this.Id)
    if($this.FailWait) { return $false }
    $this.HasExited=$true
    return $true
  }
  $value|Add-Member ScriptMethod Dispose {
    $script:ReviewOperations.Add('dispose:'+ $this.Id)
    if($this.FailDispose) { throw 'Synthetic close failure.' }
  }
  return $value
}

# Inspect the real production factory without starting its real Process.
$ownedTemporary=Join-Path ([IO.Path]::GetTempPath()) ('mixel-current-caller-source-'+[Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $ownedTemporary)
try {
  $executable=Join-Path $ownedTemporary 'owned-source-only.exe'
  $factory=New-MixelCurrentCallerProcess $executable
  try {
    $info=$factory.StartInfo
    Assert-Review ($info.FileName -ceq $executable -and $info.Arguments -ceq '' -and -not $info.UseShellExecute -and -not $info.CreateNoWindow -and $info.WorkingDirectory -ceq $ownedTemporary -and $info.UserName -ceq '' -and $info.Verb -ceq '') 'Actual factory changes arguments, caller or launcher context.'
  } finally { $factory.Dispose() }
  Write-Host 'PASS: actual factory only configures direct no-argv Process; no child started or alternate identity/verb.'
  function New-MixelCurrentCallerProcess([string]$Executable) {
    if($script:ReviewNext) { $value=$script:ReviewNext; $script:ReviewNext=$null; return $value }
    $script:ReviewSerial++
    return (New-ReviewProcess $script:ReviewSerial)
  }
  $script:CurrentCallerWorkspace=$ownedTemporary
  $script:CurrentCallerExecutable=$executable
  Reset-Review
  $script:ReviewSourceMode=$false
  $script:ReviewNext=New-ReviewProcess 101
  $script:ReviewNext.FailStart=$true
  Refuse-Review { Start-MixelCurrentCallerOwned $executable }
  Close-MixelCurrentCallerOwned
  Assert-Review ($script:ReviewOperations -contains 'dispose:101' -and -not ($script:ReviewOperations -contains 'kill:101') -and $script:CurrentCallerOwned.Count -eq 0) 'False start did not retain and close exact unstarted object.'
  $script:ReviewNext=New-ReviewProcess 102; $script:ReviewNext.ThrowStart=$true
  Refuse-Review { Start-MixelCurrentCallerOwned $executable }
  Close-MixelCurrentCallerOwned
  Assert-Review ($script:ReviewOperations -contains 'dispose:102') 'Thrown start lost exact object ownership.'
  $script:ReviewNext=New-ReviewProcess 0
  Refuse-Review { Start-MixelCurrentCallerOwned $executable }
  Stop-MixelCurrentCallerOwned; Close-MixelCurrentCallerOwned
  Assert-Review ($script:ReviewOperations -contains 'kill:0' -and $script:ReviewOperations -contains 'dispose:0') 'Bad PID after start escaped exact-object cleanup.'
  Write-Host 'PASS: false/thrown starts and post-start bad PID refuse success and retain exact object for cleanup.'

  Reset-Review; $script:ReviewSourceMode=$false
  $script:ReviewNext=New-ReviewProcess 201; $first=Start-MixelCurrentCallerOwned $executable
  $script:ReviewNext=New-ReviewProcess 202; $second=Start-MixelCurrentCallerOwned $executable
  $first.Process.FailKill=$true
  Refuse-Review { Stop-MixelCurrentCallerOwned }
  Refuse-Review { Close-MixelCurrentCallerOwned }
  Assert-Review ($script:ReviewOperations -contains 'kill:202' -and $script:ReviewOperations -contains 'dispose:202' -and -not ($script:ReviewOperations -contains 'dispose:201') -and $script:CurrentCallerOwned.Count -eq 1) 'First stop failure aborted later cleanup or closed live handle.'
  $first.Process.FailKill=$false
  Stop-MixelCurrentCallerOwned; Close-MixelCurrentCallerOwned; Stop-MixelCurrentCallerOwned; Close-MixelCurrentCallerOwned
  Assert-Review (@($script:ReviewOperations|Where-Object {$_ -ceq 'dispose:202'}).Count -eq 1 -and @($script:ReviewOperations|Where-Object {$_ -ceq 'dispose:201'}).Count -eq 1) 'Successful closure repeated on retry.'
  Write-Host 'PASS: first stop failure still attempts later children, refuses live-handle close, and retries without double close.'

  Reset-Review; $script:ReviewSourceMode=$false
  $script:ReviewNext=New-ReviewProcess 301; $first=Start-MixelCurrentCallerOwned $executable
  $script:ReviewNext=New-ReviewProcess 302; $second=Start-MixelCurrentCallerOwned $executable
  Stop-MixelCurrentCallerOwned
  $first.Process.FailDispose=$true
  Refuse-Review { Close-MixelCurrentCallerOwned }
  Assert-Review ($script:CurrentCallerOwned.Count -eq 1 -and $script:ReviewOperations -contains 'dispose:302') 'First close failure aborted later exact closure.'
  $first.Process.FailDispose=$false; Close-MixelCurrentCallerOwned
  Assert-Review (@($script:ReviewOperations|Where-Object {$_ -ceq 'dispose:302'}).Count -eq 1 -and $script:CurrentCallerOwned.Count -eq 0) 'Partial-close retry touched successful later closure.'
  Write-Host 'PASS: partial-close failure retains only failed object and retries without repeating successful later closure.'

  Reset-Review
  Invoke-MixelCurrentCallerFour
  Assert-Review ($script:CurrentCallerObserved.Count -eq 4 -and @($script:CurrentCallerObserved|ForEach-Object {$_.mode}) -join ',' -ceq 'probe,owner,probe,probe') 'Actual four-phase body did not preserve the intended phases.'
  Assert-Review ($script:CurrentCallerObserved[0].result.rawOpenError -eq 2 -and $script:CurrentCallerObserved[1].result.attestation -ceq 'attended-runtime-v2' -and $script:CurrentCallerObserved[2].result.sourceStatus.EndsWith('memoryActive=false') -and $script:CurrentCallerObserved[3].result.rawOpenError -eq 2) 'Actual four-phase body lost strict absence/owner/observer/released expectations.'
  Stop-MixelCurrentCallerOwned; Close-MixelCurrentCallerOwned
  Assert-Review ($script:CurrentCallerOwned.Count -eq 0 -and -not $script:ReviewEvent) 'Four-phase body retained synthetic owner state.'
  Write-Host 'PASS: actual four-phase functions execute synthetic absence/owner/fresh observer/released results and exact acknowledgment/cleanup.'

  Reset-Review; $script:ReviewForcedError=5
  Refuse-Review { Invoke-MixelCurrentCallerFour }
  Assert-Review ($script:CurrentCallerObserved.Count -eq 1 -and $script:CurrentCallerObserved[0].result.rawOpenError -eq 5 -and $script:CurrentCallerObserved[0].result.attestation -ceq 'guard-unavailable' -and @($script:ReviewOperations|Where-Object {$_.StartsWith('start:')}).Count -eq 1) 'Actual native5 observation was hidden or progressed to owner.'
  Stop-MixelCurrentCallerOwned; Close-MixelCurrentCallerOwned
  Write-Host 'PASS: source error5 is observed literally then fails first phase without owner progression; exact owned child cleanup remains.'

  $valid=[pscustomobject]@{pid=400;sourceStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';rawOpenError=2;attestation='';requiresClick=$false;ownerFailed=$false}
  Assert-StandardLeaseSafeResult $valid 400
  Refuse-Review { Assert-StandardLeaseSafeResult $valid 401 }
  $valid.pid=400.5; Refuse-Review { Assert-StandardLeaseSafeResult $valid 400 }
  $valid.pid=400; $valid.sourceStatus='ownerFailed=false;ownerError=none;kernel=unavailable;probeError=05;memoryActive=false'
  Refuse-Review { Assert-StandardLeaseSafeResult $valid 400 }
  Write-Host 'PASS: exact copied parser refuses wrong retained PID, noninteger PID and malformed canonical error.'
  $source=[IO.File]::ReadAllText($path)
  Assert-Review ($source.Contains('genuineStandardQualified=$false;fullEightQualified=$false;customerAppQualified=$false') -and -not $source.Contains('[DllImport(') -and -not $source.Contains('GetProcessById') -and -not $source.Contains('Start-Process') -and -not $source.Contains('-Verb RunAs')) 'Source scope or forbidden launch/native/API boundary changed.'
  Write-Host 'PASS: all qualification flags remain false and observer contains no token/native/PID lookup/elevation launch path.'
  # Execute the actual main try/finally against a preexisting owned synthetic
  # directory. The old draft wrote its summary despite refusing that directory;
  # the fixed draft must leave it untouched and preserve the refusal.
  Reset-Review
  $preexisting=Join-Path $ownedTemporary 'preexisting'
  [void](New-Item -ItemType Directory -Path $preexisting)
  [IO.File]::WriteAllText((Join-Path $preexisting 'untouched.txt'),'original')
  $bundle=[pscustomobject]@{Manifest=[pscustomobject]@{commit='synthetic';runId=1}}
  $ExpectedManifestSha256='0'*64; $Subsystem='windows'
  $script:CurrentCallerWorkspace=$preexisting
  # Restore only the original unconditional-summary bug in memory; no third
  # source file is needed by this reproducible observer/verifier pair.
  $workspaceGuard='  if($workspaceCreated) {'
  Assert-Review ([regex]::Matches($source,[regex]::Escape($workspaceGuard)).Count -eq 1) 'Exact owned workspace guard changed.'
  foreach($originalBug in @($true,$false)) {
    $draftSource=if($originalBug){$source.Replace($workspaceGuard,'  if($true) {')}else{$source}
    $draftAst=[Management.Automation.Language.Parser]::ParseInput($draftSource,[ref]$tokens,[ref]$errors)
    Assert-Review ($errors.Count -eq 0) 'Workspace failure draft parse failed.'
    $main=@($draftAst.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.TryStatementAst]})
    Assert-Review ($main.Count -eq 1) 'Actual top-level ownership try/finally boundary changed.'
    $workspaceCreated=$false; $passed=$false; $failure=$null; $cleanup=[Collections.Generic.List[string]]::new()
    . ([scriptblock]::Create($main[0].Extent.Text))
    Assert-Review ($null -ne $failure) 'Preexisting workspace did not fail closed.'
    $summary=Join-Path $preexisting 'current-caller-summary.json'
    if($originalBug) {
      Assert-Review (Test-Path -LiteralPath $summary) 'Original workspace-write negative did not reproduce.'
      Remove-Item -LiteralPath $summary
    } else { Assert-Review (-not (Test-Path -LiteralPath $summary)) 'Refused preexisting workspace was mutated by final cleanup.' }
    Assert-Review ([IO.File]::ReadAllText((Join-Path $preexisting 'untouched.txt')) -ceq 'original') 'Preexisting marker changed.'
  }
  Write-Host 'PASS: original preexisting-workspace cleanup write reproduces; corrected actual try/finally refuses without writing.'
  # Bind the embedded verifier and parser without external dependencies.
  $verifierStart=$source.IndexOf('function Assert-PortableLeaseInteger(',[StringComparison]::Ordinal)
  $parserStart=$source.IndexOf('function Assert-StandardLeaseSafeResult(',[StringComparison]::Ordinal)
  $copiesEnd=$source.IndexOf('# The functions above are copied byte-for-byte',[StringComparison]::Ordinal)
  Assert-Review ($verifierStart -ge 0 -and $parserStart -gt $verifierStart -and $copiesEnd -gt $parserStart) 'Exact source copy boundaries changed.'
  $parts=@(($source.Substring($verifierStart,$parserStart-$verifierStart).TrimEnd()+"`n"),($source.Substring($parserStart,$copiesEnd-$parserStart).TrimEnd()+"`n"))
  Assert-Review ($parts.Count -eq 2 -and $parts[0] -is [string] -and $parts[1] -is [string]) 'Exact two copied source blocks changed.'
  $pins=@('faa8d59cf411854f9f62f80adb046b056496c35699ef5e760d9ebecb73f08253','9d9e942e0a33315995dbaffdb472d656ca32a1860d0cd83c1e0134009a28d4ff')
  $hash=[Security.Cryptography.SHA256]::Create()
  try {
    for($i=0;$i -lt 2;$i++) {
      $actual=([BitConverter]::ToString($hash.ComputeHash([Text.UTF8Encoding]::new($false).GetBytes($parts[$i])))).Replace('-','').ToLowerInvariant()
      Assert-Review ($actual -ceq $pins[$i]) 'Embedded exact packaged eleven-binding verifier or six-field parser bytes changed.'
    }
  } finally {$hash.Dispose()}
  $identity=@($ast.EndBlock.Statements|Where-Object {$_ -is [Management.Automation.Language.IfStatementAst] -and $_.Extent.Text.Contains('Current-caller observer requires the exact independently verified compilation package.')})
  Assert-Review ($identity.Count -eq 1) 'Exact trusted package identity guard changed.'
  $identityBody=[scriptblock]::Create($identity[0].Extent.Text)
  function Reset-IdentityReview {
    $script:bundle=[pscustomobject]@{Manifest=[pscustomobject]@{commit='c0a3d1ccb644a83df5615b2ac737cff10c94ed87';tree='f6e860ff5997cd41b0411a5de59fc3034a082025';runId=[long]38120029931}}
    $script:ExpectedManifestSha256='f0823ec8da4fe639307dc8e950e0bcb105863621b8a408b0dea64f52916b3367'
  }
  # The control runs only the actual identity statement, never the Windows
  # platform guard, main body, Process factory Start, or native child.
  Reset-IdentityReview; . $identityBody
  foreach($mutation in @('commit','tree','run','manifest')) {
    Reset-IdentityReview
    switch($mutation) {
      'commit' {$bundle.Manifest.commit='4f013a1c5098b8ae3c8f74e8a365ebcc45ca15fb'}
      'tree' {$bundle.Manifest.tree='c9afc42259e324b54bf006acef2359a3052546f6'}
      'run' {$bundle.Manifest.runId=[long]38117513190}
      'manifest' {$ExpectedManifestSha256='a2bc8f1d360e93523af8ea2e10a933f7ec83adb9ae6086c887eba89f4fd041b7'}
    }
    Refuse-Review { . $identityBody }
  }
  Write-Host 'PASS: embedded exact eleven-binding verifier/six-field parser hashes match; actual fixed package identity statement accepts current c0 source/tree/run/manifest and rejects all four old identity mutations.'
  Write-Host 'Result: ten pure current-caller observer control groups pass; zero native child or Windows API execution.'
} finally {
  # Only this synthetic source-test directory is removed; no bundle/native evidence.
  Remove-Item -LiteralPath $ownedTemporary -Recurse -Force
}
