import 'dart:async';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/common.dart' as common;
import 'package:flutter_hbb/generated_bridge.dart';
import 'package:flutter_hbb/models/platform_model.dart' as platform;
import 'package:flutter_hbb/models/relative_mouse_model.dart';
import 'package:flutter_hbb/models/input_model.dart';
import 'package:flutter_hbb/models/model.dart';
import 'package:flutter_hbb/models/state_model.dart';
import 'package:flutter_hbb/common/shared_state.dart';
import 'package:flutter_hbb/desktop/widgets/tabbar_widget.dart';
import 'package:flutter_hbb/common/widgets/remote_input.dart';
import 'package:get/get.dart';
import 'package:uuid/uuid.dart';

class RecordingBridge implements RustdeskImpl {
  final List<String> mouse = [];
  final List<String> keys = [];
  @override
  Future<void> setCurSessionId({required UuidValue sessionId, dynamic hint}) async {}
  @override
  Future<void> sessionEnterOrLeave({required UuidValue sessionId, required bool enter, dynamic hint}) async {}
  final Set<int> held = {};
  @override
  void sessionHandleFlutterKeyEvent({required UuidValue sessionId, required String character, required int usbHid, required int lockModes, required bool downOrUp, dynamic hint}) {
    keys.add('${downOrUp ? 'down' : 'up'}:$usbHid'); if (downOrUp) { held.add(usbHid); } else { held.remove(usbHid); }
  }
  @override
  void sessionInputKey({required UuidValue sessionId, required String name, required bool down, required bool press, required bool alt, required bool ctrl, required bool shift, required bool command, dynamic hint}) {}
  String source = 'Input source 2';
  void Function()? sourceChange;
  @override
  bool isIncomingOnly({dynamic hint}) => false;
  @override
  bool isOutgoingOnly({dynamic hint}) => false;
  @override
  String mainGetInputSource({dynamic hint}) => source;
  @override
  void mainSetInputSource({required UuidValue sessionId, required String value, dynamic hint}) { sourceChange?.call(); source = value; }
  @override
  bool sessionGetToggleOptionSync({required UuidValue sessionId, required String arg, dynamic hint}) => false;
  @override
  String translate({required String name, required String locale, dynamic hint}) => name;
  @override
  int versionToNumber({required String v, dynamic hint}) => v.split('.').take(3).fold<int>(0, (n, part) => n * 1000 + int.parse(part));
  @override
  void sessionSendMouse({required UuidValue sessionId, required String msg, dynamic hint}) { mouse.add(msg); }
  @override
  dynamic noSuchMethod(Invocation invocation) => throw StateError('Unexpected native binding: ${invocation.memberName}');
}

