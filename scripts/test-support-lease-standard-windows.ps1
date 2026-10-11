# Normal native functional controls under the exact customer fixture's ordinary
# token. No debugger, registry observation, product event, or customer payload.
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') {
  throw 'Disposable standard-account lease qualification requires an isolated Windows Actions runner.'
}
$launchSource = Get-Content (Join-Path $PSScriptRoot 'test-support-launch-windows.ps1') -Raw
$class = $launchSource.IndexOf('public static class MixelOrdinaryTokenFixture {')
$first = $launchSource.LastIndexOf('using System;', $class)
$last = $launchSource.IndexOf("`n'@ }", $class)
if ($class -lt 0 -or $first -lt 0 -or $last -le $first) { throw 'Exact normal ordinary-token fixture is missing.' }
if (-not ('MixelOrdinaryTokenFixture' -as [type])) {
  $normalSource = $launchSource.Substring($first, $last - $first)
  $marker = '  public static int StartStandardUser(string executable, string username, string password) {'
  $position = $normalSource.IndexOf($marker)
  if ($position -lt 0 -or $position -ne $normalSource.LastIndexOf($marker)) { throw 'Exact normal standard-launch comparison boundary changed.' }
  # Add only a documented secondary-launch comparison and retained-token SID
  # check to the test class; preserve its original customer launch and cleanup.
  $comparison = @'
  // Exact public TOKEN_INFORMATION_CLASS values from Microsoft's winnt.h.
  // These queries observe the existing token; they never assign privileges,
  // token sessions, integrity levels, memberships or namespace permissions.
  enum ObservedTokenClass { Groups=2, Type=8, SessionId=12, ElevationType=18, LinkedToken=19, HasRestrictions=21, IntegrityLevel=25, LogonSid=28, IsAppContainer=29 }
  [StructLayout(LayoutKind.Sequential)] struct ObservedSidAttributes { public IntPtr sid; public uint attributes; }
  [StructLayout(LayoutKind.Sequential)] struct ObservedGroupsHeader { public uint count; public ObservedSidAttributes first; }
  public sealed class ObservedTokenFacts {
    public uint sessionId, integrityRid;
    public bool sessionKnown=true;
    public bool hasRestrictions, isAppContainer;
    public uint hasRestrictionsReturnedLength, isAppContainerReturnedLength;
    public string authenticatedUsers, interactive, users;
    public string logonComparison="tokenLogonSidCount=unavailable;tokenLogonSidMatchesCaller=unavailable;tokenLogonSidQuerySubject=target;tokenLogonSidQueryFailure=not-measured;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none";
    public string unavailableKind, unavailableFailure;
    public int? unavailableError, unavailableLength;
    public override string ToString() {
      if(unavailableKind!=null) return "tokenSessionId=" + (sessionKnown ? sessionId.ToString(System.Globalization.CultureInfo.InvariantCulture) : "unavailable") +
        ";tokenFacts=unavailable;tokenQueryKind=" + unavailableKind + ";tokenQueryFailure=" + unavailableFailure +
        ";tokenQueryError=" + (unavailableError.HasValue ? unavailableError.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) : "none") +
        ";tokenQueryReturnedLength=" + (unavailableLength.HasValue ? unavailableLength.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) : "none") + ";" + logonComparison;
      return "tokenSessionId=" + sessionId.ToString(System.Globalization.CultureInfo.InvariantCulture) +
        ";hasRestrictions=" + (hasRestrictions ? "true" : "false") +
        ";hasRestrictionsReturnedLength=" + hasRestrictionsReturnedLength.ToString(System.Globalization.CultureInfo.InvariantCulture) +
        ";isAppContainer=" + (isAppContainer ? "true" : "false") +
        ";isAppContainerReturnedLength=" + isAppContainerReturnedLength.ToString(System.Globalization.CultureInfo.InvariantCulture) +
        ";integrityRid=" + integrityRid.ToString(System.Globalization.CultureInfo.InvariantCulture) +
        ";authenticatedUsers=" + authenticatedUsers + ";interactive=" + interactive + ";users=" + users + ";" + logonComparison;
    }
  }
  sealed class ObservedTokenQueryFailure : InvalidOperationException {
    public ObservedTokenClass kind;
    public string failure;
    public int? error, length;
    public ObservedTokenQueryFailure(ObservedTokenClass kind, string failure, int? error, int? length) {
      this.kind=kind; this.failure=failure; this.error=error; this.length=length;
    }
  }
  static uint ObservedTokenDword(IntPtr token, ObservedTokenClass kind) {
    IntPtr data=Marshal.AllocHGlobal(4);
    try {
      int required;
      bool success=GetTokenInformation(token,(int)kind,data,4,out required);
      int error=Marshal.GetLastWin32Error();
      if (!success) throw new ObservedTokenQueryFailure(kind,"api-error",error,required);
      if (required!=4) throw new ObservedTokenQueryFailure(kind,"returned-size",null,required);
      return unchecked((uint)Marshal.ReadInt32(data));
    } finally { Marshal.FreeHGlobal(data); }
  }
  static bool ObservedTokenBoolValue(IntPtr data, int required, ObservedTokenClass kind) {
    // Actual GetTokenInformation class21 returned a successful one-byte value in a99. Read only
    // that byte for this measured class; all other classes retain exact DWORD.
    if(kind==ObservedTokenClass.HasRestrictions && required==1) return Marshal.ReadByte(data)!=0;
    if(required==4) return Marshal.ReadInt32(data)!=0;
    throw new ObservedTokenQueryFailure(kind,"returned-size",null,required);
  }
  static bool ObservedTokenBool(IntPtr token, ObservedTokenClass kind, out uint returnedLength) {
    IntPtr data=Marshal.AllocHGlobal(4);
    try {
      int required;
      bool success=GetTokenInformation(token,(int)kind,data,4,out required);
      int error=Marshal.GetLastWin32Error();
      if(!success) throw new ObservedTokenQueryFailure(kind,"api-error",error,required);
      bool value=ObservedTokenBoolValue(data,required,kind);
      returnedLength=checked((uint)required);
      return value;
    } finally { Marshal.FreeHGlobal(data); }
  }
  static IntPtr ObservedTokenBuffer(IntPtr token, ObservedTokenClass kind, out int length) {
    bool initial=GetTokenInformation(token,(int)kind,IntPtr.Zero,0,out length);
    int error=Marshal.GetLastWin32Error();
    if(initial || error!=122 || length<=0 || length>65536) throw new ObservedTokenQueryFailure(kind,"size-contract",initial ? (int?)null : error,length);
    int allocated=length; IntPtr data=Marshal.AllocHGlobal(allocated);
    try {
      bool success=GetTokenInformation(token,(int)kind,data,allocated,out length);
      int outputError=Marshal.GetLastWin32Error();
      if(!success) throw new ObservedTokenQueryFailure(kind,"api-error",outputError,length);
      if(length<=0 || length>allocated) throw new ObservedTokenQueryFailure(kind,"size-contract",null,length);
      return data;
    } catch { Marshal.FreeHGlobal(data); throw; }
  }
  static string ObservedBufferedSid(IntPtr data, int length, IntPtr sid) {
    long offset=sid.ToInt64()-data.ToInt64();
    if(offset<0 || offset>length-8 || Marshal.ReadByte(sid)!=1) throw new InvalidOperationException("Read-only token SID header bounds failed");
    int subAuthorities=Marshal.ReadByte(sid,1);
    if(subAuthorities>15 || 8+subAuthorities*4>length-offset) throw new InvalidOperationException("Read-only token SID content bounds failed");
    return new SecurityIdentifier(sid).Value;
  }
  static uint ObservedIntegrityRid(IntPtr token) {
    int length; IntPtr data=ObservedTokenBuffer(token,ObservedTokenClass.IntegrityLevel,out length);
    try {
      if(length<Marshal.SizeOf(typeof(ObservedSidAttributes))) throw new InvalidOperationException("Read-only token integrity label bounds failed");
      var label=(ObservedSidAttributes)Marshal.PtrToStructure(data,typeof(ObservedSidAttributes));
      string sid=ObservedBufferedSid(data,length,label.sid);
      if((label.attributes & 0x20)==0 || !System.Text.RegularExpressions.Regex.IsMatch(sid,@"\AS-1-16-(?:0|[1-9][0-9]{0,9})\z")) throw new InvalidOperationException("Read-only token integrity SID is not canonical");
      uint rid;
      if(!uint.TryParse(sid.Substring(7),System.Globalization.NumberStyles.None,System.Globalization.CultureInfo.InvariantCulture,out rid)) throw new InvalidOperationException("Read-only token integrity RID is out of range");
      return rid;
    } finally { Marshal.FreeHGlobal(data); }
  }
  static void ObservedMemberships(IntPtr token, ObservedTokenFacts facts) {
    facts.authenticatedUsers="absent"; facts.interactive="absent"; facts.users="absent";
    int length; IntPtr data=ObservedTokenBuffer(token,ObservedTokenClass.Groups,out length);
    try {
      int offset=Marshal.OffsetOf(typeof(ObservedGroupsHeader),"first").ToInt32();
      int itemSize=Marshal.SizeOf(typeof(ObservedSidAttributes));
      if(length<offset) throw new InvalidOperationException("Read-only token group header bounds failed");
      uint count=unchecked((uint)Marshal.ReadInt32(data));
      if(count>4096 || count>(uint)((length-offset)/itemSize)) throw new InvalidOperationException("Read-only token group count bounds failed");
      var seen=new System.Collections.Generic.HashSet<string>();
      for(uint i=0;i<count;i++) {
        var group=(ObservedSidAttributes)Marshal.PtrToStructure(IntPtr.Add(data,offset+checked((int)i)*itemSize),typeof(ObservedSidAttributes));
        string sid=ObservedBufferedSid(data,length,group.sid);
        if(sid!="S-1-5-11" && sid!="S-1-5-4" && sid!="S-1-5-32-545") continue;
        if(!seen.Add(sid) || (group.attributes & 0x14)==0x14) throw new InvalidOperationException("Read-only observed token membership is inconsistent");
        string state=(group.attributes & 0x10)!=0 ? "deny-only" : (group.attributes & 4)!=0 ? "enabled" : "disabled";
        if(sid=="S-1-5-11") facts.authenticatedUsers=state;
        else if(sid=="S-1-5-4") facts.interactive=state;
        else facts.users=state;
      }
    } finally { Marshal.FreeHGlobal(data); }
  }
  // Only the current caller's existing linked token is used; no token/session/
  // privilege setter or token from an unrelated process is opened.
  sealed class CallerLinkedTokenQueryFailure : Win32Exception {
    public CallerLinkedTokenQueryFailure(int error) : base(error,"Current caller linked-token query failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture)) {}
  }
  static IntPtr ReadCallerLinkedToken(IntPtr caller) {
    if(IntPtr.Size!=8) throw new InvalidOperationException("Caller-linked control requires x64 pointer size8");
    IntPtr data=Marshal.AllocHGlobal(8), linked=IntPtr.Zero;
    try {
      Marshal.WriteIntPtr(data,IntPtr.Zero);
      int required;
      bool success=GetTokenInformation(caller,(int)ObservedTokenClass.LinkedToken,data,8,out required);
      int error=Marshal.GetLastWin32Error();
      if(!success) throw new CallerLinkedTokenQueryFailure(error);
      if(required!=8) throw new InvalidOperationException("Current caller linked-token success length contract failed");
      linked=Marshal.ReadIntPtr(data);
      if(linked==IntPtr.Zero) throw new InvalidOperationException("Current caller linked-token success pointer contract failed");
      IntPtr result=linked; linked=IntPtr.Zero; return result;
    } finally {
      if(linked!=IntPtr.Zero) CloseHandle(linked);
      Marshal.FreeHGlobal(data);
    }
  }
  // Observe only whether this caller has a queryable existing linked token.
  // Only actual class19 query error1312 is an unavailable optional comparison.
  // Successful query alone never qualifies the token identity or lease.
  public static int? ObserveCallerLinkedTokenQueryAvailability() {
    IntPtr caller,linked=IntPtr.Zero;
    if(!OpenProcessToken(GetCurrentProcess(),8,out caller)) throw new Win32Exception();
    try {
      try { linked=ReadCallerLinkedToken(caller); }
      catch(CallerLinkedTokenQueryFailure failure) {
        if(failure.NativeErrorCode!=1312) throw;
        return failure.NativeErrorCode;
      }
      return null;
    } finally { if(linked!=IntPtr.Zero) CloseHandle(linked); CloseHandle(caller); }
  }
  static void AssertCallerLinkedTokenClassification(bool sameSid,bool callerElevated,bool linkedElevated,uint tokenType,uint elevationType) {
    if(!sameSid || !callerElevated || linkedElevated || tokenType!=1 || elevationType!=3) throw new InvalidOperationException("Caller-linked token is not the existing elevated caller SID under actual primary1/Limited3 non-elevation");
  }
  static void AssertCallerLinkedToken(IntPtr caller, IntPtr linked) {
    using(var original=new WindowsIdentity(caller)) using(var filtered=new WindowsIdentity(linked)) {
      AssertCallerLinkedTokenClassification(original.User.Value==filtered.User.Value,ElevatedToken(caller),ElevatedToken(linked),ObservedTokenDword(linked,ObservedTokenClass.Type),ObservedTokenDword(linked,ObservedTokenClass.ElevationType));
    }
  }
  public static int StartCallerLinkedUser(string executable) {
    IntPtr caller,linked=IntPtr.Zero;
    if(!OpenProcessToken(GetCurrentProcess(),8,out caller)) throw new Win32Exception();
    try {
      linked=ReadCallerLinkedToken(caller);
      AssertCallerLinkedToken(caller,linked);
      StartupInfo startup=Startup(); ProcessInfo process;
      // Existing returned-handle access only. Failure is retained; no duplicate
      // with broader access, privilege grant or alternate launch fallback.
      bool success=CreateProcessWithTokenW(linked,1,executable,new StringBuilder("\""+executable+"\""),0,IntPtr.Zero,System.IO.Path.GetDirectoryName(executable),ref startup,out process);
      int error=Marshal.GetLastWin32Error();
      if(!success) throw new Win32Exception(error,"Caller-linked process launch failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture));
      return Started(process);
    } finally { if(linked!=IntPtr.Zero) CloseHandle(linked); CloseHandle(caller); }
  }
  public static bool StartedUserSidMatchesCaller(int pid) {
    IntPtr process,token,caller;
    if(!StartedProcesses.TryGetValue(pid,out process)) throw new InvalidOperationException("Caller SID observation does not own the retained process");
    if(!OpenProcessToken(process,8,out token)) throw new Win32Exception();
    try {
      if(!OpenProcessToken(GetCurrentProcess(),8,out caller)) throw new Win32Exception();
      try {
        using(var original=new WindowsIdentity(caller)) using(var child=new WindowsIdentity(token)) {
          AssertCallerLinkedTokenClassification(original.User.Value==child.User.Value,ElevatedToken(caller),ElevatedToken(token),ObservedTokenDword(token,ObservedTokenClass.Type),ObservedTokenDword(token,ObservedTokenClass.ElevationType));
          return true;
        }
      }
      finally { CloseHandle(caller); }
    } finally { CloseHandle(token); }
  }
  static byte[] ObservedLogonSidBytes(IntPtr data,int length,out uint count) {
    int offset=Marshal.OffsetOf(typeof(ObservedGroupsHeader),"first").ToInt32();
    int stride=Marshal.SizeOf(typeof(ObservedSidAttributes));
    if(IntPtr.Size!=8 || offset!=8 || stride!=16 || length<offset) throw new InvalidOperationException("Read-only logon SID header bounds failed");
    count=unchecked((uint)Marshal.ReadInt32(data));
    if(count>1 || count>(uint)((length-offset)/stride)) throw new InvalidOperationException("Read-only logon SID count bounds failed");
    if(count==0) return null;
    var group=(ObservedSidAttributes)Marshal.PtrToStructure(IntPtr.Add(data,offset),typeof(ObservedSidAttributes));
    long sidOffset=group.sid.ToInt64()-data.ToInt64();
    if((group.attributes & 0xc0000000)!=0xc0000000 || sidOffset<offset+stride || sidOffset>length-20) throw new InvalidOperationException("Read-only logon SID pointer/attribute bounds failed");
    // Canonical logon SID is S-1-5-5-X-Y. Its private X/Y bytes stay local;
    // only count and comparison equality are serialized, never the SID or LUID.
    if(Marshal.ReadByte(group.sid)!=1 || Marshal.ReadByte(group.sid,1)!=3 ||
       Marshal.ReadByte(group.sid,2)!=0 || Marshal.ReadByte(group.sid,3)!=0 ||
       Marshal.ReadByte(group.sid,4)!=0 || Marshal.ReadByte(group.sid,5)!=0 ||
       Marshal.ReadByte(group.sid,6)!=0 || Marshal.ReadByte(group.sid,7)!=5 ||
       Marshal.ReadInt32(group.sid,8)!=5) throw new InvalidOperationException("Read-only logon SID canonical shape failed");
    byte[] value=new byte[20]; Marshal.Copy(group.sid,value,0,value.Length); return value;
  }
  static byte[] ObservedLogonSid(IntPtr token,out uint count) {
    int length;IntPtr data=ObservedTokenBuffer(token,ObservedTokenClass.LogonSid,out length);
    try { return ObservedLogonSidBytes(data,length,out count); } finally { Marshal.FreeHGlobal(data); }
  }
  static bool ObservedLogonSidEqual(byte[] first,byte[] second) {
    if(first==null || second==null || first.Length!=20 || second.Length!=20) throw new InvalidOperationException("Read-only logon SID equality requires two bounded actual identities");
    for(int i=0;i<20;i++) if(first[i]!=second[i]) return false;
    return true;
  }
  static string ObserveLogonSidComparison(IntPtr target) {
    uint? targetCount=null;string subject="target";
    try {
      uint count;byte[] first=ObservedLogonSid(target,out count);targetCount=count;
      if(first==null) return "tokenLogonSidCount=0;tokenLogonSidMatchesCaller=unavailable;tokenLogonSidQuerySubject=target;tokenLogonSidQueryFailure=empty;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none";
      subject="caller";IntPtr caller;
      if(!OpenProcessToken(GetCurrentProcess(),8,out caller)) throw new ObservedTokenQueryFailure(ObservedTokenClass.LogonSid,"api-error",Marshal.GetLastWin32Error(),null);
      try {
        byte[] second=ObservedLogonSid(caller,out count);
        if(second==null) return "tokenLogonSidCount=1;tokenLogonSidMatchesCaller=unavailable;tokenLogonSidQuerySubject=caller;tokenLogonSidQueryFailure=empty;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none";
        bool equal=ObservedLogonSidEqual(first,second);
        return "tokenLogonSidCount=1;tokenLogonSidMatchesCaller="+(equal?"true":"false")+";tokenLogonSidQuerySubject=none;tokenLogonSidQueryFailure=none;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none";
      } finally { CloseHandle(caller); }
    } catch(ObservedTokenQueryFailure failure) {
      return "tokenLogonSidCount="+(targetCount.HasValue?targetCount.Value.ToString(System.Globalization.CultureInfo.InvariantCulture):"unavailable")+
        ";tokenLogonSidMatchesCaller=unavailable;tokenLogonSidQuerySubject="+subject+";tokenLogonSidQueryFailure="+failure.failure+
        ";tokenLogonSidQueryError="+(failure.error.HasValue?failure.error.Value.ToString(System.Globalization.CultureInfo.InvariantCulture):"none")+
        ";tokenLogonSidQueryReturnedLength="+(failure.length.HasValue?failure.length.Value.ToString(System.Globalization.CultureInfo.InvariantCulture):"none");
    } catch(InvalidOperationException) {
      return "tokenLogonSidCount="+(targetCount.HasValue?targetCount.Value.ToString(System.Globalization.CultureInfo.InvariantCulture):"unavailable")+
        ";tokenLogonSidMatchesCaller=unavailable;tokenLogonSidQuerySubject="+subject+";tokenLogonSidQueryFailure=content-contract;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none";
    }
  }

  static ObservedTokenFacts ReadObservedTokenFacts(IntPtr token) {
    var facts=new ObservedTokenFacts { sessionKnown=false };
    var kind=ObservedTokenClass.SessionId;
    try {
      facts.sessionId=ObservedTokenDword(token,kind); facts.sessionKnown=true;
      kind=ObservedTokenClass.HasRestrictions; facts.hasRestrictions=ObservedTokenBool(token,kind,out facts.hasRestrictionsReturnedLength);
      kind=ObservedTokenClass.IsAppContainer; facts.isAppContainer=ObservedTokenBool(token,kind,out facts.isAppContainerReturnedLength);
      kind=ObservedTokenClass.IntegrityLevel; facts.integrityRid=ObservedIntegrityRid(token);
      kind=ObservedTokenClass.Groups; ObservedMemberships(token,facts);
    } catch(ObservedTokenQueryFailure failure) {
      facts.unavailableKind=failure.kind.ToString(); facts.unavailableFailure=failure.failure;
      facts.unavailableError=failure.error; facts.unavailableLength=failure.length;
    } catch(InvalidOperationException) {
      facts.unavailableKind=kind.ToString(); facts.unavailableFailure="content-contract";
    }
    facts.logonComparison=ObserveLogonSidComparison(token);
    return facts;
  }
  public static ObservedTokenFacts RunnerTokenFacts() {
    IntPtr token;
    if(!OpenProcessToken(GetCurrentProcess(),8,out token)) throw new Win32Exception();
    try { return ReadObservedTokenFacts(token); } finally { CloseHandle(token); }
  }
  public static ObservedTokenFacts StartedTokenFacts(int pid) {
    IntPtr process, token;
    if(!StartedProcesses.TryGetValue(pid,out process)) throw new InvalidOperationException("Token observation does not own the retained process");
    if(!OpenProcessToken(process,8,out token)) throw new Win32Exception();
    try { return ReadObservedTokenFacts(token); } finally { CloseHandle(token); }
  }
  public static ObservedTokenFacts FreshStandardTokenFacts(string username, string password, string expectedSid) {
    IntPtr token;
    if(!LogonUserW(username,".",password,2,0,out token)) throw new Win32Exception();
    try {
      using(var identity=new WindowsIdentity(token)) {
        if(identity.User.Value!=expectedSid || ElevatedToken(token)) throw new InvalidOperationException("Fresh observed logon token does not own the standard SID");
      }
      return ReadObservedTokenFacts(token);
    } finally { CloseHandle(token); }
  }
  public static int StartStandardUserWithToken(string executable, string username, string password, string expectedSid) {
    IntPtr token;
    if (!LogonUserW(username, ".", password, 2, 0, out token)) throw new Win32Exception();
    try {
      using (var identity = new WindowsIdentity(token)) {
        if (identity.User.Value != expectedSid || ElevatedToken(token)) throw new InvalidOperationException("Secondary launch did not log on the owned standard SID");
      }
      StartupInfo startup = Startup(); ProcessInfo process;
      if (!CreateProcessWithTokenW(token, 1, executable, new StringBuilder("\"" + executable + "\""), 0, IntPtr.Zero, System.IO.Path.GetDirectoryName(executable), ref startup, out process)) throw new Win32Exception();
      return Started(process);
    } finally { CloseHandle(token); }
  }
  public static bool StartedUserSidMatches(int pid, string expectedSid) {
    IntPtr process, token;
    if (!StartedProcesses.TryGetValue(pid, out process)) throw new InvalidOperationException("SID check does not own the retained process");
    if (!OpenProcessToken(process, 8, out token)) throw new Win32Exception();
    try {
      using (var identity = new WindowsIdentity(token)) { return identity.User.Value == expectedSid; }
    } finally { CloseHandle(token); }
  }
'@
  $bridge = @'
  public static System.Collections.Generic.Dictionary<string,string> ObserveOwnedNamespace(int pid) {
    IntPtr process;
    if (!StartedProcesses.TryGetValue(pid,out process) || WaitForSingleObject(process,0)!=0x102 || GetProcessId(process)!=checked((uint)pid)) throw new InvalidOperationException("Namespace observation requires an actual retained live owned process");
    return MixelCI.MixelStandardNamespaceDiagnostics.Observe(process);
  }
'@
  $normalSource = $normalSource.Insert($position,$comparison+"`n"+$bridge+"`n")
  $diagnostics = Get-Content (Join-Path $PSScriptRoot 'windows-standard-namespace-diagnostics.cs') -Raw
  Add-Type -TypeDefinition ($normalSource+"`n"+$diagnostics)
}
if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) { throw 'Owned standard-account qualification requires the elevated isolated runner.' }

