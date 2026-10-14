#!/usr/bin/env python3
"""Run generated updater/CLI and transaction fixtures without an installed app or privileges."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
import argparse
import hashlib
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("RDREPO", ROOT.parent / "rustdesk")).resolve()
PROFILE_SOURCES = [SOURCE]
spec = importlib.util.spec_from_file_location("macos_update", ROOT / "patch-macos-update.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
CRITICAL = ("Contents/MacOS/Mixel-Remote", "Contents/MacOS/service",
            "Contents/Frameworks/liblibrustdesk.dylib",
            "Contents/Frameworks/FlutterMacOS.framework/FlutterMacOS")


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=45, **kwargs)



def make_source(repo, source, newline):
    # Validate and invert only the complete late launch layer on a private copy.
    spec = importlib.util.spec_from_file_location("updater_launch_projection", ROOT / "patch-support-macos-gui-launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    with launch.previous(source) as source:
        # Read only exact whole-file profiles; no synthetic anchor-only acceptance.
        patch.apply(source, restore=True, check=True)
        raw = [patch.safe_target(source, f, optional=i == 3).read_bytes() if (source / f).exists() else None for i, f in enumerate(patch.FILES)]
        canonical = [patch.canonical(d)[0] if d is not None else None for d in raw]
        original = patch.project(*canonical, restore=True)
        for relative, data in zip(patch.FILES, original):
            if data is not None:
                path = repo / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data.replace(b'\n', newline))


def snapshot(repo):
    return {p.relative_to(repo).as_posix(): ('link:' + os.readlink(p)) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest()
            for p in repo.rglob('*') if p.is_file() or p.is_symlink()}

MOCK_TOOL = r'''#!/usr/bin/env python3
import json,os,plistlib,shutil,sys
from pathlib import Path
name=Path(sys.argv[0]).name;args=sys.argv[1:];mode=os.environ['CASE'];base=Path(os.environ['FIXTURE_ROOT']);log=base/'actions.jsonl'
def record():
 with log.open('a') as f:f.write(json.dumps({'tool':name,'args':args})+'\n')
record()
if name=='id': print(501);sys.exit(0)
if name=='stat':
 s=Path(args[-1]).stat();print(str(s.st_dev)+':'+str(s.st_ino));sys.exit(0)
if name=='codesign':
 app=Path(args[-1]);signed=app/'signed';marker=(app/'Contents/MacOS/Mixel-Remote').read_text() if app.exists() else ''
 if '-d' in args:print('TeamIdentifier=5277F8NDH4',file=sys.stderr);sys.exit(0)
 if not signed.is_file() or signed.read_text()!='yes':sys.exit(1)
 if mode=='stage-signature' and app.name=='new.app':sys.exit(1)
 if mode in ('target-signature','rollback-failed') and app.name=='Mixel-Remote.app' and marker=='new':sys.exit(1)
 sys.exit(0)
if name=='PlistBuddy':
 print(plistlib.loads(Path(args[-1]).read_bytes())[args[1].split(':')[-1].strip()]);sys.exit(0)
if name=='ditto':
 if mode=='copy-failed':Path(args[-1]).mkdir();(Path(args[-1])/'partial').write_text('partial');sys.exit(1)
 shutil.copytree(args[0],args[1],symlinks=True)
 if mode=='stage-hash':(Path(args[1])/'Contents/MacOS/service').write_text('tampered')
 sys.exit(0)
if name=='chown':sys.exit(0)
if name=='plutil':
 try:plistlib.loads(Path(args[-1]).read_bytes())
 except Exception:sys.exit(1)
 sys.exit(0)
if name=='cp':
 if mode in ('restore-daemon-failed','restore-agent-failed') and Path(args[-2]).name==('daemon.old' if mode=='restore-daemon-failed' else 'agent.old'):sys.exit(1)
 shutil.copy2(args[-2],args[-1]);sys.exit(0)
if name=='pgrep':
 if mode in ('foreign-same-name','target-running'):print(424242);sys.exit(0)
 if mode=='updater-only':print(999999);sys.exit(0)
 if mode=='duplicate-caller':print('999999\n999999');sys.exit(0)
 if mode=='malformed-pid':print('999999\nnot-a-pid');sys.exit(0)
 if mode=='pgrep-empty-success':sys.exit(0)
 if mode=='pgrep-inconsistent':print(424242);sys.exit(1)
 if mode=='pgrep-denied':print('permission denied',file=sys.stderr);sys.exit(2)
 if mode=='pgrep-warning':print(999999);print('observation uncertain',file=sys.stderr);sys.exit(0)
 sys.exit(1)
if name=='kill':sys.exit(0)
if name=='launchctl':
 target=base/'Applications/Mixel-Remote.app/Contents/MacOS/Mixel-Remote'
 if mode in ('service-failed','restore-daemon-failed','restore-agent-failed') and args[0]=='kickstart' and target.read_text()=='new':sys.exit(1)
 sys.exit(0)
if name=='mv':
 source,target=map(Path,args)
 if mode=='swap-failed' and source.name=='new.app':sys.exit(1)
 if mode=='rollback-failed' and source.name=='old.app':sys.exit(1)
 shutil.move(str(source),str(target))
 if (mode=='interrupt-old-move' and target.name=='old.app') or (mode=='interrupt-new-move' and source.name=='new.app'):
  import signal
  os.kill(os.getppid(),signal.SIGTERM)
 if mode=='target-hash' and source.name=='new.app':(target/'Contents/MacOS/service').write_text('tampered')
 sys.exit(0)
raise Exception('Unapproved fixture tool')
'''


class TransactionFixture:
    """Replace only absolute OS tool/path seams in the unchanged shell body."""
    def __init__(self, root):
        root = root.resolve()
        self.root = root
        self.source = root / "candidate's spaced.app"
        self.target = root / "Applications/Mixel-Remote.app"
        self.tools = root / "tools"
        self.tools.mkdir()
        (root / "Applications").mkdir()
        (root / "Library/LaunchAgents").mkdir(parents=True)
        (root / "Library/LaunchDaemons").mkdir(parents=True)
        for app, marker in ((self.source, "new"), (self.target, "old")):
            for name in CRITICAL:
                file = app / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(marker)
                file.chmod(0o755)
            (app / "signed").write_text("yes")
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": "ch.mixel.remote", "CFBundleExecutable": "Mixel-Remote"}))
        self.plists = [root / "Library/LaunchDaemons/ch.mixel.remote_service.plist",
                       root / "Library/LaunchAgents/ch.mixel.remote_server.plist"]
        for index, plist in enumerate(self.plists):
            plist.write_bytes(plistlib.dumps({"Label": "old-" + str(index)}))
        self.old_plists = [p.read_bytes() for p in self.plists]
        for tool in ("/usr/bin/id", "/usr/bin/stat", "/usr/bin/codesign", "/usr/libexec/PlistBuddy",
                     "/usr/bin/ditto", "/usr/sbin/chown", "/usr/bin/plutil", "/usr/bin/pgrep",
                     "/bin/launchctl", "/bin/mv", "/bin/cp"):
            path = self.tools / Path(tool).name
            path.write_text(MOCK_TOOL)
            path.chmod(0o755)
        self.shell = root / "updater-fixture.sh"
        self.write_shell((ROOT / "macos-update-bundle.sh").read_text())

    def write_shell(self, shell):
        shell = shell.replace('/Applications', str(self.root / 'Applications')).replace('/Library', str(self.root / 'Library'))
        for tool in ("/usr/bin/id", "/usr/bin/stat", "/usr/bin/codesign", "/usr/libexec/PlistBuddy",
                     "/usr/bin/ditto", "/usr/sbin/chown", "/usr/bin/plutil", "/usr/bin/pgrep",
                     "/bin/launchctl", "/bin/mv", "/bin/cp"):
            self.assert_once_or_used(shell, tool)
            shell = shell.replace(tool, str(self.tools / Path(tool).name))
        self.shell.write_text(shell)

    @staticmethod
    def assert_once_or_used(shell, token):
        if token not in shell:
            raise AssertionError("Explicit updater tool seam drifted: " + token)

    def execute(self, mode="success", service=True, source=None, bad_plist=False):
        env = {**os.environ, "CASE": mode, "FIXTURE_ROOT": str(self.root)}
        bodies = [plistlib.dumps({"Label": "ch.mixel.remote_service"}).decode(),
                  plistlib.dumps({"Label": "ch.mixel.remote_server"}).decode()]
        if bad_plist:
            bodies[1] = "not a plist"
        return run(["/bin/sh", str(self.shell), str(source or self.source), "synthetic-user", "999999",
                    "Mixel-Remote", "ch.mixel.remote", "1" if service else "0", *bodies], env=env)

    def actions(self):
        path = self.root / "actions.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def target_markers(self):
        return [(self.target / name).read_text() for name in CRITICAL]


class UpdaterTests(unittest.TestCase):
    def test_real_shell_transaction_success_and_copy_only(self):
        for service in (False, True):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as temporary:
                fixture = TransactionFixture(Path(temporary))
                result = fixture.execute(service=service)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(fixture.target_markers(), ["new"] * 4)
                self.assertFalse(list((fixture.root / "Applications").glob('.mixel-update.*')))
                tools = [r['tool'] for r in fixture.actions()]
                if service:
                    self.assertIn('launchctl', tools)
                    self.assertNotEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                else:
                    self.assertNotIn('launchctl', tools)
                    self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)

    def test_same_name_running_process_is_refused_without_signaling_or_replacement(self):
        for mode in ('foreign-same-name', 'target-running'):
            for service in (False, True):
                with self.subTest(mode=mode, service=service), tempfile.TemporaryDirectory() as temporary:
                    fixture = TransactionFixture(Path(temporary))
                    result = fixture.execute(mode, service=service)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('quit the installed app normally', result.stderr)
                    self.assertEqual(fixture.target_markers(), ['old'] * 4)
                    self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                    self.assertFalse(any(r['tool'] in ('kill', 'mv') for r in fixture.actions()))
                    self.assertFalse(list((fixture.root / 'Applications').glob('.mixel-update.*')))
        print('PASS: target or foreign same-name processes are never signaled; exact installed bundle/plists survive refusal in both service modes')

    def test_updater_caller_alone_can_complete_without_any_process_signal(self):
        for service in (False, True):
            with self.subTest(service=service), tempfile.TemporaryDirectory() as temporary:
                fixture = TransactionFixture(Path(temporary))
                result = fixture.execute('updater-only', service=service)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fixture.target_markers(), ['new'] * 4)
                self.assertFalse(any(r['tool'] == 'kill' for r in fixture.actions()))
        print('PASS: the synchronous updater caller remains excluded; ordinary copy and managed service updates complete')

    def test_process_observation_errors_or_malformed_pids_refuse_and_restore(self):
        for mode in ('duplicate-caller', 'malformed-pid', 'pgrep-empty-success',
                     'pgrep-inconsistent', 'pgrep-denied', 'pgrep-warning'):
            for service in (False, True):
                with self.subTest(mode=mode, service=service), tempfile.TemporaryDirectory() as temporary:
                    fixture = TransactionFixture(Path(temporary))
                    result = fixture.execute(mode, service=service)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(fixture.target_markers(), ['old'] * 4)
                    self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                    self.assertFalse(any(r['tool'] in ('kill', 'mv') for r in fixture.actions()))
                    self.assertFalse(list((fixture.root / 'Applications').glob('.mixel-update.*')))
        print('PASS: duplicate/malformed/empty/inconsistent/error/warning process observations fail closed and restore original state')

    def test_original_same_name_kill_attempt_reproduced_only_through_mock_signal_tool(self):
        current = (ROOT / 'macos-update-bundle.sh').read_text()
        first = current.index('# A process name supplies no executable identity')
        last = current.index('# Register each move obligation', first)
        previous = current[:first] + '''pids=$(/usr/bin/pgrep -x "$app_name" | /usr/bin/grep -vx "$cur_pid" || true)
if [ -n "$pids" ]; then printf '%s\\n' "$pids" | /usr/bin/xargs /bin/kill -9 || true; fi
''' + current[last:]
        self.assertEqual(hashlib.sha256(previous.encode()).hexdigest(), 'd0dd94eb484f86ca37f302a625d496c876e4cedd21c149749d51590702eff1cc')
        with tempfile.TemporaryDirectory() as temporary:
            fixture = TransactionFixture(Path(temporary))
            signal_tool = fixture.tools / 'kill'
            signal_tool.write_text(MOCK_TOOL); signal_tool.chmod(0o755)
            fixture.write_shell(previous)
            # This exact historical projection can only execute the owned mock,
            # which records argv and never invokes any OS signal API.
            fixture.shell.write_text(fixture.shell.read_text().replace('/bin/kill', str(signal_tool)))
            result = fixture.execute('foreign-same-name', service=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([r['args'] for r in fixture.actions() if r['tool'] == 'kill'], [['-9', '424242']])
        print('PASS: exact historical updater attempts kill -9 on a same-name foreign PID; regression uses only a logging mock and sends zero real signals')

    def test_stage_failures_never_stop_or_replace_installed_app(self):
        for mode in ('copy-failed', 'stage-signature', 'stage-hash', 'bad-plist'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                fixture = TransactionFixture(Path(temporary))
                result = fixture.execute(mode=mode, bad_plist=mode == 'bad-plist')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.target_markers(), ["old"] * 4)
                self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                self.assertFalse(any(r['tool'] in ('launchctl', 'pgrep', 'mv') for r in fixture.actions()))
                self.assertFalse(list((fixture.root / "Applications").glob('.mixel-update.*')))

    def test_post_stop_failures_restore_exact_old_bundle_and_plists(self):
        for mode in ('swap-failed', 'target-signature', 'target-hash', 'service-failed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                fixture = TransactionFixture(Path(temporary))
                result = fixture.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.target_markers(), ["old"] * 4)
                self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                self.assertFalse(list((fixture.root / "Applications").glob('.mixel-update.*')))

    def test_failed_rollback_is_fatal_and_retains_private_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = TransactionFixture(Path(temporary))
            result = fixture.execute('rollback-failed')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('rollback requires recovery', result.stderr)
            retained = list((fixture.root / 'Applications').glob('.mixel-update.*'))
            self.assertEqual(len(retained), 1)
            self.assertEqual((retained[0] / 'old.app' / CRITICAL[0]).read_text(), 'old')

    def test_alias_overlap_and_symbolic_source_refused_before_any_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = TransactionFixture(Path(temporary))
            nested = fixture.target / 'nested.app'
            nested.mkdir()
            alias = fixture.root / 'alias.app'
            alias.symlink_to(fixture.target, target_is_directory=True)
            for source in (fixture.target, nested, fixture.root / 'Applications', alias):
                result = fixture.execute(source=source)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.target_markers(), ["old"] * 4)
                self.assertEqual(fixture.actions(), [])

    def test_patch_idempotency_newlines_and_atomic_anchor_rejection(self):
        for source in PROFILE_SOURCES:
            for newline in (b'\n', b'\r\n'):
                with self.subTest(source=source, newline=newline), tempfile.TemporaryDirectory() as temporary:
                    repo = Path(temporary).resolve() / 'repo'
                    make_source(repo, source, newline)
                    original = snapshot(repo)
                    patch.apply(repo, check=True)
                    self.assertEqual(snapshot(repo), original)
                    patch.patch(repo)
                    first = snapshot(repo)
                    patch.patch(repo)
                    self.assertEqual(snapshot(repo), first)
                    patch.apply(repo, restore=True, check=True)
                    self.assertEqual(snapshot(repo), first)
                    patch.apply(repo, restore=True)
                    self.assertEqual(snapshot(repo), original)
                    patch.patch(repo)
                    core = repo / 'src/core_main.rs'
                    core.write_bytes(core.read_bytes() + patch.FIXED_CLI.encode().replace(b'\n', newline))
                    before = snapshot(repo)
                    with self.assertRaises(RuntimeError):
                        patch.patch(repo)
                    self.assertEqual(snapshot(repo), before)

    def test_actual_embedded_applescript_compiles_without_running(self):
        if not shutil.which('osacompile'):
            self.skipTest('AppleScript compiler is macOS-only; shell/Rust fixtures run on all hosts')
        with tempfile.TemporaryDirectory() as temporary:
            result = run(['/usr/bin/osacompile', '-o', str(Path(temporary) / 'update.scpt'), str(ROOT / 'macos-update.scpt')])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resource_drift_and_foreign_shell_rejected_without_source_write(self):
        for case in ('script-drift', 'script-missing', 'foreign-shell', 'shell-symlink', 'owned-shell-missing'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary).resolve() / 'repo'
                make_source(repo, PROFILE_SOURCES[0], b'\n')
                resources = repo / 'src/platform/privileges_scripts'
                script = resources / 'update.scpt'
                if case == 'script-drift': script.write_bytes(script.read_bytes() + b'changed resource')
                if case == 'script-missing': script.unlink()
                if case == 'foreign-shell': (resources / 'update.sh').write_text('unrelated shell')
                if case == 'shell-symlink':
                    foreign = repo / 'foreign'
                    foreign.write_bytes((ROOT / 'macos-update-bundle.sh').read_bytes())
                    (resources / 'update.sh').symlink_to(foreign)
                if case == 'owned-shell-missing':
                    patch.patch(repo)
                    (resources / 'update.sh').unlink()
                before = snapshot(repo)
                for restore in (False, True):
                    with self.assertRaises(RuntimeError): patch.apply(repo, restore=restore, check=True)
                    self.assertEqual(snapshot(repo), before)

    def test_whole_profile_ownership_and_drift_refusals(self):
        cases = ('drift', 'mixed-newline', 'partial', 'hardlink', 'symlink', 'directory', 'oversized', 'reparse-file', 'reparse-parent', 'reparse-root')
        refusals = 0
        for source in PROFILE_SOURCES:
            for newline in (b'\n', b'\r\n'):
                for case in cases:
                    with self.subTest(source=source, newline=newline, case=case), tempfile.TemporaryDirectory() as temporary:
                        repo = Path(temporary).resolve() / 'repo'
                        make_source(repo, source, newline)
                        leaf = repo / 'src/platform/macos.rs'
                        context = mock.patch.object(patch, 'ROOT', ROOT)
                        if case == 'drift': leaf.write_bytes(leaf.read_bytes() + newline + b'// unrelated')
                        elif case == 'mixed-newline': leaf.write_bytes(leaf.read_bytes() + (b'\r\n' if newline == b'\n' else b'\n'))
                        elif case == 'partial':
                            m = patch.canonical(leaf.read_bytes())[0]
                            leaf.write_bytes(patch.transform(m, [(patch.ORIGINAL_DAEMON, patch.FIXED_DAEMON), (patch.ORIGINAL_UPDATER, patch.FIXED_UPDATER)]).replace(b'\n', newline))
                        elif case == 'hardlink':
                            os.link(leaf, repo / 'outside-hardlink')
                        elif case == 'symlink':
                            foreign = repo / 'outside-source';leaf.rename(foreign);leaf.symlink_to(foreign)
                        elif case == 'directory': leaf.unlink();leaf.mkdir()
                        elif case == 'oversized': leaf.write_bytes(b'x' * 1000001)
                        elif case.startswith('reparse'):
                            poisoned = leaf if case == 'reparse-file' else (repo / 'src' if case == 'reparse-parent' else repo)
                            original_lstat = Path.lstat
                            def fake_lstat(path):
                                entry = original_lstat(path)
                                if path == poisoned:
                                    return SimpleNamespace(st_mode=entry.st_mode, st_nlink=entry.st_nlink, st_size=entry.st_size, st_file_attributes=0x400)
                                return entry
                            context = mock.patch.object(patch.Path, 'lstat', fake_lstat)
                        before = snapshot(repo)
                        with context:
                            for restore in (False, True):
                                with self.assertRaises(RuntimeError): patch.apply(repo, restore=restore)
                                refusals += 1
                        self.assertEqual(snapshot(repo), before)
        print('PASS:', refusals, 'whole-source ownership/drift refusals with zero file mutations; Windows reparse attributes modeled')

    def test_owned_templates_normalize_only_homogeneous_newlines(self):
        expected = None
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                repo = base / 'repo'; make_source(repo, PROFILE_SOURCES[0], b'\n')
                resources = base / 'templates'; resources.mkdir()
                for name in patch.TEMPLATE_SHA:
                    (resources / name).write_bytes(patch.canonical((ROOT / name).read_bytes())[0].replace(b'\n', newline))
                with mock.patch.object(patch, 'ROOT', resources):
                    patch.apply(repo)
                self.assertNotIn(b'\r', (repo / patch.FILES[3]).read_bytes())
                current = snapshot(repo)
                if expected is None: expected = current
                self.assertEqual(current, expected)
                for name in patch.TEMPLATE_SHA:
                    original = (resources / name).read_bytes()
                    for fault in ('mixed', 'tampered'):
                        (resources / name).write_bytes(original + (b'\r\n' if newline == b'\n' else b'\n') if fault == 'mixed' else original + b'changed')
                        before = snapshot(repo)
                        with mock.patch.object(patch, 'ROOT', resources):
                            with self.assertRaises(RuntimeError): patch.apply(repo, restore=True)
                        self.assertEqual(snapshot(repo), before)
                        (resources / name).write_bytes(original)
        print('PASS: exact owned template LF/CRLF canonical embedding; eight mixed/tampered refusals preserve all target bytes')

    def test_interrupted_successful_moves_restore_the_owned_original(self):
        for service in (False, True):
            for mode in ('interrupt-old-move', 'interrupt-new-move'):
                with self.subTest(service=service, mode=mode), tempfile.TemporaryDirectory() as temporary:
                    fixture = TransactionFixture(Path(temporary))
                    result = fixture.execute(mode, service=service)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(fixture.target_markers(), ['old'] * 4, result.stdout + result.stderr)
                    self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                    self.assertFalse(list((fixture.root / 'Applications').glob('.mixel-update.*')))
        print('PASS: actual successful old/new moves interrupted by SIGTERM restore exact original bundle/plists in both service modes')

    def test_plist_restore_attempts_are_independent_and_partial_state_is_not_started(self):
        for mode in ('restore-daemon-failed', 'restore-agent-failed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                fixture = TransactionFixture(Path(temporary))
                result = fixture.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.target_markers(), ['old'] * 4)
                self.assertIn('rollback requires recovery', result.stderr)
                calls = fixture.actions()
                restores = [r for r in calls if r['tool'] == 'cp' and Path(r['args'][-2]).name.endswith('.old')]
                self.assertEqual([Path(r['args'][-2]).name for r in restores], ['daemon.old', 'agent.old'])
                last_restore = max(i for i,r in enumerate(calls) if r in restores)
                self.assertFalse(any(r['tool'] == 'launchctl' for r in calls[last_restore+1:]))
                self.assertEqual(len(list((fixture.root / 'Applications').glob('.mixel-update.*'))), 1)
        print('PASS: both original plists independently attempted; a partial restore never restarts services and retains private recovery backup')

    def test_generated_rust_and_cli_real_subprocess_failure_and_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            candidate = base / 'candidate.app/Contents/MacOS'
            target = base / 'Applications/Mixel-Remote.app'
            candidate.mkdir(parents=True)
            target.mkdir(parents=True)
            tool = base / 'mock-os-command'
            tool.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
command=os.environ['FIXTURE_COMMAND']
with Path(os.environ['FIXTURE_ACTIONS']).open('a') as stream:stream.write(command+'\\n')
if command=='osascript' and os.environ.get('FIXTURE_ARGV'):
 Path(os.environ['FIXTURE_ARGV']).write_text(json.dumps(sys.argv[1:]))
sys.exit(1 if (os.environ['CASE'],command) in (('cancel','osascript'),('copy-failed','osascript'),('open-failed','open')) else 0)
''')
            tool.chmod(0o755)
            provider = r'''#![allow(dead_code)]
#[allow(unused_imports)] use std::{path::{Path,PathBuf},process::ExitStatus};
type ResultType<T> = Result<T,std::io::Error>;
macro_rules! anyhow { ($($a:tt)*) => {std::io::Error::new(std::io::ErrorKind::Other,format!($($a)*))}; }
macro_rules! bail { ($($a:tt)*) => {return Err(anyhow!($($a)*).into())}; }
#[macro_export] macro_rules! fixture_log { ($($a:tt)*) => { eprintln!($($a)*) }; }
mod log { #[allow(unused_imports)] pub use crate::fixture_log as error; #[allow(unused_imports)] pub use crate::fixture_log as info; #[allow(unused_imports)] pub use crate::fixture_log as warn; }
fn get_active_username()->String{"synthetic-user".to_owned()}
fn get_app_name()->String{"Mixel-Remote".to_owned()}
fn get_full_name()->String{"ch.mixel.remote".to_owned()}
fn get_bundle_id()->Option<String>{Some("ch.mixel.remote".to_owned())}
fn is_installed_daemon(_prompt:bool)->bool{std::env::var("SERVICE").unwrap()=="1"}
mod hbb_common {pub mod config {pub fn option2bool(_key:&str,value:&str)->bool{value=="Y"}}}
mod ui_interface {pub fn get_option(_key:&str)->String{std::env::var("STOPPED").unwrap_or_default()}}
struct Command(std::process::Command);
impl Command {
 fn new(name:&str)->Self {
  let logical=if name=="/usr/bin/osascript" || name=="osascript" {"osascript"} else if name=="/usr/bin/open" || name=="open" {"open"} else {panic!("Unexpected generated command")};
  let file=if logical=="osascript" && std::env::var("CASE").unwrap()=="spawn-failed" {"/nonexistent-mixel-fixture-command".to_owned()} else {std::env::var("FIXTURE_TOOL").unwrap()};
  let mut c=std::process::Command::new(file);c.env("FIXTURE_COMMAND",logical);Self(c)
 }
 fn arg<S:AsRef<std::ffi::OsStr>>(&mut self,a:S)->&mut Self{self.0.arg(a);self}
 fn status(&mut self)->std::io::Result<ExitStatus>{self.0.status()}
 fn spawn(&mut self)->std::io::Result<std::process::Child>{self.0.spawn()}
}
fn get_update_temp_dir_string()->String{std::env::var("DMG_SOURCE").unwrap()}
fn extract_dmg(_dmg:&str,_dest:&str)->ResultType<()> {
 use std::io::Write;
 let mut f=std::fs::OpenOptions::new().append(true).create(true).open(std::env::var("FIXTURE_ACTIONS").unwrap())?;
 writeln!(f,"extract")?;Ok(())
}
fn translate(s:String)->String{s}
fn update_extracted(_target:&str)->ResultType<()>{Ok(())}
'''
            # Compile the real pinned branding implementation, not an identity
            # stub. Read real resources too, so argv catches filename rewriting
            # before any signed build or administrator-authorized replacement.
            pinned_macos = (SOURCE / 'src/platform/macos.rs').read_text()
            anchor = 'fn correct_app_name(s: &str) -> String {'
            self.assertEqual(pinned_macos.count(anchor), 1, 'Pinned branding function missing or duplicated')
            start = pinned_macos.index(anchor)
            end = pinned_macos.index('\n}\n', start) + 3
            provider += pinned_macos[start:end]
            resource_dir = base / 'real-resources'
            resource_dir.mkdir()
            resources = {}
            for name in ('update.scpt', 'update.sh', 'daemon.plist', 'agent.plist'):
                path = ROOT / ('macos-update.scpt' if name == 'update.scpt' else 'macos-update-bundle.sh') if name.startswith('update.') else SOURCE / 'src/platform/privileges_scripts' / name
                body = path.read_text()
                if name.endswith('.plist'):
                    # The real pipeline normalizes this upstream bundle prefix
                    # before runtime correct_app_name() applies app branding.
                    body = body.replace('com.carriez.RustDesk', 'com.carriez.rustdesk')
                resources[name] = body
                (resource_dir / name).write_text(body)
            provider += '\nstruct Resource(&\'static str);\nimpl Resource {fn contents_utf8(&self)->Option<&\'static str>{Some(self.0)}}\nstruct Resources;\nimpl Resources {fn get_file(&self,name:&str)->Option<Resource>{match name {\n'
            for name in resources:
                provider += json.dumps(name) + ' => Some(Resource(include_str!(' + json.dumps(str(resource_dir / name)) + '))),\n'
            provider += '_ => None}}}\nstatic PRIVILEGES_SCRIPTS_DIR:Resources=Resources;\n'

            def assert_resource_arguments(arguments):
                self.assertEqual(len(arguments), 11)
                self.assertEqual(arguments[0], '-e')
                self.assertEqual(arguments[1], resources['update.scpt'])
                self.assertEqual(arguments[7], resources['update.sh'])
                self.assertIn('Contents/Frameworks/liblibrustdesk.dylib', arguments[7])
                self.assertNotIn('liblibmixel-remote.dylib', arguments[7])
                for index, name, suffix in ((2, 'daemon.plist', 'service'), (3, 'agent.plist', 'server')):
                    expected = resources[name].replace('com.carriez.rustdesk', 'ch.mixel.remote').replace('rustdesk', 'mixel-remote').replace('RustDesk', 'Mixel-Remote')
                    self.assertEqual(arguments[index], expected)
                    plist = plistlib.loads(arguments[index].encode())
                    self.assertEqual(plist['Label'], 'ch.mixel.remote_' + suffix)
                    self.assertEqual(plist['AssociatedBundleIdentifiers'], 'ch.mixel.remote')
                    self.assertIn('/Applications/Mixel-Remote.app/Contents/MacOS/', plist['WorkingDirectory'])

            updater = patch.FIXED_UPDATER.replace('format!("/Applications/{}.app", crate::get_app_name())',
                                                   'format!("{}/Applications/{}.app", std::env::var("FIXTURE_BASE").unwrap(), crate::get_app_name())')
            updater = updater.replace('format!("/Library/LaunchAgents/{}", agent)',
                                      'format!("{}/Library/LaunchAgents/{}", std::env::var("FIXTURE_BASE").unwrap(), agent)')
            cli = patch.FIXED_CLI.replace('        #[cfg(target_os = "macos")]\n', '')
            source = provider + patch.FIXED_DAEMON + updater + '\nmod platform {pub use crate::{update_me,update_from_dmg};}\n'
            source += 'mod fixture_core {use crate::{log,translate};pub fn core_main()->Option<Vec<String>> {let args:Vec<String>=std::env::args().skip(1).collect();\n' + cli + '\nNone}}\nfn main(){fixture_core::core_main();}\n'
            rust = base / 'fixture.rs'
            rust.write_text(source)
            executable = candidate / 'Mixel-Remote'
            compiler = os.environ.get('RUSTC_BIN') or shutil.which('rustc')
            self.assertTrue(compiler, 'Rust compiler required')
            compile_result = run([compiler, '--edition=2021', '--deny=warnings', str(rust), '-o', str(executable)])
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            # Retain the exact faulty all-resource branding as a compiled
            # negative control; execute its captured shell in a private fixture.
            resource_map = '.map(|body| match name {\n                "daemon.plist" | "agent.plist" => correct_app_name(body),\n                _ => body.to_owned(),\n            })'
            self.assertEqual(patch.FIXED_DAEMON.count(resource_map), 1)
            faulty_daemon = patch.FIXED_DAEMON.replace(resource_map, '.map(correct_app_name)')
            self.assertEqual(source.count(patch.FIXED_DAEMON), 1)
            rust.write_text(source.replace(patch.FIXED_DAEMON, faulty_daemon))
            faulty_executable = candidate / 'Mixel-Remote-faulty-branding'
            faulty_compile = run([compiler, '--edition=2021', '--deny=warnings', str(rust), '-o', str(faulty_executable)])
            self.assertEqual(faulty_compile.returncode, 0, faulty_compile.stdout + faulty_compile.stderr)
            (base / 'dmg/Mixel-Remote.app').mkdir(parents=True)
            agent = base / 'Library/LaunchAgents/ch.mixel.remote_server.plist'
            agent.parent.mkdir(parents=True)
            agent.write_text('synthetic-canonical-agent')
            # Compile the exact original normal updater and CLI with the same
            # command/path seams. This reproduces the defect rather than infers it.
            original_updater = patch.ORIGINAL_UPDATER.replace('std::process::Command::new', 'Command::new')
            original_daemon = patch.ORIGINAL_DAEMON.replace('std::process::Command::new', 'Command::new')
            original_cli = patch.ORIGINAL_CLI.replace('        #[cfg(target_os = "macos")]\n', '')
            original = provider + original_daemon + original_updater + '\nmod platform {pub use crate::{update_me,update_from_dmg};}\n'
            original += 'mod fixture_core {use crate::{log,translate};pub fn core_main()->Option<Vec<String>> {let args:Vec<String>=std::env::args().skip(1).collect();\n' + original_cli + '\nNone}}\nfn main(){fixture_core::core_main();}\n'
            rust.write_text(original)
            old_executable = candidate / 'Mixel-Remote-original'
            old_compile = run([compiler, '--edition=2021', '--deny=warnings', str(rust), '-o', str(old_executable)])
            self.assertEqual(old_compile.returncode, 0, old_compile.stdout + old_compile.stderr)
            actions = base / 'original-actions'
            old_env = {**os.environ, 'CASE':'cancel','SERVICE':'0','STOPPED':'',
                       'FIXTURE_ACTIONS':str(actions),'FIXTURE_TOOL':str(tool)}
            old_result = run([str(old_executable),'--update'],env=old_env)
            self.assertEqual(old_result.returncode, 0, old_result.stdout + old_result.stderr)
            self.assertIn('Update successfully!',old_result.stdout)
            self.assertEqual(actions.read_text().splitlines(), ['osascript','open'])
            print('PASS: compiled pinned original reports success/exit0 and reopens after canceled updater; corrected actual source refuses this behavior')
            for mode, service, dmg in [('cancel','0',False),('cancel','1',False),('copy-failed','0',True),
                                       ('spawn-failed','1',False),('success','0',False),('success','1',True),
                                       ('open-failed','0',False)]:
                with self.subTest(mode=mode, service=service, dmg=dmg):
                    actions = base / 'rust-actions'
                    actions.unlink(missing_ok=True)
                    env = {**os.environ, 'CASE':mode,'SERVICE':service,'STOPPED':'', 'FIXTURE_BASE':str(base),
                           'FIXTURE_ACTIONS':str(actions),'FIXTURE_TOOL':str(tool),'DMG_SOURCE':str(base/'dmg'),
                           'FIXTURE_ARGV':str(base/'captured-argv.json')}
                    result = run([str(executable),'--update',*(['synthetic.dmg'] if dmg else [])], env=env)
                    observed = actions.read_text().splitlines() if actions.exists() else []
                    if mode != 'spawn-failed':
                        assert_resource_arguments(json.loads((base/'captured-argv.json').read_text()))
                    if mode == 'success':
                        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                        self.assertIn('Update successfully!',result.stdout)
                        self.assertEqual(observed, (['extract'] if dmg else [])+['osascript','open'])
                    else:
                        self.assertNotEqual(result.returncode,0)
                        self.assertNotIn('Update successfully!',result.stdout)
                        if mode != 'open-failed':self.assertNotIn('open',observed)
            for faulty in (True, False):
                with self.subTest(faulty_branding=faulty), tempfile.TemporaryDirectory() as temporary:
                    fixture = TransactionFixture(Path(temporary))
                    renamed = fixture.root / 'Mixel-Remote.app'
                    fixture.source.rename(renamed)
                    fixture.source = renamed
                    captured = fixture.root / 'captured-argv.json'
                    env = {**os.environ, 'CASE':'success','SERVICE':'1','STOPPED':'', 'FIXTURE_BASE':str(base),
                           'FIXTURE_ACTIONS':str(fixture.root/'rust-actions'), 'FIXTURE_TOOL':str(tool),
                           'DMG_SOURCE':str(fixture.root), 'FIXTURE_ARGV':str(captured)}
                    generated = run([str(faulty_executable if faulty else executable), '--update', 'synthetic.dmg'], env=env)
                    self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
                    arguments = json.loads(captured.read_text())
                    if faulty:
                        self.assertIn('Contents/Frameworks/liblibmixel-remote.dylib', arguments[7])
                        self.assertNotIn('Contents/Frameworks/liblibrustdesk.dylib', arguments[7])
                    else:
                        assert_resource_arguments(arguments)
                    fixture.write_shell(arguments[7])
                    transaction = run(['/bin/sh', str(fixture.shell), arguments[6], arguments[4], arguments[5],
                                       arguments[8], arguments[9], arguments[10], arguments[2], arguments[3]],
                                      env={**os.environ, 'CASE':'success', 'FIXTURE_ROOT':str(fixture.root)})
                    self.assertEqual(transaction.returncode != 0, faulty, transaction.stdout + transaction.stderr)
                    self.assertEqual(fixture.target_markers(), ['old' if faulty else 'new'] * 4)
                    if faulty:
                        self.assertEqual([p.read_bytes() for p in fixture.plists], fixture.old_plists)
                        self.assertFalse(any(r['tool'] in ('launchctl','pgrep','mv') for r in fixture.actions()))
                    else:
                        for plist in fixture.plists:
                            self.assertTrue(plistlib.loads(plist.read_bytes())['Label'].startswith('ch.mixel.remote_'))
            print('PASS: actual pinned branding corrupts the real library filename in the compiled negative control and aborts before installed changes; fixed raw scripts/branded plists complete the real shell transaction')
            installed = target/'Contents/MacOS/Mixel-Remote'
            installed.parent.mkdir(parents=True)
            shutil.copy2(executable,installed)
            actions=base/'alias-actions'
            env={**os.environ,'CASE':'success','SERVICE':'0','FIXTURE_BASE':str(base),'FIXTURE_ACTIONS':str(actions),'FIXTURE_TOOL':str(tool)}
            refused=run([str(installed),'--update'],env=env)
            self.assertNotEqual(refused.returncode,0)
            self.assertIn('overlaps',refused.stderr)
            self.assertFalse(actions.exists())
            print('PASS: actual generated Rust/CLI subprocesses fail nonzero on cancel/copy/spawn/open failures, do not reopen after update failure, wait for DMG completion and reject installed-source alias')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--profile-source', type=Path, action='append', default=[])
    parser.add_argument('--source-only', action='store_true', help='Run every source/materialization guard without POSIX transaction or AppleScript execution')
    args, test_args = parser.parse_known_args()
    SOURCE = args.source.resolve()
    PROFILE_SOURCES = [SOURCE, *(path.resolve() for path in args.profile_source)]
    source_controls = ['UpdaterTests.test_patch_idempotency_newlines_and_atomic_anchor_rejection',
                       'UpdaterTests.test_resource_drift_and_foreign_shell_rejected_without_source_write',
                       'UpdaterTests.test_whole_profile_ownership_and_drift_refusals',
                       'UpdaterTests.test_owned_templates_normalize_only_homogeneous_newlines']
    if args.source_only and any(not argument.startswith('-') for argument in test_args):
        parser.error('Source-only mode requires its complete fixed source-control list')
    unittest.main(argv=[__file__, *test_args, *(source_controls if args.source_only else [])])
