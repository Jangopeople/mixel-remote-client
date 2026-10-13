  // Test-only read-only facts, inserted into the exact owned-process fixture.
  // The fatal desktop predicate remains authoritative. No SID is serialized.
  public sealed class OwnedDesktopPrimaryFacts {
    public int pid;
    public bool retainedPidMatched;
    public string status="unavailable", operation="retained-process";
    public string failureType;
    public int? nativeError, tokenCloseError;
    public bool? ownedSidMatched, elevated;
    public uint? tokenSessionId;
    public bool tokenOpened, tokenCloseAttempted, tokenClosed;
  }
  public static OwnedDesktopPrimaryFacts ObserveOwnedDesktopPrimary(int pid,string expectedSid) {
    var facts=new OwnedDesktopPrimaryFacts { pid=pid };
    IntPtr process,token=IntPtr.Zero,data=IntPtr.Zero;
    if(!StartedProcesses.TryGetValue(pid,out process)) { facts.status="unowned";return facts; }
    try {
      uint retainedPid=GetProcessId(process);
      int pidError=retainedPid==0 ? Marshal.GetLastWin32Error() : 0;
      facts.retainedPidMatched=retainedPid==(uint)pid;
      if(!facts.retainedPidMatched) {
        facts.status="retained-pid-mismatch";
        if(retainedPid==0) facts.nativeError=pidError;
        return facts;
      }
      facts.operation="OpenProcessToken-TOKEN_QUERY";
      bool opened=OpenProcessToken(process,8,out token);
      int openError=opened ? 0 : Marshal.GetLastWin32Error();
      if(!opened) { facts.nativeError=openError;return facts; }
      facts.tokenOpened=true;
      data=Marshal.AllocHGlobal(4);
      foreach(int kind in new[] {20,12}) {
        facts.operation=kind==20 ? "TokenElevation" : "TokenSessionId";
        int required;
        bool success=GetTokenInformation(token,kind,data,4,out required);
        int error=success ? 0 : Marshal.GetLastWin32Error();
        if(!success) { facts.nativeError=error;return facts; }
        if(required!=4) { facts.status="returned-size";return facts; }
        if(kind==20) facts.elevated=Marshal.ReadInt32(data)!=0;
        else facts.tokenSessionId=unchecked((uint)Marshal.ReadInt32(data));
      }
      facts.operation="primary-token-owned-SID-comparison";
      using(var identity=new WindowsIdentity(token)) {
        facts.ownedSidMatched=identity.User.Value==expectedSid;
      }
      facts.operation="complete";facts.status="available";
    } catch(Exception error) {
      facts.failureType=error.GetType().FullName;
      var native=error as Win32Exception;
      if(native!=null) facts.nativeError=native.NativeErrorCode;
    } finally {
      if(data!=IntPtr.Zero) Marshal.FreeHGlobal(data);
      if(facts.tokenOpened) {
        facts.tokenCloseAttempted=true;
        bool closed=CloseHandle(token);
        int closeError=closed ? 0 : Marshal.GetLastWin32Error();
        facts.tokenClosed=closed;
        if(!closed) facts.tokenCloseError=closeError;
      }
    }
    return facts;
  }
