#!/usr/bin/env python3
"""Patch pinned real source twice; execute its cold/warm support URI path."""
import os
import importlib.util
import json
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from sdk_discovery import find_dart

root = Path(__file__).resolve().parents[1]
upstream = Path(os.environ.get("RDREPO", root / "rustdesk"))
dart = find_dart()
if not dart:
    raise SystemExit("Dart SDK required: put dart on PATH or set DART_BIN")
rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
if not rustc:
    raise SystemExit("Rust compiler required for native cache regression check")
targets = [
    "Cargo.toml", "libs/hbb_common/Cargo.toml", "libs/hbb_common/src/lib.rs", "libs/portable/src/main.rs",
    "flutter/lib/common.dart", "flutter/lib/main.dart", "src/ui_interface.rs",
    "src/ipc.rs", "src/ui_cm_interface.rs", "src/server/connection.rs", "src/server/dbus.rs", "src/core_main.rs", "src/common.rs", "src/updater.rs",
    "flutter/lib/desktop/pages/desktop_setting_page.dart",
    "flutter/lib/utils/http_service.dart",
    "src/lang/en.rs", "src/lang/de.rs", "src/lang/fr.rs", "src/lang/it.rs",
    "libs/hbb_common/src/password_security.rs",
]


def original_source(path: str) -> str:
    if path.startswith("libs/hbb_common/"):
        git_root = upstream / "libs/hbb_common"
        git_path = path.removeprefix("libs/hbb_common/")
    else:
        git_root = upstream
        git_path = path
    return subprocess.run(
        ["git", "-C", str(git_root), "show", f"HEAD:{git_path}"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout


with tempfile.TemporaryDirectory(prefix="mixel-support-patch-") as tmp:
    repo = Path(tmp)
    for path in targets:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original_source(path), encoding="utf-8")
    env = {**os.environ, "RDREPO": str(repo)}
    patcher = root / "scripts/patch-support-invite.py"
    # Simulate Windows CP1252 as the implicit text IO encoding. All source must
    # remain valid UTF-8, including French accents and the typographic apostrophe.
    windows_encoding_runner = """
import runpy
import sys
from pathlib import Path
original_open = Path.open
def windows_default_open(self, mode='r', buffering=-1, encoding=None, *args, **kwargs):
    if 'b' not in mode and encoding in (None, 'locale'):
        encoding = 'cp1252'
    return original_open(self, mode, buffering, encoding, *args, **kwargs)
Path.open = windows_default_open
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    patched = subprocess.run([sys.executable, "-c", windows_encoding_runner, str(patcher)], env=env, capture_output=True, text=True, encoding="utf-8")
    if patched.returncode:
        raise RuntimeError(patched.stdout + patched.stderr)
    patched_paths = targets + ["flutter/lib/mixel_support_invite.dart"]
    first = {path: (repo / path).read_text(encoding="utf-8") for path in patched_paths}
    subprocess.run([sys.executable, str(patcher)], env=env, check=True, capture_output=True, text=True, encoding="utf-8")
    second = {path: (repo / path).read_text(encoding="utf-8") for path in patched_paths}
    assert first == second, "patch must be idempotent across all touched files"
    guard_spec = importlib.util.spec_from_file_location("compiled_guard", root / "scripts/test-compiled-support-guard.py")
    guard_runner = importlib.util.module_from_spec(guard_spec)
    guard_spec.loader.exec_module(guard_runner)
    manifest = repo / "libs/hbb_common/Cargo.toml"
    original_manifest = manifest.read_bytes()
    windows_manifest = original_manifest.decode("utf-8").replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    windows_modified = guard_runner.guard_test_manifest(windows_manifest.decode("utf-8")).encode("utf-8")
    assert b'webrtc = "0.14.0"' not in windows_modified
    assert b"\r\r\n" not in windows_modified, "temporary Windows manifest must not double its CRLF bytes"
    manifest.write_bytes(windows_manifest)
    original_manifest = windows_manifest
    for raises in (False, True):
        def fake_cargo(command, check):
            modified = manifest.read_text(encoding="utf-8")
            assert 'webrtc = { version = "0.14.0", optional = true }' in modified
            assert 'webrtc = "0.14.0"' not in modified.split("[dev-dependencies]", 1)[1]
            assert all(option in command for option in ("--locked", "--release", "--no-default-features", "hbb_common"))
            if raises:
                raise RuntimeError("simulated Cargo process failure")
            return SimpleNamespace(returncode=3)
        try:
            result = guard_runner.run_guard_tests(repo, fake_cargo)
            assert not raises and result == 3
        except RuntimeError as error:
            assert raises and str(error) == "simulated Cargo process failure"
        assert manifest.read_bytes() == original_manifest, "narrow test must restore upstream manifest on every failure path"
    print("PASS: actual guard test retains optional WebRTC and restores upstream manifest after Cargo failures")
    french = (repo / "src/lang/fr.rs").read_bytes()
    assert "d’assistance Mixel Remote installé doit être mis à jour".encode("utf-8") in french
    assert "d’assistance Mixel Remote installé doit être mis à jour" in french.decode("utf-8")
    print("PASS: CP1252-default source patch preserves UTF-8 translations and idempotence")
    common = first["flutter/lib/common.dart"]
    assert common.count("Future<void> _reportSupportInvite(") == 1
    assert common.count("registerProtocol('mixel-remote');") == 1
    assert "'attendedReady': true" in common
    assert "{'request': nonce}" in common
    assert "return requests.remove(&url);" in first["src/ui_interface.rs"]
    assert "Duration::from_secs(30)" in first["src/ui_interface.rs"]
    assert "timeout(6_000, request_future).await?" in first["src/common.rs"]
    assert "Some(false) // Never expose support bearer data" in first["src/common.rs"]
    native_http = first["src/common.rs"].split("pub async fn http_request_sync(", 1)[1].split("\n#[inline]", 1)[0]
    assert "let support_request =" in native_http, "support transport guard must be in native FFI function scope"
    assert "let support_request =" not in first["src/common.rs"].split("pub async fn http_request_sync(", 1)[0], "unrelated POST transport stays unchanged"
    assert "true, value.as_deref()).to_owned()" in first["src/ui_interface.rs"]
    assert "crate::common::is_server_running()" not in first["src/ui_interface.rs"].split("pub fn get_option<T: AsRef<str>>(key: T) -> String {", 1)[1].split("effective_support_approve_mode", 1)[0]
    assert "proof == 'attended-runtime-v2'" in common
    assert "support_invite_owner_lease_failed()" in first["src/ui_interface.rs"]
    assert "hold_support_invite_attended_lease()" in first["src/ui_interface.rs"]
    assert "hold_support_invite_attended_lease" not in first["src/ipc.rs"], "incoming service heartbeat must not own a foreground lease"
    assert "_supportInviteCompatibilityNotice.showIfRequired" in common
    assert "!bind.isCustomClient() && bind.mainIsInstalled()" in first["flutter/lib/desktop/pages/desktop_setting_page.dart"]
    updater = first["src/updater.rs"]
    assert updater.count("if crate::is_custom_client() || is_mixel_store_package()") == 3
    assert updater.index("if crate::is_custom_client() || is_mixel_store_package()", updater.index("fn check_update(manually:")) < updater.index("do_check_software_update().is_err()"), "Mixel updater must stop before upstream request/download"
    assert "if (!useFlutterHttp && !supportRequest)" in first["flutter/lib/utils/http_service.dart"]
    assert "sensitive: supportRequest" in first["flutter/lib/utils/http_service.dart"]
    assert "if (sensitive) throw Exception('Support request failed.');" in first["flutter/lib/utils/http_service.dart"]
    assert "_supportInviteAttendedTimer ??= Timer.periodic" in common
    assert "launch args: $args" not in first["flutter/lib/main.dart"]
    assert 'print("initialLink: $initialLink");' not in common
    assert 'debugPrint("A uri was received: $uri.' not in common
    assert 'print("uni links error: $err");' not in common
    connection = first["src/server/connection.rs"]
    guard_at = connection.index("} else if password::support_invite_must_wait(")
    recent_at = connection.index("} else if self.is_recent_session(false)")
    password_at = connection.index("} else if lr.password.is_empty()", recent_at)
    assert guard_at < recent_at < password_at, "customer click must precede remembered/password access"
    central = connection.split("async fn send_logon_response_and_keep_alive(&mut self) -> bool {", 1)[1].split("self.authorized = true;", 1)[0]
    assert "support_invite_must_wait(" in central, "all automatic authorization must wait for customer Accept"
    assert "conn.support_invite_accepted = true;" in connection.split("ipc::Data::Authorize => {", 1)[1].split("}", 1)[0]
    assert "effective_support_approve_mode(" in first["src/ui_interface.rs"], "saved password mode must still display CM Accept during support"
    assert "renew_support_invite_attended();" in connection.split("_ = second_timer.tick() => {", 1)[1].split("raii::AuthedConnID", 1)[0], "pending Accept remains visible after original guard deadline"
    core = first["src/core_main.rs"]
    assert "if args.is_empty() || _is_mixel_support_invite || crate::common::is_empty_uni_link(&args[0])" in core
    assert core.count("(args.is_empty() || _is_mixel_support_invite)") == 2, "support URI must start portable service too"
    assert core.index("hold_support_invite_attended_lease();") < core.index("crate::start_server(false, no_server)"), "arm attended mode before receiving connections"
    linux_dispatch = core.split("// linux uni (url) go here.", 1)[1].split("#[cfg(windows)]", 1)[0]
    assert "if _is_mixel_support_invite" in linux_dispatch
    assert "flutter_args.extend(args.iter().cloned());" in core
    assert "if try_send_by_dbus(args[0].clone()).is_none() { return None; }" in linux_dispatch
    assert "!support_invite_requires_click()" in first["libs/hbb_common/src/password_security.rs"]
    setter = first["src/ui_interface.rs"].split("pub fn set_option(key: String, value: String) {", 1)[1]
    runtime_only = setter.split('    if &key == "stop-service"', 1)[0]
    assert "Config::set_option" not in runtime_only and "OPTIONS.lock" not in runtime_only
    print("PASS: real pinned source patches idempotently, bearer logs redacted, customer-click guard precedes password/recent-session bypasses, preferences preserved")

    # Compile the actual generated forwarding block. The pinned Windows runner
    # appends that Rust vector after the original CLI vector, preserving URI
    # first even if initialLink is unavailable. Do not assume Rust args alone
    # are the complete Windows Dart boot arguments.
    forward_start = core.index("            // Forward URI/CLI handoffs")
    forward_end = core.index("\n        }", forward_start)
    forward = core[forward_start:forward_end]
    boot_test = repo / "native_support_boot_vector.rs"
    boot_test.write_text('''fn main() {
    let mut input = std::env::args().skip(1);
    let args = vec![input.next().unwrap()];
    let mut flutter_args: Vec<String> = input.collect();
''' + forward + '''
    for arg in flutter_args { println!("{arg}"); }
}
''', encoding="utf-8")
    boot_binary = repo / ("native_support_boot_vector.exe" if os.name == "nt" else "native_support_boot_vector")
    subprocess.run([rustc, "--edition=2021", "--deny=warnings", str(boot_test), "-o", str(boot_binary)], check=True)
    synthetic_uri = "mixel-remote://support?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-public-key-000000000000"
    native_boot_vectors = []
    for markers in (["--mixel-attended"], ["--mixel-attended", "--mixel-attended-handoff-unavailable"]):
        vector = subprocess.run([str(boot_binary), synthetic_uri, *markers], check=True, capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
        assert vector == [*markers, synthetic_uri], "Native forwarding must preserve all actual QS markers and the URI"
        native_boot_vectors.append([synthetic_uri, *vector])

    # Execute the actual generated URI parser and handler before their unrelated
    # outgoing-connection branches, without needing a desktop for these tests.
    handler = common[common.index("bool handleUriLink("):common.index("  UriLinkType? type;", common.index("bool handleUriLink("))]
    handler += "  return false;\n}\n"
    parser_start = common.index("  if (uri.scheme.toLowerCase() == 'mixel-remote' && uri.host.toLowerCase() == 'support') {")
    parser_end = common.index("  } else if (uri.authority.isEmpty &&", parser_start)
    parser = "List<String>? urlLinkToCmdArgs(Uri uri) {\n" + common[parser_start:parser_end] + "  }\n  return null;\n}\n"
    # This is the exact window-hiding condition used by the pinned app startup.
    startup_condition = "if (handledByUniLinks || handleUriLink(cmdArgs: kBootArgs))"
    assert startup_condition in first["flutter/lib/main.dart"]
    # The portable QS filename becomes this raw argument. The locked plugin
    # transports argv[1] as a string, not cmdArgs. Execute the actual generated
    # raw listener before URI canonicalization, including real restore/show.
    windows_runner = original_source("flutter/windows/runner/main.cpp")
    assert "command_line_arguments.insert(command_line_arguments.end(), rust_args.begin(), rust_args.end());" in windows_runner
    assert "project.set_dart_entrypoint_arguments(std::move(command_line_arguments));" in windows_runner
    existing_window = windows_runner.split("  if (hwnd != NULL) {", 1)[1].split("  // Attach to console", 1)[0]
    assert "if (!command_line_arguments.empty())" in existing_window
    assert "DispatchToUniLinksDesktop(hwnd);" in existing_window
    assert "rust_args" not in existing_window
    portable = original_source("libs/portable/src/main.rs")
    assert 'args = vec!["--quick_support".to_owned()];' in portable
    window_on_top = common[common.index("Future<void> windowOnTop(int? id)"):common.index("\ntypedef DialogBuilder", common.index("Future<void> windowOnTop(int? id)"))]
    listener = common[common.index("StreamSubscription? listenUniLinks("):common.index("\nenum UriLinkType", common.index("StreamSubscription? listenUniLinks("))]
    assert "linkStream.listen((String? rawLink)" in listener
    assert "uriLinkStream.listen" not in listener
    runner = repo / "flutter/lib/support_launch_test.dart"
    runner.write_text("import 'dart:async';\nimport 'mixel_support_invite.dart';\n" + """
class FakeBind {
  String mainUriPrefixSync() => 'mixel-remote://';
  void sendUrlScheme({required String url}) {}
}
final bind = FakeBind();
var shown = 0;
var reported = 0;
var renewals = 0;
Timer? _supportInviteAttendedTimer;
Future<bool> _renewSupportInviteAttended() async { renewals++; return true; }
const isDesktop = true;
const isLinux = false;
const isWeb = false;
void debugPrint(String message) {}
final nativeLinks = StreamController<String?>();
Stream<String?> get linkStream => nativeLinks.stream;
""" + "final nativeBootVectors = <List<String>>" + json.dumps(native_boot_vectors) + ";\n" + """
const kWindowMainId = 0;
const kWindowEventShow = 'show';
enum WindowType { Main }
class FakeState { bool isMinimized = false; }
final stateGlobal = FakeState();
class FakeWindowManager {
  bool visible = false;
  var restored = 0;
  Future<void> restore() async { restored++; stateGlobal.isMinimized = false; }
  Future<void> show() async { shown++; visible = true; }
  Future<void> focus() async {}
}
final windowManager = FakeWindowManager();
class FakeRustDeskWinManager {
  Future<void> registerActiveWindow(int id) async { if (id != kWindowMainId) throw StateError('Wrong restored window'); }
  void call(WindowType type, String event, Map<String, int> data) {}
}
final rustDeskWinManager = FakeRustDeskWinManager();
class WindowController {
  static WindowController fromWindowId(int id) => WindowController();
  void focus() {}
  void show() {}
}
Future<void> _reportSupportInvite(String token, String key) async { reported++; }
""" + window_on_top + handler + parser + listener + """
Future<void> main() async {
  final link = Uri(scheme: 'mixel-remote', host: 'support', queryParameters: {
    'invite':'inv_00000000-0000-0000-0000-000000000001',
    'apikey':'synthetic-public-key-000000000000'
  });
  var hidden = false;
  final handledByUniLinks = handleUriLink(uriString: link.toString());
  final kBootArgs = [link.toString()];
  """ + startup_condition + """ { hidden = true; }
  if (hidden || shown == 0) throw StateError('Cold support launch hid the app');
  final before = shown;
  if (handleUriLink(uri: link)) throw StateError('Warm support link returned connection intent');
  if (shown != before + 1) throw StateError('Warm support launch failed to show app');
  if (handleUriLink(cmdArgs: ['--support-invite'])) throw StateError('Truncated args accepted');
  if (handleUriLink(uri: Uri.parse('mixel-remote://support?invite=invalid&apikey=short'))) {
    throw StateError('Invalid invite accepted');
  }
  for (final variant in [
    link.toString().replaceFirst('://support?', '://support/?'),
    link.toString().replaceFirst('mixel-remote://support', 'MIXEL-REMOTE://SUPPORT'),
  ]) {
    if (urlLinkToCmdArgs(Uri.parse(variant)) == null) throw StateError('Normalized support URI rejected');
    if (handleUriLink(cmdArgs: [variant])) throw StateError('Normalized handoff hid app');
  }
  for (final variant in [
    link.toString().replaceFirst('://support?', '://support/other?'),
    link.toString().replaceFirst('://support?', '://user@support?'),
    link.toString().replaceFirst('://support?', '://support:443?'),
    '${link.toString()}#fragment',
  ]) {
    if (urlLinkToCmdArgs(Uri.parse(variant)) != null) throw StateError('Malformed support route accepted');
  }
  await Future<void>.delayed(Duration.zero);
  if (reported != 5) throw StateError('Only valid handoffs may schedule presence');
  // An ordinary existing GUI starts without any attended timer. Exercise the
  // actual native string stream, rather than incorrectly substituting cmdArgs.
  if (_supportInviteAttendedTimer != null || renewals != 0) throw StateError('Ordinary GUI fixture was already attended');
  stateGlobal.isMinimized = true;
  windowManager.visible = false;
  final sub = listenUniLinks()!;
  nativeLinks.add('--quick_support');
  await Future<void>.delayed(const Duration(milliseconds: 20));
  if (stateGlobal.isMinimized || !windowManager.visible || _supportInviteAttendedTimer == null || renewals != 1) {
    throw StateError('Actual raw QuickSupport handoff failed to attend and restore ordinary GUI');
  }
  final shownBeforeMalformed = shown;
  final renewalsBeforeMalformed = renewals;
  for (final variant in [
    '--quick%5fsupport', '--QUICK_SUPPORT', '--quick_support?',
    '--quick_support/', '--quick_support#fragment', '--quick_support extra',
    ' --quick_support', '--quick_support\\n', '--mixel-attended', '--quick_suppor',
  ]) {
    nativeLinks.add(variant);
  }
  await Future<void>.delayed(const Duration(milliseconds: 20));
  if (shown != shownBeforeMalformed || renewals != renewalsBeforeMalformed || reported != 5) {
    throw StateError('Malformed/encoded raw QuickSupport variant changed consent or UI');
  }
  await sub.cancel();
  await nativeLinks.close();
  if (handleUriLink(uriString: '--quick_support')) throw StateError('Cold raw QuickSupport became outbound intent');
  if (handleUriLink(cmdArgs: ['--mixel-attended'])) throw StateError('QuickSupport launch hid app');
  if (_supportInviteAttendedTimer == null) throw StateError('QuickSupport failed to maintain consent guard');
  await Future<void>.delayed(Duration.zero);
  final attendedTimer = _supportInviteAttendedTimer;
  for (final args in [['--quick_support'], ['--mixel-attended']]) {
    stateGlobal.isMinimized = true;
    windowManager.visible = false;
    final restoredBefore = windowManager.restored;
    final renewalBefore = renewals;
    if (handleUriLink(cmdArgs: args)) throw StateError('Warm QuickSupport returned outbound connection intent');
    await Future<void>.delayed(Duration.zero);
    if (stateGlobal.isMinimized || !windowManager.visible || windowManager.restored != restoredBefore + 1) {
      throw StateError('Warm QuickSupport did not restore the existing main window');
    }
    if (renewals != renewalBefore + 1 || !identical(attendedTimer, _supportInviteAttendedTimer)) {
      throw StateError('Warm QuickSupport did not renew and retain its customer consent heartbeat');
    }
  }
  if (reported != 5) throw StateError('QuickSupport must not report synthetic invite presence');
  final reportsBeforeFallback = reported;
  for (final nativeArgs in nativeBootVectors) {
    // Simulate initialLink being unavailable: only kBootArgs is handled.
    if (handleUriLink(cmdArgs: nativeArgs)) throw StateError('Native support fallback became outbound intent');
  }
  await Future<void>.delayed(Duration.zero);
  if (reported != reportsBeforeFallback + nativeBootVectors.length) {
    throw StateError('Full native marker-bearing boot vector lost valid invite reporting');
  }
  _supportInviteAttendedTimer!.cancel();
  print('PASS: generated cold/warm support URI paths show the app, reject invalid/truncated arguments, never request outbound connection');
  print('PASS: actual generated raw native link stream attends an ordinary minimized GUI, rejects encoded/extended flags and retains its consent heartbeat; cold flag returns no outbound intent');
  print('PASS: complete actual native support boot vectors retain invite reporting when initialLink is unavailable, including failed-handoff marker');
}
""", encoding="utf-8")
    subprocess.run([dart, str(runner)], check=True)

    # Execute the generated Rust one-shot cache accessor itself. This catches
    # URL correlation/consumption bugs that a reporter mock cannot expose.
    # Execute the generated same-connection setter with a deferred GUI callback.
    # Writing a frame is not acknowledgment: the old setter leaves the service
    # guard unset when the transient sender exits. Only the processed read-back
    # closes that gap, and stale/mismatched/missing replies must fail closed.
    ipc_source = first["src/ipc.rs"]
    ipc_start = ipc_source.index("pub async fn set_config_async(")
    ipc_end = ipc_source.index('\n#[tokio::main', ipc_start)
    ipc_setter = ipc_source[ipc_start:ipc_end]
    original_ipc = original_source("src/ipc.rs")
    original_start = original_ipc.index("pub async fn set_config_async(")
    original_end = original_ipc.index('\n#[tokio::main', original_start)
    original_setter = original_ipc[original_start:original_end].replace(
        "pub async fn set_config_async(", "pub async fn original_set_config_async(", 1)
    handoff_test = repo / "native_attended_handoff_test.rs"
    handoff_test.write_text('''
use std::future::Future;
use std::sync::Mutex;
use std::task::{Context, Poll, Wake, Waker};
use std::sync::Arc;
type ResultType<T> = Result<T, &'static str>;
macro_rules! bail { ($message:expr) => { return Err($message) }; }
#[derive(Clone)] enum Data { Config((String, Option<String>)), Other }
struct State { mode: &'static str, memory: bool, sender_owner: bool, receiver_owner: bool, operations: Vec<Data> }
static STATE: Mutex<State> = Mutex::new(State { mode: "ok", memory: false, sender_owner: true, receiver_owner: false, operations: Vec::new() });
mod password {
    pub const SUPPORT_INVITE_ATTESTATION: &str = "attended-runtime-v2";
    pub fn renew_support_invite_attended() { super::STATE.lock().unwrap().memory = true; }
}
struct Connection;
async fn connect(deadline: u64, postfix: &str) -> ResultType<Connection> {
    assert_eq!(deadline, 1000); assert_eq!(postfix, "");
    if STATE.lock().unwrap().mode == "disconnected" { Err("not connected") } else { Ok(Connection) }
}
impl Connection {
    async fn send_config(&mut self, name: &str, value: String) -> ResultType<()> {
        self.send(&Data::Config((name.to_owned(), Some(value)))).await
    }
    async fn send(&mut self, data: &Data) -> ResultType<()> {
        STATE.lock().unwrap().operations.push(data.clone()); Ok(())
    }
    async fn next_timeout(&mut self, deadline: u64) -> ResultType<Option<Data>> {
        assert_eq!(deadline, 1000);
        let operations = std::mem::take(&mut STATE.lock().unwrap().operations);
        assert_eq!(operations.len(), 2, "Set and get must use the same ordered connection");
        match &operations[0] {
            Data::Config((name, Some(value))) => {
                assert_eq!(name, "mixel-support-invite-attended");
                if value == "Y" { password::renew_support_invite_attended(); }
            }
            _ => panic!("Guard acknowledgment must follow renewal"),
        }
        match &operations[1] {
            Data::Config((name, None)) => assert_eq!(name, "mixel-support-invite-attended"),
            _ => panic!("Missing same-stream guard read-back"),
        }
        let mode = STATE.lock().unwrap().mode;
        let proof = if mode == "stale" { "attended-runtime-v1" } else { password::SUPPORT_INVITE_ATTESTATION };
        let name = if mode == "wrong-name" { "approve-mode" } else { "mixel-support-invite-attended" };
        match mode {
            "closed" => Ok(None), "timeout" => Err("deadline"),
            "wrong-type" => Ok(Some(Data::Other)),
            _ => Ok(Some(Data::Config((name.to_owned(), Some(proof.to_owned()))))),
        }
    }
}
async fn timeout<F: Future>(deadline: u64, future: F) -> Result<F::Output, &'static str> {
    assert_eq!(deadline, 1500); Ok(future.await)
}
struct Noop;
impl Wake for Noop { fn wake(self: Arc<Self>) {} }
fn block_on<F: Future>(future: F) -> F::Output {
    let waker = Waker::from(Arc::new(Noop)); let mut context = Context::from_waker(&waker);
    let mut future = Box::pin(future);
    match future.as_mut().poll(&mut context) { Poll::Ready(value) => value, Poll::Pending => panic!("fixture unexpectedly pending") }
}
fn reset(mode: &'static str) {
    *STATE.lock().unwrap() = State { mode, memory: false, sender_owner: true, receiver_owner: false, operations: Vec::new() };
}
''' + original_setter + ipc_setter + '''
#[test] fn original_write_only_setter_exposes_delayed_receiver_guard_gap() {
    reset("ok");
    block_on(original_set_config_async("mixel-support-invite-attended", "Y".to_owned())).unwrap();
    let mut state = STATE.lock().unwrap(); state.sender_owner = false;
    assert!(!state.memory && !state.sender_owner && !state.receiver_owner);
}
#[test] fn acknowledged_setter_covers_sender_exit_before_delayed_gui_lease() {
    reset("ok");
    block_on(set_config_async("mixel-support-invite-attended", "Y".to_owned())).unwrap();
    let mut state = STATE.lock().unwrap(); state.sender_owner = false;
    assert!(state.memory && !state.receiver_owner, "IPC memory guard must already protect the delayed receiver");
    state.receiver_owner = true; assert!(state.memory || state.receiver_owner);
}
#[test] fn missing_stale_or_mismatched_acknowledgment_cannot_release_sender() {
    for mode in ["disconnected", "closed", "timeout", "stale", "wrong-name", "wrong-type"] {
        reset(mode);
        assert!(block_on(set_config_async("mixel-support-invite-attended", "Y".to_owned())).is_err(), "{mode}");
        assert!(STATE.lock().unwrap().sender_owner, "Failed acknowledgment must retain cold foreground owner");
    }
}
#[test] fn unrelated_settings_retain_write_only_transport() {
    reset("ok");
    block_on(set_config_async("ordinary-setting", "Y".to_owned())).unwrap();
    let state = STATE.lock().unwrap(); assert_eq!(state.operations.len(), 1); assert!(!state.memory);
}
''', encoding="utf-8")
    handoff_binary = repo / ("native_attended_handoff_test.exe" if os.name == "nt" else "native_attended_handoff_test")
    subprocess.run([rustc, "--edition=2021", "--deny=warnings", "--test", str(handoff_test), "-o", str(handoff_binary)], check=True)
    subprocess.run([str(handoff_binary), "--test-threads=1"], check=True)
    assert 'if _is_quick_support || _is_mixel_support_invite {' in core
    assert 'if crate::ipc::set_config("mixel-support-invite-attended", "Y".to_owned()).is_err()' in core
    assert 'flutter_args.push("--mixel-attended-handoff-unavailable".to_owned())' in core
    print("PASS: actual generated attended IPC setter acknowledges memory renewal before delayed GUI ownership; stale/missing replies keep the cold owner and unrelated setters retain original behavior")
    ui_source = first["src/ui_interface.rs"]
    start = ui_source.index("pub fn get_async_http_status(url: String) -> Option<String> {")
    end = ui_source.index("\n#[inline]", start)
    cache_test = repo / "native_cache_test.rs"
    cache_test.write_text("""
use std::collections::HashMap;
use std::sync::{LockResult, Mutex, MutexGuard, OnceLock};
static CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();
struct RequestCache;
static ASYNC_HTTP_STATUS: RequestCache = RequestCache;
impl RequestCache {
    fn lock(&self) -> LockResult<MutexGuard<'static, HashMap<String, String>>> {
        CACHE.get_or_init(|| Mutex::new(HashMap::new())).lock()
    }
}
""" + ui_source[start:end] + """
#[test]
fn old_and_current_invite_responses_remain_isolated_and_consumed() {
    let old = "https://rs.mixel.ch/api/presence/client?request=oldnonce".to_owned();
    let current = "https://rs.mixel.ch/api/presence/client?request=newnonce".to_owned();
    ASYNC_HTTP_STATUS.lock().unwrap().insert(old.clone(), " ".to_owned());
    assert_eq!(get_async_http_status(old.clone()), Some(" ".to_owned()));
    ASYNC_HTTP_STATUS.lock().unwrap().insert(current.clone(), "status200".to_owned());
    ASYNC_HTTP_STATUS.lock().unwrap().insert(old.clone(), "status403".to_owned());
    assert_eq!(get_async_http_status(current.clone()), Some("status200".to_owned()));
    assert_eq!(get_async_http_status(current), None);
    assert_eq!(get_async_http_status(old.clone()), Some("status403".to_owned()));
    assert_eq!(get_async_http_status(old), None);
}
#[test]
fn unrelated_http_status_retains_existing_reusable_behavior() {
    let url = "https://example.invalid/generic".to_owned();
    ASYNC_HTTP_STATUS.lock().unwrap().insert(url.clone(), "status200".to_owned());
    assert_eq!(get_async_http_status(url.clone()), Some("status200".to_owned()));
    assert_eq!(get_async_http_status(url), Some("status200".to_owned()));
}
""", encoding="utf-8")
    cache_binary = repo / ("native_cache_test.exe" if os.name == "nt" else "native_cache_test")
    subprocess.run([rustc, "--edition=2021", "--test", str(cache_test), "-o", str(cache_binary)], check=True)
    subprocess.run([str(cache_binary)], check=True)

    # Compile and execute the actual generated native transport code, replacing
    # only low-level HTTP/TLS dependencies and the unchanged JSON serialization.
    # A declaration patched into the wrong function must fail this compilation.
    common_source = first["src/common.rs"]
    native_start = common_source.index("pub async fn http_request_sync(")
    native_end = common_source.index("\n#[inline]", native_start)
    native = common_source[native_start:native_end]
    # Preserve the actual outer deadline and async block; replace only response
    # metadata/JSON serialization, which are unrelated to transport semantics.
    transport = native[:native.index("    // Serialize response headers")]
    body_start = native.index("    let response_body =")
    transport += native[body_start:native.index("    // Construct the JSON object", body_start)]
    transport += "    Ok(response_body)\n" + native[native.index("    };\n    if support_request") :]
    transport_template = (root / "scripts/support-invite-transport-test.rs").read_text(encoding="utf-8")
    transport_test = repo / "native_transport_test.rs"
    transport_test.write_text(transport_template.replace("// GENERATED_HTTP_REQUEST_SYNC", transport), encoding="utf-8")
    transport_binary = repo / ("native_transport_test.exe" if os.name == "nt" else "native_transport_test")
    subprocess.run([rustc, "--edition=2021", "--test", str(transport_test), "-o", str(transport_binary)], check=True)
    subprocess.run([str(transport_binary)], check=True)
    subprocess.run([sys.executable, str(root / "scripts/test-support-dbus.py"),
                    "--source", str(repo), "--upstream", str(upstream)], check=True)
    subprocess.run([sys.executable, str(root / "scripts/test-support-quicksupport-name.py"),
                    "--source", str(repo), "--upstream", str(upstream)], check=True)

print("Result: source patch and actual URI launch regression checks passed")
