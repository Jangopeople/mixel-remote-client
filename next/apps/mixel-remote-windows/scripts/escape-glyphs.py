#!/usr/bin/env python3
"""escape-glyphs.py: writes icon glyphs (Segoe Fluent Icons, private-use characters) as \\uXXXX escapes in the C# sources, so they stay readable in reviews and diffs."""
import glob, os, re
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in glob.glob(os.path.join(HERE, 'src/**/*.cs'), recursive=True) + glob.glob(os.path.join(HERE, 'tests/**/*.cs'), recursive=True):
    text = open(path, encoding='utf-8').read()
    fixed = re.sub('[-]', lambda m: '\\u%04X' % ord(m.group(0)), text)
    if fixed != text:
        open(path, 'w', encoding='utf-8').write(fixed)
        print('escaped', os.path.relpath(path, HERE))
