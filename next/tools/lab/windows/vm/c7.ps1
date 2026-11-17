. C:\lab\uia.ps1
Focus-App
ClickName 'Open' 'Button' | Out-Null; Start-Sleep 6
Shot C:\lab\A-light-session.png
Dump -max 200 | Select-String 'Button|Pane|Text' | select -First 70
