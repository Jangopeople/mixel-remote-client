// Read-only isolated CI diagnosis. Never changes a token, DACL or object.
namespace MixelCI {
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
public sealed class NamespaceRestoreException : Win32Exception { public NamespaceRestoreException(int error) : base(error) {} }
public static class MixelStandardNamespaceDiagnostics {
  [StructLayout(LayoutKind.Sequential)] struct UnicodeString { public ushort length, maximum; public IntPtr buffer; }
  [StructLayout(LayoutKind.Sequential)] struct ObjectAttributes { public uint length; public IntPtr root, name; public uint attributes; public IntPtr security, qos; }
  [StructLayout(LayoutKind.Sequential)] struct SidAndAttributes { public IntPtr sid; public uint attributes; }
  [StructLayout(LayoutKind.Sequential)] struct TokenGroupsPrefix { public uint count; public SidAndAttributes first; }
  [StructLayout(LayoutKind.Sequential)] struct Luid { public uint low; public int high; }
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr data, int size, out int required);
  [DllImport("advapi32.dll")] static extern bool IsTokenRestricted(IntPtr token);
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool LookupPrivilegeValueW(string system, string name, out Luid value);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool ImpersonateLoggedOnUser(IntPtr token);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool RevertToSelf();
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("ntdll.dll")] static extern int NtOpenDirectoryObject(out IntPtr directory, uint access, ref ObjectAttributes attributes);
  [DllImport("ntdll.dll")] static extern uint RtlNtStatusToDosError(int status);
  static IntPtr Information(IntPtr token, int kind, out int size) {
    GetTokenInformation(token,kind,IntPtr.Zero,0,out size);
    if(size<4 || size>65536) throw new InvalidOperationException("Invalid bounded token metadata size");
    IntPtr data=Marshal.AllocHGlobal(size); int actual;
    if(!GetTokenInformation(token,kind,data,size,out actual)) { int error=Marshal.GetLastWin32Error(); Marshal.FreeHGlobal(data); throw new Win32Exception(error); }
    if(actual<4 || actual>size) { Marshal.FreeHGlobal(data); throw new InvalidOperationException("Invalid actual token metadata size"); }
    size=actual; return data;
  }
  static uint Scalar(IntPtr token, int kind) {
    int size; IntPtr data=Information(token,kind,out size);
    try { if(size!=4) throw new InvalidOperationException("Invalid token scalar size"); return unchecked((uint)Marshal.ReadInt32(data)); }
    finally { Marshal.FreeHGlobal(data); }
  }
  static uint Directory(string path, uint access) {
    if(path.Length>128) throw new InvalidOperationException("Unbounded directory path");
    IntPtr text=Marshal.StringToHGlobalUni(path), name=IntPtr.Zero, handle=IntPtr.Zero;
    try {
      var value=new UnicodeString {length=checked((ushort)(path.Length*2)),maximum=checked((ushort)((path.Length+1)*2)),buffer=text};
      name=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(UnicodeString))); Marshal.StructureToPtr(value,name,false);
      var attributes=new ObjectAttributes {length=(uint)Marshal.SizeOf(typeof(ObjectAttributes)),root=IntPtr.Zero,name=name,attributes=0x40,security=IntPtr.Zero,qos=IntPtr.Zero};
      int status=NtOpenDirectoryObject(out handle,access,ref attributes);
      if(status<0) return RtlNtStatusToDosError(status);
      if(handle==IntPtr.Zero) throw new InvalidOperationException("Successful directory open returned no handle");
      return 0;
    } finally {
      int closeError=0;
      if(handle!=IntPtr.Zero && !CloseHandle(handle)) closeError=Marshal.GetLastWin32Error();
      if(name!=IntPtr.Zero) Marshal.FreeHGlobal(name); Marshal.FreeHGlobal(text);
      if(closeError!=0) throw new Win32Exception(closeError);
    }
  }
  public static Dictionary<string,string> Observe(IntPtr process) {
    IntPtr token=IntPtr.Zero, impersonationToken=IntPtr.Zero; bool impersonating=false, revertFailed=false; int revertError=0;
    if(!OpenProcessToken(process,8,out token)) throw new Win32Exception();
    try {
      var result=new Dictionary<string,string>();
      result.Add("restricted",IsTokenRestricted(token).ToString());
      result.Add("appContainer",(Scalar(token,29)!=0).ToString());
      uint session=Scalar(token,12); result.Add("session",session.ToString());
      int size; IntPtr data=Information(token,25,out size);
      try {
        if(size<Marshal.SizeOf(typeof(SidAndAttributes))) throw new InvalidOperationException("Invalid token integrity metadata");
        var sid=new SecurityIdentifier(Marshal.ReadIntPtr(data)); var parts=sid.Value.Split('-');
        uint rid; if(parts.Length!=4 || parts[0]!="S" || parts[1]!="1" || parts[2]!="16" || !uint.TryParse(parts[3],out rid)) throw new InvalidOperationException("Invalid token integrity label");
        result.Add("integrityRid",rid.ToString());
      } finally { Marshal.FreeHGlobal(data); }
      bool users=false,authenticated=false,everyone=false,interactive=false,logon=false;
      data=Information(token,2,out size);
      try {
        int count=Marshal.ReadInt32(data),offset=(int)Marshal.OffsetOf(typeof(TokenGroupsPrefix),"first"),stride=Marshal.SizeOf(typeof(SidAndAttributes));
        if(count<0 || count>1024 || (long)offset+(long)count*stride>size) throw new InvalidOperationException("Invalid bounded token groups");
        for(int index=0;index<count;index++) {
          var entry=(SidAndAttributes)Marshal.PtrToStructure(IntPtr.Add(data,offset+index*stride),typeof(SidAndAttributes));
          bool enabled=(entry.attributes&4)!=0 && (entry.attributes&16)==0;
          if(!enabled) continue;
          var sid=new SecurityIdentifier(entry.sid);
          users|=sid.IsWellKnown(WellKnownSidType.BuiltinUsersSid); authenticated|=sid.IsWellKnown(WellKnownSidType.AuthenticatedUserSid);
          everyone|=sid.IsWellKnown(WellKnownSidType.WorldSid); interactive|=sid.IsWellKnown(WellKnownSidType.InteractiveSid);
          logon|=(entry.attributes&0xc0000000)==0xc0000000;
        }
      } finally { Marshal.FreeHGlobal(data); }
      result.Add("usersEnabled",users.ToString()); result.Add("authenticatedEnabled",authenticated.ToString()); result.Add("everyoneEnabled",everyone.ToString()); result.Add("interactiveEnabled",interactive.ToString()); result.Add("logonSidEnabled",logon.ToString());
      Luid changeNotify; if(!LookupPrivilegeValueW(null,"SeChangeNotifyPrivilege",out changeNotify)) throw new Win32Exception();
      bool notifyEnabled=false; data=Information(token,3,out size);
      try {
        int count=Marshal.ReadInt32(data); if(count<0 || count>1024 || 4L+count*12L>size) throw new InvalidOperationException("Invalid bounded token privileges");
        for(int index=0;index<count;index++) { int offset=4+index*12; if(unchecked((uint)Marshal.ReadInt32(data,offset))==changeNotify.low && Marshal.ReadInt32(data,offset+4)==changeNotify.high) notifyEnabled=(Marshal.ReadInt32(data,offset+8)&2)!=0; }
      } finally { Marshal.FreeHGlobal(data); }
      result.Add("changeNotifyEnabled",notifyEnabled.ToString());
      if(!OpenProcessToken(process,10,out impersonationToken)) { result.Add("impersonationTokenOpenError",Marshal.GetLastWin32Error().ToString()); return result; }
      if(!ImpersonateLoggedOnUser(impersonationToken)) { result.Add("impersonationError",Marshal.GetLastWin32Error().ToString()); return result; } impersonating=true;
      foreach(var entry in new [] {new KeyValuePair<string,string>("root",@"\"),new KeyValuePair<string,string>("sessions",@"\Sessions"),new KeyValuePair<string,string>("session",@"\Sessions\"+session),new KeyValuePair<string,string>("sessionObjects",@"\Sessions\"+session+@"\BaseNamedObjects"),new KeyValuePair<string,string>("globalObjects",@"\BaseNamedObjects")}) {
        result.Add(entry.Key+"TraverseError",Directory(entry.Value,2).ToString());
        result.Add(entry.Key+"QueryError",Directory(entry.Value,1).ToString());
        result.Add(entry.Key+"CreateAccessError",Directory(entry.Value,4).ToString());
      }
      return result;
    } finally {
      if(impersonating && !RevertToSelf()) { revertFailed=true; revertError=Marshal.GetLastWin32Error(); }
      int closeError=0;
      if(impersonationToken!=IntPtr.Zero && !CloseHandle(impersonationToken)) closeError=Marshal.GetLastWin32Error();
      if(token!=IntPtr.Zero && !CloseHandle(token) && closeError==0) closeError=Marshal.GetLastWin32Error();
      if(revertFailed) Environment.FailFast("Owned namespace observation could not restore the runner token",new NamespaceRestoreException(revertError));
      if(closeError!=0) throw new Win32Exception(closeError);
    }
  }
}
}
