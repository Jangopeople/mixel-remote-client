param([Parameter(Mandatory = $true)][string]$Payload)
$ErrorActionPreference = 'Stop'

# Native FileVersionInfo selects standard descriptive fields. Post-build package
# qualification additionally inspects every raw version string table/custom key.
function Assert-MixelPayloadVersionMetadata($Binary) {
  $info = $Binary.VersionInfo
  $descriptiveFields = @('Comments', 'CompanyName', 'FileDescription', 'FileVersion',
    'InternalName', 'LegalCopyright', 'LegalTrademarks', 'OriginalFilename',
    'PrivateBuild', 'ProductName', 'ProductVersion', 'SpecialBuild')
  foreach ($field in $descriptiveFields) {
    if ([string]$info.$field -match '(?i)rustdesk|purslane|com\.carriez|flutter_hbb') {
      throw "Retired Windows payload metadata: $($Binary.Name): $field."
    }
  }
  if ($Binary.Name -ieq 'desktop_multi_window_plugin.dll') {
    $expected = [ordered]@{
      CompanyName = 'Mixel IT and Corporate Services GmbH'
      FileDescription = 'Mixel Remote'
      FileVersion = '1.4.6'
      InternalName = 'desktop_multi_window_plugin'
      LegalCopyright = 'Copyright © 2026 Mixel IT and Corporate Services GmbH. All rights reserved.'
      OriginalFilename = 'desktop_multi_window_plugin.dll'
      ProductName = 'Mixel Remote'
      ProductVersion = '1.4.6'
    }
    foreach ($field in $expected.Keys) {
      if ([string]$info.$field -cne $expected[$field]) {
        throw "Invalid owned plugin metadata: $($Binary.Name): $field."
      }
    }
  }
}

if (-not (Test-Path -LiteralPath $Payload -PathType Container)) { throw 'Windows metadata payload directory is missing.' }
$binaries = @(Get-ChildItem -LiteralPath $Payload -Recurse -File -Force | Where-Object { $_.Extension -in '.exe', '.dll' })
if (@($binaries | Where-Object { $_.Name -ieq 'desktop_multi_window_plugin.dll' }).Count -ne 1) {
  throw 'Windows payload must contain exactly one desktop_multi_window_plugin.dll.'
}
foreach ($binary in $binaries) { Assert-MixelPayloadVersionMetadata $binary }
Write-Host "PASS: all $($binaries.Count) Windows PE selected version fields exclude retired branding; owned multi-window DLL metadata is exact."
