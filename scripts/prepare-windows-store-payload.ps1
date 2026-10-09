param([string]$BuildRoot = 'rustdesk/flutter/build/windows', [string]$Destination = 'store-payload')
$ErrorActionPreference = 'Stop'
$clients = @(Get-ChildItem $BuildRoot -Recurse -Filter 'mixel-remote.exe' -File |
  Where-Object { $_.Directory.Name -eq 'Release' })
if ($clients.Count -ne 1) { throw "Expected exactly one compiled Windows Release application, found $($clients.Count)." }
if (Test-Path $Destination) { Remove-Item $Destination -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Copy-Item -Path (Join-Path $clients[0].Directory.FullName '*') -Destination $Destination -Recurse -Force

# The Store manifest starts this executable directly inside WindowsApps. Do not
# package the portable installer: it extracts to LocalAppData and loses identity.
$main = Get-Item (Join-Path $Destination 'mixel-remote.exe')
Rename-Item -Path $main.FullName -NewName 'mixel-store-main.tmp'
Rename-Item -Path (Join-Path $Destination 'mixel-store-main.tmp') -NewName 'Mixel-Remote.exe'
foreach ($required in @('Mixel-Remote.exe', 'libmixel-remote.dll', 'flutter_windows.dll', 'data/icudtl.dat', 'data/flutter_assets')) {
  if (-not (Test-Path (Join-Path $Destination $required))) {
    throw "Compiled Store payload is incomplete: missing $required"
  }
}
if (@(Get-ChildItem (Join-Path $Destination 'data/flutter_assets') -Recurse -File).Count -eq 0) {
  throw 'Compiled Store payload is incomplete: Flutter assets are empty.'
}
Write-Host 'PASS: Store payload contains the compiled desktop executable, core, Flutter runtime and assets.'
