# conn5.ps1 <host letter ha|hb|hd> <runs>: connect from the Devices row N times, print click / tab / marks per run (needs the app started with stderr redirected to C:\lab\app.err)
param([string]$H = 'ha', [int]$N = 5)
. C:\lab\uia.ps1
function Clk { [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() % 100000000) }
Focus-App
for ($i = 1; $i -le $N; $i++) {
  Nav 'devices'
  $off = (Get-Item C:\lab\app.err).Length
  $b = FindR "Connect to QA linux host $H" 'Button'
  $t0 = Clk; Inv $b | Out-Null
  $tab = $null
  for ($k = 0; $k -lt 150 -and -not $tab; $k++) { Start-Sleep -Milliseconds 100; if (@(Find "Session with QA linux host $H" 'Button').Count -gt 0) { $tab = Clk } }
  Start-Sleep 2
  $fs = [IO.File]::Open('C:\lab\app.err', 'Open', 'Read', 'ReadWrite'); $fs.Seek($off, 'Begin') | Out-Null
  $txt = (New-Object IO.StreamReader($fs)).ReadToEnd(); $fs.Close()
  $m = @{}; foreach ($l in ($txt -split "`n")) { if ($l -match 'QA connect \+(\d+) (.+?) \(clock (\d+)\)') { $m[$Matches[2]] = [int64]$Matches[3] } elseif ($l -match 'QA connect UI (.+?) \(clock (\d+)\)') { $m['UI ' + $Matches[1]] = [int64]$Matches[2] } }
  $ask = ($m.Keys | ?{ $_ -like 'connect asked*' } | select -first 1)
  $row = "run $i click=$t0"
  if ($ask) { $a = $m[$ask]; $row += " asked=+$($a-$t0)"
    foreach ($key in 'request sent','accepted','transport created','offer ready','answer applied','control channel open','session authenticated','video track open','first picture decoded','UI session view creat','UI session view created','UI first picture presented') { $kk = $m.Keys | ?{ $_ -like "$key*" } | select -first 1; if ($kk) { $row += " | $key +$($m[$kk]-$t0)" } } }
  if ($tab) { $row += " | TAB +$($tab-$t0)" } else { $row += " | TAB none" }
  $row
  # end the session
  Focus-App
  $e = @(Find 'End session' 'Button' | ?{ $_.Current.BoundingRectangle.X -gt 1500 })[0]; if ($e) { Inv $e | Out-Null; Start-Sleep 1
    $d = @(Find 'End session' 'Button' | ?{ $_.Current.BoundingRectangle.Y -gt 530 -and $_.Current.BoundingRectangle.Y -lt 580 })[0]; if ($d) { Inv $d | Out-Null } }
  Start-Sleep 4
}
