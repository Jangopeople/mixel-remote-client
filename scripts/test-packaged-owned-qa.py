"""Ignored packaged adapter source/mock controls; never starts Windows apps or sets ACLs."""
import hashlib,importlib.util,json,os,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
S=ROOT/'scripts';DRIVER=S/'test-support-launch-windows.ps1';HELPER=S/'test-hosted-qa-session-directory.cs'
spec=importlib.util.spec_from_file_location('previous',S/'test-session-directory-access.py');PREVIOUS=importlib.util.module_from_spec(spec);spec.loader.exec_module(PREVIOUS)
EXPECTED_HELPER='b7f7886e2e6de9f7d76ef371473a436d8845b1396c12b63517331894d4611d4e'

def canonical_checkout_bytes(raw):
 if b'\r' in raw:
  remaining=raw.replace(b'\r\n',b'')
  if b'\r' in remaining or b'\n' in remaining:
   raise AssertionError('Mixed or bare-CR checkout source refused')
  raw=raw.replace(b'\r\n',b'\n')
 raw.decode('utf-8')
 return raw

def canonical_checkout_text(path):
 return canonical_checkout_bytes(path.read_bytes()).decode('utf-8')

QUERY_OPEN="if (-not ('MixelPackagedQaTokenFacts' -as [type])) { Add-Type @'\n"
QUERY_CLOSE="'@ -CompilerOptions /warnaserror+ -ErrorAction Stop }\n\n"
HISTORICAL_ORIGINAL_SHA='765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e'
# Exact composed launcher after reversing only this three-function adapter.
# Historical owner identity remains in the literal query binding below.
ORIGINAL_SHA='8d68c6edd8928b64048cf533f16227fe8de144679e3ddab97d8f39e339c34e10'
QUERY_SHA='539659449688beb79c3ff5dee18b817ef6e4c0a0542a5f3b3c735f6c9f4f9f50'

def original_source():
 text=canonical_checkout_text(DRIVER);start=text.index(QUERY_OPEN);end=text.index('$executablePath = (Resolve-Path $Executable).Path',start)
 text=text[:start]+text[end:]
 reversals=[
 ("$ordinaryStartedAt = $null\n$script:qaNamespaceScope=$null\n$script:qaNamespaceCleanupOkay=$true","$ordinaryStartedAt = $null"),
 ("        $desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)\n        $fixtureStage = 'qualify isolated hosted packaged QA namespace'\n        Start-OwnedPackagedQaNamespace","        $desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)"),
 ("  $qaNativeProcessesExited=$false\n  $qaCustomerProcessesExited=$false\n  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses(); $qaNativeProcessesExited=$true }","  try { [MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses() }"),
 ("  try { Stop-OwnedCustomerProcesses; $qaCustomerProcessesExited=$true }","  try { Stop-OwnedCustomerProcesses }"),
 ("  try { Restore-OwnedPackagedQaNamespace ($qaNativeProcessesExited -and $qaCustomerProcessesExited) }\n  catch { $nativeCrashCleanupFailed=$true; Write-Host 'FAIL: exact owned packaged namespace restoration/disposal additionally failed.' }\n",''),
 ("    Assert-OwnedPackagedQaCleanupBeforeDeletion\n",'')]
 for old,new in reversals:
  if text.count(old)!=1:raise AssertionError('Exact original source reverse projection changed')
  text=text.replace(old,new)
 if hashlib.sha256(text.encode()).hexdigest()!=ORIGINAL_SHA:raise AssertionError('Any original packaged launcher source changed')
 return text

def query_projection():
 text=canonical_checkout_text(DRIVER);start=text.index(QUERY_OPEN)+len(QUERY_OPEN);end=text.index(QUERY_CLOSE,start)
 projection=text[start:end]
 if hashlib.sha256(projection.encode()).hexdigest()!=QUERY_SHA:raise AssertionError('Pinned literal query projection changed')
 return projection

ORIGINAL_PIN_LOADER="""  $helper=Join-Path $PSScriptRoot 'test-hosted-qa-session-directory.cs'
  if (-not (Test-Path -LiteralPath $helper -PathType Leaf) -or
      (Get-Item -LiteralPath $helper).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
      (Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash.ToLowerInvariant() -cne 'b7f7886e2e6de9f7d76ef371473a436d8845b1396c12b63517331894d4611d4e') {
    throw 'Pinned hosted QA namespace helper differs; no directory query or write permitted.'
  }
  if (-not ('MixelHostedQaNamespace' -as [type])) { Add-Type -Path $helper -CompilerOptions /warnaserror+ -ErrorAction Stop }
"""

def original_raw_loader_source():
 text=canonical_checkout_text(DRIVER);start=text.index("  $helper=Join-Path $PSScriptRoot 'test-hosted-qa-session-directory.cs'");end=text.index('  # Constructor verifies',start)
 return text[:start]+ORIGINAL_PIN_LOADER+text[end:]

def invoke(body,csharp=None,helper_bytes=None,inputs=None):
 with tempfile.TemporaryDirectory(prefix='mixel-packaged-owned-qa-') as folder:
  folder=Path(folder);shutil.copyfile(HELPER,folder/HELPER.name)
  if helper_bytes is not None:(folder/HELPER.name).write_bytes(helper_bytes)
  for name,data in (inputs or {}).items():(folder/name).write_bytes(data)
  prefix="$ErrorActionPreference='Stop'\n"
  if csharp:
   (folder/'helper.cs').write_bytes(csharp.encode('utf-8'));prefix+="Add-Type -Path (Join-Path $PSScriptRoot 'helper.cs') -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
  (folder/'run.ps1').write_bytes((prefix+body).encode('utf-8-sig'))
  p=subprocess.run([shutil.which('pwsh'),'-NoProfile','-NonInteractive','-File',str(folder/'run.ps1')],capture_output=True,text=True,encoding='utf-8',timeout=90)
  if p.returncode:raise AssertionError(f'Exact source controls exit{p.returncode}\n{p.stdout}\n{p.stderr}')
  return p.stdout
