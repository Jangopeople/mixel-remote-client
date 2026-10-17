import 'dart:ui' show PointerDeviceKind;

import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/common.dart' as common;
import 'package:flutter_hbb/common/widgets/remote_input.dart';
import 'package:flutter_hbb/generated_bridge.dart';
import 'package:flutter_hbb/models/input_model.dart';
import 'package:flutter_hbb/models/model.dart';
import 'package:flutter_hbb/models/platform_model.dart' as platform;
import 'package:flutter_hbb/models/state_model.dart';
import 'package:uuid/uuid.dart';

// Only the Rust/native transport and remote session properties are replaced.
// InputModel, RawKeyFocusScope, MouseRegion and HardwareKeyboard are real.
class RecordingBridge implements RustdeskImpl {
  final List<String> keys = [];
  final List<String> calls = [];
  final List<String> legacyUps = [];
  bool remoteKeyboardEnabled = true;
  final Set<int> remoteHeld = {};
  final List<String> ignoredKeys = [];
  String source = 'Input source 2';
  @override
  String mainGetInputSource({dynamic hint}) => source;
  @override
  Future<void> setCurSessionId({required UuidValue sessionId, dynamic hint}) async {
    calls.add('setCurSessionId');
  }
  @override
  Future<void> sessionEnterOrLeave({required UuidValue sessionId, required bool enter, dynamic hint}) async {
    calls.add('sessionEnterOrLeave:$enter');
  }
  @override
  Future<void> sessionHandleFlutterKeyEvent({required UuidValue sessionId, required String character, required int usbHid, required int lockModes, required bool downOrUp, dynamic hint}) async {
    keys.add('${downOrUp ? 'down' : 'up'}:$usbHid');
    calls.add('key:${keys.last}');
    // Match the real server permission boundary: denied KeyEvents, including
    // Up, have no effect on the peer's already-held physical state.
    if (!remoteKeyboardEnabled) {
      ignoredKeys.add(keys.last);
    } else if (downOrUp) {
      remoteHeld.add(usbHid);
    } else {
      remoteHeld.remove(usbHid);
    }
  }
  @override
  Future<void> sessionSendMouse({required UuidValue sessionId, required String msg, dynamic hint}) async {
    calls.add('mouse:$msg');
  }
  @override
  Future<void> sessionSendPointer({required UuidValue sessionId, required String msg, dynamic hint}) async {
    calls.add('pointer:$msg');
  }
  @override
  Future<void> sessionInputKey({required UuidValue sessionId, required String name,
    required bool down, required bool press, required bool alt, required bool ctrl,
    required bool shift, required bool command, dynamic hint}) async {
    if (down || press || alt || ctrl || shift || command) {
      throw StateError('The original native exit may only send unmodified legacy releases.');
    }
    legacyUps.add(name);
    calls.add('legacy-up:$name');
    if (remoteKeyboardEnabled) {
      const physical = {'Control_L': 224, 'Control_R': 228, 'Alt_L': 226, 'Alt_R': 230,
        'Shift_L': 225, 'Shift_R': 229, 'Meta_L': 227, 'Meta_R': 231};
      remoteHeld.remove(physical[name]);
    }
  }
  @override
  dynamic noSuchMethod(Invocation invocation) => throw StateError('Unexpected native call: ${invocation.memberName}');
}

class TestPeer implements PeerInfo {
  @override
  String platform = 'Linux';
  @override
  String version = '1.4.6';
  @override
  dynamic noSuchMethod(Invocation invocation) => throw StateError('Unexpected peer property: ${invocation.memberName}');
}

class TestFfiModel implements FfiModel {
  @override
  bool keyboard = true;
  @override
  bool viewOnly = false;
  @override
  final PeerInfo pi = TestPeer();
  @override
  dynamic noSuchMethod(Invocation invocation) => throw StateError('Unexpected FFI property: ${invocation.memberName}');
}

class TestSession implements FFI {
  @override
  final UuidValue sessionId = const Uuid().v4obj();
  @override
  final TestFfiModel ffiModel = TestFfiModel();
  @override
  String id = 'owned-headless-session';
  @override
  ConnType connType = ConnType.defaultConn;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw StateError('Unexpected session property: ${invocation.memberName}');
}

const sides = <(String, PhysicalKeyboardKey, LogicalKeyboardKey)>[
  ('left Shift', PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft),
  ('right Shift', PhysicalKeyboardKey.shiftRight, LogicalKeyboardKey.shiftRight),
  ('left Control', PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft),
  ('right Control', PhysicalKeyboardKey.controlRight, LogicalKeyboardKey.controlRight),
  ('left Alt', PhysicalKeyboardKey.altLeft, LogicalKeyboardKey.altLeft),
  ('right Alt', PhysicalKeyboardKey.altRight, LogicalKeyboardKey.altRight),
  ('left Meta', PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft),
  ('right Meta', PhysicalKeyboardKey.metaRight, LogicalKeyboardKey.metaRight),
];

