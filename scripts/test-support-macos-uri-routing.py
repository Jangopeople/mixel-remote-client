#!/usr/bin/env python3
"""Exercise generated two-token URI/report routing without OS activation or HTTP."""
import ast
import hashlib
import io
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

from sdk_discovery import find_dart


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
PLUGIN_SHA = "692de81efc32ef72df56d428902afb5216d5f9e43d71c7b315d360acd7a1e115"
ENGINE = "a18df97ca57a249df5d8d68cd0820600223ce262"  # Flutter 3.24.5.
ENGINE_HEADER_SHA = "a2af9087215714bda2d9720a1fa765db8c1caf44f79f6da9b36fa8567190ace7"
PLUGIN_FILES = {
    "LICENSE", "pubspec.yaml", "macos/uni_links_desktop.podspec",
    "macos/Classes/UniLinksDesktopPlugin.swift", "lib/uni_links_desktop.dart",
    "lib/src/register_protocol.dart", "lib/src/protocol_registrar.dart",
    "lib/src/protocol_registrar_impl_windows_noop.dart",
    "lib/src/protocol_registrar_impl_windows.dart",
    "lib/src/protocol_registrar_impl_macos.dart", "windows/CMakeLists.txt",
    "windows/uni_links_desktop_plugin.cpp",
    "windows/include/uni_links_desktop/uni_links_desktop_plugin.h",
}


def original(path):
    repo = UPSTREAM
    if path.startswith("libs/hbb_common/"):
        repo /= "libs/hbb_common"
        path = path.removeprefix("libs/hbb_common/")
    return subprocess.check_output(["git", "-C", str(repo), "show", "HEAD:" + path]).decode()


def between(text, start, end):
    assert text.count(start) == 1, start
    return text[text.index(start):text.index(end, text.index(start))]


