Add-Type -AssemblyName System.Windows.Forms
$p = [System.Windows.Forms.Cursor]::Position; "cursor $($p.X),$($p.Y)"
