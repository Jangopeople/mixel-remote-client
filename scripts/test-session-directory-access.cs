// CI-only temporary DACL experiment. Never a product permission workaround.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

public static class MixelSessionDirectoryAba {
  public interface Api : IDisposable {
    uint CurrentSession();
    int OpenSessionDirectory(uint session, uint access, out IntPtr handle);
    byte[] ReadDescriptor(IntPtr handle, uint information);
    byte[] AddOwnedAce(byte[] originalAcl, byte[] ownedSid);
    bool SetDacl(IntPtr handle, byte[] descriptor, out int error);
    bool Close(IntPtr handle, out int error);
  }
  public sealed class Facts {
    public int schema=1; public string scope="CiTemporarySessionDirectoryDaclOnly";
    public uint session, openAccess=0x60000, addedMask=4; public int addedAceFlags=0;
    public int? openNtStatus; public string open="not-attempted", grant="not-attempted", restore="not-attempted", close="not-owned", unload="not-attempted";
    public bool writeAttempted, restorationVerified, cleanupOkay, originalAceOrderPreserved;
    public string originalDaclHash, expectedDaclHash, restoredDaclHash, originalLabelHash, originalOwnerHash, originalGroupHash;
    public ushort originalControl;
    public bool ownerUnchanged, groupUnchanged, controlUnchanged, labelUnchanged;
    public int grantError, restoreError, closeError;
    public bool actualWin32RouteMeasured=false, effectiveAccessCalculated=false, productQualified=false, standardEightQualified=false;
    public List<string> errors=new List<string>();
  }
  static void Require(bool value,string text) {if(!value)throw new InvalidOperationException(text);}
  static byte[] Slice(byte[] raw,int offset,int size) {
    Require(offset>=0&&size>=0&&offset<=raw.Length&&size<=raw.Length-offset,"Security descriptor range changed");
    byte[] value=new byte[size];Buffer.BlockCopy(raw,offset,value,0,size);return value;
  }
  static uint U32(byte[] raw,int offset) {return BitConverter.ToUInt32(Slice(raw,offset,4),0);}
  static ushort U16(byte[] raw,int offset) {return BitConverter.ToUInt16(Slice(raw,offset,2),0);}
  static void Put(byte[] raw,int offset,uint value) {Buffer.BlockCopy(BitConverter.GetBytes(value),0,raw,offset,4);}
  static void Put16(byte[] raw,int offset,ushort value) {Buffer.BlockCopy(BitConverter.GetBytes(value),0,raw,offset,2);}
  static bool Same(byte[] left,byte[] right) {
    if(left==null||right==null||left.Length!=right.Length)return false;
    for(int index=0;index<left.Length;index++)if(left[index]!=right[index])return false;return true;
  }
  public static string Hash(byte[] raw) {using(var hash=SHA256.Create()){return BitConverter.ToString(hash.ComputeHash(raw)).Replace("-","").ToLowerInvariant();}}
  static byte[] Sid(byte[] raw,int offset) {
    Require(offset>=20&&offset%4==0&&offset<=raw.Length-8,"Security descriptor SID bounds changed");
    Require(raw[offset]==1&&raw[offset+1]<=15,"Security descriptor SID revision changed");
    return Slice(raw,offset,8+4*raw[offset+1]);
  }
  static void LocalUserSid(byte[] sid) {
    Require(sid!=null&&sid.Length==28&&sid[0]==1&&sid[1]==5,"Owned local user SID shape changed");
    Require(sid[2]==0&&sid[3]==0&&sid[4]==0&&sid[5]==0&&sid[6]==0&&sid[7]==5&&U32(sid,8)==21,"Owned local user SID authority changed");
    Require(U32(sid,24)>=1000,"Owned account is not a newly created local QA user");
  }
  sealed class Parts {
    public ushort control;public byte[] owner,group,dacl;
    public Parts(byte[] raw) {
      Require(raw!=null&&raw.Length>=20&&raw.Length<=65536&&raw[0]==1&&raw[1]==0,"Bounded self-relative descriptor missing");
      control=U16(raw,2);Require((control&0x8000)!=0&&(control&4)!=0&&U32(raw,12)==0,"DACL snapshot is not a self-relative DACL-only query");
      int ownerAt=checked((int)U32(raw,4)),groupAt=checked((int)U32(raw,8)),aclAt=checked((int)U32(raw,16));
      owner=Sid(raw,ownerAt);group=Sid(raw,groupAt);
      Require(aclAt>=20&&aclAt%4==0&&aclAt<=raw.Length-8,"NULL or invalid DACL refused");
      dacl=Slice(raw,aclAt,U16(raw,aclAt+2));
      Require(aclAt+ dacl.Length<=ownerAt||ownerAt+owner.Length<=aclAt,"DACL and owner overlap");
      Require(aclAt+ dacl.Length<=groupAt||groupAt+group.Length<=aclAt,"DACL and group overlap");
      Require(ownerAt==groupAt||ownerAt+owner.Length<=groupAt||groupAt+group.Length<=ownerAt,"Owner and group overlap");
      ValidateAcl(dacl,null);
    }
    public bool EqualsParts(Parts other,byte[] expected) {return other!=null&&control==other.control&&Same(owner,other.owner)&&Same(group,other.group)&&Same(expected,other.dacl);}
    public byte[] Descriptor(byte[] acl) {
      byte[] output=new byte[20+owner.Length+group.Length+acl.Length];output[0]=1;Put16(output,2,control);
      Put(output,4,20);Put(output,8,(uint)(20+owner.Length));Put(output,16,(uint)(20+owner.Length+group.Length));
      Buffer.BlockCopy(owner,0,output,20,owner.Length);Buffer.BlockCopy(group,0,output,20+owner.Length,group.Length);
      Buffer.BlockCopy(acl,0,output,20+owner.Length+group.Length,acl.Length);return output;
    }
  }
  static int ValidateAcl(byte[] acl,byte[] ownedSid) {
    Require(acl.Length>=8&&acl.Length<=65535&&U16(acl,2)==acl.Length&&(acl[0]==2||acl[0]==4)&&acl[1]==0&&U16(acl,6)==0,"ACL header changed");
    int count=U16(acl,4);Require(count<=64,"ACL exceeds the diagnostic ACE bound");int position=8;
    for(int index=0;index<count;index++) {
      Require(position<=acl.Length-8,"ACE header exceeds ACL");int size=U16(acl,position+2);
      Require(size>=16&&size%4==0&&size<=acl.Length-position,"ACE size changed");
      Require(acl[position]==0||acl[position]==1,"Unknown ACE type refused before mutation");
      Require((acl[position+1]&16)==0,"Inherited ACE insertion order is outside this bounded experiment");
      Require(acl[position+8]==1&&acl[position+9]<=15&&size==16+4*acl[position+9],"ACE SID bounds changed");
      if(ownedSid!=null)Require(!Same(Slice(acl,position+8,size-8),ownedSid),"Fresh QA SID already occurs in the directory ACL");
      position+=size;
    }
    return position;
  }
  static void ValidateLabel(byte[] raw) {
    Require(raw!=null&&raw.Length>=20&&raw.Length<=65536&&raw[0]==1&&(U16(raw,2)&0x8000)!=0,"Bounded self-relative LABEL snapshot missing");
    Require(U32(raw,4)==0&&U32(raw,8)==0&&U32(raw,16)==0,"LABEL-only query unexpectedly contains other trustees");
    uint offset=U32(raw,12);bool present=(U16(raw,2)&16)!=0;
    if(!present){Require(offset==0,"Absent LABEL query has an unexpected ACL offset");return;}
    Require(offset>=20&&offset%4==0&&offset<=raw.Length-8,"LABEL ACL bounds changed");
    int at=checked((int)offset),size=U16(raw,at+2),count=U16(raw,at+4);
    Require(size>=8&&size<=raw.Length-at&&raw[at]==2&&raw[at+1]==0&&U16(raw,at+6)==0&&count<=1,"LABEL ACL header changed");
    if(count==0)return;
    int ace=at+8;Require(ace<=at+size-20&&raw[ace]==17&&U16(raw,ace+2)==20&&U32(raw,ace+4)<=7,"Mandatory LABEL ACE changed");
    Require(raw[ace+8]==1&&raw[ace+9]==1&&raw[ace+10]==0&&raw[ace+11]==0&&raw[ace+12]==0&&raw[ace+13]==0&&raw[ace+14]==0&&raw[ace+15]==16,"Mandatory LABEL trustee changed");
  }
  public static byte[] AddOneAce(byte[] original,byte[] ownedSid) {
    LocalUserSid(ownedSid);int end=ValidateAcl(original,ownedSid);int count=U16(original,4);
    Require(count<64&&original.Length+8+ownedSid.Length<=65535,"One additional ACE exceeds the diagnostic bound");
    // Preserve every original ACE and all trailing bytes in their exact order.
    byte[] value=new byte[original.Length+8+ownedSid.Length];Buffer.BlockCopy(original,0,value,0,end);
    value[end]=0;value[end+1]=0;Put16(value,end+2,(ushort)(8+ownedSid.Length));Put(value,end+4,4);
    Buffer.BlockCopy(ownedSid,0,value,end+8,ownedSid.Length);
    Buffer.BlockCopy(original,end,value,end+8+ownedSid.Length,original.Length-end);
    Put16(value,2,(ushort)value.Length);Put16(value,4,(ushort)(count+1));return value;
  }
  public sealed class Scope : IDisposable {
    readonly Api api;readonly byte[] ownedSid;IntPtr handle=IntPtr.Zero;Parts original;byte[] label,expected;bool finished;
    public readonly Facts facts=new Facts();
    public Scope(Api platform,byte[] sid) {Require(platform!=null,"Native API missing");LocalUserSid(sid);api=platform;ownedSid=(byte[])sid.Clone();}
    public void Open() {
      Require(handle==IntPtr.Zero&&facts.open=="not-attempted","Scope already opened");facts.session=api.CurrentSession();Require(facts.session>0,"Interactive runner session required");
      IntPtr output=IntPtr.Zero;int status=api.OpenSessionDirectory(facts.session,0x60000,out output);facts.openNtStatus=status;
      if(status<0){facts.open="nt-status";throw new InvalidOperationException("Owned session directory WRITE_DAC open failed");}
      Require(output!=IntPtr.Zero&&output.ToInt64()!=-1,"Successful directory open returned an invalid handle");handle=output;facts.open="opened";
      Parts snapshot=new Parts(api.ReadDescriptor(handle,7));byte[] labelSnapshot=api.ReadDescriptor(handle,16);
      ValidateLabel(labelSnapshot);byte[] oracle=AddOneAce(snapshot.dacl,ownedSid);
      byte[] transform=api.AddOwnedAce(snapshot.dacl,ownedSid);Require(Same(oracle,transform),"Public ACL helper output differs from exactly one ACE");
      original=snapshot;label=labelSnapshot;expected=transform;
      facts.originalDaclHash=Hash(original.dacl);facts.expectedDaclHash=Hash(expected);facts.originalLabelHash=Hash(label);
      facts.originalOwnerHash=Hash(original.owner);facts.originalGroupHash=Hash(original.group);facts.originalControl=original.control;facts.originalAceOrderPreserved=true;
    }
    public void Apply() {
      Require(original!=null&&handle!=IntPtr.Zero&&!facts.writeAttempted,"Validated original snapshot required");
      Require(original.EqualsParts(new Parts(api.ReadDescriptor(handle,7)),original.dacl)&&Same(label,api.ReadDescriptor(handle,16)),"Foreign pre-write descriptor drift refused");
      // A failing or throwing setter can leave state uncertain: register first.
      facts.writeAttempted=true;facts.grant="attempted";int error;
      bool set=api.SetDacl(handle,original.Descriptor(expected),out error);facts.grantError=error;
      if(!set){facts.grant="failed";throw new InvalidOperationException("Temporary DACL setter failed");}
      Require(error==0,"Successful DACL setter returned inconsistent error");
      Require(original.EqualsParts(new Parts(api.ReadDescriptor(handle,7)),expected)&&Same(label,api.ReadDescriptor(handle,16)),"Temporary descriptor readback differs from exactly one ACE");facts.grant="verified";
    }
    public void Restore() {
      Require(!finished,"Finished scope cannot restore");
      if(original==null)return;
      if(facts.writeAttempted) {
        facts.restore="attempted";int error;bool set=api.SetDacl(handle,original.Descriptor(original.dacl),out error);facts.restoreError=error;
        if(!set||error!=0){facts.restore="failed";throw new InvalidOperationException("Original DACL restore setter failed");}
      }
      Parts restored=new Parts(api.ReadDescriptor(handle,7));byte[] restoredLabel=api.ReadDescriptor(handle,16);
      facts.restoredDaclHash=Hash(restored.dacl);facts.ownerUnchanged=Same(original.owner,restored.owner);facts.groupUnchanged=Same(original.group,restored.group);
      facts.controlUnchanged=original.control==restored.control;facts.labelUnchanged=Same(label,restoredLabel);
      facts.restorationVerified=original.EqualsParts(restored,original.dacl)&&facts.labelUnchanged;
      Require(facts.restorationVerified,"Original descriptor restoration readback changed");facts.restore="verified";
    }
    public void Dispose() {
      if(finished)return;finished=true;
      try {
        if(handle!=IntPtr.Zero) {
          int error;bool okay=api.Close(handle,out error);facts.closeError=error;facts.close=okay&&error==0?"closed":"failed";
          if(okay)handle=IntPtr.Zero;
          if(!okay||error!=0)facts.errors.Add("owned-directory-close");
        }
      } catch(Exception){facts.close="exception";facts.errors.Add("owned-directory-close-exception");}
      finally {try{api.Dispose();facts.unload="released";}catch(Exception){facts.unload="failed";facts.errors.Add("owned-module-release");}}
      facts.cleanupOkay=(facts.close=="closed"||facts.close=="not-owned")&&facts.unload=="released"&&(!facts.writeAttempted||facts.restorationVerified)&&facts.errors.Count==0;
      Require(facts.cleanupOkay,"Temporary directory ownership cleanup incomplete");
    }
  }
  public sealed class NativeApi : Api {
    [StructLayout(LayoutKind.Sequential)] struct Unicode {public ushort Length,MaximumLength;public IntPtr Buffer;}
    [StructLayout(LayoutKind.Sequential)] struct Attributes {public uint Length;public IntPtr RootDirectory,ObjectName;public uint Flags;public IntPtr SecurityDescriptor,SecurityQualityOfService;}
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate int DirectoryOpen(out IntPtr handle,uint access,ref Attributes attributes);
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern bool ProcessIdToSessionId(uint pid,out uint session);
    [DllImport("kernel32.dll",ExactSpelling=true)] static extern uint GetCurrentProcessId();
    [DllImport("kernel32.dll",ExactSpelling=true,CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetSystemDirectoryW([Out] char[] buffer,uint size);
    [DllImport("kernel32.dll",ExactSpelling=true,CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr LoadLibraryExW(string name,IntPtr file,uint flags);
    [DllImport("kernel32.dll",ExactSpelling=true,CharSet=CharSet.Ansi,SetLastError=true)] static extern IntPtr GetProcAddress(IntPtr module,string name);
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern bool FreeLibrary(IntPtr module);
    [DllImport("kernel32.dll",ExactSpelling=true,SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool GetKernelObjectSecurity(IntPtr handle,uint information,IntPtr descriptor,uint capacity,out uint needed);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool SetKernelObjectSecurity(IntPtr handle,uint information,IntPtr descriptor);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool InitializeAcl(IntPtr acl,uint length,uint revision);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool GetAclInformation(IntPtr acl,IntPtr information,uint length,uint kind);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool GetAce(IntPtr acl,uint index,out IntPtr ace);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool AddAce(IntPtr acl,uint revision,uint index,IntPtr ace,uint length);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool AddAccessAllowedAceEx(IntPtr acl,uint revision,uint flags,uint mask,[In] byte[] sid);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool InitializeSecurityDescriptor(IntPtr descriptor,uint revision);
    [DllImport("advapi32.dll",ExactSpelling=true,SetLastError=true)] static extern bool SetSecurityDescriptorDacl(IntPtr descriptor,bool present,IntPtr acl,bool defaulted);
    IntPtr module=IntPtr.Zero;DirectoryOpen open;
    public NativeApi() {
      Require(Environment.OSVersion.Platform==PlatformID.Win32NT&&Environment.Is64BitProcess&&Environment.GetEnvironmentVariable("GITHUB_ACTIONS")=="true"&&
        Environment.GetEnvironmentVariable("GITHUB_EVENT_NAME")=="workflow_dispatch"&&Environment.GetEnvironmentVariable("GITHUB_JOB")=="session-directory-access-observation"&&
        Environment.GetEnvironmentVariable("GITHUB_REPOSITORY")=="Jangopeople/mixel-remote-client"&&Environment.GetEnvironmentVariable("RUNNER_ENVIRONMENT")=="github-hosted"&&
        Environment.GetEnvironmentVariable("MIXEL_SESSION_DIRECTORY_ABA")=="1","Explicit disposable hosted Windows diagnostic scope required");
    }
    public uint CurrentSession(){uint value;bool okay=ProcessIdToSessionId(GetCurrentProcessId(),out value);int error=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(error,"Session query failed");return value;}
    public int OpenSessionDirectory(uint session,uint access,out IntPtr handle) {
      Require(session==CurrentSession()&&session>0&&access==0x60000,"Only exact current-session READ_CONTROL/WRITE_DAC allowed");
      char[] buffer=new char[512];uint length=GetSystemDirectoryW(buffer,512);int error=length==0?Marshal.GetLastWin32Error():0;
      if(length==0)throw new Win32Exception(error,"System directory query failed");Require(length<512,"System directory exceeds bound");
      module=LoadLibraryExW(Path.Combine(new string(buffer,0,(int)length),"ntdll.dll"),IntPtr.Zero,0x800);error=module==IntPtr.Zero?Marshal.GetLastWin32Error():0;
      if(module==IntPtr.Zero)throw new Win32Exception(error,"System32 module load failed");IntPtr entry=GetProcAddress(module,"NtOpenDirectoryObject");error=entry==IntPtr.Zero?Marshal.GetLastWin32Error():0;
      if(entry==IntPtr.Zero)throw new Win32Exception(error,"Directory API resolution failed");open=(DirectoryOpen)Marshal.GetDelegateForFunctionPointer(entry,typeof(DirectoryOpen));
      string path="\\Sessions\\"+session.ToString(System.Globalization.CultureInfo.InvariantCulture)+"\\BaseNamedObjects";
      IntPtr text=Marshal.StringToHGlobalUni(path),name=IntPtr.Zero;
      try {Unicode value=new Unicode {Length=(ushort)(path.Length*2),MaximumLength=(ushort)((path.Length+1)*2),Buffer=text};name=Marshal.AllocHGlobal(16);Marshal.StructureToPtr(value,name,false);
        Attributes attributes=new Attributes {Length=48,ObjectName=name,Flags=64};handle=IntPtr.Zero;return open(out handle,access,ref attributes);
      }finally{try{if(name!=IntPtr.Zero)Marshal.FreeHGlobal(name);}finally{Marshal.FreeHGlobal(text);}}
    }
    public byte[] ReadDescriptor(IntPtr handle,uint information) {
      Require(handle!=IntPtr.Zero&&handle.ToInt64()!=-1&&(information==7||information==16),"Bounded descriptor query required");
      uint needed;bool okay=GetKernelObjectSecurity(handle,information,IntPtr.Zero,0,out needed);int error=okay?0:Marshal.GetLastWin32Error();
      if(okay||error!=122||needed<20||needed>65536)throw new InvalidOperationException("Descriptor sizing contract changed");
      IntPtr buffer=Marshal.AllocHGlobal((int)needed);
      try {uint returned;okay=GetKernelObjectSecurity(handle,information,buffer,needed,out returned);error=okay?0:Marshal.GetLastWin32Error();
        if(!okay)throw new Win32Exception(error,"Descriptor fill failed");Require(returned==needed,"Descriptor size changed during query");byte[] raw=new byte[needed];Marshal.Copy(buffer,raw,0,raw.Length);return raw;
      }finally{Marshal.FreeHGlobal(buffer);}
    }
    static uint[] AclSize(IntPtr acl) {
      IntPtr buffer=Marshal.AllocHGlobal(12);
      try {bool okay=GetAclInformation(acl,buffer,12,2);int error=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(error,"ACL size query failed");
        return new uint[]{unchecked((uint)Marshal.ReadInt32(buffer,0)),unchecked((uint)Marshal.ReadInt32(buffer,4)),unchecked((uint)Marshal.ReadInt32(buffer,8))};
      }finally{Marshal.FreeHGlobal(buffer);}
    }
    public byte[] AddOwnedAce(byte[] originalAcl,byte[] sid) {
      LocalUserSid(sid);int end=ValidateAcl(originalAcl,sid);uint count=U16(originalAcl,4),revision=originalAcl[0];
      byte[] oracle=AddOneAce(originalAcl,sid);IntPtr originalBuffer=IntPtr.Zero,target=IntPtr.Zero;
      try {
        originalBuffer=Marshal.AllocHGlobal(originalAcl.Length);Marshal.Copy(originalAcl,0,originalBuffer,originalAcl.Length);
        uint[] size=AclSize(originalBuffer);Require(size[0]==count&&size[1]==end&&size[2]==originalAcl.Length-end,"Original public ACL size differs from bounded snapshot");
        target=Marshal.AllocHGlobal(oracle.Length);Marshal.Copy(new byte[oracle.Length],0,target,oracle.Length);
        bool okay=InitializeAcl(target,(uint)oracle.Length,revision);int error=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(error,"ACL initialization failed");
        for(uint index=0;index<count;index++) {
          IntPtr ace=IntPtr.Zero;okay=GetAce(originalBuffer,index,out ace);error=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(error,"Original ACE query failed");
          long relative=ace.ToInt64()-originalBuffer.ToInt64();Require(relative>=8&&relative<=end-8&&relative%4==0,"Public ACE pointer is outside owned ACL");
          uint length=unchecked((ushort)Marshal.ReadInt16(ace,2));Require(length>=16&&length%4==0&&length<=end-relative,"Public ACE extent exceeds owned ACL");
          okay=AddAce(target,revision,0xffffffff,ace,length);error=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(error,"Original ACE copy failed");
        }
        okay=AddAccessAllowedAceEx(target,revision,0,4,sid);error=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(error,"Owned noninheriting ACE add failed");
        uint[] result=AclSize(target);Require(result[0]==count+1&&result[1]==end+8+sid.Length&&result[2]==size[2],"Public transformed ACL size changed");
        // Preserve the original unused ACL bytes; never edit opaque ACL members.
        if(size[2]!=0)Marshal.Copy(originalAcl,end,IntPtr.Add(target,(int)result[1]),(int)size[2]);
        byte[] output=new byte[oracle.Length];Marshal.Copy(target,output,0,output.Length);Require(Same(output,oracle),"Public ACL construction changed original ACE bytes/order");return output;
      }finally{try{if(target!=IntPtr.Zero)Marshal.FreeHGlobal(target);}finally{if(originalBuffer!=IntPtr.Zero)Marshal.FreeHGlobal(originalBuffer);}}
    }
    public bool SetDacl(IntPtr handle,byte[] descriptor,out int error) {
      Require(handle!=IntPtr.Zero&&handle.ToInt64()!=-1,"Owned DACL setter handle required");Parts value=new Parts(descriptor);
      IntPtr acl=IntPtr.Zero,security=IntPtr.Zero;
      try {
        acl=Marshal.AllocHGlobal(value.dacl.Length);Marshal.Copy(value.dacl,0,acl,value.dacl.Length);security=Marshal.AllocHGlobal(40);
        bool okay=InitializeSecurityDescriptor(security,1);int nativeError=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(nativeError,"Security descriptor initialization failed");
        okay=SetSecurityDescriptorDacl(security,true,acl,(value.control&8)!=0);nativeError=okay?0:Marshal.GetLastWin32Error();if(!okay)throw new Win32Exception(nativeError,"DACL attachment failed");
        okay=SetKernelObjectSecurity(handle,4,security);error=okay?0:Marshal.GetLastWin32Error();return okay;
      }finally{try{if(security!=IntPtr.Zero)Marshal.FreeHGlobal(security);}finally{if(acl!=IntPtr.Zero)Marshal.FreeHGlobal(acl);}}
    }
    public bool Close(IntPtr handle,out int error){bool okay=CloseHandle(handle);error=okay?0:Marshal.GetLastWin32Error();return okay;}
    public void Dispose(){if(module==IntPtr.Zero)return;IntPtr owned=module;module=IntPtr.Zero;if(!FreeLibrary(owned))throw new Win32Exception(Marshal.GetLastWin32Error(),"Owned module release failed");}
  }
}