def dart_fixture(directory, dart):
    # Use the same pinned pristine files as the existing full patch regression;
    # execute the actual patch in a disposable tree, never the working checkout.
    tree = ast.parse((ROOT / "scripts/test-support-invite.py").read_text(encoding="utf-8"))
    targets = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "targets" for t in node.targets))
    for path in targets:
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original(path), encoding="utf-8", newline="\n")
    patched = subprocess.run([sys.executable, str(ROOT / "scripts/patch-support-invite.py")],
                             env={**os.environ, "RDREPO": str(directory)},
                             capture_output=True, text=True, encoding="utf-8")
    if patched.returncode:
        raise RuntimeError(patched.stdout + patched.stderr)
    common = (directory / "flutter/lib/common.dart").read_text(encoding="utf-8")
    helper = between(common, "// Mixel support invite handoff:", "// uri link handler\n")
    handler = between(common, "bool handleUriLink(", "  UriLinkType? type;") + "  return false;\n}\n"
    listener = between(common, "StreamSubscription? listenUniLinks(", "\nenum UriLinkType")
    initial = between(common, "Future<bool> initUniLinks()", "/// Listen for uni links.")
    parser = between(common, "  if (uri.scheme.toLowerCase() == 'mixel-remote' && uri.host.toLowerCase() == 'support') {", "  } else if (uri.authority.isEmpty &&")
    parser = "List<String>? urlLinkToCmdArgs(Uri uri) {\n" + parser + "  }\n  return null;\n}\n"
    assert "linkStream.listen((String? rawLink)" in listener
    assert "armAttended: _renewSupportInviteAttended" in helper
    assert "'attendedReady': true" in helper
    test = directory / "flutter/lib/routing_fixture.dart"
    (test.parent / "fixture_http.dart").write_text(r'''
import 'dart:async';
enum HttpMethod { post }
class Response { final int statusCode; Response(this.statusCode); }
final requests = <({Uri url, Map<String, String> headers, String body})>[];
class HttpService {
  Future<Response> sendRequest(Uri url, HttpMethod method,
      {required Map<String, String> headers, required String body}) async {
    if (url.scheme != 'https' || url.host != 'rs.mixel.ch' ||
        url.path != '/api/presence/client' || method != HttpMethod.post) {
      throw StateError('Unexpected generated request route');
    }
    // This is an in-memory collector. It creates no socket or HTTP client.
    requests.add((url: url, headers: Map.of(headers), body: body));
    // Cold report enters the actual four-second failure retry. A warm intent
    // must cancel it; waiting past that retry below catches a stale reporter.
    return Response(requests.length == 1 ? 503 : 200);
  }
}
''', encoding="utf-8", newline="\n")
    test.write_text(r'''
import 'dart:async';
import 'dart:convert';
import 'mixel_support_invite.dart';
import 'fixture_http.dart' as http;
const isLinux = false, isWeb = false, isWindows = false, isMacOS = true;
String webInitialLink = '';
String? initialLink;
Future<String?> getInitialLink() async => initialLink;
void registerProtocol(String protocol) { throw StateError('Unexpected Windows registration'); }
void debugPrint(String message) {}
class FakeState { Object? overlay = Object(); }
class FakeGlobalKey { FakeState? currentState = FakeState(); }
final globalKey = FakeGlobalKey();
String translate(String key) => key;
void showToast(String message, {required Duration timeout}) {}
var shown = 0;
void windowOnTop(int? id) { shown++; }
final nativeLinks = StreamController<String?>();
Stream<String?> get linkStream => nativeLinks.stream;
class FakeBind {
  bool available = true;
  int arms = 0;
  String mainUriPrefixSync() => 'mixel-remote://';
  Future<void> mainSetOption({required String key, required String value}) async {
    if (key != 'mixel-support-invite-attended' || value != 'Y') throw StateError('Unexpected option');
    arms++;
  }
  Future<String> mainGetOption({required String key}) async => available ? 'attended-runtime-v2' : 'guard-unavailable';
  Future<String> mainGetMyId() async => 'synthetic-peer-123';
  Future<String> mainGetConnectStatus() async => '{"status_num":1,"key_confirmed":true}';
  bool mainIsCanScreenRecording({required bool prompt}) { if (prompt) throw StateError('TCC prompt'); return true; }
  bool mainIsProcessTrusted({required bool prompt}) { if (prompt) throw StateError('TCC prompt'); return true; }
  void sendUrlScheme({required String url}) { throw StateError('Unexpected native forwarding'); }
}
final bind = FakeBind();
''' + initial + listener + helper + handler + parser + r'''
const coldToken = 'inv_00000000-0000-0000-0000-000000000001';
const warmToken = 'inv_00000000-0000-0000-0000-000000000002';
const coldKey = 'synthetic-public-key-first-000000000000';
const warmKey = 'synthetic-public-key-second-000000000000';
String invite(String token, String key) => Uri(scheme: 'mixel-remote', host: 'support',
    queryParameters: {'invite': token, 'apikey': key}).toString();
Future<void> waitForRequest(String token) async {
  final deadline = DateTime.now().add(const Duration(seconds: 8));
  while (!http.requests.any((request) => jsonDecode(request.body)['token'] == token)) {
    if (DateTime.now().isAfter(deadline)) throw StateError('No request for expected synthetic token');
    await Future<void>.delayed(const Duration(milliseconds: 20));
  }
}
void checkRequest(String token, String key) {
  final matches = http.requests.where((request) => jsonDecode(request.body)['token'] == token).toList();
  if (matches.length != 1) throw StateError('Wrong request count for intent');
  final request = matches.single;
  final body = jsonDecode(request.body) as Map<String, dynamic>;
  if (body.length != 3 || body['token'] != token || body['rustdeskId'] != 'synthetic-peer-123' ||
      body['attendedReady'] != true || request.headers['apikey'] != key ||
      request.headers['Content-Type'] != 'application/json' ||
      !RegExp(r'^[0-9a-f]{32}$').hasMatch(request.url.queryParameters['request'] ?? '')) {
    throw StateError('Generated intent/report correlation differs');
  }
}
Future<void> main() async {
  final base = invite(coldToken, coldKey);
  final parserCases = <String, bool>{
    '$base&apikey=too-short': false,
    '$base&invite=invalid': false,
    '$base&APIKEY=too-short&apikey=$warmKey': false,
    '$base&APIKEY=$warmKey&apikey=too-short': true,
    base.replaceFirst(coldKey, 'too-short') + '&apikey=$warmKey': true,
    base.replaceFirst(coldKey, 'synthetic+bad-key-000000000000'): false,
    base.replaceFirst(coldKey, 'synthetic%2Bvalid-key-000000000000'): true,
  };
  for (final entry in parserCases.entries) {
    if ((urlLinkToCmdArgs(Uri.parse(entry.key)) != null) != entry.value) {
      throw StateError('Generated Dart query decoding/duplicate semantics differ');
    }
  }
  final subscription = listenUniLinks()!;
  try {
    initialLink = invite(coldToken, coldKey);
    if (await initUniLinks()) throw StateError('Cold support URI became outbound intent');
    await waitForRequest(coldToken);
    checkRequest(coldToken, coldKey);
    final coldNonce = http.requests.single.url.queryParameters['request'];
    final before = shown;
    nativeLinks.add(invite(warmToken, warmKey));
    await waitForRequest(warmToken);
    checkRequest(warmToken, warmKey);
    if (shown != before + 1 || http.requests.length != 2 ||
        http.requests.last.url.queryParameters['request'] == coldNonce) {
      throw StateError('Warm raw payload did not produce a distinct matching report');
    }
    await Future<void>.delayed(const Duration(milliseconds: 2300));
    if (http.requests.length != 2) throw StateError('Superseded cold reporter retried after its deadline');
    _supportInviteReporter?.stop();
    await Future<void>.delayed(const Duration(milliseconds: 50));
    bind.available = false;
    final armsBeforeUnavailable = bind.arms;
    final shownBeforeUnavailable = shown;
    nativeLinks.add(invite('inv_00000000-0000-0000-0000-000000000003', warmKey));
    await Future<void>.delayed(const Duration(milliseconds: 50));
    if (http.requests.length != 2 || bind.arms <= armsBeforeUnavailable || shown != shownBeforeUnavailable + 1) {
      throw StateError('Unavailable guard intent was lost or emitted a ready report');
    }
    _supportInviteReporter?.stop();
    print('PASS exact generated cold and raw warm URI paths report two distinct matching synthetic tokens/API keys/device IDs/nonces');
    print('PASS actual reporter supersedes old intent and blocks readiness when guard is unavailable; no HTTP socket, OS activation or TCC prompt');
  } finally {
    _supportInviteReporter?.stop();
    _supportInviteAttendedTimer?.cancel();
    await subscription.cancel();
    await nativeLinks.close();
  }
}
''', encoding="utf-8", newline="\n")
    subprocess.run([dart, "analyze", str(test), str(test.parent / "fixture_http.dart"),
                    str(test.parent / "mixel_support_invite.dart")], check=True)
    subprocess.run([dart, str(test)], check=True, timeout=25)


