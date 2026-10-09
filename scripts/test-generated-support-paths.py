#!/usr/bin/env python3
"""Execute generated update, Linux startup and desktop HTTP routing branches."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from sdk_discovery import find_dart


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
TARGETS = [
    "flutter/lib/common.dart", "flutter/lib/main.dart", "src/ui_interface.rs",
    "src/ipc.rs", "src/ui_cm_interface.rs", "src/server/connection.rs",
    "src/core_main.rs", "src/common.rs", "src/updater.rs",
    "flutter/lib/desktop/pages/desktop_setting_page.dart",
    "flutter/lib/utils/http_service.dart", "src/lang/en.rs", "src/lang/de.rs",
    "src/lang/fr.rs", "src/lang/it.rs", "libs/hbb_common/src/password_security.rs",
]


def pinned_source(path: str) -> str:
    directory = UPSTREAM
    if path.startswith("libs/hbb_common/"):
        directory = UPSTREAM / "libs/hbb_common"
        path = path.removeprefix("libs/hbb_common/")
    return subprocess.run(
        ["git", "-C", str(directory), "show", f"HEAD:{path}"], check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout


def early_body(source: str, signature: str, first_upstream_statement: str) -> str:
    start = source.index(signature)
    end = source.index(first_upstream_statement, start)
    return source[start:end]


def native_runner(repo: Path, rustc: str) -> None:
    updater = (repo / "src/updater.rs").read_text(encoding="utf-8")
    common = (repo / "src/common.rs").read_text(encoding="utf-8")
    core = (repo / "src/core_main.rs").read_text(encoding="utf-8")
    ui = (repo / "src/ui_interface.rs").read_text(encoding="utf-8")
    start = ui.index("pub struct UiStatus {")
    status_struct = ui[start:ui.index("\n}", start)] + "\n}\n"
    start = ui.index("UiStatus{", ui.index("static ref UI_STATUS"))
    initial_status = ui[start:ui.index("}));", start)] + "}"
    start = ui.index("                                if x > 0", ui.index("ipc::Data::OnlineStatus"))
    online_update = ui[start:ui.index("\n                            }", start)]
    # Extract the complete owned early branches, keeping the actual getter and
    # setter control flow. Observable fallthrough stands in for saved options.
    attended_options = ""
    for signature, fallback in (
        ("pub fn get_option<T: AsRef<str>>(key: T) -> String {\n", '    "saved-fallback".to_owned()\n}\n'),
        ("pub fn set_option(key: String, value: String) {\n", '    effect("saved-preference-write");\n}\n'),
    ):
        start = ui.index(signature)
        end = ui.index("\n    }\n", start) + len("\n    }\n")
        attended_options += ui[start:end] + fallback
    # The first closing line above belongs to the match arm after its complete
    # assignment; indentation prevents nested if blocks ending extraction early.
    # Execute each real generated guard before replacing the unrelated download
    # machinery with an observable side effect. The guard is never duplicated.
    updater_functions = (
        early_body(updater, "pub fn start_auto_update() {", "    let _sender")
        + '    effect("auto-update");\n}\n'
        + early_body(updater, "pub fn manually_check_update() -> ResultType<()> {", "    let sender")
        + '    effect("manual-update"); Ok(())\n}\n'
        + early_body(updater, "fn check_update(manually: bool) -> ResultType<()> {", '    #[cfg(target_os = "windows")]')
        + '    let _ = manually; effect("download"); Ok(())\n}\n'
        + early_body(common, "pub async fn do_check_software_update() -> hbb_common::ResultType<()> {", "    let (request, url)")
        + '    effect("manifest-request"); Ok(())\n}\n'
    )
    start = core.index("    let _is_mixel_support_invite = args.first()")
    classification = core[start:core.index('    #[cfg(any(target_os = "linux", target_os = "windows"))]', start)]
    start = core.index("    if args.len() > 0 && args[0].to_ascii_lowercase().starts_with(&crate::get_uri_prefix().to_ascii_lowercase())")
    dispatch = core[start:core.index("    #[cfg(windows)]", start)]
    start = core.index("    if args.is_empty() || _is_mixel_support_invite || crate::common::is_empty_uni_link(&args[0]) {")
    incoming = core[start:core.index("    } else {", start)] + "    }\n"
    # Mock only the platform scheduler: execute the actual generated closure.
    incoming = incoming.replace("std::thread::spawn", "spawn")
    source = r'''
use std::cell::RefCell;
use std::future::Future;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll, Wake, Waker};
type ResultType<T> = Result<T, &'static str>;
#[derive(Default)]
struct Trace {
    custom: bool, store: bool, warm: bool, effects: Vec<&'static str>,
    fail_owner_hold: bool, owner_failed: bool, ipc_failed: bool,
    renewals: Vec<(String, String)>,
}
thread_local! { static TRACE: RefCell<Trace> = RefCell::new(Trace::default()); }
static SOFTWARE_UPDATE_URL: Mutex<String> = Mutex::new(String::new());
fn effect(value: &'static str) { TRACE.with(|trace| trace.borrow_mut().effects.push(value)); }
fn is_custom_client() -> bool { TRACE.with(|trace| trace.borrow().custom) }
fn is_mixel_store_package() -> bool { TRACE.with(|trace| trace.borrow().store) }
fn get_uri_prefix() -> String { "mixel-remote://".to_owned() }
fn try_send_by_dbus(_uri: String) -> Option<Vec<String>> {
    effect("dbus");
    TRACE.with(|trace| if trace.borrow().warm { None } else { Some(Vec::new()) })
}
fn spawn<F: FnOnce()>(operation: F) { operation(); }
fn start_server(_installed: bool, _disabled: bool) { effect("incoming-server"); }
mod common { pub fn is_empty_uni_link(value: &str) -> bool { value == "mixel-remote://" } }
#[allow(dead_code)]
mod platform {
    pub fn try_remove_temp_update_files() {}
    pub mod macos { pub fn try_remove_temp_update_dir(_path: Option<()>) {} }
}
mod hbb_common {
    pub type ResultType<T> = super::ResultType<T>;
    #[allow(dead_code)]
    pub mod config {
        pub struct PeerConfig;
        impl PeerConfig { pub fn preload_peers() {} }
    }
    pub mod password_security {
        pub use crate::real_guard::{is_support_invite_arg, resolve_support_invite_attestation};
        pub fn hold_support_invite_attended_lease() -> bool {
            crate::effect("attended-guard");
            crate::TRACE.with(|trace| {
                let mut state = trace.borrow_mut();
                state.owner_failed = state.fail_owner_hold;
                !state.owner_failed
            })
        }
        pub fn support_invite_owner_lease_failed() -> bool {
            crate::TRACE.with(|trace| trace.borrow().owner_failed)
        }
    }
}
mod ipc {
    pub fn get_config(_key: &str) -> Result<Option<String>, ()> {
        crate::TRACE.with(|trace| if trace.borrow().ipc_failed {
            Err(())
        } else {
            Ok(Some("attended-runtime-v2".to_owned()))
        })
    }
    pub fn set_config(key: &str, value: String) -> Result<(), ()> {
        crate::TRACE.with(|trace| {
            let mut state = trace.borrow_mut();
            state.renewals.push((key.to_owned(), value));
            if state.ipc_failed { Err(()) } else { Ok(()) }
        })
    }
}
#[allow(dead_code)]
mod real_guard { include!("GENERATED_GUARD_PATH"); }
struct NoopWake;
impl Wake for NoopWake { fn wake(self: Arc<Self>) {} }
fn run<F: Future>(future: F) -> F::Output {
    let waker = Waker::from(Arc::new(NoopWake));
    let mut context = Context::from_waker(&waker);
    let mut future = Box::pin(future);
    loop { match future.as_mut().poll(&mut context) {
        Poll::Ready(value) => return value, Poll::Pending => std::thread::yield_now(),
    } }
}
GENERATED_UPDATER
GENERATED_ATTENDED_OPTIONS
GENERATED_STATUS_STRUCT
static UI_STATUS: Mutex<UiStatus> = Mutex::new(GENERATED_INITIAL_STATUS);
fn receive_service_status(mut x: i32, _c: bool) -> bool {
    let key_confirmed;
    let mouse_time = 0;
    let video_conn_count = 0;
GENERATED_ONLINE_UPDATE
    key_confirmed
}
#[test]
fn flutter_native_status_exposes_actual_service_key_confirmation() {
    assert!(!UI_STATUS.lock().unwrap().key_confirmed);
    assert!(receive_service_status(5, true));
    let status = UI_STATUS.lock().unwrap();
    assert_eq!(status.status_num, 1);
    assert!(status.key_confirmed);
    drop(status);
    assert!(!receive_service_status(5, false));
    assert!(!UI_STATUS.lock().unwrap().key_confirmed);
}
#[test]
fn actual_setter_success_renews_incoming_guard_without_saved_preferences() {
    set_option("mixel-support-invite-attended".to_owned(), "Y".to_owned());
    TRACE.with(|trace| {
        assert_eq!(trace.borrow().effects, ["attended-guard"]);
        assert_eq!(trace.borrow().renewals, [("mixel-support-invite-attended".to_owned(), "Y".to_owned())]);
    });
    assert_eq!(get_option("mixel-support-invite-attended"), "attended-runtime-v2");
}
#[test]
fn actual_setter_failed_owner_still_renews_service_but_readiness_fails_closed() {
    TRACE.with(|trace| trace.borrow_mut().fail_owner_hold = true);
    set_option("mixel-support-invite-attended".to_owned(), "Y".to_owned());
    TRACE.with(|trace| {
        assert_eq!(trace.borrow().effects, ["attended-guard"]);
        assert_eq!(trace.borrow().renewals, [("mixel-support-invite-attended".to_owned(), "Y".to_owned())]);
    });
    // The service can already attest its memory guard. Local ownership failure
    // must still prevent the reporter announcing the customer as ready.
    assert_eq!(get_option("mixel-support-invite-attended"), "guard-unavailable");
}
#[test]
fn actual_setter_successful_retry_restores_readiness_after_owner_failure() {
    TRACE.with(|trace| trace.borrow_mut().fail_owner_hold = true);
    set_option("mixel-support-invite-attended".to_owned(), "Y".to_owned());
    assert_eq!(get_option("mixel-support-invite-attended"), "guard-unavailable");
    TRACE.with(|trace| trace.borrow_mut().fail_owner_hold = false);
    set_option("mixel-support-invite-attended".to_owned(), "Y".to_owned());
    assert_eq!(get_option("mixel-support-invite-attended"), "attended-runtime-v2");
    TRACE.with(|trace| assert_eq!(trace.borrow().renewals.len(), 2));
}
#[test]
fn actual_setter_nonrenew_values_cannot_revoke_or_persist_attended_guard() {
    for value in ["", "N", "attended-runtime-v2", "invalid"] {
        set_option("mixel-support-invite-attended".to_owned(), value.to_owned());
    }
    TRACE.with(|trace| {
        assert!(trace.borrow().effects.is_empty());
        assert!(trace.borrow().renewals.is_empty());
    });
}
#[test]
fn actual_setter_ipc_failure_keeps_readiness_unavailable_without_persistence() {
    TRACE.with(|trace| trace.borrow_mut().ipc_failed = true);
    set_option("mixel-support-invite-attended".to_owned(), "Y".to_owned());
    assert_eq!(get_option("mixel-support-invite-attended"), "guard-unavailable");
    TRACE.with(|trace| {
        assert_eq!(trace.borrow().effects, ["attended-guard"]);
        assert_eq!(trace.borrow().renewals.len(), 1);
    });
}
#[test]
fn unrelated_option_falls_through_to_existing_saved_preference_path() {
    set_option("approve-mode".to_owned(), "password".to_owned());
    assert_eq!(get_option("approve-mode"), "saved-fallback");
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["saved-preference-write"]));
}
fn linux_startup(args: Vec<String>) -> Option<Vec<String>> {
    let mut flutter_args = Vec::new();
    let no_server = false;
GENERATED_CLASSIFICATION
GENERATED_DISPATCH
GENERATED_INCOMING
    Some(flutter_args)
}
#[test]
fn mixel_update_entry_points_stop_before_upstream_network_or_download() {
    TRACE.with(|trace| trace.borrow_mut().custom = true);
    start_auto_update(); manually_check_update().unwrap(); check_update(true).unwrap();
    *SOFTWARE_UPDATE_URL.lock().unwrap() = "stale-stock-release".to_owned();
    run(do_check_software_update()).unwrap();
    assert!(SOFTWARE_UPDATE_URL.lock().unwrap().is_empty());
    TRACE.with(|trace| assert!(trace.borrow().effects.is_empty()));
}
#[test]
fn store_update_entry_points_preserve_platform_store_ownership() {
    TRACE.with(|trace| trace.borrow_mut().store = true);
    start_auto_update(); manually_check_update().unwrap(); check_update(true).unwrap();
    TRACE.with(|trace| assert!(trace.borrow().effects.is_empty()));
}
#[test]
fn unrelated_stock_update_entry_points_retain_their_existing_execution() {
    start_auto_update(); manually_check_update().unwrap(); check_update(true).unwrap();
    run(do_check_software_update()).unwrap();
    TRACE.with(|trace| assert_eq!(trace.borrow().effects,
        ["auto-update", "manual-update", "download", "manifest-request"]));
}
#[test]
fn linux_cold_support_link_arms_consent_starts_incoming_and_reaches_flutter() {
    let uri = "mixel-remote://support?invite=synthetic&apikey=synthetic".to_owned();
    assert_eq!(linux_startup(vec![uri.clone()]), Some(vec![uri]));
    TRACE.with(|trace| assert_eq!(trace.borrow().effects,
        ["attended-guard", "dbus", "incoming-server"]));
}
#[test]
fn linux_warm_support_link_dispatches_without_starting_duplicate_server() {
    TRACE.with(|trace| trace.borrow_mut().warm = true);
    assert_eq!(linux_startup(vec!["mixel-remote://support?invite=synthetic".to_owned()]), None);
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["attended-guard", "dbus"]));
}
#[test]
fn linux_uppercase_support_link_keeps_its_native_incoming_handoff() {
    let uri = "MIXEL-REMOTE://SUPPORT/?invite=synthetic".to_owned();
    assert_eq!(linux_startup(vec![uri.clone()]), Some(vec![uri]));
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["attended-guard", "dbus", "incoming-server"]));
}
#[test]
fn linux_uppercase_warm_support_link_dispatches_without_duplicate_startup() {
    TRACE.with(|trace| trace.borrow_mut().warm = true);
    assert_eq!(linux_startup(vec!["MIXEL-REMOTE://SUPPORT/?invite=synthetic".to_owned()]), None);
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["attended-guard", "dbus"]));
}
#[test]
fn linux_direct_cli_support_handoff_reaches_flutter_without_uri_dispatch() {
    let args = vec!["--support-invite", "synthetic-token", "--support-apikey", "synthetic-key"]
        .iter().map(|value| value.to_string()).collect::<Vec<_>>();
    assert_eq!(linux_startup(args.clone()), Some(args));
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["attended-guard", "incoming-server"]));
}
#[test]
fn linux_cold_normal_window_keeps_its_incoming_startup() {
    assert_eq!(linux_startup(Vec::new()), Some(Vec::new()));
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["incoming-server"]));
}
#[test]
fn unrelated_outgoing_linux_link_retains_dbus_dispatch_only() {
    assert_eq!(linux_startup(vec!["mixel-remote://123456".to_owned()]), Some(Vec::new()));
    TRACE.with(|trace| assert_eq!(trace.borrow().effects, ["dbus"]));
}
'''
    source = source.replace("GENERATED_GUARD_PATH", str(ROOT / "scripts/support-invite-guard.rs").replace("\\", "\\\\"))
    source = source.replace("GENERATED_UPDATER", updater_functions)
    source = source.replace("GENERATED_ATTENDED_OPTIONS", attended_options)
    source = source.replace("GENERATED_STATUS_STRUCT", status_struct)
    source = source.replace("GENERATED_INITIAL_STATUS", initial_status)
    source = source.replace("GENERATED_ONLINE_UPDATE", online_update)
    source = source.replace("GENERATED_CLASSIFICATION", classification)
    source = source.replace("GENERATED_DISPATCH", dispatch)
    source = source.replace("GENERATED_INCOMING", incoming)
    target = repo / "generated_native_paths.rs"
    target.write_text(source, encoding="utf-8")
    binary = repo / ("generated_native_paths.exe" if os.name == "nt" else "generated_native_paths")
    subprocess.run([rustc, "--edition=2021", "--cfg", 'feature="flutter"', "--test", str(target), "-o", str(binary)], check=True)
    subprocess.run([str(binary), "--test-threads=1"], check=True)


def dart_runner(repo: Path, dart: str) -> None:
    directory = repo / "desktop_http_tests"
    directory.mkdir()
    source = (repo / "flutter/lib/utils/http_service.dart").read_text(encoding="utf-8")
    # Keep the complete generated service; replace only its Flutter/FFI/HTTP
    # imports with deterministic low-level adapters, without fetching packages.
    lines = []
    for line in source.splitlines():
        if line.startswith("import ") and line != "import 'dart:convert';":
            continue
        if line.startswith("export "):
            continue
        lines.append(line)
    (directory / "service.dart").write_text(
        "import 'fake_http.dart' as http;\nimport 'stubs.dart';\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    (directory / "fake_http.dart").write_text(r'''
class Response {
  Response(this.body, this.statusCode, {this.headers = const {}});
  final String body;
  final int statusCode;
  final Map<String, String> headers;
}
int flutterRequests = 0;
Future<Response> get(Uri uri, {Map<String, String>? headers}) async {
  flutterRequests++; return Response('flutter-response', 200);
}
Future<Response> post(Uri uri, {Map<String, String>? headers, dynamic body}) => get(uri, headers: headers);
Future<Response> put(Uri uri, {Map<String, String>? headers, dynamic body}) => get(uri, headers: headers);
Future<Response> delete(Uri uri, {Map<String, String>? headers, dynamic body}) => get(uri, headers: headers);
''', encoding="utf-8")
    (directory / "stubs.dart").write_text(r'''
bool isWeb = false;
bool kIsWeb = false;
const kOptionEnableFlutterHttpOnRust = 'enable-flutter-http-on-rust';
bool nativeOption = false;
int optionChecks = 0;
bool mainGetLocalBoolOptionSync(String key) { optionChecks++; return nativeOption; }
final bind = FakeBind();
class FakeBind {
  bool proxy = false;
  int proxyChecks = 0;
  int nativeRequests = 0;
  String lastMethod = '';
  String lastBody = '';
  String lastHeader = '';
  String response = '{"body":"native-response","headers":{},"status_code":200}';
  Future<bool> mainGetProxyStatus() async { proxyChecks++; return proxy; }
  Future<void> mainHttpRequest({required String url, required String method,
      dynamic body, required String header}) async {
    nativeRequests++; lastMethod = method; lastBody = body as String? ?? ''; lastHeader = header;
  }
  Future<String?> mainGetHttpStatus({required String url}) async => response;
}
''', encoding="utf-8")
    (directory / "runner.dart").write_text(r'''
import 'dart:async';
import 'dart:convert';
import 'service.dart';
import 'stubs.dart';
import 'fake_http.dart' as http;
void expect(bool value, String message) { if (!value) throw StateError(message); }
void reset() {
  isWeb = false; kIsWeb = false; nativeOption = false; optionChecks = 0;
  bind.proxy = false; bind.proxyChecks = 0; bind.nativeRequests = 0; http.flutterRequests = 0;
  bind.response = '{"body":"native-response","headers":{},"status_code":200}';
}
Future<void> main() async {
  var passed = 0;
  Future<void> test(String name, Future<void> Function() body) async {
    reset(); await body(); passed++; print('PASS: $name');
  }
  final support = Uri.parse('https://rs.mixel.ch/api/presence/client?request=synthetic');
  await test('desktop support without a proxy always uses bounded native HTTP', () async {
    final response = await HttpService().sendRequest(support, HttpMethod.post,
      headers: {'apikey': 'synthetic-public-key'}, body: 'synthetic-body');
    expect(response.body == 'native-response' && response.statusCode == 200, 'actual native response parsed');
    expect(bind.nativeRequests == 1 && http.flutterRequests == 0, 'support must use native transport');
    expect(optionChecks == 0 && bind.proxyChecks == 0, 'no optional proxy-state race for support');
    expect(bind.lastMethod == 'post' && bind.lastBody == 'synthetic-body' &&
      jsonDecode(bind.lastHeader)['apikey'] == 'synthetic-public-key', 'body/method/headers reach native transport');
  });
  await test('desktop support retains native transport with a configured proxy', () async {
    bind.proxy = true;
    await HttpService().sendRequest(support, HttpMethod.post);
    expect(bind.nativeRequests == 1 && http.flutterRequests == 0 && bind.proxyChecks == 0, 'native owns proxy selection');
  });
  await test('unrelated no-proxy requests retain Flutter HTTP', () async {
    await HttpService().sendRequest(Uri.parse('https://example.invalid/other'), HttpMethod.get);
    expect(bind.nativeRequests == 0 && http.flutterRequests == 1 && bind.proxyChecks == 1, 'generic routing preserved');
  });
  await test('unrelated proxy requests retain native HTTP', () async {
    bind.proxy = true;
    await HttpService().sendRequest(Uri.parse('https://example.invalid/other'), HttpMethod.get);
    expect(bind.nativeRequests == 1 && http.flutterRequests == 0, 'generic proxy routing preserved');
  });
  await test('similarly named hosts and paths are not support endpoints', () async {
    for (final url in [
      'https://rs.mixel.ch.attacker.invalid/api/presence/client?request=x',
      'https://rs.mixel.ch/api/presence/client/other?request=x',
      'http://rs.mixel.ch/api/presence/client?request=x',
      'https://rs.mixel.ch/api/presence/client?other=x',
    ]) { await HttpService().sendRequest(Uri.parse(url), HttpMethod.get); }
    expect(bind.nativeRequests == 0 && http.flutterRequests == 4, 'exact support endpoint classification');
  });
  await test('web support retains its supported Flutter transport', () async {
    isWeb = true;
    await HttpService().sendRequest(support, HttpMethod.post);
    expect(http.flutterRequests == 1 && bind.nativeRequests == 0, 'web cannot call desktop FFI');
  });
  await test('invalid support responses redact response data and parse errors', () async {
    final logs = <String>[];
    bind.response = 'synthetic-sensitive-bearer';
    Object? caught;
    await runZoned(() async {
      try { await HttpService().sendRequest(support, HttpMethod.post); }
      catch (error) { caught = error; }
    }, zoneSpecification: ZoneSpecification(print: (self, parent, zone, line) { logs.add(line); }));
    expect(caught != null && caught.toString() == 'Exception: Support request failed.', 'safe actionable exception');
    expect(logs.isEmpty, 'sensitive native response must never be printed');
  });
  print('Result: $passed generated desktop HTTP tests passed; 0 failed');
}
''', encoding="utf-8")
    common = (repo / "flutter/lib/common.dart").read_text(encoding="utf-8")
    start = common.index("    isOnline: () async {") + len("    isOnline: () async {\n")
    predicate = common[start:common.index("    },", start)]
    (directory / "readiness.dart").write_text(
        r'''import 'dart:convert';
bool isMacOS = false;
class Bind {
  String status = '';
  int statusReads = 0;
  bool screenPermission = false;
  bool trusted = false;
  Future<String> mainGetConnectStatus() async { statusReads++; return status; }
  bool mainIsCanScreenRecording({required bool prompt}) {
    if (prompt) throw StateError('Heartbeat must not prompt for permissions');
    return screenPermission;
  }
  bool mainIsProcessTrusted({required bool prompt}) {
    if (prompt) throw StateError('Heartbeat must not prompt for permissions');
    return trusted;
  }
}
final bind = Bind();
'''
        "Future<bool> actualReadiness() async {\n" + predicate + "}\n" + r'''
Future<void> main() async {
  var passed = 0;
  for (final sample in [
    ['{"status_num":1,"key_confirmed":true}', true],
    ['{"status_num":1,"key_confirmed":false}', false],
    ['{"status_num":1}', false],
    ['{"status_num":0,"key_confirmed":true}', false],
    ['{"status_num":-1,"key_confirmed":true}', false],
    ['{"status_num":"1","key_confirmed":true}', false],
    ['{"status_num":1,"key_confirmed":"true"}', false],
    ['[]', false],
  ]) {
    bind.status = sample[0] as String;
    if (await actualReadiness() != sample[1]) throw StateError('Invalid native service readiness classification');
    passed++;
  }
  isMacOS = true;
  for (final permissions in [
    [false, false, false], [false, true, false], [true, false, false], [true, true, true],
  ]) {
    bind.status = '{"status_num":1,"key_confirmed":true}';
    bind.screenPermission = permissions[0]; bind.trusted = permissions[1]; bind.statusReads = 0;
    if (await actualReadiness() != permissions[2]) throw StateError('Mac permissions must precede readiness');
    if (!permissions[2] && bind.statusReads != 0) throw StateError('Missing Mac permissions must block before service reads');
    passed++;
  }
  for (final status in ['{"status_num":0,"key_confirmed":true}', '{"status_num":1,"key_confirmed":false}']) {
    bind.status = status;
    if (await actualReadiness()) throw StateError('Mac permission grants cannot replace relay/key readiness');
    passed++;
  }
  print('Result: $passed actual generated service key/Mac permission readiness cases passed; 0 failed');
}
''', encoding="utf-8",
    )
    subprocess.run([dart, "analyze", str(directory)], check=True)
    subprocess.run([dart, str(directory / "runner.dart")], check=True)
    subprocess.run([dart, str(directory / "readiness.dart")], check=True)


def main() -> None:
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    dart = find_dart()
    if not rustc or not dart:
        raise SystemExit("Rust and Dart required: set RUSTC_BIN/DART_BIN or put both on PATH")
    with tempfile.TemporaryDirectory(prefix="mixel-generated-support-") as temporary:
        repo = Path(temporary)
        for path in TARGETS:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(pinned_source(path), encoding="utf-8")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/patch-support-invite.py")],
            env={**os.environ, "RDREPO": str(repo)}, check=True, capture_output=True,
        )
        native_runner(repo, rustc)
        dart_runner(repo, dart)
    print("Result: actual generated update, Linux startup and desktop HTTP paths passed")


if __name__ == "__main__":
    main()
