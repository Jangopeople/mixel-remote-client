#!/usr/bin/env python3
"""Await Mac page resource cleanup before force-closing support window engines."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import re

PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
OWNED = "flutter/.mixel-dependencies/macos_cleanup"
GENERATED = "flutter/lib/mixel_support_cleanup.dart"
IMPORT = "import 'package:flutter_hbb/mixel_support_cleanup.dart';\n"
FILES = {
    "flutter/lib/utils/multi_window_manager.dart": "e94128aaf434d57d54dccb719633704e814b41fee052e142c4fa9ea4a5e13f3f",
    "flutter/lib/desktop/pages/remote_page.dart": "385deead421fa28cea2bfed49528b48b30e44a58896e0a8de01725f41a4f7c67",
    "flutter/lib/desktop/pages/file_manager_page.dart": "57ea776df3892dd3882bac93d526f6490fb3ada551463883c2af775307bbc525",
    "flutter/lib/desktop/pages/view_camera_page.dart": "e858a52ad5ab545f69526d9b6175d63c6eea7330ba3b7e46c8460ebf4fa4eaec",
    "flutter/lib/desktop/pages/port_forward_page.dart": "4c65b5fdf2705b1b799aeb9e9f2207997a84560ab8dfbf6236a9011fa5c1a081",
    "flutter/lib/desktop/pages/remote_tab_page.dart": "89a43c469c6927d4b543684c1788aa8493dd8886d5dd1679bf04d4490c32dc16",
    "flutter/lib/desktop/pages/file_manager_tab_page.dart": "5fc7eefc39509d54b43386a19ab11a16002a7a864270f902335dcff70757cb85",
    "flutter/lib/desktop/pages/view_camera_tab_page.dart": "cc7f82fadedf9ab368662af3375c8f80a74512bedb6154397e7e40bd7e07dfc8",
    "flutter/lib/desktop/pages/port_forward_tab_page.dart": "96a0d3999bf5812c35ff32d3c516b4807a9e4cd6c095fa3e0c8c5169ec292eb9",
    "flutter/lib/desktop/pages/terminal_tab_page.dart": "73fcda2fb3e6d0ef7f7ae10d12bba5cb7f9d4d58ad47e1c9417dc66123fcc30a",
    "flutter/lib/desktop/pages/terminal_page.dart": "d2d17fc3b8605830aa72b3be6783d4b21b1f864f73c6926df3bb1b7f67adbd4d",
    "flutter/lib/desktop/pages/terminal_connection_manager.dart": "21059dfeb6c9a9025c90cb21a9e7a39fd5a6b9252e9396bdb1025005e3e19ffd",
}
UTILITY = r"""import 'dart:async';

const mixelPrepareWindowClose = 'mixel_prepare_window_close';

final _mixelLivePages =
    <MixelCleanupOnce, Future<void> Function()>{};
final _mixelPendingPages = <Future<void>>{};
Object? _mixelResourceError;
StackTrace? _mixelResourceStack;

void mixelRegisterPageCleanup(
    MixelCleanupOnce owner, Future<void> Function() cleanup) {
  if (!owner.started) _mixelLivePages[owner] = cleanup;
}

void _mixelTrackPageCleanup(MixelCleanupOnce owner, Future<void> future) {
  _mixelLivePages.remove(owner);
  _mixelPendingPages.add(future);
  future.then((_) {
    _mixelPendingPages.remove(future);
  }, onError: (Object error, StackTrace stack) {
    _mixelPendingPages.remove(future);
    _mixelResourceError ??= error;
    _mixelResourceStack ??= stack;
  });
}

Future<void> mixelDrainPageCleanup() async {
  while (_mixelLivePages.isNotEmpty || _mixelPendingPages.isNotEmpty) {
    final callbacks = _mixelLivePages.values.toList();
    final pending = <Future<void>>{
      for (final callback in callbacks) Future<void>.sync(callback),
      ..._mixelPendingPages,
    };
    try {
      await Future.wait<void>(pending);
    } catch (error, stack) {
      _mixelResourceError ??= error;
      _mixelResourceStack ??= stack;
    }
  }
  if (_mixelResourceError != null) {
    Error.throwWithStackTrace(_mixelResourceError!, _mixelResourceStack!);
  }
}

