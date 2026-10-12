# Normal native functional controls under the exact customer fixture's ordinary
# token. No debugger, registry observation, product event, or customer payload.
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') {
  throw 'Disposable standard-account lease qualification requires an isolated Windows Actions runner.'
}
python (Join-Path $PSScriptRoot 'test-windows-lease-observation.py')
if ($LASTEXITCODE -ne 0) { throw 'Calling-thread observation and fatal baseline source controls failed.' }
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
  // Direct ordinary creation inherits only this Actions caller's existing
  // context. It never substitutes a secondary/linked token or desktop.
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
  static extern bool CreateProcessW(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creation, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
  public static int StartCurrentCallerNative(string executable) {
    var startup=new StartupInfo { cb=Marshal.SizeOf(typeof(StartupInfo)) }; ProcessInfo process;
    bool success=CreateProcessW(executable,new StringBuilder("\""+executable+"\""),IntPtr.Zero,IntPtr.Zero,false,0,IntPtr.Zero,System.IO.Path.GetDirectoryName(executable),ref startup,out process);
    int error=Marshal.GetLastWin32Error();
    if(!success) throw new Win32Exception(error,"Current-caller source launch failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture));
    return Started(process);
  }
  public static bool StartedCurrentCallerSidMatches(int pid) {
    IntPtr process,token,caller=IntPtr.Zero;
    bool callerOpened=false; Exception primaryFailure=null;
    if(!StartedProcesses.TryGetValue(pid,out process)) throw new InvalidOperationException("Current-caller SID check does not own the retained process");
    if(!OpenProcessToken(process,8,out token)) {
      int error=Marshal.GetLastWin32Error();
      throw new Win32Exception(error,"Current-caller child primary TOKEN_QUERY failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture));
    }
    try {
      // CreateProcessW inherits the calling process's primary token even when
      // its thread impersonates. GetCurrent() observes that effective thread
      // first, so it is not the launch identity being compared here.
      if(!OpenProcessToken(GetCurrentProcess(),8,out caller)) {
        int error=Marshal.GetLastWin32Error();
        throw new Win32Exception(error,"Current-caller process primary TOKEN_QUERY failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture));
      }
      callerOpened=true;
      using(var original=new WindowsIdentity(caller)) using(var child=new WindowsIdentity(token)) { return original.User.Value==child.User.Value; }
    } catch(Exception error) {
      primaryFailure=error; throw;
    } finally {
      var failures=new System.Collections.Generic.List<Exception>();
      if(callerOpened && !CloseHandle(caller)) {
        int error=Marshal.GetLastWin32Error();
        failures.Add(new Win32Exception(error,"Current-caller primary read-only identity token close failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture)));
      }
      if(!CloseHandle(token)) {
        int error=Marshal.GetLastWin32Error();
        failures.Add(new Win32Exception(error,"Current-caller child read-only identity token close failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture)));
      }
      if(failures.Count!=0) {
        if(primaryFailure!=null) failures.Insert(0,primaryFailure);
        throw new AggregateException("Current-caller read-only identity cleanup failed",failures);
      }
    }
  }
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
  [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)]
  static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool present);
  public static bool StartedJobMembership(int pid) {
    IntPtr process;
    if(!StartedProcesses.TryGetValue(pid,out process)) throw new InvalidOperationException("Job observation does not own the retained process");
    bool present;
    bool success=IsProcessInJob(process,IntPtr.Zero,out present);
    int error=Marshal.GetLastWin32Error();
    if(!success) throw new Win32Exception(error,"Owned read-only job membership query failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture));
    return present;
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
  Add-Type -TypeDefinition $normalSource.Insert($position, $comparison + "`n")
}
if (-not [MixelOrdinaryTokenFixture]::Elevated($PID)) { throw 'Owned standard-account qualification requires the elevated isolated runner.' }

if (-not ('MixelIsolatedLeaseControl' -as [type])) { Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class MixelIsolatedLeaseControl {
  static readonly System.Collections.Generic.HashSet<IntPtr> Owned = new System.Collections.Generic.HashSet<IntPtr>();
  static readonly System.Collections.Generic.Dictionary<IntPtr,string> WorldNames = new System.Collections.Generic.Dictionary<IntPtr,string>();
  static readonly System.Collections.Generic.HashSet<IntPtr> DiagnosticDescriptors = new System.Collections.Generic.HashSet<IntPtr>();
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
    Owned.Add(handle);
    return handle;
  }
  public static IntPtr DeniedEvent(string name) { return Event(name,"D:P(A;;GA;;;BA)(A;;GA;;;SY)"); }
  public static IntPtr WorldEvent(string name) {
    IntPtr handle=Event(name,"D:P(A;;0x00100000;;;WD)(A;;GA;;;BA)(A;;GA;;;SY)");
    WorldNames.Add(handle,name);
    return handle;
  }
  // Diagnostic-only public read APIs. The original event's creation, access,
  // ACL and lifetime stay unchanged. This separate handle requests READ_CONTROL
  // only; LABEL_SECURITY_INFORMATION does not request the full audit SACL.
  [DllImport("kernel32.dll", EntryPoint="OpenEventW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
  static extern IntPtr ReadOnlyOpenEvent(uint access, bool inherit, string name);
  [DllImport("advapi32.dll", EntryPoint="GetSecurityInfo", ExactSpelling=true)]
  static extern uint ReadOnlyGetSecurityInfo(IntPtr handle, uint objectType, uint information, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
  [DllImport("advapi32.dll", EntryPoint="GetSecurityDescriptorDacl", ExactSpelling=true, SetLastError=true)]
  static extern bool ReadOnlyGetDacl(IntPtr descriptor, out bool present, out IntPtr acl, out bool defaulted);
  [DllImport("advapi32.dll", EntryPoint="GetSecurityDescriptorSacl", ExactSpelling=true, SetLastError=true)]
  static extern bool ReadOnlyGetSacl(IntPtr descriptor, out bool present, out IntPtr acl, out bool defaulted);
  [DllImport("kernel32.dll", EntryPoint="LocalFree", ExactSpelling=true, SetLastError=true)]
  static extern IntPtr ReadOnlyLocalFree(IntPtr memory);
  public sealed class SafeAceFacts {
    public byte type, flags;
    public uint mask;
    public string trustee;
  }
  public sealed class SafeLabelFacts {
    public byte type, flags;
    public uint policy;
    public uint? integrityRid;
    public string classification;
  }
  public sealed class SafeDescriptorFacts {
    public bool daclPresent, daclNull, labelAclPresent, labelAclNull;
    public SafeAceFacts[] daclAces;
    public SafeLabelFacts[] labels;
  }
  static byte[] ReadOnlyAcl(IntPtr pointer) {
    if(pointer==IntPtr.Zero) return null;
    int size=unchecked((ushort)Marshal.ReadInt16(pointer,2));
    if(size<8) throw new InvalidOperationException("Owned descriptor ACL size is invalid");
    byte[] bytes=new byte[size]; Marshal.Copy(pointer,bytes,0,size);
    if((bytes[0]!=2 && bytes[0]!=4) || BitConverter.ToUInt16(bytes,4)>1024) throw new InvalidOperationException("Owned descriptor ACL header is invalid");
    return bytes;
  }
  static uint[] ReadOnlySid(byte[] bytes,int offset,int end,out ulong authority) {
    if(offset<0 || end>bytes.Length || offset>end-8 || bytes[offset]!=1 || bytes[offset+1]>15 || offset+8+bytes[offset+1]*4>end) throw new InvalidOperationException("Owned descriptor SID bounds are invalid");
    authority=0; for(int i=2;i<8;i++) authority=(authority<<8)|bytes[offset+i];
    uint[] parts=new uint[bytes[offset+1]];
    for(int i=0;i<parts.Length;i++) parts[i]=BitConverter.ToUInt32(bytes,offset+8+i*4);
    return parts;
  }
  static string ReadOnlyTrustee(byte[] bytes,int offset,int end) {
    ulong authority; uint[] parts=ReadOnlySid(bytes,offset,end,out authority);
    if(authority==1 && parts.Length==1 && parts[0]==0) return "World";
    if(authority==5 && parts.Length==1 && parts[0]==18) return "SYSTEM";
    if(authority==5 && parts.Length==2 && parts[0]==32 && parts[1]==544) return "Administrators";
    return "other";
  }
  static int ReadOnlyAceEnd(byte[] bytes,int offset) {
    if(offset<8 || offset>bytes.Length-4) throw new InvalidOperationException("Owned descriptor ACE header bounds are invalid");
    int size=BitConverter.ToUInt16(bytes,offset+2);
    if(size<4 || size>bytes.Length-offset) throw new InvalidOperationException("Owned descriptor ACE content bounds are invalid");
    return offset+size;
  }
  static SafeDescriptorFacts ReadOnlyDescriptorFacts(bool daclPresent,IntPtr dacl,bool saclPresent,IntPtr sacl) {
    // The descriptor getters leave the ACL output invalid when Present is false.
    // Never inspect that output; absent and present-NULL remain distinct facts.
    if(!daclPresent) dacl=IntPtr.Zero;
    if(!saclPresent) sacl=IntPtr.Zero;
    byte[] access=ReadOnlyAcl(dacl), labels=ReadOnlyAcl(sacl);
    var result=new SafeDescriptorFacts { daclPresent=daclPresent, daclNull=dacl==IntPtr.Zero, labelAclPresent=saclPresent, labelAclNull=sacl==IntPtr.Zero };
    var safeAces=new System.Collections.Generic.List<SafeAceFacts>();
    if(access!=null) {
      int offset=8, count=BitConverter.ToUInt16(access,4);
      for(int i=0;i<count;i++) {
        int end=ReadOnlyAceEnd(access,offset);
        var ace=new SafeAceFacts { type=access[offset], flags=access[offset+1], trustee="other" };
        if(ace.type==0 || ace.type==1) {
          if(end-offset<16) throw new InvalidOperationException("Owned descriptor access ACE is truncated");
          ace.mask=BitConverter.ToUInt32(access,offset+4);
          ace.trustee=ReadOnlyTrustee(access,offset+8,end);
        }
        safeAces.Add(ace); offset=end;
      }
    }
    var safeLabels=new System.Collections.Generic.List<SafeLabelFacts>();
    if(labels!=null) {
      int offset=8, count=BitConverter.ToUInt16(labels,4);
      for(int i=0;i<count;i++) {
        int end=ReadOnlyAceEnd(labels,offset);
        var label=new SafeLabelFacts { type=labels[offset], flags=labels[offset+1], classification="other" };
        if(label.type==0x11) {
          if(end-offset<16) throw new InvalidOperationException("Owned descriptor integrity ACE is truncated");
          label.policy=BitConverter.ToUInt32(labels,offset+4);
          ulong authority; uint[] parts=ReadOnlySid(labels,offset+8,end,out authority);
          if(authority==16 && parts.Length==1) { label.integrityRid=parts[0]; label.classification="mandatory-integrity"; }
        }
        safeLabels.Add(label); offset=end;
      }
    }
    result.daclAces=safeAces.ToArray(); result.labels=safeLabels.ToArray();
    return result;
  }
  static void FreeDiagnosticDescriptor(IntPtr descriptor) {
    if(!DiagnosticDescriptors.Contains(descriptor)) throw new InvalidOperationException("Read-only descriptor allocation is not owned");
    IntPtr failed=ReadOnlyLocalFree(descriptor);
    int error=Marshal.GetLastWin32Error();
    if(failed!=IntPtr.Zero) throw new Win32Exception(error,"Owned read-only descriptor free failed; nativeError="+error.ToString(System.Globalization.CultureInfo.InvariantCulture));
    DiagnosticDescriptors.Remove(descriptor);
  }
  public static SafeDescriptorFacts ObserveWorldDescriptor(string name,IntPtr original) {
    Isolated(name); string actual;
    if(!Owned.Contains(original) || !WorldNames.TryGetValue(original,out actual) || actual!=name) throw new InvalidOperationException("Descriptor query does not own this exact retained World event/name");
    IntPtr handle=ReadOnlyOpenEvent(0x20000,false,name);
    int openError=Marshal.GetLastWin32Error();
    if(handle==IntPtr.Zero) throw new Win32Exception(openError,"Owned World event READ_CONTROL open failed; nativeError="+openError.ToString(System.Globalization.CultureInfo.InvariantCulture));
    if(!Owned.Add(handle)) throw new InvalidOperationException("Read-only event query returned an already-owned handle");
    IntPtr descriptor=IntPtr.Zero; Exception primaryFailure=null;
    try {
      IntPtr owner,group,dacl,sacl;
      uint result=ReadOnlyGetSecurityInfo(handle,6,0x14,out owner,out group,out dacl,out sacl,out descriptor);
      if(descriptor!=IntPtr.Zero) DiagnosticDescriptors.Add(descriptor);
      // GetSecurityInfo returns its own DWORD error, not the thread's last error.
      if(result!=0) throw new Win32Exception(unchecked((int)result),"Owned World event DACL/LABEL query failed; nativeError="+result.ToString(System.Globalization.CultureInfo.InvariantCulture));
      if(descriptor==IntPtr.Zero) throw new InvalidOperationException("Owned World event descriptor query returned no allocation");
      // Read the requested label from the returned descriptor, independently
      // of GetSecurityInfo's optional ppSacl output contract for a full SACL.
      bool daclPresent,saclPresent,defaulted;
      bool daclSuccess=ReadOnlyGetDacl(descriptor,out daclPresent,out dacl,out defaulted);
      int daclError=Marshal.GetLastWin32Error();
      if(!daclSuccess) throw new Win32Exception(daclError,"Owned World descriptor DACL read failed; nativeError="+daclError.ToString(System.Globalization.CultureInfo.InvariantCulture));
      bool saclSuccess=ReadOnlyGetSacl(descriptor,out saclPresent,out sacl,out defaulted);
      int saclError=Marshal.GetLastWin32Error();
      if(!saclSuccess) throw new Win32Exception(saclError,"Owned World descriptor label read failed; nativeError="+saclError.ToString(System.Globalization.CultureInfo.InvariantCulture));
      return ReadOnlyDescriptorFacts(daclPresent,dacl,saclPresent,sacl);
    } catch(Exception error) { primaryFailure=error; throw; }
    finally {
      var failures=new System.Collections.Generic.List<Exception>();
      if(descriptor!=IntPtr.Zero) { try { FreeDiagnosticDescriptor(descriptor); } catch(Exception error) { failures.Add(error); } }
      try { Close(handle); } catch(Exception error) { failures.Add(error); }
      if(failures.Count!=0) {
        if(primaryFailure!=null) failures.Insert(0,primaryFailure);
        throw new AggregateException("Owned World event read-only query cleanup failed",failures);
      }
    }
  }
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
    Owned.Add(handle);
    return handle;
  }
  public static void Close(IntPtr handle) {
    if(handle==IntPtr.Zero) return;
    if(!Owned.Contains(handle)) throw new InvalidOperationException("Isolated lease handle is not owned");
    if(!CloseHandle(handle)) throw new Win32Exception(Marshal.GetLastWin32Error());
    Owned.Remove(handle);
    WorldNames.Remove(handle);
  }
  public static void ClosePending() {
    var failures = new System.Collections.Generic.List<Exception>();
    foreach(IntPtr handle in new System.Collections.Generic.List<IntPtr>(Owned)) {
      try { Close(handle); } catch(Exception error) { failures.Add(error); }
    }
    foreach(IntPtr descriptor in new System.Collections.Generic.List<IntPtr>(DiagnosticDescriptors)) {
      try { FreeDiagnosticDescriptor(descriptor); } catch(Exception error) { failures.Add(error); }
    }
    if(failures.Count!=0) throw new AggregateException("Owned isolated lease handle cleanup failed", failures);
  }
}
'@ }

function Write-LeaseContextFailure([ValidateSet('job-membership','job-limits','explicit-directory-access','owned-world-descriptor')][string]$Operation, [Exception]$Failure) {
  $actual = $Failure
  for ($depth = 0; $depth -lt 8 -and $actual -is [Management.Automation.MethodInvocationException] -and $actual.InnerException; $depth++) {
    $actual = $actual.InnerException
  }
  $leaseContextFailures.Add($actual)
  $items = if ($actual -is [AggregateException]) { @($actual.Flatten().InnerExceptions) } else { @($actual) }
  $codes = @($items | Where-Object { $_ -is [ComponentModel.Win32Exception] } | ForEach-Object { $_.NativeErrorCode })
  $safeCodes = if ($codes.Count) { $codes -join ',' } else { 'none' }
  Write-Host "FAIL: source-only read-only lease context observation; operation=$Operation; exceptionType=$($actual.GetType().Name); nativeErrors=$safeCodes; fatalAfterOriginalGate=True; namedDirectoryCauseEstablished=False."
}

function Invoke-LeaseJobObservation([int]$ControlPid) {
  try {
    $inJob = [MixelOrdinaryTokenFixture]::StartedJobMembership($ControlPid)
    Write-Host "OBSERVED: retained owned source child job membership; controlPid=$ControlPid; inAnyJob=$inJob; query=IsProcessInJob(NULL); childJobLimitsMeasured=False; namedDirectoryMeasured=False."
  } catch { Write-LeaseContextFailure job-membership $_.Exception }
}

function Read-StandardChildJobLimits([uint32]$ExpectedPid) {
  $path = Join-Path $fixtureRoot 'fixture-job-limits.json'
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Owned child job-limit observation is missing.' }
  $text = [IO.File]::ReadAllText($path)
  if ($text.Length -gt 4096) { throw 'Owned child job-limit observation exceeds its bound.' }
  # PS5/7 pipeline conversion unwraps singleton JSON arrays. The native
  # publisher emits one object; reject any other root envelope before parsing.
  if (-not $text.TrimStart().StartsWith('{') -or -not $text.TrimEnd().EndsWith('}')) { throw 'Owned child job-limit root must be an object.' }
  $record = $text | ConvertFrom-Json
  $names = @($record.PSObject.Properties.Name | Sort-Object)
  $expected = @('schema','pid','inAnyJob','membershipStatus','membershipError','uiStatus','uiError','uiReturned','uiFlags','extendedStatus','extendedError','extendedReturned','extendedFlags') | Sort-Object
  if (($names -join ',') -cne ($expected -join ',')) { throw 'Owned child job-limit fields changed.' }
  foreach ($name in @('schema','pid','membershipError','uiError','uiReturned','extendedError','extendedReturned')) {
    $value = $record.$name
    if (-not ($value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]) -or
        $value -lt 0 -or $value -gt [uint32]::MaxValue) { throw 'Owned child job-limit integer is not bounded.' }
  }
  if ($record.schema -ne 1 -or $ExpectedPid -eq 0 -or $record.pid -ne $ExpectedPid -or
      $record.membershipStatus -isnot [string] -or $record.membershipStatus -cnotin @('available','error') -or
      ($record.membershipStatus -ceq 'available' -and ($record.membershipError -ne 0 -or $record.inAnyJob -isnot [bool])) -or
      ($record.membershipStatus -ceq 'error' -and $null -ne $record.inAnyJob)) { throw 'Owned child job membership facts are inconsistent.' }
  foreach ($kind in @('ui','extended')) {
    $status = $record.($kind + 'Status'); $errorCode = $record.($kind + 'Error')
    $returned = $record.($kind + 'Returned'); $flags = $record.($kind + 'Flags')
    $length = if ($kind -ceq 'ui') { 4 } else { 144 }
    if ($status -isnot [string]) { throw 'Owned child job-limit status must be a scalar string.' }
    if ($record.membershipStatus -ceq 'error' -or -not $record.inAnyJob) {
      $unused = if ($record.membershipStatus -ceq 'error') { 'membership-error' } else { 'not-in-job' }
      if ($status -cne $unused -or $errorCode -ne 0 -or $returned -ne 0 -or $null -ne $flags) { throw 'Unqueried child job limits contain invented facts.' }
    } else {
      if ($status -cnotin @('available','error','size') -or
          ($status -cne 'error' -and $errorCode -ne 0) -or
          ($status -ceq 'available' -and $returned -ne $length) -or
          ($status -ceq 'size' -and $returned -eq $length)) { throw 'Child job query result/length is inconsistent.' }
      if ($status -ceq 'available') {
        if (-not ($flags -is [int] -or $flags -is [long] -or $flags -is [uint32] -or $flags -is [uint64]) -or
            $flags -lt 0 -or $flags -gt [uint32]::MaxValue) { throw 'Child job flags are not a raw bounded DWORD.' }
      } elseif ($null -ne $flags) { throw 'Failed or short child job query published unverified flags.' }
    }
  }
  return $record
}

function Invoke-LeaseChildJobLimitsObservation([uint32]$ControlPid) {
  try {
    $record = Read-StandardChildJobLimits $ControlPid
    $complete = $record.membershipStatus -ceq 'available' -and $record.inAnyJob -and
        $record.uiStatus -ceq 'available' -and $record.extendedStatus -ceq 'available'
    Write-Host "OBSERVED: owned source child immediate job limits; controlPid=$ControlPid; query=QueryInformationJobObject(NULL); immediateJobOnly=True; completeLimitsMeasured=$complete; namedDirectoryMeasured=False; causeEstablished=False; $($record | ConvertTo-Json -Compress)."
    $failures = [Collections.Generic.List[Exception]]::new()
    if ($record.membershipStatus -ceq 'error') {
      $code = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$record.membershipError),0)
      $failures.Add([ComponentModel.Win32Exception]::new($code,'Child self job membership query failed.'))
    }
    foreach ($kind in @('ui','extended')) {
      if ($record.($kind + 'Status') -ceq 'error') {
        $code = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$record.($kind + 'Error')),0)
        $failures.Add([ComponentModel.Win32Exception]::new($code,"Child self $kind job query failed."))
      } elseif ($record.($kind + 'Status') -ceq 'size') {
        $failures.Add([InvalidOperationException]::new("Child self $kind job query returned a different structure length."))
      }
    }
    if ($failures.Count -gt 0) { throw [AggregateException]::new('Owned child job-limit observation failed.', $failures) }
  } catch { Write-LeaseContextFailure job-limits $_.Exception }
}

function Read-StandardChildDirectoryAccess([uint32]$ExpectedPid) {
  $path = Join-Path $fixtureRoot 'fixture-directory-access.json'
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Owned explicit-directory observation is missing.' }
  $text = [IO.File]::ReadAllText($path)
  if ($text.Length -gt 4096) { throw 'Owned explicit-directory observation exceeds its bound.' }
  if (-not $text.TrimStart().StartsWith('{') -or -not $text.TrimEnd().EndsWith('}')) { throw 'Owned explicit-directory root must be an object.' }
  $record = $text | ConvertFrom-Json
  $expected = @('schema','pid','scope','route','nameCreationAccessMeasured','objectsCreated','sessionStatus','sessionError','sessionId','loaderStatus','loaderError','unloadStatus','unloadError','records') | Sort-Object
  if ((@($record.PSObject.Properties.Name | Sort-Object) -join ',') -cne ($expected -join ',')) { throw 'Owned explicit-directory fields changed.' }
  foreach ($name in @('schema','pid','sessionError','loaderError','unloadError')) {
    $value = $record.$name
    if (-not ($value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]) -or
        $value -lt 0 -or $value -gt [uint32]::MaxValue) { throw 'Owned explicit-directory integer is not bounded.' }
  }
  foreach ($name in @('scope','route','sessionStatus','loaderStatus','unloadStatus')) {
    if ($record.$name -isnot [string]) { throw 'Owned explicit-directory classification must be a scalar string.' }
  }
  if ($record.schema -ne 1 -or $ExpectedPid -eq 0 -or $record.pid -ne $ExpectedPid -or
      $record.scope -cne 'ExplicitDirectoryAccessOnly' -or $record.route -cne 'ActualEventRouteUnmeasured' -or
      $record.nameCreationAccessMeasured -isnot [bool] -or $record.objectsCreated -isnot [bool] -or $record.objectsCreated -or
      $record.sessionStatus -cnotin @('available','error','unqueried') -or
      $record.loaderStatus -cnotin @('available','load-error','export-error','layout-error') -or
      $record.unloadStatus -cnotin @('closed','failed','not-loaded') -or $record.records -isnot [array]) { throw 'Owned explicit-directory identity/scope changed.' }
  if ($record.sessionStatus -ceq 'available') {
    $value = $record.sessionId
    if (-not ($value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]) -or
        $value -lt 0 -or $value -gt [uint32]::MaxValue -or $record.sessionError -ne 0) { throw 'Owned directory session is not an actual bounded DWORD.' }
  } elseif ($null -ne $record.sessionId -or ($record.sessionStatus -ceq 'unqueried' -and $record.sessionError -ne 0)) { throw 'Unmeasured directory session contains invented facts.' }
  if (($record.loaderStatus -ceq 'layout-error') -ne ($record.sessionStatus -ceq 'unqueried') -or
      ($record.loaderStatus -cin @('available','layout-error') -and $record.loaderError -ne 0) -or
      ($record.loaderStatus -cin @('load-error','layout-error') -and ($record.unloadStatus -cne 'not-loaded' -or $record.unloadError -ne 0)) -or
      ($record.loaderStatus -cin @('available','export-error') -and $record.unloadStatus -cnotin @('closed','failed')) -or
      ($record.unloadStatus -cne 'failed' -and $record.unloadError -ne 0)) { throw 'Directory loader ownership facts are inconsistent.' }
  $count = if ($record.loaderStatus -cne 'available') { 0 } elseif ($record.sessionStatus -ceq 'available') { 6 } else { 3 }
  if ($record.records.Count -ne $count -or $record.nameCreationAccessMeasured -ne ($record.loaderStatus -ceq 'available')) { throw 'Directory open attempts are missing, duplicated or invented.' }
  $creationStart = if ($record.sessionStatus -ceq 'available') { 4 } else { 2 }
  for ($i = 0; $i -lt $count; $i++) {
    $item = $record.records[$i]
    $fields = @('directory','path','access','ntStatus','status','handleReturned','closeStatus','closeError') | Sort-Object
    if ((@($item.PSObject.Properties.Name | Sort-Object) -join ',') -cne ($fields -join ',')) { throw 'Directory attempt fields changed.' }
    foreach ($name in @('directory','path','status','closeStatus')) {
      if ($item.$name -isnot [string]) { throw 'Directory attempt classification must be a scalar string.' }
    }
    foreach ($name in @('access','ntStatus','closeError')) {
      $value = $item.$name
      $minimum = if ($name -ceq 'ntStatus') { [int]::MinValue } else { 0 }
      $maximum = if ($name -ceq 'ntStatus') { [int]::MaxValue } else { [uint32]::MaxValue }
      if (-not ($value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]) -or
          $value -lt $minimum -or $value -gt $maximum) { throw 'Directory attempt native integer is not bounded.' }
    }
    $kind = if ($i -lt 2 -or $i -eq $creationStart) { 'global' } else { 'session' }
    $explicitPath = if ($kind -ceq 'global') { '\BaseNamedObjects' } else { "\Sessions\$($record.sessionId)\BaseNamedObjects" }
    $access = if ($i -ge $creationStart) { 4 } else { 1 + ($i % 2) }
    if ($item.directory -cne $kind -or $item.path -cne $explicitPath -or $item.access -ne $access -or
        $item.handleReturned -isnot [bool] -or $item.status -cnotin @('opened','nt-result','contract-error')) { throw 'Explicit directory or independent access-only right changed.' }
    if ($item.ntStatus -eq 0 -and $item.handleReturned) {
      if ($item.status -cne 'opened' -or $item.closeStatus -cnotin @('closed','failed') -or
          ($item.closeStatus -ceq 'closed' -and $item.closeError -ne 0)) { throw 'Owned directory handle close facts changed.' }
    } else {
      $status = if ($item.ntStatus -ge 0 -or $item.handleReturned) { 'contract-error' } else { 'nt-result' }
      if ($item.status -cne $status -or $item.closeStatus -cne 'not-opened' -or $item.closeError -ne 0) { throw 'Unowned directory handle was closed or NTSTATUS was reclassified.' }
    }
  }
  return $record
}

function Invoke-LeaseChildDirectoryAccessObservation([uint32]$ControlPid) {
  try {
    $record = Read-StandardChildDirectoryAccess $ControlPid
    Write-Host "OBSERVED: owned source child explicit object directory access; controlPid=$ControlPid; ExplicitDirectoryAccessOnly=True; ActualEventRouteUnmeasured=True; nameCreationAccessMeasured=$($record.nameCreationAccessMeasured); objectsCreated=False; causeEstablished=False; $($record | ConvertTo-Json -Depth 5 -Compress)."
    $failures = [Collections.Generic.List[Exception]]::new()
    foreach ($kind in @('session','loader','unload')) {
      if ($record.($kind + 'Status') -cin @('error','load-error','export-error','failed')) {
        $code = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$record.($kind + 'Error')),0)
        $failures.Add([ComponentModel.Win32Exception]::new($code,"Child self directory $kind observation failed."))
      }
    }
    if ($record.loaderStatus -ceq 'layout-error') { $failures.Add([InvalidOperationException]::new('Directory diagnostic x64 layout is unavailable.')) }
    foreach ($item in $record.records) {
      if ($item.status -ceq 'contract-error') { $failures.Add([InvalidOperationException]::new("Directory open returned an unexpected status/output contract; rawNTSTATUS=$($item.ntStatus).")) }
      if ($item.closeStatus -ceq 'failed') {
        $code = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$item.closeError),0)
        $failures.Add([ComponentModel.Win32Exception]::new($code,'Owned directory handle close failed.'))
      }
    }
    # Access-denied/not-found NTSTATUS values are measured results, not
    # invented Win32 errors or proof about CreateEvent's actual namespace.
    if ($failures.Count -gt 0) { throw [AggregateException]::new('Owned explicit-directory observation failed.', $failures) }
  } catch { Write-LeaseContextFailure explicit-directory-access $_.Exception }
}

