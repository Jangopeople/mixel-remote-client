#!/usr/bin/env python3
"""Patch pinned real source twice; execute its cold/warm support URI path."""
import os
import importlib.util
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
upstream = Path(os.environ.get("RDREPO", root / "rustdesk"))
dart = os.environ.get("DART_BIN") or shutil.which("dart")
if not dart:
    raise SystemExit("Dart SDK required: put dart on PATH or set DART_BIN")
targets = [
    "Cargo.toml", "libs/hbb_common/Cargo.toml", "libs/hbb_common/src/lib.rs",
    "flutter/lib/common.dart", "flutter/lib/main.dart", "src/ui_interface.rs",
    "src/ipc.rs", "src/ui_cm_interface.rs", "src/server/connection.rs", "src/core_main.rs", "src/common.rs", "src/updater.rs",
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
    assert "proof == 'attended-runtime-v1'" in common
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
    assert core.index("renew_support_invite_attended();") < core.index("crate::start_server(false, no_server)"), "arm attended mode before receiving connections"
    linux_dispatch = core.split("// linux uni (url) go here.", 1)[1].split("#[cfg(windows)]", 1)[0]
    assert "if _is_mixel_support_invite" in linux_dispatch
    assert "flutter_args.extend(args.iter().cloned());" in core
    assert "if try_send_by_dbus(args[0].clone()).is_none() { return None; }" in linux_dispatch
    assert "!support_invite_requires_click()" in first["libs/hbb_common/src/password_security.rs"]
    setter = first["src/ui_interface.rs"].split("pub fn set_option(key: String, value: String) {", 1)[1]
    runtime_only = setter.split('    if &key == "stop-service"', 1)[0]
    assert "Config::set_option" not in runtime_only and "OPTIONS.lock" not in runtime_only
    print("PASS: real pinned source patches idempotently, bearer logs redacted, customer-click guard precedes password/recent-session bypasses, preferences preserved")

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
    runner = repo / "flutter/lib/support_launch_test.dart"
    runner.write_text("import 'dart:async';\nimport 'mixel_support_invite.dart';\n" + """
class FakeBind { String mainUriPrefixSync() => 'mixel-remote://'; }
final bind = FakeBind();
var shown = 0;
var reported = 0;
Timer? _supportInviteAttendedTimer;
Future<bool> _renewSupportInviteAttended() async => true;
void windowOnTop(int? id) { shown++; }
Future<void> _reportSupportInvite(String token, String key) async { reported++; }
""" + handler + parser + """
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
  if (handleUriLink(cmdArgs: ['--mixel-attended'])) throw StateError('QuickSupport launch hid app');
  if (_supportInviteAttendedTimer == null) throw StateError('QuickSupport failed to maintain consent guard');
  _supportInviteAttendedTimer!.cancel();
  print('PASS: generated cold/warm support URI paths show the app, reject invalid/truncated arguments, never request outbound connection');
}
""", encoding="utf-8")
    subprocess.run([dart, str(runner)], check=True)

    # Execute the generated Rust one-shot cache accessor itself. This catches
    # URL correlation/consumption bugs that a reporter mock cannot expose.
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    if not rustc:
        raise SystemExit("Rust compiler required for native cache regression check")
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

print("Result: source patch and actual URI launch regression checks passed")
