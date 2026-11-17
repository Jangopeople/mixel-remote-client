#!/usr/bin/env bash
# hk.sh <hook line...>: one lab command for the Windows lab app (Lab/LabHook.cs), waits for and prints its answer
H="${VMHOST:-qa-win-v210}"; L="$*"
ssh "$H" "New-Item -ItemType Directory -Force C:\\lab\\hook | Out-Null; \$n=(Get-Content C:\\lab\\hook\\out.log -EA 0 | measure).Count; Set-Content -Encoding ascii C:\\lab\\hook\\cmd.tmp '$L'; Move-Item -Force C:\\lab\\hook\\cmd.tmp C:\\lab\\hook\\cmd; for(\$i=0;\$i -lt 150;\$i++){ Start-Sleep -Milliseconds 200; if((Get-Content C:\\lab\\hook\\out.log -EA 0 | measure).Count -gt \$n){ break } }; Get-Content C:\\lab\\hook\\out.log | select -Last 1" | cut -c1-600
