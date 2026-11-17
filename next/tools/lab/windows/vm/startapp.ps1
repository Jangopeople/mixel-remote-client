param([string]$Session = 'C:\lab\sess.json', [string]$Extra = '')
Get-Process mixel-remote -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 1
$env:MIXEL_QA_SESSION_FILE = $Session
if ($env:MIXEL_QA_APPEARANCE) { }
Start-Process "$env:LOCALAPPDATA\Mixel Remote\mixel-remote.exe" -WorkingDirectory "$env:LOCALAPPDATA\Mixel Remote"
Start-Sleep 6
(Get-Process mixel-remote -ErrorAction SilentlyContinue | select Id,MainWindowTitle | Out-String)
