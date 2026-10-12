#!/usr/bin/env python3
"""Guard pinned Mac subwindow engine startup and bounded close acknowledgement."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import time

PIN = "b47e8385e5a75d38319ad706a64b0ead3108b093"
TREE = "ee184480a0e519b9f51f7496d3d90674782481d6"
URL = "https://github.com/rustdesk-org/rustdesk_desktop_multi_window.git"
INVENTORY_SHA = "6eb1693729ae5132242209f2f24a5be73a2bbf72931e06d3154798c5b98b5236"
DEPENDENCY = ".mixel-dependencies/desktop_multi_window"
OLD_DEP = b"""  desktop_multi_window:
    git:
      url: https://github.com/rustdesk-org/rustdesk_desktop_multi_window"""
NEW_DEP = b"""  desktop_multi_window:
    # Exact b47e8385; guarded Mac subwindow startup and close acknowledgement.
    path: .mixel-dependencies/desktop_multi_window"""
REPLACEMENTS = {
    "macos/Classes/FlutterWindow.swift": (
        "d1e005aee1857b358fb42f469a84e7436ab73ef1cf90793c7d9e6f9a0d1052cf",
        (
            ("  init(id: Int64, arguments: String) {", "  init?(id: Int64, arguments: String) {"),
            ("    window.contentViewController = flutterViewController\n", ""),
            ("    super.init(window: window, channel: windowChannel)\n", """    // AppKit can emit move/resize events before show() calls viewWillAppear.
    // Register every plugin first, then start before attaching the view/delegate.
    guard flutterViewController.engine.run(withEntrypoint: nil) else {
      flutterViewController.engine.shutDownEngine()
      return nil
    }
    window.contentViewController = flutterViewController

    super.init(window: window, channel: windowChannel)
"""),
        ),
    ),
    "macos/Classes/MultiWindowManager.swift": (
        "8b2fdb864e4a35d6da494c6c4c974e6d5d44af59e492552227c142025b3793d5",
        (
            ("  private var windows: [Int64: BaseFlutterWindow] = [:]", """  private var windows: [Int64: BaseFlutterWindow] = [:]
  private var closingWindows: [Int64: BaseFlutterWindow] = [:]
  private var closeTimeouts: [Int64: DispatchWorkItem] = [:]"""),
            ("  func create(arguments: String) -> Int64 {", "  func create(arguments: String) -> Int64? {"),
            ("    let window = FlutterWindow(id: windowId, arguments: arguments)", """    guard let window = FlutterWindow(id: windowId, arguments: arguments) else {
      return nil
    }"""),
            (r"""  func onClose(windowId: Int64) {
    // notify flutter to close this window gracefully
    guard let window = self.windows[windowId] else {
      return
    }
    let args = NSDictionary()
    debugPrint("invoke onDestroy \(windowId)")
    window.windowChannel.invokeMethod(fromWindowId: 0, method: "onDestroy", arguments: args, result: nil)
    windows.removeValue(forKey: windowId)
  }""", r"""  func onClose(windowId: Int64) {
    // Stop reuse immediately, but keep the engine alive for Dart's async handler.
    guard let window = windows.removeValue(forKey: windowId) else {
      return
    }
    closingWindows[windowId] = window
    let timeout = DispatchWorkItem { [weak self] in
      self?.finishClosingWindow(windowId: windowId, timedOut: true)
    }
    closeTimeouts[windowId] = timeout
    DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: timeout)

    let args = NSDictionary()
    debugPrint("invoke onDestroy \(windowId)")
    window.windowChannel.invokeMethod(fromWindowId: 0, method: "onDestroy", arguments: args) { [weak self] _ in
      // Never shut down inside the engine's platform-message reply callback.
      DispatchQueue.main.async { [weak self] in
        self?.finishClosingWindow(windowId: windowId, timedOut: false)
      }
    }
  }

  private func finishClosingWindow(windowId: Int64, timedOut: Bool) {
    guard closingWindows[windowId] != nil else {
      return
    }
    closeTimeouts.removeValue(forKey: windowId)?.cancel()
    if timedOut {
      debugPrint("Timed out waiting for support-window cleanup acknowledgement.")
    }
    closingWindows.removeValue(forKey: windowId)
  }"""),
        ),
    ),
    "macos/Classes/FlutterMultiWindowPlugin.swift": (
        "4f1819a7be27ba68484e0ba9e8e0e89933f13a93d4651f6e29961350a4cdb7f2",
        (
            ("      let windowId = MultiWindowManager.shared.create(arguments: arguments ?? \"\")", """      guard let windowId = MultiWindowManager.shared.create(arguments: arguments ?? "") else {
        result(FlutterError(code: "window_engine_start_failed",
                            message: "Unable to open the support window. Please try again.", details: nil))
        return
      }"""),
        ),
    ),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_file(name, data):
    digest, replacements = REPLACEMENTS[name]
    original = data
    if sha(data) != digest and any(new and data.count(new.encode()) != 1 for _, new in replacements):
        raise RuntimeError("Incomplete or changed Mac subwindow startup patch: " + name)
    for old, new in reversed(replacements):
        if new and original.count(new.encode()) == 1:
            original = original.replace(new.encode(), old.encode(), 1)
    # The removed assignment has no marker, so reconstruct it only when all
    # other exact replacement markers establish this is the corrected source.
    if name.endswith("FlutterWindow.swift") and data.count(b"  init?(id:") == 1:
        anchor = b"    let flutterViewController = FlutterViewController(project: project)\n"
        original = original.replace(anchor, anchor + replacements[1][0].encode(), 1)
    if sha(original) != digest:
        raise RuntimeError("Pinned Mac subwindow source changed: " + name)
    corrected = original
    for old, new in replacements:
        if corrected.count(old.encode()) != 1:
            raise RuntimeError("Ambiguous Mac subwindow source boundary: " + name)
        corrected = corrected.replace(old.encode(), new.encode(), 1)
    return corrected


def original_file(name, data):
    corrected = patch_file(name, data)
    for old, new in reversed(REPLACEMENTS[name][1]):
        if new:
            corrected = corrected.replace(new.encode(), old.encode(), 1)
    if name.endswith("FlutterWindow.swift"):
        anchor = b"    let flutterViewController = FlutterViewController(project: project)\n"
        corrected = corrected.replace(anchor, anchor + REPLACEMENTS[name][1][1][0].encode(), 1)
    return corrected


def patch_dependency(data):
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    old, new = (block.replace(b"\n", newline) for block in (OLD_DEP, NEW_DEP))
    original = data.replace(new, old, 1) if data.count(new) == 1 else data
    if original.count(old) != 1 or original.count(b"  desktop_multi_window:") != 1:
        raise RuntimeError("Pinned desktop multi-window dependency changed")
    return original.replace(old, new, 1)


def inventory(files):
    return sha(json.dumps({name: sha(data) for name, data in files.items()},
                          sort_keys=True, separators=(",", ":")).encode())


def source_files(archive):
    if len(archive) > 2_000_000:
        raise RuntimeError("Pinned subwindow source archive exceeds bound")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for entry in stream.getmembers():
            name = entry.name
            path = PurePosixPath(name)
            if "\0" in name or path.is_absolute() or any(p in (".", "..", "") for p in path.parts) or str(path) != name.rstrip("/"):
                raise RuntimeError("Noncanonical pinned subwindow member")
            if entry.isdir():
                continue
            if not entry.isfile() or entry.size > 1_000_000 or name in files:
                raise RuntimeError("Unexpected pinned subwindow member")
            files[name] = stream.extractfile(entry).read()
    if len(files) != 124 or inventory(files) != INVENTORY_SHA:
        raise RuntimeError("Complete pinned subwindow inventory changed")
    return files


def fetch_source():
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory(prefix="mixel-window-fetch-") as directory:
                subprocess.run(["git", "init", "-q", directory], check=True, capture_output=True, timeout=30)
                subprocess.run(["git", "-C", directory, "fetch", "-q", "--depth=1", URL, PIN],
                               check=True, capture_output=True, timeout=60)
                head = subprocess.check_output(["git", "-C", directory, "rev-parse", "FETCH_HEAD"], timeout=30).decode().strip()
                tree = subprocess.check_output(["git", "-C", directory, "rev-parse", "FETCH_HEAD^{tree}"], timeout=30).decode().strip()
                if head != PIN or tree != TREE:
                    raise RuntimeError("Fetched subwindow identity changed")
                return source_files(subprocess.check_output([
                    "git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-C", directory,
                    "archive", "--format=tar", PIN], timeout=30))
        except (subprocess.SubprocessError, OSError):
            if attempt == 2:
                raise RuntimeError("Pinned subwindow fetch failed after 3 attempts") from None
            time.sleep(2 * (attempt + 1))


def regular_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1_000_000:
        raise RuntimeError("Nonregular owned subwindow source")
    return path.read_bytes()


def existing_files(target):
    if target.is_symlink() or not target.is_dir():
        raise RuntimeError("Owned subwindow path is not a regular directory")
    files = {}
    for path in target.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Symlink in owned subwindow source")
        if path.is_dir():
            continue
        files[path.relative_to(target).as_posix()] = regular_file(path)
    try:
        proof = json.loads(files.pop(".mixel-source.json"))
    except (KeyError, ValueError):
        raise RuntimeError("Missing owned subwindow provenance") from None
    original = dict(files)
    for name in REPLACEMENTS:
        if name not in files or patch_file(name, files[name]) != files[name]:
            raise RuntimeError("Owned subwindow startup patch missing")
        original[name] = original_file(name, files[name])
    expected = {"commit": PIN, "tree": TREE, "original_inventory_sha256": INVENTORY_SHA}
    if proof != expected or len(files) != 124 or inventory(original) != INVENTORY_SHA:
        raise RuntimeError("Owned subwindow source or provenance changed")
    expected_dirs = {p.as_posix() for name in files for p in PurePosixPath(name).parents if str(p) != "."}
    actual_dirs = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_dir()}
    if actual_dirs != expected_dirs:
        raise RuntimeError("Unexpected owned subwindow directory")
    return files


