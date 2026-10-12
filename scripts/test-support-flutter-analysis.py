#!/usr/bin/env python3
"""Prove pinned analyzer fixes and unchanged Client/terminal behavior."""
import argparse
import collections
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("mixel_flutter_analysis",ROOT/"scripts/patch-support-flutter-analysis.py")
P=importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def run(args,out,label,*,cwd=None,env=None,success=True):
    result=subprocess.run(args,cwd=cwd,env=env,capture_output=True,text=True,timeout=240)
    (out/(label+".log")).write_text(result.stdout+result.stderr)
    if success:
        assert result.returncode==0,result.stdout+result.stderr
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(repo):
    result={}
    for parent,dirs,names in os.walk(repo):
        dirs[:]=[name for name in dirs if name not in (".git","__pycache__",".dart_tool","build")]
        for name in names:
            if name==".git":
                continue
            path=Path(parent)/name
            result[path.relative_to(repo).as_posix()]=("symlink:"+os.readlink(path)
                if path.is_symlink() else sha(path.read_bytes()))
    return result


def pinned(source,name):
    return subprocess.check_output(["git","-C",str(source),"show",P.PIN+":"+name],timeout=60)


def pipeline(out,source):
    copied=out/"pipeline-source"
    copied.mkdir()
    archive=subprocess.check_output(["git","-C",str(ROOT),"archive","--format=tar","HEAD","scripts","branding"],timeout=60)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isfile():
                path=copied/member.name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(stream.extractfile(member).read())
    # Include the exact current root integration and the reviewed new helpers,
    # without mutating root-owned files.
    for name in ("apply-branding.sh","patch-support-macos-cleanup.py",
                 "patch-support-flutter-analysis.py","test-support-flutter-analysis.py"):
        shutil.copyfile(ROOT/"scripts"/name,copied/"scripts"/name)
    script=copied/"scripts/apply-branding.sh"
    shell_source="$"+"{BASH_SOURCE[0]}"
    hook='python3 "$(dirname "'+shell_source+'")/patch-support-flutter-analysis.py"\n'
    text=script.read_text()
    if hook not in text:
        script.write_text(text+"\n"+hook)
    else:
        assert text.count(hook)==1
    originals={name:pinned(source,name) for name in P.FILES if name!="flutter/lib/common.dart"}
    originals[P.CLIENT]=pinned(source,P.CLIENT)
    assert sha(originals[P.CLIENT])==P.CLIENT_SHA256
    for label,newline in (("lf",b"\n"),("crlf",b"\r\n")):
        repo=out/("pipeline-"+label)
        run(["git","clone","--quiet","--shared",str(source),str(repo)],out,"clone-"+label)
        run(["git","-C",str(repo),"checkout","--quiet",P.PIN],out,"checkout-"+label)
        library=source/"libs/hbb_common"
        library_source=str(library) if (library/".git").exists() else "https://github.com/rustdesk/hbb_common"
        run(["git","clone","--quiet",library_source,str(repo/"libs/hbb_common")],out,"hbb-clone-"+label)
        library_pin=subprocess.check_output(["git","-C",str(repo),"ls-tree","HEAD","libs/hbb_common"]).decode().split()[2]
        run(["git","-C",str(repo/"libs/hbb_common"),"checkout","--quiet",library_pin],out,"hbb-checkout-"+label)
        for name in P.FILES:
            (repo/name).write_bytes(pinned(source,name).replace(b"\n",newline))
        env=dict(os.environ,RDREPO=str(repo),BRANDING=str(copied/"branding"))
        run(["bash",str(script)],out,"pipeline-"+label+"-1",cwd=copied,env=env)
        for name,(_,corrected,*_) in P.FILES.items():
            assert sha((repo/name).read_bytes().replace(b"\r\n",b"\n"))==corrected,name
        first=snapshot(repo)
        run(["bash",str(script)],out,"pipeline-"+label+"-2",cwd=copied,env=env)
        assert snapshot(repo)==first,"Full pipeline second pass changed bytes"
        (out/("pipeline-"+label+"-snapshot.json")).write_text(json.dumps(first,sort_keys=True,indent=2)+"\n")
        name="flutter/lib/common.dart"
        target=repo/name
        saved=target.read_bytes()
        target.write_bytes(saved+b"// unapproved drift\n")
        before=snapshot(repo)
        failed=run(["bash",str(script)],out,"pipeline-"+label+"-refusal",cwd=copied,env=env,success=False)
        assert failed.returncode!=0 and "Pinned Flutter analysis input changed" in failed.stderr
        assert snapshot(repo)==before,"Drift refusal changed another pipeline file"
        target.write_bytes(saved)
        if label=="lf":
            common=saved.replace("import 'dart:convert';\n".encode(),"import 'dart:convert';\nimport 'dart:math';\n".encode(),1)
            assert sha(common)==P.FILES[name][0]
            originals[name]=common
    print("PASS: exact current complete branding and cleanup plus late analyzer hook; LF/CRLF twice byte-identical; drift fails without mutation")
    return originals


