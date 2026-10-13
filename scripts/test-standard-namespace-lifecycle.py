#!/usr/bin/env python3
"""Compile exact grant/restore/close control flow with synthetic native seams.

No constructor identity checks, native API, account, ACL, process or consent
mutation is executed. Native Windows A/B/A remains a separate mandatory gate.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(source, anchor):
    begin = source.index(anchor)
    start = source.index('{', begin)
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start + 1:end - 1]


def main():
    text = (ROOT / 'scripts/windows-standard-namespace-access.cs').read_text(encoding='utf-8')
    constructor = block(text, 'public StandardNamespaceAccess(')
    constructor = constructor[constructor.index('    bool writeAttempted=false;'):]
    dispose = block(text, 'public void Dispose()')
    close = block(text, 'Exception CloseOwned()')
    assert text.count('AccessAllowed,12,sid,false,null') == 1
    assert 'GetKernelObjectSecurity(obj,information,' in text
    assert 'ReadDescriptor(obj,7)' in text and 'ReadDescriptor(handle,16)' in text
    code = r'''
using System; using System.Collections.Generic; using System.ComponentModel;
using System.Runtime.ExceptionServices;
public class Fixture {
  public sealed class Facts {public bool writeAttempted,restorationVerified,cleanupOkay;}
  public readonly Facts facts=new Facts();
  IntPtr handle=new IntPtr(1);byte[] original,originalLabel,grantedAcl;Exception terminalFailure;
  object sid=new object();
  public static int Variant,State,Calls,CloseCalls;
  static byte[] Descriptor(IntPtr value){return new byte[]{(byte)State};}
  static byte[] ReadDescriptor(IntPtr value,uint information){if(information!=16)throw new Exception("Wrong label query");return new byte[]{1};}
  static byte[] Metadata(byte[] data){return data;}
  static byte[] WithCreateAccess(byte[] data,object sid){return new byte[]{2};}
  static byte[] Acl(byte[] data){return data;}
  bool CurrentEquals(byte[] data){return State==data[0];}
  static void Apply(IntPtr value,byte[] data){
    Calls++;
    if(data[0]==2){if(Variant==1)throw new Exception("setter before write");State=Variant==3?3:2;if(Variant==2)throw new Exception("setter after write");}
    else {if(Variant==4)throw new Exception("restore write failed");State=1;}
  }
  static bool CloseHandle(IntPtr value){CloseCalls++;return Variant!=5;}
  static class Marshal {public static int GetLastWin32Error(){return 6;}}
  public Fixture(){__CONSTRUCTOR__}
  Exception CloseOwned(){__CLOSE__}
  public void Dispose(){__DISPOSE__}
  public static void Require(bool value,string error){if(!value)throw new Exception(error);}
  public static void Run(){
    for(int variant=0;variant<=5;variant++){
      Variant=variant;State=1;Calls=CloseCalls=0;Fixture current=null;bool failed=false;
      try{current=new Fixture();}catch{failed=true;}
      if(variant==1){Require(failed&&State==1&&Calls==1&&CloseCalls==1,"Pre-write setter failure lost original");continue;}
      if(variant==2){Require(failed&&State==1&&Calls==2&&CloseCalls==1,"Partial setter failure not restored");continue;}
      if(variant==3){Require(failed&&State==3&&Calls==1&&CloseCalls==1,"Foreign change was overwritten");continue;}
      Require(!failed&&current.facts.writeAttempted&&State==2,"Grant state not registered");
      bool firstFailed=false,secondFailed=false;
      try{current.Dispose();}catch{firstFailed=true;}
      int count=Calls;
      try{current.Dispose();}catch{secondFailed=true;}
      Require(Calls==count&&CloseCalls==1,"Repeat Dispose repeated native mutation");
      if(variant==0)Require(!firstFailed&&!secondFailed&&State==1&&current.facts.cleanupOkay&&current.facts.restorationVerified,"Verified restoration not retained");
      else Require(firstFailed&&secondFailed&&!current.facts.cleanupOkay,"Failed restoration/close became success on repeat Dispose");
    }
    Variant=0;State=1;Calls=CloseCalls=0;var foreign=new Fixture();State=3;
    bool rejected=false;try{foreign.Dispose();}catch{rejected=true;}
    Require(rejected&&State==3&&Calls==1&&!foreign.facts.cleanupOkay,"Foreign post-grant change overwritten");
    Console.WriteLine("PASS: seven exact constructor/Dispose controls preserve write-attempt-before-set, partial rollback, foreign-drift refusal, repeated fatal restore/close and verified cleanup facts; native seams mocked only");
  }
}
'''.replace('__CONSTRUCTOR__', constructor).replace('__CLOSE__', close).replace('__DISPOSE__', dispose)
    pwsh = os.environ.get('PWSH_BIN') or shutil.which('pwsh')
    if not pwsh:
        raise SystemExit('PowerShell required for exact C# lifecycle controls')
    with tempfile.TemporaryDirectory(prefix='mixel-mask12-lifecycle-') as directory:
        root = Path(directory)
        (root / 'fixture.cs').write_text(code, encoding='utf-8', newline='\n')
        (root / 'run.ps1').write_text("$ErrorActionPreference='Stop'\nAdd-Type -Path (Join-Path $PSScriptRoot 'fixture.cs') -CompilerOptions /warnaserror+\n[Fixture]::Run()\n", encoding='utf-8', newline='\n')
        subprocess.run([pwsh,'-NoProfile','-NonInteractive','-File',str(root / 'run.ps1')],check=True,timeout=60)


if __name__ == '__main__':
    main()
