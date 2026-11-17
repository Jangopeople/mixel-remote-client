# enroll.ps1: installs the always-on sign-in service from the lab app (deploy token read from C:\lab\deploy-cmd.txt, never printed)
$c = Get-Content C:\lab\deploy-cmd.txt -Raw
if ($c -match "\$t='(mrt_[A-Za-z0-9_]+)'") { $env:MIXEL_DEPLOY_TOKEN = $Matches[1] } else { 'no token'; exit 2 }
$p = Start-Process (Join-Path 'C:\Users\qa\AppData\Local\Mixel Remote' 'mixel-remote.exe') -ArgumentList '--signin-enroll' -Wait -PassThru
Remove-Item Env:MIXEL_DEPLOY_TOKEN -ErrorAction SilentlyContinue
"exit " + $p.ExitCode
Get-Service | ?{ $_.Name -match 'ixel' } | % { "$($_.Name) $($_.Status) $($_.StartType)" }
