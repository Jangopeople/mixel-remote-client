#!/usr/bin/env python3
"""Source/mock controls for the optional hosted-CI directory experiment.

No Windows native security API is called by these controls.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
CS = SCRIPTS / 'test-session-directory-access.cs'
WRAPPER = SCRIPTS / 'observe-session-directory-aba-windows.ps1'

MOCK = r'''
public static class AbaMock {
 public static int checks=0;
 public static void Check(bool okay,string text){if(!okay)throw new Exception(text);checks++;}
 public static void Refuse(Action action,string marker){try{action();}catch(Exception e){Check(e.Message.Contains(marker),"Unexpected refusal: "+e.Message);return;}throw new Exception("Expected refusal: "+marker);}
 static void Put(byte[] b,int at,uint value){Buffer.BlockCopy(BitConverter.GetBytes(value),0,b,at,4);}
 static void Put16(byte[] b,int at,ushort value){Buffer.BlockCopy(BitConverter.GetBytes(value),0,b,at,2);}
 public static byte[] User(){byte[] b=new byte[28];b[0]=1;b[1]=5;b[7]=5;Put(b,8,21);Put(b,12,11);Put(b,16,22);Put(b,20,33);Put(b,24,1001);return b;}
 public static byte[] World(){byte[] b=new byte[12];b[0]=1;b[1]=1;b[7]=1;return b;}
 public static byte[] Acl(int count){byte[] b=new byte[8+20*count];b[0]=2;Put16(b,2,(ushort)b.Length);Put16(b,4,(ushort)count);for(int i=0;i<count;i++){int p=8+20*i;b[p]=0;Put16(b,p+2,20);Put(b,p+4,3);Buffer.BlockCopy(World(),0,b,p+8,12);}return b;}
 public static byte[] Descriptor(byte[] acl){byte[] b=new byte[44+acl.Length];b[0]=1;Put16(b,2,0x8004);Put(b,4,20);Put(b,8,32);Put(b,16,44);Buffer.BlockCopy(World(),0,b,20,12);Buffer.BlockCopy(World(),0,b,32,12);Buffer.BlockCopy(acl,0,b,44,acl.Length);return b;}
 public static byte[] Label(){byte[] b=new byte[48];b[0]=1;Put16(b,2,0x8010);Put(b,12,20);b[20]=2;Put16(b,22,28);Put16(b,24,1);b[28]=17;Put16(b,30,20);Put(b,32,1);b[36]=1;b[37]=1;b[43]=16;Put(b,44,4096);return b;}
 public sealed class Sid {
  public int BinaryLength {get{return 28;}}
  public Sid(string value){Check(value=="owned-local-qa-sid","Wrong mock SID");}
  public void GetBinaryForm(byte[] target,int at){Buffer.BlockCopy(User(),0,target,at,28);}
 }
 public sealed class Platform : MixelSessionDirectoryAba.Api {
  public static string Mode="normal"; public static Platform Last;
  public byte[] descriptor=Descriptor(Acl(2)),label=Label();
  public int sets=0,reads=0,closes=0,unloads=0; public string mode; public uint session=2;
  public Platform(){mode=Mode;Last=this;}
  public uint CurrentSession(){return session;}
  public int OpenSessionDirectory(uint s,uint access,out IntPtr handle){Check(s==2&&access==0x60000,"Wrong directory access or session");handle=new IntPtr(mode=="zero-handle"?0:mode=="negative-handle"?-1:123);return mode=="open-denied"?unchecked((int)0xc0000022):mode=="positive-status"?1:0;}
  public byte[] ReadDescriptor(IntPtr h,uint flags){Check(h.ToInt64()==123&&(flags==7||flags==16),"Wrong snapshot flags/handle");reads++;if(mode=="query-fails"&&reads==1)throw new System.ComponentModel.Win32Exception(5,"Injected descriptor fill error");
   byte[] raw=(byte[])(flags==7?descriptor:label).Clone();
   if(mode=="foreign-drift"&&reads==3&&flags==7)raw[56]^=1;
   if(mode=="owner-drift"&&sets==1&&flags==7)raw[28]^=1;
   if(mode=="group-drift"&&sets==1&&flags==7)raw[40]^=1;
   if(mode=="control-drift"&&sets==1&&flags==7)raw[2]^=8;
   if(mode=="label-drift"&&sets==1&&flags==16)raw[44]^=1;
   if(mode=="restore-readback-drift"&&sets>=2&&flags==7)raw[56]^=1;
   return raw;}
  public byte[] AddOwnedAce(byte[] original,byte[] sid){return MixelSessionDirectoryAba.AddOneAce(original,sid);}
  public bool SetDacl(IntPtr h,byte[] raw,out int error){Check(h.ToInt64()==123,"Wrong setter handle");sets++;descriptor=(byte[])raw.Clone();error=0;
   if(mode=="set-false"&&sets==1){error=5;return false;}
   if(mode=="set-zero-false"&&sets==1)return false;
   if(mode=="set-throw"&&sets==1)throw new Exception("Injected setter exception after mutation");
   if(mode=="restore-false"&&sets==2){error=5;return false;}
   if(mode=="restore-throw"&&sets==2)throw new Exception("Injected restore exception");
   return true;}
  public bool Close(IntPtr h,out int error){Check(h.ToInt64()==123,"Unowned poisoned handle closed");closes++;error=mode=="close-false"?6:0;if(mode=="close-throw")throw new Exception("Injected close exception");return mode!="close-false";}
  public void Dispose(){unloads++;if(mode=="unload-throw")throw new Exception("Injected unload exception");}
 }
 public static void Unit(){
  Platform.Mode="normal";var a=new Platform();var s=new MixelSessionDirectoryAba.Scope(a,User());s.Open();byte[] before=(byte[])a.descriptor.Clone();s.Apply();Check(a.sets==1&&a.descriptor.Length==before.Length+36,"Exactly one bounded ACE added");
  Check(a.descriptor[92]==0&&a.descriptor[93]==0&&BitConverter.ToUInt32(a.descriptor,96)==8,"New ACE type flags mask changed");
  for(int i=8;i<48;i++)Check(a.descriptor[44+i]==before[44+i],"Original ACE order changed");
  s.Restore();s.Dispose();Check(a.sets==2&&a.closes==1&&a.unloads==1&&s.facts.restorationVerified&&s.facts.cleanupOkay,"Normal restoration/cleanup failed");
  Check(s.facts.originalDaclHash==s.facts.restoredDaclHash&&s.facts.ownerUnchanged&&s.facts.groupUnchanged&&s.facts.controlUnchanged&&s.facts.labelUnchanged,"Snapshot facts inconsistent");
  s.Dispose();Check(a.closes==1&&a.unloads==1,"Duplicate ownership release");
  foreach(string mode in new[]{"set-false","set-zero-false","set-throw","owner-drift","group-drift","control-drift","label-drift"}){
   Platform.Mode=mode;var p=new Platform();var q=new MixelSessionDirectoryAba.Scope(p,User());q.Open();Refuse(q.Apply,mode=="set-throw"?"Injected":"Temporary");Check(q.facts.writeAttempted,"Restore not registered before setter");q.Restore();q.Dispose();Check(p.sets==2&&q.facts.restorationVerified,"Failed setter/readback did not restore original");}
  foreach(string mode in new[]{"restore-false","restore-throw","restore-readback-drift"}){
   Platform.Mode=mode;var p=new Platform();var q=new MixelSessionDirectoryAba.Scope(p,User());q.Open();q.Apply();Refuse(q.Restore,mode=="restore-throw"?"Injected":"Original");Refuse(q.Dispose,"cleanup incomplete");Check(p.closes==1&&p.unloads==1&&!q.facts.cleanupOkay,"Restore fault skipped independent cleanup");}
  foreach(string mode in new[]{"close-false","close-throw","unload-throw"}){
   Platform.Mode=mode;var p=new Platform();var q=new MixelSessionDirectoryAba.Scope(p,User());q.Open();q.Restore();Refuse(q.Dispose,"cleanup incomplete");Check(p.closes==1&&p.unloads==1,"Close fault skipped module cleanup");}
  foreach(string mode in new[]{"open-denied","zero-handle","negative-handle","query-fails"}){
   Platform.Mode=mode;var p=new Platform();var q=new MixelSessionDirectoryAba.Scope(p,User());Refuse(q.Open,mode=="open-denied"?"WRITE_DAC":mode=="query-fails"?"Injected descriptor":"invalid handle");q.Restore();q.Dispose();Check(p.sets==0&&p.closes==(mode=="query-fails"?1:0)&&p.unloads==1,"Failure poison or no-write ownership changed");}
  Platform.Mode="positive-status";a=new Platform();s=new MixelSessionDirectoryAba.Scope(a,User());s.Open();s.Restore();s.Dispose();Check(s.facts.openNtStatus==1,"Signed NT_SUCCESS semantics changed");
  Platform.Mode="foreign-drift";a=new Platform();s=new MixelSessionDirectoryAba.Scope(a,User());s.Open();Refuse(s.Apply,"Foreign pre-write");Check(a.sets==0&&!s.facts.writeAttempted,"Foreign pre-write drift mutated");s.Restore();s.Dispose();
  var bad=User();bad[24]=1;bad[25]=0;Refuse(()=>MixelSessionDirectoryAba.AddOneAce(Acl(1),bad),"newly created");
  var acl=Acl(1);acl[8]=17;Refuse(()=>MixelSessionDirectoryAba.AddOneAce(acl,User()),"Unknown ACE");
  acl=Acl(1);acl[9]=16;Refuse(()=>MixelSessionDirectoryAba.AddOneAce(acl,User()),"Inherited ACE");
  Refuse(()=>MixelSessionDirectoryAba.AddOneAce(Acl(64),User()),"additional ACE");Refuse(()=>MixelSessionDirectoryAba.AddOneAce(Acl(65),User()),"ACE bound");
  var extended=MixelSessionDirectoryAba.AddOneAce(Acl(1),User());Refuse(()=>MixelSessionDirectoryAba.AddOneAce(extended,User()),"already occurs");
  foreach(int index in new[]{0,1,4,16,46}){
   Platform.Mode="normal";a=new Platform();a.descriptor[index]=255;s=new MixelSessionDirectoryAba.Scope(a,User());Refuse(s.Open,index<=1?"descriptor missing":index==16?"invalid DACL":"changed");s.Dispose();Check(a.sets==0,"Malformed descriptor mutated");}
  Platform.Mode="normal";a=new Platform();a.label[28]=0;s=new MixelSessionDirectoryAba.Scope(a,User());Refuse(s.Open,"LABEL ACE");s.Dispose();Check(a.sets==0,"Malformed LABEL mutated");
  Console.WriteLine("PASS: actual C# mock ownership/ABI/descriptor controls checks="+checks+"; native security APIs not called.");
 }
}
public static class MixelOrdinaryTokenFixture {
 public static int stops=0,closes=0; public static string mode="normal";
 public static void StopOwnedStartedProcesses(){stops++;if(mode=="stop-fails")throw new Exception("Injected retained stop failure");}
 public static void CloseStartedObservations(){closes++;if(mode=="close-fails"||mode=="exit-fails")throw new Exception("Injected retained close failure");}
 public static string StartedStatus(int pid){return mode=="exit-fails"?"still-running":"exited:0x00000000";}
}
public static class MixelIsolatedLeaseControl {
 public static int worlds=0,closes=0;
 public static IntPtr WorldEvent(string name){AbaMock.Check(name=="Global\\Mixel-Remote-Standard-Lease-"+new string('d',32),"Wrong event identity");worlds++;return new IntPtr(321);}
 public static void Close(IntPtr h){AbaMock.Check(h.ToInt64()==321,"Unowned World close");closes++;}
}
'''

NATIVE_MOCK = r'''
    static int mockError=0,mockSets=0,mockCloses=0,mockUnloads=0;static string mockMode="normal";
    static byte[] mockDescriptor=AbaMock.Descriptor(AbaMock.Acl(2));static byte[] mockLabel=AbaMock.Label();
    static readonly HashSet<IntPtr> allocations=new HashSet<IntPtr>();
    static readonly Dictionary<IntPtr,IntPtr> securityAcls=new Dictionary<IntPtr,IntPtr>();
    static DirectoryOpen mockCallback=MockOpen;
    static IntPtr MockAlloc(int size){IntPtr h=Marshal.AllocHGlobal(size);allocations.Add(h);return h;}
    static IntPtr MockText(string text){IntPtr h=Marshal.StringToHGlobalUni(text);allocations.Add(h);return h;}
    static void MockFree(IntPtr h){AbaMock.Check(allocations.Remove(h),"Unowned native buffer free");Marshal.FreeHGlobal(h);mockError=99;}
    static uint MockCurrentPid(){return 777;}
    static bool MockSession(uint pid,out uint session){AbaMock.Check(pid==777,"Different parent PID");session=2;return true;}
    static uint MockSystemDirectory(char[] buffer,uint length){AbaMock.Check(length==512&&buffer.Length==512,"System directory capacity changed");string text="C:\\Windows\\System32";text.CopyTo(0,buffer,0,text.Length);return (uint)text.Length;}
    static IntPtr MockLoad(string path,IntPtr file,uint flags){AbaMock.Check(path.Replace('/','\\')=="C:\\Windows\\System32\\ntdll.dll"&&file==IntPtr.Zero&&flags==0x800,"Foreign module/load flags");return new IntPtr(901);}
    static IntPtr MockResolve(IntPtr h,string name){AbaMock.Check(h.ToInt64()==901&&name=="NtOpenDirectoryObject","Foreign export");return Marshal.GetFunctionPointerForDelegate(mockCallback);}
    static bool MockUnload(IntPtr h){AbaMock.Check(h.ToInt64()==901,"Unowned module release");mockUnloads++;return true;}
    static int MockOpen(out IntPtr h,uint access,ref Attributes a){AbaMock.Check(access==0x60000&&a.Length==48&&a.RootDirectory==IntPtr.Zero&&a.Flags==64&&a.SecurityDescriptor==IntPtr.Zero&&a.SecurityQualityOfService==IntPtr.Zero,"Native directory attributes changed");
      Unicode u=(Unicode)Marshal.PtrToStructure(a.ObjectName,typeof(Unicode));AbaMock.Check(u.Length==56&&u.MaximumLength==58&&Marshal.PtrToStringUni(u.Buffer)=="\\Sessions\\2\\BaseNamedObjects","Directory Unicode/path scope changed");h=new IntPtr(123);return 0;}
    static bool MockClose(IntPtr h){AbaMock.Check(h.ToInt64()==123,"Foreign directory close");mockCloses++;return true;}
    static bool MockRead(IntPtr h,uint flags,IntPtr buffer,uint size,out uint needed){AbaMock.Check(h.ToInt64()==123&&(flags==7||flags==16),"Wrong kernel descriptor query");byte[] raw=flags==7?mockDescriptor:mockLabel;needed=(uint)raw.Length;
      if(buffer==IntPtr.Zero){AbaMock.Check(size==0,"Wrong sizing capacity");mockError=122;return false;}AbaMock.Check(size==needed,"Wrong fill capacity");if(mockMode=="read-fill"){mockError=5;return false;}Marshal.Copy(raw,0,buffer,raw.Length);return true;}
    static bool MockInitializeAcl(IntPtr acl,uint size,uint revision){AbaMock.Check(size==84&&revision==2,"Native ACL capacity/revision changed");if(mockMode=="acl-init"){mockError=5;return false;}Marshal.WriteByte(acl,0,2);Marshal.WriteInt16(acl,2,(short)size);return true;}
    static int MockAclEnd(IntPtr acl){int count=(ushort)Marshal.ReadInt16(acl,4),end=8;for(int i=0;i<count;i++)end+=(ushort)Marshal.ReadInt16(acl,end+2);return end;}
    static bool MockAclSize(IntPtr acl,IntPtr output,uint length,uint kind){AbaMock.Check(length==12&&kind==2,"ACL size ABI changed");if(mockMode=="acl-info"){mockError=5;return false;}int count=(ushort)Marshal.ReadInt16(acl,4),end=MockAclEnd(acl),size=(ushort)Marshal.ReadInt16(acl,2);Marshal.WriteInt32(output,0,count);Marshal.WriteInt32(output,4,end);Marshal.WriteInt32(output,8,size-end);return true;}
    static bool MockGetAce(IntPtr acl,uint index,out IntPtr ace){AbaMock.Check(index<2,"Foreign original ACE index");ace=IntPtr.Add(acl,mockMode=="ace-pointer"?1:8+20*(int)index);return true;}
    static bool MockAddAce(IntPtr acl,uint revision,uint index,IntPtr ace,uint length){AbaMock.Check(revision==2&&index==0xffffffff&&length==20,"Native original ACE copy changed");int end=MockAclEnd(acl);byte[] raw=new byte[length];Marshal.Copy(ace,raw,0,raw.Length);Marshal.Copy(raw,0,IntPtr.Add(acl,end),raw.Length);Marshal.WriteInt16(acl,4,(short)(1+(ushort)Marshal.ReadInt16(acl,4)));return true;}
    static bool MockAddAllowed(IntPtr acl,uint revision,uint flags,uint mask,byte[] sid){AbaMock.Check(revision==2&&flags==0&&mask==8&&MixelSessionDirectoryAba.Hash(sid)==MixelSessionDirectoryAba.Hash(AbaMock.User()),"Wrong granted ACE/SID/mask/flags");
      if(mockMode=="ace-add"){mockError=5;return false;}int count=(ushort)Marshal.ReadInt16(acl,4),size=MockAclEnd(acl);byte[] raw=new byte[size];Marshal.Copy(acl,raw,0,size);Buffer.BlockCopy(BitConverter.GetBytes((ushort)size),0,raw,2,2);byte[] added=AddOneAce(raw,sid);Marshal.Copy(added,0,acl,added.Length);AbaMock.Check((ushort)Marshal.ReadInt16(acl,4)==count+1,"Mock ACE count changed");return true;}
    static bool MockInitSecurity(IntPtr sd,uint revision){AbaMock.Check(revision==1,"Security descriptor revision changed");if(mockMode=="sd-init"){mockError=5;return false;}return true;}
    static bool MockAttachDacl(IntPtr sd,bool present,IntPtr acl,bool defaulted){AbaMock.Check(present&&!defaulted,"DACL present/defaulted changed");if(mockMode=="dacl-attach"){mockError=5;return false;}securityAcls[sd]=acl;return true;}
    static bool MockSet(IntPtr h,uint flags,IntPtr sd){AbaMock.Check(h.ToInt64()==123&&flags==4&&securityAcls.ContainsKey(sd),"Setter target/flags/descriptor changed");mockSets++;IntPtr acl=securityAcls[sd];byte[] raw=new byte[(ushort)Marshal.ReadInt16(acl,2)];Marshal.Copy(acl,raw,0,raw.Length);mockDescriptor=AbaMock.Descriptor(raw);securityAcls.Remove(sd);if(mockMode=="set-fails"){mockError=5;return false;}return true;}
    public static void VerifyNativeMock(){
      var api=new NativeApi();var scope=new Scope(api,AbaMock.User());scope.Open();scope.Apply();scope.Restore();scope.Dispose();AbaMock.Check(mockSets==2&&mockCloses==1&&mockUnloads==1&&allocations.Count==0&&scope.facts.cleanupOkay,"Native normal ownership failed");
      foreach(string fault in new[]{"acl-init","acl-info","ace-pointer","ace-add"}){mockMode=fault;api=new NativeApi();AbaMock.Refuse(()=>api.AddOwnedAce(AbaMock.Acl(2),AbaMock.User()),fault=="ace-pointer"?"Public ACE pointer":fault=="acl-init"?"ACL initialization":fault=="acl-info"?"ACL size":"ACE add");AbaMock.Check(allocations.Count==0,"Native ACL fault leaked buffers");api.Dispose();}
      foreach(string fault in new[]{"sd-init","dacl-attach"}){mockMode=fault;api=new NativeApi();AbaMock.Refuse(()=>{int error;api.SetDacl(new IntPtr(123),AbaMock.Descriptor(AbaMock.Acl(2)),out error);},fault=="sd-init"?"initialization":"attachment");AbaMock.Check(allocations.Count==0,"Setter build fault leaked buffers");api.Dispose();}
      mockMode="set-fails";api=new NativeApi();int code;bool okay=api.SetDacl(new IntPtr(123),AbaMock.Descriptor(AbaMock.Acl(2)),out code);AbaMock.Check(!okay&&code==5&&mockError==99&&allocations.Count==0,"Native setter error lost after buffer frees");api.Dispose();
      mockMode="read-fill";api=new NativeApi();AbaMock.Refuse(()=>api.ReadDescriptor(new IntPtr(123),7),"Descriptor fill");AbaMock.Check(allocations.Count==0,"Descriptor fill fault leaked buffer");api.Dispose();
      Console.WriteLine("PASS: actual compiled native-wrapper mock9 paths; directory48/Unicode16/public ACL12/direct error5 preserved across frees; no native API called.");
    }
'''


def run(command, *, env=None):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=180, env=env)
    if result.returncode:
        raise AssertionError(f'command exited {result.returncode}: {command}\nSTDOUT:\n{result.stdout[-16000:]}\nSTDERR:\n{result.stderr[-16000:]}')
    return result


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPOSED_WRAPPER_PROJECTION = [('  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null\n  $cleanupFailures=[Collections.Generic.List[Exception]]::new()\n  $observations=[Collections.Generic.List[object]]::new()\n  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()\n  try {\n    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)\n    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)\n    $scope.Open()\n', '  $scope=$null; $world=[IntPtr]::Zero; $bodyFailure=$null; $restoreFailure=$null; $reportFailure=$null\n  $cleanupFailures=[Collections.Generic.List[Exception]]::new()\n  $observations=[Collections.Generic.List[object]]::new()\n  $sourceHash=(Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()\n  # Preserve quarantine in the original outer finally even if this independent\n  # diagnostic fails before the later mask12 registration/lifecycle starts.\n  $script:namespaceRestorationKnown=$false\n  try {\n    $world=[MixelIsolatedLeaseControl]::WorldEvent($eventName)\n    $scope=[MixelSessionDirectoryAba+Scope]::new([MixelSessionDirectoryAba+NativeApi]::new(),$sidBytes)\n    $scope.Open()\n', 1), ("  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or\n      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {\n    throw 'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.'\n  }\n  if ($bodyFailure -or $reportFailure) {\n    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})\n    Write-Host 'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.'\n  }\n", "  if ($restoreFailure -or $cleanupFailures.Count -gt 0 -or\n      ($scope -and $scope.facts.writeAttempted -and -not $scope.facts.restorationVerified)) {\n    throw 'Incomplete temporary directory ACL/process ownership; original Standard gate must not run in an unknown modified environment.'\n  }\n  $script:namespaceRestorationKnown=$true\n  if ($bodyFailure -or $reportFailure) {\n    $script:abaDeferredFailure=$(if($bodyFailure){$bodyFailure}else{$reportFailure})\n    Write-Host 'FAIL: restored CI-only directory experiment failed; deferred behind unchanged original Standard gate.'\n  }\n", 1), ('{\n  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",\n  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",\n  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",\n  "test-support-launch-windows.ps1": "012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec",\n  "test-support-lease-standard-windows.ps1": "a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f",\n  "test-support-lease.py": "d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604",\n  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",\n  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",\n  "test-windows-lease-observation.py": "bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e"\n}\n\'@\nforeach ($pin in $pins.PSObject.Properties) {\n  $path=Join-Path $PSScriptRoot $pin.Name\n', '{\n  "support-invite-guard.rs": "4b48b7995b36cc9662958ef8753be70ef0b6cf35626b7264f0151bc553293f29",\n  "test-session-directory-access.cs": "c562cf11de6dc395d4a6aae6d887dbe7fb970603bde74c8e4d111ac26956eb76",\n  "test-support-directory-security.cs": "3f70bcef70508b264a9559dbb9f7fc9bdbb0c3e28ea7929008dc084dec6d973e",\n  "test-support-launch-windows.ps1": "c1f3e99ebda0a146bce9b54f215feeebd0c6d19bec19f2a280259d311983c44b",\n  "test-support-lease-standard-windows.ps1": "c2897356a81f5410789fcebab4e845639a329da892b41cff4675aa08bc17f15e",\n  "test-support-lease.py": "bdba6cc24c0b247de85a3bcda79744dd932f00bb6e97d038eba14a7e43d1db64",\n  "test-support-nt-event-binding.cs": "4f166e8e0fbdce7a5fb6786862e031c5bb175704e7490a0e4a9feaac9fac62ca",\n  "test-support-nt-event-child.rs": "80cf1b1969b66568910d3a84ba46d4749db1c5744aac21b69777cd795dc0b034",\n  "test-windows-lease-observation.py": "2de9b36dc99e76523572f8db19fdeba0b2747769a87b476a10819470690f23f9",\n  "rust_toolchain.py": "2156c1452df874462a5e2a569f25a800a339cc2cb92f5732340aa12a633afa3c",\n  "windows-standard-namespace-readonly.rs": "23e5fee444459cb68ed7c2bb2ac6c8696583697ce8874019101b16659334b705",\n  "windows-standard-namespace-access.cs": "7f04727ae8161b8e4da1d2e4a74a0324549996da5af52a08c14538ccf53b2589",\n  "windows-standard-namespace-diagnostics.cs": "980178cba4736f260d1365d3f19b3371158efcd0856d75fb12c036b0a093aad8"\n}\n\'@\nforeach ($pin in $pins.PSObject.Properties) {\n  $path=Join-Path $PSScriptRoot $pin.Name\n', 1)]
HISTORICAL_WRAPPER_SHA = '5d81c7737eb2e9040fbb7f2c0046ec6649f774ca46098f3f61faceacb8a97b46'

ACK_VIEW = load('ack_unit_view', SCRIPTS / 'test-support-directory-security.py')
inverse_current_ack_source = ACK_VIEW.inverse_current_ack_source
ACK_SOURCE_INVERSES = ACK_VIEW.ACK_SOURCE_INVERSES


class Controls(unittest.TestCase):
    def ps(self, body, *, include_mock=False):
        with tempfile.TemporaryDirectory(prefix='mixel-aba-source-') as folder:
            root = Path(folder)
            (root/'helper.cs').write_text(CS.read_text(encoding='utf-8')+(MOCK if include_mock else ''), encoding='utf-8')
            prefix = "$ErrorActionPreference='Stop'\nAdd-Type -Path (Join-Path $PSScriptRoot 'helper.cs') -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
            (root/'control.ps1').write_text(prefix+body, encoding='utf-8-sig')
            return run([shutil.which('pwsh') or 'pwsh', '-NoProfile', '-NonInteractive', '-File', str(root/'control.ps1')])

    def test_mask_eight_only_and_preserved_positive_control_source(self):
        # Reverse only three exact diagnostic-mask operands to the already
        # qualified mask15 source. Every native API/lifetime/state guard stays exact.
        text=CS.read_text(encoding='utf-8')
        restored=text
        for current, previous in [('addedMask=8','addedMask=15'),
                                  ('Put(value,end+4,8);','Put(value,end+4,15);'),
                                  ('AddAccessAllowedAceEx(target,revision,0,8,sid)',
                                   'AddAccessAllowedAceEx(target,revision,0,15,sid)')]:
            self.assertEqual(restored.count(current),1)
            restored=restored.replace(current,previous)
        self.assertEqual(hashlib.sha256(restored.encode('utf-8')).hexdigest(),
                         'bf291d27fcdb4aec3cae1b19b29f70491c74159dafba5e49642746cbf6233379')
        wrapper=WRAPPER.read_text(encoding='utf-8')
        for original, added, count in COMPOSED_WRAPPER_PROJECTION:
            self.assertEqual(wrapper.count(added), count)
            wrapper = wrapper.replace(added, original)
        self.assertEqual(hashlib.sha256(wrapper.encode()).hexdigest(), HISTORICAL_WRAPPER_SHA)
        # Reverse only the exact final-carrier source-family pin rebindings.
        for current, original in [('012ad7b7a2e52ddc7bd1c2c7aae63b7726187dab2d6aa2291bbb39de82928dec', '765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'), ('d11b25c95232544eb64f5de78f0e31cbc54630dd832185c55edfb4a3157cd604', 'f1051afc3860751cfea233216fa65cd1f7e29b639200f648c203dafc294530a9'), ('bc64f1bd16d32d201de7654b04f26074428a742f896d18977a6012408329a75e', '6f1ea99ec821be91fe88c13d8b62743346265e145b694dc4f05a60e94bcb121e')]:
            self.assertEqual(wrapper.count(current), 1)
            wrapper = wrapper.replace(current, original, 1)
        self.assertEqual(wrapper.count(hashlib.sha256(CS.read_bytes()).hexdigest()),1)
        previous=wrapper.replace(hashlib.sha256(CS.read_bytes()).hexdigest(),
                                 'bf291d27fcdb4aec3cae1b19b29f70491c74159dafba5e49642746cbf6233379')
        self.assertEqual(hashlib.sha256(previous.encode('utf-8')).hexdigest(),
                         '507f96dd8c8292767aca8dff1b38fa3967f3600b30aeae9ccd767301518dd815')
        # The failed mask4 remains a second immutable source reference.
        previous_four=CS.read_text(encoding='utf-8')
        for current, previous in [('addedMask=8','addedMask=4'),
                                  ('Put(value,end+4,8);','Put(value,end+4,4);'),
                                  ('AddAccessAllowedAceEx(target,revision,0,8,sid)',
                                   'AddAccessAllowedAceEx(target,revision,0,4,sid)')]:
            self.assertEqual(previous_four.count(current),1)
            previous_four=previous_four.replace(current,previous)
        self.assertEqual(hashlib.sha256(previous_four.encode('utf-8')).hexdigest(),
                         '65fc503abd02da1fc50f0e2abe73a747fd21468512298376cea73062422b0a64')
        prior_wrapper=wrapper.replace(hashlib.sha256(CS.read_bytes()).hexdigest(),
                                    '65fc503abd02da1fc50f0e2abe73a747fd21468512298376cea73062422b0a64')
        self.assertEqual(hashlib.sha256(prior_wrapper.encode('utf-8')).hexdigest(),
                         'e570c508d1515850b6865e8eb68c4a75cfe6da9d0be057477dca968f05bca6c5')
        print('PASS: fixed DIRECTORY_CREATE_SUBDIRECTORY8 only; full native/lifetime/identity/restore/source/Std8 reverses to qualified mask15 bytes.',flush=True)

    def test_actual_csharp_scope_fault_and_descriptor_controls(self):
        result = self.ps('[AbaMock]::Unit()\n', include_mock=True)
        self.assertIn('PASS: actual C# mock', result.stdout)
        print(result.stdout.strip(), flush=True)

    def test_actual_native_argument_and_public_api_source_boundary(self):
        text=CS.read_text(encoding='utf-8')
        for actual in ['0x60000', 'OpenSessionDirectory(facts.session,0x60000,out output)',
                       'SetKernelObjectSecurity(handle,4,security)', 'information==7||information==16',
                       '"ntdll.dll"),IntPtr.Zero,0x800)', '"NtOpenDirectoryObject"',
                       'Length=48,ObjectName=name,Flags=64', '\\Sessions\\', '\\BaseNamedObjects',
                       'facts.writeAttempted=true;facts.grant="attempted"', 'error=okay?0:Marshal.GetLastWin32Error()']:
            self.assertIn(actual,text)
        for forbidden in ['AdjustTokenPrivileges', 'SetSecurityInfo(', 'AccessCheck(', 'NtCreate',
                          'SetEvent(', 'ResetEvent(', 'OpenProcessToken(', 'WaitForSingleObject(']:
            self.assertNotIn(forbidden,text)
        self.assertNotIn('S-1-',text)
        self.assertEqual(text.count('SetKernelObjectSecurity(handle,4,security)'),1)
        self.ps("'PASS: exact C# compiles warning-fatal; native calls inspected only.'\n")

    def test_actual_compiled_native_wrappers_with_only_api_boundaries_mocked(self):
        text=CS.read_text(encoding='utf-8')
        guard=text.index('      Require(Environment.OSVersion.Platform==PlatformID.Win32NT')
        last=text.index('"Explicit disposable hosted Windows diagnostic scope required");',guard)+len('"Explicit disposable hosted Windows diagnostic scope required");')
        text=text[:guard]+'      Require(true,"Mock only; native CI scope remains unchanged in shipping helper");'+text[last:]
        replacements={
          'ProcessIdToSessionId':'MockSession(pid,out session)', 'GetCurrentProcessId':'MockCurrentPid()',
          'GetSystemDirectoryW':'MockSystemDirectory(buffer,size)', 'LoadLibraryExW':'MockLoad(name,file,flags)',
          'GetProcAddress':'MockResolve(module,name)', 'FreeLibrary':'MockUnload(module)', 'CloseHandle':'MockClose(handle)',
          'GetKernelObjectSecurity':'MockRead(handle,information,descriptor,capacity,out needed)',
          'SetKernelObjectSecurity':'MockSet(handle,information,descriptor)',
          'InitializeAcl':'MockInitializeAcl(acl,length,revision)', 'GetAclInformation':'MockAclSize(acl,information,length,kind)',
          'GetAce':'MockGetAce(acl,index,out ace)', 'AddAce':'MockAddAce(acl,revision,index,ace,length)',
          'AddAccessAllowedAceEx':'MockAddAllowed(acl,revision,flags,mask,sid)',
          'InitializeSecurityDescriptor':'MockInitSecurity(descriptor,revision)',
          'SetSecurityDescriptorDacl':'MockAttachDacl(descriptor,present,acl,defaulted)',
        }
        def project(match):
            signature=match.group(1);name=re.search(r'(\w+)\(',signature).group(1)
            self.assertIn(name,replacements)
            return '    static '+signature+' {return '+replacements.pop(name)+';}'
        text,count=re.subn(r'^    \[DllImport.*\] static extern ([^;\n]+);$',project,text,flags=re.M)
        self.assertEqual(count,16)
        self.assertEqual(replacements,{})
        text=text.replace('Marshal.GetLastWin32Error()','mockError').replace('Marshal.AllocHGlobal(','MockAlloc(').replace('Marshal.FreeHGlobal(','MockFree(').replace('Marshal.StringToHGlobalUni(','MockText(')
        marker='    IntPtr module=IntPtr.Zero;DirectoryOpen open;'
        self.assertEqual(text.count(marker),1)
        text=text.replace(marker,NATIVE_MOCK+'\n'+marker,1)+MOCK
        with tempfile.TemporaryDirectory(prefix='mixel-aba-native-boundary-') as folder:
            root=Path(folder);(root/'mock.cs').write_text(text,encoding='utf-8')
            (root/'run.ps1').write_text("$ErrorActionPreference='Stop'\nAdd-Type -Path (Join-Path $PSScriptRoot 'mock.cs') -CompilerOptions /warnaserror+ -ErrorAction Stop\n[MixelSessionDirectoryAba+NativeApi]::VerifyNativeMock()\n",encoding='utf-8-sig')
            result=run([shutil.which('pwsh') or 'pwsh','-NoProfile','-NonInteractive','-File',str(root/'run.ps1')])
            print(result.stdout.strip(),flush=True)

    def test_wrapper_default_refuses_without_native_execution(self):
        env=os.environ.copy()
        env['GITHUB_ACTIONS']='false'
        with tempfile.TemporaryDirectory(prefix='mixel-aba-default-') as folder:
            destination=Path(folder)/'not-created'
            result=subprocess.run([shutil.which('pwsh') or 'pwsh','-NoProfile','-NonInteractive','-File',str(WRAPPER),'-EvidenceDirectory',str(destination)],capture_output=True,text=True,encoding='utf-8',env=env,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Explicit optional disposable hosted Windows CI scope required',result.stderr)
            self.assertFalse(destination.exists())

    def test_ack_unit_view_closed_inverse_and_drift_refusal(self):
        for name, binding in ACK_SOURCE_INVERSES.items():
            data=(SCRIPTS/name).read_bytes().replace(b'\r\n',b'\n')
            for raw in (data, data.replace(b'\n',b'\r\n')):
                self.assertEqual(hashlib.sha256(inverse_current_ack_source(name,raw)).hexdigest(),binding['inputSHA256'])
            for raw in (data+b'# changed\n', data.replace(b'\n',b'\r\n',1), data[:-1]):
                with self.assertRaises(AssertionError):
                    inverse_current_ack_source(name,raw)
        print('PASS: exact three current ack fixture byte inverses recover the frozen optional family in LF/CRLF; drift, mixed and truncated sources refuse. Frozen runtime helpers remain unchanged.')

    def test_actual_composition_preserves_original_source_and_gate(self):
        original=SCRIPTS/'test-support-lease-standard-windows.ps1'
        body=r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__WRAPPER__',[ref]$t,[ref]$e)
if($e.Count){throw 'Wrapper parse failed'}
$functions=@($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
if($functions.Count -ne 4){throw 'Function family changed'}
foreach($fn in $functions){. ([scriptblock]::Create($fn.Extent.Text))}
$original=[IO.File]::ReadAllText('__ORIGINAL__')
$composed=New-SessionDirectoryAbaScript $original 'PREFIX'
$reversed=$composed.Substring(7).Replace("  Invoke-SessionDirectoryAba`n",'')
$suffix="`nif (`$script:abaDeferredFailure) { throw `$script:abaDeferredFailure }`n"
if(-not $reversed.EndsWith($suffix)){throw 'Original fatal priority suffix changed'}
$reversed=$reversed.Substring(0,$reversed.Length-$suffix.Length)
if($reversed -cne $original){throw 'Original full source changed'}
foreach($invalid in @('missing boundary', ($original+$original))){
 $failed=$false;try{New-SessionDirectoryAbaScript $invalid 'PREFIX'|Out-Null}catch{$failed=$true}
 if(-not $failed){throw 'Missing/duplicate insertion accepted'}
}
$script:abaDeferredFailure='DIAGNOSTIC'
$first=$null;try{& ([scriptblock]::Create("throw 'ORIGINAL_NATIVE5'"+$suffix))}catch{$first=$_.Exception.Message}
if($first -cne 'ORIGINAL_NATIVE5'){throw 'Original native gate priority changed'}
'PASS: exact entire original source reverses; missing/duplicate refuse; actual old native5 outranks deferred diagnostic.'
'''.replace('__WRAPPER__',str(WRAPPER).replace("'","''")).replace('__ORIGINAL__',str(original).replace("'","''"))
        print(self.ps(body).stdout.strip(),flush=True)
        wrapper=WRAPPER.read_text(encoding='utf-8')
        pins=json.loads(re.search(r"\$pins=ConvertFrom-Json @'\n(.*?)\n'@",wrapper,re.S).group(1))
        for name,digest in pins.items():
            self.assertEqual(hashlib.sha256(inverse_current_ack_source(name, (SCRIPTS/name).read_bytes())).hexdigest(),digest,name)

    def test_literal_powershell_orchestration_restoration_and_error_precedence(self):
        body=r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__WRAPPER__',[ref]$t,[ref]$e)
foreach($fn in @($tree.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.FunctionDefinitionAst]})){
 $text=$fn.Extent.Text.Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)').Replace('[MixelSessionDirectoryAba+NativeApi]::new()','[AbaMock+Platform]::new()')
 . ([scriptblock]::Create($text))
}
function Get-LocalUser {return [pscustomobject]@{Name=$ownedUser}}
function Get-Process {return [pscustomobject]@{SessionId=2}}
function Assert-StandardLeaseSafeResult($Record,$ProcessId){if($Record.pid -ne $ProcessId){throw 'Wrong actual child tuple'}}
$script:probeCalls=[Collections.Generic.List[string]]::new();$script:nextPid=4000;$script:probeFault='none'
function Invoke-StandardLeaseControl($Mode,$Launcher,[switch]$ObserveOnly,$ObservationLabel){
 if($Mode -cne 'probe' -or -not $ObserveOnly -or $Launcher -cnotin @('with-logon','with-token')){throw 'Changed source launcher/mode'}
 $script:probeCalls.Add($ObservationLabel+';'+$Launcher)
 if($script:probeFault -eq 'before' -and $ObservationLabel.EndsWith('before')){throw 'Injected before body failure'}
 if($script:probeFault -eq 'temporary' -and $ObservationLabel.EndsWith('temporary')){throw 'Injected temporary body failure'}
 $script:nextPid++;$record=@{pid=$script:nextPid;rawOpenError=$(if($ObservationLabel.EndsWith('temporary')){0}else{5});sourceStatus='raw source classification';attestation='guard-unavailable';requiresClick=$true;ownerFailed=$false}
 [IO.File]::WriteAllText($resultPath,($record|ConvertTo-Json -Compress));return $script:nextPid
}
$ownedUser='mixellease0123456789';$ownedSid='owned-local-qa-sid';$ownedPassword='private-not-emitted'
$runnerFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=12288;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$freshFacts=[pscustomobject]@{unavailableKind=$null;integrityRid=8192;hasRestrictions=$false;isAppContainer=$false;sessionId=2}
$eventName='Global\Mixel-Remote-Standard-Lease-'+('d'*32)
$root=Join-Path $PSScriptRoot 'owned';[void][IO.Directory]::CreateDirectory($root)
$executable=Join-Path $root 'lease-standard.exe';[IO.File]::WriteAllText($executable,'unchanged-original-compiled-exe-mock')
$resultPath=Join-Path $root 'fixture-result.json'
foreach($mode in @('normal','set-false','set-throw','restore-false','restore-readback-drift','close-false','unload-throw','open-denied','query-fails','foreign-drift','body-before','body-temporary','stop-fails','close-fails','exit-fails','identity-fails')){
 $script:abaEvidence=Join-Path $root $mode;[void][IO.Directory]::CreateDirectory($script:abaEvidence)
 $script:abaDeferredFailure=$null;$script:probeCalls.Clear();$script:probeFault='none'
 $script:namespaceRestorationKnown=$true
 [AbaMock+Platform]::Mode=$mode;[MixelOrdinaryTokenFixture]::mode=$mode
 if($mode.StartsWith('body-')){[AbaMock+Platform]::Mode='normal';$script:probeFault=$mode.Substring(5)}
 if($mode -eq 'identity-fails'){$freshFacts.integrityRid=12288}
 $failed=$null;try{Invoke-SessionDirectoryAba}catch{$failed=$_}
 $fatal=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw','stop-fails','close-fails','exit-fails','identity-fails')
 if(($null -ne $failed) -ne $fatal){throw ('Wrong fatal-vs-deferred: '+$mode+' '+$failed)}
 if($mode -eq 'identity-fails'){$freshFacts.integrityRid=8192;continue}
 if([bool]$script:namespaceRestorationKnown -eq $fatal){throw ('Actual pre-registration diagnostic lost restoration/quarantine flag: '+$mode)}
 $report=[IO.File]::ReadAllText((Join-Path $script:abaEvidence 'session-directory-aba.json'))|ConvertFrom-Json
 if($report.actualWin32RouteMeasured -or $report.causeEstablished -or $report.productionWorkaroundQualified -or $report.standardEightQualified){throw 'Qualification escaped mock scope'}
 if($mode -eq 'normal'){
  if($report.observations.Count -ne 6 -or $script:probeCalls.Count -ne 6){throw 'Both source launchers/three phases missing'}
  if(($report.observations.rawOpenError -join ',') -cne '5,5,0,0,5,5'){throw 'Ordered raw outcomes changed'}
  if(-not $report.directory.restorationVerified -or -not $report.directory.cleanupOkay){throw 'Restoration/cleanup unproved'}
 }
 if(-not $fatal -and $mode -ne 'normal' -and -not $script:abaDeferredFailure){throw ('Lost deferred diagnostic: '+$mode)}
 if($mode -in @('set-false','set-throw','body-temporary')){
  if([AbaMock+Platform]::Last.sets -ne 2 -or -not $report.directory.restorationVerified){throw 'Body/setter failure skipped restore'}
 }
 if($report|ConvertTo-Json -Depth 12 -Compress|Select-String -Pattern 'owned-local-qa-sid|private-not-emitted|S-1-5-') {throw 'Private identity escaped redaction'}
}
'PASS: literal PS orchestration16 scenarios; six ordered children/both launchers; restore faults halt; restored body failures defer; no native calls.'
'''.replace('__WRAPPER__',str(WRAPPER).replace("'","''"))
        print(self.ps(body,include_mock=True).stdout.strip(),flush=True)

    def test_original_standard_and_gui_windows_metadata_and_full_conservation(self):
        lease=load('aba_original_lease',SCRIPTS/'test-support-lease.py')
        gui=load('aba_original_gui',SCRIPTS/'build-standard-gui-connection-fixture.py')
        compiler=os.environ.get('RUSTC_BIN') or shutil.which('rustc')
        self.assertTrue(compiler,'Actual Rust compiler required')
        with tempfile.TemporaryDirectory(prefix='mixel-aba-windows-metadata-') as folder:
            directory=Path(folder);event='Global\\Mixel-Remote-Standard-Lease-'+'d'*32
            guard=(SCRIPTS/'support-invite-guard.rs').read_text(encoding='utf-8')
            standard=lease.standard_windows_fixture_body(guard,directory,'mixel-aba-'+event[-32:]+'-',event)
            generated_gui=gui.generated_source(directory,event,guard)
            # Regenerate the committed helper family independently with unchanged bytes.
            original_repo=os.environ.get('MIXEL_ABA_SOURCE_ROOT')
            if original_repo:
                oldgui=load('aba_committed_gui',Path(original_repo)/'scripts/build-standard-gui-connection-fixture.py')
                self.assertEqual(generated_gui,oldgui.generated_source(directory,event,guard))
            for name,source in [('standard',standard),('gui',generated_gui)]:
                path=directory/(name+'.rs');path.write_text(source,encoding='utf-8')
                target=[] if os.name=='nt' else ['--target','x86_64-pc-windows-msvc']
                result=run([compiler,'--edition','2021','-D','warnings',*target,'--emit=metadata',str(path),'-o',str(directory/(name+'.rmeta'))])
                self.assertEqual(result.stderr,'')
            self.assertEqual(list(directory.glob('*.exe')),[])
            print('PASS: unchanged complete Standard and GUI Windows-target Rust metadata, warnings fatal; no native child executed.',flush=True)

    def test_optional_workflow_false_and_source_phase_order(self):
        text=(ROOT/'.github/workflows/preflight.yml').read_text(encoding='utf-8')
        self.assertIn('session_directory_access_observation:',text)
        block=text.split('      session_directory_access_observation:',1)[1].split('\npermissions:',1)[0]
        self.assertIn('default: false',block)
        job=text.split('\n  session-directory-access-observation:',1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.session_directory_access_observation",job)
        self.assertIn("MIXEL_SESSION_DIRECTORY_ABA: '1'",job)
        self.assertIn("GIT_CONFIG_KEY_1: core.eol",job)
        self.assertIn("GIT_CONFIG_VALUE_1: 'lf'",job)
        self.assertLess(job.index('test-session-directory-access.py'),job.index('observe-standard-valid-qa-windows.ps1'))
        self.assertIn('if: always()',job)
        self.assertNotIn('continue-on-error',job)
        self.assertNotIn('publish',job)


if __name__=='__main__':
    unittest.main(verbosity=2)