class MixelCleanupOnce {
  MixelCleanupOnce({this.trackPage = false});
  final bool trackPage;
  Future<void>? _future;
  bool get started => _future != null;
  Future<void> run(Future<void> Function() cleanup) {
    if (_future != null) return _future!;
    final completion = Completer<void>();
    _future = completion.future;
    if (trackPage) _mixelTrackPageCleanup(this, _future!);
    Future<void>.sync(cleanup).then(completion.complete,
        onError: completion.completeError);
    return _future!;
  }
}

class MixelWindowCleanup {
  Future<void>? _future;
  bool get started => _future != null;
  Future<void> run(Future<void> Function() cleanup) {
    if (_future != null) return _future!;
    final completion = Completer<void>();
    _future = completion.future.timeout(const Duration(seconds: 4));
    Future<void>.sync(cleanup).then(completion.complete,
        onError: completion.completeError);
    return _future!;
  }
}

Future<void> mixelCleanupPages(
    Iterable<Future<void> Function()> pages, void Function() clear) async {
  final pending = Future.wait<void>(
      pages.map((cleanup) => Future<void>.sync(cleanup)));
  try {
    clear();
  } finally {
    await pending;
  }
}

Future<void> mixelRunCleanupSteps(
    Iterable<Future<void> Function()> steps) async {
  Object? firstError;
  StackTrace? firstStack;
  for (final step in steps) {
    try {
      await Future<void>.sync(step);
    } catch (error, stack) {
      firstError ??= error;
      firstStack ??= stack;
    }
  }
  if (firstError != null) {
    Error.throwWithStackTrace(firstError, firstStack!);
  }
}

Map<String, Object> mixelCloseAcknowledgement(int windowId) =>
    {'window_id': windowId, 'cleanup': 'complete'};

bool mixelValidCloseAcknowledgement(dynamic value, int windowId) =>
    windowId > 0 &&
    value is Map &&
    value.length == 2 &&
    value['window_id'] is int &&
    value['window_id'] == windowId &&
    value['cleanup'] == 'complete';
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 500_000:
        raise RuntimeError("Nonregular owned Mac cleanup source: " + str(path))
    return path.read_bytes()


def safe_parents(repo, relative):
    path = repo
    for part in Path(relative).parts[:-1]:
        path /= part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError("Nonregular Mac cleanup parent: " + str(path))


def once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Changed or ambiguous Mac cleanup boundary")
    return source.replace(old, new, 1)


def function(source, anchor):
    start = source.index(anchor)
    # A complete anchor can contain named-parameter braces before the body.
    first = source.index("{", start + len(anchor) - 1)
    depth, end = 1, first + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def statements(body):
    """Split the exact pinned cleanup body at top-level Dart statement ends."""
    result, start, index = [], 0, 0
    braces = parentheses = brackets = 0
    while index < len(body):
        if body.startswith("//", index):
            newline = body.find("\n", index)
            index = len(body) if newline == -1 else newline + 1
            continue
        if body.startswith("/*", index):
            end = body.find("*/", index + 2)
            if end == -1:
                raise RuntimeError("Unterminated pinned cleanup comment")
            index = end + 2
            continue
        char = body[index]
        if char in ("'", '"'):
            quote = char * 3 if body.startswith(char * 3, index) else char
            index += len(quote)
            while index < len(body) and not body.startswith(quote, index):
                index += 2 if body[index] == "\\" else 1
            if index == len(body):
                raise RuntimeError("Unterminated pinned cleanup string")
            index += len(quote)
            continue
        braces += (char == "{") - (char == "}")
        parentheses += (char == "(") - (char == ")")
        brackets += (char == "[") - (char == "]")
        if min(braces, parentheses, brackets) < 0:
            raise RuntimeError("Unbalanced pinned cleanup statement")
        if braces == parentheses == brackets == 0 and char in (";", "}"):
            result.append(body[start:index + 1])
            start = index + 1
        index += 1
    if body[start:].strip() or any((braces, parentheses, brackets)):
        raise RuntimeError("Incomplete pinned cleanup statement")
    return result


