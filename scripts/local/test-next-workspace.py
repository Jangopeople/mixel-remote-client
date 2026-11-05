#!/usr/bin/env python3
"""Build and execute the new Rust workspace locally, retaining exact evidence.

Browser interaction remains a separate CUA test against the generated web bundle.
This driver never starts customer sessions, changes infrastructure or uploads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import selectors
import signal
import urllib.request
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "next"


def inventory():
    files = [Path(__file__).resolve()]
    for directory, children, names in os.walk(WORKSPACE):
        children[:] = sorted(name for name in children if name != "target")
        files.extend(Path(directory) / name for name in sorted(names))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def smoke_binary(output):
    environment = dict(os.environ)
    environment.update(MIXEL_SIGNAL_BIND="127.0.0.1:0", MIXEL_ISM_URL="https://identity-fixture.invalid",
                       MIXEL_ISM_PUBLIC_KEY="public-fixture-not-a-credential", MIXEL_WEB_ORIGINS="https://staging.example")
    # Only health is called: this test cannot authenticate against a live ISM.
    process = subprocess.Popen([str(WORKSPACE / "target/release/signaling_server")], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            if not selector.select(timeout=5):
                raise RuntimeError("release signaling startup timed out")
            line = process.stderr.readline()
        match = re.search(r"listening on (127\.0\.0\.1:\d+)", line)
        if not match:
            raise RuntimeError("release signaling did not bind loopback: " + line)
        with urllib.request.urlopen("http://" + match[1] + "/healthz", timeout=3) as response:
            if response.read(32) != b"ok\n":
                raise RuntimeError("unexpected release health response")
        process.send_signal(signal.SIGINT)
        if process.wait(timeout=5) != 0:
            raise RuntimeError("release signaling did not shut down cleanly")
        environment["MIXEL_SIGNAL_BIND"] = "0.0.0.0:0"
        denied = subprocess.run([str(WORKSPACE / "target/release/signaling_server")], env=environment,
                                capture_output=True, text=True, timeout=5)
        if denied.returncode == 0 or "must bind loopback" not in denied.stderr:
            raise RuntimeError("release binary accepted public staging bind")
        (output / "release-smoke.log").write_text(line + "PASS: health ok; SIGINT clean exit 0; public bind refused\n")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wasm-bindgen", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    scratch = (ROOT / "_scratch").resolve()
    if not output.is_relative_to(scratch) or output == scratch:
        parser.error("Evidence must be a new directory inside this repo's _scratch")
    output.mkdir(parents=True, exist_ok=False)
    before = inventory()
    commands = [
        ["cargo", "fmt", "--all", "--check"],
        ["cargo", "clippy", "--workspace", "--all-features", "--all-targets", "--locked", "--", "-D", "warnings"],
        ["cargo", "clippy", "-p", "web_client", "--target", "wasm32-unknown-unknown", "--locked", "--", "-D", "warnings"],
        ["cargo", "test", "--workspace", "--all-features", "--locked", "--", "--nocapture"],
        ["cargo", "build", "--workspace", "--all-features", "--release", "--locked"],
        ["cargo", "build", "-p", "web_client", "--target", "wasm32-unknown-unknown", "--release", "--locked"],
        [str(args.wasm_bindgen.resolve()), str(WORKSPACE / "target/wasm32-unknown-unknown/release/web_client.wasm"), "--target", "nodejs", "--out-dir", str(output / "node")],
        ["node", str(WORKSPACE / "crates/web_client/tests/wasm-control.cjs"), str(output / "node/web_client.js")],
        [str(args.wasm_bindgen.resolve()), str(WORKSPACE / "target/wasm32-unknown-unknown/release/web_client.wasm"), "--target", "web", "--out-dir", str(output / "web/pkg")],
    ]
    result = {"qualified": False, "browserQualified": False, "customerSessionQualified": False, "commands": []}
    try:
        for index, command in enumerate(commands, 1):
            entry = {"argv": command, "log": f"{index:02}.log"}
            result["commands"].append(entry)
            start = time.monotonic()
            with (output / entry["log"]).open("w") as log:
                completed = subprocess.run(command, cwd=WORKSPACE, stdout=log, stderr=subprocess.STDOUT, check=False)
            entry.update(exitCode=completed.returncode, seconds=round(time.monotonic() - start, 3))
            print(f"{'PASS' if completed.returncode == 0 else 'FAIL'} {index}/{len(commands)} {' '.join(command[:3])}", flush=True)
            if completed.returncode:
                print((output / entry["log"]).read_text(), file=sys.stderr)
                return 1
        shutil.copyfile(WORKSPACE / "frontends/web/tests/browser-loopback.html", output / "web/index.html")
        shutil.copyfile(WORKSPACE / "frontends/web/tests/browser-loopback.js", output / "web/browser-loopback.js")
        smoke_binary(output)
        result["releaseBinaryHealthQualified"] = True
        print("PASS release binary: loopback health, clean SIGINT shutdown and public-bind refusal", flush=True)
        after = inventory()
        result["sourceUnchanged"] = before == after
        result["sourceSha256"] = after
        result["artifacts"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
            WORKSPACE / "target/release/signaling_server", WORKSPACE / "target/wasm32-unknown-unknown/release/web_client.wasm",
            output / "web/pkg/web_client_bg.wasm",
        ]}
        result["qualified"] = result["sourceUnchanged"]
        if not result["qualified"]:
            print("FAIL: source changed during verification", file=sys.stderr)
            return 1
        print("PASS: local workspace build, lint, native network tests and compiled WASM execution; browser/customer qualification is separate")
        return 0
    finally:
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")

if __name__ == "__main__":
    sys.exit(main())
