#!/usr/bin/env python3
"""Compile actual old/fixed helper naming and prove branding fails closed."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("macos_labels", ROOT / "patch-macos-service-label.py")
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)
BUNDLE = "ch.mixel.remote"


def compiled_test(function: str, directory: Path) -> subprocess.CompletedProcess:
    source = directory / "labels.rs"
    # The exact generated helper function is compiled on any host; its macOS
    # cfg is the only attribute removed. ORG deliberately retains legacy data.
    function = function.replace('#[cfg(target_os = "macos")]\n', "")
    source.write_text('''mod hbb_common {
    pub mod config {
        pub struct Preference(&'static str);
        impl Preference {
            pub fn read(&self) -> Result<Box<String>, ()> { Ok(Box::new(self.0.to_owned())) }
        }
        pub static ORG: Preference = Preference("com.carriez");
        pub static APP_NAME: Preference = Preference("Mixel-Remote");
    }
}
''' + function + '''
#[test]
fn installed_signed_helpers_are_found_and_updater_selects_daemon_path() {
    let prefix = get_full_name();
    assert_eq!(prefix, "ch.mixel.remote");
    let daemon = format!("{}_service.plist", prefix);
    let agent = format!("{}_server.plist", prefix);
    assert_eq!(daemon, "ch.mixel.remote_service.plist");
    assert_eq!(agent, "ch.mixel.remote_server.plist");
    assert_eq!(format!("{}_server", prefix), "ch.mixel.remote_server");
    assert_eq!(*hbb_common::config::ORG.read().unwrap(), "com.carriez");
    assert_eq!(*hbb_common::config::APP_NAME.read().unwrap(), "Mixel-Remote");
}
''', encoding="utf-8")
    executable = directory / "labels-test"
    result = subprocess.run(["rustc", "--edition=2021", "--test", str(source), "-o", str(executable)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)


class ServiceLabelTests(unittest.TestCase):
    def test_compiled_original_fails_and_generated_function_passes(self):
        with tempfile.TemporaryDirectory(prefix="mixel-macos-label-test-") as temporary:
            base = Path(temporary)
            original = compiled_test(labels.ORIGINAL, base)
            self.assertNotEqual(original.returncode, 0)
            self.assertIn("FAILED", original.stdout)
            source = base / "common.rs"
            source.write_text(labels.ORIGINAL, encoding="utf-8")
            labels.patch(source, BUNDLE)
            fixed = compiled_test(source.read_text(), base)
            self.assertEqual(fixed.returncode, 0, fixed.stdout + fixed.stderr)
            print("PASS: compiled original label lookup fails; fixed helper/updater labels pass with preference namespace unchanged")

    def test_idempotent_and_retains_lf_or_crlf_bytes(self):
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "common.rs"
                prefix = b"// untouched preference setup" + newline
                source.write_bytes(prefix + labels.ORIGINAL.encode().replace(b"\n", newline))
                labels.patch(source, BUNDLE)
                expected = prefix + labels.replacement(BUNDLE).encode().replace(b"\n", newline)
                self.assertEqual(source.read_bytes(), expected)
                labels.patch(source, BUNDLE)
                self.assertEqual(source.read_bytes(), expected)

    def test_missing_duplicate_or_bad_identity_does_not_write(self):
        cases = [("// changed upstream", BUNDLE), (labels.ORIGINAL * 2, BUNDLE),
                 (labels.ORIGINAL + labels.replacement(BUNDLE), BUNDLE),
                 (labels.ORIGINAL, 'ch.mixel.remote"; unsafe()')]
        for content, bundle_id in cases:
            with self.subTest(content=content[:20]), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "common.rs"
                source.write_text(content, encoding="utf-8")
                before = source.read_bytes()
                with self.assertRaises((RuntimeError, ValueError)):
                    labels.patch(source, bundle_id)
                self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