def cleanup_steps(body):
    parts = statements(body)
    declarations, operations = [], []
    for part in parts:
        if "final closeSession =" in part:
            declarations.append(part)
        else:
            operations.append("      () async {" + part + "\n      },")
    return "".join(declarations) + "\n    await mixelRunCleanupSteps([\n" + "\n".join(operations) + "\n    ]);\n"


PAGE_TYPES = {
    "remote": ("RemotePage", "_RemotePageState"),
    "file_manager": ("FileManagerPage", "_FileManagerPageState"),
    "view_camera": ("ViewCameraPage", "_ViewCameraPageState"),
    "port_forward": ("PortForwardPage", "_PortForwardPageState"),
    "terminal": ("TerminalPage", "_TerminalPageState"),
}


def page(source, name):
    widget, state = PAGE_TYPES[name]
    getter = "  FFI get ffi => (_lastState.value! as " + state + ")._ffi;"
    exposed = """

  Future<void> mixelPrepareClose() {
    final state = _lastState.value;
    if (isMacOS && state is %s &&
        (state.mounted || state._mixelCleanupOnce.started)) {
      return state._mixelCloseResources();
    }
    return Future<void>.value();
  }""" % state
    source = once(source, getter, getter + exposed)
    initialization = function(source, "  void initState() {")
    registration = """
    if (isMacOS) {
      _mixelOwnedFfi = _ffi;
      _mixelOwnedId = widget.id;
      mixelRegisterPageCleanup(_mixelCleanupOnce, _mixelCloseResources);
    }
"""
    if name == "terminal":
        registration = registration.replace(
            "      _mixelOwnedId = widget.id;\n",
            "      _mixelOwnedId = widget.id;\n      _mixelOwnedTerminalId = widget.terminalId;\n")
    source = once(source, initialization, initialization[:-1] + registration + "}")
    async_dispose = name in ("remote", "view_camera")
    anchor = "  Future<void> dispose() async {" if async_dispose else "  void dispose() {"
    original = function(source, anchor)
    body = original.split("{", 1)[1].rsplit("}", 1)[0]
    resource_body = once(body, "    super.dispose();\n", "")
    if name == "file_manager":
        observer = "    WidgetsBinding.instance.removeObserver(this);\n"
        resource_body = once(resource_body, observer, "")
        resource_body = "\n" + observer + resource_body
        resource_body = once(resource_body, "    model.close().whenComplete(() {", "    await model.close().whenComplete(() async {")
        resource_body = once(resource_body, "      _ffi.close();", "      await _ffi.close();")
        resource_body = once(resource_body, "      Get.delete<FFI>", "      await Get.delete<FFI>")
        callback_start = resource_body.index("() async {") + len("() async {")
        callback_end = resource_body.index("\n    });", callback_start)
        callback = resource_body[callback_start:callback_end]
        resource_body = resource_body[:callback_start] + cleanup_steps(callback) + resource_body[callback_end:]
    elif name == "port_forward":
        resource_body = once(resource_body, "    _ffi.close();", "    await _ffi.close();")
        resource_body = once(resource_body, "    Get.delete<FFI>", "    await Get.delete<FFI>")
    elif name == "terminal":
        resource_body = once(resource_body, "    _tabStateSubscription?.cancel();",
                             "    await _tabStateSubscription?.cancel();")
        resource_body = once(resource_body, "    TerminalConnectionManager.releaseConnection(widget.id);",
                             "    await TerminalConnectionManager.mixelReleaseConnection(widget.id, _ffi);")
        resource_body = resource_body.replace("widget.terminalId", "_mixelOwnedTerminalId")
    # Every delayed operation uses this State's captured owner, never a later
    # widget replacement or a newer Get registration with the same tag.
    resource_body = resource_body.replace("_ffi", "ownedFfi").replace("widget.id", "ownedId")
    resource_body = resource_body.replace("$" + "{ownedId}", "$ownedId")
    if name in ("remote", "view_camera"):
        resource_body = re.sub(r"(?<![.\w])sessionId\b", "ownedSessionId", resource_body)
        deletion = "    await Get.delete<FFI>(tag: ownedId);"
        shared = "    removeSharedStates(ownedId);"
        resource_body = once(resource_body, shared, "")
        resource_body = once(resource_body, deletion, """    if (_mixelOwnsFfi(ownedId, ownedFfi)) {
      removeSharedStates(ownedId);
    }
    await _mixelDeleteOwnedFfi(ownedId, ownedFfi);""")
    elif name in ("file_manager", "port_forward"):
        prefix = "ft" if name == "file_manager" else "pf"
        tag = "'" + prefix + "_$ownedId'"
        resource_body = once(resource_body, "await Get.delete<FFI>(tag: " + tag + ");",
                             "await _mixelDeleteOwnedFfi('" + prefix + "_$ownedId', ownedFfi);")
    early = "\n    final ownedFfi = _mixelOwnedFfi;\n    final ownedId = _mixelOwnedId;\n"
    if name != "terminal":
        early += "    ownedFfi.closed = true;\n"
    if name in ("remote", "view_camera"):
        early += "    final ownedSessionId = ownedFfi.sessionId;\n"
        timer = "    _timer?.cancel();"
        resource_body = once(resource_body, timer, "")
        early += "    _timer?.cancel();\n    _timer = null;\n"
    resource_body = early + cleanup_steps(resource_body)
    resource = """

  late final FFI _mixelOwnedFfi;
  late final String _mixelOwnedId;
  final _mixelCleanupOnce = MixelCleanupOnce(trackPage: true);
  Future<void> _mixelCloseResources() =>
      _mixelCleanupOnce.run(_mixelCloseResourceBody);

  Future<void> _mixelCloseResourceBody() async {%s  }

  bool _mixelOwnsFfi(String tag, FFI owner) =>
      Get.isRegistered<FFI>(tag: tag) &&
      identical(Get.find<FFI>(tag: tag), owner);

  Future<void> _mixelDeleteOwnedFfi(String tag, FFI owner) async {
    if (_mixelOwnsFfi(tag, owner)) {
      await Get.delete<FFI>(tag: tag);
    }
  }
""" % resource_body
    if name == "terminal":
        resource = resource[:resource.index("\n  bool _mixelOwnsFfi(")]
        resource = resource.replace("  late final FFI _mixelOwnedFfi;",
                                    "  late final int _mixelOwnedTerminalId;\n  late final FFI _mixelOwnedFfi;")
    if async_dispose:
        mac_body = """
    if (isMacOS) {
      super.dispose();
      _mixelCloseResources().catchError((Object error) {
        debugPrint('Mixel support resource cleanup failed: $error');
      });
      return;
    }
"""
    else:
        mac_body = """
    if (isMacOS) {
      super.dispose();
      _mixelCloseResources().catchError((Object error) {
        debugPrint('Mixel support resource cleanup failed: $error');
      });
      return;
    }
"""
    changed = original.replace("{", "{" + mac_body, 1)
    return once(source, original, changed + resource)


