#!/usr/bin/env python3
"""Inert local Windows-driver controls; all processes/native APIs are mocked."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('windows_build_controls',ROOT/'scripts/local/build-windows.py')
W=importlib.util.module_from_spec(spec);spec.loader.exec_module(W)
B,C=W.B,W.C

def put(root,name,data=b'inert\n'):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p

def archive(machine=0x8664):
    body=struct.pack('<H',machine)+b'\0'*18
    header=b'a.obj/'.ljust(16)+b'0'.ljust(12)+b'0'.ljust(6)+b'0'.ljust(6)+b'0'.ljust(8)+str(len(body)).encode().ljust(10)+b'`\n'
    return b'!<arch>\n'+header+body

class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name).resolve()/'repo';self.root=self.repo/'_scratch/windows';self.root.mkdir(parents=True)
        self.source=self.repo/'rustdesk'
        for name in C.REQUIRED:put(self.source,name)
        put(self.source,'.git/HEAD',B.UPSTREAM_HEAD.encode())
        self.before=b'exact old epoch\n';self.after=b'exact new epoch\n'
        put(self.source,W.EPOCH,self.before)
        self.pubspec=b'dependencies:\n  extended_text: 14.0.0\n';put(self.source,'flutter/pubspec.yaml',self.pubspec)
        self.vcpkg=self.repo/'vcpkg';self.installed=self.vcpkg/'installed'/W.TRIPLET
        for name in W.CODECS:put(self.installed,'lib/'+name+'.lib',archive())
        put(self.vcpkg,'installed/vcpkg/status','\n\n'.join('Package: '+p+'\nArchitecture: '+W.TRIPLET for p in ('libvpx','libyuv','opus','aom','libjpeg-turbo')).encode())
        self.flutter=self.repo/'flutter'
        for name in ('bin/flutter.bat','bin/cache/dart-sdk/bin/dart.exe','bin/cache/artifacts/engine/windows-x64-release/flutter_windows.dll','bin/cache/artifacts/engine/windows-x64-release/flutter_windows.dll.lib'):put(self.flutter,name)
        self.tools=self.repo/'tools'
        for name in ('git.exe','bash.exe','rustup.exe','cmake.exe','ninja.exe','magick.exe','pwsh.exe','msys-2.0.dll','rustc.exe','cargo.exe','link.exe','cl.exe'):put(self.tools,name)
        self.sdk=self.repo/'sdk'
        for name in ('Include/10.0/um/Windows.h','Lib/10.0/um/x64/kernel32.lib','Lib/10.0/ucrt/x64/ucrt.lib'):put(self.sdk,name)
        put(self.repo,'System32/cmd.exe')
        self.llvm=self.repo/'llvm'
        self.libclang=put(self.llvm,'bin/libclang.dll')
        put(self.llvm,'bin/llvm-runtime.dll')
        put(self.llvm,'lib/clang/19/include/stddef.h')
        put(self.llvm,'build/retained-input.dat')
        self.pipeline=['scripts/local/build-windows.py','scripts/apply-branding.sh']
        for name in self.pipeline:put(self.repo,name)
        self.bridge=self.repo/'bridge';self.bridge.mkdir()
        self.files={name:hashlib.sha256((name+' generated').encode()).hexdigest() for name in B.BRIDGE_FILES}
        for name in B.BRIDGE_FILES:put(self.bridge,name,(name+' generated').encode())
        self.output=self.root/'fresh'
        self.args=argparse.Namespace(source=self.source,flutter=self.flutter,vcpkg=self.vcpkg,bridge=self.bridge,libclang=self.libclang,run_root=self.root,output=self.output,jobs=1)
        self.policy={'effective':'RemoteSigned','processEnvironment':'RemoteSigned','scopes':{'MachinePolicy':'Undefined','UserPolicy':'Undefined','Process':'RemoteSigned','CurrentUser':'Undefined','LocalMachine':'Undefined'}}
        self.labels=[];self.calls=[];self.fail=None;self.drift=None;self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(W,'EPOCH_BEFORE',hashlib.sha256(self.before).hexdigest()))
        self.stack.enter_context(mock.patch.object(W,'EPOCH_AFTER',hashlib.sha256(self.after).hexdigest()))
        self.stack.enter_context(mock.patch.object(W,'SHIPPING_PUBSPEC',hashlib.sha256(self.pubspec).hexdigest()))
        self.probe=types.SimpleNamespace(user_sid='S-1-5-21-inert')
        self.stack.enter_context(mock.patch.object(C,'WindowsSecurityProbe',return_value=self.probe))
        self.stack.enter_context(mock.patch.object(C,'windows_private_entry'))
        self.admission=self.stack.enter_context(mock.patch.object(C,'windows_local_admission',return_value=self.root))
        self.stack.enter_context(mock.patch.object(C.platform,'system',return_value='Windows'))
        self.stack.enter_context(mock.patch.object(C.platform,'machine',return_value='AMD64'))
        self.stack.enter_context(mock.patch.object(W,'pe_x64'))
        self.native_clang=self.stack.enter_context(mock.patch.object(W,'libclang_version',return_value='clang version 19.1.5 (inert)'))
        self.stack.enter_context(mock.patch.object(W.shutil,'which',side_effect=lambda name,**kw:str(self.tools/name)))
        self.stack.enter_context(mock.patch.object(W.shutil,'disk_usage',return_value=types.SimpleNamespace(free=100*1024**3)))
        self.stack.enter_context(mock.patch.object(W.subprocess,'run',side_effect=self.process))
        self.stack.enter_context(mock.patch.dict(sys.modules,{'brotli':types.SimpleNamespace()}))
        import rust_toolchain
        self.stack.enter_context(mock.patch.object(rust_toolchain,'find_msvc_linker',return_value=self.tools/'link.exe'))
        self.stack.enter_context(mock.patch.dict(os.environ,{'PATH':str(self.tools),'SystemRoot':str(self.repo),'VSCMD_ARG_TGT_ARCH':'x64','VSCMD_ARG_HOST_ARCH':'x64','INCLUDE':'inert','LIB':'inert','WindowsSdkDir':str(self.sdk),'WindowsSDKVersion':'10.0','PSExecutionPolicyPreference':'RemoteSigned','LIBCLANG_PATH':'untrusted caller path','PYTHONNOUSERSITE':'0','PYTHONPATH':'untrusted','PYTHONUSERBASE':'untrusted'},clear=True))
        old_call=W.Runner.call
        def recording(runner,label,*args,**kw):self.labels.append(label);return old_call(runner,label,*args,**kw)
        self.stack.enter_context(mock.patch.object(W.Runner,'call',recording))
        self.proof={'schema':1,'upstreamHead':B.UPSTREAM_HEAD,'hbbHead':B.HBB_HEAD,'flutterHead':B.FLUTTER_HEAD,'flutterVersion':B.FLUTTER_VERSION,
             'frbVersion':'1.80.1','frbFeatures':['uuid'],'macBuildQualified':True,'inputs':W.bridge_inputs(C.inventory(self.source),self.source),
             'pipeline':W.pipeline_files(self.repo,self.pipeline),'bridgeFiles':self.files,
             'exactWindowsLeafDelta':{'path':W.EPOCH,'before':W.EPOCH_BEFORE,'after':W.EPOCH_AFTER}}
        self.proof['inputsSha256']=C.binding(self.proof['inputs'])
        (self.bridge/'bridge-proof.json').write_text(json.dumps(self.proof))
    def process(self,argv,**kw):
        argv=[str(x) for x in argv];label=self.labels[-1];self.calls.append((label,argv,kw.get('env',{})));code=23 if label==self.fail else 0;out=b'inert\n'
        if Path(argv[0]).name=='git.exe':
            if 'rev-parse' in argv:
                p=Path(argv[2]);head=B.HBB_HEAD if p.name=='hbb_common' else B.VCPKG_HEAD if p==self.vcpkg else B.FLUTTER_HEAD if p==self.flutter else B.UPSTREAM_HEAD
                out=(head+'\n').encode()
            elif 'status' in argv:out=b''
            elif 'ls-files' in argv:out=('\0'.join(self.pipeline)+'\0').encode()
        elif label.endswith('-path'):out=(str(self.tools/(label.split('-')[0]+'.exe'))+'\n').encode()
        elif label=='rustc-version':out=b'rustc 1.75.0 (inert)\nhost: x86_64-pc-windows-msvc\n'
        elif label=='cargo-version':out=b'cargo 1.75.0 (inert)\n'
        elif label=='installed-rust-targets':out=(W.TARGET+'\n').encode()
        elif label=='flutter-version':out=json.dumps({'frameworkVersion':B.FLUTTER_VERSION,'frameworkRevision':B.FLUTTER_HEAD}).encode()
        elif label=='tool-pwsh.exe':out=b'7.5.0\n'
        elif label=='powershell-execution-policy':out=json.dumps(self.policy).encode()
        if code==0:
            app=self.output/'rustdesk'
            if label=='apply-branding':(app/W.EPOCH).write_bytes(self.after)
            elif label=='check-entire-app':put(self.output,'rust-check/check.result.json',b'{"status":"passed","compiled":true,"sourceUnchanged":true}')
            elif label=='build-entire-app':
                for name in ('mixel-remote.exe','libmixel-remote.dll','flutter_windows.dll','data/icudtl.dat','data/flutter_assets/asset'):put(app,'flutter/build/windows/x64/runner/Release/'+name)
            elif label=='prepare-unsigned-payload':
                shutil.copytree(app/'flutter/build/windows/x64/runner/Release',self.output/'unsigned-payload')
                (self.output/'unsigned-payload/mixel-remote.exe').rename(self.output/'unsigned-payload/Mixel-Remote.exe')
            elif label=='package-unsigned-outer':put(self.output,'dist/Mixel-Remote-UNSIGNED-local-QA.exe')
            if self.drift==label:(app/'Cargo.lock').write_bytes(b'changed lock')
        kw['stdout'].write(out);kw['stderr'].write(b'inert failure' if code else b'')
        return subprocess.CompletedProcess(argv,code)
    def build(self):
        with contextlib.redirect_stdout(io.StringIO()):return W.build(self.args,self.repo)

    def test_system_command_servicing_hardlink_is_admitted_and_hash_bound(self):
        command=self.repo/'System32/cmd.exe'
        alias=self.repo/'WinSxS/cmd.exe';alias.parent.mkdir();os.link(command,alias)
        self.build()
        tools=json.loads((self.output/'toolchains.json').read_text())
        self.assertEqual(tools['actualInputHashes'][str(command)],hashlib.sha256(command.read_bytes()).hexdigest())
        result=json.loads((self.output/'build.result.json').read_text())
        self.assertTrue(result['toolchainInputsUnchanged'])
        with self.assertRaises(RuntimeError):W.file(command)

    def test_only_exact_generated_plugin_registrants_are_outside_rust_bridge_inputs(self):
        before=W.bridge_inputs(C.inventory(self.source),self.source)
        for name in W.FLUTTER_PLUGIN_REGISTRANTS:put(self.source,name,b'Flutter-generated registration\n')
        self.assertEqual(W.bridge_inputs(C.inventory(self.source),self.source),before)
        authored='flutter/windows/runner/flutter_window.cpp'
        put(self.source,authored,b'authored native integration\n')
        rows=W.bridge_inputs(C.inventory(self.source),self.source)
        self.assertIn(authored,{row['path'] for row in rows})
        for name in W.FLUTTER_PLUGIN_REGISTRANTS:(self.source/name).unlink()
        self.assertEqual(W.bridge_inputs(C.inventory(self.source),self.source),rows)

    def test_system_command_drift_prevents_qualification(self):
        command=self.repo/'System32/cmd.exe';old_process=self.process
        def drift(argv,**kw):
            result=old_process(argv,**kw)
            if self.labels[-1]=='compiled-support-guard':command.write_bytes(b'changed system command')
            return result
        with mock.patch.object(W.subprocess,'run',side_effect=drift):
            with self.assertRaises(RuntimeError):self.build()
        result=json.loads((self.output/'build.result.json').read_text())
        self.assertFalse(result['buildQualified']);self.assertFalse(result['toolchainInputsUnchanged'])

    def test_mocked_full_build_keeps_unsigned_scope_exact_order_and_input_identity(self):
        before=C.inventory(self.source);result=self.build()
        self.assertTrue(result['buildQualified']);self.assertEqual(before,C.inventory(self.source))
        self.assertTrue(result['suppliedLibclangUnchanged']);self.assertTrue(result['executionPolicyQualified'])
        clang=json.loads((self.output/'libclang-admission.json').read_text())
        self.assertEqual(clang['dllSha256'],B.digest(self.libclang));self.assertEqual(clang['version'],'clang version 19.1.5 (inert)')
        self.assertIn('build/retained-input.dat',{row['path'] for row in clang['inputs']})
        self.native_clang.assert_called_once_with(self.libclang)
        for flag in ('nativeRuntimeQualified','signingQualified','publish'):self.assertIs(result[flag],False)
        sequence=['apply-branding','shared-bridge-rdev-profile','fetch-locked','shipping-pub-get','check-entire-app','build-entire-app','analyze-entire-flutter','compiled-support-guard','compiled-branding','prepare-unsigned-payload','package-unsigned-outer','verify-outer-manifest']
        self.assertEqual([self.labels.index(x) for x in sequence],sorted(self.labels.index(x) for x in sequence))
        build=next(argv for label,argv,_ in self.calls if label=='build-entire-app')
        self.assertEqual(build[1:],['build.py','--flutter','--skip-portable-pack'])
        for label,_,env in self.calls:
            self.assertNotIn('GITHUB_ACTIONS',env);self.assertNotIn('RUNNER_TEMP',env)
            self.assertEqual(env['PYTHONNOUSERSITE'],'1');self.assertNotIn('PYTHONPATH',env);self.assertNotIn('PYTHONUSERBASE',env)
            if label=='build-entire-app':
                self.assertNotIn('CARGO_BUILD_TARGET',env);self.assertEqual(env['CARGO_BUILD_JOBS'],'1');self.assertEqual(env['VCPKG_MAX_CONCURRENCY'],'1')
            if label in ('fetch-locked','check-entire-app','build-entire-app','compiled-support-guard'):
                self.assertEqual(env['LIBCLANG_PATH'],str(self.libclang.parent))
                self.assertEqual(env['PSExecutionPolicyPreference'],'RemoteSigned')
        self.assertLess(self.labels.index('powershell-execution-policy'),self.labels.index('apply-branding'))
        self.assertTrue(all('Set-ExecutionPolicy' not in ' '.join(argv) for _,argv,_ in self.calls))
        self.assertFalse((self.output/'build-active.json').exists())
        receipts=[json.loads(p.read_text()) for p in (self.output/'commands').glob('*.command.json')]
        self.assertTrue(all(p['scope']=='local unsigned native Windows x64 application build' for p in receipts))
    def test_failed_command_has_actual_raw_receipt_and_cannot_qualify(self):
        self.fail='build-entire-app'
        with self.assertRaises(RuntimeError):self.build()
        self.assertFalse(json.loads((self.output/'build.result.json').read_text())['buildQualified'])
        receipt=next((self.output/'commands').glob('*-build-entire-app.result.json'))
        self.assertEqual(json.loads(receipt.read_text())['returncode'],23)
        self.assertNotIn('package-unsigned-outer',self.labels)
    def test_final_lock_drift_cannot_false_pass(self):
        self.drift='compiled-support-guard'
        with self.assertRaises(RuntimeError):self.build()
        result=json.loads((self.output/'build.result.json').read_text());self.assertFalse(result['buildQualified']);self.assertFalse(result['cargoLockUnchanged'])
    def test_wrong_bridge_input_or_only_six_hashes_cannot_admit(self):
        for mutate in ('input','output','profile'):
            with self.subTest(mutate=mutate):
                app=self.root/('candidate-'+mutate);shutil.copytree(self.source,app);(app/W.EPOCH).write_bytes(self.after)
                if mutate=='input':(app/'src/lib.rs').write_bytes(b'wrong Rust input')
                if mutate=='output':(self.bridge/B.BRIDGE_FILES[0]).write_bytes(b'wrong output')
                if mutate=='profile':
                    proof={**self.proof,'inputsSha256':'bad'};(self.bridge/'bridge-proof.json').write_text(json.dumps(proof))
                with self.assertRaises(RuntimeError):W.admit_bridge(self.bridge,app,self.probe,self.repo)
    def test_exact_whole_epoch_successor_only_no_other_normalization(self):
        app=self.root/'candidate';shutil.copytree(self.source,app)
        with self.assertRaises(RuntimeError):W.admit_bridge(self.bridge,app,self.probe,self.repo)
        (app/W.EPOCH).write_bytes(self.after)
        W.admit_bridge(self.bridge,app,self.probe,self.repo)
        (app/'Cargo.lock').write_bytes(b'changed lock')
        with self.assertRaises(RuntimeError):W.admit_bridge(self.bridge,app,self.probe,self.repo)
    def test_real_crlf_whole_file_view_retains_physical_hash_and_rejects_mixed(self):
        app=self.root/'crlf';shutil.copytree(self.source,app);(app/W.EPOCH).write_bytes(self.after)
        for path in app.rglob('*'):
            if path.is_file() and '.git' not in path.parts:path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
        admitted=W.admit_bridge(self.bridge,app,self.probe,self.repo)
        epoch=next(x for x in admitted['actualWindowsPhysicalInputs'] if x['path']==W.EPOCH)
        self.assertEqual(epoch['logicalSha256'],W.EPOCH_AFTER);self.assertNotEqual(epoch['sha256'],W.EPOCH_AFTER)
        (app/'src/lib.rs').write_bytes(b'first\r\nsecond\n')
        with self.assertRaisesRegex(RuntimeError,'mixed/bare-CR'):W.admit_bridge(self.bridge,app,self.probe,self.repo)
    def test_storage_native_admission_and_jobs_refuse_before_commands(self):
        with mock.patch.object(W.shutil,'disk_usage',return_value=types.SimpleNamespace(free=9*1024**3)):
            with self.assertRaisesRegex(RuntimeError,'80GiB'):self.build()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())
        with mock.patch.object(C,'WindowsSecurityProbe',side_effect=Refusal('native only')):
            with self.assertRaises(RuntimeError):self.build()
        for jobs in (0,-1,True,999999):
            self.args.jobs=jobs
            with self.assertRaises(Exception):self.build()
        self.assertFalse(self.calls)
    def test_foreign_coff_codec_object_refuses(self):
        (self.installed/'lib/vpx.lib').write_bytes(archive(0xaa64))
        with self.assertRaisesRegex(RuntimeError,'non-AMD64'):W.codec_archives(self.vcpkg)
    def test_batch_arguments_and_environment_refuse_injection_and_secrets(self):
        for value in ('C:/x&exit/ flutter.bat','C:/%TOKEN%/flutter.bat','C:/x!bad/flutter.bat'):
            with self.assertRaises(RuntimeError):W.batch(Path('cmd.exe'),Path(value),'pub','get')
        env=W.safe_environment({'Path':'safe','AZURE_CLIENT_SECRET':'secret','GITHUB_ACTIONS':'true','RUNNER_TEMP':'spoof','CARGO_BUILD_JOBS':'99','PYTHONNOUSERSITE':'0','PYTHONPATH':'untrusted','PYTHONUSERBASE':'untrusted'})
        self.assertEqual(env['PATH'],'safe');self.assertFalse(any('secret' in x for x in env.values()));self.assertNotIn('RUNNER_TEMP',env)
        self.assertEqual(env['PYTHONNOUSERSITE'],'1');self.assertNotIn('PYTHONPATH',env);self.assertNotIn('PYTHONUSERBASE',env)

    def test_only_existing_explicit_remotesigned_process_context_is_preserved(self):
        env=W.safe_environment({'PSExecutionPolicyPreference':'RemoteSigned','LIBCLANG_PATH':'untrusted','LLVM_CONFIG_PATH':'untrusted'})
        self.assertEqual(env['PSExecutionPolicyPreference'],'RemoteSigned');self.assertNotIn('LIBCLANG_PATH',env);self.assertNotIn('LLVM_CONFIG_PATH',env)
        for value in ('Bypass','Unrestricted','Restricted','AllSigned','Undefined',''):
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'existing explicit RemoteSigned'):
                W.safe_environment({'PSExecutionPolicyPreference':value})
        with mock.patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'caller must explicitly authorize'):self.build()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())

    def test_execution_policy_requires_complete_effective_process_and_gpo_metadata(self):
        self.assertEqual(W.execution_policy(json.dumps(self.policy)),self.policy)
        mutations=[]
        for key in ('MachinePolicy','UserPolicy','Process'):
            changed=json.loads(json.dumps(self.policy));changed['scopes'][key]='Restricted';mutations.append(changed)
        for key in ('effective','processEnvironment'):
            changed=json.loads(json.dumps(self.policy));changed[key]='Bypass';mutations.append(changed)
        changed=json.loads(json.dumps(self.policy));del changed['scopes']['LocalMachine'];mutations.append(changed)
        for changed in mutations:
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):W.execution_policy(json.dumps(changed))

    def test_restrictive_gpo_is_recorded_and_stops_before_branding(self):
        self.policy['scopes']['MachinePolicy']='AllSigned';self.policy['effective']='AllSigned'
        with self.assertRaisesRegex(RuntimeError,'Group Policy'):self.build()
        result=json.loads((self.output/'build.result.json').read_text());self.assertFalse(result['buildQualified']);self.assertFalse(result['executionPolicyQualified'])
        self.assertEqual(json.loads((self.output/'execution-policy.json').read_text()),self.policy)
        self.assertNotIn('apply-branding',self.labels);self.assertIn('Group Policy',result['error'])

    def test_libclang_requires_absolute_separate_ordinary_native_dependency(self):
        for path in (Path('llvm/bin/libclang.dll'),self.tools/'clang.exe',self.source/'bin/libclang.dll',self.output/'bin/libclang.dll',self.repo.parent/'foreign/bin/libclang.dll'):
            if path.is_absolute():put(path.parent,path.name)
            with self.subTest(path=path),self.assertRaises(RuntimeError):
                W.admit_libclang(path,self.repo,self.source,self.output,self.probe)
        if self.output.exists():shutil.rmtree(self.output)
        self.native_clang.side_effect=RuntimeError('actual DLL load refused')
        with self.assertRaisesRegex(RuntimeError,'DLL load refused'):self.build()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())

    def test_missing_libclang_resource_headers_and_foreign_native_pe_refuse(self):
        header=self.llvm/'lib/clang/19/include/stddef.h';header.unlink()
        with self.assertRaisesRegex(RuntimeError,'resource headers'):self.build()
        put(self.llvm,'lib/clang/19/include/stddef.h')
        with mock.patch.object(W,'pe_x64',side_effect=RuntimeError('non-AMD64 DLL')):
            with self.assertRaisesRegex(RuntimeError,'non-AMD64'):
                W.admit_libclang(self.libclang,self.repo,self.source,self.output,self.probe)
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())

    def test_libclang_links_and_untrusted_dependency_owner_refuse(self):
        alias=self.llvm/'bin/alias.dll';os.link(self.libclang,alias)
        with self.assertRaises(RuntimeError):self.build()
        alias.unlink()
        link=self.llvm/'lib/clang/19/include/indirect.h';link.symlink_to(self.llvm/'lib/clang/19/include/stddef.h')
        with self.assertRaises(RuntimeError):self.build()
        link.unlink()
        def refuse(path,probe,*,directory):
            if Path(path)==self.llvm:raise RuntimeError('untrusted dependency owner')
        with mock.patch.object(C,'windows_private_entry',side_effect=refuse):
            with self.assertRaisesRegex(RuntimeError,'untrusted dependency owner'):self.build()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())

    def test_wrapper_rejects_llvm_flutter_overlap_before_native_load_or_output(self):
        nested=self.llvm/'flutter-sdk';nested.mkdir()
        for flutter in (self.llvm,nested):
            with self.subTest(flutter=flutter):
                self.args.flutter=flutter
                with self.assertRaisesRegex(RuntimeError,'separate owned dependency'):self.build()
                self.native_clang.assert_not_called()
                self.assertFalse(self.calls);self.assertFalse(self.output.exists())
        self.args.flutter=self.flutter
        original=self.libclang
        self.args.libclang=put(self.flutter,'llvm/bin/libclang.dll')
        with self.assertRaisesRegex(RuntimeError,'separate owned dependency'):self.build()
        self.native_clang.assert_not_called();self.assertFalse(self.calls);self.assertFalse(self.output.exists())
        self.args.libclang=original

    def test_libclang_tree_change_during_identity_refuses_before_output(self):
        def changed(path):
            (self.llvm/'bin/llvm-runtime.dll').write_bytes(b'changed dependency');return 'clang version 19.1.5'
        self.native_clang.side_effect=changed
        with self.assertRaisesRegex(RuntimeError,'changed during admission'):self.build()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())

    def test_libclang_resource_or_dll_drift_cannot_qualify(self):
        old_process=self.process
        for index,name in enumerate(('lib/clang/19/include/stddef.h','bin/libclang.dll','build/retained-input.dat')):
            with self.subTest(name=name):
                self.output=self.root/('drift-'+str(index));self.args.output=self.output
                target=self.llvm/name;before=target.read_bytes()
                def changed(argv,**kw):
                    result=old_process(argv,**kw)
                    if self.labels[-1]=='compiled-support-guard':target.write_bytes(b'changed LLVM input')
                    return result
                with mock.patch.object(W.subprocess,'run',side_effect=changed):
                    with self.assertRaises(RuntimeError):self.build()
                result=json.loads((self.output/'build.result.json').read_text());self.assertFalse(result['buildQualified']);self.assertFalse(result['suppliedLibclangUnchanged'])
                target.write_bytes(before)
    def test_default_plan_has_no_process_or_writes(self):
        with mock.patch.object(sys,'argv',['driver']),mock.patch.object(W,'build') as build,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(W.main(),0);build.assert_not_called()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())

    def test_missing_preinstalled_tool_and_unsafe_output_stop_before_compile(self):
        with mock.patch.object(W.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'preinstall required tools'):self.build()
        self.assertFalse(self.calls);self.assertFalse(self.output.exists())
        self.output.mkdir()
        with self.assertRaises(RuntimeError):self.build()
        self.assertFalse(self.calls)

    def test_bash_python_shim_has_physical_lf_even_under_windows_text_translation(self):
        original=Path.write_text
        def windows_text(path,value,*args,**kwargs):
            if path.name=='python3':return path.write_bytes(value.replace('\n','\r\n').encode())
            return original(path,value,*args,**kwargs)
        with mock.patch.object(Path,'write_text',windows_text):self.build()
        self.assertEqual((self.output/'tools-bin/python3').read_bytes(),b'#!/usr/bin/env bash\nexec "$MIXEL_LOCAL_PYTHON" "$@"\n')

    def test_pipeline_or_source_mutation_prevents_qualification(self):
        self.fail='compiled-support-guard'
        old_process=self.process
        def changed(argv,**kw):
            result=old_process(argv,**kw)
            if self.labels[-1]=='compiled-support-guard':(self.source/'src/lib.rs').write_bytes(b'changed original')
            return result
        with mock.patch.object(W.subprocess,'run',side_effect=changed):
            with self.assertRaises(RuntimeError):self.build()
        result=json.loads((self.output/'build.result.json').read_text())
        self.assertFalse(result['buildQualified']);self.assertFalse(result['suppliedSourceUnchanged'])


class FormatControls(unittest.TestCase):
    def test_libclang_identity_uses_confined_loader_and_disposes_actual_version(self):
        import ctypes
        path=Path('/inert/llvm/bin/libclang.dll')
        library=types.SimpleNamespace(**{name:mock.Mock() for name in ('clang_createIndex','clang_disposeIndex','clang_parseTranslationUnit2','clang_getClangVersion','clang_getCString','clang_disposeString')})
        value=object();library.clang_getClangVersion.return_value=value;library.clang_getCString.return_value=b'clang version 19.1.5 (inert)'
        with mock.patch.object(W.os,'name','nt'),mock.patch.object(ctypes,'CDLL',return_value=library) as loader:
            self.assertEqual(W.libclang_version(path),'clang version 19.1.5 (inert)')
        loader.assert_called_once_with(str(path),winmode=0x900);library.clang_disposeString.assert_called_once_with(value)
        del library.clang_parseTranslationUnit2
        with mock.patch.object(W.os,'name','nt'),mock.patch.object(ctypes,'CDLL',return_value=library):
            with self.assertRaisesRegex(RuntimeError,'could not expose'):W.libclang_version(path)

    def test_actual_pe_parser_requires_amd64_and_allows_system_hardlinks_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();data=bytearray(86);data[:2]=b'MZ';struct.pack_into('<I',data,60,80);data[80:86]=b'PE\0\0\x64\x86'
            exe=put(root,'cmd.exe',bytes(data));W.pe_x64(exe)
            linked=root/'system-alias.exe';os.link(exe,linked);W.pe_x64(linked)
            with self.assertRaises(RuntimeError):W.file(linked)
            linked.unlink();data[84:86]=b'\x64\xaa';exe.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError,'AMD64'):W.pe_x64(exe)

    def test_native_macos_host_cannot_enter_windows_probe(self):
        if os.name=='nt':self.skipTest('this control qualifies the non-Windows refusal')
        with self.assertRaisesRegex(RuntimeError,'actual native Windows|native Windows x64'):
            C.WindowsSecurityProbe()
        with self.assertRaisesRegex(RuntimeError,'native Windows'):
            W.libclang_version(Path('unused.dll'))

Refusal=C.Refusal
if __name__=='__main__':unittest.main(verbosity=2)
