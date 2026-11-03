"""Exact source views of the manual-only workflow trigger policy.

Only the fourteen reviewed trigger headers and the two exact frozen-controller
test command substitutions can change. Historical source controls receive their
original bytes; every other source byte continues to their whole-file guards.
The controller's complete original workflow hash remains mandatory. This module performs
no workflow dispatch, provider request, compilation, or workflow-file write.
"""
import hashlib
import json
from pathlib import Path
import re
import stat


ROOT = Path(__file__).absolute().parents[1]
PLAN = Path(__file__).with_name('manual-workflow-policy-plan.json')
PLAN_SHA256 = '264ae9926c4850fa1fa12c85208c166a3d120b10c3977c74092bae59399acb58'
HEADER = re.compile(r'on:\n(?:(?:[ \t].*|#.*)?\n)*')
ON_KEYS = re.compile(r'''(?m)^(?:on|'on'|"on")[ \t]*:.*$''')


def canonical(raw):
    logical = raw.replace(b'\r\n', b'\n')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    if b'\r' in logical or logical.replace(b'\n', newline) != raw:
        raise ValueError('Mixed manual-workflow policy source newlines')
    return logical.decode('utf-8', errors='strict'), newline


def regular(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('Unsafe manual-workflow policy source: ' + str(path))
    return path.read_bytes()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate manual-workflow policy plan key')
        result[key] = value
    return result


def plan():
    text, _ = canonical(regular(PLAN))
    if hashlib.sha256(text.encode()).hexdigest() != PLAN_SHA256:
        raise ValueError('Changed exact manual-workflow policy plan')
    value = json.loads(text, object_pairs_hook=unique,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError('Nonfinite manual-workflow policy plan value')))
    if (set(value) != {'schema', 'workflows'}
            or value['schema'] != 'mixel-manual-workflow-policy-v1'
            or len(value['workflows']) != 14):
        raise ValueError('Invalid manual-workflow policy plan')
    for name, row in value['workflows'].items():
        if (not re.fullmatch(r'\.github/workflows/[a-z0-9-]+\.yml', name)
                or set(row) != {'prefix', 'historicalHeader', 'manualHeader',
                                'bodyChanges', 'historicalSHA256'}
                or row['historicalHeader'] == row['manualHeader']):
            raise ValueError('Invalid manual-workflow policy header binding')
    return value['workflows']


def split(raw, name):
    reviewed = plan()
    if name not in reviewed:
        raise ValueError('Unknown manual-workflow policy file: ' + name)
    row = reviewed[name]
    text, newline = canonical(raw)
    prefix = row['prefix']
    if (ON_KEYS.findall(text) != ['on:'] or not text.startswith(prefix + 'on:\n')
            or re.search(r'(?m)^<<[ \t]*:', text)):
        raise ValueError('Duplicate, moved, or changed manual-workflow policy header: ' + name)
    match = HEADER.match(text, len(prefix))
    if match is None:
        raise ValueError('Missing manual-workflow policy header: ' + name)
    header = match.group()
    kinds = [kind for kind in ('historicalHeader', 'manualHeader') if row[kind] == header]
    if len(kinds) != 1:
        raise ValueError('Unreviewed manual-workflow policy header: ' + name)
    remainder = text[match.end():]
    positions = []
    for hunk in row['bodyChanges']:
        old, new = (hunk['new'], hunk['old']) if kinds[0] == 'manualHeader' else (hunk['old'], hunk['new'])
        if remainder.count(old) != 1 or new in remainder:
            raise ValueError('Changed/missing/duplicate frozen-controller policy command: ' + name)
        positions.append((remainder.index(old), len(old)))
    if any(start + length > following for (start, length), (following, _) in zip(positions, positions[1:])):
        raise ValueError('Moved frozen-controller policy commands: ' + name)
    if row['historicalSHA256'] is not None:
        previous = remainder
        if kinds[0] == 'manualHeader':
            for hunk in row['bodyChanges']:
                previous = previous.replace(hunk['new'], hunk['old'], 1)
        historical = row['prefix'] + row['historicalHeader'] + previous
        if hashlib.sha256(historical.encode()).hexdigest() != row['historicalSHA256']:
            raise ValueError('Changed complete frozen-controller workflow bytes: ' + name)
    return row, kinds[0], remainder, newline


def project_to_historical(raw, name):
    """Restore only the exact reviewed manual header; old internal views repeat."""
    row, kind, remainder, newline = split(raw, name)
    if kind == 'manualHeader':
        for hunk in row['bodyChanges']:
            remainder = remainder.replace(hunk['new'], hunk['old'], 1)
    text = row['prefix'] + row['historicalHeader'] + remainder
    return text.encode().replace(b'\n', newline)


def compose_manual(raw, name):
    """Apply the exact trigger removal to an exact historical header once."""
    row, kind, remainder, newline = split(raw, name)
    if kind != 'historicalHeader':
        raise ValueError('Manual-workflow policy is already composed: ' + name)
    for hunk in row['bodyChanges']:
        remainder = remainder.replace(hunk['old'], hunk['new'], 1)
    text = row['prefix'] + row['manualHeader'] + remainder
    return text.encode().replace(b'\n', newline)


def verify_active(root=None):
    """Require every live workflow to carry only its reviewed manual header."""
    root = ROOT if root is None else Path(root)
    reviewed = plan()
    directory = root / '.github/workflows'
    names = {str(path.relative_to(root)) for pattern in ('*.yml', '*.yaml')
             for path in directory.glob(pattern)}
    if names != set(reviewed):
        raise ValueError('Changed active manual-workflow policy inventory')
    for name in sorted(names):
        _, kind, _, _ = split(regular(root / name), name)
        if kind != 'manualHeader':
            raise ValueError('Automatic historical workflow header is not active policy: ' + name)
    return tuple(sorted(names))


if __name__ == '__main__':
    print('PASS: all ' + str(len(verify_active())) + ' active workflows are explicit manual break-glass only')
