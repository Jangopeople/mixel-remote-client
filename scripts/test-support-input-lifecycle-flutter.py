#!/usr/bin/env python3
"""Execute full production InputModel in an isolated, pinned Flutter test context."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urljoin, urlparse, unquote

PLATFORM_SHA = "c04b40555ed6fa5d55d9f3674db41c69e1f98196bcc81b8cd461160ef07eaca9"
CONTEXT_LOCK_SHA = "add5f66c5b4db8dc84f64429cb9e09e056b8ee3513e6abd73230ecb9b0e0712a"
COMMON_SHA = "8180d31aad9f5464239f049291607456424dbbc14e62b9153f2e2258ec6fc6ff"
OLD_TRANSLATION = "  return platformFFI.translate(name, localeName);"
TEST_TRANSLATION = ("  // Fixture-only native translation boundary; production RelativeMouseModel unchanged.\n"
                    "  return bind.translate(name: name, locale: localeName);")
OLD_BRIDGE = "RustdeskImpl get bind => platformFFI.ffiBind;"
TEST_BRIDGE = ("// Test fixture only: replace the native bridge boundary, not InputModel.\n"
               "RustdeskImpl? inputModelTestBinding;\n"
               "RustdeskImpl get bind => inputModelTestBinding ?? platformFFI.ffiBind;")
CONTEXT_PUBSPEC = """name: mixel_input_regression_context
publish_to: none
environment:
  sdk: '>=3.6.2 <3.7.0'
dependencies:
  flutter_test:
    sdk: flutter
  test: 1.25.8
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    text = data.decode("utf-8")
    value = text.replace("\r\n", "\n")
    if "\r" in value or ("\r\n" in text and "\n" in text.replace("\r\n", "")):
        raise ValueError("Mixed or unsupported newlines")
    return value


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected regular file: {path}")
    return path.read_bytes()


def package_root(config_path, entry):
    uri = urlparse(urljoin(config_path.as_uri(), entry["rootUri"]))
    if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
        raise ValueError("Only local resolved package roots are supported")
    path = Path(unquote(uri.path)).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Resolved package root is not a directory")
    return path


