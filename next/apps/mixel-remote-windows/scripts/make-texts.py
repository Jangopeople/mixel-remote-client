#!/usr/bin/env python3
"""make-texts.py: builds src/MixelRemote.Core/Resources/texts.json, the app's German, French and Italian.

Every screen text is an English key, written L("…") in the C# code. The translations come from the same
tables as the other Mixel Remote apps: the desktop app's table (bundled for the Apple apps as
engine-texts.json, also used to translate engine messages), the Apple apps' NATIVE table
(apps/mixel-remote-apple/scripts/make-strings.py) and, for texts only this app has, WINDOWS below
(German in Swiss spelling, Italian with "Lei"). The script fails when a key has no translation, so
nothing ships half-translated. Run after adding or changing screen texts:

    python3 scripts/make-texts.py
"""
import glob
import json
import os
import re
import runpy
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLE = os.path.join(HERE, '..', 'mixel-remote-apple')
TABLES = json.load(open(os.path.join(APPLE, 'Sources/MixelRemoteKit/Resources/engine-texts.json'), encoding='utf-8'))
NATIVE = runpy.run_path(os.path.join(APPLE, 'scripts/make-strings.py'), run_name='tables')['NATIVE']
sys.path.insert(0, os.path.join(HERE, 'scripts'))
from windows_texts import WINDOWS  # noqa: E402

LANGS = ['de', 'fr', 'it']


def keys():
    found = set()
    for path in glob.glob(os.path.join(HERE, 'src/**/*.cs'), recursive=True):
        text = open(path, encoding='utf-8').read()
        for match in re.finditer(r'\bL\("((?:[^"\\]|\\.)*)"', text):
            found.add(match.group(1).replace('\\"', '"').replace('\\\\', '\\'))
    return sorted(found)


def main():
    out = {}
    missing = []
    screen = keys()
    for index, language in enumerate(LANGS):
        texts = dict(TABLES[language]['texts'])
        for key in screen:
            value = WINDOWS.get(key, [None] * 3)[index] or (NATIVE[key][index] if key in NATIVE else None) or texts.get(key)
            if value is None:
                missing.append(f'{language}: {key}')
                continue
            if value.count('%@') + value.count('%d') != key.count('%@') + key.count('%d'):
                missing.append(f'{language}: placeholders differ in {key!r}')
            texts[key] = value
        out[language] = {'texts': texts, 'patterns': TABLES[language]['patterns']}
    if missing:
        print('Missing translations:\n  ' + '\n  '.join(sorted(set(missing))), file=sys.stderr)
        sys.exit(1)
    path = os.path.join(HERE, 'src/MixelRemote.Core/Resources/texts.json')
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(out, file, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    print(f'texts.json: {len(screen)} screen texts in {", ".join(LANGS)}')


if __name__ == '__main__':
    main()
