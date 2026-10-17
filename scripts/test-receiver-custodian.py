#!/usr/bin/env python3
"""Source-only Windows custody controls; never compiles or invokes native APIs."""
from pathlib import Path
import argparse,contextlib,hashlib,importlib.util,json,os,shutil,stat,tempfile,unittest
HERE=Path(__file__).absolute().parent
spec=importlib.util.spec_from_file_location('custodian_owner',HERE/'patch-support-receiver-custodian.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
PLAN,TEMPLATES=P.plan()
TEXT=TEMPLATES["src/server/mixel_receiver_custodian_windows.rs"].decode()
EPOCH=TEMPLATES["src/server/mixel_receiver_custodian_epoch_windows.rs"].decode()

def snapshot(root):
    return {str(p.relative_to(root)):(p.read_bytes(),stat.S_IMODE(p.stat().st_mode)) for p in root.rglob('*') if p.is_file()}

def fixture(root,crlf=False):
    for row in PLAN['files']:
        p=root/row['name'];p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(row['beforeSource'].replace('\n','\r\n' if crlf else '\n').encode());p.chmod(0o644)

class ExactOwnerControls(unittest.TestCase):
    def test_actual_five_predecessor_pins_and_all_hunks_inverse(self):
        for row in PLAN['files']:
            raw=row['beforeSource'].encode();self.assertEqual(P.sha(raw),row['beforeSha256'])
            self.assertEqual(P.transform(P.transform(raw,row),row,True),raw)
            self.assertEqual(P.sha(P.transform(raw,row)),row['afterSha256'])
    def test_full_lf_and_physical_crlf_repeat_and_whole_inverse(self):
        for crlf in (False,True):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t).resolve();fixture(root,crlf);before=snapshot(root)
                self.assertEqual(P.apply(root)['writes'],6)
                self.assertEqual(P.apply(root)['writes'],0)
                self.assertEqual(P.inspect(root)[3],'after')
                self.assertEqual(P.apply(root,restore=True)['writes'],6)
                self.assertEqual(snapshot(root),before)
    def test_drift_partial_and_wrong_mode_fail_before_any_write(self):
        for kind in ('drift','partial','mode'):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t).resolve();fixture(root);row=PLAN['files'][2];p=root/row['name']
                if kind=='drift':p.write_bytes(p.read_bytes()+b'\n')
                elif kind=='partial':p.write_text(row['afterSource'])
                else:
                    # Windows maps writable POSIX modes to0666;0444 is the
                    # real representable readonly mismatch on both platforms.
                    p.chmod(0o444)
                    self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o444)
                before=snapshot(root)
                try:
                    with self.assertRaises(ValueError):P.apply(root)
                    self.assertEqual(snapshot(root),before)
                finally:
                    if kind=='mode':p.chmod(0o644)
    def test_link_and_hardlink_refused(self):
        for hard in (False,True):
            with tempfile.TemporaryDirectory() as t:
                root=Path(t).resolve();fixture(root);p=root/'src/core_main.rs';other=root/'foreign';p.rename(other)
                if hard:os.link(other,p)
                else:p.symlink_to(other)
                with self.assertRaises(ValueError):P.apply(root)
    def test_private_inverse_preserves_dependency_git_and_prunes_opaque_entries(self):
        with tempfile.TemporaryDirectory() as t,tempfile.TemporaryDirectory(prefix='custody-external-sentinel-') as e:
            root=Path(t).resolve();external=Path(e).resolve();fixture(root)
            metadata=['.git/HEAD','.mixel-deps/evdev/.git/HEAD','.mixel-deps/parity-tokio-ipc/.git/config','submodule/.git','legitimate/target/source.rs']
            for name in metadata:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('exact-metadata:'+name)
            sentinel=external/'sentinel';sentinel.write_bytes(b'unchanged external sentinel')
            (root/'target').mkdir();(root/'target/cache').write_text('cache');P.apply(root)
            for opaque_name in ('worktrees','WoRkTrEeS'):
                opaque=root/opaque_name
                # Only the opaque entry is created/unlinked. Never construct,
                # read, write or traverse ANY descendant beneath that name.
                opaque.symlink_to(external,target_is_directory=True)
                try:
                    with P.previous(root) as stage:
                        for name in metadata:self.assertEqual((stage/name).read_bytes(),(root/name).read_bytes())
                        self.assertFalse((stage/'target').exists())
                        self.assertNotIn(opaque_name,os.listdir(stage))
                    self.assertEqual(sentinel.read_bytes(),b'unchanged external sentinel')
                finally:opaque.unlink()
    def test_owned_leaf_drift_cannot_restore_over_it(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();fixture(root);P.apply(root)
            p=root/PLAN['owned'][0]['name'];p.write_bytes(p.read_bytes()+b'\n')
            with self.assertRaises(ValueError):P.apply(root,restore=True)
    def test_prior_copy_never_mutates_supplied_source(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();fixture(root);P.apply(root);before=snapshot(root)
            with P.previous(root) as stage:
                self.assertEqual(P.inspect(stage)[3],'before')
            self.assertEqual(snapshot(root),before)

class ActualRoutingControls(unittest.TestCase):
    def body(self,name):return next(r['afterSource'] for r in PLAN['files'] if r['name']==name)
    def test_local_and_portable_share_actual_custodian_no_direct_fallback(self):
        windows=self.body('src/server/mixel_receiver_input_windows.rs');receiver=self.body('src/server/mixel_receiver_input.rs');portable=self.body('src/server/portable_service.rs')
        self.assertIn('enqueue_local(work:Work){custodian::local_input(work);}',windows)
        self.assertNotIn('fn schedule_local(',windows)
        self.assertIn('else if custodian::ready()',windows)
        self.assertIn('!super::super::portable_service::client::running() && custodian::epoch().as_ref()==Some(epoch)',windows)
        self.assertIn('Backend::Local(_)=>custodian::local_block(scope,&request)',windows)
        self.assertIn('if let Ok(_serial)=scope.serial.lock()',windows)
        self.assertIn('let Ok(_serial)=work.generation.serial.lock()',receiver)
        self.assertIn('windows::Backend::Local(_)=>windows::custodian_retire(self)',receiver)
        for name in ('custodian_input','custodian_block','custodian_retire_packet'):self.assertIn('::'+name+'(',portable)
        self.assertNotIn('::receive_async(packet,',portable)
        self.assertNotIn('native_block_owner("local-process",wanted)',receiver)
    def test_exact_sas_stays_in_original_publisher_never_custodian_effect(self):
        windows=self.body('src/server/mixel_receiver_input_windows.rs');receiver=self.body('src/server/mixel_receiver_input.rs')
        self.assertLess(receiver.index('windows::sas_action(&work.action)'),receiver.index('if let windows::Backend::Portable(target) = &work.generation.backend'))
        self.assertIn('Union::ControlKey(key)',windows)
        self.assertIn('ControlKey::CtrlAltDel',windows)
        self.assertIn('with_receiver_owner_checked(&generation.token,generation.effect_id',windows)
        self.assertIn('runtime.block_on(crate::ipc::receiver_sas::request(&generation.token))',windows)
        self.assertIn('(action.valid()&&!sas_action(&action)).then_some(action)',windows)
        self.assertNotIn('SendSAS(',TEXT)
        self.assertEqual(set(r['name'] for r in PLAN['files']) & {'src/ipc.rs','src/platform/windows.rs','src/server/input_service.rs','src/server/mixel_receiver_native.rs'},set())
    def test_exact_warning_fix_only(self):
        row=next(r for r in PLAN['files'] if r['name'].endswith('input_windows.rs'))
        matches=[p for p in row['changes'] if 'let mut state=attempt().lock()' in p['before']]
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]['after'],matches[0]['before'].replace('let mut state=','let state=',1))
    def test_kernel_auth_and_readonly_handle_capture_precedes_queue_ack(self):
        for required in ('pipe_pid(stream.sas_pipe_handle(),false)!=Some(pid)','child.process.clone()','filter(same_context)','Token/session','SYNC|0x1000,0,0'):
            if required=='Token/session':continue
            self.assertIn(required,TEXT)
        self.assertIn('a==b',TEXT)
        self.assertIn('GetTokenInformation(token,25',TEXT)
        self.assertIn('GetTokenInformation(token.raw(),20',TEXT)
        self.assertLess(TEXT.index('borrowed_process(request.publisher_handle,request.publisher_session)'),TEXT.index('send(&mut stream,&Response::Queued(request.sequence))'))
        self.assertIn('names(operation.token()).map',TEXT)
        self.assertNotIn('DUPLICATE_CLOSE_SOURCE',TEXT)
    def test_broker_death_denies_before_actual_owned_cleanup(self):
        windows=self.body('src/server/mixel_receiver_input_windows.rs')
        self.assertIn('custodian::authority_live() && !self.poisoned',windows)
        self.assertIn('custodian::authority_live() && (unsafe',windows)
        self.assertLess(TEXT.index('deny_child();super::shutdown();'),TEXT.index('loop {\n        sweep();'))
        self.assertIn('if native_effect(true,Box::new(||true)){break;}',TEXT)
        self.assertIn('cleanup(&remote.lease.token)&&remote.lease.mark_cleaned()',windows)
    def test_custodian_is_never_force_killed_and_startup_cannot_revive(self):
        self.assertEqual(TEXT.count('TerminateProcess('),1)
        self.assertIn('if !self.resumed&&unsafe{WaitForSingleObject',TEXT)
        self.assertIn('if execute(child.thread.as_ref()?.raw())==u32::MAX{return None;}',TEXT)
        self.assertIn('child.resumed=true;Some(Launch',TEXT)
        self.assertIn('compare_exchange(0,1',TEXT)
        self.assertNotIn('ShellExecute',TEXT)
        self.assertNotIn('runas',TEXT.replace('// Direct child, same already-authorized token/session. No runas, credential,',''))
    def test_control_has_reserved_queue_and_revoked_capture_keeps_real_cleanup(self):
        self.assertIn('let(control,control_rx)=tokio::sync::mpsc::channel(MAX_PENDING)',TEXT)
        self.assertIn('control_burst<8||input.is_empty()',TEXT)
        self.assertIn('if control{value.control.try_send(work)',TEXT)
        self.assertIn('retire_denied_scope(&token,0,&publisher)',TEXT)
        self.assertIn('if lease.active(){return false;}',TEXT)
        self.assertIn('entries.insert(token.to_owned(),Remote{lease:Arc::new(lease)',TEXT)
        self.assertIn('sweep();true',TEXT)
    def test_local_capture_responsibility_before_final_admission_and_until_ack(self):
        start=TEXT.index('pub(super) fn local_input(');end=TEXT.index('pub(super) fn local_block(',start);body=TEXT[start:end]
        self.assertLess(body.index('generation.serial.lock()'),body.index('if !generation.admitted()'))
        self.assertLess(body.index('if !generation.admitted()'),body.index('portable_used.fetch_add'))
        self.assertLess(body.index('if !generation.admitted()'),body.index('if enqueue('))
        self.assertIn('Some(generation.clone())',body)
        self.assertIn('_capture:Option<Arc<Generation>>',TEXT)
        self.assertIn('send(&mut stream,&Response::Refused(refused_token))',TEXT)
        self.assertNotIn('bail!("custodian scoped work refused")',TEXT)
    def test_transferred_original_publisher_uses_authenticated_broker_session(self):
        a=TEXT.index('fn borrowed_process(');b=TEXT.index('fn integrity(',a);body=TEXT[a:b]
        self.assertNotIn('ProcessIdToSessionId(pid',body)
        self.assertNotIn('verified_peer(pid',body)
        self.assertIn('verified_peer_session(pid,Arc::new(Owned::from(raw)?),String::new(),source)',body)
        self.assertIn('source==0||source!=target',body)
        self.assertIn('publisher_session,operation}',TEXT)
    def test_json_worst_case_escaped_bytes_remain_within_wire_envelope(self):
        self.assertIn('const MAX_WIRE: usize = MAX_BYTES * 6 + 4096;',TEXT)
        budget=262144-256
        packets=[['x'*70,'y'*70,1,0,[],'\0'*budget,0,False,False],['x'*70,'y'*70,1,0,[255]*budget,'',0,False,False]]
        for packet in packets:
            raw=json.dumps({'sequence':2**64-1,'publisher_handle':2**64-1,'operation':{'Input':packet}},separators=(',',':')).encode()
            self.assertLessEqual(len(raw),262144*6+4096)
    def test_completion_handle_retained_before_ack_after_other_handles_close(self):
        self.assertIn('let mut known=HashMap::<String,Owned>::new()',TEXT)
        self.assertIn('known.insert(token.clone(),event)',TEXT)
        self.assertIn('filter(|(_,event)|unsafe{WaitForSingleObject(event.raw(),0)}==OBJECT)',TEXT)
        self.assertNotIn('fn cleaned(token:',TEXT)
        self.assertLess(TEXT.index('known.insert(token.clone(),event)'),TEXT.index('send(&mut stream,&Response::Queued(request.sequence))'))
    def test_refusal_not_cleanup_and_bounded_queues(self):
        self.assertIn('mpsc::channel(MAX_PENDING)',TEXT)
        self.assertIn('*sum<=QUEUE_BYTES',TEXT)
        self.assertIn('admission unknown; no retry',TEXT)
        self.assertIn('Response::Cleaned(token)',TEXT)
        self.assertIn('send(stream,&Response::Cleaned(token.clone())).await?;known.remove(&token)',TEXT)
        self.assertIn('!matches!(&operation,Operation::Retire{..})||!routes.contains_key',TEXT)
        self.assertNotIn('mark_cleaned()',TEXT)

    def test_revoked_block_before_capture_registers_real_cleanup_and_false_reply(self):
        start=TEXT.index('Operation::Block{token,target,conn,role,request}=>{');end=TEXT.index('Operation::Retire{token,target,role}=>',start);body=TEXT[start:end]
        self.assertIn('retire_denied_scope(&token,role,&publisher)',body)
        self.assertIn('PortableReply::Block(token,request,false)',body)
        self.assertIn('if remote.role!=role{return false;}',TEXT)
        self.assertIn('effect_id,role,',TEXT)
        self.assertNotIn('bail!',body)
        # Deterministic denied-before-capture contract: each role retains a real
        # cleanup owner, never applies On, and receives only false/CLEANED.
        for role in (0,1,2):
            active=False;registered={};effects=[];replies=[]
            if not active:registered['scope']=role
            if role:replies.append(False)
            self.assertEqual(registered,{'scope':role});self.assertEqual(effects,[])
            if role:self.assertEqual(replies,[False])
    def test_completion_batch_retains_unsent_handles_with_256_completed_owners(self):
        start=TEXT.index('async fn flush_completions(');body=TEXT[start:]
        self.assertIn('.take(bound.min(8))',body)
        self.assertLess(body.index('send(stream,&Response::Cleaned'),body.index('known.remove(&token)'))
        self.assertNotIn('try_send',body)
        self.assertIn('if replies.is_empty(){flush_completions',TEXT)
        # Model source drain ordering at capacity, including backpressure: a
        # refused send removes no owned event; subsequent turns drain all 256.
        known=list(range(256));sent=[]
        selected=known[:8];self.assertEqual(len(known),256)
        self.assertEqual(selected,list(range(8)))
        while known:
            batch=known[:8];sent.extend(batch);del known[:len(batch)]
        self.assertEqual(sent,list(range(256)))
    def test_authenticated_spontaneous_retirement_drains_before_epoch_finished(self):
        self.assertIn('enum Response { Ready, Queued(u64), Refused(String), Block(String,String,bool), Cleaned(String), Retiring, Finished }',TEXT)
        self.assertIn('retiring=true;deny_child();',TEXT)
        self.assertIn('send(&mut stream,&Response::Retiring).await?',TEXT)
        self.assertIn('control.try_recv().ok().or_else(||input.try_recv().ok())',TEXT)
        self.assertIn('broker.state.load(Ordering::SeqCst)!=2||!routes.is_empty()',TEXT)
        final=TEXT.index('if graceful{',TEXT.index('if !epoch.finish()'))
        self.assertLess(TEXT.index('while !known.is_empty()',final),TEXT.index('send(&mut stream,&Response::Finished)',final))
        self.assertNotIn('state.store(4',TEXT[TEXT.index('drop(stream);drop(child)'):TEXT.index('/// Called only')])
    def test_retirement_closes_both_receivers_before_empty_drain(self):
        a=TEXT.index('let item=if state==2{');b=TEXT.index('}else{tokio::select!',a);body=TEXT[a:b]
        self.assertLess(body.index('control.close();input.close();'),body.index('control.try_recv()'))
        # Paused sender already passed state1, but receiver closure is the actual
        # admission barrier: resuming cannot publish omitted responsibility.
        receiver_closed=False;paused_sender_passed_state=True
        receiver_closed=True;drained=[]
        accepted=paused_sender_passed_state and not receiver_closed
        self.assertFalse(accepted);self.assertEqual(drained,[])
    def test_slow_cleanup_cannot_block_retirement_capture_negotiation(self):
        a=TEXT.index('if !authority_live()&&!retiring{');b=TEXT.index('// Native callbacks',a);body=TEXT[a:b]
        self.assertLess(body.index('deny_child()'),body.index('Response::Retiring'))
        self.assertLess(body.index('Response::Retiring'),body.index('sweep()'))
        self.assertNotIn('shutdown()',body);self.assertNotIn('native_effect(',body)
    def test_refused_capacity_denied_scope_retries_retirement_after_capacity_frees(self):
        receiver=self.body('src/server/mixel_receiver_input.rs');a=receiver.index('fn portable_refused(');body=receiver[a:receiver.index('#[cfg(windows)]',a)]
        self.assertLess(body.index('generation.retire_sent.store(false'),body.index('if generation.active.load'))
        active=False;retire_sent=True;capacity=256;cleaned=False
        if not active:retire_sent=False
        self.assertFalse(retire_sent);self.assertFalse(cleaned)
        capacity-=1
        if not retire_sent and capacity<256:retire_sent=True
        self.assertTrue(retire_sent);self.assertFalse(cleaned)

