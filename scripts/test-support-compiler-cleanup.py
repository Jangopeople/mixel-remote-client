#!/usr/bin/env python3
"""Mandatory exact compiler cleanup and complete prior-owner source controls."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import stat

SPEC = importlib.util.spec_from_file_location('cleanup', Path(__file__).with_name('patch-support-compiler-cleanup.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def snapshot_path_controls(out):
    # PureWindowsPath executes actual Windows path parsing/relative semantics.
    # File bytes and is_file come from real owned local LF/CRLF fixtures; no
    # Windows filesystem/native API execution is inferred on a non-Windows host.
    import inspect
    from pathlib import PurePosixPath, PureWindowsPath

    current = inspect.getsource(snapshot)
    expression = 'p.relative_to(root).as_posix()'
    assert current.count(expression) == 1
    historical = current.replace(expression, 'str(p.relative_to(root))', 1)
    original = {}
    exec(compile(historical, '<original-snapshot>', 'exec'), original)
    cases = 0
    for ending in ('\n', '\r\n'):
        stage = out / ('snapshot-path-lf' if ending == '\n' else 'snapshot-path-crlf')
        stage.mkdir()
        names = [*P.ORIGINAL, 'nested space/Unicode é 🚀.txt']
        expected = {}
        for name in names:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = ('owned snapshot payload: ' + name + ending).encode('utf-8')
            target.write_bytes(payload)
            expected[name] = payload
        assert snapshot(stage) == expected
        for flavor in (PurePosixPath, PureWindowsPath):
            logical_root = flavor('C:/owned source' if flavor is PureWindowsPath else '/owned source')
            class Entry:
                def __init__(self, physical):
                    self.physical = physical
                    self.logical = logical_root.joinpath(*physical.relative_to(stage).parts)
                def relative_to(self, root):
                    return self.logical.relative_to(root.logical)
                def is_file(self):
                    return self.physical.is_file()
                def read_bytes(self):
                    return self.physical.read_bytes()
            class Root:
                logical = logical_root
                def rglob(self, pattern):
                    assert pattern == '*'
                    return [Entry(path) for path in stage.rglob(pattern)]
            root = Root()
            before = snapshot(stage)
            assert snapshot(root) == expected
            legacy = original['snapshot'](root)
            if flavor is PureWindowsPath:
                assert 'src\\server.rs' in legacy and 'src/server.rs' not in legacy
                try:
                    legacy['src/server.rs']
                except KeyError as error:
                    assert error.args == ('src/server.rs',)
                else:
                    raise AssertionError('Original Windows key mismatch not reproduced')
            else:
                assert legacy == expected
            assert snapshot(stage) == before
            cases += 1
    print(f'PASS: actual POSIX/Windows relative-path semantics across LF/CRLF bytes; {cases} canonical snapshot profiles; original Windows KeyError reproduced; files unchanged')


    # On POSIX, a backslash is a legal literal filename character. Canonical
    # path formatting must preserve it, unlike a generic separator replacement.
    if os.name == 'nt':
        print('SKIP: native POSIX literal-backslash filename fixture on Windows; four Windows/POSIX logical path profiles above remain mandatory')
    else:
        stage = out / 'snapshot-path-literal-backslash'
        stage.mkdir()
        expected = {r'literal\separator.txt': b'literal-backslash file\n',
                    'literal/separator.txt': b'distinct nested file\n'}
        for name, payload in expected.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        before = snapshot(stage)
        assert before == expected
        generic = current.replace(expression,
                                  'str(p.relative_to(root)).replace("\\\\", "/")', 1)
        incorrect = {}
        exec(compile(generic, '<incorrect-generic-key-replacement>', 'exec'), incorrect)
        collided = incorrect['snapshot'](stage)
        assert collided != expected and set(collided) == {'literal/separator.txt'}
        assert snapshot(stage) == before
        print('PASS: actual POSIX literal-backslash filename remains distinct from slash-nested file; generic replacement collision refused; files unchanged')


def fixture(source, dest, newline='\n'):
    for name in P.ORIGINAL:
        text, _ = P.canonical((source/name).read_bytes())
        assert P.digest(text) == P.ORIGINAL[name], name
        target = dest/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.replace('\n', newline).encode())


def source_controls(source, out):
    count = 0
    for profile in ('\n', '\r\n'):
        stage = out/('profile-lf' if profile == '\n' else 'profile-crlf')
        fixture(source, stage, profile)
        before = snapshot(stage)
        P.apply(stage, check=True)
        assert snapshot(stage) == before
        P.apply(stage)
        final = snapshot(stage)
        for name in P.ORIGINAL:
            text, eol = P.canonical(final[name])
            assert eol == profile and P.digest(text) == P.CORRECTED[name]
            assert P.digest(P.transform(name, text, restore=True)) == P.ORIGINAL[name]
        P.apply(stage)
        P.apply(stage, check=True, restore=True)
        assert snapshot(stage) == final
        P.apply(stage, restore=True)
        assert snapshot(stage) == before
        count += 1
    refused = 0
    for name in P.ORIGINAL:
        for kind in ('drift', 'mixed', 'missing', 'partial', 'symlink', 'hardlink', 'invalidutf8', 'barecr'):
            stage = out/('negative-' + str(refused))
            fixture(source, stage)
            target = stage/name
            if kind == 'drift':
                target.write_bytes(target.read_bytes()+b'// owned hash drift\n')
            elif kind == 'mixed':
                target.write_bytes(target.read_bytes()+b'// deliberate CRLF\r\n')
            elif kind == 'invalidutf8':
                target.write_bytes(target.read_bytes()+b'\xff')
            elif kind == 'barecr':
                target.write_bytes(target.read_bytes()+b'\r')
            elif kind == 'missing':
                target.unlink()
            elif kind == 'partial':
                target.write_bytes(P.transform(name, target.read_text()).encode())
            elif kind == 'symlink':
                data = target.read_bytes(); target.unlink()
                outside = out/('outside-' + str(refused)); outside.write_bytes(data)
                target.symlink_to(outside)
            else:
                outside = out/('outside-' + str(refused))
                os.link(target, outside)
            before = snapshot(stage)
            for check, restore in ((True, False), (True, True), (False, False), (False, True)):
                try:
                    P.apply(stage, check=check, restore=restore)
                except (RuntimeError, FileNotFoundError, UnicodeError):
                    pass
                else:
                    raise AssertionError((name, kind, check, restore))
                assert snapshot(stage) == before
                refused += 1
    # Windows junction/reparse metadata is tested against the actual guard,
    # without claiming a native Windows junction was created on this host.
    from unittest.mock import patch
    stage = (out/'reparse-model').absolute(); fixture(source, stage)
    original = Path.lstat
    class Info:
        def __init__(self, info):
            self.st_mode, self.st_nlink, self.st_size = info.st_mode, info.st_nlink, info.st_size
            self.st_file_attributes = 0x400
    def injected(path, *a, **kw):
        info = original(path, *a, **kw)
        return Info(info) if path == stage/'src' else info
    before = snapshot(stage)
    with patch.object(Path, 'lstat', injected):
        try: P.apply(stage)
        except RuntimeError: pass
        else: raise AssertionError('reparse parent accepted')
    assert snapshot(stage) == before
    last=next(reversed(P.CHANGES));saved=P.CHANGES[last]
    stage=out/'late-literal-plan-refusal';fixture(source,stage);before=snapshot(stage)
    try:
        P.CHANGES[last]=[("deliberately absent late literal", "never written")]
        for check in (False,True):
            try:P.apply(stage,check=check)
            except RuntimeError:pass
            else:raise AssertionError('Late invalid literal accepted')
            assert snapshot(stage)==before
    finally:P.CHANGES[last]=saved
    print(f'PASS: {count} LF/CRLF exact apply/idempotence/check/restore profiles; {refused} fatal zero-write controls; actual guard reparse metadata refusal')


def run(command, folder, name, expected=0, env=None):
    result = subprocess.run(command, cwd=folder, text=True, capture_output=True, env=env)
    (folder/(name+'.stdout')).write_text(result.stdout)
    (folder/(name+'.stderr')).write_text(result.stderr)
    assert result.returncode == expected, (command, result.returncode, result.stderr[-3000:])
    return result


def rust(source, out, compiler, name, code, old_warning=None, extra=()):
    file = out/(name+'.rs'); file.write_text(code)
    command = [compiler, '--edition=2021', *extra, str(file), '-o', str(out/name)]
    if old_warning:
        result = subprocess.run(command+['-D', 'warnings'], text=True, capture_output=True)
        (out/(name+'.old.stderr')).write_text(result.stderr)
        assert result.returncode != 0 and old_warning in result.stderr, result.stderr
        # Historical execution keeps the original warnings visible.
        run(command, out, name+'-compile-original')
    else:
        run(command+['-D', 'warnings'], out, name+'-compile')
    result = run([str(out/name)], out, name+'-execute')
    return result.stdout


def between(text, start, end):
    return text[text.index(start):text.index(end, text.index(start))]


def leaf_controls(source, out, compilers):
    for i, compiler in enumerate(compilers):
        version = run([compiler, '-vV'], out, f'compiler-{i}').stdout
        assert ('release: 1.75.0' if i == 0 else 'release: 1.81.0') in version
        original = (source/'src/server/audio_service.rs').read_text()
        corrected = P.transform('src/server/audio_service.rs', original)
        allocator = (source/'libs/hbb_common/src/mem.rs').read_text()
        # Real allocator + exact actual function; ordinary Vec allocator is
        # naturally >=4 aligned. The non-aligned ownership branch is compiled
        # and source-conserved, not fabricated using an invalid Vec allocation.
        for label, content in [('old', original), ('new', corrected)]:
            fn = between(content, '    unsafe fn align_to_32(', '    #[tokio::main')
            code = 'mod hbb_common { pub mod mem { '+allocator+' } }\n'+fn+'\nfn main(){ for n in [0,1,2,3,4,32,65536] { let mut v=Vec::with_capacity(n.max(1)); v.extend((0..n).map(|x| (x%251) as u8)); let pointer=v.as_ptr(); assert_eq!(pointer as usize & 3, 0); let expected=v.clone(); let actual=unsafe {align_to_32(v)}; assert_eq!(actual,expected); assert_eq!(actual.as_ptr(),pointer); } println!("audio real allocation/data/fast-path PASS7"); }'
            rust(source, out, compiler, f'audio-{i}-{label}', code, 'value assigned to `buf`' if label == 'old' else None)
        original = (source/'src/server/rdp_input.rs').read_text()
        corrected = P.transform('src/server/rdp_input.rs', original)
        prefix = '''use std::sync::Arc;
type ResultType<T> = Result<T, ()>;
pub struct SyncConnection;
pub struct Path<'a>(&'a str);
pub struct PwStreamInfo { size:(usize,usize), position:(i32,i32) }
impl PwStreamInfo { fn get_size(&self)->(usize,usize){self.size} fn get_position(&self)->(i32,i32){self.position} }
thread_local!{static KDE:std::cell::Cell<bool>=const{std::cell::Cell::new(false)}}
fn is_kde()->bool{KDE.with(|v|v.get())}
'''
        main = '''fn main(){let mut count=0;for kde in [false,true]{for resolution in [(0,0),(800,600),(1920,1080)]{for size in [(0,0),(400,300),(1920,1080)]{KDE.with(|v|v.set(kde));let actual=RdpInputMouse::new(Arc::new(SyncConnection),Path("owned"),PwStreamInfo{size,position:(-20,80)},resolution).unwrap();let expected=if kde{Some(if resolution.0==0||size.0==0{1.}else{resolution.0 as f64/size.0 as f64})}else{None};assert_eq!(actual.scale,expected);assert_eq!(actual.position,(-20.,80.));assert_eq!(actual.session.0,"owned");assert_eq!(actual.stream.size,size);assert_eq!(Arc::strong_count(&actual.conn),1);count+=1;}}}println!("Rdp constructor calculations/owned arguments PASS{}",count);}
'''
        for label, content in [('old', original), ('new', corrected)]:
            item = between(content, '    pub struct RdpInputMouse {', '    impl MouseControllable for RdpInputMouse')
            rust(source, out, compiler, f'rdp-{i}-{label}', prefix+item+main, 'field `resolution` is never read' if label == 'old' else None)
        # Moving the three declarations into their existing disabled blocks
        # leaves the complete active input/elevation code unchanged.
        original = (source/'src/server/input_service.rs').read_text()
        corrected = P.transform('src/server/input_service.rs', original)
        constants = P.CHANGES['src/server/input_service.rs'][0][0].split('\n\n', 1)[1]
        active = between(original, 'fn active_mouse_(', '\npub fn active_mouse(') if '\npub fn active_mouse(' in original else None
        # The complete function ends at the next function marker in this source.
        active_end = original.index('\n}', original.index('fn active_mouse_(')) + 2
        oldfn = original[original.index('fn active_mouse_('):active_end]
        new_end = corrected.index('\n}', corrected.index('fn active_mouse_(')) + 2
        newfn = corrected[corrected.index('fn active_mouse_('):new_end]
        for label, declarations, fn in [('old', constants, oldfn), ('new', '', newfn)]:
            # No Duration import remains necessary in this isolated corrected
            # leaf; the full input module continues using its original import.
            code = ('use std::time::Duration;\n' if label == 'old' else '')+declarations+fn+'\nfn main(){ for connection in [-1,0,1,999] { assert!(active_mouse_(connection)); } println!("disabled mouse priority remains true PASS4"); }'
            rust(source, out, compiler, f'mouse-{i}-{label}', code, 'MOUSE_MOVE_PROTECTION_TIMEOUT' if label == 'old' else None)
        original = (source/'src/platform/linux.rs').read_text()
        oldfn = between(original, 'fn is_opensuse()', '\npub fn run_as_user')
        code = 'fn run_cmds(_: &str)->Result<String,()>{Ok(String::new())}\n'+oldfn+'\nfn main(){println!("commented legacy-only is_opensuse leaf");}'
        rust(source, out, compiler, f'opensuse-{i}-old', code, 'function `is_opensuse` is never used')
        corrected = P.transform('src/platform/linux.rs', original)
        comment = between(corrected, '/*\nfn is_opensuse()', '\n*/') + '\n*/'
        rust(source, out, compiler, f'opensuse-{i}-new', comment+'\nfn main(){println!("commented legacy declarations remain disabled");}')
    print('PASS: actual Rust1.75/1.81 audio/Rdp/mouse leaf compilation and execution; original diagnostics reproduced; constructor18/data7/mouse4 scenarios each version')



def production_snapshot(root):
    records = {}
    for directory, children, files in os.walk(root, followlinks=False):
        ignored=P.projection_ignored(root,directory,children) | {'.git'}
        children[:]=[name for name in children if name not in ignored]
        for name in children + [n for n in files if n != '.git']:
            path = Path(directory)/name; relative = path.relative_to(root).as_posix(); info = path.lstat()
            if stat.S_ISLNK(info.st_mode): records[relative] = ('symlink', os.readlink(path))
            elif stat.S_ISREG(info.st_mode): records[relative] = ('file', info.st_nlink, hashlib.sha256(path.read_bytes()).hexdigest())
            elif stat.S_ISDIR(info.st_mode): records[relative] = ('directory',)
            else: records[relative] = ('nonregular', info.st_mode)
    return records


def composed_controls(source, out, bash):
    repo = out/'complete-composed'; shutil.copytree(source, repo, symlinks=True,
                      ignore=lambda directory,names:P.projection_ignored(source,directory,names))
    P.apply(repo, restore=True)
    baseline = production_snapshot(repo)
    assert P.apply(repo, True, True, True)['writes'] == 0
    P.apply(repo, after_branding=True); final = production_snapshot(repo)
    P.apply(repo, after_branding=True)
    assert production_snapshot(repo) == final
    assert P.apply(repo, True, True, True)['writes'] == 0
    refused = 0
    for name in P.ORIGINAL:
        path = repo/name; saved = path.read_bytes(); _, eol = P.canonical(saved)
        for fault in ('drift', 'mixed'):
            suffix = ('// deliberate homogeneous drift'+eol).encode() if fault == 'drift' else b'// deliberate mixed'+(b'\n' if eol == '\r\n' else b'\r\n')
            path.write_bytes(saved+suffix); before = production_snapshot(repo)
            for check, restore in ((True,False),(True,True),(False,False),(False,True)):
                try: P.apply(repo, check, restore, before_branding=check and restore)
                except (RuntimeError, FileNotFoundError, UnicodeError): pass
                else: raise AssertionError((name, fault, 'accepted'))
                assert production_snapshot(repo) == before; refused += 1
            path.write_bytes(saved)
    # Literal macro negatives cover disjoint source, owned provenance and
    # generated utility inventories while C itself remains exact and final.
    targets = ['flutter/lib/common.dart', 'src/tray.rs', 'libs/hbb_common/src/fingerprint.rs',
               'flutter/.mixel-dependencies/macos_application_exit/flutter/lib/models/multi_window_manager.dart',
               '.mixel-deps/rdev/src/macos/keyboard.rs', '.mixel-deps/clipboard-master/src/master/x11.rs']
    attempted = []
    for index, name in enumerate(targets):
        path = repo/name
        if not path.is_file():
            if index == 3:
                names = list((repo/'flutter/.mixel-dependencies/macos_application_exit').rglob('*.dart'))
                assert names, 'Actual prior provenance is required'; path = names[0]; name = path.relative_to(repo).as_posix()
            else: raise AssertionError('Missing critical actual negative target: '+name)
        saved = path.read_bytes(); newline = b'\r\n' if b'\r\n' in saved else b'\n'
        path.write_bytes(saved+b'// disjoint prior-owner drift'+newline); before = production_snapshot(repo)
        result = subprocess.run([str(bash), str(Path(__file__).resolve().parent/'apply-branding.sh')],
              cwd=Path(__file__).resolve().parents[1], env=dict(os.environ,RDREPO=str(repo),BRANDING=str(Path(__file__).resolve().parents[1]/'branding')),
              capture_output=True,text=True,encoding='utf-8',timeout=180)
        (out/('macro-prior-refusal-'+str(index)+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        # L owns the exact final tray profile before C's prior-chain guard.
        # This one overlapping negative must fail at that earlier full hash.
        expected_refusal = ('Pinned or partial Mac launch profile changed' in result.stderr
                            if name == 'src/tray.rs' else
                            ('Compiler prior' in result.stderr or 'Composed prior' in result.stderr))
        assert result.returncode != 0 and expected_refusal, (name,result.returncode,result.stderr)
        assert production_snapshot(repo) == before, name
        path.write_bytes(saved); attempted.append(name)
    # Names used by build caches must not conceal foreign owned inventory.
    owned=repo/'flutter/.mixel-dependencies/macos_application_exit'
    poison=owned/'build/foreign-owned-source.dart';poison.parent.mkdir()
    poison.write_bytes(b'// unexpected source inventory\n');before=production_snapshot(repo)
    try:P.apply(repo,True,True,True)
    except (RuntimeError,SystemExit):pass
    else:raise AssertionError('Cache-named foreign prior inventory accepted')
    assert production_snapshot(repo)==before;poison.unlink();poison.parent.rmdir()
    # Before copytree, validate disjoint prior topology without laundering it.
    disjoint=repo/'.mixel-deps/rdev/src/macos/keyboard.rs';saved=disjoint.read_bytes()
    for fault in ('symlink','hardlink'):
        outside=out/('disjoint-'+fault)
        if fault=='symlink':outside.write_bytes(saved);disjoint.unlink();disjoint.symlink_to(outside)
        else:os.link(disjoint,outside)
        before=production_snapshot(repo)
        result=subprocess.run([str(bash),str(Path(__file__).resolve().parent/'apply-branding.sh')],
               cwd=Path(__file__).resolve().parents[1],env=dict(os.environ,RDREPO=str(repo),BRANDING=str(Path(__file__).resolve().parents[1]/'branding')),
               capture_output=True,text=True,encoding='utf-8',timeout=180)
        (out/('disjoint-'+fault+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        marker='Unsafe compiler prior source ownership' if fault=='symlink' else 'Hardlinked compiler prior source'
        assert result.returncode!=0 and marker in result.stderr,result.stderr
        assert production_snapshot(repo)==before
        if fault=='symlink':disjoint.unlink();disjoint.write_bytes(saved)
        else:outside.unlink()
    from unittest.mock import patch
    target=repo/'flutter';real_lstat=Path.lstat
    class ReparseInfo:
        def __init__(self,info):
            self.st_mode,self.st_nlink,self.st_size=info.st_mode,info.st_nlink,info.st_size
            self.st_file_attributes=0x400
    def injected(path,*args,**kwargs):
        info=real_lstat(path,*args,**kwargs)
        return ReparseInfo(info) if path==target else info
    before=production_snapshot(repo)
    with patch.object(Path,'lstat',injected):
        try:P.apply(repo,True,True,True)
        except RuntimeError as error:assert 'Unsafe compiler prior source ownership' in str(error)
        else:raise AssertionError('Disjoint reparse source parent accepted')
    assert production_snapshot(repo)==before
    print('PASS: actual macro disjoint symlink/hardlink refusal and exact outer guard disjoint Windows reparse model, all zero writes')
    clipboard=P.prior_namespace('patch-support-clipboard.py')
    library=repo/'.mixel-deps/clipboard-master/src/lib.rs';saved=library.read_bytes()
    clipboard_before=production_snapshot(repo)
    positive=0;negative=0
    logical, _=P.canonical(saved)
    for profile_eol in ('\n','\r\n'):
        for state in ('original','corrected'):
            text=P.transform('.mixel-deps/clipboard-master/src/lib.rs',logical,restore=state=='original')
            library.write_bytes(text.replace('\n',profile_eol).encode());before=production_snapshot(repo)
            clipboard['check_existing'](repo)
            assert production_snapshot(repo)==before;positive+=1
        current=library.read_bytes()
        for fault in ('drift','mixed','hardlink','symlink'):
            outside=out/('clipboard-outside-'+str(negative))
            if fault=='drift':library.write_bytes(current+('// drift'+profile_eol).encode())
            elif fault=='mixed':library.write_bytes(current+b'// mixed'+(b'\n' if profile_eol=='\r\n' else b'\r\n'))
            elif fault=='hardlink':os.link(library,outside)
            else:
                outside.write_bytes(current);library.unlink();library.symlink_to(outside)
            before=production_snapshot(repo)
            try:clipboard['check_existing'](repo)
            except RuntimeError:pass
            else:raise AssertionError('Foreign clipboard library accepted: '+fault)
            assert production_snapshot(repo)==before;negative+=1
            if fault=='hardlink':outside.unlink()
            if fault=='symlink':library.unlink()
            library.write_bytes(current)
    library.write_bytes(saved);assert production_snapshot(repo)==clipboard_before
    print(f'PASS: clipboard exact library states LF/CRLF {positive} read-only positives and {negative} foreign/mixed/ownership refusals')
    P.apply(repo, restore=True); assert production_snapshot(repo) == baseline
    (out/'composed-results.json').write_text(json.dumps({'sameFinalIdempotent':True,'compilerFatalZeroWrite':refused,'actualMacroPriorDriftZeroWrite':attempted,'suppliedInputReadOnly':True},indent=2)+'\n')
    print(f'PASS: complete composed compiler original/final reversal; {refused} compiler refusals and {len(attempted)} actual macro disjoint-owner zero-write refusals')


def source_projection_controls(source,out):
    spec=importlib.util.spec_from_file_location('compiler_actual_consumer_projection',Path(__file__).with_name('test-support-flutter-analysis.py'))
    A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
    stage=out/'projection-source';shutil.copytree(source,stage,symlinks=True,ignore=lambda directory,names:P.projection_ignored(source,directory,names))
    P.apply(stage,after_branding=True);final=production_snapshot(stage)
    with A.startup_baseline(stage) as prior:
        assert {name:P.digest(P.canonical((prior/name).read_bytes())[0]) for name in P.ORIGINAL}==P.ORIGINAL
    assert production_snapshot(stage)==final
    owned=stage/'flutter/.mixel-dependencies/macos_application_exit';poison=owned/'build/foreign-owned-source.dart'
    poison.parent.mkdir();poison.write_bytes(b'// foreign owned inventory\n')
    before=production_snapshot(stage)
    try:
        with A.startup_baseline(stage):pass
    except (RuntimeError,SystemExit):pass
    else:raise AssertionError('Actual analyzer C projection concealed owned build inventory')
    assert production_snapshot(stage)==before;poison.unlink();poison.parent.rmdir()
    target=stage/'src/tray.rs';saved=target.read_bytes()
    for fault in ('symlink','hardlink'):
        outside=out/('analyzer-disjoint-'+fault)
        if fault=='symlink':outside.write_bytes(saved);target.unlink();target.symlink_to(outside)
        else:os.link(target,outside)
        before=production_snapshot(stage)
        try:
            with A.startup_baseline(stage):pass
        except RuntimeError as error:
            marker='Unsafe compiler prior source ownership' if fault=='symlink' else 'Hardlinked compiler prior source'
            assert marker in str(error),str(error)
        else:raise AssertionError('Actual analyzer C projection laundered disjoint '+fault)
        assert production_snapshot(stage)==before
        if fault=='symlink':target.unlink();target.write_bytes(saved)
        else:outside.unlink()
    assert production_snapshot(stage)==final
    print('PASS: actual analyzer compiler projection valid inverse and disjoint owned-build/symlink/hardlink refusals, supplied input unchanged')


def wiring_controls(out, bash):
    root = Path(__file__).resolve().parents[1]
    macro = (root/'scripts/apply-branding.sh').read_text(encoding='utf-8')
    dry = 'patch-support-compiler-cleanup.py" --root "$RDREPO" --restore --check --before-branding'
    reverse = 'patch-support-compiler-cleanup.py" --root "$RDREPO" --restore\n'
    late = 'patch-support-compiler-cleanup.py" --root "$RDREPO" --after-branding'
    assert macro.count(dry) == macro.count(reverse) == macro.count(late) == 1
    assert macro.index(dry) < macro.index(reverse) < macro.index('patch-macos-startup.py" --restore')
    assert macro.index(late) > macro.rindex('patch-macos-startup.py"\n')
    for name in ['test-support-flutter-analysis.py','test-support-flutter-sdk-compatibility.py','test-support-macos-cleanup.py','test-support-macos-termination.py']:
        text = (root/'scripts'/name).read_text(encoding='utf-8')
        assert 'patch-support-compiler-cleanup.py' in text and 'test-support-compiler-cleanup.py' in text, name
        assert 'patch-support-url-log-privacy.py' in text and 'test-support-url-log-privacy.py' in text, name
    # Execute exact native workflow Bash with a child shim; first C failure
    # remains fatal and no following source test is executed.
    calls = 0
    for file in ['build.yml','preflight.yml']:
        lines = (root/'.github/workflows'/file).read_text(encoding='utf-8').splitlines()
        for index, line in enumerate(lines):
            if 'python3 scripts/test-support-compiler-cleanup.py ' not in line: continue
            folder=out/('wiring-'+str(calls));folder.mkdir()
            shim=folder/'shim.py';shim.write_bytes(b"import json,os,sys\nfrom pathlib import Path\nwith Path(os.environ['CALLS']).open('a',encoding='utf-8') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1]=='scripts/test-support-compiler-cleanup.py':sys.exit(29)\n")
            body=line.strip()+'\npython3 scripts/never-after-compiler-failure.py\n'
            prefix='python3(){ "'+sys.executable+'" "'+str(shim)+'" "$@"; }\n'
            result=subprocess.run([str(bash),'-e','-o','pipefail','-c',prefix+body],env=dict(os.environ,CALLS=str(folder/'calls.jsonl'),RUNNER_TEMP=str(folder)),capture_output=True,text=True,encoding='utf-8')
            assert result.returncode == 29, result.stderr
            rows=[json.loads(x) for x in (folder/'calls.jsonl').read_text().splitlines()]
            assert len(rows)==1 and rows[0][0]=='scripts/test-support-compiler-cleanup.py' and '--source-only' in rows[0],rows
            calls += 1
    dispatcher=(root/'scripts/test-windows-generated-source.sh').read_text()
    exact='python scripts/test-support-compiler-cleanup.py --source "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" --source-only --output "$RUNNER_TEMP/mixel-compiler-cleanup-source" --bash "$mixel_analyzer_bash"'
    assert dispatcher.count(exact)==1 and dispatcher.index(exact)<dispatcher.index('unset mixel_analyzer_bash')
    for profile in ('unix','windows'):
        folder=out/('dispatcher-'+profile);(folder/'scripts').mkdir(parents=True)
        target=folder/'scripts/test-windows-generated-source.sh';target.write_bytes(dispatcher.encode('utf-8'))
        (folder/'scripts/use-windows-msvc-bash.sh').write_bytes(b'python(){ "$MIXEL_PYTHON" "$MIXEL_SHIM" "$@"; }\ncygpath(){ printf "%s\\n" "$MIXEL_ACTIVE_BASH"; }\n')
        shim=folder/'shim.py';shim.write_bytes(b"import json,os,sys\nfrom pathlib import Path\nwith Path(os.environ['CALLS']).open('a',encoding='utf-8') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1]=='scripts/test-support-compiler-cleanup.py':sys.exit(29)\n")
        env=dict(os.environ,MIXEL_PYTHON=sys.executable,MIXEL_SHIM=str(shim),CALLS=str(folder/'calls.jsonl'),
                 RUNNER_TEMP=str(folder/'temporary préflight 🚀'),MIXEL_SDK_FULL_APP_PREFLIGHT='false',
                 OS='Windows_NT' if profile=='windows' else 'Linux',MIXEL_ACTIVE_BASH=r'C:\Program Files\Git préflight 🚀\bin\bash.exe')
        result=subprocess.run([str(bash),str(target)],env=env,capture_output=True,text=True,encoding='utf-8')
        assert result.returncode==29,result.stderr
        rows=[json.loads(line) for line in (folder/'calls.jsonl').read_text().splitlines()]
        active=rows[0][rows[0].index('--bash')+1]
        if profile=='windows': assert active==env['MIXEL_ACTIVE_BASH']
        else:
            probe=folder/'selected-active-bash.py'
            probe.write_bytes(b'import sys\nsys.stdout.buffer.write(sys.argv[1].encode("utf-8")+b"\\n")\n')
            selected_env=dict(env,MIXEL_BASH_ARGV_PROBE=str(probe))
            selected=subprocess.run([str(bash),'-c','"$MIXEL_PYTHON" "$MIXEL_BASH_ARGV_PROBE" "$BASH"'],env=selected_env,capture_output=True)
            assert selected.returncode==0 and selected.stdout==active.encode()+b'\n'
        assert rows[-2][0]=='scripts/test-support-native-warnings.py' and rows[-1][0]=='scripts/test-support-compiler-cleanup.py',rows[-2:]
        assert rows[-1][1:]==['--source',env['RUNNER_TEMP']+'/mixel-sdk-source/pipeline-lf','--source-only','--output',env['RUNNER_TEMP']+'/mixel-compiler-cleanup-source','--bash',active]
        assert not any(row[0]=='scripts/never-after-compiler-failure.py' or row[0]=='dart' for row in rows)
    print(f'PASS: first final-layer prewrite check/reverse/late ordering, four macro copiers, {calls} actual native workflow first-fatal gates and exact active Git Bash forwarding')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--evidence-dir', '--output', dest='evidence_dir', type=Path, required=True)
    parser.add_argument('--rustc-175'); parser.add_argument('--rustc-181')
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--bash', type=Path)
    args = parser.parse_args()
    args.source = args.source.resolve(strict=True)
    args.evidence_dir = args.evidence_dir.absolute()
    args.evidence_dir.mkdir(exist_ok=False, parents=True)
    # Use the same already-qualified actual active Bash contract as W.
    shell_spec=importlib.util.spec_from_file_location('compiler_shell',Path(__file__).with_name('test-support-native-warnings.py'))
    shell=importlib.util.module_from_spec(shell_spec);shell_spec.loader.exec_module(shell)
    args.bash=shell.qualified_bash(args.bash,os.name=='nt')
    shell.shell_path_controls(args.evidence_dir)
    launch_spec=importlib.util.spec_from_file_location('compiler_launch_previous',Path(__file__).with_name('patch-support-macos-gui-launch.py'))
    launch=importlib.util.module_from_spec(launch_spec);launch_spec.loader.exec_module(launch)
    with launch.previous(args.source) as previous:
        args.source=previous
        qualify_previous(args,parser)


def qualify_previous(args,parser):
    before=production_snapshot(args.source)
    original=args.evidence_dir/'exact-original';original.mkdir()
    texts, _, (profile, state)=P.inventory(args.source)
    assert profile=='branded', 'Source controls require complete final branded source'
    # Validate supplied ownership before any fixture copier can regularize it.
    assert P.apply(args.source,True,True,True)['writes']==0
    for name, text in texts.items():
        target=original/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(P.transform(name,text,True).encode('utf-8'))
    snapshot_path_controls(args.evidence_dir)
    source_controls(original,args.evidence_dir)
    composed_controls(args.source,args.evidence_dir,args.bash)
    source_projection_controls(args.source,args.evidence_dir)
    wiring_controls(args.evidence_dir,args.bash)
    if not args.source_only:
        if not args.rustc_175 or not args.rustc_181: parser.error('Leaf replay requires both actual compilers')
        leaf_source=args.evidence_dir/'leaf-source';shutil.copytree(args.source,leaf_source,ignore=lambda directory,names:P.projection_ignored(args.source,directory,names))
        P.apply(leaf_source,restore=True)
        leaf_controls(leaf_source,args.evidence_dir,[args.rustc_175,args.rustc_181])
    assert production_snapshot(args.source)==before
    print('PASS: mandatory compiler cleanup source/whole-owner controls; supplied source unchanged; native package acceptance remains separate')


if __name__ == '__main__': main()
