#!/usr/bin/env python3
"""Guard Linux terminal-close continuations against a destroyed main window."""
import argparse
import contextlib
import hashlib
from pathlib import Path
import shutil
import stat
import tempfile

PIN = '1abc897c451c8b5bbff3792509a7fef9d12f2ce3'
SCRIPTS = Path(__file__).resolve().parent
PRIOR = '15f2eed4e6278466c948113001d4a3d451f86f36b2f75bcc682fd5ec436e475c'
FRESH = {
    'flutter/lib/common.dart': '95ffcc4fdc3d83564d19d5ad7c999adbe65cdc37829cf466d43d5a9684e7f8de',
    'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd37441c9917f79895985cddc3dbd6862866712548aa37915a1459e7d23863cf5',
}
ORIGINAL = {
    'flutter/lib/common.dart': '95fd0cafc27db22e6b8ae39553bef6b0bdc46364eab7851d33f90813bcd5dd9a',
    'flutter/lib/desktop/widgets/tabbar_widget.dart': '41719ae626738fa169509768aca435f3ee51f05497f8ab185dd2d439bd27aa43',
}
CHANGES = {'flutter/lib/common.dart': [('/// Window status callback\n'
                              'Future<void> onActiveWindowChanged() async {',
                              '// Native close can destroy the Linux view before its Future '
                              'completes.\n'
                              'bool mixelLinuxApplicationExitStarted = false;\n'
                              '\n'
                              '/// Window status callback\n'
                              'Future<void> onActiveWindowChanged() async {'),
                             ('  if (rustDeskWinManager.getActiveWindows().isEmpty) {\n'
                              '    if (isMacOS) {',
                              '  if (rustDeskWinManager.getActiveWindows().isEmpty) {\n'
                              '    if (isLinux) {\n'
                              '      if (mixelLinuxApplicationExitStarted) return;\n'
                              '      mixelLinuxApplicationExitStarted = true;\n'
                              '    }\n'
                              '    if (isMacOS) {'),
                             ('Future<void> saveWindowPosition(WindowType type,\n'
                              '    {int? windowId, bool? flush}) async {',
                              'Future<void> saveWindowPosition(WindowType type,\n'
                              '    {int? windowId, bool? flush, bool mixelLinuxExitSnapshot = '
                              'false}) async {\n'
                              '  bool discardLinuxFrame() =>\n'
                              '      isLinux &&\n'
                              '      type == WindowType.Main &&\n'
                              '      mixelLinuxApplicationExitStarted &&\n'
                              '      !mixelLinuxExitSnapshot;\n'
                              '  if (discardLinuxFrame()) return;'),
                             ('          bind.isIncomingOnly() ? false : await '
                              'windowManager.isMaximized();\n'
                              '      if (isFullscreen || isMaximized) {',
                              '          bind.isIncomingOnly() ? false : await '
                              'windowManager.isMaximized();\n'
                              '      if (discardLinuxFrame()) return;\n'
                              '      if (isFullscreen || isMaximized) {'),
                             ('        position = await windowManager.getPosition(\n'
                              '            ignoreDevicePixelRatio: _ignoreDevicePixelRatio);\n'
                              '        sz = await windowManager.getSize(\n'
                              '            ignoreDevicePixelRatio: _ignoreDevicePixelRatio);',
                              '        position = await windowManager.getPosition(\n'
                              '            ignoreDevicePixelRatio: _ignoreDevicePixelRatio);\n'
                              '        if (discardLinuxFrame()) return;\n'
                              '        sz = await windowManager.getSize(\n'
                              '            ignoreDevicePixelRatio: _ignoreDevicePixelRatio);\n'
                              '        if (discardLinuxFrame()) return;'),
                             ('          saveWindowPosition(WindowType.Main),\n'
                              '          rustDeskWinManager.closeAllSubWindows()',
                              '          saveWindowPosition(WindowType.Main, '
                              'mixelLinuxExitSnapshot: true),\n'
                              '          rustDeskWinManager.closeAllSubWindows()')],
 'flutter/lib/desktop/widgets/tabbar_widget.dart': [('  _saveFrame({bool? flush}) async {\n'
                                                     '    try {',
                                                     '  _saveFrame({bool? flush}) async {\n'
                                                     '    if (isLinux && '
                                                     'mixelLinuxApplicationExitStarted) return;\n'
                                                     '    try {'),
                                                    ('  void onWindowClose() async {\n'
                                                     '    mainWindowClose()',
                                                     '  void onWindowClose() async {\n'
                                                     '    if (isLinux && '
                                                     'mixelLinuxApplicationExitStarted) return;\n'
                                                     '    mainWindowClose()'),
                                                    ('    await _saveFrame(flush: true);\n'
                                                     '\n'
                                                     '    // hide window on close',
                                                     '    await _saveFrame(flush: true);\n'
                                                     '    if (isLinux && '
                                                     'mixelLinuxApplicationExitStarted) return;\n'
                                                     '\n'
                                                     '    // hide window on close'),
                                                    ('        await '
                                                     'rustDeskWinManager.unregisterActiveWindow(kMainWindowId);\n'
                                                     '      }\n'
                                                     '      // macOS specific workaround',
                                                     '        await '
                                                     'rustDeskWinManager.unregisterActiveWindow(kMainWindowId);\n'
                                                     '      }\n'
                                                     '      if (isLinux && '
                                                     'mixelLinuxApplicationExitStarted) return;\n'
                                                     '      // macOS specific workaround')]}