def pinned_vendor_fixture(generated):
    vendor = ROOT / "scripts/vendor/uni_links_desktop"
    local = generated / "flutter/local_plugins/uni_links_desktop"
    assert {path.relative_to(vendor).as_posix() for path in vendor.rglob("*") if path.is_file()} == PLUGIN_FILES
    assert {path.relative_to(local).as_posix() for path in local.rglob("*") if path.is_file()} == PLUGIN_FILES
    windows_root = PureWindowsPath("C:/fixture/vendor")
    windows_paths = {windows_root / path for path in PLUGIN_FILES}
    assert {str(path.relative_to(windows_root)) for path in windows_paths} != PLUGIN_FILES
    assert {path.relative_to(windows_root).as_posix() for path in windows_paths} == PLUGIN_FILES
    archive = urlopen("https://pub.dev/api/archives/uni_links_desktop-0.1.7.tar.gz", timeout=20).read()
    assert hashlib.sha256(archive).hexdigest() == PLUGIN_SHA
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for path in sorted(PLUGIN_FILES):
            assert (local / path).read_bytes() == (vendor / path).read_bytes(), path
            if path != "macos/Classes/UniLinksDesktopPlugin.swift":
                assert (vendor / path).read_bytes() == tar.extractfile(path).read(), path
        plugin = tar.extractfile("macos/Classes/UniLinksDesktopPlugin.swift").read().decode()
    assert "  uni_links_desktop:\n    path: local_plugins/uni_links_desktop\n" in (generated / "flutter/pubspec.yaml").read_text(encoding="utf-8")
    print("PASS exact pinned archive identity, all 12 unmodified dependency/license/platform files and generated repo-local override; Mac correction is isolated")
    return plugin