function Invoke-LeaseWorldDescriptorObservation([string]$Name, [IntPtr]$Handle, [string]$Namespace) {
  try {
    if ($Namespace -cnotin @('bare','local','global')) { throw 'Unsafe descriptor namespace classification.' }
    $facts = [MixelIsolatedLeaseControl]::ObserveWorldDescriptor($Name,$Handle)
    Write-Host "OBSERVED: exact retained isolated World event descriptor; namespace=$Namespace; extraAccess=READ_CONTROL; information=DACL+LABEL; namedDirectoryMeasured=False; $($facts | ConvertTo-Json -Depth 5 -Compress)."
  } catch { Write-LeaseContextFailure owned-world-descriptor $_.Exception }
}

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


function Read-StandardCallingThreadTokens([uint32]$ExpectedPid, [switch]$Unnamed) {
  $filename = if ($Unnamed) { 'fixture-thread-token-unnamed.json' } else { 'fixture-thread-token-initial.json' }
  $path = Join-Path $fixtureRoot $filename
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Owned calling-thread token result is missing.' }
  $text = [IO.File]::ReadAllText($path)
  if ($text.Length -gt 32768) { throw 'Owned calling-thread token result exceeds its bound.' }
  $record = $text | ConvertFrom-Json
  foreach ($field in @('schema','pid')) {
    $value = $record.$field
    $numeric = $value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]
    if (-not $numeric -or $value -lt 1 -or $value -gt [uint32]::MaxValue) { throw 'Owned calling-thread token metadata is not a bounded integer.' }
  }
  if ((@($record.PSObject.Properties.Name | Sort-Object) -join ',') -cne 'pid,records,schema' -or
      $record.schema -ne 1 -or $record.pid -ne $ExpectedPid -or $ExpectedPid -eq 0 -or
      $record.records -isnot [array] -or $record.records.Count -lt 1 -or $record.records.Count -gt 32) {
    throw 'Owned calling-thread token result has invalid schema or retained PID.'
  }
  $expected = @('phase','tid','threadStatus','threadError','threadType','threadImpersonationLevel','primaryStatus','primaryError','primaryType','threadLogonMatchesPrimary','threadClose','threadCloseError','primaryClose','primaryCloseError') | Sort-Object
  $tid = $null
  foreach ($item in $record.records) {
    foreach ($field in @('phase','threadStatus','primaryStatus','threadClose','primaryClose')) {
      if ($item.$field -isnot [string]) { throw 'Owned calling-thread token classification is not a scalar string.' }
    }
    if ((@($item.PSObject.Properties.Name | Sort-Object) -join ',') -cne ($expected -join ',') -or
        $item.phase -cnotin @('CreateEventExW','CreateEventExW-unnamed','OpenEventW') -or
        $item.threadStatus -cnotin @('available','absent','open-error','statistics-error','statistics-size') -or
        $item.primaryStatus -cnotin @('available','open-error','statistics-error','statistics-size') -or
        $item.threadClose -cnotin @('closed','not-opened','failed') -or $item.primaryClose -cnotin @('closed','not-opened','failed')) {
      throw 'Owned calling-thread token observation contains an unknown field or classification.'
    }
    foreach ($field in @('tid','threadError','threadType','threadImpersonationLevel','primaryError','primaryType','threadCloseError','primaryCloseError')) {
      $value = $item.$field
      $numeric = $value -is [int] -or $value -is [long] -or $value -is [uint32] -or $value -is [uint64]
      if (-not $numeric -or $value -lt 0 -or $value -gt [uint32]::MaxValue) { throw 'Owned calling-thread token native value is not a bounded integer.' }
    }
    if ($item.tid -eq 0 -or ($null -ne $tid -and $item.tid -ne $tid)) { throw 'Source fixture event calls were not observed on the same actual calling thread.' }
    $tid = $item.tid
    $available = $item.threadStatus -ceq 'available' -and $item.primaryStatus -ceq 'available'
    if (($available -and $item.threadLogonMatchesPrimary -isnot [bool]) -or
        (-not $available -and $null -ne $item.threadLogonMatchesPrimary) -or
        ($item.threadStatus -ceq 'available' -and ($item.threadType -ne 2 -or $item.threadImpersonationLevel -gt 3 -or $item.threadError -ne 0)) -or
        ($item.primaryStatus -ceq 'available' -and ($item.primaryType -ne 1 -or $item.primaryError -ne 0)) -or
        ($item.threadStatus -cne 'available' -and ($item.threadType -ne 0 -or $item.threadImpersonationLevel -ne 0)) -or
        ($item.primaryStatus -cne 'available' -and $item.primaryType -ne 0) -or
        ($item.threadStatus -ceq 'absent' -and ($item.threadError -ne 1008 -or $item.threadClose -cne 'not-opened'))) {
      throw 'Owned calling-thread token observation contains inconsistent identity facts.'
    }
    foreach ($subject in @('thread','primary')) {
      $opened = $item.("${subject}Status") -cin @('available','statistics-error','statistics-size')
      $close = $item.("${subject}Close")
      if (($opened -and $close -cnotin @('closed','failed')) -or
          (-not $opened -and $close -cne 'not-opened') -or
          ($close -cne 'failed' -and $item.("${subject}CloseError") -ne 0)) {
        throw 'Owned calling-thread token observation contains inconsistent handle cleanup.'
      }
    }
  }
  $unnamedCalls = @($record.records | Where-Object { $_.phase -ceq 'CreateEventExW-unnamed' }).Count
  $expectedUnnamedCalls = if ($Unnamed) { 1 } else { 0 }
  if ($unnamedCalls -ne $expectedUnnamedCalls) { throw 'Owned calling-thread token result does not match the requested publication stage.' }
  # All serialized fields are canonical classifications or bounded numeric
  # facts. SID, LUID, token ID, credentials and raw arguments are absent.
  return $record
}

