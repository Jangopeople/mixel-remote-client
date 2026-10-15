#!/usr/bin/env python3
"""Offline private admission controls. Synthetic metadata never qualifies native QA."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

HERE=Path(__file__).resolve().parent
REPOSITORY=next(parent for parent in (HERE,*HERE.parents) if (parent/'scripts/test-support-launch-windows.ps1').is_file())
spec=importlib.util.spec_from_file_location('private95',HERE/'prepare-private-standard-foreground95.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
A=P.load_pinned('prepare-standard-foreground95.py')
C=P.load_pinned('collect-windows-release-runtime.py')
OBSERVATIONS=HERE/'qa/current77-private-standard95-evidence'
SHAPES=HERE/'qa/current77-private-standard95-source-fixtures'


def encode(value):return (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
def digest(value):return hashlib.sha256(value).hexdigest()


class PrivateAdmission(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mixel-private95-offline-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.repository=self.root/'repo';self.repository.mkdir()
        (self.repository/'scripts').mkdir()
        self.collection=self.repository/'release-runtime-collection';self.collection.mkdir()
        (self.repository/P.EVIDENCE_PREFIX).mkdir(parents=True)
        for name in A.PINS:
            (self.repository/'scripts'/name).write_bytes((REPOSITORY/'scripts'/name).read_bytes())
        self.runner=self.root/'runner';self.runner.mkdir();self.output=self.runner/('mixel-standard-foreground95-'+'a'*32)
        self.raw={};self.data={};self.paths={}
        for key,name in [('producerJobs','38774820528-20261015T174605Z.json'),
                         ('preflightJobs','38774157443-20261015T174606Z.json'),
                         ('comparisonJobs','38774157532-20261015T174605Z.json')]:
            value=json.loads((SHAPES/name).read_bytes())
            for job in value['jobs']:
                job['status']='completed'
                job['conclusion']='skipped' if job['name'] in (P.PRODUCER_SKIP|P.PREF_SKIP) else 'failure' if job['id']==P.ARM else 'success'
                for step in job.get('steps',[]):
                    if job['id']==P.WINDOWS:
                        if step['number'] in P.WINDOWS_STEPS:
                            step['status']='completed';step['conclusion']='success'
                        elif step['number'] in P.WINDOWS_DIAGNOSTIC_SKIP:
                            step['status']='completed';step['conclusion']='skipped'
                        elif step['status']!='completed':
                            # Unit-only nonmandatory post/cache hooks remain
                            # skipped, rather than manufacturing success.
                            step['status']='completed';step['conclusion']='skipped'
                    else:
                        step['status']='completed';step['conclusion']='failure' if job['id']==P.ARM and step['number']==25 else 'success'
            if key=='producerJobs':
                value['jobs'].append({'id':90001,'name':'upload','run_id':P.RUN,'run_attempt':1,'head_sha':P.SOURCE,
                                      'status':'completed','conclusion':'skipped','steps':[]})
                value['total_count']=len(value['jobs'])
            self.data[key]=value
        for key,run,conclusion,path,event in [('producer',P.RUN,'failure','build.yml','workflow_dispatch'),
                ('preflight',P.PREFLIGHT,'success','preflight.yml','pull_request'),
                ('comparison',P.COMPARISON,'success','windows-branding-idempotence.yml','pull_request')]:
            self.data[key]={'id':run,'head_sha':P.SOURCE,'run_attempt':1,'repository':{'full_name':'Jangopeople/mixel-remote-client'},
                'status':'completed','conclusion':conclusion,'path':'.github/workflows/'+path,'event':event,
                'html_url':'https://github.com/Jangopeople/mixel-remote-client/actions/runs/'+str(run)}
        self.data['windowsJob']=copy.deepcopy(next(j for j in self.data['producerJobs']['jobs'] if j['id']==P.WINDOWS))
        self.data['armJob']=copy.deepcopy(next(j for j in self.data['producerJobs']['jobs'] if j['id']==P.ARM))
        self.data['commit']={'sha':P.SOURCE,'commit':{'tree':{'sha':P.TREE}}}
        self.raw['request']=(OBSERVATIONS/'request.json').read_bytes()
        self.raw['sourceInputs']=(OBSERVATIONS/'sourceInputs.json').read_bytes()
        self.data['request']=json.loads(self.raw['request']);self.data['sourceInputs']=json.loads(self.raw['sourceInputs'])
        self.raw['windowsJobLog']=('SYNTHETIC OFFLINE CHECKOUT '+P.SOURCE+'\n').encode()
        self.raw['armJobLog']=(OBSERVATIONS/'armJobLog.log').read_bytes()
        self.data['checkoutEvidence']={'schema':'root-observed-current77-windows-checkout-v1','source':P.SOURCE,
            'tree':P.TREE,'job':P.WINDOWS,'rootOriginalCheckoutVerified':True,'originalLogSha256':digest(self.raw['windowsJobLog']),
            'lineNumber':1,'checkoutSourceLine':self.raw['windowsJobLog'].decode().strip()}
        mappings={};artifacts={}
        payload=b'SYNTHETIC ONLY - not PE, not signed, never executed'
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w') as z:z.writestr('Mixel-Remote.exe',payload)
        roles={
            'windows':{'mixel-remote-1.4.6-x86_64.exe':payload,'Mixel-Remote-QS.exe':payload,'Mixel-Remote-Support-Windows.exe':payload},
            'windows-msix-payload':{'Mixel-Remote-Store-Payload.zip':inner.getvalue(),
                                  'Mixel-Remote-Store-Payload.sha256':digest(inner.getvalue()).encode()}}
        for index,(name,files) in enumerate(roles.items()):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                for member,value in files.items():z.writestr(member,value)
            key='windowsArchive' if name=='windows' else 'payloadArchive'
            self.raw[key]=stream.getvalue()
            artifact={'id':90010+index,'name':name,'expired':False,'digest':'sha256:'+digest(self.raw[key]),
                'size_in_bytes':len(self.raw[key]),'workflow_run':{'id':P.RUN,'head_sha':P.SOURCE}}
            self.data['windowsArtifact' if name=='windows' else 'payloadArtifact']=artifact
            artifacts[name]={'id':artifact['id'],'digest':artifact['digest']}
            mappings[name]={'artifact_id':artifact['id'],'artifact_digest':artifact['digest'],'actual_archive_digest':artifact['digest'],
                           'files_sha256':{'artifacts/'+name+'/'+member:digest(value) for member,value in files.items()}}
        self.data['provenance']={'evidence_kind':'debugger-free-windows-customer-release-gate-inputs',
            'repository':'Jangopeople/mixel-remote-client','build_run_id':P.RUN,'build_url':self.data['producer']['html_url'],
            'source_commit':P.SOURCE,'application_source_baseline':P.SOURCE,'build_status':'completed','build_conclusion':'failure',
            'producer_windows_job_id':P.WINDOWS,'producer_windows_job_conclusion':'success','publication':False,'collected_artifacts':mappings}
        self.data['rootCollectorProvenance']=copy.deepcopy(self.data['provenance'])
        required=['scripts/prepare-private-standard-foreground95.py','scripts/test-private-standard-foreground95.py',
            'scripts/prepare-standard-foreground95.py','scripts/test-standard-foreground95-adapter.py',
            'scripts/collect-windows-release-runtime.py','scripts/capture-private-standard95-state.py',
            'scripts/private_standard95_workflow_projection.py','.github/workflows/build.yml']
        self.binding={'schema':'root-current77-private-standard95-decision-v1','scope':P.SCOPE,'authorizedBy':'Root',
            'privateDiagnosticGo':True,'root_whole_green_verified':False,'normal_qualified':False,'release_qualified':False,
            'publication':False,'source':P.SOURCE,'tree':P.TREE,'producerRun':P.RUN,'attempt':1,'windowsJob':P.WINDOWS,
            'failedArmJob':P.ARM,'preflightRun':P.PREFLIGHT,'comparisonRun':P.COMPARISON,'artifacts':artifacts,
            'reviewed_harness_blobs':{name:'a'*64 for name in required}}
        self.binding_path=self.root/'root-decision.json'
        self.live_state=self.root/'fresh-originals';self.live_state.mkdir()
        self.save()

    def save(self):
        refs={}
        for name in P.REFERENCES:
            if name in self.data and name not in ('request','sourceInputs'):
                self.raw[name]=(json.dumps(self.data[name],indent=2)+'\n').encode() if name in ('provenance','rootCollectorProvenance') else encode(self.data[name])
            logical=P.REFERENCE_PATHS[name]
            path=self.collection/logical[len('collector/'):] if logical.startswith('collector/') else self.repository/logical
            path.write_bytes(self.raw[name]);self.paths[name]=path
            refs[name]={'path':logical,'bytes':len(self.raw[name]),'sha256':digest(self.raw[name])}
        self.binding['references']=refs
        lf=self.raw['rootCollectorProvenance']
        self.binding['derived_provenance_serializations']={k:{'bytes':len(v),'sha256':digest(v)}
            for k,v in {'lf':lf,'crlf':lf.replace(b'\n',b'\r\n')}.items()}
        self.binding_path.write_bytes(encode(self.binding));self.binding_digest=digest(self.binding_path.read_bytes())
        for name in ('producer','windowsJob','armJob','preflight','comparison'):
            (self.live_state/(name+'.json')).write_bytes(encode(self.data[name]))

    def qualify(self):return P.qualify(self.binding_path,self.binding_digest,self.repository,self.collection)

    def mutate_job(self,name,conclusion):
        job=next(j for j in self.data['producerJobs']['jobs'] if j['name']==name)
        job['conclusion']=conclusion
        if name=='windows':self.data['windowsJob']=copy.deepcopy(job)
        if job['id']==P.ARM:self.data['armJob']=copy.deepcopy(job)

    def test_synthetic_positive_preserves_actual_failure_labels(self):
        binding,data,raw,artifacts=self.qualify()
        self.assertEqual(data['producer']['conclusion'],'failure')
        self.assertEqual(data['armJob']['conclusion'],'failure')
        self.assertFalse(binding['root_whole_green_verified']);self.assertFalse(binding['normal_qualified'])
        self.assertEqual(set(artifacts),{'windows','windows-msix-payload'})

    def test_missing_root_decision_refuses_without_helpers_or_output(self):
        del self.binding['privateDiagnosticGo'];self.save()
        with patch.object(P,'load_pinned',side_effect=AssertionError('No helper/native/Git before evidence gate')):
            with self.assertRaisesRegex(ValueError,'Root private decision'):self.qualify()
        self.assertFalse(self.output.exists())

    def test_wrong_source_attempt_job_and_whole_labels(self):
        for label,change in [
            ('source',lambda:self.data['producer'].update(head_sha='0'*40)),
            ('attempt',lambda:self.data['producer'].update(run_attempt=2)),
            ('windows-job',lambda:self.data['windowsJob'].update(id=1)),
            ('fake-whole-success',lambda:self.data['producer'].update(conclusion='success')),
            ('pending-whole',lambda:self.data['producer'].update(status='in_progress',conclusion=None)),
            ('masked-arm',lambda:self.mutate_job('macos (aarch64, aarch64-apple-darwin)','success'))]:
            with self.subTest(label=label):
                baseline=copy.deepcopy(self.data);change();self.save()
                with self.assertRaises(ValueError):self.qualify()
                self.data=baseline

    def test_normal_release_or_publication_authority_refuses(self):
        for key in ['root_whole_green_verified','normal_qualified','release_qualified','publication']:
            self.binding[key]=True;self.save()
            with self.assertRaisesRegex(ValueError,'remain false'):self.qualify()
            self.binding[key]=False

    def test_windows_mandatory_steps_all_remain_fatal(self):
        for number in P.WINDOWS_STEPS:
            with self.subTest(number=number):
                step=next(s for s in self.data['windowsJob']['steps'] if s['number']==number)
                job=next(j for j in self.data['producerJobs']['jobs'] if j['id']==P.WINDOWS)
                whole=next(s for s in job['steps'] if s['number']==number)
                step['conclusion']=whole['conclusion']='skipped';self.save()
                with self.assertRaises(ValueError):self.qualify()
                step['conclusion']=whole['conclusion']='success'

    def test_exact_disabled_diagnostic38_is_skipped_never_invented_success(self):
        self.qualify()
        step=next(s for s in self.data['windowsJob']['steps'] if s['number']==38)
        self.assertEqual(step['conclusion'],'skipped')
        self.assertEqual(next(s for s in self.data['windowsJob']['steps'] if s['number']==39)['conclusion'],'success')
        job=next(j for j in self.data['producerJobs']['jobs'] if j['id']==P.WINDOWS)
        whole=next(s for s in job['steps'] if s['number']==38)
        step['conclusion']=whole['conclusion']='success';self.save()
        with self.assertRaisesRegex(ValueError,'Disabled diagnostic Windows step 38'):self.qualify()

    def test_windows_failure_other_failure_and_comparison_not_green_refuse(self):
        changes=[lambda:self.mutate_job('windows','failure'),lambda:self.mutate_job('linux','failure'),
                 lambda:self.mutate_job('macos (x86_64, x86_64-apple-darwin)','failure'),
                 lambda:self.data['comparisonJobs']['jobs'][0].update(conclusion='failure'),
                 lambda:self.data['comparisonJobs']['jobs'][1].update(status='in_progress',conclusion=None)]
        for change in changes:
            baseline=copy.deepcopy(self.data);change();self.save()
            with self.assertRaises(ValueError):self.qualify()
            self.data=baseline

    def test_every_required_preflight_must_be_success(self):
        for job in self.data['preflightJobs']['jobs']:
            if job['name'] in P.PREF_SKIP:continue
            job['conclusion']='failure';self.save()
            with self.assertRaises(ValueError):self.qualify()
            job['conclusion']='success'

    def test_actual_arm_failure_step_must_remain_exact(self):
        for key,value in [('name','invented failure'),('number',99),('conclusion','success')]:
            arm=next(j for j in self.data['producerJobs']['jobs'] if j['id']==P.ARM)
            step=next(s for s in arm['steps'] if s['number']==25);old=step[key];step[key]=value
            self.data['armJob']=copy.deepcopy(arm);self.save()
            with self.assertRaises(ValueError):self.qualify()
            step[key]=old

    def test_retained_arm_warm_failure_log_cannot_be_relabelled_or_replaced(self):
        original=self.raw['armJobLog']
        self.raw['armJobLog']=original.replace(b'customer app exited',b'customer app survived')
        self.save()
        with self.assertRaisesRegex(ValueError,'original ARM warm-exit failure log'):self.qualify()

    def test_original_digest_id_archive_alias_and_provenance_refusals(self):
        changes=[lambda:self.data['provenance'].update(build_conclusion='success'),
                 lambda:self.data['windowsArtifact'].update(id=123),
                 lambda:self.data['windowsArtifact'].update(digest='sha256:'+'0'*64),
                 lambda:self.data['provenance']['collected_artifacts']['windows']['files_sha256'].update({'artifacts/windows/Mixel-Remote-QS.exe':'0'*64}),
                 lambda:self.data['checkoutEvidence'].update(originalLogSha256='0'*64)]
        for change in changes:
            baseline=copy.deepcopy(self.data);change();self.save()
            with self.assertRaises(ValueError):self.qualify()
            self.data=baseline
        self.save();self.paths['windowsArchive'].write_bytes(b'altered')
        with self.assertRaisesRegex(ValueError,'byte binding'):self.qualify()

    def test_duplicate_json_linked_and_foreign_input_refusals(self):
        self.binding_path.write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError,'Duplicate JSON'):
            P.qualify(self.binding_path,digest(self.binding_path.read_bytes()),self.repository,self.collection)
        self.save();alias=self.root/'alias';alias.symlink_to(self.paths['producer'])
        self.binding['references']['producer']['path']=str(alias)
        self.binding_path.write_bytes(encode(self.binding))
        with self.assertRaisesRegex(ValueError,'fixed private evidence'):
            P.qualify(self.binding_path,digest(self.binding_path.read_bytes()),self.repository,self.collection)

    def test_nonfinite_json_constants_refused(self):
        for value in ('NaN','Infinity','-Infinity'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'Nonfinite JSON'):
                P.unique_json('{"value":'+value+'}')

    def test_native_windows_provenance_crlf_is_explicit_derivative_not_original_normalization(self):
        lf=self.raw['provenance'];output=io.BytesIO()
        stream=io.TextIOWrapper(output,encoding='utf-8',newline='\r\n')
        stream.write(lf.decode());stream.flush();crlf=output.getvalue();stream.detach()
        self.assertEqual(crlf,lf.replace(b'\n',b'\r\n'))
        self.paths['provenance'].write_bytes(crlf)
        self.qualify()
        for wrong in [crlf.replace(b'\r\n',b'\n',1),crlf.replace(b'\r\n',b'\r',1),
                      crlf.replace(b'"build_conclusion": "failure"',b'"build_conclusion": "success"')]:
            self.paths['provenance'].write_bytes(wrong)
            with self.assertRaises(ValueError):self.qualify()

    def test_windows_shaped_owner_check_never_calls_getuid_or_silently_falls_back(self):
        # Deliberately absent getuid; Mac host cannot accidentally satisfy a
        # Unix ownership fallback while exercising Windows branch selection.
        windows=SimpleNamespace(name='nt',O_RDONLY=os.O_RDONLY,O_NOFOLLOW=getattr(os,'O_NOFOLLOW',0),
            open=os.open,fdopen=os.fdopen,fstat=os.fstat)
        with patch.object(P,'os',windows),patch.object(P,'windows_owner_matches_current_sid',return_value=True) as owner:
            self.assertEqual(P.owned_read(self.paths['producer'],1_000_000),self.raw['producer'])
            owner.assert_called_once()
        with patch.object(P,'os',windows),patch.object(P,'windows_owner_matches_current_sid',return_value=False):
            with self.assertRaisesRegex(ValueError,'owner differs'):P.owned_read(self.paths['producer'],1_000_000)
        with patch.object(P,'os',windows),patch.object(P,'windows_owner_matches_current_sid',side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):P.owned_read(self.paths['producer'],1_000_000)

    def test_windows_exact_tokenuser_and_distinct_tokenowner_accept_foreign_refuses(self):
        same=lambda left,right:left==right
        self.assertTrue(P.owner_matches_token_sids('runnerSID','runnerSID','defaultOwnerSID',same))
        self.assertTrue(P.owner_matches_token_sids('defaultOwnerSID','runnerSID','defaultOwnerSID',same))
        self.assertFalse(P.owner_matches_token_sids('foreignSID','runnerSID','defaultOwnerSID',same))
        # Membership is irrelevant: an Administrators label is not accepted
        # unless it is the actual current token's exact TokenOwner fact.
        self.assertFalse(P.owner_matches_token_sids('BUILTIN\\Administrators','runnerSID','differentDefaultOwnerSID',same))
        for triple in [(None,'runnerSID','defaultOwnerSID'),('owner',None,'defaultOwnerSID'),('owner','runnerSID',None)]:
            with self.assertRaisesRegex(ValueError,'SID missing'):P.owner_matches_token_sids(*triple,same)

    def test_fixed_relative_roles_and_collection_root_cannot_escape(self):
        for value in ['../producer.json','C:/Users/Root/producer.json','collector/../producer.json']:
            self.binding['references']['producer']['path']=value
            self.binding_path.write_bytes(encode(self.binding))
            with self.assertRaisesRegex(ValueError,'fixed private evidence'):
                P.qualify(self.binding_path,digest(self.binding_path.read_bytes()),self.repository,self.collection)
        self.save()
        with self.assertRaisesRegex(ValueError,'collector directory'):
            P.qualify(self.binding_path,self.binding_digest,self.repository,self.root)

    def test_fresh_attempt_source_status_drift_refused_before_and_after(self):
        before=P.check_live_files(self.live_state)
        self.assertEqual(set(before),{'producer','windowsJob','armJob','preflight','comparison'})
        for name,key,value in [('producer','run_attempt',2),('windowsJob','run_attempt',2),
            ('armJob','conclusion','success'),('preflight','conclusion','failure'),
            ('comparison','head_sha','0'*40),('windowsJob','status','in_progress')]:
            with self.subTest(name=name,key=key):
                path=self.live_state/(name+'.json');old=path.read_bytes();record=json.loads(old)
                record[key]=value;path.write_bytes(encode(record))
                with self.assertRaises(ValueError):P.check_live_files(self.live_state)
                path.write_bytes(old)

    def test_exact_same_driver_projection_and_manifest_false_flags(self):
        original_sources=A.read_sources(self.repository)
        expected,edits=A.projected(original_sources[A.DRIVER],original_sources[A.OBSERVER])
        def helpers(name):return A if name=='prepare-standard-foreground95.py' else C
        with patch.dict(os.environ,{'RUNNER_TEMP':str(self.runner)}),patch.object(P,'load_pinned',side_effect=helpers),\
                patch.object(A,'verify_harness',return_value={'offlineSyntheticHarnessOnly':True}):
            record=P.prepare(self.repository,self.output,self.binding_path,self.binding_digest,self.collection,'b'*40,'c'*40,self.live_state)
        self.assertEqual((self.output/'test-standard-foreground95.generated.ps1').read_bytes(),expected.encode())
        self.assertEqual(record['generated_driver_sha256'],digest(expected.encode()))
        self.assertEqual(record['actual_whole_facts']['producer'],{'status':'completed','conclusion':'failure'})
        self.assertEqual(record['actual_arm_failure'],{'id':P.ARM,'status':'completed','conclusion':'failure'})
        for key in ['normal_qualified','release_qualified','publication','root_whole_green_verified','native_runtime_qualified','standard_outer_qualified']:
            self.assertIs(record[key],False)
        inverse=expected
        for old,new in reversed(edits):inverse=A.replace_one(inverse,new,old)
        self.assertEqual(inverse,original_sources[A.DRIVER])
        with self.assertRaisesRegex(ValueError,'Existing output'):
            with patch.dict(os.environ,{'RUNNER_TEMP':str(self.runner)}),patch.object(P,'load_pinned',side_effect=helpers),\
                    patch.object(A,'verify_harness',return_value={}):
                P.prepare(self.repository,self.output,self.binding_path,self.binding_digest,self.collection,'b'*40,'c'*40,self.live_state)


class CallerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('capture95',HERE/'capture-private-standard95-state.py')
        cls.capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.capture)
        spec=importlib.util.spec_from_file_location('private_projection',HERE/'private_standard95_workflow_projection.py')
        cls.projection=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.projection)

    def test_explicit_private_request_is_distinct_and_cannot_publish_or_build(self):
        self.assertFalse(self.capture.validate_request({}))
        valid={'PRIVATE_STANDARD95':'true','GITHUB_EVENT_NAME':'workflow_dispatch','RUNTIME_RUN_ID':str(P.RUN),
            'PUBLISH_REQUESTED':'false','WINDOWS_RELEASE_GATE':'false','COLLECT_WINDOWS_PREFLIGHT_FAILURE':'false'}
        self.assertTrue(self.capture.validate_request(valid))
        for key,value in [('PUBLISH_REQUESTED','true'),('WINDOWS_RELEASE_GATE','true'),('RUNTIME_RUN_ID','1'),
            ('COLLECT_WINDOWS_PREFLIGHT_FAILURE','true'),('RUNTIME_MACOS_RUN','1'),('RUNTIME_LINUX_RUN','1'),
            ('INPUT_ONLY','windows'),('INPUT_UPSTREAM_VERSION','1.4.6')]:
            candidate=dict(valid);candidate[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.capture.validate_request(candidate)

    def test_missing_real_root_gate_refuses_before_provider_or_native_or_output(self):
        with tempfile.TemporaryDirectory(prefix='private95-no-gate-') as directory:
            root=Path(directory).resolve();output=root/('mixel-private95-live-before-'+'a'*32)
            with patch.object(self.capture,'api',side_effect=AssertionError('Provider forbidden')) as provider,\
                    patch.object(self.capture,'hosted_context',side_effect=AssertionError('Native context forbidden')):
                with self.assertRaisesRegex(ValueError,'Root gate is absent'):
                    self.capture.capture(root,root/self.capture.ROOT_GATE,'a'*64,'b'*40,'c'*40,output,'before')
            provider.assert_not_called();self.assertFalse(output.exists())

    def test_restore_outside_hosted_context_refuses_before_git_or_prefix_or_copy(self):
        with tempfile.TemporaryDirectory(prefix='private95-restore-refusal-') as directory:
            root=Path(directory).resolve();original=(root/'untouched.txt');original.write_bytes(b'owned user changes\n')
            with patch.object(self.capture.subprocess,'check_output',side_effect=AssertionError('Git forbidden')) as git,\
                patch.object(self.capture.tempfile,'TemporaryDirectory',side_effect=AssertionError('Prefix forbidden')) as prefix,\
                patch.object(self.capture.shutil,'copyfile',side_effect=AssertionError('Copy forbidden')) as copyfile:
                with self.assertRaisesRegex(ValueError,'hosted private Windows runtime context'):
                    self.capture.restore_git_bytes(root)
            git.assert_not_called();prefix.assert_not_called();copyfile.assert_not_called()
            self.assertEqual(original.read_bytes(),b'owned user changes\n')

    def test_restore_context_binds_workspace_ref_source_clean_state_and_temp_owner(self):
        with tempfile.TemporaryDirectory(prefix='private95-restore-context-') as directory:
            root=Path(directory).resolve();(root/'scripts').mkdir();temp=root/'runner-temp';temp.mkdir()
            source='b'*40;ref='refs/heads/codex/private-qa'
            environment={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','GITHUB_JOB':'runtime',
                'GITHUB_REPOSITORY':self.capture.REPOSITORY,'GITHUB_EVENT_NAME':'workflow_dispatch',
                'GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':source,'GITHUB_WORKFLOW_SHA':source,
                'MIXEL_PRIVATE_STANDARD95_SCOPE':P.SCOPE,'GITHUB_REF':ref,
                'GITHUB_WORKFLOW_REF':self.capture.REPOSITORY+'/.github/workflows/build.yml@'+ref,
                'GITHUB_WORKSPACE':str(root),'RUNNER_TEMP':str(temp)}
            def invoke(values,git_values=None,owner=True):
                windows=SimpleNamespace(name='nt',environ=values)
                with patch.object(self.capture,'os',windows),patch.object(self.capture,'HERE',root/'scripts'),\
                    patch.object(self.capture,'windows_directory_owner_matches_current_token',return_value=owner),\
                    patch.object(self.capture.subprocess,'check_output',side_effect=git_values or [source.encode()+b'\n',b'']):
                    return self.capture.restore_context(root)
            self.assertEqual(invoke(environment),temp)
            for key,value in [('GITHUB_EVENT_NAME','push'),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_JOB','other'),
                ('GITHUB_WORKSPACE',str(temp)),('GITHUB_WORKFLOW_REF','wrong'),('GITHUB_WORKFLOW_SHA','c'*40),
                ('MIXEL_PRIVATE_STANDARD95_SCOPE',''),('GITHUB_REF','unscoped')]:
                bad=dict(environment);bad[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):invoke(bad)
            with self.assertRaisesRegex(ValueError,'dirty tracked'):invoke(environment,[source.encode()+b'\n',b' M scripts/user.py\n'])
            with self.assertRaisesRegex(ValueError,'HEAD differs'):invoke(environment,[b'c'*40+b'\n'])
            with self.assertRaisesRegex(ValueError,'owned runner'):invoke(environment,owner=False)

    def test_fresh_api_stdout_is_retained_as_exact_bytes(self):
        raw=b'{\r\n "exact":true\r\n}\r\n'
        with patch.object(self.capture.subprocess,'run',return_value=subprocess.CompletedProcess([],0,raw,b'')) as run:
            self.assertEqual(self.capture.api('actions/runs/1'),raw)
            self.assertEqual(run.call_args.args[0][:4],['gh','api','--hostname','github.com'])
        with patch.object(self.capture.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'{"bad":NaN}',b'')):
            with self.assertRaises(ValueError):self.capture.api('actions/runs/1')

    def test_full_raw_capture_facade_preserves_failure_and_before_after_bytes(self):
        fixture=PrivateAdmission();fixture.setUp()
        try:
            responses={endpoint:fixture.raw[name] for name,endpoint in self.capture.ENDPOINTS.items()}
            for stage in ('before','after'):
                output=fixture.runner/('mixel-private95-live-'+stage+'-'+'d'*32)
                with patch.object(self.capture,'precheck',return_value={'syntheticHarnessOnly':True}),\
                    patch.object(self.capture,'hosted_context'),patch.object(self.capture,'api',side_effect=lambda path:responses[path]) as api,\
                    patch.dict(os.environ,{'RUNNER_TEMP':str(fixture.runner)}):
                    record=self.capture.capture(fixture.repository,fixture.binding_path,fixture.binding_digest,'b'*40,'c'*40,output,stage)
                self.assertEqual(api.call_count,5);self.assertEqual(record['stage'],stage)
                self.assertEqual(record['wholeProducer'],'completed/failure');self.assertFalse(record['publication'])
                for name in self.capture.ENDPOINTS:self.assertEqual((output/(name+'.json')).read_bytes(),fixture.raw[name])
        finally:fixture.doCleanups()

    def test_complete_private_workflow_inverse_and_mutations(self):
        text=(REPOSITORY/'.github/workflows/build.yml').read_text()
        normal=self.projection.inverse_private_standard95(text,'build.yml')
        self.assertEqual(digest(normal.encode()),self.projection.NORMAL_WORKFLOW_SHA256)
        spec=importlib.util.spec_from_file_location('normal_projection',HERE/'standard_foreground95_workflow_projection.py')
        normal_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(normal_module)
        original=normal_module.inverse_standard_foreground95_runtime_checks(text,'build.yml')
        self.assertEqual(digest(original.encode()),normal_module.APP77_BUILD_WORKFLOW_SHA256)
        self.assertLess(text.index('Prepare PRIVATE genuine Standard foreground95'),text.index('Exercise actual incoming support runtime'))
        self.assertIn("always() && inputs.runtime_windows_private_standard95 && steps.private_before.outcome == 'success'",text)
        self.assertIn('capture-private-standard95-state.py restore-git-bytes --repository',text)
        for old,new in [('runtime_windows_private_standard95:', 'private95_typo:'),
            ('--stage after','--stage before'),('-Portable -QuickSupport -ExpectedPayload','-QuickSupport -ExpectedPayload'),
            ("Private Root gate is absent; no collection or application work permitted.","gate optional")]:
            mutant=text.replace(old,new,1)
            with self.subTest(old=old),self.assertRaises(AssertionError):self.projection.inverse_private_standard95(mutant,'build.yml')

    def test_all_actual_private_runtime_powershell_blocks_parse_without_execution(self):
        text=(REPOSITORY/'.github/workflows/build.yml').read_text()
        runtime=text.split('\n  runtime:\n',1)[1].split('\n  runtime-macos:',1)[0]
        blocks=[]
        for segment in runtime.split('      - '):
            if '        shell: pwsh\n' not in segment or '        run: |\n' not in segment:continue
            body=segment.split('        run: |\n',1)[1]
            blocks.append('\n'.join(line[10:] for line in body.splitlines() if line.startswith('          ')))
        self.assertGreaterEqual(len(blocks),12)
        with tempfile.TemporaryDirectory(prefix='private95-pwsh-parse-') as directory:
            fixture=Path(directory)/'blocks.json';fixture.write_text(json.dumps(blocks))
            script=Path(directory)/'parse.ps1';script.write_text("$items=Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json\nforeach($text in $items){$tokens=$null;$errors=$null;[void][Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors | Out-String)}}\nWrite-Host ('PASS: actual private runtime PowerShell blocks parsed: '+$items.Count)\n")
            result=subprocess.run([shutil.which('pwsh'),'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),str(fixture)],
                capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS: actual private runtime PowerShell blocks parsed:',result.stdout)

    def test_clean_git_checkout_without_artifacts_and_actual_autocrlf_restoration(self):
        with tempfile.TemporaryDirectory(prefix='private95-clean-Git-') as directory:
            root=Path(directory).resolve();(root/'scripts').mkdir();(root/'.github/workflows').mkdir(parents=True)
            names=set(A.PINS)|{'prepare-standard-foreground95.py','collect-windows-release-runtime.py',
                'prepare-private-standard-foreground95.py','test-private-standard-foreground95.py',
                'capture-private-standard95-state.py','private_standard95_workflow_projection.py',
                'standard_foreground95_workflow_projection.py'}
            for name in names:shutil.copy2(HERE/name,root/'scripts'/name)
            shutil.copytree(HERE/'qa',root/'scripts/qa')
            shutil.copy2(REPOSITORY/'.github/workflows/build.yml',root/'.github/workflows/build.yml')
            def git(*args,input=None):
                result=subprocess.run(['git','-C',str(root),*args],input=input,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'));return result.stdout
            git('init','--quiet');git('add','scripts','.github/workflows/build.yml')
            git('-c','user.name=Offline QA','-c','user.email=offline@example.invalid','commit','--quiet','-m','Synthetic local source controls')
            original=(root/'scripts/prepare-private-standard-foreground95.py').read_bytes()
            paths=git('ls-files','-z','--','scripts','.github/workflows/build.yml')
            with tempfile.TemporaryDirectory(prefix='private95-crlf-prefix-') as converted_directory:
                prefix=Path(converted_directory)
                git('-c','core.autocrlf=true','checkout-index','--force','--prefix='+prefix.as_posix()+'/','-z','--stdin',input=paths)
                for name in paths[:-1].decode().split('\0'):shutil.copyfile(prefix/name,root/name)
            converted=(root/'scripts/prepare-private-standard-foreground95.py').read_bytes()
            self.assertIn(b'\r\n',converted);self.assertNotEqual(converted,original)
            workflow=(root/'.github/workflows/build.yml').read_text()
            restore=next(line.strip() for line in workflow.splitlines() if 'capture-private-standard95-state.py restore-git-bytes' in line)
            command=shlex.split(restore.replace('$env:GITHUB_WORKSPACE',str(root)))
            environment=dict(os.environ,RUNNER_TEMP=str(root.parent))
            self.assertIn('restore-git-bytes',command)
            # Only this source fixture substitutes the internal authorization
            # guard. The production function and CLI expose no bypass.
            with patch.object(self.capture,'restore_context',return_value=root.parent):
                self.capture.restore_git_bytes(root)
            self.assertEqual((root/'scripts/prepare-private-standard-foreground95.py').read_bytes(),original)
            self.assertEqual(original,git('show','HEAD:scripts/prepare-private-standard-foreground95.py'))
            self.assertFalse((root/'artifacts').exists())
            child=subprocess.run([sys.executable,'-B','scripts/test-private-standard-foreground95.py','PrivateAdmission'],
                cwd=root,capture_output=True,text=True,timeout=90)
            self.assertEqual(child.returncode,0,child.stdout+child.stderr)
            self.assertIn('Ran 19 tests',child.stderr);self.assertIn('OK',child.stderr)


if __name__=='__main__':unittest.main(verbosity=2)
