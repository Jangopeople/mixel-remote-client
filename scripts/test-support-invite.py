#!/usr/bin/env python3
"""Verify the support-invite patch against the pinned source shape and twice-run idempotence."""
import os
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="mixel-support-patch-") as tmp:
    repo = Path(tmp)
    target = repo / "flutter/lib/common.dart"
    target.parent.mkdir(parents=True)
    target.write_text('''print("initialLink: $initialLink");
debugPrint("A uri was received: $uri. handleByFlutter $handleByFlutter");
import 'package:uni_links/uni_links.dart';
Future<bool> initUniLinks() async {
  if (isLinux) {
    return false;
  }
}
// uri link handler
bool handleUriLink({List<String>? cmdArgs, Uri? uri, String? uriString}) {
  if (args.isEmpty) {
    windowOnTop(null);
    return true;
  }
}
List<String>? urlLinkToCmdArgs(Uri uri) {
  if (uri.authority.isEmpty &&
      uri.path.split('').every((char) => char == '/')) {
    return [];
  }
}
''')
    env = {**os.environ, "RDREPO": str(repo)}
    patcher = root / "scripts/patch-support-invite.py"
    subprocess.run(["python3", str(patcher)], env=env, check=True, capture_output=True, text=True)
    first = target.read_text()
    subprocess.run(["python3", str(patcher)], env=env, check=True, capture_output=True, text=True)
    second = target.read_text()
    assert first == second, "patch must be idempotent"
    assert first.count("Future<void> _reportSupportInvite(") == 1
    assert first.count("--support-invite") == 2
    assert first.count("registerProtocol('mixel-remote');") == 1
    assert "package:uni_links_desktop/uni_links_desktop.dart" in first
    assert "https://rs.mixel.ch/api/presence/client" in first
    assert "Initial app link received." in first
    assert "An app link was received." in first
    assert 'print("initialLink: $initialLink");' not in first
    assert 'debugPrint("A uri was received: $uri.' not in first
print("PASS: support invite URI validates, reports app ID without connecting, redacts invite URLs from logs, and patches idempotently")
