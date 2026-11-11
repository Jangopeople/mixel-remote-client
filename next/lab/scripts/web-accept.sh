#!/bin/bash
# web-accept.sh [sound]: accept the pending request on the Win10 lab host (1024x768) with control; optionally play a sound there.
cd "$(dirname "$0")"
./q.sh click 279 358 1024 768; sleep 0.6; ./q.sh click 279 421 1024 768; sleep 1; ./q.sh click 876 677 1024 768; sleep 10
[ "$1" = sound ] && ssh qa-win10 "\$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\\Users\\qa\\qa-lab\\snd.ps1'; \$p = New-ScheduledTaskPrincipal -UserId 'qa' -LogonType Interactive -RunLevel Limited; Register-ScheduledTask -TaskName 'lab-snd' -Action \$a -Principal \$p -Force | Out-Null; Start-ScheduledTask -TaskName 'lab-snd'; Start-Sleep 12; Unregister-ScheduledTask -TaskName 'lab-snd' -Confirm:\$false"
echo done
