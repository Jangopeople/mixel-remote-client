# Function declarations only. No top-level Add-Type, native call, policy change,
# input injection, process control or automatic acceptance. Root may paste these
# reviewed functions in the normal same-owner Windows console. Initialize is an
# explicit admitted action; observe returns only identity/guard and input state.
function Get-MixelHeldLogicalSHA([string]$Text) {
  if($Text.Contains("`r") -and $Text.Replace("`r`n",'').Contains("`n")) {
    throw 'Mixed source newlines'
  }
  $v=$Text.Replace("`r`n","`n")
  if($v.Contains("`r")) { throw 'Lone carriage return' }
  $h=[Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($h.ComputeHash(
    [Text.Encoding]::UTF8.GetBytes($v)))).Replace('-','').ToLowerInvariant() }
  finally { $h.Dispose() }
}
function Compile-MixelHeldManagedSources([string]$IdentitySource,[string]$KeyStateSource) {
  if((Get-MixelHeldLogicalSHA $IdentitySource) -cne
     '1b0b4fd52a307f2076e3296722b8891fbe11c05b3361be3fce0f47ed5433efe8' -or
     (Get-MixelHeldLogicalSHA $KeyStateSource) -cne 'eb64c8b3f35be4c3f11ff743e2912f767e45c1b22f91ca5c92bf2f24127374ac') {
    throw 'Exact read-only source literals required'
  }
  if(('MixelPc01ReadOnly070' -as [type]) -or ('MixelHeldInputReadOnly' -as [type])) {
    throw 'Fresh PowerShell process required for exact managed sources'
  }
  # Windows PowerShell 5.1 has no Add-Type -CompilerOptions. Compile the two
  # exact source units together so the key observer resolves the identity type.
  # Compilation loads definitions only; no compiled observation method runs.
  $provider=New-Object Microsoft.CSharp.CSharpCodeProvider
  try {
    $options=New-Object System.CodeDom.Compiler.CompilerParameters
    $options.GenerateExecutable=$false
    $options.GenerateInMemory=$true
    $options.TreatWarningsAsErrors=$true
    $options.WarningLevel=4
    [void]$options.ReferencedAssemblies.Add('System.dll')
    [void]$options.ReferencedAssemblies.Add('System.Core.dll')
    $result=$provider.CompileAssemblyFromSource($options,[string[]]@($IdentitySource,$KeyStateSource))
    if($result.Errors.Count -ne 0) {
      $diagnostic=($result.Errors|ForEach-Object { $_.ToString() }) -join "`n"
      throw ('Exact managed source compilation failed: '+$diagnostic)
    }
    [void]$result.CompiledAssembly.GetType('MixelPc01ReadOnly070',$true,$false)
    [void]$result.CompiledAssembly.GetType('MixelHeldInputReadOnly',$true,$false)
  } finally { $provider.Dispose() }
}
function Initialize-MixelHeldObserver([string]$RootAdmissionJson,[string]$ExpectedSHA256,
                                    [string]$IdentitySource,[string]$KeyStateSource) {
  if($ExpectedSHA256 -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Root hash required' }
  # Admission bytes are never normalized: the independent Root hash is exact.
  $hash=[Security.Cryptography.SHA256]::Create()
  try { $actual=([BitConverter]::ToString($hash.ComputeHash(
    [Text.Encoding]::UTF8.GetBytes($RootAdmissionJson)))).Replace('-','').ToLowerInvariant() }
  finally { $hash.Dispose() }
  if($actual -cne $ExpectedSHA256) { throw 'Exact Root admission bytes required' }
  $A=$RootAdmissionJson|ConvertFrom-Json
  if($A.schema -cne 'mixel-six-owner-held-input-admission-v1' -or $A.decidedBy -cne 'Root' -or
     $A.decision -cne 'admit-one-private-held-input-controller') { throw 'Root admission required' }
  $expires=[DateTimeOffset]::Parse($A.expiresAtUTC)
  if($expires -le [DateTimeOffset]::UtcNow -or
     $expires -gt [DateTimeOffset]::UtcNow.AddHours(2)) { throw 'Expired admission' }
  foreach($name in @('compositionReceiptSHA256','qualificationReceiptSHA256','cold95ReceiptSHA256')) {
    if($A.binding.target.$name -cnotmatch '\A[0-9a-f]{64}\z') { throw 'Qualified target required' }
  }
  foreach($p in $A.flags.PSObject.Properties) {
    if($p.Value -isnot [bool] -or $p.Value) { throw 'No global qualification flags' }
  }
  $H=$A.binding.host;$T=$A.binding.target
  $B=[pscustomobject]@{ApplicationSource=$T.source;ApplicationTree=$T.tree;
    ProducerRun=$T.producerRun;RunAttempt=$T.runAttempt;AdmissionSha256=$ExpectedSHA256;
    MainPid=[int]$H.MainPid;Birth=[long]$H.Birth;Session=[int]$H.Session;
    Sid=$H.Sid;Id=$H.registeredId;
    Outer=[pscustomobject]@{Path=$H.OuterPath;Sha256=$H.OuterSHA256};
    Main=[pscustomobject]@{Path=$H.MainPath;Sha256=$H.MainSHA256};
    Core=[pscustomobject]@{Path=$H.CorePath;Sha256=$H.CoreSHA256}}
  Assert-HeldHostBinding $B
  Compile-MixelHeldManagedSources $IdentitySource $KeyStateSource
  return $B
}
function Assert-HeldHostBinding($B) {
  if ($null -eq $B -or $B.ApplicationSource -isnot [string] -or
      $B.ApplicationSource -cnotmatch '\A[0-9a-f]{40}\z' -or
      $B.ApplicationTree -isnot [string] -or
      $B.ApplicationTree -cnotmatch '\A[0-9a-f]{40}\z' -or
      ($B.ProducerRun -isnot [int] -and $B.ProducerRun -isnot [long]) -or
      $B.ProducerRun -le 0 -or
      ($B.RunAttempt -isnot [int] -and $B.RunAttempt -isnot [long]) -or
      $B.RunAttempt -le 0) { throw 'Exact qualified six-owner target required' }
  if ($B.AdmissionSha256 -isnot [string] -or $B.AdmissionSha256 -cnotmatch '\A[0-9a-f]{64}\z') { `
  throw 'Genuine Root admission hash required' }
  if ($null -eq $B -or $B.MainPid -isnot [int] -or $B.MainPid -le 0 -or $B.Birth -isnot [long] `
  -or $B.Birth -le 0) { throw 'Actual admitted PID and kernel FILETIME required' }
  if ($B.Session -isnot [int] -or $B.Session -lt 0 -or $B.Sid -isnot [string] -or $B.Sid `
  -cnotmatch '\AS-1-5-21-(?:[0-9]+-){3}[0-9]+\z') { throw 'Actual admitted session/SID required' }
  if ($B.Id -isnot [string] -or $B.Id -cnotmatch '\A[a-zA-Z0-9-]{6,32}\z') { throw `
  'Actual admitted registered ID required' }
  foreach($name in @('Outer','Main','Core')) {
    $row=$B.$name
    if ($row.Path -isnot [string] -or $row.Path -cnotmatch '\A[A-Za-z]:\\' -or $row.Path.Contains( `
  '*') -or $row.Path.Contains('?')) { throw 'Exact admitted local file path required' }
    if(@($row.Path.Split('\')|Where-Object { $_.ToLowerInvariant() -ceq 'worktrees' -or
        $_ -ceq '..' }).Count -gt 0) { throw 'Ordinary payload paths only' }
    if ($row.Sha256 -isnot [string] -or $row.Sha256 -cnotmatch '\A[0-9a-f]{64}\z') { throw `
  'Actual admitted file digest required' }
  }
  if (-not ([IO.Path]::GetFileName($B.Main.Path)).Equals('Mixel-Remote.exe', `
  [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected admitted main image' }
  if (-not ([IO.Path]::GetFileName($B.Core.Path)).Equals('libmixel-remote.dll', `
  [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected admitted core image' }
  if (-not ([IO.Path]::GetDirectoryName($B.Core.Path)).Equals([IO.Path]::GetDirectoryName( `
  $B.Main.Path),[StringComparison]::OrdinalIgnoreCase)) { throw `
  'Core is not the admitted main sibling' }
}
function Get-HeldHostHashes($B) {
  $h=[ordered]@{}
  foreach($name in @('Outer','Main','Core')) {
    $row=$B.$name;$f=Get-Item -LiteralPath $row.Path -Force -ErrorAction Stop
    if ($f.PSIsContainer -or $f.Length -le 0 -or $f.Length -gt 536870912 -or ($f.Attributes -band `
  [IO.FileAttributes]::ReparsePoint)) { throw 'Unexpected admitted regular file' }
    $before=[string]$f.Length+':'+[string]$f.LastWriteTimeUtc.Ticks
    $actual=(Get-FileHash -LiteralPath $row.Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    $f=Get-Item -LiteralPath $row.Path -Force -ErrorAction Stop
    if ($actual -cne $row.Sha256 -or $before -cne ( `
  [string]$f.Length+':'+[string]$f.LastWriteTimeUtc.Ticks)) { throw `
  'Admitted file identity changed' }
    $h[$name]=$actual
  }
  return $h
}
function Get-HeldHostReply([string]$Json,[uint32]$ExpectedPid) {
  $r=[MixelPc01ReadOnly070]::RequestWithServerPid($Json,$ExpectedPid)
  if ($r.ServerPid -ne $ExpectedPid) { throw 'Actual kernel pipe owner changed' }
  return ($r.Json|ConvertFrom-Json)
}
function Get-HeldHostConfig($R,[string]$Key) {
  if ($R.t -isnot [string] -or $R.t -cne 'Config' -or $R.c -isnot [array] -or $R.c.Count -ne 2 `
  -or $R.c[0] -isnot [string] -or $R.c[0] -cne $Key -or $R.c[1] -isnot [string]) { throw `
  'Unexpected typed Config reply' }
  return $R.c[1]
}
function Get-MixelHeldForeground($B,[uint32]$ExpectedPid) {
  if($ExpectedPid -eq 0) { throw 'Actual foreground PID required' }
  $f=[MixelPc01ReadOnly070]::Observe([int]$ExpectedPid)
  if($f.pid -ne $ExpectedPid -or $f.birth -le 0 -or $f.sid -cne $B.Sid -or
     $f.session -ne $B.Session -or $f.elevated -or $f.integrity -ne 8192) {
    throw 'Unknown or inaccessible foreground integrity/owner/session'
  }
  return $f
}
function Get-HeldHostObservation($B,[ValidateSet('normal-disconnect-shift','normal-disconnect-leftButton',
  'viewer-loss-shift','viewer-loss-leftButton')][string]$Case) {
  Assert-HeldHostBinding $B
  $who=[Security.Principal.WindowsIdentity]::GetCurrent()
  try { if ($who.User.Value -cne $B.Sid -or (Get-Process -Id $PID).SessionId -ne $B.Session) { `
  throw 'Console is not the admitted owner/session' } } finally { $who.Dispose() }
  $context=[MixelHeldInputReadOnly]::Context()
  $foreground=Get-MixelHeldForeground $B $context.foregroundPid
  $keysBefore=[MixelHeldInputReadOnly]::Observe()
  if($keysBefore.foregroundPid -ne $foreground.pid) { throw 'Foreground changed before high-bit reads' }
  $h=Get-HeldHostHashes $B
  $f=[MixelPc01ReadOnly070]::Observe($B.MainPid)
  $p=Get-Process -Id $B.MainPid
  if ($f.pid -ne $B.MainPid -or $f.birth -ne $B.Birth -or $f.sid -cne $B.Sid -or $f.session -ne `
  $B.Session -or $f.elevated -or $f.integrity -ne 8192) { throw `
  'Actual process is not the admitted same-owner Medium process' }
  if (-not $f.path.Equals($B.Main.Path,[StringComparison]::OrdinalIgnoreCase) -or `
  $p.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $f.birth) { throw `
  'Actual process image/birth changed' }
  $guard=Get-HeldHostConfig (Get-HeldHostReply `
  '{"t":"Config","c":["mixel-support-invite-attended",null]}' $B.MainPid) `
  'mixel-support-invite-attended'
  $lease=Get-HeldHostConfig (Get-HeldHostReply `
  '{"t":"Config","c":["mixel-support-invite-lease-status",null]}' $B.MainPid) `
  'mixel-support-invite-lease-status'
  $online=Get-HeldHostReply '{"t":"OnlineStatus","c":null}' $B.MainPid
  $server=Get-HeldHostConfig (Get-HeldHostReply '{"t":"Config","c":["rendezvous_server",null]}' `
  $B.MainPid) 'rendezvous_server'
  $id=Get-HeldHostConfig (Get-HeldHostReply '{"t":"Config","c":["id",null]}' $B.MainPid) 'id'
  $video=Get-HeldHostReply '{"t":"VideoConnCount","c":null}' $B.MainPid
  if ($guard -cne 'attended-runtime-v2' -or $id -cne $B.Id -or $server.Split(',')[0] -cne `
  'rs.mixel.ch:21116') { throw 'Actual guard/registration/rendezvous differs' }
  if ($online.t -isnot [string] -or $online.t -cne 'OnlineStatus' -or $online.c -isnot [array] `
  -or $online.c.Count -ne 2 -or ($online.c[0] -isnot [int] -and $online.c[0] -isnot [long]) -or `
  $online.c[0] -le 0 -or $online.c[1] -isnot [bool] -or -not $online.c[1]) { throw `
  'Unexpected typed online/key proof' }
  $pattern=(
    '\AownerFailed=(?<owner>true|false);' +
    'ownerError=(?<ownerError>none|0|-?[1-9][0-9]{0,9});' +
    'kernel=(?<kernel>active|absent|unavailable);' +
    'probeError=(?<probeError>none|0|-?[1-9][0-9]{0,9});' +
    'memoryActive=(?<memory>true|false)\z'
  )
  $m=[regex]::Match($lease,$pattern)
  if (-not $m.Success) { throw 'Invalid typed lease proof' }
  foreach($name in @('ownerError','probeError')) { $v=$m.Groups[$name].Value;[int]$n=0;if($v -cne `
  'none' -and -not [int]::TryParse($v,[Globalization.NumberStyles]::AllowLeadingSign, `
  [Globalization.CultureInfo]::InvariantCulture,[ref]$n)){throw 'Lease native code overflow'} }
  if ($m.Groups['owner'].Value -cne 'false' -or $m.Groups['ownerError'].Value -cne 'none' -or `
  $m.Groups['kernel'].Value -cne 'active' -or $m.Groups['probeError'].Value -cne 'none' -or `
  $m.Groups['memory'].Value -cne 'true') { throw 'Actual owned active kernel lease missing' }
  if ($video.t -isnot [string] -or $video.t -cne 'VideoConnCount' -or ($video.c -isnot [int] -and `
  $video.c -isnot [long]) -or $video.c -notin @(0,1)) { throw `
  'Unexpected typed authenticated Remote count' }
  $after=[MixelPc01ReadOnly070]::Observe($B.MainPid)
  if ($after.birth -ne $f.birth -or $after.sid -cne $f.sid -or $after.session -ne $f.session -or `
  $after.elevated -or $after.integrity -ne 8192 -or -not $after.path.Equals($f.path, `
  [StringComparison]::OrdinalIgnoreCase)) { throw 'Process identity changed during sample' }
  $end=Get-HeldHostHashes $B
  $keys=[MixelHeldInputReadOnly]::Observe()
  $foregroundAfter=Get-MixelHeldForeground $B $keys.foregroundPid
  $finalContext=[MixelHeldInputReadOnly]::Context()
  if($foregroundAfter.pid -ne $foreground.pid -or $foregroundAfter.birth -ne $foreground.birth -or
     $keys.foregroundPid -ne $foreground.pid -or $finalContext.foregroundPid -ne $foreground.pid) {
    throw 'Foreground PID/birth changed during high-bit sample'
  }
  foreach($name in @('shift','leftShift','rightShift','leftButton','rightButton')) {
    if($keys.$name -ne $keysBefore.$name) { throw 'Input changed during sample; retry required' }
  }
  if($keys.inputDesktop -cne $keysBefore.inputDesktop -or
     $keys.observerDesktop -cne $keysBefore.observerDesktop) {
    throw 'Input desktop changed during sample'
  }
  return [pscustomobject]@{applicationSource=$B.ApplicationSource;producerRun=$B.ProducerRun;
    runAttempt=$B.RunAttempt;RootAdmissionSha256=$B.AdmissionSha256;case=$Case;
    observedAtUTC=[DateTime]::UtcNow.ToString('o');pid=$f.pid;birth=$f.birth;
    startTimeUtc=$p.StartTime.ToUniversalTime().ToString('o');sid=$f.sid;session=$f.session;
    integrity=$f.integrity;elevated=$f.elevated;path=$f.path;registeredId=$id;
    guard=$guard;kernelLease='active';keyConfirmed=$online.c[1];rendezvousState=$online.c[0];
    remoteAuthenticated=$video.c;pipeOwnerPid=$B.MainPid;ipcQueries=6;
    outerSha256=$h.Outer;mainSha256=$h.Main;coreSha256=$h.Core;
    monotonicMilliseconds=[long]$keys.monotonicMilliseconds;foregroundPid=[long]$keys.foregroundPid;
    inputDesktop=$keys.inputDesktop;observerDesktop=$keys.observerDesktop;
    foregroundBirth=$foreground.birth;foregroundSid=$foreground.sid;
    foregroundSession=$foreground.session;foregroundIntegrity=$foreground.integrity;
    foregroundElevated=$foreground.elevated;desktopAccess='existing-token-dacl-hook-and-journal';
    highBits=[ordered]@{shift=$keys.shift;leftShift=$keys.leftShift;rightShift=$keys.rightShift;
                      leftButton=$keys.leftButton;rightButton=$keys.rightButton}}
}

function Get-MixelHeldHostSeries($Binding,[string]$Case,[int]$Seconds=120) {
  if($Seconds -lt 30 -or $Seconds -gt 180) { throw 'Bounded observation window required' }
  Assert-HeldHostBinding $Binding
  $clock=[Diagnostics.Stopwatch]::StartNew()
  $rows=[Collections.Generic.List[object]]::new()
  try {
    while($clock.Elapsed.TotalSeconds -lt $Seconds) {
      # Unknown errors remain failures. A transition during a sample may be
      # retried, but never fabricates an up/down or authenticated observation.
      try { $rows.Add((Get-HeldHostObservation $Binding $Case)) }
      catch {
        if($_.Exception.Message -cne 'Input changed during sample; retry required') { throw }
      }
      Start-Sleep -Milliseconds 400
    }
    return ,$rows.ToArray()
  } finally { [MixelPc01ReadOnly070]::CloseObservations() }
}