if (-not ('MixelIsolatedLeaseControl' -as [type])) { Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MixelIsolatedLeaseControl {
  [StructLayout(LayoutKind.Sequential)] struct Attributes { public uint length; public IntPtr descriptor; public int inherit; }
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string text, uint version, out IntPtr descriptor, IntPtr size);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateEventExW(ref Attributes attributes, string name, uint flags, uint access);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateMutexW(ref Attributes attributes, bool owner, string name);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
  static void Isolated(string name) {
    if (!System.Text.RegularExpressions.Regex.IsMatch(name, @"\A(?:(?:Global|Local)\\)?Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z")) throw new InvalidOperationException("Expected GUID-isolated lease control name");
  }
  static IntPtr Event(string name, string sddl) {
    Isolated(name);
    IntPtr descriptor;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,out descriptor,IntPtr.Zero)) throw new Win32Exception();
    IntPtr handle; int error;
    try {
      Attributes attributes=new Attributes { length=(uint)Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=0 };
      handle=CreateEventExW(ref attributes,name,0,0x100000);
      error=handle==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    } finally { LocalFree(descriptor); }
    if(handle==IntPtr.Zero) throw new Win32Exception(error);
    return handle;
  }
  public static IntPtr DeniedEvent(string name) { return Event(name,"D:P(A;;GA;;;BA)(A;;GA;;;SY)"); }
  public static IntPtr WorldEvent(string name) { return Event(name,"D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)"); }
  public static IntPtr WrongObject(string name) {
    Isolated(name);
    // Grant the requested SYNCHRONIZE right to the ordinary account, so an
    // access denial cannot obscure the intended wrong-object type error.
    IntPtr descriptor;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW("D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)",1,out descriptor,IntPtr.Zero)) throw new Win32Exception();
    IntPtr handle; int error;
    try {
      Attributes attributes=new Attributes { length=(uint)Marshal.SizeOf(typeof(Attributes)), descriptor=descriptor, inherit=0 };
      handle=CreateMutexW(ref attributes,false,name);
      error=handle==IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
    } finally { LocalFree(descriptor); }
    if(handle==IntPtr.Zero) throw new Win32Exception(error);
    return handle;
  }
  public static void Close(IntPtr handle) { if(handle!=IntPtr.Zero && !CloseHandle(handle)) throw new Win32Exception(); }
}
'@ }

