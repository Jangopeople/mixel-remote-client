#!/usr/bin/env python3
"""Isolated source-fixture directory security controls; native Win32 unqualified."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

RUNTIME_PROJECTION = [('private static byte[] TokenBuffer(NativeApi api,IntPtr token,int kind,out int error) {', 'private static byte[] TokenBuffer(NativeApi api,IntPtr token,int kind,out int error,out bool querySucceeded) {'), ('    uint required; bool first=api.TokenInformation(token,kind,IntPtr.Zero,0,out required,out error);', '    querySucceeded=false;\n    uint required; bool first=api.TokenInformation(token,kind,IntPtr.Zero,0,out required,out error);'), ('      return result;\n    } finally { Marshal.FreeHGlobal(buffer); }', '      querySucceeded=true;\n      return result;\n    } finally { Marshal.FreeHGlobal(buffer); }'), ('        try { bytes=TokenBuffer(api,token,kind,out error); status=bytes==null?(error==0?"absent":"error"):"available"; failure=error; }', '        try { bool querySucceeded; bytes=TokenBuffer(api,token,kind,out error,out querySucceeded); status=!querySucceeded?"error":bytes==null?"absent":"available"; failure=error; }')]

def qualified_runtime_projection(source):
    for old,new in RUNTIME_PROJECTION:
        if source.count(new)!=1: raise AssertionError("Exact token query-success runtime projection changed")
        source=source.replace(new,old)
    return source.replace("// Isolated source fixture only. The parent observes two explicit existing object","// Artifact-only prototype. The parent observes two explicit existing object")

PROJECTION = [
    (r"""""", r"""  $directorySecuritySource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'test-support-directory-security.cs'))
  $directorySecurityClass = $directorySecuritySource.IndexOf('public static class DirectorySecurityPrototype {')
  if ($directorySecurityClass -lt 0 -or $directorySecurityClass -ne $directorySecuritySource.LastIndexOf('public static class DirectorySecurityPrototype {')) { throw 'Exact read-only directory security helper changed.' }
""", 1),
    (r"""""", r"""  public static DirectorySecurityPrototype.Observation StartedDirectorySecurityFacts(int pid,string ownedSid) {
    // The existing fixture created and verified this QA account locally. Only
    // redacted byte equality leaves the helper; neither SID nor account name is output.
    var identity=new SecurityIdentifier(ownedSid);
    byte[] bytes=new byte[identity.BinaryLength];identity.GetBinaryForm(bytes,0);
    return DirectorySecurityPrototype.ObserveRetained(new DirectorySecurityPrototype.NativeApi(),pid,StartedProcesses,bytes);
  }
""", 1),
    (r"""  Add-Type -TypeDefinition $normalSource.Insert($position, $comparison + "`n")
""", r"""  Add-Type -TypeDefinition ("using System.Collections.Generic;`n" + $normalSource.Insert($position, $comparison + "`n") + "`n" + $directorySecuritySource.Substring($directorySecurityClass))
""", 1),
    (r"""      if(descriptor!=IntPtr.Zero) DiagnosticDescriptors.Add(descriptor);
      // GetSecurityInfo returns its own DWORD error, not the thread's last error.
      if(result!=0) throw new Win32Exception(unchecked((int)result),"Owned World event DACL/LABEL query failed; nativeError="+result.ToString(System.Globalization.CultureInfo.InvariantCulture));
      if(descriptor==IntPtr.Zero) throw new InvalidOperationException("Owned World event descriptor query returned no allocation");
""", r"""      // GetSecurityInfo returns its own DWORD error. Failed outputs do not
      // establish allocation ownership, even when the output pointer is nonzero.
      if(result!=0) { descriptor=IntPtr.Zero; throw new Win32Exception(unchecked((int)result),"Owned World event DACL/LABEL query failed; nativeError="+result.ToString(System.Globalization.CultureInfo.InvariantCulture)); }
      if(descriptor==IntPtr.Zero) throw new InvalidOperationException("Owned World event descriptor query returned no allocation");
      DiagnosticDescriptors.Add(descriptor);
""", 1),
    (r"""""", r"""function Invoke-LeaseDirectorySecurityObservation([uint32]$ExpectedPid) {
  try {
    if ($ExpectedPid -eq 0 -or -not $ownedSid) { throw 'Retained directory observation requires the exact child and owned QA identity.' }
    $facts = [MixelOrdinaryTokenFixture]::StartedDirectorySecurityFacts($ExpectedPid,$ownedSid)
    if ($facts -isnot [DirectorySecurityPrototype+Observation] -or $facts.schema -ne 1 -or $facts.pid -ne $ExpectedPid -or
        $facts.scope -cne 'ExplicitDirectorySecurityOnly' -or $facts.context -cne 'ParentCall' -or
        $facts.identityComparison -cne 'PrimaryTokensOnly' -or $facts.parentCallingThreadTokenMeasured -or
        $facts.actualEventRouteMeasured -or $facts.effectiveAccessCalculated -or $facts.objectsCreated -or $facts.causeEstablished -or
        $facts.directories.Count -ne 2 -or $facts.directories[0].pathKind -cne 'global' -or $facts.directories[1].pathKind -cne 'child-session') {
      throw 'Read-only directory security facts changed their exact retained subject or measurement scope.'
    }
    $json = $facts | ConvertTo-Json -Depth 8 -Compress
    if ($json.Length -gt 131072 -or $json.Contains('S-1-') -or $json.Contains('SecurityIdentifier')) { throw 'Read-only directory security output exceeds its bounded redacted schema.' }
    Write-Host "OBSERVED: parent read-only explicit directory security; controlPid=$ExpectedPid; information=DACL4,LABEL16; requestedAccess=READ_CONTROL; ActualEventRouteUnmeasured=True; $json."
    $failures = [Collections.Generic.List[Exception]]::new()
    foreach ($stage in @('load','unload')) {
      $expected = if ($stage -ceq 'load') { 'available' } else { 'unloaded' }
      if ($facts.$stage -cne $expected) {
        $failures.Add([InvalidOperationException]::new("Directory security module stage incomplete: $stage=$($facts.$stage)."))
        $code = if ($stage -ceq 'load') { $facts.loadError } else { $facts.unloadError }
        if (($stage -ceq 'unload' -and $facts.unload -ceq 'error') -or
            ($stage -ceq 'load' -and $facts.load -cin @('system-directory-error','load-error','api-unavailable'))) {
          if ($null -ne $code) { $failures.Add([ComponentModel.Win32Exception]::new([int]$code,"Directory security native module failure: stage=$stage.")) }
        }
      }
    }
    foreach ($subject in @('current','child')) {
      $token = $facts.$subject
      if ($token.open -cne 'available' -or $token.user -cne 'available' -or $token.logon -cnotin @('available','absent') -or
          $token.integrity -cne 'available' -or $token.close -cne 'closed') {
        $failures.Add([InvalidOperationException]::new("Directory security primary comparison incomplete: subject=$subject."))
      }
      foreach ($field in @('open','user','logon','integrity','close')) {
        if ($token.$field -ceq 'error' -and $null -ne $token.($field + 'Error')) {
          $failures.Add([ComponentModel.Win32Exception]::new([int]$token.($field + 'Error'),"Directory security native primary comparison failure: subject=$subject; stage=$field."))
        }
      }
    }
    foreach ($row in $facts.directories) {
      # A literal nonzero NTSTATUS is an explicit read-access result. Do not
      # invent a Win32 mapping or use it to diagnose the unchanged event route.
      if ($row.open -ceq 'nt-error') {
        if ($null -eq $row.ntStatus -or $row.ntStatus -eq 0 -or $row.close -cne 'not-opened') { throw 'Read-only directory security NT result or failed-output ownership changed.' }
        continue
      }
      if ($row.open -cne 'opened' -or $row.ntStatus -ne 0 -or $row.close -cne 'closed') {
        $failures.Add([InvalidOperationException]::new("Directory security open/close contract incomplete: path=$($row.pathKind)."))
      }
      if ($row.close -ceq 'error' -and $null -ne $row.closeError) {
        $failures.Add([ComponentModel.Win32Exception]::new([int]$row.closeError,"Directory security owned directory close failed: path=$($row.pathKind)."))
      }
      foreach ($field in @('dacl','label')) {
        $query = $row.$field
        if ($query.information -ne $(if ($field -ceq 'dacl') { 4 } else { 16 }) -or $query.aces.Count -gt 64) {
          throw 'Read-only directory security query identity or ACE bound changed.'
        }
        if ($query.status -ceq 'error') {
          $code = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$query.nativeError),0)
          $failures.Add([ComponentModel.Win32Exception]::new($code,"Directory security direct query failed: path=$($row.pathKind); information=$($query.information)."))
        } elseif ($query.status -cne 'available' -or $query.free -cne 'freed' -or
                  $query.parse -cnotin @('complete','empty','absent','null','unknown-ace')) {
          $failures.Add([InvalidOperationException]::new("Directory security query/getter/cleanup incomplete: path=$($row.pathKind); information=$($query.information)."))
        }
        if ($query.parse -cin @('control-error','getter-error','acl-api-error') -and $null -ne $query.getterError) {
          $failures.Add([ComponentModel.Win32Exception]::new([int]$query.getterError,"Directory security native descriptor/ACL getter failed: path=$($row.pathKind); information=$($query.information)."))
        }
        if ($query.free -ceq 'error' -and $null -ne $query.freeError) {
          $failures.Add([ComponentModel.Win32Exception]::new([int]$query.freeError,"Directory security owned descriptor release failed: path=$($row.pathKind); information=$($query.information)."))
        }
        # Unknown ACEs are reported as incomplete facts, never a permission
        # calculation. The bounded header facts remain useful and are not errors.
      }
    }
    if ($failures.Count -gt 0) { throw [AggregateException]::new('Read-only explicit directory security observation incomplete.', $failures) }
  } catch { Write-LeaseContextFailure explicit-directory-security $_.Exception }
}

