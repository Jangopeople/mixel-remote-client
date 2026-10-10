$ErrorActionPreference = 'Stop'
$repository = Split-Path $PSScriptRoot -Parent
foreach ($file in (Get-ChildItem $PSScriptRoot -Filter '*.ps1' -File)) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) { throw "PowerShell parse failed: $($file.Name): $errors" }
}
Write-Host 'PASS: every Windows pipeline script parses cleanly.'

# Compile the exact native OS fixture used by the real ordinary-to-QS runtime
# test; its policy checks are testable without launching or changing an app.
$launchSource = Get-Content (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') -Raw
$fixtureClass = $launchSource.IndexOf('public static class MixelOrdinaryTokenFixture {')
$fixtureStart = $launchSource.LastIndexOf('using System;', $fixtureClass)
$fixtureEnd = $launchSource.IndexOf("`n'@ }", $fixtureClass)
if ($fixtureClass -lt 0 -or $fixtureStart -lt 0 -or $fixtureEnd -le $fixtureStart) {
  throw 'Actual ordinary-token runtime fixture is missing.'
}
if (-not ('MixelOrdinaryTokenFixture' -as [type])) {
  Add-Type -TypeDefinition $launchSource.Substring($fixtureStart, $fixtureEnd - $fixtureStart)
}
foreach ($name in @('\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2', '\Sessions\0\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2')) {
  if (-not [MixelOrdinaryTokenFixture]::IsLeaseName($name)) { throw 'Exact runtime lease name rejected.' }
}
foreach ($name in @('', '\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v1', '\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2-other', '\Sessions\1\BaseNamedObjects\Mixel-Remote-Attended-Runtime-v2')) {
  if ([MixelOrdinaryTokenFixture]::IsLeaseName($name)) { throw 'Unrelated process event accepted as the global v2 lease.' }
}
if (-not ('MixelOwnedLeaseFixture' -as [type])) { Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MixelOwnedLeaseFixture {
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern IntPtr CreateEventExW(IntPtr attributes, string name, uint flags, uint access);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
  public static IntPtr Create() {
    IntPtr handle = CreateEventExW(IntPtr.Zero, "Global\\Mixel-Remote-Attended-Runtime-v2", 0, 0x100000);
    if (handle == IntPtr.Zero) throw new Win32Exception();
    return handle;
  }
}
'@ }
if ([MixelOrdinaryTokenFixture]::OwnsLease($PID)) { throw 'Fixture already owns the product event.' }
$fixtureHandle = [MixelOwnedLeaseFixture]::Create()
try {
  if (-not [MixelOrdinaryTokenFixture]::OwnsLease($PID)) { throw 'Actual SYNCHRONIZE event handle was not attributed to its process.' }
} finally {
  [void][MixelOwnedLeaseFixture]::CloseHandle($fixtureHandle)
}
if ([MixelOrdinaryTokenFixture]::OwnsLease($PID)) { throw 'Closed event handle remains attributed to its process.' }
Write-Host 'PASS: exact native runtime fixture observes the current PID owning the real SYNCHRONIZE-only global v2 event, then observes its handle release; unrelated event names fail closed.'

# Exercise the exact disposable-account bootstrap before spending time on a
# full signed build. The original runtime failure occurred before its GUI wait
# and was hidden by deleting a name whose account creation had failed.
if ($env:GITHUB_ACTIONS -ceq 'true' -and [MixelOrdinaryTokenFixture]::Elevated($PID)) {
  $accountStart = $launchSource.IndexOf('$random = [byte[]]::new(24)')
  $accountEnd = $launchSource.IndexOf("`$fixtureStage = 'add owned standard account to users'", $accountStart)
  if ($accountStart -lt 0 -or $accountEnd -le $accountStart) { throw 'Actual owned account bootstrap is missing.' }
  $ownedUser = 'mixelqs' + [Guid]::NewGuid().ToString('N').Substring(0, 10)
  $ownedSid = $null
  $descriptionRejected = $false
  $negativeSid = $null
  try {
    $negativeAccount = New-LocalUser -Name $ownedUser -NoPassword -Description 'Owned disposable Mixel QuickSupport runtime fixture'
    $negativeSid = $negativeAccount.SID.Value
  } catch [System.Management.Automation.ParameterBindingException] {
    if (-not $_.Exception.Message.Contains('Description') -or -not $_.Exception.Message.Contains('48')) { throw }
    $descriptionRejected = $true
  } finally {
    if ($negativeSid) { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($negativeSid)) }
  }
  if (-not $descriptionRejected) { throw 'Native New-LocalUser did not reject the original invalid 51-character fixture description.' }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) { throw 'Invalid-description negative control unexpectedly created an account.' }
  Write-Host 'PASS: actual New-LocalUser rejects the original invalid 51-character description before creating any account.'
  try {
    . ([scriptblock]::Create($launchSource.Substring($accountStart, $accountEnd - $accountStart)))
    if (-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) {
      throw 'Actual owned standard account was not created with its recorded SID.'
    }
    Write-Host 'PASS: actual ordinary runtime bootstrap creates its isolated standard account using memory-only random credentials.'
  } finally {
    $ownedPassword = $null
    $random = $null
    if ($ownedSid) { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) }
  }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) { throw 'Owned account remained after its actual SID was removed.' }
  Write-Host 'PASS: actual standard account cleanup removes only the created SID and leaves no test account.'
}