def apply(repo):
    cargo = regular_file(repo / "Cargo.toml").decode()
    package = re.search(r"(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)", cargo)
    if not package or re.findall(r'^version\s*=\s*"([^"]+)"\s*$', package[1], re.M) != ["1.4.6"]:
        raise RuntimeError("Mac window startup correction requires upstream 1.4.6")
    pubspec = repo / "flutter/pubspec.yaml"
    corrected = patch_dependency(regular_file(pubspec))
    parent = repo / "flutter/.mixel-dependencies"
    for path in (repo / "flutter", parent):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError("Owned subwindow parent is not a regular directory")
    target = repo / "flutter" / DEPENDENCY
    if target.exists() or target.is_symlink():
        existing_files(target)
    else:
        files = fetch_source()
        for name in REPLACEMENTS:
            files[name] = patch_file(name, files[name])
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".mixel-window-stage-", dir=parent) as directory:
            stage = Path(directory) / "desktop_multi_window"
            stage.mkdir()
            for name, data in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            proof = {"commit": PIN, "tree": TREE, "original_inventory_sha256": INVENTORY_SHA}
            (stage / ".mixel-source.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
            existing_files(stage)
            if target.exists() or target.is_symlink():
                raise RuntimeError("Owned subwindow destination appeared during preparation")
            stage.rename(target)
    pubspec.write_bytes(corrected)
    print("   patched pinned Mac subwindow lifecycle: plugins → engine → native view/delegate; startup failure returns FlutterError; close retains engine until Dart reply or 5s fallback; shared cache untouched")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
