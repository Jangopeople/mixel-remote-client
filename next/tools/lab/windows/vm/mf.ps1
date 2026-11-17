. C:\lab\uia.ps1
Focus-App; Nav "devices"; Keys "{ESC}"; Find "More for" "Button" -Contains | %{ $r=$_.Current.BoundingRectangle; "{0} y={1}" -f $_.Current.Name,[int]$r.Y }
