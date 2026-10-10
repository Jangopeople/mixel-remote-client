#!/usr/bin/env python3
"""Focused ownership and failure-path checks for the HTTPS fixture runner."""
import importlib.util
import copy
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import signal
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
        for blocked, mixed in ((False, False), (True, False), (False, True)):
            session = desktop.Session(Path("proofs"), Path("client.deb"), False, blocked, mixed_transports=mixed)
            session.ids["host"] = "123456789"
            with patch.object(session, "query", return_value=0), patch.object(session, "launch") as launch, \
                    patch.object(session, "cm", return_value="real-cm"), patch.object(session, "screenshot"), \
                    patch.object(session, "pending_accept"), patch.object(desktop.time, "sleep"):
                session.request("argument-regression")
            expected = ["--connect", "123456789"] + ([] if blocked or mixed else ["--relay"])
            launch.assert_called_once_with("controller", expected)

    def test_actual_file_request_uses_automatic_https_without_cli_relay_flag(self):
        for blocked, mixed in ((False, False), (True, False), (False, True)):
            session = desktop.Session(Path("proofs"), Path("client.deb"), False, blocked, mixed_transports=mixed)
            session.ids["host"] = "123456789"
            with patch.object(session, "stop_controller_gui"), patch.object(session, "run"), \
                    patch.object(session, "launch") as launch, patch.object(session, "accept", side_effect=RuntimeError("stop before UI")):
                with self.assertRaisesRegex(RuntimeError, "stop before UI"):
                    session.files("hash-not-used-in-this-argument-test")
            expected = ["--file-transfer", "123456789"] + ([] if blocked or mixed else ["--relay"])
            launch.assert_called_once_with("controller", expected)

    def test_mixed_firewall_preserves_host_initial_udp_and_controller_native_tcp(self):
        session = desktop.Session(Path("proofs"), Path("client.deb"), False, False, mixed_transports=True)
        with patch.object(session, "run") as run:
            session.firewall("host")
            session.firewall("controller")
            session.firewall("host", after_registration=True)
        self.assertEqual(run.call_args_list, [
            unittest.mock.call("host", ["iptables", "-A", "OUTPUT", "-p", "udp", "--dport", "21116", "-j", "ACCEPT"], user="root"),
            unittest.mock.call("controller", ["iptables", "-A", "OUTPUT", "-p", "udp", "--dport", "21115:21119", "-j", "REJECT"], user="root"),
            unittest.mock.call("host", ["iptables", "-A", "OUTPUT", "-p", "tcp", "--dport", "21115:21119", "-j", "REJECT"], user="root"),
        ])

    def test_mixed_transport_rejects_initial_https_instead_of_claiming_native_registration(self):
        session = desktop.Session(Path("proofs"), Path("client.deb"), False, False,
                                  {"pin": "test-public-pin", "network": "owned-test", "address": "192.168.48.2"}, True)
        sockets = "ESTAB 0 0 192.168.48.3:40000 192.168.48.2:443 users:((mixel-remote))\n"
        rules = "[4:320] -A OUTPUT -p udp -m udp --dport 21116 -j ACCEPT\n"
        with patch.object(session, "fixture_tcp", return_value=(sockets, sockets.splitlines())), \
                patch.object(session, "run", return_value=subprocess.CompletedProcess([], 0, rules, "")):
            with self.assertRaisesRegex(AssertionError, "unexpectedly used HTTPS"):
                session.native_udp_proof("initial-registration")

    def test_mixed_transport_cannot_pass_without_actual_controller_native_tcp_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            proofs = Path(temporary)
            for role in ("host", "controller"):
                (proofs / role).mkdir()
            session = desktop.Session(proofs, Path("client.deb"), False, False,
                                      {"pin": "test-public-pin", "network": "owned-test", "address": "192.168.48.2"}, True)
            session.initial_udp_packets = 1
            session.server = {"host": 100, "controller": 100}
            sockets = "ESTAB 0 0 192.168.48.3:40000 192.168.48.2:443 users:((mixel-remote))\n"
            rules = "[0:0] -A OUTPUT -p tcp -m tcp --dport 21116 -j ACCEPT\n"
            with patch.object(session, "query", return_value=1), patch.object(session, "native_udp_proof", return_value=2), \
                    patch.object(session, "app_tcp_evidence", side_effect=lambda role: (sockets, [{"address": "192.168.48.2", "port": 443, "pids": [100]}] + ([{"address": "192.168.48.2", "port": 21117, "pids": [101]}] if role == "controller" else []))), \
                    patch.object(session, "run", return_value=subprocess.CompletedProcess([], 0, rules, "")):
                with self.assertRaisesRegex(AssertionError, "native TCP21116 control"):
                    session.active_mixed_proof()
            self.assertFalse((proofs / "mixed-transport-proof.json").exists())

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

    def test_every_peer_cleanup_command_is_bounded_and_all_are_attempted(self):
        session = desktop.Session(Path("proofs"), Path("client.deb"), False, True)
        session.created = ["host", "controller"]
        limits = []
        def fail(arguments, **kwargs):
            limits.append(kwargs["timeout"])
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])
        with patch.object(session, "run", side_effect=lambda role, arguments, **kwargs: fail(arguments, **kwargs)), patch.object(desktop, "command", side_effect=fail):
            errors = session.cleanup()
        self.assertEqual(limits, [2] * 6 + [4, 4, 3])
        self.assertEqual(len(errors), 9)
        self.assertEqual(sum(limits), 23)
        limits.clear()
        with patch.object(session, "capture") as capture, patch.object(desktop, "command", side_effect=fail):
            self.assertEqual(len(session.cleanup(interrupted=True)), 3)
        capture.assert_not_called()
        self.assertEqual(limits, [4, 4, 3])

    def test_combined_peer_and_relay_cleanup_limits_fit_sigterm_grace(self):
        limits = []
        def run(arguments, **kwargs):
            limits.append(kwargs["timeout"] + kwargs["termination_grace"] + 2 * kwargs["kill_grace"])
            if "inspect" in arguments:
                return subprocess.CompletedProcess(arguments, 0, json.dumps([{"Labels": fixture.LABEL, "Config": {"Labels": fixture.LABEL}}]), "")
            raise subprocess.TimeoutExpired(arguments, kwargs["timeout"])
        with tempfile.TemporaryDirectory() as output, patch.object(fixture, "execute", run):
            self.assertEqual(len(fixture.cleanup(MANIFEST, Path(output))), 8)
        self.assertEqual(len(limits), 13)
        self.assertLess(23 + sum(limits), 60)
        limits.clear()
        with tempfile.TemporaryDirectory() as output, patch.object(fixture, "execute", run):
            self.assertEqual(len(fixture.cleanup(MANIFEST, Path(output), interrupted=True)), 5)
        self.assertEqual(len(limits), 10)
        self.assertLess(11 + sum(limits), 60)

    @unittest.skipUnless(os.name == "posix", "target Linux SIGTERM semantics")
    def test_real_sigterm_runs_actual_peer_cleanup_before_parent_timeout_returns(self):
        with tempfile.TemporaryDirectory(prefix="mixel-peer-sigterm-") as temporary:
            folder = Path(temporary)
            marker = folder / "cleanup.json"
            calls = folder / "docker-calls.jsonl"
            executable = folder / "docker"
            executable.write_text("#!" + sys.executable + "\nimport json,sys\nfrom pathlib import Path\nwith Path(" + repr(str(calls)) + ").open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n")
            executable.chmod(0o755)
            script = folder / "child.py"
            script.write_text("import importlib.util,signal,sys,time,json\nfrom pathlib import Path\n"
                "spec=importlib.util.spec_from_file_location('actual_session'," + repr(str(PATH.with_name('test-support-session-linux.py'))) + ")\n"
                "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\n"
                "session=module.Session(Path(" + repr(str(folder)) + "),Path('synthetic.deb'),False,True)\n"
                "session.created=['host','controller']\n"
                "signal.signal(signal.SIGTERM,lambda signum,frame:sys.exit(128+signum))\n"
                "try:time.sleep(30)\n"
                "finally:Path(" + repr(str(marker)) + ").write_text(json.dumps({'errors':session.cleanup(interrupted=True),'result':'failed'}))\n")
            with patch.dict(os.environ, {"PATH": str(folder) + os.pathsep + os.environ["PATH"]}):
                with self.assertRaises(subprocess.TimeoutExpired):
                    fixture.execute([sys.executable, str(script)], timeout=1, capture=True)
            self.assertEqual(json.loads(marker.read_text()), {"errors": [], "result": "failed"})
            commands = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual(len(commands), 3)
            self.assertEqual([command[:2] for command in commands], [["rm", "-f"], ["rm", "-f"], ["image", "rm"]])

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
                    self.assertIn("--mixed-transports", arguments)
                    self.assertNotIn("--native-blocked", arguments)
                    raise failure
                return subprocess.CompletedProcess(arguments, 0, "", "")
            with patch.object(sys, "argv", [str(PATH), "--deb", str(deb), "--proofs", str(output), "--mixed-transports"]), \
                    patch.object(fixture, "execute", run), patch.object(fixture, "cleanup", return_value=[]) as cleanup:
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    fixture.main()
            self.assertIs(caught.exception, failure)
            cleanup.assert_called_once()
            lifecycle = json.loads((output / "fixture-lifecycle.json").read_text())
            self.assertEqual(lifecycle["result"], "failed")
            self.assertEqual(lifecycle["owned_fixture_cleanup"], "completed")
            self.assertIs(lifecycle["mixed_transports"], True)


