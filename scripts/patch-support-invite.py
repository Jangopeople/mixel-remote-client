#!/usr/bin/env python3
"""Install the attended support handoff into fresh or already-patched 1.4.6 source."""
import os
import re
from pathlib import Path

scripts = Path(__file__).resolve().parent
rdrepo = Path(os.environ.get("RDREPO", scripts.parent / "rustdesk"))


def replace_once(text: str, before: str, after: str, description: str) -> str:
    if after in text:
        return text
    if before not in text:
        raise SystemExit(f"Could not find {description}")
    return text.replace(before, after, 1)


source = rdrepo / "flutter/lib/common.dart"
text = source.read_text(encoding="utf-8")
text = replace_once(
    text,
    "import 'package:uni_links/uni_links.dart';",
    "import 'package:uni_links/uni_links.dart';\nimport 'package:uni_links_desktop/uni_links_desktop.dart';",
    "uni_links import",
)
text = replace_once(
    text,
    "import 'dart:async';",
    "import 'dart:async';\nimport 'mixel_support_invite.dart';",
    "async import",
)
registration = """  if (isWindows) {
    registerProtocol('mixel-remote');
  }
"""
guard = """  if (isLinux) {
    return false;
  }
"""
if registration.strip() not in text:
    text = replace_once(text, guard, guard + registration, "initUniLinks guard")

helper = """// Mixel support invite handoff: presence only; customer accepts every connection.
MixelSupportInviteReporter? _supportInviteReporter;
Timer? _supportInviteAttendedTimer;
bool _supportInviteRenewing = false;
final _supportInviteCompatibilityNotice = MixelSupportCompatibilityNotice();

Future<bool> _renewSupportInviteAttended() async {
  if (_supportInviteRenewing) return false;
  _supportInviteRenewing = true;
  try {
    // A special runtime-only IPC command, never a saved preference.
    await bind.mainSetOption(key: 'mixel-support-invite-attended', value: 'Y')
      .timeout(const Duration(seconds: 3));
    final proof = await bind.mainGetOption(key: 'mixel-support-invite-attended')
      .timeout(const Duration(seconds: 3));
    _supportInviteCompatibilityNotice.showIfRequired(proof, () {
      if (globalKey.currentState?.overlay == null) return false;
      showToast(translate('mixel_support_component_update_required'),
        timeout: const Duration(seconds: 20));
      return true;
    });
    return proof == 'attended-runtime-v2';
  } catch (_) {
    return false;
  } finally {
    _supportInviteRenewing = false;
  }
}

Future<void> _reportSupportInvite(String token, String apiKey) async {
  if (!MixelSupportInviteReporter.validInvite(token, apiKey)) return;
  _supportInviteReporter?.stop();
  // Keep the customer's accept requirement for the lifetime of this main app,
  // even if the invite expires or the network becomes unavailable.
  _supportInviteAttendedTimer ??= Timer.periodic(
    const Duration(seconds: 20), (_) => _renewSupportInviteAttended());
  final reporter = MixelSupportInviteReporter(
    readId: () async => await bind.mainGetMyId(),
    isOnline: () async {
      if (isMacOS && (!bind.mainIsCanScreenRecording(prompt: false) ||
          !bind.mainIsProcessTrusted(prompt: false))) return false;
      final status = jsonDecode(await bind.mainGetConnectStatus());
      return status is Map && status['status_num'] is num &&
        status['status_num'] > 0 && status['key_confirmed'] == true;
    },
    armAttended: _renewSupportInviteAttended,
    report: (token, apiKey, id, nonce) async {
      final response = await http.HttpService().sendRequest(
        Uri.https('rs.mixel.ch', '/api/presence/client', {'request': nonce}),
        http.HttpMethod.post,
        headers: {'Content-Type': 'application/json', 'apikey': apiKey},
        body: jsonEncode({'token': token, 'rustdeskId': id, 'attendedReady': true}),
      );
      return response.statusCode;
    },
  );
  _supportInviteReporter = reporter;
  await reporter.run(token, apiKey);
  if (identical(_supportInviteReporter, reporter)) _supportInviteReporter = null;
}

"""
marker = "// uri link handler\n"
new_helper_marker = "// Mixel support invite handoff: presence only; customer accepts every connection.\n"
if new_helper_marker in text:
    start = text.index(new_helper_marker)
    end = text.index(marker, start)
    text = text[:start] + helper + text[end:]
elif "Future<void> _reportSupportInvite(" in text:
    start = text.index("Future<void> _reportSupportInvite(")
    end = text.index(marker, start)
    text = text[:start] + helper + text[end:]
else:
    text = replace_once(text, marker, helper + marker, "URI helper insertion")

