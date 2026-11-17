# runi.ps1 <script.ps1> [args...]: on the VM, runs the script in the interactive desktop session of user qa (hidden), output to <script>.out, <script>.done when finished.
param([string]$Script, [Parameter(ValueFromRemainingArguments)][string[]]$Rest)
$b = [IO.Path]::GetFileNameWithoutExtension($Script)
Remove-Item "C:\lab\$b.done","C:\lab\$b.out" -ErrorAction SilentlyContinue
$arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"& '$Script' $($Rest -join ' ') *> 'C:\lab\$b.out'; Set-Content 'C:\lab\$b.done' 1`""
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
$p = New-ScheduledTaskPrincipal -UserId 'MIXEL-QA-WIN\qa' -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "lab_$b" -Action $a -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName "lab_$b"
