Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^mixel-remote-lab' } | ForEach-Object { "killing $($_.Name)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$d="$env:USERPROFILE\qa-lab"
Set-Content "$d\run.cmd" "set MIXEL_QA_SESSION_FILE=$d\session.json`r`nset MIXEL_QA_SECOND_DISPLAY=1`r`nset MIXEL_QA_SOUND_LOG=$d\sound.txt`r`nset MIXEL_QA_VIDEO_LOG=$d\video.txt`r`nstart `"`" `"$d\mixel-remote-lab.exe`"" -Encoding ASCII
$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$d\run.cmd`""
$p = New-ScheduledTaskPrincipal -UserId 'qa' -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'lab-start' -Action $a -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName 'lab-start'; Start-Sleep 8
Unregister-ScheduledTask -TaskName 'lab-start' -Confirm:$false
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'mixel|desktop_app' } | ForEach-Object { "{0} {1} session {2}" -f $_.ProcessId,$_.Name,$_.SessionId }