text = text.replace('print("initialLink: $initialLink");', "debugPrint('Initial app link received.');")
text = text.replace('debugPrint("A uri was received: $uri. handleByFlutter $handleByFlutter");', "debugPrint('An app link was received.');")
# Malformed URI exceptions may contain their input; never print bearer URLs.
text = text.replace('debugPrintStack(label: "$err");\n    return false;\n  }\n}\n\n/// Listen for uni links.', "debugPrint('Initial app link could not be parsed.');\n    return false;\n  }\n}\n\n/// Listen for uni links.")
text = text.replace('print("uni links error: $err");', "debugPrint('App link could not be parsed.');")

empty_handler = """  if (args.isEmpty) {
    windowOnTop(null);
    return true;
  }
"""
support_handler = """  if ((args.contains('--mixel-attended') || args.contains('--quick_support')) &&
      !args.contains('--support-invite')) {
    // Native boot arguments and the exact raw Windows handoff both arm the
    // existing foreground process, which owns and renews its consent lease.
    windowOnTop(null);
    _supportInviteAttendedTimer ??= Timer.periodic(
      const Duration(seconds: 20), (_) => _renewSupportInviteAttended());
    Future.delayed(Duration.zero, _renewSupportInviteAttended);
    return false;
  }
  final supportInviteIndex = args.indexOf('--support-invite');
  final supportApiKeyIndex = args.indexOf('--support-apikey');
  if (supportInviteIndex >= 0 && supportApiKeyIndex >= 0 &&
      supportInviteIndex + 1 < args.length && supportApiKeyIndex + 1 < args.length) {
    final token = args[supportInviteIndex + 1];
    final apiKey = args[supportApiKeyIndex + 1];
    if (!MixelSupportInviteReporter.validInvite(token, apiKey)) return false;
    windowOnTop(null);
    Future.delayed(Duration.zero, () => _reportSupportInvite(token, apiKey));
    // true means outbound connection intent to main.dart and hides the app.
    // This handoff must display the app on both cold and warm launches.
    return false;
  }
"""
start_marker = "  final supportInviteIndex = args.indexOf('--support-invite');\n"
attended_marker = "  if ((args.contains('--mixel-attended') || args.contains('--quick_support')) &&\n"
old_attended_marker = "  if (args.contains('--mixel-attended') && !args.contains('--support-invite')) {\n"
if attended_marker in text:
    start_marker = attended_marker
elif old_attended_marker in text:
    start_marker = old_attended_marker
if start_marker in text:
    start = text.index(start_marker)
    end = text.index(empty_handler, start)
    text = text[:start] + support_handler + text[end:]
else:
    text = replace_once(text, empty_handler, support_handler + empty_handler, "URI argument handler")

parser_before = """  if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
"""
parser_after = """  if (uri.scheme.toLowerCase() == 'mixel-remote' && uri.host.toLowerCase() == 'support') {
    if (uri.userInfo.isNotEmpty || uri.hasPort || uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/')) return null;
    final params = uri.queryParameters.map((k, v) => MapEntry(k.toLowerCase(), v));
    final token = params['invite'] ?? '';
    final apiKey = params['apikey'] ?? '';
    if (!MixelSupportInviteReporter.validInvite(token, apiKey)) return null;
    return ['--support-invite', token, '--support-apikey', apiKey];
  } else if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
"""
old_parser = "  if (uri.scheme == 'mixel-remote' && uri.authority == 'support') {"
new_parser = "  if (uri.scheme.toLowerCase() == 'mixel-remote' && uri.host.toLowerCase() == 'support') {"
if old_parser in text or new_parser in text:
    start = text.index(old_parser if old_parser in text else new_parser)
    end = text.index("  } else if (uri.authority.isEmpty &&", start)
    text = text[:start] + parser_after.split("  } else if (uri.authority.isEmpty &&")[0] + text[end:]
else:
    text = replace_once(text, parser_before, parser_after, "support URI parser")
text = replace_once(text,
    "if (args[0].startsWith(bind.mainUriPrefixSync())) {",
    "if (args[0].toLowerCase().startsWith(bind.mainUriPrefixSync().toLowerCase())) {",
    "case-insensitive protocol command line")
