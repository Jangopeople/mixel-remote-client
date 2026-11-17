. C:\lab\uia.ps1
Get-Process mixel-remote | %{ "cpu " + $_.CPU + " resp " + $_.Responding + " threads " + $_.Threads.Count + " ws " + [int]($_.WorkingSet64/1MB) }
$sw = [Diagnostics.Stopwatch]::StartNew(); $n = @(Dump -max 50).Count; "dump lines $n in " + $sw.ElapsedMilliseconds + " ms"
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-10)} -ErrorAction SilentlyContinue | ?{ $_.Message -match 'mixel' } | select -First 5 TimeCreated, Id, @{n='m';e={$_.Message.Substring(0,[Math]::Min(200,$_.Message.Length))}} | fl
