. C:\lab\uia.ps1
Focus-App
$o = @(Find 'Open' 'Button'); "open buttons: " + $o.Count
if ($o.Count) { Click $o[0] }
Start-Sleep 8
Shot C:\lab\A-light-session.png
Get-Process mixel-remote | %{ "cpu " + $_.CPU }
Dump -max 150 | Select-String 'Button|Pane|Custom|Image' | select -First 50
