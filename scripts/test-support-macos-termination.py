#!/usr/bin/env python3
"""Run main-owned Mac Quit controls against the actual transformed sources."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mixel_app_exit', ROOT/'scripts/patch-support-macos-termination.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
PROFILE = P.FILES

sdk_spec=importlib.util.spec_from_file_location("mixel_sdk_compatibility_composition",ROOT/"scripts/patch-support-flutter-sdk-compatibility.py")
S=importlib.util.module_from_spec(sdk_spec)
sdk_spec.loader.exec_module(S)

file_transfer_spec=importlib.util.spec_from_file_location("mixel_file_transfer_composition",ROOT/"scripts/patch-support-file-transfer-window.py")
F=importlib.util.module_from_spec(file_transfer_spec)
file_transfer_spec.loader.exec_module(F)
analysis_spec=importlib.util.spec_from_file_location("mixel_final_source_fixture",ROOT/"scripts/test-support-flutter-analysis.py")
A=importlib.util.module_from_spec(analysis_spec);analysis_spec.loader.exec_module(A)


def run(args, out, label, cwd=None):
    if os.name=='nt' and Path(args[0]).suffix.lower()=='.bat':
        if any(any(character in str(arg) for character in '\r\n"%!^&|<>') for arg in args):
            raise RuntimeError('Unsafe native Flutter batch fixture argument')
        # Native Python cannot execute Git Bash's extensionless Flutter script.
        # Explicit cmd.exe with /d avoids user AutoRun; quote every owned arg.
        args='cmd.exe /d /s /c "'+' '.join('"'+str(arg)+'"' for arg in args)+'"'
    result = subprocess.run(args, cwd=cwd,env=dict(os.environ,PYTHONIOENCODING='utf-8'),capture_output=True, text=True,
                            encoding='utf-8', timeout=300)
    (out/(label+'.log')).write_text(result.stdout+result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout+result.stderr
    return result.stdout


def snapshot(root):
    records = {}
    for parent, dirs, files in os.walk(root):
        dirs[:] = [n for n in dirs if n != '__pycache__']
        for name in files:
            path = Path(parent)/name
            records[path.relative_to(root).as_posix()] = ('link:'+os.readlink(path)
                if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file() else 'special:'+str(path.lstat().st_mode))
    return records


def source_controls(source, out):
    with A.startup_baseline(source) as plain:
        if plain != source.resolve():
            return source_controls(plain, out)
    if (source/F.GENERATED).exists(): F.apply(source,restore=True,check=True); F.verify_previous_layers(source)
    else: (S.apply(source,check=True) if (source/S.OWNED).exists() else P.apply(source,check=True))
    count = 0
    for ending in (b'\n', b'\r\n'):
        fixture = out/('source-lf' if ending == b'\n' else 'source-crlf')
        fixture.mkdir()
        for name in ('Cargo.toml', *P.FILES):
            data = canonical(source,name) if name in P.FILES else (source/name).read_bytes().replace(b'\r\n', b'\n')
            if name in P.FILES: assert P.digest(data) == PROFILE[name], name
            target = fixture/name; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data.replace(b'\n', ending))
        (fixture/'preserved-windows.bin').write_bytes(b'unchanged win\r\n')
        (fixture/'preserved-linux.bin').write_bytes(b'unchanged linux\n')
        original = snapshot(fixture)
        P.apply(fixture); corrected = snapshot(fixture)
        assert corrected['preserved-windows.bin'] == original['preserved-windows.bin']
        assert corrected['preserved-linux.bin'] == original['preserved-linux.bin']
        P.apply(fixture); assert snapshot(fixture) == corrected
        P.apply(fixture, True)
        assert all(snapshot(fixture)[name] == value for name,value in original.items())
        P.apply(fixture); assert snapshot(fixture) == corrected
        count += 4
        cases = [('changed-target', next(iter(P.FILES))),
                 ('changed-provenance', 'flutter/.mixel-dependencies/macos_application_exit/'+next(iter(P.FILES))),
                 ('changed-utility', P.GENERATED), ('wrong-version', 'Cargo.toml')]
        for label, name in cases:
            bad = out/(label+('-lf' if ending == b'\n' else '-crlf'))
            shutil.copytree(fixture,bad)
            target = bad/name
            target.write_bytes(target.read_bytes()+b'\ninvalid drift\n') if name != 'Cargo.toml' else target.write_bytes(b'[package]\nversion="2.0"\n')
            before = snapshot(bad)
            try: P.apply(bad)
            except RuntimeError: pass
            else: raise AssertionError(label+' must refuse')
            assert snapshot(bad) == before
            count += 1
        for label in ('symlink-target','hardlink-target','extra-proof','mixed-newline'):
            bad=out/(label+('-lf' if ending == b'\n' else '-crlf'))
            shutil.copytree(fixture,bad)
            target=bad/next(iter(P.FILES))
            if label=='symlink-target':
                target.rename(target.with_name('retained-source'))
                target.symlink_to(target.with_name('retained-source').name)
            elif label=='hardlink-target': os.link(target,target.with_name('extra-hardlink'))
            elif label=='extra-proof':
                (bad/'flutter/.mixel-dependencies/macos_application_exit/extra').write_bytes(b'foreign')
            else: target.write_bytes(target.read_bytes()+b'\r\n\n')
            before=snapshot(bad)
            try: P.apply(bad)
            except RuntimeError: pass
            else: raise AssertionError(label+' must refuse')
            assert snapshot(bad)==before
            count+=1
        labels=['mixed-targets','missing-utility','unowned-utility','extra-directory',
                'hardlink-utility','symlink-owned-parent','mixed-restore-inputs']
        if hasattr(os,'mkfifo'): labels.append('extra-fifo')
        for label in labels:
            bad=out/(label+('-lf' if ending == b'\n' else '-crlf'))
            shutil.copytree(fixture,bad)
            first=next(iter(P.FILES))
            if label=='mixed-targets':
                (bad/first).write_bytes(canonical(source,first).replace(b'\n',ending))
            elif label=='missing-utility': (bad/P.GENERATED).unlink()
            elif label=='unowned-utility': shutil.rmtree(bad/'flutter/.mixel-dependencies/macos_application_exit')
            elif label=='extra-directory': (bad/'flutter/.mixel-dependencies/macos_application_exit/extra').mkdir()
            elif label=='extra-fifo': os.mkfifo(bad/'flutter/.mixel-dependencies/macos_application_exit/extra-fifo')
            elif label=='hardlink-utility': os.link(bad/P.GENERATED,bad/'utility-hardlink')
            elif label=='symlink-owned-parent':
                parent=bad/'flutter/.mixel-dependencies'
                parent.rename(bad/'retained-dependencies'); parent.symlink_to('../retained-dependencies',target_is_directory=True)
            else:
                (bad/P.GENERATED).unlink(); shutil.rmtree(bad/'flutter/.mixel-dependencies/macos_application_exit')
                for name in P.FILES:
                    (bad/name).write_bytes(canonical(source,name).replace(b'\n',ending))
                changed=next(name for name in P.FILES if P.FILES[name]!=P.FRESH[name])
                data=subprocess.check_output(['git','show',P.PIN+':'+changed],cwd=ROOT/'rustdesk')
                (bad/changed).write_bytes(data.replace(b'\n',ending))
            before=snapshot(bad)
            for restore in (False,True):
                try: P.apply(bad,restore)
                except RuntimeError: pass
                else: raise AssertionError(label+' must refuse '+str(restore))
                assert snapshot(bad)==before
                count+=1
    print(f'PASS: {count} application-exit materialization/idempotence/restore/drift controls.')
    return out/'source-lf'


def canonical(source,name):
    current,_=P.normalize(S.regular(source/S.OWNED,name) if (source/S.OWNED).exists() and name in S.FILES else P.regular(source,name))
    if name in F.COMPOSED and (source/F.GENERATED).exists(): current=F.transform(name,current,restore=True)
    if P.digest(current)==PROFILE[name]: return current
    original=P.regular(source/'flutter/.mixel-dependencies/macos_application_exit',name)
    if P.digest(original)!=PROFILE[name] or current!=P.transform(name,original):
        raise RuntimeError('Unqualified application-exit source '+name)
    utility,_=P.normalize(P.regular(source,P.GENERATED))
    if utility!=P.UTILITY.encode(): raise RuntimeError('Unqualified application-exit utility')
    return original


def previous_cleanup_controls(out, fixture):
    spec=importlib.util.spec_from_file_location('mixel_prior_cleanup_controls',ROOT/'scripts/patch-support-macos-cleanup.py')
    prior=importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
    helper,_=P.normalize((ROOT/'scripts/patch-support-macos-cleanup.py').read_bytes())
    assert P.digest(helper)=='197bc021abf3cb8563b92dfca30d5ba7f1c93328db4318cf56386baa9cf6a0fb'
    valid=out/'previous-cleanup-valid'
    shutil.copytree(fixture,valid)
    P.apply(valid,restore=True)
    for name in prior.FILES:
        data=subprocess.check_output(['git','-C',str(ROOT/'rustdesk'),'show',P.PIN+':'+name])
        assert prior.sha(data)==prior.FILES[name]
        target=valid/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
    prior.apply(valid)
    P.apply(valid)
    before=snapshot(valid); P.apply(valid,check=True); assert snapshot(valid)==before
    # Source813 runtime identities were bound before adding this materialization guard.
    # Fixed byte hashes also work in shallow hosted Actions checkouts.
    historical_runtime={'flutter/lib/common.dart': '390d75c80128850d598794502b6f5d265eb99750f64ebfc98140770661cb2cb0', 'flutter/lib/desktop/pages/desktop_home_page.dart': '51ea09a0714b2e92cde95e8676663b4666ba2489f0230f05dec48a95c3606874', 'flutter/lib/desktop/pages/desktop_tab_page.dart': 'db034e32b19d8e99482903e9adb83f9f91ee24bfeb509f0e7658a6217783d074', 'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd05d6fd84b6c2f51775dfb7e2eb928367a81addbba0dbef56adc2494935685f6', 'flutter/lib/main.dart': '982765b4bd1bdf52aec77af49398ff590697978178248d546ec777b947e3d2b8', 'flutter/lib/mixel_support_app_exit.dart': '37b2f956dbbf07e4129c7b0db1d695d11dbdb1952699e4124238b9befcfbf06e', 'flutter/lib/models/ab_model.dart': 'b4c7038cb74488a2916dc2b34e33d25a3084d33a2dcf4989b4af249a2567e5fe', 'flutter/lib/models/native_model.dart': '0c78091698b2aa83fbf4ae4ed283a84c730d319336960f667b501c1eb52ef4b8', 'flutter/lib/models/server_model.dart': '0c85a7230d2277f9d2dea092b81883717b184f7c315f25833078bfd0824bcda8', 'flutter/lib/utils/multi_window_manager.dart': '27e065acf8f7feb2c80151437c071689a2dc33f1bdc9b8ce9ecacd8b74e2ede4', 'flutter/macos/Runner/AppDelegate.swift': '8a70d4f1af3f0ad28a4119d6cd887b2096f8869a559b7589fc54015d19d771a1', 'flutter/macos/Runner/MainFlutterWindow.swift': 'f497d07f72e4704a821faef262a9717c6b540957c613dc0d8db05410f2579aa1'}
    expected_runtime={'flutter/lib/common.dart': '3ee4fd4229ffb0b664c8efdae5f85daf7d316391b86de0074135dbad6b258a3b', 'flutter/lib/desktop/pages/desktop_home_page.dart': 'db693e0fc9c59bb8b15aacab256eac6df9b0622d2f7bf412b02d622b29e0cc7f', 'flutter/lib/desktop/pages/desktop_tab_page.dart': 'db034e32b19d8e99482903e9adb83f9f91ee24bfeb509f0e7658a6217783d074', 'flutter/lib/desktop/widgets/tabbar_widget.dart': 'd05d6fd84b6c2f51775dfb7e2eb928367a81addbba0dbef56adc2494935685f6', 'flutter/lib/main.dart': '982765b4bd1bdf52aec77af49398ff590697978178248d546ec777b947e3d2b8', 'flutter/lib/mixel_support_app_exit.dart': '37b2f956dbbf07e4129c7b0db1d695d11dbdb1952699e4124238b9befcfbf06e', 'flutter/lib/models/ab_model.dart': 'b4c7038cb74488a2916dc2b34e33d25a3084d33a2dcf4989b4af249a2567e5fe', 'flutter/lib/models/native_model.dart': 'd80cb9aa666b49c83de4ddbd3ce79cb99859a9fe16d479cbb36d5643cdb7d4df', 'flutter/lib/models/server_model.dart': '0c85a7230d2277f9d2dea092b81883717b184f7c315f25833078bfd0824bcda8', 'flutter/lib/utils/multi_window_manager.dart': '27e065acf8f7feb2c80151437c071689a2dc33f1bdc9b8ce9ecacd8b74e2ede4', 'flutter/macos/Runner/AppDelegate.swift': '8a70d4f1af3f0ad28a4119d6cd887b2096f8869a559b7589fc54015d19d771a1', 'flutter/macos/Runner/MainFlutterWindow.swift': 'f497d07f72e4704a821faef262a9717c6b540957c613dc0d8db05410f2579aa1'}
    if PROFILE == P.HISTORICAL_FILES:
        expected_runtime = historical_runtime
    else:
        # The qualified current ACK profile retains its needed dart:math import.
        # Prove its entire runtime is the earlier pinned composed body plus only
        # that one import; keep the historical no-ACK profile byte-exact above.
        common = P.regular(valid, 'flutter/lib/common.dart')
        needed_math = b"import 'dart:math';\n"
        assert common.count(needed_math) == 1
        assert P.digest(common.replace(needed_math, b'', 1)) == '090eb7733b1567a8aa2347039ec45966dad68e0620dd14904e4f6cca895b7c3c'
    assert set(expected_runtime)==set(P.FILES)|{P.GENERATED}
    conserved={}
    for name,expected in expected_runtime.items():
        actual=P.regular(valid,name)
        assert P.digest(actual)==expected,name
        conserved[name]=P.digest(actual)
    cases=[('target-'+str(i),name,'append') for i,name in enumerate(prior.FILES)]
    cases += [('provenance-'+str(i),prior.OWNED+'/'+name,'append') for i,name in enumerate(prior.FILES)]
    cases += [('utility-change',prior.GENERATED,'append'),('utility-missing',prior.GENERATED,'unlink'),
              ('proof-change',prior.OWNED+'/proof.json','append'),('proof-missing',prior.OWNED+'/proof.json','unlink'),
              ('owned-missing',prior.OWNED,'remove'),('both-missing',prior.OWNED,'remove-both'),
              ('extra-file',prior.OWNED+'/foreign','new'),('extra-directory',prior.OWNED+'/foreign','directory'),
              ('projected-newline','flutter/lib/utils/multi_window_manager.dart','crlf')]
    if hasattr(os,'mkfifo'): cases += [('fifo',prior.OWNED+'/foreign','fifo')]
    count=0
    for label,name,operation in cases:
        bad=out/('previous-cleanup-'+label); shutil.copytree(valid,bad)
        target=bad/name
        if operation=='append': target.write_bytes(target.read_bytes()+b'// foreign drift\n')
        elif operation=='unlink': target.unlink()
        elif operation in ('remove','remove-both'):
            shutil.rmtree(target)
            if operation=='remove-both': (bad/prior.GENERATED).unlink()
        elif operation=='new': target.write_bytes(b'foreign')
        elif operation=='directory': target.mkdir()
        elif operation=='fifo': os.mkfifo(target)
        else: target.write_bytes(target.read_bytes().replace(b'\n',b'\r\n'))
        before=snapshot(bad)
        for restore,check in ((True,False),(False,True),(False,False)):
            try: P.apply(bad,restore=restore,check=check)
            except RuntimeError: pass
            else: raise AssertionError('Earlier cleanup drift accepted: '+label)
            assert snapshot(bad)==before,'Earlier cleanup refusal mutated actual source: '+label
            count+=1
    verifier=out/'changed-prior-verifier/scripts'; verifier.mkdir(parents=True)
    (verifier/'patch-support-macos-cleanup.py').write_bytes(helper+b'\n# unapproved verifier\n')
    copied={'__name__':'mixel_changed_prior_guard','__file__':str(verifier/'patch-support-macos-termination.py')}
    exec(compile((ROOT/'scripts/patch-support-macos-termination.py').read_bytes(),'copied-current-guard','exec'),copied)
    before=snapshot(valid)
    try: copied['apply'](valid,restore=True)
    except RuntimeError as error: assert 'Changed earlier Mac cleanup verifier' in str(error)
    else: raise AssertionError('Unapproved prior verifier accepted')
    assert snapshot(valid)==before
    count+=1
    current=(ROOT/'scripts/patch-support-macos-termination.py').read_bytes().replace(b'\r\n',b'\n')
    normalized=b'    helper, _ = normalize(regular(helper_path.parent, helper_path.name))\n'
    raw=b'    helper = regular(helper_path.parent, helper_path.name)\n'
    assert current.count(normalized)==1
    original=current.replace(normalized,raw)
    # The production helper changed only its exact composed common.dart pin
    # for the qualified needed-math foundation. Preserve the historical whole
    # helper identity while executing the raw verifier with current inputs.
    current_common_pin = b'66f58cc2606eba8a240e0f960c39c8ba18c14616352ef60b83f6202af5cf605a'
    earlier_common_pin = b'7d8953486dc5087cf7d87bc8c625710ca982f7d7bca5f7f58a4c85ecf3ab2695'
    assert original.count(current_common_pin) == 1
    assert P.digest(original.replace(current_common_pin, earlier_common_pin, 1)) == '3f54cf5084df454de970aca15ef1183989f6c40fa0d35807cca9c1f6a639e513'
    for label,data,accepted in (('lf',helper,True),('crlf',helper.replace(b'\n',b'\r\n'),True),
        ('mixed',helper.replace(b'\n',b'\r\n',1),False),('tampered',helper+b'\n# altered verifier\n',False)):
        scripts=out/('prior-verifier-newline-'+label)/'scripts'; scripts.mkdir(parents=True)
        (scripts/'patch-support-macos-cleanup.py').write_bytes(data)
        scope={'__name__':'mixel_actual_verifier_newlines','__file__':str(scripts/'patch-support-macos-termination.py')}
        exec(compile(current,'actual-current-newline-guard','exec'),scope)
        before=snapshot(valid)
        if accepted:
            scope['apply'](valid,check=True)
            assert snapshot(valid)==before
        else:
            for restore,check in ((True,False),(False,True),(False,False)):
                try: scope['apply'](valid,restore=restore,check=check)
                except RuntimeError: pass
                else: raise AssertionError('Unapproved verifier newline/source accepted: '+label)
                assert snapshot(valid)==before
                count+=1
        if label=='crlf':
            baseline={'__name__':'mixel_actual_173_raw_guard','__file__':scope['__file__']}
            exec(compile(original,'exact-173-original-raw-hash-guard','exec'),baseline)
            try: baseline['apply'](valid,check=True)
            except RuntimeError as error: assert 'Changed earlier Mac cleanup verifier' in str(error)
            else: raise AssertionError('Original173 must reject the real CRLF verifier')
            assert snapshot(valid)==before
    (out/'previous-cleanup-guard-proof.json').write_text(json.dumps({'fatal_zero_mutation_controls':count,
        'all_prior_targets_and_provenance_checked':12,'both_missing_inputs_rejected':True,
        'actual_projected_newline_guard':True,'prior_verifier_sha256':P.digest(helper),
        'actual_LF_CRLF_verifier_directories_pass':True,'actual_mixed_tampered_verifiers_refused':True,
        'composed_raw_hash_CRLF_failure_reproduced':True,
        'historical_173_raw_verifier_sha256':'b0a82ff321131cb8b26dff1ec04d77a88a4c06ba20599910cda96b5d6df6b5b5',
        'bound_generated_runtime_hashes_conserved':conserved, 'profile':'historical' if PROFILE == P.HISTORICAL_FILES else 'composed'},indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(f'PASS: {count} earlier cleanup target/provenance/utility/inventory guards refuse before writes; all12 generated runtime files match the exact selected profile.')


def pipeline_snapshot(root):
    records={}
    for parent,dirs,files in os.walk(root):
        dirs[:]=[name for name in dirs if name not in ('.git','__pycache__')]
        for name in files:
            path=Path(parent)/name
            records[path.relative_to(root).as_posix()]=('link:'+os.readlink(path)
                if path.is_symlink() else P.digest(path.read_bytes()))
    return records


def full_branding(out,bash):
    copied=out/'branding-pipeline-copy'; copied.mkdir()
    revision=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    archive_args=['git','-C',str(ROOT),'-c','core.autocrlf=false','-c','core.eol=lf','archive','--format=tar',revision,'scripts','branding']
    archive=subprocess.check_output(archive_args)
    inherited=dict(os.environ,GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='core.autocrlf',GIT_CONFIG_VALUE_0='true')
    assert subprocess.check_output(archive_args,env=inherited)==archive,'Inherited Git CRLF changed the exact pipeline source archive'
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            if member.isfile():
                target=copied/member.name; target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(stream.extractfile(member).read())
    for name in ('apply-branding.sh','patch-support-url-log-privacy.py','test-support-url-log-privacy.py','patch-support-macos-termination.py','test-support-macos-termination.py',
                 'patch-support-flutter-sdk-compatibility.py','test-support-flutter-sdk-compatibility.py',
                 'test-support-flutter-analysis.py','test-support-macos-cleanup.py','test-support-macos-window.py',
                 'patch-support-file-transfer-window.py','test-support-file-transfer-window.py',
                 'patch-support-keyboard-default.py',
                 'patch-support-uinput-scroll.py',
                 'patch-support-uinput-device.py','test-support-uinput-device.py',
                 'patch-support-native-warnings.py','test-support-native-warnings.py',
                 'patch-support-compiler-cleanup.py','test-support-compiler-cleanup.py',
                 'patch-support-uri-routing.py','test-support-uri-routing.py',
                 'patch-support-macos-gui-launch.py', 'test-support-macos-gui-launch.py', 'support-macos-gui-launch.rs',
                 'patch-support-fingerprint.py','test-support-fingerprint.py',
                 'patch-support-fatal-crash.py','test-support-fatal-crash.py',
                 'patch-macos-update.py','test-macos-update.py','macos-update.scpt','macos-update-bundle.sh'):
        shutil.copy2(ROOT/'scripts'/name,copied/'scripts'/name)
    A.copy_current_pipeline_inputs(copied)
    verifier=copied/'scripts/patch-support-macos-cleanup.py'
    verifier_lf,_=P.normalize(verifier.read_bytes())
    assert P.digest(verifier_lf)=='197bc021abf3cb8563b92dfca30d5ba7f1c93328db4318cf56386baa9cf6a0fb'
    # Execute the complete pipeline with the actual verifier bytes produced by
    # a Windows CRLF checkout, rather than only modeling its digest.
    verifier.write_bytes(verifier_lf.replace(b'\n',b'\r\n'))
    script=copied/'scripts/apply-branding.sh'; results={}
    for label,newline in (('lf',b'\n'),('crlf',b'\r\n')):
        repo=out/('full-branding-'+label)
        subprocess.run(['git','-c','core.autocrlf=false','clone','--quiet','--shared',str(ROOT/'rustdesk'),str(repo)],check=True)
        subprocess.run(['git','-C',str(repo),'-c','core.autocrlf=false','checkout','--quiet',P.PIN],check=True)
        subprocess.run(['git','-c','core.autocrlf=false','clone','--quiet','--shared',str(ROOT/'rustdesk/libs/hbb_common'),str(repo/'libs/hbb_common')],check=True)
        pin=subprocess.check_output(['git','-C',str(repo),'ls-tree','HEAD','libs/hbb_common'],text=True).split()[2]
        subprocess.run(['git','-C',str(repo/'libs/hbb_common'),'-c','core.autocrlf=false','checkout','--quiet',pin],check=True)
        for name in P.FRESH:
            data=subprocess.check_output(['git','-C',str(repo),'show',P.PIN+':'+name])
            assert P.digest(data)==P.FRESH[name],name
            (repo/name).write_bytes(data.replace(b'\n',newline))
        env=dict(os.environ,RDREPO=str(repo),BRANDING=str(copied/'branding'),PYTHONIOENCODING='utf-8')
        for attempt in (1,2):
            completed=subprocess.run([bash,script.as_posix()],cwd=copied,env=env,capture_output=True,text=True,encoding='utf-8',timeout=300)
            (out/f'full-branding-{label}-{attempt}.log').write_text(completed.stdout+completed.stderr,encoding='utf-8')
            assert completed.returncode==0,completed.stdout+completed.stderr
            assert 'coordinated bounded main-engine Mac application exit' in completed.stdout
            with A.startup_baseline(repo) as checked_repo:
                for name in P.FILES:
                    original=P.regular(checked_repo/'flutter/.mixel-dependencies/macos_application_exit',name)
                    assert P.digest(original)==P.FILES[name]
                    current,_=P.normalize(S.regular(checked_repo/S.OWNED,name) if name in S.FILES else P.regular(checked_repo,name))
                    # Existing complete branding already normalizes some targets
                    # to LF. The standalone controls prove our incoming-byte
                    # preservation; here both original checkout newline variants
                    # must produce exact corrected bodies and stable all-file bytes.
                    if name in F.COMPOSED and name not in S.FILES and (checked_repo/F.GENERATED).exists(): current=F.transform(name,current,restore=True)
                    assert current==P.transform(name,original),name
                F.verify_previous_layers(checked_repo) if (checked_repo/F.GENERATED).exists() else S.apply(checked_repo,check=True)
                for name in S.FILES:
                    original=S.regular(checked_repo/S.OWNED,name)
                    actual=S.normalize(S.regular(checked_repo,name))[0]
                    if name in F.COMPOSED and (checked_repo/F.GENERATED).exists(): actual=F.transform(name,actual,restore=True)
                    assert actual==S.transform(name,original),name
            current=pipeline_snapshot(repo)
            if attempt==1: first=current
            else: assert first==current,'Whole branding changed on its second pass'
        target=repo/next(iter(P.FILES)); saved=target.read_bytes(); target.write_bytes(saved+b'// drift\n')
        before=pipeline_snapshot(repo)
        failed=subprocess.run([bash,script.as_posix()],cwd=copied,env=env,capture_output=True,text=True,encoding='utf-8',timeout=300)
        (out/f'full-branding-{label}-drift-refusal.log').write_text(failed.stdout+failed.stderr,encoding='utf-8')
        assert failed.returncode!=0 and pipeline_snapshot(repo)==before,'Pipeline drift mutated earlier targets'
        target.write_bytes(saved)
        results[label]={'files':len(current),'passes':2,'exact_all_11_targets':True,'all_bytes_idempotent':True,'drift_refuses_without_mutation':True}
    (out/'full-branding-integration.json').write_text(json.dumps({'script_sha256':P.digest(script.read_bytes()),
        'actual_prior_verifier_checkout_newline':'crlf','normalized_prior_verifier_sha256':P.digest(verifier_lf),
        'results':results},indent=2)+'\n',encoding='utf-8')
    print('PASS: complete branding LF/CRLF twice, exact Quit targets, whole-pipeline idempotence and zero-mutation drift refusal.')


ORIGINAL_DELEGATE = r'''
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
typedef enum { kFlutterAppExitTypeCancelable, kFlutterAppExitTypeRequired } FlutterAppExitType;
@interface FlutterEngineTerminationHandler : NSObject
@property BOOL shouldTerminate;
@property NSUInteger requests;
- (void)requestApplicationTermination:(id)sender exitType:(FlutterAppExitType)type result:(id)result;
@end
@implementation FlutterEngineTerminationHandler
- (void)requestApplicationTermination:(id)sender exitType:(FlutterAppExitType)type result:(id)result {
 (void)sender; (void)type; (void)result; self.shouldTerminate=YES; self.requests++;
}
@end
@interface FlutterAppDelegate : NSObject
@property FlutterEngineTerminationHandler* terminationHandler;
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication*)sender;
@end
@implementation FlutterAppDelegate
__ORIGINAL_METHOD__
@end
int main() { @autoreleasepool {
 FlutterAppDelegate* delegate=[FlutterAppDelegate new];
 FlutterEngineTerminationHandler* main=[FlutterEngineTerminationHandler new];
 FlutterEngineTerminationHandler* secondary=[FlutterEngineTerminationHandler new];
 delegate.terminationHandler=main;
 // Same retained-handler assignment as each actual engine initializer.
 delegate.terminationHandler=secondary;
 assert([delegate applicationShouldTerminate:nil]==NSTerminateCancel);
 assert(main.requests==0 && secondary.requests==1);
 assert([delegate applicationShouldTerminate:nil]==NSTerminateNow);
 puts("PASS: exact original delegate queries the latest mocked engine; repeated Quit returns Now before the first reply or resource cleanup.");
}}
'''


def original_controls(out, framework=None, sdk_version=None):
    if framework is None and sdk_version is None:
        for version in ('3.24.5', '3.27.4'):
            destination = out/('pinned-engine-'+version)
            destination.mkdir()
            original_controls(destination, sdk_version=version)
        return
    spec=importlib.util.spec_from_file_location('mixel_quit_sdk_profile',ROOT/'scripts/test-support-macos-window.py')
    window=importlib.util.module_from_spec(spec); spec.loader.exec_module(window)
    version=window.framework_profile(framework.resolve()) if framework else sdk_version
    profile=window.SDK_PROFILES[version]
    revision=profile['engine']
    base='https://raw.githubusercontent.com/flutter/engine/'+revision+'/shell/platform/darwin/macos/framework/Source/'
    sources={}
    for name in ('FlutterAppDelegate.mm','FlutterEngine.mm'):
        with urlopen(base+name,timeout=30) as response: data=response.read(200000)
        if name=='FlutterEngine.mm': assert P.digest(data)==profile['native']['Source/FlutterEngine.mm']
        else: assert P.digest(data)=='c71c33c770713f350f0c183a6ee3d7f28a364f653e1c3044e942efe2dfbbee15'
        (out/name).write_bytes(data); sources[name]=data.decode('utf-8')
    source=sources['FlutterAppDelegate.mm']
    anchor='- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication* _Nonnull)sender {'
    start=source.index(anchor); method=source[start:P.brace_end(source,start)+1]
    assert 'flutterAppDelegate.terminationHandler = self;' in sources['FlutterEngine.mm']
    assert sources['FlutterEngine.mm'].count('_terminationHandler = [[FlutterEngineTerminationHandler alloc] initWithEngine:self\n                                                                       terminator:nil];')==1
    manifest={'flutter_version':version,'revision':revision,'files':{name:P.digest((out/name).read_bytes()) for name in sources},
              'original_delegate_method_sha256':P.digest(method.encode()),
              'mock_boundary':'Handler only models its actual pending shouldTerminate flag; no app/engine launch.'}
    (out/'original-source-bindings.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    program=out/'exact-original-delegate.mm'
    program.write_text(ORIGINAL_DELEGATE.replace('__ORIGINAL_METHOD__',method),encoding='utf-8')
    binary=out/'exact-original-delegate'
    run(['xcrun','clang++','-fobjc-arc','-Wall','-Wextra','-Werror','-framework','Foundation','-framework','AppKit',str(program),'-o',str(binary)],out,'original-delegate-compile')
    text=run([str(binary)],out,'original-delegate')
    assert 'repeated Quit returns Now' in text


SWIFT = r'''
import Foundation
import CoreFoundation
__COORDINATOR__
final class Harness {
 var queue: [() -> Void] = []
 var timers: [() -> Void] = []
 var cancellations: [Bool] = []
 var queryReplies: [(Any?) -> Void] = []
 var prepareReplies: [(Any?) -> Void] = []
 var logs: [String] = []
 var terminations = 0
 var queryCalls = 0
 var prepareCalls = 0
 var callbackDepth = 0
 lazy var exit = MixelTerminationCoordinator(
  deferCall: { [unowned self] in self.queue.append($0) },
  deadline: { [unowned self] _, callback in
   let index = self.timers.count
   self.timers.append(callback); self.cancellations.append(false)
   return { [unowned self] in self.cancellations[index] = true }
  },
  query: { [unowned self] in self.queryCalls += 1; self.queryReplies.append($0) },
  prepare: { [unowned self] in self.prepareCalls += 1; self.prepareReplies.append($0) },
  terminate: { [unowned self] in
   precondition(self.callbackDepth == 0, "engine shut down inside channel reply")
   self.terminations += 1
  }, log: { [unowned self] in self.logs.append($0) })
 func pump() { let next = queue.removeFirst(); next() }
 func drain() { while !queue.isEmpty { pump() } }
 func reply(_ callback: (Any?) -> Void, _ value: Any?) {
  callbackDepth += 1; callback(value); callbackDepth -= 1
 }
 func timeout(_ index: Int) { if !cancellations[index] { timers[index]() } }
}
var passed = 0
func check(_ name: String, _ operation: () -> Void) { operation(); passed += 1; print("PASS: " + name) }
check("native reentrant Quit reserves one main query before dispatch") {
 let h=Harness(); h.exit.request(); h.exit.request(); h.exit.request();
 precondition(h.queryCalls==0 && h.queue.count==1); h.drain()
 precondition(h.queryCalls==1 && h.prepareCalls==0)
}
check("aggregate cancellation starts no resources and permits a later Quit") {
 let h=Harness(); var replies:[String]=[]
 h.exit.request { replies.append($0) }; h.drain()
 h.reply(h.queryReplies[0],["response":"cancel"]); h.drain()
 precondition(replies==["cancel"] && h.prepareCalls==0 && h.terminations==0)
 h.exit.request(); h.drain(); precondition(h.queryCalls==2)
}
check("successful main-owned cleanup reply unwinds before process termination") {
 let h=Harness(); var replies:[String]=[]
 h.exit.request { replies.append($0) }; h.drain()
 h.reply(h.queryReplies[0],["response":"exit"]); h.drain()
 precondition(h.prepareCalls==1 && h.terminations==0)
 h.reply(h.prepareReplies[0],["window_id":0,"cleanup":"complete"])
 precondition(h.terminations==0); h.pump()
 precondition(replies==["exit"] && h.terminations==0); h.drain()
 precondition(h.terminations==1 && h.logs.isEmpty)
}
check("all duplicate requesters receive once-only completion") {
 let h=Harness(); var replies:[String]=[]
 for _ in 0..<4 { h.exit.request { replies.append($0) } }; h.drain()
 h.reply(h.queryReplies[0],["response":"exit"]); h.drain()
 h.reply(h.prepareReplies[0],["window_id":0,"cleanup":"complete"]); h.drain()
 precondition(h.queryCalls==1 && h.prepareCalls==1 && h.terminations==1 && replies.count==4)
}
check("hung observer query cancels without pretending cleanup or consent") {
 let h=Harness(); h.exit.request(); h.drain(); h.timeout(0); h.drain()
 precondition(h.exit.phase == .idle && h.prepareCalls==0 && h.terminations==0 && h.logs.count==1)
 h.reply(h.queryReplies[0],["response":"exit"]); h.drain(); precondition(h.prepareCalls==0)
}
check("hung resource cleanup honors permitted Quit and reports incomplete cleanup") {
 let h=Harness(); h.exit.request(); h.drain()
 h.reply(h.queryReplies[0],["response":"exit"]); h.drain(); h.timeout(1); h.drain()
 precondition(h.terminations==1 && h.logs.count==1)
 h.reply(h.prepareReplies[0],["window_id":0,"cleanup":"complete"]); h.drain()
 precondition(h.terminations==1 && h.logs.count==1)
}
for value:Any in [NSNull(), ["response":"unexpected"], ["response":"exit","extra":1], ["response":true]] {
 check("malformed aggregate query keeps running") {
  let h=Harness(); h.exit.request(); h.drain(); h.reply(h.queryReplies[0],value); h.drain()
  precondition(h.prepareCalls==0 && h.terminations==0 && h.logs.count==1)
 }
}
for value:Any in [NSNull(), ["window_id":1,"cleanup":"complete"], ["window_id":false,"cleanup":"complete"], ["window_id":0.5,"cleanup":"complete"], ["window_id":0,"cleanup":"failed"], ["window_id":0,"cleanup":"complete","extra":1]] {
 check("invalid resource acknowledgement cannot report complete cleanup") {
  let h=Harness(); h.exit.request(); h.drain(); h.reply(h.queryReplies[0],["response":"exit"]); h.drain()
  h.reply(h.prepareReplies[0],value); h.drain(); precondition(h.terminations==1 && h.logs.count==1)
 }
}
for duringPrepare in [false,true] {
 check("required exit supersedes old ordinary callbacks without a stale marker") {
  let h=Harness(); var replies:[String]=[]; h.exit.request { replies.append($0) }; h.drain()
  if duringPrepare { h.reply(h.queryReplies[0],["response":"exit"]); h.drain() }
  h.callbackDepth += 1; h.exit.required { replies.append($0) }; h.callbackDepth -= 1
  precondition(h.terminations==0 && replies==["exit","exit"])
  h.reply(h.queryReplies[0],["response":"cancel"])
  if duringPrepare { h.reply(h.prepareReplies[0],["window_id":0,"cleanup":"complete"]) }
  h.drain(); precondition(h.terminations==1)
 }
}

check("approved ordinary request receives exit without adding a pending callback") {
 let h=Harness(); var replies:[String]=[]; h.exit.required { replies.append($0) }
 h.exit.request { replies.append($0) }; h.exit.required { replies.append($0) }
 h.drain(); precondition(replies==["exit","exit","exit"] && h.terminations==1 && h.queryCalls==0)
}
check("required during queued query invalidates it without consulting observers") {
 let h=Harness(); h.exit.request(); h.exit.required { _ in }; h.drain()
 precondition(h.queryCalls==0 && h.prepareCalls==0 && h.terminations==1)
}
print("PASS: \(passed) actual native coordinator lifecycle controls.")
'''


def swift_controls(out, framework, fixture):
    source=out/'actual-termination-coordinator.swift'
    source.write_text(SWIFT.replace('__COORDINATOR__',P.COORDINATOR),encoding='utf-8')
    binary=out/'actual-termination-coordinator'
    run(['xcrun','swiftc','-warnings-as-errors',str(source),'-o',str(binary)],out,'swift-coordinator-compile')
    text=run([str(binary)],out,'swift-coordinator')
    assert '20 actual native coordinator lifecycle controls' in text,text
    if framework:
        sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
        # AppDelegate product body is complete; only unrelated host symbols are
        # small compile fixtures. Native engine/public channels are actual SDK.
        stub=out/'native-host-symbols.swift'
        stub.write_text('''import Cocoa
class MainFlutterWindow: NSWindow { func mixelFinishNativeResources() {} }
func dummy_method_to_enforce_bundling() {}
func handle_applicationShouldOpenUntitledFile() {}
''',encoding='utf-8')
        for target in ('arm64-apple-macosx12.3','x86_64-apple-macosx10.14'):
            run(['xcrun','swiftc','-typecheck','-warnings-as-errors','-sdk',sdk,
                 '-target',target,'-F',str(framework.parent),
                 str(fixture/'flutter/macos/Runner/AppDelegate.swift'),str(stub)],out,'full-appdelegate-'+target)
        plugins={'desktop_multi_window':'FlutterMultiWindowPlugin','desktop_drop':'DesktopDropPlugin',
                 'device_info_plus':'DeviceInfoPlusMacosPlugin','flutter_custom_cursor':'FlutterCustomCursorPlugin',
                 'package_info_plus':'FPPPackageInfoPlusPlugin','path_provider_foundation':'PathProviderPlugin',
                 'sqflite':'SqflitePlugin','uni_links_desktop':'UniLinksDesktopPlugin',
                 'url_launcher_macos':'UrlLauncherPlugin','wakelock_plus':'WakelockPlusMacosPlugin',
                 'window_size':'WindowSizePlugin','texture_rgba_renderer':'TextureRgbaRendererPlugin',
                 'screen_retriever':None,'window_manager':None}
        host=out/'unrelated-plugin-and-core-boundary.swift'
        host.write_text('''import Cocoa
import FlutterMacOS
func rustdesk_core_main() {}
func hiddenWindowAtLaunch() {}
func RegisterGeneratedPlugins(registry: FlutterPluginRegistry) {}
func dummy_method_to_enforce_bundling() {}
func handle_applicationShouldOpenUntitledFile() {}
''',encoding='utf-8')
        for target in ('arm64-apple-macosx12.3','x86_64-apple-macosx10.14'):
            modules=out/('plugin-import-boundary-'+target); modules.mkdir()
            for module,klass in plugins.items():
                source='import FlutterMacOS\n'
                if klass:
                    source+='public class '+klass+' { public static func register(with registrar: FlutterPluginRegistrar) {}\n'
                    if module=='desktop_multi_window':
                        source+=' public static func setOnWindowCreatedCallback(_ callback: @escaping (FlutterViewController) -> Void) {}'
                    source+=' }\n'
                path=modules/(module+'.swift'); path.write_text(source,encoding='utf-8')
                run(['xcrun','swiftc','-emit-module','-warnings-as-errors','-sdk',sdk,'-target',target,
                     '-F',str(framework.parent),'-module-name',module,'-emit-module-path',str(modules/(module+'.swiftmodule')),
                     str(path)],out,'import-boundary-'+module+'-'+target)
            run(['xcrun','swiftc','-typecheck','-warnings-as-errors','-sdk',sdk,'-target',target,
                 '-F',str(framework.parent),'-I',str(modules),str(fixture/'flutter/macos/Runner/AppDelegate.swift'),
                 str(fixture/'flutter/macos/Runner/MainFlutterWindow.swift'),str(host)],out,'full-mainflutterwindow-and-delegate-'+target)
        print('PASS: complete AppDelegate actual pinned Flutter SDK ARM12.3/Intel10.14.')
        print('PASS: complete MainFlutterWindow+AppDelegate bodies both arches; unrelated plugin registration/core boundaries explicit.')


DART_TEST = r'''
import 'dart:async';
import 'dart:ui' as ui;
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/mixel_support_app_exit.dart';

void main() {
  final binding = TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('org.rustdesk.rustdesk/host'), null);
  });
  test('owned pending callbacks and async subscription cancellation are awaited', () async {
    final owner = MixelExitOwner(); final work = Completer<void>(); final cancelled = Completer<void>();
    var calls=0; owner.register(() => cancelled.future);
    final poll=owner.track(() async { calls++; await work.future; });
    final stop=owner.stop(); var finished=false; stop.then((_) => finished=true);
    owner.poll(() async { calls++; });
    await Future<void>.delayed(Duration.zero); expect(finished,false); expect(calls,1);
    work.complete(); await poll; await Future<void>.delayed(Duration.zero); expect(finished,false);
    cancelled.complete(); await stop; expect(finished,true); expect(identical(owner.stop(),stop),true);
  });
  test('synchronous stop reentry is reserved before stopper invocation', () async {
    final owner=MixelExitOwner(); var calls=0; Future<void>? reentered;
    owner.register(() async { calls++; reentered=owner.stop(); });
    final stop=owner.stop(); await stop; expect(calls,1); expect(identical(stop,reentered),true);
  });
  test('periodic callbacks stop without changing unrelated timers', () async {
    final owner=MixelExitOwner(); owner.register(); var calls=0; var foreign=0;
    mixelExitPeriodic(owner,const Duration(milliseconds:1),() async { calls++; },immediate:true);
    final other=Timer.periodic(const Duration(milliseconds:1),(_) => foreign++);
    await Future<void>.delayed(const Duration(milliseconds:12)); await owner.stop(); final atStop=calls;
    await Future<void>.delayed(const Duration(milliseconds:12)); other.cancel();
    expect(calls,atStop); expect(atStop,greaterThan(0)); expect(foreign,greaterThan(0));
  });
  test('actual late tracked failure is retained by drain', () async {
    final owner=MixelExitOwner(); owner.register(); final failed=Completer<void>();
    owner.poll(() => failed.future); failed.completeError(StateError('poll failure'));
    await Future<void>.delayed(Duration.zero);
    await expectLater(owner.stop(),throwsStateError);
  });
  test('strict ACK rejects another window, false integer, extra field and incomplete cleanup', () {
    expect(mixelValidAppAcknowledgement({'window_id':0,'cleanup':'complete'},0),true);
    for (final value in [ {'window_id':1,'cleanup':'complete'}, {'window_id':false,'cleanup':'complete'},
        {'window_id':0.0,'cleanup':'complete'}, {'window_id':0,'cleanup':'failed'},
        {'window_id':0,'cleanup':'complete','extra':1}]) {
      expect(mixelValidAppAcknowledgement(value,0),false);
    }
  });
  test('actual main callback caches synchronous preparation reentry and returns honest failure', () async {
    final messenger=binding.defaultBinaryMessenger; var ready=0; var calls=0;
    messenger.setMockMethodCallHandler(const MethodChannel('org.rustdesk.rustdesk/host'),(call) async {
      expect(call.method,'mixelExitReady'); ready++; return null;
    });
    final pending=Completer<void>(); await mixelInstallMainExitHandler(() async { calls++; await pending.future; });
    final responses=<Completer<ByteData?>>[];
    for (var i=0;i<2;i++) {
      final response=Completer<ByteData?>(); responses.add(response);
      messenger.handlePlatformMessage('org.rustdesk.rustdesk/host',
          const StandardMethodCodec().encodeMethodCall(const MethodCall(mixelAppExitPrepare)),response.complete);
    }
    await Future<void>.delayed(Duration.zero); expect(calls,1); expect(ready,1);
    pending.completeError(StateError('main owned failure'));
    for (final response in responses) {
      final value=const StandardMethodCodec().decodeEnvelope((await response.future)!);
      expect(value,{'window_id':0,'cleanup':'failed'});
    }
  });
  test('every failed and later owner is attempted with actual error preserved', () async {
    var calls=0; final first=MixelExitOwner(); final later=MixelExitOwner();
    first.register(() async { calls++; throw StateError('owned failure'); });
    later.register(() async { calls++; });
    await expectLater(mixelDrainExitOwners(),throwsStateError); expect(calls,2);
  });
}
'''

OWNED_PATHS = r'''
import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/mixel_support_app_exit.dart';

const isMacOS=true;
enum DesktopType { main, cm }
const desktopType=DesktopType.main;
enum PermissionAuthorizeType { authorized }
class NativeQueries {
  Future<String> mainGetConnectStatus() async => '{"status_num":0}';
  Future<String?> cmCheckClientsLength({required int length}) async => null;
}
final bind=NativeQueries();
class NativeWindow { void close() {} }
final windowManager=NativeWindow();
void debugPrint(String value) {}

class OriginalPoll {
 final _mixelExit=MixelExitOwner();
 final password=Completer<void>();
 var _connectStatus=0; final _clients=<int>[]; var _zeroClientLengthCounter=0;
 bool hideCm=false;
 void notifyListeners() {} void updateClientState(String value) {}
 void hideCmWindow() {} void showCmWindow() {}
 Future<void> updatePasswordModel() => password.future;
 Future<void> run() async {
 __ORIGINAL_POLL__
 await timerCallback();
 }
}
class CorrectedPoll {
 final _mixelExit=MixelExitOwner();
 final password=Completer<void>();
 var _connectStatus=0; final _clients=<int>[]; var _zeroClientLengthCounter=0;
 bool hideCm=false;
 void notifyListeners() {} void updateClientState(String value) {}
 void hideCmWindow() {} void showCmWindow() {}
 Future<void> updatePasswordModel() => password.future;
 Future<void> run() async {
 __CORRECTED_POLL__
 await timerCallback();
 }
}
class OriginalAudio {
 final _mixelExit=MixelExitOwner(); final audio=Completer<PermissionAuthorizeType>();
 bool mounted=true, watchIsCanRecordAudio=true; var mutations=0;
 Future<PermissionAuthorizeType> osxCanRecordAudio() => audio.future;
 void setState(void Function() callback) { callback(); mutations++; }
 Future<void> run() async { __ORIGINAL_AUDIO__ }
}
class CorrectedAudio {
 final _mixelExit=MixelExitOwner(); final audio=Completer<PermissionAuthorizeType>();
 bool mounted=true, watchIsCanRecordAudio=true; var mutations=0;
 Future<PermissionAuthorizeType> osxCanRecordAudio() => audio.future;
 void setState(void Function() callback) { callback(); mutations++; }
 Future<void> run() async { __CORRECTED_AUDIO__ }
}
void main() {
 TestWidgetsFlutterBinding.ensureInitialized();
 test('exact original Server poll drops pending password; corrected owner awaits it',() async {
   final old=OriginalPoll(); old._mixelExit.register();
   await old._mixelExit.track(old.run); await old._mixelExit.stop();
   expect(old.password.isCompleted,false); old.password.complete();
   final fixed=CorrectedPoll(); fixed._mixelExit.register();
   final poll=fixed._mixelExit.track(fixed.run);
   await Future<void>.delayed(Duration.zero);
   var stopped=false; final stop=fixed._mixelExit.stop().then((_) => stopped=true);
   await Future<void>.delayed(Duration.zero); expect(stopped,false);
   fixed.password.complete(); await poll; await stop; expect(stopped,true);
 });
 test('exact original Home audio microtask escapes; corrected tracked await cannot mutate disposed State',() async {
   final old=OriginalAudio(); old._mixelExit.register();
   await old._mixelExit.track(old.run); old.mounted=false; await old._mixelExit.stop();
   old.audio.complete(PermissionAuthorizeType.authorized);
   await Future<void>.delayed(Duration.zero); expect(old.mutations,1);
   final fixed=CorrectedAudio(); fixed._mixelExit.register();
   final run=fixed._mixelExit.track(fixed.run); await Future<void>.delayed(Duration.zero);
   fixed.mounted=false; var stopped=false;
   final stop=fixed._mixelExit.stop().then((_) => stopped=true);
   await Future<void>.delayed(Duration.zero); expect(stopped,false);
   fixed.audio.complete(PermissionAuthorizeType.authorized);
   await run; await stop; expect(stopped,true); expect(fixed.mutations,0);
 });
}
'''

TERMINAL_OWNER = r'''
import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/mixel_support_app_exit.dart';
void main() {
 TestWidgetsFlutterBinding.ensureInitialized();
 test('disposed owner cannot re-register; replacement survives; terminal drain waits late registration',() async {
   final old=MixelExitOwner(); var oldStops=0; old.register(() async { oldStops++; });
   final replacement=MixelExitOwner(); var newStops=0; replacement.register(() async { newStops++; });
   await old.stop(); expect(oldStops,1); expect(newStops,0);
   expect(() => old.register(),throwsStateError);
   final gate=Completer<void>(); var lateStops=0, ticks=0;
   final first=MixelExitOwner(); first.register(() => gate.future);
   var finished=false; final drain=mixelDrainExitOwners().then((_) => finished=true);
   final late=MixelExitOwner(); final lateGate=Completer<void>();
   late.register(() async { lateStops++; await lateGate.future; });
   final timer=mixelExitPeriodic(late,const Duration(milliseconds:1),() async { ticks++; },immediate:true);
   expect(late.stopping,true); expect(timer.isActive,false);
   gate.complete(); await Future<void>.delayed(const Duration(milliseconds:8));
   expect(finished,false); expect(lateStops,1); expect(ticks,0); expect(newStops,1);
   lateGate.complete(); await drain; expect(finished,true);
 });
}
'''

EXIT_API = r'''
import 'dart:async';
import 'dart:ui' as ui;
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
const _mixelHost=MethodChannel('org.rustdesk.rustdesk/host');
// Test binding overrides exitApplication itself; restore the exact pinned
// ServicesBinding method as the public superclass boundary under test.
class PublicServicesExitBinding extends AutomatedTestWidgetsFlutterBinding {
 __SDK_EXIT_METHOD__
}
class ActualExitBinding extends PublicServicesExitBinding {
 __ACTUAL_EXIT_METHOD__
}
void main() {
 final binding=ActualExitBinding();
 test('exact public override preserves pre-ready platform fallback and explicit required/cancelable routes',() async {
   var ready=false; final calls=<String>[];
   binding.defaultBinaryMessenger.setMockMethodCallHandler(_mixelHost,(call) async {
     calls.add(call.method);
     if(call.method=='mixelExitAvailable') return ready;
     return {'response':'exit'};
   });
   binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform,(call) async {
     calls.add(call.method); expect(call.method,'System.exitApplication');
     expect(call.arguments,{'type':'cancelable','exitCode':7}); return {'response':'cancel'};
   });
   expect(await binding.exitApplication(ui.AppExitType.cancelable,7),ui.AppExitResponse.cancel);
   expect(calls,['mixelExitAvailable','System.exitApplication']); calls.clear();
   ready=true; expect(await binding.exitApplication(ui.AppExitType.cancelable),ui.AppExitResponse.exit);
   expect(calls,['mixelExitAvailable','mixelExitRequested']); calls.clear();
   expect(await binding.exitApplication(ui.AppExitType.required,9),ui.AppExitResponse.exit);
   expect(calls,['mixelExitRequired']);
 });
}
'''

def extract(source, anchor):
    start=source.index(anchor)
    return source[start:P.brace_end(source,start)+1]


def extracted_owner_controls(widget, pipeline, fixture, out, flutter):
    cases={}
    for mode, root in (('original',pipeline),('corrected',fixture)):
        server=(canonical(root,'flutter/lib/models/server_model.dart').decode() if mode=='original' else
                (root/'flutter/lib/models/server_model.dart').read_text(encoding='utf-8'))
        home=(canonical(root,'flutter/lib/desktop/pages/desktop_home_page.dart').decode() if mode=='original' else
              (root/'flutter/lib/desktop/pages/desktop_home_page.dart').read_text(encoding='utf-8'))
        cases[mode+'_poll']=extract(server,'timerCallback() async {')
        cases[mode+'_audio']=extract(home,'if (watchIsCanRecordAudio) {')
    (out/'owned-method-bindings.json').write_text(json.dumps({
        key:{'sha256':P.digest(value.encode()),'bytes':len(value.encode())}
        for key,value in cases.items()},indent=2)+'\n',encoding='utf-8')
    program=OWNED_PATHS
    for key,value in cases.items(): program=program.replace('__'+key.upper()+'__',value)
    (widget/'test/extracted_owned_paths_test.dart').write_text(program,encoding='utf-8')
    (widget/'test/terminal_owner_test.dart').write_text(TERMINAL_OWNER,encoding='utf-8')
    method=extract(P.UTILITY,'Future<ui.AppExitResponse> exitApplication(')
    sdk=(flutter.parent.parent/'packages/flutter/lib/src/services/binding.dart').read_text(encoding='utf-8')
    sdk_method=extract(sdk,'Future<ui.AppExitResponse> exitApplication(')
    (out/'sdk-public-exit-method.dart').write_text(sdk_method,encoding='utf-8')
    (widget/'test/exit_api_test.dart').write_text(EXIT_API.replace('__ACTUAL_EXIT_METHOD__',method)
        .replace('__SDK_EXIT_METHOD__',sdk_method),encoding='utf-8')



MANAGER_STUB = "const bool isMacOS=true;const bool isWindows=false;\nconst mixelAppWindowPrepare='mixel_prepare_application_window';\nenum WindowType {Main,RemoteDesktop,FileTransfer,ViewCamera,PortForward,Terminal,Unknown}\nclass Offset {const Offset(num x,num y);Object operator &(Size size)=>Object();}\nclass Size {Size(num x,num y);}\nclass Colors {static const black=0;}\nString getWindowNameWithId(String id,{required WindowType overrideType})=>id;\nclass WindowController {\n final int windowId;bool alive=true;WindowController(this.windowId);\n void setInitBackgroundColor(Object x){}void setFrame(Object x){}void center(){}void setTitle(String x){}\n Future<void> show() async {}\n}\nclass DesktopMultiWindow {\n static final calls=<int>[];static final callbacks=<int,Completer<dynamic>>{};\n static Completer<WindowController>? creating;static int creations=0;\n static bool malformed=false;static bool failed=false;static int? held;\n static Future<WindowController> createWindow(String x) {creations++;return creating!.future;}\n static Future<dynamic> invokeMethod(int id,String method) {\n  if(method!=mixelAppWindowPrepare)throw StateError('Non-resource-only message');\n  calls.add(id);\n  if(id==1 && malformed)return Future.value({'window_id':true,'cleanup':'complete'});\n  if(id==2 && failed)return Future.error(StateError('child 2 failure'));\n  if(id==held){final c=Completer<dynamic>();callbacks[id]=c;return c.future;}\n  return Future.value({'window_id':id,'cleanup':'complete'});\n }\n}\nclass Manager {\n bool _mixelClosingSubWindows=false;final _mixelPendingCreations=<Future<void>>{};\n final _mixelExitPendingWindowIds=<int>{};final _mixelAppExitOnce=MixelCleanupOnce();\n bool get mixelApplicationExitStarted=>_mixelAppExitOnce.started;\n final byType=<WindowType,List<int>>{};var destroyed=0;var preparedNative=0;var active=0;\n List<int> _findWindowsByType(WindowType type)=>byType[type]??[];\n Future<void> _mixelPrepareWindowClose(int id)async{preparedNative++;}\n Future<void> _mixelForceCloseWindow(int id)async{destroyed++;}\n void registerActiveWindow(int id){active++;}\n"

MANAGER_CONTROLS = "void check(bool value,String reason){if(!value)throw StateError(reason);}\nFuture<void> tick()=>Future<void>.delayed(Duration.zero);\nFuture<void> main(List<String> args)async{\n final mode=args.single;final m=Manager();\n if(mode=='fanout'){\n  m.byType.addAll({WindowType.Main:[0],WindowType.RemoteDesktop:[1,1],WindowType.FileTransfer:[2],WindowType.ViewCamera:[3],WindowType.PortForward:[4],WindowType.Terminal:[5],WindowType.Unknown:[2]});\n  var ownerStops=0;final owner=MixelExitOwner();owner.register(()async{ownerStops++;});\n  final first=m.mixelPrepareApplicationExit();final second=m.mixelPrepareApplicationExit();\n  check(identical(first,second),'Preparation not cached');await first;\n  check(DesktopMultiWindow.calls.toSet().containsAll([1,2,3,4,5])&&DesktopMultiWindow.calls.length==5,'All five types/dedup failed');\n  check(ownerStops==1&&m.destroyed==0&&m.preparedNative==0,'Duplicate or destructive resource preparation');\n  var refused=false;try{await m.newSessionWindow(WindowType.RemoteDesktop,'synthetic','synthetic',[],true);}on StateError{refused=true;}\n  check(refused&&DesktopMultiWindow.creations==0,'Actual creation gate permitted new native creation');\n  print('PASS: exact manager all5types/dedup/cached main owner drain/non-destructive fanout/actual post-gate creation refusal');\n }else if(mode=='errors'){\n  m.byType[WindowType.RemoteDesktop]=[1,2,3,4,5];DesktopMultiWindow.malformed=true;DesktopMultiWindow.failed=true;DesktopMultiWindow.held=5;\n  var ownerStops=0;final owner=MixelExitOwner();owner.register(()async{ownerStops++;throw StateError('main owner first error');});\n  var finished=false;Object? caught;\n  final waiting=m.mixelPrepareApplicationExit().then<void>((_){finished=true;},onError:(Object error,StackTrace stack){caught=error;finished=true;});\n  await tick();await tick();\n  check(DesktopMultiWindow.calls.length==5&&!finished,'One failure skipped fanout or held later reply');\n  DesktopMultiWindow.callbacks[5]!.complete({'window_id':5,'cleanup':'complete'});await waiting;\n  check(caught is StateError&&caught.toString().contains('main owner first error'),'Original main error lost');\n  check(ownerStops==1&&m.destroyed==0,'Failure skipped main stop or destroyed engine');\n  print('PASS: exact manager starts/awaits all children despite malformed ACK+child failure; preserves first main error and no engine destruction');\n }else if(mode=='creation'){\n  DesktopMultiWindow.creating=Completer<WindowController>();DesktopMultiWindow.held=13;\n  Object? creationError;final opening=m.newSessionWindow(WindowType.RemoteDesktop,'synthetic','synthetic',[],true).then<int>((_)=>throw StateError('Late window unexpectedly accepted'),onError:(Object error,StackTrace stack){creationError=error;return -1;});\n  await tick();check(DesktopMultiWindow.creations==1,'No actual pending source creation');\n  var finished=false;final exiting=m.mixelPrepareApplicationExit().then((_)=>finished=true);\n  await tick();check(!finished&&DesktopMultiWindow.calls.isEmpty,'Exit skipped pending creation');\n  final child=WindowController(13);DesktopMultiWindow.creating!.complete(child);await opening;await tick();await tick();\n  check(creationError is StateError&&DesktopMultiWindow.calls.length==1&&DesktopMultiWindow.calls.single==13,'Late created ID missing resource-only fanout');\n  check(child.alive&&m.destroyed==0&&m.preparedNative==0&&!finished,'Origin engine destroyed before pending reply');\n  DesktopMultiWindow.callbacks[13]!.complete({'window_id':13,'cleanup':'complete'});await exiting;\n  check(child.alive&&m.destroyed==0&&m.active==0,'Application preparation changed engine lifetime or active windows');\n  print('PASS: exact pending-creation source retains originating engine, resource-only ACK awaited, gate blocks registration/native destruction');\n }else if(mode=='bad-ack'){\n  m.byType[WindowType.FileTransfer]=[1];DesktopMultiWindow.malformed=true;Object? caught;\n  await m.mixelPrepareApplicationExit().then<void>((_)=>throw StateError('Malformed ACK accepted'),onError:(Object error,StackTrace stack){caught=error;});\n  check(caught is StateError&&caught.toString().contains('Invalid application-window'),'Malformed ACK not fatal');\n  print('PASS: exact fanout refuses malformed window ACK without qualifying cleanup');\n }else{throw StateError('Unknown control');}\n}\n"

def manager_controls(out, fixture, pipeline, flutter):
    source=(fixture/'flutter/lib/utils/multi_window_manager.dart').read_text(encoding='utf-8')
    cleanup=P.regular(pipeline,'flutter/lib/mixel_support_cleanup.dart').decode()
    core='final _mixelExitOwners'+P.UTILITY.split('final _mixelExitOwners',1)[1].split('// Required exits',1)[0]
    methods={anchor:extract(source,anchor) for anchor in (
        '  Future<int> newSessionWindow(', '  void _mixelAssertCanOpen()',
        '  Future<void> mixelPrepareApplicationExit()')}
    # The exact application preparation uses an arrow expression ending );.
    methods['  Future<void> mixelPrepareApplicationExit()'] += ');'
    program=out/'actual-extracted-manager-control.dart'
    program.write_text("import 'dart:async';\n"+cleanup.replace("import 'dart:async';",'')+'\n'+core+
        MANAGER_STUB+'\n'.join(methods.values())+'\n}\n'+MANAGER_CONTROLS,encoding='utf-8')
    (out/'manager-method-bindings.json').write_text(json.dumps({
        name:P.digest(body.encode()) for name,body in methods.items()},indent=2)+'\n',encoding='utf-8')
    dart=flutter.parent/('cache/dart-sdk/bin/dart.exe' if os.name=='nt' else 'cache/dart-sdk/bin/dart')
    for mode in ('fanout','errors','creation','bad-ack'):
        text=run([str(dart),str(program),mode],out,'actual-manager-'+mode)
        assert text.startswith('PASS: '),text
    print('PASS: exact manager all window types, all-error fanout, pending creation origin lifetime, strict ACK and terminal creation gate.')


def dart_controls(out, app, flutter, pipeline):
    fixture=out/'source-lf'
    if app:
        copied=out/'app-fixture'
        with A.startup_baseline(app) as checked_app:
            shutil.copytree(checked_app,copied,ignore=shutil.ignore_patterns('.git','build','__pycache__'))
        file_transfer=(copied/F.GENERATED).exists() or (copied/F.GENERATED).is_symlink()
        if file_transfer: F.apply(copied,restore=True)
        if (copied/S.OWNED).exists(): S.apply(copied,restore=True)
        for name in (*P.FILES,P.GENERATED):
            shutil.copy2(fixture/name,copied/name)
        shutil.copytree(fixture/'flutter/.mixel-dependencies/macos_application_exit',
                        copied/'flutter/.mixel-dependencies/macos_application_exit',dirs_exist_ok=True)
        P.apply(copied,check=True)
        for name in ('flutter/test/cm_test.dart','flutter/lib/models/web_model.dart',
                     'flutter/lib/web/common.dart','flutter/lib/mobile/pages/terminal_page.dart'):
            shutil.copy2(pipeline/name,copied/name)
    widget=out/'widget-fixture'
    (widget/'lib').mkdir(parents=True)
    (widget/'test').mkdir()
    (widget/'pubspec.yaml').write_text('''name: flutter_hbb
environment:
  sdk: '^3.5.4'
dependencies:
  flutter:
    sdk: flutter
dev_dependencies:
  flutter_test:
    sdk: flutter
''',encoding='utf-8')
    shutil.copy2(fixture/P.GENERATED,widget/'lib/mixel_support_app_exit.dart')
    test=widget/'test/mixel_app_exit_test.dart'
    test.write_text(DART_TEST.replace("import 'dart:ui' as ui;", "import 'dart:typed_data';"),encoding='utf-8')
    extracted_owner_controls(widget,pipeline,fixture,out,flutter)
    manager_controls(out,fixture,pipeline,flutter)
    run([str(flutter),'pub','get'],out,'flutter-exit-fixture-pub-get',widget)
    run([str(flutter),'test','--no-pub','test'],out,'flutter-actual-exit-resource-tests',widget)
    if app:
        version=json.loads(run([str(flutter),'--version','--machine'],out,'actual-full-app-sdk-version',copied/'flutter'))['frameworkVersion']
        if version==S.SDK:
            S.apply(copied)
            if file_transfer: F.apply(copied)
            run([str(flutter),'pub','get'],out,'actual-full-app-sdk-pub-get',copied/'flutter')
        run([str(flutter),'analyze','--no-pub','--no-fatal-infos'],out,'flutter-full-app-analysis',copied/'flutter')
    print('PASS: actual Flutter resource/handler controls'+(' and complete app analysis.' if app else '.'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=Path(os.environ.get('RDREPO','rustdesk')))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--framework',type=Path)
    parser.add_argument('--app',type=Path)
    parser.add_argument('--flutter',type=Path)
    parser.add_argument('--full-branding',action='store_true')
    parser.add_argument('--bash',type=Path,help='Exact active Git Bash executable on Windows')
    args=parser.parse_args()
    analysis_spec=importlib.util.spec_from_file_location('mixel_qualified_analysis_shell',ROOT/'scripts/test-support-flutter-analysis.py')
    analysis=importlib.util.module_from_spec(analysis_spec); analysis_spec.loader.exec_module(analysis)
    bash=analysis.branding_bash(args.bash,os.name=='nt')
    args.source=args.source.resolve()
    args.output=args.output.resolve() if args.output else (Path(tempfile.mkdtemp(prefix='mixel-app-exit-test-'))/'controls').resolve()
    for name in ('framework','app','flutter'):
        value=getattr(args,name)
        if value is not None: setattr(args,name,value.resolve())
    if os.name=='nt' and args.flutter and args.flutter.suffix.lower()!='.bat':
        args.flutter=args.flutter.with_suffix('.bat')
        if not args.flutter.is_file(): raise RuntimeError('Native Windows Flutter batch launcher is missing')
    args.output.mkdir(parents=True,exist_ok=False)
    identities={name:P.digest(P.normalize(P.regular(args.source,name))[0]) for name in P.FILES}
    if identities==P.FRESH:
        full_branding(args.output,bash)
        args.source=args.output/'full-branding-lf'
        args.full_branding=False
    global PROFILE
    provenance = args.source/'flutter/.mixel-dependencies/macos_application_exit'
    if provenance.exists():
        identities = {name:P.digest(P.normalize(P.regular(provenance,name))[0]) for name in P.FILES}
        if identities not in (P.FILES, P.HISTORICAL_FILES):
            raise RuntimeError('Mixed application-exit fixture profiles')
        PROFILE = P.HISTORICAL_FILES if identities == P.HISTORICAL_FILES else P.FILES
    fixture=source_controls(args.source,args.output)
    previous_cleanup_controls(args.output,fixture)
    if shutil.which('xcrun'):
        original_controls(args.output,args.framework)
        swift_controls(args.output,args.framework,fixture)
    if args.flutter: dart_controls(args.output,args.app,args.flutter,args.source)
    if args.full_branding: full_branding(args.output,bash)

if __name__=='__main__': main()