function Read-StandardFixtureResult([string]$Path, [DateTime]$Deadline) {
  $sharingFailure = $null
  while ($true) {
    if ($null -ne $sharingFailure -and [DateTime]::UtcNow -ge $Deadline) { throw $sharingFailure }
    try { return [IO.File]::ReadAllText($Path) } catch {
      $failure = $_.Exception
      while ($failure -isnot [IO.IOException] -and $null -ne $failure.InnerException) {
        $failure = $failure.InnerException
      }
      # Reuse the original readiness deadline. Only the observed Windows
      # sharing violation may be transient; every other read failure is fatal.
      if ($failure -isnot [IO.IOException] -or $failure.HResult -ne -2147024864) { throw }
      if ($null -eq $sharingFailure) { $sharingFailure = $_ }
      if ([DateTime]::UtcNow -ge $Deadline) { throw $sharingFailure }
      Start-Sleep -Milliseconds 20
    }
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
  foreach ($filename in @('fixture-thread-token-initial.json','fixture-thread-token-unnamed.json','fixture-job-limits.json','fixture-directory-access.json')) {
    $threadResult = Join-Path $fixtureRoot $filename
    if (Test-Path -LiteralPath $threadResult) { Remove-Item -LiteralPath $threadResult }
  }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-thread-token.request'), 'observe', [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-job-limits.request'), 'observe', [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-directory-access.request'), 'observe', [Text.UTF8Encoding]::new($false))
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
    $text = Read-StandardFixtureResult $resultPath $deadline
    if ($text.Length -gt 4096) { throw 'Owned lease result exceeds its bound.' }
    $record = $text | ConvertFrom-Json
    Assert-StandardLeaseSafeResult $record $controlPid
    $threadTokens = Read-StandardCallingThreadTokens $controlPid
    Write-Host "OBSERVED: owned child calling-thread token immediately before native event APIs; controlPid=$controlPid; mode=$Mode; $($threadTokens | ConvertTo-Json -Depth 4 -Compress)."
    Invoke-LeaseJobObservation $controlPid
    Invoke-LeaseChildJobLimitsObservation $controlPid
    Invoke-LeaseChildDirectoryAccessObservation $controlPid
    if (-not $ObserveOnly -and ($record.sourceStatus -cne $ExpectedStatus -or
        $record.rawOpenError -ne $ExpectedError -or $record.attestation -cne $ExpectedProof -or
        $record.requiresClick -isnot [bool] -or $record.requiresClick -ne $ExpectedRequired -or
        $record.ownerFailed -isnot [bool] -or $record.ownerFailed -ne $ExpectedOwnerFailed)) {
      throw ('Actual standard-account source lease classification mismatch: ' + ($record | ConvertTo-Json -Compress))
    }
    if (@($threadTokens.records | Where-Object { $_.threadClose -ceq 'failed' -or $_.primaryClose -ceq 'failed' }).Count -gt 0) {
      throw 'Owned read-only calling-thread token handle cleanup failed; native process cleanup is required.'
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
      $threadTokens = Read-StandardCallingThreadTokens $controlPid -Unnamed
      Write-Host "OBSERVED: owned child calling-thread token including exact unnamed event API; controlPid=$controlPid; $($threadTokens | ConvertTo-Json -Depth 4 -Compress)."
      Write-Host "OBSERVED: standard-token unnamed event control; launcher=$Launcher; subsystem=$Subsystem; controlPid=$controlPid; ordinarySessionId=$actualSession; ownedSidMatched=$sidMatches; elevated=$elevated; phase=$($unnamed.phase); created=$($unnamed.created); createError=$($unnamed.createError); closed=$($unnamed.closed); closeError=$($unnamed.closeError); sourceStatusAfter=$($unnamed.sourceStatusAfter); rawOpenErrorAfter=$($unnamed.rawOpenErrorAfter); guardEmptyAfter=$($unnamed.attestationAfter -ceq ''); guardConfirmedAfter=$($unnamed.attestationAfter -ceq 'attended-runtime-v2'); requiresClickAfter=$($unnamed.requiresClickAfter)."
      Assert-StandardUnnamedEventControl $unnamed $record
      if (@($threadTokens.records | Where-Object { $_.threadClose -ceq 'failed' -or $_.primaryClose -ceq 'failed' }).Count -gt 0) {
        throw 'Owned read-only calling-thread token handle cleanup failed; native process cleanup is required.'
      }
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

function Invoke-CurrentCallerLeaseControl([ValidateSet('probe','owner')][string]$Mode, [string]$ExpectedStatus,
    [int]$ExpectedError, [string]$ExpectedProof, [bool]$ExpectedRequired, [switch]$KeepAlive) {
  if ([IO.Path]::GetFileName($executable) -cne 'lease-standard.exe') { throw 'Current-caller comparison requires the unchanged base console source fixture.' }
  foreach ($filename in @('fixture-result.json','fixture-thread-token-initial.json','fixture-thread-token-unnamed.json','fixture-unnamed.request','fixture-unnamed-result.json','fixture-job-limits.json','fixture-directory-access.json')) {
    $path = Join-Path $fixtureRoot $filename
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
  }
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-thread-token.request'), 'observe', [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-job-limits.request'), 'observe', [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-directory-access.request'), 'observe', [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $fixtureRoot 'fixture-mode.txt'), $Mode, [Text.UTF8Encoding]::new($false))
  $controlPid = [MixelOrdinaryTokenFixture]::StartCurrentCallerNative($executable)
  $control = $null; $caller = $null
  try {
    $control = Get-Process -Id $controlPid
    $caller = Get-Process -Id $PID
    $sidMatches = [MixelOrdinaryTokenFixture]::StartedCurrentCallerSidMatches($controlPid)
    $elevated = [MixelOrdinaryTokenFixture]::Elevated($controlPid)
    $callerElevated = [MixelOrdinaryTokenFixture]::Elevated($PID)
    $actualSession = $control.SessionId
    $tokenFacts = [MixelOrdinaryTokenFixture]::StartedTokenFacts($controlPid)
    if (-not $sidMatches -or $elevated -ne $callerElevated -or $actualSession -ne $caller.SessionId -or
        -not $tokenFacts.sessionKnown -or $tokenFacts.sessionId -ne $actualSession -or
        $tokenFacts.logonComparison -cne 'tokenLogonSidCount=1;tokenLogonSidMatchesCaller=true;tokenLogonSidQuerySubject=none;tokenLogonSidQueryFailure=none;tokenLogonSidQueryError=none;tokenLogonSidQueryReturnedLength=none') {
      throw 'Direct current-caller source child did not preserve the actual caller SID/elevation/session/logon identity.'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $resultPath -PathType Leaf) -and [DateTime]::UtcNow -lt $deadline) {
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid).StartsWith('exited:')) { break }
      Start-Sleep -Milliseconds 50
    }
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw 'Direct current-caller source child did not produce its bounded native result.' }
    $text = Read-StandardFixtureResult $resultPath $deadline
    if ($text.Length -gt 4096) { throw 'Direct current-caller native result exceeds its bound.' }
    $record = $text | ConvertFrom-Json
    Assert-StandardLeaseSafeResult $record $controlPid
    $threadTokens = Read-StandardCallingThreadTokens $controlPid
    Write-Host "OBSERVED: direct current Actions caller GUID functional control; tokenContext=unchanged-current-caller; genuineStandardQualified=False; fullEightQualified=False; customerAppQualified=False; mode=$Mode; controlPid=$controlPid; processSessionId=$actualSession; ownedCallerSidMatched=$sidMatches; elevated=$elevated; callerElevated=$callerElevated; $tokenFacts; $($record | ConvertTo-Json -Compress)."
    Write-Host "OBSERVED: direct current Actions caller pre-API calling-thread tokens; controlPid=$controlPid; mode=$Mode; $($threadTokens | ConvertTo-Json -Depth 4 -Compress)."
    Invoke-LeaseJobObservation $controlPid
    Invoke-LeaseChildJobLimitsObservation $controlPid
    Invoke-LeaseChildDirectoryAccessObservation $controlPid
    if ($record.sourceStatus -cne $ExpectedStatus -or $record.rawOpenError -ne $ExpectedError -or
        $record.attestation -cne $ExpectedProof -or $record.requiresClick -ne $ExpectedRequired -or $record.ownerFailed) {
      throw ('Actual current-caller source lease classification mismatch: ' + ($record | ConvertTo-Json -Compress))
    }
    $failedTokenCloses = @($threadTokens.records | Where-Object { $_.threadClose -ceq 'failed' -or $_.primaryClose -ceq 'failed' })
    if ($failedTokenCloses.Count -gt 0) { throw 'Direct current-caller read-only token handle cleanup failed.' }
    if ($KeepAlive) {
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'still-running') { throw 'Direct current-caller source owner exited before its fresh observer.' }
    } else {
      $releasePath = Join-Path $fixtureRoot "fixture-release-$controlPid"
      [IO.File]::WriteAllText($releasePath, 'verified')
      $deadline = [DateTime]::UtcNow.AddSeconds(10)
      while ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -ceq 'still-running' -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 50 }
      if ([MixelOrdinaryTokenFixture]::StartedStatus($controlPid) -cne 'exited:0x00000000') { throw 'Direct current-caller source child did not exit successfully after acknowledgment.' }
      # The same base directory is reused by the original standard terminal.
      # A later reused PID must not inherit an earlier caller acknowledgment.
      # The exact source child consumes this acknowledgment before exit. Only
      # remove a residual file; cleanup errors for an existing file stay fatal.
      if (Test-Path -LiteralPath $releasePath) { Remove-Item -LiteralPath $releasePath }
    }
    return $controlPid
  } finally {
    try { if ($control) { $control.Dispose() } }
    finally { if ($caller) { $caller.Dispose() } }
  }
}

