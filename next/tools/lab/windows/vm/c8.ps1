. C:\lab\uia.ps1
Focus-App
$o = @(Find 'Open' 'Button'); "open buttons: " + $o.Count
if ($o.Count) { Click $o[0] }
Start-Sleep 5
Shot C:\lab\A-light-session.png
Dump -max 120 | Select-String 'Button|Pane|Text|Image|Custom' | select -First 60