""", 1),
    (r"""""", r"""    Invoke-LeaseDirectorySecurityObservation $controlPid
""", 2),
    ("[ValidateSet('job-membership','job-limits','explicit-directory-access','owned-world-descriptor')]",
     "[ValidateSet('job-membership','job-limits','explicit-directory-access','explicit-directory-security','owned-world-descriptor')]",1),
]

def project_standard_source(source):
    for original,added,count in PROJECTION:
        if source.count(added)!=count:
            raise AssertionError("Exact directory diagnostic integration projection changed")
        source=source.replace(added,original)
    if hashlib.sha256(source.encode()).hexdigest()!="12994b9179c150231b580d78bd64b17fca68ba1a9d819bf5ac3b5d3cd3101b62":
        raise AssertionError("Unrelated original lease source changed")
    return source

CSHARP_CONTROLS = r"""using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public static class DirectorySecurityControls {
  static void Check(bool value,string message) { if(!value) throw new InvalidOperationException(message); }
  static byte[] Sid(byte authority,params uint[] parts) {
    byte[] bytes=new byte[8+4*parts.Length]; bytes[0]=1; bytes[1]=(byte)parts.Length; bytes[7]=authority;
    for(int i=0;i<parts.Length;i++) Buffer.BlockCopy(BitConverter.GetBytes(parts[i]),0,bytes,8+4*i,4); return bytes;
  }
  static byte[] BuildAce(byte type,uint mask,byte[] sid) {
    byte[] bytes=new byte[8+sid.Length]; bytes[0]=type; Buffer.BlockCopy(BitConverter.GetBytes((ushort)bytes.Length),0,bytes,2,2);
    Buffer.BlockCopy(BitConverter.GetBytes(mask),0,bytes,4,4); Buffer.BlockCopy(sid,0,bytes,8,sid.Length); return bytes;
  }
  static byte[] BuildAcl(params byte[][] aces) {
    int size=8; foreach(byte[] ace in aces) size+=ace.Length;
    byte[] bytes=new byte[size]; bytes[0]=2;
    Buffer.BlockCopy(BitConverter.GetBytes((ushort)size),0,bytes,2,2); Buffer.BlockCopy(BitConverter.GetBytes((ushort)aces.Length),0,bytes,4,2);
    int offset=8; foreach(byte[] ace in aces) { Buffer.BlockCopy(ace,0,bytes,offset,ace.Length); offset+=ace.Length; } return bytes;
  }
  public sealed class Fake : DirectorySecurityPrototype.NativeApi,IDisposable {
    public readonly string scenario; public int loadCalls,unloadCalls,opens,queries,frees,closes,tokenOpens,tokenCalls;
    public readonly List<string> paths=new List<string>(); public readonly List<uint> information=new List<uint>();
    private readonly HashSet<IntPtr> handles=new HashSet<IntPtr>(); private readonly Dictionary<IntPtr,uint> allocations=new Dictionary<IntPtr,uint>();
    private readonly Dictionary<IntPtr,uint> allocationKinds=new Dictionary<IntPtr,uint>(); private bool loaded;
    public int UnresolvedHandles { get { return handles.Count; } }
    public int UnresolvedAllocations { get { return allocations.Count; } }
    public bool UnresolvedModule { get { return loaded; } }
    public Fake(string scenario) { this.scenario=scenario; }
    public override IntPtr CurrentProcess() { return new IntPtr(-1); }
    public override uint ProcessId(IntPtr process,out int error) { Check(process==new IntPtr(55),"Unexpected borrowed child"); error=0; return scenario=="pid-mismatch"?99u:4321u; }
    public override bool Session(uint pid,out uint session,out int error) { Check(pid==4321,"Unexpected session PID"); session=2; error=scenario=="session-error"?5:0; return error==0; }
    public override bool OpenToken(IntPtr process,out IntPtr token,out int error) {
      Check(process==CurrentProcess() || process==new IntPtr(55),"Other process token inspected"); tokenOpens++;
      if(scenario=="token-open-error") { token=new IntPtr(1); error=5; return false; }
      if(scenario=="token-invalid-minus-one" || scenario=="token-invalid-zero") { token=scenario=="token-invalid-zero"?IntPtr.Zero:new IntPtr(-1); error=0; return true; }
      token=new IntPtr(process==CurrentProcess()?101:102); error=0; Check(handles.Add(token),"Duplicate token ownership"); return true;
    }
    public override bool TokenInformation(IntPtr token,int kind,IntPtr data,uint length,out uint returned,out int error) {
      tokenCalls++; Check(handles.Contains(token) && (token==new IntPtr(101) || token==new IntPtr(102)),"Token query unowned");
      bool current=token==new IntPtr(101) || scenario=="high-child"; Check(kind==1 || kind==2 || kind==25,"Unexpected token class");
      uint[] parts=kind==2?new uint[] {5,current?11u:22u,33}:kind==25?new uint[] {current?12288u:8192u}:new uint[] {21,current?100u:200u};
      byte[] sid=Sid(kind==25?(byte)16:(byte)5,parts); int offset=kind==2?24:16;
      returned=(uint)(offset+sid.Length);
      if(kind==2 && (scenario=="token-groups-empty8" || scenario=="token-groups-short7")) returned=scenario=="token-groups-empty8"?8u:7u;
      if(data==IntPtr.Zero) { Check(length==0,"Token initial size changed"); error=kind==2 && scenario=="logon-first-error0"?0:scenario=="token-first-error"?5:122; return false; }
      Check(length==returned,"Exact token allocation changed"); error=0;
      if(scenario=="token-read-error" || kind==2 && scenario=="logon-read-error0") { error=scenario=="logon-read-error0"?0:5; return false; }
      if(kind==2 && scenario=="token-groups-empty8") {Marshal.WriteInt32(data,0);return true;}
      if(kind==2) {
        Marshal.WriteInt32(data,1); Marshal.WriteIntPtr(data,8,IntPtr.Add(data,offset)); Marshal.WriteInt32(data,16,unchecked((int)0xc0000000));
        if(scenario=="token-group-count") Marshal.WriteInt32(data,1025);
      } else Marshal.WriteIntPtr(data,scenario=="token-pointer-outside"?new IntPtr(1):IntPtr.Add(data,offset));
      Marshal.Copy(sid,0,IntPtr.Add(data,offset),sid.Length); return true;
    }
    public override string Load(out int error) {
      loadCalls++; Check(!loaded,"Duplicate module load");
      if(scenario=="load-error") { error=126; return "load-error"; }
      loaded=true; error=scenario=="api-unavailable"?127:0; return scenario=="api-unavailable"?"api-unavailable":"available";
    }
    public override string Unload(out int error) { unloadCalls++; error=scenario=="unload-error"?6:0; string state=loaded?(error==0?"unloaded":"error"):"not-loaded"; if(error==0) loaded=false; return state; }
    public override int OpenDirectory(out IntPtr handle,uint access,ref DirectorySecurityPrototype.ObjectAttributes attributes) {
      opens++; Check(loaded && access==0x20000,"Not READ_CONTROL-only loaded API");
      Check(attributes.Length==48 && attributes.RootDirectory==IntPtr.Zero && attributes.Attributes==64 && attributes.SecurityDescriptor==IntPtr.Zero && attributes.SecurityQualityOfService==IntPtr.Zero,"OBJECT_ATTRIBUTES contract changed");
      var value=(DirectorySecurityPrototype.UnicodeString)Marshal.PtrToStructure(attributes.ObjectName,typeof(DirectorySecurityPrototype.UnicodeString));
      string path=Marshal.PtrToStringUni(value.Buffer,value.Length/2); paths.Add(path);
      Check(value.Length==path.Length*2 && value.MaximumLength==value.Length+2,"Unicode length/NUL contract changed");
      Check(path==(opens==1?"\\BaseNamedObjects":"\\Sessions\\2\\BaseNamedObjects"),"Not two explicit directories");
      if(scenario=="nt-denied") { handle=new IntPtr(1); return unchecked((int)0xc0000022); }
      if(scenario=="nt-missing") { handle=new IntPtr(1); return unchecked((int)0xc0000034); }
      if(scenario=="null-handle") { handle=IntPtr.Zero; return 0; }
      if(scenario=="borrowed-handle") { handle=new IntPtr(55); return 0; }
      handle=new IntPtr(200+opens); Check(handles.Add(handle),"Duplicate directory ownership"); return 0;
    }
    public override uint Query(IntPtr handle,uint kind,out IntPtr descriptor) {
      queries++; information.Add(kind); Check(handles.Contains(handle) && handle.ToInt64()>200 && (kind==4 || kind==16),"Wrong handle/query flags");
      if(scenario=="query-error" || scenario=="dacl-query-error" && kind==4 || scenario=="label-query-error" && kind==16) { descriptor=new IntPtr(1); return kind==4?5u:1314u; }
      if(scenario=="missing-descriptor") { descriptor=IntPtr.Zero; return 0; }
      byte[] acl;
      if(kind==4) acl=BuildAcl(BuildAce(0,4,Sid(5,21,200)),BuildAce(1,0x100000,Sid(1,0)),BuildAce(0,2,Sid(5,5,22,33)));
      else acl=BuildAcl(BuildAce(0x11,1,Sid(16,12288)));
      if(scenario=="high-child" && kind==4) acl=BuildAcl(BuildAce(0,4,Sid(5,21,100)),BuildAce(0,4,Sid(5,5,11,33)),BuildAce(0,4,Sid(5,21,200)));
      if(scenario=="empty") acl=BuildAcl();
      if(scenario=="unknown") acl=BuildAcl(new byte[] {9,0,4,0});
      if(scenario=="acl-size") { acl[2]=255; acl[3]=255; }
      if(scenario=="acl-count") { acl[4]=255; acl[5]=255; }
      if(scenario=="ace-size") { acl[10]=0; acl[11]=0; }
      if(scenario=="sid-size") acl[17]=15;
      if(scenario=="sid-revision") acl[16]=2;
      descriptor=Marshal.AllocHGlobal(20+acl.Length); Marshal.Copy(new byte[20+acl.Length],0,descriptor,20+acl.Length); Marshal.Copy(acl,0,IntPtr.Add(descriptor,20),acl.Length);
      allocations.Add(descriptor,(uint)(20+acl.Length)); allocationKinds.Add(descriptor,kind); return 0;
    }
    public override string DescriptorSize(IntPtr descriptor,out uint length,out int error) {
      Check(allocations.ContainsKey(descriptor),"Unowned descriptor validation"); length=scenario=="acl-oversize-free"?1048576:allocations[descriptor]; error=0;
      return scenario=="invalid-descriptor"?"invalid-descriptor":scenario=="not-self-relative"?"not-self-relative":"available";
    }
    public override bool Acl(IntPtr descriptor,uint kind,out bool present,out IntPtr acl,out bool defaulted,out int error) {
      Check(allocations.ContainsKey(descriptor) && allocationKinds[descriptor]==kind,"Wrong descriptor getter");
      present=scenario!="absent" && !(scenario=="absent-dacl" && kind==4) && !(scenario=="absent-label" && kind==16);
      defaulted=true; acl=present?(scenario=="null"?IntPtr.Zero:scenario=="acl-outside"?new IntPtr(1):IntPtr.Add(descriptor,20)):new IntPtr(1);
      error=scenario=="getter-error"?5:0; return error==0;
    }
    public override bool AclSize(IntPtr acl,out uint count,out uint used,out uint free,out int error) {
      IntPtr descriptor=IntPtr.Subtract(acl,20); Check(allocations.ContainsKey(descriptor),"Unowned ACL size query");
      count=scenario=="acl-count"?65u:unchecked((ushort)Marshal.ReadInt16(acl,4));
      used=scenario=="acl-size"?65535u:allocations[descriptor]-20; free=scenario=="acl-oversize-free"?65536u:0; error=scenario=="acl-size-api-error"?5:0; return error==0;
    }
    public override bool Ace(IntPtr acl,uint index,out IntPtr ace,out int error) {
      int offset=8; for(uint i=0;i<index;i++) offset+=unchecked((ushort)Marshal.ReadInt16(acl,offset+2));
      ace=scenario=="ace-pointer-outside"?new IntPtr(1):IntPtr.Add(acl,scenario=="duplicate-ace"?8:scenario=="ace-misaligned"?offset+1:offset);
      error=scenario=="ace-api-error"?5:0; return error==0;
    }
    public override bool Free(IntPtr descriptor,out int error) {
      frees++; Check(allocations.ContainsKey(descriptor),"Unowned/duplicate descriptor free"); error=scenario=="free-error"?6:0;
      if(error==0) { Marshal.FreeHGlobal(descriptor); allocations.Remove(descriptor); allocationKinds.Remove(descriptor); } return error==0;
    }
    public override bool Close(IntPtr handle,out int error) {
      closes++; Check(handle!=new IntPtr(55) && handle!=CurrentProcess() && handles.Contains(handle),"Borrowed/unowned/duplicate close");
      error=scenario=="close-error"?6:0; if(error==0) handles.Remove(handle); return error==0;
    }
    public void Dispose() {
      Check(handles.Count==(scenario=="close-error"?4:0),"Native unresolved handle ownership erased or unexpected");
      Check(allocations.Count==(scenario=="free-error"?4:0),"Native unresolved allocation ownership erased or unexpected");
      Check(loaded==(scenario=="unload-error"),"Native unresolved module reference erased or unexpected");
      // Harness-only teardown of mocked ownership and real test allocations.
      // This is not a second native cleanup call and never proves native release.
      foreach(IntPtr pointer in allocations.Keys) Marshal.FreeHGlobal(pointer);
      allocations.Clear(); allocationKinds.Clear(); handles.Clear(); loaded=false;
    }
  }
  static DirectorySecurityPrototype.Observation Observe(Fake api) {
    return DirectorySecurityPrototype.ObserveRetained(api,4321,new Dictionary<int,IntPtr> {{4321,new IntPtr(55)}},Sid(5,21,200));
  }
  public static DirectorySecurityPrototype.Observation Positive() {
    using(Fake api=new Fake("positive")) {
      var result=Observe(api);
      Check(api.UnresolvedHandles==0 && api.UnresolvedAllocations==0 && !api.UnresolvedModule,"Successful native model cleanup incomplete");
      Check(api.tokenOpens==2 && api.tokenCalls==12 && api.opens==2 && api.queries==4 && api.frees==4 && api.closes==4 && api.loadCalls==1 && api.unloadCalls==1,"Once-only owned call counts changed");
      Check(result.context=="ParentCall" && result.identityComparison=="PrimaryTokensOnly" && !result.parentCallingThreadTokenMeasured && !result.actualEventRouteMeasured && !result.effectiveAccessCalculated && !result.objectsCreated && !result.causeEstablished,"Facts exceed measurement");
      Check(result.childSession==2 && result.child.integrityRid==8192 && result.current.integrityRid==12288,"Exact token identity summaries changed");
      Check(api.information.Count==4 && api.information[0]==4 && api.information[1]==16 && api.information[2]==4 && api.information[3]==16,"Independent exact information flags changed");
      foreach(var row in result.directories) {
        Check(row.ntStatus==0 && row.close=="closed" && row.dacl.nativeError==0 && row.label.nativeError==0,"Independent returned statuses lost");
        Check(row.dacl.parse=="complete" && row.label.parse=="complete" && row.dacl.free=="freed" && row.label.free=="freed","Descriptor parsing/free facts changed");
        Check(row.dacl.aces[0].ownedUserMatches==true && row.dacl.aces[0].childPrimaryMatches==true && row.dacl.aces[0].currentPrimaryMatches==false && row.dacl.aces[2].childLogonMatches==true,"Exact owned/child/current SID comparisons changed");
        Check(row.label.aces[0].integrityRid==12288 && row.label.aces[0].mask==1,"High/NW label dropped");
      }
      return result;
    }
  }
  public static int Run() {
    Positive();
    string[] cases={"nt-denied","nt-missing","null-handle","borrowed-handle","load-error","api-unavailable","unload-error","query-error","dacl-query-error","label-query-error","missing-descriptor","invalid-descriptor","not-self-relative","getter-error","free-error","close-error","absent","absent-dacl","absent-label","null","empty","unknown","acl-size","acl-count","ace-size","sid-size","sid-revision","acl-outside","token-open-error","token-first-error","token-read-error","token-pointer-outside","token-group-count","acl-size-api-error","ace-api-error","ace-pointer-outside","duplicate-ace","acl-oversize-free","ace-misaligned","token-invalid-minus-one","token-invalid-zero","token-groups-empty8","token-groups-short7","high-child","logon-first-error0","logon-read-error0"};
    foreach(string scenario in cases) using(Fake api=new Fake(scenario)) {
      var result=Observe(api); Check(api.unloadCalls==1,"Module cleanup not attempted");
      Check(api.UnresolvedHandles==(scenario=="close-error"?4:0) && api.UnresolvedAllocations==(scenario=="free-error"?4:0) && api.UnresolvedModule==(scenario=="unload-error"),"One-attempt failure must retain unresolved native model ownership");
      if(scenario=="nt-denied" || scenario=="nt-missing") { Check(api.queries==0 && api.frees==0 && api.closes==2,"Failed NT poisoned output used"); foreach(var row in result.directories) Check(row.ntStatus==(scenario=="nt-denied"?unchecked((int)0xc0000022):unchecked((int)0xc0000034)),"Signed NTSTATUS lost"); }
      else if(scenario=="null-handle" || scenario=="borrowed-handle") Check(api.queries==0 && api.closes==2,"Invalid/borrowed handle claimed");
      else if(scenario=="load-error" || scenario=="api-unavailable") Check(api.opens==0,"Unavailable API invoked");
      else {
        Check(api.queries==4,"Sibling query skipped after failure");
        foreach(var row in result.directories) {
          if(scenario=="query-error") Check(row.dacl.nativeError==5 && row.label.nativeError==1314 && api.frees==0,"Direct DWORD/failed ownership contract changed");
          if(scenario=="dacl-query-error") Check(row.dacl.nativeError==5 && row.label.parse=="complete" && api.frees==2,"Label suppressed by failed DACL");
          if(scenario=="label-query-error") Check(row.dacl.parse=="complete" && row.label.nativeError==1314 && api.frees==2,"DACL suppressed by failed label");
          if(scenario=="missing-descriptor") Check(row.dacl.status=="missing-descriptor" && api.frees==0,"Missing descriptor freed");
          if(scenario=="invalid-descriptor" || scenario=="not-self-relative") Check(row.dacl.parse==scenario && !row.dacl.complete && api.frees==4,"Descriptor contract refusal/cleanup lost");
          if(scenario=="getter-error") Check(row.dacl.parse=="getter-error" && row.dacl.getterError==5 && row.label.parse=="getter-error" && api.frees==4,"Getter failure lost/poison output inspected");
          if(scenario=="absent" || scenario=="absent-dacl") Check(row.dacl.parse=="absent" && row.dacl.isNull==null && row.dacl.defaulted==null && row.dacl.aces.Length==0,"Undefined absent ACL outputs read");
          if(scenario=="absent" || scenario=="absent-label") Check(row.label.parse=="absent" && row.label.isNull==null && row.label.defaulted==null && row.label.aces.Length==0,"Undefined absent label outputs read");
          if(scenario=="null") Check(row.dacl.parse=="null" && row.dacl.present==true && row.dacl.isNull==true,"NULL conflated with absent");
          if(scenario=="empty") Check(row.dacl.parse=="empty" && row.dacl.aces.Length==0,"Empty ACL conflated with NULL");
          if(scenario=="unknown") Check(row.dacl.parse=="unknown-ace" && !row.dacl.complete && row.dacl.aces[0].mask==null && row.dacl.aces[0].childPrimaryMatches==null,"Unknown ACE invented content");
          if(scenario=="acl-size" || scenario=="acl-count" || scenario=="ace-size" || scenario=="sid-size" || scenario=="sid-revision" || scenario=="acl-outside" || scenario=="ace-pointer-outside" || scenario=="duplicate-ace" || scenario=="acl-oversize-free" || scenario=="ace-misaligned") Check(row.dacl.parse=="bounds-contract" && !row.dacl.complete && api.frees==4,"Malformed ACL accepted/leaked");
          if(scenario=="acl-size-api-error" || scenario=="ace-api-error") Check(row.dacl.parse=="acl-api-error" && row.dacl.getterError==5 && !row.dacl.complete && api.frees==4,"ACL public API failure lost");
          if(scenario=="free-error") Check(row.dacl.free=="error" && row.dacl.freeError==6 && row.close=="closed","Free failure hid close");
          if(scenario=="close-error") Check(row.dacl.free=="freed" && row.close=="error" && row.closeError==6,"Close failure lost");
        }
      }
      if(scenario=="token-open-error" || scenario=="token-first-error" || scenario=="token-read-error") Check(result.child.userError==5 || result.child.openError==5,"Direct token error lost");
      if(scenario=="token-open-error") Check(result.directories[0].dacl.aces[0].childPrimaryMatches==null,"Missing identity reported as mismatch");
      if(scenario=="token-invalid-minus-one" || scenario=="token-invalid-zero") Check(result.child.open=="invalid-output" && result.child.close=="not-opened" && api.closes==2,"Invalid token pseudo-handle claimed/closed");
      if(scenario=="token-pointer-outside") Check(result.child.user=="contract" && result.child.integrity=="contract" && result.directories[0].dacl.aces[0].childPrimaryMatches==null,"Outside token SID pointer inspected/accepted");
      if(scenario=="token-group-count") Check(result.child.logon=="contract" && result.directories[0].dacl.aces[2].childLogonMatches==null,"Unbounded token groups accepted");
      if(scenario=="token-groups-empty8") Check(result.current.logon=="absent" && result.child.logon=="absent" && result.child.logonError==0 && result.directories[0].dacl.aces[2].childLogonMatches==null && result.directories[0].dacl.aces[2].currentLogonMatches==null,"Valid8-byte empty TOKEN_GROUPS rejected or invented logon match");
      if(scenario=="logon-first-error0" || scenario=="logon-read-error0") Check(result.current.logon=="error" && result.child.logon=="error" && result.current.logonError==0 && result.child.logonError==0 && result.directories[0].dacl.aces[2].childLogonMatches==null,"Failed logon query native0 conflated with genuine absence");
      if(scenario=="token-groups-short7") Check(result.child.logon=="contract" && result.directories[0].dacl.aces[2].childLogonMatches==null,"Invalid7-byte TOKEN_GROUPS accepted");
      if(scenario=="unload-error") Check(result.unload=="error" && result.unloadError==6,"Module cleanup failure lost");
      if(scenario=="high-child") Check(result.child.integrityRid==12288 && result.directories[0].dacl.aces[0].currentPrimaryMatches==true && result.directories[0].dacl.aces[0].childPrimaryMatches==true && result.directories[0].dacl.aces[0].ownedUserMatches==false && result.directories[0].dacl.aces[1].currentLogonMatches==true && result.directories[0].dacl.aces[1].childLogonMatches==true && result.directories[0].dacl.aces[2].ownedUserMatches==true,"High same-user/logon comparison conflated QA ownership/current/child identities");
    }
    foreach(string scenario in new string[] {"pid-mismatch","session-error"}) using(Fake api=new Fake(scenario)) { bool refused=false; try { Observe(api); } catch(Exception) { refused=true; } Check(refused && api.loadCalls==0 && api.tokenOpens==0,"Foreign/missing child identity not refused early"); }
    using(Fake api=new Fake("positive")) { bool refused=false; try { DirectorySecurityPrototype.ObserveRetained(api,4321,new Dictionary<int,IntPtr>(),Sid(5,21,200)); } catch(InvalidOperationException) { refused=true; } Check(refused && api.loadCalls==0 && api.tokenOpens==0,"Unretained child accepted"); }
    return cases.Length+4;
  }
  public static DirectorySecurityPrototype.Observation TokenQueryZero(string scenario) {
    using(Fake api=new Fake(scenario)) { return Observe(api); }
  }
  public static void OriginalMissingLabel() {
    using(Fake api=new Fake("positive")) {
      var result=Observe(api); Check(api.queries==2 && result.directories[0].label.status=="not-attempted" && result.directories[0].label.aces.Length==0,"Original missing label boundary changed");
      Check(result.directories[0].dacl.parse=="complete","Original DACL boundary not exercised");
    }
  }
}
"""

NATIVE_MOCKS = r"""
    static DirectorySecurityControls.Fake Boundary;
    static NtOpen KeptDelegate;
    static int NativeLastError,NativeLoads,NativeUnloads,NativeResolves,NativeQueries,NativeAcls,NativeTokens;
    static void Assert(bool condition,string message){if(!condition)throw new InvalidOperationException(message);}
    static uint GetSystemDirectoryW(StringBuilder path,uint capacity){Assert(capacity==32768,"System32 capacity changed");path.Append(@"C:\Windows\System32");return 19;}
    static IntPtr LoadLibraryExW(string path,IntPtr file,uint flags){Assert(path.Replace('/', '\\')==@"C:\Windows\System32\ntdll.dll" && file==IntPtr.Zero && flags==0x800,"Unrestricted/foreign module load");NativeLoads++;int ignored;Assert(Boundary.Load(out ignored)=="available","Mock module setup");return new IntPtr(333);}
    static IntPtr GetProcAddress(IntPtr module,string name){Assert(module==new IntPtr(333) && name=="NtOpenDirectoryObject","Foreign entry resolved");NativeResolves++;KeptDelegate=Boundary.OpenDirectory;return Marshal.GetFunctionPointerForDelegate(KeptDelegate);}
    static bool FreeLibrary(IntPtr module){Assert(module==new IntPtr(333),"Borrowed/foreign module free");NativeUnloads++;int ignored;Assert(Boundary.Unload(out ignored)=="unloaded","Mock unload");return true;}
    static IntPtr GetCurrentProcess(){return Boundary.CurrentProcess();}
    static uint GetProcessId(IntPtr process){uint result=Boundary.ProcessId(process,out NativeLastError);return result;}
    static bool ProcessIdToSessionId(uint pid,out uint session){return Boundary.Session(pid,out session,out NativeLastError);}
    static bool OpenProcessToken(IntPtr process,uint access,out IntPtr token){Assert(access==8,"Token access is not QUERY-only");NativeTokens++;return Boundary.OpenToken(process,out token,out NativeLastError);}
    static bool GetTokenInformation(IntPtr token,int kind,IntPtr data,uint length,out uint returned){return Boundary.TokenInformation(token,kind,data,length,out returned,out NativeLastError);}
    static uint GetSecurityInfo(IntPtr handle,uint objectType,uint information,IntPtr owner,IntPtr group,IntPtr dacl,IntPtr sacl,out IntPtr descriptor){Assert(objectType==6 && (information==4 || information==16) && owner==IntPtr.Zero && group==IntPtr.Zero && dacl==IntPtr.Zero && sacl==IntPtr.Zero,"Combined/unsupported security information");NativeQueries++;NativeLastError=87;return Boundary.Query(handle,information,out descriptor);}
    static bool GetSecurityDescriptorDacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted){return Boundary.Acl(descriptor,4,out present,out acl,out defaulted,out NativeLastError);}
    static bool GetSecurityDescriptorSacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted){return Boundary.Acl(descriptor,16,out present,out acl,out defaulted,out NativeLastError);}
    static bool IsValidSecurityDescriptor(IntPtr descriptor){uint length;int ignored;return Boundary.DescriptorSize(descriptor,out length,out ignored)=="available";}
    static bool GetSecurityDescriptorControl(IntPtr descriptor,out ushort control,out uint revision){control=0x8000;revision=1;NativeLastError=87;return true;}
    static uint GetSecurityDescriptorLength(IntPtr descriptor){uint length;int ignored;Assert(Boundary.DescriptorSize(descriptor,out length,out ignored)=="available","Invalid length dependency");return length;}
    static bool GetAclInformation(IntPtr acl,out AclSizeInformation size,uint length,int informationClass){Assert(length==12 && informationClass==2,"ACL public size query ABI changed");NativeAcls++;uint count,used,free;bool ok=Boundary.AclSize(acl,out count,out used,out free,out NativeLastError);size=new AclSizeInformation{AceCount=count,AclBytesInUse=used,AclBytesFree=free};return ok;}
    static bool GetAce(IntPtr acl,uint index,out IntPtr ace){return Boundary.Ace(acl,index,out ace,out NativeLastError);}
    static IntPtr LocalFree(IntPtr descriptor){return Boundary.Free(descriptor,out NativeLastError)?IntPtr.Zero:descriptor;}
    static bool CloseHandle(IntPtr handle){return Boundary.Close(handle,out NativeLastError);}
    public static void RunActualNativeWrapperControl(){
      using(var fake=new DirectorySecurityControls.Fake("positive")){
        Boundary=fake;
        var result=DirectorySecurityPrototype.ObserveRetained(new NativeApi(),4321,new Dictionary<int,IntPtr>{{4321,new IntPtr(55)}},new byte[]{1,2,0,0,0,0,0,5,21,0,0,0,200,0,0,0});
        Assert(NativeLoads==1 && NativeUnloads==1 && NativeResolves==1 && NativeQueries==4 && NativeAcls==4 && NativeTokens==2,"Exact native boundary call ownership changed");
        Assert(result.directories[0].dacl.parse=="complete" && result.directories[1].label.parse=="complete" && result.loadError==0 && result.unloadError==0,"Literal wrapper result contract changed");
        Assert(result.directories[0].dacl.nativeError==0 && result.directories[0].dacl.getterError==0,"Stale last error used for direct return/success");
      }
    }