def vendor_checkout_fixture(directory):
    # Actual Git checkout control: Windows autocrlf must not rewrite pinned
    # dependency/license bytes. This does not change the caller's Git config.
    root = directory / "vendor-git-checkout"
    root.mkdir()
    def git(*arguments):
        subprocess.run(["git", "-C", str(root), *arguments], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    git("init", "--quiet")
    git("config", "core.autocrlf", "true")
    relative = Path("scripts/vendor/uni_links_desktop")
    vendor = ROOT / relative
    target = root / relative
    target.mkdir(parents=True)
    license = target / "LICENSE"
    original = (vendor / "LICENSE").read_bytes()
    license.write_bytes(original)
    git("add", ".")
    license.unlink()
    git("checkout-index", "--all", "--force")
    assert license.read_bytes() != original and b"\r\n" in license.read_bytes()
    (root / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    for path in sorted(PLUGIN_FILES):
        file = target / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes((vendor / path).read_bytes())
    git("add", ".")
    for path in PLUGIN_FILES:
        (target / path).unlink()
    git("checkout-index", "--all", "--force")
    for path in PLUGIN_FILES:
        assert (target / path).read_bytes() == (vendor / path).read_bytes(), path
    print("PASS actual Git autocrlf negative control and exact 13-file vendor/license checkout bytes preserved by repository attributes")


def swift_fixture(directory, plugin):
    # Execute only a synthetic plugin/engine fixture. No NSApplication,
    # NSWorkspace, app bundle, real URL launch or application termination.
    assert plugin.count("import FlutterMacOS") == 1
    header_url = f"https://raw.githubusercontent.com/flutter/engine/{ENGINE}/shell/platform/darwin/macos/framework/Headers/FlutterAppLifecycleDelegate.h"
    header = urlopen(header_url, timeout=20).read()
    assert hashlib.sha256(header).hexdigest() == ENGINE_HEADER_SHA
    assert b"- (BOOL)handleOpenURLs:(NSArray<NSURL*>*)urls;" in header
    (directory / "FlutterAppLifecycleDelegate.h").write_bytes(header)
    (directory / "FlutterMacros.h").write_text("#define FLUTTER_DARWIN_EXPORT\n", encoding="utf-8", newline="\n")
    swift = directory / "main.swift"
    swift.write_text(r'''
import Cocoa
public typealias FlutterEventSink = (Any?) -> Void
public typealias FlutterResult = (Any?) -> Void
let FlutterMethodNotImplemented = "not-implemented"
public protocol FlutterPlugin {}
public protocol FlutterStreamHandler {}
public class FlutterError: NSObject {}
public class FlutterMethodCall { let method: String; init(_ method: String) { self.method = method } }
var lifecycleDelegates = [NSObject & FlutterAppLifecycleDelegate]()
public class FlutterPluginRegistrar {
  let messenger = NSObject()
  var retained = [NSObject]()
  func addMethodCallDelegate(_ delegate: NSObject, channel: FlutterMethodChannel) { retained.append(delegate) }
  func addApplicationDelegate(_ delegate: NSObject & FlutterAppLifecycleDelegate) { lifecycleDelegates.append(delegate) }
}
class FlutterMethodChannel { init(name: String, binaryMessenger: NSObject) {} }
class FlutterEventChannel {
  init(name: String, binaryMessenger: NSObject) {}
  func setStreamHandler(_ handler: FlutterStreamHandler) {}
}
''' + plugin.replace("import FlutterMacOS", "") + r'''
let primary = FlutterPluginRegistrar()
UniLinksDesktopPlugin.register(with: primary)
let main = UniLinksDesktopPlugin.instance
var primaryMessages = [String]()
_ = main.onListen(withArguments: nil, eventSink: { primaryMessages.append($0 as! String) })
let secondary = FlutterPluginRegistrar()
UniLinksDesktopPlugin.register(with: secondary)
let other = UniLinksDesktopPlugin.instance
var secondaryMessages = [String]()
_ = other.onListen(withArguments: nil, eventSink: { secondaryMessages.append($0 as! String) })
precondition(main !== other && lifecycleDelegates.count == 2)
precondition(main.responds(to: NSSelectorFromString("handleOpenURLs:")))
// Exact pinned FlutterAppDelegate semantics: registration order, first YES ends delivery.
func dispatch(_ urls: [URL]) {
  for delegate in lifecycleDelegates {
    if delegate.responds(to: NSSelectorFromString("handleOpenURLs:")), delegate.handleOpen?(urls) == true { return }
  }
}
let cold = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-first-key-000000000000"
let warm = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000002&apikey=synthetic-second-key-000000000000"
dispatch([URL(string: cold)!])
dispatch([URL(string: warm)!])
precondition(primaryMessages == [cold, warm] && secondaryMessages.isEmpty)
var initial: String?
main.handle(FlutterMethodCall("getInitialLink"), result: { initial = $0 as? String })
precondition(initial == cold)
print("PASS pinned Swift plugin matches actual Flutter selector and delivers distinct URLs to first registered primary engine despite secondary singleton registration")
// Baseline negative control: the exact plugin claims a new warm support URL
// with no listener, but neither onListen nor getInitialLink recovers it.
_ = main.onCancel(withArguments: nil)
let gap = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000003&apikey=synthetic-gap-key-000000000000"
dispatch([URL(string: gap)!])
precondition(primaryMessages == [cold, warm] && secondaryMessages.isEmpty)
_ = main.onListen(withArguments: nil, eventSink: { primaryMessages.append($0 as! String) })
main.handle(FlutterMethodCall("getInitialLink"), result: { initial = $0 as? String })
precondition(initial == cold && primaryMessages == [cold, warm])
print("PASS negative control reproduces dropped second warm support URI during a real canceled-listener gap in the exact pinned Swift plugin")
''' , encoding="utf-8", newline="\n")
    output = directory / "swift-routing-fixture"
    subprocess.run(["xcrun", "swiftc", "-import-objc-header", str(directory / "FlutterAppLifecycleDelegate.h"),
                    str(swift), "-o", str(output)], check=True)
    subprocess.run([str(output)], check=True)
    # Compile the actual generated repo-local plugin correction separately.
    # The baseline above must continue reproducing the original defect.
    generated = directory / "dart/flutter/local_plugins/uni_links_desktop/macos/Classes/UniLinksDesktopPlugin.swift"
    patched = generated.read_text(encoding="utf-8")
    assert patched.count("import FlutterMacOS") == 1
    baseline = swift.read_text(encoding="utf-8")
    original_plugin = plugin.replace("import FlutterMacOS", "")
    assert baseline.count(original_plugin) == 1
    positive = baseline[:baseline.index("// Baseline negative control:")].replace(
        original_plugin, patched.replace("import FlutterMacOS", ""), 1)
    assert positive.count("precondition(initial == cold)") == 1
    positive = positive.replace("precondition(initial == cold)", "precondition(initial == \"\")", 1)
    positive = positive.replace(
        "PASS pinned Swift plugin matches actual Flutter selector and delivers distinct URLs to first registered primary engine despite secondary singleton registration",
        "PASS generated Mac plugin keeps exact Flutter selector and first-primary engine delivery despite secondary singleton registration")
    positive += r'''
// A true listener gap: two later support intents arrive after cancellation.
// Only the latest is retained, and clearing before emission prevents replay.
_ = main.onCancel(withArguments: nil)
let gap = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000003&apikey=synthetic-gap-key-000000000000"
let newest = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000004&apikey=synthetic-newest-key-000000000000"
dispatch([URL(string: gap)!, URL(string: newest)!, URL(string: "mixel-remote://123456789")!])
precondition(primaryMessages == [cold, warm] && secondaryMessages.isEmpty)
main.handle(FlutterMethodCall("getInitialLink"), result: { initial = $0 as? String })
precondition(initial == "", "Repeated initial getter must not return stale handled support URI")
_ = main.onListen(withArguments: nil, eventSink: { primaryMessages.append($0 as! String) })
precondition(primaryMessages == [cold, warm, newest] && secondaryMessages.isEmpty)
_ = main.onListen(withArguments: nil, eventSink: { primaryMessages.append($0 as! String) })
precondition(primaryMessages == [cold, warm, newest])
print("PASS generated Mac plugin delivers only the newest validated support intent exactly once after a canceled-listener gap")

// Cold startup already retrieves its first URI through getInitialLink. Avoid a
// duplicate queued cold delivery while preserving a distinct later warm URI.
let coldOnly = UniLinksDesktopPlugin()
precondition(coldOnly.handleOpen([URL(string: cold)!]))
var coldInitial: String?
coldOnly.handle(FlutterMethodCall("getInitialLink"), result: { coldInitial = $0 as? String })
var coldReplays = [String]()
_ = coldOnly.onListen(withArguments: nil, eventSink: { coldReplays.append($0 as! String) })
precondition(coldInitial == cold && coldReplays.isEmpty)
let coldThenWarm = UniLinksDesktopPlugin()
precondition(coldThenWarm.handleOpen([URL(string: cold)!, URL(string: warm)!]))
coldThenWarm.handle(FlutterMethodCall("getInitialLink"), result: { coldInitial = $0 as? String })
var warmReplays = [String]()
_ = coldThenWarm.onListen(withArguments: nil, eventSink: { warmReplays.append($0 as! String) })
precondition(coldInitial == warm && warmReplays.isEmpty)
// runApp subscribes before the asynchronous window-ready initUniLinks callback;
// the opposite startup ordering must not supersede a newer queued warm invite.
let listenerBeforeInitial = UniLinksDesktopPlugin()
precondition(listenerBeforeInitial.handleOpen([URL(string: cold)!, URL(string: warm)!]))
var earlyReplays = [String]()
_ = listenerBeforeInitial.onListen(withArguments: nil, eventSink: { earlyReplays.append($0 as! String) })
var laterInitial: String?
listenerBeforeInitial.handle(FlutterMethodCall("getInitialLink"), result: { laterInitial = $0 as? String })
precondition(earlyReplays == [warm] && (laterInitial == warm || laterInitial == ""), "Newer queued invite must not be superseded by stale cold initial getter")
print("PASS generated initial getter/listener orderings deliver newest support intent once, suppress stale cold replay and preserve distinct pending warm intent")

let ordinary = "mixel-remote://123456789"
let ordinaryInitial = UniLinksDesktopPlugin()
precondition(ordinaryInitial.handleOpen([URL(string: ordinary)!, URL(string: warm)!]))
_ = ordinaryInitial.onListen(withArguments: nil, eventSink: { _ in })
ordinaryInitial.handle(FlutterMethodCall("getInitialLink"), result: { laterInitial = $0 as? String })
precondition(laterInitial == ordinary, "Ordinary initial URI semantics changed")
let invalid = [ordinary,
  "mixel-remote://support/?invite=invalid&apikey=synthetic-first-key-000000000000",
  cold + "#fragment", cold.replacingOccurrences(of: "support/", with: "support:123/"),
  cold.replacingOccurrences(of: "support/", with: "user@support/"),
  cold.replacingOccurrences(of: "support/", with: "support/not-support"),
  cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: "too-short"),
  cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: "synthetic+bad-key-000000000000"),
  cold + "&apikey=too-short", cold + "&invite=invalid",
  cold + "&APIKEY=too-short&apikey=synthetic-second-key-000000000000",
  cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: String(repeating: "a", count: 4097)),
  cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: "synthetic%20bad-key-000000000000")]
for value in invalid {
  let instance = UniLinksDesktopPlugin()
  precondition(instance.handleOpen([URL(string: value)!]))
  var replayed = [String]()
  _ = instance.onListen(withArguments: nil, eventSink: { replayed.append($0 as! String) })
  precondition(replayed.isEmpty)
  // Live event semantics, including ordinary links, stay byte-identical.
  precondition(instance.handleOpen([URL(string: value)!]))
  precondition(replayed == [URL(string: value)!.absoluteString])
}
let maximum = cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: String(repeating: "%21", count: 4096))
let maxInstance = UniLinksDesktopPlugin()
precondition(maxInstance.handleOpen([URL(string: maximum)!]))
var maxReplayed = [String]()
_ = maxInstance.onListen(withArguments: nil, eventSink: { maxReplayed.append($0 as! String) })
precondition(maxReplayed == [URL(string: maximum)!.absoluteString])
let encodedPlus = cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: "synthetic%2Bvalid-key-000000000000")
let plusInstance = UniLinksDesktopPlugin()
precondition(plusInstance.handleOpen([URL(string: encodedPlus)!]))
var plusReplayed = [String]()
_ = plusInstance.onListen(withArguments: nil, eventSink: { plusReplayed.append($0 as! String) })
precondition(plusReplayed == [URL(string: encodedPlus)!.absoluteString])
for value in [cold.replacingOccurrences(of: "synthetic-first-key-000000000000", with: "too-short") + "&apikey=synthetic-second-key-000000000000",
              cold + "&APIKEY=synthetic-second-key-000000000000&apikey=too-short"] {
  let instance = UniLinksDesktopPlugin()
  precondition(instance.handleOpen([URL(string: value)!]))
  var replayed = [String]()
  _ = instance.onListen(withArguments: nil, eventSink: { replayed.append($0 as! String) })
  precondition(replayed == [URL(string: value)!.absoluteString])
}
print("PASS ordinary and malformed URLs keep original live behavior with no pending replay; maximum printable percent-encoded support key is accepted")
'''
    positive_dir = directory / "patched-swift"
    positive_dir.mkdir()
    positive_source = positive_dir / "main.swift"
    positive_source.write_text(positive, encoding="utf-8", newline="\n")
    positive_output = positive_dir / "swift-routing-fixture"
    subprocess.run(["xcrun", "swiftc", "-import-objc-header", str(directory / "FlutterAppLifecycleDelegate.h"),
                    str(positive_source), "-o", str(positive_output)], check=True)
    subprocess.run([str(positive_output)], check=True)


def main():
    dart = find_dart()
    if not dart:
        raise SystemExit("Dart SDK required; set DART_BIN")
    with tempfile.TemporaryDirectory(prefix="mixel-macos-uri-fixture-") as temp:
        directory = Path(temp)
        dart_fixture(directory / "dart", dart)
        plugin = pinned_vendor_fixture(directory / "dart")
        vendor_checkout_fixture(directory)
        if sys.platform == "darwin":
            swift_fixture(directory, plugin)
        else:
            print("SKIP native Swift selector fixture: macOS required; generated Dart routing checks passed")


if __name__ == "__main__":
    main()
