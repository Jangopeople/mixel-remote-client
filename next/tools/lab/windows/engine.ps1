# engine.ps1 [test|build|all] [Features]: on win-build, the Rust engine of the sources unpacked by engine.sh (W:\MixelRemoteBuild\win-210\src\next):
# `test` = cargo test -p mixel_engine --lib + clippy -D warnings, `build` = the release core DLL (mixel_core_capi). The cargo cache is
# the one of the earlier Windows builds (registry crates are reused), logs are kept in W:\MixelRemoteBuild\win-210\logs. No secrets.
param([string]$Step = 'all', [string]$Features = '')
$ErrorActionPreference = 'Continue'
$root = 'W:\MixelRemoteBuild\win-210'; $src = "$root\src\next"
New-Item -ItemType Directory "$root\logs" -Force | Out-Null
cmd /c "call `"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat`" -no_logo -arch=x64 -host_arch=x64 >nul 2>&1 && set" | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] } }
$env:MIXEL_DESKTOP_SERVICE_CONFIG = "$src\deployment\public-service-config.json"
$env:MIXEL_ENGINE_APP_VERSION = '2.1.0'
$env:CARGO_TARGET_DIR = 'W:\MixelRemoteBuild\windows-native\target'
$env:RUSTFLAGS = "-C target-feature=+crt-static --remap-path-prefix=$env:USERPROFILE=~ --remap-path-prefix=$src=next"
Set-Location $src
if ($Step -in 'test','all') {
  cmd /c "cargo test --locked -p mixel_engine --lib > `"$root\logs\engine-test.log`" 2>&1"; "TEST exit $LASTEXITCODE"
  Select-String -Path "$root\logs\engine-test.log" -Pattern '^test result|FAILED|panicked' | Select-Object -Last 8 | ForEach-Object { $_.Line }
  cmd /c "cargo clippy --locked -p mixel_engine --all-targets -- -D warnings > `"$root\logs\engine-clippy.log`" 2>&1"; "CLIPPY exit $LASTEXITCODE"
  if ($LASTEXITCODE -ne 0) { Get-Content "$root\logs\engine-clippy.log" -Tail 30 }
}
if ($Step -in 'build','all') {
  $feat = if ($Features) { "--features $Features" } else { '' }
  cmd /c "cargo build --locked --release -p mixel_core_capi $feat > `"$root\logs\engine-build.log`" 2>&1"; "BUILD exit $LASTEXITCODE"
  if ($LASTEXITCODE -ne 0) { Get-Content "$root\logs\engine-build.log" -Tail 30 }
  Get-ChildItem "$env:CARGO_TARGET_DIR\release" -Filter 'mixel*' | Where-Object { $_.Extension -in '.dll','.exe' } | Format-Table Name, Length, LastWriteTime -auto
}