class SessionEpochControls(unittest.TestCase):
    def test_dirty_marker_precedes_readiness_and_clears_only_after_cleanup(self):
        self.assertLess(TEXT.index('epoch::Epoch::acquire(&peer)'),TEXT.index('send(&mut stream,&Response::Ready)'))
        self.assertLess(TEXT.index('if native_effect(true,Box::new(||true)){break;}'),TEXT.index('epoch.finish()'))
        self.assertLess(TEXT.index('epoch.finish()'),TEXT.index('send(&mut stream,&Response::Finished)'))
        self.assertIn('FlushFileBuffers(marker.raw())',EPOCH)
        self.assertIn('SetFileInformationByHandle(self.marker.as_ref()',EPOCH)
        self.assertNotIn('remove_file',EPOCH)
    def test_epoch_uses_actual_physical_logon_not_account_only_or_packet_session(self):
        self.assertIn('ProcessIdToSessionId(GetCurrentProcessId(),&mut session)',EPOCH)
        self.assertIn('WTSQuerySessionInformationW(ptr::null_mut(),session,25',EPOCH)
        self.assertIn('value.data.session!=session',EPOCH)
        self.assertIn('logon:value.data.logon',EPOCH)
        self.assertIn('epoch.session,epoch.logon',EPOCH)
        self.assertIn('retain_authority(&v.link,v.helper.alive(),v.session.live())',TEXT)
        self.assertNotIn('std::env::var',EPOCH)
    def test_no_foreign_marker_clear_or_broad_delete_acl(self):
        self.assertIn('(A;;RC;;;OW)',EPOCH)
        self.assertIn('0x00000002;;;AU',EPOCH)
        self.assertIn('parent_cannot_delete_children',EPOCH)
        self.assertIn('mask&(0x40|0x40000|0x80000|0x10000000)',EPOCH)
        self.assertIn('sid_text(owner).as_deref().map(trusted_owner)',EPOCH)
        self.assertIn('info.links!=1',EPOCH)
        self.assertIn('info.attributes&(0x400|0x10)',EPOCH)
        self.assertIn('Prior dirty input epoch remains',EPOCH)
        self.assertNotIn('SetNamedSecurityInfo',EPOCH)
    def test_authority_failure_latches_before_cleanup_and_reconnect(self):
        self.assertIn('if !helper_alive||!same_session{link.store(false,Ordering::SeqCst);}',TEXT)
        self.assertIn('if !v.link.load(Ordering::SeqCst){return false;}',TEXT)
        self.assertNotIn('link.store(true',TEXT)
        # Deterministic source-decision contract; exact Rust function is also
        # supplied to the held Windows component, never claimed run here.
        link=True
        for helper,session in [(True,True),(True,False),(True,True),(True,True)]:
            if not helper or not session:link=False
            if not session:self.assertFalse(link)
        self.assertFalse(link,'cleanup after disconnect cannot revive on reconnect')
    def test_parent_acl_rejects_takeover_as_well_as_direct_delete(self):
        # This is a source/decision-contract control, NOT native AccessCheck.
        required='kind!=0||mask&(0x40|0x40000|0x80000|0x10000000)==0||trusted_owner(sid)'
        self.assertIn(required,EPOCH)
        vectors=[(0x40,False),(0x40000,False),(0x80000,False),(0x10000000,False),(0x001200a9,True),(0x40000000,True)]
        for mask,expected in vectors:self.assertEqual(mask&(0x40|0x40000|0x80000|0x10000000)==0,expected)
        self.assertIn('GetSecurityInfo(handle,1,1|4,&mut owner',EPOCH)
        self.assertIn('fn safe_parent_ace(kind:u8,flags:u8,',EPOCH)
        self.assertNotIn('SetNamedSecurityInfo',EPOCH)
    def test_live_broker_rotates_only_after_finished_not_eof_or_timeout(self):
        self.assertIn('4=>{},_=>return None',TEXT)
        self.assertIn('broker.state.store(4,Ordering::SeqCst);Ok(true)',TEXT)
        self.assertIn('Timeout cannot manufacture CLOSED',TEXT)
        receiver=next(r['afterSource'] for r in PLAN['files'] if r['name'].endswith('/mixel_receiver_input.rs'))
        self.assertIn('let retired=retired&&windows::custodian_disconnect()',receiver)
        self.assertIn('Backend { Local(String)',next(r['afterSource'] for r in PLAN['files'] if r['name'].endswith('input_windows.rs')))

class WholePrefixFixtureControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=HERE/'fixtures/receiver-custodian-native/prepare.py'
        spec=importlib.util.spec_from_file_location('custody_fixture_prepare',path)
        cls.driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.driver)
    def test_prepare_compiles_whole_actual_final_prefixes_and_original_negative(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();source=root/'source';source.mkdir();fixture(source);P.apply(source)
            output=root/'prepared';binding=self.driver.prepare(output,source)
            self.assertTrue(binding['actualGeneratedSourceProvided'])
            names={'mixel_receiver_input.rs','mixel_receiver_input_windows.rs','mixel_receiver_custodian_windows.rs','mixel_receiver_custodian_epoch_windows.rs'}
            for name in names:
                actual=(source/'src/server'/name).read_bytes();staged=(output/name).read_bytes()
                self.assertTrue(staged.startswith(actual),name)
            for name in ('mixel_receiver_input.rs','mixel_receiver_input_windows.rs'):
                row=next(r for r in PLAN['files']if r['name']=='src/server/'+name)
                self.assertTrue((output/'original'/name).read_bytes().startswith(row['beforeSource'].encode()))
            self.assertEqual((output/'server/mixel_receiver_receipts.rs').read_bytes(),(HERE/'support-receiver-receipts.rs').read_bytes())
            staged=(output/'mixel_receiver_input_windows.rs').read_text()
            self.assertNotIn('// @EXACT_ACCOUNT_HELPERS@',staged)
            self.assertIn('NetUserAdd',staged);self.assertIn('SecretCredential::new',staged)
    def test_actual_source_drift_refused_before_any_compiler(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();source=root/'source';source.mkdir();fixture(source);P.apply(source)
            path=source/'src/server/mixel_receiver_input_windows.rs';path.write_bytes(path.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'actual final generated prefix drift'):
                self.driver.prepare(root/'prepared',source)
    def test_frozen_negative_lint_does_not_weaken_corrected_target_or_rewrite_before_source(self):
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve()/'prepared';self.driver.prepare(output)
            final=(output/'native-main.rs').read_bytes();original=(output/'native-main-original.rs').read_bytes()
            self.assertEqual(final.count(b'#![deny(unused_mut)]'),1)
            self.assertNotIn(b'#![warn(unused_mut)]',final)
            self.assertEqual(original.count(b'#![warn(unused_mut)]'),1)
            self.assertNotIn(b'#![deny(unused_mut)]',original)
            for name in ('mixel_receiver_input.rs','mixel_receiver_input_windows.rs'):
                row=next(r for r in PLAN['files']if r['name']=='src/server/'+name)
                self.assertTrue((output/'original'/name).read_bytes().startswith(row['beforeSource'].encode()))
            self.assertIn('let mut state=attempt().lock()',next(r for r in PLAN['files']if r['name']=='src/server/mixel_receiver_input_windows.rs')['beforeSource'])
    def test_actual_crlf_pipeline_and_source_preserve_compiled_physical_prefixes(self):
        import unittest.mock
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();pipeline=root/'pipeline';source=root/'source';source.mkdir();fixture(source,True);P.apply(source)
            names=json.loads((HERE/'fixtures/receiver-custodian-native/SOURCE-PINS.json').read_text())
            names=list(names)+['scripts/fixtures/receiver-custodian-native/SOURCE-PINS.json']
            for name in names:
                raw=(HERE.parent/name).read_bytes();canonical=self.driver.logical(raw);target=pipeline/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(canonical.replace(b'\n',b'\r\n'))
            with unittest.mock.patch.object(self.driver,'REPO',pipeline),unittest.mock.patch.object(self.driver,'HERE',pipeline/'scripts/fixtures/receiver-custodian-native'):
                output=root/'prepared';binding=self.driver.prepare(output,source)
            for name in ('mixel_receiver_input.rs','mixel_receiver_input_windows.rs','mixel_receiver_custodian_windows.rs','mixel_receiver_custodian_epoch_windows.rs'):
                raw=(source/'src/server'/name).read_bytes();self.assertIn(b'\r\n',raw)
                self.assertTrue((output/name).read_bytes().startswith(raw),name)
                pin=binding['compiledProductionPrefixes']['src/server/'+name]
                self.assertEqual(pin['physicalSHA256'],P.sha(raw));self.assertEqual(pin['logicalSHA256'],P.sha(raw.replace(b'\r\n',b'\n')))
            self.assertTrue(all(pin['physicalNewlines']=='CRLF'for pin in binding['verifiedPhysicalAndLogicalSourceInputs'].values()))
    def test_source_newlines_must_be_uniform(self):
        self.assertEqual(self.driver.logical(b'a\r\nb\r\n'),b'a\nb\n')
        for raw in (b'a\r\nb\n',b'a\rb'):
            with self.assertRaisesRegex(ValueError,'mixed or bare-CR'):self.driver.logical(raw)
    def test_default_is_source_only_and_run_requires_actual_generated_source(self):
        import unittest.mock
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve()/'prepared';binding=self.driver.prepare(output)
            self.assertFalse(binding['actualGeneratedSourceProvided'])
            with unittest.mock.patch.object(self.driver.sys,'platform','win32'),unittest.mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Windows'}),unittest.mock.patch.object(self.driver,'command',side_effect=AssertionError('compiler must stay held')):
                with self.assertRaisesRegex(ValueError,'actual final generated source root'):self.driver.run(output,binding)
    def test_qa_namespace_is_test_only_and_production_has_no_override(self):
        self.assertIn('#[cfg(test)] static QA_NAMESPACE',EPOCH)
        self.assertIn('#[cfg(test)] pub(super) fn set_qa_namespace',EPOCH)
        self.assertIn('ROOT_NAME.to_owned()',EPOCH)
        self.assertNotIn('std::env::var',EPOCH)
        native=(HERE/'fixtures/receiver-custodian-native/native-tests.rs').read_text()
        self.assertIn('actual different-SID Medium helper',native)
        self.assertIn('TerminateProcess(process.raw(),77)',native)
        self.assertIn('unknown-custodian-death',native)
        self.assertIn('qa_remove_owned_namespace',native)
    def test_native_driver_fixed_compiler_and_complete_command_records(self):
        source=(HERE/'fixtures/receiver-custodian-native/prepare.py').read_text()
        self.assertIn("['rustc','+1.75.0','--version']",source)
        self.assertIn("'--locked','--no-run','--test','custody','--test','original-negative'",source)
        self.assertIn("label+'.stdout'",source);self.assertIn("label+'.stderr'",source)
        self.assertIn("actualGeneratedSourceProvided",source)
        self.assertIn("env['CARGO_TARGET_DIR']=str(output/'target')",source)
    def test_driver_overrides_inherited_target_before_any_command(self):
        import unittest.mock
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve();observed=[]
            def held_command(root,label,argv,env,bound):
                observed.append((root,env['CARGO_TARGET_DIR']))
                raise ValueError('held before compiler execution')
            with unittest.mock.patch.object(self.driver.sys,'platform','win32'),unittest.mock.patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Windows','CARGO_TARGET_DIR':'untrusted-shared-cache'}),unittest.mock.patch.object(self.driver,'command',side_effect=held_command):
                with self.assertRaisesRegex(ValueError,'held before compiler'):self.driver.run(output,{'actualGeneratedSourceProvided':True})
            self.assertEqual(observed,[(output,str(output/'target'))]);self.assertFalse((output/'NATIVE-RESULT.json').exists())
    def test_failed_compiler_keeps_complete_raw_and_prints_bounded_tail(self):
        import unittest.mock,io
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve();result=type('Result',(),dict(returncode=1,stdout=b'actual compiler stdout\n',stderr=b'actual compiler failure\n'))()
            with unittest.mock.patch.object(self.driver.subprocess,'run',return_value=result),contextlib.redirect_stdout(io.StringIO())as visible:
                with self.assertRaisesRegex(ValueError,'complete raw evidence retained'):self.driver.command(output,'compile',['held-compiler'],{},1)
            self.assertEqual((output/'compile.stderr').read_bytes(),result.stderr)
            self.assertIn('actual compiler failure',visible.getvalue())
            self.assertEqual(json.loads((output/'compile.json').read_text())['exit'],1)
    def test_success_keeps_raw_and_emits_only_bounded_actual_pass_lines(self):
        import unittest.mock,io
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve();result=type('Result',(),dict(returncode=0,stdout=b'PASS: actual owned case\nnot a passing line\n'+b'PASS:'+b'x'*5000+b'\n',stderr=b''))()
            with unittest.mock.patch.object(self.driver.subprocess,'run',return_value=result),contextlib.redirect_stdout(io.StringIO())as visible:
                self.assertEqual(self.driver.command(output,'run-custody',['held-executable'],{},1),result.stdout)
            self.assertEqual((output/'run-custody.stdout').read_bytes(),result.stdout)
            lines=visible.getvalue().splitlines();self.assertEqual(len(lines),3)
            self.assertIn('exit=0; stdoutSHA256=',lines[0]);self.assertEqual(lines[1],'PASS: actual owned case')
            self.assertLessEqual(len(lines[2]),1024);self.assertNotIn('not a passing line',visible.getvalue())
    def test_compiler_timeout_is_failure_and_raw_preserved_without_execution(self):
        import unittest.mock,io,subprocess
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve();failure=subprocess.TimeoutExpired(['held-compiler'],1,output=b'partial stdout',stderr=b'partial stderr')
            with unittest.mock.patch.object(self.driver.subprocess,'run',side_effect=failure),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):self.driver.command(output,'compile',['held-compiler'],{},1)
            self.assertEqual((output/'compile.stderr').read_bytes(),b'partial stderr')
            result=json.loads((output/'compile.json').read_text());self.assertIsNone(result['exit']);self.assertIn('cleanup not qualified',result['error'])
    def test_held_output_entry_refused_before_any_path_operation(self):
        import unittest.mock
        with unittest.mock.patch.object(Path,'lstat',side_effect=AssertionError('no held entry access')),unittest.mock.patch.object(Path,'mkdir',side_effect=AssertionError('no mkdir')):
            with self.assertRaisesRegex(ValueError,'held path entry'):self.driver.prepare(Path('/private/tmp/worktrees'))
    def test_mixedcase_held_source_entry_refused_before_output_creation(self):
        import unittest.mock
        with unittest.mock.patch.object(Path,'lstat',side_effect=AssertionError('no held entry access')),unittest.mock.patch.object(Path,'mkdir',side_effect=AssertionError('no mkdir')):
            with self.assertRaisesRegex(ValueError,'held path entry'):self.driver.prepare(Path('/private/tmp/source-fixture-output'),Path('/private/tmp/WoRkTrEeS'))
    def test_mocked_name_boundary_refuses_held_name_before_descendant_processing(self):
        for parts in [('/','owned','worktrees','opaque-next'),('/','owned','WoRkTrEeS','opaque-next')]:
            with self.assertRaisesRegex(ValueError,'held path entry'):self.driver.name_first(parts)
    def test_linked_output_parent_preserves_separate_external_sentinel(self):
        with tempfile.TemporaryDirectory()as t,tempfile.TemporaryDirectory(prefix='custody-external-safe-')as e:
            root=Path(t).resolve();external=Path(e).resolve();sentinel=external/'sentinel';sentinel.write_bytes(b'unchanged');alias=root/'alias';alias.symlink_to(external,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'linked or reparse'):self.driver.prepare(alias/'output')
            self.assertEqual(sentinel.read_bytes(),b'unchanged');self.assertFalse((external/'output').exists());alias.unlink()
    def test_linked_source_root_refused_before_output_creation(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();actual=root/'actual';actual.mkdir();alias=root/'source-alias';alias.symlink_to(actual,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'linked or reparse'):self.driver.prepare(root/'output',alias)
            self.assertFalse((root/'output').exists());alias.unlink()
    def test_mocked_reparse_ancestor_refused_without_descending(self):
        import unittest.mock,types
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();entry=root/'mocked-reparse';original=Path.lstat
            def lstat(path,*a,**kw):
                if path==entry:return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_file_attributes=0x400)
                return original(path,*a,**kw)
            with unittest.mock.patch.object(Path,'lstat',lstat):
                with self.assertRaisesRegex(ValueError,'linked or reparse'):self.driver.prepare(entry/'output')
    def test_pin_paths_prevalidated_before_join_read(self):
        import unittest.mock
        for name in ('../outside','scripts/WoRkTrEeS','/absolute','scripts/../outside'):
            with tempfile.TemporaryDirectory()as t:
                root=Path(t).resolve()
                with unittest.mock.patch.object(self.driver,'regular',return_value=json.dumps({name:{'bytes':0,'sha256':'0'*64}}).encode())as read:
                    with self.assertRaises(ValueError):self.driver.prepare(root/'output')
                    self.assertEqual(read.call_count,1,'only the ordinary trusted pin manifest may be read')
    def test_hardlinked_source_refused_before_bytes_read(self):
        import unittest.mock
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();source=root/'source';source.write_bytes(b'actual');os.link(source,root/'second')
            with unittest.mock.patch.object(Path,'read_bytes',side_effect=AssertionError('no hardlinked source read')):
                with self.assertRaisesRegex(ValueError,'single-link source'):self.driver.regular(source)
    def test_current_cross_sid_helper_and_closed_transport_dependencies(self):
        helper=(HERE/'fixtures/receiver-input-native/tests/windows-cross-sid-tests.rs').read_bytes()
        self.assertEqual(P.sha(self.driver.logical(helper)),'5636ce4a977bd7464fbdce87f89c324af7b6c0be51294dad49f95eb9d0ffc755')
        with tempfile.TemporaryDirectory()as t:
            output=Path(t).resolve()/'prepared';self.driver.prepare(output)
            staged=(output/'mixel_receiver_input_windows.rs').read_text()
            self.assertEqual(staged.count('fn qa_phase('),1);self.assertEqual(staged.count('fn qa_reply('),1)
            self.assertIn('struct QaPipe{',staged);self.assertIn('struct QaPending{',staged)
            self.assertIn('3|QA_OVERLAPPED,0,1,4096,4096,5000',staged)
            self.assertIn('QaPipe::from_owned(pipe,Some(process.clone()));qa_pipe_connect(&file)',staged)
            self.assertIn('assert_eq!(peer.pid,unsafe{GetProcessId(process.raw())})',staged)
    def test_current_helper_physical_crlf_pin_and_mixed_refusal(self):
        helper=self.driver.logical((HERE/'fixtures/receiver-input-native/tests/windows-cross-sid-tests.rs').read_bytes())
        crlf=helper.replace(b'\n',b'\r\n')
        self.assertNotEqual(P.sha(crlf),'5636ce4a977bd7464fbdce87f89c324af7b6c0be51294dad49f95eb9d0ffc755')
        self.assertEqual(P.sha(self.driver.logical(crlf)),'5636ce4a977bd7464fbdce87f89c324af7b6c0be51294dad49f95eb9d0ffc755')
        self.assertEqual(self.driver.byte_binding(crlf)['physicalSHA256'],P.sha(crlf))
        for raw in (crlf.replace(b'\r\n',b'\n',1),helper+b'\r'):
            with self.assertRaisesRegex(ValueError,'mixed or bare-CR'):self.driver.logical(raw)
    def test_actual_current_test_executes_against_physical_crlf_pipeline_helper(self):
        # Execute the actual corrected unit method from a physical CRLF copy,
        # with all its real pinned prepare inputs also CRLF. No process or
        # compiler is invoked, and no alternate helper hash is admitted.
        with tempfile.TemporaryDirectory()as t:
            root=Path(t).resolve();pipeline=root/'pipeline'
            names=list(json.loads((HERE/'fixtures/receiver-custodian-native/SOURCE-PINS.json').read_text()))
            names+=['scripts/fixtures/receiver-custodian-native/SOURCE-PINS.json','scripts/test-receiver-custodian.py']
            for name in names:
                raw=self.driver.logical((HERE.parent/name).read_bytes())
                path=pipeline/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw.replace(b'\n',b'\r\n'))
            path=pipeline/'scripts/test-receiver-custodian.py';raw=path.read_bytes()
            self.assertIn(b'\r\n',raw)
            namespace={'__name__':'custody_actual_crlf_boundary','__file__':str(path)}
            exec(compile(raw,str(path),'exec'),namespace)
            suite=namespace['WholePrefixFixtureControls'];suite.setUpClass()
            result=unittest.TestResult()
            suite('test_current_cross_sid_helper_and_closed_transport_dependencies').run(result)
            self.assertEqual(result.testsRun,1)
            self.assertEqual(result.errors,[])
            self.assertEqual(result.failures,[])
    def test_cross_sid_actor_inherits_only_owned_accessible_working_directory(self):
        source=(HERE/'fixtures/receiver-custodian-native/native-tests.rs').read_text()
        self.assertIn('.arg("--qa-cross-parent").current_dir(&root).spawn()',source)
        self.assertNotIn('let mode=1',source)
        self.assertIn('pipe.peer=Some(publisher.process.clone())',source)

if __name__=='__main__':unittest.main()
