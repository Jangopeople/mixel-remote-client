#!/usr/bin/env python3
"""Fit remote desktop and file-transfer initial/restored windows to a reported screen work area."""
import argparse
import contextlib
import io
import hashlib
import os
from pathlib import Path
import stat
import tempfile

PIN = '1abc897c451c8b5bbff3792509a7fef9d12f2ce3'
COMMON = 'flutter/lib/common.dart'
MANAGER = 'flutter/lib/utils/multi_window_manager.dart'
GENERATED = 'flutter/lib/utils/mixel_file_transfer_geometry.dart'
PRISTINE = {COMMON: '95ffcc4fdc3d83564d19d5ad7c999adbe65cdc37829cf466d43d5a9684e7f8de',
            MANAGER: 'e94128aaf434d57d54dccb719633704e814b41fee052e142c4fa9ea4a5e13f3f'}
HISTORICAL_COMPOSED = {COMMON: '390d75c80128850d598794502b6f5d265eb99750f64ebfc98140770661cb2cb0',
            MANAGER: '27e065acf8f7feb2c80151437c071689a2dc33f1bdc9b8ce9ecacd8b74e2ede4'}
COMPOSED = {COMMON: '3ee4fd4229ffb0b664c8efdae5f85daf7d316391b86de0074135dbad6b258a3b',
            MANAGER: '27e065acf8f7feb2c80151437c071689a2dc33f1bdc9b8ce9ecacd8b74e2ede4'}
