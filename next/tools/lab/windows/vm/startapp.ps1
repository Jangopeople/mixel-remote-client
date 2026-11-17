param([string]$Session = 'C:\lab\sess.json', [string]$Extra = '')
Get-Process mixel-remote -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 1
$env:MIXEL_QA_SESSION_FILE = $Session
$env:MIXEL_QA_VIDEO_LOG = "C:\lab\video.log"
if ($env:MIXEL_QA_APPEARANCE) { }
Remove-Item C:\lab\app.err,C:\lab\app.out -ErrorAction SilentlyContinue
Start-Process "$env:LOCALAPPDATA\Mixel Remote\mixel-remote.exe" -WorkingDirectory "$env:LOCALAPPDATA\Mixel Remote" -RedirectStandardError C:\lab\app.err -RedirectStandardOutput C:\lab\app.out
Start-Sleep 6
(Get-Process mixel-remote -ErrorAction SilentlyContinue | select Id,MainWindowTitle | Out-String)
