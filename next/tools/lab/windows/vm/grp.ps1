# grp.ps1 <device name> [new group name]: row menu -> "Move to group." -> prints the controls of the dialog; with a name: types it into the new-group field and creates it, prints the list again
param([string]$Dev = 'QA linux host hb', [string]$New = '')
. C:\lab\uia.ps1
function ItemEl($name) { $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name); [System.Windows.Automation.AutomationElement]::RootElement.FindFirst('Descendants', $c) }
function Dlg { foreach ($e in (Find '' '' -Contains)) { $r = $e.Current.BoundingRectangle; $t = $e.Current.ControlType.ProgrammaticName -replace 'ControlType\.',''
  if ($r.X -gt 640 -and $r.X -lt 1300 -and $r.Y -gt 200 -and $r.Y -lt 800 -and $t -match 'Button|CheckBox|RadioButton|ListItem|Edit|Text|ComboBox') { "{0} {1} [{2},{3} {4}x{5}]" -f $t, $e.Current.Name, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height } } }
Focus-App; Nav 'devices'; Keys '{ESC}'
Inv (FindR "More for $Dev" 'Button'); Start-Sleep 2
Keys '{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}{DOWN}'; Keys '{ENTER}'; Start-Sleep 3
Shot C:\lab\G2.png; "DIALOG:"; Dlg | select -first 25
