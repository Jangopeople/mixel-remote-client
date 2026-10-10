param(
  [string]$TemporaryRoot,
  [switch]$ExportToGitHubEnvironment
)
$ErrorActionPreference = 'Stop'
if ($env:OS -cne 'Windows_NT') { throw 'Native MSVC activation requires Windows.' }
if ($ExportToGitHubEnvironment -and [string]::IsNullOrWhiteSpace($env:GITHUB_ENV)) {
  throw 'Native MSVC environment export requires the owned GitHub environment file.'
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) { throw 'Installed Visual Studio discovery tool is absent.' }
$installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or @($installation).Count -ne 1 -or [string]::IsNullOrWhiteSpace($installation)) {
  throw 'Installed x64 MSVC toolchain discovery failed.'
}
$devcmd = Join-Path $installation 'Common7/Tools/VsDevCmd.bat'
if (-not (Test-Path -LiteralPath $devcmd -PathType Leaf) -or $devcmd -match '["%\r\n]') {
  throw 'Installed MSVC developer environment is absent or cannot be represented as a literal batch path.'
}
if ([string]::IsNullOrWhiteSpace($TemporaryRoot)) { $TemporaryRoot = $env:RUNNER_TEMP }
if ([string]::IsNullOrWhiteSpace($TemporaryRoot)) { $TemporaryRoot = [IO.Path]::GetTempPath() }
if (-not (Test-Path -LiteralPath $TemporaryRoot -PathType Container)) { throw 'Native MSVC temporary root is absent.' }
$owned = Join-Path ([IO.Path]::GetFullPath($TemporaryRoot)) ('mixel-msvc-env-' + [Guid]::NewGuid().ToString('N'))
if ($owned -match '["%\r\n]') { throw 'Owned MSVC batch path cannot be represented as a literal command path.' }
$created = $false
$process = $null
try {
  New-Item -ItemType Directory -Path $owned | Out-Null
  $created = $true
  $batch = Join-Path $owned 'activate.cmd'
  $snapshot = Join-Path $owned 'environment.utf16'
  # Keep batch syntax in its own file. PowerShell's Windows cmd.exe Legacy
  # argument reconstruction must never re-escape an embedded developer path.
  # The developer script may start children which retain inherited pipe handles.
  # Snapshot the completed environment with cmd's own UTF-16 set redirection;
  # correctness then depends on the retained command process, never pipe EOF.
  $body = '@echo off' + "`r`n" + 'call "%MIXEL_MSVC_DEVCMD%" -no_logo -arch=x64 -host_arch=x64 >nul 2>&1' + "`r`n" + 'if errorlevel 1 exit /b 1' + "`r`n" + 'set > "%MIXEL_MSVC_ENV_OUTPUT%"' + "`r`n" + 'if errorlevel 1 exit /b 1' + "`r`n"
  [IO.File]::WriteAllText($batch, $body, [Text.UTF8Encoding]::new($false))
  $info = [Diagnostics.ProcessStartInfo]::new()
  $info.FileName = $env:ComSpec
  # Arguments is a raw Windows command line; .NET does not CRT-escape the quotes.
  # The only /c command is the quoted owned filename, with no /s quote stripping.
  $info.Arguments = '/d /v:off /u /c "' + $batch + '"'
  $info.Environment['MIXEL_MSVC_DEVCMD'] = $devcmd
  $info.Environment['MIXEL_MSVC_ENV_OUTPUT'] = $snapshot
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $info
  if (-not $process.Start()) { throw 'Installed MSVC command processor did not start.' }
  if (-not $process.WaitForExit(30000)) {
    $process.Kill($true)
    [void]$process.WaitForExit(5000)
    throw 'Installed MSVC developer environment import exceeded its time bound.'
  }
  if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $snapshot -PathType Leaf)) {
    throw 'Installed MSVC developer environment import failed its exit/output bounds; private output withheld.'
  }
  $record = Get-Item -LiteralPath $snapshot -Force
  if (($record.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $record.Length -le 0 -or $record.Length -gt 2097152 -or $record.Length % 2 -ne 0) {
    throw 'Installed MSVC developer environment snapshot failed its file/output bounds; private output withheld.'
  }
  $snapshotBytes = [byte[]]::new(2097154)
  $snapshotLength = 0
  $snapshotStream = [IO.File]::Open($snapshot, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    while ($snapshotLength -lt $snapshotBytes.Length) {
      $readCount = $snapshotStream.Read($snapshotBytes, $snapshotLength, $snapshotBytes.Length - $snapshotLength)
      if ($readCount -eq 0) { break }
      $snapshotLength += $readCount
    }
  } finally { $snapshotStream.Dispose() }
  if ($snapshotLength -le 0 -or $snapshotLength -gt 2097152 -or $snapshotLength % 2 -ne 0) {
    throw 'Installed MSVC developer environment snapshot failed its read/output bounds; private output withheld.'
  }
  $text = [Text.UnicodeEncoding]::new($false, $false, $true).GetString($snapshotBytes, 0, $snapshotLength)
  if ($text.Length -eq 0 -or $text.Length -gt 1048576) {
    throw 'Installed MSVC developer environment import failed its exit/output bounds; private output withheld.'
  }
  $environment = @($text -split '\r?\n' | Where-Object { $_.Length -gt 0 })
  if ($environment.Count -gt 4096) { throw 'Installed MSVC environment inventory exceeds its bound.' }
  # Validate every record and protected variable before writing any runner file.
  $updates = [System.Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach ($line in $environment) {
    if ($line.StartsWith('=')) { continue }
    if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_()]*?)=(.*)$') { throw 'Unexpected native MSVC environment record; private output withheld.' }
    $name, $value = $Matches[1], $Matches[2]
    if ($name -ceq 'MIXEL_MSVC_DEVCMD') {
      if ($value -cne $devcmd) { throw 'Owned MSVC developer path changed in its child environment.' }
      continue
    }
    if ($name -ceq 'MIXEL_MSVC_ENV_OUTPUT') {
      if ($value -cne $snapshot) { throw 'Owned MSVC environment snapshot path changed in its child environment.' }
      continue
    }
    if ([Environment]::GetEnvironmentVariable($name, 'Process') -cne $value) {
      if ($name -match '^(GITHUB_|RUNNER_|NODE_OPTIONS$)') { throw 'MSVC attempted to replace a protected runner variable.' }
      if ($updates.ContainsKey($name)) { throw 'Duplicate changed MSVC environment record.' }
      $updates.Add($name, $value)
    }
  }
  $target = if ($updates.ContainsKey('VSCMD_ARG_TGT_ARCH')) { $updates['VSCMD_ARG_TGT_ARCH'] } else { $env:VSCMD_ARG_TGT_ARCH }
  $hostArch = if ($updates.ContainsKey('VSCMD_ARG_HOST_ARCH')) { $updates['VSCMD_ARG_HOST_ARCH'] } else { $env:VSCMD_ARG_HOST_ARCH }
  if ($target -cne 'x64' -or $hostArch -cne 'x64') { throw 'Installed MSVC developer environment is not x64 host and target.' }
  foreach ($entry in $updates.GetEnumerator()) {
    if ($ExportToGitHubEnvironment) {
      $delimiter = 'MIXEL_MSVC_' + [Guid]::NewGuid().ToString('N')
      Add-Content -LiteralPath $env:GITHUB_ENV -Value ($entry.Key + '<<' + $delimiter + "`n" + $entry.Value + "`n" + $delimiter) -Encoding utf8
    }
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
  }
  if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw 'Installed native MSVC compiler is unavailable after environment import.' }
  Write-Host ('PASS: installed x64 MSVC environment imported through owned batch; ' + $updates.Count + ' changed toolchain variables, values withheld.')
} finally {
  if ($process) { $process.Dispose() }
  if ($created) { Remove-Item -LiteralPath $owned -Recurse -Force }
}
