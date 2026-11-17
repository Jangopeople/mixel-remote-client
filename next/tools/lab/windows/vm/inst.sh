#!/usr/bin/env bash
# inst.sh [label]: copies the lab installer from win-build to the VM, installs it silently (stops the app first)
set -e; T=$(mktemp -d); scp -q "win-build:W:/MixelRemoteBuild/v210-win/out/Mixel-Remote-Windows-x64-2.1.0-lab.exe" $T/i.exe
scp -q $T/i.exe qa-win-v210:C:/lab/Mixel-Remote-Windows-x64-2.1.0-lab.exe; rm -rf $T
ssh qa-win-v210 'Get-Process mixel-remote* -EA 0 | Stop-Process -Force; Start-Sleep 2; & C:\lab\Mixel-Remote-Windows-x64-2.1.0-lab.exe /S; Start-Sleep 15; (Get-Item "$env:LOCALAPPDATA\Mixel Remote\mixel-remote.exe").LastWriteTime'