text = replace_once(text,
    """  } else if (uriString != null) {
    final uri = Uri.tryParse(uriString);
    if (uri != null) {
      args = urlLinkToCmdArgs(uri);
    }
  }
""", """  } else if (uriString != null) {
    // The locked Windows plugin forwards argv[1] as a raw string. Recognize
    // this one internal intent before Uri.parse can canonicalize variants.
    if (uriString == '--quick_support') {
      args = ['--mixel-attended'];
    } else {
      final uri = Uri.tryParse(uriString);
      if (uri != null) {
        args = urlLinkToCmdArgs(uri);
      }
    }
  }
""", "exact raw QuickSupport attended intent")
text = replace_once(text,
    """  final sub = uriLinkStream.listen((Uri? uri) {
    debugPrint('An app link was received.');
    if (uri != null) {
      if (handleByFlutter) {
        handleUriLink(uri: uri);
      } else {
        bind.sendUrlScheme(url: uri.toString());
      }
    } else {
      print("uni listen error: uri is empty.");
    }
""", """  // Preserve the exact native payload before URI canonicalization. In
  // particular, percent-encoded or extended QuickSupport flags are rejected.
  final sub = linkStream.listen((String? rawLink) {
    debugPrint('An app link was received.');
    if (rawLink != null) {
      if (handleByFlutter) {
        handleUriLink(uriString: rawLink);
      } else {
        final uri = Uri.tryParse(rawLink);
        if (uri != null) bind.sendUrlScheme(url: uri.toString());
      }
    } else {
      print("uni listen error: uri is empty.");
    }
""", "raw Windows native handoff stream")
source.write_text(text, encoding="utf-8")
(rdrepo / "flutter/lib/mixel_support_invite.dart").write_text(
    (scripts / "support-invite-reporter.dart").read_text(encoding="utf-8"), encoding="utf-8"
)

main = rdrepo / "flutter/lib/main.dart"
main.write_text(main.read_text(encoding="utf-8").replace('debugPrint("launch args: $args");', "debugPrint('Main app launch received.');"), encoding="utf-8")

password = rdrepo / "libs/hbb_common/src/password_security.rs"
text = password.read_text(encoding="utf-8")
guard_code = (scripts / "support-invite-guard.rs").read_text(encoding="utf-8") + "\n"
guard_marker = "// Mixel attended support guard. Runtime only: never change saved login preferences.\n"
if guard_marker not in text:
    text = replace_once(text, "lazy_static::lazy_static! {", guard_code + "lazy_static::lazy_static! {", "password guard insertion")
else:
    start = text.index(guard_marker)
    end = text.index("lazy_static::lazy_static! {", start)
    text = text[:start] + guard_code + text[end:]
text = replace_once(text, "    approve_mode() == ApproveMode::Password\n", "    !support_invite_requires_click()\n        && approve_mode() == ApproveMode::Password\n", "connection-manager visibility guard")
password.write_text(text, encoding="utf-8")

ui = rdrepo / "src/ui_interface.rs"
text = ui.read_text(encoding="utf-8")
# Regenerate this complete owned branch when upgrading older branded source.
# Its closing brace has exactly four spaces; nested branches have eight.
for signature, branch in (
    ("pub fn get_option<T: AsRef<str>>(key: T) -> String {\n", '    if key.as_ref() == "mixel-support-invite-attended" {\n'),
    ("pub fn set_option(key: String, value: String) {\n", '    if key == "mixel-support-invite-attended" {\n'),
):
    start = text.index(signature) + len(signature)
    if text.startswith(branch, start):
        closing = re.search(r"(?m)^    }\n", text[start:])
        if closing is None:
            raise SystemExit("Could not find owned attended option branch")
        text = text[:start] + text[start + closing.end():]
text = replace_once(text, "pub fn get_option<T: AsRef<str>>(key: T) -> String {\n", """pub fn get_option<T: AsRef<str>>(key: T) -> String {
    if key.as_ref() == "mixel-support-invite-attended" {
        if hbb_common::password_security::support_invite_owner_lease_failed() {
            return "guard-unavailable".to_owned();
        }
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        {
            return match ipc::get_config("mixel-support-invite-attended") {
                Ok(value) => hbb_common::password_security::resolve_support_invite_attestation(
                    true, value.as_deref()).to_owned(),
                Err(_) => "guard-unavailable".to_owned(),
            };
        }
        #[cfg(any(target_os = "android", target_os = "ios"))]
        return if hbb_common::password_security::support_invite_requires_click() {
            hbb_common::password_security::SUPPORT_INVITE_ATTESTATION.to_owned()
        } else {
            "guard-unavailable".to_owned()
        };
    }
""", "runtime guard getter")
text = replace_once(text, """        let map = OPTIONS.lock().unwrap();
        if let Some(v) = map.get(key.as_ref()) {
            v.to_owned()
        } else {
            "".to_owned()
        }
""", """        let saved = {
            let map = OPTIONS.lock().unwrap();
            map.get(key.as_ref()).cloned().unwrap_or_default()
        };
        if key.as_ref() == "approve-mode" {
            hbb_common::password_security::effective_support_approve_mode(
                &saved, hbb_common::password_security::support_invite_guard_is_confirmed(&get_option("mixel-support-invite-attended")))
        } else {
            saved
        }
""", "effective attended click mode for customer Accept UI")
text = replace_once(text, "pub fn set_option(key: String, value: String) {\n", """pub fn set_option(key: String, value: String) {
    if key == "mixel-support-invite-attended" {
        if value == "Y" {
            // Only this foreground caller owns the process lease. The service
            // heartbeat handler must remain memory-only across IPC.
            // Keep the incoming memory guard renewed even if the kernel lease
            // cannot be acquired. The getter still blocks readiness on that
            // failure; never let it silently restore password-based approval.
            hbb_common::password_security::hold_support_invite_attended_lease();
            #[cfg(not(any(target_os = "android", target_os = "ios")))]
            ipc::set_config("mixel-support-invite-attended", value).ok();
        }
        return; // Runtime only. Never write OPTIONS or Config.
    }
""", "runtime guard setter")
ui.write_text(text, encoding="utf-8")

