#!/usr/bin/env python3
"""Coordinate bounded main-engine Mac Quit after aggregate Flutter cancellation."""
import hashlib
import contextlib
import io
import os
from pathlib import Path
import re
import stat
import tempfile

PIN = "1abc897c451c8b5bbff3792509a7fef9d12f2ce3"
GENERATED = "flutter/lib/mixel_support_app_exit.dart"
IMPORT = "import 'package:flutter_hbb/mixel_support_app_exit.dart';\n"
FILES = {'flutter/macos/Runner/AppDelegate.swift': '5df2c6487e28b1042ba9863a965da1de0b16bf3d8b08b4f3e78fed8a21b5cf10', 'flutter/macos/Runner/MainFlutterWindow.swift': '92db632c368dbfb9954206b941de4d611c0bf3e98676fade05cb45cd6670af4c', 'flutter/lib/main.dart': 'd2960a121fb44da10670905494824091bdcfbeb34bea35719aadc05c1826ec80', 'flutter/lib/common.dart': '65af38effd117b36c614c729192c1c20a69e2ac6a7a0ade89b1890b2cc9a7138', 'flutter/lib/utils/multi_window_manager.dart': 'a0e9f3580f90e0398e3c1e5ff3a7cfa525b40e386227c8a16408d71efa6e5da5', 'flutter/lib/desktop/pages/desktop_home_page.dart': '3d182f2a82232bd8d57fa3ef44c002b65e03bbd9dd8c4fb1e536e4265c33cbf3', 'flutter/lib/desktop/pages/desktop_tab_page.dart': 'fac626f31e59978d36abb32d3322ef59978f37e265b9d150db7d155b90f1d8a9', 'flutter/lib/desktop/widgets/tabbar_widget.dart': '16e51a8d1ecccea6a9e978d88ce7f9be927f3f66e688702a5ecc18e457dd5101', 'flutter/lib/models/native_model.dart': '37e58276da1c97247a758f13e6fe00c90236fb11f795b794178393067b790bc3', 'flutter/lib/models/server_model.dart': 'febc11a1baac03dac0d84b8f85a2655784a7ceffb0789c42359b22f0aef330f9', 'flutter/lib/models/ab_model.dart': 'ac8118640a408db95028cb077471daf694f71e2ead9c6a4c2ee38c81e3b111a4'}

FRESH = {'flutter/macos/Runner/AppDelegate.swift': '5df2c6487e28b1042ba9863a965da1de0b16bf3d8b08b4f3e78fed8a21b5cf10', 'flutter/macos/Runner/MainFlutterWindow.swift': '92db632c368dbfb9954206b941de4d611c0bf3e98676fade05cb45cd6670af4c', 'flutter/lib/main.dart': 'd688a496b23e58cc9a57d85d8ebd9f1e6f43cec3d82ccad632098a0dec5fc0df', 'flutter/lib/common.dart': '95ffcc4fdc3d83564d19d5ad7c999adbe65cdc37829cf466d43d5a9684e7f8de', 'flutter/lib/utils/multi_window_manager.dart': 'e94128aaf434d57d54dccb719633704e814b41fee052e142c4fa9ea4a5e13f3f', 'flutter/lib/desktop/pages/desktop_home_page.dart': '84fd3df40fd02508de9e36df3092ff1c1d7359ce92466240d093df6dbf945af8', 'flutter/lib/desktop/pages/desktop_tab_page.dart': 'fac626f31e59978d36abb32d3322ef59978f37e265b9d150db7d155b90f1d8a9', 'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd37441c9917f79895985cddc3dbd6862866712548aa37915a1459e7d23863cf5', 'flutter/lib/models/native_model.dart': 'dfb3af1de5b884dae7928535b09d575a591487e2cf6b0b89e11c1abf9bfe05a6', 'flutter/lib/models/server_model.dart': 'febc11a1baac03dac0d84b8f85a2655784a7ceffb0789c42359b22f0aef330f9', 'flutter/lib/models/ab_model.dart': 'e1c79b068b46042f5d7a1c3a4437b656a10e6b64cd67b671969faf1c2ac7c176'}