class SavedPasswordProofTests(unittest.TestCase):
    def test_saved_password_mode_requires_native_isolation_before_any_peer_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            deb = Path(temporary) / "synthetic.deb"
            deb.write_bytes(b"synthetic")
            for extra in ([], ["--require-native"], ["--require-native", "--native-blocked"]):
                with self.subTest(extra=extra), patch.object(sys, "argv", [str(desktop.__file__), "--deb", str(deb), "--proofs", str(Path(temporary) / "proofs"), "--saved-password-consent", *extra]), \
                        patch.object(desktop.platform, "machine", return_value="x86_64"), patch.object(desktop, "command") as commands:
                    with self.assertRaisesRegex(SystemExit, "requires native amd64"):
                        desktop.main()
                    commands.assert_not_called()
            with patch.object(sys, "argv", [str(PATH), "--deb", str(deb), "--proofs", str(Path(temporary) / "proofs"), "--saved-password-consent"]), \
                    patch.object(fixture, "execute") as commands:
                with self.assertRaisesRegex(SystemExit, "requires native blocked"):
                    fixture.main()
                commands.assert_not_called()

    def test_exact_collected_linux_bytes_are_bound_to_application_source_independent_of_qa(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deb = root / "artifacts/linux/client.deb"
            deb.parent.mkdir(parents=True)
            deb.write_bytes(b"owned synthetic artifact provenance control")
            digest = hashlib.sha256(deb.read_bytes()).hexdigest()
            source, run_id = "7" * 40, "123456789"
            url = "https://github.com/Jangopeople/mixel-remote-client/actions/runs/" + run_id
            metadata = {"id": int(run_id), "head_sha": source, "event": "workflow_dispatch", "html_url": url,
                        "status": "completed", "conclusion": "failure"}
            provenance = {"repository": "Jangopeople/mixel-remote-client", "build_run_id": int(run_id), "build_url": url,
                          "event": "workflow_dispatch", "head_sha": source, "source_commit": source,
                          "application_source_baseline": source, "collected_artifacts": {"linux": {
                              "artifact_id": 123, "artifact_digest": "sha256:" + "a" * 64,
                              "files_sha256": {"artifacts/linux/client.deb": digest}}}}
            def write(meta, prov):
                (root / "run-metadata.json").write_text(json.dumps(meta))
                (root / "artifact-provenance.json").write_text(json.dumps(prov))
            write(metadata, provenance)
            evidence = desktop.verify_application_artifact(root, deb, run_id, source, digest)
            self.assertEqual(evidence["application_source_baseline"], source)
            self.assertEqual(evidence["application_run_conclusion"], "failure")
            self.assertEqual(evidence["artifact_sha256"], digest)
            negatives = [
                ("metadata run", ["metadata", "id"], 1),
                ("metadata source", ["metadata", "head_sha"], "8" * 40),
                ("metadata URL", ["metadata", "html_url"], url + "0"),
                ("metadata event", ["metadata", "event"], "push"),
                ("repository", ["provenance", "repository"], "other/repository"),
                ("artifact run", ["provenance", "build_run_id"], 1),
                ("artifact URL", ["provenance", "build_url"], url + "0"),
                ("artifact event", ["provenance", "event"], "push"),
                *[(field, ["provenance", field], "8" * 40) for field in ("head_sha", "source_commit", "application_source_baseline")],
                ("artifact id", ["provenance", "collected_artifacts", "linux", "artifact_id"], 0),
                ("boolean artifact id", ["provenance", "collected_artifacts", "linux", "artifact_id"], True),
                ("archive digest", ["provenance", "collected_artifacts", "linux", "artifact_digest"], "sha256:invalid"),
                ("old mapped bytes", ["provenance", "collected_artifacts", "linux", "files_sha256", "artifacts/linux/client.deb"], "0" * 64),
            ]
            for label, keys, value in negatives:
                with self.subTest(label=label):
                    documents = {"metadata": copy.deepcopy(metadata), "provenance": copy.deepcopy(provenance)}
                    item = documents
                    for key in keys[:-1]:
                        item = item[key]
                    item[keys[-1]] = value
                    write(documents["metadata"], documents["provenance"])
                    with self.assertRaises((AssertionError, ValueError)):
                        desktop.verify_application_artifact(root, deb, run_id, source, digest)
            write(metadata, provenance)
            deb.write_bytes(b"different actual bytes")
            with self.assertRaisesRegex(AssertionError, "Actual DEB bytes"):
                desktop.verify_application_artifact(root, deb, run_id, source, digest)
            outside = root / "outside.deb"
            outside.write_bytes(b"synthetic")
            with self.assertRaisesRegex(ValueError, "collected Linux artifact"):
                desktop.verify_application_artifact(root, outside, run_id, source, digest)
            for bad_run, bad_source, bad_hash in (("0123", source, digest), (run_id, source[:7], digest), (run_id, source, digest[:7]), (None, source, digest)):
                with self.assertRaisesRegex(ValueError, "explicit run"):
                    desktop.verify_application_artifact(root, deb, bad_run, bad_source, bad_hash)

    def test_password_mode_starts_ordinary_guis_and_preserves_exact_host_preferences(self):
        session = desktop.PasswordSession(Path("proofs"), Path("client.deb"), False, True)
        with patch.object(session, "launch", return_value=123) as launch, patch.object(session, "windows", return_value=["owned"]):
            for role in ("host", "controller"):
                session.start_gui(role)
            self.assertEqual(launch.call_args_list, [unittest.mock.call("host", []), unittest.mock.call("controller", [])])
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "Mixel-Remote2.toml"
            config.write_text('[options]\nkey = "public-synthetic-pin"\n')
            commands = []
            def run(_role, arguments, **_kwargs):
                commands.append(arguments)
                if arguments[:2] == ["python3", "-c"]:
                    exec(compile(arguments[2].replace("/home/guest/.config/mixel-remote/Mixel-Remote2.toml", str(config)), "actual preference seed", "exec"), {})
            with patch.object(session, "run", side_effect=run):
                session.firewall("host")
            self.assertIn('approve-mode = "password"\nverification-method = "use-permanent-password"\n', config.read_text())
            self.assertIn('key = "public-synthetic-pin"', config.read_text())
            self.assertEqual([item[1] for item in commands if item[0] == "iptables"], ["-A", "-A"])

    def exercise_flow(self, failure=None):
        with tempfile.TemporaryDirectory() as temporary:
            session = desktop.PasswordSession(Path(temporary), Path("client.deb"), False, True)
            session.ids, session.gui_pid = {"host": "synthetic-id"}, {"host": 100}
            saved = {"approve-mode": "password", "verification-method": "use-permanent-password", "key": "public-test-pin"}
            state = {"guard": False, "authorized": True, "requested": False, "password": None}
            manifest, events = {}, []
            def run(role, arguments, **kwargs):
                self.assertEqual((role, arguments[:2], kwargs), ("host", [desktop.EXE, "--password"], {"user": "root"}))
                state["password"] = arguments[2]
                return subprocess.CompletedProcess(arguments, 0, "Done!", "")
            def launch(role, arguments):
                events.append(("launch", role, arguments[:2]))
                if role == "host":
                    self.assertEqual(arguments, [desktop.URI])
                    state["guard"] = True
                    return 200
                self.assertEqual(arguments, ["--connect", "synthetic-id", "--password", state["password"]])
                state["authorized"] = not state["guard"]
            def query(_role, kind, content=None):
                if kind == "Options":
                    return dict(saved)
                if kind == "VideoConnCount":
                    return 1 if state["authorized"] or (failure == "password bypass" and state["requested"]) else 0
                if content[0] == "permanent-password":
                    return [content[0], state["password"]]
                return [content[0], "attended-runtime-v2" if state["guard"] else ""]
            def pending(label):
                events.append(("pending", label))
                state["requested"] = True
            def accept(label, already_requested):
                self.assertTrue(already_requested)
                events.append(("accept", label))
                state["authorized"] = True
            def fresh(label):
                events.append(("video", label))
                if failure == "stale accepted video" and "accepted" in label:
                    raise RuntimeError("unchanged strict video gate rejected")
            clock = itertools.count(100.0, .25)
            with patch.object(session, "run", side_effect=run), patch.object(session, "launch", side_effect=launch), \
                    patch.object(session, "query", side_effect=query), patch.object(session, "windows", return_value=["original-window"]), \
                    patch.object(session, "activate"), patch.object(session, "gui"), patch.object(session, "screenshot"), \
                    patch.object(session, "fresh_video", side_effect=fresh), patch.object(session, "disconnect"), \
                    patch.object(session, "stop_controller_gui"), patch.object(session, "process_snapshot", return_value={"exit_statuses": [{"pid": 200, "wait_status": 0}]}), \
                    patch.object(desktop.Session, "health", return_value=True), patch.object(session, "pending_accept", side_effect=pending), \
                    patch.object(session, "connect", side_effect=accept), patch.object(session, "active_https_proof") as tls, \
                    patch.object(desktop.time, "monotonic", side_effect=lambda: next(clock)), patch.object(desktop.time, "sleep"), \
                    patch.object(sys, "stdout", io.StringIO()):
                if failure:
                    with self.assertRaises((AssertionError, RuntimeError)):
                        session.saved_password_consent(manifest)
                    self.assertNotIn("actual_accept_then_authorized_video", manifest)
                    tls.assert_not_called()
                else:
                    session.saved_password_consent(manifest)
                    self.assertGreaterEqual(manifest["attended_auth0_observations"][-1]["seconds"], 12)
                    self.assertTrue(manifest["valid_password_baseline_autoauthorized_without_accept"])
                    self.assertTrue(manifest["saved_preferences_and_password_preserved"])
                    self.assertTrue(manifest["warm_uri_handoff"]["same_original_gui_kernel_lease_verified_after_sender_exit"])
                    self.assertLess(events.index(("video", "valid-password-baseline-video")), events.index(("launch", "host", [desktop.URI])))
                    self.assertLess(events.index(("pending", "valid-password-attended-after-wait")), events.index(("accept", "valid-password-attended")))
                    tls.assert_called_once_with("valid-password-attended")
            return events

    def test_exact_flow_keeps_positive_password_control_before_guard_and_accept_after_twelve_seconds(self):
        self.exercise_flow()

    def test_password_bypass_and_stale_video_remain_failures_before_success_flags(self):
        bypass = self.exercise_flow("password bypass")
        self.assertFalse(any(event[0] == "accept" for event in bypass))
        self.exercise_flow("stale accepted video")

    def test_wrapper_forwards_exact_native_password_and_provenance_contract_and_cleans_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deb = root / "client.deb"
            deb.write_bytes(b"synthetic")
            output = root / "proofs"
            arguments = [str(PATH), "--deb", str(deb), "--proofs", str(output), "--require-native", "--saved-password-consent",
                         "--artifact-run-id", "123", "--artifact-root", str(root), "--artifact-source-commit", "7" * 40, "--expected-sha256", "0" * 64]
            def run(command, **_kwargs):
                if "--keep-fixture" in command:
                    directory = Path(command[command.index("--keep-fixture") + 1])
                    directory.mkdir()
                    (directory / "manifest.json").write_text(json.dumps({**MANIFEST, "fixture_address": "192.168.1.2",
                        "public_pin_path": str(directory / "pin"), "public_test_ca_path": str(directory / "ca.crt")}))
                elif command[0] == "docker":
                    return subprocess.CompletedProcess(command, 0, json.dumps([{"Config": {"Labels": fixture.LABEL}, "Labels": fixture.LABEL}]), "")
                else:
                    for flag in ("--require-native", "--saved-password-consent", "--native-blocked"):
                        self.assertIn(flag, command)
                    for flag, value in (("--artifact-run-id", "123"), ("--artifact-root", str(root)),
                                        ("--artifact-source-commit", "7" * 40), ("--expected-sha256", "0" * 64)):
                        self.assertEqual(command[command.index(flag) + 1], value)
                    raise RuntimeError("intentional child proof failure")
                return subprocess.CompletedProcess(command, 0, "", "")
            with patch.object(sys, "argv", arguments), patch.object(fixture, "execute", run), patch.object(fixture, "cleanup", return_value=[]) as cleanup:
                with self.assertRaisesRegex(RuntimeError, "intentional child"):
                    fixture.main()
            cleanup.assert_called_once()
            self.assertEqual(json.loads((output / "fixture-lifecycle.json").read_text())["result"], "failed")


class TransportOracleTests(unittest.TestCase):
    @staticmethod
    def socket(port=443, pid=100, peer="192.168.48.2", process="mixel-remote", local=41000):
        return f'ESTAB 0 0 192.168.48.3:{local} {peer}:{port} users:(("{process}",pid={pid},fd=20))\n'

    def snapshots(self, mixed=False):
        return {"host": self.socket() + ("" if mixed else self.socket(local=41001)),
                "controller": self.socket() + self.socket(port=21117 if mixed else 443, pid=101, local=41001)}

    def exercise(self, snapshots, mixed=False, executable=desktop.EXE):
        with tempfile.TemporaryDirectory(prefix="mixel-socket-oracle-") as temporary:
            proofs = Path(temporary)
            for role in snapshots:
                (proofs / role).mkdir()
            session = desktop.Session(proofs, Path("client.deb"), False, not mixed,
                {"pin": "public-fixture-pin", "network": "owned-fixture", "address": "192.168.48.2"}, mixed)
            session.server = {"host": 100, "controller": 100}
            session.initial_udp_packets = 1
            def run(role, arguments, **kwargs):
                if arguments[0] == "ss":
                    output = snapshots[role]
                elif arguments[0] == "readlink":
                    output = executable + "\n"
                elif arguments[0] == "iptables-save":
                    output = "[2:300] -A OUTPUT -p " + ("udp" if role == "host" else "tcp") + " -m tcp --dport 21116 -j ACCEPT\n"
                else:
                    raise AssertionError(arguments)
                return subprocess.CompletedProcess(arguments, 0, output, "")
            with patch.object(session, "run", side_effect=run), \
                    patch.object(session, "other_peer_addresses", side_effect=lambda role: {"192.168.48.4" if role == "host" else "192.168.48.3"}), \
                    patch.object(session, "query", side_effect=lambda role, kind: 1 if kind == "VideoConnCount" else [1, True]), \
                    patch.object(sys, "stdout", io.StringIO()):
                if mixed:
                    session.active_mixed_proof()
                else:
                    session.active_https_proof()
            return json.loads((proofs / ("mixed-transport-proof.json" if mixed else "https-transport-proof.json")).read_text())

    def test_exact_app_registration_and_relay_sockets_pass(self):
        result = self.exercise(self.snapshots())
        self.assertEqual(len(result["host"]["connections"]), 2)
        self.assertEqual(result["controller"]["connections"][1]["pids"], [101])
        self.assertNotEqual(result["host"]["connections"][0]["local_endpoint"], result["host"]["connections"][1]["local_endpoint"])

    def test_mixed_native_udp_and_exact_host_tls_relay_pass(self):
        result = self.exercise(self.snapshots(mixed=True), mixed=True)
        self.assertEqual(result["host_udp_packets"], 2)
        self.assertEqual(result["socket_owners"]["controller"]["connections"][1]["port"], 21117)

    def test_actual_mixed_oracle_waits_for_later_real_udp_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            proofs = Path(temporary)
            for role in ("host", "controller"):
                (proofs / role).mkdir()
            session = desktop.Session(proofs, Path("synthetic.deb"), False, False,
                                      {"pin": "public-fixture-pin", "network": "owned", "address": "192.168.48.2"}, True)
            session.initial_udp_packets = 2
            session.server = {"host": 100, "controller": 100}
            host = [{"port": 443, "pids": [100]}]
            controller = [{"port": 443, "pids": [100]}, {"port": 21117, "pids": [101]}]
            with patch.object(session, "native_udp_proof", side_effect=[2, 2, 3]) as udp, \
                    patch.object(session, "query", return_value=1), patch.object(session, "app_tcp_evidence", side_effect=[("", host), ("", controller)]), \
                    patch.object(session, "run", return_value=subprocess.CompletedProcess([], 0, "[3:300] -A OUTPUT -p tcp -m tcp --dport 21116 -j ACCEPT\n", "")), \
                    patch.object(desktop.time, "sleep"), patch.object(sys, "stdout", io.StringIO()):
                session.active_mixed_proof()
            self.assertEqual(udp.call_count, 3)
            self.assertEqual(json.loads((proofs / "mixed-transport-proof.json").read_text())["host_udp_packets"], 3)

    def test_actual_mixed_oracle_never_accepts_stopped_udp_registration_after_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = desktop.Session(Path(temporary), Path("synthetic.deb"), False, False, mixed_transports=True)
            session.initial_udp_packets = 2
            with patch.object(session, "native_udp_proof", return_value=2), patch.object(session, "query", return_value=1), \
                    patch.object(session, "app_tcp_evidence") as sockets, \
                    patch.object(desktop.time, "monotonic", side_effect=[0, 0, 46]), patch.object(desktop.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "Timed out: actual host UDP registration"):
                    session.active_mixed_proof()
            sockets.assert_not_called()
            self.assertFalse((Path(temporary) / "mixed-transport-proof.json").exists())

    def test_unrelated_https_and_other_process_cannot_stand_in_for_relay(self):
        for replacement in (self.socket(peer="203.0.113.10"), self.socket(process="curl"),
                            'ESTAB 0 0 192.168.48.3:41001 192.168.48.2:443\n'):
            snapshots = self.snapshots()
            snapshots["host"] = self.socket() + replacement
            with self.subTest(replacement=replacement), self.assertRaisesRegex(AssertionError, "two exact relay"):
                self.exercise(snapshots)

    def test_direct_opposite_peer_tcp_is_rejected_even_on_ephemeral_port(self):
        for mixed in (False, True):
            snapshots = self.snapshots(mixed)
            snapshots["host"] += self.socket(peer="192.168.48.4", port=37851)
            with self.subTest(mixed=mixed), self.assertRaisesRegex(AssertionError, "Direct established socket"):
                self.exercise(snapshots, mixed)

    def test_process_name_without_exact_installer_executable_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "not the exact tested Mixel"):
            self.exercise(self.snapshots(), executable="/usr/bin/another-mixel-remote")

    def test_registration_socket_must_belong_to_actual_incoming_pid(self):
        snapshots = self.snapshots()
        snapshots["controller"] = self.socket(pid=101) + self.socket(pid=102, local=41001)
        with self.assertRaisesRegex(AssertionError, "Incoming registration process"):
            self.exercise(snapshots)

    def test_one_kernel_socket_with_duplicate_fds_or_owners_cannot_count_as_two(self):
        for same_pid in (True, False):
            second = 100 if same_pid else 101
            snapshots = self.snapshots()
            snapshots["host"] = f'ESTAB 0 0 192.168.48.3:41000 192.168.48.2:443 users:(("mixel-remote",pid=100,fd=20),("mixel-remote",pid={second},fd=21))\n'
            with self.subTest(same_pid=same_pid), self.assertRaisesRegex(AssertionError, "two exact relay"):
                self.exercise(snapshots)

    def registration_snapshots(self, snapshots, health_values=None, authenticated=0):
        session = desktop.Session(Path("proofs"), Path("synthetic.deb"), False, True,
            {"pin": "public-fixture-pin", "network": "owned", "address": "192.168.48.2"})
        session.server["host"] = 100
        output = iter(snapshots)
        def run(role, arguments, **kwargs):
            self.assertEqual(role, "host")
            if arguments[0] == "ss":
                value = next(output)
            elif arguments[0] == "readlink":
                value = desktop.EXE + "\n"
            else:
                raise AssertionError(arguments)
            return subprocess.CompletedProcess(arguments, 0, value, "")
        healthy = {"incoming_pid": 100, "online_status": [15, True]}
        with patch.object(session, "run", side_effect=run), \
                patch.object(session, "other_peer_addresses", return_value={"192.168.48.4"}), \
                patch.object(session, "query", return_value=authenticated), \
                patch.object(session, "health", side_effect=health_values if health_values is not None else lambda role: healthy):
            baseline = session.registration_tls_snapshot()
            self.assertIsNotNone(baseline, "The baseline is a real auth0 key-confirmed registration socket")
            return baseline, session.fresh_registration_tls(baseline)

    def test_actual_registration_recovery_rejects_cached_online_on_old_established_socket(self):
        baseline, recovered = self.registration_snapshots([self.socket(), self.socket()])
        self.assertEqual(baseline["incoming_pid"], 100)
        self.assertIsNone(recovered)

    def test_actual_registration_recovery_requires_new_tuple_same_pid_and_guarded_key(self):
        baseline, recovered = self.registration_snapshots([self.socket(), self.socket(local=41002)])
        self.assertEqual(recovered["incoming_pid"], baseline["incoming_pid"])
        self.assertNotEqual(recovered["connections"][0]["local_endpoint"], baseline["connections"][0]["local_endpoint"])
        self.assertEqual(recovered["authenticated_sessions"], 0)
        self.assertTrue(recovered["guarded_ipc_before"])
        self.assertTrue(recovered["guarded_ipc_after"])

    def test_actual_registration_recovery_rejects_other_process_foreign_endpoint_or_two_lanes(self):
        for changed in (self.socket(pid=101, local=41002), self.socket(peer="203.0.113.10", local=41002),
                        self.socket(local=41002) + self.socket(local=41003)):
            with self.subTest(changed=changed):
                self.assertIsNone(self.registration_snapshots([self.socket(), changed])[1])

    def test_actual_registration_recovery_rechecks_key_confirmation_after_kernel_snapshot(self):
        healthy = {"incoming_pid": 100, "online_status": [15, True]}
        self.assertIsNone(self.registration_snapshots([self.socket(), self.socket(local=41002)],
                                                     [healthy, healthy, healthy, None])[1])

    def test_actual_registration_snapshot_rejects_authenticated_session_before_socket_selection(self):
        session = desktop.Session(Path("proofs"), Path("synthetic.deb"), False, True)
        with patch.object(session, "query", return_value=1), patch.object(session, "health") as health, \
                patch.object(session, "app_tcp_evidence") as sockets:
            self.assertIsNone(session.registration_tls_snapshot())
        health.assert_not_called()
        sockets.assert_not_called()