function Assert-StandardLeaseSafeResult($Record, [uint32]$ExpectedPid) {
  $names = @($Record.PSObject.Properties.Name | Sort-Object)
  $expectedNames = @('attestation','ownerFailed','pid','rawOpenError','requiresClick','sourceStatus')
  $pidIsNumber = $Record.pid -is [int] -or $Record.pid -is [long] -or $Record.pid -is [uint32] -or $Record.pid -is [uint64]
  $errorIsNumber = $Record.rawOpenError -is [int] -or $Record.rawOpenError -is [long] -or $Record.rawOpenError -is [uint32] -or $Record.rawOpenError -is [uint64]
  if (($names -join ',') -cne ($expectedNames -join ',') -or $ExpectedPid -eq 0 -or
      -not $pidIsNumber -or $Record.pid -ne $ExpectedPid -or -not $errorIsNumber -or
      $Record.rawOpenError -lt 0 -or $Record.rawOpenError -gt [uint32]::MaxValue -or
      $Record.sourceStatus -isnot [string] -or $Record.attestation -isnot [string] -or
      $Record.attestation -cnotin @('','attended-runtime-v2','guard-unavailable') -or
      $Record.requiresClick -isnot [bool] -or $Record.ownerFailed -isnot [bool]) {
    throw 'Owned standard-account result is not the exact PID or canonical safe source classification.'
  }
  $codePattern = '(?:none|0|-?[1-9][0-9]{0,9})'
  $fields = [regex]::Match($Record.sourceStatus, ('\AownerFailed=(?<owner>true|false);ownerError=(?<ownerError>' + $codePattern + ');kernel=(?<kernel>active|absent|unavailable);probeError=(?<probeError>' + $codePattern + ');memoryActive=(true|false)\z'))
  if (-not $fields.Success -or
      (($fields.Groups['owner'].Value -ceq 'true') -ne ($fields.Groups['ownerError'].Value -cne 'none')) -or
      (($fields.Groups['kernel'].Value -ceq 'unavailable') -ne ($fields.Groups['probeError'].Value -cne 'none')) -or
      (($fields.Groups['owner'].Value -ceq 'true') -ne $Record.ownerFailed)) {
    throw 'Owned standard-account result contains inconsistent read-only native lease classification.'
  }
  foreach ($field in @('ownerError','probeError')) {
    $value = $fields.Groups[$field].Value
    [int]$nativeCode = 0
    if ($value -cne 'none' -and -not [int]::TryParse($value, [Globalization.NumberStyles]::AllowLeadingSign, [Globalization.CultureInfo]::InvariantCulture, [ref]$nativeCode)) {
      throw 'Owned standard-account result contains an out-of-range native lease error.'
    }
  }
}

