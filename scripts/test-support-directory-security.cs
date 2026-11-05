using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

// Isolated source fixture only. The parent observes two explicit existing object
// directories; this never identifies the Win32 event's internally resolved route.
public static class DirectorySecurityPrototype {
  [StructLayout(LayoutKind.Sequential)] public struct UnicodeString {
    public ushort Length, MaximumLength; public IntPtr Buffer;
  }
  [StructLayout(LayoutKind.Sequential)] public struct ObjectAttributes {
    public uint Length; public IntPtr RootDirectory, ObjectName;
    public uint Attributes; public IntPtr SecurityDescriptor, SecurityQualityOfService;
  }
  public class NativeApi {
    [StructLayout(LayoutKind.Sequential)] private struct AclSizeInformation { public uint AceCount,AclBytesInUse,AclBytesFree; }
    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    public delegate int NtOpen(out IntPtr handle, uint access, ref ObjectAttributes attributes);
    private IntPtr module; private NtOpen open;
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)] static extern uint GetSystemDirectoryW(StringBuilder path,uint capacity);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)] static extern IntPtr LoadLibraryExW(string path,IntPtr file,uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Ansi, ExactSpelling=true, SetLastError=true)] static extern IntPtr GetProcAddress(IntPtr library,string name);
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)] static extern bool FreeLibrary(IntPtr library);
    [DllImport("kernel32.dll", ExactSpelling=true)] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)] static extern uint GetProcessId(IntPtr process);
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)] static extern bool ProcessIdToSessionId(uint pid,out uint session);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool OpenProcessToken(IntPtr process,uint access,out IntPtr token);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool GetTokenInformation(IntPtr token,int kind,IntPtr data,uint length,out uint returned);
    [DllImport("advapi32.dll", ExactSpelling=true)] static extern uint GetSecurityInfo(IntPtr handle,uint objectType,uint information,IntPtr owner,IntPtr group,IntPtr dacl,IntPtr sacl,out IntPtr descriptor);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool GetSecurityDescriptorDacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool GetSecurityDescriptorSacl(IntPtr descriptor,out bool present,out IntPtr acl,out bool defaulted);
    [DllImport("advapi32.dll", ExactSpelling=true)] static extern bool IsValidSecurityDescriptor(IntPtr descriptor);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool GetSecurityDescriptorControl(IntPtr descriptor,out ushort control,out uint revision);
    [DllImport("advapi32.dll", ExactSpelling=true)] static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool GetAclInformation(IntPtr acl,out AclSizeInformation size,uint length,int informationClass);
    [DllImport("advapi32.dll", ExactSpelling=true, SetLastError=true)] static extern bool GetAce(IntPtr acl,uint index,out IntPtr ace);
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)] static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    public virtual IntPtr CurrentProcess() { return GetCurrentProcess(); }
    public virtual uint ProcessId(IntPtr process,out int error) { uint pid=GetProcessId(process); error=pid==0?Marshal.GetLastWin32Error():0; return pid; }
    public virtual bool Session(uint pid,out uint session,out int error) { bool ok=ProcessIdToSessionId(pid,out session); error=ok?0:Marshal.GetLastWin32Error(); return ok; }
    public virtual bool OpenToken(IntPtr process,out IntPtr token,out int error) { bool ok=OpenProcessToken(process,8,out token); error=ok?0:Marshal.GetLastWin32Error(); return ok; }
    public virtual bool TokenInformation(IntPtr token,int kind,IntPtr data,uint length,out uint returned,out int error) { bool ok=GetTokenInformation(token,kind,data,length,out returned); error=ok?0:Marshal.GetLastWin32Error(); return ok; }
    public virtual string Load(out int error) {
      if(module!=IntPtr.Zero || open!=null) throw new InvalidOperationException("Module already owned");
      StringBuilder path=new StringBuilder(32768); uint size=GetSystemDirectoryW(path,32768);
      error=size==0?Marshal.GetLastWin32Error():0;
      if(size==0) return "system-directory-error";
      if(size>=32768) return "system-directory-size";
      // Absolute System32 location and documented restricted search; no borrowed
      // module is unloaded. The reference stays alive through both directory calls.
      module=LoadLibraryExW(System.IO.Path.Combine(path.ToString(),"ntdll.dll"),IntPtr.Zero,0x800);
      if(module==IntPtr.Zero) { error=Marshal.GetLastWin32Error(); return "load-error"; }
      IntPtr entry=GetProcAddress(module,"NtOpenDirectoryObject");
      if(entry==IntPtr.Zero) { error=Marshal.GetLastWin32Error(); return "api-unavailable"; }
      open=(NtOpen)Marshal.GetDelegateForFunctionPointer(entry,typeof(NtOpen)); return "available";
    }
    public virtual string Unload(out int error) {
      error=0; if(module==IntPtr.Zero) return "not-loaded";
      bool ok=FreeLibrary(module); error=ok?0:Marshal.GetLastWin32Error();
      if(ok) { module=IntPtr.Zero; open=null; } return ok?"unloaded":"error";
    }
    public virtual int OpenDirectory(out IntPtr handle,uint access,ref ObjectAttributes attributes) {
      if(open==null) throw new InvalidOperationException("Directory API unavailable");
      return open(out handle,access,ref attributes);
    }
    public virtual uint Query(IntPtr handle,uint information,out IntPtr descriptor) {
      return GetSecurityInfo(handle,6,information,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,out descriptor);
    }
    public virtual bool Acl(IntPtr descriptor,uint information,out bool present,out IntPtr acl,out bool defaulted,out int error) {
      bool ok=information==4?GetSecurityDescriptorDacl(descriptor,out present,out acl,out defaulted):GetSecurityDescriptorSacl(descriptor,out present,out acl,out defaulted);
      error=ok?0:Marshal.GetLastWin32Error(); return ok;
    }
    public virtual string DescriptorSize(IntPtr descriptor,out uint length,out int error) {
      length=0; error=0;
      if(!IsValidSecurityDescriptor(descriptor)) return "invalid-descriptor";
      ushort control; uint revision;
      if(!GetSecurityDescriptorControl(descriptor,out control,out revision)) { error=Marshal.GetLastWin32Error(); return "control-error"; }
      if(revision!=1 || (control&0x8000)==0) return "not-self-relative";
      length=GetSecurityDescriptorLength(descriptor);
      return length>=20 && length<=1048576?"available":"descriptor-size";
    }
    public virtual bool AclSize(IntPtr acl,out uint count,out uint used,out uint free,out int error) {
      if(Marshal.SizeOf(typeof(AclSizeInformation))!=12) throw new InvalidOperationException("ACL size ABI changed");
      AclSizeInformation size; bool ok=GetAclInformation(acl,out size,12,2);
      error=ok?0:Marshal.GetLastWin32Error(); count=size.AceCount; used=size.AclBytesInUse; free=size.AclBytesFree; return ok;
    }
    public virtual bool Ace(IntPtr acl,uint index,out IntPtr ace,out int error) { bool ok=GetAce(acl,index,out ace); error=ok?0:Marshal.GetLastWin32Error(); return ok; }
    public virtual bool Free(IntPtr descriptor,out int error) { IntPtr result=LocalFree(descriptor); error=result==IntPtr.Zero?0:Marshal.GetLastWin32Error(); return result==IntPtr.Zero; }
    public virtual bool Close(IntPtr handle,out int error) { bool ok=CloseHandle(handle); error=ok?0:Marshal.GetLastWin32Error(); return ok; }
  }
  public sealed class AceFacts {
    public byte type,flags; public ushort size; public uint? mask,integrityRid;
    public string classification="unknown";
    public bool? ownedUserMatches,currentPrimaryMatches,childPrimaryMatches,currentLogonMatches,childLogonMatches;
  }
  public sealed class QueryFacts {
    public uint information; public uint? nativeError; public string status="not-attempted",parse="not-attempted",free="not-allocated";
    public int? getterError,freeError; public bool? present,isNull,defaulted; public bool complete;
    public AceFacts[] aces=new AceFacts[0];
  }
  public sealed class DirectoryFacts {
    public string pathKind; public int? ntStatus; public string open="not-attempted",close="not-opened"; public int? closeError;
    public QueryFacts dacl=new QueryFacts { information=4 },label=new QueryFacts { information=16 };
  }
  public sealed class TokenFacts {
    public string open="not-attempted",user="not-attempted",logon="not-attempted",integrity="not-attempted",close="not-opened";
    public int? openError,userError,logonError,integrityError,closeError; public uint? integrityRid;
  }
  private sealed class Identity {
    public readonly TokenFacts facts=new TokenFacts(); internal byte[] user,logon;
  }
  public sealed class Observation {
    public int schema=1,pid; public uint childSession; public string scope="ExplicitDirectorySecurityOnly",context="ParentCall",identityComparison="PrimaryTokensOnly";
    public bool parentCallingThreadTokenMeasured=false,actualEventRouteMeasured=false,effectiveAccessCalculated=false,objectsCreated=false,causeEstablished=false;
    public string load="not-attempted",unload="not-loaded"; public int? loadError,unloadError;
    public TokenFacts current,child; public DirectoryFacts[] directories;
  }
  private static bool Equal(byte[] a,byte[] b) {
    if(a==null || b==null || a.Length!=b.Length) return false;
    for(int i=0;i<a.Length;i++) if(a[i]!=b[i]) return false; return true;
  }
  private static bool? Matches(byte[] sid,byte[] identity) { return identity==null?(bool?)null:Equal(sid,identity); }
  private static byte[] Sid(byte[] bytes,int offset,int end) {
    if(offset<0 || end>bytes.Length || offset>end-8 || bytes[offset]!=1 || bytes[offset+1]>15) throw new InvalidOperationException("SID header bounds");
    int size=8+bytes[offset+1]*4;
    if(size>end-offset) throw new InvalidOperationException("SID content bounds");
    byte[] result=new byte[size]; Buffer.BlockCopy(bytes,offset,result,0,size); return result;
  }
  private static ulong Authority(byte[] sid) { ulong result=0; for(int i=2;i<8;i++) result=(result<<8)|sid[i]; return result; }
  private static string Classification(byte[] sid) {
    ulong a=Authority(sid); int n=sid[1];
    if(a==1 && n==1 && BitConverter.ToUInt32(sid,8)==0) return "World";
    if(a==5 && n==1 && BitConverter.ToUInt32(sid,8)==18) return "SYSTEM";
    if(a==5 && n==2 && BitConverter.ToUInt32(sid,8)==32 && BitConverter.ToUInt32(sid,12)==544) return "Administrators";
    if(a==16 && n==1) return "MandatoryIntegrity";
    return "other";
  }
  private static byte[] BufferSid(IntPtr buffer,int length,IntPtr pointer) {
    long relative=pointer.ToInt64()-buffer.ToInt64();
    if(relative<0 || relative>length-8) throw new InvalidOperationException("Token SID outside allocation");
    int offset=(int)relative; byte[] bytes=new byte[length]; Marshal.Copy(buffer,bytes,0,length); return Sid(bytes,offset,length);
  }
  private static byte[] TokenBuffer(NativeApi api,IntPtr token,int kind,out int error,out bool querySucceeded) {
    querySucceeded=false;
    uint required; bool first=api.TokenInformation(token,kind,IntPtr.Zero,0,out required,out error);
    if(first) throw new InvalidOperationException("Token size query contract");
    if(error!=122) return null;
    uint minimum=kind==2?8u:16u;
    if(required<minimum || required>1048576) throw new InvalidOperationException("Token buffer size bound");
    IntPtr buffer=Marshal.AllocHGlobal((int)required);
    try {
      byte[] zero=new byte[required]; Marshal.Copy(zero,0,buffer,zero.Length);
      uint returned; if(!api.TokenInformation(token,kind,buffer,required,out returned,out error)) return null;
      if(returned>required || returned<minimum) throw new InvalidOperationException("Token returned size bound");
      byte[] result;
      if(kind==1 || kind==25) result=BufferSid(buffer,(int)returned,Marshal.ReadIntPtr(buffer));
      else {
        uint count=unchecked((uint)Marshal.ReadInt32(buffer));
        if(count>1024 || (ulong)8+(ulong)count*16>returned) throw new InvalidOperationException("Token groups bound");
        result=null;
        for(uint i=0;i<count;i++) {
          int offset=8+(int)i*16; uint attributes=unchecked((uint)Marshal.ReadInt32(buffer,offset+8));
          if((attributes&0xc0000000)!=0xc0000000) continue;
          if(result!=null) throw new InvalidOperationException("Multiple logon SIDs");
          result=BufferSid(buffer,(int)returned,Marshal.ReadIntPtr(buffer,offset));
          if(Authority(result)!=5 || result[1]!=3 || BitConverter.ToUInt32(result,8)!=5) throw new InvalidOperationException("Logon SID contract");
        }
      }
      querySucceeded=true;
      return result;
    } finally { Marshal.FreeHGlobal(buffer); }
  }
  private static Identity ReadIdentity(NativeApi api,IntPtr borrowedProcess) {
    Identity result=new Identity(); IntPtr token; int error;
    if(!api.OpenToken(borrowedProcess,out token,out error)) { result.facts.open="error"; result.facts.openError=error; return result; }
    if(token==IntPtr.Zero || token.ToInt64()==-1 || token==borrowedProcess) { result.facts.open="invalid-output"; return result; }
    result.facts.open="available"; result.facts.openError=0;
    try {
      foreach(int kind in new int[] {1,2,25}) {
        string status; byte[] bytes=null; int? failure=null;
        try { bool querySucceeded; bytes=TokenBuffer(api,token,kind,out error,out querySucceeded); status=!querySucceeded?"error":bytes==null?"absent":"available"; failure=error; }
        catch(InvalidOperationException) { status="contract"; }
        if(kind==1) { result.user=bytes; result.facts.user=status; result.facts.userError=failure; }
        else if(kind==2) { result.logon=bytes; result.facts.logon=status; result.facts.logonError=failure; }
        else {
          if(bytes!=null && Authority(bytes)==16 && bytes[1]==1) result.facts.integrityRid=BitConverter.ToUInt32(bytes,8);
          else if(bytes!=null) status="contract";
          result.facts.integrity=status; result.facts.integrityError=failure;
        }
      }
    } finally { bool ok=api.Close(token,out error); result.facts.close=ok?"closed":"error"; result.facts.closeError=error; }
    return result;
  }
  private static AceFacts[] ParseAcl(NativeApi api,IntPtr descriptor,uint descriptorLength,IntPtr acl,uint information,byte[] ownedUser,Identity current,Identity child,out bool complete,out int? nativeError) {
    complete=false; nativeError=null;
    long relative=acl.ToInt64()-descriptor.ToInt64();
    if(relative<20 || relative>(long)descriptorLength-8) throw new InvalidOperationException("ACL outside descriptor allocation");
    uint count,used,free; int error;
    if(!api.AclSize(acl,out count,out used,out free,out error)) { nativeError=error; return new AceFacts[0]; }
    if(count>64 || used<8 || (ulong)used+free>65535 || (ulong)used+free>(ulong)(descriptorLength-relative)) throw new InvalidOperationException("ACL size-information bound");
    List<AceFacts> result=new List<AceFacts>(); long previousEnd=8; complete=true;
    for(uint i=0;i<count;i++) {
      IntPtr pointer;
      if(!api.Ace(acl,i,out pointer,out error)) { nativeError=error; complete=false; return new AceFacts[0]; }
      long offset=pointer.ToInt64()-acl.ToInt64();
      if(offset<previousEnd || offset>(long)used-4 || (pointer.ToInt64()&3)!=0) throw new InvalidOperationException("ACE pointer bound");
      int length=unchecked((ushort)Marshal.ReadInt16(pointer,2));
      if(length<4 || (length&3)!=0 || length>used-offset) throw new InvalidOperationException("ACE size bound");
      byte[] bytes=new byte[length]; Marshal.Copy(pointer,bytes,0,length);
      AceFacts fact=new AceFacts {type=bytes[0],flags=bytes[1],size=(ushort)length};
      bool known=information==4?(fact.type==0 || fact.type==1):fact.type==0x11;
      if(known) {
        if(length<16) throw new InvalidOperationException("Known ACE truncated");
        fact.mask=BitConverter.ToUInt32(bytes,4); byte[] sid=Sid(bytes,8,length);
        if(sid.Length!=length-8) throw new InvalidOperationException("Known ACE trailing bytes");
        fact.classification=Classification(sid);
        if(information==4) {
          fact.ownedUserMatches=Matches(sid,ownedUser);
          fact.currentPrimaryMatches=Matches(sid,current.user); fact.childPrimaryMatches=Matches(sid,child.user);
          fact.currentLogonMatches=Matches(sid,current.logon); fact.childLogonMatches=Matches(sid,child.logon);
        } else if(fact.classification=="MandatoryIntegrity") fact.integrityRid=BitConverter.ToUInt32(sid,8);
        else complete=false;
      } else complete=false;
      result.Add(fact); previousEnd=offset+length;
    }
    return result.ToArray();
  }
  private static void ReadQuery(NativeApi api,IntPtr handle,QueryFacts facts,byte[] ownedUser,Identity current,Identity child) {
    IntPtr descriptor=IntPtr.Zero; uint result=api.Query(handle,facts.information,out descriptor); facts.nativeError=result;
    // Only documented successful output establishes allocation ownership.
    if(result!=0) { facts.status="error"; return; }
    if(descriptor==IntPtr.Zero) { facts.status="missing-descriptor"; return; }
    facts.status="available";
    try {
      bool present,defaulted; IntPtr acl; int error;
      uint length; string descriptorStatus=api.DescriptorSize(descriptor,out length,out error);
      if(descriptorStatus!="available") { facts.parse=descriptorStatus; facts.getterError=error; return; }
      if(!api.Acl(descriptor,facts.information,out present,out acl,out defaulted,out error)) { facts.parse="getter-error"; facts.getterError=error; return; }
      facts.getterError=0; facts.present=present;
      // Absent getter outputs are undefined, so do not inspect either of them.
      if(!present) { facts.parse="absent"; facts.complete=true; return; }
      facts.defaulted=defaulted; facts.isNull=acl==IntPtr.Zero;
      if(acl==IntPtr.Zero) { facts.parse="null"; facts.complete=true; return; }
      try { bool complete; int? parseError; facts.aces=ParseAcl(api,descriptor,length,acl,facts.information,ownedUser,current,child,out complete,out parseError); facts.complete=complete; facts.parse=parseError!=null?"acl-api-error":complete?(facts.aces.Length==0?"empty":"complete"):"unknown-ace"; if(parseError!=null) facts.getterError=parseError; }
      catch(InvalidOperationException) { facts.parse="bounds-contract"; }
    } finally { int error; bool ok=api.Free(descriptor,out error); facts.free=ok?"freed":"error"; facts.freeError=error; }
  }
  public static Observation ObserveRetained(NativeApi api,int pid,Dictionary<int,IntPtr> retained,byte[] ownedUserSid) {
    // Actual fixture integration must pass its existing StartedProcesses map.
    // Never acquire, reopen, duplicate, close, or impersonate a process handle.
    IntPtr borrowed;
    if(pid<=0 || retained==null || !retained.TryGetValue(pid,out borrowed) || borrowed==IntPtr.Zero) throw new InvalidOperationException("Child process is not retained");
    // The existing fixture owns the QA account and supplies its locally held
    // binary SID. Validate/copy that input; neither SID nor account name is output.
    if(ownedUserSid==null || ownedUserSid.Length>68) throw new InvalidOperationException("Owned QA identity absent");
    byte[] ownedUser=Sid(ownedUserSid,0,ownedUserSid.Length);
    if(ownedUser.Length!=ownedUserSid.Length) throw new InvalidOperationException("Owned QA identity length");
    if(IntPtr.Size!=8 || Marshal.SizeOf(typeof(UnicodeString))!=16 || Marshal.SizeOf(typeof(ObjectAttributes))!=48 || Marshal.OffsetOf(typeof(ObjectAttributes),"ObjectName").ToInt32()!=16 || Marshal.OffsetOf(typeof(ObjectAttributes),"Attributes").ToInt32()!=24) throw new InvalidOperationException("Exact x64 ABI required");
    int error; uint actual=api.ProcessId(borrowed,out error); if(actual!=(uint)pid) throw new InvalidOperationException("Retained child PID changed");
    uint session; if(!api.Session(actual,out session,out error)) throw new System.ComponentModel.Win32Exception(error,"Retained child session query");
    Observation facts=new Observation {pid=pid,childSession=session};
    Identity current=ReadIdentity(api,api.CurrentProcess()),child=ReadIdentity(api,borrowed); facts.current=current.facts; facts.child=child.facts;
    facts.directories=new DirectoryFacts[] {new DirectoryFacts {pathKind="global"},new DirectoryFacts {pathKind="child-session"}};
    try {
      facts.load=api.Load(out error); facts.loadError=error;
      if(facts.load!="available") return facts;
      for(int index=0;index<2;index++) {
        DirectoryFacts row=facts.directories[index]; string path=index==0?"\\BaseNamedObjects":"\\Sessions\\"+session.ToString(System.Globalization.CultureInfo.InvariantCulture)+"\\BaseNamedObjects";
        IntPtr text=Marshal.StringToHGlobalUni(path),name=IntPtr.Zero,handle=IntPtr.Zero;
        try {
          int bytes=Encoding.Unicode.GetByteCount(path); if(bytes>65532) throw new InvalidOperationException("Unicode length bound");
          UnicodeString value=new UnicodeString {Length=(ushort)bytes,MaximumLength=(ushort)(bytes+2),Buffer=text};
          name=Marshal.AllocHGlobal(16); Marshal.StructureToPtr(value,name,false);
          ObjectAttributes attributes=new ObjectAttributes {Length=48,RootDirectory=IntPtr.Zero,ObjectName=name,Attributes=64,SecurityDescriptor=IntPtr.Zero,SecurityQualityOfService=IntPtr.Zero};
          int status=api.OpenDirectory(out handle,0x20000,ref attributes); row.ntStatus=status;
          if(status!=0) { row.open="nt-error"; handle=IntPtr.Zero; continue; }
          if(handle==IntPtr.Zero || handle==borrowed || handle==api.CurrentProcess()) { row.open="invalid-output"; handle=IntPtr.Zero; continue; }
          row.open="opened";
          ReadQuery(api,handle,row.dacl,ownedUser,current,child); ReadQuery(api,handle,row.label,ownedUser,current,child);
        } finally {
          try {
            if(handle!=IntPtr.Zero) { bool ok=api.Close(handle,out error); row.close=ok?"closed":"error"; row.closeError=error; }
          } finally {
            if(name!=IntPtr.Zero) Marshal.FreeHGlobal(name); Marshal.FreeHGlobal(text);
          }
        }
      }
      return facts;
    } finally { facts.unload=api.Unload(out error); facts.unloadError=error; }
  }
}
