# v210.ps1 <core|corelab|app|applab|pack|packlab> [-Version 2.1.0]: build steps of the Windows test pass on win-build, all inside
# W:\MixelRemoteBuild\v210-win (own target dirs; nothing of other builds is touched). No secrets.
param([string]$Step = 'all', [string]$Version = '2.1.0')
$ErrorActionPreference = 'Continue'
$root = 'W:\MixelRemoteBuild\v210-win'; $src = "$root\src\next"; $app = "$src\apps\mixel-remote-windows"
New-Item -ItemType Directory "$root\logs","$root\out" -Force | Out-Null
function VsEnv { cmd /c "call `"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat`" -no_logo -arch=x64 -host_arch=x64 >nul 2>&1 && set" | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] } } }
function ETest {
  VsEnv
  $env:MIXEL_DESKTOP_SERVICE_CONFIG = "$src\deployment\public-service-config.json"; $env:MIXEL_ENGINE_APP_VERSION = $Version
  $env:CARGO_TARGET_DIR = "$root\target-test"; Set-Location $src
  foreach ($f in @('', '--features qa-session')) {
    cmd /c "cargo test --locked -p mixel_engine --lib $f self_update > `"$root\logs\etest.log`" 2>&1"; "ETEST [$f] exit $LASTEXITCODE"
    Get-Content "$root\logs\etest.log" | Select-String 'test result|FAILED|error' | Select-Object -First 4
  }
}
function Core($lab) {
  VsEnv
  $env:MIXEL_DESKTOP_SERVICE_CONFIG = "$src\deployment\public-service-config.json"
  $env:MIXEL_ENGINE_APP_VERSION = $Version
  $tag = if ($lab) { 'lab' } else { 'rel' }
  $env:CARGO_TARGET_DIR = "$root\target-$tag"
  $env:RUSTFLAGS = "-C target-feature=+crt-static --remap-path-prefix=$env:USERPROFILE=~ --remap-path-prefix=$src=next"
  Set-Location $src
  $feat = if ($lab) { '--features qa-session' } else { '' }
  cmd /c "cargo build --locked --release -p mixel_core_capi $feat > `"$root\logs\core-$tag.log`" 2>&1"; "CORE $tag exit $LASTEXITCODE"
  if ($LASTEXITCODE -ne 0) { Get-Content "$root\logs\core-$tag.log" -Tail 40 }
  Get-ChildItem "$env:CARGO_TARGET_DIR\release" -Filter 'mixel*' | Where-Object { $_.Extension -in '.dll','.exe' } | Format-Table Name, Length -auto
}
function App($lab) {
  $env:NUGET_PACKAGES = "$root\nuget"; $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'
  $tag = if ($lab) { 'lab' } else { 'rel' }
  $core = "$app\core-$tag"
  New-Item -ItemType Directory $core -Force | Out-Null
  Copy-Item "$root\target-$tag\release\mixel_core.dll", "$root\target-$tag\release\mixel-remote-helper.exe" $core -Force
  Set-Location $app
  $props = if ($lab) { "-p:Lab=true -p:MixelCore=$core" } else { "-p:MixelCore=$core" }
  cmd /c "dotnet publish src\MixelRemote.App\MixelRemote.App.csproj -c Release -r win-x64 $props -o `"$root\out\app-$tag`" > `"$root\logs\publish-$tag.log`" 2>&1"; "PUBLISH $tag exit $LASTEXITCODE"
  if ($LASTEXITCODE -ne 0) { Get-Content "$root\logs\publish-$tag.log" -Tail 40 }
  if ($lab) {
    cmd /c "dotnet build tools\MixelRemote.LabCheck\MixelRemote.LabCheck.csproj -c Release -o `"$root\out\labcheck`" > `"$root\logs\labcheck.log`" 2>&1"; "LABCHECK exit $LASTEXITCODE"
  }
}
function Pack($tag) {
  $out = "$root\out\Mixel-Remote-Windows-x64-$Version-$tag.exe"
  & 'C:\Users\builder\AppData\Local\tauri\NSIS\makensis.exe' /V2 "/DVERSION=$Version" "/DSOURCE=$root\out\app-$tag" "/DOUTFILE=$out" "$app\installer\mixel-remote.nsi"
  "MAKENSIS exit $LASTEXITCODE"; if (Test-Path $out) { "installer: $out " + (Get-Item $out).Length }
}
function Test {
  $env:NUGET_PACKAGES = "$root\nuget"; $env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'; $env:DOTNET_NOLOGO = '1'; Set-Location $app
  cmd /c "dotnet test tests\MixelRemote.Core.Tests\MixelRemote.Core.Tests.csproj -c Release --nologo > `"$root\logs\dotnet-test.log`" 2>&1"; "TEST exit $LASTEXITCODE"
  Get-Content "$root\logs\dotnet-test.log" | Select-String -Pattern 'Passed!|Failed!|error|Failed ' | Select-Object -First 30 | ForEach-Object { $_.ToString() }
}
switch ($Step) {
  'test' { Test } 'etest' { ETest }
  'core' { Core $false } 'corelab' { Core $true } 'app' { App $false } 'applab' { App $true } 'pack' { Pack 'rel' } 'packlab' { Pack 'lab' }
  'all' { Core $true; App $true; Pack 'lab' }
}