def tabs(source, name):
    widget, _ = PAGE_TYPES[name]
    old = ('} else if (call.method == "onDestroy") {\n'
           + ("        // Clean up sessions before window destruction (bounded wait)\n        await _closeAllTabs();"
              if name == "terminal" else
              "      tabController.clear();" if name in ("remote", "view_camera") else
              "        tabController.clear();")
           + ("\n      }" if name in ("file_manager", "port_forward", "terminal") else "\n    }"))
    indentation = "      " if name in ("file_manager", "port_forward", "terminal") else "    "
    added = ('} else if (isMacOS && (call.method == mixelPrepareWindowClose ||\n'
             + indentation + '    call.method == "onDestroy")) {\n'
             + indentation + '  await _mixelPrepareWindowClose();\n'
             + indentation + '  return mixelCloseAcknowledgement(windowId());\n'
             + indentation + old)
    source = once(source, old, added)
    if name == "port_forward":
        source = once(source, 'debugPrint("port forward $id exists");\n          return;',
                      'debugPrint("port forward $id exists");\n          return null;')
    closing_guard = """
      if (isMacOS && _mixelWindowCleanup.started &&
          call.method != mixelPrepareWindowClose && call.method != "onDestroy") {
        throw StateError('The support window is closing. Please try again.');
      }
"""
    if name in ("remote", "view_camera"):
        handler = "  Future<dynamic> _remoteMethodHandler(call, fromWindowId) async {"
    else:
        handler = "rustDeskWinManager.setMethodHandler((call, fromWindowId) async {"
    source = once(source, handler, handler + closing_guard)
    setup = """
      final pages = tabController.state.value.tabs
          .map((tab) => tab.page).whereType<%s>().toList();
""" % widget
    if name == "terminal":
        for anchor in ("  void _addNewTerminal(String peerId, {int? terminalId}) {",
                       "  void _addNewTerminalForCurrentPeer({int? terminalId}) {"):
            source = once(source, anchor, anchor + "\n    if (isMacOS && _mixelWindowCleanup.started) return;")
        source = once(source, "    for (final terminalId in sortedSessions) {",
                      "    for (final terminalId in sortedSessions) {\n      if (isMacOS && _mixelWindowCleanup.started) return;")
        setup += """
      final keys = tabController.state.value.tabs.map((tab) => tab.key).toList();
      _windowClosing = true;
      try {
        await Future.wait<void>(keys.map((key) =>
            _closeTerminalSessionIfNeeded(key, persistAll: true)));
      } finally {
        await mixelCleanupPages(
            pages.map((page) => page.mixelPrepareClose), tabController.clear);
      }
"""
    else:
        setup += """
      await mixelCleanupPages(
          pages.map((page) => page.mixelPrepareClose), tabController.clear);
"""
    setup = "\n      try {" + setup + """
      } finally {
        await mixelDrainPageCleanup();
      }
"""
    method = """
  final _mixelWindowCleanup = MixelWindowCleanup();
  Future<void> _mixelPrepareWindowClose() =>
      _mixelWindowCleanup.run(() async {%s    });

""" % setup
    return once(source, "  int windowId() {", method + "  int windowId() {")