foreach ($required in @(
    "`$initialHealth.attendedProof -cne ''",
    '[MixelOrdinaryTokenFixture]::Elevated($main.Id)',
    '[MixelOrdinaryTokenFixture]::OwnsLease($main.Id)',
    '$warmLaunch.WaitForExit(60000)',
    '$started.Elapsed.TotalSeconds -lt 95',
    '$window -ne $originalWindow',
    '$initialHealth.incomingPid -ne $main.Id',
    'Assert-OwnedIncomingHealth $warmHealth',
    'Assert-OwnedIncomingHealth $currentHealth',
    'Assert-OwnedIncomingHealth $finalHealth',
    "-ArgumentList '--quick_support'",
    'actual customer QS portable launcher',
    'exact signed compiled QS argument (diagnostic)',
    '(-not $Portable -and -not $CompiledQuickSupportDiagnostic)',
    'Compiled diagnostic executable differs from the retained signed payload entry.',
    'if ($ownedSid) { try { Remove-LocalUser -SID',
    '$primaryFailure = $_',
    'if ($primaryFailure) { Write-Host',
    '-OrdinaryThenQuickSupport')) {
  if (-not $launchSource.Contains($required)) { throw "Ordinary-to-QS runtime proof lost a required native assertion: $required" }
}

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
if ([MixelSupportIpcProbe]::ConsistentServerPid([uint32[]]@(42, 42, 42, 42, 42)) -ne 42) {
  throw 'Consistent incoming server PID was rejected.'
}
foreach ($pids in @(@(0, 0, 0, 0, 0), @(42, 42, 43, 42, 42), @(42, 42, 42, 42))) {
  Assert-Fails { [MixelSupportIpcProbe]::ConsistentServerPid([uint32[]]$pids) } 'missing or inconsistent incoming server PID'
}
if (-not ('MixelSupportProbeFixture' -as [type])) { Add-Type @'
using System;
using System.Collections.Concurrent;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Threading.Tasks;
public static class MixelSupportProbeFixture {
  static readonly ConcurrentQueue<string> Requests = new ConcurrentQueue<string>();
  public static string[] Requested() { return Requests.ToArray(); }
  public static Task Serve(string[] responses) {
    string previous;
    while (Requests.TryDequeue(out previous)) { }
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
          Requests.Enqueue(Encoding.UTF8.GetString(request));
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
$options = '{"t":"Options","c":{"custom-rendezvous-server":"rs.mixel.ch","relay-server":"rs.mixel.ch","key":"OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo="}}'
$rendezvous = '{"t":"Config","c":["rendezvous_server","rs.mixel.ch:21116,rs.mixel.ch:21116"]}'
$device = '{"t":"Config","c":["id","123456789"]}'
$fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $options, $rendezvous, $device))
$health = Get-MixelSupportRuntimeHealth
$fixture.GetAwaiter().GetResult()
$expectedRequests = @(
  '{"t":"Config","c":["mixel-support-invite-attended",null]}',
  '{"t":"OnlineStatus","c":null}',
  '{"t":"Options","c":null}',
  '{"t":"Config","c":["rendezvous_server",null]}',
  '{"t":"Config","c":["id",null]}')
$actualRequests = [MixelSupportProbeFixture]::Requested()
if ($actualRequests.Count -ne $expectedRequests.Count) { throw 'IPC health probe issued an unexpected number of requests.' }
for ($index = 0; $index -lt $expectedRequests.Count; $index++) {
  if ($actualRequests[$index] -cne $expectedRequests[$index]) {
    throw 'IPC health probe must send only the exact framed read-only runtime queries.'
  }
}
if ($health.incomingPid -ne $PID -or -not $health.attendedReady -or -not $health.keyConfirmed -or $health.rendezvousState -ne 1 -or
    -not $health.brandedRelay -or $health.registeredId -ne '123456789' -or $health.rendezvousServer -ne 'rs.mixel.ch:21116') {
  throw 'Runtime IPC probe failed to parse a valid framed response.'
}
Write-Host 'PASS: actual named-pipe IPC decodes fragmented attended, online, branded relay/key, rendezvous and registered-ID responses.'
Write-Host 'PASS: actual framed health requests are read-only and never arm consent or change options.'
Write-Host 'PASS: all five actual named-pipe responses belong to the fixture server PID; missing or inconsistent PID attribution fails closed.'

foreach ($invalid in @('{"t":"OnlineStatus","c":[1,"false"]}', '{"t":"OnlineStatus","c":["1",true]}', '{"t":"OnlineStatus","c":[1]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'malformed online proof'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @(
    '{"t":"Options","c":[]}',
    $options.Replace('rs.mixel.ch', 'other.example'),
    $options.Replace('OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo=', 'wrong-public-key'),
    $options.Replace('"key":', '"mixel-support-invite-attended":"Y","key":'))) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'wrong/malformed relay defaults or persisted attended guard'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('{"t":"Config","c":["rendezvous_server","other.example:21116"]}', '{"t":"Config","c":["id","rs.mixel.ch:21116"]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $options, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'wrong/malformed rendezvous proof'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('{"t":"Config","c":["id",""]}', '{"t":"Config","c":["id",123456789]}', '{"t":"Config","c":["id","invalid id!"]}', '{"t":"Config","c":["id","123456789\n"]}', '{"t":"Config","c":["temporary-password","123456789"]}')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($guard, $online, $options, $rendezvous, $invalid))
  Assert-Fails { Get-MixelSupportRuntimeHealth } 'wrong/malformed registered ID'
  $fixture.GetAwaiter().GetResult()
}
foreach ($invalid in @('oversized', 'truncated')) {
  $fixture = [MixelSupportProbeFixture]::Serve(@($invalid))
  Assert-Fails { [MixelSupportIpcProbe]::Request('{"t":"OnlineStatus","c":null}') } 'invalid IPC frame'
  $fixture.GetAwaiter().GetResult()
}
Write-Host 'PASS: malformed online/relay/key/rendezvous/ID proofs, persisted consent, oversized frames and truncated frames fail closed.'
