#!/usr/bin/env python3
"""Execute original/corrected Mac cleanup futures and exact ownership controls."""
import argparse
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

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mixel_cleanup_patch", ROOT / "scripts/patch-support-macos-cleanup.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
TERMINATION_SPEC = importlib.util.spec_from_file_location("mixel_cleanup_exit_composition", ROOT / "scripts/patch-support-macos-termination.py")
T = importlib.util.module_from_spec(TERMINATION_SPEC)
TERMINATION_SPEC.loader.exec_module(T)

sdk_spec=importlib.util.spec_from_file_location("mixel_sdk_compatibility_composition",ROOT/"scripts/patch-support-flutter-sdk-compatibility.py")
S=importlib.util.module_from_spec(sdk_spec)
sdk_spec.loader.exec_module(S)

file_transfer_spec=importlib.util.spec_from_file_location("mixel_file_transfer_composition",ROOT/"scripts/patch-support-file-transfer-window.py")
F=importlib.util.module_from_spec(file_transfer_spec)
file_transfer_spec.loader.exec_module(F)


def source_files(source):
    archive_args = ["git", "-C", str(source), "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", P.PIN] + list(P.FILES)
    archive = subprocess.check_output(archive_args, timeout=60)
    inherited = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.autocrlf", GIT_CONFIG_VALUE_0="true")
    assert subprocess.check_output(archive_args, env=inherited, timeout=60) == archive, "Inherited Git CRLF changed the pinned cleanup source"
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        files = {member.name: stream.extractfile(member).read() for member in stream if member.isfile()}
    assert set(files) == set(P.FILES)
    assert all(P.sha(data) == P.FILES[name] for name, data in files.items())
    return files


def fixture(root, files, newline=b"\n"):
    root.mkdir()
    (root / "Cargo.toml").write_bytes(
        b'[package]\nversion = "1.4.6"\n'.replace(b"\n", newline))
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.replace(b"\n", newline))


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def refusal(operation):
    try:
        operation()
    except (RuntimeError, ValueError, OSError):
        return
    raise AssertionError("Changed cleanup ownership accepted")


def materialization(out, files):
    for profile in P.PAGE_TYPES:
        name="flutter/lib/desktop/pages/"+profile+"_page.dart"
        anchor="  Future<void> dispose() async {" if profile in ("remote","view_camera") else "  void dispose() {"
        original=P.function(files[name].decode(),anchor)
        changed=P.function(P.transform(name,files[name]).decode(),anchor)
        mac=P.function(changed,"    if (isMacOS) {")
        assert changed.split(mac,1)[1].rsplit("}",1)[0].strip()==original.split("{",1)[1].rsplit("}",1)[0].strip()
    for label, newline in (("lf", b"\n"), ("crlf", b"\r\n")):
        root = out / label
        fixture(root, files, newline)
        P.apply(root)
        before = snapshot(root)
        P.apply(root)
        assert snapshot(root) == before
        for name, original in files.items():
            corrected = P.transform(name, original).replace(b"\n", newline)
            assert (root / name).read_bytes() == corrected
            assert (root / P.OWNED / name).read_bytes() == original
        assert (root / P.GENERATED).read_bytes() == P.UTILITY.encode()
    count = 0
    for label, cargo in (
        ("wrong-package-version", '[package]\nversion = "1.4.7"\n[dependencies]\nversion = "1.4.6"\n'),
        ("missing-package", '[dependencies]\nversion = "1.4.6"\n'),
        ("duplicate-package", '[package]\nversion = "1.4.6"\n[package]\nversion = "1.4.6"\n'),
        ("duplicate-version", '[package]\nversion = "1.4.6"\nversion = "1.4.6"\n'),
        ("missing-version", '[package]\nname = "fixture"\n[dependencies]\nversion = "1.4.6"\n'),
    ):
        root = out / label
        fixture(root, files)
        (root / "Cargo.toml").write_text(cargo)
        before = snapshot(root)
        refusal(lambda: P.apply(root))
        assert snapshot(root) == before
        count += 1
    for name in files:
        root = out / ("source-drift-" + Path(name).stem)
        fixture(root, files)
        (root / name).write_bytes(files[name] + b"// drift\n")
        before = snapshot(root)
        refusal(lambda: P.apply(root))
        assert snapshot(root) == before
        count += 1
    for label, mutate in (
        ("target-drift", lambda root: (root / next(iter(files))).write_text("drift")),
        ("helper-drift", lambda root: (root / P.GENERATED).write_text("drift")),
        ("owned-source-drift", lambda root: (root / P.OWNED / next(iter(files))).write_text("drift")),
        ("extra-member", lambda root: (root / P.OWNED / "unexpected").write_text("drift")),
        ("extra-directory", lambda root: (root / P.OWNED / "unexpected").mkdir()),
        ("owned-directory-symlink", lambda root: (root / P.OWNED / "linked").symlink_to(out / "lf" / P.OWNED, target_is_directory=True)),
        ("extra-provenance-key", lambda root: (root / P.OWNED / "proof.json").write_text(
            json.dumps(dict(json.loads((root / P.OWNED / "proof.json").read_text()), extra="drift")))),
        ("wrong-pin", lambda root: (root / P.OWNED / "proof.json").write_text("{}")),
        ("boolean-schema", lambda root: (root / P.OWNED / "proof.json").write_text(
            json.dumps(dict(json.loads((root / P.OWNED / "proof.json").read_text()), schema=True)))),
        ("target-symlink", lambda root: ((root / P.GENERATED).unlink(), (root / P.GENERATED).symlink_to(out / "lf" / P.GENERATED))),
        ("target-hardlink", lambda root: ((root / P.GENERATED).unlink(), os.link(out / "lf" / P.GENERATED, root / P.GENERATED))),
    ):
        root = out / label
        shutil.copytree(out / "lf", root)
        mutate(root)
        before = snapshot(root)
        refusal(lambda: P.apply(root))
        assert snapshot(root) == before
        count += 1
    print("PASS: original non-Mac disposal bodies retained; exact 12-file owned inventory, LF/CRLF preservation, byte idempotence and " + str(count) + " fatal source/ownership controls")