function Read-StandardUnnamedEventResult([uint32]$ExpectedPid) {
  $path = Join-Path $fixtureRoot 'fixture-unnamed-result.json'
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Owned unnamed event observation result is missing.' }
  $text = [IO.File]::ReadAllText($path)
  if ($text.Length -gt 4096) { throw 'Owned unnamed event observation exceeds its bound.' }
  $record = $text | ConvertFrom-Json
  $names = @($record.PSObject.Properties.Name | Sort-Object)
  $expected = @('pid','phase','created','createError','closed','closeError','sourceStatusAfter','rawOpenErrorAfter','attestationAfter','requiresClickAfter') | Sort-Object
  $pidIsNumber = $record.pid -is [int] -or $record.pid -is [long] -or $record.pid -is [uint32] -or $record.pid -is [uint64]
  if (($names -join ',') -cne ($expected -join ',') -or -not $pidIsNumber -or
      $ExpectedPid -eq 0 -or $record.pid -ne $ExpectedPid -or
      $record.phase -isnot [string] -or $record.phase -cnotin @('sddl','create','close') -or
      $record.created -isnot [bool] -or $record.closed -isnot [bool]) {
    throw 'Owned unnamed event result has invalid schema, types or retained PID.'
  }
  foreach ($field in @('createError','closeError')) {
    $value = $record.$field
    $isNumber = $value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]
    if (-not $isNumber -or $value -lt 0 -or $value -gt [uint32]::MaxValue) {
      throw 'Owned unnamed event native error is not a bounded integer.'
    }
  }
  if ((-not $record.created -and ($record.phase -ceq 'close' -or $record.closed -or $record.closeError -ne 0)) -or
      ($record.created -and ($record.phase -cne 'close' -or $record.createError -ne 0 -or
          ($record.closed -and $record.closeError -ne 0)))) {
    throw 'Owned unnamed event result contains inconsistent create/close stage fields.'
  }
  $after = [pscustomobject]@{pid=$record.pid;sourceStatus=$record.sourceStatusAfter;rawOpenError=$record.rawOpenErrorAfter;attestation=$record.attestationAfter;requiresClick=$record.requiresClickAfter;ownerFailed=$false}
  Assert-StandardLeaseSafeResult $after $ExpectedPid
  if (-not $record.sourceStatusAfter.StartsWith('ownerFailed=false;ownerError=none;') -or
      -not $record.sourceStatusAfter.EndsWith(';memoryActive=false') -or
      $record.sourceStatusAfter.Contains(';kernel=active;') -or $record.attestationAfter -ceq 'attended-runtime-v2') {
    throw 'Read-only unnamed observation unexpectedly armed memory or acquired a named owner lease.'
  }
  return $record
}