UTILITY = r"""import 'dart:async';
import 'dart:ui' as ui;

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

const mixelAppExitPrepare = 'mixel_prepare_application_exit';
const mixelAppWindowPrepare = 'mixel_prepare_application_window';
const _mixelHost = MethodChannel('org.rustdesk.rustdesk/host');
final _mixelExitOwners = <MixelExitOwner>{};
bool _mixelExitStarted = false;
Object? _mixelExitError;
StackTrace? _mixelExitStack;

// One engine owns its resource handles; application preparation does not close
// engines, so every originating native/Dart channel reply can still unwind.
class MixelExitOwner {
  bool stopping = false;
  bool _registered = false;
  Timer? timer;
  final _pending = <Future<void>>{};
  final _stoppers = <Future<void> Function()>[];
  Future<void>? _stopFuture;
  Object? _error;
  StackTrace? _stack;

  void register([Future<void> Function()? stopper]) {
    if (_registered || stopping) {
      throw StateError('Application resource owner can be registered only once');
    }
    _registered = true;
    if (stopper != null) _stoppers.add(stopper);
    _mixelExitOwners.add(this);
    if (_mixelExitStarted) stop().ignore();
  }

  Future<void> track(Future<void> Function() operation) {
    if (stopping) return Future<void>.value();
    // Reserve before executing, including synchronous callback reentry.
    final completion = Completer<void>();
    final future = completion.future;
    _pending.add(future);
    Future<void>.sync(operation).then(completion.complete,
        onError: completion.completeError);
    future.then<void>((_) { _pending.remove(future); },
        onError: (Object error, StackTrace stack) {
      _pending.remove(future);
      _error ??= error;
      _stack ??= stack;
    });
    return future;
  }

  void poll(Future<void> Function() operation) => track(operation).ignore();

  Future<void> stop() {
    if (_stopFuture != null) return _stopFuture!;
    final completion = Completer<void>();
    _stopFuture = completion.future;
    stopping = true;
    timer?.cancel();
    timer = null;
    final attempts = <Future<void>>[
      for (final stopper in _stoppers) Future<void>.sync(stopper),
      ..._pending,
    ];
    Future.wait<void>(attempts.map((future) =>
        future.timeout(const Duration(milliseconds: 4500))))
        .then((_) {
      _mixelExitOwners.remove(this);
      if (_error != null) {
        _mixelExitError ??= _error;
        _mixelExitStack ??= _stack;
        completion.completeError(_error!, _stack!);
      } else {
        completion.complete();
      }
    }, onError: (Object error, StackTrace stack) {
      _mixelExitOwners.remove(this);
      _mixelExitError ??= _error ?? error;
      _mixelExitStack ??= _stack ?? stack;
      completion.completeError(_error ?? error, _stack ?? stack);
    });
    return _stopFuture!;
  }
}

Future<void> mixelDrainExitOwners() async {
  _mixelExitStarted = true;
  // Stop every owner before awaiting any one owner, including failed/hung ones.
  Object? firstError;
  StackTrace? firstStack;
  while (_mixelExitOwners.isNotEmpty) {
    final attempts = _mixelExitOwners.toList().map((owner) => owner.stop()).toList();
    try { await Future.wait<void>(attempts); } catch (error, stack) {
      firstError ??= error;
      firstStack ??= stack;
    }
  }
  firstError ??= _mixelExitError;
  firstStack ??= _mixelExitStack;
  if (firstError != null) Error.throwWithStackTrace(firstError, firstStack!);
}

Timer mixelExitPeriodic(MixelExitOwner owner, Duration interval,
    Future<void> Function() callback, {bool immediate = false}) {
  if (immediate) Future<void>.delayed(Duration.zero, () => owner.poll(callback));
  final timer = Timer.periodic(interval, (_) => owner.poll(callback));
  if (owner.stopping) { timer.cancel(); } else { owner.timer = timer; }
  return timer;
}

bool mixelValidAppAcknowledgement(dynamic value, int windowId) =>
    windowId >= 0 && value is Map && value.length == 2 &&
    value['window_id'] is int && value['window_id'] == windowId &&
    value['cleanup'] == 'complete';

// Required exits own one native message: its result is sent before native Quit
// is queued. There is no armed bypass marker waiting for a second message.
class MixelWidgetsFlutterBinding extends WidgetsFlutterBinding {
  @override
  Future<ui.AppExitResponse> exitApplication(ui.AppExitType exitType,
      [int exitCode = 0]) async {
    if (exitType == ui.AppExitType.cancelable &&
        await _mixelHost.invokeMethod<bool>('mixelExitAvailable') != true) {
      return super.exitApplication(exitType, exitCode);
    }
    final response = await _mixelHost.invokeMapMethod<String, dynamic>(
        exitType == ui.AppExitType.required
            ? 'mixelExitRequired' : 'mixelExitRequested');
    return response?['response'] == 'exit'
        ? ui.AppExitResponse.exit : ui.AppExitResponse.cancel;
  }
}

Future<void> mixelRequestAppExit() async {
  await WidgetsBinding.instance.exitApplication(ui.AppExitType.cancelable);
}

Future<void> mixelInstallMainExitHandler(
    Future<void> Function() prepare) async {
  Future<void>? preparation;
  _mixelHost.setMethodCallHandler((call) async {
    if (call.method != mixelAppExitPrepare) {
      throw MissingPluginException('Unknown main application exit method');
    }
    // Cache before starting so a synchronous reentry cannot duplicate resources.
    if (preparation == null) {
      final completion = Completer<void>();
      preparation = completion.future;
      Future<void>.sync(prepare).then(completion.complete,
          onError: completion.completeError);
    }
    try {
      await preparation;
      return <String, Object>{'window_id': 0, 'cleanup': 'complete'};
    } catch (error) {
      debugPrint('Mixel application resource cleanup failed: $error');
      return <String, Object>{'window_id': 0, 'cleanup': 'failed'};
    }
  });
  await _mixelHost.invokeMethod<void>('mixelExitReady');
}
"""