def fixture(root,files,newline=b"\n"):
    root.mkdir()
    (root/"Cargo.toml").write_bytes(b'[package]\nversion = "1.4.6"\n'.replace(b"\n",newline))
    for name,data in files.items():
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data.replace(b"\n",newline))


def refusal(root,out):
    before=snapshot(root)
    try:
        P.apply(root)
    except (RuntimeError,OSError,ValueError):
        assert snapshot(root)==before
        return
    raise AssertionError("Unqualified analyzer input was accepted")


def source_controls(out,files):
    for label,newline in (("lf",b"\n"),("crlf",b"\r\n")):
        root=out/("source-"+label)
        fixture(root,files,newline)
        before=snapshot(root)
        P.apply(root)
        after=snapshot(root)
        assert {name for name in before if before[name]!=after[name]}==set(P.FILES)
        for name in P.FILES:
            assert (root/name).read_bytes()==P.transform(name,files[name]).replace(b"\n",newline)
        P.apply(root)
        assert snapshot(root)==after
    count=0
    for name in list(P.FILES)+[P.CLIENT,"Cargo.toml"]:
        root=out/("refuse-"+name.replace("/","-").replace(".","-"))
        shutil.copytree(out/"source-lf",root)
        target=root/name
        target.write_bytes(target.read_bytes()+b"// drift\n")
        if name=="Cargo.toml":
            target.write_text('[package]\nversion = "1.4.5"\n')
        refusal(root,out);count+=1
    for label,operation in (
        ("symlink",lambda path: (path.unlink(),path.symlink_to(out/"source-lf"/P.CLIENT))),
        ("hardlink",lambda path: (path.unlink(),os.link(out/"source-lf"/P.CLIENT,path))),
        ("mixed-newlines",lambda path:path.write_bytes(files[P.CLIENT].replace(b"\n",b"\r\n",1))),
    ):
        root=out/("refuse-"+label)
        shutil.copytree(out/"source-lf",root)
        operation(root/P.CLIENT)
        refusal(root,out);count+=1
    for label,data in (
        ("missing-package",b'version = "1.4.6"\n'),
        ("wrong-package",b'[other]\nversion = "1.4.6"\n'),
        ("duplicate-package",b'[package]\nversion = "1.4.6"\n[package]\nversion = "1.4.6"\n'),
        ("duplicate-version",b'[package]\nversion = "1.4.6"\nversion = "1.4.6"\n'),
        ("dependency-only-version",b'[package]\nversion = "1.4.5"\n[dependencies]\nversion = "1.4.6"\n'),
    ):
        root=out/("refuse-"+label)
        shutil.copytree(out/"source-lf",root)
        (root/"Cargo.toml").write_bytes(data)
        refusal(root,out);count+=1
    print("PASS: exactly six target files change; original/corrected hash guards, LF/CRLF byte idempotence and "+str(count)+" fatal source/constructor/link/newline controls")