function Assert-StandardUnnamedEventControl($Record, $Before) {
  # The safe observational reader preserves an API failure's literal zero code.
  # Refuse that unknown functional outcome after it has been printed.
  if (-not $Record.created -and $Record.createError -eq 0) {
    throw 'Owned unnamed event API failed without a native error code; functional outcome is unknown.'
  }
  if ($Record.created -and -not $Record.closed) {
    if ($Record.closeError -eq 0) {
      throw 'Exact owned unnamed event handle close failed without a native error code; functional outcome is unknown.'
    }
    throw 'Exact owned unnamed event handle did not close successfully; owned process cleanup is required.'
  }
  if ($Record.sourceStatusAfter -cne $Before.sourceStatus -or
      $Record.rawOpenErrorAfter -ne $Before.rawOpenError -or
      $Record.attestationAfter -cne $Before.attestation -or
      $Record.requiresClickAfter -ne $Before.requiresClick) {
    throw 'Isolated unnamed event observation changed the exact untouched named guard classification.'
  }
}


function Invoke-StandardLeaseControl([ValidateSet('probe','owner','failed-owner')][string]$Mode, [string]$ExpectedStatus, [int]$ExpectedError,
    [string]$ExpectedProof, [bool]$ExpectedRequired, [bool]$ExpectedOwnerFailed, [switch]$KeepAlive,
    [switch]$ObserveOnly, [string]$ObservationLabel,
    [ValidateSet('with-logon','with-token','with-caller-linked')][string]$Launcher = 'with-logon',
    [ValidateSet('console','windows')][string]$Subsystem = 'console', [switch]$UnnamedControl) {
  $expectedExecutable = if ($Subsystem -ceq 'windows') { 'lease-standard-gui.exe' } else { 'lease-standard.exe' }
  if ([IO.Path]::GetFileName($executable) -cne $expectedExecutable) { throw 'Owned source control executable does not match its declared subsystem.' }
  $unnamedRequest = Join-Path $fixtureRoot 'fixture-unnamed.request'
  $unnamedResult = Join-Path $fixtureRoot 'fixture-unnamed-result.json'
  if (Test-Path -LiteralPath $unnamedRequest) { Remove-Item -LiteralPath $unnamedRequest }
  if (Test-Path -LiteralPath $unnamedResult) { Remove-Item -LiteralPath $unnamedResult }
  if ($UnnamedControl) {
    if ($Launcher -ceq 'with-caller-linked' -or -not $ObserveOnly -or $Mode -cne 'probe' -or $KeepAlive -or
        $ObservationLabel -cne "global-$Subsystem-initial-untouched-absent") { throw 'Unnamed event observation is limited to the initial untouched subsystem probe.' }
    [IO.File]::WriteAllText($unnamedRequest, 'observe', [Text.UTF8Encoding]::new($false))
  }
  if (Test-Path $resultPath) { Remove-Item -LiteralPath $resultPath }
  $namespacePath = Join-Path $fixtureRoot 'fixture-namespace.json'
  if (Test-Path $namespacePath) { Remove-Item -LiteralPath $namespacePath }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-mode.txt'), $Mode, [Text.UTF8Encoding]::new($false))
  if ($Launcher -ceq 'with-caller-linked') {
    if (-not $ObserveOnly) { throw 'Caller-linked UAC token cannot satisfy pure standard-account acceptance.' }
    $controlPid=[MixelOrdinaryTokenFixture]::StartCallerLinkedUser($executable)
  } elseif ($Launcher -ceq 'with-token') {
    $controlPid = [MixelOrdinaryTokenFixture]::StartStandardUserWithToken($executable, $ownedUser, $ownedPassword, $ownedSid)
  } else {
    $controlPid = [MixelOrdinaryTokenFixture]::StartStandardUser($executable, $ownedUser, $ownedPassword)
  }
  $control = $null
  try {
    $control = Get-Process -Id $controlPid
    $sidMatches = if ($Launcher -ceq 'with-caller-linked') { [MixelOrdinaryTokenFixture]::StartedUserSidMatchesCaller($controlPid) } else { [MixelOrdinaryTokenFixture]::StartedUserSidMatches($controlPid, $ownedSid) }
    $elevated = [MixelOrdinaryTokenFixture]::Elevated($controlPid)
    $actualSession = $control.SessionId
    if (-not $sidMatches -or $elevated -or $actualSession -ne (Get-Process -Id $PID).SessionId) {
      throw 'Lease control is not the verified source identity under the actual non-elevated same-session token.'
    }
    $tokenFacts = [MixelOrdinaryTokenFixture]::StartedTokenFacts($controlPid)
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path $resultPath) -and [DateTime]::UtcNow -lt $deadline) {
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid).StartsWith('exited:')) { break }
      Start-Sleep -Milliseconds 50
    }
    if (-not (Test-Path $resultPath)) { throw 'Owned standard-account lease control did not produce its bounded native result.' }
    $text = [IO.File]::ReadAllText($resultPath)
    if ($text.Length -gt 4096) { throw 'Owned lease result exceeds its bound.' }
    $record = $text | ConvertFrom-Json
    Assert-StandardLeaseSafeResult $record $controlPid
    Write-Host "OBSERVED: primary source lease result before secondary token diagnosis; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2')."
    try {
      if (Test-Path $namespacePath) {
        if ((Get-Item -LiteralPath $namespacePath).Length -gt 4096) { throw 'Bounded child namespace observation exceeded its byte limit.' }
        $namespaceText = [IO.File]::ReadAllText($namespacePath)
        if ($namespaceText.Length -gt 4096) { throw 'Bounded child namespace observation exceeded its limit.' }
        $namespaceRecord = $namespaceText | ConvertFrom-Json
        if ($namespaceRecord.pid -ne $controlPid) { throw 'Child namespace observation does not belong to the actual owned PID.' }
        foreach ($property in $namespaceRecord.PSObject.Properties) {
          if ($property.Name -cnotmatch '\A(?:pid|session|observationError|GlobalAbsoluteEventError|SessionAbsoluteEventError|(?:Global|Local)Alias(?:OpenError|QueryError|TargetKind)|(?:root|sessions|sessionRoot|sessionObjects|globalObjects)(?:TraverseError|QueryError|CreateAccessError|CreateSubdirectoryAccessError|CombinedCreationAccessError|AllSpecificAccessError))\z' -or ($property.Value -isnot [long] -and $property.Value -isnot [int])) { throw 'Child namespace observation is not a bounded scalar record.' }
          if ($property.Name -ceq 'observationError') {
            if ($property.Value -lt -2147483648 -or $property.Value -gt 2147483647) { throw 'Child namespace native error exceeds its signed scalar domain.' }
          } elseif ($property.Value -lt 0 -or $property.Value -gt 4294967295) { throw 'Child namespace DWORD exceeds its unsigned scalar domain.' }
          if ($property.Name -cmatch '\A(?:Global|Local)AliasTargetKind\z' -and $property.Value -gt 3) { throw 'Child namespace alias target kind exceeds its numeric domain.' }
        }
        $namespaceNames = @($namespaceRecord.PSObject.Properties.Name)
        if ($namespaceNames -ccontains 'observationError') {
          if ($namespaceNames.Count -ne 2) { throw 'Child namespace error record contains unexpected fields.' }
        } else {
          if ($namespaceNames.Count -ne 40 -or $namespaceRecord.session -lt 0) { throw 'Child namespace observation is incomplete or has an invalid session.' }
          foreach ($alias in @('Global','Local')) {
            $open = $namespaceRecord.PSObject.Properties[$alias+'AliasOpenError'].Value
            $query = $namespaceRecord.PSObject.Properties[$alias+'AliasQueryError'].Value
            $kind = $namespaceRecord.PSObject.Properties[$alias+'AliasTargetKind'].Value
            if (($open -ne 0 -and ($query -ne 4294967295 -or $kind -ne 0)) -or
                ($open -eq 0 -and (($query -ne 0 -and $kind -ne 0) -or ($query -eq 0 -and $kind -eq 0)))) {
              throw 'Child namespace alias observation has inconsistent numeric states.'
            }
          }
        }
        Write-Host ('OBSERVED: actual child-token namespace access; ' + ($namespaceRecord | ConvertTo-Json -Compress))
      } else { Write-Host 'OBSERVED: secondary child namespace observation unavailable.' }
    } catch { Write-Host 'OBSERVED: secondary child namespace observation invalid or unavailable; strict source gate remains required.' }
    try {
      $tokenObservation = [MixelOrdinaryTokenFixture]::ObserveOwnedNamespace($controlPid)
      Write-Host ('OBSERVED: actual owned standard-token namespace access; ' + ($tokenObservation | ConvertTo-Json -Compress))
    } catch {
      $nativeFailure = $_.Exception
      while ($nativeFailure.InnerException) { $nativeFailure = $nativeFailure.InnerException }
      if ($nativeFailure -is [MixelCI.NamespaceRestoreException]) { throw }
      $nativeCode = if ($nativeFailure -is [ComponentModel.Win32Exception]) { $nativeFailure.NativeErrorCode } else { $nativeFailure.HResult }
      Write-Host "OBSERVED: secondary owned token namespace diagnosis unavailable; nativeCode=$nativeCode."
    }
    if (-not $ObserveOnly -and ($record.sourceStatus -cne $ExpectedStatus -or
        $record.rawOpenError -ne $ExpectedError -or $record.attestation -cne $ExpectedProof -or
        $record.requiresClick -isnot [bool] -or $record.requiresClick -ne $ExpectedRequired -or
        $record.ownerFailed -isnot [bool] -or $record.ownerFailed -ne $ExpectedOwnerFailed)) {
      throw ('Actual standard-account source lease classification mismatch: ' + ($record | ConvertTo-Json -Compress))
    }
    if ($ObserveOnly) {
      if ($ObservationLabel -cnotmatch '\A(?:bare|local|global)-[a-z]+(?:-[a-z]+)*\z') { throw 'Invalid safe namespace observation label.' }
      if ($Launcher -ceq 'with-caller-linked') {
        Write-Host "OBSERVED: caller-linked-token GUID functional control; tokenContext=current-caller-uac-linked; pureStandardAccount=False; subsystem=$Subsystem; scenario=$ObservationLabel; mode=$Mode; controlPid=$controlPid; ordinarySessionId=$actualSession; ownedCallerSidMatched=$sidMatches; elevated=$elevated; $tokenFacts; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
      } else {
      Write-Host "OBSERVED: standard-token GUID functional control; launcher=$Launcher; subsystem=$Subsystem; scenario=$ObservationLabel; mode=$Mode; controlPid=$controlPid; ordinarySessionId=$actualSession; ownedSidMatched=$sidMatches; elevated=$elevated; $tokenFacts; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
      }
    } else {
      Write-Host "FIXTURE: actual source lease under non-elevated same-session token; mode=$Mode; $($record.sourceStatus); rawOpenError=$($record.rawOpenError); guardEmpty=$($record.attestation -ceq ''); guardConfirmed=$($record.attestation -ceq 'attended-runtime-v2'); requiresClick=$($record.requiresClick)."
    }
    if ($UnnamedControl) {
      $deadline = [DateTime]::UtcNow.AddSeconds(10)
      while (-not (Test-Path -LiteralPath $unnamedResult -PathType Leaf) -and [DateTime]::UtcNow -lt $deadline) {
        if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid).StartsWith('exited:')) { break }
        Start-Sleep -Milliseconds 50
      }
      $unnamed = Read-StandardUnnamedEventResult $controlPid
      Write-Host "OBSERVED: standard-token unnamed event control; launcher=$Launcher; subsystem=$Subsystem; controlPid=$controlPid; ordinarySessionId=$actualSession; ownedSidMatched=$sidMatches; elevated=$elevated; phase=$($unnamed.phase); created=$($unnamed.created); createError=$($unnamed.createError); closed=$($unnamed.closed); closeError=$($unnamed.closeError); sourceStatusAfter=$($unnamed.sourceStatusAfter); rawOpenErrorAfter=$($unnamed.rawOpenErrorAfter); guardEmptyAfter=$($unnamed.attestationAfter -ceq ''); guardConfirmedAfter=$($unnamed.attestationAfter -ceq 'attended-runtime-v2'); requiresClickAfter=$($unnamed.requiresClickAfter)."
      Assert-StandardUnnamedEventControl $unnamed $record
    }
    if (-not $KeepAlive) {
      [IO.File]::WriteAllText((Join-Path $fixtureRoot "fixture-release-$controlPid"), 'verified')
      $deadline = [DateTime]::UtcNow.AddSeconds(10)
      while ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 50
      }
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'exited:0x00000000') { throw 'Exact owned functional lease process did not exit successfully.' }
    } elseif ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'still-running') {
      throw 'Actual standard-account lease owner exited before independent observation.'
    }
    return $controlPid
  } finally { if ($control) { $control.Dispose() } }
}

