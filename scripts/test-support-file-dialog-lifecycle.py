#!/usr/bin/env python3
"""Run the actual pinned event loop across close/await races using real Dart timers."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("file_dialog_lifecycle", HERE / "patch-support-file-dialog-lifecycle.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

DRIVER = r'''
void check(bool value, String message) {
  if (!value) throw StateError(message);
}

class ProbeEvent extends BaseEvent<String, String> {
  final ProbeLoop owner;
  ProbeEvent(this.owner, String value) : super('item', value);
  @override
  EventCallback<String>? findCallback(String type) =>
      (value) => owner.step('consume:$value');
}

class ProbeLoop extends BaseEventLoop<String, String> {
  final String? hold;
  final trace = <String>[];
  final entered = Completer<void>();
  final release = Completer<void>();
  final firstClear = Completer<void>();
  final secondClear = Completer<void>();
  int clearCount = 0;
  ProbeLoop([this.hold]);

  Future<void> step(String name) async {
    trace.add(name);
    if (name == hold && !entered.isCompleted) {
      entered.complete();
      await release.future;
      trace.add('$name:finished');
    }
  }

  @override
  Future<void> onEventsStartConsuming() => step('start');
  @override
  Future<void> onPreConsume(BaseEvent<String, String> evt) =>
      step('pre:${evt.data}');
  @override
  Future<void> onPostConsume(BaseEvent<String, String> evt) =>
      step('post:${evt.data}');
  @override
  Future<void> onEventsClear() async {
    await step('clear');
    clearCount++;
    if (clearCount == 1) firstClear.complete();
    if (clearCount == 2) secondClear.complete();
  }

  void add(String value) => pushEvent(ProbeEvent(this, value));
}

Future<void> closeDuring(String stage) async {
  final loop = ProbeLoop(stage);
  try {
    loop.add('first');
    if (stage != 'clear') loop.add('second');
    await loop.onReady();
    await loop.entered.future.timeout(const Duration(seconds: 2));
    final before = List<String>.of(loop.trace);
    await loop.close();
    await loop.close();
    loop.add('after-close');
    await loop.onReady();
    loop.release.complete();
    // More than two actual polling periods: the old implementation resumes
    // queued callbacks and recreates an active periodic timer here.
    await Future<void>.delayed(const Duration(milliseconds: 250));
    check(loop.trace.join('|') == [...before, '$stage:finished'].join('|'),
        'CLOSE_VIOLATION: later callback ran after $stage resumed: ${loop.trace}');
    check(loop._timer == null,
        'CLOSE_VIOLATION: timer was recreated after $stage resumed');
    check(loop.evts.isEmpty,
        'CLOSE_VIOLATION: closed loop retained or accepted queued events');
  } finally {
    if (!loop.release.isCompleted) loop.release.complete();
    await loop.close();
  }
  print('PASS: close during $stage allows the awaited callback to finish; no later callback, queue or timer');
}

Future<void> closeIdle() async {
  final loop = ProbeLoop();
  try {
    loop.add('queued');
    await loop.onReady();
    await loop.close();
    await loop.close();
    loop.add('after-close');
    await loop.onReady();
    await Future<void>.delayed(const Duration(milliseconds: 250));
    check(loop.trace.isEmpty && loop.evts.isEmpty && loop._timer == null,
        'CLOSE_VIOLATION: idle close retained work or allowed restart');
  } finally {
    await loop.close();
  }
  print('PASS: idle close is terminal and idempotent; later push/onReady cannot restart it');
}

Future<void> normalBatches() async {
  final loop = ProbeLoop();
  try {
    loop.add('first');
    loop.add('second');
    await loop.onReady();
    await loop.firstClear.future.timeout(const Duration(seconds: 2));
    await Future<void>.delayed(Duration.zero);
    check(loop._timer?.isActive == true, 'Unclosed first batch did not rearm');
    loop.add('third');
    await loop.secondClear.future.timeout(const Duration(seconds: 2));
    await Future<void>.delayed(Duration.zero);
    check(loop._timer?.isActive == true, 'Unclosed second batch did not rearm');
    check(loop.trace.join('|') ==
        'start|pre:first|consume:first|post:first|pre:second|consume:second|post:second|clear|start|pre:third|consume:third|post:third|clear',
        'Unclosed callback ordering changed: ${loop.trace}');
    check(loop.evts.isEmpty, 'Normal loop did not drain both batches');
  } finally {
    await loop.close();
  }
  print('PASS: unclosed loop preserves exact callback order, drains both batches and rearms after each');
}

Future<void> repeatedReady(bool duringCallback) async {
  final loop = ProbeLoop(duringCallback ? 'consume:first' : null);
  try {
    if (duringCallback) {
      loop.add('first');
      loop.add('second');
    }
    await loop.onReady();
    if (duringCallback) {
      await loop.entered.future.timeout(const Duration(seconds: 2));
      await loop.onReady();
      check(loop._timer == null,
          'READY_VIOLATION: repeated readiness started a timer during an awaited callback');
      await Future<void>.delayed(const Duration(milliseconds: 150));
      check(loop.trace.join('|') == 'start|pre:first|consume:first',
          'READY_VIOLATION: a second consumer ran while the first callback awaited');
      loop.release.complete();
      await loop.firstClear.future.timeout(const Duration(seconds: 2));
      await Future<void>.delayed(Duration.zero);
      check(loop.trace.join('|') ==
          'start|pre:first|consume:first|consume:first:finished|post:first|pre:second|consume:second|post:second|clear',
          'READY_VIOLATION: callback order changed after repeated readiness');
      check(loop._timer?.isActive == true,
          'READY_VIOLATION: normal callback completion did not rearm');
    } else {
      final originalTimer = loop._timer;
      await loop.onReady();
      check(identical(loop._timer, originalTimer),
          'READY_VIOLATION: repeated readiness created another periodic timer');
      await loop.close();
      check(originalTimer?.isActive == false && loop._timer == null,
          'READY_VIOLATION: close left the original polling timer active');
    }
  } finally {
    if (!loop.release.isCompleted) loop.release.complete();
    await loop.close();
  }
  print('PASS: repeated onReady ${duringCallback ? "during an awaited callback" : "while idle"} preserves one consumer and closes every timer');
}

Future<void> main(List<String> args) async {
  final mode = args.single;
  if (mode == 'normal') {
    await normalBatches();
  } else if (mode == 'idle') {
    await closeIdle();
  } else if (mode == 'repeat-idle' || mode == 'repeat-await') {
    await repeatedReady(mode == 'repeat-await');
  } else {
    await closeDuring(mode);
  }
}
'''

OWNER_STUBS = r'''
typedef SessionID = String;
typedef GetSessionID = SessionID Function();
typedef GetDialogManager = DialogManager? Function();
const bool isWeb = false;
class DialogManager {
  int dismissCount = 0;
  void dismissAll() { dismissCount++; }
}
class FFI {
  String sessionId = 'owned-mobile-session';
  final dialogManager = DialogManager();
}
class FileFetcher { FileFetcher(Object getSessionID); }
enum JobState { none, done }
class Job {
  final int id;
  JobState state = JobState.none;
  Job(this.id);
}
class JobTable {
  final entries = <Job>[];
  int refreshCount = 0;
  Job operator [](int index) => entries[index];
  void refresh() { refreshCount++; }
}
class JobController {
  final GetSessionID getSessionID;
  final jobTable = JobTable();
  JobController(this.getSessionID, Object getDialogManager);
  int getJob(int id) => jobTable.entries.indexWhere((job) => job.id == id);
  Future<void> cancelJob(int id) async {
    await bind.sessionCancelJob(sessionId: getSessionID(), actId: id);
  }
}
class FileController {
  int readyCount = 0;
  int closeCount = 0;
  FileController({required bool isLocal, required Object getSessionID,
    required Object rootState, required Object jobController,
    required Object fileFetcher, required Object getOtherSideDirectoryData});
  Object directoryData() => Object();
  Future<void> onReady() async { readyCount++; }
  Future<void> close() async { closeCount++; }
}
class FileDialogEventLoop extends ProbeLoop {
  int skipCount = 0;
  int overrideCount = 0;
  bool skipValue = false;
  bool? overrideValue;
  FileDialogEventLoop() : super('consume:first');
  void setSkip(bool value) { skipCount++; skipValue = value; }
  void setOverrideConfirm(bool? value) { overrideCount++; overrideValue = value; }
}
String translate(String value) => value;
class Binding {
  final confirms = <Map<String, Object>>[];
  final cancels = <Map<String, Object>>[];
  bool holdConfirm = false;
  bool holdCancel = false;
  final confirmEntered = Completer<void>();
  final confirmRelease = Completer<void>();
  final cancelEntered = Completer<void>();
  final cancelRelease = Completer<void>();
  Future<void> sessionCancelJob({required SessionID sessionId, required int actId}) async {
    cancels.add({'sessionId': sessionId, 'actId': actId});
    if (holdCancel) { cancelEntered.complete(); await cancelRelease.future; }
  }
  Future<void> sessionSetConfirmOverrideFile({required SessionID sessionId,
      required int actId, required int fileNum, required bool needOverride,
      required bool remember, required bool isUpload}) async {
    confirms.add({'sessionId': sessionId, 'actId': actId, 'fileNum': fileNum,
      'needOverride': needOverride, 'remember': remember, 'isUpload': isUpload});
    if (holdConfirm) { confirmEntered.complete(); await confirmRelease.future; }
  }
}
final bind = Binding();
'''

OWNER_DIALOG_STUB = r'''
  bool fileConfirmCheckboxRemember = false;
  final dialogEntered = Completer<void>();
  final dialogReply = Completer<bool?>();
  Future<bool?> showFileConfirmDialog(String title, String path, bool overwrite, bool identical) async {
    dialogEntered.complete();
    return dialogReply.future;
  }
'''

OWNER_DRIVER = r'''
Future<void> main(List<String> args) async {
  final ffi = FFI();
  final owner = FileModel(WeakReference(ffi));
  final old = owner.evtLoop;
  try {
    old.add('first');
    old.add('old-queued');
    await owner.onReady();
    await old.entered.future.timeout(const Duration(seconds: 2));
    final oldTrace = List<String>.of(old.trace);
    await owner.close();
    check(old.isClosed, 'OWNER_VIOLATION: old loop was not closed');
    await owner.onReady();
    final fresh = owner.evtLoop;
    check(!identical(fresh, old) && !fresh.isClosed,
        'OWNER_VIOLATION: reused FileModel did not create a fresh session loop');
    fresh.add('new-session');
    await fresh.firstClear.future.timeout(const Duration(seconds: 2));
    await Future<void>.delayed(Duration.zero);
    check(fresh._timer?.isActive == true,
        'OWNER_VIOLATION: fresh loop did not drain and rearm');
    old.release.complete();
    await Future<void>.delayed(const Duration(milliseconds: 250));
    check(old.trace.join('|') == [...oldTrace, 'consume:first:finished'].join('|') &&
        old._timer == null && old.evts.isEmpty && old.isClosed,
        'OWNER_VIOLATION: old awaited callback resumed old work or reopened its loop');
    check(fresh.trace.join('|') ==
        'start|pre:new-session|consume:new-session|post:new-session|clear' &&
        fresh._timer?.isActive == true && !fresh.isClosed,
        'OWNER_VIOLATION: old completion interfered with the fresh session');
    check(owner.localController.readyCount == 2 && owner.remoteController.readyCount == 2 &&
        owner.localController.closeCount == 1 && owner.remoteController.closeCount == 1 &&
        ffi.dialogManager.dismissCount == 1,
        'OWNER_VIOLATION: actual owner controller/close order changed');
  } finally {
    if (!old.release.isCompleted) old.release.complete();
    await owner.close();
    await old.close();
  }
  print('PASS: actual reused FileModel creates a fresh session loop; old awaited callback finishes without queued work, rearm or interference');
}
'''

DIALOG_DRIVER = r'''
Future<void> main(List<String> args) async {
  final mode = args.single;
  final ffi = FFI();
  final owner = FileModel(WeakReference(ffi));
  owner.fileConfirmCheckboxRemember = true;
  final old = owner.evtLoop;
  final job = Job(7);
  owner.jobController.jobTable.entries.add(job);
  final evt = <String, dynamic>{'id': '7', 'file_num': '0', 'read_path': 'owned-fixture',
    'is_identical': 'false', 'is_upload': 'true'};
  try {
    await owner.onReady();
    if (mode == 'closed') {
      await owner.close();
      await owner.overrideFileConfirm(evt, overrideConfirm: true);
      check(bind.confirms.isEmpty && bind.cancels.isEmpty && old.skipCount == 0 && old.overrideCount == 0,
          'DIALOG_VIOLATION: closed owner performed confirmation work');
    } else if (mode.startsWith('normal-')) {
      await owner.overrideFileConfirm(evt,
          overrideConfirm: mode == 'normal-cancel' ? false : (mode == 'normal-skip' ? null : true),
          skip: mode == 'normal-skip');
      if (mode == 'normal-cancel') {
        check(bind.cancels.single['sessionId'] == ffi.sessionId && bind.confirms.isEmpty &&
            job.state == JobState.done && owner.jobController.jobTable.refreshCount == 1 &&
            old.overrideCount == 1 && old.overrideValue == false,
            'DIALOG_VIOLATION: normal cancellation behavior changed');
      } else {
        final skipped = mode == 'normal-skip';
        final record = bind.confirms.single;
        check(record['sessionId'] == ffi.sessionId && record['actId'] == 7 && record['fileNum'] == 0 &&
            record['needOverride'] == !skipped && record['remember'] == true && record['isUpload'] == true &&
            bind.cancels.isEmpty && old.skipCount == 1 && old.skipValue == skipped &&
            old.overrideCount == 1 && old.overrideValue == (skipped ? null : true),
            'DIALOG_VIOLATION: normal confirm/skip behavior changed');
      }
    } else {
      bind.holdCancel = mode == 'cancel' || mode == 'cancel-cleared';
      bind.holdConfirm = mode == 'confirm';
      final pending = owner.overrideFileConfirm(evt,
          overrideConfirm: bind.holdCancel ? false : (bind.holdConfirm ? true : null));
      if (bind.holdCancel) {
        await bind.cancelEntered.future.timeout(const Duration(seconds: 2));
      } else if (bind.holdConfirm) {
        await bind.confirmEntered.future.timeout(const Duration(seconds: 2));
      } else {
        await owner.dialogEntered.future.timeout(const Duration(seconds: 2));
      }
      if (mode == 'cancel-cleared') {
        owner.jobController.jobTable.entries.clear();
        bind.cancelRelease.complete();
        await pending;
        check(owner.jobController.jobTable.refreshCount == 0 && old.overrideCount == 1,
            'DIALOG_VIOLATION: removed job was indexed or normal owner completion was lost');
      } else {
        if (mode != 'session-change') await owner.close();
        ffi.sessionId = 'fresh-mobile-session';
        if (mode != 'session-change') await owner.onReady();
        final fresh = owner.evtLoop;
        final freshJob = Job(7);
        owner.jobController.jobTable.entries
          ..clear()
          ..add(freshJob);
        if (bind.holdCancel) {
          bind.cancelRelease.complete();
        } else if (bind.holdConfirm) {
          bind.confirmRelease.complete();
        } else {
          owner.dialogReply.complete(true);
        }
        await pending;
        check(fresh.skipCount == 0 && fresh.overrideCount == 0 &&
            freshJob.state == JobState.none && owner.jobController.jobTable.refreshCount == 0,
            'DIALOG_VIOLATION: stale awaited response changed the fresh session loop/job');
        check(bind.confirms.length == (bind.holdConfirm ? 1 : 0) &&
            bind.cancels.length == (bind.holdCancel ? 1 : 0),
            'DIALOG_VIOLATION: stale awaited response sent a new session command');
        for (final record in [...bind.confirms, ...bind.cancels]) {
          check(record['sessionId'] == 'owned-mobile-session',
              'DIALOG_VIOLATION: confirmation/cancellation used the fresh session identity');
        }
      }
    }
  } finally {
    if (!owner.dialogReply.isCompleted) owner.dialogReply.complete(null);
    if (!bind.confirmRelease.isCompleted) bind.confirmRelease.complete();
    if (!bind.cancelRelease.isCompleted) bind.cancelRelease.complete();
    await owner.close();
    await old.close();
  }
  print('PASS: actual overrideFileConfirm $mode preserves session ownership across awaits');
}
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(root):
    result = {}
    for parent, directories, names in os.walk(root):
        for name in directories + names:
            path = Path(parent) / name
            record = path.lstat()
            key = path.relative_to(root).as_posix()
            if stat.S_ISLNK(record.st_mode):
                result[key] = ("symlink", os.readlink(path))
            elif stat.S_ISREG(record.st_mode):
                result[key] = ("file", digest(path.read_bytes()), record.st_nlink, stat.S_IMODE(record.st_mode))
            else:
                result[key] = ("other", record.st_mode)
    return result


class LifecycleTests(unittest.TestCase):
    def test_actual_macro_prior_refusal_preserves_final_overlay(self):
        bash = shutil.which("bash")
        self.assertTrue(bash, "Bash is required for the actual branding prewrite control")
        with tempfile.TemporaryDirectory(prefix="mixel dialog macro refusal ") as temporary:
            root = Path(temporary).resolve()
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("apply-branding.sh", "patch-support-file-dialog-lifecycle.py"):
                (scripts / name).write_bytes((HERE / name).read_bytes())
            # Run the complete actual macro up to the whole prior-owner gate.
            # The prior owner alone is inert and refuses before any of its writes.
            (scripts / "patch-support-uri-routing.py").write_bytes(b'''import sys
if len(sys.argv)!=6 or sys.argv[1]!='--root' or sys.argv[3:]!=['--restore','--check','--before-branding']:
    raise SystemExit('Unexpected whole prior-owner gate argv')
print('CONTROL: complete prior owner refused unrelated drift')
raise SystemExit(47)
''')
            branding = root / "branding"
            branding.mkdir()
            (branding / "branding.env").write_bytes(b"")
            source = root / "source"
            for name, data in ORIGINALS.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(P.transform(data, name=name))
            (source / "unrelated-drift").write_bytes(b"prior owner must reject this without changing a valid lifecycle overlay\n")
            before = snapshot(source)
            result = subprocess.run([bash, str(scripts / "apply-branding.sh")],
                                    env=dict(os.environ, RDREPO=str(source), BRANDING=str(branding)),
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 47, result.stdout + result.stderr)
            self.assertIn("complete prior owner refused unrelated drift", result.stdout)
            self.assertEqual(snapshot(source), before, "A valid lifecycle overlay changed before complete prior-owner refusal")
        print("PASS: exact branding macro performs read-only lifecycle validation before a whole prior-owner refusal; both final overlay files stay byte-identical", flush=True)

    def test_actual_windows_dispatch_failure_is_fatal(self):
        bash = shutil.which("bash")
        if os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            if candidate.is_file():
                bash = str(candidate)
        self.assertTrue(bash, "Bash is required to execute the unchanged source dispatcher")
        lifecycle = "scripts/test-support-file-dialog-lifecycle.py"
        with tempfile.TemporaryDirectory(prefix="mixel dialog dispatch préflight ") as temporary:
            root = Path(temporary).resolve()
            scripts = root / "scripts"
            scripts.mkdir()
            dispatcher = scripts / "test-windows-generated-source.sh"
            dispatcher.write_bytes((HERE / dispatcher.name).read_bytes())
            # Only the native environment and individual test bodies are inert
            # stand-ins. The complete dispatcher and shell fatal ordering run.
            (scripts / "use-windows-msvc-bash.sh").write_bytes(b'''python() { "$MIXEL_DIALOG_ORDER_PYTHON" "$MIXEL_DIALOG_ORDER_SHIM" "$@"; }
cygpath() { printf "%s\\n" "$MIXEL_DIALOG_ORDER_BASH"; }
''')
            shim = root / "record actual argv.py"
            shim.write_bytes(b'''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=='--probe':
    print(json.dumps(args[1:]))
    sys.exit(0)
with Path(os.environ['MIXEL_DIALOG_ORDER_CALLS']).open('a',encoding='utf-8') as output:
    output.write(json.dumps(args)+'\\n')
if args[0]==os.environ['MIXEL_DIALOG_ORDER_FAIL']:
    sys.exit(int(os.environ['MIXEL_DIALOG_ORDER_CODE']))
''')
            executables = root / "tool directory Zürich 🚀"
            executables.mkdir()
            for tool in ("dart", "flutter"):
                path = executables / tool
                path.write_bytes(('''#!/usr/bin/env bash
"$MIXEL_DIALOG_ORDER_PYTHON" "$MIXEL_DIALOG_ORDER_SHIM" --''' + tool + ''' "$@"
''').encode("utf-8"))
                path.chmod(0o755)
            runner = root / "runner temporary Genève 🚀"
            runner.mkdir()
            calls = root / "actual calls.jsonl"
            environment = dict(os.environ, PATH=str(executables) + os.pathsep + os.environ.get("PATH", ""),
                               MIXEL_DIALOG_ORDER_PYTHON=Path(sys.executable).resolve().as_posix(),
                               MIXEL_DIALOG_ORDER_SHIM=shim.as_posix(), MIXEL_DIALOG_ORDER_CALLS=str(calls),
                               MIXEL_DIALOG_ORDER_BASH=r"C:\Program Files\Git préflight 🚀\bin\bash.exe",
                               RUNNER_TEMP=runner.as_posix(), MIXEL_SDK_FULL_APP_PREFLIGHT="false",
                               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            probe = subprocess.run([bash, "-c", '''"$MIXEL_DIALOG_ORDER_PYTHON" "$MIXEL_DIALOG_ORDER_SHIM" --probe "$RUNNER_TEMP/mixel-sdk-source/pipeline-lf" "$(command -v dart)" "$RUNNER_TEMP/mixel-file-dialog-lifecycle"'''],
                                   env=environment, capture_output=True, text=True, encoding="utf-8", timeout=15)
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            expected_source, expected_dart, expected_output = json.loads(probe.stdout)
            expected = [lifecycle, "--source", expected_source, "--dart", expected_dart,
                        "--output", expected_output]
            for profile in ("unix", "windows"):
                for code in (23, 31):
                    with self.subTest(profile=profile, failure=code):
                        calls.unlink(missing_ok=True)
                        case = dict(environment, OS="Windows_NT" if profile == "windows" else "Linux",
                                    MIXEL_DIALOG_ORDER_FAIL=lifecycle, MIXEL_DIALOG_ORDER_CODE=str(code))
                        result = subprocess.run([bash, str(dispatcher)], env=case, cwd=root,
                                                capture_output=True, text=True, encoding="utf-8", timeout=30)
                        rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
                        (OUTPUT / f"dispatch-{profile}-{code}.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
                        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                        self.assertEqual(rows[-1], expected)
                        self.assertEqual([row[0] for row in rows[-3:]],
                                         ["scripts/test-support-native-warnings.py",
                                          "scripts/test-support-compiler-cleanup.py", lifecycle])
                        self.assertEqual(sum(row[0] == lifecycle for row in rows), 1)
                        self.assertFalse(any(row[0].startswith("--") for row in rows),
                                         "Reporter Dart ran after fatal lifecycle regression")
            for target, code in (("scripts/test-support-lease.py", 23),
                                 ("scripts/test-support-compiler-cleanup.py", 29)):
                calls.unlink(missing_ok=True)
                case = dict(environment, OS="Linux", MIXEL_DIALOG_ORDER_FAIL=target, MIXEL_DIALOG_ORDER_CODE=str(code))
                result = subprocess.run([bash, str(dispatcher)], env=case, cwd=root,
                                        capture_output=True, text=True, encoding="utf-8", timeout=30)
                rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(rows[-1][0], target)
                self.assertFalse(any(row[0] == lifecycle or row[0].startswith("--") for row in rows))
        print("PASS: exact Windows dispatcher preserves source/Dart/output argv with Unicode/spaces; lifecycle exits 23/31 stop both reporters; prior lease/compiler failures still stop first", flush=True)

    def test_source_roundtrips(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                target = root / P.SOURCE
                original = ORIGINAL.replace(b"\n", newline)
                for name, data in ORIGINALS.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data.replace(b"\n", newline))
                target.chmod(0o640)
                (root / "unrelated").write_bytes(b"preserve this file\n")
                before = snapshot(root)
                with contextlib.redirect_stdout(io.StringIO()):
                    P.apply(root, check=True)
                    self.assertEqual(snapshot(root), before)
                    P.apply(root)
                    patched = snapshot(root)
                    self.assertNotEqual(patched, before)
                    self.assertEqual(target.read_bytes(), P.transform(original))
                    self.assertEqual((root / P.MODEL).read_bytes(), P.transform(ORIGINALS[P.MODEL].replace(b"\n", newline), name=P.MODEL))
                    P.apply(root)
                    P.apply(root, check=True)
                    P.apply(root, restore=True, check=True)
                    self.assertEqual(snapshot(root), patched)
                    P.apply(root, restore=True)
                    self.assertEqual(snapshot(root), before)
                    P.apply(root, restore=True)
                    self.assertEqual(snapshot(root), before)

    def test_strict_source_refusals(self):
        corrected = P.transform(ORIGINAL)
        for data in (ORIGINAL + b"// foreign drift\n", ORIGINAL[:-1], b"\xff" + ORIGINAL,
                     ORIGINAL.replace(b"\n", b"\r\n", 1),
                     corrected.replace(b"_closed = true", b"_closed = false", 1),
                     corrected + P.OLD.encode(), corrected + b"// foreign drift\n"):
            for restore in (False, True):
                with self.subTest(data=digest(data), restore=restore):
                    with self.assertRaises((RuntimeError, UnicodeError)):
                        P.transform(data, restore)
        model = ORIGINALS[P.MODEL]
        for data in (model + b"// foreign drift\n", model[:-1],
                     P.transform(model, name=P.MODEL).replace(b"evtLoop.isClosed", b"evtLoop.isOpen", 1),
                     model.replace(P.MODEL_REPLACEMENTS[0][0].encode(), P.MODEL_REPLACEMENTS[0][1].encode(), 1)):
            for restore in (False, True):
                with self.subTest(model=digest(data), restore=restore), self.assertRaises((RuntimeError, UnicodeError)):
                    P.transform(data, restore, name=P.MODEL)

    def test_ownership_refusals_do_not_write(self):
        faults = ["missing", "drift", "partial", "hardlink", "symlink-file", "symlink-parent",
                  "symlink-root", "symlink-ancestor", "model-drift", "model-missing", "model-hardlink",
                  "partial-pair-loop", "partial-pair-model"]
        if hasattr(os, "mkfifo"):
            faults.append("fifo")
        for fault in faults:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                container = Path(temporary).resolve()
                root = container / "source"
                target = root / P.SOURCE
                for name, data in ORIGINALS.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                external = container / "external"
                external.write_bytes(b"external sentinel\n")
                called_root = root
                if fault == "missing":
                    target.unlink()
                elif fault == "drift":
                    target.write_bytes(ORIGINAL + b"// drift\n")
                elif fault == "partial":
                    target.write_bytes(P.transform(ORIGINAL).replace(b"_closed = true", b"_closed = false", 1))
                elif fault == "hardlink":
                    os.link(target, container / "linked")
                elif fault == "symlink-file":
                    target.unlink()
                    target.symlink_to(external)
                elif fault == "symlink-parent":
                    saved = container / "saved-parent"
                    target.parent.rename(saved)
                    target.parent.symlink_to(saved, target_is_directory=True)
                elif fault in ("symlink-root", "symlink-ancestor"):
                    alias = container / "alias"
                    alias.symlink_to(root if fault == "symlink-root" else container, target_is_directory=True)
                    called_root = alias if fault == "symlink-root" else alias / "source"
                elif fault == "fifo":
                    target.unlink()
                    os.mkfifo(target)
                elif fault == "model-drift":
                    (root / P.MODEL).write_bytes(ORIGINALS[P.MODEL] + b"// drift\n")
                elif fault == "model-missing":
                    (root / P.MODEL).unlink()
                elif fault == "model-hardlink":
                    os.link(root / P.MODEL, container / "linked")
                elif fault in ("partial-pair-loop", "partial-pair-model"):
                    name = P.SOURCE if fault == "partial-pair-loop" else P.MODEL
                    (root / name).write_bytes(P.transform(ORIGINALS[name], name=name))
                before = snapshot(container)
                for restore, check in ((False, False), (True, False), (False, True), (True, True)):
                    with self.assertRaises((RuntimeError, OSError, UnicodeError)):
                        P.apply(called_root, restore, check)
                    self.assertEqual(snapshot(container), before)

    def test_branding_hook_order(self):
        body = (HERE / "apply-branding.sh").read_text(encoding="utf-8")
        prefix = 'python3 "$(dirname "${BASH_SOURCE[0]}")/patch-support-file-dialog-lifecycle.py" --root "$RDREPO"'
        expected = [prefix + " --check", prefix]
        actual = [line for line in body.splitlines() if '/patch-support-file-dialog-lifecycle.py"' in line]
        self.assertEqual(actual, expected)
        self.assertLess(body.index(expected[0]), body.index('/patch-support-uri-routing.py"'))
        self.assertGreater(body.index("\n" + expected[1] + "\n"), body.rindex('/patch-support-uri-routing.py"'))

    def test_actual_reused_file_model(self):
        annotation = "import 'package:flutter/foundation.dart';"
        replacement = "const visibleForTesting = _VisibleForTesting();\nclass _VisibleForTesting { const _VisibleForTesting(); }"
        loop_driver = DRIVER.replace("Future<void> main(List<String> args) async {", "Future<void> loopMain(List<String> args) async {", 1)
        original_model = ORIGINALS[P.MODEL].decode()
        for profile, newline in (("lf", b"\n"), ("crlf", b"\r\n")):
            loop = P.transform(ORIGINAL.replace(b"\n", newline)).decode()
            model = P.transform(ORIGINALS[P.MODEL].replace(b"\n", newline), name=P.MODEL).decode()
            # Copy the complete actual declarations, constructor, onReady and
            # close. Only unrelated controllers/FFI are inert test boundaries.
            start = model.index("class FileModel {")
            end = model.index("  Future<void> refreshAll()", start)
            actual_owner = model[start:end] + "\n}\n"
            program = OUTPUT / ("actual-reused-owner-" + profile + ".dart")
            program.write_bytes((loop.replace(annotation, replacement, 1) + loop_driver + OWNER_STUBS + actual_owner + OWNER_DRIVER).encode("utf-8"))
            result = self.run_dart(program, "owner", "owner-reuse-" + profile)
            print(profile + ": " + result.stdout.strip(), flush=True)
        start = original_model.index("class FileModel {")
        end = original_model.index("  Future<void> refreshAll()", start)
        old_owner = original_model[start:end] + "\n}\n"
        baseline = OUTPUT / "actual-original-reused-owner.dart"
        baseline.write_bytes((P.transform(ORIGINAL).decode().replace(annotation, replacement, 1) + loop_driver + OWNER_STUBS + old_owner + OWNER_DRIVER).encode("utf-8"))
        self.run_dart(baseline, "owner", "owner-reuse-original", success=False, marker="OWNER_VIOLATION:")

    def test_actual_dialog_response_session_ownership(self):
        annotation = "import 'package:flutter/foundation.dart';"
        replacement = "const visibleForTesting = _VisibleForTesting();\nclass _VisibleForTesting { const _VisibleForTesting(); }"
        loop_driver = DRIVER.replace("Future<void> main(List<String> args) async {", "Future<void> loopMain(List<String> args) async {", 1)
        for profile, newline in (("lf", b"\n"), ("crlf", b"\r\n")):
            loop = P.transform(ORIGINAL.replace(b"\n", newline)).decode()
            model = P.transform(ORIGINALS[P.MODEL].replace(b"\n", newline), name=P.MODEL).decode()
            start = model.index("class FileModel {")
            end = model.index("  Future<void> refreshAll()", start)
            actual_owner = model[start:end]
            begin_override = model.index("  Future<void> overrideFileConfirm(")
            end_override = model.index("  bool fileConfirmCheckboxRemember", begin_override)
            actual_override = model[begin_override:end_override]
            prefix = loop.replace(annotation, replacement, 1) + loop_driver + OWNER_STUBS + actual_owner + OWNER_DIALOG_STUB
            program = OUTPUT / ("actual-dialog-owner-" + profile + ".dart")
            program.write_bytes((prefix + actual_override + "\n}\n" + DIALOG_DRIVER).encode("utf-8"))
            analysis = subprocess.run([str(DART), "analyze", str(program)], capture_output=True,
                                      text=True, encoding="utf-8", timeout=30)
            (OUTPUT / (profile + "-dialog-analyze.log")).write_text(analysis.stdout + analysis.stderr, encoding="utf-8")
            self.assertEqual(analysis.returncode, 0, analysis.stdout + analysis.stderr)
            for mode in ("dialog", "cancel", "confirm", "session-change", "cancel-cleared", "closed",
                         "normal-confirm", "normal-cancel", "normal-skip"):
                result = self.run_dart(program, mode, "dialog-" + profile + "-" + mode)
                print(profile + ": " + result.stdout.strip(), flush=True)
            if profile == "lf":
                baseline = OUTPUT / "actual-original-dialog-owner.dart"
                baseline.write_bytes((prefix + P.OVERRIDE_OLD + "\n}\n" + DIALOG_DRIVER).encode("utf-8"))
                for mode in ("dialog", "cancel", "confirm", "session-change"):
                    self.run_dart(baseline, mode, "dialog-original-" + mode, success=False, marker="DIALOG_VIOLATION:")

    def run_dart(self, program, mode, label, success=True, marker="CLOSE_VIOLATION:"):
        result = subprocess.run([str(DART), str(program), mode], capture_output=True, text=True,
                                encoding="utf-8", timeout=15)
        (OUTPUT / (label + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout.startswith("PASS: "), result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, "Original close race unexpectedly passed")
            self.assertIn(marker, result.stderr)
        return result

    def test_actual_async_lifecycle(self):
        annotation = "import 'package:flutter/foundation.dart';"
        replacement = "const visibleForTesting = _VisibleForTesting();\nclass _VisibleForTesting { const _VisibleForTesting(); }"
        self.assertEqual(ORIGINAL.decode().count(annotation), 1)
        for profile, newline in (("lf", b"\n"), ("crlf", b"\r\n")):
            corrected = P.transform(ORIGINAL.replace(b"\n", newline)).decode()
            program = OUTPUT / ("actual-loop-" + profile + ".dart")
            # The annotation is the sole Flutter dependency. All event-loop
            # declarations and executable statements come from the actual source.
            program.write_bytes((corrected.replace(annotation, replacement, 1) + DRIVER).encode("utf-8"))
            analysis = subprocess.run([str(DART), "analyze", str(program)], capture_output=True,
                                      text=True, encoding="utf-8", timeout=30)
            (OUTPUT / (profile + "-analyze.log")).write_text(analysis.stdout + analysis.stderr, encoding="utf-8")
            self.assertEqual(analysis.returncode, 0, analysis.stdout + analysis.stderr)
            for mode in ("start", "pre:first", "consume:first", "post:first", "clear", "idle", "normal", "repeat-idle", "repeat-await"):
                with self.subTest(profile=profile, mode=mode):
                    label = profile + "-" + mode.replace(":", "-")
                    result = self.run_dart(program, mode, label)
                    print(profile + ": " + result.stdout.strip(), flush=True)
        original_program = OUTPUT / "actual-original-loop.dart"
        original_program.write_bytes((ORIGINAL.decode().replace(annotation, replacement, 1) + DRIVER).encode("utf-8"))
        for mode in ("start", "pre:first", "consume:first", "post:first", "clear", "idle"):
            self.run_dart(original_program, mode, "original-" + mode.replace(":", "-"), success=False)
        for mode in ("repeat-idle", "repeat-await"):
            self.run_dart(original_program, mode, "original-" + mode, success=False, marker="READY_VIOLATION:")
        result = self.run_dart(original_program, "normal", "original-normal")
        print("Original negative controls: all five awaited stages, idle close and both repeated-ready cases fail as expected; unclosed two-batch behavior passes", flush=True)


def main():
    global ORIGINAL, ORIGINALS, DART, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("RDREPO", "./rustdesk")))
    parser.add_argument("--dart", type=Path, default=shutil.which("dart"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.dart or not args.dart.is_file():
        parser.error("--dart must identify an actual Dart SDK executable; async tests cannot be skipped")
    DART = args.dart.resolve()
    ORIGINAL = P.transform((args.source / P.SOURCE).read_bytes(), restore=True)
    # Every test starts from the complete exact pinned source, including when
    # the supplied checkout already contains the final overlay.
    ORIGINAL = ORIGINAL.replace(b"\r\n", b"\n")
    assert digest(ORIGINAL) == P.ORIGINAL_SHA
    model = P.transform((args.source / P.MODEL).read_bytes(), restore=True, name=P.MODEL).replace(b"\r\n", b"\n")
    assert digest(model) == P.MODEL_SHA
    ORIGINALS = {P.SOURCE: ORIGINAL, P.MODEL: model}
    with contextlib.ExitStack() as stack:
        if args.output:
            OUTPUT = args.output.absolute()
            OUTPUT.mkdir(parents=True, exist_ok=False)
        else:
            OUTPUT = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="mixel-file-dialog-tests-")))
        print("Pinned event-loop source: " + P.ORIGINAL_SHA + "; patched: " + digest(P.transform(ORIGINAL)), flush=True)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(LifecycleTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
