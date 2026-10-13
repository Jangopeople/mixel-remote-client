# Read-only query of the actual incoming support endpoint. Never arms the guard,
# writes preferences, starts a connection, or supplies a real invite/API key.
if (-not ('MixelSupportIpcProbe' -as [type])) {
  Add-Type @'
using System;
using System.ComponentModel;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class MixelSupportIpcProbe {
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint processId);
  public sealed class Response {
    public string Json { get; private set; }
    public uint ServerPid { get; private set; }
    public Response(string json, uint serverPid) { Json = json; ServerPid = serverPid; }
  }
  public static uint ConsistentServerPid(uint[] pids) {
    if (pids == null || pids.Length != 5 || pids[0] == 0) throw new InvalidDataException("Incoming support PID attribution is incomplete.");
    foreach (uint pid in pids) if (pid != pids[0]) throw new InvalidDataException("Incoming support IPC changed process during its health observation.");
    return pids[0];
  }
  static void ReadExact(Stream stream, byte[] buffer) {
    int offset = 0;
    while (offset < buffer.Length) {
      var read = stream.ReadAsync(buffer, offset, buffer.Length - offset);
      if (!read.Wait(2000)) throw new TimeoutException("Incoming support IPC response timed out.");
      if (read.Result == 0) throw new IOException("Incoming support IPC closed before its response.");
      offset += read.Result;
    }
  }
  public static string Request(string json) { return RequestWithServerPid(json).Json; }
  public static Response RequestWithServerPid(string json) {
    using (var pipe = new NamedPipeClientStream(".", "Mixel-Remote\\query", PipeDirection.InOut, PipeOptions.Asynchronous)) {
      pipe.Connect(2000);
      uint serverPid;
      if (!GetNamedPipeServerProcessId(pipe.SafePipeHandle, out serverPid) || serverPid == 0) throw new Win32Exception();
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
      return new Response(Encoding.UTF8.GetString(response), serverPid);
    }
  }
}
'@
}

