param([Parameter(Mandatory = $true)][string]$Payload)
$ErrorActionPreference = 'Stop'
foreach ($required in @('Mixel-Remote.exe', 'libmixel-remote.dll', 'flutter_windows.dll', 'data/icudtl.dat', 'data/flutter_assets')) {
  if (-not (Test-Path (Join-Path $Payload $required))) { throw "Windows payload is missing $required" }
}
$binaries = @(Get-ChildItem $Payload -Recurse -File | Where-Object { $_.Extension -in '.exe', '.dll' })
if ($binaries.Count -lt 3) { throw 'Windows payload has no complete executable/core/runtime set.' }
foreach ($binary in $binaries) {
  $signature = Get-AuthenticodeSignature -FilePath $binary.FullName
  if ($signature.Status -ne 'Valid') { throw "Invalid payload signature: $($binary.Name): $($signature.Status)" }
}
Write-Host "PASS: all $($binaries.Count) Windows payload executable and DLL signatures are Valid."
