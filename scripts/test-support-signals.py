#!/usr/bin/env python3
"""Run the actual generated Linux entrypoint against fatal native socket writes."""
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get('RDREPO', ROOT / 'rustdesk'))
SPEC = importlib.util.spec_from_file_location('mixel_support_signals', ROOT / 'scripts/patch-support-signals.py')
PATCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCHER)


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    for end in range(opening + 1, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[start:end + 1]
    raise RuntimeError('Incomplete native runner entrypoint')


class LinuxRunnerSignals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = subprocess.run(
            ['git', '-C', str(UPSTREAM), 'show', 'HEAD:flutter/linux/main.cc'],
            check=True, capture_output=True, text=True).stdout
        cls.patched = PATCHER.patch(cls.original)

    def test_generated_entrypoint_is_idempotent_and_before_library_loading(self):
        self.assertEqual(PATCHER.patch(self.patched), self.patched)
        main = function(self.patched, 'int main(int argc, char** argv)')
        self.assertTrue(main.startswith(PATCHER.PATCHED_ENTRY))
        self.assertLess(main.index('std::signal(SIGPIPE, SIG_IGN)'), main.index('flutter_rustdesk_core_main()'))
        self.assertIn('== SIG_ERR', main)
        self.assertIn('std::perror(', main)
        self.assertIn('return EXIT_FAILURE;', main)
        self.assertEqual(self.patched.count('std::signal('), 1)
        self.assertEqual(PATCHER.patch(self.original.replace('\n', '\r\n').replace('\r\n', '\n')), self.patched)

    def test_upstream_drift_and_partial_signal_edits_fail_closed(self):
        for changed in [self.original.replace(PATCHER.ENTRY, 'int renamed_main(int argc, char** argv) {\n'),
                        self.original.replace(PATCHER.INCLUDE, '#include <different-loader.h>\n'),
                        self.patched.replace('std::signal(SIGPIPE, SIG_IGN)', 'std::signal(SIGTERM, SIG_IGN)'),
                        self.patched + '\n' + PATCHER.PATCHED_ENTRY]:
            with self.subTest(source=changed[-80:]), self.assertRaises(RuntimeError):
                PATCHER.patch(changed)

    def test_actual_patcher_repeats_bytes_and_preserves_source_on_rejection(self):
        with tempfile.TemporaryDirectory(prefix='mixel-signal-patch-') as temporary:
            repo = Path(temporary)
            file = repo / 'flutter/linux/main.cc'
            file.parent.mkdir(parents=True)
            file.write_bytes(self.original.replace('\n', '\r\n').encode('utf-8'))
            environment = {**os.environ, 'RDREPO': str(repo)}
            command = [os.sys.executable, str(ROOT / 'scripts/patch-support-signals.py')]
            subprocess.run(command, env=environment, check=True, capture_output=True)
            patched = file.read_bytes()
            self.assertEqual(file.read_text(encoding='utf-8'), self.patched)
            subprocess.run(command, env=environment, check=True, capture_output=True)
            self.assertEqual(file.read_bytes(), patched)
            changed = patched.replace(b'std::signal(SIGPIPE, SIG_IGN)', b'std::signal(SIGTERM, SIG_IGN)')
            file.write_bytes(changed)
            failed = subprocess.run(command, env=environment, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('Pinned upstream Linux runner signal initialization changed', failed.stderr)
            self.assertEqual(file.read_bytes(), changed)

    @unittest.skipIf(os.name == 'nt', 'SIGPIPE is a POSIX signal; Windows runner is unchanged')
    def test_actual_main_preserves_epipe_and_other_termination_signals(self):
        compiler = shutil.which('c++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'A C++ compiler is required for the native signal regression')
        with tempfile.TemporaryDirectory(prefix='mixel-native-signals-') as temporary:
            directory = Path(temporary)
            stub = r'''
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sys/socket.h>
#include <unistd.h>
struct MyApplication {};
#define g_autoptr(type) type*
#define G_APPLICATION(value) value
bool gIsConnectionManager = false;
MyApplication* my_application_new() {return nullptr;}
int g_application_run(MyApplication*,int,char**) {return 0;}
const char* scenario = nullptr;
bool flutter_rustdesk_core_main() {
  // This boundary replaces only dlopen/GTK. The actual production main body
  // must already have set its policy before any embedded/native code runs.
  if (std::strcmp(scenario,"term")==0) std::raise(SIGTERM);
  if (std::strcmp(scenario,"int")==0) std::raise(SIGINT);
  if (std::strcmp(scenario,"signal")==0) std::raise(13);
  int sockets[2];
  if (socketpair(AF_UNIX,SOCK_STREAM,0,sockets)!=0) std::abort();
  close(sockets[1]);
  char value='x';
  auto written=write(sockets[0],&value,1); // no MSG_NOSIGNAL or Rust runtime
  if (written!=-1 || errno!=EPIPE) std::abort();
  close(sockets[0]);
  std::puts("PASS: actual native broken socket returns EPIPE and process survives");
  return false;
}
__INVALID_SIGNAL__
__ACTUAL_MAIN__
int main(int argc,char**argv) {
  scenario=argc>1?argv[1]:"write";
  // An embedded C++ executable starts with SIG_DFL. Set it explicitly so the
  // launching Python process's own ignored SIGPIPE cannot hide the defect.
  if (std::signal(13,SIG_DFL)==SIG_ERR) std::abort();
  if (std::signal(SIGTERM,SIG_DFL)==SIG_ERR) std::abort();
  if (std::signal(SIGINT,SIG_DFL)==SIG_ERR) std::abort();
  return mixel_runner_main(argc,argv);
}
'''
            def build(source, name, invalid=False):
                actual = function(source, 'int main(int argc, char** argv)').replace(
                    'int main(int argc, char** argv)', 'int mixel_runner_main(int argc, char** argv)', 1)
                code = stub.replace('__ACTUAL_MAIN__', actual).replace(
                    '__INVALID_SIGNAL__', '#undef SIGPIPE\n#define SIGPIPE -1' if invalid else '')
                file = directory / (name + '.cc')
                binary = directory / name
                file.write_text(code, encoding='utf-8')
                subprocess.run([compiler, '-std=c++11', '-Wall', '-Wextra', '-Werror', str(file), '-o', str(binary)], check=True)
                return binary

            original = build(self.original, 'original')
            fatal = subprocess.run([str(original), 'write'], capture_output=True, text=True, timeout=10)
            self.assertEqual(fatal.returncode, -signal.SIGPIPE, 'Positive control must die on actual broken native write')
            print('PASS: unpatched embedded runner reproduces fatal SIGPIPE13 from native socket write', flush=True)
            patched = build(self.patched, 'patched')
            for scenario in ['write', 'signal']:
                result = subprocess.run([str(patched), scenario], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('returns EPIPE and process survives', result.stdout)
            for scenario, number in [('term', signal.SIGTERM), ('int', signal.SIGINT)]:
                result = subprocess.run([str(patched), scenario], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, -number, 'Unrelated termination signals must remain effective')
            invalid = build(self.patched, 'invalid', invalid=True)
            failure = subprocess.run([str(invalid), 'write'], capture_output=True, text=True, timeout=10)
            self.assertEqual(failure.returncode, 1)
            self.assertIn('Mixel Remote: failed to initialize broken-pipe handling', failure.stderr)
            self.assertNotIn('process survives', failure.stdout)
            print('PASS: actual patched runner survives SIGPIPE/EPIPE, preserves SIGINT/SIGTERM, and fails startup on SIG_ERR', flush=True)


if __name__ == '__main__':
    unittest.main()
