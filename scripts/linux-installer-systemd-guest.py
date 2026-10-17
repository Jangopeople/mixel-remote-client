#!/usr/bin/env python3
"""FUTURE disposable Ubuntu22.04 systemd guest only; never run during preparation."""
import hashlib,json,os,pathlib,subprocess,time
P=pathlib.Path
OUT=P('/var/tmp/mixel-systemd-qa');OUT.mkdir(mode=0o700,exist_ok=False)
seq=0
EXPECTED=json.loads(P('/mnt/mixel-candidate/expected.json').read_text())
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
 candidate=P('/mnt/mixel-candidate/candidate.deb');require(sha(candidate)==EXPECTED['DEBSHA256']=='993f516763f85cd749faff8752e52ad5ac7be40b21e78d5878ec3a40422abc47','wrong-qualified-candidate')
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
 result={'status':'PASS-real-systemd-installer-service-lifecycle','DEBSHA256':EXPECTED['DEBSHA256'],'mainSHA256':EXPECTED['mainSHA256'],'identity':release,'commands':seq,'actualPID1Systemd':True,'rootMainPIDExecutableProven':True,'stable30Seconds':True,'restartStopStartDisableEnableRemovePurgeProven':True,'unrelatedSentinelPreserved':True,'serviceOwners':[first,second,third,fourth],'nativeQualified':False,'normalReleaseQualified':False,'publication':False,'taskComplete':False}
 (OUT/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
except Exception as error:
 (OUT/'FAILED.json').write_text(json.dumps({'error':str(error),'commands':seq,'serviceAccepted':False,'taskComplete':False})+'\n')
 subprocess.run(['journalctl','-b','-u','mixel-remote.service','--no-pager'],stdout=(OUT/'failure-journal.log').open('wb'),stderr=subprocess.STDOUT)
 raise