FUNCTIONS=r'''
$t=$null;$e=$null;$tree=[Management.Automation.Language.Parser]::ParseFile('__DRIVER__',[ref]$t,[ref]$e)
if($e.Count){throw 'Complete actual packaged adapter parse refusal'}
$names=@('Start-OwnedPackagedQaNamespace','Restore-OwnedPackagedQaNamespace','Assert-OwnedPackagedQaCleanupBeforeDeletion')
$functions=@($tree.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -in $names})
if($functions.Count -ne 3){throw 'Exact packaged three-function family changed'}
$functionTexts=@(foreach($fn in $functions){
 $fn.Extent.Text.Replace('[Security.Principal.SecurityIdentifier]::new($ownedSid)','[AbaMock+Sid]::new($ownedSid)').Replace('[MixelHostedQaNamespace]::HostedScopeAllowed(', '[PackagedGuardMock]::Allowed(')
})
$functionPath=Join-Path $PSScriptRoot 'actual-functions.ps1'
[IO.File]::WriteAllText($functionPath,($functionTexts -join "`n"),[Text.UTF8Encoding]::new($true))
. $functionPath
'''.replace('__DRIVER__',str(DRIVER).replace("'","''"))
MOCK=PREVIOUS.MOCK.split('public static class MixelOrdinaryTokenFixture',1)[0].replace('MixelSessionDirectoryAba','MixelHostedQaNamespace').replace('mask==8','mask==15').replace('ToUInt32(a.descriptor,96)==8','ToUInt32(a.descriptor,96)==15')
MASK12=r'''
public static class PackagedGuardMock {
 public static bool permitted=true;
 public static bool Allowed(bool windows,bool x64,string actions,string repository,string runner,string job,string eventName,string reference,string workflow,string toggle,string context){return permitted;}
}
namespace MixelCI {
 public sealed class StandardNamespaceAccess : IDisposable {
  public sealed class Facts {public int grantMask=12;public bool writeAttempted,restorationVerified,cleanupOkay;}
  public static string Mode="normal";public static int constructions,disposals,writes,closes;public static StandardNamespaceAccess Last;
  public readonly Facts facts=new Facts();readonly string mode;Exception terminalFailure;bool closed;
  public StandardNamespaceAccess(string sid,string user,uint session){
   if(sid!="owned-local-qa-sid"||user!="mixelqs0123456789"||session!=2)throw new Exception("Foreign packaged mask12 identity");
   constructions++;mode=Mode;Last=this;
   if(mode=="query-fails"||mode=="open-denied"){closed=true;closes++;throw new Exception("Injected mask12 preparation failure");}
   facts.writeAttempted=true;writes++;
   if(mode=="set-false"||mode=="set-zero-false"||mode=="set-throw"){
    writes++;facts.restorationVerified=true;facts.cleanupOkay=true;closed=true;closes++;
    throw new Exception("Injected partial write; constructor rolled back before fatal return");
   }
  }
  public void Dispose(){
   disposals++;
   if(closed){if(terminalFailure!=null)throw terminalFailure;return;}
   if(mode=="restore-false"||mode=="restore-readback-drift"||mode=="foreign-drift")terminalFailure=new Exception("Injected mask12 restoration failure");
   else{writes++;facts.restorationVerified=true;}
   closes++;closed=true;
   if(mode=="close-false")terminalFailure=new Exception("Injected mask12 close failure");
   facts.cleanupOkay=(terminalFailure==null&&facts.restorationVerified);
   if(terminalFailure!=null)throw terminalFailure;
  }
 }
}
'''
FACTS=r'''
public static class MixelPackagedQaTokenFacts {
 public sealed class Fact {public uint sessionId=2,integrityRid;public bool sessionKnown=true,hasRestrictions,isAppContainer;public string unavailableKind,users="enabled",authenticatedUsers="enabled",interactive="enabled";}
 public static Fact caller=new Fact{integrityRid=12288},fresh=new Fact{integrityRid=8192};public static int queries;
 public static Fact RunnerTokenFacts(){queries++;return caller;}
 public static Fact FreshStandardTokenFacts(string name,string password,string sid){queries++;if(name!="mixelqs0123456789"||password!="memory-only"||sid!="owned-local-qa-sid")throw new Exception("Foreign identity/token query");return fresh;}
}
'''
SETUP=r'''
function Get-LocalUser{param($SID,$Name);if($script:removed){return $null};return [pscustomobject]@{Name=$ownedUser}}
function Get-Process{return [pscustomobject]@{SessionId=2}}
$ownedUser='mixelqs0123456789';$ownedSid='owned-local-qa-sid';$ownedPassword='memory-only';$desktopAccess=[object]::new()
$env:MIXEL_HOSTED_OWNED_QA_NAMESPACE='1'
$script:namespaceAccess=$null;$script:namespaceRestorationKnown=$true;$script:namespaceFacts=$null;$script:qaNamespaceCleanupOkay=$true;$primaryFailure=$null
[MixelCI.StandardNamespaceAccess]::Mode='normal'
'''
class PackagedControls(unittest.TestCase):
 def test_historical_mask15_scope_faults_remain_separate_from_current_adapter(self):
  candidate=canonical_checkout_text(DRIVER)
  start=candidate.index('function Start-OwnedPackagedQaNamespace {');end=candidate.index('function Restore-OwnedPackagedQaNamespace',start)
  for forbidden in ('MixelHostedQaNamespace+Scope','MixelHostedQaNamespace+NativeApi','.Open()','.Apply()'):
   self.assertNotIn(forbidden,candidate[start:end])
  body=r'''
foreach($mode in @('normal','set-false','set-zero-false','set-throw','query-fails','open-denied','restore-false','restore-readback-drift','close-false','unload-throw')){
 [AbaMock+Platform]::Mode=$mode;$platform=[AbaMock+Platform]::new()
 $scope=[MixelHostedQaNamespace+Scope]::new($platform,[AbaMock]::User())
 $startFailure=$null;try{$scope.Open();$scope.Apply()}catch{$startFailure=$_}
 $restoreFailure=$null;$disposeFailure=$null
 try{$scope.Restore()}catch{$restoreFailure=$_}
 finally{try{$scope.Dispose()}catch{$disposeFailure=$_}}
 $unsafe=$mode -in @('restore-false','restore-readback-drift','close-false','unload-throw')
 if(($null -ne $restoreFailure -or $null -ne $disposeFailure) -ne $unsafe -or $scope.facts.cleanupOkay -eq $unsafe){throw ('Historical cleanup attribution changed: '+$mode)}
 if($platform.unloads -ne 1){throw 'Independent historical module dispose skipped'}
 if($mode -in @('set-false','set-zero-false','set-throw') -and ($platform.sets -ne 2 -or -not $scope.facts.restorationVerified)){throw 'Historical partial setter did not restore exact original'}
}
'PASS: historical mask15 Scope ten pure fault cases retained separately; current packaged adapter never constructs that scope.'
'''
  print(invoke(body,HELPER.read_text()+MOCK).splitlines()[-1])
 def test_actual_mask12_constructor_and_dispose_bodies_with_mocked_native_seams(self):
  spec=importlib.util.spec_from_file_location('mask12_lifecycle',S/'test-standard-namespace-lifecycle.py')
  lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)
  lifecycle.main()
 def test_actual_shared_fence_compiles_and_unqualified_context_cannot_construct(self):
  self.assertEqual(hashlib.sha256(canonical_checkout_bytes(HELPER.read_bytes())).hexdigest(),EXPECTED_HELPER)
  body="Add-Type -Path '"+str(HELPER)+"' -CompilerOptions /warnaserror+ -ErrorAction Stop\n"
  body+="try{[MixelHostedQaNamespace+NativeApi]::new('UnqualifiedSourceControl')|Out-Null;throw 'Unexpected local host constructor'}catch{if($_.Exception.Message -ceq 'Unexpected local host constructor'){throw}}\n'PASS: complete shared native ABI warning-fatal compilation; exact local host refusal before native Open.'\n"
  print(invoke(body).strip())
 def test_original_whole_functions_and_all_runtime_gates_conserved(self):
  original=original_source();candidate=canonical_checkout_text(DRIVER)
  body=r'''
$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'original.ps1'),[ref]$t,[ref]$e);if($e.Count){throw 'Original parse'}
$t=$null;$e=$null;$b=[Management.Automation.Language.Parser]::ParseFile('__NEW__',[ref]$t,[ref]$e);if($e.Count){throw 'Candidate parse'}
$old=@($a.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
$new=@($b.EndBlock.Statements|Where-Object{$_ -is [Management.Automation.Language.FunctionDefinitionAst]})
foreach($f in $old){$same=@($new|Where-Object{$_.Name -ceq $f.Name});if($same.Count-ne 1 -or $same[0].Extent.Text.Replace("`r`n","`n") -cne $f.Extent.Text){throw ('Original function changed: '+$f.Name)}}
if($new.Count -ne $old.Count+3){throw 'Unexpected function family delta'}
'PASS: every complete original PowerShell function canonical bytes conserved across LF/CRLF.'
'''.replace('__NEW__',str(DRIVER).replace("'","''"))
  print(invoke(body,inputs={'original.ps1':original.encode()}).strip())
  cls=original[original.index('public static class MixelOrdinaryTokenFixture {'):original.index("\n'@ }",original.index('public static class MixelOrdinaryTokenFixture {'))]
  self.assertIn(cls,candidate)
  for first,last in [("    $fixtureStage = 'verify actual ordinary process token'","  } else {\n    $main = Start-CustomerApp"),("  $window = Wait-VisibleMain $main.Id","  foreach ($logRoot in @(")]:
   block=original[original.index(first):original.index(last,original.index(first))];self.assertIn(block,candidate)
  self.assertIn("if (-not [MixelOrdinaryTokenFixture]::Elevated($PID))",candidate)
  self.assertIn('$ordinaryPid = [MixelOrdinaryTokenFixture]::StartLinkedToken($ordinaryExecutable)',candidate)
  setup=candidate.index("        Start-OwnedPackagedQaNamespace\n")
  self.assertGreater(setup,candidate.index('$desktopAccess = [MixelOrdinaryTokenFixture+DesktopAccess]::new($ownedSid)'))
  self.assertLess(setup,candidate.index('Invoke-OwnedLiveCrashControl $ownedSid'))
  self.assertLess(setup,candidate.index('StartStandardUser($ordinaryExecutable'))
  restore=candidate.index('  try { Restore-OwnedPackagedQaNamespace ($qaNativeProcessesExited -and $qaCustomerProcessesExited) }')
  self.assertGreater(restore,candidate.rindex('try { Stop-OwnedCustomerProcesses;'))
  self.assertLess(restore,candidate.rindex('try { [MixelOrdinaryTokenFixture]::CloseStartedObservations() }'))
  deletion=candidate.index('    Assert-OwnedPackagedQaCleanupBeforeDeletion\n')
  self.assertGreater(deletion,candidate.rindex('if ($desktopAccess) { try { $desktopAccess.Dispose()'))
  self.assertLess(deletion,candidate.rindex('if ($ownedSid) {\n      $profileRemoved'))
 def test_exact_literal_query_projection_and_read_only_rights(self):
  body=canonical_checkout_text(DRIVER);projection=query_projection();self.assertIn(projection,body)
  original=canonical_checkout_text(S/'test-support-lease-standard-windows.ps1')
  binding={'literal_block_sha256': {'[StructLayout(LayoutKind.Sequential)] struct ObservedGroupsHeader': '4052847b1ee42734b79becb6840b71ac899cd6e52a24a4b9a00e7d8c07f027e0', '[StructLayout(LayoutKind.Sequential)] struct ObservedSidAttributes': '7de3ef63fd444b7dc32d79b36fe86607f14395f539b721e98f33d15c5d36e737', 'enum ObservedTokenClass': 'e6c82863b35706f245c8973b123ce950294b08a607c41ac1f686b430459f7084', 'public sealed class ObservedTokenFacts': '6b6d90f987d349e6565ee6832029c5a185a7f873515bd2cd24059a4ff638bff0', 'public static ObservedTokenFacts FreshStandardTokenFacts': 'b69729d38e632d69c9a20789b8691d8930ceabaa93d4403390b94fd8921480f3', 'public static ObservedTokenFacts RunnerTokenFacts': 'd5d14777d27f0301b27051ed5bdbcc6ac3f5e2f49bd7f7bbf2bec98ee8df9afc', 'sealed class ObservedTokenQueryFailure': 'c93f7eb3cdac6ad0b397e44946e1788056683e98621e1767ffd74bf8f2b76ef0', 'static IntPtr ObservedTokenBuffer': 'ca693d4f4c840444ad3e4f95298f80db5bb2c92cd5cc5266765f77ed0cb77935', 'static ObservedTokenFacts ReadObservedTokenFacts': 'bf80765fe3cfc43c4146ffce0b64b8bd6905814398dee354fa12706bc96bff44', 'static bool ObservedLogonSidEqual': '7b0b0d34e2ed55d1f8a986be37aee6c6ab243418d10da8d62a6ccc624ff50030', 'static bool ObservedTokenBool(': '97adc5ed939034c85f0f28ab200aff0200f8600ba4976871d83ba88b6a596001', 'static bool ObservedTokenBoolValue': 'a44fc0d5e73ebd54312e4fdc35704e5e738ef217783ea79ee0f02115a4fb96ea', 'static byte[] ObservedLogonSid(': 'f3ce2949481b25d0acf7761f14f455312efe16e71e9a8180ebdb0fc3ec0cb3f2', 'static byte[] ObservedLogonSidBytes': '83395b84e9d671f402f720f0aadba6b33840e7e1bc30c28b4ae4ac33164cb250', 'static string ObserveLogonSidComparison': '6200f92cd8f5c87432c601766c6ffdba54c6b5f3c8021ac1682ac3d9ec852ad1', 'static string ObservedBufferedSid': 'f17974fc212bd5054dde9dc3e7cbbb51073cc98523708e75456f2a63bcbdb490', 'static uint ObservedIntegrityRid': 'debb0abe968406e891d8785a8b26bf14dca414bed0fe810566c945fa36806564', 'static uint ObservedTokenDword': '0fd865bd259ec0b9e0adf91a646116eda6c8c476779357fd952eca727c4f922f', 'static void ObservedMemberships': '20fa43d13fac47102b9515cde2a1cc410e75acc98687bb87d49c8268dc8d0d67'}, 'native_api_rights_changed': False, 'native_close_adapter': 'One attempt; FALSE including native0 throws; no retry, failed handle ownership remains unresolved until process exit or explicitly harness-only disposal.', 'original_elevated_token_block_sha256': '5462e7bcb57fc13456baed8982a7ba9ba5c2c3631016cdf3d55c7e2ae541e290', 'original_launch_ps_sha256': '765b98c6324161018321eadb3528a14a844dc4dd1bee1df158c9f68db6c8af8e', 'original_standard_ps_sha256': 'a9b0bf8ee68209f78a82ca4260c9d35faf10449a25f5976775731e98042a057f', 'source_head': '572336bc9445b98a071d9922f345c375da47f716'}
  # Actual brace extraction has already retained every selected complete declaration/method.
  self.assertEqual(binding['original_launch_ps_sha256'],HISTORICAL_ORIGINAL_SHA)
  for name,digest in binding['literal_block_sha256'].items():
   start=original.index(name);at=original.index('{',start);depth=0;block=None
   for i in range(at,len(original)):
    if original[i]=='{':depth+=1
    elif original[i]=='}':
     depth-=1
     if depth==0:block=original[start:i+1];break
   self.assertIsNotNone(block);self.assertEqual(hashlib.sha256(block.encode()).hexdigest(),digest);self.assertIn(block,projection)
  self.assertNotIn('SetTokenInformation',projection);self.assertNotIn('AdjustTokenPrivileges',projection);self.assertNotIn('DuplicateToken',projection)
  self.assertIn('if(!success)throw new Win32Exception(error,"Owned packaged QA read-only token close failed")',projection)
  self.assertIn('if(identity.User.Value!=expectedSid || ElevatedToken(token))',projection)
  print(invoke("Add-Type -Path (Join-Path $PSScriptRoot 'token-projection.cs') -CompilerOptions /warnaserror+ -ErrorAction Stop\n'PASS: complete literal token query C# compilation; no Win32 token query executed.'\n",inputs={'token-projection.cs':projection.encode()}).strip())
 def test_actual_setup_and_restoration_partial_and_unknown_cases(self):
  body=FUNCTIONS+SETUP+r'''
foreach($mode in @('normal','set-false','set-zero-false','set-throw','query-fails','open-denied','restore-false','restore-readback-drift','close-false','foreign-drift')){
 [MixelCI.StandardNamespaceAccess]::Mode=$mode;[MixelCI.StandardNamespaceAccess]::constructions=0;[MixelCI.StandardNamespaceAccess]::disposals=0;[MixelCI.StandardNamespaceAccess]::writes=0;[MixelCI.StandardNamespaceAccess]::closes=0
 $script:namespaceAccess=$null;$script:namespaceRestorationKnown=$true;$script:namespaceFacts=$null;$script:qaNamespaceCleanupOkay=$true
 $startError=$null;try{Start-OwnedPackagedQaNamespace}catch{$startError=$_}
 $restoreError=$null;try{Restore-OwnedPackagedQaNamespace $true}catch{$restoreError=$_}
 $constructorFailed=$mode -in @('set-false','set-zero-false','set-throw','query-fails','open-denied')
 if(($null-ne $startError)-ne $constructorFailed){throw ('Constructor fatal attribution '+$mode)}
 $unsafe=$mode -ne 'normal'
 if(($null-ne $restoreError)-ne $unsafe -or $script:qaNamespaceCleanupOkay-eq $unsafe){throw ('Cleanup classification '+$mode)}
 $p=[MixelCI.StandardNamespaceAccess]::Last
 if($p.facts.grantMask-ne 12 -or [MixelCI.StandardNamespaceAccess]::constructions-ne 1){throw 'Single mask12 scope changed'}
 if($constructorFailed -and ($script:namespaceAccess -or $script:namespaceRestorationKnown -or $script:namespaceFacts)){throw 'Failed constructor falsely released quarantine'}
 if($mode-in @('set-false','set-zero-false','set-throw') -and ([MixelCI.StandardNamespaceAccess]::writes-ne 2 -or -not $p.facts.restorationVerified)){throw 'Partial write mock did not establish restored-but-fatal constructor case'}
 $writes=[MixelCI.StandardNamespaceAccess]::writes;$closes=[MixelCI.StandardNamespaceAccess]::closes
 $again=$null;try{Restore-OwnedPackagedQaNamespace $true}catch{$again=$_}
 if(($null-ne $again)-ne $unsafe -or [MixelCI.StandardNamespaceAccess]::writes-ne $writes -or [MixelCI.StandardNamespaceAccess]::closes-ne $closes){throw 'Repeated disposal became success or repeated native mutation'}
 $refused=$false;try{Assert-OwnedPackagedQaCleanupBeforeDeletion}catch{$refused=$true}
 if($refused-ne $unsafe){throw 'Deletion guard changed'}
}
'PASS: actual single-mask12 setup/restoration ten cases; partial-write fatal constructor keeps quarantine; repeated failed Dispose stays fatal without repeated writes/closes; unknown cleanup cannot delete account/root.'
'''
  print(invoke(body,HELPER.read_text()+MOCK+FACTS+MASK12).splitlines()[-1])
 def test_source_identity_unavailable_and_high_or_foreign_refuse_before_grant(self):
  body=FUNCTIONS+SETUP+r'''
foreach($bad in @('high','restricted','appcontainer','session','unknownsession','query0','users','authenticated','interactive','callerlow','name')){
 [MixelPackagedQaTokenFacts]::caller=[MixelPackagedQaTokenFacts+Fact]::new();[MixelPackagedQaTokenFacts]::caller.integrityRid=12288
 [MixelPackagedQaTokenFacts]::fresh=[MixelPackagedQaTokenFacts+Fact]::new();[MixelPackagedQaTokenFacts]::fresh.integrityRid=8192
 $ownedUser='mixelqs0123456789';$script:namespaceAccess=$null;$script:qaNamespaceCleanupOkay=$true
 switch($bad){
  high{[MixelPackagedQaTokenFacts]::fresh.integrityRid=12288}
  restricted{[MixelPackagedQaTokenFacts]::fresh.hasRestrictions=$true}
  appcontainer{[MixelPackagedQaTokenFacts]::fresh.isAppContainer=$true}
  session{[MixelPackagedQaTokenFacts]::fresh.sessionId=3}
  unknownsession{[MixelPackagedQaTokenFacts]::fresh.sessionKnown=$false}
  query0{[MixelPackagedQaTokenFacts]::fresh.unavailableKind='NativeError0'}
  users{[MixelPackagedQaTokenFacts]::fresh.users='deny-only'}
  authenticated{[MixelPackagedQaTokenFacts]::fresh.authenticatedUsers='disabled'}
  interactive{[MixelPackagedQaTokenFacts]::fresh.interactive='absent'}
  callerlow{[MixelPackagedQaTokenFacts]::caller.integrityRid=8192}
  name{$ownedUser='foreign'}
 }
 $refused=$false;$reason=$null;try{Start-OwnedPackagedQaNamespace}catch{$refused=$true;$reason=$_.Exception.Message}
 $expectedReason=if($bad-eq 'name'){'Exact newly owned packaged QA fallback account and desktop setup required.'}else{'Exact High-to-Medium owned standard-token same-session facts required before packaged bootstrap.'}
 if(-not $refused -or $script:namespaceAccess -or $reason-cne $expectedReason -or [MixelCI.StandardNamespaceAccess]::constructions-ne 0){throw ('Identity refusal attribution changed '+$bad+': '+$reason)}
}
$ownedUser='mixelqs0123456789';$env:MIXEL_HOSTED_OWNED_QA_NAMESPACE='0'
$refused=$false;try{Start-OwnedPackagedQaNamespace}catch{$refused=$true};if(-not $refused){throw 'Invalid toggle accepted'}
$env:MIXEL_HOSTED_OWNED_QA_NAMESPACE=$null;$script:namespaceAccess=$null;Start-OwnedPackagedQaNamespace
if($script:namespaceAccess){throw 'Unselected original fallback gained namespace mutation'}
$env:MIXEL_HOSTED_OWNED_QA_NAMESPACE='1';[PackagedGuardMock]::permitted=$false
$refused=$false;try{Start-OwnedPackagedQaNamespace}catch{$refused=$true};if(-not $refused -or [MixelCI.StandardNamespaceAccess]::constructions-ne 0){throw 'Foreign hosted scope reached mask12 grant'}
'PASS:11 exact identity/native-query0/refusal scenarios plus invalid/default toggle and hosted fence; no failed identity reaches mask12 constructor/grant.'
'''
  print(invoke(body,HELPER.read_text()+MOCK+FACTS+MASK12).splitlines()[-1])
 def test_failed_live_exit_restores_independently_retains_account_and_original_error(self):
  body=FUNCTIONS+SETUP+r'''
Start-OwnedPackagedQaNamespace;$p=[MixelCI.StandardNamespaceAccess]::Last
$refused=$false;try{Restore-OwnedPackagedQaNamespace $false}catch{$refused=$true}
if(-not $refused -or $script:qaNamespaceCleanupOkay -or [MixelCI.StandardNamespaceAccess]::writes-ne 2 -or [MixelCI.StandardNamespaceAccess]::closes-ne 1 -or [MixelCI.StandardNamespaceAccess]::disposals-ne 1 -or -not $script:namespaceFacts.restorationVerified -or $script:namespaceAccess){throw 'Unconfirmed process skipped independent restoration or qualified deletion'}
$primaryFailure=[Management.Automation.ErrorRecord]::new([InvalidOperationException]::new('ORIGINAL_NATIVE5'),'Original5',[Management.Automation.ErrorCategory]::InvalidOperation,$null)
$got=$null;try{Assert-OwnedPackagedQaCleanupBeforeDeletion}catch{$got=$_}
if($null-eq $got -or $got.Exception.Message-cne 'ORIGINAL_NATIVE5'){throw 'Unknown ownership replaced original5'}
Restore-OwnedPackagedQaNamespace $true;Assert-OwnedPackagedQaCleanupBeforeDeletion
if([MixelCI.StandardNamespaceAccess]::writes-ne 2 -or [MixelCI.StandardNamespaceAccess]::closes-ne 1 -or [MixelCI.StandardNamespaceAccess]::disposals-ne 1 -or -not $script:qaNamespaceCleanupOkay){throw 'Late confirmed exit did not drain exactly once'}
Restore-OwnedPackagedQaNamespace $true
if([MixelCI.StandardNamespaceAccess]::writes-ne 2 -or [MixelCI.StandardNamespaceAccess]::closes-ne 1 -or [MixelCI.StandardNamespaceAccess]::disposals-ne 1){throw 'Repeated restored scope double-cleaned'}
'PASS: failed/live exit independently restores once; account/root retained, later confirmed exit reuses verified cleanup; original5 remains first fatal.'
'''
  print(invoke(body,HELPER.read_text()+MOCK+FACTS+MASK12).splitlines()[-1])
 def test_actual_canonical_helper_loader_lf_crlf_and_exact_fatal_profiles(self):
  raw=canonical_checkout_bytes(HELPER.read_bytes());self.assertEqual(hashlib.sha256(raw).hexdigest(),EXPECTED_HELPER)
  constructor_replacement=".Replace('[MixelHostedQaNamespace]::HostedScopeAllowed(', '[PackagedGuardMock]::Allowed(')"
  real_loader_functions=FUNCTIONS.replace(constructor_replacement,'')
  self.assertNotEqual(real_loader_functions,FUNCTIONS)
  tiny_sid='public static class AbaMock { public sealed class Sid { public Sid(string value){} } }'
  real_loader_setup=SETUP.replace("[MixelCI.StandardNamespaceAccess]::Mode='normal'\n",'')
  for name,value in [('lf',raw),('crlf',raw.replace(b'\n',b'\r\n'))]:
   body=real_loader_functions+real_loader_setup+"$env:GITHUB_ACTIONS='source-control-only'\n"+r"""
$reason=$null;try{Start-OwnedPackagedQaNamespace}catch{$reason=$_.Exception.Message}
if($reason-cne 'Exact opt-in hosted packaged scope required.' -or -not ('MixelHostedQaNamespace'-as[type]) -or $script:namespaceAccess){throw ('Canonical loader/pure fence boundary changed: '+$reason)}
'PASS: canonical loader compiles exact helper before local pure hosted fence refusal; no native Open.'
"""
   self.assertIn('PASS: canonical loader',invoke(body,tiny_sid,helper_bytes=value))
  for name,value,reason in [
   ('mixed',raw.replace(b'\n',b'\r\n',1),'Mixed hosted QA helper newlines refused.'),
   ('barecr',raw+b'\r','Mixed hosted QA helper newlines refused.'),
   ('tampered',raw+b'// changed\n','Pinned hosted QA namespace helper differs; no directory query or write permitted.'),
   ('bom',b'\xef\xbb\xbf'+raw,'Pinned hosted QA namespace helper differs; no directory query or write permitted.')]:
   body=real_loader_functions+real_loader_setup+"$env:GITHUB_ACTIONS='source-control-only'\n"+f"$expected='{reason}'\n"+r"""
$reason=$null;try{Start-OwnedPackagedQaNamespace}catch{$reason=$_.Exception.Message}
if($reason-cne $expected -or ('MixelHostedQaNamespace'-as[type]) -or $script:namespaceAccess){throw ('Malformed loader failure attribution changed: '+$reason)}
'PASS: loader refusal precedes compile, token query and namespace Open.'
"""
   self.assertIn('PASS: loader refusal',invoke(body,tiny_sid,helper_bytes=value))
  old=FUNCTIONS.replace("'"+str(DRIVER)+"'","(Join-Path $PSScriptRoot 'old-loader.ps1')")+SETUP+r"""
$reason=$null;try{Start-OwnedPackagedQaNamespace}catch{$reason=$_.Exception.Message}
if($reason-cne 'Pinned hosted QA namespace helper differs; no directory query or write permitted.' -or $script:namespaceAccess){throw 'Original raw CRLF pin refusal lost'}
'PASS: original raw LF-only pin rejects genuine CRLF helper.'
"""
  self.assertIn('PASS: original raw LF-only',invoke(old,HELPER.read_text()+MOCK+FACTS+MASK12,helper_bytes=raw.replace(b'\n',b'\r\n'),inputs={'old-loader.ps1':original_raw_loader_source().encode()}))
  print('PASS: literal LF/CRLF canonical Add-Type loader; four exact malformed refusals; original CRLF refusal preserved.')
 def test_actual_complete_lf_crlf_checkout_profiles_and_strict_canonical_refusals(self):
  cases=['PackagedControls.test_actual_shared_fence_compiles_and_unqualified_context_cannot_construct',
         'PackagedControls.test_original_whole_functions_and_all_runtime_gates_conserved',
         'PackagedControls.test_actual_canonical_helper_loader_lf_crlf_and_exact_fatal_profiles']
  raw=canonical_checkout_bytes(HELPER.read_bytes())
  self.assertEqual(hashlib.sha256(raw).hexdigest(),EXPECTED_HELPER)
  self.assertEqual(canonical_checkout_bytes(raw.replace(b'\n',b'\r\n')),raw)
  for malformed in (raw.replace(b'\n',b'\r\n',1),raw+b'\r'):
   with self.assertRaisesRegex(AssertionError,'Mixed or bare-CR checkout source refused'):
    canonical_checkout_bytes(malformed)
  for changed in (raw+b'// changed\n',b'\xef\xbb\xbf'+raw):
   self.assertNotEqual(hashlib.sha256(canonical_checkout_bytes(changed)).hexdigest(),EXPECTED_HELPER)
  for profile in ('lf','crlf'):
   with tempfile.TemporaryDirectory(prefix='mixel-packaged-fixture-profile-') as folder:
    scripts=Path(folder)/'scripts'
    shutil.copytree(S,scripts,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for path in scripts.rglob('*'):
     if not path.is_file() or path.suffix not in ('.py','.sh','.ps1','.rs','.dart','.swift','.h','.mm','.c','.cpp','.cs','.bat'):
      continue
     source=canonical_checkout_bytes(path.read_bytes())
     path.write_bytes(source if profile=='lf' else source.replace(b'\n',b'\r\n'))
    result=subprocess.run([os.sys.executable,'-B','-Werror',str(scripts/'test-packaged-owned-qa.py'),*cases,'-v'],
                          capture_output=True,text=True,encoding='utf-8',timeout=90)
    self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
    self.assertIn('Ran 3 tests',result.stderr)
    self.assertIn('\nOK\n',result.stderr)
  print('PASS: three former packaged fixture failures from complete LF/CRLF checkouts; strict mixed/bare-CR/hash drift/BOM controls retained; no recursive profile test or native APIs.')
 def test_literal_complete_outer_finally_restores_after_stop_failures_and_retains_primary(self):
  body=FUNCTIONS+SETUP+r"""
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('__DRIVER__',[ref]$t,[ref]$e)
$blocks=@($ast.FindAll({param($n) $n-is[Management.Automation.Language.TryStatementAst] -and $n.Finally -and $n.Finally.Extent.Text.Contains('actual retained owned native process termination additionally failed')},$true))
if($e.Count -or $blocks.Count-ne 1){throw 'Literal whole outer cleanup extraction changed'}
$final=$blocks[0].Finally.Extent.Text
if(-not $final.StartsWith('{') -or -not $final.EndsWith('}')){throw 'Outer finally brace envelope changed'}
$final=$final.Substring(1,$final.Length-2)
$projections=[ordered]@{
 '[MixelOrdinaryTokenFixture]::StopOwnedStartedProcesses()'='Stop-MockedStartedOwners';
 '[MixelOrdinaryTokenFixture]::CloseStartedObservations()'='Close-MockedStartedObservations';
 '[MixelOrdinaryTokenFixture]::RemoveProfile($ownedSid)'='Remove-MockedProfile $ownedSid';
 '[Security.Principal.SecurityIdentifier]::new($ownedSid)'='[AbaMock+Sid]::new($ownedSid)'
}
foreach($projection in $projections.GetEnumerator()){
 if(($final.Split(@($projection.Key),[StringSplitOptions]::None).Length-1)-ne 1){throw ('Outer exact API projection changed '+$projection.Key)}
 $final=$final.Replace($projection.Key,$projection.Value)
}
function Start-Sleep{}
function Stop-MockedStartedOwners{$script:nativeStops++;if($mode-in@('native-stop','both-stop')){throw 'Owned native stop failed'}}
function Stop-OwnedCustomerProcesses{$script:customerStops++;if($mode-in@('customer-stop','both-stop')){throw 'Owned customer stop failed'}}
function Close-MockedStartedObservations{
 $script:observationCloses++
 if([MixelCI.StandardNamespaceAccess]::disposals-ne 1){throw 'Namespace restoration was not independently attempted before observation close'}
 if($mode-eq 'observation-close'){throw 'Owned observation close failed'}
}
function Remove-MockedProfile{$script:profiles++}
function Remove-LocalUser{param($SID);$script:accounts++}
function Remove-Item{param($Path,[switch]$Recurse,[switch]$Force);$script:roots++}
foreach($mode in @('normal','native-stop','customer-stop','both-stop','restore-false','close-false','foreign-drift','observation-close')){
 $script:nativeStops=0;$script:customerStops=0;$script:observationCloses=0;$script:profiles=0;$script:accounts=0;$script:roots=0;$script:desktopRestores=0
 $script:namespaceAccess=$null;$script:namespaceRestorationKnown=$true;$script:namespaceFacts=$null;$script:qaNamespaceCleanupOkay=$true
 [MixelCI.StandardNamespaceAccess]::constructions=0;[MixelCI.StandardNamespaceAccess]::disposals=0;[MixelCI.StandardNamespaceAccess]::writes=0;[MixelCI.StandardNamespaceAccess]::closes=0
 [MixelCI.StandardNamespaceAccess]::Mode=if($mode-in@('restore-false','close-false','foreign-drift')){$mode}else{'normal'}
 $desktopAccess=[pscustomobject]@{};$desktopAccess|Add-Member -MemberType ScriptMethod -Name Dispose -Value {$script:desktopRestores++}
 Start-OwnedPackagedQaNamespace;$p=[MixelCI.StandardNamespaceAccess]::Last
 $nativeCrash=$null;$ordinaryPid=$null;$ordinaryProfileRoot=$null;$ordinaryRoot='owned-payload';$nativeCrashCleanupFailed=$false
 $primaryFailure=[Management.Automation.ErrorRecord]::new([InvalidOperationException]::new('ORIGINAL_NATIVE5'),'Original5',[Management.Automation.ErrorCategory]::InvalidOperation,$null)
 $caught=$null
 $script:packagedCredentialsCleared=$false
 $credentialFence="`$ownedPassword='mock-secret';`$random='mock-secret';try {`n"+$final+"`n} finally { `$script:packagedCredentialsCleared=(`$null -eq `$ownedPassword -and `$null -eq `$random) }"
 try{try{throw $primaryFailure}finally{& ([scriptblock]::Create($credentialFence))}}catch{$caught=$_}
 if(-not $caught -or $caught.Exception.Message-cne 'ORIGINAL_NATIVE5'){throw ('Original primary error was lost '+$mode)}
 if(-not $script:packagedCredentialsCleared){throw ('Temporary credentials survived outer cleanup '+$mode)}
 if($script:nativeStops-ne 1 -or $script:customerStops-ne 1 -or $script:observationCloses-ne 1 -or $script:desktopRestores-ne 1 -or [MixelCI.StandardNamespaceAccess]::disposals-ne 1 -or [MixelCI.StandardNamespaceAccess]::constructions-ne 1){throw ('Independent complete cleanup or single mask12 scope was skipped '+$mode)}
 $unsafe=$mode-in@('native-stop','customer-stop','both-stop','restore-false','close-false','foreign-drift')
 if($unsafe){
  if($script:qaNamespaceCleanupOkay -or $script:profiles-ne 0 -or $script:accounts-ne 0 -or $script:roots-ne 0){throw ('Unknown cleanup deleted recovery resources '+$mode)}
 }else{
  if(-not $script:qaNamespaceCleanupOkay -or $script:profiles-ne 1 -or $script:accounts-ne 1 -or $script:roots-ne 1){throw ('Verified cleanup did not preserve original deletion behavior '+$mode)}
 }
 if($mode-notin@('restore-false','foreign-drift') -and ([MixelCI.StandardNamespaceAccess]::writes-ne 2 -or [MixelCI.StandardNamespaceAccess]::closes-ne 1 -or -not $script:namespaceFacts.restorationVerified)){throw ('Stop failure retained temporary shared-directory grant '+$mode)}
 if($mode-in@('restore-false','close-false','foreign-drift') -and $script:namespaceFacts.cleanupOkay){throw 'Unsuccessful native cleanup labelled fully closed'}
}
'PASS: literal whole outer finally eight fault orders; one mask12 scope, independent restoration before observation close, desktop restore attempted, temporary credentials cleared, unknown ownership retains account/profile/root, original5 unchanged.'
""".replace('__DRIVER__',str(DRIVER).replace("'","''"))
  print(invoke(body,HELPER.read_text()+MOCK+FACTS+MASK12).splitlines()[-1])
if __name__=='__main__':unittest.main(verbosity=2)