def function(source,anchor):
    start=source.index(anchor)
    first=source.index("{",start+len(anchor)-1)
    end,depth=first+1,1
    while depth:
        depth+=(source[end]=="{")-(source[end]=="}")
        end+=1
    return source[start:end]


def dart_controls(out,files,dart):
    client=files[P.CLIENT].decode()
    client=client[client.index("class Client {"):client.index("  Client.fromJson(")]+"}\n"
    prefix="""class RxInt { RxInt(this.value); final int value; }
extension ReactiveInt on int { RxInt get obs=>RxInt(this); }
"""
    for label,data in (("original",files["flutter/test/cm_test.dart"]),
                       ("corrected",P.transform("flutter/test/cm_test.dart",files["flutter/test/cm_test.dart"]))):
        source=data.decode()
        clients=source[source.index("final testClients"):source.index("\n/// flutter run")]
        main="""void main() {
  if(testClients.length!=4) throw StateError('Missing CM fixture clients');
  for(var i=0;i<testClients.length;i++) {
    final value=testClients[i];
    if(value.id!=i || value.authorized || value.isFileTransfer ||
        value.isViewCamera || value.isTerminal || value.file ||
        !value.keyboard || value.clipboard || value.audio ||
        value.name.isEmpty || value.peerId.isEmpty) {
      throw StateError('Nine-field constructor changed CM fixture identity/permission defaults');
    }
  }
  print('PASS: actual pinned Client nine-field constructor and four corrected CM clients preserve identity and false file/terminal defaults');
}
"""
        path=out/(label+"-client-control.dart")
        path.write_text(prefix+client+clients+main)
        result=run([str(dart),"analyze","--format=machine",str(path)],out,label+"-client-analyze",success=label=="corrected")
        if label=="original":
            errors=[line for line in result.stdout.splitlines() if line.startswith("ERROR|")]
            assert result.returncode!=0 and len(errors)==4 and all("|EXTRA_POSITIONAL_ARGUMENTS|" in line for line in errors)
        else:
            run([str(dart),str(path)],out,"actual-client-constructor")
    terminal="flutter/lib/mobile/pages/terminal_page.dart"
    original=function(files[terminal].decode(),"  void _sendKeyToTerminal(String key) {")
    corrected=function(P.transform(terminal,files[terminal]).decode(),"  void _sendKeyToTerminal(String key) {")
    program="""class TerminalModel {
  final keys=<String>[];
  void sendVirtualKey(String key) {keys.add(key);}
}
"""+ "\n".join("class "+name+" { final _terminalModel=TerminalModel();\n"+method+"\n}" for name,method in (
        ("Original",original),("Corrected",corrected)))+r"""
void main() {
  final original=Original(),corrected=Corrected();
  for(final key in ['Esc','Tab','Ctrl+C','↑','↓','→','←','Home','End','PgUp','PgDn','','a','é','🚀']) {
    original._sendKeyToTerminal(key);corrected._sendKeyToTerminal(key);
  }
  if(original._terminalModel.keys.join('|')!=corrected._terminalModel.keys.join('|')||
      corrected._terminalModel.keys.length!=15) {
    throw StateError('Removing redundant nullable check changed actual terminal key mapping');
  }
  print('PASS: actual original/corrected terminal methods emit identical control, navigation, empty, Unicode and ordinary keys');
}
"""
    path=out/"actual-terminal-key-control.dart"
    path.write_text(program)
    run([str(dart),str(path)],out,"actual-terminal-key-control")
    print("PASS: actual original Client fixture fails with exactly four argument errors; corrected actual constructor compiles/runs; all actual terminal key mappings conserved")


def diagnostic_counts(text):
    parts=[line.split(" • ") for line in text.splitlines() if " • " in line]
    return collections.Counter(part[0].strip() for part in parts),parts


