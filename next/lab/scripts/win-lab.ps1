$ErrorActionPreference='Stop'
$work='W:\MixelRemoteBuild\mixel-remote-client\_scratch'; $src="$work\managed-session-port-r2"; $fix="$work\qa-lab-r2"
if(-not (Test-Path "$fix\next\frontends\desktop\node_modules")){
  robocopy "$src\next" "$fix\next" /E /XD target /NFL /NDL /NJH /NJS /NP | Out-Null; if($LASTEXITCODE -ge 8){ throw "robocopy next" }
  robocopy "$src\branding" "$fix\branding" /E /NFL /NDL /NJH /NJS /NP | Out-Null; if($LASTEXITCODE -ge 8){ throw "robocopy branding" }
}
New-Item -ItemType Directory "$fix\overlay" -Force | Out-Null
tar -xzf "$HOME\qa-next.tgz" -C "$fix\overlay"; if($LASTEXITCODE -ne 0){ throw 'untar' }
robocopy "$fix\overlay\next" "$fix\next" /E /NFL /NDL /NJH /NJS /NP | Out-Null; if($LASTEXITCODE -ge 8){ throw "overlay" }
Remove-Item "$fix\overlay" -Recurse -Force
cmd /c "call `"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat`" -no_logo -arch=x64 -host_arch=x64 >nul 2>&1 && set" | ForEach-Object { if($_ -match '^([^=]+)=(.*)$'){ Set-Item -Path "env:$($matches[1])" -Value $matches[2] } }
$env:MIXEL_DESKTOP_SERVICE_CONFIG="$fix\next\deployment\public-service-config.json"; $env:CARGO_TARGET_DIR="$fix\target"
Set-Location "$fix\next"
$sw=[Diagnostics.Stopwatch]::StartNew()
cmd /c "cargo build --locked --release -p desktop_app --features qa-session > `"$fix\build.log`" 2>&1"
"BUILD exit $LASTEXITCODE in $([int]$sw.Elapsed.TotalSeconds)s"
if($LASTEXITCODE -ne 0){ Get-Content "$fix\build.log" -Tail 25; exit 1 }
"exe " + (Get-FileHash "$fix\target\release\desktop_app.exe").Hash.Substring(0,16)