def connection(source):
    method = """
  static final _mixelOwnerCloses = Expando<MixelCleanupOnce>();

  static Future<void> _mixelCloseOwner(FFI owner) {
    final once = _mixelOwnerCloses[owner] ??= MixelCleanupOnce();
    return once.run(owner.close);
  }

  static Future<void> mixelReleaseConnection(String peerId, FFI owner) async {
    if (!identical(_connections[peerId], owner)) {
      await _mixelCloseOwner(owner);
      return;
    }
    final refCount = _connectionRefCount[peerId] ?? 0;
    if (refCount <= 1) {
      final ffi = _connections.remove(peerId);
      _connectionRefCount.remove(peerId);
      if (ffi != null) {
        try {
          await _mixelCloseOwner(ffi);
        } finally {
          final tag = 'terminal_$peerId';
          if (Get.isRegistered<FFI>(tag: tag) &&
              identical(Get.find<FFI>(tag: tag), ffi)) {
            await Get.delete<FFI>(tag: tag);
          }
        }
      }
    } else {
      _connectionRefCount[peerId] = refCount - 1;
    }
  }

"""
    return once(source, "  /// Check if a connection exists for a peer", method + "  /// Check if a connection exists for a peer")


def manager(source):
    source = once(source, "  final Set<int> _inactiveWindows = {};",
                  """  bool _mixelClosingSubWindows = false;
  final _mixelCloseAllOnce = MixelCleanupOnce();
  final Set<Future<void>> _mixelPendingCreations = {};
  final Set<int> _inactiveWindows = {};""")
    gate = """
  void _mixelAssertCanOpen() {
    if (isMacOS && _mixelClosingSubWindows) {
      throw StateError('Support windows are closing. Please try again.');
    }
  }

"""
    source = once(source, "  Future<void> closeAllSubWindows() async {",
                  gate + """  Future<void> closeAllSubWindows() async {
    if (isMacOS) {
      _mixelClosingSubWindows = true;
      await _mixelCloseAllOnce.run(() async {
        try {
          await mixelRunCleanupSteps([
            () => Future.wait<void>(_mixelPendingCreations.toList())
                .timeout(const Duration(milliseconds: 4500)),
            () async => await Future.wait(
                WindowType.values.map((e) => _closeWindows(e))),
          ]);
        } catch (error) {
          debugPrint('Mixel support window cleanup failed: $error');
          rethrow;
        }
      });
      return;
    }""")
    for anchor in ("  Future<MultiWindowCallResult> _newSession(",
                   "  Future<MultiWindowCallResult> newSession(",
                   "  Future<MultiWindowCallResult> newTerminal(",
                   "  Future<MultiWindowCallResult> call(",
                   "  moveTabToNewWindow(", "  openMonitorSession("):
        start = source.index(anchor)
        body = source.index(") async {", start) + len(") async {")
        source = source[:body] + "\n    _mixelAssertCanOpen();" + source[body:]
    anchor = "  Future<int> newSessionWindow("
    start = source.index(anchor)
    first = source.index(") async {", start) + len(") async ")
    depth, end = 1, first + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    original = source[start:end]
    original_body = original[first-start+1:-1]
    mac = once(original_body,
               "    final windowController = await DesktopMultiWindow.createWindow(msg);",
               """    final windowController = await DesktopMultiWindow.createWindow(msg);
    if (_mixelClosingSubWindows) {
      final ownedWindowId = windowController.windowId;
      await mixelRunCleanupSteps([
        () => _mixelPrepareWindowClose(ownedWindowId),
        () => _mixelForceCloseWindow(ownedWindowId),
      ]);
      finished.complete();
      throw StateError('Support window creation was cancelled during cleanup');
    }""")
    changed = original[:first-start+1] + """
    if (isMacOS) {
      _mixelAssertCanOpen();
      final finished = Completer<void>();
      _mixelPendingCreations.add(finished.future);
      finished.future.ignore();
      try {%s
      } catch (error, stack) {
        if (!finished.isCompleted) finished.completeError(error, stack);
        rethrow;
      } finally {
        if (!finished.isCompleted) finished.complete();
        _mixelPendingCreations.remove(finished.future);
      }
    }
""" % mac + original_body + "}"
    source = once(source, original, changed)
    method = """
  Future<void> _mixelPrepareWindowClose(int windowId) async {
    try {
      final acknowledgement = await DesktopMultiWindow.invokeMethod(
          windowId, mixelPrepareWindowClose).timeout(
              const Duration(milliseconds: 4500));
      if (!mixelValidCloseAcknowledgement(acknowledgement, windowId)) {
        throw StateError('Invalid support-window cleanup acknowledgement');
      }
    } catch (error) {
      debugPrint('Mixel support cleanup failed for window $windowId: $error');
      rethrow;
    }
  }

  Future<void> _mixelForceCloseWindow(int windowId) async {
    await mixelRunCleanupSteps([
      () => WindowController.fromWindowId(windowId).setPreventClose(false)
          .timeout(const Duration(milliseconds: 4500)),
      () => WindowController.fromWindowId(windowId).close()
          .timeout(const Duration(milliseconds: 4500)),
    ]);
    _activeWindows.remove(windowId);
  }

"""
    source = once(source, "  Future<void> _closeWindows(WindowType type) async {",
                  method + "  Future<void> _closeWindows(WindowType type) async {")
    source = once(source, "    for (int i = 0; i < windows.length; i++) {",
                  """    final preparations = isMacOS
        ? {for (final windowId in windows)
            windowId: _mixelPrepareWindowClose(windowId)}
        : <int, Future<void>>{};
    for (final preparation in preparations.values) {
      preparation.ignore();
    }
    Object? firstCloseError;
    StackTrace? firstCloseStack;
    for (int i = 0; i < windows.length; i++) {""")
    source = once(source, "      try {\n        await WindowController.fromWindowId(wId).setPreventClose(false);",
                  """      if (isMacOS) {
        try {
          await mixelRunCleanupSteps([
            () async => await preparations[wId],
            () => _mixelForceCloseWindow(wId),
          ]);
        } catch (error, stack) {
          firstCloseError ??= error;
          firstCloseStack ??= stack;
        }
        continue;
      }
      try {
        await WindowController.fromWindowId(wId).setPreventClose(false);""")
    source = once(source, "    clearWindowType(type);\n  }",
                  """    if (firstCloseError != null) {
      Error.throwWithStackTrace(firstCloseError, firstCloseStack!);
    }
    clearWindowType(type);
  }""")
    source = once(source, "      windows = _findWindowsByType(type);",
                  """      windows = isMacOS
          ? List<int>.of(_findWindowsByType(type))
          : _findWindowsByType(type);""")
    source = "import 'dart:async';\n" + source
    return source


