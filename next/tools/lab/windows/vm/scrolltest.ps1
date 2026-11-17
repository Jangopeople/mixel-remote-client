# scrolltest.ps1: a scrollable window (500 numbered lines) that logs what input reaches it (top visible line, wheel deltas, clicks, typed text) to C:\lab\scrolltest.log
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$f = New-Object Windows.Forms.Form; $f.Text = 'scrolltest'; $f.StartPosition = 'Manual'; $f.Location = '100,100'; $f.Size = '900,700'; $f.TopMost = $true
$t = New-Object Windows.Forms.TextBox; $t.Dock = 'Top'; $t.Font = 'Consolas,14'
$r = New-Object Windows.Forms.RichTextBox; $r.Dock = 'Fill'; $r.ReadOnly = $true; $r.Font = 'Consolas,14'; $r.Text = (1..500 | % { "line $_" }) -join "`n"
$f.Controls.Add($r); $f.Controls.Add($t)
$script:wheel = 0; $script:clicks = ''
$r.Add_MouseWheel({ param($s, $e) $script:wheel += $e.Delta })
$r.Add_MouseDown({ param($s, $e) $script:clicks += "click($($e.Button)@$($e.X),$($e.Y)) " })
$tm = New-Object Windows.Forms.Timer; $tm.Interval = 200
$tm.Add_Tick({ $top = $r.GetLineFromCharIndex($r.GetCharIndexFromPosition((New-Object Drawing.Point(5, 5)))); "top=$top wheel=$script:wheel text='$($t.Text)' $script:clicks" | Set-Content C:\lab\scrolltest.log })
$tm.Start()
$f.Add_Shown({ $f.Activate(); $t.Focus() })
[Windows.Forms.Application]::Run($f)