DART = r'''// Mixel session geometry uses window_size reported screen coordinates.
import 'dart:ui';
import 'package:flutter/foundation.dart';
import 'package:window_size/window_size.dart' as window_size;

bool _mixelFiniteRect(Rect rect) =>
    rect.left.isFinite && rect.top.isFinite && rect.right.isFinite &&
    rect.bottom.isFinite && rect.width >= 1 && rect.height >= 1;

Rect fitMixelFileTransferFrame(Rect requested, List<Rect> workAreas,
    {Rect? preferredArea, bool center = false}) {
  final validAreas = workAreas.where(_mixelFiniteRect).toList();
  final desired = _mixelFiniteRect(requested)
      ? requested
      : const Rect.fromLTWH(0, 0, 1280, 720);
  // Without a real display observation, retain the valid requested geometry.
  if (validAreas.isEmpty) return desired;
  var area = validAreas.first;
  if (center && preferredArea != null && validAreas.contains(preferredArea)) {
    area = preferredArea;
  } else {
    double largestIntersection = 0;
    for (final candidate in validAreas) {
      final intersection = desired.intersect(candidate);
      final overlap = intersection.isEmpty ? 0.0 : intersection.width * intersection.height;
      if (overlap > largestIntersection) {
        largestIntersection = overlap;
        area = candidate;
      }
    }
    if (largestIntersection == 0 && preferredArea != null &&
        validAreas.contains(preferredArea)) {
      area = preferredArea;
    }
  }
  final width = desired.width.clamp(1.0, area.width).toDouble();
  final height = desired.height.clamp(1.0, area.height).toDouble();
  final left = center
      ? area.left + (area.width - width) / 2
      : desired.left.clamp(area.left, area.right - width).toDouble();
  final top = center
      ? area.top + (area.height - height) / 2
      : desired.top.clamp(area.top, area.bottom - height).toDouble();
  return Rect.fromLTWH(left, top, width, height);
}

Future<Rect> mixelFileTransferWindowFrame(Rect requested,
    {bool center = false, Future<Rect> Function()? nativeWindowFrame}) async {
  try {
    final screens = await window_size.getScreenList();
    Rect? preferredArea;
    Rect? reportedWindowFrame;
    try {
      final info = await window_size.getWindowInfo();
      preferredArea = info.screen?.visibleFrame;
      reportedWindowFrame = info.frame;
    } catch (_) {
      // A missing current-window screen does not invalidate the screen list.
      // Mac calibration still requires the current engine's reported frame.
    }
    var workAreas = screens.map((screen) => screen.visibleFrame).toList();
    if (nativeWindowFrame != null) {
      final nativeFrame = await nativeWindowFrame();
      if (reportedWindowFrame == null ||
          !_mixelFiniteRect(reportedWindowFrame) ||
          !_mixelFiniteRect(nativeFrame) ||
          (nativeFrame.left - reportedWindowFrame.left).abs() > 0.5 ||
          (nativeFrame.width - reportedWindowFrame.width).abs() > 0.5 ||
          (nativeFrame.height - reportedWindowFrame.height).abs() > 0.5) {
        throw StateError('Current Mac window-frame observations do not match');
      }
      // The pinned Mac plugins use different vertical screen anchors. Convert
      // only reported work areas into the existing native frame coordinate plane;
      // saved frames and explicit-screen requests retain their native semantics.
      final offset = Offset(0, nativeFrame.top - reportedWindowFrame.top);
      workAreas = workAreas.map((area) => area.shift(offset)).toList();
      preferredArea = preferredArea?.shift(offset);
    }
    return fitMixelFileTransferFrame(requested, workAreas,
        preferredArea: preferredArea, center: center);
  } catch (_) {
    debugPrint('Mixel session work area is unavailable; retaining valid frame.');
    return fitMixelFileTransferFrame(requested, const []);
  }
}
'''
IMPORT = "import 'package:flutter_hbb/utils/mixel_file_transfer_geometry.dart';\n"
INITIAL = '''    if (!withScreenRect) {
      windowController
        ..setFrame(const Offset(0, 0) &
            Size(1280 + windowId * 20, 720 + windowId * 20))
        ..center()
        ..setTitle(getWindowNameWithId(
          remoteId,
          overrideType: type,
        ));
'''
INITIAL_NEW = '''    if (!withScreenRect) {
      if (type == WindowType.FileTransfer ||
          type == WindowType.RemoteDesktop) {
        final frame = await mixelFileTransferWindowFrame(
            const Offset(0, 0) &
                Size(1280 + windowId * 20, 720 + windowId * 20),
            center: true,
            nativeWindowFrame: isMacOS
                ? WindowController.fromWindowId(kWindowId ?? kMainWindowId).getFrame
                : null);
        await windowController.setFrame(frame);
        windowController.setTitle(getWindowNameWithId(
          remoteId,
          overrideType: type,
        ));
      } else {
        windowController
          ..setFrame(const Offset(0, 0) &
              Size(1280 + windowId * 20, 720 + windowId * 20))
          ..center()
          ..setTitle(getWindowNameWithId(
            remoteId,
            overrideType: type,
          ));
      }
'''
NO_SAVED = '''      default:
        // No need to change the position of a sub window if no position is saved,
'''
NO_SAVED_NEW = '''      default:
        if (type == WindowType.FileTransfer ||
          type == WindowType.RemoteDesktop) {
          final wc = WindowController.fromWindowId(windowId!);
          final frame = await mixelFileTransferWindowFrame(await wc.getFrame(),
              center: true,
              nativeWindowFrame: isMacOS
                  ? WindowController.fromWindowId(kWindowId ?? kMainWindowId).getFrame
                  : null);
          await wc.setFrame(frame);
        }
        // No need to change the position of a sub window if no position is saved,
'''
SAVED = '''  final size = await _adjustRestoreMainWindowSize(lpos.width, lpos.height);
  final offsetLeftTop = await _adjustRestoreMainWindowOffset(
    lpos.offsetWidth,
    lpos.offsetHeight,
    size.width,
    size.height,
  );
'''
SAVED_NEW = '''  var size = await _adjustRestoreMainWindowSize(lpos.width, lpos.height);
  var offsetLeftTop = await _adjustRestoreMainWindowOffset(
    lpos.offsetWidth,
    lpos.offsetHeight,
    size.width,
    size.height,
  );
  if (type == WindowType.FileTransfer ||
      type == WindowType.RemoteDesktop) {
    final frame = await mixelFileTransferWindowFrame(
        Rect.fromLTWH(lpos.offsetWidth ?? 0, lpos.offsetHeight ?? 0,
            size.width, size.height),
        center: lpos.offsetWidth == null || lpos.offsetHeight == null,
        nativeWindowFrame: isMacOS
            ? WindowController.fromWindowId(kWindowId ?? kMainWindowId).getFrame
            : null);
    size = frame.size;
    offsetLeftTop = frame.topLeft;
  }
'''
REPLACEMENTS = {COMMON: (("import 'dart:convert';\n", "import 'dart:convert';\n" + IMPORT),
                         (NO_SAVED, NO_SAVED_NEW), (SAVED, SAVED_NEW)),
                MANAGER: (("import 'dart:convert';\n", "import 'dart:convert';\n" + IMPORT),
                          (INITIAL, INITIAL_NEW))}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalize(data):
    data.decode('utf-8')
    logical = data.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    if b'\r' in logical or logical.replace(b'\n', newline) != data:
        raise RuntimeError('Mixed file-transfer window newlines')
    return logical, newline


def transform(name, data, restore=False):
    logical, newline = normalize(data)
    original = logical
    for before, after in reversed(REPLACEMENTS[name]):
        b, a = before.encode(), after.encode()
        if a in original:
            if original.count(a) != (2 if name == MANAGER and before == INITIAL else 1):
                raise RuntimeError('Mixed file-transfer window source: ' + name)
            original = original.replace(a, b)
    if sha(original) not in (PRISTINE[name], COMPOSED[name], HISTORICAL_COMPOSED[name]):
        raise RuntimeError('Pinned file-transfer window source changed: ' + name)
    if restore:
        result = original
    else:
        if sha(original) not in (COMPOSED[name], HISTORICAL_COMPOSED[name]):
            raise RuntimeError('File-transfer window requires completed branding: ' + name)
        result = original
        for before, after in REPLACEMENTS[name]:
            b, a = before.encode(), after.encode()
            if result.count(b) != (2 if name == MANAGER and before == INITIAL else 1) or a in result:
                raise RuntimeError('Ambiguous file-transfer window source: ' + name)
            result = result.replace(b, a)
    return result.replace(b'\n', newline)