# Native proxy-aware HTTP results are keyed by URL. Give each support heartbeat
# an opaque unique URL, consume completed results, and expire abandoned ones.
text = ui.read_text(encoding="utf-8")
# Flutter must expose the actual service key registration result as well as
# socket reachability before an invite can announce the customer as ready.
text = re.sub(
    r'\n([ \t]*)#\[cfg\(not\(feature = "flutter"\)\)\]\n\1((?:pub |let mut )?key_confirmed\b)',
    r'\n\1\2', text,
)
text = text.replace("""                                #[cfg(not(feature = "flutter"))]
                                {
                                    key_confirmed = _c;
                                }
""", "                                key_confirmed = _c;\n")
text = replace_once(text, """pub fn get_async_http_status(url: String) -> Option<String> {
    match ASYNC_HTTP_STATUS.lock().unwrap().get(&url) {
""", """pub fn get_async_http_status(url: String) -> Option<String> {
    let mut requests = ASYNC_HTTP_STATUS.lock().unwrap();
    if url.starts_with("https://rs.mixel.ch/api/presence/client?request=") {
        if requests.get(&url).map(|value| value.as_str()) == Some(" ") {
            return Some(" ".to_owned());
        }
        return requests.remove(&url);
    }
    match requests.get(&url) {
""", "consume support request results")
text = replace_once(text, """        current_request.lock().unwrap().insert(url, res);
    });
}
""", """        current_request.lock().unwrap().insert(url.clone(), res);
        if url.starts_with("https://rs.mixel.ch/api/presence/client?request=") {
            // The UI may close or supersede before consuming the result.
            std::thread::sleep(std::time::Duration::from_secs(30));
            current_request.lock().unwrap().remove(&url);
        }
    });
}
""", "expire abandoned support HTTP results")
ui.write_text(text, encoding="utf-8")

common_rs = rdrepo / "src/common.rs"
text = common_rs.read_text(encoding="utf-8")
tls_before = "    let danger_accept_invalid_cert = get_cached_tls_accept_invalid_cert(tls_url);\n"
tls_after = """    let support_request = url.starts_with("https://rs.mixel.ch/api/presence/client?request=");
    let danger_accept_invalid_cert = if support_request {
        Some(false) // Never expose support bearer data through invalid-certificate fallback.
    } else {
        get_cached_tls_accept_invalid_cert(tls_url)
    };
"""
http_start = text.index("pub async fn http_request_sync(")
http_end = text.index("\n#[inline]", http_start)
# Older patch versions matched the same TLS line in post_request. Restore that
# unrelated transport; scope all support TLS/timeouts to the actual FFI handler.
prefix = text[:http_start].replace(tls_after, tls_before, 1)
http = text[http_start:http_end]
http = replace_once(http, tls_before, tls_after, "validated support HTTPS in native FFI transport")
if "    let request_future = async {" not in http:
    http = replace_once(http, """    let response = get_http_response_async(
        &url,
        tls_url,
        &method,
        body.clone(),
        &header,
        tls_type,
        danger_accept_invalid_cert,
        danger_accept_invalid_cert,
    )
    .await?;
""", """    let response_future = get_http_response_async(
        &url,
        tls_url,
        &method,
        body.clone(),
        &header,
        tls_type,
        danger_accept_invalid_cert,
        danger_accept_invalid_cert,
    );
    let response = if support_request {
        timeout(6_000, response_future).await??
    } else {
        response_future.await?
    };
""", "bounded proxy-aware support HTTP timeout")
    http = replace_once(http, "    let response_body = response.text().await?;\n", """    let response_body = if support_request {
        timeout(6_000, response.text()).await??
    } else {
        response.text().await?
    };
""", "bounded support response body timeout")
    # Header and body reads share one deadline. Separate timeouts used to allow a
    # native request to outlive the reporter and overlap the next heartbeat.
    http = http.replace("    let response_future = get_http_response_async(\n",
                        "    let request_future = async {\n    let response_future = get_http_response_async(\n", 1)
    http = http.replace("""    let response = if support_request {
        timeout(6_000, response_future).await??
    } else {
        response_future.await?
    };
""", "    let response = response_future.await?;\n", 1)
    http = http.replace("""    let response_body = if support_request {
        timeout(6_000, response.text()).await??
    } else {
        response.text().await?
    };
""", "    let response_body = response.text().await?;\n", 1)
    http = http.replace("""    serde_json::to_string(&result).map_err(|e| anyhow!("Failed to serialize response: {}", e))
}
""", """    serde_json::to_string(&result).map_err(|e| anyhow!("Failed to serialize response: {}", e))
    };
    if support_request {
        timeout(6_000, request_future).await?
    } else {
        request_future.await
    }
}
""", 1)
text = prefix + http + text[http_end:]
text = text.replace("ui_interface::{get_option, is_installed, set_option}", "ui_interface::{get_option, set_option}")
text = text.replace("    use std::net::ToSocketAddrs;\n", "")
text = text.replace("    use hbb_common::protobuf::Enum;\n", "")
common_rs.write_text(text, encoding="utf-8")

