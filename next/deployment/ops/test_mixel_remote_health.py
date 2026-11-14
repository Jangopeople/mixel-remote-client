#!/usr/bin/env python3
"""Tests of the health monitor's own logic: python3 -m unittest next/deployment/ops/test_mixel_remote_health.py"""
import importlib.util
import os
import types
import unittest

_spec = importlib.util.spec_from_file_location("mixel_remote_health", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixel-remote-health.py"))
health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health)


class Docker:
    """A stand-in for the docker command: which images exist, and what `docker load` brings."""

    def __init__(self, images=(), archive_holds=None, load_fails=False):
        self.images = set(images)
        self.archive_holds = archive_holds
        self.load_fails = load_fails
        self.calls = []

    def __call__(self, command, **_):
        self.calls.append(command[1:3])
        if command[1:3] == ["image", "inspect"]:
            return types.SimpleNamespace(returncode=0 if command[3] in self.images else 1, stdout="", stderr="")
        if command[1] == "load":
            if self.load_fails:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="open archive: unexpected EOF")
            if self.archive_holds:
                self.images.add(self.archive_holds)
            return types.SimpleNamespace(returncode=0, stdout="Loaded image", stderr="")
        raise AssertionError(command)


IMAGE = "mixel-remote-status-probe:current"
COPY = "/var/lib/mixel-remote-status/probe-image.tar"


class ProbeImage(unittest.TestCase):
    def test_an_image_that_is_there_is_left_alone(self):
        docker = Docker(images=[IMAGE])
        self.assertEqual(health.ensure_probe_image(IMAGE, COPY, run=docker, exists=lambda _: True), "")
        self.assertEqual(docker.calls, [["image", "inspect"]])

    def test_a_pruned_image_is_loaded_from_its_copy(self):
        docker = Docker(archive_holds=IMAGE)
        note = health.ensure_probe_image(IMAGE, COPY, run=docker, exists=lambda path: path == COPY)
        self.assertIn("loaded from its copy", note)
        self.assertIn(IMAGE, docker.images)

    def test_without_a_copy_the_check_says_what_is_missing(self):
        with self.assertRaises(health.CheckError) as raised:
            health.ensure_probe_image(IMAGE, COPY, run=Docker(), exists=lambda _: False)
        self.assertIn("no copy", str(raised.exception))

    def test_a_copy_that_does_not_load_or_holds_another_image_fails_the_check(self):
        for docker in (Docker(load_fails=True), Docker(archive_holds="something:else")):
            with self.assertRaises(health.CheckError) as raised:
                health.ensure_probe_image(IMAGE, COPY, run=docker, exists=lambda _: True)
            self.assertIn("could not be loaded", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
