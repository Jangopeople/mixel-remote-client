#!/usr/bin/env python3
"""The elevation layer applies once, restores to the exact input and refuses partial trees."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "patch-support-elevation.py"
spec = importlib.util.spec_from_file_location("elevation", SCRIPT)
elevation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(elevation)


def fixture(root: Path, crlf: bool) -> dict[str, bytes]:
    """One file per target holding every expected input block exactly once."""
    blocks: dict[str, list[str]] = {}
    for name, old, _ in elevation.CHANGES:
        blocks.setdefault(name, []).append(old)
    written = {}
    for index, (name, parts) in enumerate(blocks.items()):
        text = "// head\n" + "\n// --\n".join(parts) + "\n// tail\n"
        raw = (text.replace("\n", "\r\n") if crlf and index % 2 else text).encode()
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        written[name] = raw
    return written


def run(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True)


def snapshot(root: Path, names) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in names}


class ElevationLayer(unittest.TestCase):
    def test_blocks_are_distinct_and_new_contains_no_second_old(self):
        for name, old, new in elevation.CHANGES:
            self.assertNotEqual(old, new)
            self.assertLessEqual(new.count(old), 1, name)

    def test_apply_is_idempotent_and_restore_is_exact(self):
        for crlf in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                before = fixture(root, crlf)
                self.assertEqual(run("--check", str(root)).returncode, 0)
                self.assertEqual(snapshot(root, before), before, "check must not write")
                self.assertEqual(run(str(root)).returncode, 0)
                after = snapshot(root, before)
                self.assertTrue(all(after[name] != before[name] for name in before))
                self.assertEqual(run(str(root)).returncode, 0)
                self.assertEqual(snapshot(root, before), after, "second apply must not change anything")
                self.assertEqual(run("--restore", "--check", str(root)).returncode, 0)
                self.assertEqual(snapshot(root, before), after, "check must not write")
                self.assertEqual(run("--restore", str(root)).returncode, 0)
                self.assertEqual(snapshot(root, before), before, "restore must be the exact inverse")
                self.assertEqual(run("--restore", str(root)).returncode, 0)
                self.assertEqual(snapshot(root, before), before)

    def test_partial_layer_and_changed_input_are_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = fixture(root, False)
            self.assertEqual(run(str(root)).returncode, 0)
            name, old, new = elevation.CHANGES[0]
            path = root / name
            path.write_bytes(path.read_bytes().replace(new.encode(), old.encode()))
            state = snapshot(root, before)
            for arguments in ((str(root),), ("--restore", str(root))):
                result = run(*arguments)
                self.assertEqual(result.returncode, 1)
                self.assertIn("partial", result.stdout)
                self.assertEqual(snapshot(root, before), state)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = fixture(root, False)
            name, old, _ = elevation.CHANGES[-1]
            path = root / name
            path.write_bytes(path.read_bytes().replace(old.encode(), b"// changed upstream\n"))
            state = snapshot(root, before)
            self.assertEqual(run(str(root)).returncode, 1)
            self.assertEqual(snapshot(root, before), state)

    def test_restore_on_a_fresh_checkout_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(run("--restore", "--check", directory).returncode, 0)
            self.assertEqual(run("--restore", directory).returncode, 0)

    def test_windows_driver_applies_the_layer_last_and_branding_stays_pinned(self):
        driver = (HERE / "local/build-windows.py").read_text()
        order = [driver.index(token) for token in (
            "call('apply-branding'", "admit_bridge(args.bridge", "call('windows-compiler-integration'",
            "call('elevation-layer',[sys.executable,repo/'scripts/patch-support-elevation.py','--check',app])",
            "call('elevation-layer',[sys.executable,repo/'scripts/patch-support-elevation.py',app])",
            "call('fetch-locked'")]
        self.assertEqual(order, sorted(order))
        # The shared pipeline, its projections and the CI workflow pin the source without this layer.
        self.assertNotIn("patch-support-elevation", (HERE / "apply-branding.sh").read_text())
        self.assertNotIn("patch-support-elevation", (HERE / "receiver_input_integration_projection.py").read_text())

    def test_guarded_restore_needs_restore(self):
        self.assertEqual(run("--guarded", ".").returncode, 2)

if __name__ == "__main__":
    unittest.main()