COORDINATOR = r"""
// Main-engine coordinator. Callbacks and deadlines run on the platform thread.
// Public Flutter channels retain every engine until their replies unwind.
final class MixelTerminationCoordinator {
    enum Phase { case idle, querying, preparing, approved }
    private(set) var phase = Phase.idle
    private var generation: UInt64 = 0
    private var cancelDeadline: (() -> Void)?
    private var results: [(String) -> Void] = []
    let deferCall: (@escaping () -> Void) -> Void
    let deadline: (TimeInterval, @escaping () -> Void) -> (() -> Void)
    let query: (@escaping (Any?) -> Void) -> Void
    let prepare: (@escaping (Any?) -> Void) -> Void
    let terminate: () -> Void
    let log: (String) -> Void

    init(deferCall: @escaping (@escaping () -> Void) -> Void,
         deadline: @escaping (TimeInterval, @escaping () -> Void) -> (() -> Void),
         query: @escaping (@escaping (Any?) -> Void) -> Void,
         prepare: @escaping (@escaping (Any?) -> Void) -> Void,
         terminate: @escaping () -> Void, log: @escaping (String) -> Void) {
        self.deferCall = deferCall; self.deadline = deadline
        self.query = query; self.prepare = prepare
        self.terminate = terminate; self.log = log
    }

    func request(_ result: ((String) -> Void)? = nil) {
        if phase == .approved { result?("exit"); return }
        if let result = result { results.append(result) }
        guard phase == .idle else { return }
        phase = .querying
        generation &+= 1
        let token = generation
        cancelDeadline = deadline(15) { [weak self] in
            guard let self = self, self.generation == token,
                  self.phase == .querying else { return }
            self.log("Mixel Quit query timed out; application kept running.")
            self.cancel(token)
        }
        deferCall { [weak self] in
            guard let self = self, self.generation == token,
                  self.phase == .querying else { return }
            self.query { [weak self] reply in
                self?.deferCall { [weak self] in self?.queried(reply, token) }
            }
        }
    }

    private func queried(_ reply: Any?, _ token: UInt64) {
        guard generation == token, phase == .querying else { return }
        guard let value = reply as? [String: Any], value.count == 1,
              let response = value["response"] as? String,
              response == "exit" || response == "cancel" else {
            log("Mixel Quit query failed; application kept running.")
            cancel(token); return
        }
        if response == "cancel" { cancel(token); return }
        phase = .preparing
        cancelDeadline?()
        cancelDeadline = deadline(15) { [weak self] in
            guard let self = self, self.generation == token,
                  self.phase == .preparing else { return }
            self.log("Mixel Quit resource cleanup timed out; cleanup incomplete.")
            self.approve(token)
        }
        prepare { [weak self] reply in
            self?.deferCall { [weak self] in
                guard let self = self, self.generation == token,
                      self.phase == .preparing else { return }
                if !Self.complete(reply) {
                    self.log("Mixel Quit resource cleanup failed; cleanup incomplete.")
                }
                self.approve(token)
            }
        }
    }

    private static func complete(_ reply: Any?) -> Bool {
        guard let value = reply as? [String: Any], value.count == 2,
              let id = value["window_id"] as? NSNumber,
              CFGetTypeID(id) != CFBooleanGetTypeID(),
              ["i", "q"].contains(String(cString: id.objCType)),
              id.int64Value == 0,
              value["cleanup"] as? String == "complete" else { return false }
        return true
    }

    private func cancel(_ token: UInt64) {
        guard generation == token else { return }
        cancelDeadline?(); cancelDeadline = nil
        phase = .idle
        let callbacks = results; results.removeAll()
        callbacks.forEach { $0("cancel") }
    }

    private func approve(_ token: UInt64) {
        guard generation == token, phase != .approved else { return }
        phase = .approved
        cancelDeadline?(); cancelDeadline = nil
        let callbacks = results; results.removeAll()
        callbacks.forEach { $0("exit") }
        // The query, resource and requesting-engine replies have returned before
        // NSApplicationWillTerminate can shut down their messengers.
        deferCall { [weak self] in
            guard let self = self, self.phase == .approved else { return }
            self.terminate()
        }
    }

    func required(_ result: @escaping (String) -> Void) {
        if phase == .approved { result("exit"); return }
        generation &+= 1
        cancelDeadline?(); cancelDeadline = nil
        // Required supersedes every older query/cleanup reply without querying
        // or waiting for resources, as Flutter's required exit contract states.
        phase = .idle
        results.append(result)
        approve(generation)
    }
}
"""

DELEGATE = r"""
    private var mixelExitReady = false
    private lazy var mixelExit = MixelTerminationCoordinator(
        deferCall: { operation in DispatchQueue.main.async(execute: operation) },
        deadline: { seconds, operation in
            let work = DispatchWorkItem(block: operation)
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
            return { work.cancel() }
        },
        query: { [weak self] completion in
            guard let controller = self?.mainFlutterWindow?.contentViewController
                    as? FlutterViewController else {
                completion(nil); return
            }
            FlutterMethodChannel(name: "flutter/platform",
                binaryMessenger: controller.engine.binaryMessenger,
                codec: FlutterJSONMethodCodec.sharedInstance())
                .invokeMethod("System.requestAppExit", arguments: nil, result: completion)
        },
        prepare: { [weak self] completion in
            guard let controller = self?.mainFlutterWindow?.contentViewController
                    as? FlutterViewController else {
                completion(nil); return
            }
            FlutterMethodChannel(name: "org.rustdesk.rustdesk/host",
                binaryMessenger: controller.engine.binaryMessenger)
                .invokeMethod("mixel_prepare_application_exit", arguments: nil,
                              result: completion)
        },
        terminate: { [weak self] in
            // Detach only this process's Flutter window delegates before the
            // SDK's WillTerminate observers invalidate their engines.
            for window in NSApplication.shared.windows
                where window.contentViewController is FlutterViewController {
                window.delegate = nil
            }
            (self?.mainFlutterWindow as? MainFlutterWindow)?.mixelFinishNativeResources()
            NSApplication.shared.terminate(self)
        }, log: { message in NSLog("%@", message) })

    override func applicationShouldTerminate(_ sender: NSApplication)
        -> NSApplication.TerminateReply {
        if mixelExit.phase == .approved { return .terminateNow }
        // Before main Dart readiness, preserve the SDK's immediate exit and
        // install/connection-manager behavior.
        guard mixelExitReady else { return super.applicationShouldTerminate(sender) }
        mixelExit.request()
        return .terminateCancel
    }

    func mixelHandleExit(_ call: FlutterMethodCall, registrar: FlutterPluginRegistrar,
                        result: @escaping FlutterResult) {
        switch call.method {
        case "mixelExitAvailable":
            result(mixelExitReady)
        case "mixelExitReady":
            guard let main = mainFlutterWindow?.contentViewController as? FlutterViewController,
                  registrar.messenger as AnyObject === main.engine.binaryMessenger as AnyObject else {
                result(FlutterError(code: "wrong_exit_owner",
                    message: "Only the main engine can own application cleanup", details: nil))
                return
            }
            mixelExitReady = true
            result(nil)
        case "mixelExitRequired":
            mixelExit.required { response in result(["response": response]) }
        case "mixelExitRequested":
            guard mixelExitReady else {
                result(["response": "cancel"]); return
            }
            mixelExit.request { response in result(["response": response]) }
        default:
            result(FlutterMethodNotImplemented)
        }
    }
"""