# All attended heartbeats use the bounded, proxy-aware native transport. The
# upstream no-proxy Flutter HTTP branch does not cancel timed-out sockets.
http_service = rdrepo / "flutter/lib/utils/http_service.dart"
text = http_service.read_text(encoding="utf-8")
text = replace_once(text, "    var useFlutterHttp = (isWeb || kIsWeb);\n", """    final supportRequest = url.scheme == 'https' && url.host == 'rs.mixel.ch' &&
        url.path == '/api/presence/client' && url.queryParameters.containsKey('request');
    var useFlutterHttp = (isWeb || kIsWeb);
""", "support native HTTP classification")
text = replace_once(text, "    if (!useFlutterHttp) {\n", "    if (!useFlutterHttp && !supportRequest) {\n", "support proxy transport routing")
text = replace_once(text, "    return _parseHttpResponse(resJson);\n", "    return _parseHttpResponse(resJson, sensitive: supportRequest);\n", "redacted support transport errors")
text = replace_once(text, "  http.Response _parseHttpResponse(String responseJson) {\n", "  http.Response _parseHttpResponse(String responseJson, {bool sensitive = false}) {\n", "sensitive response parser")
text = replace_once(text, "    } catch (e) {\n      print('Failed to parse response", """    } catch (e) {
      if (sensitive) throw Exception('Support request failed.');
      print('Failed to parse response""", "redact bearer response before logging")
text = text.replace("      default:\n        throw Exception('Unsupported HTTP method');\n", "")
http_service.write_text(text, encoding="utf-8")

settings = rdrepo / "flutter/lib/desktop/pages/desktop_setting_page.dart"
text = settings.read_text(encoding="utf-8")
text = text.replace("final showAutoUpdate = isWindows && bind.mainIsInstalled() && !Platform.resolvedExecutable.toLowerCase().contains('windowsapps');", "final showAutoUpdate = isWindows && bind.mainIsInstalled();")
text = replace_once(text, "    final showAutoUpdate = isWindows && bind.mainIsInstalled();\n", "    final showAutoUpdate = isWindows && !bind.isCustomClient() && bind.mainIsInstalled();\n", "Mixel settings external-update guard")
settings.write_text(text, encoding="utf-8")

updater = rdrepo / "src/updater.rs"
text = updater.read_text(encoding="utf-8")
store_guard = """fn is_mixel_store_package() -> bool {
    #[cfg(target_os = "windows")]
    return std::env::current_exe()
        .map(|path| hbb_common::password_security::is_mixel_store_package_path(&path.to_string_lossy()))
        .unwrap_or(true);
    #[cfg(not(target_os = "windows"))]
    return false;
}

"""
if store_guard not in text:
    text = replace_once(text, "enum UpdateMsg {\n", store_guard + "enum UpdateMsg {\n", "native Store package identity guard")
text = text.replace("if is_mixel_store_package() {", "if crate::is_custom_client() || is_mixel_store_package() {")
text = replace_once(text, "pub fn start_auto_update() {\n", "pub fn start_auto_update() {\n    if crate::is_custom_client() || is_mixel_store_package() { return; }\n", "Store auto updater start guard")
text = replace_once(text, "pub fn manually_check_update() -> ResultType<()> {\n", "pub fn manually_check_update() -> ResultType<()> {\n    if crate::is_custom_client() || is_mixel_store_package() { return Ok(()); }\n", "Store manual external updater guard")
text = replace_once(text, "fn check_update(manually: bool) -> ResultType<()> {\n", "fn check_update(manually: bool) -> ResultType<()> {\n    if crate::is_custom_client() || is_mixel_store_package() { return Ok(()); }\n", "Store external download/update execution guard")
text = text.replace("if is_mixel_store_package() {", "if crate::is_custom_client() || is_mixel_store_package() {")
updater.write_text(text, encoding="utf-8")

