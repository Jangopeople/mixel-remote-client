# Pure actual-Windows managed regression: no constructor, native ACL write,
# account creation, process launch, token change, event or consent mutation.
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'Actual Windows is required for the managed namespace ACL regression.' }
if (('MixelCI.StandardNamespaceAccess' -as [type]) -or ('MixelCI.StandardNamespaceAccessManagedRegression' -as [type])) {
  throw 'The exact managed namespace regression requires a fresh PowerShell process.'
}
$managedDriver = @'
namespace MixelCI {
using System;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
public static class StandardNamespaceAccessManagedRegression {
  static void Require(bool value,string message) { if(!value) throw new InvalidOperationException(message); }
  static object Invoke(string name,params object[] args) {
    var method=typeof(StandardNamespaceAccess).GetMethod(name,BindingFlags.Static|BindingFlags.NonPublic);
    if(method==null) throw new InvalidOperationException("Exact namespace helper method is missing: "+name);
    try {return method.Invoke(null,args);} catch(TargetInvocationException error) {throw error.InnerException;}
  }
  static byte[] Acl(byte[] data) {return (byte[])Invoke("Acl",data);}
  static byte[] Grant(byte[] data,SecurityIdentifier sid) {return (byte[])Invoke("WithCreateAccess",data,sid);}
  static bool Equal(byte[] left,byte[] right) {return (bool)Invoke("Equal",left,right);}
  static void Reject(Action action,string message) {bool rejected=false;try{action();}catch(ArgumentException){rejected=true;}catch(InvalidOperationException){rejected=true;}catch(OverflowException){rejected=true;}Require(rejected,message);}
  static byte[] Binary(GenericAce ace) {var value=new byte[ace.BinaryLength];ace.GetBinaryForm(value,0);return value;}
  static byte[] Binary(GenericSecurityDescriptor descriptor) {var value=new byte[descriptor.BinaryLength];descriptor.GetBinaryForm(value,0);return value;}
  static byte[] Binary(GenericAcl acl) {var value=new byte[acl.BinaryLength];acl.GetBinaryForm(value,0);return value;}
  static bool Bytes(byte[] left,byte[] right) {if(left.Length!=right.Length)return false;for(int i=0;i<left.Length;i++)if(left[i]!=right[i])return false;return true;}
  static byte[] Copy(byte[] value) {return (byte[])value.Clone();}
  public static void Run() {
    var owned=new SecurityIdentifier("S-1-5-21-111-222-333-1001");
    var other=new SecurityIdentifier("S-1-5-21-111-222-333-1002");
    var world=new SecurityIdentifier("S-1-1-0");
    var dacl=new RawAcl(GenericAcl.AclRevisionDS,4);
    dacl.InsertAce(0,new CommonAce(AceFlags.None,AceQualifier.AccessDenied,0x100000,other,false,null));
    dacl.InsertAce(1,new CommonAce(AceFlags.ContainerInherit,AceQualifier.AccessAllowed,0x20000,world,true,new byte[]{0xaa,0xbb,0xcc,0xdd}));
    dacl.InsertAce(2,new ObjectAce(AceFlags.Inherited,AceQualifier.AccessAllowed,0x40000,other,ObjectAceFlags.ObjectAceTypePresent|ObjectAceFlags.InheritedObjectAceTypePresent,new Guid("11111111-2222-3333-4444-555555555555"),new Guid("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),false,null));
    dacl.InsertAce(3,new CommonAce(AceFlags.Inherited,AceQualifier.AccessAllowed,0x100000,world,false,null));
    var sacl=new RawAcl(GenericAcl.AclRevision,1);
    sacl.InsertAce(0,new CommonAce(AceFlags.SuccessfulAccess,AceQualifier.SystemAudit,0x100000,world,false,null));
    var flags=ControlFlags.DiscretionaryAclPresent|ControlFlags.DiscretionaryAclDefaulted|ControlFlags.DiscretionaryAclProtected|ControlFlags.DiscretionaryAclAutoInherited|ControlFlags.DiscretionaryAclAutoInheritRequired|ControlFlags.SystemAclPresent;
    var original=Binary(new RawSecurityDescriptor(flags,other,world,sacl,dacl));
    var frozen=Copy(original);var updated=Grant(original,owned);
    Require(Bytes(original,frozen),"Pure namespace grant mutated its original descriptor argument");
    var result=new RawSecurityDescriptor(updated,0);
    Require(result.DiscretionaryAcl.Count==dacl.Count+1,"Namespace grant added other than one ACE");
    Require(result.Owner.Equals(other) && result.Group.Equals(world) && Bytes(Binary(result.SystemAcl),Binary(sacl)),"Namespace grant changed owner, group or SACL");
    Require((result.ControlFlags&(ControlFlags)0x150c)==(new RawSecurityDescriptor(original,0).ControlFlags&(ControlFlags)0x150c),"Namespace grant changed existing DACL control flags");
    int found=0,oldIndex=0;
    for(int index=0;index<result.DiscretionaryAcl.Count;index++) {
      var ace=result.DiscretionaryAcl[index];var qualified=ace as QualifiedAce;
      if(qualified!=null && qualified.SecurityIdentifier.Equals(owned)) {
        var added=ace as CommonAce;
        Require(index==2 && added!=null && added.AceQualifier==AceQualifier.AccessAllowed && added.AccessMask==12 && added.AceFlags==AceFlags.None && !added.IsCallback && added.OpaqueLength==0,"Owned namespace ACE is not the one explicit create-object/subdirectory grant before inherited ACEs");
        found++;
      } else {
        Require(oldIndex<dacl.Count && Bytes(Binary(ace),Binary(dacl[oldIndex])),"Namespace grant changed an original ACE blob or relative order");oldIndex++;
      }
    }
    Require(found==1 && oldIndex==dacl.Count,"Owned namespace grant lost an original ACE or duplicated its own SID");
    Console.WriteLine("PASS: actual Windows helper preserves original deny/allow/callback/object/inherited ACE blobs and relative order, owner/group/SACL/DACL flags, and adds exactly one mask12/flags0 noncallback owned SID before inherited ACEs");
    // Test genuine native ACL padding rather than RawAcl's reserialized length.
    var minimal=Binary(new RawSecurityDescriptor(flags&~ControlFlags.SystemAclPresent,null,null,null,dacl));
    int offset=checked((int)BitConverter.ToUInt32(minimal,16));int oldSize=BitConverter.ToUInt16(minimal,offset+2);
    Require(offset+oldSize==minimal.Length,"Expected minimal fixture DACL at descriptor end");
    var padded=new byte[minimal.Length+8];Buffer.BlockCopy(minimal,0,padded,0,minimal.Length);Buffer.BlockCopy(BitConverter.GetBytes(checked((ushort)(oldSize+8))),0,padded,offset+2,2);
    for(int i=0;i<8;i++)padded[minimal.Length+i]=(byte)(0xe0+i);
    var paddedAcl=Acl(padded);Require(paddedAcl.Length==oldSize+10,"Raw helper discarded native unused ACL padding");
    var changed=Copy(padded);changed[changed.Length-1]^=1;
    Require(!Equal(paddedAcl,Acl(changed)),"Native padding difference was hidden by RawAcl normalization");
    changed=Copy(padded);changed[2]^=8;
    Require(!Equal(paddedAcl,Acl(changed)),"Native DACL defaulted flag difference was not retained");
    Require(Equal(paddedAcl,Acl(Copy(padded))) && !Equal(paddedAcl,null),"Native ACL comparison violated exact equality/null handling");
    Console.WriteLine("PASS: actual native padding and DACL-defaulted changes compare unequal; exact copied native DACL compares equal");
    var nullDacl=Binary(new RawSecurityDescriptor(ControlFlags.DiscretionaryAclPresent,null,null,null,null));
    Reject(()=>Acl(nullDacl),"Null native DACL was accepted");Reject(()=>Grant(nullDacl,owned),"Null native DACL grant was accepted");
    Reject(()=>Acl(null),"Null descriptor was accepted");Reject(()=>Acl(new byte[19]),"Truncated native descriptor was accepted");
    var oversized=new byte[65537];Buffer.BlockCopy(minimal,0,oversized,0,minimal.Length);
    Reject(()=>Acl(oversized),"Oversized native descriptor was accepted");Reject(()=>Grant(oversized,owned),"Oversized namespace grant was accepted");
    changed=Copy(minimal);changed[3]&=0x7f;Reject(()=>Acl(changed),"Non-self-relative native descriptor was accepted");
    changed=Copy(minimal);Buffer.BlockCopy(BitConverter.GetBytes(uint.MaxValue),0,changed,16,4);Reject(()=>Acl(changed),"Out-of-range native ACL offset was accepted");
    changed=Copy(minimal);Buffer.BlockCopy(BitConverter.GetBytes((ushort)7),0,changed,offset+2,2);Reject(()=>Acl(changed),"Undersized native ACL header was accepted");
    changed=Copy(minimal);Buffer.BlockCopy(BitConverter.GetBytes(ushort.MaxValue),0,changed,offset+2,2);Reject(()=>Acl(changed),"Out-of-range native ACL size was accepted");
    foreach(byte revision in new byte[]{0,2,0xff}) {
      changed=Copy(minimal);changed[0]=revision;
      Reject(()=>Acl(changed),"Malformed native security descriptor revision was accepted");
      Reject(()=>Grant(changed,owned),"Malformed security descriptor revision grant was accepted");
    }
    foreach(byte revision in new byte[]{0,1,3,0xff}) {
      changed=Copy(minimal);changed[offset]=revision;
      Reject(()=>Acl(changed),"Malformed native ACL revision was accepted");
      Reject(()=>Grant(changed,owned),"Malformed native ACL revision grant was accepted");
    }
    foreach(byte revision in new byte[]{2,4}) {
      var supportedAcl=new RawAcl(revision,1);
      supportedAcl.InsertAce(0,new CommonAce(AceFlags.None,AceQualifier.AccessAllowed,1,world,false,null));
      var supported=Binary(new RawSecurityDescriptor(ControlFlags.DiscretionaryAclPresent,null,null,null,supportedAcl));
      int supportedOffset=checked((int)BitConverter.ToUInt32(supported,16));
      Require(supported[0]==1 && supported[supportedOffset]==revision,"Supported native descriptor/ACL revision fixture was normalized unexpectedly");
      var extracted=Acl(supported);Require(extracted[2]==revision,"Supported native ACL revision was rejected or changed");
      var supportedGrant=new RawSecurityDescriptor(Grant(supported,owned),0);
      Require(supportedGrant.DiscretionaryAcl.Count==2 && Bytes(Binary(supportedGrant.DiscretionaryAcl[0]),Binary(supportedAcl[0])),"Supported native ACL revision grant lost its original ACE");
    }
    Console.WriteLine("PASS: actual helper accepts descriptor revision1 with native ACL revisions2/4 and rejects descriptor revisions0/2/255 and native ACL revisions0/1/3/255 for both extraction and grant");
    var collision=new RawAcl(GenericAcl.AclRevision,1);collision.InsertAce(0,new CommonAce(AceFlags.None,AceQualifier.AccessDenied,4,owned,false,null));
    Reject(()=>Grant(Binary(new RawSecurityDescriptor(ControlFlags.DiscretionaryAclPresent,null,null,null,collision)),owned),"Existing owned deny ACE was overwritten or duplicated");
    Console.WriteLine("PASS: actual helper rejects null, truncated, oversized, nonrelative, malformed/offset/size-invalid and pre-existing owned-SID collision fixtures");
    Console.WriteLine("SCOPE: actual Windows managed private-method regression only; no native ACL writes/accounts/processes/tokens/events and no product readiness inferred");
  }
}
}
'@
$exactHelper = Get-Content (Join-Path $PSScriptRoot 'windows-standard-namespace-access.cs') -Raw
Add-Type -TypeDefinition ($exactHelper + "`n" + $managedDriver) -CompilerOptions '/warnaserror+'
[MixelCI.StandardNamespaceAccessManagedRegression]::Run()
