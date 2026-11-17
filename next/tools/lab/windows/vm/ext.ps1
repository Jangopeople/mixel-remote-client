DisplaySwitch.exe /extend
Start-Sleep 4
Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::AllScreens | %{ $_.DeviceName+" "+$_.Bounds }
