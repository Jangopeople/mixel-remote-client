param([string]$Tag = 'light')
. C:\lab\uia.ps1
Focus-App
foreach ($s in 'devices','sessions','reports','settings') {
  $b = @(Find "section-$s" 'Button' -Contains) ; if (-not $b) { $b = @((AppWindows | %{ $_.FindAll([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, "section-$s"))) })) }
  if ($b) { Click $b[0]; Start-Sleep 2; Shot "C:\lab\A-$Tag-$s.png" } else { "no $s" }
}
"ok"
