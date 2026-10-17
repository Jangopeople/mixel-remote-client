#!/usr/bin/env python3
"""Replay exact composed support dispatch/exit methods with synthetic Dart IO.

The app input is read only. This fixture never starts an app, native channel,
service, or IPC listener; the bind and stream facade are in-memory substitutes.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_UTILITY_SHA = "37b2f956dbbf07e4129c7b0db1d695d11dbdb1952699e4124238b9befcfbf06e"
NATIVE = "flutter/lib/models/native_model.dart"


def block(text, anchor):
    if text.count(anchor) != 1:
        raise AssertionError("Missing or duplicate exact method: " + anchor)
    begin = text.index(anchor)
    opening = text.index("{", begin)
    depth = 1
    end = opening + 1
    while depth:
        if end == len(text):
            raise AssertionError("Unterminated exact method: " + anchor)
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]


def once(text, old, new):
    if text.count(old) != 1:
        raise AssertionError("Changed negative reconstruction boundary")
    return text.replace(old, new)


SCAFFOLD = r'''
import 'dart:async';
import 'dart:convert';
import 'dart:math';

bool isMacOS = true;
enum DesktopType { main, remote }
DesktopType desktopType = DesktopType.main;
const kAppTypeDesktopRemote = 'remote';
const kWindowId = 0;
typedef StreamEventHandler = Future<void> Function(Map<String, dynamic>);
typedef HandleEvent = Future<void> Function(Map<String, dynamic>);
final messages = <String>[];
void debugPrint(String message) => messages.add(message);
List<String>? urlLinkToCmdArgs(Uri uri) =>
    uri.scheme == 'mixel-remote' && uri.host == 'support'
      ? ['--support-invite'] : null;
class SyntheticBind {
  final values = <String>[];
  Future<void> mainSetOption({required String key, required String value}) async {
    if (key != 'mixel-support-url-callback-ready-v1') throw StateError('Wrong key');
    values.add(value);
  }
}
final bind = SyntheticBind();
class RustdeskImpl {
  late final StreamController<String> controller;
  var starts = 0;
  var cancellations = 0;
  RustdeskImpl({Future<void> Function()? cancellation}) {
    controller = StreamController<String>(sync: true, onCancel: () async {
      cancellations++;
      await cancellation?.call();
    });
  }
  Stream<String> startGlobalEventStream({required String appType}) {
    starts++;
    return controller.stream;
  }
}
'''

CONTROLS = r'''
void check(bool value, String reason) { if (!value) throw StateError(reason); }
Future<void> settle() async {
  for (var index = 0; index < 8; index++) await Future<void>.delayed(Duration.zero);
}
final supportEvent = <String, dynamic>{
  'name': 'on_url_scheme_received', 'url': 'mixel-remote://support/?fixture=synthetic'};
void start(ActualNative native, RustdeskImpl core) {
  native._startListenEvent(core);
  setMixelSupportUrlUiReady(true);
}
Future<void> main(List<String> arguments) async {
  final negative = arguments.first == 'negative';
  final legacyRegistration = arguments.first == 'legacy-registration';
  final mode = arguments.last;
  if (mode == 'held') {
    final native = ActualNative(); final core = RustdeskImpl(); start(native, core);
    core.controller.add(json.encode(supportEvent)); await settle();
    check(native._mixelSupportUrlPendingEvent != null, 'Support event was not deferred');
    final held = Completer<void>(); var started = 0; var released = false;
    native.setEventCallback((event) async {
      check(event['url'] == supportEvent['url'], 'Pending support payload changed');
      started++; await held.future; released = true;
    });
    check(started == 1 && !released, 'Maintenance dispatch did not suspend');
    check(native._mixelExit._pending.length == (negative ? 0 : 1), 'Tracking boundary changed');
    var drained = false;
    final drain = mixelDrainExitOwners().then((_) => drained = true);
    await settle();
    check(drained == negative && !released, 'Drain did not distinguish detached/tracked dispatch');
    held.complete(); await drain; await settle();
    check(drained && released, 'Held dispatch did not finish after release');
    await core.controller.close();
    print(negative ? 'PASS: negative exact detached maintenance reproduces premature drain.'
      : 'PASS: exact tracked pending support dispatch holds drain until callback release.');
  } else if (mode == 'callback-error') {
    final native = ActualNative(); final core = RustdeskImpl(); start(native, core);
    core.controller.add(json.encode(supportEvent)); await settle();
    native.setEventCallback((_) async { throw StateError('synthetic-bearer'); });
    await settle(); await mixelDrainExitOwners();
    check(messages.length == 1 && messages.single == 'Mixel support URL dispatch failed.',
      'Original safe callback logging or error handling changed');
    await core.controller.close();
    print('PASS: exact pending callback failure retains safe logging and successful owner drain.');
  } else if (mode == 'after-stop') {
    final native = ActualNative(); final core = RustdeskImpl(); start(native, core);
    await native._mixelExit.stop();
    var dispatched = 0;
    // Replay a stale maintenance call with a retained callback/pending snapshot.
    native._eventCallback = (_) async { dispatched++; };
    native._mixelSupportUrlPendingEvent = Map.of(supportEvent);
    native._mixelSupportUrlPendingAge = Stopwatch()..start();
    native._mixelSupportUrlPendingGeneration = mixelSupportUrlGeneration;
    _mixelSupportUrlStreamReady = true; _mixelSupportUrlUiReady = true;
    _mixelSupportUrlCallbackReady = true;
    native._maintainMixelSupportUrlPending(true); await settle();
    check(dispatched == (negative ? 1 : 0), 'Stopped maintenance dispatch classification changed');
    check(native._mixelSupportUrlPendingEvent == null &&
      native._mixelSupportUrlPendingAge == null && native._mixelSupportUrlPendingGeneration == null,
      'Stopped maintenance retained support state');
    await core.controller.close();
    print(negative ? 'PASS: negative exact maintenance reproduces dispatch after stop.'
      : 'PASS: exact maintenance after stop drops pending support state without dispatch.');
  } else if (mode == 'support-only') {
    final native = ActualNative(); final core = RustdeskImpl(); start(native, core);
    final ordinary = <String, dynamic>{'name': 'ordinary-event', 'ordinary': 7};
    check(!native._deferMixelSupportUrlEvent(ordinary) &&
      native._mixelSupportUrlPendingEvent == null, 'Ordinary event entered support pending queue');
    check(native._deferMixelSupportUrlEvent({...supportEvent, 'extra': 'discard'}),
      'Validated support event was not retained');
    final received = <Map<String, dynamic>>[];
    native.setEventCallback((event) async { received.add(event); }); await settle();
    check(received.length == 1 && received.single.length == 2 &&
      received.single['url'] == supportEvent['url'], 'Support pending projection changed');
    core.controller.add(json.encode(ordinary)); await settle();
    check(received.length == 2 && received.last['ordinary'] == 7 &&
      native._mixelSupportUrlPendingEvent == null, 'Ordinary dispatch fields or queue behavior changed');
    await mixelDrainExitOwners(); await core.controller.close();
    print('PASS: pending maintenance retains only validated support projection; ordinary dispatch remains complete.');
  } else if (mode == 'generation') {
    final old = ActualNative(); final oldCore = RustdeskImpl(); start(old, oldCore);
    var oldCalls = 0; var newCalls = 0;
    old.setEventCallback((_) async { oldCalls++; });
    final oldGeneration = mixelSupportUrlGeneration;
    // Sync stream dispatch reaches the actual asynchronous tryHandle boundary.
    oldCore.controller.add(json.encode(supportEvent));
    final current = ActualNative(); final newCore = RustdeskImpl(); start(current, newCore);
    current.setEventCallback((_) async { newCalls++; });
    final currentGeneration = mixelSupportUrlGeneration;
    check(currentGeneration != oldGeneration, 'Replacement generation did not change');
    await settle();
    check(oldCalls == 0 && newCalls == 0, 'In-flight old event crossed async generation replacement');
    oldCore.controller.add(json.encode(supportEvent)); await settle();
    check(oldCalls == 0, 'Old event crossed pre-dispatch generation fence');
    final publications = bind.values.length;
    await old._mixelExit.stop(); await settle();
    check(mixelSupportUrlCanReceive && bind.values.length == publications,
      'Stale stop callback cleared or published replacement readiness');
    newCore.controller.add(json.encode(supportEvent)); await settle();
    check(newCalls == 1, 'Current support generation did not remain usable');
    await mixelDrainExitOwners(); await oldCore.controller.close(); await newCore.controller.close();
    print('PASS: exact async/pre-dispatch generation fences and stale stopper preserve current readiness.');
  } else if (mode == 'same-instance') {
    final native = ActualNative(); final cancellation = Completer<void>();
    final first = RustdeskImpl(cancellation: () => cancellation.future);
    final replacement = RustdeskImpl(); start(native, first);
    var calls = 0; native.setEventCallback((_) async { calls++; });
    final firstGeneration = mixelSupportUrlGeneration;
    // The original handler is suspended at its exact tryHandle await while
    // the same ActualNative receives a new stream and generation.
    first.controller.add(json.encode(supportEvent));
    Object? replacementError;
    try { start(native, replacement); } catch (error) { replacementError = error; }
    final currentGeneration = mixelSupportUrlGeneration;
    check(currentGeneration != firstGeneration && replacement.starts == 1,
      'Same-instance replacement did not install a new generation');
    await settle();
    check(calls == 0, 'Same-instance stale event crossed asynchronous generation fence');
    check(_mixelExitOwners.length == 1 && native._mixelExit._stoppers.length == 1,
      'Same instance acquired another exit owner or stopper');
    if (legacyRegistration) {
      check(replacementError is StateError && replacementError.toString() ==
        'Bad state: Application resource owner can be registered only once',
        'Exact original duplicate-registration failure was not reproduced');
      check(first.cancellations == 0 && native._mixelExit._pending.isEmpty,
        'Legacy registration unexpectedly owns prior stream cancellation');
      replacement.controller.add(json.encode(supportEvent)); await settle();
      check(calls == 1 && mixelSupportUrlCanReceive,
        'Original failure did not occur after replacement stream/readiness installation');
      await native._mixelExit.stop(); await settle();
      check(mixelSupportUrlCanReceive,
        'Original captured-generation stopper unexpectedly cleared current readiness');
      cancellation.complete();
      await first.controller.close(); await replacement.controller.close();
      print('PASS: exact registration inverse reproduces same-instance throw and stale stopper readiness.');
    } else {
      check(replacementError == null && first.cancellations == 1 &&
        native._mixelExit._pending.length == 1,
        'Corrected same-instance restart did not track held prior subscription cancellation');
      replacement.controller.add(json.encode(supportEvent)); await settle();
      check(calls == 1 && mixelSupportUrlCanReceive,
        'Corrected replacement stream is not usable');
      var drained = false;
      final drain = mixelDrainExitOwners().then((_) => drained = true);
      await settle();
      check(!drained && !mixelSupportUrlCanReceive && replacement.cancellations == 1 &&
        native._mixelGlobalSubscription == null && native._eventCallback == null &&
        bind.values.last == 'N:$currentGeneration:$_mixelSupportUrlReadyEpoch',
        'Latest stopper did not clear readiness/stream or drain ignored prior cancellation');
      final late = RustdeskImpl(); final publications = bind.values.length;
      native._startListenEvent(late); await settle();
      check(late.starts == 0 && mixelSupportUrlGeneration == currentGeneration &&
        bind.values.length == publications && native._mixelGlobalSubscription == null,
        'Stopping same-instance owner accepted another stream or published readiness');
      cancellation.complete(); await drain;
      check(drained && _mixelExitOwners.isEmpty && native._mixelExit._pending.isEmpty,
        'Held previous cancellation was not drained after release');
      await first.controller.close(); await replacement.controller.close();
      unawaited(late.controller.close());
      print('PASS: exact same-instance replacement registers once, tracks prior cancellation, stops latest generation, and refuses restart after stop.');
    }
  } else if (mode == 'historical-owner') {
    final owner = MixelExitOwner(); final held = Completer<void>(); var stopped = 0;
    owner.register(() async { stopped++; });
    final pending = owner.track(() => held.future); var drained = false;
    final drain = mixelDrainExitOwners().then((_) => drained = true); await settle();
    check(owner.stopping && stopped == 1 && !drained, 'Historical tracked owner stop/drain changed');
    var late = 0; await owner.track(() async { late++; });
    check(late == 0, 'Stopping owner accepted late operation');
    held.complete(); await pending; await drain;
    check(drained && stopped == 1, 'Historical owner completion changed');
    print('PASS: unchanged historical owner track/stop/drain semantics and late-operation refusal.');
  } else if (mode == 'ordinary-platform') {
    isMacOS = false;
    final native = ActualNative(); final core = RustdeskImpl(); var calls = 0;
    native._startListenEvent(core); native.setEventCallback((_) async { calls++; });
    core.controller.add(json.encode({'name': 'ordinary-event'})); await settle();
    check(calls == 1 && _mixelExitOwners.isEmpty && native._mixelGlobalSubscription == null &&
      bind.values.isEmpty, 'Ordinary platform acquired Mac ownership or support readiness');
    await core.controller.close();
    print('PASS: ordinary-platform direct dispatch and original stream ownership remain unchanged.');
  } else { throw StateError('Unknown control'); }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "rustdesk")
    args = parser.parse_args()
    app = args.app.resolve()
    analysis_spec = importlib.util.spec_from_file_location(
        "mixel_final_exit_support_fixture", ROOT / "scripts/test-support-flutter-analysis.py")
    analysis = importlib.util.module_from_spec(analysis_spec)
    analysis_spec.loader.exec_module(analysis)
    # Verify the historical exit/ACK bodies against their exact prior layer in
    # a private copy; final startup behavior has its own actual-method gate.
    with analysis.startup_baseline(app) as plain:
        verify_app(plain)


def verify_app(app):
    spec = importlib.util.spec_from_file_location("mixel_exit_support_fixture", ROOT / "scripts/patch-support-macos-termination.py")
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    prior = app / "flutter/.mixel-dependencies/macos_application_exit"
    native = patch.normalize(patch.regular(app, NATIVE))[0].decode()
    original = patch.normalize(patch.regular(prior, NATIVE))[0]
    if patch.transform(NATIVE, original).decode() != native:
        raise AssertionError("Generated native model differs from exact composed transform")
    utility = patch.normalize(patch.regular(app, patch.GENERATED))[0].decode()
    if utility != patch.UTILITY or hashlib.sha256(utility.encode()).hexdigest() != HISTORICAL_UTILITY_SHA:
        raise AssertionError("Historical exit owner utility changed")
    core = utility[utility.index("final _mixelExitOwners"):utility.index("bool mixelValidAppAcknowledgement")]
    common = patch.normalize(patch.regular(prior, "flutter/lib/common.dart"))[0].decode()
    readiness = common[common.index("bool _mixelSupportUrlCallbackReady"):common.index("// Mixel support invite handoff:")]
    maintain = block(native, "  void _maintainMixelSupportUrlPending(")
    negative = once(maintain, "if (_mixelExit.stopping || !liveUi ||", "if (!liveUi ||")
    negative = once(negative, "    _mixelExit.poll(() async {", "    () async {")
    negative = once(negative, "    });", "    }();")
    if negative != block(original.decode(), "  void _maintainMixelSupportUrlPending("):
        raise AssertionError("Negative is not the exact saved ACK maintenance method")
    fields = native[native.index("  StreamEventHandler? _eventCallback;"):native.index("  String? _mixelSupportUrlPendingGeneration;") + len("  String? _mixelSupportUrlPendingGeneration;")]
    methods = [block(native, anchor) for anchor in (
        "  Future<bool> tryHandle(", "  void _clearMixelSupportUrlPending(",
        "  bool _deferMixelSupportUrlEvent(", "  void _startListenEvent(",
        "  void setEventCallback(")]
    legacy_start = once(methods[3],
        "    if (isMacOS && _mixelExit.stopping) return;\n"
        "    final previousSubscription = _mixelGlobalSubscription;\n"
        "    if (isMacOS && previousSubscription != null) {\n"
        "      _mixelExit.poll(previousSubscription.cancel);\n"
        "    }\n", "")
    legacy_start = once(legacy_start, "    _mixelGlobalGeneration = supportGeneration;\n", "")
    legacy_start = once(legacy_start,
        "    if (isMacOS && !_mixelGlobalStopperRegistered) {\n"
        "      _mixelGlobalStopperRegistered = true;\n", "    if (isMacOS) {\n")
    legacy_start = once(legacy_start,
        "        final activeGeneration = _mixelGlobalGeneration;\n"
        "        if (activeGeneration == mixelSupportUrlGeneration) {\n"
        "          setMixelSupportUrlStreamReady(false, generation: activeGeneration);",
        "        if (supportGeneration == mixelSupportUrlGeneration) {\n"
        "          setMixelSupportUrlStreamReady(false, generation: supportGeneration);")
    legacy_fields = once(fields,
        "  bool _mixelGlobalStopperRegistered = false;\n  String? _mixelGlobalGeneration;\n", "")
    dartexe = os.environ.get("DART_BIN") or shutil.which("dart")
    if not dartexe:
        raise SystemExit("DART_BIN or Dart on PATH is required")
    with tempfile.TemporaryDirectory(prefix="mixel-termination-support-") as directory:
        directory = Path(directory)
        for profile, maintenance in (("negative", negative),
                                     ("legacy-registration", maintain),
                                     ("corrected", maintain)):
            program = directory / (profile + ".dart")
            selected_fields = legacy_fields if profile == "legacy-registration" else fields
            selected_methods = list(methods)
            if profile == "legacy-registration":
                selected_methods[3] = legacy_start
            model = "class ActualNative {\n  String _appType = 'main';\n  bool get isMain => _appType == 'main';\n  final _eventHandlers = <String, Map<String, HandleEvent>>{};\n" + selected_fields + "\n" + maintenance + "\n" + "\n".join(selected_methods) + "\n}\n"
            program.write_text(SCAFFOLD + readiness + core + model + CONTROLS, encoding="utf-8", newline="\n")
            analyzed = subprocess.run([dartexe, "analyze", str(program)], capture_output=True, text=True, encoding="utf-8", timeout=60)
            if analyzed.returncode:
                raise AssertionError(analyzed.stdout + analyzed.stderr)
            print(analyzed.stdout.strip())
            modes = (("held", "after-stop") if profile == "negative" else
                     ("same-instance",) if profile == "legacy-registration" else
                     ("held", "callback-error", "after-stop", "support-only", "generation",
                      "same-instance", "historical-owner", "ordinary-platform"))
            for mode in modes:
                result = subprocess.run([dartexe, str(program), profile, mode], capture_output=True, text=True, encoding="utf-8", timeout=30)
                if result.returncode:
                    raise AssertionError(result.stdout + result.stderr)
                print(result.stdout.strip())
    print("PASS: composed support/exit regression; three exact negatives and eight corrected source-only controls; no app or native IPC execution.")


if __name__ == "__main__":
    main()
