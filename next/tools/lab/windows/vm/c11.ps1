Add-Type -Name W2 -Namespace X -MemberDefinition '[DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int pid);'
$p = Get-Process mixel-remote; $pid0 = 0
$ui = [X.W2]::GetWindowThreadProcessId($p.MainWindowHandle, [ref]$pid0); "UI thread id $ui"
$a = @{}; $p.Threads | %{ $a[$_.Id] = $_.TotalProcessorTime.TotalSeconds }
Start-Sleep 5
$p.Refresh(); $p.Threads | %{ $d = $_.TotalProcessorTime.TotalSeconds - $a[$_.Id]; if ($d -gt 0.2) { "thread $($_.Id) +$([math]::Round($d,2)) s state $($_.ThreadState) wait $($_.WaitReason)" } }
