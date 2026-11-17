. C:\lab\uia.ps1
Focus-App; Keys "^1"; Start-Sleep 2
$c = @(Find 'Connect' 'Button' | ?{ $_.Current.BoundingRectangle.Y -gt 300 -and $_.Current.BoundingRectangle.Y -lt 400 })[0]; Click $c
Start-Sleep 15
Get-Process mixel-remote | %{ "cpu " + $_.CPU }
Start-Sleep 5; Get-Process mixel-remote | %{ "cpu " + $_.CPU }
Get-Content "$env:LOCALAPPDATA\ch.mixel.remote.next.qa\windows-app.log" -ErrorAction SilentlyContinue | select -Last 6
