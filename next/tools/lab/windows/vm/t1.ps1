. C:\lab\uia.ps1
$p = Get-Process mixel-remote | ?{ $_.MainWindowHandle -ne 0 } | select -First 1
[Win]::ShowWindow($p.MainWindowHandle, 3) | Out-Null; Start-Sleep 1
ClickName 'Sign in' 'Hyperlink' | Out-Null
Start-Sleep 1
Dump -max 60
