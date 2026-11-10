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
OWNERS = (*BASE_OWNERS, 'patch-support-receiver-uinput.py',
          'patch-support-receiver-uinput-deps.py', 'patch-support-receiver-sas.py',
          'patch-support-receiver-custodian.py')
LATER_OWNERS = OWNERS[3:]


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


def mac_raw_modifier_workflow(raw, name, restore=True):
    """Project only the exact successor Mac test recipe for historical guards."""
    if name != '.github/workflows/build.yml':
        return raw
    text, newline = canonical(raw)
    before = '      - name: Prove desktop input lifetime regressions\n        run: |\n          python3 scripts/test-support-input-lifecycle-flutter.py \\\n            --source rustdesk --flutter "$(command -v flutter)" \\\n            --fixture scripts/fixtures/input-lifecycle/source2_pointer_modifiers_test.dart \\\n            --expected-input a15f40adcf4887b146b78caa9c183938d1023fcb5dc31e71c64f17093d389b02 \\\n            --expected-fixture 65d294d32c706c8d03e53428d1cae15fb69921ed3d53a9cf03cf6bdbb7a2a7d7 \\\n            --relative-fixture scripts/fixtures/input-lifecycle/relative_mouse_late_enable_test.dart \\\n            --expected-relative-fixture 7e4148cf60fa04cb9c39fc70bb00e1f346e55006089f15242f7ec8fcd05a1ab0 \\\n            --expected-relative-model 99c3f74d8ca12bf27fa9269f74d9490fb7f859106cbc35a554938011d1edf9f6 \\\n            --expected-count 180 \\\n            --context-lock scripts/fixtures/input-lifecycle/flutter-test-context.lock \\\n            --output "$RUNNER_TEMP/mixel-input-lifecycle-flutter"\n'
    after = '      - name: Prove desktop input lifetime regressions\n        run: |\n          python3 scripts/test-support-input-lifecycle-flutter.py \\\n            --source rustdesk --flutter "$(command -v flutter)" \\\n            --fixture scripts/fixtures/input-lifecycle/source2_pointer_modifiers_test.dart \\\n            --expected-input d3a8027e2c49534c7d685a847eeb4709774290d4fa3afa5ae7b717aa17b65607 \\\n            --expected-fixture 37b91c62c3be6d148d6f3f95f781d9108df608c5335630d8c4d20a6c2d4553c4 \\\n            --relative-fixture scripts/fixtures/input-lifecycle/relative_mouse_late_enable_test.dart \\\n            --expected-relative-fixture 7e4148cf60fa04cb9c39fc70bb00e1f346e55006089f15242f7ec8fcd05a1ab0 \\\n            --expected-relative-model 99c3f74d8ca12bf27fa9269f74d9490fb7f859106cbc35a554938011d1edf9f6 \\\n            --expected-count 189 \\\n            --context-lock scripts/fixtures/input-lifecycle/flutter-test-context.lock \\\n            --output "$RUNNER_TEMP/mixel-input-lifecycle-flutter"\n'
    if text.count(before) + text.count(after) != 1:
        raise ValueError('Changed/missing/duplicate Mac raw-modifier test recipe')
    selected = after if text.count(after) else before
    # The complete block must remain in the shipping Mac job. Other drift is
    # preserved for the existing complete historical workflow hash to reject.
    mac = text.index('  macos:\n')
    windows = text.index('  windows:\n', mac)
    if not mac < text.index(selected) < windows:
        raise ValueError('Moved Mac raw-modifier test recipe')
    wanted = before if restore else after
    return text.replace(selected, wanted, 1).replace('\n', newline).encode()


# The Windows elevation layer wraps the whole receiver chain in apply-branding:
# it is taken out before the first owner and applied after the last one. Its
# two exact invocation blocks are an outer view of this file, like the workflow
# policy view: removed before the reviewed hunks are inverted, added back after
# they are composed. Any other spelling or a single block stays fatal.
ELEVATION_FILE = 'scripts/apply-branding.sh'
ELEVATION_FIRST = (
    '# The Windows elevation layer is the outermost one. Take it out first so every\n'
    '# earlier owner sees its own output on a repeated branding pass.\n'
    'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-elevation.py" --restore --check "$RDREPO"\n'
    'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-elevation.py" --restore --guarded "$RDREPO"\n'
    '\n')
ELEVATION_FIRST_ANCHOR = '# Validate the disjoint lifecycle pair without changing any source before the\n'
ELEVATION_LAST_ANCHOR = 'echo "✓ Branding applied — product identity is $APP_NAME only."\n'
ELEVATION_LAST = (
    '\n'
    '# Quick Support elevation: the session outlives the helper switch and the\n'
    '# consented SYSTEM helper is accepted. Applied after every receiver layer.\n'
    'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-elevation.py" --check "$RDREPO"\n'
    'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-elevation.py" "$RDREPO"\n'
    'echo "✓ Windows elevation layer applied."\n')


