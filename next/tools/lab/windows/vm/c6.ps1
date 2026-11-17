. C:\lab\uia.ps1
Focus-App
ClickAt 1085 644; Start-Sleep 1
ClickAt 1795 330; Start-Sleep 2; Shot C:\lab\A-light-connecting.png
for ($i=0; $i -lt 30; $i++) { Start-Sleep 1; if (@(Find 'Open' 'Button').Count -gt 0) { "open notice after $i s"; break } }
Shot C:\lab\A-light-ready-notice.png
Dump -max 300 | Select-String 'Open|ready|Session|In session|Dismiss'
