#!/usr/bin/env python3
"""Run exact source ownership and asynchronous Linux terminal-close regressions."""
import argparse

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

# Load the exact sibling helper for direct, runpy and file-spec entry points.
_input_lifecycle_spec = importlib.util.spec_from_file_location(
    "mixel_input_lifecycle_projection",
    Path(__file__).with_name("input_lifecycle_integration_projection.py"),
)
input_lifecycle = importlib.util.module_from_spec(_input_lifecycle_spec)
_input_lifecycle_spec.loader.exec_module(input_lifecycle)

SPEC = importlib.util.spec_from_file_location('linux_close_patch', Path(__file__).with_name('patch-support-linux-window-close.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
MANAGER_SHA = 'd739b66bc07a3a15cf52d5279d072befd32846ffec52a66a78cfa41f20673c8f'
FILES = {}


def function(source, anchor):
    start = source.index(anchor)
    first = source.index('{', start + len(anchor) - 1)
    depth, end = 1, first + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class SourceControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mixel-linux-close-test-')
        self.root = Path(self.temporary.name).resolve()/'source'
        self.root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, newline=b'\n', tabbar_newline=None):
        for name, raw in FILES.items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            selected = tabbar_newline if tabbar_newline is not None and 'tabbar_widget' in name else newline
            path.write_bytes(raw.replace(b'\n', selected))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_lf_crlf_exact_inverse_and_idempotence(self):
        for newline, tabbar_newline in ((b'\n', b'\n'), (b'\n', b'\r\n'),
                                        (b'\r\n', b'\n'), (b'\r\n', b'\r\n')):
            self.fixture(newline, tabbar_newline)
            before = self.snapshot()
            self.assertEqual(P.apply(self.root, verify=False)['writes'], 2)
            patched = self.snapshot()
            self.assertEqual(P.apply(self.root, verify=False)['writes'], 0)
            self.assertEqual(self.snapshot(), patched)
            self.assertEqual(P.apply(self.root, restore=True, verify=False)['writes'], 2)
            self.assertEqual(self.snapshot(), before)

    def test_partial_profile_and_drift_refuse_without_writes(self):
        self.fixture()
        first = next(iter(FILES))
        (self.root/first).write_text(P.transform(first, FILES[first].decode()))
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            P.apply(self.root, verify=False)
        self.assertEqual(self.snapshot(), before)
        self.fixture()
        (self.root/first).write_bytes(FILES[first]+b'// drift\n')
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            P.apply(self.root, restore=True, verify=False)
        self.assertEqual(self.snapshot(), before)

    def test_mixed_newlines_refuse_without_writes(self):
        self.fixture()
        first = next(iter(FILES))
        (self.root/first).write_bytes(FILES[first].replace(b'\n', b'\r\n', 1))
        before = self.snapshot()
        with self.assertRaises(RuntimeError):
            P.apply(self.root, verify=False)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_and_hardlink_refuse_without_writes(self):
        self.fixture()
        first = next(iter(FILES))
        path = self.root/first
        external = self.root.parent/'external'
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
        with self.assertRaises(RuntimeError):
            P.apply(self.root, verify=False)
        self.assertEqual(external.read_bytes(), FILES[first])
        path.unlink()
        import os
        os.link(external, path)
        with self.assertRaises(RuntimeError):
            P.apply(self.root, verify=False)
        self.assertEqual(external.read_bytes(), FILES[first])

    def test_all_non_linux_source_bytes_are_conserved(self):
        for name, raw in FILES.items():
            changed = P.transform(name, raw.decode())
            self.assertEqual(P.transform(name, changed, True).encode(), raw)
            for before, after in P.CHANGES[name]:
                inserted = after.replace(before, '') if before in after else after
                self.assertTrue(any(token in inserted for token in ('isLinux', 'mixelLinuxApplicationExitStarted', 'discardLinuxFrame', 'mixelLinuxExitSnapshot')))


PROGRAM = r"""import 'dart:async';
bool isLinux = true, isWindows = false, isMacOS = false;
bool mixelLinuxApplicationExitStarted = false;
const kMainWindowId = 0, kWindowEventHide = 'hide';
int? kWindowId = 0;
WindowType? kWindowType = WindowType.Main;
enum WindowType { Main, RemoteDesktop, ViewCamera }
enum DesktopTabType { main }
late Manager rustDeskWinManager;
late NativeWindow windowManager;
Completer<void>? frameGate;
int saveCalls = 0, macExitCalls = 0;
void debugPrint(String text) {}
void debugPrintStack({required String label}) {}
void periodic_immediate(Duration duration, void Function() callback) {}
Future<void> mixelRequestAppExit() async { macExitCalls++; }
class RdPlatformChannel {
  static final instance = RdPlatformChannel();
  void terminate() {}
}
bool useOriginalSave = false;
Completer<void>? firstPositionGate;
const _ignoreDevicePixelRatio = false, windowFramePrefix = 'frame_';
LastWindowPosition? _lastWindowPosition;
typedef WindowKey = ({WindowType type, int? windowId});
class Offset { const Offset(this.dx, this.dy); final double dx, dy; }
class Size { const Size(this.width, this.height); final double width, height; }
class Rect { final topLeft = const Offset(1, 2), size = const Size(10, 20); }
class LastWindowPosition {
  LastWindowPosition(this.width, this.height, this.offsetWidth, this.offsetHeight,
      this.maximized, this.fullscreen);
  final double? width, height, offsetWidth, offsetHeight;
  final bool maximized, fullscreen;
  static LastWindowPosition? loadFromString(String raw) => null;
  bool equals(LastWindowPosition other) => false;
}
class Debounce {
  bool isRunning = false;
  void cancel() {}
  void call(Future<void> Function() callback) {}
}
final _saveWindowDebounce = Debounce();
class Binding {
  bool isIncomingOnly() => false;
  String getLocalFlutterOption({required String k}) => '';
}
final bind = Binding();
Future<void> _saveWindowPositionActual(WindowKey key) async {}
Future<void> saveWindowPosition(WindowType type,
    {int? windowId, bool? flush, bool mixelLinuxExitSnapshot = false}) =>
    useOriginalSave ? originalSaveWindowPosition(type, windowId: windowId, flush: flush) :
    correctedSaveWindowPosition(type, windowId: windowId, flush: flush,
        mixelLinuxExitSnapshot: mixelLinuxExitSnapshot);
__ORIGINAL_SAVE_POSITION__
__CORRECTED_SAVE_POSITION__
class NativeWindow {
  bool live = true;
  int hideCalls = 0, staleHideCalls = 0, closeCalls = 0, preventCalls = 0;
  int staleQueryCalls = 0, sizeCalls = 0;
  void checkLive() {
    if (!live) { staleQueryCalls++; throw StateError('stale frame query'); }
  }
  Future<bool> isMaximized() async { checkLive(); return false; }
  Future<Offset> getPosition({required bool ignoreDevicePixelRatio}) async {
    checkLive(); saveCalls++;
    if (frameGate != null && saveCalls <= 2) await frameGate!.future;
    if (firstPositionGate != null && saveCalls == 1) await firstPositionGate!.future;
    return const Offset(1, 2);
  }
  Future<Size> getSize({required bool ignoreDevicePixelRatio}) async {
    checkLive(); sizeCalls++; return const Size(10, 20);
  }
  Completer<void>? closeGate;
  Future<void> Function()? reentrantClose;
  Future<void>? reentrantResult;
  Future<void> hide() async {
    hideCalls++;
    if (!live) { staleHideCalls++; throw StateError('stale hide'); }
  }
  Future<void> setPreventClose(bool value) async { preventCalls++; }
  Future<void> close() async {
    closeCalls++;
    live = false; // GTK can invalidate the window before its reply arrives.
    reentrantResult = reentrantClose?.call();
    if (closeGate != null) await closeGate!.future;
  }
  Future<bool> isFullScreen() async => false;
  Future<void> setFullScreen(bool value) async {}
}
class Manager {
  final _activeWindows = <int>{0};
  final _inactiveWindows = <int>{};
  final _windowActiveCallbacks = <Future<void> Function()>[];
  bool mixelApplicationExitStarted = false;
  int cleanupCalls = 0;
  Completer<void>? cleanupGate;
  Future<void> closeAllSubWindows() async {
    cleanupCalls++;
    if (cleanupGate != null) await cleanupGate!.future;
  }
  Future<void> call(WindowType type, String method, Object payload) async {}
__MANAGER__
}
class WindowController {
  static WindowController fromWindowId(int id) => WindowController();
  Future<void> show() async {}
  Future<void> focus() async {}
  Future<void> hide() async {}
  Future<bool> isFullScreen() async => false;
  Future<void> setFullscreen(bool value) async {}
  Future<bool> isMaximized() async => false;
  Future<Rect> getFrame() async => Rect();
}
class Tabs { int length = 0; void clear() {} }
class Exit { bool stopping = false; void poll(Future<void> Function() callback) { callback(); } }
class BoolState { bool isTrue = false; }
class Global {
  final fullscreen = BoolState();
  void setFullscreen(bool value, {bool? procWnd}) {}
}
final stateGlobal = Global();
class Base { Future<void> onWindowClose() async {} }
class Original extends Base {
  bool isMainWindow = true, mounted = true;
  DesktopTabType tabType = DesktopTabType.main;
  final controller = Tabs(), _mixelExit = Exit();
  int _macOSCheckRestoreCounter = 0;
  Timer? _macOSCheckRestoreTimer, _mixelCloseTimer;
  Future<bool> Function()? onWindowCloseButton;
__ORIGINAL_FRAME__
__ORIGINAL_CLOSE__
}
class Corrected extends Base {
  bool isMainWindow = true, mounted = true;
  DesktopTabType tabType = DesktopTabType.main;
  final controller = Tabs(), _mixelExit = Exit();
  int _macOSCheckRestoreCounter = 0;
  Timer? _macOSCheckRestoreTimer, _mixelCloseTimer;
  Future<bool> Function()? onWindowCloseButton;
__CORRECTED_FRAME__
__CORRECTED_CLOSE__
}
__ORIGINAL_ACTIVE__
__CORRECTED_ACTIVE__
void check(bool condition, String message) {
  if (!condition) throw StateError(message);
}
Future<void> tick() => Future<void>.delayed(Duration.zero);
void reset(Future<void> Function() active) {
  isLinux = true; isWindows = false; isMacOS = false;
  mixelLinuxApplicationExitStarted = false;
  frameGate = null; firstPositionGate = null; saveCalls = 0; macExitCalls = 0;
  _lastWindowPosition = null; useOriginalSave = identical(active, originalActive);
  rustDeskWinManager = Manager().._windowActiveCallbacks.add(active);
  windowManager = NativeWindow();
}
Future<void> main() async {
  reset(originalActive);
  final old = Original();
  Object? originalError;
  try { await old.onWindowClose(); } catch (error) { originalError = error; }
  check(originalError is StateError && originalError.toString().contains('stale hide') &&
      windowManager.staleHideCalls == 1 && windowManager.closeCalls == 1,
      'Original actual async continuation did not reproduce stale hide');
  print('PASS: original actual unregister/notify/last-active close resumes a stale native hide');

  reset(correctedActive);
  final corrected = Corrected();
  await corrected.onWindowClose();
  check(mixelLinuxApplicationExitStarted && !windowManager.live &&
      windowManager.closeCalls == 1 && windowManager.hideCalls == 0 &&
      rustDeskWinManager.cleanupCalls == 1, 'Corrected final close accessed dead window');
  print('PASS: corrected final Linux close completes once without any stale hide');

  for (final original in [true, false]) {
    reset(original ? originalActive : correctedActive);
    firstPositionGate = Completer<void>();
    final pendingFrame = original ? Original()._saveFrame() : Corrected()._saveFrame();
    await tick();
    check(saveCalls == 1 && windowManager.sizeCalls == 0,
        'Actual getPosition native reply was not delayed');
    rustDeskWinManager._activeWindows.clear();
    await (original ? originalActive() : correctedActive());
    check(!windowManager.live && windowManager.closeCalls == 1,
        'Concurrent last-active callback did not destroy the native window');
    firstPositionGate!.complete();
    await pendingFrame;
    check(windowManager.staleQueryCalls == (original ? 1 : 0),
        'In-flight actual multi-await frame sequence did not conserve terminal guard');
  }
  print('PASS: original in-flight getPosition resumes stale getSize; corrected actual multi-await frame body refuses the subsequent native query');

  reset(correctedActive);
  final duplicate = Corrected();
  frameGate = Completer<void>();
  rustDeskWinManager.cleanupGate = Completer<void>();
  windowManager.closeGate = Completer<void>();
  windowManager.reentrantClose = duplicate.onWindowClose;
  final first = duplicate.onWindowClose(), second = duplicate.onWindowClose();
  await tick();
  check(saveCalls == 2 && !mixelLinuxApplicationExitStarted, 'Actual frame barrier was not awaited');
  frameGate!.complete();
  await tick();
  check(mixelLinuxApplicationExitStarted && windowManager.live &&
      rustDeskWinManager.cleanupCalls == 1 && windowManager.closeCalls == 0,
      'Duplicate close escaped synchronous terminal admission before cleanup');
  final savesBefore = saveCalls;
  await duplicate.onWindowClose();
  await duplicate._saveFrame();
  check(saveCalls == savesBefore, 'Late queued callback saved the terminal window frame');
  rustDeskWinManager.cleanupGate!.complete();
  await tick();
  check(!windowManager.live && windowManager.closeCalls == 1 &&
      windowManager.hideCalls == 0, 'Reentrant native close touched destroyed window');
  await windowManager.reentrantResult;
  await correctedActive();
  windowManager.closeGate!.complete();
  await Future.wait([first, second]);
  check(windowManager.closeCalls == 1 && windowManager.preventCalls == 1 &&
      windowManager.hideCalls == 0 && rustDeskWinManager.cleanupCalls == 1,
      'Delayed cleanup/native reply or duplicate last-active callback repeated close');
  print('PASS: duplicate pre-close frame awaits, delayed cleanup/reply, reentrant native close and late frame callbacks stay terminal');

  reset(correctedActive);
  rustDeskWinManager._activeWindows.add(1);
  await Corrected().onWindowClose();
  check(!mixelLinuxApplicationExitStarted && windowManager.live &&
      windowManager.hideCalls == 1 && windowManager.closeCalls == 0 &&
      rustDeskWinManager._activeWindows.contains(1), 'Nonfinal main hide lost another active session');
  print('PASS: nonfinal Linux main window hides and preserves another active session');

  for (final platform in ['Windows', 'macOS']) {
    reset(correctedActive);
    isLinux = false; isWindows = platform == 'Windows'; isMacOS = platform == 'macOS';
    rustDeskWinManager._activeWindows.add(1);
    await Corrected().onWindowClose();
    check(!mixelLinuxApplicationExitStarted && windowManager.hideCalls == 1 &&
        windowManager.closeCalls == 0, 'Non-Linux main close behavior changed');
  }
  print('PASS: Windows and macOS nonfinal main hide paths remain unchanged');
}
"""


def dart_control(source, dart, evidence):
    common = FILES['flutter/lib/common.dart'].decode()
    tabbar = FILES['flutter/lib/desktop/widgets/tabbar_widget.dart'].decode()
    manager, _ = P.canonical((source/'flutter/lib/utils/multi_window_manager.dart').read_bytes())
    assert P.digest(manager.encode()) == MANAGER_SHA, 'Changed actual manager source'
    methods = '\n'.join(function(manager, anchor) for anchor in (
        '  Set<int> getActiveWindows() {', '  Future<void> _notifyActiveWindow() async {',
        '  Future<void> unregisterActiveWindow(int windowId) async {'))
    program = PROGRAM.replace('__MANAGER__', methods)
    for label, c, t in (
            ('ORIGINAL', common, tabbar),
            ('CORRECTED', P.transform('flutter/lib/common.dart', common),
             P.transform('flutter/lib/desktop/widgets/tabbar_widget.dart', tabbar))):
        active = function(c, 'Future<void> onActiveWindowChanged() async {')
        active = active.replace('onActiveWindowChanged()', label.lower()+'Active()', 1)
        save_start = c.index('Future<void> saveWindowPosition(WindowType type,')
        save_anchor = c[save_start:c.index(') async {', save_start)+len(') async {')]
        save_position = function(c, save_anchor)
        save_position = save_position.replace('saveWindowPosition(', label.lower()+'SaveWindowPosition(', 1)
        program = program.replace('__'+label+'_SAVE_POSITION__', save_position)
        frame = function(t, '  _saveFrame({bool? flush}) async {')
        close = function(t, '  void onWindowClose() async {')
        # Only expose the actual void-async body's completion to the test driver.
        close = close.replace('void onWindowClose()', 'Future<void> onWindowClose()', 1)
        program = program.replace('__'+label+'_ACTIVE__', active)
        program = program.replace('__'+label+'_FRAME__', frame)
        program = program.replace('__'+label+'_CLOSE__', close)
    path = evidence/'actual-linux-close-control.dart'
    path.write_text(program)
    for label, command in (
            ('analyze', [str(dart), 'analyze', str(path)]),
            ('async', [str(dart), str(path)])):
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (evidence/(label+'.log')).write_text(result.stdout+result.stderr)
        print(result.stdout+result.stderr, end='')
        assert result.returncode == 0, label+' actual source control failed'


def main(_projected_source=None, _input_checked=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Exact fully composed application source')
    parser.add_argument('--dart', type=Path)
    parser.add_argument('--evidence-dir', type=Path)
    args = parser.parse_args()
    if _projected_source is not None:
        args.source = _projected_source
    if not _input_checked:
        with input_lifecycle.current_source(args.source) as previous_input:
            return main(previous_input, _input_checked=True)
    uri_spec = importlib.util.spec_from_file_location('linux_close_uri_fixture', Path(__file__).with_name('patch-support-uri-routing.py'))
    uri = importlib.util.module_from_spec(uri_spec)
    uri_spec.loader.exec_module(uri)
    if uri.has_layer(args.source):
        with uri.previous(args.source) as previous:
            return main(previous, _input_checked=True)
    for name, expected in P.ORIGINAL.items():
        logical, _ = P.canonical((args.source/name).read_bytes())
        if P.digest(logical.encode()) == P.CORRECTED[name]:
            logical = P.transform(name, logical, True)
        assert P.digest(logical.encode()) == expected, 'Changed composed source: '+name
        FILES[name] = logical.encode()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SourceControls))
    assert result.wasSuccessful(), 'Source ownership controls failed'
    with tempfile.TemporaryDirectory(prefix='mixel-linux-close-dart-') as temporary:
        evidence = args.evidence_dir.resolve() if args.evidence_dir else Path(temporary).resolve()
        evidence.mkdir(parents=True, exist_ok=True)
        if args.dart:
            dart_control(args.source, args.dart, evidence)
    print('PASS: exact Linux terminal-close source ownership and composition controls')


if __name__ == '__main__':
    main()
