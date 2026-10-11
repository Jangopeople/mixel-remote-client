#!/usr/bin/env python3
"""Exercise portable QA source boundaries and malformed PE/result refusals."""
import copy
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from rust_toolchain import rustc_command

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


bundle = load("mixel_standard_qa_bundle", "build-standard-lease-qa-bundle.py")
lease = load("mixel_standard_qa_source", "test-support-lease.py")


def owned_pe(name="kernel32.dll"):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<H", data, 148, 240)
    struct.pack_into("<H", data, 152, 0x20B)
    struct.pack_into("<I", data, 260, 16)
    struct.pack_into("<II", data, 272, 0x1000, 40)
    struct.pack_into("<IIII", data, 400, 512, 0x1000, 512, 512)
    struct.pack_into("<IIIII", data, 512, 0, 0, 0, 0x1040, 0)
    data[576:576 + len(name) + 1] = name.encode("ascii") + b"\0"
    return data


class PortableSourceTests(unittest.TestCase):
    def test_current_generated_body_changes_only_main_entry_assertion(self):
        original = (ROOT / "scripts/support-invite-guard.rs").read_text(encoding="utf-8")
        source = lease.standard_windows_fixture_body(original, Path("owned-qa-source"), "mixel-standard-lease-" + "a" * 32 + "-", "Global\\Mixel-Remote-Standard-Lease-" + "b" * 32)
        changed = bundle.portable_body(source)
        self.assertEqual(changed.count(bundle.REFUSAL), 1)
        self.assertEqual(changed.replace(bundle.REFUSAL, "", 1), source)
        self.assertLess(changed.index(bundle.REFUSAL), changed.index("lease_main();"))
        self.assertNotIn('const EVENT: &str = "Global\\\\Mixel-Remote-Attended', changed)

    def test_missing_duplicate_or_already_narrowed_entry_is_refused(self):
        for body in ("fn main() {}", bundle.ENTRY * 2, bundle.REFUSAL + bundle.ENTRY):
            with self.subTest(body=body), self.assertRaises(ValueError):
                bundle.portable_body(body)

    def test_nonwindows_or_wrong_job_cannot_start_compilation(self):
        if bundle.os.name == "nt" and bundle.os.environ.get("GITHUB_JOB") == bundle.JOB:
            self.skipTest("This source control is deliberately outside the compilation job")
        with self.assertRaisesRegex(ValueError, "explicitly scoped Windows QA job"):
            bundle.build(ROOT / "mixel-qa-must-not-be-created")
        self.assertFalse((ROOT / "mixel-qa-must-not-be-created").exists())

    def test_actual_git_blob_binding_rejects_changed_and_untracked_build_source(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-git-binding-") as folder:
            root = Path(folder)
            names = (*bundle.SOURCES, *bundle.ADAPTERS.values(), bundle.BUILD_SOURCE, *bundle.BUILD_CONTROLS)
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("owned source: " + name + "\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Mixel QA", "-c", "user.email=qa@example.test", "commit", "--quiet", "-m", "Owned QA source control"], cwd=root, check=True, capture_output=True)
            with patch.object(bundle, "ROOT", root):
                self.assertEqual(len(bundle.git_source_bindings()), 13)
                builder = root / bundle.BUILD_SOURCE
                original = builder.read_bytes()
                builder.write_bytes(original + b"changed builder\n")
                with self.assertRaisesRegex(ValueError, "exact committed blob"):
                    bundle.git_source_bindings()
                builder.write_bytes(original)
                helper = root / "scripts/rust_toolchain.py"
                original_helper = helper.read_bytes()
                helper.write_bytes(original_helper + b"changed linker selection\n")
                with self.assertRaisesRegex(ValueError, "exact committed blob"):
                    bundle.git_source_bindings()
                helper.write_bytes(original_helper)
                observer = root / "scripts/windows-standard-namespace-readonly.rs"
                original_observer = observer.read_bytes()
                observer.write_bytes(original_observer + b"changed compiled namespace observer\n")
                with self.assertRaisesRegex(ValueError, "exact committed blob"):
                    bundle.git_source_bindings()
                observer.write_bytes(original_observer)
                launch = root / "scripts/test-support-launch-windows.ps1"
                original_launch = launch.read_bytes()
                launch.write_bytes(original_launch + b"changed retained native ownership\n")
                with self.assertRaisesRegex(ValueError, "exact committed blob"):
                    bundle.git_source_bindings()
                launch.write_bytes(original_launch)
                subprocess.run(["git", "rm", "--cached", bundle.BUILD_SOURCE], cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "-c", "user.name=Mixel QA", "-c", "user.email=qa@example.test", "commit", "--quiet", "-m", "Remove only the owned build-source binding"], cwd=root, check=True, capture_output=True)
                with self.assertRaises(subprocess.CalledProcessError):
                    bundle.git_source_bindings()

    def test_both_adapters_pin_exact_integrated_source_and_linker_binding(self):
        for name in bundle.ADAPTERS.values():
            source = (ROOT / name).read_text(encoding="utf-8-sig")
            pins = dict(re.findall(r"'(scripts/[^']+)' = '([0-9a-f]{64})'", source))
            self.assertEqual(set(pins), set(bundle.SOURCES))
            for path, value in pins.items():
                self.assertEqual(value, hashlib.sha256((ROOT / path).read_bytes()).hexdigest())
            self.assertEqual(source.count("'scripts/rust_toolchain.py'"), 1)
            source_allow = re.search(r"\$sourceAllow=@\(([^\n]+)\)", source)
            self.assertIsNotNone(source_allow)
            actual = re.findall(r"'([^']+)'", source_allow.group(1))
            expected = (*bundle.SOURCES, *bundle.ADAPTERS.values(), bundle.BUILD_SOURCE, *bundle.BUILD_CONTROLS)
            self.assertEqual(len(actual), 13)
            self.assertEqual(set(actual), set(expected))

    def test_actual_static_compile_selects_native_linker_preserves_flags_and_rejects_conflict(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-native-linker-") as folder:
            tools = Path(folder).resolve()
            linker = tools / "bin/Hostx64/x64/link.exe"
            linker.parent.mkdir(parents=True)
            linker.write_bytes(b"MZ native fixture")
            environment = {"VCTOOLSINSTALLDIR": str(tools)}
            verified = []
            def select(command):
                return rustc_command(command, windows=True, environment=environment,
                                     verifier=lambda path: verified.append(path))
            with patch.object(bundle, "rustc_command", side_effect=select), patch.object(bundle.subprocess, "run") as run:
                bundle.compile_static_fixture("explicit rustc", Path("owned source.rs"), Path("owned app.exe"))
                self.assertEqual(verified, [linker])
                self.assertEqual(run.call_args.args[0], ["explicit rustc", *bundle.STATIC_ARGS,
                                 "owned source.rs", "-o", "owned app.exe", "-C", "linker=" + str(linker)])
                self.assertEqual(run.call_args.kwargs, {"capture_output": True, "timeout": 90})
                run.reset_mock()
                environment["cargo_target_x86_64_pc_windows_msvc_linker"] = str(tools / "shadow-link.exe")
                with self.assertRaisesRegex(RuntimeError, "different Windows linkers"):
                    bundle.compile_static_fixture("explicit rustc", Path("owned.rs"), Path("owned.exe"))
                run.assert_not_called()


class OwnedPEMetadataTests(unittest.TestCase):
    def test_bounded_x64_system_import(self):
        self.assertEqual(bundle.pe_imports(owned_pe()), ["kernel32.dll"])

    def test_malformed_header_sections_and_import_directory_are_refused(self):
        values = [b"", b"MZ" * 64]
        for offset, fmt, value in ((0x3C, "<I", 4096), (132, "<H", 0x14C), (134, "<H", 0),
                                   (134, "<H", 97), (148, "<H", 1), (152, "<H", 0x10B),
                                   (260, "<I", 1), (272, "<I", 0), (276, "<I", 19),
                                   (276, "<I", 65537), (412, "<I", 900), (524, "<I", 0x5000)):
            data = owned_pe()
            struct.pack_into(fmt, data, offset, value)
            values.append(data)
        values.append(owned_pe()[:700])
        empty = owned_pe()
        empty[512:532] = bytes(20)
        values.append(empty)
        missing_end = owned_pe()
        struct.pack_into("<IIIII", missing_end, 532, 0, 0, 0, 0x1040, 0)
        values.append(missing_end)
        self.assertEqual(len(values), 17)
        for data in values:
            with self.subTest(length=len(data)), self.assertRaises((ValueError, UnicodeError)):
                bundle.pe_imports(data)

    def test_ambiguous_rva_and_unterminated_name_are_refused(self):
        data = owned_pe()
        struct.pack_into("<H", data, 134, 2)
        data[432:472] = data[392:432]
        with self.assertRaisesRegex(ValueError, "not uniquely file-backed"):
            bundle.pe_imports(data)
        data = owned_pe()
        data[576:836] = b"a" * 260
        with self.assertRaisesRegex(ValueError, "name exceeds bound"):
            bundle.pe_imports(data)

    def test_directory_count_must_fit_actual_optional_header(self):
        data = owned_pe()
        struct.pack_into("<H", data, 148, 128)
        data[280:320] = data[392:432]
        with self.assertRaisesRegex(ValueError, "import-directory contract"):
            bundle.pe_imports(data)
        struct.pack_into("<I", data, 260, 2)
        self.assertEqual(bundle.pe_imports(data), ["kernel32.dll"])
        struct.pack_into("<I", data, 260, 17)
        with self.assertRaisesRegex(ValueError, "import-directory contract"):
            bundle.pe_imports(data)

    def test_external_runtime_and_invalid_dll_names_are_refused(self):
        for name in ("vcruntime140.dll", "msvcp140.dll", "msvcr100.dll", "concrt140.dll", "ucrtbase.dll", "../secret.dll", "kernel32", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundle.pe_imports(owned_pe(name))


class OwnedPackageInventoryTests(unittest.TestCase):
    def populate(self, root):
        for name in bundle.PACKAGE_MEMBERS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"owned inventory control")

    def test_exact_compiler_auxiliary_names_are_excluded_before_manifest(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-package-") as folder:
            root = Path(folder)
            self.populate(root)
            for name in ("lease-standard.pdb", "lease-standard-gui.pdb"):
                (root / "fixture" / name).write_bytes(b"owned synthetic auxiliary inventory control")
            with self.assertRaisesRegex(ValueError, "file inventory differs"):
                bundle.bundle_members(root)
            bundle.remove_owned_compiler_sidecars(root / "fixture")
            self.assertEqual(len(bundle.bundle_members(root)), 12)
            self.assertEqual({p.relative_to(root).as_posix() for p in bundle.bundle_members(root)}, bundle.PACKAGE_MEMBERS)

    def test_unexpected_missing_or_nested_members_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-package-") as folder:
            root = Path(folder)
            self.populate(root)
            extra = root / "fixture" / "unexpected.pdb"
            extra.write_bytes(b"owned synthetic unexpected inventory control")
            bundle.remove_owned_compiler_sidecars(root / "fixture")
            self.assertTrue(extra.exists())
            with self.assertRaisesRegex(ValueError, "file inventory differs"):
                bundle.bundle_members(root)
            extra.unlink()
            nested = root / "fixture" / "unexpected"
            nested.mkdir()
            with self.assertRaisesRegex(ValueError, "directory inventory differs"):
                bundle.bundle_members(root)
            nested.rmdir()
            (root / "fixture" / "lease-standard.exe").unlink()
            with self.assertRaisesRegex(ValueError, "missing a required member"):
                bundle.bundle_members(root)

    def test_auxiliary_directory_and_oversized_file_are_refused_without_deletion(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-package-") as folder:
            fixture = Path(folder)
            sidecar = fixture / "lease-standard.pdb"
            sidecar.mkdir()
            with self.assertRaisesRegex(ValueError, "bounded ordinary file"):
                bundle.remove_owned_compiler_sidecars(fixture)
            self.assertTrue(sidecar.is_dir())
            sidecar.rmdir()
            with sidecar.open("wb") as stream:
                stream.truncate(64 * 1024 * 1024 + 1)
            with self.assertRaisesRegex(ValueError, "bounded ordinary file"):
                bundle.remove_owned_compiler_sidecars(fixture)
            self.assertEqual(sidecar.stat().st_size, 64 * 1024 * 1024 + 1)


class RetainedLoaderResultTests(unittest.TestCase):
    def test_joined_loader_secondary_observation_is_removed_before_exact_inventory(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-loader-") as folder:
            root = Path(folder)
            OwnedPackageInventoryTests().populate(root)
            fixture = root / "fixture"
            record = {"pid": 123, "sourceStatus": "ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false",
                      "rawOpenError": 2, "attestation": "", "requiresClick": False, "ownerFailed": False}

            class Child:
                pid, returncode = 123, None

                def __init__(self, *_args, **_kwargs):
                    (fixture / "fixture-result.json").write_text(json.dumps(record), encoding="utf-8")
                    (fixture / "fixture-namespace.json").write_text("{}", encoding="utf-8")

                def poll(self):
                    return self.returncode

                def communicate(self, timeout):
                    self.returncode = 0
                    return b"", b""

                def kill(self):
                    raise AssertionError("Already completed source loader must not be killed")

            refusal = subprocess.CompletedProcess([], 1, b"", bundle.REFUSAL.split('"')[1].encode())
            with patch.object(bundle.subprocess, "run", return_value=refusal), patch.object(bundle.subprocess, "Popen", Child):
                result = bundle.loader_smoke(fixture / "lease-standard.exe", fixture)
            self.assertFalse(result["standardUserQualified"])
            self.assertFalse((fixture / "fixture-namespace.json").exists())
            self.assertEqual(len(bundle.bundle_members(root)), 12)

    def test_secondary_observation_refuses_preexisting_or_nonregular_and_oversized_members(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-loader-bounds-") as folder:
            root = Path(folder)
            observation = root / "fixture-namespace.json"
            observation.mkdir()
            with self.assertRaisesRegex(ValueError, "new owned fixture records"):
                bundle.loader_smoke(root / "unused.exe", root)
            with self.assertRaisesRegex(ValueError, "bounded ordinary file"):
                bundle.remove_owned_loader_observation(observation)
            self.assertTrue(observation.is_dir())
            observation.rmdir()
            observation.write_bytes(b"x" * 4097)
            with self.assertRaisesRegex(ValueError, "bounded ordinary file"):
                bundle.remove_owned_loader_observation(observation)
            self.assertEqual(observation.stat().st_size, 4097)
            observation.unlink()
            target = root / "must-preserve.json"
            target.write_bytes(b"owned symlink target")
            observation.write_bytes(b"owned reparse metadata control")
            reparse = type("ReparseRecord", (), {"st_mode": 0o100644, "st_file_attributes": 0x400, "st_size": 30})()
            with patch.object(bundle.Path, "lstat", return_value=reparse), self.assertRaisesRegex(ValueError, "bounded ordinary file"):
                bundle.remove_owned_loader_observation(observation)
            self.assertEqual(observation.read_bytes(), b"owned reparse metadata control")
            if bundle.os.name != "nt":
                # Exercise an actual host symlink too; Windows reparse metadata
                # is tested above without requiring a symlink-creation privilege.
                observation.unlink()
                observation.symlink_to(target)
                with self.assertRaisesRegex(ValueError, "new owned fixture records"):
                    bundle.loader_smoke(root / "unused.exe", root)
                with self.assertRaisesRegex(ValueError, "bounded ordinary file"):
                    bundle.remove_owned_loader_observation(observation)
                self.assertTrue(observation.is_symlink())
            self.assertEqual(target.read_bytes(), b"owned symlink target")

    def test_unconfirmed_child_exit_preserves_secondary_observation(self):
        with tempfile.TemporaryDirectory(prefix="mixel-qa-loader-unjoined-") as folder:
            root = Path(folder)

            class Child:
                pid, returncode = 123, None

                def __init__(self, *_args, **_kwargs):
                    (root / "fixture-namespace.json").write_text("{}", encoding="utf-8")

                def poll(self):
                    return None

                def kill(self):
                    pass

                def communicate(self, timeout):
                    raise subprocess.TimeoutExpired("owned loader", timeout)

            refusal = subprocess.CompletedProcess([], 1, b"", bundle.REFUSAL.split('"')[1].encode())
            with patch.object(bundle.subprocess, "run", return_value=refusal), patch.object(bundle.subprocess, "Popen", Child), patch.object(bundle.time, "monotonic", side_effect=(0, 11)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    bundle.loader_smoke(root / "unused.exe", root)
            self.assertEqual((root / "fixture-namespace.json").read_text(), "{}")

    def test_safe_result_is_only_a_loader_classification(self):
        record = {"pid": 123, "sourceStatus": "ownerFailed=false;ownerError=none;kernel=unavailable;probeError=5;memoryActive=false",
                  "rawOpenError": 5, "attestation": "guard-unavailable", "requiresClick": True, "ownerFailed": False}
        bundle.safe_result(record, 123)

    def test_unowned_pid_wrong_types_and_noncanonical_results_are_refused(self):
        record = {"pid": 123, "sourceStatus": "ownerFailed=false;ownerError=none;kernel=absent;probeError=none;memoryActive=false",
                  "rawOpenError": 2, "attestation": "", "requiresClick": False, "ownerFailed": False}
        mutations = [("pid", 0), ("pid", 124), ("pid", True), ("pid", "123"), ("rawOpenError", -1),
                     ("rawOpenError", 0x100000000), ("rawOpenError", True), ("rawOpenError", "2"),
                     ("attestation", "ready"), ("requiresClick", "false"), ("ownerFailed", "false"),
                     ("sourceStatus", "invalid"), ("sourceStatus", None)]
        for status in (
            "ownerFailed=false;ownerError=none;kernel=unavailable;probeError=05;memoryActive=false",
            "ownerFailed=false;ownerError=none;kernel=unavailable;probeError=4294967296;memoryActive=false",
            "ownerFailed=false;ownerError=5;kernel=absent;probeError=none;memoryActive=false",
            "ownerFailed=false;ownerError=none;kernel=absent;probeError=5;memoryActive=false",
            "ownerFailed=true;ownerError=5;kernel=absent;probeError=none;memoryActive=false",
            "ownerFailed=false;ownerError=none;kernel=unavailable;probeError=-2147483649;memoryActive=false",
        ):
            mutations.append(("sourceStatus", status))
        for key, value in mutations:
            changed = copy.deepcopy(record)
            changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                bundle.safe_result(changed, 123)
        for changed in (None, {**record, "extra": True}, {key: value for key, value in record.items() if key != "pid"}):
            with self.assertRaises(ValueError):
                bundle.safe_result(changed, 123)


if __name__ == "__main__":
    unittest.main()
