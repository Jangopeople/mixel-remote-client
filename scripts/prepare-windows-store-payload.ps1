$ErrorActionPreference = 'Stop'
$client = Get-ChildItem rustdesk/flutter/build/windows -Recurse -Filter 'mixel-remote.exe' -File |
  Where-Object { $_.Directory.Name -eq 'Release' } | Select-Object -First 1
if (-not $client) { throw 'Compiled Windows application directory not found.' }
New-Item -ItemType Directory -Force -Path store-payload | Out-Null
Copy-Item -Path (Join-Path $client.Directory.FullName '*') -Destination store-payload -Recurse -Force

# The Store manifest starts this executable directly inside WindowsApps. Do not
# package the portable installer: it extracts to LocalAppData and loses identity.
$main = Get-ChildItem store-payload -Filter 'mixel-remote.exe' -File | Select-Object -First 1
Rename-Item -Path $main.FullName -NewName 'mixel-store-main.tmp'
Rename-Item -Path 'store-payload/mixel-store-main.tmp' -NewName 'Mixel-Remote.exe'
foreach ($required in @('Mixel-Remote.exe', 'libmixel-remote.dll', 'flutter_windows.dll', 'data/icudtl.dat', 'data/flutter_assets')) {
  if (-not (Test-Path (Join-Path store-payload $required))) {
    throw "Compiled Store payload is incomplete: missing $required"
  }
}
Write-Host 'PASS: Store payload contains the compiled desktop executable, core, Flutter runtime and assets.'