text = common_rs.read_text(encoding="utf-8")
text = replace_once(text, "pub async fn do_check_software_update() -> hbb_common::ResultType<()> {\n", """pub async fn do_check_software_update() -> hbb_common::ResultType<()> {
    if crate::is_custom_client() {
        // Mixel updates come from signed releases or the platform store. The
        // upstream manifest describes stock RustDesk binaries and relay defaults.
        *SOFTWARE_UPDATE_URL.lock().unwrap() = String::new();
        return Ok(());
    }
""", "preserve Mixel identity during upstream update checks")
common_rs.write_text(text, encoding="utf-8")

translations = {
    "en": "The installed Mixel Remote support component must be updated before this support link can connect.",
    "de": "Die installierte Mixel Remote Support-Komponente muss aktualisiert werden, bevor dieser Support-Link eine Verbindung herstellen kann.",
    "fr": "Le composant d’assistance Mixel Remote installé doit être mis à jour avant de pouvoir vous connecter avec ce lien.",
    "it": "Il componente di supporto Mixel Remote installato deve essere aggiornato prima di poter utilizzare questo link per connettersi.",
}
for locale, message in translations.items():
    target = rdrepo / f"src/lang/{locale}.rs"
    text = target.read_text(encoding="utf-8")
    entry = f'        ("mixel_support_component_update_required", "{message}"),\n'
    if entry not in text:
        text = replace_once(text, "    [\n", "    [\n" + entry, f"{locale} support component update notice")
    target.write_text(text, encoding="utf-8")

ipc = rdrepo / "src/ipc.rs"
text = ipc.read_text(encoding="utf-8")
text = text.replace("keys::{self, OPTION_ALLOW_WEBSOCKET}", "keys::OPTION_ALLOW_WEBSOCKET")
text = replace_once(text, """                } else if name == "trusted-devices" {
                    value = Some(Config::get_trusted_devices_json());
""", """                } else if name == "trusted-devices" {
                    value = Some(Config::get_trusted_devices_json());
                } else if name == "mixel-support-invite-attended" {
                    value = Some(if password::support_invite_requires_click() {
                        password::SUPPORT_INVITE_ATTESTATION.to_owned()
                    } else {
                        String::new()
                    });
""", "runtime IPC guard getter")
text = replace_once(text, """                } else if name == "unlock-pin" {
                    Config::set_unlock_pin(&value);
""", """                } else if name == "unlock-pin" {
                    Config::set_unlock_pin(&value);
                } else if name == "mixel-support-invite-attended" {
                    if value == "Y" {
                        password::renew_support_invite_attended();
                    }
""", "runtime IPC guard setter")
text = replace_once(text, """    c.send_config(name, value).await?;
    Ok(())
}
""", """    let attended_handoff = name == "mixel-support-invite-attended" && value == "Y";
    let request = async {
        c.send_config(name, value).await?;
        if attended_handoff {
            // The service handles frames serially on this same connection.
            // Its reply therefore follows the memory-only renewal, even while
            // the receiving Flutter isolate has not acquired its own lease yet.
            c.send(&Data::Config((name.to_owned(), None))).await?;
            match c.next_timeout(1_000).await? {
                Some(Data::Config((reply_name, Some(proof))))
                    if reply_name == name
                        && proof == password::SUPPORT_INVITE_ATTESTATION => {}
                _ => bail!("Attended handoff was not acknowledged"),
            }
        }
        Ok(())
    };
    if attended_handoff {
        timeout(1_500, request).await?
    } else {
        request.await
    }
}
""", "acknowledged runtime-only IPC attended handoff")
ipc.write_text(text, encoding="utf-8")

dbus = rdrepo / "src/server/dbus.rs"
text = dbus.read_text(encoding="utf-8")
text = replace_once(text, """            #[cfg(feature = "flutter")]
            {
                use crate::flutter;
                let data = HashMap::from([
""", """            #[cfg(feature = "flutter")]
            {
                // DBus acknowledges the queued event before Flutter processes
                // it. Own the foreground lease in this receiver first, so an
                // ordinary GUI cannot auto-authorize between sender exit and
                // the Dart attended callback. No saved setting is changed.
                if hbb_common::password_security::is_support_invite_arg(&_uni_links) {
                    if !hbb_common::password_security::hold_support_invite_attended_lease()
                        && crate::ipc::set_config("mixel-support-invite-attended", "Y".to_owned()).is_err()
                    {
                        // Keep the sender's cold attended fallback rather than
                        // claiming that an unprotected receiver accepted it.
                        return Ok(("attended-guard-unavailable".to_owned(),));
                    }
                }
                use crate::flutter;
                let data = HashMap::from([
""", "native Linux DBus receiver consent ownership before event enqueue")
text = replace_once(text, """                match crate::flutter::push_global_event(flutter::APP_TYPE_MAIN, event) {
                    None => log::error!("failed to find main event stream"),
                    Some(false) => {
                        log::error!("failed to add dbus message to flutter global dbus stream.")
                    }
                    Some(true) => {}
                }
""", """                let delivery = crate::flutter::push_global_event(flutter::APP_TYPE_MAIN, event);
                match delivery {
                    None => log::error!("failed to find main event stream"),
                    Some(false) => {
                        log::error!("failed to add dbus message to flutter global dbus stream.")
                    }
                    Some(true) => {}
                }
                if hbb_common::password_security::is_support_invite_arg(&_uni_links)
                    && delivery != Some(true)
                {
                    // A queued support intent must reach the existing GUI;
                    // otherwise retain the sender's cold attended fallback.
                    return Ok(("attended-handoff-unavailable".to_owned(),));
                }
""", "native Linux DBus support delivery acknowledgment")
dbus.write_text(text, encoding="utf-8")

