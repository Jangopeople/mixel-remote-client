. C:\lab\uia.ps1
Focus-App
$b = @(Find 'Add this computer' 'Button') | sort { $_.Current.BoundingRectangle.Y } | select -Last 1
Click $b; Start-Sleep 15
Shot C:\lab\A-light-added.png
Dump -max 100 | Select-String 'This computer|Close|ID' | select -First 8
