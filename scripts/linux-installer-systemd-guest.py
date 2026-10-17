#!/usr/bin/env python3
"""FUTURE disposable Ubuntu22.04 systemd guest only; never run during preparation."""
import hashlib,json,os,pathlib,subprocess,time,stat,sys,re
P=pathlib.Path
CLEANUP_MODE=sys.argv[1:]==['--cleanup-owned-fixtures']
if sys.argv[1:]and not CLEANUP_MODE:raise RuntimeError('unknown-guest-arguments')
OUT=P('/var/tmp/mixel-systemd-qa')
if CLEANUP_MODE:
 info=OUT.lstat()
 if not stat.S_ISDIR(info.st_mode)or info.st_uid!=0 or info.st_mode&0o7777!=0o700:raise RuntimeError('cleanup-evidence-not-owned')
else:OUT.mkdir(mode=0o700,exist_ok=False)
seq=0
if CLEANUP_MODE:
 seq=max((int(q.stem)for q in OUT.iterdir()if len(q.stem)==3 and q.stem.isdigit()),default=0)
 if seq>500:raise RuntimeError('cleanup-command-sequence-bound')
EXPECTED=json.loads(P('/mnt/mixel-candidate/expected.json').read_text())
BASELINE_DEB_SHA='993f516763f85cd749faff8752e52ad5ac7be40b21e78d5878ec3a40422abc47'
if EXPECTED.get('DEBSHA256')!='0666ca6f87008654c8e06d17a9555ea6e207d4fec3491b10abee076308a9bd58':raise RuntimeError('guest-not-qualified-derived0666')
if not isinstance(EXPECTED.get('instance'),str)or not re.fullmatch(r'mixel-linux-installer-systemd-[1-9][0-9]*-[1-9][0-9]*',EXPECTED['instance']):raise RuntimeError('guest-instance')
def require(ok,why):
 if not ok:raise RuntimeError(why)
def sha(p):return hashlib.sha256(P(p).read_bytes()).hexdigest()
def run(argv,allowed=(0,)):
 global seq;seq+=1;r=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600)
 stem=OUT/f'{seq:03d}';stem.with_suffix('.stdout').write_bytes(r.stdout);stem.with_suffix('.stderr').write_bytes(r.stderr)
 stem.with_suffix('.json').write_text(json.dumps({'argv':argv,'exit':r.returncode,'stdoutSHA256':hashlib.sha256(r.stdout).hexdigest(),'stderrSHA256':hashlib.sha256(r.stderr).hexdigest()},indent=2)+'\n')
 require(r.returncode in allowed,'command-failed-'+str(seq));return r.stdout.decode().strip()
