#!/usr/bin/env python3
"""Correct pinned Linux uinput setup ABI/errors and route Back/Forward buttons."""
import argparse
import hashlib
from pathlib import Path
import stat

TARGET = 'src/server/uinput.rs'
PIN = '1abc897c451c8b5bbff3792509a7fef9d12f2ce3'
BASE_HASHES = ('ef9dfa4600f15d5ae05465560af8ca584e2b525108b32e270bd80f12072b1a57', '95e5cf0fd0f33cd3a4e81588d8a935734db1bff98ea67fb21ef3d9b3936e3563', '58074c8c3e35db15ed44515805ceae82a14f329cf545554c51648c32255759f7', '8ea8e45ade030b56852ecc7c71fd69418b8fecbd116f2b1365f5ce5e4671fe3f')
OLD_FIELD = '        ff_effects_max: c_ulong,\n'
NEW_FIELD = '        ff_effects_max: c_uint,\n'
MACRO = '''            // Preserve the immediate kernel error before partial-device Drop.
            macro_rules! checked_ioctl {
                ($($argument:expr),* $(,)?) => {{
                    if ioctl($($argument),*) < 0 {
                        return Err(Error::last_os_error());
                    }
                }};
            }
'''

CAPABILITY = '                checked_ioctl!(fd, UI_SET_KEYBIT, BTN_MIDDLE);\n'
EXTRA_CAPABILITIES = '''                checked_ioctl!(fd, UI_SET_KEYBIT, BTN_SIDE);
                checked_ioctl!(fd, UI_SET_KEYBIT, BTN_EXTRA);
'''
MAP = '                    enigo::MouseButton::Right => mouce::MouseButton::Right,\n'
EXTRA_MAP = '''                    enigo::MouseButton::Back => mouce::MouseButton::Side,
                    enigo::MouseButton::Forward => mouce::MouseButton::Extra,
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def block(text, marker):
    begin = text.index(marker)
    opening = text.index('{', begin)
    depth = 1
    for end in range(opening + 1, len(text)):
        depth += (text[end] == '{') - (text[end] == '}')
        if depth == 0:
            return text[begin:end + 1]
    raise RuntimeError('Incomplete uinput constructor')


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    normalized = text.replace('\r\n', '\n')
    if '\r' in normalized or ('\r\n' in text and '\n' in text.replace('\r\n', '')):
        raise RuntimeError('Mixed uinput device source newlines')
    return normalized, '\r\n' if '\r\n' in text else '\n'


def inverse(text):
    constructor = block(text, 'pub fn new(rng_x:')
    if text.count(NEW_FIELD) != 1 or text.count(OLD_FIELD) or constructor.count(MACRO) != 1 \
            or constructor.count('checked_ioctl!(') != 18 or text.count(EXTRA_CAPABILITIES) != 1 or text.count(EXTRA_MAP) != 3:
        raise RuntimeError('Mixed uinput device correction')
    original_constructor = constructor.replace(EXTRA_CAPABILITIES, '', 1).replace(MACRO, '', 1).replace('checked_ioctl!(', 'ioctl(')
    return text.replace(constructor, original_constructor, 1).replace(NEW_FIELD, OLD_FIELD, 1).replace(EXTRA_MAP, '')


def transform(raw, restore=False):
    text, newline = canonical(raw)
    original = inverse(text) if MACRO in text or NEW_FIELD in text or 'checked_ioctl!(' in text or EXTRA_MAP in text or EXTRA_CAPABILITIES in text else text
    if sha(original.encode()) not in BASE_HASHES or original.count(OLD_FIELD) != 1:
        raise RuntimeError('Pinned uinput device source changed')
    constructor = block(original, 'pub fn new(rng_x:')
    if constructor.count('ioctl(') != 16 or constructor.count('            let manager = UInputMouseManager {') != 1:
        raise RuntimeError('Pinned uinput setup arguments changed')
    new_constructor = constructor.replace('ioctl(', 'checked_ioctl!(').replace(
        '            let manager = UInputMouseManager {', MACRO + '            let manager = UInputMouseManager {', 1)
    corrected = original.replace(constructor, new_constructor, 1).replace(OLD_FIELD, NEW_FIELD, 1)
    if corrected.count(CAPABILITY) != 1 or corrected.count(MAP) != 3:
        raise RuntimeError('Pinned uinput button arguments changed')
    corrected = corrected.replace(CAPABILITY, CAPABILITY + EXTRA_CAPABILITIES, 1).replace(MAP, MAP + EXTRA_MAP)
    if inverse(corrected) != original:
        raise RuntimeError('Uinput device exact source reversal failed')
    return (original if restore else corrected).replace('\n', newline).encode('utf-8')


def apply(root, check=False, restore=False):
    root = Path(root).absolute()
    target = root / TARGET
    for path in [*reversed(target.parents), target]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Unsafe uinput device source ownership')
        if path == target:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError('Uinput device source must be a single-link regular file')
        elif not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('Unsafe uinput device source parent')
    raw = target.read_bytes()
    corrected = transform(raw, restore)
    if not check and raw != corrected:
        target.write_bytes(corrected)
    return sha(corrected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    apply(args.source, args.check, args.restore)
    print('PASS pinned uinput setup ABI and all18 immediate ioctl checks; Back/Forward routing')


if __name__ == '__main__':
    main()
