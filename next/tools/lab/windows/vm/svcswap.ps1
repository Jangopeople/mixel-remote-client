# svcswap.ps1 old|new: the service's program swap while a session is open, with unix-ms marks. old = the update helper's script as shipped (stop, sleep 5, copy, start);
# new = stop, wait until the service has really stopped, copy at once, start.
param([string]$Mode = 'old')
function T { [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() }
$dst = 'C:\Program Files\Mixel Remote Sign-in Screen\mixel-remote-signin.exe'; $src = 'C:\lab\helper3.exe'
"start $(T)"
sc.exe stop MixelRemoteSignIn | Out-Null; "stop requested $(T)"
if ($Mode -eq 'old') { Start-Sleep 5 } else { while ((Get-Service MixelRemoteSignIn).Status -ne 'Stopped') { Start-Sleep -Milliseconds 50 }; "stopped $(T)"; while (Get-Process mixel-remote-signin -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 50 } }
"waited $(T)"
for ($i = 0; $i -lt 100; $i++) { try { Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop; break } catch { Start-Sleep -Milliseconds 100 } }
"copied $(T)"
sc.exe start MixelRemoteSignIn | Out-Null; "start requested $(T)"
while ((Get-Service MixelRemoteSignIn).Status -ne 'Running') { Start-Sleep -Milliseconds 50 }; "running $(T)"
