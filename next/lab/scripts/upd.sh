#!/bin/bash
# upd.sh: build lab exe, install on both VMs (Win10 also updates the always-on service), start apps
set -e; L=/Volumes/MAC_Mini_Ext/Project_Mixel/mixel-remote-client/_scratch/lab
cd $L/../../.worktrees/managed-app && COPYFILE_DISABLE=1 tar --exclude target --exclude node_modules --exclude dist --exclude 'next/apps' --exclude 'next/meet' --exclude 'next/lab' --exclude 'next/crates/mobile_app/gen' -czf $L/qa-next.tgz next
cd $L; scp -q qa-next.tgz win-build: && ssh win-build 'powershell -NoProfile -ExecutionPolicy Bypass -File win-lab.ps1' 2>&1 | tail -2 | tee /dev/stderr | grep -q "BUILD exit 0"
ssh win-build 'powershell -NoProfile -Command "(Select-String -Path W:\MixelRemoteBuild\mixel-remote-client\_scratch\qa-lab-r2\build.log -Pattern ^warning,^error).Line"' | head
scp -q win-build:'W:/MixelRemoteBuild/mixel-remote-client/_scratch/qa-lab-r2/target/release/desktop_app.exe' mixel-remote-lab.exe
for h in qa-win10 qa-win11; do ssh ${h} 'Get-Process mixel-remote-lab -EA 0 | Stop-Process -Force; Start-Sleep 2'; scp -q mixel-remote-lab.exe "${h}:qa-lab/mixel-remote-lab.exe"; ./mint.sh ${h} >/dev/null; done
ssh qa-win10 'if(Get-Service MixelRemoteSignIn -EA 0){ sc.exe stop MixelRemoteSignIn | Out-Null; Start-Sleep 4; Copy-Item qa-lab\mixel-remote-lab.exe "C:\Program Files\Mixel Remote Sign-in Screen\mixel-remote-signin.exe" -Force; sc.exe start MixelRemoteSignIn | Out-Null }; powershell -NoProfile -ExecutionPolicy Bypass -File qa-lab\start-lab.ps1 | Select -Last 1'
ssh qa-win11 'powershell -NoProfile -ExecutionPolicy Bypass -File qa-lab\start-lab.ps1' | tail -1
