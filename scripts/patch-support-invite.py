#!/usr/bin/env python3
"""Add the attended support invite handoff to the pinned RustDesk checkout."""
import os
from pathlib import Path

rdrepo = Path(os.environ.get("RDREPO", Path(__file__).resolve().parents[1] / "rustdesk"))
source = rdrepo / "flutter/lib/common.dart"
text = source.read_text()
uni_import = "import 'package:uni_links/uni_links.dart';"
desktop_import = "import 'package:uni_links_desktop/uni_links_desktop.dart';"
if desktop_import not in text:
    if uni_import not in text:
        raise SystemExit("Could not find uni_links import in common.dart")
    text = text.replace(uni_import, uni_import + "\n" + desktop_import, 1)

registration = '''  if (isWindows) {
    registerProtocol('mixel-remote');
  }
'''
registration_anchor = '''  if (isLinux) {
    return false;
  }
'''
if registration.strip() not in text:
    if registration_anchor not in text:
        raise SystemExit("Could not find initUniLinks platform guard in common.dart")
    text = text.replace(registration_anchor, registration_anchor + registration, 1)

marker = "// uri link handler\n"
helper = '''Future<void> _reportSupportInvite(String token, String apiKey) async {
  if (!RegExp(r'^inv_[0-9a-f-]{36}$', caseSensitive: false).hasMatch(token) ||
      apiKey.length < 20 || apiKey.length > 4096) return;
  try {
    final id = (await bind.mainGetMyId()).trim();
    if (!RegExp(r'^[a-zA-Z0-9-]{6,32}$').hasMatch(id)) return;
    await http.HttpService().sendRequest(
      Uri.parse('https://rs.mixel.ch/api/presence/client'),
      http.HttpMethod.post,
      headers: {'Content-Type': 'application/json', 'apikey': apiKey},
      body: jsonEncode({'token': token, 'rustdeskId': id}),
    );
  } catch (_) {
    // Presence is best-effort; never block app startup or log invite data.
    debugPrint('Support presence report unavailable.');
  }
}

'''
if helper.strip() not in text:
    if marker not in text:
        raise SystemExit("Could not find URI handler insertion point in common.dart")
    text = text.replace(marker, helper + marker, 1)
text = text.replace('print("initialLink: $initialLink");', "debugPrint('Initial app link received.');")
text = text.replace('debugPrint("A uri was received: $uri. handleByFlutter $handleByFlutter");', "debugPrint('An app link was received.');")

needle = '''  if (args.isEmpty) {
    windowOnTop(null);
    return true;
  }
'''
replacement = '''  final supportInviteIndex = args.indexOf('--support-invite');
  final supportApiKeyIndex = args.indexOf('--support-apikey');
  if (supportInviteIndex >= 0 && supportApiKeyIndex >= 0 &&
      supportInviteIndex + 1 < args.length && supportApiKeyIndex + 1 < args.length) {
    final token = args[supportInviteIndex + 1];
    final apiKey = args[supportApiKeyIndex + 1];
    Future.delayed(Duration.zero, () => _reportSupportInvite(token, apiKey));
    return true;
  }
  if (args.isEmpty) {
    windowOnTop(null);
    return true;
  }
'''
if replacement not in text:
    if needle not in text:
        raise SystemExit("Could not find URI argument handling point in common.dart")
    text = text.replace(needle, replacement, 1)

needle = '''  if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
'''
replacement = '''  if (uri.scheme == 'mixel-remote' && uri.authority == 'support') {
    final params = uri.queryParameters.map((k, v) => MapEntry(k.toLowerCase(), v));
    final token = params['invite'] ?? '';
    final apiKey = params['apikey'] ?? '';
    if (!RegExp(r'^inv_[0-9a-f-]{36}$', caseSensitive: false).hasMatch(token) ||
        apiKey.length < 20 || apiKey.length > 4096) return null;
    return ['--support-invite', token, '--support-apikey', apiKey];
  } else if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
'''
if replacement not in text:
    if needle not in text:
        raise SystemExit("Could not find URI parser insertion point in common.dart")
    text = text.replace(needle, replacement, 1)

source.write_text(text)
print("Support invite handoff patched into common.dart")