def inventory(root):
    result = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(x in (".dart_tool", "build", ".git") for x in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Production source symlink refused: {rel}")
        if path.is_file():
            result[str(rel)] = sha(regular(path))
    return result


def run(args):
    if sys.platform != "darwin":
        raise ValueError("These production Mac/source2 tests require a Mac host")
    flutter = Path(args.flutter).resolve(strict=True)
    source_root = Path(args.source).resolve(strict=True)
    source = source_root / "flutter"
    fixture = Path(args.fixture).resolve(strict=True)
    relative_fixture = Path(args.relative_fixture).resolve(strict=True)
    relative_test = regular(relative_fixture)
    if sha(relative_test) != args.expected_relative_fixture:
        raise ValueError("Exact relative lifecycle fixture hash differs")
    if relative_fixture.name == "source2_pointer_modifiers_test.dart":
        raise ValueError("Regression fixture names must be distinct")
    relative_model = regular(source / "lib/models/relative_mouse_model.dart")
    if sha(canonical(relative_model).encode()) != args.expected_relative_model:
        raise ValueError("Complete production RelativeMouseModel hash differs")
    common = regular(source / "lib/common.dart")
    if sha(canonical(common).encode()) != COMMON_SHA:
        raise ValueError("Complete production translation boundary hash differs")
    if not 1 <= args.expected_count <= 10000:
        raise ValueError("Expected test count must be a positive bounded integer")
    context_lock = regular(Path(args.context_lock))
    if sha(context_lock) != CONTEXT_LOCK_SHA:
        raise ValueError("Exact pinned regression dependency lock required")
    requested_output = Path(args.output).absolute()
    if requested_output.is_symlink():
        raise ValueError("Output symlink refused")
    output = requested_output.resolve()
    # All prerequisites are checked before the owned evidence directory is created.
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a fresh owned directory")
    protected = (source_root, flutter.parent.parent, fixture, relative_fixture,
                 Path(args.context_lock).resolve(strict=True))
    if any(output.is_relative_to(path) for path in protected):
        raise ValueError("Output must be separate from source, SDK and fixture inputs")
    model = regular(source / "lib/models/input_model.dart")
    if sha(canonical(model).encode()) != args.expected_input:
        raise ValueError("Complete production InputModel hash differs")
    platform = regular(source / "lib/models/platform_model.dart")
    if sha(canonical(platform).encode()) != PLATFORM_SHA:
        raise ValueError("Complete production platform bridge hash differs")
    test = regular(fixture)
    if sha(test) != args.expected_fixture:
        raise ValueError("Exact regression fixture hash differs")
    config_path = source / ".dart_tool/package_config.json"
    config_bytes = regular(config_path)
    config = json.loads(config_bytes)
    source_roots = {x["name"]: package_root(config_path, x) for x in config["packages"]}
    if source_roots.get("flutter_hbb") != source:
        raise ValueError("Production package identity differs")
    production = inventory(source)
    if production["lib/models/input_model.dart"] != sha(model):
        raise ValueError("Production InputModel changed before source inventory")
    if production["lib/models/platform_model.dart"] != sha(platform):
        raise ValueError("Production bridge changed before source inventory")
    if production["lib/common.dart"] != sha(common) or production["lib/models/relative_mouse_model.dart"] != sha(relative_model):
        raise ValueError("Production relative model or translation changed before inventory")
    fixture_rel = "test/source2_pointer_modifiers_test.dart"
    relative_fixture_rel = "test/" + relative_fixture.name
    if fixture_rel in production or relative_fixture_rel in production:
        raise ValueError("The isolated fixture must not overwrite production tests")
    output.mkdir(parents=True)
    env = dict(os.environ, CI="true", FLUTTER_SUPPRESS_ANALYTICS="true",
               PUB_CACHE=str(output / "pub-cache"))
    commands = []

    def execute(name, command, cwd, timeout):
        started = time.monotonic()
        try:
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            # subprocess.run has already killed and waited for its timed-out child.
            # Its exception retains the exact partial byte streams read so far.
            stdout, stderr = error.stdout or b"", error.stderr or b""
            (output / f"{name}.stdout.log").write_bytes(stdout)
            (output / f"{name}.stderr.log").write_bytes(stderr)
            commands.append({"name": name, "command": command, "cwd": str(cwd),
                             "exitCode": None, "seconds": time.monotonic() - started,
                             "timedOut": True, "timeoutSeconds": timeout,
                             "stdoutBytes": len(stdout), "stderrBytes": len(stderr)})
            (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
            raise RuntimeError(f"{name} timed out after {timeout}s; see retained logs") from error
        (output / f"{name}.stdout.log").write_bytes(result.stdout)
        (output / f"{name}.stderr.log").write_bytes(result.stderr)
        commands.append({"name": name, "command": command, "cwd": str(cwd),
                         "exitCode": result.returncode, "seconds": time.monotonic() - started})
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode:
            raise RuntimeError(f"{name} failed with exit {result.returncode}; see retained logs")
        return result

    sdk = json.loads(execute("sdk", [str(flutter), "--version", "--machine"], output, 120).stdout)
    if sdk["frameworkVersion"] != "3.27.4" or sdk["dartSdkVersion"] != "3.6.2":
        raise ValueError("Exact Flutter3.27.4/Dart3.6.2 SDK required")
    context = output / "test-context"
    context.mkdir()
    (context / "pubspec.yaml").write_text(CONTEXT_PUBSPEC)
    (context / "pubspec.lock").write_bytes(context_lock)
    execute("test-context", [str(flutter), "pub", "get", "--enforce-lockfile"], context, 180)
    if regular(context / "pubspec.lock") != context_lock:
        raise ValueError("Regression dependency resolution changed its pinned lock")
    context_config = context / ".dart_tool/package_config.json"
    test_config = json.loads(regular(context_config))
    test_roots = {x["name"]: package_root(context_config, x) for x in test_config["packages"]}
    if test_roots["flutter"] != source_roots["flutter"]:
        raise ValueError("Production and regression Flutter SDK roots differ")
    app = output / "app"
    shutil.copytree(source, app, ignore=shutil.ignore_patterns(".dart_tool", "build", ".git"))
    if inventory(app) != production:
        raise ValueError("Source copy changed production paths or bytes")
    if sha(canonical(regular(app / "lib/models/input_model.dart")).encode()) != args.expected_input:
        raise ValueError("Actual scratch InputModel differs from reviewed pin")
    target_platform = app / "lib/models/platform_model.dart"
    text = canonical(platform)
    if text.count(OLD_BRIDGE) != 1:
        raise ValueError("Exact isolated bridge insertion differs")
    newline = "\r\n" if b"\r\n" in platform else "\n"
    patched_platform = text.replace(OLD_BRIDGE, TEST_BRIDGE).replace("\n", newline).encode()
    target_platform.write_bytes(patched_platform)
    common_text = canonical(common)
    if common_text.count(OLD_TRANSLATION) != 1:
        raise ValueError("Exact isolated translation insertion differs")
    common_newline = "\r\n" if b"\r\n" in common else "\n"
    patched_common = common_text.replace(OLD_TRANSLATION, TEST_TRANSLATION).replace("\n", common_newline).encode()
    (app / "lib/common.dart").write_bytes(patched_common)
    (app / "test").mkdir(exist_ok=True)
    (app / "test/source2_pointer_modifiers_test.dart").write_bytes(test)
    (app / relative_fixture_rel).write_bytes(relative_test)
    merged = {}
    for item in config["packages"]:
        value = dict(item)
        root = source_roots[item["name"]]
        if root.is_relative_to(source):
            root = app / root.relative_to(source)
        value["rootUri"] = root.as_uri() + "/"
        merged[item["name"]] = value
    for item in test_config["packages"]:
        if item["name"] == "mixel_input_regression_context":
            continue
        value = dict(item, rootUri=test_roots[item["name"]].as_uri() + "/")
        merged[item["name"]] = value
    (app / ".dart_tool").mkdir()
    test_config["packages"] = list(merged.values())
    (app / ".dart_tool/package_config.json").write_text(json.dumps(test_config, indent=2) + "\n")
    expected_scratch = dict(production)
    expected_scratch["lib/models/platform_model.dart"] = sha(patched_platform)
    expected_scratch["lib/common.dart"] = sha(patched_common)
    expected_scratch[fixture_rel] = sha(test)
    expected_scratch[relative_fixture_rel] = sha(relative_test)
    if inventory(app) != expected_scratch or inventory(source) != production:
        raise ValueError("Production or exact isolated source changed before tests")
    if regular(config_path) != config_bytes or regular(fixture) != test or regular(relative_fixture) != relative_test:
        raise ValueError("Resolved production context or fixture changed before tests")
    result = execute("actual-flutter-test", [str(flutter), "test", "--no-pub", "--reporter", "expanded",
                     fixture_rel, relative_fixture_rel], app, 180)
    if b"All tests passed!" not in result.stdout:
        raise ValueError("Actual Flutter test success marker absent")
    last = result.stdout.decode().splitlines()[-1]
    if not re.fullmatch(r"[0-9:]+ \+" + str(args.expected_count) + r": All tests passed!", last):
        raise ValueError("Exact complete test count differs or tests were skipped")
    if inventory(source) != production or regular(config_path) != config_bytes:
        raise ValueError("Original production source or context changed during regression")
    if inventory(app) != expected_scratch or regular(fixture) != test or regular(relative_fixture) != relative_test:
        raise ValueError("Exact isolated source or fixture changed during regression")
    receipt = {"result": "PASS_ACTUAL_FULL_PRODUCTION_INPUTMODEL_FLUTTER_TEST",
               "inputSha256": args.expected_input, "fixtureSha256": args.expected_fixture,
               "relativeModelSha256": args.expected_relative_model,
               "relativeFixtureSha256": args.expected_relative_fixture,
               "passedTestCount": args.expected_count,
               "sourceFilesUnchanged": len(production), "productionPubspecAndLockUnchanged": True,
               "fixtureOnlyProductionSeams": ["lib/models/platform_model.dart", "lib/common.dart"],
               "contextPubspec": CONTEXT_PUBSPEC, "contextLockSha256": sha(regular(context / "pubspec.lock")),
               "sourcePackages": {k: str(v) for k, v in source_roots.items()},
               "testPackages": {k: str(v) for k, v in test_roots.items()},
               "commands": commands, "nativeApplicationQualified": False}
    (output / "RESULT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS_ACTUAL_FULL_PRODUCTION_INPUTMODEL_FLUTTER_TEST")
    print(result.stdout.decode().splitlines()[-1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "flutter", "fixture", "expected-input", "expected-fixture", "context-lock", "output"):
        parser.add_argument("--" + name, required=True)
    for name in ("relative-fixture", "expected-relative-fixture", "expected-relative-model"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    run(parser.parse_args())
