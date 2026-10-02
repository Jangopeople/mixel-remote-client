#!/usr/bin/env python3
"""Install the attended support handoff into fresh or already-patched 1.4.6 source."""
import os
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
text = source.read_text()
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

Future<bool> _renewSupportInviteAttended() async {
  if (_supportInviteRenewing) return false;
  _supportInviteRenewing = true;
  try {
    // A special runtime-only IPC command, never a saved preference.
    await bind.mainSetOption(key: 'mixel-support-invite-attended', value: 'Y');
    return await bind.mainGetOption(key: 'mixel-support-invite-attended') == 'Y';
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
      final status = jsonDecode(await bind.mainGetConnectStatus());
      return status is Map && status['status_num'] is num && status['status_num'] > 0;
    },
    armAttended: _renewSupportInviteAttended,
    report: (token, apiKey, id) async {
      final response = await http.HttpService().sendRequest(
        Uri.parse('https://rs.mixel.ch/api/presence/client'),
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
support_handler = """  final supportInviteIndex = args.indexOf('--support-invite');
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
if start_marker in text:
    start = text.index(start_marker)
    end = text.index(empty_handler, start)
    text = text[:start] + support_handler + text[end:]
else:
    text = replace_once(text, empty_handler, support_handler + empty_handler, "URI argument handler")

parser_before = """  if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
"""
parser_after = """  if (uri.scheme == 'mixel-remote' && uri.authority == 'support') {
    final params = uri.queryParameters.map((k, v) => MapEntry(k.toLowerCase(), v));
    final token = params['invite'] ?? '';
    final apiKey = params['apikey'] ?? '';
    if (!MixelSupportInviteReporter.validInvite(token, apiKey)) return null;
    return ['--support-invite', token, '--support-apikey', apiKey];
  } else if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
"""
if "  if (uri.scheme == 'mixel-remote' && uri.authority == 'support') {" in text:
    start = text.index("  if (uri.scheme == 'mixel-remote' && uri.authority == 'support') {")
    end = text.index("  } else if (uri.authority.isEmpty &&", start)
    text = text[:start] + parser_after.split("  } else if (uri.authority.isEmpty &&")[0] + text[end:]
else:
    text = replace_once(text, parser_before, parser_after, "support URI parser")
source.write_text(text)
(rdrepo / "flutter/lib/mixel_support_invite.dart").write_text(
    (scripts / "support-invite-reporter.dart").read_text()
)

main = rdrepo / "flutter/lib/main.dart"
main.write_text(main.read_text().replace('debugPrint("launch args: $args");', "debugPrint('Main app launch received.');"))

password = rdrepo / "libs/hbb_common/src/password_security.rs"
text = password.read_text()
guard_code = (scripts / "support-invite-guard.rs").read_text() + "\n"
guard_marker = "// Mixel attended support guard. Runtime only: never change saved login preferences.\n"
if guard_marker not in text:
    text = replace_once(text, "lazy_static::lazy_static! {", guard_code + "lazy_static::lazy_static! {", "password guard insertion")
else:
    start = text.index(guard_marker)
    end = text.index("lazy_static::lazy_static! {", start)
    text = text[:start] + guard_code + text[end:]
text = replace_once(text, "    approve_mode() == ApproveMode::Password\n", "    !support_invite_requires_click()\n        && approve_mode() == ApproveMode::Password\n", "connection-manager visibility guard")
password.write_text(text)

ui = rdrepo / "src/ui_interface.rs"
text = ui.read_text()
text = replace_once(text, "pub fn get_option<T: AsRef<str>>(key: T) -> String {\n", """pub fn get_option<T: AsRef<str>>(key: T) -> String {
    if key.as_ref() == "mixel-support-invite-attended" {
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        {
            if let Ok(Some(value)) = ipc::get_config("mixel-support-invite-attended") {
                return value;
            }
            if crate::platform::is_installed() {
                // A separate installed service must confirm the guard.
                return String::new();
            }
        }
        return if hbb_common::password_security::support_invite_requires_click() {
            "Y".to_owned()
        } else {
            String::new()
        };
    }
""", "runtime guard getter")
text = replace_once(text, "pub fn set_option(key: String, value: String) {\n", """pub fn set_option(key: String, value: String) {
    if key == "mixel-support-invite-attended" {
        if value == "Y" {
            hbb_common::password_security::renew_support_invite_attended();
            #[cfg(not(any(target_os = "android", target_os = "ios")))]
            ipc::set_config("mixel-support-invite-attended", value).ok();
        }
        return; // Runtime only. Never write OPTIONS or Config.
    }
""", "runtime guard setter")
ui.write_text(text)

ipc = rdrepo / "src/ipc.rs"
text = ipc.read_text()
text = replace_once(text, """                } else if name == "trusted-devices" {
                    value = Some(Config::get_trusted_devices_json());
""", """                } else if name == "trusted-devices" {
                    value = Some(Config::get_trusted_devices_json());
                } else if name == "mixel-support-invite-attended" {
                    value = Some(if password::support_invite_requires_click() {
                        "Y".to_owned()
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
ipc.write_text(text)

connection = rdrepo / "src/server/connection.rs"
text = connection.read_text()
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
text = replace_once(text, """            } else if (password::approve_mode() == ApproveMode::Click
""", """            } else if password::support_invite_must_wait(self.support_invite_attended, self.support_invite_accepted)
                || (password::approve_mode() == ApproveMode::Click
""", "attended guard before password/recent-session auto-authorization")
connection.write_text(text)

# The native bootstrap normally starts the incoming server only on an empty
# command line. A support URI is an incoming help request, not an outgoing
# connection command, so the same server/portable startup must run for it.
core = rdrepo / "src/core_main.rs"
text = core.read_text()
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
        hbb_common::password_security::renew_support_invite_attended();
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
core.write_text(text)
print("Support invite handoff patched: visible app, stable presence heartbeat, runtime customer accept guard, redacted bearer logs")