def once(source, before, after):
    if source.count(before) != 1:
        raise RuntimeError("Changed or ambiguous Mac application-exit boundary")
    return source.replace(before, after, 1)


def brace_end(text, start):
    opening = text.index('{', start)
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == '{': depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0: return index
    raise RuntimeError('Unterminated pinned application-exit source')


def register_after_handles(text, initializer):
    anchor = '    if (isMacOS) _mixelExit.register(() async {'
    if text.count(anchor) != 1:
        raise RuntimeError('Changed pinned application-exit owner registration')
    start = text.index(anchor)
    end = brace_end(text, start) + len(');') + 1
    if text[end:end+1] != '\n': raise RuntimeError('Changed owner callback terminator')
    chunk = text[start:end]
    chunk = chunk.replace('if (isMacOS) _mixelExit.register',
                          'if (isMacOS) {\n      _mixelExit.register', 1) + '\n    }'
    text = text[:start] + text[end+1:]
    end = brace_end(text, text.index(initializer))
    return text[:end-2] + chunk + '\n  ' + text[end:]


def transform(name, data):
    if digest(data) != FILES[name]:
        raise RuntimeError("Changed Mac application-exit source: " + name)
    text = data.decode('utf-8')
    stem = Path(name).stem
    if stem == 'AppDelegate':
        text = once(text, 'import FlutterMacOS\n', 'import FlutterMacOS\nimport CoreFoundation\n')
        text = once(text, '    var launched = false;\n', '    var launched = false;\n' + DELEGATE)
        return (text + COORDINATOR).encode()
    if stem == 'MainFlutterWindow':
        text = once(text, '                case "terminate":\n                    NSApplication.shared.terminate(self)\n                    result(nil)', '''                case "mixelExitAvailable", "mixelExitReady", "mixelExitRequired", "mixelExitRequested":
                    guard let delegate = NSApplication.shared.delegate as? AppDelegate else {
                        result(FlutterMethodNotImplemented); return
                    }
                    delegate.mixelHandleExit(call, registrar: registrar, result: result)
                case "terminate":
                    result(nil)
                    DispatchQueue.main.async { NSApplication.shared.terminate(self) }''')
        text = once(text, '    public func setMethodHandler(registrar: FlutterPluginRegistrar) {', '''    public func mixelFinishNativeResources() {
        disableNativeRelativeMouseMode()
    }

    public func setMethodHandler(registrar: FlutterPluginRegistrar) {''')
        return text.encode()
    if stem == 'main':
        text = once(text, '  WidgetsFlutterBinding.ensureInitialized();', '''  if (isMacOS) {
    MixelWidgetsFlutterBinding();
  } else {
    WidgetsFlutterBinding.ensureInitialized();
  }''')
        text = once(text, 'class _AppState extends State<App> with WidgetsBindingObserver {', '''class _AppState extends State<App> with WidgetsBindingObserver {
  final _mixelExit = MixelExitOwner();''')
        text = once(text, '    WidgetsBinding.instance.addObserver(this);', '''    WidgetsBinding.instance.addObserver(this);
    if (isMacOS) {
      final brightnessHandler = WidgetsBinding.instance.platformDispatcher.onPlatformBrightnessChanged;
      _mixelExit.register(() async {
      WidgetsBinding.instance.removeObserver(this);
        if (identical(WidgetsBinding.instance.platformDispatcher.onPlatformBrightnessChanged,
            brightnessHandler)) {
          WidgetsBinding.instance.platformDispatcher.onPlatformBrightnessChanged = null;
        }
      });
      if (desktopType == DesktopType.main) {
        WidgetsBinding.instance.addPostFrameCallback((_) async {
          if (_mixelExit.stopping || !mounted) return;
          await mixelInstallMainExitHandler(rustDeskWinManager.mixelPrepareApplicationExit);
        });
      }
    }''')
        text = once(text, '    WidgetsBinding.instance.removeObserver(this);\n    super.dispose();', '''    if (isMacOS) _mixelExit.stop().ignore();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();''')
    elif stem == 'common':
        anchor = '  if (rustDeskWinManager.getActiveWindows().isEmpty) {\n'
        text = once(text, anchor, anchor + '''    if (isMacOS) {
      if (rustDeskWinManager.mixelApplicationExitStarted) return;
      await mixelRequestAppExit();
      return;
    }
''')
    elif stem == 'multi_window_manager':
        text = once(text, '  final Set<Future<void>> _mixelPendingCreations = {};', '''  final Set<Future<void>> _mixelPendingCreations = {};
  final Set<int> _mixelExitPendingWindowIds = {};''')
        text = once(text, '''      final ownedWindowId = windowController.windowId;
      await mixelRunCleanupSteps([''', '''      final ownedWindowId = windowController.windowId;
      if (mixelApplicationExitStarted) {
        // Keep a newly created originating engine alive for its exit reply.
        _mixelExitPendingWindowIds.add(ownedWindowId);
        finished.complete();
        throw StateError('Support window creation was cancelled during application exit');
      }
      await mixelRunCleanupSteps([''')
        text = once(text, '    DesktopMultiWindow.setMethodHandler(handler);', '''    if (!isMacOS) {
      DesktopMultiWindow.setMethodHandler(handler);
      return;
    }
    DesktopMultiWindow.setMethodHandler(handler == null ? null : (call, fromWindowId) async {
      if (isMacOS && call.method == mixelAppWindowPrepare) {
        await Future.wait<void>([
          Future<void>.sync(() async {
            final acknowledgement = await handler(
                const MethodCall(mixelPrepareWindowClose), fromWindowId);
            if (!mixelValidCloseAcknowledgement(acknowledgement, kWindowId!)) {
              throw StateError('Invalid support-window resource acknowledgement');
            }
          }),
          mixelDrainExitOwners(),
        ]);
        return <String, Object>{'window_id': kWindowId!, 'cleanup': 'complete'};
      }
      return handler(call, fromWindowId);
    });''')
        text = once(text, '  Future<void> closeAllSubWindows() async {', '''  final _mixelAppExitOnce = MixelCleanupOnce();
  bool get mixelApplicationExitStarted => _mixelAppExitOnce.started;

  Future<void> mixelPrepareApplicationExit() => _mixelAppExitOnce.run(() async {
    _mixelClosingSubWindows = true;
    Object? firstError;
    StackTrace? firstStack;
    Future<void> attempt(Future<void> Function() operation) async {
      try { await operation(); } catch (error, stack) {
        firstError ??= error;
        firstStack ??= stack;
      }
    }
    // Pending creation cleanup and main resource owners all begin immediately.
    await Future.wait<void>([
      attempt(() => Future.wait<void>(_mixelPendingCreations.toList())
          .timeout(const Duration(milliseconds: 4500))),
      attempt(mixelDrainExitOwners),
    ]);
    final ids = <int>{
      ..._mixelExitPendingWindowIds,
      for (final type in WindowType.values)
        if (type != WindowType.Main) ..._findWindowsByType(type),
    };
    // Resource-only fanout keeps even a requesting secondary engine alive.
    await Future.wait<void>(ids.map((id) => attempt(() async {
      final acknowledgement = await DesktopMultiWindow.invokeMethod(id,
          mixelAppWindowPrepare).timeout(const Duration(milliseconds: 5000));
      if (!mixelValidAppAcknowledgement(acknowledgement, id)) {
        throw StateError('Invalid application-window cleanup acknowledgement');
      }
    })));
    if (firstError != null) Error.throwWithStackTrace(firstError!, firstStack!);
  });

  Future<void> closeAllSubWindows() async {''')
    elif stem == 'desktop_home_page':
        text = once(text, '  Timer? _updateTimer;', '  Timer? _updateTimer;\n  final _mixelExit = MixelExitOwner();')
        text = once(text, '  _updateWindowSize() {', '''  _updateWindowSize() {
    if (isMacOS && (_mixelExit.stopping || !mounted)) return;''')
        text = once(text, '''      WidgetsBinding.instance.addPostFrameCallback((_) {
        _updateWindowSize();
      });''', '''      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (isMacOS && (_mixelExit.stopping || !mounted)) return;
        _updateWindowSize();
      });''')
        text = once(text, '    _updateTimer = periodic_immediate(const Duration(seconds: 1), () async {', '''    if (isMacOS) _mixelExit.register(() async {
      rustDeskWinManager.unregisterActiveWindowListener(onActiveWindowChanged);
      WidgetsBinding.instance.removeObserver(this);
      await _uniLinksSubscription?.cancel();
      if (Get.isRegistered<RxBool>(tag: 'stop-service') &&
          identical(Get.find<RxBool>(tag: 'stop-service'), svcStopped)) {
        Get.delete<RxBool>(tag: 'stop-service');
      }
    });
    Future<void> updateStatus() async {''')
        text = once(text, '      await gFFI.serverModel.fetchID();', '''      if (isMacOS && (_mixelExit.stopping || !mounted)) return;
      await gFFI.serverModel.fetchID();
      if (isMacOS && (_mixelExit.stopping || !mounted)) return;''')
        text = once(text, '      final error = await bind.mainGetError();', '''      final error = await bind.mainGetError();
      if (isMacOS && (_mixelExit.stopping || !mounted)) return;''')
        text = once(text, '      final v = await mainGetBoolOption(kOptionStopService);', '''      final v = await mainGetBoolOption(kOptionStopService);
      if (isMacOS && (_mixelExit.stopping || !mounted)) return;''')
        text = once(text, '''          Future.microtask(() async {
            if ((await osxCanRecordAudio() ==
                PermissionAuthorizeType.authorized)) {
              watchIsCanRecordAudio = false;
              setState(() {});
            }
          });''', '''          final audioPermission = await osxCanRecordAudio();
          if (_mixelExit.stopping || !mounted) return;
          if (audioPermission == PermissionAuthorizeType.authorized) {
            watchIsCanRecordAudio = false;
            setState(() {});
          }''')
        text = once(text, "    Get.put<RxBool>(svcStopped, tag: 'stop-service');", '''    _updateTimer = isMacOS
        ? mixelExitPeriodic(_mixelExit, const Duration(seconds: 1),
            updateStatus, immediate: true)
        : periodic_immediate(const Duration(seconds: 1), updateStatus);
    Get.put<RxBool>(svcStopped, tag: 'stop-service');''')
        # The one changed callback boundary is the exact end of the retained body.
        text = once(text, "    });\n    _updateTimer = isMacOS", "    }\n    _updateTimer = isMacOS")
        text = once(text, '''    _uniLinksSubscription?.cancel();
    Get.delete<RxBool>(tag: 'stop-service');
    _updateTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);''', '''    if (isMacOS) {
      _mixelExit.stop().ignore();
    } else {
      _uniLinksSubscription?.cancel();
      Get.delete<RxBool>(tag: 'stop-service');
      _updateTimer?.cancel();
      WidgetsBinding.instance.removeObserver(this);
    }''')
    elif stem == 'desktop_tab_page':
        text = once(text, '  final tabController = DesktopTabController(tabType: DesktopTabType.main);', '''  final tabController = DesktopTabController(tabType: DesktopTabType.main);
  final _mixelExit = MixelExitOwner();''')
        text = once(text, '    RemoteCountState.init();', '''    if (isMacOS) _mixelExit.register(() async {
      if (Get.isRegistered<DesktopTabController>() &&
          identical(Get.find<DesktopTabController>(), tabController)) {
        Get.delete<DesktopTabController>();
      }
    });
    RemoteCountState.init();''')
        text = once(text, '    Get.delete<DesktopTabController>();\n\n    super.dispose();', '''    if (isMacOS) {
      _mixelExit.stop().ignore();
    } else {
      Get.delete<DesktopTabController>();
    }
    super.dispose();''')
    elif stem == 'tabbar_widget':
        text = once(text, '  Timer? _macOSCheckRestoreTimer;', '''  Timer? _macOSCheckRestoreTimer;
  Timer? _mixelInitialTimer;
  Timer? _mixelCloseTimer;
  final _mixelExit = MixelExitOwner();''')
        text = once(text, '    DesktopMultiWindow.addListener(this);', '''    if (isMacOS) _mixelExit.register(() async {
      DesktopMultiWindow.removeListener(this);
      windowManager.removeListener(this);
      _macOSCheckRestoreTimer?.cancel();
      _macOSCheckRestoreTimer = null;
      _mixelInitialTimer?.cancel();
      _mixelCloseTimer?.cancel();
      _mixelInitialTimer = null;
      _mixelCloseTimer = null;
    });
    DesktopMultiWindow.addListener(this);''')
        text = once(text, '    DesktopMultiWindow.removeListener(this);\n    windowManager.removeListener(this);', '''    if (isMacOS) _mixelExit.stop().ignore();
    DesktopMultiWindow.removeListener(this);
    windowManager.removeListener(this);''')
        delayed_start = text.index('    Future.delayed(Duration(milliseconds: 500), () {')
        delayed_end = text.index('\n    });', delayed_start) + len('\n    });')
        original_delayed = text[delayed_start:delayed_end]
        corrected_delayed = '''    if (isMacOS) {
      _mixelInitialTimer = Timer(const Duration(milliseconds: 500), () {
        _mixelExit.poll(() async {
          if (_mixelExit.stopping || !mounted) return;
          final maximized = isMainWindow
              ? await windowManager.isMaximized()
              : await WindowController.fromWindowId(kWindowId!).isMaximized();
          if (_mixelExit.stopping || !mounted) return;
          if (stateGlobal.isMaximized.value != maximized) {
            WidgetsBinding.instance.addPostFrameCallback((_) {
              if (_mixelExit.stopping || !mounted) return;
              setState(() => stateGlobal.setMaximized(maximized));
            });
          }
        });
      });
    } else {
''' + original_delayed + '\n    }'
        text = once(text, original_delayed, corrected_delayed)
        text = once(text, '''      _macOSCheckRestoreCounter = 0;
      _macOSCheckRestoreTimer =
          Timer.periodic(Duration(milliseconds: 30), (timer) async {
        _macOSCheckRestoreCounter++;
        if (!await checkFullscreen() || _macOSCheckRestoreCounter >= 30) {
          _macOSCheckRestoreTimer?.cancel();
          _macOSCheckRestoreTimer = null;
          Timer(Duration(milliseconds: 700), () async => await closeFunc());
        }
      });''', '''      if (_mixelExit.stopping || !mounted) return;
      _macOSCheckRestoreCounter = 0;
      _macOSCheckRestoreTimer?.cancel();
      _mixelCloseTimer?.cancel();
      _macOSCheckRestoreTimer =
          Timer.periodic(const Duration(milliseconds: 30), (timer) {
        _mixelExit.poll(() async {
          if (_mixelExit.stopping || !mounted) return;
          _macOSCheckRestoreCounter++;
          final fullscreen = await checkFullscreen();
          if (_mixelExit.stopping || !mounted || !identical(_macOSCheckRestoreTimer, timer)) return;
          if (!fullscreen || _macOSCheckRestoreCounter >= 30) {
            timer.cancel();
            _macOSCheckRestoreTimer = null;
            _mixelCloseTimer = Timer(const Duration(milliseconds: 700), () {
              _mixelExit.poll(() async {
                if (_mixelExit.stopping || !mounted) return;
                await closeFunc();
              });
            });
          }
        });
      });''')
    elif stem == 'native_model':
        text = once(text, "import 'dart:convert';", "import 'dart:async';\nimport 'dart:convert';")
        text = once(text, '  StreamEventHandler? _eventCallback;', '''  StreamEventHandler? _eventCallback;
  StreamSubscription<String>? _mixelGlobalSubscription;
  final _mixelExit = MixelExitOwner();''')
        text = once(text, '    sink.listen((message) {\n      () async {', '''    if (isMacOS) _mixelExit.register(() async {
      await _mixelGlobalSubscription?.cancel();
      _mixelGlobalSubscription = null;
      _eventCallback = null;
      _eventHandlers.clear();
    });
    final subscription = sink.listen((message) {
      Future<void> dispatch() async {''')
        text = once(text, '      }();\n    });\n  }\n\n  void setEventCallback', '''      }
      if (isMacOS) {
        _mixelExit.poll(dispatch);
      } else {
        dispatch();
      }
    });
    if (isMacOS) _mixelGlobalSubscription = subscription;
  }

  void setEventCallback''')
    elif stem == 'server_model':
        text = once(text, '  WeakReference<FFI> parent;\n\n  ServerModel(this.parent)', '''  WeakReference<FFI> parent;
  final _mixelExit = MixelExitOwner();

  ServerModel(this.parent)''')
        text = once(text, '    if (!isTest) {\n      Future.delayed', '''    if (isMacOS) {
      _mixelExit.register(() async {
        cmHiddenTimer?.cancel();
        cmHiddenTimer = null;
      });
    }
    if (!isTest) {
      Future.delayed''')
        text = once(text, '    timerCallback() async {', '''    timerCallback() async {
      if (isMacOS && _mixelExit.stopping) return;''')
        text = once(text, "      final statusNum = connectionStatus['status_num'] as int;", '''      if (isMacOS && _mixelExit.stopping) return;
      final statusNum = connectionStatus['status_num'] as int;''')
        text = once(text, '        final res = await bind.cmCheckClientsLength(length: _clients.length);', '''        final res = await bind.cmCheckClientsLength(length: _clients.length);
        if (isMacOS && _mixelExit.stopping) return;''')
        text = once(text, '      updatePasswordModel();', '''      if (isMacOS) {
        await updatePasswordModel();
      } else {
        updatePasswordModel();
      }''')
        text = once(text, '  updatePasswordModel() async {', '''  updatePasswordModel() async {
    if (isMacOS && _mixelExit.stopping) return;''')
        text = once(text, '    /*\n    var hideCm = option2bool', '''    if (isMacOS && _mixelExit.stopping) return;
    /*
    var hideCm = option2bool''')
        text = once(text, '    final oldPwdText = _serverPasswd.text;', '''    if (isMacOS && _mixelExit.stopping) return;
    final oldPwdText = _serverPasswd.text;''')
        text = once(text, '  void _addTab(Client client) {', '''  void _addTab(Client client) {
    if (isMacOS && _mixelExit.stopping) return;''')
        text = once(text, '''    Future.delayed(Duration.zero, () async {
      if (!hideCm) windowOnTop(null);
    });''', '''    if (isMacOS) {
      _mixelExit.poll(() async {
        await Future<void>.delayed(Duration.zero);
        if (_mixelExit.stopping) return;
        if (!hideCm) await windowOnTop(null);
      });
    } else {
      Future.delayed(Duration.zero, () async {
        if (!hideCm) windowOnTop(null);
      });
    }''')
        text = once(text, '''      cmHiddenTimer = Timer(const Duration(seconds: 3), () {
        if (!hideCm) windowManager.minimize();
        cmHiddenTimer = null;
      });''', '''      if (isMacOS) {
        cmHiddenTimer?.cancel();
        cmHiddenTimer = Timer(const Duration(seconds: 3), () {
          cmHiddenTimer = null;
          _mixelExit.poll(() async {
            if (_mixelExit.stopping) return;
            if (!hideCm) await windowManager.minimize();
          });
        });
      } else {
        cmHiddenTimer = Timer(const Duration(seconds: 3), () {
          if (!hideCm) windowManager.minimize();
          cmHiddenTimer = null;
        });
      }''')
        text = once(text, '''        if (await bind.optionSynced()) {
          await timerCallback();
        }''', '''        Future<void> initialize() async {
          if (await bind.optionSynced()) await timerCallback();
        }
        if (isMacOS) {
          _mixelExit.poll(initialize);
        } else {
          await initialize();
        }''')
        text = once(text, '''      Timer.periodic(Duration(milliseconds: 500), (timer) async {
        await timerCallback();
      });''', '''      if (isMacOS) {
        mixelExitPeriodic(_mixelExit, const Duration(milliseconds: 500), timerCallback);
      } else {
        Timer.periodic(Duration(milliseconds: 500), (timer) async {
          await timerCallback();
        });
      }''')
    elif stem == 'ab_model':
        text = once(text, '  WeakReference<FFI> parent;\n\n  AbModel(this.parent)', '''  WeakReference<FFI> parent;
  final _mixelExit = MixelExitOwner();

  AbModel(this.parent)''')
        text = once(text, '    if (desktopType == DesktopType.main) {\n      Timer.periodic(Duration(milliseconds: 500), (timer) async {', '''    if (isMacOS) _mixelExit.register();
    if (desktopType == DesktopType.main) {
      Future<void> pollRecent() async {''')
        text = once(text, '          _syncFromRecent();\n        }\n      });', '''          await _syncFromRecent();
        }
      }
      if (isMacOS) {
        mixelExitPeriodic(_mixelExit, const Duration(milliseconds: 500), pollRecent);
      } else {
        Timer.periodic(Duration(milliseconds: 500), (timer) async {
          if (_timerCounter++ % 6 == 0) {
            if (!gFFI.userModel.isLogin) return;
            if (!listInitialized) return;
            if (!current.initialized || !current.canWrite()) return;
            _syncFromRecent();
          }
        });
      }''')
    else:
        raise RuntimeError('Unknown application-exit target')
    if stem in ('desktop_home_page', 'desktop_tab_page', 'tabbar_widget', 'native_model'):
        anchor = ('_DesktopTabPageState() {' if stem == 'desktop_tab_page' else
                  'void _startListenEvent(' if stem == 'native_model' else 'void initState() {')
        text = register_after_handles(text, anchor)
    return (IMPORT + text).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(repo, name):
    path = repo
    for part in Path(name).parts[:-1]:
        path /= part
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError('Unsafe application-exit parent: ' + name)
    path /= Path(name).name
    attrs = path.lstat()
    if not stat.S_ISREG(attrs.st_mode) or attrs.st_nlink != 1 or attrs.st_size > 500000:
        raise RuntimeError('Nonregular application-exit source: ' + name)
    return path.read_bytes()


