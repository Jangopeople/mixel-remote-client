. C:\lab\uia.ps1
Focus-App
$r = ClickName 'Sign in' 'Hyperlink'
if (-not $r) { $r = ClickName 'Sign in to Mixel' 'Hyperlink' -Contains }
Start-Sleep 1
if (ClickName 'Continue with Google' 'Button') { Start-Sleep 12 }
Focus-App
$b = @(Find 'Devices' 'Button' -Contains) | ?{ $_.Current.AutomationId -eq 'section-devices' } | select -First 1
if ($b) { Click $b }
"signed in: " + [bool]$b
