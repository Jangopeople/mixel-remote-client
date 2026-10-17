$ErrorActionPreference = 'Stop'
# Exercise the exact production QA cleanup with real owned and unrelated
# processes. It must observe owned exit, preserve the initial PID snapshot,
# preserve unrelated paths and propagate cleanup failures.
$source = Get-Content (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') -Raw
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'Customer launch script has PowerShell parse errors.' }
$cleanup = @($ast.FindAll({ param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -ceq 'Stop-OwnedCustomerProcesses'
}, $true))
if ($cleanup.Count -ne 1) { throw 'Exact owned customer cleanup function is missing or duplicated.' }
. ([scriptblock]::Create($cleanup[0].Extent.Text))
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('mixel-customer-cleanup-' + [Guid]::NewGuid().ToString('N'))
$processes = [System.Collections.Generic.List[Diagnostics.Process]]::new()
try {
  $ownedRoot = Join-Path $temporary 'owned'
  $unrelatedRoot = Join-Path $temporary 'unrelated'
  New-Item -ItemType Directory $ownedRoot, $unrelatedRoot | Out-Null
  if (-not $IsWindows) {
    $temporary = (& realpath $temporary)
    if ($LASTEXITCODE -ne 0) { throw 'Owned cleanup control path could not be canonicalized.' }
    $ownedRoot = Join-Path $temporary 'owned'
    $unrelatedRoot = Join-Path $temporary 'unrelated'
  }
  if ($IsWindows) {
    $executablePath = Join-Path $ownedRoot 'Mixel-Owned-Cleanup-Control.exe'
    $controlSource = Join-Path $temporary 'control.cs'
    'public static class OwnedCleanupControl { public static void Main() { System.Threading.Thread.Sleep(120000); } }' |
      Set-Content $controlSource -Encoding utf8
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    & $compiler /nologo /target:exe "/out:$executablePath" $controlSource
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $executablePath)) { throw 'Owned cleanup control compilation failed.' }
    $arguments = @()
  } else {
    $executablePath = Join-Path $ownedRoot 'mixel-owned-cleanup-control'
    $controlSource = Join-Path $temporary 'control.c'
    "#include <unistd.h>`nint main(void) { sleep(120); return 0; }" | Set-Content $controlSource -Encoding utf8
    & cc -Wall -Wextra -Werror $controlSource -o $executablePath
    if ($LASTEXITCODE -ne 0) { throw 'Owned cleanup control compilation failed.' }
    $arguments = @()
  }
  $unrelatedExecutable = Join-Path $unrelatedRoot ([IO.Path]::GetFileName($executablePath))
  Copy-Item $executablePath $unrelatedExecutable
  $startOptions = @{ PassThru = $true }
  if ($arguments.Count -gt 0) { $startOptions.ArgumentList = $arguments }
  $existing = Start-Process -FilePath $executablePath @startOptions
  $processes.Add($existing)
  $before = @(Get-Process | Select-Object -ExpandProperty Id)
  $owned = Start-Process -FilePath $executablePath @startOptions
  $processes.Add($owned)
  $unrelated = Start-Process -FilePath $unrelatedExecutable @startOptions
  $processes.Add($unrelated)
  $runtimePath = $executablePath
  $Portable = $false
  $ordinaryRoot = $null
  Start-Sleep -Milliseconds 250
  if ($existing.HasExited -or $owned.HasExited -or $unrelated.HasExited) {
    throw "Owned process controls exited before qualification: existing=$($existing.HasExited)/$($existing.ExitCode), owned=$($owned.HasExited)/$($owned.ExitCode), unrelated=$($unrelated.HasExited)/$($unrelated.ExitCode)."
  }
  Stop-OwnedCustomerProcesses
  if (-not $owned.WaitForExit(1000) -or $existing.HasExited -or $unrelated.HasExited) {
    throw "Exact cleanup failed to terminate only the newly owned process: ownedExited=$($owned.HasExited), existingExited=$($existing.HasExited), unrelatedExited=$($unrelated.HasExited), actualOwnedPath=$($owned.Path), expectedOwnedPath=$executablePath."
  }
  Write-Host 'PASS: actual customer cleanup waits for exact owned process exit and preserves both the pre-existing PID and unrelated executable path.'
  & {
    $failure = [pscustomobject]@{ Path = $executablePath; Id = 2147483000; HasExited = $false; Handle = [IntPtr]::Zero; Disposed = $false }
    $failure | Add-Member ScriptMethod Kill { throw 'Owned termination failure control.' }
    $failure | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    function Get-Process { return $failure }
    $rejected = $false
    try { Stop-OwnedCustomerProcesses } catch { $rejected = $true }
    if (-not $rejected -or -not $failure.Disposed) { throw 'Exact cleanup swallowed a termination failure or leaked its process observation.' }
  }
  Write-Host 'PASS: actual cleanup function propagates an owned termination failure and disposes its process observation.'
} finally {
  foreach ($process in $processes) {
    try {
      if (-not $process.HasExited) { $process.Kill() }
      if (-not $process.WaitForExit(10000)) { throw 'Owned cleanup-test process did not exit.' }
    } finally { $process.Dispose() }
  }
  if (Test-Path $temporary) { Remove-Item $temporary -Recurse -Force }
}
