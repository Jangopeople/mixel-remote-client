. C:\lab\uia.ps1
Focus-App; Nav 'devices'; Keys '{ESC}'
$btn = FindR "More for MIXEL-QA-WIN (2)" 'Button'
Inv $btn; Start-Sleep 2
$u = Find 'Unattended access' 'MenuItem' | select -first 1
try { $u.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern).Expand() } catch { "expand failed" }; Start-Sleep 1
Find 'Who may connect' '' -Contains | %{ "{0} {1} y={2} x={3}" -f $_.Current.ControlType.ProgrammaticName, $_.Current.Name, [int]$_.Current.BoundingRectangle.Y, [int]$_.Current.BoundingRectangle.X }
Shot C:\lab\M2.png; Keys '{ESC}'
