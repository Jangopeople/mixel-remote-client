#!/usr/bin/env python3
"""Exercise actual test archive commands with hostile inherited Git EOL settings."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONSUMERS = (
    ("test-support-macos-cleanup.py", "source_files"),
    ("test-support-macos-cleanup.py", "full_branding"),
    ("test-support-macos-termination.py", "full_branding"),
    ("test-support-flutter-analysis.py", "shell_controls"),
    ("test-support-flutter-analysis.py", "pipeline"),
)


def command_expression(filename, function):
    tree = ast.parse((ROOT / "scripts" / filename).read_text(encoding="utf-8"))
    body = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function)
    assignments = [node.value for node in ast.walk(body)
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "archive_args"
                           for target in node.targets)]
    if assignments:
        assert len(assignments) == 1
        return assignments[0]
    expressions = [node for node in ast.walk(body) if isinstance(node, ast.List)
                   and any(isinstance(item, ast.Constant) and item.value == "archive"
                           for item in node.elts)]
    assert len(expressions) == 1
    return expressions[0]


def members(command, environment):
    raw = subprocess.check_output(command, env=environment, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return {entry.name: archive.extractfile(entry).read()
                for entry in archive if entry.isfile()}


class PinnedArchiveTests(unittest.TestCase):
    def test_five_actual_commands_preserve_blob_bytes_and_originals_reproduce(self):
        with tempfile.TemporaryDirectory(prefix="mixel archive préflight ") as directory:
            repo = Path(directory).resolve()
            fixture = {
                ".gitattributes": b"* text=auto\n",
                "scripts/apply-branding.sh": b"#!/bin/bash\nprintf 'Mixel QA\\n'\n",
                "branding/branding.env": b'APP_NAME="Mixel-Remote"\n',
                "flutter/lib/qa.dart": b"// pinned fixture\nvoid main() {}\n",
            }
            for name, data in fixture.items():
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            git = ["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
            subprocess.run([*git, "add", "--", "."], check=True, capture_output=True)
            subprocess.run([*git, "-c", "user.name=Mixel QA", "-c", "user.email=qa@invalid",
                            "commit", "-qm", "Pinned archive fixture"], check=True, capture_output=True)
            configuration = (repo / ".git/config").read_bytes()
            context = {"ROOT": repo, "source": repo, "revision": "HEAD", "mode": "false",
                       "str": str, "list": list,
                       "P": types.SimpleNamespace(PIN="HEAD", FILES={"flutter/lib/qa.dart":
                            hashlib.sha256(fixture["flutter/lib/qa.dart"]).hexdigest()})}
            for filename, function in CONSUMERS:
                with self.subTest(source=filename, function=function):
                    command = eval(compile(ast.Expression(command_expression(filename, function)),
                                           filename, "eval"), {"__builtins__": {}}, context)
                    self.assertEqual(command.count("core.eol=lf"), 1)
                    old = command.copy()
                    index = old.index("core.eol=lf")
                    self.assertEqual(old[index - 1], "-c")
                    del old[index - 1:index + 1]
                    for autocrlf in ("false", "true"):
                        for eol in ("lf", "crlf"):
                            environment = dict(os.environ, GIT_CONFIG_COUNT="2",
                                               GIT_CONFIG_KEY_0="core.autocrlf",
                                               GIT_CONFIG_VALUE_0=autocrlf,
                                               GIT_CONFIG_KEY_1="core.eol", GIT_CONFIG_VALUE_1=eol)
                            result = members(command, environment)
                            self.assertTrue(result)
                            self.assertEqual(result, {name: fixture[name] for name in result})
                            if eol == "crlf":
                                original = members(old, environment)
                                self.assertEqual(original,
                                                 {name: fixture[name].replace(b"\n", b"\r\n")
                                                  for name in original})
                                self.assertNotEqual(original, result)
                            self.assertEqual((repo / ".git/config").read_bytes(), configuration)
                            self.assertEqual({name: (repo / name).read_bytes() for name in fixture},
                                             fixture)
            print("PASS: five actual archive expressions retain exact LF blobs in four inherited "
                  "EOL profiles; original CRLF corruption reproduces; owned config/source unchanged.")


    def test_actual_dependency_checkouts_obey_both_profiles_under_inherited_settings(self):
        source = (ROOT / "scripts/test-support-flutter-analysis.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "dependency_profile")
        pipeline = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "pipeline")
        initial = [node.args[0] for node in ast.walk(pipeline)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "run" and node.args and isinstance(node.args[0], ast.List)
                   and any(isinstance(item, ast.Starred) and isinstance(item.value, ast.Name)
                           and item.value.id == "arguments" for item in node.args[0].elts)]
        self.assertEqual(len(initial), 1)
        selected_env = [node.value for node in ast.walk(pipeline)
                        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                        and target.id == "env" for target in node.targets)]
        self.assertEqual(len(selected_env), 1)
        old_function = ast.parse(ast.unparse(function)).body[0]
        checkouts = [node.args[0] for node in ast.walk(old_function)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id == "run" and node.args and isinstance(node.args[0], ast.List)
                     and any(isinstance(item, ast.Constant) and item.value == "checkout"
                             for item in node.args[0].elts)]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(ast.literal_eval(checkouts[0].elts[3]), "-c")
        self.assertEqual(ast.literal_eval(checkouts[0].elts[5]), "-c")
        del checkouts[0].elts[3:7]
        fixture = {".gitattributes": b"* text=auto\n",
                   ".github/workflows/rust.yml": b"name: owned fixture\non: push\n",
                   "src/keyboard.rs": b"// canonical keyboard fixture\n",
                   "src/linux/grab.rs": b"// canonical Linux producer fixture\n"}
        original_failures = 0
        with tempfile.TemporaryDirectory(prefix="mixel dependency profile préflight ") as directory:
            base = Path(directory).resolve()
            repo = base / "dependency"; repo.mkdir()
            out = base / "logs"; out.mkdir()
            for name, data in fixture.items():
                target = repo / name; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            git = ["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "core.eol=lf"]
            subprocess.run([*git, "add", "--", "."], check=True, capture_output=True)
            subprocess.run([*git, "-c", "user.name=Mixel QA", "-c", "user.email=qa@invalid",
                            "commit", "-qm", "Dependency profile fixture"], check=True, capture_output=True)
            head = subprocess.check_output([*git, "rev-parse", "HEAD"])
            other = base / "other-dependency"
            subprocess.run(["git", "init", "-q", str(other)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(other), "config", "--local", "core.autocrlf", "true"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "--local", "core.eol", "crlf"], check=True)
            other_config = (other / ".git/config").read_bytes()
            def actual_run(command, output, label):
                result = subprocess.run(command, check=True, capture_output=True, timeout=30)
                (output / (label + ".log")).write_bytes(result.stdout + result.stderr)
            context = {"subprocess": subprocess, "run": actual_run, "json": json,
                       "sha": lambda data: hashlib.sha256(data).hexdigest(),
                       "R": types.SimpleNamespace(FILES={"src/keyboard.rs"})}
            exec(compile(ast.Module(body=[function], type_ignores=[]), "actual-profile", "exec"), context)
            old_context = context.copy()
            exec(compile(ast.fix_missing_locations(ast.Module(body=[old_function], type_ignores=[])),
                         "original-profile", "exec"), old_context)
            for autocrlf in ("false", "true"):
                for eol in ("lf", "crlf"):
                    environment = {"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.autocrlf",
                                   "GIT_CONFIG_VALUE_0": autocrlf,
                                   "GIT_CONFIG_KEY_1": "core.eol", "GIT_CONFIG_VALUE_1": eol}
                    with patch.dict(os.environ, environment):
                        for setting in ("false", "true"):
                            with self.subTest(autocrlf=autocrlf, eol=eol, requested=setting):
                                requested = b"\r\n" if setting == "true" else b"\n"
                                inherited = b"\r\n" if autocrlf == "true" or eol == "crlf" else b"\n"
                                label = autocrlf + "-" + eol + "-" + setting
                                for name, data in fixture.items(): (repo / name).write_bytes(data)
                                context["dependency_profile"](out, repo, setting, "profile-" + label)
                                self.assertEqual({name: (repo / name).read_bytes() for name in fixture},
                                                 {name: data.replace(b"\n", requested) for name, data in fixture.items()})
                                config = (repo / ".git/config").read_bytes()
                                caller_before = dict(os.environ)
                                macro_env = eval(compile(ast.Expression(selected_env[0]), "actual-env", "eval"),
                                                 {"dict": dict, "os": os},
                                                 {"repo": repo, "copied": base, "initial_profile": setting})
                                self.assertEqual(macro_env["GIT_CONFIG_COUNT"], "0")
                                self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "config", "core.autocrlf"],
                                                                         env=macro_env).strip(), setting.encode())
                                self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "config", "core.eol"],
                                                                         env=macro_env).strip(), ("crlf" if setting == "true" else "lf").encode())
                                self.assertEqual(subprocess.check_output(["git", "-C", str(other), "config", "core.autocrlf"],
                                                                         env=macro_env).strip(), b"true")
                                self.assertEqual(subprocess.check_output(["git", "-C", str(other), "config", "core.eol"],
                                                                         env=macro_env).strip(), b"crlf")
                                self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "diff", "--name-only", "HEAD"],
                                                                         env=macro_env), b"")
                                self.assertEqual(dict(os.environ), caller_before)
                                for name, data in fixture.items(): (repo / name).write_bytes(data)
                                if inherited == requested:
                                    old_context["dependency_profile"](out, repo, setting, "original-" + label)
                                else:
                                    with self.assertRaises(AssertionError) as refusal:
                                        old_context["dependency_profile"](out, repo, setting, "original-" + label)
                                    self.assertIn(str(refusal.exception), fixture)
                                    original_failures += 1
                                self.assertEqual((repo / ".git/config").read_bytes(), config)
                                command = eval(compile(ast.Expression(initial[0]), "actual-initial", "eval"),
                                               {"str": str}, {"dependency": repo, "initial_profile": setting,
                                                               "arguments": ["checkout", "--quiet", "HEAD", "--", "."]})
                                self.assertEqual(command[3:7], ["-c", "core.autocrlf=" + setting, "-c",
                                                               "core.eol=" + ("crlf" if setting == "true" else "lf")])
                                for name in fixture: (repo / name).unlink()
                                actual_run(command, out, "initial-" + label)
                                self.assertEqual({name: (repo / name).read_bytes() for name in fixture},
                                                 {name: data.replace(b"\n", requested) for name, data in fixture.items()})
                                for name in fixture: (repo / name).unlink()
                                actual_run(command[:3] + command[7:], out, "old-initial-" + label)
                                self.assertEqual({name: (repo / name).read_bytes() for name in fixture},
                                                 {name: data.replace(b"\n", inherited) for name, data in fixture.items()})
                                if inherited != requested: original_failures += 1
                                for name, data in fixture.items(): (repo / name).write_bytes(data)
                                overlays = {"src/keyboard.rs": b"// verified keyboard overlay\n",
                                            "src/linux/grab.rs": b"// native text IO overlay\r\n"}
                                for name, data in overlays.items(): (repo / name).write_bytes(data)
                                context["dependency_profile"](out, repo, setting, "overlay-" + label)
                                self.assertEqual((repo / "src/keyboard.rs").read_bytes(),
                                                 overlays["src/keyboard.rs"].replace(b"\n", requested))
                                self.assertEqual((repo / "src/linux/grab.rs").read_bytes(), overlays["src/linux/grab.rs"])
                                unexpected = repo / ".github/workflows/rust.yml"
                                unexpected.write_bytes(b"unrelated drift\n")
                                before = {name: (repo / name).read_bytes() for name in fixture}
                                with self.assertRaisesRegex(AssertionError, "Unexpected fixture modification"):
                                    context["dependency_profile"](out, repo, setting, "drift-" + label)
                                self.assertEqual({name: (repo / name).read_bytes() for name in fixture}, before)
                                self.assertEqual((repo / ".git/config").read_bytes(), config)
            self.assertEqual(subprocess.check_output([*git, "rev-parse", "HEAD"]), head)
            self.assertEqual((other / ".git/config").read_bytes(), other_config)
        self.assertEqual(original_failures, 8)
        print("PASS: both actual dependency checkout paths obey LF/CRLF in all four inherited profiles; "
              "eight original mismatches reproduce; verified overlays and unrelated-drift refusal remain exact.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