class TestSession implements FFI {
  @override
  final UuidValue sessionId = const Uuid().v4obj();
  @override
  late final FfiModel ffiModel = FfiModel(WeakReference<FFI>(this));
  @override
  late InputModel inputModel;
  @override
  bool closed = false;
  @override
  String id = 'owned-headless-lifecycle';
  @override
  ConnType connType = ConnType.defaultConn;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw StateError('Unexpected session access: ${invocation.memberName}');
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('org.rustdesk.rustdesk/host');
  late RecordingBridge bridge;
  late RelativeMouseModel model;
  late List<Completer<Object?>> requests;
  late List<MethodCall> calls;
  late bool permission;
  late bool camera;
  late bool allowed;
  late String source;
  late String version;
  late String peer;
  late DesktopTabController tabs;
  late List<RelativeMouseModel> models;
  Completer<bool>? releaseBlock;
  bool releaseAllowed = true;
  RelativeMouseModel makeModel({bool Function()? selected, UuidValue? owner}) {
    final result = RelativeMouseModel(sessionId: owner ?? const Uuid().v4obj(), enabled: false.obs,
      keyboardPerm: () => permission, isViewCamera: () => camera,
      canActivate: () => allowed && (selected?.call() ?? true), inputSource: () => source,
      peerVersion: () => version, peerPlatform: () => peer,
      modify: (msg) => msg, getPointerInsideImage: () => true, setPointerInsideImage: (_) {});
    result.updateImageWidgetSize(const Size(100, 100)); models.add(result); return result;
  }
  Future<void> flush(WidgetTester tester) async { await tester.pump(); await tester.pump(const Duration(milliseconds: 120)); await tester.pump(); }
  List<MethodCall> getReleases() => calls.where((call) => call.method == 'disableNativeRelativeMouseMode').toList();
  setUp(() {
    expect(common.isMacOS, isTrue);
    common.isTest = true;
    permission = true; camera = false; allowed = true; source = 'Input source 2'; version = '1.4.6'; peer = 'Windows'; models = []; releaseBlock = null; releaseAllowed = true;
    bridge = RecordingBridge(); platform.inputModelTestBinding = bridge;
    tabs = Get.put(DesktopTabController(tabType: DesktopTabType.remoteScreen));
    tabs.state.value.tabs.add(TabInfo(key: 'owned-headless-lifecycle', label: 'owned', page: const SizedBox()));
    requests = []; calls = [];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (call.method == 'enableNativeRelativeMouseMode') {
        final completion = Completer<Object?>(); requests.add(completion);
        final result = await completion.future;
        if (result is bool) {
          return {'status': result ? 'enabled' : 'inactive',
            'owner': (call.arguments as Map)['owner'], 'generation': (call.arguments as Map)['generation']};
        }
        return result;
      }
      if (call.method == 'disableNativeRelativeMouseMode') return releaseBlock?.future ?? releaseAllowed;
      throw StateError('Unexpected host call ${call.method}');
    });
    model = makeModel();
  });
  tearDown(() async {
    for (final request in requests) { if (!request.isCompleted) request.complete(false); }
    await Future<void>.delayed(Duration.zero);
    if (releaseBlock != null && !releaseBlock!.isCompleted) releaseBlock!.complete(true);
    releaseBlock = null; releaseAllowed = true;
    for (final item in models) { item.setRelativeMouseMode(false); }
    await Future<void>.delayed(Duration.zero);
    for (final item in models) { item.dispose(); }
    await Future<void>.delayed(Duration.zero);
    Get.delete<DesktopTabController>();
    platform.inputModelTestBinding = null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null);
  });
  for (final cancellation in ['disable', 'dispose', 'permission', 'inactive']) {
    testWidgets('pending enable cannot survive $cancellation', (tester) async {
      expect(model.setRelativeMouseMode(true), isTrue);
      await tester.pump(); expect(requests.length, 1);
      if (cancellation == 'disable') model.setRelativeMouseMode(false);
      if (cancellation == 'dispose') model.dispose();
      if (cancellation == 'permission') permission = false;
      if (cancellation == 'inactive') model.onWindowBlur();
      requests.single.complete(true);
      await tester.pump(); await tester.pump();
      expect(model.enabled.value, isFalse);
      expect(calls.where((call) => call.method == 'disableNativeRelativeMouseMode').length, 1);
      expect(bridge.mouse.where((msg) => msg.contains('"relative_mouse_mode":"1"')), isEmpty);
    });
  }

  for (final restriction in ['camera', 'view/session identity', 'source']) {
    testWidgets('pending enable revalidates $restriction at completion', (tester) async {
      expect(model.setRelativeMouseMode(true), isTrue); await flush(tester);
      if (restriction == 'camera') camera = true;
      if (restriction == 'view/session identity') allowed = false;
      if (restriction == 'source') source = 'Input source 1';
      requests.single.complete(true); await flush(tester);
      expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
      expect(getReleases().single.arguments, calls.first.arguments);
    });
  }
  testWidgets('initial denied, inactive and disposed models make no enable request', (tester) async {
    permission = false; expect(model.setRelativeMouseMode(true), isFalse);
    permission = true; allowed = false; expect(model.setRelativeMouseMode(true), isFalse);
    allowed = true; camera = true; expect(model.setRelativeMouseMode(true), isFalse);
    camera = false; model.onWindowBlur(); expect(model.setRelativeMouseMode(true), isFalse);
    model.onWindowFocus(); model.dispose(); expect(model.setRelativeMouseMode(true), isFalse);
    await flush(tester); expect(requests, isEmpty);
  });
  testWidgets('same-model and competing-model pending enables are refused', (tester) async {
    expect(model.setRelativeMouseMode(true), isTrue);
    expect(model.setRelativeMouseMode(true), isFalse);
    final other = makeModel(); expect(other.setRelativeMouseMode(true), isFalse);
    await flush(tester); expect(requests.length, 1);
    requests.single.complete(true); await flush(tester);
    expect(model.enabled.value, isTrue); expect(other.enabled.value, isFalse);
    expect(model.setRelativeMouseMode(true), isTrue); await flush(tester); expect(requests.length, 1);
  });
  testWidgets('cancelled request retains its reservation through exact late-release completion', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester);
    model.setRelativeMouseMode(false);
    final other = makeModel(); expect(other.setRelativeMouseMode(true), isFalse);
    releaseBlock = Completer<bool>(); requests.single.complete(true); await flush(tester);
    expect(getReleases().length, 1); expect(other.setRelativeMouseMode(true), isFalse);
    expect(model.setRelativeMouseMode(true), isFalse);
    releaseBlock!.complete(true); await flush(tester); releaseBlock = null;
    expect(other.setRelativeMouseMode(true), isTrue); await flush(tester);
    requests.last.complete(true); await flush(tester); expect(other.enabled.value, isTrue);
    final count = getReleases().length; model.setRelativeMouseMode(false); await flush(tester);
    expect(getReleases().length, count, reason: 'Old model cannot release the new owner.');
    expect(other.enabled.value, isTrue);
  });
  testWidgets('active disable blocks a replacement until the owned release is confirmed', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    releaseBlock = Completer<bool>(); model.setRelativeMouseMode(false); await flush(tester);
    final other = makeModel(); expect(other.setRelativeMouseMode(true), isFalse);
    releaseBlock!.complete(true); await flush(tester); releaseBlock = null;
    expect(other.setRelativeMouseMode(true), isTrue); await flush(tester);
    requests.last.complete(true); await flush(tester); expect(other.enabled.value, isTrue);
  });
  testWidgets('failed cleanup retains ownership and permits only exact retry', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); model.setRelativeMouseMode(false);
    releaseAllowed = false; requests.single.complete(true); await flush(tester);
    final other = makeModel(); expect(other.setRelativeMouseMode(true), isTrue); await flush(tester);
    expect(other.enabled.value, isFalse); expect(requests.length, 1); expect(getReleases().length, 4);
    releaseAllowed = true; expect(model.setRelativeMouseMode(true), isTrue); await flush(tester);
    expect(getReleases().length, 5); expect(getReleases()[0].arguments, getReleases()[4].arguments);
    requests.last.complete(false); await flush(tester);
    expect(other.setRelativeMouseMode(true), isTrue); await flush(tester);
    requests.last.complete(false); await flush(tester);
  });
  testWidgets('platform false and exception do not enable or publish an enable marker', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(false); await flush(tester);
    expect(model.enabled.value, isFalse); expect(getReleases(), isEmpty);
    expect(model.setRelativeMouseMode(true), isTrue); await flush(tester);
    requests.last.completeError(PlatformException(code: 'owned-synthetic-error')); await flush(tester);
    expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
    expect(bridge.mouse.where((msg) => msg.contains('"relative_mouse_mode":"1"')), isEmpty);
  });
  testWidgets('source lifecycle cancellation survives source round trip before reply', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester);
    RelativeMouseModel.cancelForInputSourceChange(model.sessionId);
    source = 'Input source 1'; source = 'Input source 2';
    requests.single.complete(true); await flush(tester);
    expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
  });
  testWidgets('normal enable uses owned strict lease and preserves relative delta protocol', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester);
    final args = calls.single.arguments as Map;
    expect(args.keys.toSet(), {'owner', 'generation'}); expect(args['owner'], model.sessionId.toString());
    expect(args['generation'], isA<int>()); expect(args['generation'] as int, greaterThan(0));
    requests.single.complete(true); await flush(tester);
    expect(model.enabled.value, isTrue);
    expect(bridge.mouse.where((msg) => msg.contains('"relative_mouse_mode":"1"')).length, 1);
    model.sendRelativeMouseMove(4, 5); await tester.pump(const Duration(milliseconds: 20)); await flush(tester);
    expect(bridge.mouse.any((msg) => msg.contains('move_relative')), isTrue);
  });
  testWidgets('local Command+G can always exit an owned active lock after permission loss', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    permission = false;
    expect(model.handleKeyEvent(const KeyDownEvent(physicalKey: PhysicalKeyboardKey.keyG,
      logicalKey: LogicalKeyboardKey.keyG, timeStamp: Duration.zero),
      ctrlPressed: false, shiftPressed: false, altPressed: false, commandPressed: true), isTrue);
    await flush(tester); expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
  });
  for (final condition in ['permission', 'viewOnly', 'closed', 'focus', 'camera', 'source']) {
    testWidgets('production InputModel completion refuses changed $condition', (tester) async {
      bridge.source = 'Input source 2'; stateGlobal.getInputSource(force: true); stateGlobal.isFocused.value = true;
      final session = TestSession(); session.ffiModel.pi.version = '1.4.6'; session.ffiModel.pi.platform = 'Windows';
      session.inputModel = InputModel(WeakReference<FFI>(session)); session.inputModel.updateImageWidgetSize(const Size(100,100));
      expect(session.inputModel.setRelativeMouseMode(true), isTrue); await flush(tester);
      if (condition == 'permission') {
        KeyboardEnabledState.init(session.id);
        session.ffiModel.updatePermission({'name': 'permission', 'keyboard': 'false'}, session.id);
      }
      if (condition == 'viewOnly') session.ffiModel.setViewOnly(session.id, true);
      if (condition == 'closed') session.closed = true;
      if (condition == 'focus') stateGlobal.isFocused.value = false;
      if (condition == 'camera') session.connType = ConnType.viewCamera;
      if (condition == 'source') await stateGlobal.setInputSource(session.sessionId, 'Input source 1');
      requests.single.complete(true); await flush(tester);
      expect(session.inputModel.relativeMouseMode.value, isFalse); expect(getReleases().length, 1);
      session.inputModel.disposeRelativeMouseMode(); await flush(tester); KeyboardEnabledState.delete(session.id);
    });
  }

  for (final condition in ['permission', 'viewOnly', 'source', 'window']) {
    testWidgets('production caller cancellation survives $condition round trip', (tester) async {
      bridge.source = 'Input source 2'; stateGlobal.getInputSource(force: true); stateGlobal.isFocused.value = true;
      final session = TestSession(); session.ffiModel.pi.version = '1.4.6'; session.ffiModel.pi.platform = 'Windows';
      session.inputModel = InputModel(WeakReference<FFI>(session)); session.inputModel.updateImageWidgetSize(const Size(100,100));
      expect(session.inputModel.setRelativeMouseMode(true), isTrue); await flush(tester);
      if (condition == 'permission') {
        KeyboardEnabledState.init(session.id);
        session.ffiModel.updatePermission({'name': 'permission', 'keyboard': 'false'}, session.id);
        session.ffiModel.updatePermission({'name': 'permission', 'keyboard': 'true'}, session.id);
      }
      if (condition == 'viewOnly') {
        session.ffiModel.setViewOnly(session.id, true); session.ffiModel.setViewOnly(session.id, false);
      }
      if (condition == 'source') {
        await stateGlobal.setInputSource(session.sessionId, 'Input source 1');
        await stateGlobal.setInputSource(session.sessionId, 'Input source 2');
      }
      if (condition == 'window') { session.inputModel.onWindowBlur(); session.inputModel.onWindowFocus(); }
      requests.single.complete(true); await flush(tester);
      expect(session.inputModel.relativeMouseMode.value, isFalse); expect(getReleases().length, 1);
      session.inputModel.disposeRelativeMouseMode(); await flush(tester); KeyboardEnabledState.delete(session.id);
    });
  }
  testWidgets('active Mac mode exits on blur and never rearms itself on focus', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    model.onWindowBlur(); await flush(tester); model.onWindowFocus(); await flush(tester);
    expect(model.enabled.value, isFalse); expect(getReleases().length, 1); expect(requests.length, 1);
  });

  testWidgets('replacing the owning InputModel refuses old pending completion', (tester) async {
    bridge.source = 'Input source 2'; stateGlobal.getInputSource(force: true); stateGlobal.isFocused.value = true;
    final session = TestSession(); session.ffiModel.pi.version = '1.4.6'; session.ffiModel.pi.platform = 'Windows';
    final old = InputModel(WeakReference<FFI>(session)); session.inputModel = old;
    old.updateImageWidgetSize(const Size(100,100)); expect(old.setRelativeMouseMode(true), isTrue); await flush(tester);
    session.inputModel = InputModel(WeakReference<FFI>(session));
    requests.single.complete(true); await flush(tester);
    expect(old.relativeMouseMode.value, isFalse); expect(getReleases().length, 1);
    old.disposeRelativeMouseMode(); session.inputModel.disposeRelativeMouseMode(); await flush(tester);
  });
  testWidgets('an inactive or unidentified production session cannot begin native setup', (tester) async {
    bridge.source = 'Input source 2'; stateGlobal.getInputSource(force: true); stateGlobal.isFocused.value = true;
    final session = TestSession(); session.ffiModel.pi.version = '1.4.6'; session.ffiModel.pi.platform = 'Windows';
    session.inputModel = InputModel(WeakReference<FFI>(session)); session.inputModel.updateImageWidgetSize(const Size(100,100));
    session.closed = true; expect(session.inputModel.setRelativeMouseMode(true), isFalse);
    session.closed = false; session.id = ''; expect(session.inputModel.setRelativeMouseMode(true), isFalse);
    await flush(tester); expect(requests, isEmpty);
    session.inputModel.disposeRelativeMouseMode(); await flush(tester);
  });

  for (final capability in ['old version', 'remote Linux']) {
    testWidgets('pending enable refuses changed peer capability: $capability', (tester) async {
      model.setRelativeMouseMode(true); await flush(tester);
      if (capability == 'old version') { version = '0.1.0'; } else { peer = 'Linux'; }
      requests.single.complete(true); await flush(tester);
      expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
    });
  }
  testWidgets('cleanupRequired reply retains uncertain owner until actual toolbar retry', (tester) async {
    model.toggleRelativeMouseMode(); await flush(tester); releaseAllowed = false;
    final lease = Map<String, Object?>.from(calls.first.arguments as Map);
    requests.single.complete({'status':'cleanupRequired', ...lease}); await flush(tester);
    expect(model.enabled.value, isFalse); expect(getReleases().length, 2);
    final other = makeModel(); expect(other.setRelativeMouseMode(true), isTrue); await flush(tester);
    expect(other.enabled.value, isFalse); expect(requests.length, 1); expect(getReleases().length, 4);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 170))); releaseAllowed = true;
    model.toggleRelativeMouseMode(); await flush(tester);
    expect(getReleases().length, 5); expect(requests.length, 2);
    requests.last.complete(true); await flush(tester); expect(model.enabled.value, isTrue);
  });
  testWidgets('actual toggle recovers a release whose native retry finished after acknowledgement', (tester) async {
    model.toggleRelativeMouseMode(); await flush(tester); requests.single.complete(true); await flush(tester);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 170))); releaseAllowed = false;
    model.toggleRelativeMouseMode(); await flush(tester);
    expect(model.enabled.value, isFalse); expect(getReleases().length, 2);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 170))); releaseAllowed = true;
    model.toggleRelativeMouseMode(); await flush(tester);
    expect(requests.length, 2); requests.last.complete(true); await flush(tester);
    expect(model.enabled.value, isTrue);
  });
  for (final cancellation in ['disable', 'dispose']) {
    testWidgets('never completed enable is bounded and exact-cleaned after $cancellation', (tester) async {
      model.setRelativeMouseMode(true); await flush(tester);
      if (cancellation == 'dispose') { model.dispose(); } else { model.setRelativeMouseMode(false); }
      await tester.pump(const Duration(milliseconds: 1100)); await flush(tester);
      expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
      expect(getReleases().single.arguments, calls.first.arguments);
      final other = makeModel(); expect(other.setRelativeMouseMode(true), isTrue); await flush(tester);
      requests.last.complete(true); await flush(tester); expect(other.enabled.value, isTrue);
      requests.first.complete(true); await flush(tester);
      expect(model.enabled.value, isFalse); expect(other.enabled.value, isTrue);
    });
  }
  testWidgets('never completed exact disable remains owned but permits a later bounded toolbar retry', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    releaseBlock = Completer<bool>(); model.setRelativeMouseMode(false); await flush(tester);
    await tester.pump(const Duration(milliseconds: 1100)); await flush(tester);
    await tester.pump(const Duration(milliseconds: 1100)); await flush(tester);
    expect(getReleases().length, 2); expect(model.enabled.value, isFalse);
    final other = makeModel(selected: () => false); expect(other.setRelativeMouseMode(true), isFalse);
    releaseBlock!.complete(true); releaseBlock = null; await flush(tester);
    model.toggleRelativeMouseMode(); await flush(tester); expect(requests.length, 2);
    requests.last.complete(true); await flush(tester); expect(model.enabled.value, isTrue);
  });
  for (final malformed in ['wrong owner', 'wrong generation', 'extra key', 'bool generation', 'unknown status']) {
    testWidgets('unattributed enable reply exact-cleans only its own request: $malformed', (tester) async {
      model.setRelativeMouseMode(true); await flush(tester);
      final result = <String,Object?>{'status':'enabled', ...Map<String,Object?>.from(calls.first.arguments as Map)};
      if (malformed == 'wrong owner') result['owner'] = 'foreign';
      if (malformed == 'wrong generation') result['generation'] = 999;
      if (malformed == 'extra key') result['extra'] = true;
      if (malformed == 'bool generation') result['generation'] = true;
      if (malformed == 'unknown status') result['status'] = 'maybe';
      requests.single.complete(result); await flush(tester);
      expect(model.enabled.value, isFalse); expect(getReleases().length, 1);
      expect(getReleases().single.arguments, calls.first.arguments);
    });
  }
  testWidgets('synchronous C7 input-source setter fences reentrant activation and always settles', (tester) async {
    bridge.source = 'Input source 2'; stateGlobal.getInputSource(force:true); stateGlobal.isFocused.value=true;
    final session=TestSession();session.ffiModel.pi.version='1.4.6';session.ffiModel.pi.platform='Windows';
    session.inputModel=InputModel(WeakReference<FFI>(session));session.inputModel.updateImageWidgetSize(const Size(100,100));
    var observedSynchronousCall = false;
    bridge.sourceChange = () {
      observedSynchronousCall = true;
      expect(stateGlobal.isInputSourceChanging, isTrue);
      expect(session.inputModel.setRelativeMouseMode(true), isFalse);
    };
    final changed=stateGlobal.setInputSource(session.sessionId,'Input source 1');
    expect(observedSynchronousCall, isTrue);
    expect(stateGlobal.getInputSource(), 'Input source 1');
    expect(stateGlobal.isInputSourceChanging, isFalse);
    await changed;bridge.sourceChange=null;
    expect(requests,isEmpty);
    expect(session.inputModel.setRelativeMouseMode(true),isTrue);await flush(tester);
    requests.single.complete(true);await flush(tester);expect(session.inputModel.relativeMouseMode.value,isTrue);
    bridge.sourceChange = () { throw StateError('owned synchronous setter failure'); };
    await expectLater(stateGlobal.setInputSource(session.sessionId,'Input source 2'), throwsStateError);
    bridge.sourceChange = null; await flush(tester);
    expect(stateGlobal.isInputSourceChanging, isFalse);
    expect(stateGlobal.getInputSource(), 'Input source 1');
    expect(session.inputModel.relativeMouseMode.value, isFalse);
    session.inputModel.disposeRelativeMouseMode();await flush(tester);
  });
  for (final state in ['pending', 'retained']) {
    testWidgets('production InputModel local Command+G escapes $state despite denied input', (tester) async {
      bridge.source='Input source 2';stateGlobal.getInputSource(force:true);stateGlobal.isFocused.value=true;
      final session=TestSession();session.ffiModel.pi.version='1.4.6';session.ffiModel.pi.platform='Windows';
      session.inputModel=InputModel(WeakReference<FFI>(session));session.inputModel.updateImageWidgetSize(const Size(100,100));
      await tester.pumpWidget(Directionality(textDirection:TextDirection.ltr,child:RawKeyFocusScope(inputModel:session.inputModel,child:const SizedBox())));
      await flush(tester);session.inputModel.setRelativeMouseMode(true);await flush(tester);
      if(state=='retained') {
        releaseAllowed=false;final lease=Map<String,Object?>.from(calls.first.arguments as Map);
        requests.single.complete({'status':'cleanupRequired',...lease});await flush(tester);
        releaseAllowed=true;
      }
      session.ffiModel.setViewOnly(session.id,true);
      await tester.sendKeyDownEvent(LogicalKeyboardKey.metaLeft,platform:'macos');
      await tester.sendKeyDownEvent(LogicalKeyboardKey.keyG,platform:'macos');await flush(tester);
      if(state=='pending'){requests.single.complete(true);await flush(tester);}
      expect(session.inputModel.relativeMouseMode.value,isFalse);
      expect(getReleases(),isNotEmpty);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.keyG,platform:'macos');
      await tester.sendKeyUpEvent(LogicalKeyboardKey.metaLeft,platform:'macos');await flush(tester);
      session.inputModel.disposeRelativeMouseMode();await flush(tester);
    });
  }
  for(final phase in ['pending','active']) {
    testWidgets('actual tab selection cancels $phase A before B and rejects stale A callback',(tester)async{
      bridge.source='Input source 2';stateGlobal.getInputSource(force:true);stateGlobal.isFocused.value=true;
      final session=TestSession();session.id='A';session.ffiModel.pi.version='1.4.6';session.ffiModel.pi.platform='Windows';
      tabs.state.value.tabs.clear();tabs.state.value.tabs.addAll([
        TabInfo(key:'A',label:'A',page:const SizedBox()),TabInfo(key:'B',label:'B',page:const SizedBox())]);
      final selected=<String>[];tabs.onSelected=selected.add;
      session.inputModel=InputModel(WeakReference<FFI>(session));session.inputModel.updateImageWidgetSize(const Size(100,100));
      expect(session.inputModel.setRelativeMouseMode(true),isTrue);await flush(tester);
      if(phase=='active'){requests.single.complete(true);await flush(tester);}
      tabs.jumpTo(1);await flush(tester);
      tabs.notifySelected('A');expect(selected,['B']);expect(tabs.isSelectedKey('A'),isFalse);
      if(phase=='pending'){requests.single.complete(true);await flush(tester);}
      expect(session.inputModel.relativeMouseMode.value,isFalse);expect(getReleases().length,1);
      expect(session.inputModel.setRelativeMouseMode(true),isFalse);
      session.inputModel.disposeRelativeMouseMode();await flush(tester);
    });
  }
  testWidgets('removing selected tab A cancels its pending request before B admission',(tester)async{
    bridge.source='Input source 2';stateGlobal.getInputSource(force:true);stateGlobal.isFocused.value=true;
    final session=TestSession();session.id='A';session.ffiModel.pi.version='1.4.6';session.ffiModel.pi.platform='Windows';
    tabs.state.value.tabs.clear();tabs.state.value.tabs.addAll([
      TabInfo(key:'A',label:'A',page:const SizedBox()),TabInfo(key:'B',label:'B',page:const SizedBox())]);
    session.inputModel=InputModel(WeakReference<FFI>(session));session.inputModel.updateImageWidgetSize(const Size(100,100));
    session.inputModel.setRelativeMouseMode(true);await flush(tester);tabs.remove(0);await flush(tester);
    requests.single.complete(true);await flush(tester);expect(session.inputModel.relativeMouseMode.value,isFalse);
    expect(tabs.isSelectedKey('B'),isTrue);expect(getReleases().length,1);
    session.inputModel.disposeRelativeMouseMode();await flush(tester);
  });
  testWidgets('buffered relative motion is discarded when local mode is disabled',(tester)async{
    model.setRelativeMouseMode(true);await flush(tester);requests.single.complete(true);await flush(tester);
    bridge.mouse.clear();model.sendRelativeMouseMove(4,5);model.setRelativeMouseMode(false);await flush(tester);
    expect(bridge.mouse.where((msg)=>msg.contains('move_relative')&&msg.contains('"x":"4"')),isEmpty);
  });

  Future<void> delta(WidgetTester tester, Object? payload) async {
    final done=Completer<void>();
    await tester.binding.defaultBinaryMessenger.handlePlatformMessage(channel.name,
      const StandardMethodCodec().encodeMethodCall(MethodCall('onMouseDelta',payload)),(_){done.complete();});
    await done.future;await flush(tester);
  }
  testWidgets('native delta accepts only exact current owner and request generation',(tester)async{
    model.setRelativeMouseMode(true);await flush(tester);requests.single.complete(true);await flush(tester);
    final lease=Map<String,Object?>.from(calls.first.arguments as Map);bridge.mouse.clear();
    for(final bad in [<String,Object?>{'owner':'foreign','generation':lease['generation'],'dx':4,'dy':5},
      {...lease,'generation':999,'dx':4,'dy':5},{...lease,'generation':true,'dx':4,'dy':5},
      {...lease,'dx':true,'dy':5},{...lease,'dx':4,'dy':'5'},{...lease,'dx':4,'dy':5,'extra':0}]) {
      await delta(tester,bad);expect(bridge.mouse,isEmpty);
    }
    await delta(tester,{...lease,'dx':4,'dy':5});
    expect(bridge.mouse,['{"type":"move_relative","x":"4","y":"5"}']);
    model.setRelativeMouseMode(false);await flush(tester);bridge.mouse.clear();
    await delta(tester,{...lease,'dx':4,'dy':5});expect(bridge.mouse,isEmpty);
  });
  testWidgets('queued old-generation delta cannot reach a replacement lease of the same model',(tester)async{
    model.setRelativeMouseMode(true);await flush(tester);requests.single.complete(true);await flush(tester);
    final oldLease=Map<String,Object?>.from(calls.first.arguments as Map);
    model.setRelativeMouseMode(false);await flush(tester);
    model.setRelativeMouseMode(true);await flush(tester);requests.last.complete(true);await flush(tester);
    final newLease=Map<String,Object?>.from(calls.lastWhere((c)=>c.method=='enableNativeRelativeMouseMode').arguments as Map);
    bridge.mouse.clear();await delta(tester,{...oldLease,'dx':4,'dy':5});expect(bridge.mouse,isEmpty);
    await delta(tester,{...newLease,'dx':4,'dy':5});expect(bridge.mouse.length,1);
  });
  for(final phase in ['active','pending','retained']) {
    testWidgets('real RawKeyFocus Cmd-down G-down Cmd-up G-up preserves exact escape ownership: $phase',(tester)async{
      bridge.source='Input source 2';stateGlobal.getInputSource(force:true);stateGlobal.isFocused.value=true;
      final session=TestSession();session.ffiModel.pi.version='1.4.6';session.ffiModel.pi.platform='Windows';
      session.inputModel=InputModel(WeakReference<FFI>(session));session.inputModel.updateImageWidgetSize(const Size(100,100));
      await tester.pumpWidget(Directionality(textDirection:TextDirection.ltr,child:RawKeyFocusScope(inputModel:session.inputModel,child:const SizedBox())));
      await flush(tester);session.inputModel.setRelativeMouseMode(true);await flush(tester);
      if(phase=='active'){requests.single.complete(true);await flush(tester);}
      if(phase=='retained'){
        releaseAllowed=false;final lease=Map<String,Object?>.from(calls.first.arguments as Map);
        requests.single.complete({'status':'cleanupRequired',...lease});await flush(tester);
      }
      await tester.sendKeyDownEvent(LogicalKeyboardKey.metaLeft,platform:'macos');await flush(tester);
      expect(bridge.held,contains(227));
      if(phase!='active')session.ffiModel.setViewOnly(session.id,true);
      releaseAllowed=true;
      await tester.sendKeyDownEvent(LogicalKeyboardKey.keyG,platform:'macos');await flush(tester);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.metaLeft,platform:'macos');await flush(tester);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.keyG,platform:'macos');await flush(tester);
      if(phase=='pending'){requests.single.complete(true);await flush(tester);}
      session.ffiModel.setViewOnly(session.id,false);session.inputModel.enterOrLeave(true);await flush(tester);
      expect(bridge.keys.where((k)=>k.endsWith(':10')),isEmpty,reason:'Consumed G has no orphan Down, repeat or Up.');
      expect(bridge.keys,['down:227','up:227']);expect(bridge.held,isEmpty);
      expect(session.inputModel.relativeMouseMode.value,isFalse);
      session.inputModel.enterOrLeave(false);session.inputModel.disposeRelativeMouseMode();await flush(tester);
    });
  }
  testWidgets('escape consumes only its own repeat and Up after reset, not Command Up',(tester)async{
    model.setRelativeMouseMode(true);await flush(tester);
    bool key(KeyEvent e,bool command)=>model.handleKeyEvent(e,ctrlPressed:false,shiftPressed:false,altPressed:false,commandPressed:command);
    expect(key(const KeyDownEvent(physicalKey:PhysicalKeyboardKey.keyG,logicalKey:LogicalKeyboardKey.keyG,timeStamp:Duration.zero),true),isTrue);
    expect(key(const KeyRepeatEvent(physicalKey:PhysicalKeyboardKey.keyG,logicalKey:LogicalKeyboardKey.keyG,timeStamp:Duration.zero),true),isTrue);
    expect(key(const KeyUpEvent(physicalKey:PhysicalKeyboardKey.metaLeft,logicalKey:LogicalKeyboardKey.metaLeft,timeStamp:Duration.zero),false),isFalse);
    expect(key(const KeyUpEvent(physicalKey:PhysicalKeyboardKey.keyG,logicalKey:LogicalKeyboardKey.keyG,timeStamp:Duration.zero),false),isTrue);
    expect(key(const KeyUpEvent(physicalKey:PhysicalKeyboardKey.keyG,logicalKey:LogicalKeyboardKey.keyG,timeStamp:Duration.zero),false),isFalse);
    requests.single.complete(true);await flush(tester);expect(model.enabled.value,isFalse);
  });
  for(final action in ['dispose','viewOnly','deselect']) {
    testWidgets('production RelativeMouseModel discards queued motion on $action',(tester)async{
      tabs.state.value.tabs.clear();tabs.state.value.tabs.add(TabInfo(key:'A',label:'A',page:const SizedBox()));
      model=makeModel(selected:()=>tabs.isSelectedKey('A'));
      model.setRelativeMouseMode(true);await flush(tester);requests.single.complete(true);await flush(tester);
      bridge.mouse.clear();model.sendRelativeMouseMove(4,5);
      if(action=='dispose')model.dispose();
      if(action=='viewOnly'){allowed=false;RelativeMouseModel.revalidateSessionAdmission();}
      if(action=='deselect')tabs.add(TabInfo(key:'B',label:'B',page:const SizedBox()));
      await flush(tester);
      expect(bridge.mouse.where((m)=>m.contains('move_relative')&&!m.contains('"x":"0"')),isEmpty);
    });
  }

  TestSession inputSession(String id) {
    final session = TestSession(); session.id = id;
    session.ffiModel.pi.version = '1.4.6'; session.ffiModel.pi.platform = 'Windows';
    session.inputModel = InputModel(WeakReference<FFI>(session));
    session.inputModel.updateImageWidgetSize(const Size(100, 100));
    addTearDown(() async {
      releaseAllowed = true;
      session.inputModel.disposeRelativeMouseMode();
      await Future<void>.delayed(Duration.zero);
    });
    return session;
  }
  void twoSelectedTabs() {
    bridge.source = 'Input source 2'; stateGlobal.getInputSource(force: true);
    stateGlobal.isFocused.value = true;
    tabs.state.value.tabs.clear();
    tabs.state.value.tabs.addAll([
      TabInfo(key: 'A', label: 'A', page: const SizedBox()),
      TabInfo(key: 'B', label: 'B', page: const SizedBox()),
    ]);
  }
  testWidgets('disposed A exact cleanup acknowledgement admits selected B normal toolbar', (tester) async {
    twoSelectedTabs(); final a = inputSession('A'); final b = inputSession('B');
    a.inputModel.toggleRelativeMouseMode(); await flush(tester);
    requests.single.complete(true); await flush(tester);
    final oldLease = Map<String, Object?>.from(calls.first.arguments as Map);
    releaseAllowed = false;
    Timer(const Duration(milliseconds: 110), () { releaseAllowed = true; });
    a.inputModel.disposeRelativeMouseMode(); await tester.pump();
    // Select B while A's first bounded cleanup is still in flight; selection
    // must not schedule an accidental fresh retry after the later restoration.
    tabs.jumpTo(1); await flush(tester);
    expect(getReleases().length, 2); expect(requests.length, 1);
    b.inputModel.toggleRelativeMouseMode(); await flush(tester);
    expect(requests.length, 2, reason: 'Selected B can acknowledge only retained disabled A before requesting B.');
    expect(getReleases().last.arguments, oldLease);
    final newLease = calls.lastWhere((c) => c.method == 'enableNativeRelativeMouseMode').arguments;
    expect((newLease as Map)['owner'], b.sessionId.toString());
    requests.last.complete(true); await flush(tester);
    expect(b.inputModel.relativeMouseMode.value, isTrue);
    final released = getReleases().length;
    a.inputModel.setRelativeMouseMode(false); await flush(tester);
    expect(getReleases().length, released); expect(b.inputModel.relativeMouseMode.value, isTrue);
  });
  testWidgets('persistent disposed-owner cleanup failure admits no replacement and remains retryable', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    final lease = calls.first.arguments; releaseAllowed = false; model.dispose(); await flush(tester);
    final other = makeModel(); other.toggleRelativeMouseMode(); await flush(tester);
    expect(requests.length, 1); expect(other.enabled.value, isFalse);
    expect(getReleases().length, 4); expect(getReleases().every((c) => c.arguments.toString() == lease.toString()), isTrue);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 170)));
    releaseAllowed = true; other.toggleRelativeMouseMode(); await flush(tester);
    expect(requests.length, 2); requests.last.complete(true); await flush(tester);
    expect(other.enabled.value, isTrue);
  });
  testWidgets('new selected admission is rechecked after retained foreign cleanup', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    releaseAllowed = false; model.dispose(); await flush(tester);
    bool selected = true; final other = makeModel(selected: () => selected);
    releaseBlock = Completer<bool>(); other.toggleRelativeMouseMode(); await tester.pump();
    expect(getReleases().length, 3); selected = false;
    releaseBlock!.complete(true); releaseBlock = null; await flush(tester);
    expect(requests.length, 1); expect(other.enabled.value, isFalse);
    selected = true; other.setRelativeMouseMode(true); await flush(tester);
    expect(requests.length, 2); requests.last.complete(true); await flush(tester);
    expect(other.enabled.value, isTrue);
  });
  testWidgets('cancelled foreign-owner reconciliation cannot enable its stale requester', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    releaseAllowed = false; model.dispose(); await flush(tester);
    final other = makeModel(); releaseBlock = Completer<bool>();
    other.toggleRelativeMouseMode(); await tester.pump(); other.setRelativeMouseMode(false);
    releaseBlock!.complete(true); releaseBlock = null; await flush(tester);
    expect(requests.length, 1); expect(other.enabled.value, isFalse);
    final newest = makeModel(); newest.setRelativeMouseMode(true); await flush(tester);
    requests.last.complete(true); await flush(tester); expect(newest.enabled.value, isTrue);
    final count = getReleases().length; other.setRelativeMouseMode(false); model.setRelativeMouseMode(false);
    await flush(tester); expect(getReleases().length, count); expect(newest.enabled.value, isTrue);
  });
  testWidgets('active requested pending and inflight foreign owners cannot be preempted', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester);
    final other = makeModel(); expect(other.setRelativeMouseMode(true), isFalse);
    requests.single.complete(true); await flush(tester);
    expect(other.setRelativeMouseMode(true), isFalse); expect(getReleases(), isEmpty);
    releaseAllowed = false; model.dispose(); await flush(tester);
    releaseBlock = Completer<bool>(); other.setRelativeMouseMode(true); await tester.pump();
    final third = makeModel(); expect(third.setRelativeMouseMode(true), isFalse);
    expect(other.setRelativeMouseMode(true), isFalse);
    releaseBlock!.complete(true); releaseBlock = null; await flush(tester);
    requests.last.complete(true); await flush(tester);
    expect(other.enabled.value, isTrue); expect(third.setRelativeMouseMode(true), isFalse);
  });
  testWidgets('production permission event invalidates Source2 owners after keyboard false', (tester) async {
    bridge.source = 'Input source 2'; stateGlobal.getInputSource(force: true); stateGlobal.isFocused.value = true;
    final session = inputSession('owned-headless-lifecycle');
    KeyboardEnabledState.init(session.id);
    addTearDown(() { KeyboardEnabledState.delete(session.id); });
    await tester.pumpWidget(Directionality(textDirection: TextDirection.ltr,
      child: RawKeyFocusScope(inputModel: session.inputModel, child: const SizedBox())));
    await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft, platform: 'macos'); await flush(tester);
    expect(bridge.held, contains(225));
    session.ffiModel.updatePermission({'keyboard': 'false'}, session.id); await flush(tester);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft, platform: 'macos'); await flush(tester);
    session.ffiModel.updatePermission({'keyboard': 'true'}, session.id); await flush(tester);
    await tester.sendKeyDownEvent(LogicalKeyboardKey.keyB, platform: 'macos');
    await tester.sendKeyUpEvent(LogicalKeyboardKey.keyB, platform: 'macos'); await flush(tester);
    expect(bridge.keys, ['down:225', 'up:225', 'down:5', 'up:5']); expect(bridge.held, isEmpty);
  });

  testWidgets('same-session replacement never reuses an old lease generation or queued delta', (tester) async {
    model.setRelativeMouseMode(true); await flush(tester); requests.single.complete(true); await flush(tester);
    final oldLease = Map<String, Object?>.from(calls.first.arguments as Map);
    model.dispose(); await flush(tester);
    final other = makeModel(owner: model.sessionId);
    other.toggleRelativeMouseMode(); await flush(tester);
    final newLease = Map<String, Object?>.from(calls.lastWhere((c) => c.method == 'enableNativeRelativeMouseMode').arguments as Map);
    expect(newLease['owner'], oldLease['owner']);
    expect(newLease['generation'], isNot(oldLease['generation']));
    expect(newLease['generation'], inInclusiveRange(1, 9007199254740991));
    requests.last.complete(true); await flush(tester); bridge.mouse.clear();
    await delta(tester, {...oldLease, 'dx': 4, 'dy': 5}); expect(bridge.mouse, isEmpty);
    await delta(tester, {...newLease, 'dx': 4, 'dy': 5});
    expect(bridge.mouse, ['{"type":"move_relative","x":"4","y":"5"}']);
    final count = getReleases().length; model.setRelativeMouseMode(false); await flush(tester);
    expect(getReleases().length, count); expect(other.enabled.value, isTrue);
  });

  for (final roundTrip in ['tab B-C-B', 'input source 2-1-2']) {
    testWidgets('waiting reconciliation is cancelled by $roundTrip', (tester) async {
      twoSelectedTabs();
      tabs.state.value.tabs.add(TabInfo(key: 'C', label: 'C', page: const SizedBox()));
      model.setRelativeMouseMode(true); await flush(tester);
      requests.single.complete(true); await flush(tester);
      releaseAllowed = false; model.dispose(); await flush(tester);
      tabs.jumpTo(1); await flush(tester);
      final b = inputSession('B');
      releaseBlock = Completer<bool>();
      b.inputModel.toggleRelativeMouseMode(); await tester.pump();
      expect(b.inputModel.isRelativeMouseRequestedOrEnabled, isTrue);
      if (roundTrip == 'tab B-C-B') {
        tabs.jumpTo(2); tabs.jumpTo(1); await tester.pump();
      } else {
        await stateGlobal.setInputSource(b.sessionId, 'Input source 1');
        await stateGlobal.setInputSource(b.sessionId, 'Input source 2');
      }
      releaseBlock!.complete(true); releaseBlock = null; await flush(tester);
      expect(requests.length, 1, reason: 'A completed round trip cannot revive B reconciliation intent.');
      expect(b.inputModel.isRelativeMouseRequestedOrEnabled, isFalse);
      expect(b.inputModel.relativeMouseMode.value, isFalse);
      releaseAllowed = true;
      expect(b.inputModel.setRelativeMouseMode(true), isTrue); await flush(tester);
      expect(requests.length, 2); requests.last.complete(true); await flush(tester);
      expect(b.inputModel.relativeMouseMode.value, isTrue);
    });
  }
}
