# Read-only query of the actual incoming support endpoint. Never arms the guard,
# writes preferences, starts a connection, or supplies a real invite/API key.
if (-not ('MixelSupportIpcProbe' -as [type])) {
  Add-Type @'
using System;
using System.IO;
using System.IO.Pipes;
using System.Text;
public static class MixelSupportIpcProbe {
  static void ReadExact(Stream stream, byte[] buffer) {
    int offset = 0;
    while (offset < buffer.Length) {
      var read = stream.ReadAsync(buffer, offset, buffer.Length - offset);
      if (!read.Wait(2000)) throw new TimeoutException("Incoming support IPC response timed out.");
      if (read.Result == 0) throw new IOException("Incoming support IPC closed before its response.");
      offset += read.Result;
    }
  }
  public static string Request(string json) {
    using (var pipe = new NamedPipeClientStream(".", "Mixel-Remote\\query", PipeDirection.InOut, PipeOptions.Asynchronous)) {
      pipe.Connect(2000);
      var body = Encoding.UTF8.GetBytes(json);
      if (body.Length > 16383) throw new InvalidDataException("IPC probe request exceeds its bound.");
      int headerLength = body.Length <= 63 ? 1 : 2;
      int header = (body.Length << 2) | (headerLength - 1);
      var packet = new byte[headerLength + body.Length];
      packet[0] = (byte)header;
      if (headerLength == 2) packet[1] = (byte)(header >> 8);
      Buffer.BlockCopy(body, 0, packet, headerLength, body.Length);
      var write = pipe.WriteAsync(packet, 0, packet.Length);
      if (!write.Wait(2000)) throw new TimeoutException("Incoming support IPC request timed out.");
      var first = new byte[1];
      ReadExact(pipe, first);
      int responseHeaderLength = (first[0] & 3) + 1;
      int responseHeader = first[0];
      for (int index = 1; index < responseHeaderLength; index++) {
        var next = new byte[1];
        ReadExact(pipe, next);
        responseHeader |= next[0] << (8 * index);
      }
      int size = responseHeader >> 2;
      if (size < 0 || size > 65536) throw new InvalidDataException("Incoming support IPC response exceeds its bound.");
      var response = new byte[size];
      ReadExact(pipe, response);
      return Encoding.UTF8.GetString(response);
    }
  }
}
'@
}

function Get-MixelSupportRuntimeHealth {
  $guard = [MixelSupportIpcProbe]::Request('{"t":"Config","c":["mixel-support-invite-attended",null]}') | ConvertFrom-Json
  if ($guard.t -ne 'Config' -or $guard.c -isnot [array] -or $guard.c.Count -ne 2 -or
      $guard.c[0] -ne 'mixel-support-invite-attended' -or $guard.c[1] -isnot [string]) {
    throw 'Incoming support IPC returned an unexpected guard response.'
  }
  $online = [MixelSupportIpcProbe]::Request('{"t":"OnlineStatus","c":null}') | ConvertFrom-Json
  if ($online.t -ne 'OnlineStatus' -or $online.c -isnot [array] -or $online.c.Count -ne 2 -or
      $online.c[0] -isnot [long] -or $online.c[1] -isnot [bool]) {
    throw 'Incoming support IPC returned an unexpected online response.'
  }
  $options = [MixelSupportIpcProbe]::Request('{"t":"Options","c":null}') | ConvertFrom-Json
  $expectedOptions = @{
    'custom-rendezvous-server' = 'rs.mixel.ch'
    'relay-server' = 'rs.mixel.ch'
    'key' = 'OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo='
  }
  if ($options.t -ne 'Options' -or $options.c -isnot [pscustomobject]) {
    throw 'Incoming support IPC returned unexpected relay options.'
  }
  foreach ($entry in $expectedOptions.GetEnumerator()) {
    $actual = $options.c.PSObject.Properties[$entry.Key]
    if ($null -eq $actual -or $actual.Value -isnot [string] -or $actual.Value -cne $entry.Value) {
      throw 'Actual incoming support server is not using the branded relay defaults.'
    }
  }
  if ($null -ne $options.c.PSObject.Properties['mixel-support-invite-attended']) {
    throw 'Attended support guard leaked into saved preferences.'
  }
  $rendezvous = [MixelSupportIpcProbe]::Request('{"t":"Config","c":["rendezvous_server",null]}') | ConvertFrom-Json
  if ($rendezvous.t -ne 'Config' -or $rendezvous.c -isnot [array] -or $rendezvous.c.Count -ne 2 -or
      $rendezvous.c[0] -cne 'rendezvous_server' -or $rendezvous.c[1] -isnot [string] -or
      $rendezvous.c[1].Split(',')[0] -cne 'rs.mixel.ch:21116') {
    throw 'Actual incoming rendezvous server does not match rs.mixel.ch.'
  }
  $device = [MixelSupportIpcProbe]::Request('{"t":"Config","c":["id",null]}') | ConvertFrom-Json
  if ($device.t -ne 'Config' -or $device.c -isnot [array] -or $device.c.Count -ne 2 -or
      $device.c[0] -cne 'id' -or $device.c[1] -isnot [string] -or
      $device.c[1] -cnotmatch '\A[a-zA-Z0-9-]{6,32}\z') {
    throw 'Actual incoming support server has no usable support device ID.'
  }
  return [pscustomobject]@{
    attendedProof = $guard.c[1]
    attendedReady = ($guard.c[1] -eq 'attended-runtime-v2')
    rendezvousState = $online.c[0]
    keyConfirmed = $online.c[1]
    registeredId = $device.c[1]
    rendezvousServer = $rendezvous.c[1].Split(',')[0]
    brandedRelay = $true
  }
}

function Wait-MixelSupportRuntimeHealth([string]$Scenario, [int]$TimeoutSeconds = 60, [switch]$RequireOnline) {
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  $last = 'Incoming support endpoint did not respond.'
  while ([DateTime]::UtcNow -lt $deadline) {
    try {
      $health = Get-MixelSupportRuntimeHealth
      $last = $health | ConvertTo-Json -Compress
      if ($health.attendedReady -and (-not $RequireOnline -or ($health.rendezvousState -gt 0 -and $health.keyConfirmed))) {
        Write-Host "PASS: $Scenario actual incoming IPC proves attended-runtime-v2, branded relay and key, registered ID; rendezvousState=$($health.rendezvousState), keyConfirmed=$($health.keyConfirmed)."
        return $health
      }
    } catch { $last = $_.Exception.Message }
    Start-Sleep -Milliseconds 500
  }
  throw "$Scenario failed: actual incoming support guard is unavailable: $last"
}
