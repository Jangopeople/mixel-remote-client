using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

// Test-only observation of one existing, identity-bound World event.
// It does not replace or satisfy the original native Win32 lease gate.
public static class MixelNtEventBinding {
  [StructLayout(LayoutKind.Sequential)] public struct UnicodeString {
    public ushort Length,MaximumLength; public IntPtr Buffer;
  }
  [StructLayout(LayoutKind.Sequential)] public struct ObjectAttributes {
    public uint Length; public IntPtr RootDirectory,ObjectName;
    public uint Attributes; public IntPtr SecurityDescriptor,SecurityQualityOfService;
  }
  [UnmanagedFunctionPointer(CallingConvention.Winapi)]
  public delegate int NtOpenEvent(ref IntPtr handle,uint access,ref ObjectAttributes attributes);
  [UnmanagedFunctionPointer(CallingConvention.Winapi,SetLastError=true)]
  [return:MarshalAs(UnmanagedType.Bool)]
  public delegate bool CompareObjectHandles(IntPtr first,IntPtr second);
  public class KernelApi {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)] static extern uint GetSystemDirectoryW(StringBuilder path,uint capacity);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true,SetLastError=true)] static extern IntPtr LoadLibraryExW(string path,IntPtr file,uint flags);
    [DllImport("kernel32.dll",CharSet=CharSet.Ansi,ExactSpelling=true,SetLastError=true)] static extern IntPtr GetProcAddress(IntPtr module,string name);
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern bool FreeLibrary(IntPtr module);
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern uint GetProcessId(IntPtr process);
    [DllImport("kernel32.dll",ExactSpelling=true)] static extern uint GetCurrentProcessId();
    public virtual uint SystemDirectory(StringBuilder path,uint capacity,out int error) { uint size=GetSystemDirectoryW(path,capacity);error=size==0?Marshal.GetLastWin32Error():0;return size; }
    public virtual IntPtr Load(string path,out int error) { IntPtr value=LoadLibraryExW(path,IntPtr.Zero,0x800);error=value==IntPtr.Zero?Marshal.GetLastWin32Error():0;return value; }
    public virtual IntPtr Resolve(IntPtr module,string name,out int error) { IntPtr value=GetProcAddress(module,name);error=value==IntPtr.Zero?Marshal.GetLastWin32Error():0;return value; }
    public virtual NtOpenEvent BindOpen(IntPtr entry) { return (NtOpenEvent)Marshal.GetDelegateForFunctionPointer(entry,typeof(NtOpenEvent)); }
    public virtual CompareObjectHandles BindCompare(IntPtr entry) { return (CompareObjectHandles)Marshal.GetDelegateForFunctionPointer(entry,typeof(CompareObjectHandles)); }
    public virtual int LastError() { return Marshal.GetLastWin32Error(); }
    public virtual bool FreeModule(IntPtr module,out int error) { bool ok=FreeLibrary(module);error=ok?0:Marshal.GetLastWin32Error();return ok; }
    public virtual bool Close(IntPtr handle,out int error) { bool ok=CloseHandle(handle);error=ok?0:Marshal.GetLastWin32Error();return ok; }
    public virtual uint ProcessId(IntPtr process,out int error) { uint pid=GetProcessId(process);error=pid==0?Marshal.GetLastWin32Error():0;return pid; }
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern uint WaitForSingleObject(IntPtr process,uint milliseconds);
    public virtual uint WaitProcessZero(IntPtr process,out int error) {uint result=WaitForSingleObject(process,0);error=result==0xffffffff?Marshal.GetLastWin32Error():0;return result;}
    public virtual uint CurrentPid() { return GetCurrentProcessId(); }
    public virtual IntPtr AllocateName(int bytes) { return Marshal.AllocHGlobal(bytes); }
    public virtual void FreeName(IntPtr value) { Marshal.FreeHGlobal(value); }
  }
  public sealed class ModuleFacts {
    public string name,load="not-attempted",resolve="not-attempted",release="not-acquired";
    public int? loadError,resolveError,releaseError;
  }
  public sealed class Facts {
    public int schema=1;public string phase,nonce,targetHash;
    public uint pid,requestedAccess=0x100000;
    public string systemDirectory="not-attempted",open="not-attempted",close="not-owned",problem;
    public int? systemError,firstWin32Error,compareError,closeError,ntStatus,processIdentityError;
    public uint? observedProcessPid,observedProcessWaitResult;public bool? observedProcessRunning;
    public bool? sameObject;public bool identityBound,bindingHeld,cleanupOkay=true;
    public bool actualEventRouteMeasured=false,effectiveAccessCalculated=false,objectsCreated=false,causeEstablished=false;
    public ModuleFacts[] modules=new ModuleFacts[] {new ModuleFacts{name="ntdll.dll"},new ModuleFacts{name="Kernelbase.dll"}};
  }
  // The private target is an input to the owned test transport, never an
  // output fact. Hash/nonce equality is not an authorization capability.
  public sealed class Request {
    public string phase="ChildSelfRequest",candidate,nonce,targetHash;
  }
  private sealed class NativeSession {
    internal readonly KernelApi api;internal readonly Facts facts;
    private readonly IntPtr[] modules=new IntPtr[2];
    private NtOpenEvent open;private CompareObjectHandles compare;
    private bool acquired,released;
    internal NativeSession(KernelApi api,Facts facts) {this.api=api;this.facts=facts;}
    private void Error(int code) {if(facts.firstWin32Error==null)facts.firstWin32Error=code;}
    internal bool Acquire() {
      if(acquired||released)throw new InvalidOperationException("Native session already used");acquired=true;
      StringBuilder path=new StringBuilder(32768);int error;
      uint size=api.SystemDirectory(path,32768,out error);facts.systemError=error;
      if(size==0){facts.systemDirectory="error";Error(error);return false;}
      if(size>=32768||path.Length!=(int)size||!System.IO.Path.IsPathRooted(path.ToString())){facts.systemDirectory="invalid-output";return false;}
      facts.systemDirectory="available";
      for(int i=0;i<2;i++) {
        ModuleFacts row=facts.modules[i];IntPtr module=api.Load(System.IO.Path.Combine(path.ToString(),row.name),out error);row.loadError=error;
        if(module==IntPtr.Zero){row.load="error";Error(error);return false;}
        if(module.ToInt64()==-1){row.load="invalid-output";return false;}
        modules[i]=module;row.load="acquired";
        string symbol=i==0?"NtOpenEvent":"CompareObjectHandles";IntPtr entry=api.Resolve(module,symbol,out error);row.resolveError=error;
        if(entry==IntPtr.Zero){row.resolve="error";Error(error);return false;}
        if(entry.ToInt64()==-1){row.resolve="invalid-output";return false;}
        if(i==0)open=api.BindOpen(entry);else compare=api.BindCompare(entry);
        row.resolve="available";
      }
      return true;
    }
    internal int Open(string target,ref IntPtr handle,out int? observedStatus) {
      observedStatus=null;
      if(released||open==null)throw new InvalidOperationException("Native open unavailable");
      IntPtr text=IntPtr.Zero,name=IntPtr.Zero;
      try {
        byte[] bytes=Encoding.Unicode.GetBytes(target);text=api.AllocateName(bytes.Length+2);
        Marshal.Copy(bytes,0,text,bytes.Length);Marshal.WriteInt16(text,bytes.Length,0);
        name=api.AllocateName(16);var value=new UnicodeString{Length=(ushort)bytes.Length,MaximumLength=(ushort)(bytes.Length+2),Buffer=text};Marshal.StructureToPtr(value,name,false);
        var attributes=new ObjectAttributes{Length=48,RootDirectory=IntPtr.Zero,ObjectName=name,Attributes=0x40,SecurityDescriptor=IntPtr.Zero,SecurityQualityOfService=IntPtr.Zero};
        handle=IntPtr.Zero;int status=open(ref handle,0x100000,ref attributes);observedStatus=status;return status;
      } finally {try{if(name!=IntPtr.Zero)api.FreeName(name);}finally{if(text!=IntPtr.Zero)api.FreeName(text);}}
    }
    internal bool Same(IntPtr first,IntPtr second) {
      if(released||compare==null)throw new InvalidOperationException("Native comparison unavailable");
      bool same=compare(first,second);int error=same?0:api.LastError();facts.compareError=error;if(!same)Error(error);return same;
    }
    internal void Close(IntPtr handle) {
      try{int error;bool ok=api.Close(handle,out error);facts.close=ok?"closed":"failed";facts.closeError=error;if(!ok)Error(error);}
      catch(Exception){facts.close="exception";}
      if(facts.close!="closed")facts.cleanupOkay=false;
    }
    internal void Release() {
      if(released)return;released=true;
      // A failure cannot stop the other independently acquired reference's
      // release. Failed releases remain unresolved facts; there is no retry.
      for(int i=1;i>=0;i--) {
        if(modules[i]==IntPtr.Zero)continue;ModuleFacts row=facts.modules[i];
        try{int error;bool ok=api.FreeModule(modules[i],out error);row.release=ok?"released":"failed";row.releaseError=error;if(ok)modules[i]=IntPtr.Zero;else Error(error);}
        catch(Exception){row.release="exception";}
        if(row.release!="released")facts.cleanupOkay=false;
      }
    }
  }
  private static void Layout() {
    if(IntPtr.Size!=8||Marshal.SizeOf(typeof(UnicodeString))!=16||Marshal.SizeOf(typeof(ObjectAttributes))!=48||Marshal.OffsetOf(typeof(UnicodeString),"Buffer").ToInt32()!=8||Marshal.OffsetOf(typeof(ObjectAttributes),"ObjectName").ToInt32()!=16||Marshal.OffsetOf(typeof(ObjectAttributes),"Attributes").ToInt32()!=24)throw new InvalidOperationException("Exact x64 layouts required");
  }
  private static string Hash(string target) {using(var d=SHA256.Create())return BitConverter.ToString(d.ComputeHash(Encoding.Unicode.GetBytes(target))).Replace("-","").ToLowerInvariant();}
  private static bool ValidHandle(IntPtr h) {return h!=IntPtr.Zero&&h.ToInt64()!=-1;}
  private static void Candidate(string target,string leaf) {
    if(target==null||!target.StartsWith("\\",StringComparison.Ordinal)||target.StartsWith("\\\\",StringComparison.Ordinal)||target.IndexOf('\0')>=0||target.IndexOf('/')>=0||!target.EndsWith("\\"+leaf,StringComparison.Ordinal)||Encoding.Unicode.GetByteCount(target)>65532)throw new InvalidOperationException("One explicit matching absolute target required");
  }
  public sealed class ParentBinding : IDisposable {
    private readonly NativeSession session;private readonly IntPtr borrowed;
    private readonly Dictionary<IntPtr,string> retainedWorld;private readonly string worldName,candidate;
    private IntPtr verification;private bool alive=true,issued;private Request expected;
    public readonly Facts facts;
    private ParentBinding(NativeSession native,IntPtr original,IntPtr owned,Dictionary<IntPtr,string> retained,string world,string target,Facts row) {session=native;borrowed=original;verification=owned;retainedWorld=retained;worldName=world;candidate=target;facts=row;}
    private void Held() {
      facts.bindingHeld=false;string world;if(!alive||verification==IntPtr.Zero||!retainedWorld.TryGetValue(borrowed,out world)||world!=worldName)throw new InvalidOperationException("Exact parent binding no longer held");
      facts.sameObject=session.Same(borrowed,verification);if(facts.sameObject!=true)throw new InvalidOperationException("Exact parent binding no longer held");facts.bindingHeld=true;
    }
    public Request IssueRequest() {
      Held();if(issued)throw new InvalidOperationException("One owned request only");issued=true;
      expected=new Request{candidate=candidate,nonce=Guid.NewGuid().ToString("N"),targetHash=facts.targetHash};
      return new Request{candidate=expected.candidate,nonce=expected.nonce,targetHash=expected.targetHash};
    }
    // Test-only transport binding; authorization remains the unchanged
    // caller's account/token/session checks and retained StartedProcesses map.
    private int issuedChildPid;
    private void ChildIdentity(int pid,Dictionary<int,IntPtr> started) {
      IntPtr process;int error;
      if(pid<=0||started==null||!started.TryGetValue(pid,out process)||!ValidHandle(process))throw new InvalidOperationException("Exact retained child absent");
      uint actual=session.api.ProcessId(process,out error);
      facts.observedProcessPid=actual;facts.processIdentityError=error;
      if(actual!=(uint)pid||error!=0)throw new InvalidOperationException("Exact retained child PID changed");
      uint wait=session.api.WaitProcessZero(process,out error);
      facts.observedProcessWaitResult=wait;facts.observedProcessRunning=wait==0x102&&error==0;
      if(wait!=0x102||error!=0)throw new InvalidOperationException("Exact retained child is not still running");
    }
    public Request IssueKnownChildRequest(int pid,uint publishedOriginalPid,Dictionary<int,IntPtr> started) {
      lock(this) {
        Held();ChildIdentity(pid,started);
        if(publishedOriginalPid!=(uint)pid)throw new InvalidOperationException("Original result PID changed");
        Request request=IssueRequest();issuedChildPid=pid;return request;
      }
    }
    public void ValidateTransportReply(ChildReply reply,int pid,Dictionary<int,IntPtr> started) {
      lock(this) {
        Held();ChildIdentity(pid,started);
        if(expected==null||issuedChildPid!=pid||reply==null||reply.pid!=(uint)pid||reply.nonce!=expected.nonce||reply.targetHash!=expected.targetHash)throw new InvalidOperationException("Owned request tuple changed");
        Transport.ValidateSemantics(reply);Held();
      }
    }
    public void AcknowledgeTransport(ChildReply reply,int pid,Dictionary<int,IntPtr> started,string ownedDirectory,ProtocolFiles files=null) {
      lock(this) {
        ValidateTransportReply(reply,pid,started);
        // Release only this already validated child. Diagnostic denial or failed
        // cleanup remains a literal measurement; it cannot satisfy the old gate.
        ChildIdentity(pid,started);
        Transport.PublishNew(System.IO.Path.Combine(ownedDirectory,"fixture-release-"+pid.ToString(System.Globalization.CultureInfo.InvariantCulture)),Encoding.UTF8.GetBytes("verified"),files);
        Held();expected=null;
      }
    }

    public void Dispose() {lock(this){if(!alive)return;alive=false;facts.bindingHeld=false;try{if(verification!=IntPtr.Zero){session.Close(verification);verification=IntPtr.Zero;}}finally{session.Release();}}}

    public static ParentBinding Open(KernelApi api,IntPtr borrowed,Dictionary<IntPtr,string> retainedWorld,string candidate,out Facts facts) {
      Layout();string world;
      if(!ValidHandle(borrowed)||retainedWorld==null||!retainedWorld.TryGetValue(borrowed,out world)||!Regex.IsMatch(world,@"\AGlobal\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z"))throw new InvalidOperationException("Exact retained World event required");
      Candidate(candidate,world.Substring(7));facts=new Facts{phase="ParentBinding",targetHash=Hash(candidate)};
      var native=new NativeSession(api,facts);IntPtr handle=IntPtr.Zero;int? observedStatus=null;bool transferred=false;
      try {
        if(!native.Acquire())return null;int status=native.Open(candidate,ref handle,out observedStatus);facts.ntStatus=status;
        if(status<0){facts.open="nt-status";return null;}
        if(!ValidHandle(handle)||handle==borrowed){facts.open="invalid-output";return null;}
        facts.open="opened";facts.sameObject=native.Same(borrowed,handle);if(facts.sameObject!=true)return null;
        var bound=new ParentBinding(native,borrowed,handle,retainedWorld,world,candidate,facts);facts.identityBound=true;facts.bindingHeld=true;transferred=true;return bound;
      } finally {facts.ntStatus=observedStatus;if(!transferred){try{if(observedStatus.HasValue&&observedStatus.Value>=0&&ValidHandle(handle)&&handle!=borrowed)native.Close(handle);}finally{native.Release();}}}
    }
  }

  // Fixed protocol names only; no arbitrary inventory or recursive cleanup.
  public sealed class ProtocolFiles {
    public readonly string directory;
    readonly Dictionary<string,byte[]> owned=new Dictionary<string,byte[]>(StringComparer.Ordinal);
    static readonly string[] Names={"fixture-nt-event-armed","fixture-nt-event-armed.new","fixture-nt-event.request","fixture-nt-event.request.new","fixture-nt-event-result.json","fixture-nt-event-result.new"};
    public ProtocolFiles(string root) {
      directory=System.IO.Path.GetFullPath(root);
      if(!System.IO.Directory.Exists(directory)||(System.IO.File.GetAttributes(directory)&System.IO.FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("Owned protocol directory must be regular");
      foreach(string name in Names) {
        string path=System.IO.Path.Combine(directory,name);
        try {System.IO.File.GetAttributes(path);throw new InvalidOperationException("Unowned protocol path already exists");}
        catch(System.IO.FileNotFoundException) {}
      }
    }
    string Exact(string path) {
      string full=System.IO.Path.GetFullPath(path),name=System.IO.Path.GetFileName(full);
      if(System.IO.Path.GetDirectoryName(full)!=directory||Array.IndexOf(Names,name)<0&&!Regex.IsMatch(name,@"\Afixture-release-[1-9][0-9]{0,9}(?:\.new)?\z"))throw new InvalidOperationException("Protocol path is not a fixed owned role");
      return full;
    }
    public void Created(string path,byte[] expected) {path=Exact(path);if(owned.ContainsKey(path))throw new InvalidOperationException("Protocol role already owned");owned.Add(path,expected);}
    public void Transferred(string temporary,string final,byte[] expected) {Drained(temporary);Created(final,(byte[])expected.Clone());}
    public void Drained(string path) {owned.Remove(Exact(path));}
    public void AdoptValidatedReply(string path,byte[] bytes,ParentBinding binding,int pid,Dictionary<int,IntPtr> started) {
      binding.ValidateTransportReply(Transport.ReadReply(bytes),pid,started);
      Created(path,(byte[])bytes.Clone());
    }
    public void Cleanup() {
      var failures=new List<Exception>();
      foreach(string path in new List<string>(owned.Keys)) {
        try {
          System.IO.FileAttributes attrs;
          try {attrs=System.IO.File.GetAttributes(path);}catch(System.IO.FileNotFoundException){Drained(path);continue;}
          if((attrs&(System.IO.FileAttributes.Directory|System.IO.FileAttributes.ReparsePoint))!=0)throw new InvalidOperationException("Owned protocol role changed type");
          byte[] expected=owned[path];
          if(expected!=null) {
            byte[] actual=Transport.ReadBoundedBytes(path,512+2048);
            if(actual.Length!=expected.Length)throw new InvalidOperationException("Owned protocol role changed bytes");
            for(int i=0;i<actual.Length;i++)if(actual[i]!=expected[i])throw new InvalidOperationException("Owned protocol role changed bytes");
          }
          System.IO.File.Delete(path);Drained(path);
        } catch(Exception failure) {failures.Add(failure);}
      }
      // A malformed/unvalidated reply or preexisting role is not owned by this
      // ledger. Retain it and prevent a later recursive fixture cleanup from
      // bypassing this exact-path ownership boundary.
      foreach(string name in Names) {
        string path=System.IO.Path.Combine(directory,name);
        if(owned.ContainsKey(path))continue;
        try {System.IO.File.GetAttributes(path);failures.Add(new InvalidOperationException("Unowned fixed protocol role retained"));}
        catch(System.IO.FileNotFoundException) {}
        catch(Exception failure) {failures.Add(failure);}
      }
      if(failures.Count!=0)throw new AggregateException("Owned protocol cleanup failed",failures);
    }
  }

  public sealed class ChildReply {
    public uint pid,systemError,loadError,resolveError,closeError,releaseError;
    public string nonce,targetHash,systemStatus,loadStatus,resolveStatus,openStatus,closeStatus,releaseStatus;
    public int? ntStatus;public bool cleanupOkay;
  }
  public static class Transport {
    const string U="(0|[1-9][0-9]{0,9})";
    static string Field(string name,string pattern) {return "\\\""+name+"\\\":(?<"+name+">"+pattern+")";}
    static string Text(string name,string values) {return "\\\""+name+"\\\":\\\"(?<"+name+">"+values+")\\\"";}
    static readonly Regex ReplyPattern=new Regex("\\A\\{"+
      "\\\"schema\\\":\\\"mixel-nt-event-child-v1\\\",\\\"phase\\\":\\\"ChildSelf\\\","+
      Field("pid",U)+","+Text("nonce","[0-9a-f]{32}")+","+Text("targetHash","[0-9a-f]{64}")+",\\\"requestedAccess\\\":1048576,"+
      Text("systemStatus","not-attempted|available|error|invalid-output")+","+Field("systemError",U)+","+
      Text("loadStatus","not-attempted|acquired|error|invalid-output")+","+Field("loadError",U)+","+
      Text("resolveStatus","not-attempted|available|error|invalid-output")+","+Field("resolveError",U)+","+
      Text("openStatus","not-attempted|opened|nt-status|invalid-output")+","+Field("ntStatus","null|0|-?[1-9][0-9]{0,9}")+","+
      Text("closeStatus","not-owned|closed|failed")+","+Field("closeError",U)+","+
      Text("releaseStatus","not-acquired|released|failed")+","+Field("releaseError",U)+","+Field("cleanupOkay","true|false")+"\\}\\n\\z",RegexOptions.CultureInvariant,TimeSpan.FromMilliseconds(100));

    public static byte[] ReadBoundedBytes(string path,int maximum) {
      if(maximum<1||maximum>4096)throw new InvalidOperationException("Protocol byte bound changed");
      var attrs=System.IO.File.GetAttributes(path);
      if((attrs&(System.IO.FileAttributes.Directory|System.IO.FileAttributes.ReparsePoint))!=0)throw new InvalidOperationException("Protocol input must be regular");
      using(var stream=new System.IO.FileStream(path,System.IO.FileMode.Open,System.IO.FileAccess.Read,System.IO.FileShare.Read))
      using(var memory=new System.IO.MemoryStream()) {
        byte[] buffer=new byte[maximum+1];int read;
        while((read=stream.Read(buffer,0,buffer.Length))!=0){memory.Write(buffer,0,read);if(memory.Length>maximum)throw new InvalidOperationException("Protocol input exceeds byte bound");}
        return memory.ToArray();
      }
    }

    public static ChildReply ReadReply(byte[] bytes) {
      if(bytes==null||bytes.Length==0||bytes.Length>2048)throw new InvalidOperationException("Reply byte bound");
      string text=new UTF8Encoding(false,true).GetString(bytes);Match row=ReplyPattern.Match(text);
      if(!row.Success)throw new InvalidOperationException("Reply exact scalar schema");
      Func<string,uint> u=name=>uint.Parse(row.Groups[name].Value,System.Globalization.CultureInfo.InvariantCulture);
      Func<string,string> s=name=>row.Groups[name].Value;
      var reply=new ChildReply{pid=u("pid"),nonce=s("nonce"),targetHash=s("targetHash"),systemStatus=s("systemStatus"),systemError=u("systemError"),
        loadStatus=s("loadStatus"),loadError=u("loadError"),resolveStatus=s("resolveStatus"),resolveError=u("resolveError"),
        openStatus=s("openStatus"),ntStatus=s("ntStatus")=="null"?(int?)null:int.Parse(s("ntStatus"),System.Globalization.CultureInfo.InvariantCulture),
        closeStatus=s("closeStatus"),closeError=u("closeError"),releaseStatus=s("releaseStatus"),releaseError=u("releaseError"),cleanupOkay=s("cleanupOkay")=="true"};
      ValidateSemantics(reply);return reply;
    }
    public static void ValidateSemantics(ChildReply r) {
      if(r==null||r.pid==0)throw new InvalidOperationException("Reply child PID absent");
      if(Array.IndexOf(new[]{"not-attempted","available","error","invalid-output"},r.systemStatus)<0 ||
         Array.IndexOf(new[]{"not-attempted","acquired","error","invalid-output"},r.loadStatus)<0 ||
         Array.IndexOf(new[]{"not-attempted","available","error","invalid-output"},r.resolveStatus)<0 ||
         Array.IndexOf(new[]{"not-attempted","opened","nt-status","invalid-output"},r.openStatus)<0 ||
         Array.IndexOf(new[]{"not-owned","closed","failed"},r.closeStatus)<0 ||
         Array.IndexOf(new[]{"not-acquired","released","failed"},r.releaseStatus)<0)throw new InvalidOperationException("Unknown native classification");
      bool loaded=r.loadStatus=="acquired";
      if(r.systemStatus=="available"&&r.systemError!=0||r.loadStatus=="acquired"&&r.loadError!=0||r.resolveStatus=="available"&&r.resolveError!=0)throw new InvalidOperationException("Successful API error changed");
      if(r.systemStatus!="available"&&(r.loadStatus!="not-attempted"||r.loadError!=0))throw new InvalidOperationException("Load without actual system directory");
      if(!loaded&&(r.resolveStatus!="not-attempted"||r.resolveError!=0||r.releaseStatus!="not-acquired"||r.releaseError!=0))throw new InvalidOperationException("Unowned module lifecycle");
      if(loaded&&(r.releaseStatus!="released"&&r.releaseStatus!="failed"))throw new InvalidOperationException("Acquired module not released");
      if(r.releaseStatus=="released"&&r.releaseError!=0)throw new InvalidOperationException("Successful module release error");
      if(r.resolveStatus=="available"&&(r.openStatus=="not-attempted"||r.ntStatus==null))throw new InvalidOperationException("Resolved native entry not called");
      if(r.resolveStatus!="available"&&(r.openStatus!="not-attempted"||r.ntStatus!=null))throw new InvalidOperationException("Native open without resolved entry");
      if(r.openStatus=="not-attempted"&&(r.ntStatus!=null||r.closeStatus!="not-owned"||r.closeError!=0))throw new InvalidOperationException("Unattempted native lifecycle");
      if(r.openStatus=="nt-status"&&(r.ntStatus==null||r.ntStatus>=0||r.closeStatus!="not-owned"||r.closeError!=0))throw new InvalidOperationException("Failed native output ownership");
      if(r.openStatus=="invalid-output"&&(r.ntStatus==null||r.ntStatus<0||r.closeStatus!="not-owned"||r.closeError!=0))throw new InvalidOperationException("Invalid successful output lifecycle");
      if(r.openStatus=="opened"&&(r.ntStatus==null||r.ntStatus<0||(r.closeStatus!="closed"&&r.closeStatus!="failed")))throw new InvalidOperationException("NT_SUCCESS owned handle lifecycle");
      if(r.closeStatus=="closed"&&r.closeError!=0||r.closeStatus=="not-owned"&&r.closeError!=0)throw new InvalidOperationException("Successful or unopened handle close error");
      if(r.cleanupOkay!=(r.closeStatus!="failed"&&r.releaseStatus!="failed"))throw new InvalidOperationException("Cleanup failure was hidden");
    }
    public static void WriteNew(string path,byte[] bytes) {
      using(var stream=new System.IO.FileStream(path,System.IO.FileMode.CreateNew,System.IO.FileAccess.Write,System.IO.FileShare.None)) {stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
    }
    public static void PublishNew(string final,byte[] bytes,ProtocolFiles files=null) {
      string temporary=final+".new";bool owned=false;Exception primary=null;
      try {
        using(var stream=new System.IO.FileStream(temporary,System.IO.FileMode.CreateNew,System.IO.FileAccess.Write,System.IO.FileShare.None)) {
          owned=true;if(files!=null)files.Created(temporary,null);stream.Write(bytes,0,bytes.Length);stream.Flush(true);
        }
        System.IO.File.Move(temporary,final);owned=false;if(files!=null)files.Transferred(temporary,final,bytes);
      } catch(Exception error) {primary=error;throw;}
      finally {
        if(owned) {
          try {System.IO.File.Delete(temporary);if(files!=null)files.Drained(temporary);}
          catch(Exception cleanup) {if(primary!=null)throw new AggregateException("Owned transport write and cleanup failed",primary,cleanup);throw;}
        }
      }
    }
    public static void Arm(string directory,ProtocolFiles files=null) {
      if(!System.IO.Directory.Exists(directory)||(System.IO.File.GetAttributes(directory)&System.IO.FileAttributes.ReparsePoint)!=0)throw new InvalidOperationException("Existing owned regular directory required");
      PublishNew(System.IO.Path.Combine(directory,"fixture-nt-event-armed"),Encoding.ASCII.GetBytes("observe\n"),files);
    }
    public static void WriteRequest(string directory,int pid,Request request,ProtocolFiles files=null) {
      if(pid<=0||request==null||request.phase!="ChildSelfRequest"||!Regex.IsMatch(request.nonce??"",@"\A[0-9a-f]{32}\z")||!Regex.IsMatch(request.targetHash??"",@"\A[0-9a-f]{64}\z")||Hash(request.candidate??"")!=request.targetHash)throw new InvalidOperationException("Private known-child request changed");
      if(!Regex.IsMatch(request.candidate??"",@"\A\\BaseNamedObjects\\Mixel-Remote-Standard-Lease-[0-9a-f]{32}\z"))throw new InvalidOperationException("One explicit Global candidate required");
      byte[] text=Encoding.Unicode.GetBytes(request.candidate);byte[] bytes;
      using(var memory=new System.IO.MemoryStream())using(var writer=new System.IO.BinaryWriter(memory)) {
        writer.Write(Encoding.ASCII.GetBytes("MXLNTE01"));writer.Write((uint)pid);writer.Write(Encoding.ASCII.GetBytes(request.nonce));writer.Write(Encoding.ASCII.GetBytes(request.targetHash));writer.Write((uint)text.Length);writer.Write(text);writer.Flush();bytes=memory.ToArray();
      }
      if(bytes.Length>512)throw new InvalidOperationException("Private request byte bound");
      PublishNew(System.IO.Path.Combine(directory,"fixture-nt-event.request"),bytes,files);
    }
  }
}
