#!/usr/bin/env python3
"""Exercise real helper subprocesses against an isolated mock xcrun executable."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/notarize-macos.py"
spec = importlib.util.spec_from_file_location("mixel_notarize", HELPER)
notary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notary)
IDENTIFIER = "499cbbb2-6652-4d6b-b28c-6923d13b53e0"
OTHER_ID = "66519984-d94e-4da8-ae26-72e2d7ca2663"
AUTH = ["--apple-id", "synthetic-review@example.invalid", "--password", "synthetic-notary-secret", "--team-id", "SYNTHETIC1"]


@unittest.skipIf(os.name == "nt", "macOS helper executable regression requires Unix process semantics")
class NotarizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mixel-notary-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifact = self.root / "signed artifact.dmg"
        self.artifact.write_bytes(b"owned signed artifact fixture")
        self.logs = self.root / "proof"
        self.calls_file = self.root / "calls.jsonl"
        executable = self.root / "xcrun"
        executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
calls_file = Path(os.environ["MIXEL_NOTARY_TEST_CALLS"])
calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
with calls_file.open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[0] == "stapler":
    sys.exit(0)
assert args[:1] == ["notarytool"], args
responses = json.loads(os.environ["MIXEL_NOTARY_TEST_RESPONSES"])
operation = args[1]
if operation == "submit":
    response = responses["submit"]
else:
    assert operation == "wait", args
    count = sum(call[:2] == ["notarytool", "wait"] for call in calls)
    response = responses["wait"][count]
if "json" in response:
    print(json.dumps(response["json"]))
else:
    print(response.get("stdout", ""))
print(response.get("stderr", ""), file=sys.stderr)
sys.exit(response.get("code", 0))
''', encoding="utf-8")
        executable.chmod(0o755)
        self.environment = {"PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                            "MIXEL_NOTARY_TEST_CALLS": str(self.calls_file)}
        self.delays = []

    def responses(self, wait, submit=None):
        self.environment["MIXEL_NOTARY_TEST_RESPONSES"] = json.dumps({
            "submit": submit or {"json": {"id": IDENTIFIER, "message": "Successfully uploaded file"}},
            "wait": wait})

    def invoke(self):
        with patch.dict(os.environ, self.environment):
            return notary.notarize(self.artifact, self.logs, AUTH, sleep=self.delays.append)

    def calls(self):
        return [json.loads(line) for line in self.calls_file.read_text().splitlines()]

    def assert_calls(self, waits):
        calls = self.calls()
        self.assertEqual(sum(call[:2] == ["notarytool", "submit"] for call in calls), 1)
        actual_waits = [call for call in calls if call[:2] == ["notarytool", "wait"]]
        self.assertEqual(len(actual_waits), waits)
        for call in actual_waits:
            self.assertEqual(call[2], IDENTIFIER)
            self.assertEqual(call[-2:], ["--timeout", "600s"])
            self.assertIn("json", call)
        for file in self.logs.iterdir():
            for secret in AUTH[1::2]:
                self.assertNotIn(secret, file.read_text())

    def test_accepted_exact_id_submits_once_and_retains_json_proof(self):
        self.responses([{"json": {"id": IDENTIFIER, "status": "Accepted"}}])
        result = self.invoke()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["submission_id"], IDENTIFIER)
        self.assertEqual(json.loads((self.logs / "wait-1.stdout.log").read_text())["status"], "Accepted")
        self.assert_calls(1)
        self.assertEqual(self.delays, [])

    def test_actual_apple_poll_timeout_retries_same_id_without_resubmission(self):
        failure = {"code": 1, "stderr": 'Error: HTTPError(statusCode: nil, error: Error Domain=NSURLErrorDomain Code=-1001 "The request timed out.")'}
        self.responses([failure, failure, {"json": {"id": IDENTIFIER, "status": "Accepted"}}])
        self.assertTrue(self.invoke()["accepted"])
        self.assert_calls(3)
        self.assertEqual(self.delays, [10, 20])

    def test_pending_or_http_unavailable_waits_are_bounded(self):
        for response in ({"code": 69, "json": {"id": IDENTIFIER, "status": "In Progress"}},
                         {"code": 1, "stderr": "HTTP status code 503 Service Unavailable"}):
            with self.subTest(response=response):
                self.calls_file.unlink(missing_ok=True)
                self.delays.clear()
                self.responses([response] * 3)
                with self.assertRaisesRegex(notary.NotaryError, "three bounded waits"):
                    self.invoke()
                self.assert_calls(3)
                self.assertEqual(self.delays, [10, 20])
                self.assertFalse(json.loads((self.logs / "result.json").read_text())["accepted"])

    def test_invalid_rejection_fails_immediately_even_with_transport_error(self):
        for code in (0, 1):
            with self.subTest(code=code):
                self.calls_file.unlink(missing_ok=True)
                self.responses([{"code": code, "json": {"id": IDENTIFIER, "status": "Invalid"},
                                 "stderr": "HTTP 503 synthetic-notary-secret"}])
                with self.assertRaisesRegex(notary.NotaryError, "stapling is forbidden"):
                    self.invoke()
                self.assert_calls(1)
                self.assertEqual(self.delays, [])

    def test_wrong_missing_or_malformed_response_id_cannot_attest_acceptance(self):
        for identifier in (OTHER_ID, "../../unsafe", None):
            with self.subTest(identifier=identifier):
                self.calls_file.unlink(missing_ok=True)
                self.responses([{"json": {"id": identifier, "status": "Accepted"}}])
                with self.assertRaises(notary.NotaryError):
                    self.invoke()
                self.assert_calls(1)

    def test_submission_failure_or_invalid_id_is_never_retried(self):
        for response in ({"code": 1, "stderr": "The request timed out."},
                         {"json": {"id": "--unsafe"}}, {"stdout": "malformed"}):
            with self.subTest(response=response):
                self.calls_file.unlink(missing_ok=True)
                self.responses([], submit=response)
                with self.assertRaises(notary.NotaryError):
                    self.invoke()
                self.assert_calls(0)

    def test_authentication_or_malformed_wait_error_is_fatal_without_retry(self):
        for response in ({"code": 1, "stderr": "HTTP 401 credentials rejected"},
                         {"stdout": "malformed"}, {"json": ["Accepted"]}):
            with self.subTest(response=response):
                self.calls_file.unlink(missing_ok=True)
                self.responses([response])
                with self.assertRaises(notary.NotaryError):
                    self.invoke()
                self.assert_calls(1)

    def test_real_cli_acceptance_alone_controls_downstream_stapling(self):
        for status in ("Accepted", "Invalid"):
            with self.subTest(status=status):
                self.calls_file.unlink(missing_ok=True)
                self.responses([{"json": {"id": IDENTIFIER, "status": status}}])
                with patch.dict(os.environ, self.environment):
                    script = 'set -e\n"$MIXEL_TEST_PYTHON" "$MIXEL_TEST_HELPER" "$MIXEL_TEST_ARTIFACT" --log-dir "$MIXEL_TEST_LOGS" --keychain-profile fixture-profile\nxcrun stapler staple "$MIXEL_TEST_ARTIFACT"\n'
                    result = subprocess.run(["bash", "-c", script], env={**os.environ,
                                            "MIXEL_TEST_PYTHON": sys.executable, "MIXEL_TEST_HELPER": str(HELPER),
                                            "MIXEL_TEST_ARTIFACT": str(self.artifact), "MIXEL_TEST_LOGS": str(self.logs)},
                                            capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode == 0, status == "Accepted")
                self.assertEqual(any(call[:1] == ["stapler"] for call in self.calls()), status == "Accepted")
                if status == "Invalid":
                    self.assertIn("stapling is forbidden", result.stderr)
                self.assert_calls(1)

    def test_callers_use_helper_before_stapling_both_artifacts(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        local = (ROOT / "scripts/local/sign-and-publish-macos.sh").read_text()
        self.assertEqual(workflow.count('python3 ../scripts/notarize-macos.py "$'), 2)
        self.assertEqual(local.count('python3 "$NOTARY_HELPER" "$'), 2)
        self.assertNotIn("notarytool submit", workflow)
        self.assertNotIn("notarytool submit", local)


if __name__ == "__main__":
    unittest.main(verbosity=2)