def pipeline_snapshot(repo):
    result={}
    for parent,dirs,names in os.walk(repo):
        dirs[:]=[name for name in dirs if name!=".git"]
        for name in names:
            path=Path(parent)/name
            if name==".git":
                continue
            key=path.relative_to(repo).as_posix()
            result[key]=("symlink:"+os.readlink(path) if path.is_symlink()
                         else hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def full_branding(out, files, source, bash=None):
    """Run the exact current pipeline and validate both owned correction layers."""
    analyzer_spec=importlib.util.spec_from_file_location("mixel_cleanup_branding_shell",ROOT/"scripts/test-support-flutter-analysis.py")
    analyzer=importlib.util.module_from_spec(analyzer_spec)
    analyzer_spec.loader.exec_module(analyzer)
    bash=analyzer.branding_bash(bash,os.name=="nt")
    copied=out/"branding-pipeline-copy"
    copied.mkdir()
    revision=subprocess.check_output(["git","-C",str(ROOT),"rev-parse","HEAD"],text=True).strip()
    archive_args=["git","-C",str(ROOT),"-c","core.autocrlf=false","-c","core.eol=lf","archive","--format=tar",
                  revision,"scripts","branding"]
    archive=subprocess.check_output(archive_args,timeout=60)
    inherited=dict(os.environ,GIT_CONFIG_COUNT="1",GIT_CONFIG_KEY_0="core.autocrlf",GIT_CONFIG_VALUE_0="true")
    assert subprocess.check_output(archive_args,env=inherited,timeout=60)==archive,"Inherited Git CRLF changed the exact pipeline source"
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isfile():
                target=copied/member.name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(stream.extractfile(member).read())
    for name in ("apply-branding.sh","patch-support-url-log-privacy.py","test-support-url-log-privacy.py","patch-support-macos-cleanup.py","test-support-macos-cleanup.py",
                 "patch-support-macos-termination.py","patch-support-flutter-sdk-compatibility.py",
                 "test-support-flutter-sdk-compatibility.py","test-support-flutter-analysis.py",
                 "test-support-macos-termination.py","test-support-macos-window.py",
                 "patch-support-file-transfer-window.py","test-support-file-transfer-window.py",
                 "patch-support-file-dialog-lifecycle.py","test-support-file-dialog-lifecycle.py",
                 "patch-support-keyboard-default.py",
                 "patch-support-uinput-scroll.py",
                 "patch-support-uinput-device.py","test-support-uinput-device.py",
                 "patch-support-native-warnings.py","test-support-native-warnings.py",
                 "patch-support-compiler-cleanup.py","test-support-compiler-cleanup.py",
                 "patch-support-uri-routing.py","test-support-uri-routing.py",
                 "patch-support-macos-gui-launch.py","test-support-macos-gui-launch.py","support-macos-gui-launch.rs",
                 "patch-support-fingerprint.py","test-support-fingerprint.py",
                 "patch-support-fatal-crash.py","test-support-fatal-crash.py",
                 "patch-macos-update.py","test-macos-update.py","macos-update.scpt","macos-update-bundle.sh"):
        shutil.copyfile(ROOT/"scripts"/name,copied/"scripts"/name)
    analyzer.copy_current_pipeline_inputs(copied)
    script=copied/"scripts/apply-branding.sh"
    original=script.read_text()
    shell_source="$"+"{BASH_SOURCE[0]}"
    anchor='python3 "$(dirname "'+shell_source+'")/patch-support-macos-window.py"\n'
    assert original.count(anchor)==1
    hook='python3 "$(dirname "'+shell_source+'")/patch-support-macos-cleanup.py"\n'
    assert original.count(hook)==1 and original.count(anchor+hook)==1
    proof={"root_head":revision,
           "actual_cleanup_hook":"immediately after patch-support-macos-window.py",
           "pipeline_source_sha256":hashlib.sha256(archive).hexdigest(),
           "pipeline_script_sha256":hashlib.sha256(script.read_bytes()).hexdigest(),
           "cleanup_helper_sha256":hashlib.sha256((copied/"scripts/patch-support-macos-cleanup.py").read_bytes()).hexdigest(),
           "termination_helper_sha256":hashlib.sha256((copied/"scripts/patch-support-macos-termination.py").read_bytes()).hexdigest(),
           "inherited_Git_CRLF_cannot_change_sources":True,
           "results":{}}
    for label,newline in (("lf",b"\n"),("crlf",b"\r\n")):
        repo=out/("full-branding-"+label)
        subprocess.run(["git","-c","core.autocrlf=false","clone","--quiet","--shared",str(source),str(repo)],check=True,timeout=60)
        subprocess.run(["git","-C",str(repo),"-c","core.autocrlf=false","checkout","--quiet",P.PIN],check=True,timeout=60)
        subprocess.run(["git","-c","core.autocrlf=false","clone","--quiet","--shared",str(source/"libs/hbb_common"),str(repo/"libs/hbb_common")],
                       check=True,timeout=60)
        pin=subprocess.check_output(["git","-C",str(repo),"ls-tree","HEAD","libs/hbb_common"]).decode().split()[2]
        subprocess.run(["git","-C",str(repo/"libs/hbb_common"),"-c","core.autocrlf=false","checkout","--quiet",pin],check=True,timeout=60)
        for name,data in files.items():
            (repo/name).write_bytes(data.replace(b"\n",newline))
        env=dict(os.environ,RDREPO=repo.as_posix(),BRANDING=(copied/"branding").as_posix(),PYTHONIOENCODING="utf-8")
        for attempt in (1,2):
            result=subprocess.run([bash,script.as_posix()],cwd=copied,env=env,capture_output=True,text=True,encoding="utf-8",timeout=240)
            (out/("full-branding-"+label+"-"+str(attempt)+".log")).write_text(result.stdout+result.stderr,encoding="utf-8")
            assert result.returncode==0,result.stdout+result.stderr
            assert "patched bounded Mac page cleanup" in result.stdout
            assert "native loaders, protocol handlers, Linux app identity and relay defaults verified" in result.stdout
            before_check=pipeline_snapshot(repo)
            with analyzer.startup_baseline(repo) as checked_repo:
                F.verify_previous_layers(checked_repo) if (checked_repo/F.GENERATED).exists() else S.apply(checked_repo,check=True)
                assert pipeline_snapshot(repo)==before_check,"Final Quit validation mutated the pipeline"
                for name,data in files.items():
                    corrected=P.transform(name,data)
                    if name in T.FILES:
                        canonical=T.regular(checked_repo/"flutter/.mixel-dependencies/macos_application_exit",name)
                        assert canonical==corrected,name
                        corrected=T.transform(name,canonical)
                    if name in S.FILES:
                        canonical=S.regular(checked_repo/S.OWNED,name)
                        assert canonical==corrected,name
                        corrected=S.transform(name,canonical)
                    actual=(checked_repo/name).read_bytes()
                    if name in F.COMPOSED and (checked_repo/F.GENERATED).exists(): actual=F.transform(name,actual,restore=True)
                    assert actual==corrected.replace(b"\n",newline),name
                    assert (checked_repo/P.OWNED/name).read_bytes()==data,name
                assert (checked_repo/P.GENERATED).read_bytes()==P.UTILITY.encode()
                assert (checked_repo/T.GENERATED).read_bytes()==T.UTILITY.encode()
            current=pipeline_snapshot(repo)
            if attempt==1:
                first=current
            else:
                assert current==first,"Complete branding pipeline mutated on its second pass"
        (out/("full-branding-"+label+"-snapshot.json")).write_text(json.dumps(current,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        name=next(iter(files))
        target=repo/name
        saved=target.read_bytes()
        target.write_bytes(saved+b"// unauthorized target drift\n")
        before=pipeline_snapshot(repo)
        rejected=subprocess.run([bash,script.as_posix()],cwd=copied,env=env,capture_output=True,text=True,encoding="utf-8",timeout=240)
        (out/("full-branding-"+label+"-drift-refusal.log")).write_text(rejected.stdout+rejected.stderr,encoding="utf-8")
        assert rejected.returncode!=0 and "Owned Mac cleanup target changed: "+name in rejected.stderr
        assert pipeline_snapshot(repo)==before,"Earlier full-pipeline hooks modified bytes before cleanup drift refusal"
        target.write_bytes(saved)
        proof["results"][label]={"all_files":len(current),"passes":2,"exact_12_targets":True,
                                "strict_final_Quit_composition":True,"exact_both_utilities":True,
                                "complete_byte_idempotence":True,"fatal_drift_no_mutation":True}
    (out/"full-branding-integration-proof.json").write_text(json.dumps(proof,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("PASS: actual complete branding LF/CRLF twice; exact cleanup/Quit composition, both owned utilities, whole-pipeline byte idempotence and fatal drift without mutation")


DART = r"""
import 'dart:async';
import 'mixel_support_cleanup.dart';

void check(bool condition, String message) {
  if (!condition) throw StateError(message);
}

class RemotePage {
  final MixelCleanupOnce once = MixelCleanupOnce();
  final Completer<void> gate = Completer<void>();
  int calls = 0;
  bool migration = false;
  bool? nativeCloseSession;
  Future<void> mixelPrepareClose() => once.run(() async {
    calls++;
    nativeCloseSession = !migration;
    await gate.future;
  });
}
class Tab { final RemotePage page; Tab(this.page); }
class TabState { final List<Tab> tabs = []; TabState get value => this; }
class Controller {
  final TabState state = TabState();
  int clears=0;
  void clear() { clears++; state.tabs.clear(); }
}
class Target {
  final Controller tabController = Controller();
  __TARGET__
}
var activeTarget = Target();
var replyOverride;
var useOverride = false;
var failures = <String>[];
class DesktopMultiWindow {
  static Future<dynamic> invokeMethod(int id, String method) async {
    check(method==mixelPrepareWindowClose, 'Wrong prepare method');
    if (useOverride) return replyOverride;
    await activeTarget._mixelPrepareWindowClose();
    return mixelCloseAcknowledgement(id);
  }
}
void debugPrint(String text) { failures.add(text); }
class Manager {
  __MANAGER__
}
Future<void> main() async {
  for (final run in [MixelCleanupOnce().run, MixelWindowCleanup().run]) {
    final gate=Completer<void>();
    Future<void>? reentered;
    var calls=0;
    final outer=run(() {
      calls++;
      reentered=run(() async { calls++; });
      return gate.future;
    });
    check(calls==1 && identical(outer,reentered),
        'Synchronous callback reentry duplicated cleanup or changed shared promise');
    gate.complete();
    await outer;
  }
  final exactError=StateError('exact synthetic error');
  final exactStack=StackTrace.current;
  final errorOnce=MixelCleanupOnce();
  Object? observedError;
  StackTrace? observedStack;
  try {
    await errorOnce.run(() => Future<void>.error(exactError,exactStack));
  } catch (error,stack) {
    observedError=error; observedStack=stack;
  }
  check(identical(exactError,observedError) && identical(exactStack,observedStack),
      'Cached promise lost exact error or stack');
  print('PASS: synchronous reentrant cleanup reserves one shared promise before callback; exact error/stack preserved');

  // Exact original handler only clears reactive tabs. It cannot await resource Futures.
  final originalController=Controller();
  final originalPage=RemotePage();
  originalController.state.tabs.add(Tab(originalPage));
  Future<void> originalDestroy() async { __ORIGINAL__ }
  var tabController=originalController;
  await originalDestroy();
  check(originalController.clears==1 && originalPage.calls==0,
      'Original onDestroy unexpectedly performed explicit owned resource cleanup');
  print('PASS: original pinned onDestroy acknowledges before page cleanup starts');

  final first=RemotePage(), migrating=RemotePage()..migration=true;
  activeTarget.tabController.state.tabs.addAll([Tab(first),Tab(migrating)]);
  var completed=false;
  final pending=Manager()._mixelPrepareWindowClose(71).then((_) { completed=true; });
  await Future<void>.delayed(Duration.zero);
  check(!completed && first.calls==1 && migrating.calls==1 &&
      activeTarget.tabController.clears==1, 'Prepare acknowledged before native cleanup');
  final duplicate=activeTarget._mixelPrepareWindowClose();
  final frameworkDispose=first.mixelPrepareClose();
  check(first.calls==1 && first.nativeCloseSession==true &&
      migrating.nativeCloseSession==false, 'Cleanup duplicated or migration closed native session');
  first.gate.complete();
  await Future<void>.delayed(Duration.zero);
  check(!completed, 'Main termination proceeded while second page cleanup pending');
  migrating.gate.complete();
  await Future.wait<void>([pending,duplicate,frameworkDispose]);
  check(completed && first.calls==1 && migrating.calls==1 &&
      activeTarget.tabController.clears==1 && failures.isEmpty, 'Ack/resource ownership failed');
  print('PASS: exact corrected target/caller wait for all async resource Futures; repeated prepare/dispose run once; migration preserves native session');

  for (final value in [null, true, 'complete', {}, {'window_id':71,'cleanup':'failed'},
      {'window_id':'71','cleanup':'complete'}, {'window_id':72,'cleanup':'complete'},
      {'window_id':71,'cleanup':'complete','extra':true}]) {
    useOverride=true; replyOverride=value;
    final before=failures.length;
    var rejected=false;
    try { await Manager()._mixelPrepareWindowClose(71); }
    catch (_) { rejected=true; }
    check(rejected && failures.length==before+1, 'Malformed or foreign-window acknowledgement accepted');
  }
  useOverride=true; replyOverride=mixelCloseAcknowledgement(71);
  final before=failures.length;
  await Manager()._mixelPrepareWindowClose(71);
  check(failures.length==before, 'Valid owned acknowledgement rejected');
  check(!mixelValidCloseAcknowledgement(mixelCloseAcknowledgement(0),0),
      'Main window accepted as task-owned subwindow');
  print('PASS: exact caller validates owned integer window ID and complete result; malformed/foreign/extra fields are explicit failures');

  final attempted=<String>[];
  final errors=MixelWindowCleanup();
  var failed=false;
  try {
    await errors.run(() => mixelCleanupPages([
      () async { attempted.add('first'); throw StateError('synthetic first failure'); },
      () async { attempted.add('second'); await Future<void>.delayed(Duration(milliseconds:25)); attempted.add('second-complete'); }
    ], () { attempted.add('clear'); }));
  } catch (_) { failed=true; }
  check(failed && attempted.contains('second-complete') && attempted.contains('clear'),
      'One failure skipped another owned cleanup or silently succeeded');
  print('PASS: error propagates after other pages are attempted; cleanup failure never becomes complete ack');

  final steps=<String>[];
  failed=false;
  try {
    await mixelRunCleanupSteps([
      () async { steps.add('pre-resource'); throw StateError('pre-resource failure'); },
      () async { steps.add('native-close'); throw StateError('native-close failure'); },
      () async { steps.add('final-owner-cleanup'); }
    ]);
  } catch (_) { failed=true; }
  check(failed && steps.join(',')=='pre-resource,native-close,final-owner-cleanup',
      'Earlier resource failure skipped native/final cleanup or silently passed');
  print('PASS: ordered cleanup attempts native close and final ownership cleanup after earlier failures');

  final hung=MixelWindowCleanup();
  final watch=Stopwatch()..start();
  var timedOut=false;
  try { await hung.run(() => Completer<void>().future); }
  on TimeoutException { timedOut=true; }
  watch.stop();
  check(timedOut && watch.elapsedMilliseconds>=3900 && watch.elapsedMilliseconds<6000,
      'Hung target cleanup did not use actual four-second bound');
  print('PASS: actual 4s hung target bound; elapsed_ms='+watch.elapsedMilliseconds.toString());
}
"""


def execute(out, args, label, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90,cwd=cwd,
                            env=dict(os.environ,CI="true",FLUTTER_SUPPRESS_ANALYTICS="true",DART_SUPPRESS_ANALYTICS="true"))
    (out / (label + ".log")).write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout + result.stderr)


def dart_controls(out, files, dart, common_source):
    patched = {name: P.transform(name, data).decode() for name, data in files.items()}
    source = patched["flutter/lib/desktop/pages/remote_tab_page.dart"]
    start = source.index("  final _mixelWindowCleanup")
    end = source.index("  int windowId()", start)
    target = source[start:end]
    manager = P.function(patched["flutter/lib/utils/multi_window_manager.dart"],
                         "  Future<void> _mixelPrepareWindowClose(int windowId) async {")
    original = P.function(files["flutter/lib/desktop/pages/remote_tab_page.dart"].decode(),
                          "  Future<dynamic> _remoteMethodHandler(")
    destroy = original.split('} else if (call.method == "onDestroy") {',1)[1].split("}",1)[0]
    program = DART.replace("__TARGET__",target).replace("__MANAGER__",manager).replace("__ORIGINAL__",destroy)
    # Dart captures local variables at their declaration; make the pinned tab
    # controller binding precede the extracted handler declaration.
    program = program.replace("  Future<void> originalDestroy()", "  var tabController=originalController;\n  Future<void> originalDestroy()")
    program = program.replace("  var tabController=originalController;\n  await originalDestroy();", "  await originalDestroy();")
    (out / "mixel_support_cleanup.dart").write_text(P.UTILITY)
    (out / "actual-cleanup-control.dart").write_text(program)
    execute(out,[str(dart),"analyze",str(out / "mixel_support_cleanup.dart"),str(out / "actual-cleanup-control.dart")],"dart-analyze")
    execute(out,[str(dart),str(out / "actual-cleanup-control.dart")],"actual-dart-cleanup")
    manager_controls(out, files, dart, common_source)
    (out/"actual-registry-control.dart").write_text(REGISTRY)
    execute(out,[str(dart),"analyze",str(out/"actual-registry-control.dart")],"registry-analyze")
    execute(out,[str(dart),str(out/"actual-registry-control.dart")],"actual-registry")


REGISTRY=r"""
import 'dart:async';
import 'mixel_support_cleanup.dart';
void check(bool value,String message) {if(!value) throw StateError(message);}
Future<void> tick()=>Future<void>.delayed(Duration.zero);
Future<void> main() async {
  final first=MixelCleanupOnce(trackPage:true),late=MixelCleanupOnce(trackPage:true);
  final firstGate=Completer<void>(),lateGate=Completer<void>();
  var firstCalls=0,lateCalls=0,complete=false;
  mixelRegisterPageCleanup(first,()=>first.run(() {firstCalls++;return firstGate.future;}));
  final drain=mixelDrainPageCleanup().then((_) {complete=true;});
  await tick();
  mixelRegisterPageCleanup(late,()=>late.run(() {lateCalls++;return lateGate.future;}));
  firstGate.complete();
  await tick();
  check(firstCalls==1&&lateCalls==1&&!complete,
      'Drain skipped a live State registered during the first async cleanup await');
  lateGate.complete();await drain;
  await mixelDrainPageCleanup();
  check(complete&&firstCalls==1&&lateCalls==1,'Registry retained or duplicated completed resources');
  print('PASS: real engine registry drains live handles added during await; finished resources are removed exactly once');

  final failed=MixelCleanupOnce(trackPage:true),pending=MixelCleanupOnce(trackPage:true);
  final pendingGate=Completer<void>();
  final error=StateError('exact registered cleanup error');
  final stack=StackTrace.current;
  mixelRegisterPageCleanup(failed,()=>failed.run(()=>Future<void>.error(error,stack)));
  mixelRegisterPageCleanup(pending,()=>pending.run(()=>pendingGate.future));
  Object? observed;
  StackTrace? observedStack;
  var acknowledged=false;
  final failedDrain=mixelDrainPageCleanup().then((_) {acknowledged=true;},
      onError:(Object e,StackTrace s) {observed=e;observedStack=s;});
  await tick();
  check(!acknowledged&&observed==null,'Registry failure skipped another pending resource');
  pendingGate.complete();await failedDrain;
  check(identical(observed,error)&&identical(observedStack,stack)&&!acknowledged,
      'Registry failed cleanup lost exact provenance or acknowledged complete');
  observed=null;
  try {await mixelDrainPageCleanup();} catch(e) {observed=e;}
  check(identical(observed,error),'A removed failed page silently became a successful later acknowledgement');
  print('PASS: registry failure waits every started resource, preserves exact error/stack and remains failed for later prepare');
}
"""


MANAGER = r"""
import 'dart:async';
import 'dart:convert';
import 'mixel_support_cleanup.dart';
const isMacOS=true,isWindows=false,isLinux=false;
void check(bool value,String message) {
  if(!value) throw StateError(message);
}
final logs=<String>[],actions=<String>[];
void debugPrint(String value) {logs.add(value);}
enum WindowType {Main,RemoteDesktop,FileTransfer,ViewCamera,PortForward,Terminal,Unknown}
class Colors {static const black=0;}
class Offset {
  const Offset(int x,int y);
  Object operator &(Size size)=>Object();
}
class Size {Size(int w,int h);}
String getWindowNameWithId(String id,{required WindowType overrideType})=>id;
class WindowController {
  WindowController(this.windowId);
  final int windowId;
  static final owners=<int,WindowController>{};
  static WindowController fromWindowId(int id)=>owners.putIfAbsent(id,()=>WindowController(id));
  bool failPrevent=false,failClose=false,hungClose=false;
  int preventCalls=0,closeCalls=0,showCalls=0;
  void setInitBackgroundColor(Object value) {}
  void setFrame(Object value) {}
  void center() {}
  void setTitle(String value) {}
  Future<void> show() async {showCalls++;}
  Future<void> setPreventClose(bool value) async {
    actions.add('prevent:$windowId');preventCalls++;
    if(failPrevent) throw StateError('prevent failed:$windowId');
  }
  Future<void> close() async {
    actions.add('close:$windowId');closeCalls++;
    if(failClose) throw StateError('close failed:$windowId');
    if(hungClose) await Completer<void>().future;
  }
}
class DesktopMultiWindow {
  static Completer<WindowController>? creation;
  static final preparations=<int,Completer<void>>{};
  static final failures=<int>{};
  static Future<WindowController> createWindow(String message)=>creation!.future;
  static Future<dynamic> invokeMethod(int id,String method) async {
    check(method==mixelPrepareWindowClose,'Incorrect prepare method');
    actions.add('prepare:$id');
    if(failures.contains(id)) throw StateError('prepare failed:$id');
    await preparations[id]?.future;
    return mixelCloseAcknowledgement(id);
  }
}
class Manager {
  bool _mixelClosingSubWindows=false;
  final _mixelCloseAllOnce=MixelCleanupOnce();
  final Set<Future<void>> _mixelPendingCreations={};
  final _activeWindows=<int>{};
  final inventories=<WindowType,List<int>>{};
  List<int> _findWindowsByType(WindowType type)=>inventories.putIfAbsent(type,()=>[]);
  void clearWindowType(WindowType type) {inventories[type]?.clear();}
  void registerActiveWindow(int id) {_activeWindows.add(id);}
  Set<int> getActiveWindows()=>_activeWindows;
  Future<void> saveWindowPosition(WindowType type,{required int windowId}) async {
    actions.add('save:$windowId');
  }
  __MANAGER_METHODS__
}
class Target {
  final _mixelWindowCleanup=MixelWindowCleanup();
  final pending=Completer<void>();
  Future<void> start()=>_mixelWindowCleanup.run(()=>pending.future);
  Future<void> incoming(String method) async {
    final call=Call(method);
    __TARGET_GUARD__
  }
}
class Call {Call(this.method);final String method;}
var rustDeskWinManager=Manager();
final windowManager=WindowController.fromWindowId(0);
Future<void> saveWindowPosition(WindowType type) async {}
void debugPrintStack({required String label}) {logs.add(label);}
void periodic_immediate(Duration duration,Future<void> Function() callback) {
  actions.add('termination-scheduled');
}
class RdPlatformChannel {
  static final instance=RdPlatformChannel();
  Future<void> terminate() async {}
}
__ACTIVE_WINDOW_HANDLER__
class FFI {
  final gate=Completer<void>();
  int calls=0;
  Future<void> close() {calls++;return gate.future;}
}
class Get {
  static final owners=<String,Object>{};
  static int deletions=0;
  static bool isRegistered<T>({required String tag})=>owners[tag] is T;
  static T find<T>({required String tag})=>owners[tag] as T;
  static Future<bool> delete<T>({required String tag}) async {
    deletions++;owners.remove(tag);return true;
  }
}
class TerminalConnectionManager {
  static final _connections=<String,FFI>{};
  static final _connectionRefCount=<String,int>{};
  __TERMINAL_METHODS__
}
class TerminalPage {
  final String? password=null,connToken=null;
  final bool? isSharedPassword=null,forceRelay=null;
}
class TerminalTab {
  TerminalTab(this.key,this.page);
  final String key;
  final Object page;
}
class TerminalController {
  final tabs=<TerminalTab>[TerminalTab('peer_1',TerminalPage())];
  TerminalController get state=>this;
  TerminalController get value=>this;
  TerminalTab get selectedTabInfo=>tabs.first;
  var additions=0;
  void add(TerminalTab tab) {additions++;tabs.add(tab);}
  void clear() {tabs.clear();}
}
__RESTORE_CLASSES__
Future<Object?> failureOf(Future<dynamic> future) async {
  try {await future;return null;} catch(error) {return error;}
}
Future<void> tick() =>Future<void>.delayed(Duration.zero);
Future<void> main() async {
  rustDeskWinManager.registerActiveWindow(0);
  await onActiveWindowChanged();
  check(!rustDeskWinManager._mixelClosingSubWindows&&windowManager.closeCalls==0,
      'Ordinary viewer disconnect closed/latching main while it remained active');
  final reconnect=WindowController.fromWindowId(79);
  DesktopMultiWindow.creation=Completer<WindowController>()..complete(reconnect);
  check(await rustDeskWinManager.newSessionWindow(WindowType.RemoteDesktop,
      'reconnect','message',[],false)==79,
      'Ordinary viewer disconnect prevented fresh connection creation');
  print('PASS: actual pinned active-window handler preserves main-active disconnect/reconnect; terminal closeAll latch is not entered');
  final originalRestore=OriginalRestore(),correctedRestore=CorrectedRestore();
  final originalFuture=failureOf(originalRestore._restoreSessions('{"persistent_sessions":[7,8]}'));
  final correctedFuture=failureOf(correctedRestore._restoreSessions('{"persistent_sessions":[7,8]}'));
  await tick();
  check(originalRestore.tabController.additions==1&&correctedRestore.tabController.additions==1,
      'Actual terminal restoration did not enter its 300ms asynchronous gap');
  final restoreGate=Completer<void>();
  final originalClose=originalRestore._mixelWindowCleanup.run(()=>restoreGate.future);
  final correctedClose=correctedRestore._mixelWindowCleanup.run(()=>restoreGate.future);
  originalRestore.tabController.clear();correctedRestore.tabController.clear();
  check(await originalFuture is StateError,
      'Original already-running terminal restore unexpectedly stopped after forced clear');
  check(await correctedFuture==null&&correctedRestore.tabController.additions==1,
      'Corrected terminal restore added a late tab or failed after forced clear');
  correctedRestore._addNewTerminalForCurrentPeer();
  correctedRestore._addNewTerminal('peer');
  check(correctedRestore.tabController.additions==1,
      'Forced terminal close accepted a direct keyboard/add-button tab');
  restoreGate.complete();await Future.wait<void>([originalClose,correctedClose]);
  print('PASS: exact original delayed terminal restoration fails after clear; corrected restore and direct add guards stop already-running late additions');
  final target=Target();
  final targetCleanup=target.start();
  for(final method in ['new_remote_desktop','new_file_transfer','new_terminal','get_cached_session_data']) {
    check(await failureOf(target.incoming(method)) is StateError,
        'Closing target accepted a late add/connect/migration method');
  }
  await target.incoming(mixelPrepareWindowClose);
  await target.incoming('onDestroy');
  target.pending.complete();await targetCleanup;
  print('PASS: actual target guard rejects late add/connect/migration while preserving duplicate prepare/onDestroy');

  final originalOwner=FFI();
  TerminalConnectionManager._connections['original']=originalOwner;
  TerminalConnectionManager._connectionRefCount['original']=1;
  Get.owners['terminal_original']=originalOwner;
  TerminalConnectionManager.releaseConnection('original');
  check(originalOwner.calls==1&&!originalOwner.gate.isCompleted&&
      !Get.owners.containsKey('terminal_original'),
      'Original pinned terminal release unexpectedly awaited FFI disposal');
  originalOwner.gate.complete();
  print('PASS: original pinned terminal release deletes registration before its native Future completes');

  final shared=FFI();
  TerminalConnectionManager._connections['peer']=shared;
  TerminalConnectionManager._connectionRefCount['peer']=2;
  Get.owners['terminal_peer']=shared;
  await TerminalConnectionManager.mixelReleaseConnection('peer',shared);
  check(shared.calls==0&&TerminalConnectionManager._connectionRefCount['peer']==1,
      'Releasing one terminal closed a shared sibling transport');
  var released=false;
  final last=TerminalConnectionManager.mixelReleaseConnection('peer',shared).then((_) {released=true;});
  final duplicate=TerminalConnectionManager.mixelReleaseConnection('peer',shared);
  await tick();
  check(!released&&shared.calls==1,'Last terminal close was unawaited or duplicated');
  final replacement=FFI();
  TerminalConnectionManager._connections['peer']=replacement;
  TerminalConnectionManager._connectionRefCount['peer']=3;
  Get.owners['terminal_peer']=replacement;
  final before=Get.deletions;
  shared.gate.complete();await Future.wait<void>([last,duplicate]);
  check(identical(TerminalConnectionManager._connections['peer'],replacement)&&
      TerminalConnectionManager._connectionRefCount['peer']==3&&
      identical(Get.owners['terminal_peer'],replacement)&&Get.deletions==before,
      'Delayed terminal owner deleted/released newer same-peer replacement');
  print('PASS: actual terminal methods preserve shared sibling references; await last native owner once; replacement maps/tag/refcount survive delayed release');

  final closing=Manager();
  final cancelledWindow=WindowController.fromWindowId(81);
  DesktopMultiWindow.creation=Completer<WindowController>();
  final created=failureOf(closing.newSessionWindow(
      WindowType.RemoteDesktop,'peer','message',closing._findWindowsByType(WindowType.RemoteDesktop),false));
  await tick();
  check(closing._mixelPendingCreations.length==1,'Native creation is not tracked before await');
  var closed=false;
  final all=closing.closeAllSubWindows().then((_) {closed=true;});
  final repeated=closing.closeAllSubWindows();
  await tick();
  check(!closed,'Main close acknowledged while native creation was pending');
  check(await failureOf(closing.newSessionWindow(
      WindowType.RemoteDesktop,'late','late',[],false)) is StateError,
      'Main accepted a new creation during close');
  DesktopMultiWindow.creation!.complete(cancelledWindow);
  await Future.wait<void>([all,repeated]);
  check(await created is StateError&&closed&&cancelledWindow.closeCalls==1&&
      cancelledWindow.showCalls==0&&closing._findWindowsByType(WindowType.RemoteDesktop).isEmpty,
      'In-flight native creation was shown/orphaned or not cancelled before main acknowledgement');
  check(await failureOf(closing.newSessionWindow(WindowType.RemoteDesktop,
      'late-terminal','message',[],false)) is StateError,
      'Termination interval reopened native window admission after drain');
  await closing.closeAllSubWindows();
  check(cancelledWindow.closeCalls==1,'Repeated terminal drain duplicated old window close');
  print('PASS: actual native creation token prevents premature main completion and cancels in-flight window; duplicate drain shares work; late create is rejected throughout the main termination interval');

  final inventory=Manager();
  final ids=inventory._findWindowsByType(WindowType.RemoteDesktop)..addAll([91,92,93]);
  final first=WindowController.fromWindowId(91)..failPrevent=true;
  DesktopMultiWindow.preparations[91]=Completer<void>();
  final outcome=failureOf(inventory.closeAllSubWindows());
  await tick();
  check(actions.contains('prepare:91')&&actions.contains('prepare:92')&&actions.contains('prepare:93'),
      'Preparations were not started for every captured window before awaiting');
  ids.remove(92); // A concurrent hide event mutates the original live list.
  DesktopMultiWindow.preparations[91]!.complete();
  final closeError=await outcome;
  check(closeError is StateError&&closeError.toString().contains('prevent failed:91')&&first.closeCalls==1&&
      WindowController.fromWindowId(92).closeCalls==1&&WindowController.fromWindowId(93).closeCalls==1,
      'First prevent failure skipped close/later captured IDs or falsely acknowledged complete');
  print('PASS: stable actual close inventory survives concurrent hide removal; prevent failure still attempts close and every later ID; main completion reports failure');

  final prepareFailure=Manager();
  prepareFailure._findWindowsByType(WindowType.FileTransfer).add(101);
  DesktopMultiWindow.failures.add(101);
  check(await failureOf(prepareFailure.closeAllSubWindows()) is StateError&&
      WindowController.fromWindowId(101).closeCalls==1,
      'Prepare failure skipped native close or silently acknowledged complete');

  final createFailure=Manager();
  final failedWindow=WindowController.fromWindowId(111)..failPrevent=true;
  DesktopMultiWindow.creation=Completer<WindowController>();
  final failedCreate=failureOf(createFailure.newSessionWindow(
      WindowType.RemoteDesktop,'peer','message',[],false));
  final failedAll=failureOf(createFailure.closeAllSubWindows());
  DesktopMultiWindow.creation!.complete(failedWindow);
  check(await failedCreate is StateError&&await failedAll is StateError&&failedWindow.closeCalls==1,
      'Cancelled creation lost exact cleanup failure or skipped native close');
  print('PASS: preparation/cancelled-creation failures preserve failed main acknowledgement while native close is still attempted');

  final hung=Manager();
  DesktopMultiWindow.preparations[121]=Completer<void>();
  final watch=Stopwatch()..start();
  check(await failureOf(hung._mixelPrepareWindowClose(121)) is TimeoutException,
      'Hung caller IPC did not fail with its actual timeout');
  watch.stop();
  check(watch.elapsedMilliseconds>=4400&&watch.elapsedMilliseconds<6500,
      'Caller IPC used an incorrect bound');
  print('PASS: actual hung caller IPC 4.5s bound; elapsed_ms='+watch.elapsedMilliseconds.toString());

  final delayed=Manager();
  DesktopMultiWindow.creation=Completer<WindowController>();
  final delayedWindow=WindowController.fromWindowId(131);
  final delayedCreate=failureOf(delayed.newSessionWindow(
      WindowType.RemoteDesktop,'delayed','message',[],false));
  final delayedClose=failureOf(delayed.closeAllSubWindows());
  check(await delayedClose is TimeoutException,
      'Hung creation did not preserve its bounded failed close acknowledgement');
  check(delayed._mixelClosingSubWindows,'Terminal failure reopened window admission before main termination');
  DesktopMultiWindow.creation!.complete(delayedWindow);
  check(await delayedCreate is StateError&&delayedWindow.closeCalls==1&&
      delayedWindow.showCalls==0,
      'Pre-drain native create arriving after timeout escaped terminal cancellation');
  print('PASS: timed-out native creation remains owned during termination; late completion is closed and never shown');

  final hungNative=Manager();
  WindowController.fromWindowId(141).hungClose=true;
  final nativeWatch=Stopwatch()..start();
  check(await failureOf(hungNative._mixelForceCloseWindow(141)) is TimeoutException,
      'Hung actual native close did not preserve bounded failure');
  nativeWatch.stop();
  check(nativeWatch.elapsedMilliseconds>=4400&&nativeWatch.elapsedMilliseconds<6500,
      'Actual native close used an incorrect bound');
  print('PASS: actual hung native close 4.5s bound; elapsed_ms='+nativeWatch.elapsedMilliseconds.toString());

  final failedTerminal=FFI();
  TerminalConnectionManager._connections['failed-peer']=failedTerminal;
  TerminalConnectionManager._connectionRefCount['failed-peer']=1;
  Get.owners['terminal_failed-peer']=failedTerminal;
  final terminalOutcome=failureOf(TerminalConnectionManager.mixelReleaseConnection('failed-peer',failedTerminal));
  final terminalError=StateError('exact terminal native close failure');
  failedTerminal.gate.completeError(terminalError);
  check(identical(await terminalOutcome,terminalError)&&
      !Get.owners.containsKey('terminal_failed-peer')&&
      !TerminalConnectionManager._connections.containsKey('failed-peer'),
      'Actual terminal failure skipped owned registration cleanup or lost error provenance');
  print('PASS: actual terminal native failure still removes its own maps/tag and preserves exact failure');
}
"""


def manager_controls(out, files, dart, common_source):
    patched={name:P.transform(name,data).decode() for name,data in files.items()}
    source=patched["flutter/lib/utils/multi_window_manager.dart"]
    manager_methods="\n".join(P.function(source,anchor) for anchor in (
        "  void _mixelAssertCanOpen() {","  Future<void> closeAllSubWindows() async {",
        "  Future<int> newSessionWindow(","  Future<void> _mixelPrepareWindowClose(int windowId) async {",
        "  Future<void> _mixelForceCloseWindow(int windowId) async {",
        "  Future<void> _closeWindows(WindowType type) async {"))
    connection=patched["flutter/lib/desktop/pages/terminal_connection_manager.dart"]
    start=connection.index("  static final _mixelOwnerCloses")
    end=connection.index("  /// Check if a connection exists",start)
    terminal=connection[start:end]+P.function(connection,"  static void releaseConnection(String peerId) {")
    target=patched["flutter/lib/desktop/pages/remote_tab_page.dart"]
    start=target.index("      if (isMacOS && _mixelWindowCleanup.started")
    end=target.index("\n      }",start)+len("\n      }")
    guard=target[start:end]
    active=P.function(common_source,"Future<void> onActiveWindowChanged() async {")
    classes=[]
    terminal_path="flutter/lib/desktop/pages/terminal_tab_page.dart"
    for class_name,source in (("OriginalRestore",files[terminal_path].decode()),
                              ("CorrectedRestore",patched[terminal_path])):
        restore_methods="\n".join(P.function(source,anchor) for anchor in (
            "  Future<void> _restoreSessions(String arguments) async {",
            "  void _addNewTerminal(String peerId, {int? terminalId}) {",
            "  void _addNewTerminalForCurrentPeer({int? terminalId}) {"))
        classes.append("""class %s {
  final _mixelWindowCleanup=MixelWindowCleanup();
  final tabController=TerminalController();
  var _nextTerminalId=2;
  (String,int)? _parseTabKey(String key)=>('peer',1);
  TerminalTab _createTerminalTab({required String peerId,required int terminalId,
      String? password,bool? isSharedPassword,bool? forceRelay,String? connToken}) =>
      TerminalTab(peerId+'_'+terminalId.toString(),TerminalPage());
%s
}
"""%(class_name,restore_methods))
    program=MANAGER.replace("__MANAGER_METHODS__",manager_methods).replace("__TARGET_GUARD__",guard).replace("__TERMINAL_METHODS__",terminal).replace("__ACTIVE_WINDOW_HANDLER__",active).replace("__RESTORE_CLASSES__","\n".join(classes))
    (out/"actual-manager-terminal-control.dart").write_text(program)
    execute(out,[str(dart),"analyze",str(out/"actual-manager-terminal-control.dart")],"manager-terminal-analyze")
    execute(out,[str(dart),str(out/"actual-manager-terminal-control.dart")],"actual-manager-terminal")


WIDGET = r"""
import 'dart:async';
import 'package:flutter/material.dart' hide debugPrint;
import 'package:flutter_test/flutter_test.dart';
import '../lib/mixel_support_cleanup.dart';

const isMacOS=true;
const isWeb=false;
const profile='__PROFILE__';
final closeSessionOnDispose=<String,bool>{};
var removedShared=0, disabledWakelocks=0, hostStops=0;
void debugPrint(String text) {}
void removeSharedStates(String id) { removedShared++; }
class SimpleWrapper<T> { T value; SimpleWrapper(this.value); }
class Input {
  dynamic onRelativeMouseModeDisabled;
  void enterOrLeave(bool value) {}
}
class Texture {
  bool fail=false;
  void onRemotePageDispose(bool value) {
    if(fail) throw StateError('synthetic texture failure');
  }
  void onViewCameraPageDispose(bool value) {
    if(fail) throw StateError('synthetic texture failure');
  }
}
class ImageModel { void disposeImage() {} }
class CursorModel { void disposeImages() {} }
class Dialogs {
  void hideMobileActionsOverlay() {}
  void dismissAll() {}
}
class FileModel {
  final gate=Completer<void>();
  int calls=0;
  Future<void> close() { calls++; return gate.future; }
}
class FFI {
  bool closed=false;
  final sessionId='fixture-session';
  final inputModel=Input(), textureModel=Texture();
  final imageModel=ImageModel(), cursorModel=CursorModel();
  final dialogManager=Dialogs(), fileModel=FileModel();
  final gate=Completer<void>();
  int closeCalls=0;
  bool? closeSessionObserved;
  Future<void> close({bool closeSession=true}) {
    closeCalls++;closeSessionObserved=closeSession;return gate.future;
  }
  void unregisterTerminalModel(int id) {}
}
class Get {
  static final owners=<String,Object>{};
  static int deletions=0;
  static bool isRegistered<T>({required String tag}) => owners[tag] is T;
  static T find<T>({required String tag}) => owners[tag] as T;
  static Future<bool> delete<T>({required String tag}) async {
    deletions++;owners.remove(tag);return true;
  }
}
class WakelockManager {
  static void disable(Object key) { disabledWakelocks++; }
}
class DesktopMultiWindow {
  static void removeListener(Object object) {}
}
class Bind {
  void hostStopSystemKeyPropagate({required bool stopped}) { hostStops++; }
}
final bind=Bind();
enum SystemUiMode { manual }
enum SystemUiOverlay { top,bottom }
class SystemChrome {
  static Future<void> setEnabledSystemUIMode(SystemUiMode mode,
      {required List<SystemUiOverlay> overlays}) async {}
}
class TerminalModel { void dispose() {} }
class TerminalConnectionManager {
  static late FFI owner;
  static Future<void> mixelReleaseConnection(String peerId,FFI captured) {
    if(!identical(owner,captured)) throw StateError('fixture owner changed');
    return captured.close();
  }
  static void releaseConnection(String peerId) { owner.close(); }
}
String tagFor(String id) => profile=='file_manager' ? 'ft_'+id :
    profile=='port_forward' ? 'pf_'+id : profile=='terminal' ? 'terminal_'+id : id;

class __WIDGET__ extends StatefulWidget {
  __WIDGET__({required this.id,required this.ffiOwner,this.register=true});
  final String id;
  final FFI ffiOwner;
  final bool register;
  final int terminalId=1;
  final _lastState=SimpleWrapper<State<__WIDGET__>?>(null);
  __EXPOSED__
  @override State<__WIDGET__> createState() {
    final state=__STATE__();_lastState.value=state;return state;
  }
}
class __STATE__ extends State<__WIDGET__> with WidgetsBindingObserver {
  late FFI _ffi;
  final _uniqueKey=Object();
  final _rawKeyFocusNode=FocusNode();
  final _terminalFocusNode=FocusNode();
  final _terminalModel=TerminalModel();
  StreamSubscription<int>? _tabStateSubscription;
  Timer? _timer,_pointerLockCenterDebounceTimer;
  FileModel get model=>_ffi.fileModel;
  String get sessionId=>_ffi.sessionId;
  @override void initState() {
    super.initState();_ffi=widget.ffiOwner;
    Get.owners[tagFor(widget.id)]=_ffi;
    TerminalConnectionManager.owner=_ffi;
    WidgetsBinding.instance.addObserver(this);
    _mixelOwnedFfi=_ffi;_mixelOwnedId=widget.id;
    __TERMINAL_INIT__
    if(widget.register) {
      mixelRegisterPageCleanup(_mixelCleanupOnce,_mixelCloseResources);
    }
    if(profile=='remote'||profile=='view_camera') {
      _timer=Timer.periodic(const Duration(milliseconds:5), (_) {
        if(!mounted) throw StateError('timer survived synchronous State.dispose');
      });
    }
  }
  @override Widget build(BuildContext context)=>const SizedBox(width:10,height:10);
  __DISPOSE__
  __RESOURCES__
}
Future<Object?> failureOf(Future<void> future) async {
  try {await future;return null;} catch(error) {return error;}
}

void main() {
  testWidgets('original clear can acknowledge before frame disposal starts',
      (tester) async {
    final ffi=FFI();
    final page=__WIDGET__(id:'original-peer',ffiOwner:ffi,register:false);
    final show=ValueNotifier<bool>(true);
    await tester.pumpWidget(MaterialApp(home:ValueListenableBuilder<bool>(
      valueListenable:show,builder:(_,visible,__)=>visible?page:const SizedBox())));
    // This is the pinned onDestroy effect: clear reactive tabs before a frame.
    Future<void> originalDestroy() async { show.value=false; }
    await originalDestroy();
    expect(ffi.closeCalls,0);
    expect(page._lastState.value!.mounted,true);
    if(profile=='file_manager') ffi.fileModel.gate.complete();
    await tester.pump();
    await tester.pump();
    expect(tester.takeException(),isNull);
    ffi.gate.complete();
    await page.mixelPrepareClose();
    await tester.pump();
    show.dispose();
  });

  testWidgets('empty tabs before disposal still drain captured page owner',
      (tester) async {
    final ffi=FFI();
    final page=__WIDGET__(id:'removed-peer',ffiOwner:ffi);
    final show=ValueNotifier<bool>(true);
    await tester.pumpWidget(MaterialApp(home:ValueListenableBuilder<bool>(
      valueListenable:show,builder:(_,visible,__)=>visible?page:const SizedBox())));
    show.value=false;
    var done=false;
    final drain=mixelDrainPageCleanup().then((_) {done=true;});
    await tester.pump();
    expect(tester.takeException(),isNull);
    expect(page._lastState.value!.mounted,false);
    expect(done,false);
    if(profile=='file_manager') {
      expect(ffi.closeCalls,0);
      ffi.fileModel.gate.complete();
      await tester.pump();
    }
    expect(ffi.closeCalls,1);
    ffi.gate.complete();
    await drain;
    await page.mixelPrepareClose();
    expect(ffi.closeCalls,1);
    expect(done,true);
    await tester.pump();
    expect(tester.takeException(),isNull);
    show.dispose();
  });

  testWidgets('framework super.dispose is synchronous during pending cleanup',
      (tester) async {
    final ffi=FFI();
    final page=__WIDGET__(id:'pending-peer',ffiOwner:ffi);
    await tester.pumpWidget(MaterialApp(home:page));
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(milliseconds:20));
    expect(tester.takeException(),isNull);
    expect(page._lastState.value!.mounted,false);
    if(profile=='file_manager') {
      ffi.fileModel.gate.complete();await tester.pump();
    }
    expect(ffi.closeCalls,1);
    ffi.gate.complete();await page.mixelPrepareClose();
    await tester.pump();
    expect(tester.takeException(),isNull);
  });

  testWidgets('delayed owner cannot delete a newer registration with same tag',
      (tester) async {
    final ffi=FFI(),replacement=FFI();
    final page=__WIDGET__(id:'reused-peer',ffiOwner:ffi);
    await tester.pumpWidget(MaterialApp(home:page));
    final pending=page.mixelPrepareClose();
    final before=Get.deletions;
    Get.owners[tagFor('reused-peer')]=replacement;
    if(profile=='file_manager') {
      ffi.fileModel.gate.complete();await tester.pump();
    }
    ffi.gate.complete();await pending;
    expect(identical(Get.owners[tagFor('reused-peer')],replacement),true);
    expect(Get.deletions,before);
    await tester.pumpWidget(const SizedBox());
    await tester.pump();expect(tester.takeException(),isNull);
    Get.owners.remove(tagFor('reused-peer'));
  });

  if(profile=='remote'||profile=='view_camera') {
    testWidgets('migration flag is consumed once and preserves native session',
        (tester) async {
      final ffi=FFI();
      final page=__WIDGET__(id:'migrating-peer',ffiOwner:ffi);
      closeSessionOnDispose['migrating-peer']=false;
      await tester.pumpWidget(MaterialApp(home:page));
      final pending=page.mixelPrepareClose();
      await tester.pumpWidget(const SizedBox());
      await tester.pump();
      expect(ffi.closeCalls,1);
      expect(ffi.closeSessionObserved,false);
      ffi.gate.complete();await pending;
      await tester.pump();expect(tester.takeException(),isNull);
    });
  }
  testWidgets('real resource failure still attempts native and final cleanup',
      (tester) async {
    final ffi=FFI();
    final page=__WIDGET__(id:'failed-resource-peer',ffiOwner:ffi);
    await tester.pumpWidget(MaterialApp(home:page));
    if(profile=='remote'||profile=='view_camera') ffi.textureModel.fail=true;
    final outcome=failureOf(page.mixelPrepareClose());
    await tester.pumpWidget(const SizedBox());
    if(profile=='file_manager') {
      ffi.fileModel.gate.completeError(StateError('synthetic file model failure'));
      await tester.pump();
    }
    expect(ffi.closeCalls,1);
    if(profile=='port_forward'||profile=='terminal') {
      ffi.gate.completeError(StateError('synthetic native failure'));
    } else {
      ffi.gate.complete();
    }
    expect(await outcome,isA<StateError>());
    await tester.pump();
    expect(tester.takeException(),isNull);
    if(profile!='terminal') {
      expect(Get.owners.containsKey(tagFor('failed-resource-peer')),false);
    }
  });
}
"""


def widget_controls(out, files, flutter):
    fixture=out/"widget-fixture"
    (fixture/"lib").mkdir(parents=True)
    (fixture/"test").mkdir()
    (fixture/"lib/mixel_support_cleanup.dart").write_text(P.UTILITY)
    (fixture/"pubspec.yaml").write_text("""name: mixel_cleanup_widget_fixture
environment:
  sdk: '>=3.5.4 <4.0.0'
dependencies:
  flutter:
    sdk: flutter
dev_dependencies:
  flutter_test:
    sdk: flutter
""")
    for profile,(widget,state) in P.PAGE_TYPES.items():
        name="flutter/lib/desktop/pages/"+profile+"_page.dart"
        source=P.transform(name,files[name]).decode()
        dispose=P.function(source,"  Future<void> dispose() async {" if profile in ("remote","view_camera") else "  void dispose() {")
        exposed=P.function(source,"  Future<void> mixelPrepareClose() {")
        start=source.index("  late final ")
        # Generated lifetime fields are after dispose; upstream earlier fields
        # are excluded by anchoring the exact newly emitted ownership field.
        start=source.index("  late final int _mixelOwnedTerminalId;" if profile=="terminal" else "  late final FFI _mixelOwnedFfi;")
        end=source.index("\n  }\n",source.index("  Future<void> _mixelCloseResourceBody()",start))+len("\n  }\n")
        if profile!="terminal":
            end=source.index("\n  }\n",source.index("  Future<void> _mixelDeleteOwnedFfi(",end))+len("\n  }\n")
        resources=source[start:end]
        text=WIDGET.replace("__PROFILE__",profile).replace("__WIDGET__",widget).replace("__STATE__",state).replace(
            "__EXPOSED__",exposed).replace("__DISPOSE__",dispose).replace("__RESOURCES__",resources).replace(
            "__TERMINAL_INIT__","_mixelOwnedTerminalId=widget.terminalId;" if profile=="terminal" else "")
        (fixture/"test"/(profile+"_lifetime_test.dart")).write_text(text)
    execute(out,[str(flutter),"pub","get"],"widget-pub-get",cwd=fixture)
    result=subprocess.run([str(flutter),"test","--no-pub","--reporter","expanded"],
                          cwd=fixture,capture_output=True,text=True,timeout=180,
                          env=dict(os.environ,CI="true",FLUTTER_SUPPRESS_ANALYTICS="true",DART_SUPPRESS_ANALYTICS="true"))
    (out/"actual-flutter-widgettest.log").write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
    print(result.stdout+result.stderr)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path(os.environ.get("RDREPO",ROOT/"rustdesk")))
    parser.add_argument("--evidence-dir",type=Path)
    parser.add_argument("--dart",type=Path)
    parser.add_argument("--flutter",type=Path)
    parser.add_argument("--full-branding",action="store_true")
    parser.add_argument("--bash",type=Path,help="Actual active Git Bash executable; required full path for Windows --full-branding")
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mixel-cleanup-control-") as temporary:
        out=args.evidence_dir.resolve() if args.evidence_dir else Path(temporary).resolve()
        out.mkdir(parents=True,exist_ok=True)
        files=source_files(args.source)
        materialization(out,files)
        if args.full_branding:
            full_branding(out,files,args.source.resolve(),args.bash)
        if args.dart:
            common_source=subprocess.check_output(["git","-C",str(args.source),"show",P.PIN+":flutter/lib/common.dart"],timeout=60).decode()
            (out/"pinned-common.dart").write_text(common_source)
            dart_controls(out,files,args.dart,common_source)
        if args.flutter:
            widget_controls(out,files,args.flutter)
    print("PASS: bounded Mac cleanup source/ownership qualification")


if __name__=="__main__":
    main()
