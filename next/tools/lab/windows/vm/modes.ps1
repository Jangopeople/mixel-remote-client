Add-Type @'
using System; using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)] public struct DM { [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName; public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields; public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput; public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName; public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency, dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight; }
public static class D2 { [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern bool EnumDisplaySettings(string n, int m, ref DM d); }
'@
$d = New-Object DM; $d.dmSize = [int16][Runtime.InteropServices.Marshal]::SizeOf($d); $i = 0; $s = @{}
while ([D2]::EnumDisplaySettings($null, $i, [ref]$d)) { $s["$($d.dmPelsWidth)x$($d.dmPelsHeight)"] = 1; $i++ }
($s.Keys | sort) -join ' '
