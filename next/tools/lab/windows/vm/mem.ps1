# prints cpu (percent of one core over 10 s) and working set of the app, and how long a hook round trip takes
$p = Get-Process mixel-remote; $c0 = $p.CPU; Start-Sleep 10; $p = Get-Process mixel-remote
"cpu {0:0}% of one core, ws {1} MB, threads {2}" -f (($p.CPU - $c0) * 10), [int]($p.WorkingSet64/1MB), $p.Threads.Count
