#!/usr/bin/env python3
"""Focused ownership and failure-path checks for the HTTPS fixture runner."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name("test-support-session-https-linux.py")
SPEC = importlib.util.spec_from_file_location("session_fixture", PATH)
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
DESKTOP_SPEC = importlib.util.spec_from_file_location("desktop_session", PATH.with_name("test-support-session-linux.py"))
desktop = importlib.util.module_from_spec(DESKTOP_SPEC)
DESKTOP_SPEC.loader.exec_module(desktop)
NAME = "mixel-registration-proof-0123456789ab"
MANIFEST = {"kind": "mixel-isolated-registration-proof", "production_mutations": False,
            "network": NAME + "-net", "volume": NAME + "-data",
            "containers": [NAME + suffix for suffix in ("-hbbs", "-hbbr", "-router")]}


class FixtureTests(unittest.TestCase):
    def test_actual_remote_request_uses_automatic_https_without_cli_relay_flag(self):
        for blocked in (False, True):
            session = desktop.Session(Path("proofs"), Path("client.deb"), False, blocked)
            session.ids["host"] = "123456789"
            with patch.object(session, "query", return_value=0), patch.object(session, "launch") as launch, \
                    patch.object(session, "cm", return_value="real-cm"), patch.object(session, "screenshot"), \
                    patch.object(session, "pending_accept"), patch.object(desktop.time, "sleep"):
                session.request("argument-regression")
            expected = ["--connect", "123456789"] + ([] if blocked else ["--relay"])
            launch.assert_called_once_with("controller", expected)

    def test_actual_file_request_uses_automatic_https_without_cli_relay_flag(self):
        for blocked in (False, True):
            session = desktop.Session(Path("proofs"), Path("client.deb"), False, blocked)
            session.ids["host"] = "123456789"
            with patch.object(session, "stop_controller_gui"), patch.object(session, "run"), \
                    patch.object(session, "launch") as launch, patch.object(session, "accept", side_effect=RuntimeError("stop before UI")):
                with self.assertRaisesRegex(RuntimeError, "stop before UI"):
                    session.files("hash-not-used-in-this-argument-test")
            expected = ["--file-transfer", "123456789"] + ([] if blocked else ["--relay"])
            launch.assert_called_once_with("controller", expected)

    def test_actual_child_timeout_runs_its_finally_and_remains_a_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child-cleanup-marker"
            code = ("import signal,sys,time;from pathlib import Path\n"
                    "signal.signal(signal.SIGTERM,lambda signum,frame:sys.exit(128+signum))\n"
                    "try:\n time.sleep(30)\n"
                    "finally:\n Path(" + repr(str(marker)) + ").write_text('actual finally ran')\n")
            with self.assertRaises(subprocess.TimeoutExpired):
                fixture.execute([sys.executable, "-c", code], timeout=1, capture=True)
            self.assertEqual(marker.read_text(), "actual finally ran")

    def test_manifest_rejects_live_or_unrelated_resources(self):
        for changes in ({"network": "bridge"}, {"volume": "production-data"},
                        {"containers": ["live-hbbs"]}, {"production_mutations": True}):
            with self.assertRaises(ValueError):
                fixture.validate_manifest({**MANIFEST, **changes})

    def test_missing_ownership_label_never_removes_resource(self):
        calls = []
        def run(arguments, **_kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 0, json.dumps([{"Config": {"Labels": {}}}]), "")
        with tempfile.TemporaryDirectory() as output, patch.object(fixture, "execute", run):
            errors = fixture.cleanup(MANIFEST, Path(output))
        self.assertEqual(len(errors), 5)
        self.assertFalse(any("rm" in arguments for arguments in calls))

    def test_capture_and_removal_failure_do_not_skip_other_owned_resources(self):
        removed = []
        def run(arguments, **_kwargs):
            if "inspect" in arguments:
                labels = {"Labels": fixture.LABEL, "Config": {"Labels": fixture.LABEL}}
                return subprocess.CompletedProcess(arguments, 0, json.dumps([labels]), "")
            if "logs" in arguments:
                raise subprocess.TimeoutExpired(arguments, 30)
            if "rm" in arguments:
                removed.append(arguments[-1])
                if arguments[-1].endswith("-router"):
                    raise subprocess.CalledProcessError(1, arguments)
            return subprocess.CompletedProcess(arguments, 0, "", "")
        with tempfile.TemporaryDirectory() as output, patch.object(fixture, "execute", run):
            errors = fixture.cleanup(MANIFEST, Path(output))
        self.assertEqual(set(removed), set(MANIFEST["containers"] + [MANIFEST["network"], MANIFEST["volume"]]))
        self.assertEqual(len(errors), 4)

    def test_success_removes_exact_manifest_resources(self):
        removed = []
        def run(arguments, **_kwargs):
            if "inspect" in arguments:
                return subprocess.CompletedProcess(arguments, 0, json.dumps([{"Labels": fixture.LABEL, "Config": {"Labels": fixture.LABEL}}]), "")
            if "rm" in arguments:
                removed.append(arguments[-1])
            return subprocess.CompletedProcess(arguments, 0, "", "")
        with tempfile.TemporaryDirectory() as output, patch.object(fixture, "execute", run):
            self.assertEqual(fixture.cleanup(MANIFEST, Path(output)), [])
        self.assertEqual(removed, list(reversed(MANIFEST["containers"])) + [MANIFEST["network"], MANIFEST["volume"]])

    def test_session_failure_still_cleans_and_writes_failed_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deb = root / "client.deb"
            deb.write_bytes(b"fixture")
            output = root / "proofs"
            failure = subprocess.CalledProcessError(9, ["actual-desktop-harness"])
            def run(arguments, **_kwargs):
                if "--keep-fixture" in arguments:
                    directory = Path(arguments[arguments.index("--keep-fixture") + 1])
                    directory.mkdir()
                    manifest = {**MANIFEST, "fixture_address": "192.168.1.2",
                                "public_pin_path": str(directory / "pin"),
                                "public_test_ca_path": str(directory / "ca.crt")}
                    (directory / "manifest.json").write_text(json.dumps(manifest))
                elif arguments[0] == "docker":
                    return subprocess.CompletedProcess(arguments, 0, json.dumps([{"Config": {"Labels": fixture.LABEL}, "Labels": fixture.LABEL}]), "")
                else:
                    raise failure
                return subprocess.CompletedProcess(arguments, 0, "", "")
            with patch.object(sys, "argv", [str(PATH), "--deb", str(deb), "--proofs", str(output)]), \
                    patch.object(fixture, "execute", run), patch.object(fixture, "cleanup", return_value=[]) as cleanup:
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    fixture.main()
            self.assertIs(caught.exception, failure)
            cleanup.assert_called_once()
            lifecycle = json.loads((output / "fixture-lifecycle.json").read_text())
            self.assertEqual(lifecycle["result"], "failed")
            self.assertEqual(lifecycle["owned_fixture_cleanup"], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
