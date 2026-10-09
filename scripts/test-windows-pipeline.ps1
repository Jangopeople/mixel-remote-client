$ErrorActionPreference = 'Stop'
$repository = Split-Path $PSScriptRoot -Parent
foreach ($file in (Get-ChildItem $PSScriptRoot -Filter '*.ps1' -File)) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) { throw "PowerShell parse failed: $($file.Name): $errors" }
}
Write-Host 'PASS: every Windows pipeline script parses cleanly.'

function Assert-Fails([scriptblock]$Action, [string]$Scenario) {
  $failed = $false
  try { & $Action } catch { $failed = $true }
  if (-not $failed) { throw "Expected failure was accepted: $Scenario" }
}

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('mixel-windows-pipeline-' + [Guid]::NewGuid())
try {
  $build = Join-Path $temporary 'build'
  $release = Join-Path $build 'x64/runner/Release'
  $payload = Join-Path $temporary 'payload'
  New-Item -ItemType Directory -Force (Join-Path $release 'data/flutter_assets') | Out-Null
  foreach ($file in @('mixel-remote.exe', 'libmixel-remote.dll', 'flutter_windows.dll', 'data/icudtl.dat', 'data/flutter_assets/AssetManifest.json')) {
    Set-Content (Join-Path $release $file) -Value 'compiled-test-fixture' -Encoding utf8
  }
  New-Item -ItemType Directory -Force $payload | Out-Null
  Set-Content (Join-Path $payload 'stale.dll') -Value 'must-be-removed'
  & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload
  if (-not (Test-Path (Join-Path $payload 'Mixel-Remote.exe')) -or (Test-Path (Join-Path $payload 'stale.dll'))) {
    throw 'Payload preparation failed to normalize the main name or remove stale bytes.'
  }
  Write-Host 'PASS: payload preparation starts clean and preserves the compiled application and assets.'
  Remove-Item (Join-Path $release 'data/flutter_assets/AssetManifest.json')
  Assert-Fails { & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload } 'empty Flutter assets'
  Set-Content (Join-Path $release 'data/flutter_assets/AssetManifest.json') -Value '{}'
  Remove-Item (Join-Path $release 'libmixel-remote.dll')
  Assert-Fails { & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload } 'missing core DLL'
  Set-Content (Join-Path $release 'libmixel-remote.dll') -Value 'compiled-test-fixture'
  $duplicate = Join-Path $build 'other/Release'
  New-Item -ItemType Directory -Force $duplicate | Out-Null
  Set-Content (Join-Path $duplicate 'mixel-remote.exe') -Value 'ambiguous-build'
  Assert-Fails { & (Join-Path $PSScriptRoot 'prepare-windows-store-payload.ps1') -BuildRoot $build -Destination $payload } 'ambiguous compiled application'
  Write-Host 'PASS: incomplete and ambiguous Windows builds fail closed.'
} finally {
  if (Test-Path $temporary) { Remove-Item $temporary -Recurse -Force }
}

. (Join-Path $PSScriptRoot 'support-runtime-probe-windows.ps1')
if (-not ('MixelSupportProbeFixture' -as [type])) { Add-Type @'
using System;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Threading.Tasks;
public static class MixelSupportProbeFixture {
  public static Task Serve(string[] responses) {
    return Task.Run(() => {
      foreach (string response in responses) {
        using (var pipe = new NamedPipeServerStream("Mixel-Remote\\query", PipeDirection.InOut, 1, PipeTransmissionMode.Byte)) {
          pipe.WaitForConnection();
          int first = pipe.ReadByte();
          int headerLength = (first & 3) + 1;
          int header = first;
          for (int i = 1; i < headerLength; i++) header |= pipe.ReadByte() << (8 * i);
          var request = new byte[header >> 2];
          int offset = 0;
          while (offset < request.Length) {
            int count = pipe.Read(request, offset, request.Length - offset);
            if (count == 0) throw new IOException("Fixture request was truncated");
            offset += count;
          }
          if (response == "oversized") {
            int largeHeader = (65537 << 2) | 2;
            for (int i = 0; i < 3; i++) pipe.WriteByte((byte)(largeHeader >> (8 * i)));
          } else if (response == "truncated") {
            pipe.WriteByte(5); // Two-byte response header, second byte missing.
          } else {
            var body = Encoding.UTF8.GetBytes(response);
            int length = body.Length <= 63 ? 1 : 2;
            int replyHeader = (body.Length << 2) | (length - 1);
            for (int i = 0; i < length; i++) pipe.WriteByte((byte)(replyHeader >> (8 * i)));
            // Exercise actual ReadExact framing with fragmented response bytes.
            foreach (byte value in body) pipe.WriteByte(value);
          }
          pipe.Flush();
        }
      }
    });
  }
}
'@ }

$guard = '{"t":"Config","c":["mixel-support-invite-attended","attended-runtime-v2"]}'
$online = '{"t":"OnlineStatus","c":[1,true]}'
$fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online))
$health = Get-MixelSupportRuntimeHealth
$fixture.GetAwaiter().GetResult()
if (-not $health.attendedReady -or -not $health.keyConfirmed -or $health.rendezvousState -ne 1) {
  throw 'Runtime IPC probe failed to parse a valid framed response.'
}
Write-Host 'PASS: actual named-pipe IPC decodes fragmented attended and online responses.'

foreach ($invalid in @('{"t":"OnlineStatus","c":[1,"false"]}', '{"t":"OnlineStatus","c":["1",true]}', '{"t":"OnlineStatus","c":[1]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'malformed online proof'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('oversized', 'truncated')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($invalid))
  Assert-Fails { [MixelSupportIpcProbe]::Request('{"t":"OnlineStatus","c":null}') } 'invalid IPC frame'
  $fixture.GetAwaiter().GetResult()
}
Write-Host 'PASS: malformed online proofs, oversized frames and truncated frames fail closed.'
