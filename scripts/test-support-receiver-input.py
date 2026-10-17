#!/usr/bin/env python3
"""Source ownership, exact inverse, and failure-closed receiver migration controls."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).absolute().parent


def helper():
    spec = importlib.util.spec_from_file_location('mixel_receiver_input_patch', HERE / 'patch-support-receiver-input.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def must_refuse(module, root, operation=None):
    before = snapshot(root)
    try:
        (operation or (lambda: module.apply(root)))()
    except (RuntimeError, FileNotFoundError):
        pass
    else:
        raise AssertionError('Changed/partial source was accepted')
    assert snapshot(root) == before, 'Refusal changed supplied source'


def source_contracts(module):
    plan = module.load_plan()
    assert set(plan['profiles']) == {'fresh', 'original', 'final', 'previous'}
    assert all(set(v) == set(plan['profiles']['fresh']) for v in plan['profiles'].values())
    assert set(plan['changes']) == set(plan['inverse']) == set(plan['profiles']['fresh']) - set(module.LEAVES)
    for name, pairs in plan['changes'].items():
        assert len(pairs) == len(plan['inverse'][name])
        for (before, after), (undo_before, undo_after) in zip(pairs, plan['inverse'][name]):
            assert before and undo_before and undo_after
            assert undo_before.replace(after, before, 1) == undo_after if after else before in undo_after
    core = (HERE / 'support-receiver-input.rs').read_text(encoding='utf-8')
    windows = (HERE / 'support-receiver-input-windows.rs').read_text(encoding='utf-8')
    assert 'sync_channel::<Work>(MAX_PENDING)' in core and 'pending_bytes.fetch_update' in core
    assert 'with_receiver_owner_scope_checked' in core and 'with_receiver_owner_checked(' not in core
    assert 'cleanup_done' in core and 'input_service::release_receiver_owner(token)' in core
    assert 'clear_relative_mouse_active' not in core and 'fn relative()' not in core
    assert 'enum Backend { Local, Portable(String) }' in windows
    assert 'GetNamedPipeServerProcessId' in windows and 'GetNamedPipeClientProcessId' in windows
    assert 'source == 0 || source != target' in windows and 'process: Some(peer.process.clone())' in windows
    assert 'OpenEventW(SYNC,0' in windows and 'OpenMutexW(MUTEX_ACCESS,0' in windows
    assert 'GetLastError() } == 183' in windows and 'D:P(A;;GA;;;' in windows
    assert 'PhantomData<std::rc::Rc<()>>' in windows
    assert 'LogonUserW' in windows and '0x400 | 4' in windows and 'ResumeThread' in windows
    assert 'DuplicateHandle(GetCurrentProcess(),GetCurrentProcess(),suspended.process.raw()' in windows
    assert 'startup.publisher.take().map(Some)' in windows and 'struct SecretCredential' in windows
    assert 'pub(crate) fn quiesce_helper_transition()' in core
    for forbidden in ('ResetEvent', 'Global\\\\', ';;;WD)', 'INFINITE', 'std::process::abort'):
        assert forbidden not in windows, forbidden
    changes = '\n'.join(after for pairs in plan['changes'].values() for _, after in pairs)
    assert 'Mouse(_) | Pointer(_) | Key(_) => {}' in changes
    assert 'Receiver::new(id)' in changes and 'receiver_pipe_peer' in changes
    assert 'ReceiverPublisherHandle' in changes and 'receiver_pipe_publisher' in changes and 'SecretCredential::new(password)' in changes
    assert changes.index('if !conn.receiver_input.close()') < changes.index('conn.chat_unanswered = false;')
    workflow = (HERE.parent / '.github/workflows/preflight.yml').read_text(encoding='utf-8')
    assert 'receiver-native:' in workflow and 'receiver_native_only:' in workflow
    assert "type: boolean\n        default: false" in workflow
    assert 'matrix.os' in workflow and 'test-support-receiver-input-native.py' in workflow
    skip = "if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.receiver_native_only) }}"
    assert workflow.count(skip) == 5, 'Normal mandatory jobs must retain only the manual diagnostic exception'
    assert 'PYTHONUNBUFFERED: "1"' in workflow
    print('PASS: receiver source contracts, pinned templates, backend/lease boundaries and normal CI gates', flush=True)


def actual_profile(module, source):
    if module.inspect(source,module.load_plan())[1] in ('final','previous'):
        with module.previous(source) as prior:
            return actual_profile(module, prior)
    plan = module.load_plan()
    changed_files = len(plan['profiles']['fresh'])
    with tempfile.TemporaryDirectory(prefix='mixel-receiver-source-controls-') as tmp:
        root = Path(tmp).resolve() / 'source'; root.mkdir()
        for name in plan['profiles']['fresh']:
            if name in module.LEAVES: continue
            dst = root / name; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source / name, dst)
        original = snapshot(root)
        assert module.apply(root, check=True)['writes'] == changed_files and snapshot(root) == original
        assert module.apply(root)['writes'] == changed_files
        final = snapshot(root); assert module.apply(root)['writes'] == 0 and snapshot(root) == final
        assert module.is_current(root)
        mode = (root / 'src/ipc.rs').stat().st_mode
        with module.previous(root) as prior:
            assert snapshot(prior) == original
            assert (prior / 'src/ipc.rs').stat().st_mode == mode
            (prior / 'src/ipc.rs').write_bytes(b'private mutation only')
        assert snapshot(root) == final
        assert module.apply(root, restore=True, check=True)['writes'] == changed_files and snapshot(root) == final
        assert module.apply(root, restore=True)['writes'] == changed_files and snapshot(root) == original
        assert not module.is_current(root)
        prior = dict(original)
        for name,pairs in plan['migration']['inverse'].items():
            text = prior[name].decode('utf-8')
            for after,before in pairs:
                assert text.count(before) == 1
                text = text.replace(before,after,1)
            prior[name] = text.encode()
        for name,text in plan['migration']['owned'].items(): prior[name] = text.encode()
        for name,raw in prior.items(): (root / name).write_bytes(raw)
        assert not module.is_current(root)
        assert module.apply(root, check=True)['profile'] == 'previous' and snapshot(root) == prior
        module.apply(root); assert module.is_current(root); module.apply(root,restore=True); assert snapshot(root) == original
        for name,raw in prior.items(): (root / name).write_bytes(raw)
        module.apply(root,restore=True); assert snapshot(root) == original
        for name in list(original):
            path = root / name; before = path.read_bytes(); path.write_bytes(before + b'\n// rejected drift\n'); must_refuse(module, root); path.write_bytes(before)
        module.apply(root); leaf = root / next(iter(module.LEAVES)); leaf.unlink(); must_refuse(module, root)
        for name, raw in original.items(): (root / name).write_bytes(raw)
        for name in module.LEAVES:
            path = root / name
            if path.exists(): path.unlink()
        target = root / 'src/ipc.rs'; other = root / 'hardlink.rs'; other.hardlink_to(target); must_refuse(module, root); other.unlink()
        raw = target.read_bytes(); target.write_bytes(raw.replace(b'\n', b'\r\n', 1)); must_refuse(module, root); target.write_bytes(raw)
        for name, raw in original.items(): (root / name).write_bytes(raw.replace(b'\n', b'\r\n'))
        crlf = snapshot(root); module.apply(root); module.apply(root, restore=True); assert snapshot(root) == crlf
    with tempfile.TemporaryDirectory(prefix='mixel-receiver-checkout-crlf-') as tmp:
        folder = Path(tmp).resolve(); scripts = folder / 'scripts'; scripts.mkdir()
        for name in ('patch-support-receiver-input.py', 'support-receiver-input-plan.json', *module.LEAVES.values()):
            raw = (HERE / name).read_bytes().replace(b'\r\n', b'\n')
            (scripts / name).write_bytes(raw.replace(b'\n', b'\r\n'))
        spec = importlib.util.spec_from_file_location('mixel_receiver_crlf_checkout', scripts / 'patch-support-receiver-input.py')
        checkout = importlib.util.module_from_spec(spec); spec.loader.exec_module(checkout)
        root = folder / 'source'; root.mkdir()
        for name in plan['profiles']['fresh']:
            if name in module.LEAVES: continue
            dst = root / name; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source / name, dst)
        original = snapshot(root); checkout.apply(root); assert checkout.is_current(root); checkout.apply(root, restore=True); assert snapshot(root) == original
        changed = scripts / 'support-receiver-input.rs'; changed.write_bytes(changed.read_bytes().replace(b'\r\n', b'\n', 1)); must_refuse(checkout, root)
    print('PASS: actual whole-source apply/check/idempotence/private inverse/pinned-prior migration, drift/partial/hardlink/newline refusals and CRLF source/checkout restoration', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--source', type=Path); args = parser.parse_args()
    module = helper(); source_contracts(module)
    if args.source: actual_profile(module, args.source.absolute())


if __name__ == '__main__': main()
