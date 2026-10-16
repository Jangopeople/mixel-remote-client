#!/usr/bin/env python3
"""Exercise exact post-build cache pruning and non-following source copies."""
import argparse
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


def load_helper(path):
    spec = importlib.util.spec_from_file_location("input_lifecycle_flutter_inventory", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = None


class ProductionTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mixel-input-inventory-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        self.external = self.base / "external"
        self.external.mkdir()
        (self.external / "sentinel").write_bytes(b"outside-owned-tree-must-not-be-read-or-copied")
        self.write("lib/models/input_model.dart", b"actual production bytes\n")

    def write(self, rel, data=b"production\n"):
        path = self.source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def generated_tree(self, rel):
        root = self.source.joinpath(*rel)
        root.mkdir(parents=True)
        links = root / ".plugin_symlinks"
        links.mkdir()
        (links / "window_manager").symlink_to(self.external, target_is_directory=True)
        (links / "broken").symlink_to(self.base / "missing", target_is_directory=True)
        (root / "generated.cache").write_bytes(b"generated-only\n")
        return root

    def assert_both_refuse(self):
        with self.assertRaises((ValueError, OSError)):
            HELPER.inventory(self.source)
        with self.assertRaises((ValueError, OSError)):
            HELPER.copy_source(self.source, self.base / "copy")
        self.assertEqual((self.external / "sentinel").read_bytes(), b"outside-owned-tree-must-not-be-read-or-copied")

    def test_all_seven_exact_generated_roots_are_pruned_before_descent(self):
        expected = HELPER.inventory(self.source)
        for rel in HELPER.GENERATED_SOURCE_DIRS:
            self.generated_tree(rel)
        real_listdir = os.listdir
        excluded_ids = {os.stat(self.source.joinpath(*rel)).st_ino for rel in HELPER.GENERATED_SOURCE_DIRS}

        def no_generated_descent(fd):
            self.assertNotIn(os.fstat(fd).st_ino, excluded_ids)
            return real_listdir(fd)

        with mock.patch.object(HELPER.os, "listdir", side_effect=no_generated_descent):
            self.assertEqual(HELPER.inventory(self.source), expected)
            self.assertEqual(HELPER.copy_source(self.source, self.base / "copy"), expected)
        self.assertEqual(HELPER.inventory(self.base / "copy"), expected)
        for rel in HELPER.GENERATED_SOURCE_DIRS:
            self.assertFalse(self.base.joinpath("copy", *rel).exists())

    def test_each_desktop_generated_cache_with_broken_plugin_link_is_supported(self):
        expected = HELPER.inventory(self.source)
        for rel in (("windows", "flutter", "ephemeral"), ("linux", "flutter", "ephemeral"),
                    ("macos", "Flutter", "ephemeral")):
            with self.subTest(root=rel):
                self.generated_tree(rel)
                self.assertEqual(HELPER.inventory(self.source), expected)

    def test_non_generated_ephemeral_directories_remain_inventoried_and_copied(self):
        rels = ("lib/ephemeral/kept.dart", "windows/other/ephemeral/kept.txt",
                "macos/flutter/ephemeral/kept.txt", "linux/flutter/ephemeral-extra/kept.txt",
                "lib/Pods/kept.dart", "lib/.symlinks/kept.dart", "macos/other/Pods/kept.txt")
        for rel in rels:
            self.write(rel)
        expected = HELPER.inventory(self.source)
        self.assertTrue(set(rels).issubset(expected))
        HELPER.copy_source(self.source, self.base / "copy")
        self.assertEqual(HELPER.inventory(self.base / "copy"), expected)

    def test_non_generated_ephemeral_and_plugin_symlinks_remain_fatal(self):
        for i, rel in enumerate(("lib/ephemeral/link", "windows/other/ephemeral/link",
                                 "macos/flutter/ephemeral/link", "linux/flutter/ephemeral-extra/link",
                                 "lib/.plugin_symlinks/window_manager", "lib/Pods/link", "lib/.symlinks/link")):
            with self.subTest(root=rel):
                path = self.source / rel
                path.parent.mkdir(parents=True)
                path.symlink_to(self.external, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink or special file refused"):
                    HELPER.inventory(self.source)
                path.unlink()

    def test_every_generated_root_must_be_a_real_directory(self):
        for rel in HELPER.GENERATED_SOURCE_DIRS:
            with self.subTest(root=rel):
                path = self.source.joinpath(*rel)
                path.parent.mkdir(parents=True, exist_ok=True)
                for kind in ("directory-link", "broken-link", "file"):
                    with self.subTest(kind=kind):
                        if kind == "file":
                            path.write_bytes(b"not a generated directory")
                        else:
                            path.symlink_to(self.external if kind == "directory-link" else self.base / "missing")
                        with self.assertRaisesRegex(ValueError, "Generated source root must be a real directory"):
                            HELPER.inventory(self.source)
                        path.unlink()

    def test_arbitrary_file_directory_and_broken_symlinks_are_fatal(self):
        for name, target in (("file-link", self.external / "sentinel"), ("directory-link", self.external),
                             ("broken-link", self.base / "missing")):
            with self.subTest(kind=name):
                path = self.source / name
                path.symlink_to(target)
                with self.assertRaisesRegex(ValueError, "symlink or special file refused"):
                    HELPER.inventory(self.source)
                path.unlink()

    def test_existing_any_component_cache_exclusions_remain_exact(self):
        expected = HELPER.inventory(self.source)
        for name in (".dart_tool", "build", ".git"):
            (self.source / name).symlink_to(self.external, target_is_directory=True)
            self.write("lib/nested/" + name + "/ignored.dat")
        HELPER.copy_source(self.source, self.base / "copy")
        self.assertEqual(HELPER.inventory(self.source), expected)
        self.assertEqual(HELPER.inventory(self.base / "copy"), expected)

    def test_source_root_symlink_is_refused(self):
        link = self.base / "linked-source"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "source root must be a real directory"):
            HELPER.inventory(link)
        with self.assertRaisesRegex(ValueError, "source root must be a real directory"):
            HELPER.copy_source(link, self.base / "copy")

    def test_existing_destination_root_symlink_is_refused_without_writes(self):
        destination = self.base / "copy"
        destination.symlink_to(self.external, target_is_directory=True)
        before = set(self.external.iterdir())
        with self.assertRaises(FileExistsError):
            HELPER.copy_source(self.source, destination)
        self.assertEqual(set(self.external.iterdir()), before)

    def test_output_parent_symlink_is_refused_without_writes(self):
        parent = self.base / "output"
        parent.symlink_to(self.external, target_is_directory=True)
        with self.assertRaises(OSError):
            HELPER.copy_source(self.source, parent / "app")
        self.assertFalse((self.external / "app").exists())

    def test_file_replaced_by_symlink_at_open_is_refused_without_following(self):
        path = self.write("race.txt")
        real_open = os.open
        triggered = []

        def replace_before_open(name, flags, *args, **kwargs):
            if name == "race.txt" and not triggered:
                triggered.append(True)
                path.rename(self.base / "original-race.txt")
                path.symlink_to(self.external / "sentinel")
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(HELPER.os, "open", side_effect=replace_before_open):
            with self.assertRaises(OSError):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(triggered)
        self.assertFalse((self.base / "copy/race.txt").exists())

    def test_directory_replaced_by_symlink_at_open_is_refused_without_following(self):
        path = self.write("racedir/file.txt").parent
        real_open = os.open
        triggered = []

        def replace_before_open(name, flags, *args, **kwargs):
            if name == "racedir" and not triggered:
                triggered.append(True)
                path.rename(self.base / "original-racedir")
                path.symlink_to(self.external, target_is_directory=True)
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(HELPER.os, "open", side_effect=replace_before_open):
            with self.assertRaises(OSError):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(triggered)
        self.assertFalse((self.base / "copy/racedir/sentinel").exists())

    def test_file_modified_during_read_is_refused(self):
        path = self.write("race.txt", b"old bytes\n")
        inode = path.stat().st_ino
        real_read = os.read
        changed = []

        def change_after_read(fd, count):
            data = real_read(fd, count)
            if data and os.fstat(fd).st_ino == inode and not changed:
                changed.append(True)
                path.write_bytes(b"changed during actual read\n")
            return data

        with mock.patch.object(HELPER.os, "read", side_effect=change_after_read):
            with self.assertRaisesRegex(ValueError, "source file changed"):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(changed)

    def test_file_replaced_after_read_is_refused(self):
        path = self.write("race.txt", b"old bytes\n")
        inode = path.stat().st_ino
        real_read = os.read
        changed = []

        def replace_after_read(fd, count):
            data = real_read(fd, count)
            if data and os.fstat(fd).st_ino == inode and not changed:
                changed.append(True)
                path.rename(self.base / "original-race.txt")
                path.symlink_to(self.external / "sentinel")
            return data

        with mock.patch.object(HELPER.os, "read", side_effect=replace_after_read):
            with self.assertRaisesRegex(ValueError, "source file changed"):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(changed)

    def test_parent_replaced_during_descent_is_refused(self):
        path = self.write("racedir/file.txt").parent
        inode = path.stat().st_ino
        real_listdir = os.listdir
        changed = []

        def replace_after_open(fd):
            if os.fstat(fd).st_ino == inode and not changed:
                changed.append(True)
                path.rename(self.base / "original-racedir")
                path.symlink_to(self.external, target_is_directory=True)
            return real_listdir(fd)

        with mock.patch.object(HELPER.os, "listdir", side_effect=replace_after_open):
            with self.assertRaisesRegex(ValueError, "source directory (entries )?changed"):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(changed)
        self.assertFalse((self.base / "copy/racedir/sentinel").exists())

    def test_traversal_errors_are_fatal_instead_of_partial_inventories(self):
        with mock.patch.object(HELPER.os, "listdir", side_effect=PermissionError("owned denial")):
            with self.assertRaises(PermissionError):
                HELPER.inventory(self.source)
        with mock.patch.object(HELPER.os, "listdir", side_effect=FileNotFoundError("owned disappearance")):
            with self.assertRaises(FileNotFoundError):
                HELPER.copy_source(self.source, self.base / "copy")

    def test_changed_copied_bytes_are_detected_by_original_inventory_comparison(self):
        before = HELPER.inventory(self.source)
        copied = self.base / "copy"
        self.assertEqual(HELPER.copy_source(self.source, copied), before)
        (copied / "lib/models/input_model.dart").write_bytes(b"corrupt copied production bytes\n")
        self.assertNotEqual(HELPER.inventory(copied), before)
        self.assertEqual(HELPER.inventory(self.source), before)

    def test_entry_added_after_enumeration_is_refused(self):
        inode = self.source.stat().st_ino
        real_listdir = os.listdir
        changed = []

        def add_after_listing(fd):
            names = real_listdir(fd)
            if os.fstat(fd).st_ino == inode and not changed:
                changed.append(True)
                self.write("added-after-listing.dart")
            return names

        with mock.patch.object(HELPER.os, "listdir", side_effect=add_after_listing):
            with self.assertRaisesRegex(ValueError, "source directory entries changed"):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(changed)

    def test_output_ancestor_replacement_cannot_redirect_copy(self):
        ancestor = self.base / "owned-ancestor"
        (ancestor / "parent").mkdir(parents=True)
        (self.external / "parent").mkdir()
        real_open = os.open
        changed = []

        def replace_ancestor(name, flags, *args, **kwargs):
            if name == "owned-ancestor" and not changed:
                changed.append(True)
                ancestor.rename(self.base / "original-ancestor")
                ancestor.symlink_to(self.external, target_is_directory=True)
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(HELPER.os, "open", side_effect=replace_ancestor):
            with self.assertRaises(OSError):
                HELPER.copy_source(self.source, ancestor / "parent/app")
        self.assertTrue(changed)
        self.assertFalse((self.external / "parent/app").exists())

    def test_source_ancestor_symlink_is_refused(self):
        link = self.base / "linked-ancestor"
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OSError):
            HELPER.inventory(link / "source")

    def test_readonly_directories_are_populated_before_mode_is_preserved(self):
        path = self.write("readonly/kept.dart")
        path.chmod(0o444)
        path.parent.chmod(0o555)
        self.addCleanup(path.parent.chmod, 0o755)
        copied = self.base / "copy"
        HELPER.copy_source(self.source, copied)
        self.addCleanup((copied / "readonly").chmod, 0o755)
        self.assertEqual((copied / "readonly/kept.dart").read_bytes(), path.read_bytes())
        self.assertEqual((copied / "readonly").stat().st_mode & 0o777, 0o555)
        self.assertEqual((copied / "readonly/kept.dart").stat().st_mode & 0o777, 0o444)

    def test_fstat_error_closes_newly_opened_file_descriptor(self):
        path = self.write("failure.txt")
        inode = path.stat().st_ino
        real_fstat = os.fstat
        failed_fd = []

        def fail_owned_descriptor(fd):
            value = real_fstat(fd)
            if value.st_ino == inode and not failed_fd:
                failed_fd.append(fd)
                raise OSError("owned fstat failure")
            return value

        with mock.patch.object(HELPER.os, "fstat", side_effect=fail_owned_descriptor):
            with self.assertRaisesRegex(OSError, "owned fstat failure"):
                HELPER.inventory(self.source)
        self.assertEqual(len(failed_fd), 1)
        with self.assertRaises(OSError):
            real_fstat(failed_fd[0])

    def test_regular_file_replaced_by_fifo_at_open_is_nonblocking_and_refused(self):
        path = self.write("race-fifo.txt")
        real_open = os.open
        changed = []

        def replace_before_open(name, flags, *args, **kwargs):
            if name == "race-fifo.txt" and not changed:
                changed.append(True)
                path.rename(self.base / "original-race-fifo.txt")
                os.mkfifo(path)
                self.assertTrue(flags & os.O_NONBLOCK, "Regular-file opens must not block on FIFO replacement")
            return real_open(name, flags, *args, **kwargs)

        with mock.patch.object(HELPER.os, "open", side_effect=replace_before_open):
            with self.assertRaisesRegex(ValueError, "source changed during open"):
                HELPER.copy_source(self.source, self.base / "copy")
        self.assertTrue(changed)
        self.assertFalse((self.base / "copy/race-fifo.txt").exists())

    def test_special_files_are_refused(self):
        os.mkfifo(self.source / "unexpected-fifo")
        self.assert_both_refuse()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", type=Path,
                        default=Path(__file__).with_name("test-support-input-lifecycle-flutter.py"))
    args, test_args = parser.parse_known_args()
    HELPER = load_helper(args.helper)
    unittest.main(argv=[__file__] + test_args)