cm = rdrepo / "src/ui_cm_interface.rs"
text = cm.read_text(encoding="utf-8").replace("config::{keys::*, option2bool}", "config::keys::*")
cm.write_text(text, encoding="utf-8")

connection = rdrepo / "src/server/connection.rs"
text = connection.read_text(encoding="utf-8")
text = replace_once(text, "    authorized: bool,\n", "    authorized: bool,\n    support_invite_attended: bool,\n    support_invite_accepted: bool,\n", "connection attended state")
text = replace_once(text, "            authorized: false,\n", "            authorized: false,\n            support_invite_attended: password::support_invite_requires_click(),\n            support_invite_accepted: false,\n", "connection attended state initialization")
text = replace_once(text, """                        ipc::Data::Authorize => {
                            conn.require_2fa.take();
""", """                        ipc::Data::Authorize => {
                            conn.support_invite_accepted = true;
                            conn.require_2fa.take();
""", "explicit customer Accept event")
text = replace_once(text, """    async fn handle_login_request_without_validation(&mut self, lr: &LoginRequest) {
        self.lr = lr.clone();
""", """    async fn handle_login_request_without_validation(&mut self, lr: &LoginRequest) {
        self.support_invite_attended |= password::support_invite_requires_click();
        self.lr = lr.clone();
""", "latch attended requirement for pending connection")
text = replace_once(text, """        if self.authorized {
            return true;
        }
        if self.require_2fa.is_some()""", """        if self.authorized {
            return true;
        }
        self.support_invite_attended |= password::support_invite_requires_click();
        if password::support_invite_must_wait(self.support_invite_attended, self.support_invite_accepted) {
            self.try_start_cm(self.lr.my_id.clone(), self.lr.my_name.clone(), false);
            self.send_login_error(crate::client::LOGIN_MSG_NO_PASSWORD_ACCESS).await;
            return true;
        }
        if self.require_2fa.is_some()""", "central authorization guard for password, 2FA and switch paths")
text = text.replace("            } else if password::support_invite_requires_click()\n", "            } else if password::support_invite_must_wait(self.support_invite_attended, self.support_invite_accepted)\n")
text = replace_once(text, """                _ = second_timer.tick() => {
                    #[cfg(windows)]
""", """                _ = second_timer.tick() => {
                    if password::support_invite_must_wait(conn.support_invite_attended, conn.support_invite_accepted) {
                        // Keep Accept visible for a latched pending request even
                        // if the main app's initial guard deadline passes.
                        password::renew_support_invite_attended();
                    }
                    #[cfg(windows)]
""", "pending customer Accept visibility retention")
text = replace_once(text, """            } else if (password::approve_mode() == ApproveMode::Click
""", """            } else if password::support_invite_must_wait(self.support_invite_attended, self.support_invite_accepted)
                || (password::approve_mode() == ApproveMode::Click
""", "attended guard before password/recent-session auto-authorization")
connection.write_text(text, encoding="utf-8")

# The native bootstrap normally starts the incoming server only on an empty
# command line. A support URI is an incoming help request, not an outgoing
# connection command, so the same server/portable startup must run for it.
core = rdrepo / "src/core_main.rs"
text = core.read_text(encoding="utf-8")
# Both native entrypoints must classify the executable name, never its parent
# directory. Published Windows support aliases are attended even when a
# browser adds its usual positive decimal copy number to the filename.
quick_support_before = """    let exe = exe.to_lowercase();
    exe.contains("-qs-") || exe.contains("-qs.exe") || exe.contains("_qs.exe")
"""
quick_support_after = """    let exe = exe.rsplit(['\\\\', '/']).next().unwrap_or_default().to_ascii_lowercase();
    let customer_name = exe.strip_suffix(".exe").unwrap_or_default();
    let customer_name = match customer_name.rsplit_once(" (") {
        Some((name, suffix)) if suffix.strip_suffix(')')
            .map(|number| !number.is_empty()
                && !number.starts_with('0')
                && number.bytes().all(|digit| digit.is_ascii_digit()))
            .unwrap_or(false) => name,
        _ => customer_name,
    };
    matches!(customer_name, "mixel-remote-support-windows" | "mixel-remote-support" | "mixel-remote-qs")
        || exe.contains("-qs-") || exe.contains("-qs.exe") || exe.contains("_qs.exe")
"""
text = replace_once(text, quick_support_before, quick_support_after,
                    "native QuickSupport executable basename and customer alias")
