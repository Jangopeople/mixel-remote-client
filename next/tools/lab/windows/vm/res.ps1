param([int]$W = 1920, [int]$H = 1080)
Add-Type @'
using System; using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)] public struct DEVMODE { [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName; public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields; public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput; public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName; public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency, dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight; }
public static class Disp { [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern int ChangeDisplaySettings(ref DEVMODE d, int flags); [DllImport("user32.dll", CharSet = CharSet.Ansi)] public static extern bool EnumDisplaySettings(string n, int m, ref DEVMODE d); }
'@
$d = New-Object DEVMODE; $d.dmSize = [int16][Runtime.InteropServices.Marshal]::SizeOf($d)
[Disp]::EnumDisplaySettings($null, -1, [ref]$d) | Out-Null
$d.dmPelsWidth = $W; $d.dmPelsHeight = $H; $d.dmFields = 0x80000 -bor 0x100000
"result " + [Disp]::ChangeDisplaySettings([ref]$d, 1)
