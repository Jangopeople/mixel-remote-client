"""Strict terminal receiver integration; historical whole-source guards stay fatal.

The terminal owners are inverted in private copies before historical component
fixtures inspect their exact original sources. Their own compiled fixtures test
the new receiver and receipt implementations separately.
"""
import contextlib
import hashlib
import importlib.util
import json
import stat
from pathlib import Path


HERE = Path(__file__).absolute().parent
PLAN = HERE / 'receiver-input-integration-plan.json'
BASE_OWNERS = ('patch-support-receiver-input.py', 'patch-support-receiver-native.py')
OWNERS = (*BASE_OWNERS, 'patch-support-receiver-uinput.py')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    text = raw.decode('utf-8', errors='strict')
    logical = text.replace('\r\n', '\n')
    newline = '\r\n' if '\r\n' in text else '\n'
    if '\r' in logical or logical.replace('\n', newline) != text:
        raise ValueError('Mixed receiver integration source newlines')
    return logical, newline


def regular(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('Unsafe receiver integration source: ' + str(path))
    return path.read_bytes()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate receiver integration plan key')
        result[key] = value
    return result


def plan():
    text, _ = canonical(regular(PLAN))
    result = json.loads(text, object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError('Nonfinite receiver integration plan value')))
    if (set(result) != {'schema', 'owners', 'files', 'targets'}
            or result['schema'] != 'mixel-receiver-integration-v1'
            or set(result['owners']) != set(OWNERS)
            or set(result['files']) != {'scripts/apply-branding.sh',
                '.github/workflows/build.yml', '.github/workflows/preflight.yml'}):
        raise ValueError('Invalid receiver integration plan')
    for name, pin in result['owners'].items():
        logical, _ = canonical(regular(HERE / name))
        if digest(logical.encode()) != pin:
            raise ValueError('Changed exact receiver integration owner: ' + name)
    return result


def owner(name):
    reviewed = plan()
    if name not in reviewed['owners']:
        raise ValueError('Unknown receiver integration owner')
    spec = importlib.util.spec_from_file_location(
        'mixel_receiver_prior_' + name.replace('-', '_'), HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(raw, name, restore):
    """Inverse only exact ordered hunks, preserving other layers for their guards."""
    reviewed = plan()
    if name not in reviewed['files']:
        raise ValueError('Unknown receiver integration file')
    text, newline = canonical(raw)
    changes = reviewed['files'][name]['changes']
    positions = []
    for change in changes:
        if set(change) != {'old', 'new'} or not change['old'] or not change['new']:
            raise ValueError('Invalid receiver integration hunk')
        expected, replacement = ((change['new'], change['old']) if restore else
                                 (change['old'], change['new']))
        if text.count(expected) != 1:
            raise ValueError('Changed/missing/moved receiver integration hunk: ' + name)
        positions.append((text.index(expected), len(expected)))
        if not restore and replacement in text:
            raise ValueError('Duplicated receiver integration hunk: ' + name)
    if any(a + length > b for (a, length), (b, _) in zip(positions, positions[1:])):
        raise ValueError('Reordered/overlapping receiver integration hunks: ' + name)
    for change in reversed(changes):
        expected, replacement = ((change['new'], change['old']) if restore else
                                 (change['old'], change['new']))
        text = text.replace(expected, replacement, 1)
    if restore and any(token in text for token in
            (*OWNERS, 'receiver_native_only', '  receiver-native:')):
        raise ValueError('Additional unreviewed receiver integration invocation: ' + name)
    return text.replace('\n', newline).encode()


def compose(raw, name):
    return project(raw, name, restore=False)


def inverse(raw, name):
    return project(raw, name, restore=True)


def committed(raw, name):
    """Only the exact pre-terminal HEAD is a historical committed-file carrier."""
    reviewed = plan()
    if name not in reviewed['files']:
        raise ValueError('Unknown receiver committed integration file')
    text, _ = canonical(raw)
    if digest(text.encode()) == reviewed['files'][name]['prior_sha256']:
        return raw
    return inverse(raw, name)


def checked_owners(root):
    """Inspect both complete owners before any copy or inverse can start."""
    receiver, native = (owner(name) for name in BASE_OWNERS)
    receiver_final = receiver.is_current(root)
    native_final = native.is_current(root)
    if receiver_final != native_final:
        raise RuntimeError('Partial receiver/native terminal composition')
    return receiver, native, receiver_final


@contextlib.contextmanager
def before_receiver(root):
    """Strictly invert only later device-sink ownership for receiver controls."""
    root = Path(root).absolute()
    uinput = owner(OWNERS[2])
    profile = uinput.inspect(root)[3]
    if profile == 'after':
        with uinput.previous(root) as stage:
            yield stage
    else:
        yield root


@contextlib.contextmanager
def previous(root):
    """Remove exact terminal layers in one private copy, latest owner first."""
    root = Path(root).absolute()
    uinput = owner(OWNERS[2])
    profile = uinput.inspect(root)[3]
    if profile == 'after':
        with uinput.previous(root) as stage:
            receiver, native, final = checked_owners(stage)
            if not final:
                raise RuntimeError('Device sink requires both complete receiver layers')
            receiver.apply(stage, restore=True, check=True)
            native.apply(stage, restore=True, check=True)
            receiver.apply(stage, restore=True)
            native.apply(stage, restore=True)
            yield stage
        return
    receiver, native, final = checked_owners(root)
    if final != (profile == 'before'):
        raise RuntimeError('Inconsistent exact receiver/device-sink prefix')
    if not final:
        yield root
        return
    with receiver.previous(root) as stage:
        native.apply(stage, restore=True)
        yield stage


def prior_owner():
    path = HERE / 'patch-support-input-lifecycle.py'
    spec = importlib.util.spec_from_file_location('mixel_receiver_historical_guard', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_priors(root):
    """Run the unchanged whole prior chain before any supplied-tree inverse."""
    with previous(root) as prior:
        prior_owner().apply(prior, restore=True, check=True)


def restore_private(root):
    """Explicit in-place inverse for a caller-owned historical negative fixture."""
    # Check the entire new chain in a private copy before the supplied fixture
    # can change. A later malformed inner owner cannot leave an outer inverse.
    with previous(root):
        pass
    uinput = owner(OWNERS[2])
    uinput.apply(root, restore=True, check=True)
    uinput.apply(root, restore=True)
    receiver, native, _ = checked_owners(root)
    receiver.apply(root, restore=True, check=True)
    native.apply(root, restore=True, check=True)
    receiver.apply(root, restore=True)
    native.apply(root, restore=True)
    if receiver.is_current(root) or native.is_current(root) or uinput.is_current(root):
        raise RuntimeError('Receiver private fixture inverse did not retire every terminal layer')


def owned_targets():
    return frozenset(plan()['targets'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--check-priors', action='store_true', required=True)
    arguments = parser.parse_args()
    check_priors(arguments.root)
    print('PASS: exact receiver inverse preserves the complete historical no-write refusal chain')
