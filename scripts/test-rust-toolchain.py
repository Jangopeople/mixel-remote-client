#!/usr/bin/env python3
"""Qualify explicit MSVC selection despite a shadowing Git Bash linker."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rust_toolchain import find_msvc_linker, rustc_command, verify_msvc_linker


class RustToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mixel native MSVC préflight ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / "Visual Studio/VC/Tools/MSVC/14.44.35207"
        self.linker = self.tools / "bin/Hostx64/x64/link.exe"
        self.linker.parent.mkdir(parents=True)
        self.linker.write_bytes(b"MZsynthetic-native-linker")
        self.linker.with_name("cl.exe").write_bytes(b"MZsynthetic-native-compiler")
        self.environment = {"VCToolsInstallDir": str(self.tools), "PATH": str(self.root / "Git/usr/bin")}
        self.qualified = []

    def verifier(self, path):
        self.qualified.append(path)
        self.assertEqual(path, self.linker)

    def test_explicit_native_linker_ignores_path_shadow_and_preserves_all_flags(self):
        command = ["custom-rustc.exe", "--edition=2021", "--test", "source with spaces.rs", "-o", "output.exe",
                   "-C", "link-arg=/INCREMENTAL:NO", "-C", "link-arg=actual object.obj", "--cfg", 'feature="flutter"']
        result = rustc_command(command, windows=True, environment=self.environment, verifier=self.verifier)
        self.assertEqual(result, command + ["-C", "linker=" + str(self.linker)])
        self.assertEqual(self.qualified, [self.linker])
        self.assertEqual(command[0], "custom-rustc.exe")

    def test_unix_command_does_not_probe_or_change_linker_arguments(self):
        command = ["/pinned/rustc", "--target", "x86_64-apple-darwin", "-C", "link-arg=-framework", "-C", "linker=custom"]
        self.assertEqual(rustc_command(command, windows=False, environment={}, verifier=lambda _: self.fail("Unix linker probed")), command)

    def test_cargo_must_use_the_same_exact_linker(self):
        environment = {**self.environment, "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER": str(self.linker)}
        self.assertEqual(find_msvc_linker(environment, verifier=self.verifier), self.linker)
        environment["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"] = str(self.root / "Git/usr/bin/link.exe")
        with self.assertRaisesRegex(RuntimeError, "different Windows linkers"):
            find_msvc_linker(environment, verifier=self.verifier)

    def test_plain_copied_native_environment_preserves_case_insensitive_selection(self):
        copied = {key.upper(): value for key, value in self.environment.items()}
        copied["cargo_target_x86_64_pc_windows_msvc_linker"] = str(self.linker)
        self.assertEqual(find_msvc_linker(copied, verifier=self.verifier), self.linker)
        copied["VCToolsInstallDir"] = str(self.tools)
        self.assertEqual(find_msvc_linker(copied, verifier=self.verifier), self.linker)
        copied["VCToolsInstallDir"] = str(self.root / "other-toolchain")
        with self.assertRaisesRegex(RuntimeError, "Conflicting Windows environment aliases"):
            find_msvc_linker(copied, verifier=self.verifier)

    def test_missing_relative_control_and_indirect_tool_paths_fail_closed(self):
        for value in ("", "relative/tools", str(self.tools) + "\n", str(self.root / "absent")):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                find_msvc_linker({"VCToolsInstallDir": value}, verifier=self.verifier)
        self.linker.unlink()
        outside = self.root / "other-link.exe"; outside.write_bytes(b"MZoutside")
        self.linker.symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "indirect"):
            find_msvc_linker(self.environment, verifier=self.verifier)

    def test_native_pe_identity_rejects_coreutils_and_other_microsoft_tools(self):
        for identity, passed in (({"CompanyName": "Microsoft Corporation", "OriginalFilename": "LINK.EXE"}, True),
                                 ({"CompanyName": "GNU", "OriginalFilename": "link.exe"}, False),
                                 ({"CompanyName": "Microsoft Corporation", "OriginalFilename": "cl.exe"}, False),
                                 ({"OriginalFilename": "link.exe"}, False), ({}, False)):
            with self.subTest(identity=identity), patch("rust_toolchain.read_windows_version_info", return_value=identity) as reader:
                if passed:
                    verify_msvc_linker(self.linker)
                else:
                    with self.assertRaises(RuntimeError): verify_msvc_linker(self.linker)
                self.assertEqual(reader.call_args.args[0], self.linker)
        self.linker.write_bytes(b"not-a-PE")
        with self.assertRaisesRegex(RuntimeError, "PE executable"): verify_msvc_linker(self.linker)

    def test_existing_linker_is_preserved_only_when_matching(self):
        for flags in (["-C", "linker=" + str(self.linker)], ["-Clinker=" + str(self.linker)]):
            command = ["rustc", "source.rs", *flags]
            self.assertEqual(rustc_command(command, windows=True, environment=self.environment, verifier=self.verifier), command)
        for flags in (["-C", "linker=link.exe"], ["-Clinker=link.exe"],
                      ["-C", "linker=" + str(self.linker), "-C", "linker=" + str(self.linker)]):
            with self.assertRaisesRegex(RuntimeError, "conflicts"):
                rustc_command(["rustc", "source.rs", *flags], windows=True, environment=self.environment, verifier=self.verifier)

    def test_spaced_source_basename_requires_explicit_valid_crate_before_linking(self):
        rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
        self.assertTrue(rustc, "Rust compiler is required for the actual source-path control")
        source = self.root / "actual fixture.rs"
        source.write_text('fn main() {}\n', encoding="utf-8")
        command = [rustc, "--edition=2021", "--deny=warnings", str(source), "--emit=metadata", "-o", str(self.root / "actual fixture.rmeta")]
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("invalid character", rejected.stderr)
        self.assertNotIn("linking with", rejected.stderr)
        corrected = subprocess.run(command + ["--crate-name", "mixel_native_linker_control"], capture_output=True, text=True, timeout=60)
        self.assertEqual(corrected.returncode, 0, corrected.stderr)


def native_git_bash_control():
    if os.name != "nt":
        raise RuntimeError("Actual Git Bash/MSVC control requires Windows")
    selected = find_msvc_linker(os.environ)
    if os.environ.get("CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER") != str(selected):
        raise RuntimeError("Activation did not export the verified Cargo linker")
    rustc = os.environ.get("RUSTC_BIN") or shutil.which("rustc")
    if not rustc: raise RuntimeError("Actual Rust compiler is missing")
    # Git Bash normally prepends usr/bin containing coreutils link.exe. Prepend
    # that exact installed directory explicitly to keep the negative realistic.
    git = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git"
    coreutils = git / "usr/bin/link.exe"
    if not coreutils.is_file(): raise RuntimeError("Actual Git Bash coreutils linker is absent")
    environment = {**os.environ, "PATH": str(coreutils.parent) + os.pathsep + os.environ["PATH"]}
    with tempfile.TemporaryDirectory(prefix="mixel MSVC native compile préflight ") as temporary:
        root = Path(temporary); source = root / "actual fixture.rs"; executable = root / "actual fixture.exe"
        source.write_text('fn main() { println!("PASS: explicit MSVC linked actual Rust despite PATH coreutils shadow"); }\n', encoding="utf-8")
        command = [rustc, "--edition=2021", "--deny=warnings", "--crate-name", "mixel_native_linker_control", str(source), "-o", str(executable), "-C", "link-arg=/INCREMENTAL:NO"]
        rejected = subprocess.run(command + ["-C", "linker=" + str(coreutils)], capture_output=True, text=True, timeout=60, env=environment)
        # GNU diagnostics vary with argument count, quoting and locale. The
        # actual same Rust source/flags must fail through the installed Git
        # coreutils executable and succeed through verified Microsoft LINK.
        if rejected.returncode == 0 or "linking with" not in rejected.stderr or "link.exe" not in rejected.stderr:
            raise RuntimeError("Actual coreutils linker did not reproduce the failed native gate")
        subprocess.run(rustc_command(command, environment=environment), check=True, timeout=60, env=environment)
        subprocess.run([str(executable)], check=True, timeout=10, env=environment)
        print("PASS: actual coreutils negative control; verified native linker and Cargo path agree, spaced Unicode source/output and Rust flags preserved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--native-windows", action="store_true"); arguments = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RustToolchainTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful(): raise SystemExit(1)
    if arguments.native_windows: native_git_bash_control()
