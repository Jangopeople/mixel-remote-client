#!/usr/bin/env python3
"""Verify task fixture partial creation and real SIGTERM cleanup boundaries."""
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "scripts/test-relay-ws-bridge-docker.py"
spec = importlib.util.spec_from_file_location("mixel_gateway_fixture", FILE)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class LifecycleTests(unittest.TestCase):
    def test_cleanup_attempts_every_resource_after_first_failure(self):
        commands = []

        def execute(command, **kwargs):
            self.assertEqual(kwargs["timeout"], 10)
            commands.append(command)
            return subprocess.CompletedProcess(command, 1 if len(commands) == 1 else 0, "", "failed" if len(commands) == 1 else "")

        with patch.object(fixture.subprocess, "run", side_effect=execute):
            with self.assertRaisesRegex(RuntimeError, "after attempting every resource"):
                fixture.cleanup(["owned-first", "owned-second"], "owned-network", "owned-volume")
        self.assertEqual(commands, [["docker", "rm", "-f", "owned-second"],
                                    ["docker", "rm", "-f", "owned-first"],
                                    ["docker", "network", "rm", "owned-network"],
                                    ["docker", "volume", "rm", "owned-volume"]])

    def test_absent_preenrolled_names_are_safe_to_clean(self):
        with patch.object(fixture.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "Error: No such container: owned-not-created")):
            fixture.cleanup(["owned-not-created"])

    def test_interrupted_creation_enrolls_resource_before_return(self):
        removals = []

        def docker(*arguments, **_kwargs):
            if arguments[0] == "run" and "-d" in arguments:
                raise SystemExit(143)

        def cleanup(containers, network, volume):
            removals.append((list(containers), network, volume))

        with patch.object(fixture, "docker", side_effect=docker), patch.object(fixture, "cleanup", side_effect=cleanup):
            with self.assertRaises(SystemExit) as stopped:
                fixture.main(skip_build=True)
        self.assertEqual(stopped.exception.code, 143)
        self.assertEqual(len(removals), 1)
        containers, network, volume = removals[0]
        self.assertEqual(len(containers), 1)
        self.assertTrue(containers[0].endswith("-hbbs"))
        self.assertTrue(network.endswith("-net") and volume.endswith("-data"))

    def test_cleanup_error_never_masks_python310_style_original_error(self):
        class OriginalFailure(RuntimeError):
            def __getattribute__(self, name):
                if name == "add_note":
                    raise AttributeError(name)
                return super().__getattribute__(name)

        def docker(*arguments, **_kwargs):
            if arguments[0] == "network":
                raise OriginalFailure("original creation error")

        diagnostics = io.StringIO()
        with patch.object(fixture, "docker", side_effect=docker), patch.object(fixture, "cleanup", side_effect=RuntimeError("cleanup error")), patch.object(fixture.sys, "stderr", diagnostics):
            with self.assertRaisesRegex(OriginalFailure, "original creation error"):
                fixture.main(skip_build=True)
        self.assertIn("cleanup error", diagnostics.getvalue())

    @unittest.skipUnless(os.name == "posix", "target Linux SIGTERM semantics")
    def test_real_builder_sigterm_runs_enrolled_cleanup_before_exit(self):
        with tempfile.TemporaryDirectory(prefix="mixel-builder-signal-") as temporary:
            folder = Path(temporary)
            calls = folder / "calls.jsonl"
            ready = folder / "created-marker"
            docker = folder / "docker"
            docker.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys, time
from pathlib import Path
arguments=sys.argv[1:]
with Path(os.environ['MIXEL_FIXTURE_CALLS']).open('a') as file:
    file.write(json.dumps(arguments)+'\n')
if arguments[0]=='run' and '-d' in arguments:
    Path(os.environ['MIXEL_FIXTURE_READY']).write_text('daemon resource created before CLI return')
    while True:
        time.sleep(0.05)
''')
            docker.chmod(0o755)
            environment = {**os.environ, "PATH": str(folder) + os.pathsep + os.environ["PATH"],
                           "MIXEL_FIXTURE_CALLS": str(calls), "MIXEL_FIXTURE_READY": str(ready)}
            process = subprocess.Popen([sys.executable, str(FILE), "--skip-build"], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 143, stdout + stderr)
                commands = [json.loads(line) for line in calls.read_text().splitlines()]
                enrolled = next(command for command in commands if command[0] == "run" and "-d" in command)
                container = enrolled[enrolled.index("--name") + 1]
                self.assertIn(["rm", "-f", container], commands)
                self.assertTrue(any(command[:2] == ["network", "rm"] for command in commands))
                self.assertTrue(any(command[:2] == ["volume", "rm"] for command in commands))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
