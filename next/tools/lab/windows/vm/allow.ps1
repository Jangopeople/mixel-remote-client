# allow.ps1: answers the consent window of the Windows host: ticks every box (screen, control, clipboard, files, chat, recording) and presses "Allow with control"
. C:\lab\uia.ps1
foreach ($n in 'I confirm sharing this whole display','Keyboard & mouse','Clipboard','Files','Chat') {
  $c = @(FindR $n 'CheckBox')[0]; if ($c) { $t = $c.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern); if ($t.Current.ToggleState -ne 'On') { $t.Toggle(); Start-Sleep -Milliseconds 300 }; "$n " + $t.Current.ToggleState }
}
Inv (FindR 'Allow with control' 'Button')