String wire(String action, PhysicalKeyboardKey key) => '$action:${key.usbHidUsage & 0xffff}';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late RecordingBridge bridge;
  late TestSession session;
  late InputModel input;
  late FocusNode focus;

  setUp(() {
    expect(common.isMacOS, isTrue, reason: 'This candidate models the actual Mac source2 route.');
    common.isTest = true;
    bridge = RecordingBridge();
    platform.inputModelTestBinding = bridge;
    stateGlobal.getInputSource(force: true);
    session = TestSession();
    input = InputModel(WeakReference<FFI>(session));
    focus = FocusNode();
  });

  tearDown(() {
    input.disposeRelativeMouseMode();
    platform.inputModelTestBinding = null;
  });

  Future<TestGesture> mount(WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: Center(child:
      RawKeyFocusScope(focusNode: focus, inputModel: input, child:
        MouseRegion(onEnter: (_) { focus.requestFocus(); input.enterOrLeave(true); },
          onExit: (_) => input.enterOrLeave(false),
          child: const SizedBox(width: 200, height: 200)))))));
    await tester.pump();
    focus.requestFocus();
    await tester.pump();
    final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: const Offset(1, 1));
    await mouse.moveTo(const Offset(400, 300));
    await tester.pump();
    return mouse;
  }

  Future<void> down(WidgetTester tester, PhysicalKeyboardKey physical, LogicalKeyboardKey logical) async {
    await tester.sendKeyDownEvent(logical, physicalKey: physical, platform: 'macos');
    await tester.pump();
  }
  Future<void> up(WidgetTester tester, PhysicalKeyboardKey physical, LogicalKeyboardKey logical) async {
    await tester.sendKeyUpEvent(logical, physicalKey: physical, platform: 'macos');
    await tester.pump();
  }

  for (final side in sides) {
    testWidgets('${side.$1} survives a real pointer leave/reentry before A', (tester) async {
      final mouse = await mount(tester);
      await down(tester, side.$2, side.$3);
      await mouse.moveTo(const Offset(1, 1));
      await tester.pump();
      expect(HardwareKeyboard.instance.physicalKeysPressed, contains(side.$2));
      await mouse.moveTo(const Offset(400, 300));
      await tester.pump();
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, side.$2, side.$3);
      expect(bridge.keys, [wire('down', side.$2), wire('up', side.$2),
        wire('down', side.$2), wire('down', PhysicalKeyboardKey.keyA),
        wire('up', PhysicalKeyboardKey.keyA), wire('up', side.$2)]);
      await mouse.removePointer();
    });

    testWidgets('${side.$1} crossing alone adds no tap or duplicate release', (tester) async {
      final mouse = await mount(tester);
      await down(tester, side.$2, side.$3);
      await mouse.moveTo(const Offset(1, 1));
      await tester.pump();
      await mouse.moveTo(const Offset(400, 300));
      await tester.pump();
      input.enterOrLeave(true); // Duplicate geometry/focus entry is idempotent.
      await input.moveMouse(10, 20); // Hover must not arm a bare Alt/Meta tap.
      await mouse.moveTo(const Offset(1, 1));
      await tester.pump();
      input.enterOrLeave(false);
      await up(tester, side.$2, side.$3);
      expect(bridge.keys, [wire('down', side.$2), wire('up', side.$2)]);
      expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
      await mouse.removePointer();
    });

    testWidgets('${side.$1} released outside is not resurrected', (tester) async {
      final mouse = await mount(tester);
      await down(tester, side.$2, side.$3);
      await mouse.moveTo(const Offset(1, 1));
      await tester.pump();
      focus.unfocus();
      await tester.pump();
      await up(tester, side.$2, side.$3);
      expect(HardwareKeyboard.instance.physicalKeysPressed, isNot(contains(side.$2)));
      await mouse.moveTo(const Offset(400, 300));
      await tester.pump();
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      expect(bridge.keys, [wire('down', side.$2), wire('up', side.$2),
        wire('down', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.keyA)]);
      await mouse.removePointer();
    });

    testWidgets('${side.$1} pressed outside keeps actual physical side on entry', (tester) async {
      final mouse = await mount(tester);
      await mouse.moveTo(const Offset(1, 1));
      await tester.pump();
      focus.unfocus();
      await tester.pump();
      await down(tester, side.$2, side.$3);
      expect(bridge.keys, isEmpty);
      await mouse.moveTo(const Offset(400, 300));
      await tester.pump();
      expect(bridge.keys, isEmpty);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, side.$2, side.$3);
      expect(bridge.keys, [wire('down', side.$2), wire('down', PhysicalKeyboardKey.keyA),
        wire('up', PhysicalKeyboardKey.keyA), wire('up', side.$2)]);
      await mouse.removePointer();
    });

    testWidgets('${side.$1} repeat after reentry cannot leave an uncached down', (tester) async {
      final mouse = await mount(tester);
      await down(tester, side.$2, side.$3);
      input.enterOrLeave(false);
      input.enterOrLeave(true);
      await tester.sendKeyRepeatEvent(side.$3, physicalKey: side.$2, platform: 'macos');
      await tester.pump();
      await up(tester, side.$2, side.$3);
      expect(bridge.keys, [wire('down', side.$2), wire('up', side.$2)]);
      expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
      await mouse.removePointer();
    });
  }

  for (var family = 0; family < sides.length; family += 2) {
    final left = sides[family];
    final right = sides[family + 1];
    testWidgets('both ${left.$1.split(' ').last} sides retain the other side and release exactly once', (tester) async {
      final mouse = await mount(tester);
      await down(tester, left.$2, left.$3);
      await down(tester, right.$2, right.$3);
      await mouse.moveTo(const Offset(1, 1));
      await tester.pump();
      await mouse.moveTo(const Offset(400, 300));
      await tester.pump();
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, left.$2, left.$3);
      expect([input.shift, input.ctrl, input.alt, input.command][family ~/ 2], isTrue);
      await up(tester, right.$2, right.$3);
      expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
      for (final side in [left, right]) {
        expect(bridge.keys.where((event) => event == wire('down', side.$2)).length, 2);
        expect(bridge.keys.where((event) => event == wire('up', side.$2)).length, 2);
      }
      final count = bridge.keys.length;
      input.enterOrLeave(false);
      expect(bridge.keys.length, count);
      await mouse.removePointer();
    });
  }

  testWidgets('repeated entry with forwarded modifiers emits no duplicate events', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    input.enterOrLeave(true);
    input.enterOrLeave(true);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.shiftLeft)]);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.shiftLeft), wire('up', PhysicalKeyboardKey.shiftLeft)]);
    await mouse.removePointer();
  });

  testWidgets('all modifiers precede a real click and its modified mouse payload', (tester) async {
    final mouse = await mount(tester);
    final selected = [sides[0], sides[2], sides[4], sides[6]];
    for (final side in selected) { await down(tester, side.$2, side.$3); }
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    bridge.calls.clear();
    await input.tapDown(MouseButtons.left);
    expect(bridge.calls.take(4), [for (final side in selected) 'key:${wire('down', side.$2)}']);
    expect(bridge.calls[4], contains('"ctrl":"true"'));
    expect(bridge.calls[4], contains('"shift":"true"'));
    expect(bridge.calls[4], contains('"alt":"true"'));
    expect(bridge.calls[4], contains('"command":"true"'));
    await input.tapUp(MouseButtons.left);
    for (final side in selected.reversed) { await up(tester, side.$2, side.$3); }
    expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
    await mouse.removePointer();
  });

  testWidgets('a real wheel action restores held Shift before its payload', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftRight, LogicalKeyboardKey.shiftRight);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    bridge.calls.clear();
    await input.scroll(1);
    expect(bridge.calls.first, 'key:${wire('down', PhysicalKeyboardKey.shiftRight)}');
    expect(bridge.calls[1], contains('"shift":"true"'));
    await up(tester, PhysicalKeyboardKey.shiftRight, LogicalKeyboardKey.shiftRight);
    await mouse.removePointer();
  });

  testWidgets('actual desktop PointerScrollEvent restores before the unchanged wheel payload', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.controlRight, LogicalKeyboardKey.controlRight);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    bridge.calls.clear();
    input.onPointerSignalImage(const PointerScrollEvent(scrollDelta: Offset(0, 20)));
    expect(bridge.calls, ['key:${wire('down', PhysicalKeyboardKey.controlRight)}', 'mouse:{"type": "wheel", "x": "0", "y": "-1"}']);
    await up(tester, PhysicalKeyboardKey.controlRight, LogicalKeyboardKey.controlRight);
    await mouse.removePointer();
  });

  testWidgets('actual trackpad pan restores before its unchanged payload', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.altRight, LogicalKeyboardKey.altRight);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    bridge.calls.clear();
    input.onPointerPanZoomStart(const PointerPanZoomStartEvent());
    input.onPointerPanZoomUpdate(const PointerPanZoomUpdateEvent(panDelta: Offset(0, 50)));
    expect(bridge.calls, ['key:${wire('down', PhysicalKeyboardKey.altRight)}', 'mouse:{"type": "trackpad", "x": "0", "y": "3"}']);
    await up(tester, PhysicalKeyboardKey.altRight, LogicalKeyboardKey.altRight);
    await mouse.removePointer();
  });

  testWidgets('empty-string hover buttons and zero wheel never create a modifier tap', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.metaRight, LogicalKeyboardKey.metaRight);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    // Exact ordinary-hover shape produced by processEventToPeer after its
    // numeric zero is converted by mouseButtonsToPeer().
    input.modify({'type': '', 'buttons': '', 'x': '10', 'y': '20'});
    input.onPointerSignalImage(const PointerScrollEvent(scrollDelta: Offset.zero));
    input.enterOrLeave(false);
    await up(tester, PhysicalKeyboardKey.metaRight, LogicalKeyboardKey.metaRight);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.metaRight), wire('up', PhysicalKeyboardKey.metaRight)]);
    await mouse.removePointer();
  });

  testWidgets('cancelled trackpad fling cannot restore modifiers after another boundary', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.altLeft, LogicalKeyboardKey.altLeft);
    input.onPointerPanZoomStart(const PointerPanZoomStartEvent());
    input.onPointerPanZoomUpdate(const PointerPanZoomUpdateEvent(panDelta: Offset(0, 50)));
    input.onPointerPanZoomEnd(const PointerPanZoomEndEvent());
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    final count = bridge.keys.length;
    await tester.pump(const Duration(milliseconds: 200));
    expect(bridge.keys.length, count);
    input.enterOrLeave(false);
    await up(tester, PhysicalKeyboardKey.altLeft, LogicalKeyboardKey.altLeft);
    expect(bridge.keys.length, count);
    await mouse.removePointer();
  });

  testWidgets('leave retains a denied release until permission returns before new input', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft);
    session.ffiModel.keyboard = false;
    bridge.remoteKeyboardEnabled = false;
    input.enterOrLeave(false);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.controlLeft)]);
    expect(bridge.remoteHeld, {PhysicalKeyboardKey.controlLeft.usbHidUsage & 0xffff});
    focus.unfocus();
    await tester.pump();
    await up(tester, PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft);
    session.ffiModel.keyboard = true;
    bridge.remoteKeyboardEnabled = true;
    focus.requestFocus();
    await tester.pump();
    await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.controlLeft), wire('up', PhysicalKeyboardKey.controlLeft),
      wire('down', PhysicalKeyboardKey.keyB), wire('up', PhysicalKeyboardKey.keyB)]);
    expect(bridge.remoteHeld, isEmpty);
    await mouse.removePointer();
  });

  testWidgets('A repeat restores Shift once without inventing ordinary keys', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    final count = bridge.keys.length;
    await tester.sendKeyRepeatEvent(LogicalKeyboardKey.keyA, physicalKey: PhysicalKeyboardKey.keyA, platform: 'macos');
    await tester.pump();
    expect(bridge.keys.skip(count), [wire('down', PhysicalKeyboardKey.shiftLeft), wire('down', PhysicalKeyboardKey.keyA)]);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    await mouse.removePointer();
  });

  testWidgets('held ordinary A is never replayed by image entry', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    input.enterOrLeave(true);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    expect(bridge.keys.where((key) => key == wire('down', PhysicalKeyboardKey.keyA)).length, 1);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await mouse.removePointer();
  });

  testWidgets('actual logical mapping stays paired with its pressed physical key', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    expect(HardwareKeyboard.instance.lookUpLayout(PhysicalKeyboardKey.capsLock), LogicalKeyboardKey.controlLeft);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.capsLock), wire('up', PhysicalKeyboardKey.capsLock),
      wire('down', PhysicalKeyboardKey.capsLock), wire('down', PhysicalKeyboardKey.keyA),
      wire('up', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.capsLock)]);
    await mouse.removePointer();
  });

  testWidgets('two physical keys mapped to one logical modifier each retain release ownership', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    await down(tester, PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft);
    input.enterOrLeave(false);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.capsLock), wire('down', PhysicalKeyboardKey.controlLeft),
      wire('up', PhysicalKeyboardKey.capsLock), wire('up', PhysicalKeyboardKey.controlLeft)]);
    input.enterOrLeave(true);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    expect(input.ctrl, isTrue);
    await up(tester, PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft);
    expect(input.ctrl, isFalse);
    for (final physical in [PhysicalKeyboardKey.capsLock, PhysicalKeyboardKey.controlLeft]) {
      expect(bridge.keys.where((key) => key == wire('down', physical)).length, 2);
      expect(bridge.keys.where((key) => key == wire('up', physical)).length, 2);
    }
    final count = bridge.keys.length;
    input.enterOrLeave(false);
    expect(bridge.keys.length, count);
    await mouse.removePointer();
  });

  testWidgets('generic Super mapping has the same owned release safety as Meta', (tester) async {
    final mouse = await mount(tester);
    // Mac raw-event simulation only defines left/right Meta. Feed a generic
    // mapping to real HardwareKeyboard and the production InputModel directly.
    // This one ancillary alias case does not claim Mac raw/Focus routing.
    void dispatch(KeyEvent event) {
      HardwareKeyboard.instance.handleKeyEvent(event);
      input.handleKeyEvent(event);
    }
    dispatch(const KeyDownEvent(physicalKey: PhysicalKeyboardKey.metaLeft,
      logicalKey: LogicalKeyboardKey.superKey, timeStamp: Duration.zero));
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    dispatch(const KeyDownEvent(physicalKey: PhysicalKeyboardKey.keyA,
      logicalKey: LogicalKeyboardKey.keyA, timeStamp: Duration.zero));
    dispatch(const KeyUpEvent(physicalKey: PhysicalKeyboardKey.keyA,
      logicalKey: LogicalKeyboardKey.keyA, timeStamp: Duration.zero));
    dispatch(const KeyUpEvent(physicalKey: PhysicalKeyboardKey.metaLeft,
      logicalKey: LogicalKeyboardKey.superKey, timeStamp: Duration.zero));
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.metaLeft), wire('up', PhysicalKeyboardKey.metaLeft),
      wire('down', PhysicalKeyboardKey.metaLeft), wire('down', PhysicalKeyboardKey.keyA),
      wire('up', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.metaLeft)]);
    expect(input.command, isFalse);
    await mouse.removePointer();
  });

  testWidgets('changing to source1 still releases this model\'s prior source2 ownership', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.altRight, LogicalKeyboardKey.altRight);
    bridge.source = 'Input source 1';
    stateGlobal.getInputSource(force: true);
    input.enterOrLeave(false);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.altRight), wire('up', PhysicalKeyboardKey.altRight)]);
    expect(bridge.calls, contains('sessionEnterOrLeave:false'));
    input.enterOrLeave(true);
    await up(tester, PhysicalKeyboardKey.altRight, LogicalKeyboardKey.altRight);
    expect(bridge.keys.length, 2);
    await mouse.removePointer();
  });

  for (final restriction in ['viewOnly', 'camera', 'permission']) {
    testWidgets('$restriction release/regain without a boundary reconciles prior ownership before A', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
      if (restriction == 'viewOnly') session.ffiModel.viewOnly = true;
      if (restriction == 'camera') session.connType = ConnType.viewCamera;
      if (restriction == 'permission') session.ffiModel.keyboard = false;
      await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
      expect(bridge.keys, [wire('down', PhysicalKeyboardKey.shiftLeft)], reason: 'Denied input must emit nothing.');
      expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
      session.ffiModel.viewOnly = false;
      session.connType = ConnType.defaultConn;
      session.ffiModel.keyboard = true;
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      expect(bridge.keys, [wire('down', PhysicalKeyboardKey.shiftLeft), wire('up', PhysicalKeyboardKey.shiftLeft),
        wire('down', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.keyA)]);
      expect(input.shift, isFalse);
      await mouse.removePointer();
    });

    testWidgets('$restriction does not restore or inject pending modifiers', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft);
      input.enterOrLeave(false);
      if (restriction == 'viewOnly') session.ffiModel.viewOnly = true;
      if (restriction == 'camera') session.connType = ConnType.viewCamera;
      if (restriction == 'permission') session.ffiModel.keyboard = false;
      input.enterOrLeave(true);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      expect(bridge.keys, [wire('down', PhysicalKeyboardKey.metaLeft), wire('up', PhysicalKeyboardKey.metaLeft)]);
      input.enterOrLeave(false);
      focus.unfocus();
      await tester.pump();
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
      await up(tester, PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft);
      await mouse.removePointer();
    });
  }

  testWidgets('source1 still delegates entry/exit and emits no Flutter keys', (tester) async {
    bridge.source = 'Input source 1';
    stateGlobal.getInputSource(force: true);
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    expect(bridge.keys, isEmpty);
    expect(bridge.calls, containsAllInOrder(['sessionEnterOrLeave:true', 'sessionEnterOrLeave:false', 'sessionEnterOrLeave:true']));
    await mouse.removePointer();
  });

  testWidgets('source2 retains physical mapping when legacy keyboard mode is selected', (tester) async {
    input.keyboardMode = 'legacy';
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.keyA);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.shiftLeft), wire('up', PhysicalKeyboardKey.shiftLeft),
      wire('down', PhysicalKeyboardKey.shiftLeft), wire('down', PhysicalKeyboardKey.keyA),
      wire('up', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.shiftLeft)]);
    await mouse.removePointer();
  });

  testWidgets('existing relative-mouse Command+G is consumed without replaying its shortcut', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft);
    input.enterOrLeave(false);
    input.enterOrLeave(true);
    input.relativeMouseMode.value = true; // Test the actual existing shortcut handler, not native pointer locking.
    final count = bridge.keys.length;
    await down(tester, PhysicalKeyboardKey.keyG, LogicalKeyboardKey.keyG);
    await up(tester, PhysicalKeyboardKey.keyG, LogicalKeyboardKey.keyG);
    expect(input.relativeMouseMode.value, isFalse);
    // Lifecycle repair owns the consumed G until its exact Up; neither half
    // may reach the peer. Other physical modifier releases remain independent.
    expect(bridge.keys.skip(count), isEmpty);
    await up(tester, PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft);
    expect(bridge.keys.skip(count), isEmpty);
    expect(bridge.remoteHeld, isEmpty);
    await mouse.removePointer();
  });

  const nativeExitLegacyUps = ['Control_L', 'Control_R', 'Alt_L', 'Alt_R',
    'Shift_L', 'Shift_R', 'Meta_L', 'Meta_R'];

  testWidgets('native relative exit releases remapped physical A before clearing ownership', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA)]);
    input.relativeMouseMode.value = true;
    input.exitRelativeMouseModeWithKeyRelease(); // Exact model.dart native event target.
    expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.keyA)]);
    expect(bridge.legacyUps, nativeExitLegacyUps);
    expect(bridge.calls.indexOf('key:${wire('up', PhysicalKeyboardKey.keyA)}'), lessThan(bridge.calls.indexOf('legacy-up:Control_L')));
    expect(input.relativeMouseMode.value, isFalse);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    expect(bridge.keys.length, 2, reason: 'Real Up must not create another down or lose a prior physical release.');
    input.exitRelativeMouseModeWithKeyRelease();
    expect(bridge.keys.length, 2);
    expect(bridge.legacyUps, nativeExitLegacyUps);
    await mouse.removePointer();
  });

  for (var family = 0; family < sides.length; family += 2) {
    final left = sides[family];
    final right = sides[family + 1];
    testWidgets('native relative exit releases both ${left.$1.split(' ').last} sides once', (tester) async {
      final mouse = await mount(tester);
      await down(tester, left.$2, left.$3);
      await down(tester, right.$2, right.$3);
      input.relativeMouseMode.value = true;
      input.exitRelativeMouseModeWithKeyRelease();
      expect(bridge.keys, [wire('down', left.$2), wire('down', right.$2), wire('up', left.$2), wire('up', right.$2)]);
      expect(bridge.legacyUps, nativeExitLegacyUps);
      await up(tester, left.$2, left.$3);
      await up(tester, right.$2, right.$3);
      expect(bridge.keys.length, 4);
      await mouse.removePointer();
    });
  }

  testWidgets('native relative exit owns both physical keys sharing one logical modifier', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    await down(tester, PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft);
    input.relativeMouseMode.value = true;
    input.exitRelativeMouseModeWithKeyRelease();
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.capsLock), wire('down', PhysicalKeyboardKey.controlLeft),
      wire('up', PhysicalKeyboardKey.capsLock), wire('up', PhysicalKeyboardKey.controlLeft)]);
    await up(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    await up(tester, PhysicalKeyboardKey.controlLeft, LogicalKeyboardKey.controlLeft);
    expect(bridge.keys.length, 4);
    await mouse.removePointer();
  });

  for (final restriction in ['permission', 'viewOnly', 'camera', 'source1']) {
    testWidgets('native relative exit releases existing source2 owner after $restriction change', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      input.relativeMouseMode.value = true;
      if (restriction == 'permission') session.ffiModel.keyboard = false;
      if (restriction == 'viewOnly') session.ffiModel.viewOnly = true;
      if (restriction == 'camera') session.connType = ConnType.viewCamera;
      if (restriction == 'source1') {
        bridge.source = 'Input source 1';
        stateGlobal.getInputSource(force: true);
      }
      input.exitRelativeMouseModeWithKeyRelease();
      if (restriction == 'source1') {
        expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.keyA)]);
        expect(bridge.legacyUps, nativeExitLegacyUps);
      } else {
        expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA)]);
        expect(bridge.legacyUps, isEmpty);
      }
      session.ffiModel.keyboard = true;
      session.ffiModel.viewOnly = false;
      session.connType = ConnType.defaultConn;
      bridge.source = 'Input source 2';
      stateGlobal.getInputSource(force: true);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      expect(bridge.keys.length, 2);
      expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
      await mouse.removePointer();
    });
  }

  testWidgets('held remapped modifier restores once after native exit without pointer boundary', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    input.relativeMouseMode.value = true;
    input.exitRelativeMouseModeWithKeyRelease();
    await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.keyA),
      wire('down', PhysicalKeyboardKey.keyA), wire('down', PhysicalKeyboardKey.keyB),
      wire('up', PhysicalKeyboardKey.keyB), wire('up', PhysicalKeyboardKey.keyA)]);
    await mouse.removePointer();
  });

  testWidgets('normal source1 native exit preserves the eight legacy releases only', (tester) async {
    bridge.source = 'Input source 1';
    stateGlobal.getInputSource(force: true);
    final mouse = await mount(tester);
    input.relativeMouseMode.value = true;
    input.exitRelativeMouseModeWithKeyRelease();
    expect(bridge.keys, isEmpty);
    expect(bridge.legacyUps, nativeExitLegacyUps);
    await mouse.removePointer();
  });

  testWidgets('inactive native exit remains a no-op and does not discard live ownership', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    input.exitRelativeMouseModeWithKeyRelease();
    expect(bridge.legacyUps, isEmpty);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA)]);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    expect(bridge.keys, [wire('down', PhysicalKeyboardKey.keyA), wire('up', PhysicalKeyboardKey.keyA)]);
    await mouse.removePointer();
  });

  void denyKeyboard() {
    session.ffiModel.keyboard = false;
    bridge.remoteKeyboardEnabled = false;
  }
  void allowKeyboard() {
    session.ffiModel.keyboard = true;
    bridge.remoteKeyboardEnabled = true;
  }
  void deniedCleanup(String mode) {
    if (mode == 'native exit') {
      input.exitRelativeMouseModeWithKeyRelease();
      input.exitRelativeMouseModeWithKeyRelease();
    } else if (mode == 'leave') {
      input.enterOrLeave(false);
      input.enterOrLeave(false);
    } else {
      input.resetModifiers();
      input.resetModifiers();
    }
  }

  for (final mode in ['native exit', 'leave', 'reset']) {
    testWidgets('server-gated remapped release survives denied $mode and real Up until B', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      input.relativeMouseMode.value = mode == 'native exit';
      denyKeyboard();
      deniedCleanup(mode);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      expect(bridge.remoteHeld, {4}, reason: 'The remote gate has accepted no release while denied.');
      allowKeyboard();
      await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      expect(bridge.remoteHeld, isEmpty, reason: 'An owned release must survive cleanup and flush on regain.');
      expect(bridge.keys, ['down:4', 'up:4', 'down:5', 'up:5']);
      expect(bridge.ignoredKeys, isEmpty, reason: 'No new source2 input may be attempted while denied.');
      await mouse.removePointer();
    });

    testWidgets('server-gated same-logical dual owners survive denied $mode independently', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      await down(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
      input.relativeMouseMode.value = mode == 'native exit';
      denyKeyboard();
      deniedCleanup(mode);
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      await up(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
      expect(bridge.remoteHeld, {4, 57});
      allowKeyboard();
      await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      expect(bridge.remoteHeld, isEmpty);
      expect(bridge.keys, ['down:4', 'down:57', 'up:4', 'up:57', 'down:5', 'up:5']);
      expect(bridge.ignoredKeys, isEmpty);
      await mouse.removePointer();
    });
  }

  for (final action in ['click', 'actual wheel']) {
    testWidgets('server-gated pending Up precedes first permitted $action without a boundary', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      input.relativeMouseMode.value = true;
      denyKeyboard();
      input.exitRelativeMouseModeWithKeyRelease();
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      allowKeyboard();
      bridge.calls.clear();
      if (action == 'click') {
        await input.tapDown(MouseButtons.left);
        await input.tapUp(MouseButtons.left);
      } else {
        input.onPointerSignalImage(const PointerScrollEvent(scrollDelta: Offset(0, 20)));
      }
      expect(bridge.remoteHeld, isEmpty);
      expect(bridge.calls.first, 'key:up:4');
      expect(bridge.calls[1], startsWith('mouse:'));
      expect(bridge.keys, ['down:4', 'up:4']);
      expect(bridge.ignoredKeys, isEmpty);
      await mouse.removePointer();
    });
  }

  testWidgets('server-gated first permitted modifier Up flushes old ownership without a Down', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    input.relativeMouseMode.value = true;
    denyKeyboard();
    input.exitRelativeMouseModeWithKeyRelease();
    allowKeyboard();
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    expect(bridge.remoteHeld, isEmpty);
    expect(bridge.keys, ['down:4', 'up:4']);
    expect(bridge.ignoredKeys, isEmpty);
    await mouse.removePointer();
  });

  for (final mode in ['native exit', 'leave']) {
    testWidgets('server-gated held modifier flushes old Up then restores once after denied $mode', (tester) async {
      final mouse = await mount(tester);
      await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      input.relativeMouseMode.value = mode == 'native exit';
      denyKeyboard();
      deniedCleanup(mode);
      allowKeyboard();
      await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      expect(bridge.keys, ['down:4', 'up:4', 'down:4', 'down:5', 'up:5']);
      expect(bridge.remoteHeld, {4});
      await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
      expect(bridge.remoteHeld, isEmpty);
      expect(bridge.keys.last, 'up:4');
      expect(bridge.ignoredKeys, isEmpty);
      await mouse.removePointer();
    });
  }

  for (final side in sides) {
    testWidgets('server-gated ${side.$1} release survives denied leave/reset', (tester) async {
      final mouse = await mount(tester);
      await down(tester, side.$2, side.$3);
      denyKeyboard();
      input.enterOrLeave(false);
      input.resetModifiers();
      await up(tester, side.$2, side.$3);
      allowKeyboard();
      await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      expect(bridge.remoteHeld, isEmpty);
      expect(bridge.keys, [wire('down', side.$2), wire('up', side.$2), 'down:5', 'up:5']);
      expect(bridge.ignoredKeys, isEmpty);
      await mouse.removePointer();
    });
  }

  testWidgets('server-gated entry/hover flushes an old Meta Up without restoring a bare tap', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.metaRight, LogicalKeyboardKey.metaRight);
    denyKeyboard();
    input.enterOrLeave(false);
    input.resetModifiers();
    allowKeyboard();
    input.enterOrLeave(true);
    input.enterOrLeave(true);
    input.modify({'type': '', 'buttons': '', 'x': '10', 'y': '20'});
    expect(bridge.remoteHeld, isEmpty);
    expect(bridge.keys, ['down:231', 'up:231']);
    await up(tester, PhysicalKeyboardKey.metaRight, LogicalKeyboardKey.metaRight);
    expect(bridge.keys, ['down:231', 'up:231']);
    expect(bridge.ignoredKeys, isEmpty);
    await mouse.removePointer();
  });

  testWidgets('server-gated pending source2 Up precedes source1 native entry without Flutter Down', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    input.relativeMouseMode.value = true;
    denyKeyboard();
    input.exitRelativeMouseModeWithKeyRelease();
    bridge.source = 'Input source 1';
    stateGlobal.getInputSource(force: true);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    input.enterOrLeave(false);
    allowKeyboard();
    bridge.calls.clear();
    input.enterOrLeave(true);
    expect(bridge.remoteHeld, isEmpty);
    expect(bridge.calls.take(2), ['key:up:4', 'sessionEnterOrLeave:true']);
    expect(bridge.keys, ['down:4', 'up:4']);
    expect(bridge.ignoredKeys, isEmpty);
    await mouse.removePointer();
  });

  // This models the separate native wire-ownership barrier terminating its old
  // holds on revoke. It does not qualify native ordering/acknowledgement. The
  // historical mode has no hook call, exactly as the existing actor does today;
  // final mode dynamically calls the actual public production method (no stub).
  const useRevocationHook = bool.fromEnvironment('MIXEL_REVOCATION_HOOK', defaultValue: true);
  void revokeWithWireReset() {
    session.ffiModel.keyboard = false;
    bridge.remoteKeyboardEnabled = false;
    bridge.remoteHeld.clear();
    if (useRevocationHook) {
      (input as dynamic).onKeyboardPermissionRevoked();
    }
  }

  for (final side in sides) {
    testWidgets('native-wire-reset model restores held ${side.$1} after revoke with no denied event', (tester) async {
      final mouse = await mount(tester);
      await down(tester, side.$2, side.$3);
      final before = List<String>.from(bridge.calls);
      revokeWithWireReset();
      revokeWithWireReset();
      expect(bridge.calls, before, reason: 'The revocation hook must not inject Up, Down or any native call.');
      expect(HardwareKeyboard.instance.physicalKeysPressed, contains(side.$2));
      expect(bridge.remoteHeld, isEmpty);
      allowKeyboard();
      await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
      expect(bridge.remoteHeld, {side.$2.usbHidUsage & 0xffff},
        reason: 'First real permitted action restores the physically held modifier after native wire reset.');
      expect(bridge.keys, [wire('down', side.$2), wire('up', side.$2),
        wire('down', side.$2), 'down:5', 'up:5']);
      await up(tester, side.$2, side.$3);
      expect(bridge.remoteHeld, isEmpty);
      expect(bridge.keys.last, wire('up', side.$2));
      expect(bridge.ignoredKeys, isEmpty);
      await mouse.removePointer();
    });
  }

  testWidgets('native-wire-reset model restores held Shift before first actual mouse action', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    revokeWithWireReset();
    allowKeyboard();
    bridge.calls.clear();
    await input.tapDown(MouseButtons.left);
    await input.tapUp(MouseButtons.left);
    expect(bridge.remoteHeld, {225});
    expect(bridge.calls.take(2), ['key:up:225', 'key:down:225']);
    expect(bridge.calls[2], startsWith('mouse:'));
    expect(bridge.keys, ['down:225', 'up:225', 'down:225']);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    expect(bridge.remoteHeld, isEmpty);
    await mouse.removePointer();
  });

  testWidgets('native-wire-reset model retains two remapped same-logical owners through hook', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    await down(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    revokeWithWireReset();
    allowKeyboard();
    await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    expect(bridge.remoteHeld, {4, 57});
    expect(bridge.keys, ['down:4', 'down:57', 'up:4', 'up:57', 'down:4', 'down:57', 'down:5', 'up:5']);
    await up(tester, PhysicalKeyboardKey.keyA, LogicalKeyboardKey.controlLeft);
    expect(bridge.remoteHeld, {57});
    await up(tester, PhysicalKeyboardKey.capsLock, LogicalKeyboardKey.controlLeft);
    expect(bridge.remoteHeld, isEmpty);
    await mouse.removePointer();
  });

  testWidgets('native-wire-reset hook invalidates prior source2 owner while already in source1', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    bridge.source = 'Input source 1';
    stateGlobal.getInputSource(force: true);
    final before = List<String>.from(bridge.calls);
    revokeWithWireReset();
    expect(bridge.calls, before);
    allowKeyboard();
    // Source1 has no Flutter-held restoration. A later real source2 action must
    // still not treat the pre-revoke owner as delivered after native reset.
    bridge.source = 'Input source 2';
    stateGlobal.getInputSource(force: true);
    await down(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    await up(tester, PhysicalKeyboardKey.keyB, LogicalKeyboardKey.keyB);
    expect(bridge.remoteHeld, {225});
    expect(bridge.keys, ['down:225', 'up:225', 'down:225', 'down:5', 'up:5']);
    await up(tester, PhysicalKeyboardKey.shiftLeft, LogicalKeyboardKey.shiftLeft);
    expect(bridge.remoteHeld, isEmpty);
    await mouse.removePointer();
  });

  testWidgets('native-wire-reset model modifier released first after regain creates no fresh Down', (tester) async {
    final mouse = await mount(tester);
    await down(tester, PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft);
    revokeWithWireReset();
    allowKeyboard();
    await up(tester, PhysicalKeyboardKey.metaLeft, LogicalKeyboardKey.metaLeft);
    expect(bridge.remoteHeld, isEmpty);
    expect(bridge.keys, ['down:227', 'up:227']);
    expect([input.shift, input.ctrl, input.alt, input.command], everyElement(isFalse));
    await mouse.removePointer();
  });

  testWidgets('native-wire-reset hook on source1 without UI ownership is release-only no-op', (tester) async {
    bridge.source = 'Input source 1';
    stateGlobal.getInputSource(force: true);
    final mouse = await mount(tester);
    bridge.calls.clear();
    revokeWithWireReset();
    revokeWithWireReset();
    expect(bridge.calls, isEmpty);
    expect(bridge.keys, isEmpty);
    expect(bridge.legacyUps, isEmpty);
    expect(bridge.remoteHeld, isEmpty);
    await mouse.removePointer();
  });
}
