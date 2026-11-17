. C:\lab\uia.ps1
Focus-App
ClickAt 1795 363; Start-Sleep 8
Shot C:\lab\A-light-failed.png
$b = @(Find 'Add this computer' 'Button'); "buttons: " + $b.Count
$last = $b | sort { $_.Current.BoundingRectangle.Y } | select -Last 1
Click $last; Start-Sleep 3
Shot C:\lab\A-light-add-from-panel.png
(Dump -max 150 | Select-String 'Name of this computer|Not connected') -join ' | '