function Invoke-StandardLeaseSubsystemObservations([ValidateSet('with-logon','with-token','with-caller-linked')][string]$Launcher) {
  # Each selected executable observes its own untouched GUID. Both binaries in
  # each pair have byte-identical source apart from the crate subsystem header.
  # This experiment cannot satisfy the original strict console fixture below.
  $savedRoot=$script:fixtureRoot; $savedExecutable=$script:executable; $savedResult=$script:resultPath
  try {
    foreach ($subsystem in @('console','windows')) {
      $script:fixtureRoot=Join-Path $savedRoot "$Launcher-$subsystem-pair"
      New-Item -ItemType Directory -Path $script:fixtureRoot | Out-Null
      $filename=if ($subsystem -ceq 'windows') { 'lease-standard-gui.exe' } else { 'lease-standard.exe' }
      $script:executable=Join-Path $script:fixtureRoot $filename
      $script:resultPath=Join-Path $script:fixtureRoot 'fixture-result.json'
      $endpoint='Global\Mixel-Remote-Standard-Lease-' + [Guid]::NewGuid().ToString('N')
      python (Join-Path $PSScriptRoot 'test-support-lease.py') --export-standard-windows-fixture $script:fixtureRoot --event $endpoint --export-standard-windows-subsystem-pair
      if ($LASTEXITCODE -ne 0) { throw 'Exact source console/Windows subsystem pair compilation failed.' }
      try {
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -Subsystem $subsystem -ObserveOnly -ObservationLabel "global-$subsystem-initial-untouched-absent" -UnnamedControl:($Launcher -cne 'with-caller-linked'))
        [void](Invoke-StandardLeaseControl -Mode owner -Launcher $Launcher -Subsystem $subsystem -KeepAlive -ObserveOnly -ObservationLabel "global-$subsystem-explicit-source-owner")
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -Subsystem $subsystem -ObserveOnly -ObservationLabel "global-$subsystem-fresh-probe-after-owner-request")
        [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -Subsystem $subsystem -ObserveOnly -ObservationLabel "global-$subsystem-after-confirmed-owner-request-process-termination")
      } finally {
        [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
      }
    }
  } finally {
    $script:fixtureRoot=$savedRoot; $script:executable=$savedExecutable; $script:resultPath=$savedResult
  }
}

