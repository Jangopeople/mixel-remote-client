# poll.ps1 <seconds> <outfile>: asks the lab hook for `status` about every 200 ms and writes "unix-ms phase fps frame" per answer (runs in an ssh session on the technician VM)
param([int]$Seconds = 120, [string]$Out = 'C:\lab\poll.txt')
New-Item -ItemType Directory -Force C:\lab\hook | Out-Null
Remove-Item $Out -ErrorAction SilentlyContinue
$end = (Get-Date).AddSeconds($Seconds); $w = New-Object IO.StreamWriter($Out, $false); $w.AutoFlush = $true
while ((Get-Date) -lt $end) {
  $n = @(Get-Content C:\lab\hook\out.log -ErrorAction SilentlyContinue).Count
  Set-Content -Encoding ascii C:\lab\hook\cmd.tmp 'status'; Move-Item -Force C:\lab\hook\cmd.tmp C:\lab\hook\cmd
  $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 50; if (@(Get-Content C:\lab\hook\out.log -ErrorAction SilentlyContinue).Count -gt $n) { break } }
  $l = (Get-Content C:\lab\hook\out.log -ErrorAction SilentlyContinue | Select-Object -Last 1)
  $t1 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  if ($l -match 'phase=(\S+) .*fps=(\d+) frame=(\S+)') { $w.WriteLine("$t0 $t1 $($Matches[1]) fps=$($Matches[2]) $($Matches[3])") }
  Start-Sleep -Milliseconds 100
}
$w.Close()