portable = rdrepo / "libs/portable/src/main.rs"
portable_text = portable.read_text(encoding="utf-8")
portable_text = replace_once(portable_text,
                            "\n".join("    " + line if line else line for line in quick_support_before.split("\n")),
                            "\n".join("    " + line if line else line for line in quick_support_after.split("\n")),
                            "portable QuickSupport executable basename and customer alias")
portable.write_text(portable_text, encoding="utf-8")
# Upgrade the two earlier foreground bootstrap arms to own the kernel lease.
text = text.replace(
    "hbb_common::password_security::renew_support_invite_attended();",
    "hbb_common::password_security::hold_support_invite_attended_lease();",
)
bootstrap_before = """        i += 1;
    }
    #[cfg(any(target_os = "linux", target_os = "windows"))]
"""
bootstrap_after = """        i += 1;
    }
    let _is_mixel_support_invite = args.first()
        .map(|arg| hbb_common::password_security::is_support_invite_arg(arg))
        .unwrap_or(false);
    if _is_mixel_support_invite {
        // Arm before the incoming server starts, not after asynchronous UI init.
        hbb_common::password_security::hold_support_invite_attended_lease();
    }
    #[cfg(any(target_os = "linux", target_os = "windows"))]
"""
text = replace_once(text, bootstrap_before, bootstrap_after, "native support URI classification")
text = replace_once(text, """        _is_quick_support |= !crate::platform::is_installed()
            && args.is_empty()
""", """        _is_quick_support |= !crate::platform::is_installed()
            && (args.is_empty() || _is_mixel_support_invite)
""", "support URI quicksupport classification")
text = replace_once(text, """    if !crate::platform::is_installed()
        && args.is_empty()
        && _is_quick_support
""", """    if !crate::platform::is_installed()
        && (args.is_empty() || _is_mixel_support_invite)
        && _is_quick_support
""", "support URI portable service startup")
text = replace_once(text, "    if args.is_empty() || crate::common::is_empty_uni_link(&args[0]) {\n", "    if args.is_empty() || _is_mixel_support_invite || crate::common::is_empty_uni_link(&args[0]) {\n", "support URI incoming rendezvous/server startup")
# Linux tries to dispatch to an existing window before normal server startup.
# On a cold launch, continue through startup and keep the original URI for Dart.
text = replace_once(text,
    "    if args.len() > 0 && args[0].starts_with(&crate::get_uri_prefix()) {\n",
    "    if args.len() > 0 && args[0].to_ascii_lowercase().starts_with(&crate::get_uri_prefix().to_ascii_lowercase()) {\n",
    "normalized Linux protocol activation")
text = replace_once(text, """        return try_send_by_dbus(args[0].clone());
""", """        if _is_mixel_support_invite {
            if try_send_by_dbus(args[0].clone()).is_none() { return None; }
        } else {
            return try_send_by_dbus(args[0].clone());
        }
""", "Linux cold incoming support startup")
text = replace_once(text,
    "        crate::portable_service::client::set_quick_support(_is_quick_support);\n",
    """        crate::portable_service::client::set_quick_support(_is_quick_support);
        if _is_quick_support {
            hbb_common::password_security::hold_support_invite_attended_lease();
            #[cfg(feature = "flutter")]
            flutter_args.push("--mixel-attended".to_owned());
        }
        #[cfg(feature = "flutter")]
        if _is_quick_support || _is_mixel_support_invite {
            // Renew the old incoming service while this invocation still owns
            // its foreground lease. Do not release it before the asynchronous
            // WM_COPYDATA callback can acquire the existing GUI's own lease.
            if crate::ipc::set_config("mixel-support-invite-attended", "Y".to_owned()).is_err() {
                flutter_args.push("--mixel-attended-handoff-unavailable".to_owned());
            }
        }
""", "QuickSupport double-click requires ongoing customer consent")
text = replace_once(text,
    "    if args.is_empty() || _is_mixel_support_invite || crate::common::is_empty_uni_link(&args[0]) {\n",
    """    if args.is_empty() || _is_mixel_support_invite || crate::common::is_empty_uni_link(&args[0]) {
        #[cfg(feature = "flutter")]
        if _is_mixel_support_invite {
            // Forward URI/CLI handoffs on every desktop, including Linux cold
            // starts and normalized protocol case, keeping the main UI visible.
            flutter_args.extend(args.iter().cloned());
        }
""", "forward all incoming support arguments to Flutter")
core.write_text(text, encoding="utf-8")
print("Support invite handoff patched: visible app, stable presence heartbeat, runtime customer accept guard, redacted bearer logs")