def elevation_view(text, name, restore):
    if name != ELEVATION_FILE:
        return text
    first = ELEVATION_FIRST + ELEVATION_FIRST_ANCHOR
    last = ELEVATION_LAST_ANCHOR + ELEVATION_LAST
    mentions = text.count('patch-support-elevation.py')
    if restore:
        if mentions == 0:
            return text
        if text.count(first) != 1 or text.count(last) != 1 or mentions != 4:
            raise ValueError('Changed/missing/moved elevation invocation: ' + name)
        return text.replace(last, ELEVATION_LAST_ANCHOR, 1).replace(first, ELEVATION_FIRST_ANCHOR, 1)
    if mentions or text.count(ELEVATION_FIRST_ANCHOR) != 1 or text.count(ELEVATION_LAST_ANCHOR) != 1:
        raise ValueError('Duplicated or unanchored elevation invocation: ' + name)
    return text.replace(ELEVATION_FIRST_ANCHOR, first, 1).replace(ELEVATION_LAST_ANCHOR, last, 1)

def project(raw, name, restore):
    """Inverse only exact ordered hunks, preserving other layers for their guards."""
    reviewed = plan()
    if name not in reviewed['files']:
        raise ValueError('Unknown receiver integration file')
    workflow = name.startswith('.github/workflows/')
    if restore and workflow:
        raw = workflow_policy().project_to_historical(raw, name)
        raw = mac_raw_modifier_workflow(raw, name, restore=True)
    text, newline = canonical(raw)
    if restore:
        text = elevation_view(text, name, restore=True)
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
    if not restore:
        text = elevation_view(text, name, restore=False)
    result = text.replace('\n', newline).encode()
    if workflow and not restore:
        result = workflow_policy().compose_manual(result, name)
        result = mac_raw_modifier_workflow(result, name, restore=False)
    return result


def workflow_policy():
    """Strict policy source view; old receiver pins and hunks remain unchanged."""
    spec = importlib.util.spec_from_file_location(
        'mixel_receiver_manual_workflow_policy', HERE / 'manual_workflow_policy_projection.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
def _outer_previous(root, names, private=False):
    """Inverse newer complete owners in one copy; never recover from drift."""
    if not names:
        yield root, private
        return
    outer = owner(names[0])
    if outer.is_current(root):
        if private:
            outer.apply(root, restore=True, check=True)
            outer.apply(root, restore=True)
            with _outer_previous(root, names[1:], True) as result:
                yield result
        else:
            with outer.previous(root) as stage:
                with _outer_previous(stage, names[1:], True) as result:
                    yield result
    else:
        with _outer_previous(root, names[1:], private) as result:
            yield result


@contextlib.contextmanager
def before_receiver(root):
    """Strictly invert only later sink owners for the receiver's own controls."""
    root = Path(root).absolute()
    with contextlib.ExitStack() as stack:
        base, copied = stack.enter_context(_without_elevation(root))
        stage, private = stack.enter_context(_outer_previous(base, tuple(reversed(LATER_OWNERS)), copied))
        uinput = owner(OWNERS[2])
        if uinput.inspect(stage)[3] != 'after':
            yield stage
        elif private:
            uinput.apply(stage, restore=True, check=True)
            uinput.apply(stage, restore=True)
            yield stage
        else:
            with uinput.previous(stage) as prior:
                yield prior


@contextlib.contextmanager
def _base_previous(root, private, expected_final):
    receiver, native, final = checked_owners(root)
    if final != expected_final:
        raise RuntimeError('Inconsistent exact receiver/device-sink prefix')
    if not final:
        yield root
    elif private:
        receiver.apply(root, restore=True, check=True)
        native.apply(root, restore=True, check=True)
        receiver.apply(root, restore=True)
        native.apply(root, restore=True)
        yield root
    else:
        with receiver.previous(root) as stage:
            native.apply(stage, restore=True)
            yield stage


@contextlib.contextmanager
def _device_previous(root, private=False):
    uinput = owner(OWNERS[2])
    profile = uinput.inspect(root)[3]
    if profile == 'after':
        if private:
            uinput.apply(root, restore=True, check=True)
            uinput.apply(root, restore=True)
            with _base_previous(root, True, True) as prior:
                yield prior
        else:
            with uinput.previous(root) as stage:
                with _base_previous(stage, True, True) as prior:
                    yield prior
    else:
        with _base_previous(root, private, profile == 'before') as prior:
            yield prior


def elevation_owner():
    spec = importlib.util.spec_from_file_location(
        'mixel_receiver_elevation_layer', HERE / 'patch-support-elevation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _without_elevation(root):
    """The elevation layer sits above every receiver owner: step under it first."""
    layer = elevation_owner()
    if layer.is_current(root):
        with layer.previous(root) as stage:
            yield stage, True
    else:
        yield root, False


@contextlib.contextmanager
def previous(root):
    """Remove exact terminal layers, latest owner first, in one private copy."""
    root = Path(root).absolute()
    with _without_elevation(root) as (base, copied):
        with _outer_previous(base, tuple(reversed(LATER_OWNERS)), copied) as (stage, private):
            with _device_previous(stage, private) as prior:
                yield prior


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
    if elevation_owner().is_current(root):
        elevation_owner().restore_in_place(root)
    for name in reversed(OWNERS[2:]):
        layer = owner(name)
        layer.apply(root, restore=True, check=True)
        layer.apply(root, restore=True)
    receiver, native, _ = checked_owners(root)
    receiver.apply(root, restore=True, check=True)
    native.apply(root, restore=True, check=True)
    receiver.apply(root, restore=True)
    native.apply(root, restore=True)
    if any(owner(name).is_current(root) for name in OWNERS):
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
