#!/usr/bin/env python3
"""Reject mismatched build/archive inputs without network or installer execution."""
import copy
import hashlib
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
import zipfile

API = runpy.run_path(str(Path(__file__).with_name("collect-linux-runtime-artifact.py")))
SOURCE = "7f060ab92e6c9bcfa726e9d316e73605627b721e"
RUN = 38056398014
REPO = API["REPOSITORY"]


class ArtifactControls(unittest.TestCase):
    def setUp(self):
        self.run = {"id": RUN, "repository": {"full_name": REPO},
                    "event": "workflow_dispatch", "path": ".github/workflows/build.yml",
                    "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
                    "head_sha": SOURCE, "status": "completed", "conclusion": "failure"}
        self.artifact = {"name": "linux", "expired": False, "id": 11672123448,
                         "digest": "sha256:" + "a" * 64, "size_in_bytes": 4096,
                         "workflow_run": {"id": RUN, "head_sha": SOURCE}}

    def test_failed_overall_build_is_not_promoted_or_relabelled(self):
        self.assertEqual(API["validate_run"](self.run, RUN), SOURCE)
        self.assertEqual(self.run["conclusion"], "failure")
        self.assertEqual(API["select_linux_artifact"]([self.artifact], RUN, SOURCE), self.artifact)

    def test_run_attribution_negatives(self):
        changes = {"id": RUN + 1, "repository": {"full_name": "unrelated/repo"},
                   "event": "pull_request", "path": "other.yml", "html_url": "https://example.com",
                   "head_sha": "not-a-source", "status": "queued"}
        for key, value in changes.items():
            with self.subTest(field=key):
                run = copy.deepcopy(self.run)
                run[key] = value
                with self.assertRaises(ValueError):
                    API["validate_run"](run, RUN)
        for invalid in ("0", "01", "-1", "1;command", "1\n", "9" * 21):
            with self.subTest(run=invalid), self.assertRaises(ValueError):
                API["validate_run"](self.run, invalid)

    def test_artifact_attribution_negatives(self):
        for key, value in {"expired": True, "id": 0, "digest": "sha256:invalid",
                           "size_in_bytes": API["MAX_ARCHIVE"] + 1,
                           "workflow_run": {"id": RUN + 1, "head_sha": SOURCE}}.items():
            with self.subTest(field=key):
                artifact = copy.deepcopy(self.artifact)
                artifact[key] = value
                with self.assertRaises(ValueError):
                    API["select_linux_artifact"]([artifact], RUN, SOURCE)
        for artifacts in ([], [self.artifact, self.artifact]):
            with self.subTest(count=len(artifacts)), self.assertRaises(ValueError):
                API["select_linux_artifact"](artifacts, RUN, SOURCE)
        wrong_source = copy.deepcopy(self.artifact)
        wrong_source["workflow_run"]["head_sha"] = "0" * 40
        with self.assertRaises(ValueError):
            API["select_linux_artifact"]([wrong_source], RUN, SOURCE)

    def test_actual_archive_bytes_and_path_controls(self):
        with tempfile.TemporaryDirectory(prefix="mixel-artifact-control-") as temporary:
            path = Path(temporary) / "linux.zip"
            def archive(names, *, symlink=False, oversized=False):
                with zipfile.ZipFile(path, "w") as contents:
                    for name in names:
                        entry = zipfile.ZipInfo(name)
                        if symlink:
                            entry.external_attr = 0o120777 << 16
                        contents.writestr(entry, b"synthetic package bytes")
                if oversized:
                    data = bytearray(path.read_bytes())
                    struct.pack_into("<I", data, data.index(b"PK\x01\x02") + 24, API["MAX_PACKAGE"] + 1)
                    path.write_bytes(data)
                return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            name = "mixel-remote-1.4.6-x86_64.deb"
            digest = archive([name])
            actual_name, data, actual_digest = API["read_linux_package"](path, digest)
            self.assertEqual((actual_name, data, actual_digest), (name, b"synthetic package bytes", digest))
            with self.assertRaises(ValueError):
                API["read_linux_package"](path, "sha256:" + "0" * 64)
            for names, options in (([], {}), ([name, "other.deb"], {}),
                                   (["../" + name], {}), (["/" + name], {}),
                                   ([name], {"symlink": True}), ([name], {"oversized": True})):
                with self.subTest(names=names, options=options):
                    digest = archive(names, **options)
                    with self.assertRaises(ValueError):
                        API["read_linux_package"](path, digest)
            digest = archive([name])
            path.write_bytes(path.read_bytes() + b"unexpected bytes")
            with self.assertRaises(ValueError):
                API["read_linux_package"](path, digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