def birth(pid):
 try:return P(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
 except FileNotFoundError:return None
def properties():
 raw=run(['systemctl','show','mixel-remote.service','-p','User','-p','ExecStart','-p','FragmentPath','-p','MainPID','-p','ActiveState','-p','SubState','-p','Result','-p','NRestarts','-p','ControlGroup'])
 return dict(row.split('=',1)for row in raw.splitlines())
def live():
 r=properties();pid=int(r['MainPID']);require(pid>1 and r['User']=='root'and r['ActiveState']=='active'and r['SubState']=='running'and r['Result']=='success'and r['NRestarts']=='0','service-not-stable-root-active')
 require(os.path.realpath(r['FragmentPath'])==os.path.realpath('/usr/lib/systemd/system/mixel-remote.service')and'/usr/bin/mixel-remote --service'in r['ExecStart'],'wrong-real-systemd-unit-or-ExecStart')
 require(P(f'/proc/{pid}').stat().st_uid==0 and os.path.realpath(f'/proc/{pid}/exe')=='/usr/share/mixel-remote/mixel-remote'and sha(f'/proc/{pid}/exe')==EXPECTED['mainSHA256'],'MainPID-not-exact-installed-root-payload')
 cg=P('/sys/fs/cgroup'+r['ControlGroup']);require(pid in [int(v)for v in (cg/'cgroup.procs').read_text().split()],'MainPID-not-in-actual-systemd-cgroup')
 return {'pid':pid,'birth':birth(pid),'cgroup':str(cg),'mainSHA256':sha(f'/proc/{pid}/exe')}
def gone(owner,cgroup_empty=True):
 require(birth(owner['pid'])!=owner['birth'],'old-main-process-still-alive')
 p=P(owner['cgroup'])/'cgroup.procs'
 if cgroup_empty:require(not p.exists()or not p.read_text().strip(),'owned-service-cgroup-not-settled')
def inactive(owner):
 require(run(['systemctl','is-active','mixel-remote.service'],(3,4))in('inactive','unknown'),'service-still-active');gone(owner)
# Only these instance-owned synthetic fixtures may be touched by extra cases.
FIXTURE_PATHS = {
 'override':P('/etc/systemd/system/mixel-remote.service'),
 'wants':P('/etc/systemd/system/multi-user.target.wants/mixel-remote.service'),
 'policy':P('/usr/sbin/policy-rc.d'), 'policy-events':OUT/'policy-events',
}
FIXTURE_UID=0
FIXTURE_GID=0
LEDGER=OUT/'owned-fixtures.json'

def fixture_identity(path, events=False):
 info=path.lstat()
 require(info.st_uid==FIXTURE_UID and info.st_gid==FIXTURE_GID and info.st_nlink==1,'fixture-foreign-owner-or-hardlink')
 row={'dev':info.st_dev,'ino':info.st_ino,'uid':info.st_uid,'gid':info.st_gid,'mode':info.st_mode&0o7777}
 if stat.S_ISLNK(info.st_mode):
  row.update(kind='link',target=os.readlink(path))
 elif stat.S_ISREG(info.st_mode):
  require(info.st_size<=65536 and info.st_mode&0o7000==0,'fixture-regular-bound')
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   opened=os.fstat(fd);require((opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino),'fixture-read-replacement')
   raw=os.read(fd,65537);require(len(raw)==info.st_size,'fixture-read-size')
  finally:os.close(fd)
  if events:
   require(len(raw)<=4096 and raw.decode().splitlines()[0:1]==[EXPECTED['instance']],'policy-event-instance')
   require(all(v in ('mixel-remote.service:start','mixel-remote.service:stop','mixel-remote:start','mixel-remote:stop')for v in raw.decode().splitlines()[1:]),'unexpected-policy-event')
  row.update(kind='file',sha256=hashlib.sha256(raw).hexdigest())
 else:raise RuntimeError('fixture-not-regular-or-link')
 return row

def fixture_ledger():
 if not os.path.lexists(LEDGER):return {}
 info=LEDGER.lstat()
 require(stat.S_ISREG(info.st_mode)and info.st_uid==FIXTURE_UID and info.st_gid==FIXTURE_GID and info.st_nlink==1 and info.st_mode&0o7777==0o600 and info.st_size<=16384,'fixture-ledger-not-private')
 fd=os.open(LEDGER,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  opened=os.fstat(fd);require((opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino),'fixture-ledger-replacement')
  raw=os.read(fd,16385);require(len(raw)==info.st_size,'fixture-ledger-read-size')
 finally:os.close(fd)
 value=json.loads(raw);require(value.get('instance')==EXPECTED['instance']and set(value)=={'instance','records'}and isinstance(value['records'],dict),'fixture-ledger-instance')
 require(not set(value['records'])-set(FIXTURE_PATHS),'fixture-ledger-paths')
 for name,row in value['records'].items():
  require(isinstance(row,dict)and set(row)=={'active','identity'}and type(row['active'])is bool and isinstance(row['identity'],dict),'fixture-ledger-row')
 return value['records']

def save_fixture_ledger(records):
 require(not set(records)-set(FIXTURE_PATHS),'fixture-ledger-write-paths')
 previous=LEDGER.lstat()if os.path.lexists(LEDGER)else None
 if previous is not None:fixture_ledger()
 temporary=OUT/'owned-fixtures-next.json'
 fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 temporary_owner=os.fstat(fd)
 try:
  raw=(json.dumps({'instance':EXPECTED['instance'],'records':records},sort_keys=True)+'\n').encode();require(len(raw)<=16384,'fixture-ledger-write-bound')
  with os.fdopen(fd,'wb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
  if previous is not None:
   current=LEDGER.lstat();require((current.st_dev,current.st_ino)==(previous.st_dev,previous.st_ino),'fixture-ledger-write-replacement')
  else:require(not os.path.lexists(LEDGER),'fixture-ledger-create-replacement')
  os.replace(temporary,LEDGER)
  directory=os.open(OUT,os.O_RDONLY)
  try:os.fsync(directory)
  finally:os.close(directory)
 finally:
  if os.path.lexists(temporary):
   current=temporary.lstat()
   if(current.st_dev,current.st_ino)==(temporary_owner.st_dev,temporary_owner.st_ino):temporary.unlink()

def create_fixture(name, raw=None, target=None, mode=0o644):
 require(name in FIXTURE_PATHS and (raw is None)!=(target is None),'fixture-create-contract')
 records=fixture_ledger();path=FIXTURE_PATHS[name]
 require(not os.path.lexists(path)and not records.get(name,{}).get('active'),'fixture-already-present')
 require(path.parent.is_dir()and not path.parent.is_symlink()and path.parent.stat().st_uid==FIXTURE_UID,'fixture-parent-owner')
 owned=None
 try:
  if target is not None:
   os.symlink(target,path);owned=path.lstat()
  else:
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   owned=os.fstat(fd)
   with os.fdopen(fd,'wb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),mode)
  records[name]={'active':True,'identity':fixture_identity(path,events=name=='policy-events')}
  save_fixture_ledger(records)
 except BaseException:
  if owned is not None and os.path.lexists(path):
   current=path.lstat()
   if(current.st_dev,current.st_ino)==(owned.st_dev,owned.st_ino):path.unlink()
  raise

def remember_wants():
 path=FIXTURE_PATHS['wants'];require(path.is_symlink()and os.readlink(path)=='/etc/systemd/system/mixel-remote.service','unexpected-owned-wants')
 records=fixture_ledger();require(not records.get('wants',{}).get('active'),'wants-already-owned')
 records['wants']={'active':True,'identity':fixture_identity(path)};save_fixture_ledger(records)

def require_fixture(name):
 records=fixture_ledger();require(name in records and records[name]['active'],'fixture-not-owned')
 actual=fixture_identity(FIXTURE_PATHS[name],events=name=='policy-events');expected=dict(records[name]['identity'])
 if name=='policy-events':actual.pop('sha256');expected.pop('sha256')
 require(actual==expected,'fixture-identity-or-bytes-changed')
 return actual

def fixture_unit_properties():
 raw=run(['systemctl','show','--all','mixel-remote.service','--property=Id,LoadState,FragmentPath,MainPID,ActiveState,SubState,DropInPaths'])
 rows=dict(v.split('=',1)for v in raw.splitlines())
 require(set(rows)=={'Id','LoadState','FragmentPath','MainPID','ActiveState','SubState','DropInPaths'}and rows['Id']=='mixel-remote.service'and rows['DropInPaths']=='','fixture-unit-properties')
 return rows

def cleanup_fixtures():
 records=fixture_ledger()
 # Validate the complete present closure before any stop/unlink operation.
 for name,row in records.items():
  path=FIXTURE_PATHS[name]
  if row['active']and os.path.lexists(path):require_fixture(name)
  elif not row['active']:require(not os.path.lexists(path),'inactive-fixture-replaced')
 def remove(name):
  if name in records and records[name]['active']:
   path=FIXTURE_PATHS[name]
   if os.path.lexists(path):require_fixture(name);path.unlink()
   records[name]['active']=False;save_fixture_ledger(records)
 # An intentional denial never controls package cleanup outside its own phase.
 remove('policy')
 override=records.get('override')
 if override and override['active']and os.path.lexists(FIXTURE_PATHS['override']):
  identity=override['identity']
  if identity['kind']=='file':
   state=fixture_unit_properties()
   require(state['MainPID']=='0'and state['FragmentPath']==str(FIXTURE_PATHS['override'])and state['LoadState']=='loaded'and state['ActiveState']in('active','inactive'),'synthetic-unit-foreign-process')
   if state['ActiveState']=='active':run(['systemctl','stop','mixel-remote.service'])
   if records.get('wants',{}).get('active')and os.path.lexists(FIXTURE_PATHS['wants']):
    require_fixture('wants');run(['systemctl','disable','mixel-remote.service'])
    require(not os.path.lexists(FIXTURE_PATHS['wants']),'owned-wants-disable-failed')
    records['wants']['active']=False;save_fixture_ledger(records)
  else:require(identity['target']=='/dev/null','fixture-mask-target')
 remove('wants');remove('override')
 # Retain the bounded nonsecret policy event log as evidence until job upload.
 if override and override['active']is False:run(['systemctl','daemon-reload'])


def phase_install(candidate):
 command=['dpkg','--install',str(candidate)]
 return run(command)

def phase_remove_purge():
 run(['apt-get','--yes','--no-download','remove','mixel-remote'])
 run(['apt-get','--yes','--no-download','purge','mixel-remote'])
 require(run(['dpkg-query','-W','-f=${db:Status-Status}','mixel-remote'],(1,))=='','phase-package-not-purged')

def phase_absent():
 for name in ('/usr/bin/mixel-remote','/usr/share/mixel-remote','/usr/lib/systemd/system/mixel-remote.service','/etc/systemd/system/mixel-remote.service','/etc/systemd/system/multi-user.target.wants/mixel-remote.service','/usr/sbin/policy-rc.d'):
  require(not os.path.lexists(name),'phase-left-product-or-fixture-'+name)
 require(run(['dpkg','--audit'])=='','phase-dpkg-audit')
 require(run(['systemctl','is-system-running','--wait'])=='running','phase-systemd-unhealthy')

def extra_case(name, action):
 failure=None
 try:action()
 except BaseException as error:failure=error
 try:cleanup_fixtures()
 except BaseException as cleanup_error:
  try:(OUT/(name+'-cleanup-failed.json')).write_text(json.dumps({'errorType':type(cleanup_error).__name__,'primaryFailure':type(failure).__name__ if failure else None})+'\n')
  except OSError:pass
  if failure is None:raise
 if failure is not None:raise failure
 (OUT/(name+'-PASS.json')).write_text(json.dumps({'status':'PASS-actual-'+name,'DEBSHA256':EXPECTED['DEBSHA256'],'instance':EXPECTED['instance'],'normalReleaseQualified':False,'nativeQualified':False,'taskComplete':False})+'\n')

def extra_lifecycle_cases(candidate):
 phase_absent()
 def upgrade():
  phase_install(candidate);first=live();phase_install(candidate);second=live()
  require((first['pid'],first['birth'])!=(second['pid'],second['birth']),'upgrade-did-not-replace-owned-main');gone(first,False)
  phase_remove_purge();run(['systemctl','daemon-reload']);inactive(second);phase_absent()
 extra_case('normal-upgrade',upgrade)
 def custom():
  raw=('[Unit]\nDescription=Mixel isolated QA administrator '+EXPECTED['instance']+'\n[Service]\nType=oneshot\nExecStart=/usr/bin/true\nRemainAfterExit=yes\n[Install]\nWantedBy=multi-user.target\n').encode()
  create_fixture('override',raw=raw);run(['systemctl','daemon-reload'])
  run(['systemctl','enable','mixel-remote.service']);remember_wants();run(['systemctl','start','mixel-remote.service'])
  initial=fixture_unit_properties();require(initial['MainPID']=='0'and initial['ActiveState']=='active'and initial['SubState']=='exited','synthetic-override-not-active')
  for operation in ('install','reconfigure','remove-purge'):
   if operation=='remove-purge':phase_remove_purge()
   else:phase_install(candidate)
   require_fixture('override');require_fixture('wants')
   require(fixture_unit_properties()==initial,'administrator-override-state-changed')
   require(not os.path.lexists('/usr/lib/systemd/system/mixel-remote.service'),'foreign-override-vendor-unit-written')
 extra_case('administrator-override',custom);phase_absent()
 def mask():
  create_fixture('override',target='/dev/null');run(['systemctl','daemon-reload'])
  before=fixture_unit_properties();require(before['LoadState']=='masked'and before['MainPID']=='0','mask-not-loaded')
  for operation in ('install','reconfigure','remove-purge'):
   if operation=='remove-purge':phase_remove_purge()
   else:phase_install(candidate)
   require_fixture('override');require(fixture_unit_properties()==before,'administrator-mask-changed')
   require(not os.path.lexists('/usr/lib/systemd/system/mixel-remote.service'),'mask-vendor-unit-written')
 extra_case('administrator-mask',mask);phase_absent()
 def policy():
  create_fixture('policy-events',raw=(EXPECTED['instance']+'\n').encode(),mode=0o600)
  log=str(FIXTURE_PATHS['policy-events']);require("'"not in log and '\n'not in log,'policy-log-path')
  raw=("#!/bin/sh\ncase \"${1-}:${2-}\" in\n mixel-remote.service:start|mixel-remote.service:stop|mixel-remote:start|mixel-remote:stop)\n  printf '%s:%s\\n' \"$1\" \"$2\" >> '"+log+"'\n  exit 101;;\n *) exit 102;;\nesac\n").encode()
  create_fixture('policy',raw=raw,mode=0o700)
  phase_install(candidate);require_fixture('policy');require_fixture('policy-events')
  state=fixture_unit_properties();require(state['LoadState']=='loaded'and state['MainPID']=='0'and state['ActiveState']=='inactive','policy-did-not-deny-real-start')
  require(sha('/usr/share/mixel-remote/mixel-remote')==EXPECTED['mainSHA256'],'policy-installed-wrong-main')
  phase_remove_purge();require_fixture('policy');require_fixture('policy-events')
  events=FIXTURE_PATHS['policy-events'].read_text().splitlines()[1:]
  require(any(v.endswith(':start')for v in events)and any(v.endswith(':stop')for v in events),'missing-actual-policy101-start-stop')
  run(['dpkg-query','-S','/usr/bin/deb-systemd-invoke'])
 extra_case('policy101',policy);phase_absent()
 phase_install(candidate);last=live();phase_remove_purge();run(['systemctl','daemon-reload']);inactive(last);phase_absent()
 require_fixture('policy-events')
 return {'normalUpgrade':True,'administratorOverridePreserved':True,'administratorMaskPreserved':True,'realPolicy101StartStopDenied':True,'normalServiceResumedAfterPolicy':True}


def reject_baseline_avoidable_warnings():
 for number in range(1,seq+1):
  metadata=json.loads((OUT/f'{number:03d}.json').read_text());argv=metadata['argv']
  if argv[0]in('dpkg','apt-get'):
   text=(OUT/f'{number:03d}.stdout').read_text()+(OUT/f'{number:03d}.stderr').read_text()
   require('Failed to stop mixel-remote.service: Unit mixel-remote.service not loaded.'not in text,'avoidable-first-install-stop-warning')
   require("rm: cannot remove '/etc/systemd/system/mixel-remote.service'"not in text,'avoidable-missing-override-remove-warning')

if CLEANUP_MODE:
 require(os.geteuid()==0,'cleanup-fixtures-root-required')
 cleanup_fixtures();sys.exit(0)

try:
 require(os.geteuid()==0 and os.uname().machine=='x86_64','guest-root-amd64-required')
 release=dict(v.split('=',1)for v in P('/etc/os-release').read_text().splitlines()if'='in v)
 require(release['ID'].strip('"')=='ubuntu'and release['VERSION_ID'].strip('"')=='22.04','wrong-guest-release')
 require(run(['dpkg','--print-architecture'])=='amd64'and os.path.basename(os.path.realpath('/proc/1/exe'))=='systemd','real-amd64-PID1-systemd-required')
 require(run(['systemctl','is-system-running','--wait'])=='running','guest-systemd-not-healthy')
 require(not P('/usr/sbin/policy-rc.d').exists(),'policy-rc.d-must-not-mask-real-service')
 for p in ['/usr/bin/mixel-remote','/usr/lib/systemd/system/mixel-remote.service','/etc/systemd/system/mixel-remote.service','/usr/share/mixel-remote','/root/.config/mixel-remote']:
  require(not os.path.lexists(p),'guest-not-fresh-'+p)
 require(run(['dpkg-query','-W','-f=${db:Status-Status}','mixel-remote'],(1,))=='','guest-package-present')
 candidate=P('/mnt/mixel-candidate/candidate.deb');require(sha(candidate)==EXPECTED['DEBSHA256']=='0666ca6f87008654c8e06d17a9555ea6e207d4fec3491b10abee076308a9bd58','wrong-qualified-candidate')
 sentinel=P('/var/lib/mixel-systemd-qa-unrelated-sentinel');sentinel.write_text(EXPECTED['instance']+'\n');sentinelhash=sha(sentinel)
 run(['dpkg','--install',str(candidate)])
 require(run(['dpkg-query','-W','-f=${Status}','mixel-remote'])=='install ok installed','package-not-configured')
 require(os.readlink('/usr/bin/mixel-remote')=='/usr/share/mixel-remote/mixel-remote','wrong-postinst-alias')
 main=P('/usr/share/mixel-remote/mixel-remote');require(sha(main)==EXPECTED['mainSHA256']and main.stat().st_uid==main.stat().st_gid==0 and main.stat().st_mode&0o777==0o755,'installed-main-hash-owner-mode')
 require(sha('/usr/share/mixel-remote/files/systemd/mixel-remote.service')==EXPECTED['packagedUnitSHA256'],'wrong-packaged-unit')
 require(P('/usr/bin/pkill').is_file()and sha('/usr/lib/systemd/system/mixel-remote.service')==EXPECTED['installedUnitSHA256'],'wrong-exact-postinst-pkill-projection')
 require(run(['systemctl','is-enabled','mixel-remote.service'])=='enabled','not-enabled-after-postinst')
 first=live()
 for _ in range(3):time.sleep(10);require(live()==first,'active-30-second-proof-or-restart-count-changed')
 run(['systemctl','restart','mixel-remote.service']);second=live();require((second['pid'],second['birth'])!=(first['pid'],first['birth']),'restart-did-not-create-new-owner');gone(first,False)
 run(['systemctl','stop','mixel-remote.service']);inactive(second)
 run(['systemctl','start','mixel-remote.service']);third=live()
 run(['systemctl','disable','--now','mixel-remote.service']);inactive(third);require(run(['systemctl','is-enabled','mixel-remote.service'],(1,))=='disabled','disable-not-observed')
 run(['systemctl','enable','--now','mixel-remote.service']);fourth=live();require(run(['systemctl','is-enabled','mixel-remote.service'])=='enabled','enable-not-observed')
 config=P('/root/.config/mixel-remote');config.mkdir(parents=True,exist_ok=True);(config/'qa-synthetic').write_text('synthetic QA only\n')
 run(['apt-get','--yes','--no-download','remove','mixel-remote']);run(['systemctl','daemon-reload']);inactive(fourth)
 require(not os.path.lexists('/usr/bin/mixel-remote')and not P('/usr/lib/systemd/system/mixel-remote.service').exists()and not P('/etc/systemd/system/mixel-remote.service').exists(),'remove-left-alias-or-system-unit')
 require(not os.path.lexists('/etc/systemd/system/multi-user.target.wants/mixel-remote.service'),'remove-left-enabled-wants-symlink')
 require(run(['systemctl','is-enabled','mixel-remote.service'],(1,4))in('not-found','disabled',''),'remove-left-enabled-unit')
 run(['apt-get','--yes','--no-download','purge','mixel-remote']);run(['systemctl','daemon-reload'])
 require(run(['dpkg-query','-W','-f=${db:Status-Status}','mixel-remote'],(1,))==''and not config.exists(),'purge-left-package-or-root-product-config')
 require(sha(sentinel)==sentinelhash,'unrelated-sentinel-changed')
 require(run(['dpkg','--audit'])=='','final-dpkg-audit-findings');run(['journalctl','-b','-u','mixel-remote.service','--no-pager'])
 baselineCommands=seq;require(baselineCommands==31,'baseline31-command-phase-changed');reject_baseline_avoidable_warnings()
 extraCases=extra_lifecycle_cases(candidate)
 result={'status':'PASS-real-systemd-installer-service-lifecycle','DEBSHA256':EXPECTED['DEBSHA256'],'mainSHA256':EXPECTED['mainSHA256'],'identity':release,'commands':seq,'baselineCommands':baselineCommands,'baselineDEBSHA256':BASELINE_DEB_SHA,'privateExtraCases':extraCases,'binarySource':'070da25001f97fabf67d08401e2cb15bde37a33b','packagingSource':'73e7c3decf800ba5db5867c135bff5ef42080b83','actualPID1Systemd':True,'rootMainPIDExecutableProven':True,'stable30Seconds':True,'restartStopStartDisableEnableRemovePurgeProven':True,'unrelatedSentinelPreserved':True,'serviceOwners':[first,second,third,fourth],'nativeQualified':False,'normalReleaseQualified':False,'publication':False,'taskComplete':False}
 (OUT/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
except Exception as error:
 (OUT/'FAILED.json').write_text(json.dumps({'error':str(error),'commands':seq,'serviceAccepted':False,'taskComplete':False})+'\n')
 subprocess.run(['journalctl','-b','-u','mixel-remote.service','--no-pager'],stdout=(OUT/'failure-journal.log').open('wb'),stderr=subprocess.STDOUT)
 raise
