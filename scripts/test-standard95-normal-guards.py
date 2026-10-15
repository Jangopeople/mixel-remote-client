#!/usr/bin/env python3
"""Synthetic file/source controls only; never provider, account, app or native95 proof."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
import standard95_guard as G

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

A = load('normal_guard_adapter', 'prepare-standard-foreground95.py')
C = load('normal_capture', 'capture-standard95-state.py')
T = load('normal_adapter_controls', 'test-standard-foreground95-adapter.py')
W = load('normal_workflow', 'standard_foreground95_workflow_projection.py')
DRIVER = '9001338303432e41ea2b5af0b34ac878ab3f6ebc06eb9ab1796b18a2fe8a4018'


def encode(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def synthetic_facts():
    """Unit-only clones, never retained as a Root decision or provider original."""
    values = {}
    def run(role, number, workflow, event):
        values[role] = dict(id=number, run_attempt=1, head_sha=G.SOURCE, status='completed', conclusion='success',
            event=event, path='.github/workflows/' + workflow, repository={'full_name':G.REPOSITORY},
            html_url='https://github.com/' + G.REPOSITORY + '/actions/runs/' + str(number))
    run('producer',G.RUN,'build.yml','workflow_dispatch')
    run('preflight',G.PREFLIGHT,'preflight.yml','pull_request')
    run('comparison',G.COMPARISON,'windows-branding-idempotence.yml','pull_request')
    serial = 1
    for role, names, skipped, number in [('producerJobs',G.PRODUCER,G.PRODUCER_SKIP,G.RUN),
            ('preflightJobs',G.PREF,G.PREF_SKIP,G.PREFLIGHT),('comparisonJobs',G.COMPARE,set(),G.COMPARISON)]:
        members=[]
        for name in sorted(names):
            job_id = G.WINDOWS if name == 'windows' else serial; serial += 1
            steps=[] if name in skipped else [dict(number=1,name='Unit-only setup',status='completed',conclusion='success')]
            if name == 'windows':
                mandatory = ['Prove file-dialog lifecycle before complete Windows source checks',
                    'Preflight generated support source and reporter','Build (cargo + flutter via build.py)',
                    'Require the complete generated Flutter app to analyze without errors or warnings',
                    'Test compiled attended support guard','Exercise the signed portable customer download',
                    'Exercise the signed QuickSupport download without an invite',
                    'Require the original Windows source preflight before customer artifact upload']
                steps += [dict(number=n+2,name=step,status='completed',conclusion='success') for n,step in enumerate(mandatory)]
                steps += [dict(number=10,name='Retain verified signed Windows installers for unpublished diagnosis',
                               status='completed',conclusion='skipped')]
            members.append(dict(id=job_id,run_id=number,run_attempt=1,head_sha=G.SOURCE,name=name,
                status='completed',conclusion='skipped' if name in skipped else 'success',steps=steps))
        values[role]=dict(total_count=len(members),jobs=members)
    return values


def install_facts(root, live, values=None):
    values = values or synthetic_facts()
    gate = T.gate(); gate['references']={}; gate['original_evidence_sha256']={}
    for role in G.ROLES:
        raw=encode(values[role]); path=root/(G.EVIDENCE+role+'.json');path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw);(live/(role+'.json')).write_bytes(raw)
        gate['references'][role]={'path':G.EVIDENCE+role+'.json','bytes':len(raw),'sha256':G.sha(raw)}
        gate['original_evidence_sha256'][role]=G.sha(raw)
    return gate


class NormalGuards(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mixel-normal95-source-only-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.repo=self.root/'repo';self.repo.mkdir()
        self.live=self.root/'live';self.live.mkdir()
        self.gate=install_facts(self.repo,self.live)
        self.expected=G.originals(self.repo,self.gate)

    def test_full_synthetic_whole_success_and_conditional_skips(self):
        actual=G.facts(synthetic_facts(),self.gate)
        self.assertEqual(actual,self.expected)
        self.assertEqual(actual['producer']['windows'],G.WINDOWS)
        self.assertEqual(set(actual['comparison']),G.COMPARE)
        self.assertEqual(len(G.live_state(self.live,self.gate,self.expected)),6)
        self.assertEqual(A.validate_gate(self.gate)['source'],G.SOURCE)

    def test_all_four_producer_platforms_must_pass_no_partial_or_failure_relabel(self):
        for name in G.PRODUCER-G.PRODUCER_SKIP:
            for conclusion in ['failure','cancelled','timed_out','skipped',None]:
                data=synthetic_facts();job=next(x for x in data['producerJobs']['jobs'] if x['name']==name)
                job['conclusion']=conclusion
                with self.subTest(name=name,conclusion=conclusion),self.assertRaises(ValueError):G.facts(data,self.gate)
        for status,conclusion in [('in_progress',None),('completed','failure')]:
            data=synthetic_facts();data['producer'].update(status=status,conclusion=conclusion)
            with self.assertRaises(ValueError):G.facts(data,self.gate)

    def test_all_required_preflight_and_both_actual_comparisons_must_pass(self):
        for role,names in [('preflightJobs',G.PREF-G.PREF_SKIP),('comparisonJobs',G.COMPARE)]:
            for name in names:
                data=synthetic_facts();next(x for x in data[role]['jobs'] if x['name']==name)['conclusion']='failure'
                with self.subTest(role=role,name=name),self.assertRaises(ValueError):G.facts(data,self.gate)
        data=synthetic_facts();data['preflight']['conclusion']='failure'
        with self.assertRaises(ValueError):G.facts(data,self.gate)

    def test_exact_source_attempt_ids_full_inventory_and_unique_steps(self):
        edits=[('head_sha','0'*40),('run_attempt',2),('run_attempt',True),('id',True)]
        for role in ['producer','preflight','comparison']:
            for key,value in edits:
                data=synthetic_facts();data[role][key]=value
                with self.subTest(role=role,key=key),self.assertRaises(ValueError):G.facts(data,self.gate)
        for change in ['head','attempt','duplicate-name','duplicate-id','missing','extra','count','step-failure','step-duplicate']:
            data=synthetic_facts();inv=data['producerJobs'];job=inv['jobs'][0]
            if change=='head':job['head_sha']='f'*40
            elif change=='attempt':job['run_attempt']=2
            elif change=='duplicate-name':inv['jobs'][1]['name']=job['name']
            elif change=='duplicate-id':inv['jobs'][1]['id']=job['id']
            elif change=='missing':inv['jobs'].pop();inv['total_count']-=1
            elif change=='extra':inv['jobs'].append(copy.deepcopy(job));inv['total_count']+=1
            elif change=='count':inv['total_count']=True
            elif change=='step-failure':job['steps'][0]['conclusion']='failure'
            else:job['steps'].append(copy.deepcopy(job['steps'][0]))
            with self.subTest(change=change),self.assertRaises(ValueError):G.facts(data,self.gate)
        data=synthetic_facts();next(x for x in data['producerJobs']['jobs'] if x['name']=='windows')['id']+=1
        with self.assertRaises(ValueError):G.facts(data,self.gate)

    def test_every_named_windows_native_and_source_gate_must_actually_pass(self):
        sample=synthetic_facts();windows=next(x for x in sample['producerJobs']['jobs'] if x['name']=='windows')
        for target in windows['steps'][1:-1]:
            data=copy.deepcopy(sample);job=next(x for x in data['producerJobs']['jobs'] if x['name']=='windows')
            next(x for x in job['steps'] if x['name']==target['name'])['conclusion']='skipped'
            with self.subTest(name=target['name']),self.assertRaises(ValueError):G.facts(data,self.gate)

    def test_raw_original_hash_size_path_and_nonfinite_json_refusals(self):
        for change in ['path','bytes','sha','summary']:
            gate=copy.deepcopy(self.gate)
            if change=='path':gate['references']['producer']['path']='scripts/elsewhere.json'
            elif change=='bytes':gate['references']['producer']['bytes']+=1
            elif change=='sha':gate['references']['producer']['sha256']='f'*64
            else:gate['original_evidence_sha256']['producer']='f'*64
            with self.subTest(change=change),self.assertRaises(ValueError):G.originals(self.repo,gate)
        for raw in ['{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}','{"a":-Infinity}']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):G.unique_json(raw)
        raw=(self.repo/(G.EVIDENCE+'producer.json')).read_bytes()
        (self.repo/(G.EVIDENCE+'producer.json')).write_bytes(raw.replace(b'\n',b'\r\n'))
        with self.assertRaises(ValueError):G.originals(self.repo,self.gate)

    def test_live_raw_bytes_are_preserved_and_identity_change_refuses(self):
        path=self.live/'producer.json';path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
        hashes=G.live_state(self.live,self.gate,self.expected)
        self.assertEqual(hashes['producer'],G.sha(path.read_bytes()))
        self.assertNotEqual(hashes['producer'],self.gate['original_evidence_sha256']['producer'])
        data=G.unique_json(path.read_bytes());data['run_attempt']=2;path.write_bytes(encode(data))
        with self.assertRaises(ValueError):G.live_state(self.live,self.gate,self.expected)

    def test_owned_file_read_single_link_canonical_size_and_change_guards(self):
        path=self.root/'owned';path.write_bytes(b'owned')
        self.assertEqual(G.owned_read(path,5),b'owned')
        with self.assertRaises(ValueError):G.owned_read(path,4)
        link=self.root/'hard';os.link(path,link)
        with self.assertRaises(ValueError):G.owned_read(path,100)
        link.unlink();sym=self.root/'sym';sym.symlink_to(path)
        with self.assertRaises(ValueError):G.owned_read(sym,100)
        with self.assertRaises(ValueError):G.owned_read(Path('relative'),100)

    def test_windows_shaped_owned_read_has_no_unix_uid_fallback(self):
        path=self.root/'owned';path.write_bytes(b'owned')
        winos=types.SimpleNamespace(name='nt',open=os.open,fdopen=os.fdopen,fstat=os.fstat,O_RDONLY=os.O_RDONLY,O_NOFOLLOW=getattr(os,'O_NOFOLLOW',0))
        with patch.object(G,'os',winos),patch.object(G,'windows_owner_matches_current_sid',return_value=True) as owner:
            self.assertEqual(G.owned_read(path,5),b'owned');owner.assert_called_once()
        with patch.object(G,'os',winos),patch.object(G,'windows_owner_matches_current_sid',return_value=False),self.assertRaises(ValueError):G.owned_read(path,5)
        self.assertFalse(hasattr(winos,'getuid'))

    def test_token_user_or_actual_default_owner_only(self):
        equal=lambda a,b:a==b
        self.assertTrue(G.owner_matches_token_sids('user','user','default-owner',equal))
        self.assertTrue(G.owner_matches_token_sids('default-owner','user','default-owner',equal))
        self.assertFalse(G.owner_matches_token_sids('foreign','user','default-owner',equal))
        with self.assertRaises(ValueError):G.owner_matches_token_sids('', 'user','default-owner',equal)

    def test_actual_prepare_uses_real_original_live_and_projection_validators(self):
        for name,text in A.read_sources(ROOT).items():
            path=self.repo/'scripts'/name;path.parent.mkdir(exist_ok=True);path.write_bytes(text.encode())
        gate_path=self.repo/G.ROOT_GATE;gate_path.write_bytes(encode(self.gate))
        collected=self.root/'collected.json';collected.write_bytes(encode(T.collection()))
        output=self.root/('mixel-standard-foreground95-'+'a'*32)
        with patch.dict(os.environ,{'RUNNER_TEMP':str(self.root)}),patch.object(A,'verify_harness',return_value={'sourceOnlySyntheticHarness':True}):
            record=A.prepare(self.repo,output,gate_path,G.sha(gate_path.read_bytes()),collected,'a'*40,'b'*40,self.live)
        self.assertEqual(G.sha((output/'test-standard-foreground95.generated.ps1').read_bytes()),DRIVER)
        self.assertIs(record['native_runtime_qualified'],False)
        self.assertIs(record['standard_outer_qualified'],False)
        self.assertEqual(record['fresh_original_sha256'],G.live_state(self.live,self.gate,self.expected))

    def test_missing_gate_precludes_provider_or_output(self):
        output=self.root/('mixel-normal95-live-before-'+'a'*32)
        with patch.object(G,'hosted_context',return_value=self.root),patch.object(C,'api',side_effect=AssertionError('No provider before seal')) as api:
            with self.assertRaisesRegex(ValueError,'Root gate absent'):
                C.capture(self.repo,self.repo/G.ROOT_GATE,'a'*64,'b'*40,'c'*40,output,'before')
            api.assert_not_called();self.assertFalse(output.exists())

    def test_capture_retains_six_exact_raw_objects_before_and_after(self):
        gate_path=self.repo/G.ROOT_GATE;gate_path.write_bytes(encode(self.gate));data=synthetic_facts()
        reverse={value:key for key,value in G.ENDPOINTS.items()}
        def api(endpoint):return encode(data[reverse[endpoint]])
        with patch.object(G,'hosted_context',return_value=self.root),patch.object(C.A,'verify_harness',return_value={'sourceOnlySyntheticHarness':True}),patch.object(C,'api',side_effect=api):
            for stage in ('before','after'):
                output=self.root/('mixel-normal95-live-'+stage+'-'+'b'*32)
                record=C.capture(self.repo,gate_path,G.sha(gate_path.read_bytes()),'c'*40,'d'*40,output,stage)
                self.assertEqual(record['stage'],stage);self.assertIs(record['native95Qualified'],False)
                for role in G.ROLES:self.assertEqual((output/(role+'.json')).read_bytes(),encode(data[role]))

    def test_api_is_bounded_readonly_exact_endpoint_and_strict_json(self):
        response=types.SimpleNamespace(returncode=0,stdout=b'{"ok":true}',stderr=b'')
        with patch.object(C.subprocess,'run',return_value=response) as run:
            self.assertEqual(C.api(G.ENDPOINTS['producer']),response.stdout)
            self.assertEqual(run.call_args.args[0],['gh','api','--hostname','github.com','repos/'+G.REPOSITORY+'/'+G.ENDPOINTS['producer']])
        for raw in [b'',b'{"a":NaN}',b'{"a":1,"a":2}']:
            with patch.object(C.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout=raw)),self.assertRaises(ValueError):C.api(G.ENDPOINTS['producer'])

    def test_unhosted_restore_refuses_before_git_or_mutation(self):
        with patch.dict(os.environ,{},clear=True),patch.object(G.subprocess,'check_output',side_effect=AssertionError('No Git before actual hosted scope')) as git:
            with self.assertRaisesRegex(ValueError,'hosted runtime context'):G.restore_git_bytes(self.repo,self.repo/'scripts')
            git.assert_not_called()

    def test_hosted_context_requires_exact_workflow_scope_clean_git_and_owned_temp(self):
        env=dict(GITHUB_SHA='a'*40,GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_JOB='runtime',
            GITHUB_REPOSITORY=G.REPOSITORY,GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_RUN_ATTEMPT='1',GITHUB_WORKFLOW_SHA='a'*40,
            MIXEL_STANDARD95_SOURCE_SCOPE=G.SCOPE,WINDOWS_RELEASE_GATE='true',RUNTIME_RUN_ID=str(G.RUN),PUBLISH_REQUESTED='false',
            GITHUB_REF='refs/heads/codex/normal95',GITHUB_WORKFLOW_REF=G.REPOSITORY+'/.github/workflows/build.yml@refs/heads/codex/normal95',
            GITHUB_WORKSPACE=str(self.repo),RUNNER_TEMP=str(self.root))
        def git(argv,**kwargs):return b'a'*40+b'\n' if argv[-2:]==['rev-parse','HEAD'] else b''
        fake=types.SimpleNamespace(name='nt',environ=env)
        with patch.object(G,'os',fake),patch.object(G,'windows_directory_owner_matches_current_token',return_value=True),patch.object(G.subprocess,'check_output',side_effect=git):
            self.assertEqual(G.hosted_context(self.repo,self.repo/'scripts'),self.root)
            for key,value in [('GITHUB_JOB','windows'),('GITHUB_RUN_ATTEMPT','2'),('PUBLISH_REQUESTED','true'),('WINDOWS_RELEASE_GATE','false'),('GITHUB_WORKFLOW_REF','other')]:
                before=env[key];env[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):G.hosted_context(self.repo,self.repo/'scripts')
                env[key]=before
            with patch.object(G,'windows_directory_owner_matches_current_token',return_value=False),self.assertRaises(ValueError):G.hosted_context(self.repo,self.repo/'scripts')
            with patch.object(G.subprocess,'check_output',side_effect=[b'a'*40+b'\n',b' M scripts/dirty\n']),self.assertRaises(ValueError):G.hosted_context(self.repo,self.repo/'scripts')

    def test_git_byte_restore_has_actual_crlf_control_no_cli_scope_escape(self):
        git=shutil.which('git');self.assertIsNotNone(git)
        scripts=self.repo/'scripts';scripts.mkdir(exist_ok=True);path=scripts/'source.py';path.write_bytes(b'line1\nline2\n')
        gate=self.repo/G.ROOT_GATE;gate.write_bytes(b'{"unitOnly":true}\n')
        workflow=self.repo/'.github/workflows/build.yml';workflow.parent.mkdir(parents=True);workflow.write_bytes(b'on: workflow_dispatch\n')
        def run(*argv):return subprocess.run([git,'-C',str(self.repo),*argv],check=True,capture_output=True,timeout=30)
        run('init','-q');run('config','core.autocrlf','true');run('config','core.safecrlf','false')
        run('add','.');run('-c','user.name=SourceControl','-c','user.email=source-control@example.invalid','commit','-qm','unit-only fixture')
        originals={p:p.read_bytes() for p in [path,gate,workflow]}
        for p in originals:p.unlink() # owned unit fixture: simulate a fresh converted checkout
        run('checkout-index','--index','--force','--all')
        for p,raw in originals.items():self.assertEqual(p.read_bytes(),raw.replace(b'\n',b'\r\n'))
        self.assertEqual(run('status','--porcelain','--untracked-files=no').stdout,b'')
        head_tree=run('rev-parse','HEAD','HEAD^{tree}').stdout
        full_index=run('ls-files','--stage','-z').stdout
        with patch.object(G,'hosted_context',return_value=self.root):G.restore_git_bytes(self.repo,scripts)
        for p,raw in originals.items():self.assertEqual(p.read_bytes(),raw)
        self.assertEqual(run('status','--porcelain','--untracked-files=no').stdout,b'')
        self.assertEqual(run('rev-parse','HEAD','HEAD^{tree}').stdout,head_tree)
        self.assertEqual(run('ls-files','--stage','-z').stdout,full_index)
        self.assertNotIn('--skip', (HERE/'capture-standard95-state.py').read_text())
        self.assertNotIn('test-only-hosted', (HERE/'capture-standard95-state.py').read_text())

    def test_git_stat_reset_refuses_real_changes_without_staging_or_overwriting(self):
        git=shutil.which('git');self.assertIsNotNone(git)
        scripts=self.repo/'scripts';source=scripts/'source.py';source.write_bytes(b'line1\nline2\n')
        workflow=self.repo/'.github/workflows/build.yml';workflow.parent.mkdir(parents=True);workflow.write_bytes(b'on: workflow_dispatch\n')
        actual_run=subprocess.run
        def run(*argv):return actual_run([git,'-C',str(self.repo),*argv],check=True,capture_output=True,timeout=30)
        run('init','-q');run('config','core.autocrlf','true');run('config','core.safecrlf','false')
        run('add','.');run('-c','user.name=SourceControl','-c','user.email=source-control@example.invalid','commit','-qm','unit-only fixture')
        run('checkout-index','--index','--force','--all')
        self.assertEqual(run('status','--porcelain','--untracked-files=no').stdout,b'')
        head_tree=run('rev-parse','HEAD','HEAD^{tree}').stdout;full_index=run('ls-files','--stage','-z').stdout
        source_sha=head_tree.splitlines()[0].decode()
        env=dict(GITHUB_SHA=source_sha,GITHUB_ACTIONS='true',RUNNER_ENVIRONMENT='github-hosted',GITHUB_JOB='runtime',
            GITHUB_REPOSITORY=G.REPOSITORY,GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_RUN_ATTEMPT='1',GITHUB_WORKFLOW_SHA=source_sha,
            MIXEL_STANDARD95_SOURCE_SCOPE=G.SCOPE,WINDOWS_RELEASE_GATE='true',RUNTIME_RUN_ID=str(G.RUN),PUBLISH_REQUESTED='false',
            GITHUB_REF='refs/heads/codex/normal95',GITHUB_WORKFLOW_REF=G.REPOSITORY+'/.github/workflows/build.yml@refs/heads/codex/normal95',
            GITHUB_WORKSPACE=str(self.repo),RUNNER_TEMP=str(self.root))
        modified=source.read_bytes()+b'# actual preexisting edit\n';source.write_bytes(modified)
        with patch.object(G,'os',types.SimpleNamespace(name='nt',environ=env)),\
                patch.object(G,'windows_directory_owner_matches_current_token',return_value=True),\
                patch.object(G.shutil,'copyfile',side_effect=AssertionError('No copy before clean checkout')) as copy_file:
            with self.assertRaisesRegex(ValueError,'clean exact checkout'):G.restore_git_bytes(self.repo,scripts)
            copy_file.assert_not_called()
        self.assertEqual(source.read_bytes(),modified)
        self.assertEqual(run('ls-files','--stage','-z').stdout,full_index)
        self.assertEqual(run('rev-parse','HEAD','HEAD^{tree}').stdout,head_tree)
        self.assertEqual(run('status','--porcelain','--untracked-files=no').stdout,b' M scripts/source.py\n')
        run('checkout-index','--index','--force','--all')
        self.assertEqual(run('status','--porcelain','--untracked-files=no').stdout,b'')
        injections=[]
        def inject_real_edit(argv,**kwargs):
            if argv[3:]==['update-index','-z','--index-info']:
                modified=source.read_bytes()+b'# actual edit before stat reset\n';source.write_bytes(modified);injections.append(modified)
            return actual_run(argv,**kwargs)
        with patch.object(G,'hosted_context',return_value=self.root),patch.object(G.subprocess,'run',side_effect=inject_real_edit):
            with self.assertRaisesRegex(ValueError,'clean exact checkout'):G.restore_git_bytes(self.repo,scripts)
        self.assertEqual(len(injections),1);self.assertEqual(source.read_bytes(),injections[0])
        self.assertEqual(run('ls-files','--stage','-z').stdout,full_index)
        self.assertEqual(run('rev-parse','HEAD','HEAD^{tree}').stdout,head_tree)
        self.assertEqual(run('status','--porcelain','--untracked-files=no').stdout,b' M scripts/source.py\n')

    def test_runtime_bootstrap_order_strict_inverse_both_oses_and_native_conservation(self):
        text=(ROOT/'.github/workflows/build.yml').read_text();runtime=text[text.index('\n  runtime:\n'):text.index('\n  runtime-macos:\n')]
        order=['Restore exact normal QA Git bytes before source admission','Normal Root gate absent;',
            'Admit normal whole-green source before collection or application work','Qualify exact ordinary Windows desktop fixture',
            'Collect exact successful customer Windows release','prepare-standard-foreground95.py',
            'Retain fresh normal same-HEAD facts after original cleanup']
        for before,after in zip(order,order[1:]):self.assertLess(runtime.index(before),runtime.index(after))
        self.assertIn('os: [windows-2025, windows-2022]',runtime)
        self.assertIn('timeout-minutes: 15',runtime)
        self.assertIn("always() && inputs.runtime_windows_release_gate && steps.normal_before.outcome == 'success'",runtime)
        self.assertEqual(G.sha(W.inverse_standard_foreground95_runtime_checks(text,'build.yml').encode()),W.APP_BUILD_WORKFLOW_SHA256)
        sources=A.read_sources(ROOT);candidate,_=A.projected(sources[A.DRIVER],sources[A.OBSERVER])
        self.assertEqual(G.sha(candidate.encode()),DRIVER)
        self.assertEqual(G.sha((HERE/'collect-windows-release-runtime.py').read_bytes()),'5bd875229f3311ff6c9bb87d35be8422fe380bd60324104784626d1a6f64429a')


if __name__=='__main__':unittest.main(verbosity=2)