def normalize(data):
    normalized = data.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    if data != normalized.replace(b'\n', newline):
        raise RuntimeError('Mixed application-exit line endings')
    return normalized, newline


def validate_previous_cleanup(repo, prepared):
    """Execute the pinned earlier layer's guards before changing its inputs."""
    owned_name = 'flutter/.mixel-dependencies/macos_cleanup'
    utility_name = 'flutter/lib/mixel_support_cleanup.dart'
    # Standalone eleven-file lifecycle fixtures have none of these page files.
    # A full checkout still triggers validation if both owned inputs are deleted.
    page_names = ('remote_page', 'file_manager_page', 'view_camera_page',
                  'port_forward_page', 'remote_tab_page', 'file_manager_tab_page',
                  'view_camera_tab_page', 'port_forward_tab_page', 'terminal_tab_page',
                  'terminal_page', 'terminal_connection_manager')
    triggers = (owned_name, utility_name, *('flutter/lib/desktop/pages/' + name + '.dart'
                                         for name in page_names))
    if not any((repo / name).exists() or (repo / name).is_symlink() for name in triggers):
        return
    helper_path = Path(__file__).resolve().parent / 'patch-support-macos-cleanup.py'
    helper, _ = normalize(regular(helper_path.parent, helper_path.name))
    if digest(helper) != '197bc021abf3cb8563b92dfca30d5ba7f1c93328db4318cf56386baa9cf6a0fb':
        raise RuntimeError('Changed earlier Mac cleanup verifier')
    namespace = {'__name__': 'mixel_previous_cleanup_verifier', '__file__': str(helper_path)}
    exec(compile(helper, str(helper_path), 'exec'), namespace)
    prior_files, prior_regular = namespace['FILES'], namespace['regular']
    owned = repo / owned_name
    for name in (*prior_files, utility_name, owned_name + '/proof.json'):
        namespace['safe_parents'](repo, name)
    if not owned.is_dir() or owned.is_symlink() or not (repo / utility_name).is_file():
        raise RuntimeError('Missing earlier Mac cleanup ownership')
    try:
        # The original helper remains authoritative, including its complete
        # provenance inventory. It may write only this disposable projection.
        with tempfile.TemporaryDirectory(prefix='mixel-exit-cleanup-preflight-') as directory:
            stage = Path(directory)
            for name in ('Cargo.toml', *prior_files, utility_name):
                data = prior_regular(repo / name)
                if name in FILES:
                    _, newline = normalize(data)
                    data = prepared[name][0].replace(b'\n', newline)
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (stage / owned_name).mkdir(parents=True)
            for path in owned.rglob('*'):
                relative = path.relative_to(owned)
                target = stage / owned_name / relative
                if path.is_symlink():
                    raise RuntimeError('Symlink in earlier Mac cleanup inventory')
                if path.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    data = prior_regular(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            with contextlib.redirect_stdout(io.StringIO()):
                namespace['apply'](stage)
    except (OSError, ValueError) as error:
        raise RuntimeError('Invalid earlier Mac cleanup ownership') from error


def apply(repo, restore=False, check=False):
    cargo, _ = normalize(regular(repo, 'Cargo.toml'))
    packages = re.findall(rb'(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)', cargo)
    versions = re.findall(rb'(?m)^version\s*=\s*"([^\"]+)"\s*$', packages[0]) if len(packages) == 1 else []
    if versions != [b'1.4.6']:
        raise RuntimeError('Application-exit correction requires upstream 1.4.6')
    generated = repo / GENERATED
    originals = repo / 'flutter/.mixel-dependencies/macos_application_exit'
    for relative in (GENERATED, 'flutter/.mixel-dependencies/macos_application_exit/source'):
        parent = repo
        for part in Path(relative).parts[:-1]:
            parent /= part
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise RuntimeError('Unsafe application-exit owned parent')
    if restore and not generated.exists() and not generated.is_symlink():
        if not originals.exists():
            identities = {}
            for name in FILES:
                current, _ = normalize(regular(repo, name))
                identities[name] = digest(current)
            if identities not in (FILES, FRESH):
                raise RuntimeError('Noncanonical or mixed application-exit restore inputs')
            return
        for name in FILES:
            current, _ = normalize(regular(repo, name))
            if digest(current) != FILES[name]:
                raise RuntimeError('Missing application-exit utility with noncanonical source')
    if originals.is_symlink():
        raise RuntimeError('Unsafe application-exit provenance')
    prepared = {}
    states = set()
    if originals.exists():
        expected = set(FILES)
        inventory = set()
        directories = set()
        for path in originals.rglob('*'):
            if path.is_symlink(): raise RuntimeError('Unsafe application-exit provenance')
            if not (path.is_dir() or path.is_file()):
                raise RuntimeError('Nonregular application-exit provenance')
            if path.is_dir(): directories.add(path.relative_to(originals).as_posix())
            if path.is_file(): inventory.add(path.relative_to(originals).as_posix())
        expected_directories = {parent.as_posix() for name in FILES
            for parent in Path(name).parents if str(parent) != '.'}
        if inventory != expected or directories != expected_directories:
            raise RuntimeError('Changed application-exit provenance inventory')
    else:
        if restore: raise RuntimeError('Missing application-exit provenance')
    for name in FILES:
        current, newline = normalize(regular(repo, name))
        original = regular(originals, name) if originals.exists() else current
        if digest(original) != FILES[name]:
            raise RuntimeError('Changed application-exit original source: ' + name)
        corrected = transform(name, original)
        if current not in (original, corrected):
            raise RuntimeError('Changed application-exit current source: ' + name)
        states.add('original' if current == original else 'corrected')
        prepared[name] = original, (original if restore else corrected).replace(b'\n', newline)
    if len(states) != 1:
        raise RuntimeError('Mixed application-exit pipeline targets')
    utility = UTILITY.encode()
    if generated.exists() or generated.is_symlink():
        existing, _ = normalize(regular(repo, GENERATED))
        if existing != utility: raise RuntimeError('Changed application-exit utility')
        if not originals.exists() or states != {'corrected'}:
            raise RuntimeError('Application-exit utility does not match target ownership')
    elif originals.exists() and not restore:
        # Restored canonical inputs keep owned pristine copies for the next pass.
        if any(normalize(regular(repo, name))[0] != prepared[name][0] for name in FILES):
            raise RuntimeError('Missing application-exit utility')
    validate_previous_cleanup(repo, prepared)
    if check:
        return
    if not originals.exists():
        for name, (original, _) in prepared.items():
            target = originals / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
    for name, (_, corrected) in prepared.items():
        if regular(repo, name) != corrected: (repo / name).write_bytes(corrected)
    if restore:
        if generated.exists(): generated.unlink()
    else:
        generated.write_bytes(utility)
    print('   coordinated bounded main-engine Mac application exit' if not restore else
          '   restored exact canonical application-exit pipeline inputs')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('checkout', type=Path)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    apply(args.checkout.resolve(), args.restore, args.check)