function Get-MixelSupportRuntimeHealth {
  $guardReply = [MixelSupportIpcProbe]::RequestWithServerPid('{"t":"Config","c":["mixel-support-invite-attended",null]}')
  $guard = $guardReply.Json | ConvertFrom-Json
  if ($guard.t -isnot [string] -or $guard.t -cne 'Config' -or $guard.c -isnot [array] -or $guard.c.Count -ne 2 -or
      $guard.c[0] -isnot [string] -or $guard.c[0] -cne 'mixel-support-invite-attended' -or $guard.c[1] -isnot [string]) {
    throw 'Incoming support IPC returned an unexpected guard response.'
  }
  $onlineReply = [MixelSupportIpcProbe]::RequestWithServerPid('{"t":"OnlineStatus","c":null}')
  $online = $onlineReply.Json | ConvertFrom-Json
  if ($online.t -isnot [string] -or $online.t -cne 'OnlineStatus' -or $online.c -isnot [array] -or $online.c.Count -ne 2 -or
      ($online.c[0] -isnot [int] -and $online.c[0] -isnot [long]) -or $online.c[1] -isnot [bool]) {
    throw 'Incoming support IPC returned an unexpected online response.'
  }
  $optionsReply = [MixelSupportIpcProbe]::RequestWithServerPid('{"t":"Options","c":null}')
  $options = $optionsReply.Json | ConvertFrom-Json
  $expectedOptions = @{
    'custom-rendezvous-server' = 'rs.mixel.ch'
    'relay-server' = 'rs.mixel.ch'
    'key' = 'OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo='
  }
  if ($options.t -isnot [string] -or $options.t -cne 'Options' -or $options.c -isnot [pscustomobject]) {
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
  $rendezvousReply = [MixelSupportIpcProbe]::RequestWithServerPid('{"t":"Config","c":["rendezvous_server",null]}')
  $rendezvous = $rendezvousReply.Json | ConvertFrom-Json
  if ($rendezvous.t -isnot [string] -or $rendezvous.t -cne 'Config' -or $rendezvous.c -isnot [array] -or $rendezvous.c.Count -ne 2 -or
      $rendezvous.c[0] -isnot [string] -or $rendezvous.c[0] -cne 'rendezvous_server' -or $rendezvous.c[1] -isnot [string] -or
      $rendezvous.c[1].Split(',')[0] -cne 'rs.mixel.ch:21116') {
    throw 'Actual incoming rendezvous server does not match rs.mixel.ch.'
  }
  $deviceReply = [MixelSupportIpcProbe]::RequestWithServerPid('{"t":"Config","c":["id",null]}')
  $device = $deviceReply.Json | ConvertFrom-Json
  if ($device.t -isnot [string] -or $device.t -cne 'Config' -or $device.c -isnot [array] -or $device.c.Count -ne 2 -or
      $device.c[0] -isnot [string] -or $device.c[0] -cne 'id' -or $device.c[1] -isnot [string] -or
      $device.c[1] -cnotmatch '\A[a-zA-Z0-9-]{6,32}\z') {
    throw 'Actual incoming support server has no usable support device ID.'
  }
  return [pscustomobject]@{
    incomingPid = [MixelSupportIpcProbe]::ConsistentServerPid([uint32[]]@($guardReply.ServerPid, $onlineReply.ServerPid, $optionsReply.ServerPid, $rendezvousReply.ServerPid, $deviceReply.ServerPid))
    attendedProof = $guard.c[1]
    attendedReady = ($guard.c[1] -ceq 'attended-runtime-v2')
    rendezvousState = $online.c[0]
    keyConfirmed = $online.c[1]
    registeredId = $device.c[1]
    rendezvousServer = $rendezvous.c[1].Split(',')[0]
    brandedRelay = $true
  }
}

function Get-MixelSupportLeaseDiagnostic([uint32]$ExpectedIncomingPid) {
  $reply = [MixelSupportIpcProbe]::RequestWithServerPid('{"t":"Config","c":["mixel-support-invite-lease-status",null]}')
  $status = $reply.Json | ConvertFrom-Json
  if ($ExpectedIncomingPid -eq 0 -or $reply.ServerPid -ne $ExpectedIncomingPid -or
      $status.t -isnot [string] -or $status.t -cne 'Config' -or $status.c -isnot [array] -or $status.c.Count -ne 2 -or
      $status.c[0] -isnot [string] -or $status.c[0] -cne 'mixel-support-invite-lease-status' -or $status.c[1] -isnot [string]) {
    throw 'Actual incoming support endpoint returned an unattributed or invalid read-only lease observation.'
  }
  $codePattern = '(?:none|0|-?[1-9][0-9]{0,9})'
  $fields = [regex]::Match($status.c[1], ('\AownerFailed=(?<owner>true|false);ownerError=(?<ownerError>' + $codePattern + ');kernel=(?<kernel>active|absent|unavailable);probeError=(?<probeError>' + $codePattern + ');memoryActive=(true|false)\z'))
  if (-not $fields.Success -or
      (($fields.Groups['owner'].Value -ceq 'true') -ne ($fields.Groups['ownerError'].Value -cne 'none')) -or
      (($fields.Groups['kernel'].Value -ceq 'unavailable') -ne ($fields.Groups['probeError'].Value -cne 'none'))) {
    throw 'Actual incoming support endpoint returned an inconsistent read-only lease observation.'
  }
  foreach ($field in @('ownerError', 'probeError')) {
    $value = $fields.Groups[$field].Value
    [int]$nativeCode = 0
    if ($value -cne 'none' -and -not [int]::TryParse($value, [Globalization.NumberStyles]::AllowLeadingSign, [Globalization.CultureInfo]::InvariantCulture, [ref]$nativeCode)) {
      throw 'Actual incoming support endpoint returned an out-of-range native lease error.'
    }
  }
  return $status.c[1]
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
