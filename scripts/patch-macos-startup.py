#!/usr/bin/env python3
"""Bound Mac startup and refuse readiness until critical FFI init succeeds.

The overlay is applied after branding. --restore removes only this exact
overlay before the next branding pass, so older patch owners see their exact
input. Every source and resource is validated before the first file write.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parent
INPUT_HASHES = {'flutter/lib/models/native_model.dart': 'd80cb9aa666b49c83de4ddbd3ce79cb99859a9fe16d479cbb36d5643cdb7d4df', 'flutter/lib/main.dart': '982765b4bd1bdf52aec77af49398ff590697978178248d546ec777b947e3d2b8', 'flutter/lib/common.dart': '1bde36857cbe0c9f7734b4d989ad806c1c0773a823f0443dcb9962305a525cf5'}
MARKER = "MIXEL_MACOS_STARTUP_V1"
RESOURCE = "flutter/lib/mixel_startup_failure.dart"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def operations():
    native = "flutter/lib/models/native_model.dart"
    main = "flutter/lib/main.dart"
    common = "flutter/lib/common.dart"
    changes = []

    def add(path, before, after, count=1):
        changes.append((path, before, after, count))

    # The final composed URL ACK queue uses Random.secure() on all desktops.
    # Earlier historical analyzer ownership removes the formerly unused import.
    add(common, "import 'dart:convert';\n", "import 'dart:convert';\nimport 'dart:math';\n")

    add(native, "  StreamEventHandler? _eventCallback;\n", """  StreamEventHandler? _eventCallback;
  // MIXEL_MACOS_STARTUP_V1: only critical initialization grants readiness.
  bool _mixelNativeInitialized = false;
  bool get mixelNativeInitialized => !isMacOS || _mixelNativeInitialized;
  void invalidateMixelNativeInitialization() {
    if (isMacOS) _mixelNativeInitialized = false;
  }
  Future<T> _mixelCriticalInit<T>(Future<T> operation) => isMacOS
      ? operation.timeout(const Duration(seconds: 5)) : operation;
