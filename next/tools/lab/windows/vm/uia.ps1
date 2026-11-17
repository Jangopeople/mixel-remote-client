# uia.ps1: helpers for driving the app through UI Automation in the VM desktop. Dot-source it: . C:\lab\uia.ps1
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class Win { 
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, int d, int e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool r);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, int extra);
}
'@
function Root { [System.Windows.Automation.AutomationElement]::RootElement }
function AppWindows { $ids = (Get-Process mixel-remote -ErrorAction SilentlyContinue).Id
  $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, 0)
  foreach ($id in $ids) { $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$id)
    (Root).FindAll([System.Windows.Automation.TreeScope]::Children, $cond) | ForEach-Object { $_ } } }
function Find([string]$name, [string]$type = '', [switch]$Contains, $scope = $null) {
  $res = @(); $roots = if ($scope) { @($scope) } else { @(AppWindows) }
  foreach ($w in $roots) { foreach ($e in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
    try { $n = $e.Current.Name; $t = $e.Current.ControlType.ProgrammaticName -replace 'ControlType\.',''
      $ok = if ($Contains) { $n -like "*$name*" } else { $n -eq $name }
      if ($ok -and ($type -eq '' -or $t -eq $type)) { $res += $e } } catch {} } }
  $res }
function Center($e) { $r = $e.Current.BoundingRectangle; @([int]($r.X + $r.Width/2), [int]($r.Y + $r.Height/2)) }
function ClickAt([int]$x, [int]$y, [switch]$Right, [switch]$Double) {
  [Win]::SetCursorPos($x, $y) | Out-Null; Start-Sleep -Milliseconds 120
  $d = if ($Right) { 0x8 } else { 0x2 }; $u = if ($Right) { 0x10 } else { 0x4 }
  [Win]::mouse_event($d, 0, 0, 0, 0); [Win]::mouse_event($u, 0, 0, 0, 0)
  if ($Double) { Start-Sleep -Milliseconds 80; [Win]::mouse_event($d, 0, 0, 0, 0); [Win]::mouse_event($u, 0, 0, 0, 0) }
  Start-Sleep -Milliseconds 400 }
function Click($e, [switch]$Right) { $c = Center $e; ClickAt $c[0] $c[1] -Right:$Right }
function ClickName([string]$name, [string]$type = '', [switch]$Contains, [switch]$Right) { $e = @(Find $name $type -Contains:$Contains)[0]; if (-not $e) { "NOT FOUND: $name"; return $false }; Click $e -Right:$Right; $true }
function Keys([string]$k) { [System.Windows.Forms.SendKeys]::SendWait($k); Start-Sleep -Milliseconds 300 }
function Shot([string]$path) { $b = [System.Windows.Forms.SystemInformation]::VirtualScreen; $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose() }
function Dump($scope = $null, [int]$max = 400) { $roots = if ($scope) { @($scope) } else { @(AppWindows) }; $i = 0
  foreach ($w in $roots) { foreach ($e in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) { try {
    $t = $e.Current.ControlType.ProgrammaticName -replace 'ControlType\.',''; $r = $e.Current.BoundingRectangle
    if ($e.Current.Name -ne '' -and -not $e.Current.IsOffscreen) { "{0,-12} {1} [{2},{3} {4}x{5}] {6}" -f $t, $e.Current.Name, [int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height, $e.Current.AutomationId; $i++; if ($i -ge $max) { return } } } catch {} } } }
function Focus-App { $p = Get-Process mixel-remote -ErrorAction SilentlyContinue | ?{ $_.MainWindowHandle -ne 0 } | select -First 1; if ($p) { [Win]::ShowWindow($p.MainWindowHandle, 3) | Out-Null; [Win]::SetForegroundWindow($p.MainWindowHandle) | Out-Null } }
