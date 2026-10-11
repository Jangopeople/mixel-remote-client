// Isolated Windows CI fixture only: enable an owned disposable account to use
// the existing interactive session namespace, then restore its exact DACL.
// No consent event is created and no product authorization/token is changed.
namespace MixelCI {
using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
public sealed class StandardNamespaceAccess : IDisposable {
  [StructLayout(LayoutKind.Sequential)] struct UnicodeString { public ushort length, maximum; public IntPtr buffer; }
  [StructLayout(LayoutKind.Sequential)] struct ObjectAttributes { public uint length; public IntPtr root, name; public uint attributes; public IntPtr security, qos; }
  [DllImport("kernel32.dll")] static extern uint GetCurrentProcessId();
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool ProcessIdToSessionId(uint pid, out uint session);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("ntdll.dll")] static extern int NtOpenDirectoryObject(out IntPtr directory, uint access, ref ObjectAttributes attributes);
  [DllImport("ntdll.dll")] static extern uint RtlNtStatusToDosError(int status);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetKernelObjectSecurity(IntPtr handle, uint information, byte[] descriptor, uint length, out uint needed);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool SetKernelObjectSecurity(IntPtr handle, uint information, byte[] descriptor);
  IntPtr handle;
  byte[] original, grantedAcl;
  static byte[] Descriptor(IntPtr obj) {
    uint required;
    bool first=GetKernelObjectSecurity(obj,4,null,0,out required);
    int error=first ? 0 : Marshal.GetLastWin32Error();
    if(first || error!=122 || required<20 || required>65536) throw new InvalidOperationException("Invalid bounded namespace security descriptor");
    byte[] data=new byte[required]; uint actual;
    if(!GetKernelObjectSecurity(obj,4,data,required,out actual)) { error=Marshal.GetLastWin32Error(); throw new Win32Exception(error); }
    if(actual<20 || actual>required) throw new InvalidOperationException("Invalid actual namespace security descriptor size");
    return data;
  }
  static byte[] Acl(byte[] bytes) {
    if(bytes==null || bytes.Length<20 || bytes.Length>65536 || bytes[0]!=1) throw new InvalidOperationException("Invalid bounded native security descriptor");
    ushort nativeFlags=BitConverter.ToUInt16(bytes,2);
    uint offset=BitConverter.ToUInt32(bytes,16);
    if((nativeFlags&0x8004)!=0x8004 || offset<20 || offset>(uint)bytes.Length-8) throw new InvalidOperationException("Namespace must retain an explicit self-relative existing DACL");
    int position=checked((int)offset);
    if(bytes[position]!=2 && bytes[position]!=4) throw new InvalidOperationException("Invalid native ACL revision");
    int length=BitConverter.ToUInt16(bytes,position+2);
    if(length<8 || length>bytes.Length-position) throw new InvalidOperationException("Invalid bounded native DACL size");
    var descriptor=new RawSecurityDescriptor(bytes,0);
    if(descriptor.DiscretionaryAcl==null) throw new InvalidOperationException("Namespace must retain an explicit existing DACL");
    // Compare the actual native ACL (including its unused padding) rather than
    // reserializing RawAcl, which can discard unused native ACL bytes.
    ushort flags=(ushort)(nativeFlags&0x150c); // present/defaulted/auto-inherit/protected
    byte[] result=new byte[length+2]; result[0]=(byte)flags; result[1]=(byte)(flags>>8);
    Buffer.BlockCopy(bytes,position,result,2,length); return result;
  }
  static bool Equal(byte[] left,byte[] right) {
    if(left==null || right==null || left.Length!=right.Length) return false;
    int difference=0; for(int index=0;index<left.Length;index++) difference|=left[index]^right[index]; return difference==0;
  }
  static byte[] WithCreateAccess(byte[] bytes,SecurityIdentifier sid) {
    var descriptor=new RawSecurityDescriptor(bytes,0);
    // This validates the original DACL and preserves every original ACE.
    Acl(bytes);
    int position=descriptor.DiscretionaryAcl.Count;
    for(int index=0;index<descriptor.DiscretionaryAcl.Count;index++) {
      var existing=descriptor.DiscretionaryAcl[index];
      var qualified=existing as QualifiedAce;
      if(qualified!=null && qualified.SecurityIdentifier.Equals(sid)) throw new InvalidOperationException("Disposable SID already has a namespace ACE");
      if((existing.AceFlags&AceFlags.Inherited)!=0 && position==descriptor.DiscretionaryAcl.Count) position=index;
    }
    // Win32 namespace bootstrap requests query|traverse|create-object|create-subdirectory (15).
    // Existing query/traverse (3) are unchanged; add only object/subdirectory (4|8).
    descriptor.DiscretionaryAcl.InsertAce(position,new CommonAce(AceFlags.None,AceQualifier.AccessAllowed,12,sid,false,null));
    byte[] result=new byte[descriptor.BinaryLength]; descriptor.GetBinaryForm(result,0); return result;
  }
  static void Apply(IntPtr obj,byte[] descriptor) {
    if(!SetKernelObjectSecurity(obj,4,descriptor)) { int error=Marshal.GetLastWin32Error(); throw new Win32Exception(error); }
  }
  static IntPtr Open(uint session) {
    string path=@"\Sessions\"+session+@"\BaseNamedObjects";
    IntPtr text=Marshal.StringToHGlobalUni(path),name=IntPtr.Zero,obj=IntPtr.Zero; Exception failure=null;
    try {
      var unicode=new UnicodeString {length=checked((ushort)(path.Length*2)),maximum=checked((ushort)((path.Length+1)*2)),buffer=text};
      name=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UnicodeString))); Marshal.StructureToPtr(unicode,name,false);
      var attributes=new ObjectAttributes {length=checked((uint)Marshal.SizeOf(typeof(ObjectAttributes))),name=name,attributes=0x40};
      // Only READ_CONTROL + WRITE_DAC, query and traverse on this directory.
      int status=NtOpenDirectoryObject(out obj,0x00060003,ref attributes);
      if(status<0) throw new Win32Exception(unchecked((int)RtlNtStatusToDosError(status)));
      if(obj==IntPtr.Zero) throw new InvalidOperationException("Successful session namespace open returned no handle");
      IntPtr result=obj; obj=IntPtr.Zero; return result;
    } catch(Exception error) { failure=error; throw; } finally {
      int closeError=0; bool closeFailed=false;
      if(obj!=IntPtr.Zero && !CloseHandle(obj)) { closeFailed=true; closeError=Marshal.GetLastWin32Error(); }
      if(name!=IntPtr.Zero) Marshal.FreeHGlobal(name); Marshal.FreeHGlobal(text);
      if(closeFailed) {
        var closingFailure=new Win32Exception(closeError);
        if(failure!=null) throw new AggregateException("Namespace open and owned handle cleanup failed",failure,closingFailure);
        throw closingFailure;
      }
    }
  }
  public StandardNamespaceAccess(string ownedSid,string ownedUser,uint expectedSession) {
    if(Environment.GetEnvironmentVariable("GITHUB_ACTIONS")!="true" || Environment.OSVersion.Platform!=PlatformID.Win32NT) throw new InvalidOperationException("Namespace access preparation is restricted to isolated Windows Actions");
    var sid=new SecurityIdentifier(ownedSid);
    if(!sid.IsAccountSid() || sid.AccountDomainSid==null || !sid.Value.StartsWith("S-1-5-21-",StringComparison.Ordinal) || sid.Value!=ownedSid) throw new InvalidOperationException("Namespace access requires a canonical owned local account SID");
    if(ownedUser==null || !System.Text.RegularExpressions.Regex.IsMatch(ownedUser,@"\Amixel(?:qs|lease)[0-9a-f]{10}\z")) throw new InvalidOperationException("Namespace access requires the disposable fixture account name");
    var account=(NTAccount)sid.Translate(typeof(NTAccount));
    if(!string.Equals(account.Value,Environment.MachineName+"\\"+ownedUser,StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("Namespace SID does not identify the exact owned local fixture account");
    using(var caller=WindowsIdentity.GetCurrent()) {
      if(caller.User==null || caller.User.Equals(sid)) throw new InvalidOperationException("Namespace account must differ from the actual runner identity");
    }
    uint actual;
    if(!ProcessIdToSessionId(GetCurrentProcessId(),out actual)) throw new Win32Exception(Marshal.GetLastWin32Error());
    if(actual==0 || actual!=expectedSession) throw new InvalidOperationException("Namespace access requires the actual caller interactive session");
    handle=Open(actual);
    bool applied=false;
    try {
      original=Descriptor(handle); byte[] updated=WithCreateAccess(original,sid); grantedAcl=Acl(updated);
      if(!Equal(Acl(Descriptor(handle)),Acl(original))) throw new InvalidOperationException("Namespace DACL changed before the owned grant; refusing to overwrite it");
      Apply(handle,updated); applied=true;
      if(!Equal(Acl(Descriptor(handle)),grantedAcl)) throw new InvalidOperationException("Namespace object/subdirectory grant changed unexpected DACL bytes");
    } catch(Exception primary) {
      var failures=new List<Exception> {primary};
      if(applied) {
        try {
          if(!Equal(Acl(Descriptor(handle)),grantedAcl)) throw new InvalidOperationException("Namespace DACL changed outside the constructor owned grant; refusing to overwrite it");
          Apply(handle,original);
          if(!Equal(Acl(Descriptor(handle)),Acl(original))) throw new InvalidOperationException("Namespace constructor did not restore its exact original DACL");
        } catch(Exception restoreFailure) { failures.Add(restoreFailure); }
      }
      var closingFailure=CloseOwned(); if(closingFailure!=null) failures.Add(closingFailure);
      if(failures.Count>1) throw new AggregateException("Namespace preparation and owned cleanup failed",failures);
      throw;
    }
  }
  Exception CloseOwned() {
    IntPtr closing=handle; handle=IntPtr.Zero; original=null; grantedAcl=null;
    if(closing!=IntPtr.Zero && !CloseHandle(closing)) { int error=Marshal.GetLastWin32Error(); return new Win32Exception(error); }
    return null;
  }

  public void Dispose() {
    if(handle==IntPtr.Zero) return;
    Exception failure=null;
    try {
      if(!Equal(Acl(Descriptor(handle)),grantedAcl)) throw new InvalidOperationException("Namespace DACL changed outside the owned grant; refusing to overwrite it");
      Apply(handle,original);
      if(!Equal(Acl(Descriptor(handle)),Acl(original))) throw new InvalidOperationException("Namespace original DACL restoration was not exact");
    } catch(Exception error) { failure=error; }
    var closingFailure=CloseOwned();
    if(failure!=null && closingFailure!=null) throw new AggregateException("Namespace restoration and owned handle cleanup failed",failure,closingFailure);
    if(failure!=null) ExceptionDispatchInfo.Capture(failure).Throw();
    if(closingFailure!=null) throw closingFailure;
  }
}
}
