. C:\lab\uia.ps1
Focus-App
ClickName 'Add this computer' 'Button' | Out-Null
Start-Sleep 2; Shot C:\lab\A-light-add-this.png
Dump -max 120 | Select-String 'Add|computer|Name|group|Cancel|Close'