CORRECTED = {'flutter/lib/common.dart': '3fe1d6e6ec8e9d8f2ebafc7ad6b175a0d4eb80dd476b687b6aad9fe673a66be8', 'flutter/lib/desktop/widgets/tabbar_widget.dart': '0c655dca46e5ae8779aa0c64132778355946aaebafc335f9e477e93cb23edab5'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    logical = text.replace('\r\n', '\n')
    if '\r' in logical or ('\r\n' in text and '\n' in text.replace('\r\n', '')):
        raise RuntimeError('Mixed Linux close source newlines')
    return logical, '\r\n' if '\r\n' in text else '\n'


def safe_source(root, name):
    path = root
    for part in Path(name).parts:
        path /= part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Unsafe Linux close source: ' + name)
        if path != root/name and not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('Unsafe Linux close source parent: ' + name)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 500_000:
        raise RuntimeError('Nonregular Linux close source: ' + name)
    return path


def transform(name, text, restore=False):
    pairs = list(reversed(CHANGES[name])) if restore else CHANGES[name]
    for before, after in pairs:
        if restore:
            before, after = after, before
        if text.count(before) != 1:
            raise RuntimeError('Changed Linux close literal: ' + name)
        text = text.replace(before, after, 1)
    return text


def prior_namespace():
    raw = safe_source(SCRIPTS, 'patch-support-macos-gui-launch.py').read_bytes()
    text, _ = canonical(raw)
    if digest(text.encode()) != PRIOR:
        raise RuntimeError('Changed Linux close prior verifier')
    namespace = {'__name__': 'mixel_linux_close_prior',
                 '__file__': str(SCRIPTS/'patch-support-macos-gui-launch.py')}
    exec(compile(text, namespace['__file__'], 'exec'), namespace)
    return namespace


def apply(root, check=False, restore=False, verify=True):
    root = Path(root).absolute()
    for parent in (root, *root.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Unsafe Linux close root')
    texts, hashes = {}, {}
    for name in ORIGINAL:
        text, newline = canonical(safe_source(root, name).read_bytes())
        texts[name] = (text, newline)
        hashes[name] = digest(text.encode())
    matches = [state for state, pins in
               (('fresh', FRESH), ('original', ORIGINAL), ('corrected', CORRECTED))
               if hashes == pins]
    if len(matches) != 1:
        raise RuntimeError('Pinned or partial Linux close profile changed')
    state = matches[0]
    if state == 'fresh' and not restore:
        raise RuntimeError('Linux close final layer requires complete branding')
    planned = []
    for name, (text, newline) in texts.items():
        result = text
        if state == 'corrected' and restore:
            result = transform(name, text, True)
        elif state == 'original' and not restore:
            result = transform(name, text)
        target = ORIGINAL[name] if state != 'fresh' and restore else CORRECTED[name] if state != 'fresh' else FRESH[name]
        if digest(result.encode()) != target:
            raise RuntimeError('Changed Linux close inverse: ' + name)
        planned.append((root/name, result.replace('\n', newline).encode()))
    if verify:
        prior = prior_namespace()
        if state == 'corrected':
            compiler = prior['prior']('patch-support-compiler-cleanup.py')
            compiler['validate_projection_sources'](root)
            with tempfile.TemporaryDirectory(prefix='mixel-linux-close-prior-') as temporary:
                stage = Path(temporary).resolve()/'source'
                shutil.copytree(root, stage, symlinks=True,
                                ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names))
                for name, (text, newline) in texts.items():
                    (stage/name).write_bytes(transform(name, text, True).replace('\n', newline).encode())
                prior['apply'](stage, check=True, restore=True, verify=True)
        else:
            prior['apply'](root, check=True, restore=True, verify=True)
    writes = 0
    for path, raw in planned:
        if not check and path.read_bytes() != raw:
            path.write_bytes(raw)
            writes += 1
    return {'profile': state, 'writes': writes, 'checkedFiles': len(texts)}


def has_layer(root):
    return any((Path(root)/name).is_file() and
               digest(canonical((Path(root)/name).read_bytes())[0].encode()) == expected
               for name, expected in CORRECTED.items())


@contextlib.contextmanager
def previous(root):
    """Expose the exact previous owner tree without modifying supplied source."""
    root = Path(root).absolute()
    apply(root, check=True, restore=True)
    prior = prior_namespace()
    compiler = prior['prior']('patch-support-compiler-cleanup.py')
    originals = {name: safe_source(root, name).read_bytes() for name in ORIGINAL}
    with tempfile.TemporaryDirectory(prefix='mixel-linux-close-inverse-') as temporary:
        stage = Path(temporary).resolve()/'source'
        shutil.copytree(root, stage, symlinks=True,
                        ignore=lambda directory, names: compiler['projection_ignored'](root, directory, names))
        apply(stage, restore=True, verify=False)
        try:
            yield stage
        finally:
            if any(safe_source(root, name).read_bytes() != raw for name, raw in originals.items()):
                raise RuntimeError('Linux close projection mutated supplied source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--before-branding', action='store_true')
    parser.add_argument('--after-branding', action='store_true')
    args = parser.parse_args()
    if args.before_branding and not (args.restore and args.check):
        parser.error('--before-branding requires --restore --check')
    if args.after_branding and args.restore:
        parser.error('--after-branding cannot restore')
    print(apply(args.root, args.check, args.restore))


if __name__ == '__main__':
    main()