function Invoke-OptionalCallerLinkedLeaseSubsystemObservations {
  $unavailableError = [MixelOrdinaryTokenFixture]::ObserveCallerLinkedTokenQueryAvailability()
  if ($null -ne $unavailableError) {
    if ($unavailableError -ne 1312) { throw 'Optional caller-linked query returned an unexpected availability classifier.' }
    Write-Host "OBSERVED: optional caller-linked comparison unavailable; operation=GetTokenInformation; tokenInformationClass=19; nativeError=$unavailableError; actualChildCount=0; fallback=False."
    return
  }
  Invoke-StandardLeaseSubsystemObservations with-caller-linked
}

function Invoke-StandardLeaseNamespaceObservations([ValidateSet('with-logon','with-token')][string]$Launcher) {
  # These are observations only. They cannot satisfy the unchanged original
  # Global absence/error2 or consent gate that follows this isolated matrix.
  $savedRoot=$script:fixtureRoot; $savedExecutable=$script:executable; $savedResult=$script:resultPath
  try {
    foreach ($namespace in @('bare','local','global')) {
      $script:fixtureRoot=Join-Path $savedRoot "$Launcher-$namespace"
      New-Item -ItemType Directory -Path $script:fixtureRoot | Out-Null
      $script:executable=Join-Path $script:fixtureRoot 'lease-standard.exe'
      $script:resultPath=Join-Path $script:fixtureRoot 'fixture-result.json'
      $name='Mixel-Remote-Standard-Lease-' + [Guid]::NewGuid().ToString('N')
      $endpoint=switch ($namespace) { 'bare' { $name }; 'local' { 'Local\'+$name }; 'global' { 'Global\'+$name } }
      python (Join-Path $PSScriptRoot 'test-support-lease.py') --export-standard-windows-fixture $script:fixtureRoot --event $endpoint
      if ($LASTEXITCODE -ne 0) { throw 'Normal namespace source control compilation failed.' }
      $matrixHandle=[IntPtr]::Zero
      try {
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-initial-untouched-absent")
        [void](Invoke-StandardLeaseControl -Mode owner -Launcher $Launcher -KeepAlive -ObserveOnly -ObservationLabel "$namespace-explicit-source-owner")
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-fresh-probe-after-owner-request")
        [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-after-confirmed-owner-request-process-termination")
        $matrixHandle=[MixelIsolatedLeaseControl]::WorldEvent($endpoint)
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-existing-world-synchronize-event")
        [MixelIsolatedLeaseControl]::Close($matrixHandle)
        $matrixHandle=[IntPtr]::Zero
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-after-confirmed-world-event-close")
      } finally {
        if ($matrixHandle -ne [IntPtr]::Zero) { [MixelIsolatedLeaseControl]::Close($matrixHandle) }
        [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
        [MixelOrdinaryTokenFixture]::CloseStartedObservations()
      }
    }
  } finally {
    $script:fixtureRoot=$savedRoot; $script:executable=$savedExecutable; $script:resultPath=$savedResult
  }
}

$ownedUser = 'mixellease' + [Guid]::NewGuid().ToString('N').Substring(0,10)
$ownedSid = $null; $ownedPassword = $null; $random = $null
$fixtureRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('mixel-standard-lease-' + [Guid]::NewGuid().ToString('N'))
$eventName = 'Global\Mixel-Remote-Standard-Lease-' + [Guid]::NewGuid().ToString('N')
$executable = Join-Path $fixtureRoot 'lease-standard.exe'
$resultPath = Join-Path $fixtureRoot 'fixture-result.json'
$desktopAccess = $null; $namespaceAccess = $null; $profileStarted = $false; $eventHandle = [IntPtr]::Zero; $failure = $null
$originalDesktop = [MixelOrdinaryTokenFixture]::DesktopAclHashes()
try {
  # Use the exact customer account bootstrap; its random password stays in memory.
  $accountStart = $launchSource.IndexOf('$random = [byte[]]::new(24)')
  $accountEnd = $launchSource.IndexOf("`$fixtureStage = 'grant owned standard desktop access'", $accountStart)
  if ($accountStart -lt 0 -or $accountEnd -le $accountStart) { throw 'Exact owned standard-account bootstrap is missing.' }
  . ([scriptblock]::Create($launchSource.Substring($accountStart,$accountEnd-$accountStart)))
  if (-not $ownedSid -or (Get-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid))).Name -cne $ownedUser) {
    throw 'Owned standard-account SID was not recorded exactly.'
  }
  New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
  $acl = Get-Acl $fixtureRoot
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
    [Security.Principal.SecurityIdentifier]::new($ownedSid),'Modify','ContainerInherit,ObjectInherit','None','Allow'))
  Set-Acl -Path $fixtureRoot -AclObject $acl
  python (Join-Path $PSScriptRoot 'test-support-lease.py') --export-standard-windows-fixture $fixtureRoot --event $eventName
  if ($LASTEXITCODE -ne 0) { throw 'Actual standard-account lease source compilation failed.' }
  $desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)
  $profileStarted = $true
  $runnerFacts=[MixelOrdinaryTokenFixture]::RunnerTokenFacts()
  $freshFacts=[MixelOrdinaryTokenFixture]::FreshStandardTokenFacts($ownedUser,$ownedPassword,$ownedSid)
  Write-Host "OBSERVED: read-only native token classification; subject=runner; processSessionId=$((Get-Process -Id $PID).SessionId); $runnerFacts."
  Write-Host "OBSERVED: read-only native token classification; subject=fresh-standard-logon; processSessionId=none; ownedSidMatched=True; elevated=False; $freshFacts."
  Invoke-StandardLeaseSubsystemObservations with-logon
  Invoke-StandardLeaseSubsystemObservations with-token
  Invoke-StandardLeaseNamespaceObservations with-logon
  Invoke-StandardLeaseNamespaceObservations with-token
  # Supplemental current-caller UAC linked-token observations cannot satisfy
  # the disposable pure standard-account acceptance that follows unchanged.
  Invoke-OptionalCallerLinkedLeaseSubsystemObservations
  # Preserve the original namespace-access classification as an A/B/A negative
  # control. These observed values cannot satisfy any positive consent gate.
  [void](Invoke-StandardLeaseControl -Mode probe -ObserveOnly -ObservationLabel 'global-original-before-owned-grant')
  $beforeGrant = [IO.File]::ReadAllText($resultPath) | ConvertFrom-Json
  $absent = 'ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
  $unavailable = 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false'
  $initialAbsent = $beforeGrant.sourceStatus -ceq $absent -and $beforeGrant.rawOpenError -eq 2 -and $beforeGrant.attestation -ceq '' -and -not $beforeGrant.requiresClick -and -not $beforeGrant.ownerFailed
  $initialDenied = $beforeGrant.sourceStatus -ceq $unavailable -and $beforeGrant.rawOpenError -eq 5 -and $beforeGrant.attestation -ceq 'guard-unavailable' -and $beforeGrant.requiresClick -and -not $beforeGrant.ownerFailed
  if (-not $initialAbsent -and -not $initialDenied) { throw 'Original unprepared standard namespace is neither safely absent nor safely unavailable.' }
  if (-not ('MixelCI.StandardNamespaceAccess' -as [type])) {
    Add-Type -TypeDefinition (Get-Content (Join-Path $PSScriptRoot 'windows-standard-namespace-access.cs') -Raw)
  }
  $namespaceAccess = [MixelCI.StandardNamespaceAccess]::new($ownedSid, $ownedUser, [uint32](Get-Process -Id $PID).SessionId)
  Write-Host 'FIXTURE: exact caller-session namespace grants only object/subdirectory creation (mask12) to the owned disposable SID; product guard and event ACLs remain enforced.'

  $active = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=false'
  $activeOwner = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=true'
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: actual standard-account source probe safely observes absent GUID event, raw error2, empty attestation and saved authorization behavior.'
  $ownerPid = Invoke-StandardLeaseControl owner $activeOwner 0 'attended-runtime-v2' $true $false -KeepAlive
  [void](Invoke-StandardLeaseControl probe $active 0 'attended-runtime-v2' $true $false)
  Write-Host 'PASS: actual standard-account source creates the SYNCHRONIZE lease and a fresh ordinary process observes its live confirmed owner.'
  [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
  if ([MixelOrdinaryTokenFixture]::StartedStatus($ownerPid) -cne 'exited:0x00000001') { throw 'Exact retained standard-account owner handle did not observe its termination.' }
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: confirmed owned standard-account owner termination releases the GUID event; fresh actual source returns empty ordinary attestation.'
  $eventHandle = [MixelIsolatedLeaseControl]::DeniedEvent($eventName)
  try {
    [void](Invoke-StandardLeaseControl probe 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false' 5 'guard-unavailable' $true $false)
    Write-Host 'PASS: actual standard-account inaccessible GUID event retains native error5 and unavailable attestation; authorization remains fail closed.'
  } finally { [MixelIsolatedLeaseControl]::Close($eventHandle); $eventHandle = [IntPtr]::Zero }
  $eventHandle = [MixelIsolatedLeaseControl]::WrongObject($eventName)
  try {
    [void](Invoke-StandardLeaseControl probe 'ownerFailed=false;ownerError=none;kernel=unavailable;probeError=6;memoryActive=false' 6 'guard-unavailable' $true $false)
    [void](Invoke-StandardLeaseControl failed-owner 'ownerFailed=true;ownerError=6;kernel=unavailable;probeError=6;memoryActive=true' 6 'guard-unavailable' $true $true)
    Write-Host 'PASS: actual standard-account wrong-object probe and failed owner acquisition retain native error6 and ownerFailed classification without false readiness.'
  } finally { [MixelIsolatedLeaseControl]::Close($eventHandle); $eventHandle = [IntPtr]::Zero }
  [void](Invoke-StandardLeaseControl probe $absent 2 '' $false $false)
  Write-Host 'PASS: closed wrong-object control leaves no GUID lease or false readiness in a fresh actual standard-account process.'
  [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  $namespaceAccess.Dispose(); $namespaceAccess = $null
  [void](Invoke-StandardLeaseControl probe $beforeGrant.sourceStatus ([int]$beforeGrant.rawOpenError) $beforeGrant.attestation ([bool]$beforeGrant.requiresClick) ([bool]$beforeGrant.ownerFailed))
  Write-Host 'PASS: native A/B/A namespace preparation preserves the original absent-or-denied classification after exact DACL restoration; no false readiness remains.'

} catch {
  $failure = $_
  throw
} finally {
  $cleanup = [System.Collections.Generic.List[string]]::new()
  if ($eventHandle -ne [IntPtr]::Zero) { try { [MixelIsolatedLeaseControl]::Close($eventHandle) } catch { $cleanup.Add('Owned isolated event handle cleanup failed.') } }
  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $cleanup.Add('Exact owned standard lease process cleanup failed.') }
  [MixelOrdinaryTokenFixture]::CloseStartedObservations()
  if ($namespaceAccess) { try { $namespaceAccess.Dispose() } catch { $cleanup.Add('Owned standard lease namespace DACL restoration failed.') } }
  if ($desktopAccess) { try { $desktopAccess.Dispose() } catch { $cleanup.Add('Owned standard lease desktop ACL restoration failed.') } }
  if ([MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $originalDesktop) { $cleanup.Add('Owned standard lease desktop ACL hashes changed.') }
  if ($profileStarted -and $ownedSid) {
    $removed = $false
    for ($attempt=0; $attempt -lt 5; $attempt++) {
      try { [MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid); $removed=$true; break }
      catch { if ($attempt -lt 4) { Start-Sleep -Seconds 2 } }
    }
    if (-not $removed) { $cleanup.Add('Owned standard lease profile cleanup failed.') }
  }
  if ($ownedSid) { try { Remove-LocalUser -SID ([Security.Principal.SecurityIdentifier]::new($ownedSid)) } catch { $cleanup.Add('Owned standard lease SID cleanup failed.') } }
  $ownedPassword = $null; $random = $null
  if (Test-Path $fixtureRoot) { try { Remove-Item -LiteralPath $fixtureRoot -Recurse -Force } catch { $cleanup.Add('Owned standard lease files cleanup failed.') } }
  if (Get-LocalUser -Name $ownedUser -ErrorAction SilentlyContinue) { $cleanup.Add('Owned standard lease account remains.') }
  if ($cleanup.Count -gt 0) {
    if ($failure) { Write-Host ('FAIL: standard lease cleanup additionally failed: ' + ($cleanup -join ' ')) }
    else { throw ($cleanup -join ' ') }
  } else { Write-Host 'PASS: standard-account lease controls remove only owned processes/profile/SID/files and restore exact original desktop ACLs.' }
}