"""

class DirectorySecurityControls(unittest.TestCase):
    @staticmethod
    def engines():
        core=shutil.which('pwsh')
        if not core:
            raise AssertionError('Actual PowerShell required')
        values=[(core,'Core')]
        if os.name=='nt':
            desktop=shutil.which('powershell.exe')
            if not desktop or Path(desktop).resolve()==Path(core).resolve():
                raise AssertionError('Stock Desktop5.1 required on Windows')
            values.append((desktop,'Desktop'))
        return values

    def source(self):
        return (ROOT/'scripts/test-support-directory-security.cs').read_text(encoding='utf-8')

    def run_script(self,csharp,body):
        with tempfile.TemporaryDirectory(prefix='mixel-directory-security-source-') as directory:
            root=Path(directory)
            (root/'control.cs').write_text(csharp,encoding='utf-8')
            script=root/'control.ps1'
            script.write_text(r'''
param([string]$ExpectedEdition,[string]$Repository)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -cne $ExpectedEdition -or ($ExpectedEdition -ceq 'Desktop' -and ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1))) {throw 'Actual required PowerShell edition/version unavailable'}
Add-Type -Path (Join-Path $PSScriptRoot 'control.cs') -ErrorAction Stop
'''+body,encoding='utf-8-sig')
            for engine,edition in self.engines():
                result=subprocess.run([engine,'-NoLogo','-NoProfile','-File',str(script),'-ExpectedEdition',edition,'-Repository',str(ROOT)],capture_output=True,text=True,encoding='utf-8',timeout=35)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                print(result.stdout,flush=True)

    def test_exact_qualified_runtime_and_whole_original_lease_conservation(self):
        source=self.source()
        prior=qualified_runtime_projection(source)
        self.assertEqual(hashlib.sha256(prior.encode()).hexdigest(),'480aff569786e128ab07452e66c37aab5290cd6748fa09b15c95a346bbbfdac7')
        ps=(ROOT/'scripts/test-support-lease-standard-windows.ps1').read_text(encoding='utf-8')
        project_standard_source(ps)
        self.assertNotRegex(source,r'\b(?:SetSecurityInfo|AccessCheck|OpenProcess|DuplicateToken|NtOpenEvent|AdjustTokenPrivileges|SetTokenInformation|ImpersonateLoggedOnUser)\s*\(')
        self.assertEqual(ps.count('    Invoke-LeaseDirectorySecurityObservation $controlPid\n'),2)

    def test_actual_csharp_50_cases_and_separate_label_negative(self):
        source=self.source()
        self.run_script(source+'\n'+CSHARP_CONTROLS[CSHARP_CONTROLS.index('public static class '):],r'''
$count=[DirectorySecurityControls]::Run()
if($count -ne 50){throw 'Mandatory50 directory controls changed'}
$facts=[DirectorySecurityControls]::Positive() | ConvertTo-Json -Depth 8 -Compress
if($facts.Contains('S-1-') -or $facts.Contains('System.Byte') -or $facts.Contains('SecurityIdentifier') -or $facts.Contains('LUID')){throw 'Private identity exported'}
Write-Host 'PASS: actual C#50 source cases; native0 failed first/read logon queries remain errors; DACL/LABEL independent,64ACE limit, empty8-byte groups, signed/direct errors, ownership/bounds and redacted high/medium/owned primary/logon identity; failed close/free/unload retains unresolved mock ownership, harness teardown separate; all native APIs mocked.'
''')
        needle='ReadQuery(api,handle,row.dacl,ownedUser,current,child); ReadQuery(api,handle,row.label,ownedUser,current,child);'
        self.assertEqual(source.count(needle),1)
        original=source.replace(needle,'ReadQuery(api,handle,row.dacl,ownedUser,current,child);')
        self.run_script(original+'\n'+CSHARP_CONTROLS[CSHARP_CONTROLS.index('public static class '):],r'''
[DirectorySecurityControls]::OriginalMissingLabel()
Write-Host 'PASS: exact original DACL-only projection cannot observe mandatory label; no actual Windows access/route/cause inference.'
''')

    def test_original_native0_query_false_is_not_valid_logon_absence(self):
        original=qualified_runtime_projection(self.source())
        self.run_script(original+'\n'+CSHARP_CONTROLS[CSHARP_CONTROLS.index('public static class '):],r'''
foreach($case in @('logon-first-error0','logon-read-error0')) {
  $facts=[DirectorySecurityControls]::TokenQueryZero($case)
  if($facts.child.logon -cne 'absent' -or $facts.child.logonError -ne 0 -or $facts.current.logon -cne 'absent'){throw 'Exact original false/native0 logon query gap no longer reproduces'}
}
Write-Host 'PASS: exact480 original first/read token-query FALSE/native0 incorrectly reports absent; corrected50 treats both as errors, legitimate8-byte empty groups remain absent. Native APIs mocked.'
''')

    def test_exact_close_throw_drains_local_buffers_and_module_once(self):
        source=self.source()
        fixed='''          try {
            if(handle!=IntPtr.Zero) { bool ok=api.Close(handle,out error); row.close=ok?"closed":"error"; row.closeError=error; }
          } finally {
            if(name!=IntPtr.Zero) Marshal.FreeHGlobal(name); Marshal.FreeHGlobal(text);
          }'''
        old='''          if(handle!=IntPtr.Zero) { bool ok=api.Close(handle,out error); row.close=ok?"closed":"error"; row.closeError=error; }
          if(name!=IntPtr.Zero) Marshal.FreeHGlobal(name); Marshal.FreeHGlobal(text);'''
        self.assertEqual(source.count(fixed),1)
        original=source.replace(fixed,old)
        prior=qualified_runtime_projection(original)
        self.assertEqual(hashlib.sha256(prior.encode()).hexdigest(),'d504f69ec54052e450bee7af3d9479488007a44ec5c5ba645c06748292e1c14a')
        anchor='      error=scenario=="close-error"?6:0; if(error==0) handles.Remove(handle); return error==0;'
        self.assertEqual(CSHARP_CONTROLS.count(anchor),1)
        controls=CSHARP_CONTROLS.replace(anchor,'''      error=0; handles.Remove(handle);
      if(handle.ToInt64()>200) { DirectorySecurityPrototype.CloseDidThrow=true; throw new InvalidOperationException("explicit close binding exception"); }
      return true;''')
        helpers='''
  public static bool CloseDidThrow;
  public static int FreesAfterCloseThrow;
  static void TrackedFree(IntPtr memory) { if(CloseDidThrow) FreesAfterCloseThrow++; Marshal.FreeHGlobal(memory); }
'''
        test='''
public static class CloseThrowControl {
  public static void Run(int expected) {
    using(var api=new DirectorySecurityControls.Fake("positive")) {
      Exception observed=null;
      try { DirectorySecurityPrototype.ObserveRetained(api,4321,new Dictionary<int,IntPtr>{{4321,new IntPtr(55)}},new byte[]{1,2,0,0,0,0,0,5,21,0,0,0,200,0,0,0}); }
      catch(Exception error) { observed=error; }
      if(observed==null || observed.Message!="explicit close binding exception" || DirectorySecurityPrototype.FreesAfterCloseThrow!=expected || api.unloadCalls!=1) throw new InvalidOperationException("Close throw cleanup boundary changed");
    }
  }
}
'''
        for label,body,expected in [('original',original,0),('corrected',source,2)]:
            self.assertEqual(body.count('Marshal.FreeHGlobal('),3)
            traced=body.replace('Marshal.FreeHGlobal(','TrackedFree(')
            end=traced.rfind('\n}')
            combined=traced[:end]+helpers+traced[end:]+'\n'+controls[controls.index('public static class '):]+test
            self.run_script(combined,'[CloseThrowControl]::Run('+str(expected)+")\nWrite-Host 'PASS: "+label+' exact runtime close-throw: local frees='+str(expected)+"; module unload once; original exception retained; local tracing calls real FreeHGlobal and native APIs mocked.'\n")

    def test_literal_native_wrapper_arguments_and_error_domains(self):
        source=self.source()
        native,count=re.subn(r'    \[DllImport\([^\n]+\)\] static extern [^\n]+;\n','',source)
        self.assertEqual(count,19)
        self.assertEqual(native.count('Marshal.GetLastWin32Error()'),14)
        native=native.replace('Marshal.GetLastWin32Error()','NativeLastError')
        anchor='    public virtual IntPtr CurrentProcess()'
        self.assertEqual(native.count(anchor),1)
        native=native.replace(anchor,NATIVE_MOCKS+'\n'+anchor)
        self.run_script(native+'\n'+CSHARP_CONTROLS[CSHARP_CONTROLS.index('public static class '):],r'''
[DirectorySecurityPrototype+NativeApi]::RunActualNativeWrapperControl()
Write-Host 'PASS: literal native wrappers19/14; absolute System32/dynamic reference, directory READ_CONTROL, token8, kernel-object separate4/16, public ACL12/class2; authoritative signedNT/directDWORD and immediate BOOL errors retained. Native APIs mocked.'
''')

    def test_actual_ordinary_fixture_composite_compiles_helper_without_launch(self):
        ps=(ROOT/'scripts/test-support-lease-standard-windows.ps1').read_text(encoding='utf-8')
        launch=(ROOT/'scripts/test-support-launch-windows.ps1').read_text(encoding='utf-8')
        cls=launch.index('public static class MixelOrdinaryTokenFixture {')
        first=launch.rindex('using System;',0,cls)
        end=launch.index("\n'@ }",cls)
        normal=launch[first:end]
        comparison=ps[ps.index("  $comparison = @'\n")+len("  $comparison = @'\n"):ps.index("\n'@\n  Add-Type",ps.index("  $comparison = @'\n"))]
        marker='  public static int StartStandardUser(string executable, string username, string password) {'
        self.assertEqual(normal.count(marker),1)
        normal=normal.replace(marker,comparison+'\n'+marker)
        helper=self.source()
        composite='using System.Collections.Generic;\n'+normal+'\n'+helper[helper.index('public static class DirectorySecurityPrototype {'):]
        self.run_script(composite,r'''
Write-Host 'PASS: complete actual ordinary-token fixture + exact injected comparison + helper compile as one assembly; no launch/native API executed; original retained dictionary borrowed, QA SID conversion local.'
''')

    def test_actual_powershell_observer_preserves_facts_and_native5_precedence(self):
        extra=r'''
public static class MixelOrdinaryTokenFixture {
  public static DirectorySecurityPrototype.Observation Facts;
  public static DirectorySecurityPrototype.Observation StartedDirectorySecurityFacts(int pid,string sid){
    if(pid!=4321 || sid!="local-owned-only")throw new InvalidOperationException("Wrong retained child/local QA input");return Facts;
  }
}
'''
        self.run_script(self.source()+'\n'+CSHARP_CONTROLS[CSHARP_CONTROLS.index('public static class '):]+extra,r'''
$source=[IO.File]::ReadAllText((Join-Path $Repository 'scripts/test-support-lease-standard-windows.ps1'))
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Actual complete lease helper no longer parses'}
foreach($name in @('Write-LeaseContextFailure','Invoke-LeaseDirectorySecurityObservation')) {
  $match=$ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true)
  if($match.Count -ne 1){throw 'Exact directory observer function missing'}
  . ([scriptblock]::Create($match[0].Extent.Text))
}
$ownedSid='local-owned-only';$leaseContextFailures=[Collections.Generic.List[Exception]]::new()
$script:printed=[Collections.Generic.List[string]]::new();function Write-Host {param($Object)$script:printed.Add([string]$Object)}
$facts=[DirectorySecurityControls]::Positive();[MixelOrdinaryTokenFixture]::Facts=$facts
Invoke-LeaseDirectorySecurityObservation 4321
if($leaseContextFailures.Count -ne 0 -or $script:printed.Count -ne 1 -or -not $script:printed[0].Contains('ActualEventRouteUnmeasured=True;') -or -not $script:printed[0].Contains('"information":16') -or $script:printed[0].Contains('S-1-')){throw 'Valid independent label/DACL facts lost or promoted to effective permission'}
foreach($case in @('token-groups-empty8','logon-first-error0','logon-read-error0','query','query-zero','query-dword','getter','free','close','loader','token','unknown','nt-denied','nt-zero','wrong-pid','wrong-scope','too-many','wrong-information')) {
  $facts=if($case -cin @('token-groups-empty8','logon-first-error0','logon-read-error0')){[DirectorySecurityControls]::TokenQueryZero($case)}else{[DirectorySecurityControls]::Positive()};[MixelOrdinaryTokenFixture]::Facts=$facts
  $leaseContextFailures.Clear();$script:printed.Clear()
  switch($case) {
    query {$facts.directories[0].dacl.status='error';$facts.directories[0].dacl.nativeError=5}
    query-zero {$facts.directories[0].dacl.status='error';$facts.directories[0].dacl.nativeError=0}
    query-dword {$facts.directories[0].dacl.status='error';$facts.directories[0].dacl.nativeError=[uint32]::MaxValue}
    getter {$facts.directories[0].dacl.parse='getter-error';$facts.directories[0].dacl.getterError=0}
    free {$facts.directories[0].dacl.free='error';$facts.directories[0].dacl.freeError=6}
    close {$facts.directories[0].close='error';$facts.directories[0].closeError=0}
    loader {$facts.load='api-unavailable';$facts.loadError=127}
    token {$facts.child.close='error';$facts.child.closeError=6}
    unknown {$facts.directories[0].dacl.parse='unknown-ace';$facts.directories[0].dacl.complete=$false}
    nt-denied {$facts.directories[0].open='nt-error';$facts.directories[0].ntStatus=-1073741790;$facts.directories[0].close='not-opened'}
    nt-zero {$facts.directories[0].open='nt-error';$facts.directories[0].ntStatus=0}
    wrong-pid {$facts.pid=99}
    wrong-scope {$facts.causeEstablished=$true}
    too-many {$facts.directories[0].dacl.aces=[DirectorySecurityPrototype+AceFacts[]]::new(65)}
    wrong-information {$facts.directories[0].label.information=4}
  }
  Invoke-LeaseDirectorySecurityObservation 4321
  $expectedFailure=$case -cnotin @('unknown','nt-denied','token-groups-empty8')
  if($expectedFailure -ne ($leaseContextFailures.Count -gt 0)){throw "Observation error/incomplete scope changed $case"}
  $nativeCode=switch($case){logon-first-error0{'0,0'}logon-read-error0{'0,0'}query{'5'}query-zero{'0'}query-dword{'-1'}getter{'0'}free{'6'}close{'0'}loader{'127'}token{'6'}}
  if($null -ne $nativeCode -and -not $script:printed[1].Contains("nativeErrors=$nativeCode;")){throw "Exact native error bits/zero lost $case"}
}
$oldGate=$ast.FindAll({param($node)$node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if (-not $ObserveOnly -and ($record.sourceStatus')},$true)
$terminal=$ast.FindAll({param($node)$node -is [Management.Automation.Language.IfStatementAst] -and $node.Extent.Text.StartsWith('if ($leaseContextFailures.Count -gt 0)')},$true)
if($oldGate.Count -ne 1 -or $terminal.Count -ne 1 -or $terminal[0].Extent.StartOffset -lt $source.IndexOf('PASS: closed wrong-object control leaves no GUID lease')){throw 'Original native or diagnostic terminal changed'}
$ObserveOnly=$false;$ExpectedStatus='ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false';$ExpectedError=2;$ExpectedProof='';$ExpectedRequired=$false;$ExpectedOwnerFailed=$false
$record=[pscustomobject]@{pid=4321;sourceStatus='ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false';rawOpenError=5;attestation='guard-unavailable';requiresClick=$true;ownerFailed=$false}
$message='';try{& ([scriptblock]::Create($oldGate[0].Extent.Text));& ([scriptblock]::Create($terminal[0].Extent.Text))}catch{$message=$_.Exception.Message}
if(-not $message.Contains('Actual standard-account source lease classification mismatch') -or -not $message.Contains('"rawOpenError":5')){throw 'Observer replaced original native5 fatal result'}
$record.sourceStatus=$ExpectedStatus;$record.rawOpenError=2;$record.attestation='';$record.requiresClick=$false
$message='';try{& ([scriptblock]::Create($oldGate[0].Extent.Text));& ([scriptblock]::Create($terminal[0].Extent.Text))}catch{$message=$_.Exception.Message}
if(-not $message.Contains('Read-only source lease context observation failed after the unchanged original gate')){throw 'Observer failure accepted after passing original gate'}
Microsoft.PowerShell.Utility\Write-Host 'PASS: actual PowerShell observer preserves scoped bounded/redacted DACL+LABEL facts; query/free/close/load and schema failures deferred; unknown/explicitNT results never become access conclusions; exact original native5 gate remains first/fatal, diagnostic faults fatal after original pass. All native APIs mocked.'
''')

if __name__=='__main__':
    unittest.main()
