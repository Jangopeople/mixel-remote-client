#!/usr/bin/env python3
"""Exercise bounded displayfd readiness and exact-child Xvfb cleanup."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location("mixel_clipboard_tests", Path(__file__).with_name("test-support-clipboard.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@unittest.skipUnless(os.name == "posix", "displayfd uses POSIX descriptors")
class OwnedServerTests(unittest.TestCase):
    def child(self, source):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        process = subprocess.Popen([sys.executable, "-c", source, str(write_fd)], pass_fds=(write_fd,),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.close(write_fd)
        self.addCleanup(process.stderr.close)
        self.addCleanup(helper.stop_owned_process, process, .5)
        return process, read_fd

    def test_actual_complete_readiness(self):
        process, descriptor = self.child('import os,sys,time; os.write(int(sys.argv[1]),b"17\\n"); time.sleep(10)')
        self.assertEqual(helper.read_xvfb_display(process, descriptor, 2), ":17")
        helper.stop_owned_process(process, .5)
        self.assertIsNotNone(process.poll())

    def test_actual_exit_retains_startup_diagnostic(self):
        process, descriptor = self.child('import sys; sys.stderr.write("owned server startup failure\\n"); sys.exit(23)')
        with self.assertRaisesRegex(RuntimeError, "before readiness"):
            helper.read_xvfb_display(process, descriptor, 2)
        self.assertEqual(process.wait(timeout=2), 23)
        self.assertEqual(process.stderr.read(), b"owned server startup failure\n")

    def test_partial_record_is_bounded(self):
        process, descriptor = self.child('import os,sys,time; os.write(int(sys.argv[1]),b"17"); time.sleep(10)')
        with self.assertRaisesRegex(RuntimeError, "did not report readiness"):
            helper.read_xvfb_display(process, descriptor, .2)

    def test_invalid_records_never_invent_display(self):
        for record in (b"\n", b"not-a-display\n", b"17\nextra", b"65536\n", b"9" * 33 + b"\n"):
            with self.subTest(record=record):
                process, descriptor = self.child('import os,sys,time; os.write(int(sys.argv[1]),' + repr(record) + '); time.sleep(10)')
                with self.assertRaisesRegex(RuntimeError, "invalid|oversized"):
                    helper.read_xvfb_display(process, descriptor, 2)
                helper.stop_owned_process(process, .5)

    def test_actual_term_resistant_child_is_killed_and_reaped(self):
        process, descriptor = self.child('import os,sys,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.write(int(sys.argv[1]),b"18\\n"); time.sleep(10)')
        self.assertEqual(helper.read_xvfb_display(process, descriptor, 2), ":18")
        helper.stop_owned_process(process, .1)
        self.assertEqual(process.returncode, -signal.SIGKILL)

    def test_already_exited_child_is_not_signalled(self):
        process, _ = self.child('import sys; sys.exit(19)')
        self.assertEqual(process.wait(timeout=2), 19)
        helper.stop_owned_process(process, .1)
        self.assertEqual(process.returncode, 19)


if __name__ == "__main__":
    unittest.main(verbosity=2)
