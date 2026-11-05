#!/usr/bin/env python3
"""Run strict source controls and source-generated, fully redirected Bash fixtures.

These fixtures never execute a packaged maintainer script or any real service
command. They redirect every absolute product path to a private temporary tree,
and supply closed command stubs. They prove shell control flow/error propagation,
not live dpkg/systemd or the Debian helper's own implementation.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
args, remaining = parser.parse_known_args()
spec = importlib.util.spec_from_file_location('lifecycle', Path(__file__).with_name('patch-debian-service-lifecycle.py'))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
M.apply(args.source, check=True)
BASE = M.transform({n: (args.source/'res/DEBIAN'/n).read_bytes() for n in M.ORIGINAL_SHA256}, restore=True)
CURRENT = M.transform(BASE)
UNIT = (args.source/'res/mixel-remote.service').read_bytes()
BASH = shutil.which('bash')
assert BASH, 'Bash is required; no skipped lifecycle controls'

STUB = r'''import hashlib,json,os,shutil,sys
from pathlib import Path
command=Path(sys.argv[0]).name
argv=sys.argv[1:]
root=Path(os.environ['FIXTURE_ROOT'])
with (root/'commands.jsonl').open('a') as stream:stream.write(json.dumps([command,*argv])+'\n')
def fail(code,message):
 print(message,file=sys.stderr);sys.exit(code)
if command=='ls':
 assert argv==['-al',str(root/'proc/1/exe')]
 print('fixture '+('/lib/systemd/systemd' if os.environ.get('INIT','systemd')=='systemd' else '/sbin/init'))
elif command=='ps':
 assert argv==['-ef']
elif command=='sleep':
 assert argv==['1']
elif command=='service':
 assert argv==['mixel-remote','stop']
 fail(5,'Failed to stop mixel-remote.service: Unit mixel-remote.service not loaded.')
elif command=='systemctl':
 if argv==['show','--all','mixel-remote.service','--property=LoadState']:
  if os.environ.get('QUERY_FAIL'):fail(23,'fixture query failure')
  print(os.environ.get('LOAD_STATE','LoadState=loaded'))
 else:
  assert argv in [['stop','mixel-remote.service'],['stop','mixel-remote'],['disable','mixel-remote'],['daemon-reload'],['enable','mixel-remote'],['start','mixel-remote.service']]
  if os.environ.get('FAIL_ACTION')==argv[0]:fail(41,'fixture '+argv[0]+' failure')
elif command=='deb-systemd-invoke':
 assert argv in [['stop','mixel-remote.service'],['start','mixel-remote.service']]
 if os.environ.get('POLICY')=='101':
  print('fixture policy-rc.d returned 101; action not run',file=sys.stderr)
 else:
  os.execv(str(root/'bin/systemctl'),[str(root/'bin/systemctl'),*argv])
elif command in ('readlink','sha256sum'):
 assert len(argv)==2 and argv[0]=='--'
 path=Path(argv[1]);assert path.is_relative_to(root)
 if os.environ.get('FAIL_ACTION')==command:fail(29,'fixture '+command+' failure')
 if command=='readlink':print(os.readlink(path))
 else:print(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+str(path))
elif command=='rm':
 paths=[Path(a) for a in argv if not a.startswith('-')]
 flags=[a for a in argv if a.startswith('-')]
 assert set(flags)<=set(['-f','-rf','--']) and paths
 assert all(p.is_relative_to(root) and p!=root for p in paths)
 code=0
 for path in paths:
  if os.environ.get('RM_FAIL_PATH')==str(path):fail(37,'fixture removal failure')
  if path.is_symlink() or path.is_file():path.unlink()
  elif path.is_dir() and '-rf' in flags:shutil.rmtree(path)
  elif path.exists():fail(1,'fixture refusing nonregular removal')
  elif not any('f' in a for a in flags):
   print("rm: cannot remove '"+str(path)+"': No such file or directory",file=sys.stderr);code=1
 sys.exit(code)
else:raise AssertionError(command)
'''

class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mixel-debian-lifecycle-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = ['/usr/share/mixel-remote/files/systemd/mixel-remote.service',
                      '/usr/share/mixel-remote/mixel-remote', '/etc/systemd/system/mixel-remote.service',
                      '/usr/lib/systemd/system/mixel-remote.service', '/lib/systemd/system/mixel-remote.service',
                      '/usr/lib/systemd/user/mixel-remote.service', '/run/systemd/system',
                      '/usr/lib/systemd/system/', '/usr/bin/mixel-remote', '/usr/bin/libsciter-gtk.so',
                      '/usr/bin/pkill', '/proc/1/exe']
        self.bindings = {v:str(self.root/v.lstrip('/')) for v in self.paths}
        for v in self.bindings.values():Path(v).parent.mkdir(parents=True,exist_ok=True)
        self.override = Path(self.bindings['/etc/systemd/system/mixel-remote.service'])
        self.vendor = Path(self.bindings['/usr/lib/systemd/system/mixel-remote.service'])
        self.template = Path(self.bindings['/usr/share/mixel-remote/files/systemd/mixel-remote.service'])
        self.template.write_bytes(UNIT)
        Path(self.bindings['/run/systemd/system']).mkdir()
        (self.root/'bin').mkdir()
        for command in ['ls','ps','sleep','service','systemctl','deb-systemd-invoke','readlink','sha256sum','rm']:
            q=self.root/'bin'/command
            q.write_text('#!'+sys.executable+'\n'+STUB);q.chmod(0o700)
        self.sentinel=self.root/'foreign-sentinel';self.sentinel.write_bytes(b'unchanged foreign bytes')

    def execute(self,name,action=None,original=False,**env):
        value=(BASE if original else CURRENT)[name].decode()
        pattern=re.compile('|'.join(re.escape(p) for p in sorted(self.paths,key=len,reverse=True)))
        value=pattern.sub(lambda match:self.bindings[match.group()],value)
        # No unbound product, manager or PID path may reach an executed fixture.
        unbound=value
        for bound in sorted(self.bindings.values(),key=len,reverse=True):unbound=unbound.replace(bound,'OWNED')
        self.assertNotIn('/etc/systemd/',unbound)
        self.assertNotIn('/run/systemd/system',unbound)
        for prefix in ['/usr/','/lib/','/proc/','/etc/','/run/']:self.assertNotIn(prefix,unbound)
        script=self.root/(name+'.fixture.sh');script.write_text(value)
        check=subprocess.run([BASH,'-n',str(script)],capture_output=True,timeout=3)
        self.assertEqual(check.returncode,0,check.stderr)
        environment={k:v for k,v in os.environ.items() if k not in ['DPKG_ROOT','LOAD_STATE','POLICY','FAIL_ACTION','QUERY_FAIL','RM_FAIL_PATH','INIT']}
        environment.update(PATH=str(self.root/'bin')+os.pathsep+os.defpath,FIXTURE_ROOT=str(self.root),**env)
        result=subprocess.run([BASH,str(script),action or ('configure' if name=='postinst' else 'remove' if name=='prerm' else 'install')],
                              capture_output=True,text=True,env=environment,timeout=3)
        log=self.root/'commands.jsonl'
        calls=[json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        self.assertEqual(self.sentinel.read_bytes(),b'unchanged foreign bytes')
        return result,calls

    def ok(self,result):self.assertEqual(result.returncode,0,result.stderr)
    def service_calls(self,calls):return [c for c in calls if c[0] in ['systemctl','deb-systemd-invoke','service']]
    def test_exact_inverse_idempotence_and_uniform_crlf(self):
        self.assertEqual(M.transform(CURRENT),CURRENT)
        self.assertEqual(M.transform(CURRENT,restore=True),BASE)
        self.assertEqual(M.transform(BASE,restore=True),BASE)
        crlf={n:v.replace(b'\n',b'\r\n') for n,v in BASE.items()}
        self.assertEqual(M.transform(crlf),{n:v.replace(b'\n',b'\r\n') for n,v in CURRENT.items()})
        self.assertEqual(M.transform(M.transform(crlf),restore=True),crlf)
    def test_drift_duplicate_partial_and_inventory_refusals(self):
        for candidate in [{**BASE,'preinst':BASE['preinst']+b'\n'},
                          {**BASE,'prerm':BASE['prerm'].replace(b'systemctl stop',b'systemctl stop stop')},
                          {**BASE,'preinst':CURRENT['preinst']},
                          {n:v for n,v in BASE.items() if n!='postinst'}]:
            with self.assertRaises(RuntimeError):M.transform(candidate)
    def test_mixed_newline_refusal(self):
        with self.assertRaises(RuntimeError):M.transform({**BASE,'preinst':BASE['preinst'].replace(b'\n',b'\r\n',1)})
    def test_apply_preserves_modes_and_unrelated_files(self):
        source=self.root/'source';(source/'res/DEBIAN').mkdir(parents=True)
        for n,v in BASE.items():(source/'res/DEBIAN'/n).write_bytes(v);(source/'res/DEBIAN'/n).chmod(0o755)
        (source/'res/mixel-remote.service').write_bytes(UNIT)
        other=source/'res/DEBIAN/postrm';other.write_bytes(b'unchanged postrm')
        M.apply(source,check=True)
        self.assertEqual((source/'res/DEBIAN/preinst').read_bytes(),BASE['preinst'])
        M.apply(source);M.apply(source)
        for n,v in CURRENT.items():
            self.assertEqual((source/'res/DEBIAN'/n).read_bytes(),v)
            self.assertEqual((source/'res/DEBIAN'/n).stat().st_mode&0o777,0o755)
        self.assertEqual(other.read_bytes(),b'unchanged postrm')
        M.apply(source,restore=True)
        for n,v in BASE.items():self.assertEqual((source/'res/DEBIAN'/n).read_bytes(),v)
    def test_apply_refuses_wrong_unit_before_any_write(self):
        source=self.root/'source';(source/'res/DEBIAN').mkdir(parents=True)
        for n,v in BASE.items():(source/'res/DEBIAN'/n).write_bytes(v)
        (source/'res/mixel-remote.service').write_bytes(UNIT+b'changed')
        with self.assertRaises(RuntimeError):M.apply(source)
        for n,v in BASE.items():self.assertEqual((source/'res/DEBIAN'/n).read_bytes(),v)
    def test_first_install_absence_no_stop_warning(self):
        result,calls=self.execute('preinst',LOAD_STATE='LoadState=not-found');self.ok(result)
        self.assertEqual(result.stderr,'');self.assertEqual(len(self.service_calls(calls)),1)
    def test_upgrade_loaded_stops_through_policy_helper(self):
        result,calls=self.execute('preinst','upgrade');self.ok(result)
        self.assertIn(['deb-systemd-invoke','stop','mixel-remote.service'],calls)
        self.assertIn(['systemctl','stop','mixel-remote.service'],calls)
    def test_inactive_loaded_stop_is_success(self):
        result,calls=self.execute('preinst',LOAD_STATE='LoadState=loaded');self.ok(result)
        self.assertEqual(result.stderr,'')
    def test_absent_remove_does_not_emit_rm_warning(self):
        result,calls=self.execute('prerm',LOAD_STATE='LoadState=not-found');self.ok(result)
        self.assertEqual(result.stderr,'');self.assertEqual(len(self.service_calls(calls)),1)
    def test_query_failure_is_visible_and_fatal(self):
        result,calls=self.execute('preinst',QUERY_FAIL='1')
        self.assertEqual(result.returncode,23);self.assertIn('query failure',result.stderr)
        self.assertFalse(any(c[0]=='deb-systemd-invoke' for c in calls))
    def test_stop_failure_is_visible_and_prevents_disable(self):
        result,calls=self.execute('prerm',FAIL_ACTION='stop')
        self.assertEqual(result.returncode,41);self.assertIn('stop failure',result.stderr)
        self.assertNotIn(['systemctl','disable','mixel-remote'],calls)
    def test_disable_failure_is_visible_and_prevents_unit_remove(self):
        self.vendor.write_bytes(UNIT)
        result,calls=self.execute('prerm',FAIL_ACTION='disable')
        self.assertEqual(result.returncode,41);self.assertIn('disable failure',result.stderr);self.assertTrue(self.vendor.exists())
    def test_unknown_load_state_refuses(self):
        result,calls=self.execute('preinst',LOAD_STATE='LoadState=error')
        self.assertEqual(result.returncode,1);self.assertIn('Unexpected Mixel service load state',result.stderr)
    def test_package_policy_denial_is_not_operation_failure(self):
        result,calls=self.execute('preinst',POLICY='101');self.ok(result)
        self.assertIn('policy-rc.d returned 101',result.stderr)
        self.assertNotIn(['systemctl','stop','mixel-remote.service'],calls)
    def test_non_systemd_unchanged_no_manager_operations(self):
        result,calls=self.execute('preinst',INIT='init');self.ok(result);self.assertEqual(self.service_calls(calls),[])
    def test_systemd_manager_not_running_does_not_query(self):
        Path(self.bindings['/run/systemd/system']).rmdir()
        result,calls=self.execute('preinst');self.ok(result);self.assertEqual(self.service_calls(calls),[])
    def test_offline_dpkg_root_does_not_touch_live_manager(self):
        result,calls=self.execute('preinst',DPKG_ROOT='/offline-fixture');self.ok(result);self.assertEqual(self.service_calls(calls),[])
    def test_known_regular_legacy_override_is_removed(self):
        self.override.write_bytes(UNIT);self.vendor.write_bytes(UNIT)
        result,calls=self.execute('prerm');self.ok(result);self.assertFalse(self.override.exists());self.assertFalse(self.vendor.exists())
    def test_known_transformed_legacy_override_is_removed(self):
        self.override.write_bytes(UNIT.replace(b'pkill',b'/usr/bin/pkill'))
        result,calls=self.execute('prerm');self.ok(result);self.assertFalse(self.override.exists())
    def test_known_valid_legacy_link_is_removed_without_following(self):
        self.vendor.write_bytes(UNIT);self.override.symlink_to(self.vendor)
        result,calls=self.execute('prerm');self.ok(result);self.assertFalse(self.override.is_symlink())
    def test_known_dangling_legacy_link_is_removed(self):
        self.override.symlink_to(self.vendor)
        result,calls=self.execute('prerm',LOAD_STATE='LoadState=not-found');self.ok(result);self.assertFalse(self.override.is_symlink())
    def test_foreign_link_and_target_are_preserved(self):
        self.override.symlink_to(self.sentinel)
        result,calls=self.execute('prerm');self.ok(result);self.assertTrue(self.override.is_symlink());self.assertEqual(self.service_calls(calls),[])
    def test_known_link_to_modified_vendor_unit_is_preserved(self):
        self.vendor.write_bytes(b'administrator modified vendor unit')
        self.override.symlink_to(self.vendor)
        result,calls=self.execute('preinst');self.ok(result)
        self.assertTrue(self.override.is_symlink());self.assertEqual(self.service_calls(calls),[])
        self.assertEqual(self.vendor.read_bytes(),b'administrator modified vendor unit')
    def test_link_read_error_is_visible_and_fatal(self):
        self.override.symlink_to(self.vendor)
        result,calls=self.execute('preinst',FAIL_ACTION='readlink')
        self.assertEqual(result.returncode,29);self.assertIn('readlink failure',result.stderr)
        self.assertEqual(self.service_calls(calls),[])
    def test_loaded_masked_manager_state_still_checks_operation_failures(self):
        result,calls=self.execute('preinst',LOAD_STATE='LoadState=masked',FAIL_ACTION='stop')
        self.assertEqual(result.returncode,41);self.assertIn('stop failure',result.stderr)
    def test_remove_preserves_foreign_vendor_target_of_override(self):
        self.vendor.write_bytes(b'administrator modified vendor unit')
        self.override.symlink_to(self.vendor)
        result,calls=self.execute('prerm');self.ok(result)
        self.assertEqual(self.vendor.read_bytes(),b'administrator modified vendor unit')
        self.assertTrue(self.override.is_symlink());self.assertEqual(self.service_calls(calls),[])
    def test_configure_preserves_foreign_vendor_target_of_override(self):
        self.vendor.write_bytes(b'administrator modified vendor unit')
        self.override.symlink_to(self.vendor)
        result,calls=self.execute('postinst');self.ok(result)
        self.assertEqual(self.vendor.read_bytes(),b'administrator modified vendor unit')
        self.assertTrue(self.override.is_symlink());self.assertEqual(self.service_calls(calls),[])
    def test_postinst_start_failure_is_not_hidden(self):
        result,calls=self.execute('postinst',FAIL_ACTION='start')
        self.assertEqual(result.returncode,41);self.assertIn('start failure',result.stderr)
    def test_postinst_enable_failure_is_not_hidden(self):
        result,calls=self.execute('postinst',FAIL_ACTION='enable')
        self.assertEqual(result.returncode,41);self.assertIn('enable failure',result.stderr)
        self.assertNotIn(['deb-systemd-invoke','start','mixel-remote.service'],calls)
    def test_custom_regular_override_and_enablement_preserved(self):
        self.override.write_bytes(b'[Service]\nExecStart=/custom/administrator\n')
        result,calls=self.execute('prerm');self.ok(result)
        self.assertEqual(self.override.read_bytes(),b'[Service]\nExecStart=/custom/administrator\n');self.assertEqual(self.service_calls(calls),[])
        self.assertIn('Preserving administrator',result.stdout)
    def test_foreign_dangling_link_preserved(self):
        self.override.symlink_to(self.root/'foreign-absent')
        result,calls=self.execute('prerm');self.ok(result);self.assertTrue(self.override.is_symlink());self.assertEqual(self.service_calls(calls),[])
    def test_administrator_mask_preserved(self):
        self.override.symlink_to('/dev/null')
        result,calls=self.execute('preinst');self.ok(result);self.assertEqual(os.readlink(self.override),'/dev/null');self.assertEqual(self.service_calls(calls),[])
    def test_nonregular_override_not_opened_or_removed(self):
        os.mkfifo(self.override)
        result,calls=self.execute('prerm');self.ok(result);self.assertTrue(self.override.exists())
        self.assertFalse(any(c[0]=='sha256sum' for c in calls));self.assertEqual(self.service_calls(calls),[])
    def test_legacy_override_read_error_is_not_hidden(self):
        self.override.write_bytes(UNIT)
        result,calls=self.execute('prerm',FAIL_ACTION='sha256sum')
        self.assertEqual(result.returncode,29);self.assertIn('sha256sum failure',result.stderr);self.assertTrue(self.override.exists())
    def test_unit_removal_error_is_not_hidden(self):
        self.vendor.write_bytes(UNIT)
        result,calls=self.execute('prerm',RM_FAIL_PATH=str(self.vendor))
        self.assertEqual(result.returncode,37);self.assertIn('removal failure',result.stderr);self.assertTrue(self.vendor.exists())
    def test_postinst_default_file_alias_and_manager_sequence(self):
        result,calls=self.execute('postinst');self.ok(result)
        self.assertTrue(Path(self.bindings['/usr/bin/mixel-remote']).is_symlink())
        self.assertEqual(self.vendor.read_bytes(),UNIT)
        self.assertEqual(self.service_calls(calls),[['systemctl','daemon-reload'],['systemctl','enable','mixel-remote'],['deb-systemd-invoke','start','mixel-remote.service'],['systemctl','start','mixel-remote.service']])
    def test_postinst_policy_denial_does_not_start(self):
        result,calls=self.execute('postinst',POLICY='101');self.ok(result)
        self.assertNotIn(['systemctl','start','mixel-remote.service'],calls);self.assertIn('policy-rc.d returned 101',result.stderr)
    def test_postinst_chroot_no_manager_preserves_file_install(self):
        Path(self.bindings['/run/systemd/system']).rmdir()
        result,calls=self.execute('postinst');self.ok(result);self.assertEqual(self.vendor.read_bytes(),UNIT);self.assertEqual(self.service_calls(calls),[])
    def test_postinst_foreign_override_is_not_deleted_or_managed(self):
        self.override.write_bytes(b'custom override unchanged')
        result,calls=self.execute('postinst');self.ok(result);self.assertEqual(self.override.read_bytes(),b'custom override unchanged');self.assertEqual(self.service_calls(calls),[])
    def test_postinst_known_legacy_override_migrates_to_vendor(self):
        self.override.write_bytes(UNIT)
        result,calls=self.execute('postinst');self.ok(result);self.assertFalse(self.override.exists());self.assertEqual(self.vendor.read_bytes(),UNIT)
    def test_postinst_manager_errors_propagate(self):
        result,calls=self.execute('postinst',FAIL_ACTION='daemon-reload')
        self.assertEqual(result.returncode,41);self.assertIn('daemon-reload failure',result.stderr)
        self.assertNotIn(['systemctl','enable','mixel-remote'],calls)
    def test_original_warning_reproduced_in_closed_source_fixture(self):
        result,calls=self.execute('preinst',original=True);self.ok(result)
        self.assertIn('Unit mixel-remote.service not loaded',result.stderr)
    def test_original_remove_warning_reproduced_in_closed_source_fixture(self):
        self.vendor.write_bytes(UNIT)
        result,calls=self.execute('prerm',original=True);self.ok(result)
        self.assertIn('No such file or directory',result.stderr)
    def test_other_maintainer_action_is_noop(self):
        result,calls=self.execute('prerm','failed-upgrade');self.ok(result);self.assertEqual(calls,[])

if __name__=='__main__':unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)
