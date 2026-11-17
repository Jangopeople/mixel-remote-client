# who.ps1 <device name> [toggle name part]: row menu -> Unattended access -> "Who may connect..." ; prints the dialog's switches; with a name part toggles that one
param([string]$Dev = 'MIXEL-QA-WIN (2)', [string]$Toggle = '')
. C:\lab\uia.ps1
function ItemEl($name) { $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name); [System.Windows.Automation.AutomationElement]::RootElement.FindFirst('Descendants', $c) }
function Dlg { foreach ($e in (Find '' '' -Contains)) { $r = $e.Current.BoundingRectangle; $t = $e.Current.ControlType.ProgrammaticName -replace 'ControlType\.',''
  if ($r.X -gt 500 -and $r.X -lt 1500 -and $r.Y -gt 150 -and $r.Y -lt 900 -and $t -match 'Button|CheckBox|Text|Edit|Switch|Toggle') { $tg = ''; try { $tg = $e.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern).Current.ToggleState } catch {}; "{0} {1} {2}" -f $t, $e.Current.Name, $tg } } }
Focus-App; Nav 'devices'; Keys '{ESC}'; Keys '{ESC}'
$btn = FindR "More for $Dev" 'Button'
Inv $btn; Start-Sleep 2
Keys '{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}'; Keys '{RIGHT}'; Start-Sleep 1; Keys '{DOWN}{DOWN}{DOWN}'; Keys '{ENTER}'; Start-Sleep 7
if ($Toggle) { ClickAt 745 515; Start-Sleep 3 }
Shot C:\lab\WHO.png; "DIALOG:"; Dlg | select -first 20

Keys '{ESC}'
