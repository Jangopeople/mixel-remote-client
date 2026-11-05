#!/usr/bin/env python3
"""Source-only private URL logs and single-owner Mac support forwarding controls."""
import ast
import base64
import hashlib
import importlib.util
import os
import re
import types
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('url_privacy', ROOT / 'scripts/patch-support-url-log-privacy.py')
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
FIXTURES = {'rustdesk': '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',
'tao': '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'}


def source(kind):
    return zlib.decompress(base64.b64decode(FIXTURES[kind]))


def function(text, name):
    start = text.index('extern "C" fn ' + name + '(')
    opening = text.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


class PrivacyControls(unittest.TestCase):
    def test_exact_file_and_callback_inverse_lf_crlf(self):
        for kind in P.SOURCES:
            original = source(kind)
            self.assertEqual(P.digest(original), P.SOURCES[kind]['original'])
            for profile in (b'\n', b'\r\n'):
                raw = original.replace(b'\n', profile)
                after = P.transform(kind, raw)
                self.assertEqual(P.transform(kind, after), after)
                self.assertEqual(P.transform(kind, after, True), raw)
                self.assertEqual(P.transform(kind, raw, True), raw)
                normalized, eol = P.canonical(after)
                self.assertEqual(eol.encode(), profile)
                self.assertEqual(P.digest(normalized.encode()), P.CORRECTED[kind])
                inverse = normalized
                if kind == 'rustdesk':
                    self.assertEqual(inverse.count(P.SUPPORT_OWNER_GUARD + P.CALLBACK_ANCHOR), 1)
                    inverse = inverse.replace(P.SUPPORT_OWNER_GUARD + P.CALLBACK_ANCHOR, P.CALLBACK_ANCHOR)
                inverse = inverse.replace(P.SOURCES[kind]['after'], P.SOURCES[kind]['before'])
                self.assertEqual(inverse.encode(), original)
                name = 'handle_open_urls' if kind == 'rustdesk' else 'application_open_urls'
                self.assertEqual(function(inverse, name), function(original.decode(), name))
                self.assertEqual(normalized.count(P.SOURCES[kind]['after']), 1)
                self.assertNotIn(P.SOURCES[kind]['before'], normalized)

    def test_old_privacy_only_profile_restores_exactly_before_new_apply(self):
        original = source('rustdesk')
        legacy = original.replace(P.SOURCES['rustdesk']['before'].encode(), P.SOURCES['rustdesk']['after'].encode())
        self.assertEqual(P.digest(legacy), P.PRIVACY_ONLY_RUSTDESK)
        for newline in (b'\n', b'\r\n'):
            raw = legacy.replace(b'\n', newline)
            with self.assertRaisesRegex(RuntimeError, 'requires exact early restoration'):
                P.transform('rustdesk', raw)
            restored = P.transform('rustdesk', raw, True)
            self.assertEqual(restored, original.replace(b'\n', newline))
            fixed = P.transform('rustdesk', restored)
            self.assertEqual(P.transform('rustdesk', fixed, True), restored)
            with tempfile.TemporaryDirectory(prefix='url-privacy-migration-') as directory:
                root = Path(directory).resolve()
                path = root / P.SOURCES['rustdesk']['path']
                path.parent.mkdir(parents=True); path.write_bytes(raw)
                self.assertEqual(P.apply(root, 'rustdesk', True, True), 0)
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(P.apply(root, 'rustdesk', True), 1)
                self.assertEqual(path.read_bytes(), restored)
                self.assertEqual(P.apply(root, 'rustdesk'), 1)
                self.assertEqual(path.read_bytes(), fixed)
        for corruption in (legacy+b'// foreign\n', legacy.replace(b'URL received.', b'URL changed.'),
                           legacy.replace(b'\n', b'\r\n', 1)):
            for restore in (False, True):
                with self.assertRaises(RuntimeError): P.transform('rustdesk', corruption, restore)

    def test_actual_native_callback_scope_uses_existing_validator(self):
        # Reuse the literal native-array adapters from the unchanged original
        # compiled callback test; execute the actual complete callback bodies.
        tree = ast.parse((ROOT/'scripts/test-support-url-log-privacy.py').read_text())
        owner = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                     and n.name == 'test_compiled_actual_callback_forwarding_and_fixed_payloads')
        assignment = next(n for n in owner.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'harness' for t in n.targets))
        harness = ast.literal_eval(assignment.value)
        main = r"""fn main() {
    let valid="mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000001&apikey=synthetic-fixture-key-000000".to_owned();
    let ordinary="mixel-remote://123456789".to_owned();
    let invalid="mixel-remote://support/?invite=invalid&apikey=synthetic-fixture-key-000000".to_owned();
    let values=vec![valid.clone(),ordinary.clone(),invalid.clone()];
    let role=std::env::args().nth(1).unwrap_or_default();
    let skip=cfg!(feature="flutter") && matches!(role.as_str(),"--tray"|"--server");
    let expected=if skip {vec![ordinary,invalid]}else{values.clone()};
    let array=FakeArray{urls:values.iter().map(|value|FakeUrl{string:CString::new(value.as_str()).unwrap()}).collect()};
    application_open_urls(&Object(0),Sel(std::ptr::null()),std::ptr::null(),&array as *const FakeArray as id);
    let mut forwarded=FORWARDED.lock().unwrap();
    while forwarded.len()<expected.len(){let (next,result)=DONE.wait_timeout(forwarded,Duration::from_secs(5)).unwrap();forwarded=next;assert!(!result.timed_out());}
    let mut actual=forwarded.clone();actual.sort();let mut sorted=expected;sorted.sort();assert_eq!(actual,sorted);
    assert_eq!(*PARSED.lock().unwrap(),values); // Event::Opened retains all URLs.
    assert_eq!(LOGS.lock().unwrap().iter().filter(|(level,_)|*level=="debug").count(),3);
    println!("PASS: exact native callback scopes validated support to Tao only for Flutter tray/server; all other callbacks and URLs conserved");
}
"""
        harness = harness[:harness.index('fn main() {')] + main
        queue = (ROOT/'scripts/support-macos-url-queue.rs').resolve()
        self.assertTrue(queue.is_file())
        parser = 'mod server {pub(crate) mod mixel_support_url {\n' + queue.read_text() + '\n}}\n'
        bodies = ('mod delegate { use super::{Object,Sel,id,cocoa,log}; use super::cocoa::foundation::NSString; #[no_mangle] '
                  +function(P.transform('rustdesk',source('rustdesk')).decode(),'handle_open_urls')+' }\n'
                  +'extern "C" { fn handle_open_urls(obj: &Object, sel: Sel, id: id, urls: id); }\n'
                  +function(P.transform('tao',source('tao')).decode(),'application_open_urls'))
        rustc=os.environ.get('RUSTC_BIN') or shutil.which('rustc')
        self.assertIsNotNone(rustc)
        with tempfile.TemporaryDirectory(prefix='url-single-owner-rust-') as directory:
            root=Path(directory).resolve()
            for enabled in (False, True):
                path=root/('flutter.rs' if enabled else 'nonflutter.rs')
                path.write_text(harness.replace('/* ACTUAL_CALLBACKS */',parser+bodies),encoding='utf-8',newline='\n')
                binary=path.with_suffix('')
                command=[str(rustc),'--edition=2021','--cfg','test','-Dwarnings',str(path),'-o',str(binary)]
                if enabled:command.extend(['--cfg','feature="flutter"'])
                result=subprocess.run(command,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                for role in ('--tray','--server','--cm','--service','--connect','', '--tray-extra'):
                    result=subprocess.run([str(binary),*([role] if role else [])],capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(result.stdout,'PASS: exact native callback scopes validated support to Tao only for Flutter tray/server; all other callbacks and URLs conserved\n')

    def test_exact_support_owner_guard_drift_refuses_before_any_write(self):
        corrected = P.transform('rustdesk', source('rustdesk'))
        mutations = (
            corrected.replace(b'feature = "flutter"', b'feature = "wrong"'),
            corrected.replace(b'Some("--tray" | "--server")', b'Some("--tray" | "--cm")'),
            corrected.replace(b'&& crate::server::mixel_support_url::is_valid_support_url(&theurl)', b'|| crate::server::mixel_support_url::is_valid_support_url(&theurl)'),
            corrected.replace(b'                continue;\n', b'                return;\n'),
            corrected.replace(P.SUPPORT_OWNER_GUARD.encode(), b'            // unknown callback owner\n'),
            corrected.replace(P.SUPPORT_OWNER_GUARD.encode(), (P.SUPPORT_OWNER_GUARD*2).encode()),
        )
        self.assertTrue(all(raw != corrected for raw in mutations))
        refused = 0
        with tempfile.TemporaryDirectory(prefix='url-owner-refusals-') as directory:
            root=Path(directory).resolve(); path=root/P.SOURCES['rustdesk']['path']
            path.parent.mkdir(parents=True)
            for raw in mutations:
                for newline in (b'\n',b'\r\n'):
                    before=raw.replace(b'\n',newline);path.write_bytes(before)
                    for restore in (False,True):
                        for check in (False,True):
                            with self.assertRaisesRegex(RuntimeError,'Pinned URL logger source changed'):
                                P.apply(root,'rustdesk',restore,check)
                            self.assertEqual(path.read_bytes(),before)
                            refused+=1
        self.assertEqual(refused,48)

    def test_unknown_encoding_and_source_refused(self):
        for kind in P.SOURCES:
            original = source(kind)
            for value in (original+b'// foreign\n', b'\xef\xbb\xbf'+original,
                          original+b'\xff', original+b'\r', original.replace(b'\n', b'\r\n', 1),
                          original.replace(P.SOURCES[kind]['before'].encode(), b'trace!("unknown");')):
                for restore in (False, True):
                    with self.assertRaises((RuntimeError, UnicodeError)):
                        P.transform(kind, value, restore)

    def test_actual_file_check_repeat_restore_and_mode(self):
        with tempfile.TemporaryDirectory(prefix='url-privacy-file-') as directory:
            root = Path(directory).resolve()
            for kind in P.SOURCES:
                target = root / P.SOURCES[kind]['path']; target.parent.mkdir(parents=True, exist_ok=True)
                for eol in (b'\n', b'\r\n'):
                    original = source(kind).replace(b'\n', eol)
                    target.write_bytes(original); target.chmod(0o640)
                    self.assertEqual(P.apply(root, kind, check=True), 0)
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(P.apply(root, kind), 1)
                    after = target.read_bytes()
                    self.assertEqual(P.apply(root, kind), 0)
                    self.assertEqual(P.apply(root, kind, restore=True, check=True), 0)
                    self.assertEqual(target.read_bytes(), after)
                    self.assertEqual(P.apply(root, kind, restore=True), 1)
                    self.assertEqual(target.read_bytes(), original)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_actual_unsafe_file_refusal_zero_writes(self):
        with tempfile.TemporaryDirectory(prefix='url-privacy-refusal-') as directory:
            base = Path(directory).resolve()
            for kind in P.SOURCES:
                for case in ('foreign', 'symlink', 'hardlink', 'parent-symlink'):
                    root=base/(kind+'-'+case); target=root/P.SOURCES[kind]['path']
                    target.parent.mkdir(parents=True); target.write_bytes(source(kind))
                    outside=base/(kind+'-'+case+'-outside'); outside.write_bytes(source(kind))
                    if case=='foreign': target.write_bytes(target.read_bytes()+b'// foreign\n')
                    elif case=='symlink': target.unlink(); target.symlink_to(outside)
                    elif case=='hardlink': target.unlink(); os.link(outside, target)
                    else:
                        parent=target.parent; moved=base/(kind+'-parent-real'); parent.rename(moved); parent.symlink_to(moved, target_is_directory=True)
                    before=target.read_bytes()
                    for restore in (False, True):
                        for check in (False, True):
                            with self.assertRaises(RuntimeError): P.apply(root, kind, restore, check)
                            self.assertEqual(target.read_bytes(), before)

    def test_owned_cargo_reservation_and_marker_refusals(self):
        with tempfile.TemporaryDirectory(prefix='url-privacy-home-') as directory:
            base=Path(directory).resolve(); home=base/'owned'
            self.assertEqual(P.reserve_cargo_home(home), home)
            self.assertEqual(P.reserve_cargo_home(home), home)
            self.assertEqual(P.validate_cargo_home(home), home)
            marker=home/P.MARKER; original=marker.read_bytes()
            marker.write_bytes(original.replace(b'privacy', b'foreign'))
            with self.assertRaises(RuntimeError): P.validate_cargo_home(home)
            marker.write_bytes(original); marker.chmod(0o644)
            with self.assertRaises(RuntimeError): P.validate_cargo_home(home)
            marker.chmod(0o600); home.chmod(0o777)
            with self.assertRaises(RuntimeError): P.validate_cargo_home(home)
            home.chmod(0o700)
            unowned=base/'unowned'; unowned.mkdir(); (unowned/'existing').write_text('fixture')
            with self.assertRaises(RuntimeError): P.reserve_cargo_home(unowned)
            alias=base/'alias'; alias.symlink_to(home, target_is_directory=True)
            with self.assertRaises(RuntimeError): P.validate_cargo_home(alias)
            shared=base/'.cargo'
            with self.assertRaises(RuntimeError): P.reserve_cargo_home(shared)
            self.assertFalse(shared.exists())

    def test_tao_identity_inventory_and_inverse_guard_with_git_seam(self):
        with tempfile.TemporaryDirectory(prefix='url-privacy-tao-') as directory:
            home=Path(directory).resolve()/'owned'; P.reserve_cargo_home(home)
            checkout=home/'git/checkouts/tao-fixture/288c219'; checkout.mkdir(parents=True); (checkout/'.git/objects').mkdir(parents=True)
            target=checkout/P.SOURCES['tao']['path']; target.parent.mkdir(parents=True); target.write_bytes(source('tao'))
            other=checkout/'Cargo.toml'; other.write_bytes(b'[package]\nname="fixture"\n')
            originals={P.SOURCES['tao']['path']:source('tao'), 'Cargo.toml':other.read_bytes()}
            head={name:('100644','a'*40) for name in originals}; index=dict(head)
            common_dir='.git'
            def git(_checkout, *args):
                self.assertEqual(_checkout, checkout)
                if args==('rev-parse','HEAD'): return P.TAO_COMMIT.encode()+b'\n'
                if args==('rev-parse','HEAD^{tree}'): return P.TAO_TREE.encode()+b'\n'
                if args==('rev-parse','--git-dir'): return b'.git\n'
                if args==('rev-parse','--git-common-dir'): return common_dir.encode()+b'\n'
                if args==('rev-parse','--show-toplevel'): return str(checkout).encode()+b'\n'
                if args==('ls-tree','-r','-z','HEAD'): return b''.join((mode+' blob '+oid+'\t'+name+'\0').encode() for name,(mode,oid) in head.items())
                if args==('ls-files','--stage','-z'): return b''.join((mode+' '+oid+' 0\t'+name+'\0').encode() for name,(mode,oid) in index.items())
                if args==('ls-files','--others','-z'): return b''
                if args[0]=='show': return originals[args[1][5:]]
                raise AssertionError(args)
            with patch.object(P,'git',git):
                self.assertEqual(P.tao_checkout(home), checkout)
                self.assertEqual(P.apply(checkout,'tao'),1)
                self.assertEqual(P.tao_checkout(home), checkout)
                before=target.read_bytes(); other.write_bytes(b'foreign')
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                self.assertEqual(target.read_bytes(),before)
                other.write_bytes(originals['Cargo.toml'])
                target.write_bytes(before+b'// foreign\n')
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                target.write_bytes(before)
                del index['Cargo.toml']
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                self.assertEqual(target.read_bytes(),before)
                index.update(head); index['Cargo.toml']=('100644','b'*40)
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                index.update(head)
                other.chmod(0o755)
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                other.chmod(0o644)
                outside=Path(directory).resolve()/'external-git';outside.mkdir()
                common_dir=str(outside)
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                common_dir='.git'
                internal_common=home/'git/common-owned';(internal_common/'objects/info').mkdir(parents=True)
                (internal_common/'objects/info/alternates').write_text(str(outside)+'\n')
                common_dir=str(internal_common)
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                self.assertEqual(target.read_bytes(),before)
                common_dir='.git'
                info=checkout/'.git/objects/info';info.mkdir()
                alternate=info/'alternates';alternate.write_text(str(outside)+'\n')
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                alternate.unlink()
                internal=home/'git/db/owned/objects';internal.mkdir(parents=True)
                alternate.write_text(str(internal)+'\n')
                self.assertEqual(P.tao_checkout(home),checkout)
                alternate.unlink()
                original_git=checkout/'.git'; moved=checkout/'git-fixture-saved';original_git.rename(moved)
                original_git.symlink_to(moved,target_is_directory=True)
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
                original_git.unlink();moved.rename(original_git)
            with patch.object(P,'git',lambda checkout,*args:b'wrong\n' if args==('rev-parse','HEAD') else git(checkout,*args)):
                with self.assertRaises(RuntimeError): P.tao_checkout(home)
            second=checkout.parent/'other'; second.mkdir();(second/'.git').mkdir()
            with self.assertRaises(RuntimeError): P.tao_checkout(home)

    def test_compiled_actual_callback_forwarding_and_fixed_payloads(self):
        rustc=os.environ.get('RUSTC_BIN') or shutil.which('rustc')
        self.assertIsNotNone(rustc, 'Rust compiler is mandatory for source callback controls')
        harness='#![allow(non_camel_case_types,non_snake_case,dead_code)]\nuse std::ffi::CString;\nuse std::sync::{Mutex,Condvar};\nuse std::time::Duration;\nuse cocoa::foundation::NSString;\n#[repr(transparent)] #[derive(Clone,Copy)] struct Sel(*const ());\n#[repr(transparent)] struct Object(u8);\ntype id=*const std::ffi::c_void;\nstruct FakeUrl { string:CString }\nstruct FakeArray { urls:Vec<FakeUrl> }\nstatic FORWARDED:Mutex<Vec<String>>=Mutex::new(Vec::new());\nstatic PARSED:Mutex<Vec<String>>=Mutex::new(Vec::new());\nstatic LOGS:Mutex<Vec<(&str,String)>>=Mutex::new(Vec::new());\nstatic DONE:Condvar=Condvar::new();\n#[macro_export] macro_rules! fixture_debug { ($($a:tt)*)=>{ crate::LOGS.lock().unwrap().push(("debug",format!($($a)*))) }; }\nmod log { pub use crate::fixture_debug as debug; }\nmacro_rules! trace { ($($a:tt)*)=>{ crate::LOGS.lock().unwrap().push(("trace",format!($($a)*))) }; }\nmod cocoa { pub mod foundation {\n    pub trait NSArray { fn count(self)->usize; fn objectAtIndex(self,i:usize)->*const crate::FakeUrl; }\n    impl NSArray for crate::id {\n        fn count(self)->usize { unsafe{(*(self as *const crate::FakeArray)).urls.len()} }\n        fn objectAtIndex(self,i:usize)->*const crate::FakeUrl { unsafe{ &(&(*(self as *const crate::FakeArray)).urls)[i] } }\n    }\n    pub trait NSURL { fn absoluteString(self)->*const std::ffi::CString; }\n    impl NSURL for *const crate::FakeUrl { fn absoluteString(self)->*const std::ffi::CString { unsafe{&(*self).string} } }\n    pub trait NSString { fn UTF8String(self)->*const std::ffi::c_char; }\n    impl NSString for *const std::ffi::CString { fn UTF8String(self)->*const std::ffi::c_char { unsafe{(*self).as_ptr()} } }\n} }\nuse cocoa::foundation::{NSArray,NSURL};\nuse std::ffi::CStr;\nmod url {\n    #[derive(Debug)] pub struct Url(pub String);\n    impl Url { pub fn parse(value:&str)->Result<Self,()> { Ok(Self(value.to_owned())) } }\n}\nstruct AppState;\nimpl AppState { fn open_urls(urls:Vec<url::Url>) { *PARSED.lock().unwrap()=urls.into_iter().map(|u|u.0).collect(); } }\nfn handle_url_scheme(value:String) { FORWARDED.lock().unwrap().push(value); DONE.notify_all(); }\n/* ACTUAL_CALLBACKS */\nfn main() {\n    let expected=vec!["mixel-remote://support/?invite=synthetic-fixture-one".to_owned(),"mixel-remote://support/?invite=synthetic-fixture-two".to_owned()];\n    let array=FakeArray{urls:expected.iter().map(|value|FakeUrl{string:CString::new(value.as_str()).unwrap()}).collect()};\n    application_open_urls(&Object(0),Sel(std::ptr::null()),std::ptr::null(),&array as *const FakeArray as id);\n    let mut forwarded=FORWARDED.lock().unwrap();\n    while forwarded.len()<expected.len() { let (next,result)=DONE.wait_timeout(forwarded,Duration::from_secs(5)).unwrap();forwarded=next;assert!(!result.timed_out()); }\n    let mut actual=forwarded.clone();actual.sort();let mut sorted=expected.clone();sorted.sort();assert_eq!(actual,sorted);\n    assert_eq!(*PARSED.lock().unwrap(),expected);\n    let logs=LOGS.lock().unwrap();assert_eq!(logs.iter().filter(|(level,_)|*level=="debug").count(),2);\n    assert_eq!(logs.iter().filter(|(level,_)|*level=="trace").count(),3);\n    if /* CORRECTED */ { for (_,message) in logs.iter() { assert!(!message.contains("mixel-remote://"));assert!(!message.contains("synthetic-fixture")); } }\n    else { assert_eq!(logs.iter().filter(|(_,message)|message.contains("mixel-remote://")).count(),3); }\n    println!("PASS: actual callbacks preserve all original URLs and logging levels/counts");\n}\n'
        with tempfile.TemporaryDirectory(prefix='url-privacy-rust-') as directory:
            base=Path(directory).resolve()
            for corrected in (False, True):
                rd=source('rustdesk');tao=source('tao')
                if corrected:rd=P.transform('rustdesk',rd);tao=P.transform('tao',tao)
                bodies='mod delegate { use super::{Object,Sel,id,cocoa,log}; use super::cocoa::foundation::NSString; #[no_mangle] '+function(rd.decode(),'handle_open_urls')+' }\nextern "C" { fn handle_open_urls(obj: &Object, sel: Sel, id: id, urls: id); }\n'+function(tao.decode(),'application_open_urls')
                code=harness.replace('/* ACTUAL_CALLBACKS */', bodies).replace('/* CORRECTED */','true' if corrected else 'false')
                path=base/('corrected.rs' if corrected else 'original.rs');path.write_text(code)
                binary=base/('corrected' if corrected else 'original')
                result=subprocess.run([str(rustc),'--edition=2021','-Dwarnings',str(path),'-o',str(binary)],capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,'PASS: actual callbacks preserve all original URLs and logging levels/counts\n')

    def test_unchanged_actual_privacy_matcher_rejects_old_payload_only(self):
        text=(ROOT/'scripts/test-support-launch-macos.py').read_text()
        module=ast.parse(text)
        node=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='privacy_issue')
        namespace={'re':re,'unix':types.SimpleNamespace(TOKEN='fixture-token',API_KEY='fixture-api'), 'WARM_TOKEN':'fixture-warm'}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<source-only-privacy-issue>','exec'),namespace)
        matcher=namespace['privacy_issue']
        synthetic=b'mixel-remote://support/?invite=synthetic-fixture'
        self.assertTrue(matcher(b'URL received: '+synthetic))
        self.assertTrue(matcher(b'Get `application:openURLs:` URLs: ['+synthetic+b']'))
        for message in (b'URL received.',b'Trigger `application:openURLs:`',b'Get `application:openURLs:` URLs received.',b'Completed `application:openURLs:`'):
            self.assertFalse(matcher(message))

    def test_mandatory_workflow_and_macro_order(self):
        helper='scripts/patch-support-url-log-privacy.py'
        macro=(ROOT/'scripts/apply-branding.sh').read_text()
        check='python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-url-log-privacy.py" --root "$RDREPO" --restore --check'
        restore=check.removesuffix(' --check')
        apply=restore.removesuffix(' --restore')
        self.assertEqual(macro.count(check+'\n'),1)
        self.assertLess(macro.index(check),macro.index('patch-support-compiler-cleanup.py'))
        self.assertLess(macro.index(restore+'\n'),macro.index('patch-macos-startup.py'))
        self.assertGreater(macro.index(apply+'\n'),macro.rindex('patch-support-compiler-cleanup.py'))
        build=(ROOT/'.github/workflows/build.yml').read_text()
        mac=build.split('\n  macos:',1)[1].split('\n  windows:',1)[0]
        self.assertEqual(mac.count('mixel_cargo_home="$RUNNER_TEMP/mixel-owned-cargo-${{ matrix.job.arch }}"'),1)
        self.assertEqual(mac.count('echo "CARGO_HOME=$mixel_cargo_home" >> "$GITHUB_ENV"'),1)
        self.assertEqual(mac.count('--reserve-cargo-home "$mixel_cargo_home"'),1)
        self.assertLess(mac.index('--reserve-cargo-home'),mac.index('Install Rust'))
        self.assertLess(mac.index('Apply Mixel-Remote branding'),mac.index('cargo fetch --locked'))
        self.assertLess(mac.index('cargo fetch --locked'),mac.index('--cargo-home "$CARGO_HOME"'))
        self.assertLess(mac.index('--cargo-home "$CARGO_HOME"'),mac.index('Preflight generated support source'))
        self.assertIn("-url-privacy-${{ hashFiles('scripts/patch-support-url-log-privacy.py', 'scripts/test-support-url-log-privacy.py') }}",mac)
        self.assertNotIn('continue-on-error',mac)
        for workflow in ('build.yml','preflight.yml'):
            text=(ROOT/'.github/workflows'/workflow).read_text()
            self.assertEqual(text.count('python3 scripts/test-support-url-log-privacy.py'),1)


if __name__=='__main__':
    unittest.main()
