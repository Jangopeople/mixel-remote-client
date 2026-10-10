#!/usr/bin/env python3
"""Verify pinned clipboard patch; optionally run its actual native X11 listener."""
import argparse
import importlib.util
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = Path(os.environ.get("RDREPO", ROOT / "rustdesk"))
spec = importlib.util.spec_from_file_location("mixel_clipboard_patch", ROOT / "scripts/patch-support-clipboard.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def reject(operation, expected):
    try:
        operation()
    except RuntimeError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError("Conflicting clipboard patch unexpectedly accepted")


DRIVER = r'''
use clipboard_master::{CallbackResult, ClipboardHandler, Master};
use std::{io::{self, Write}, process::Command, time::Duration};
struct Handler {mode:String}
impl ClipboardHandler for Handler {
 fn on_clipboard_change(&mut self)->CallbackResult {
  let output=Command::new("xclip").args(["-selection","clipboard","-out"]).output().unwrap();
  println!("OBSERVED {}",String::from_utf8_lossy(&output.stdout));io::stdout().flush().unwrap();
  match self.mode.as_str() {
   "stop"=>CallbackResult::Stop,
   "fatal-handler"=>CallbackResult::StopWithError(io::Error::new(io::ErrorKind::Other,"synthetic callback failure")),
   _=>CallbackResult::Next,
  }
 }
}
fn main() {
 let mode=std::env::args().nth(1).unwrap_or_default();
 let mut listener=Master::new(Handler {mode:mode.clone()}).unwrap();
 if mode.starts_with("shutdown") {
  let shutdown=listener.shutdown_channel();
  std::thread::spawn(move || {
   std::thread::sleep(Duration::from_millis(if mode=="shutdown-active" {1200} else {750}));
   shutdown.signal();
  });
 }
 println!("STARTING");io::stdout().flush().unwrap();
 match listener.run_x11() {
  Ok(())=>println!("DONE"),
  Err(error)=>{println!("ERROR {error}");std::process::exit(3);}
 }
}
'''


class Lines:
    def __init__(self, process):
        self.process = process
        self.buffer = b""

    def line(self, timeout):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                return None
            data = os.read(self.process.stdout.fileno(), 4096)
            if not data:
                return None
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line.decode()

    def observed(self, expected, timeout=3):
        deadline = time.monotonic() + timeout
        output = []
        while time.monotonic() < deadline:
            line = self.line(deadline - time.monotonic())
            if line is None:
                break
            output.append(line)
            if line == "OBSERVED " + expected:
                return
        raise AssertionError(f"Actual X11 listener missed {expected!r}: {output}")


def native_x11(repo, lock):
    if not os.name == "posix" or not Path("/proc").is_dir():
        raise RuntimeError("Actual X11 listener proof requires Linux")
    for tool in ("cargo", "Xvfb", "xclip"):
        if not shutil.which(tool):
            raise RuntimeError("Actual X11 proof requires " + tool)
    project = repo / "native-x11"
    (project / "src").mkdir(parents=True)
    # Keep the exact patched package inside the standalone test workspace;
    # otherwise Cargo tries to load the intentionally minimal RustDesk fixture.
    package = project / "dependency"
    shutil.copytree(repo / patcher.DEPENDENCY, package, ignore=shutil.ignore_patterns(".git"))
    dependency = package.as_posix()
    (project / "Cargo.toml").write_text(
        '[package]\nname="mixel-clipboard-proof"\nversion="0.1.0"\nedition="2021"\n'
        + '[workspace]\n[dependencies]\nclipboard-master={path="' + dependency + '"}\n'
    )
    (project / "Cargo.lock").write_text(lock)
    (project / "src/main.rs").write_text(DRIVER)
    subprocess.run(["cargo", "build", "--manifest-path", str(project / "Cargo.toml")], check=True)
    binary = project / "target/debug/mixel-clipboard-proof"
    read_fd, write_fd = os.pipe()
    xvfb = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1000x700x24", "-ac", "-nolisten", "tcp"],
                            pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    processes = []
    try:
        ready, _, _ = select.select([read_fd], [], [], 10)
        assert ready, "Own Xvfb failed to start"
        with os.fdopen(read_fd) as pipe:
            display = ":" + pipe.readline().strip()
        environment = {**os.environ, "DISPLAY": display}

        def put(text, selection="clipboard"):
            subprocess.run(["xclip", "-selection", selection, "-in"], input=text.encode(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment, check=True, timeout=3)

        def start(mode="listen", env=None):
            process = subprocess.Popen([str(binary), mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=env or environment, bufsize=0)
            processes.append(process)
            lines = Lines(process)
            assert lines.line(3) == "STARTING"
            time.sleep(.6)  # Initial subscription readiness, never delay rapid changes.
            return process, lines

        process, lines = start()
        put("mixel-primary-does-not-trigger", "primary")
        assert lines.line(.8) is None, "PRIMARY falsely triggered CLIPBOARD callback"
        for iteration in range(5):
            first, second = f"mixel-first-{iteration}", f"mixel-second-{iteration}"
            put(first)
            lines.observed(first)
            # A real new owner arrives immediately after the previous callback,
            # during the listener's500ms sleep. The old pinned code loses it.
            put(second)
            assert subprocess.check_output(["xclip", "-selection", "clipboard", "-out"], env=environment, timeout=3).decode() == second
            lines.observed(second)
        process.terminate()
        process.wait(timeout=3)
        print("PASS: actual native X11 listener delivers five rapid real ownership changes and ignores PRIMARY")

        for mode, expected in (("stop", 0), ("fatal-handler", 3)):
            process, lines = start(mode)
            put("mixel-" + mode)
            lines.observed("mixel-" + mode)
            assert process.wait(timeout=3) == expected
            result = lines.line(1)
            assert result == "DONE" if expected == 0 else result == "ERROR synthetic callback failure"
        print("PASS: actual native callback Stop and fatal error terminate with their correct results")

        for mode in ("shutdown-idle", "shutdown-active"):
            process, lines = start(mode)
            if mode == "shutdown-active":
                put("mixel-active-before-shutdown")
                lines.observed("mixel-active-before-shutdown")
            assert process.wait(timeout=3) == 0
            assert lines.line(1) == "DONE"
        print("PASS: actual native shutdown channel stops idle and active listeners promptly")

        process, lines = start(env={**environment, "DISPLAY": ":499"})
        assert process.wait(timeout=3) == 3
        assert "Failed to initialize clipboard" in (lines.line(1) or "")
        print("PASS: actual unavailable X server fails with its original initialization error")

        process, lines = start()
        xvfb.terminate()
        xvfb.wait(timeout=3)
        assert process.wait(timeout=3) == 3
        assert (lines.line(1) or "").startswith("ERROR ")
        print("PASS: actual X server connection loss remains a terminal error")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        if xvfb.poll() is None:
            xvfb.terminate()
            xvfb.wait(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-x11", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mixel-clipboard-tests-") as temporary:
        repo = Path(temporary)
        for relative in ("Cargo.toml", "Cargo.lock"):
            source = subprocess.run(["git", "-C", str(UPSTREAM), "show", "HEAD:" + relative],
                                    check=True, capture_output=True, text=True, encoding="utf-8").stdout
            (repo / relative).write_text(source, encoding="utf-8")
        original_lock = (repo / "Cargo.lock").read_text()
        patcher.apply(repo)
        dependency = repo / patcher.DEPENDENCY
        files = [repo / "Cargo.toml", repo / "Cargo.lock", dependency / patcher.SOURCE]
        before = [file.read_bytes() for file in files]
        patcher.apply(repo)
        assert [file.read_bytes() for file in files] == before
        source_line = f'source = "git+{patcher.UPSTREAM_URL}#{patcher.PIN}"\n'
        assert (repo / "Cargo.lock").read_text() == original_lock.replace(source_line, "", 1)
        assert (repo / "Cargo.toml").read_text().count(patcher.PATCH_TABLE) == 1
        original = subprocess.run(["git", "-C", str(dependency), "show", "HEAD:" + patcher.SOURCE],
                                  check=True, capture_output=True, text=True, encoding="utf-8").stdout
        actual = files[2].read_text()
        assert actual == patcher.patch_x11(original)
        assert "event.selection == selection" in actual and "Event::XfixesSelectionNotify" in actual
        reject(lambda: patcher.patch_x11(original.replace("Failed to load clipboard", "source drift")), "source changed")
        reject(lambda: patcher.patch_lock(original_lock.replace(patcher.PIN, "0" * 40)), "revision changed")
        reject(lambda: patcher.patch_manifest(files[0].read_text().replace(patcher.DEPENDENCY, "wrong-path")), "Conflicting clipboard")
        files[2].write_text(actual.replace("subscribed_sequence.unwrap()", "subscribed_sequence.unwrap_or(0)"))
        reject(lambda: patcher.apply(repo), "unexpected changes")
        files[2].write_text(actual)
        untracked = dependency / "build.rs"
        untracked.write_text('fn main(){panic!("must never execute");}')
        reject(lambda: patcher.apply(repo), "untracked files")
        untracked.unlink()
        print("PASS: pinned clipboard override preserves dependency edges and rejects source/revision/manifest drift; repeated application is byte-identical")
        if args.native_x11:
            native_x11(repo, original_lock)
    print("Result: clipboard source regression checks passed" + (" with actual native X11 lifecycle" if args.native_x11 else ""))


if __name__ == "__main__":
    main()
