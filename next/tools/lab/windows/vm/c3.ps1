. C:\lab\uia.ps1
Focus-App
ClickAt 1085 662; Start-Sleep 1
ClickAt 1608 937; Start-Sleep 2
Shot C:\lab\A-light-add-from-panel.png
$d = @(Dump -max 150 | Select-String 'Name of this computer|Not connected'); "dialog/panel lines: " + ($d -join ' | ')
ClickAt 805 560; Start-Sleep 15
Shot C:\lab\A-light-added.png
Dump -max 100 | Select-String 'This computer|Online|ready|offline' | select -First 5