def transform(name, data):
    if sha(data) != FILES[name]:
        raise RuntimeError("Pinned Mac cleanup source changed: " + name)
    source = data.decode("utf-8")
    stem = Path(name).stem
    if stem == "multi_window_manager":
        result = manager(source)
    elif stem == "terminal_connection_manager":
        result = connection(source)
    elif stem.endswith("_tab_page"):
        result = tabs(source, stem.removesuffix("_tab_page"))
    else:
        result = page(source, stem.removesuffix("_page"))
    result = IMPORT + result
    return result.encode()


def apply(repo):
    for name in tuple(FILES) + (GENERATED, OWNED + "/proof.json"):
        safe_parents(repo, name)
    cargo = regular(repo / "Cargo.toml").decode().replace("\r\n", "\n")
    packages = re.findall(r"(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)", cargo)
    if (len(packages) != 1 or
            re.findall(r'^version\s*=\s*"([^"]+)"\s*$', packages[0], re.M) != ["1.4.6"]):
        raise RuntimeError("Mac cleanup correction requires upstream 1.4.6")
    owned = repo / OWNED
    expected_members = set(FILES) | {"proof.json"}
    originals, line_endings = {}, {}
    if owned.exists():
        if owned.is_symlink() or not owned.is_dir():
            raise RuntimeError("Invalid owned Mac cleanup directory")
        actual, actual_dirs = set(), set()
        for path in owned.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("Symlink in owned Mac cleanup inventory")
            if path.is_dir():
                actual_dirs.add(path.relative_to(owned).as_posix())
            else:
                regular(path)
                actual.add(path.relative_to(owned).as_posix())
        if actual != expected_members:
            raise RuntimeError("Owned Mac cleanup inventory changed")
        expected_dirs = {p.as_posix() for name in FILES for p in Path(name).parents if str(p) != "."}
        if actual_dirs != expected_dirs:
            raise RuntimeError("Owned Mac cleanup directory inventory changed")
        proof = json.loads(regular(owned / "proof.json"))
        if (set(proof) != {"pin", "schema", "line_endings"} or
                proof.get("pin") != PIN or type(proof.get("schema")) is not int or
                proof.get("schema") != 1 or not isinstance(proof.get("line_endings"), dict) or
                set(proof.get("line_endings", {})) != set(FILES)):
            raise RuntimeError("Owned Mac cleanup provenance changed")
        line_endings = proof["line_endings"]
        for name in FILES:
            originals[name] = regular(owned / name)
    else:
        if owned.is_symlink():
            raise RuntimeError("Invalid owned Mac cleanup directory")
        for name in FILES:
            data = regular(repo / name)
            line_endings[name] = "crlf" if b"\r\n" in data else "lf"
            originals[name] = data.replace(b"\r\n", b"\n")
    corrected = {}
    for name, original in originals.items():
        corrected[name] = transform(name, original)
        if line_endings[name] not in ("lf", "crlf"):
            raise RuntimeError("Owned Mac cleanup line ending changed")
        if line_endings[name] == "crlf":
            corrected[name] = corrected[name].replace(b"\n", b"\r\n")
        current = regular(repo / name)
        if owned.exists():
            if current != corrected[name]:
                raise RuntimeError("Owned Mac cleanup target changed: " + name)
        elif current.replace(b"\r\n", b"\n") != original:
            raise RuntimeError("Mac cleanup target changed during preparation")
    generated = repo / GENERATED
    if generated.exists() or generated.is_symlink():
        if regular(generated) != UTILITY.encode():
            raise RuntimeError("Owned Mac cleanup helper changed")
    if not owned.exists():
        owned.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".mixel-cleanup-stage-", dir=owned.parent) as directory:
            stage = Path(directory) / "macos_cleanup"
            stage.mkdir()
            for name, original in originals.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original)
            (stage / "proof.json").write_text(json.dumps(
                {"pin": PIN, "schema": 1, "line_endings": line_endings}, sort_keys=True) + "\n")
            if owned.exists() or owned.is_symlink():
                raise RuntimeError("Mac cleanup destination appeared during preparation")
            stage.rename(owned)
    for name, data in corrected.items():
        (repo / name).write_bytes(data)
    generated.write_bytes(UTILITY.encode())
    print("   patched bounded Mac page cleanup: cached resource futures → validated window acknowledgement → native force close")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
