#!/usr/bin/env python3
"""Execute actual generated startup with private plugin/FFI seams; no native app."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from sdk_discovery import find_dart

SCRIPTS = Path(__file__).resolve().parent
UPSTREAM = Path(os.environ.get("RDREPO", SCRIPTS.parent / "rustdesk"))
spec = importlib.util.spec_from_file_location("startup_patch", SCRIPTS / "patch-macos-startup.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


fixture_spec = importlib.util.spec_from_file_location("mixel_historical_close_fixture", Path(__file__).with_name("test-support-flutter-analysis.py"))
historical_fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(historical_fixture)


def between(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def method(text, start):
    pos = text.index(start)
    opening = text.index("{", pos)
    # These pinned bodies contain no unmatched literal braces.
    depth = 1
    end = opening + 1
    while depth:
        if text[end] == "{": depth += 1
        if text[end] == "}": depth -= 1
        end += 1
    return text[pos:end]


SEAMS = r'''
import 'dart:async';
import 'dart:convert';
String platform='mac';
bool get isMacOS=>platform=='mac'; bool get isWindows=>platform=='windows';
bool get isLinux=>platform=='linux'; bool get isAndroid=>platform=='android';
bool get isIOS=>platform=='ios';
const kAppTypeMain='main';
int? kWindowId;
enum DesktopType { main,cm }
DesktopType desktopType=DesktopType.main;
int androidVersion=0,windowsBuildNumber=0;
String version='';
typedef F3Dart=void Function(); typedef F3=void Function();
class Case {
 final String mode; final calls=<String>[]; final blocks=<String,Completer<void>>{};
 bool listener=false,stream=false,core=false,handlers=false,callback=false,ui=false,failureUi=false;
 int writes=0,proofReads=0,onlineReads=0;
 String deviceId='',deviceName='';
 Case(this.mode);
 Future<void> invoke(String name) {
  if(mode=='$name-sync-throw') {calls.add(name);throw StateError('synthetic-sync-failure');}
  return step(name);
 }
 Future<void> step(String name) async {
  calls.add(name);
  if(mode=='$name-hang') await (blocks[name]=Completer<void>()).future;
  if(mode=='$name-throw') throw StateError('synthetic-failure');
 }
}
late Case active;
void debugPrint(String s){} void debugPrintStack({String? label}){}
class DynamicLibrary {
 static DynamicLibrary process()=>DynamicLibrary(); static DynamicLibrary open(String n)=>DynamicLibrary();
 F3 lookupFunction<A,B>(String name)=>(){};
}
class Dir {String path='/synthetic-documents';}
Future<Dir> getApplicationDocumentsDirectory() async {final state=active;await state.step('documents');return Dir();}
class ExternalPath {static Future<List<String>> getExternalStorageDirectories() async=>['/synthetic'];}
class AndroidVersion {int sdkInt=1;} class UtsName {String machine='synthetic';}
class AndroidDeviceInfo {String brand='synthetic',model='synthetic';int id=1;AndroidVersion version=AndroidVersion();}
class IosDeviceInfo {UtsName utsname=UtsName();String identifierForVendor='synthetic';}
class LinuxDeviceInfo {String name='synthetic',id='synthetic';String? machineId='synthetic';}
class WindowsDeviceInfo {String computerName='synthetic';}
class MacOsDeviceInfo {String computerName='synthetic';String? systemGUID='synthetic';}
class DeviceInfoPlugin {
 final Case state=active;
 Future<AndroidDeviceInfo> get androidInfo async=>AndroidDeviceInfo();
 Future<IosDeviceInfo> get iosInfo async=>IosDeviceInfo();
 Future<LinuxDeviceInfo> get linuxInfo async=>LinuxDeviceInfo();
 Future<WindowsDeviceInfo> get windowsInfo async=>WindowsDeviceInfo();
 Future<MacOsDeviceInfo> get macOsInfo async {await state.step('device-info');return MacOsDeviceInfo();}
}
int getWindowsTargetBuildNumber()=>1;
class RustdeskImpl {
 final Case state=active; RustdeskImpl(DynamicLibrary d);
 void mainStartDbusServer(){state.listener=true;} void mainStartIpcUrlServer(){state.calls.add('url-listener');state.listener=true;}
 String mainGetDataDirIos({required String appDir})=>'';
 Future<void> cmInit()=>state.step('cm-init');
 Future<void> mainDeviceId({required String id}) async {state.deviceId=id;await state.step('device-id');}
 Future<void> mainDeviceName({required String name}) async {state.deviceName=name;await state.step('device-name');}
 Future<void> mainSetHomeDir({required String home})=>state.step('home-dir');
 Future<void> mainInit({required String appDir,required String customClientConfig}) async {await state.step('main-init');state.core=true;}
 Future<void> mainCheckConnectStatus()=>state.invoke('connect-status');
 void pluginSyncUi({required String syncTo}){} void pluginListReload(){}
 Future<void> mainSetOption({required String key,required String value}) async {state.writes++;}
 Future<String> mainGetOption({required String key}) async {state.proofReads++;return 'attended-runtime-v2';}
 bool mainIsCanScreenRecording({required bool prompt})=>true;
 bool mainIsProcessTrusted({required bool prompt})=>true;
 Future<String> mainGetConnectStatus() async {state.onlineReads++;return '{"status_num":1,"key_confirmed":true}';}
}
abstract class Native {
 String _dir='',_homeDir='',_appType='';
 F3? _session_get_rgba;
 late RustdeskImpl _ffiBind;
 bool get isMain=>_appType==kAppTypeMain;
 bool get mixelNativeInitialized=>true;
 void invalidateMixelNativeInitialization(){}
 void _startListenEvent(RustdeskImpl bind){active.calls.add('event-stream');active.stream=true;}
 Future<String> getVersion() async {final state=active;await state.step('version');return 'synthetic';}
 bool get symbolLoaded => _session_get_rgba != null;
 Future<void> init(String appType);
}
late Native platformFFI;
RustdeskImpl get bind=>platformFFI._ffiBind;
Future<void> initGlobalFFI() async {await active.step('global-ffi');}
void _registerEventHandler(){active.calls.add('handlers');active.handlers=true;}
void updateSystemWindowTheme(){} void checkUpdate(){}
Future<void> showMixelStartupFailure({int? windowId}) async {active.calls.add('failure-ui');active.ui=true;active.failureUi=true;}
class ServerModel {void startService(){active.calls.add('event-callback');active.callback=true;}}
class Cache {final String type;Cache(this.type);Future<void> loadCache()=>active.invoke(type);}
class UserModel {void refreshCurrentUser(){}}
class GlobalFfi {final serverModel=ServerModel(),abModel=Cache('ab-cache'),groupModel=Cache('group-cache'),userModel=UserModel();}
final gFFI=GlobalFfi();
class WindowManager {
 Future<void> ensureInitialized()=>active.invoke('window-init');
 Future<void> setPreventClose(bool prevent)=>active.invoke('window-close');
 Future<void> setMovable(bool movable)=>active.invoke('window-movable');
}
final windowManager=WindowManager();
void disableWindowMovable(int? id){}
class App{}
void runApp(App app){active.calls.add('run-app');active.ui=true;}
bool _mixelSupportUrlCallbackReady=true,_mixelSupportUrlStreamReady=true,_mixelSupportUrlUiReady=true;
String _mixelSupportUrlGeneration='11111111111111111111111111111111';
int _mixelSupportUrlReadyEpoch=0;
void Function(bool)? _mixelSupportUrlPendingMaintenance;
bool _supportInviteRenewing=false;
class Notice {void showIfRequired(String proof,bool Function() show) {}}
final _supportInviteCompatibilityNotice=Notice();
class View {Object? overlay;}
class Key {View? currentState;}
final globalKey=Key();
String translate(String s)=>s;
void showToast(String s,{Duration? timeout}){}
void check(bool okay,String message){if(!okay)throw StateError(message);}
'''

SCENARIOS = r'''
Future<void> scenario(String mode,{bool original=false,String os='mac',bool cm=false}) async {
 platform=os;desktopType=cm?DesktopType.cm:DesktopType.main;
 active=Case(mode);version='';platformFFI=original?OriginalNative():FixedNative();final state=active;
 var finished=false;Object? failure;
 final operation=mode.startsWith('window-')
     ? (original?originalWindowEntry():fixedWindowEntry())
     : (original?originalRunMainApp(true):runMainApp(true));
 final future=operation.then((_) {finished=true;},onError:(Object error) {finished=true;failure=error;});
 await Future<void>.delayed(const Duration(milliseconds:110));
 final critical=['device-id','device-name','home-dir','main-init','cm-init','global-ffi'].any((name)=>mode.startsWith('$name-'));
 if(original && mode.startsWith('window-init-')) {
  check(!state.listener && !state.ui && !state.core,'original pre-init window path unexpectedly reached core/UI');
  check(mode.endsWith('-hang')?!finished:finished && failure!=null,'actual original window failure not reproduced: $mode');
 } else if(original && mode.endsWith('-sync-throw')) {
  check(finished && failure!=null && state.core && !state.ui,'original sync optional failure not reproduced: $mode');
  check(state.callback==!mode.startsWith('connect-status-'),'original callback order changed: $mode');
 } else if(original && mode.endsWith('-hang')) {
  check(!finished && state.listener && !state.ui,'actual original hang not reproduced: $mode');
 } else if(original && mode.endsWith('-throw')) {
  check(finished && !state.core && state.callback && state.ui && !state.failureUi,'actual original swallowed failure not reproduced: $mode');
 } else if(critical && os=='mac') {
  check(finished && state.failureUi && state.ui,'critical failure did not show failure UI: $mode');
  check(!platformFFI.mixelNativeInitialized && !state.callback && !state.handlers,'failed startup continued into models/callback: $mode');
  check(!mixelSupportUrlCanReceive && !mixelSupportUrlHasLiveUi,'failed core advertised readiness: $mode');
  _publishMixelSupportUrlReady();
  check(!await _renewSupportInviteAttended() && !await reporterIsOnline(),'failed startup claimed attended/online: $mode');
  check(state.writes==0 && state.proofReads==0 && state.onlineReads==0,'failed startup touched runtime proof: $mode');
  if(mode=='main-init-hang') {
   state.blocks['main-init']!.complete();await Future<void>.delayed(const Duration(milliseconds:10));
   check(state.core && !platformFFI.mixelNativeInitialized && !mixelSupportUrlCanReceive && !state.callback,'late core completion resurrected failed startup');
  }
 } else {
  check(failure==null,'fixed startup leaked error: $mode/$os');
  check(finished && state.core && state.callback && state.ui && !state.failureUi,'healthy/fallback failed: $mode/$os');
  check(platformFFI.mixelNativeInitialized && platformFFI.symbolLoaded,'healthy core readiness missing');
  if(os=='mac') {
   check(mixelSupportUrlCanReceive && mixelSupportUrlHasLiveUi,'healthy readiness blocked');
   _publishMixelSupportUrlReady();check(await _renewSupportInviteAttended() && await reporterIsOnline(),'healthy proof blocked');
   check(state.writes==2 && state.proofReads==1 && state.onlineReads==1,'healthy proof call parity changed');
   if(mode.startsWith('device-info-'))check(state.deviceId=='NA' && state.deviceName=='Flutter','metadata fallback changed existing defaults');
  }
 }
 print('PASS ${original?'original negative':'fixed'} $os $mode${cm?' cm':''}');
 if(finished)await future;
}
Future<void> main() async {
 for(final mode in ['device-info-hang','device-info-throw','main-init-hang','main-init-throw','version-hang','connect-status-hang','ab-cache-hang'])await scenario(mode,original:true);
 for(final mode in ['window-init-hang','window-init-throw','window-init-sync-throw','connect-status-sync-throw','ab-cache-sync-throw','group-cache-sync-throw'])await scenario(mode,original:true);
 for(final mode in ['healthy','documents-hang','documents-throw','device-info-hang','device-info-throw','version-hang','version-throw','connect-status-hang','connect-status-throw','ab-cache-hang','ab-cache-throw','group-cache-hang'])await scenario(mode);
 for(final mode in ['window-init-hang','window-init-throw','window-init-sync-throw','connect-status-sync-throw','ab-cache-sync-throw','group-cache-sync-throw'])await scenario(mode);
 for(final name in ['window-close','window-movable'])for(final suffix in ['hang','throw','sync-throw'])await scenario('$name-$suffix');
 for(final name in ['device-id','device-name','home-dir','main-init','global-ffi'])for(final suffix in ['hang','throw']) {
  // initGlobalFFI is a synchronous model constructor in real source, so only
  // its throwing seam is meaningful; no invented production timeout here.
  if(name=='global-ffi' && suffix=='hang')continue;
  await scenario('$name-$suffix');
 }
 await scenario('cm-init-hang',cm:true);await scenario('cm-init-throw',cm:true);
 for(final os in ['windows','linux','android','ios'])await scenario('healthy',os:os);
 print('PASS: actual startup metadata fallbacks, critical denial, safe UI, late core completion, readiness/reporter gates and non-Mac healthy parity');
}
'''


def dart_fixture(baseline, generated):
    native = "flutter/lib/models/native_model.dart"
    main = "flutter/lib/main.dart"
    common = generated["flutter/lib/common.dart"]
    original_init = between(baseline[native], "  Future<void> init(String appType)", "  Future<bool> tryHandle")
    fixed_init = between(generated[native], "  Future<void> init(String appType)", "  Future<bool> tryHandle")
    # Extract only the startup-owned fields. Earlier overlays may insert their
    # own lifecycle resources before the pending-event map; those have separate
    # actual-owner fixtures and must not be replaced with synthetic classes.
    field_start = generated[native].index("  // MIXEL_MACOS_STARTUP_V1")
    field_end = generated[native].index(": operation;\n", field_start) + len(": operation;\n")
    fields = generated[native][field_start:field_end]
    original_env = between(baseline[main], "Future<void> initEnv(", "void runMainApp(").replace("initEnv(", "originalInitEnv(", 1)
    original_run = between(baseline[main], "void runMainApp(", "  bool? alwaysOnTop;").replace("void runMainApp(", "Future<void> originalRunMainApp(", 1).replace("await initEnv(", "await originalInitEnv(") + "}\n"
    fixed_env = between(generated[main], "Future<bool> initEnv(", "void runMainApp(")
    fixed_run = between(generated[main], "void runMainApp(", "  bool? alwaysOnTop;").replace("void runMainApp(", "Future<void> runMainApp(", 1) + "}\n"
    def window_entry(text, name, run):
        start = text.index("    desktopType = DesktopType.main;", text.index("Future<void> main("))
        end = text.index("\n  }\n}", start)
        body = text[start:end].replace("runMainApp(true);", "await " + run + "(true);")
        return "Future<void> " + name + "() async {\n" + body + "\n}\n"
    entries = window_entry(baseline[main], "originalWindowEntry", "originalRunMainApp") + window_entry(generated[main], "fixedWindowEntry", "runMainApp")
    gates = "\n".join(line for line in common.splitlines() if line.startswith("bool get mixelSupportUrlCanReceive") or line.startswith("  _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;") or line.startswith("bool get mixelSupportUrlHasLiveUi"))
    publisher = method(common, "void _publishMixelSupportUrlReady()")
    renew = method(common, "Future<bool> _renewSupportInviteAttended()")
    online = between(common, "    isOnline: () async {", "    armAttended:")
    online = online.replace("    isOnline: () async {", "Future<bool> reporterIsOnline() async {", 1).rstrip().removesuffix("},") + "}\n"
    fixture = SEAMS + "class OriginalNative extends Native {\n" + original_init + "}\nclass FixedNative extends Native {\n" + fields + fixed_init + "}\n" + original_env + original_run + fixed_env + fixed_run + entries + gates + publisher + renew + online + SCENARIOS
    # Accelerate only explicit timeout duration seams, never branch/order/body.
    fixture = fixture.replace("const Duration(seconds: 2)", "const Duration(milliseconds: 25)").replace("const Duration(seconds: 5)", "const Duration(milliseconds: 40)")
    return fixture


def read_startup_profile(source):
    launch_spec = importlib.util.spec_from_file_location("startup_launch_projection", SCRIPTS / "patch-support-macos-gui-launch.py")
    launch = importlib.util.module_from_spec(launch_spec)
    launch_spec.loader.exec_module(launch)
    with historical_fixture.linux_close_baseline(source) as prior:
        with launch.previous(prior) as startup_source:
            return {name: (startup_source / name).read_text() for name in patch.INPUT_HASHES}


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        current = read_startup_profile(UPSTREAM)
        cls.baseline = patch.transform(current, reverse=True) if any(patch.MARKER in value for value in current.values()) else current
        if any(patch.digest(cls.baseline[name]) != value for name, value in patch.INPUT_HASHES.items()):
            # CI may invoke this gate before branding. Rebuild the exact full
            # product pipeline in a private pinned checkout, never the input.
            with tempfile.TemporaryDirectory(prefix="mixel-startup-source-") as directory:
                root = Path(directory).resolve() / "rustdesk"
                def command(args, **kwargs):
                    result = subprocess.run(args, capture_output=True, text=True, timeout=90, **kwargs)
                    if result.returncode: raise RuntimeError(result.stdout[-3000:] + result.stderr[-3000:])
                    return result
                command(["git", "clone", "--quiet", "--shared", "--branch", "1.4.6", str(UPSTREAM), str(root)])
                command(["git", "clone", "--quiet", "--shared", str(UPSTREAM / "libs/hbb_common"), str(root / "libs/hbb_common")])
                pin = command(["git", "-C", str(root), "ls-tree", "HEAD", "libs/hbb_common"]).stdout.split()[2]
                command(["git", "-C", str(root / "libs/hbb_common"), "checkout", "--quiet", pin])
                command(["bash", str(SCRIPTS / "apply-branding.sh")], cwd=SCRIPTS.parent,
                        env={**os.environ, "RDREPO": str(root), "BRANDING": str(SCRIPTS.parent / "branding")})
                current = read_startup_profile(root)
                cls.baseline = patch.transform(current, reverse=True)
        cls.generated = patch.transform(cls.baseline)
        cls.dart = find_dart()
        if not cls.dart: raise SystemExit("Dart SDK required for actual Mac startup gate")

    def fixture(self, root, crlf=False):
        for name, text in self.baseline.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.replace("\n", "\r\n" if crlf else "\n").encode())

    def test_actual_source_startup_failures_and_healthy_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "startup.dart"
            source.write_text(dart_fixture(self.baseline, self.generated))
            result = subprocess.run([self.dart, "run", str(source)], capture_output=True, text=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout, end="")
            result = subprocess.run([self.dart, "analyze", str(source)], capture_output=True, text=True, timeout=35)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout, end="")

    def test_exact_idempotent_overlay_restore_lf_and_crlf(self):
        for crlf in (False, True):
            with self.subTest(crlf=crlf), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root, crlf)
                before = {name: (root / name).read_bytes() for name in patch.INPUT_HASHES}
                patch.apply(root)
                first = {name: (root / name).read_bytes() for name in patch.INPUT_HASHES}
                patch.apply(root)
                self.assertEqual(first, {name: (root / name).read_bytes() for name in patch.INPUT_HASHES})
                patch.apply(root, restore=True)
                self.assertEqual(before, {name: (root / name).read_bytes() for name in patch.INPUT_HASHES})
                patch.apply(root)
                self.assertEqual(first, {name: (root / name).read_bytes() for name in patch.INPUT_HASHES})

    def test_source_corruption_partial_overlay_and_ui_collision_refused_before_writes(self):
        for case in ("baseline", "fixed", "missing-ui", "foreign-ui", "symlink-ui", "symlink-source", "hardlink-source", "mixed-newlines", "symlink-parent"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.fixture(root)
                if case in ("fixed", "missing-ui"): patch.apply(root)
                path = root / "flutter/lib/main.dart"
                ui = root / patch.RESOURCE
                if case in ("baseline", "fixed"): path.write_text(path.read_text() + "// changed source\n")
                if case == "missing-ui": ui.unlink()
                if case == "foreign-ui": ui.write_text("foreign")
                if case == "symlink-ui": ui.symlink_to(path)
                if case == "symlink-source":
                    actual = root / "main-copy.dart"
                    path.rename(actual)
                    path.symlink_to(actual)
                if case == "hardlink-source": os.link(path, root / "main-linked.dart")
                if case == "mixed-newlines": path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n", 1))
                if case == "symlink-parent":
                    parent = root / "flutter/lib/models"
                    actual = root / "models-moved"
                    parent.rename(actual)
                    parent.symlink_to(actual, target_is_directory=True)
                before = {name: (root / name).read_bytes() for name in patch.INPUT_HASHES}
                with self.assertRaises(SystemExit): patch.apply(root)
                self.assertEqual(before, {name: (root / name).read_bytes() for name in patch.INPUT_HASHES})

    def test_all_entrypoints_stop_and_ui_has_no_native_dependency(self):
        main = self.generated["flutter/lib/main.dart"]
        self.assertNotIn("  await initEnv(", main)
        self.assertEqual(main.count("if (!await initEnv("), 5)
        ui = (SCRIPTS / "macos-startup-failure.dart").read_text()
        for forbidden in ("common.dart", "native_model", "platformFFI", "gFFI", "bind.", "translate(", "initEnv("):
            self.assertNotIn(forbidden, ui)
        self.assertIn("runApp(MixelStartupFailureApp(windowId: windowId))", ui)
        self.assertIn("SingleChildScrollView", ui)
        self.assertIn("exit(1)", ui)
        for locale in ("de", "fr", "it"): self.assertIn("case '" + locale + "':", ui)
        common = self.generated["flutter/lib/common.dart"]
        self.assertIn("import 'dart:math';", common)
        self.assertIn("Random.secure()", common)
        self.assertIn("Future<void> _reportSupportInvite(String token, String apiKey) async {\n  if (isMacOS && !platformFFI.mixelNativeInitialized) return;", common)
        self.assertIn("import 'package:flutter_hbb/consts.dart';", common)
        self.assertNotIn("import '../consts.dart';", common)

    def test_metadata_callees_only_update_in_memory_metadata(self):
        ffi = (UPSTREAM / "src/flutter_ffi.rs").read_text(encoding="utf-8")
        self.assertEqual(method(ffi, "pub fn main_device_id(id: String)"),
                         "pub fn main_device_id(id: String) {\n    *crate::common::DEVICE_ID.lock().unwrap() = id;\n}")
        self.assertEqual(method(ffi, "pub fn main_device_name(name: String)"),
                         "pub fn main_device_name(name: String) {\n    *crate::common::DEVICE_NAME.lock().unwrap() = name;\n}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