function Invoke-CurrentCallerLeaseFour {
  # The original terminal later uses this same executable and Global GUID.
  # A passing caller comparison remains diagnostic, never standard/app proof.
  $absent = 'ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
  $active = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=false'
  $activeOwner = 'ownerFailed=false;ownerError=none;kernel=active;probeError=none;memoryActive=true'
  try {
    [void](Invoke-CurrentCallerLeaseControl probe $absent 2 '' $false)
    $ownerPid = Invoke-CurrentCallerLeaseControl owner $activeOwner 0 'attended-runtime-v2' $true -KeepAlive
    $observerPid = Invoke-CurrentCallerLeaseControl probe $active 0 'attended-runtime-v2' $true
    if ($observerPid -eq $ownerPid) { throw 'Current-caller owner and fresh observer are not distinct retained processes.' }
    [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()
    if ([MixelOrdinaryTokenFixture]::StartedStatus($ownerPid) -cne 'exited:0x00000001') { throw 'Exact retained current-caller owner did not observe its termination.' }
    [MixelOrdinaryTokenFixture]::CloseStartedObservations()
    [void](Invoke-CurrentCallerLeaseControl probe $absent 2 '' $false)
    Write-Host 'PASS: diagnostic-only current Actions caller executes unchanged source/GUID absent2, own/live0 and confirmed owner-drop/absent2; genuineStandardQualified=False; fullEightQualified=False; customerAppQualified=False.'
  } finally {
    try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
    finally { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }
  }
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
        try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
        finally { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -Subsystem $subsystem -ObserveOnly -ObservationLabel "global-$subsystem-after-confirmed-owner-request-process-termination")
      } finally {
        try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
        finally { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }
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
        try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
        finally { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-after-confirmed-owner-request-process-termination")
        $matrixHandle=[MixelIsolatedLeaseControl]::WorldEvent($endpoint)
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-existing-world-synchronize-event")
        Invoke-LeaseWorldDescriptorObservation $endpoint $matrixHandle $namespace
        [MixelIsolatedLeaseControl]::Close($matrixHandle)
        $matrixHandle=[IntPtr]::Zero
        [void](Invoke-StandardLeaseControl -Mode probe -Launcher $Launcher -ObserveOnly -ObservationLabel "$namespace-after-confirmed-world-event-close")
      } finally {
        try {
          if ($matrixHandle -ne [IntPtr]::Zero) { [MixelIsolatedLeaseControl]::Close($matrixHandle); $matrixHandle=[IntPtr]::Zero }
        } finally {
          try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }
          finally { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }
        }
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
$desktopAccess = $null; $profileStarted = $false; $eventHandle = [IntPtr]::Zero; $failure = $null
$leaseContextFailures = [System.Collections.Generic.List[Exception]]::new()
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
  $currentCallerFailure = $null
  try { Invoke-CurrentCallerLeaseFour }
  catch {
    $currentCallerFailure = $_
    Write-Host ('FAIL: direct current-caller diagnostic remains fatal after the unchanged standard controls: ' + $_.Exception.Message)
  }
  Invoke-StandardLeaseSubsystemObservations with-logon
  Invoke-StandardLeaseSubsystemObservations with-token
  Invoke-StandardLeaseNamespaceObservations with-logon
  Invoke-StandardLeaseNamespaceObservations with-token
  # Supplemental current-caller UAC linked-token observations cannot satisfy
  # the disposable pure standard-account acceptance that follows unchanged.
  Invoke-OptionalCallerLinkedLeaseSubsystemObservations
  $absent = 'ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false'
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
  if ($currentCallerFailure) { throw $currentCallerFailure }
  if ($leaseContextFailures.Count -gt 0) {
    throw [AggregateException]::new('Read-only source lease context observation failed after the unchanged original gate.', $leaseContextFailures)
  }
} catch {
  $failure = $_
  throw
} finally {
  $cleanup = [System.Collections.Generic.List[string]]::new()
  if ($eventHandle -ne [IntPtr]::Zero) { try { [MixelIsolatedLeaseControl]::Close($eventHandle) } catch { $cleanup.Add('Owned isolated event handle cleanup failed.') } }
  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() } catch { $cleanup.Add('Exact owned standard lease process cleanup failed.') }
  try { [MixelOrdinaryTokenFixture]::CloseStartedObservations() } catch { $cleanup.Add('Exact owned standard lease handle close failed.') }
  try { [MixelIsolatedLeaseControl]::ClosePending() } catch { $cleanup.Add('Owned retained isolated lease handle cleanup failed.') }
  if ($desktopAccess) { try { $desktopAccess.Dispose() } catch { $cleanup.Add('Owned standard lease desktop ACL restoration failed.') } }
  try { if ([MixelOrdinaryTokenFixture]::DesktopAclHashes() -cne $originalDesktop) { $cleanup.Add('Owned standard lease desktop ACL hashes changed.') } }
  catch { $cleanup.Add('Owned standard lease desktop ACL hash verification failed.') }
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
