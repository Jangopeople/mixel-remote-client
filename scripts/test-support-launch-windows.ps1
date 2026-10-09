param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [switch]$Portable,
  [switch]$QuickSupport,
  [string]$ExpectedPayload
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'support-runtime-probe-windows.ps1')

if (-not ('MixelSupportWindowTest' -as [type])) { Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class MixelSupportWindowTest {
  public delegate bool EnumCallback(IntPtr hwnd, IntPtr parameter);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback callback, IntPtr parameter);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int command);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder title, int maximum);
}
'@
}

$executablePath = (Resolve-Path $Executable).Path
$runtimePath = $executablePath
if ($Portable) {
  if (-not $ExpectedPayload) { throw 'Portable runtime verification requires the expected signed payload.' }
  $runtimePath = Join-Path $env:LOCALAPPDATA 'mixel-remote/Mixel-Remote.exe'
}
$token = 'inv_00000000-0000-0000-0000-000000000002'
# Windows protocol activation can canonicalize an authority-only URI by adding
# this slash. Exercise the same semantic support URI the Dart parser accepts.
$uri = "mixel-remote://support/?invite=$token&apikey=synthetic-invalid-public-key-000000000000"
$before = @(Get-Process | Select-Object -ExpandProperty Id)
$main = $null
$launchScenario = if ($QuickSupport) { 'QuickSupport double-click' } else { 'support URI launch' }

function Start-CustomerApp {
  if ($QuickSupport) { return Start-Process -FilePath $executablePath -PassThru }
  return Start-Process -FilePath $executablePath -ArgumentList $uri -PassThru
}

function Find-MainWindow([int]$ProcessId) {
  $found = [System.Collections.Generic.List[IntPtr]]::new()
  [MixelSupportWindowTest]::EnumWindows({
    param($window, $parameter)
    [uint32]$ownerId = 0
    [void][MixelSupportWindowTest]::GetWindowThreadProcessId($window, [ref]$ownerId)
    if ($ownerId -eq $ProcessId) {
      $title = [System.Text.StringBuilder]::new(512)
      [void][MixelSupportWindowTest]::GetWindowText($window, $title, 512)
      if ($title.ToString() -eq 'Mixel-Remote') { $found.Add($window) }
    }
    return $true
  }, [IntPtr]::Zero) | Out-Null
  return $found.ToArray()
}

function Wait-VisibleMain([int]$ProcessId, [string]$Scenario) {
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  while ([DateTime]::UtcNow -lt $deadline) {
    foreach ($window in (Find-MainWindow $ProcessId)) {
      if ([MixelSupportWindowTest]::IsWindowVisible($window) -and
          -not [MixelSupportWindowTest]::IsIconic($window)) {
        return $window
      }
    }
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
      throw "$Scenario failed: main app exited before showing customer UI."
    }
    Start-Sleep -Milliseconds 250
  }
  throw "$Scenario failed: customer app window did not become visible on the Windows runner desktop."
}

function Wait-PortableMain {
  $deadline = [DateTime]::UtcNow.AddSeconds(60)
  while ([DateTime]::UtcNow -lt $deadline) {
    foreach ($candidate in (Get-Process | Where-Object {
      $before -notcontains $_.Id -and $_.Path -and
      $_.Path.Equals($runtimePath, [StringComparison]::OrdinalIgnoreCase)
    })) {
      if (@(Find-MainWindow $candidate.Id).Count -gt 0) { return $candidate }
    }
    Start-Sleep -Milliseconds 250
  }
  throw 'Portable customer launcher did not extract and start the customer app.'
}

try {
  $main = Start-CustomerApp
  # Let FFI initialization and the cold URI handler finish before checking the
  # stable window state; the original bug hid an initially-created main window.
  Start-Sleep -Seconds 15
  if ($Portable) {
    $main = Wait-PortableMain
    $expectedRoot = (Resolve-Path $ExpectedPayload).Path
    $extractedRoot = Split-Path $runtimePath -Parent
    foreach ($file in (Get-ChildItem $expectedRoot -Recurse -File)) {
      $relative = [IO.Path]::GetRelativePath($expectedRoot, $file.FullName)
      $extracted = Join-Path $extractedRoot $relative
      if (-not (Test-Path $extracted) -or
          (Get-FileHash $file.FullName -Algorithm SHA256).Hash -ne (Get-FileHash $extracted -Algorithm SHA256).Hash) {
        throw "Portable customer launcher extracted different bytes: $relative"
      }
    }
    & (Join-Path $PSScriptRoot 'verify-windows-payload.ps1') -Payload $extractedRoot
    Write-Host 'PASS: portable customer launcher extracts the exact signed application and assets.'
  }
  $window = Wait-VisibleMain $main.Id "Cold $launchScenario"
  Start-Sleep -Seconds 3
  if (-not [MixelSupportWindowTest]::IsWindowVisible($window)) {
    throw "Cold $launchScenario failed: main app became hidden after initialization."
  }
  Write-Host "PASS: cold $launchScenario shows customer app."
  $coldHealth = Wait-MixelSupportRuntimeHealth "Cold $launchScenario" -RequireOnline

  [void][MixelSupportWindowTest]::ShowWindow($window, 6)
  Start-Sleep -Seconds 1
  if (-not [MixelSupportWindowTest]::IsIconic($window)) {
    throw 'Warm-launch setup failed: runner could not minimize the customer app.'
  }
  Start-CustomerApp | Out-Null
  $window = Wait-VisibleMain $main.Id "Warm $launchScenario"
  Write-Host "PASS: warm $launchScenario restores visible customer app."
  $warmHealth = Wait-MixelSupportRuntimeHealth "Warm $launchScenario" -RequireOnline

  foreach ($logRoot in @(
      (Join-Path $env:APPDATA 'Mixel-Remote'),
      (Join-Path $env:LOCALAPPDATA 'Mixel-Remote'),
      (Join-Path $env:APPDATA 'MixelRemote'))) {
    if (Test-Path $logRoot) {
      $leaks = Get-ChildItem $logRoot -Recurse -Filter '*.log' -File |
        Select-String -SimpleMatch $token
      if ($leaks) { throw 'Support invite bearer appeared in app logs.' }
    }
  }
  Write-Host 'PASS: synthetic support invite bearer absent from app log files.'
} finally {
  # Only stop processes newly started from this runner-owned build directory.
  Get-Process | Where-Object {
    $before -notcontains $_.Id -and $_.Path -and
    ($_.Path.StartsWith((Split-Path $executablePath -Parent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
     ($Portable -and $_.Path.StartsWith((Split-Path $runtimePath -Parent) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)))
  } | Stop-Process -Force -ErrorAction SilentlyContinue
}
