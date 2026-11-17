Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Screen]::AllScreens | %{ "$($_.DeviceName) $($_.Bounds) primary=$($_.Primary)" }
