// Run in a real Flutter package after copying the actual UI resource to lib.
// Native window method channels are private test seams; no Rust bridge exists.
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/mixel_startup_failure.dart';

void main() {
  final binding = TestWidgetsFlutterBinding.ensureInitialized();
  final calls = <MethodCall>[];

  setUp(() {
    calls.clear();
    binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('window_manager'), (call) async {
      calls.add(call);
      if (call.method == 'isMinimized' || call.method == 'isVisible') return false;
      return null;
    });
  });

  test('fixed localized text covers German, French, Italian and English', () {
    for (final locale in ['de_CH', 'fr-CH', 'it_CH', 'en_US', 'unknown']) {
      final text = mixelStartupFailureText(locale);
      expect(text.length, 3);
      expect(text.every((value) => value.isNotEmpty), isTrue);
      expect(text[0], contains('Mixel Remote'));
    }
    expect(mixelStartupFailureText('de_CH')[2], 'App schliessen');
    expect(mixelStartupFailureText('fr-CH')[2], 'Fermer l’application');
    expect(mixelStartupFailureText('it_CH')[2], 'Chiudi l’app');
    expect(mixelStartupFailureText('unknown')[2], 'Close app');
    expect(mixelStartupFailureText('de_CH', secondary: true)[2], 'Fenster schliessen');
    expect(mixelStartupFailureText('fr-CH', secondary: true)[2], 'Fermer la fenêtre');
    expect(mixelStartupFailureText('it_CH', secondary: true)[2], 'Chiudi la finestra');
    expect(mixelStartupFailureText('unknown', secondary: true)[2], 'Close window');
  });

  testWidgets('actual failure UI renders before window plugins and releases close', (tester) async {
    await tester.runAsync(() => showMixelStartupFailure());
    await tester.pumpAndSettle();
    expect(find.byType(MixelStartupFailureApp), findsOneWidget);
    expect(find.byType(FilledButton), findsOneWidget);
    expect(find.byIcon(Icons.error_outline), findsOneWidget);
    expect(calls.map((call) => call.method), containsAllInOrder([
      'ensureInitialized', 'setPreventClose', 'setOpacity', 'show', 'focus',
      'setTitle', 'setBounds']));
    expect(calls.firstWhere((call) => call.method == 'setPreventClose').arguments,
        {'isPreventClose': false});
    expect(tester.takeException(), isNull);
  });

  testWidgets('actual failure UI remains visible if window plugin throws', (tester) async {
    binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('window_manager'), (call) async {
      throw PlatformException(code: 'synthetic-unavailable');
    });
    await tester.runAsync(() => showMixelStartupFailure());
    await tester.pumpAndSettle();
    expect(find.byType(MixelStartupFailureApp), findsOneWidget);
    expect(find.byType(FilledButton), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('failed initialization metadata still attempts foreground show and focus', (tester) async {
    binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('window_manager'), (call) async {
      calls.add(call);
      if (call.method == 'ensureInitialized') {
        throw PlatformException(code: 'synthetic-metadata-unavailable');
      }
      if (call.method == 'isMinimized') return false;
      return null;
    });
    await tester.runAsync(() => showMixelStartupFailure());
    await tester.pumpAndSettle();
    expect(calls.map((call) => call.method), containsAllInOrder(['show', 'focus']));
    expect(find.byType(MixelStartupFailureApp), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('secondary failure shows and focuses only its own window', (tester) async {
    final secondaryCalls = <MethodCall>[];
    binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('mixin.one/flutter_multi_window'), (call) async {
      secondaryCalls.add(call);
      return null;
    });
    await tester.runAsync(() => showMixelStartupFailure(windowId: 7));
    await tester.pumpAndSettle();
    expect(secondaryCalls.map((call) => call.method), ['setPreventClose', 'show', 'focus']);
    expect(secondaryCalls.skip(1).map((call) => call.arguments), [7, 7]);
    await tester.tap(find.byType(FilledButton));
    await tester.pumpAndSettle();
    expect(secondaryCalls.map((call) => call.method), ['setPreventClose', 'show', 'focus', 'setPreventClose', 'close']);
    expect(secondaryCalls.last.arguments, 7);
    expect(calls, isEmpty);
    expect(find.byType(MixelStartupFailureApp), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('hung window metadata times out and still attempts foreground display', (tester) async {
    final blocked = Completer<Object?>();
    binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('window_manager'), (call) async {
      calls.add(call);
      if (call.method == 'ensureInitialized') return blocked.future;
      if (call.method == 'isMinimized') return false;
      return null;
    });
    await tester.runAsync(() => showMixelStartupFailure());
    await tester.pumpAndSettle();
    expect(calls.map((call) => call.method), containsAllInOrder(['show', 'focus']));
    expect(find.byType(MixelStartupFailureApp), findsOneWidget);
    expect(tester.takeException(), isNull);
    blocked.complete(null);
  });

  testWidgets('small window and large text scroll without a blank screen or overflow', (tester) async {
    await tester.binding.setSurfaceSize(const Size(320, 220));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    tester.platformDispatcher.textScaleFactorTestValue = 2;
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    await tester.pumpWidget(const MixelStartupFailureApp());
    await tester.pumpAndSettle();
    expect(find.byType(SingleChildScrollView), findsOneWidget);
    expect(find.byType(FilledButton), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.ensureVisible(find.byType(FilledButton));
    expect(tester.takeException(), isNull);
  });
}
