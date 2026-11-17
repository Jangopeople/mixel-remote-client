. C:\lab\uia.ps1
Focus-App; Keys "^1"; Start-Sleep 2
ClickAt 1795 330
Start-Sleep 2; Shot C:\lab\A-light-connecting.png
Start-Sleep 12; Shot C:\lab\A-light-after-connect.png
Dump -max 200 | Select-String 'Not connected|Add this|ready|Open|Close|Connecting|computer'