def analyze_app(out,source,flutter,files):
    # The supplied complete dependency/codegen context is read-only. Restore
    # the exact pre-correction six files only in an owned analysis fixture, so
    # this proof also works after apply-branding has already applied the fix.
    source_before=snapshot(source/"flutter")
    for name in P.FILES:
        P.transform(name,(source/name).read_bytes())
    checkout=out/"complete-app-fixture"
    for name in ("lib","test","assets",".mixel-dependencies"):
        if (source/"flutter"/name).exists():
            shutil.copytree(source/"flutter"/name,checkout/"flutter"/name)
    for name in ("pubspec.yaml","pubspec.lock","analysis_options.yaml",".dart_tool/package_config.json"):
        target=checkout/"flutter"/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/"flutter"/name,target)
    shutil.copyfile(source/"Cargo.toml",checkout/"Cargo.toml")
    for name in P.FILES:
        (checkout/name).write_bytes(files[name])
    env=dict(os.environ,CI="true",FLUTTER_SUPPRESS_ANALYTICS="true",DART_SUPPRESS_ANALYTICS="true")
    before=snapshot(checkout/"flutter")
    baseline=run([str(flutter),"analyze","--no-pub","--no-fatal-infos"],out,"actual-full-app-baseline",cwd=checkout/"flutter",env=env,success=False)
    counts,_=diagnostic_counts(baseline.stdout+baseline.stderr)
    assert counts["error"]==4 and counts["warning"]==5,counts
    for name in P.FILES:
        path=out/"actual-original"/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((checkout/name).read_bytes())
    P.apply(checkout)
    corrected=run([str(flutter),"analyze","--no-pub","--no-fatal-infos"],out,"actual-full-app-corrected",cwd=checkout/"flutter",env=env)
    fixed,_=diagnostic_counts(corrected.stdout+corrected.stderr)
    assert fixed["error"]==0 and fixed["warning"]==0 and fixed["info"]==counts["info"],fixed
    after=snapshot(checkout/"flutter")
    assert {name for name in before if before[name]!=after[name]}=={name.removeprefix("flutter/") for name in P.FILES}
    assert snapshot(source/"flutter")==source_before,"Supplied analysis checkout changed"
    for name in P.FILES:
        path=out/"actual-corrected"/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((checkout/name).read_bytes())
    (out/"complete-app-analysis-proof.json").write_text(json.dumps(
        {"baseline_exit":baseline.returncode,"corrected_exit":corrected.returncode,
         "baseline":dict(counts),"corrected":dict(fixed),"exact_six_target_conservation":True,
         "source_sha256":{name:sha((ROOT/"scripts"/name).read_bytes()) for name in (
             "patch-support-flutter-analysis.py","test-support-flutter-analysis.py")}},sort_keys=True,indent=2)+"\n")
    print("PASS: real full Flutter3.24.5 application analyze: original4errors5warnings → corrected0errors0warnings; "+str(fixed["info"])+" existing info; exact six-file source conservation")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path(os.environ.get("RDREPO",ROOT/"rustdesk")))
    parser.add_argument("--evidence-dir",type=Path)
    parser.add_argument("--dart",type=Path)
    parser.add_argument("--flutter",type=Path)
    parser.add_argument("--analysis-checkout",type=Path,help="Read-only complete app/dependency/codegen context; verification builds an owned six-file baseline/fixed fixture")
    args=parser.parse_args()
    if bool(args.flutter)!=bool(args.analysis_checkout):
        parser.error("--flutter and --analysis-checkout must be supplied together")
    with tempfile.TemporaryDirectory(prefix="mixel-flutter-analysis-") as temporary:
        out=args.evidence_dir.resolve() if args.evidence_dir else Path(temporary)
        out.mkdir(parents=True,exist_ok=True)
        files=pipeline(out,args.source.resolve())
        source_controls(out,files)
        if args.dart:
            dart_controls(out,files,args.dart)
        if args.flutter:
            analyze_app(out,args.analysis_checkout.resolve(),args.flutter,files)
    print("PASS: pinned Flutter analyzer correction qualification")


if __name__=="__main__":
    main()