class DesktopReadinessTests(unittest.TestCase):
    @staticmethod
    def tsv(name):
        return "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n" + "5\t1\t1\t1\t1\t1\t30\t12\t300\t30\t95\t" + name + "\n"

    def test_actual_rendered_filename_parser_rejects_empty_wrong_and_ambiguous_rows(self):
        bounds = (520, 278, 975, 320)
        self.assertEqual(desktop.Session.rendered_row(self.tsv(desktop.FILE), bounds), (580, 287))
        for name in ("", "another-proof.bin", desktop.FILE + ".part"):
            self.assertIsNone(desktop.Session.rendered_row(self.tsv(name), bounds))
        duplicated = self.tsv(desktop.FILE) + self.tsv(desktop.FILE).splitlines()[1] + "\n"
        with self.assertRaisesRegex(AssertionError, "ambiguous"):
            desktop.Session.rendered_row(duplicated, bounds)

    def test_actual_process_snapshot_does_not_rewrite_guest_owned_proof_on_host(self):
        session = desktop.Session(Path("runner-proofs"), Path("synthetic.deb"), False, True)
        session.server["host"] = 100
        state = {"ipc_pid": "100", "memory": {"memory.events": "oom_kill 0"}}
        with patch.object(session, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(state), "")), \
                patch.object(Path, "write_text", side_effect=PermissionError("Guest UID differs from runner")) as host_write:
            self.assertEqual(session.process_snapshot("host", "before-network-drop"), state)
        host_write.assert_not_called()

    def test_actual_remote_row_wait_refreshes_empty_listing_before_returning_exact_visible_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            proofs = Path(temporary)
            (proofs / "controller").mkdir()
            session = desktop.Session(proofs, Path("synthetic.deb"), False, True)
            ocr = iter((self.tsv(""), self.tsv(desktop.FILE)))
            actions = []
            def run(role, arguments, **kwargs):
                if arguments[0] == "tesseract":
                    actions.append("read actual rendered remote row")
                    return subprocess.CompletedProcess(arguments, 0, next(ocr), "")
                return subprocess.CompletedProcess(arguments, 0, "", "")
            with patch.object(session, "geometry", return_value={"X": 0, "Y": 40}), \
                    patch.object(session, "run", side_effect=run), patch.object(session, "screenshot"), \
                    patch.object(session, "click", side_effect=lambda *args: actions.append(("real refresh", args))), \
                    patch.object(desktop.time, "sleep"):
                point = session.file_row("remote", "real-window", "readiness", refresh=True)
            self.assertEqual(point, (580, 287))
            self.assertEqual(actions, ["read actual rendered remote row", ("real refresh", ("controller", 935, 180)), "read actual rendered remote row"])

    def test_actual_input_targets_observed_controls_and_checks_text_before_button(self):
        with tempfile.TemporaryDirectory() as temporary:
            proofs = Path(temporary)
            (proofs / "host").mkdir()
            (proofs / "host/input.json").write_text(json.dumps({"count": 0, "text": ""}))
            session = desktop.Session(proofs, Path("synthetic.deb"), False, True)
            state = {"focus": "", "text": "", "events": [], "controls": {
                "entry": {"x": 100, "y": 500, "width": 800, "height": 40},
                "button": {"x": 300, "y": 630, "width": 450, "height": 80}}}
            clicks = []
            mapping = lambda x, y: (x * 1.5 + 30, y * 1.5 + 10)
            def click(role, x, y):
                self.assertEqual(role, "controller", "A product proof must never inject host input")
                clicks.append((x, y))
                if len(clicks) == 1:
                    state["focus"] = ".!entry"
                else:
                    self.assertEqual(state["text"], desktop.TEXT)
                    (proofs / "host/input.json").write_text(json.dumps({"count": 1, "text": state["text"]}))
            def gui(role, arguments):
                self.assertEqual(role, "controller")
                self.assertEqual(arguments, ["xdotool", "type", "--clearmodifiers", "--delay", "60", desktop.TEXT])
                state["text"] = desktop.TEXT
            with patch.object(session, "video_map", side_effect=lambda _: (mapping, state)), \
                    patch.object(session, "fresh_video", return_value=(mapping, state)), \
                    patch.object(session, "fixture_state", return_value=state), patch.object(session, "click", side_effect=click), \
                    patch.object(session, "gui", side_effect=gui), patch.object(session, "screenshot"), \
                    patch.object(desktop.time, "sleep"), patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(session.input(), {"count": 1, "text": desktop.TEXT})
            self.assertEqual(clicks, [mapping(500, 520), mapping(525, 670)])

    def test_actual_clock_decoder_rejects_stale_hidden_corrupt_or_far_future_video(self):
        def samples(counter):
            return [[245, 246, 247] if bit == "1" else [8, 7, 9] for bit in "1010" + format(counter, "016b")]
        self.assertEqual(desktop.decode_marker(samples(42), 42), 42)
        self.assertEqual(desktop.decode_marker(samples(65535), 1), 65535)
        self.assertEqual(desktop.decode_marker(samples(43), 42), 43)
        for observed in (samples(34), samples(45), [[20, 20, 20]] * 20, [[229, 29, 54]] * 20, samples(42)[:-1]):
            with self.subTest(samples=observed), self.assertRaises(RuntimeError):
                desktop.decode_marker(observed, 42)

    def test_actual_fresh_frame_gate_requires_two_different_current_decoded_clocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = desktop.Session(Path(temporary), Path("synthetic.deb"), False, True)
            values = iter((120, 120, 121))
            calls = []
            def video(label):
                counter = next(values)
                calls.append(counter)
                return "actual mapping", {"marker": {"counter": counter}, "decoded_marker": {"decoded_counter": counter, "rectangle": [91, 128, 300]},
                                          "video_observation": {"source_before_capture": counter - 1, "source_after_capture": counter}}
            with patch.object(session, "video_map", side_effect=video), patch.object(desktop.time, "sleep"):
                self.assertEqual(session.fresh_video("current-video")[0], "actual mapping")
            self.assertEqual(calls, [120, 120, 121])
            observations = json.loads((Path(temporary) / "current-video-fresh-frames.json").read_text())
            self.assertEqual(len(observations), 3)
            self.assertEqual(observations[-1]["capture"], {"source_before_capture": 120, "source_after_capture": 121})

    def test_recovery_transport_gate_dispatches_same_strict_oracle_and_keeps_phase_label(self):
        for mixed in (False, True):
            session = desktop.Session(Path("proofs"), Path("synthetic.deb"), False, not mixed, mixed_transports=mixed)
            with patch.object(session, "active_https_proof") as https, patch.object(session, "active_mixed_proof") as partial:
                session.active_transport_proof("service-restart")
                session.active_transport_proof("network-reconnect")
            selected, unused = (partial, https) if mixed else (https, partial)
            self.assertEqual(selected.call_args_list, [unittest.mock.call("service-restart"), unittest.mock.call("network-reconnect")])
            unused.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "requires the POSIX launch supervisor used in the Linux fixture")
    def test_actual_launch_supervisor_retains_app_pid_and_records_failed_exit_status(self):
        with tempfile.TemporaryDirectory(prefix="mixel-launch-status-") as temporary:
            folder = Path(temporary)
            (folder / "dbus-address").write_text("synthetic-test-bus-only")
            session = desktop.Session(folder, Path("synthetic.deb"), False, True)
            def run(_role, arguments, **_kwargs):
                self.assertEqual(arguments[:2], ["bash", "-c"])
                return subprocess.run(["bash", "-c", arguments[2].replace("/proofs", str(folder))], capture_output=True, text=True, check=True)
            with patch.object(desktop, "EXE", sys.executable), patch.object(session, "run", side_effect=run):
                pid = session.launch("host", ["-c", "import sys;sys.exit(24)"])
            result = desktop.until("actual supervised child exit", lambda: next(iter(folder.glob("launch-*-exit.json")), None), timeout=3)
            self.assertEqual(json.loads(result.read_text())["pid"], pid)
            self.assertEqual(json.loads(result.read_text())["wait_status"], 24)

    @unittest.skipUnless(os.name == "posix", "requires POSIX signal wait status")
    def test_actual_launch_supervisor_records_sigkill_without_claiming_normal_exit(self):
        with tempfile.TemporaryDirectory(prefix="mixel-launch-killed-") as temporary:
            folder = Path(temporary)
            (folder / "dbus-address").write_text("synthetic-test-bus-only")
            session = desktop.Session(folder, Path("synthetic.deb"), False, True)
            def run(_role, arguments, **_kwargs):
                return subprocess.run(["bash", "-c", arguments[2].replace("/proofs", str(folder))], capture_output=True, text=True, check=True)
            with patch.object(desktop, "EXE", sys.executable), patch.object(session, "run", side_effect=run):
                pid = session.launch("host", ["-c", "import time;time.sleep(10)"])
            os.kill(pid, signal.SIGKILL)
            result = desktop.until("actual supervised signal status", lambda: next(iter(folder.glob("launch-*-exit.json")), None), timeout=3)
            self.assertEqual(json.loads(result.read_text())["pid"], pid)
            self.assertEqual(json.loads(result.read_text())["wait_status"], 137)


if __name__ == "__main__":
    unittest.main(verbosity=2)