def safe_path(repo, name, optional=False):
    target = repo / name
    for path in (*reversed(repo.parents), repo):
        record = path.lstat()
        if not stat.S_ISDIR(record.st_mode) or getattr(record, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Unsafe file-transfer window parent')
    parent = repo
    for part in Path(name).parts[:-1]:
        parent /= part
        record = parent.lstat()
        if not stat.S_ISDIR(record.st_mode) or getattr(record, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Unsafe file-transfer window parent')
    if optional and not target.exists() and not target.is_symlink():
        return None
    record = target.lstat()
    if not stat.S_ISREG(record.st_mode) or record.st_nlink != 1 or record.st_size > 500000 or getattr(record, 'st_file_attributes', 0) & 0x400:
        raise RuntimeError('Nonregular file-transfer window source: ' + name)
    return target.read_bytes()


def verify_previous_layers(repo):
    """Validate exact earlier overlay ownership against a read-only FT reversal."""
    with contextlib.redirect_stdout(io.StringIO()):
        apply(repo, restore=True, check=True)
    folder = Path(__file__).absolute().parent
    sdk_data, _ = normalize(safe_path(folder, 'patch-support-flutter-sdk-compatibility.py'))
    if sha(sdk_data) != '25538193da045b58ec9b44cf14f055963b649b3376a48568c221f2eef9da5a82':
        raise RuntimeError('Changed file-transfer prior SDK verifier')
    namespace = {'__name__': 'mixel_file_transfer_prior_sdk', '__file__': str(folder/'patch-support-flutter-sdk-compatibility.py')}
    exec(compile(sdk_data, namespace['__file__'], 'exec'), namespace)
    termination = namespace['load_prior']('patch-support-macos-termination.py', namespace['PRIOR_SHA'])
    cleanup = namespace['load_prior']('patch-support-macos-cleanup.py', namespace['CLEANUP_SHA'])
    names = set(namespace['FILES']) | set(termination['FILES']) | set(cleanup['FILES']) | {'Cargo.toml', COMMON, MANAGER}
    optional = (termination['GENERATED'], cleanup['GENERATED'])
    with tempfile.TemporaryDirectory(prefix='mixel-file-transfer-prior-') as temporary:
        stage = Path(temporary).resolve()
        for name in sorted(names | set(optional)):
            data = safe_path(repo, name, optional=name in optional)
            if data is None:
                continue
            if name in COMPOSED:
                data = transform(name, data, restore=True)
            target = stage/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        for relative in (namespace['OWNED'], 'flutter/.mixel-dependencies/macos_application_exit', cleanup['OWNED']):
            owned = repo/relative
            if not owned.exists() and not owned.is_symlink():
                continue
            safe_path(repo, relative+'/placeholder', optional=True)
            (stage/relative).mkdir(parents=True, exist_ok=True)
            for path in sorted(owned.rglob('*')):
                name = path.relative_to(repo).as_posix(); record = path.lstat()
                if stat.S_ISDIR(record.st_mode) and not getattr(record, 'st_file_attributes', 0) & 0x400:
                    (stage/name).mkdir(parents=True, exist_ok=True)
                else:
                    data = safe_path(repo, name); target=stage/name
                    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        with contextlib.redirect_stdout(io.StringIO()):
            namespace['apply'](stage, restore=True, check=True)


def apply(repo, restore=False, check=False):
    repo = repo.absolute()
    originals = {name: safe_path(repo, name) for name in COMPOSED}
    generated = safe_path(repo, GENERATED, optional=True)
    replacements = {name: transform(name, data, restore) for name, data in originals.items()}
    canonical_originals = {name: transform(name, data, True) for name, data in originals.items()}
    kinds = {sha(normalize(data)[0]) == sha(normalize(canonical_originals[name])[0]) for name, data in originals.items()}
    if len(kinds) != 1:
        raise RuntimeError('Partial file-transfer window overlay')
    corrected = not next(iter(kinds))
    if corrected:
        if generated is None or normalize(generated)[0] != DART.encode():
            raise RuntimeError('Missing or changed owned file-transfer geometry')
    elif generated is not None:
        raise RuntimeError('Unexpected file-transfer geometry source')
    canonical_pair = {name: sha(normalize(data)[0]) for name, data in canonical_originals.items()}
    if canonical_pair not in (PRISTINE, COMPOSED, HISTORICAL_COMPOSED):
        raise RuntimeError('Mixed file-transfer window branding state')
    if not check:
        if not restore and generated is None:
            with (repo / GENERATED).open('xb') as stream:
                stream.write(DART.encode())
        for name, data in replacements.items():
            if data != originals[name]:
                (repo / name).write_bytes(data)
        if restore and corrected:
            (repo / GENERATED).unlink()
    print('Remote desktop/file-transfer geometry verified: initial and saved reported screen coordinates; other window types preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path, nargs='?', default=Path(os.environ.get('RDREPO', './rustdesk')))
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--before-branding', action='store_true')
    args = parser.parse_args()
    if args.before_branding and not (args.restore and args.check):
        parser.error('--before-branding requires --restore --check')
    apply(args.repo, args.restore, args.check)
    if args.before_branding:
        verify_previous_layers(args.repo.absolute())
