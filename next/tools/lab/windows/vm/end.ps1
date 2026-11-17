. C:\lab\uia.ps1
Focus-App; ClickName 'End' 'Button' | Out-Null; Start-Sleep 2; Shot C:\lab\A-light-end-dialog.png
Dump -max 60 | Select-String 'End session|Cancel|Yes'