""")
    add(native, "    _appType = appType;\n", "    _appType = appType;\n    if (isMacOS) _mixelNativeInitialized = false;\n")
    add(native, "        _dir = (await getApplicationDocumentsDirectory()).path;", """        final directory = getApplicationDocumentsDirectory();
        _dir = (await (isMacOS
            ? directory.timeout(const Duration(seconds: 2)) : directory)).path;""")
    add(native, "        debugPrint('Failed to get documents directory: $e');", """        if (isMacOS) {
          debugPrint('Mixel startup documents metadata unavailable; using default.');
        } else {
          debugPrint('Failed to get documents directory: $e');
        }""")
    add(native, """        MacOsDeviceInfo macOsInfo = await deviceInfo.macOsInfo;
        name = macOsInfo.computerName;
        id = macOsInfo.systemGUID ?? '';""", """        try {
          final macOsInfo = await deviceInfo.macOsInfo
              .timeout(const Duration(seconds: 2));
          name = macOsInfo.computerName;
          id = macOsInfo.systemGUID ?? '';
        } catch (_) {
          // The existing NA/Flutter defaults are metadata, not a new identity.
          debugPrint('Mixel startup device metadata unavailable; using defaults.');
        }""")
    add(native, "        await _ffiBind.cmInit();", "        await _mixelCriticalInit(_ffiBind.cmInit());")
    add(native, "      await _ffiBind.mainDeviceId(id: id);", "      await _mixelCriticalInit(_ffiBind.mainDeviceId(id: id));")
    add(native, "      await _ffiBind.mainDeviceName(name: name);", "      await _mixelCriticalInit(_ffiBind.mainDeviceName(name: name));")
    add(native, "      await _ffiBind.mainSetHomeDir(home: _homeDir);", "      await _mixelCriticalInit(_ffiBind.mainSetHomeDir(home: _homeDir));")
    add(native, """      await _ffiBind.mainInit(
        appDir: _dir,
        customClientConfig: '',
      );
    } catch (e) {
      debugPrintStack(label: 'initialize failed: $e');
    }
    version = await getVersion();""", """      await _mixelCriticalInit(_ffiBind.mainInit(
        appDir: _dir,
        customClientConfig: '',
      ));
      if (isMacOS) _mixelNativeInitialized = true;
    } catch (e) {
      if (isMacOS) {
        // A timed-out future may still complete, but cannot grant readiness.
        _mixelNativeInitialized = false;
        debugPrint('Mixel native startup initialization failed.');
        return;
      }
      debugPrintStack(label: 'initialize failed: $e');
    }
    if (isMacOS) {
      try {
        version = await getVersion().timeout(const Duration(seconds: 2));
      } catch (_) {
        debugPrint('Mixel startup package metadata unavailable; using default.');
      }
    } else {
      version = await getVersion();
    }""")
    add(main, "import 'common.dart';", "import 'mixel_startup_failure.dart';\nimport 'common.dart';")
    for indentation, count in (("    ", 2), ("  ", 1)):
        add(main, "\n" + indentation + "await windowManager.ensureInitialized();", "\n" + indentation + """if (isMacOS) {
""" + indentation + """  await _mixelMacStartupRefresh(() => windowManager.ensureInitialized());
""" + indentation + """} else {
""" + indentation + """  await windowManager.ensureInitialized();
""" + indentation + "}", count)
    add(main, """    windowManager.setPreventClose(true);
    if (isMacOS) {
      disableWindowMovable(kWindowId);
    }""", """    if (isMacOS) {
      await _mixelMacStartupRefresh(() => windowManager.setPreventClose(true));
      await _mixelMacStartupRefresh(() => windowManager.setMovable(false));
    } else {
      windowManager.setPreventClose(true);
    }""")
    add(main, """  WindowController.fromWindowId(kWindowId!).setPreventClose(true);
  if (isMacOS) {
    disableWindowMovable(kWindowId);
  }""", """  if (isMacOS) {
    final window = WindowController.fromWindowId(kWindowId!);
    await _mixelMacStartupRefresh(() => window.setPreventClose(true));
    await _mixelMacStartupRefresh(() => window.setMovable(false));
  } else {
    WindowController.fromWindowId(kWindowId!).setPreventClose(true);
  }""")
    add(main, """Future<void> initEnv(String appType) async {
  // global shared preference
  await platformFFI.init(appType);
  // global FFI, use this **ONLY** for global configuration
  // for convenience, use global FFI on mobile platform
  // focus on multi-ffi on desktop first
  await initGlobalFFI();
  // await Firebase.initializeApp();
  _registerEventHandler();
  // Update the system theme.
  updateSystemWindowTheme();
}
""", """// MIXEL_MACOS_STARTUP_V1: failed core init shows native-independent UI.
Future<bool> initEnv(String appType) async {
  try {
    await platformFFI.init(appType);
    if (isMacOS && !platformFFI.mixelNativeInitialized) {
      await showMixelStartupFailure(windowId: kWindowId);
      return false;
    }
    // Construct global models only after the native core initialized.
    await initGlobalFFI();
    _registerEventHandler();
    updateSystemWindowTheme();
    return true;
  } catch (_) {
    if (!isMacOS) rethrow;
    platformFFI.invalidateMixelNativeInitialization();
    debugPrint('Mixel startup environment initialization failed.');
    await showMixelStartupFailure(windowId: kWindowId);
    return false;
  }
}

Future<void> _mixelMacStartupRefresh(Future<void> Function() operation) async {
  try {
    await operation().timeout(const Duration(seconds: 2));
  } catch (_) {
    debugPrint('Mixel optional startup refresh unavailable; continuing.');
  }
}
""")
    add(main, "  await initEnv(kAppTypeMain);", "  if (!await initEnv(kAppTypeMain)) return;", 3)
    add(main, "  await initEnv(appType);", "  if (!await initEnv(appType)) return;")
    add(main, "  await initEnv(kAppTypeConnectionManager);", "  if (!await initEnv(kAppTypeConnectionManager)) return;")
    add(main, "  await bind.mainCheckConnectStatus();", """  if (isMacOS) {
    await _mixelMacStartupRefresh(() => bind.mainCheckConnectStatus());
  } else {
    await bind.mainCheckConnectStatus();
  }""")
    add(main, """  await Future.wait([gFFI.abModel.loadCache(), gFFI.groupModel.loadCache()]);
  gFFI.userModel.refreshCurrentUser();
  runApp(App());

  bool? alwaysOnTop;""", """  if (isMacOS) {
    await _mixelMacStartupRefresh(() => Future.wait([
      gFFI.abModel.loadCache(), gFFI.groupModel.loadCache()
    ]).then<void>((_) {}));
  } else {
    await Future.wait([gFFI.abModel.loadCache(), gFFI.groupModel.loadCache()]);
  }
  gFFI.userModel.refreshCurrentUser();
  runApp(App());

  bool? alwaysOnTop;""")
    add(common, "import '../consts.dart';", "import 'package:flutter_hbb/consts.dart';")
    add(common, "bool get mixelSupportUrlCanReceive => _mixelSupportUrlCallbackReady &&", "bool get mixelSupportUrlCanReceive => platformFFI.mixelNativeInitialized && _mixelSupportUrlCallbackReady &&")
    add(common, "bool get mixelSupportUrlHasLiveUi => _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;", "bool get mixelSupportUrlHasLiveUi => platformFFI.mixelNativeInitialized && _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;")
    add(common, "  if (_mixelSupportUrlGeneration.isEmpty) return;", "  if (_mixelSupportUrlGeneration.isEmpty || !platformFFI.mixelNativeInitialized) return;")
    add(common, "  final ready = _mixelSupportUrlCallbackReady && _mixelSupportUrlStreamReady && _mixelSupportUrlUiReady;", "  final ready = mixelSupportUrlCanReceive;")
    add(common, "Future<bool> _renewSupportInviteAttended() async {", "Future<bool> _renewSupportInviteAttended() async {\n  if (isMacOS && !platformFFI.mixelNativeInitialized) return false;")
    add(common, "Future<void> _reportSupportInvite(String token, String apiKey) async {", "Future<void> _reportSupportInvite(String token, String apiKey) async {\n  if (isMacOS && !platformFFI.mixelNativeInitialized) return;")
    add(common, "    isOnline: () async {", "    isOnline: () async {\n      if (isMacOS && !platformFFI.mixelNativeInitialized) return false;")
    return changes


def transform(texts, reverse=False):
    output = dict(texts)
    changes = operations()
    if reverse:
        changes = reversed(changes)
    for path, before, after, count in changes:
        source, target = (after, before) if reverse else (before, after)
        if output[path].count(source) != count:
            raise SystemExit("Pinned Mac startup source drift: " + path)
        output[path] = output[path].replace(source, target, count)
    return output


def apply(root, restore=False):
    paths = {name: root / name for name in INPUT_HASHES}
    def regular(path):
        cursor = root
        for part in path.relative_to(root).parts[:-1]:
            cursor /= part
            if cursor.is_symlink() or not cursor.is_dir():
                raise SystemExit("Mac startup source parent must be a regular directory")
        attributes = path.lstat()
        if not stat.S_ISREG(attributes.st_mode) or attributes.st_nlink != 1:
            raise SystemExit("Mac startup sources must be single-link regular files")
        raw = path.read_bytes()
        normalized = raw.replace(b"\r\n", b"\n")
        newline = b"\r\n" if b"\r\n" in raw else b"\n"
        if raw != normalized.replace(b"\n", newline):
            raise SystemExit("Mac startup source has mixed line endings")
        return normalized.decode("utf-8"), newline
    inventory = {name: regular(path) for name, path in paths.items()}
    texts = {name: value[0] for name, value in inventory.items()}
    source_resource = SCRIPTS / "macos-startup-failure.dart"
    resource = root / RESOURCE
    if source_resource.is_symlink() or not source_resource.is_file() or resource.is_symlink():
        raise SystemExit("Mac startup UI resource must be a regular file")
    ui = source_resource.read_bytes()
    if resource.exists() and (not resource.is_file() or resource.read_bytes() != ui):
        raise SystemExit("Foreign Mac startup UI resource; refusing to overwrite")
    has_overlay = any(MARKER in text for text in texts.values())
    if not has_overlay and restore:
        return
    baseline = transform(texts, reverse=True) if has_overlay else texts
    if any(digest(baseline[name]) != expected for name, expected in INPUT_HASHES.items()):
        raise SystemExit("Pinned Mac startup input digest mismatch; refusing all writes")
    fixed = transform(baseline)
    if has_overlay and fixed != texts:
        raise SystemExit("Mac startup overlay corruption; refusing all writes")
    if has_overlay and not resource.is_file():
        raise SystemExit("Mac startup UI resource missing; refusing all writes")
    output = baseline if restore else fixed
    # Preserve the original line ending style; validate the whole set first.
    for name, path in paths.items():
        if output[name] != texts[name]:
            newline = inventory[name][1]
            path.write_bytes(output[name].replace("\n", newline.decode()).encode())
    if not restore and not resource.exists():
        resource.write_bytes(ui)


def check_prior_overlays(root):
    """Validate prior owners in an exact private inverse before any root write."""
    root = Path(root).resolve()
    if not any(MARKER.encode() in (root / name).read_bytes() for name in INPUT_HASHES):
        return
    with tempfile.TemporaryDirectory(prefix="mixel-startup-prior-check-") as temporary:
        projected = Path(temporary).resolve() / "source"
        shutil.copytree(root, projected, symlinks=True,
                        ignore=shutil.ignore_patterns("build", ".dart_tool", "__pycache__"))
        apply(projected, restore=True)
        # Tao is the other final overlay; its exact inverse preserves the
        # native warning owner's complete src/tray.rs profile.
        checks = [
            ("patch-support-macos-tray-url.py", ["--restore"]),
            ("patch-macos-update.py", ["--source", str(projected), "--restore", "--check"]),
            ("patch-support-fatal-crash.py", ["--source", str(projected), "--restore", "--check"]),
            ("patch-support-native-warnings.py", ["--source", str(projected), "--restore", "--check", "--before-branding"]),
            ("patch-support-fingerprint.py", ["--restore", "--check", str(projected)]),
            ("patch-support-uinput-device.py", ["--source", str(projected), "--restore", "--check"]),
            ("patch-support-file-transfer-window.py", ["--restore", "--check", "--before-branding", str(projected)]),
            ("patch-support-keyboard-default.py", ["--check"]),
            ("patch-support-macos-rdev.py", ["--restore", "--check", str(projected)]),
        ]
        environment = dict(os.environ, RDREPO=str(projected))
        for name, arguments in checks:
            result = subprocess.run([sys.executable, str(SCRIPTS / name), *arguments],
                                    env=environment, capture_output=True, text=True,
                                    encoding="utf-8", timeout=90)
            if result.returncode:
                raise SystemExit("Composed prior owner validation failed: " + name + "\n" + result.stderr[-4000:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check-prior", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ.get("RDREPO", SCRIPTS.parent / "rustdesk"))
    if args.check_prior:
        if args.restore: parser.error("--check-prior cannot restore supplied source")
        check_prior_overlays(root)
        print("PASS: exact prior overlay ownership validated before any supplied-source write")
        return
    apply(root, args.restore)
    print("PASS: Mac startup overlay " + ("restored for branding" if args.restore else "bounds metadata, gates core readiness and provides safe failure UI"))


if __name__ == "__main__":
    main()
