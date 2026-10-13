#!/usr/bin/env python3
"""Exercise the packaged Mac tray-to-customer URL route on an isolated CI Mac.

This starts the exact app as --server, sends a synthetic support URL through
LaunchServices, and requires a distinct visible GUI plus guarded incoming IPC.
It never connects to a remote desktop or grants customer/OS permissions.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("mixel_mac_desktop", SCRIPTS / "test-support-launch-macos.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
unix = desktop.unix
URL_SOCKET = Path(str(unix.IPC_PATH) + "_url")
TRAY_MARKER = b"Mixel support URL tray route: dispatch"
MAX_LOG_BYTES = 4 * 1024 * 1024
CRITICAL = (
    "Contents/MacOS/Mixel-Remote", "Contents/MacOS/service",
    "Contents/Frameworks/liblibrustdesk.dylib",
    "Contents/Frameworks/FlutterMacOS.framework/FlutterMacOS",
)


def tray_swift(source):
    changes = (
        ('if action == "launch" || action == "ordinary" {',
         'if action == "launch" || action == "ordinary" || action == "tray" {'),
        ('if (action == "ordinary" || arguments[4] == "cold")',
         'if (action == "ordinary" || action == "tray" || arguments[4] == "cold")'),
        ('    configuration.addsToRecentItems = false',
         '    configuration.addsToRecentItems = false\n    if action == "tray" { configuration.arguments = ["--server"] }'),
        ('    if action == "ordinary" {\n        NSWorkspace.shared.openApplication',
         '    if action == "ordinary" || action == "tray" {\n        NSWorkspace.shared.openApplication'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise RuntimeError("Pinned Mac launch helper changed; refusing tray fixture")
        source = source.replace(before, after, 1)
    return source


def isolated_ci():
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "macOS":
        raise RuntimeError("Packaged tray verification requires an isolated GitHub macOS runner")


def process_path(pid):
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    library.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    library.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    if type(pid) is not int or pid <= 0 or library.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        raise RuntimeError("Fixture process identity is unavailable")
    return Path(os.fsdecode(buffer.value))


def peer_pid(connection):
    pid = struct.unpack("i", connection.getsockopt(0, 0x002, 4))[0]
    if pid <= 0:
        raise RuntimeError("Fixture IPC has no kernel process owner")
    return pid


def query(pid, app, kind, content=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(unix.IPC_PATH))
        if peer_pid(connection) != pid or process_path(pid) != app / "Contents/MacOS/Mixel-Remote":
            raise RuntimeError("Fixture incoming IPC belongs to another process")
        connection.sendall(unix.frame(json.dumps({"t": kind, "c": content}).encode()))
        response = json.loads(unix.read_frame(connection))
    if not isinstance(response, dict) or response.get("t") != kind or "c" not in response:
        raise RuntimeError("Fixture IPC returned a different response")
    return response["c"]


def endpoint_inactive(endpoint):
    if not os.path.lexists(endpoint):
        return
    attributes = endpoint.lstat()
    if not stat.S_ISSOCK(attributes.st_mode) or attributes.st_uid != os.getuid():
        raise RuntimeError("Pre-existing IPC path is not this runner's socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        try:
            connection.connect(str(endpoint))
        except (FileNotFoundError, ConnectionRefusedError):
            return
    raise RuntimeError("A Mixel IPC endpoint is already active; refusing tray fixture")


def log_snapshot(root):
    return {path: path.lstat() for path in desktop.scoped_files(root, recursive=True)}


def new_tray_marker(root, baseline):
    for path in desktop.scoped_files(root, recursive=True):
        if path.suffix != ".log":
            continue
        current = path.lstat()
        previous = baseline.get(path)
        if previous is not None and desktop.file_identity(previous) == desktop.file_identity(current):
            continue
        data, opened = desktop.bounded_read(path, MAX_LOG_BYTES)
        offset = previous.st_size if previous is not None and (previous.st_dev, previous.st_ino) == (opened.st_dev, opened.st_ino) and previous.st_size <= len(data) else 0
        fresh = data[offset:]
        if desktop.privacy_issue(fresh):
            raise RuntimeError("Tray fixture log contains invitation credentials")
        if TRAY_MARKER in fresh:
            return True
    return False


def wait(description, operation, server_pid, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        os.kill(server_pid, 0)
        try:
            result = operation()
            if result:
                return result
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout):
            pass
        time.sleep(0.1)
    raise RuntimeError(description + " did not become ready")


def health(server_pid, app, attended):
    if not no_connections(server_pid, app):
        raise RuntimeError("Tray fixture unexpectedly has an authenticated connection")
    proof = query(server_pid, app, "Config", ["mixel-support-invite-attended", None])
    expected = "attended-runtime-v2" if attended else ""
    if proof != ["mixel-support-invite-attended", expected]:
        return False
    online = query(server_pid, app, "OnlineStatus")
    if not isinstance(online, list) or len(online) != 2 or type(online[0]) is not int or type(online[1]) is not bool:
        raise RuntimeError("Tray fixture online response is invalid")
    options = query(server_pid, app, "Options")
    expected_options = {"custom-rendezvous-server": "rs.mixel.ch", "relay-server": "rs.mixel.ch", "key": unix.PUBLIC_KEY}
    if not isinstance(options, dict) or any(options.get(key) != value for key, value in expected_options.items()) or "mixel-support-invite-attended" in options:
        raise RuntimeError("Tray fixture relay or runtime-only consent configuration is invalid")
    return online[0] > 0 and online[1]


def gui_owner(app, server_pid):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(str(URL_SOCKET))
        pid = peer_pid(connection)
    if pid == server_pid or process_path(pid) != app / "Contents/MacOS/Mixel-Remote":
        raise RuntimeError("Tray route did not create a distinct exact customer GUI")
    return pid


def no_connections(server_pid, app):
    count = query(server_pid, app, "VideoConnCount")
    if type(count) is not int or count < 0:
        raise RuntimeError("Tray fixture connection count is invalid")
    return count == 0


def launch_identity(helper, app, pid):
    state = desktop.probe(helper, pid, app)
    value = state.get("launchTime")
    if not state.get("running") or type(value) not in (int, float) or not 0 < value < time.time() + 5:
        raise RuntimeError("Tray fixture process has no retained launch identity")
    return value


def terminate_owned(helper, app, pid, launch_time, server_pid):
    def authorize():
        if process_path(pid) != app / "Contents/MacOS/Mixel-Remote" or launch_identity(helper, app, pid) != launch_time:
            raise RuntimeError("Tray fixture cleanup process identity changed")
        if not no_connections(server_pid, app):
            raise RuntimeError("Tray fixture cleanup refused an authenticated connection")
    authorize()
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if not desktop.helper_request(helper, "probe", str(pid)).get("running"):
            return
        time.sleep(0.1)
    authorize()
    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if not desktop.helper_request(helper, "probe", str(pid)).get("running"):
            return
        time.sleep(0.1)
    raise RuntimeError("Owned tray fixture did not exit after termination")


def cleanup_endpoint(endpoint, pid):
    endpoint_inactive(endpoint)
    socket_identity = desktop.file_identity(endpoint.lstat()) if os.path.lexists(endpoint) else None
    pid_file = endpoint.with_suffix(".pid")
    if pid is None or not os.path.lexists(pid_file):
        return
    data, attributes = desktop.bounded_read(pid_file, 64)
    if data.decode().strip() != str(pid):
        return
    if desktop.file_identity(pid_file.lstat()) != desktop.file_identity(attributes):
        raise RuntimeError("Tray fixture cleanup process identity changed")
    current_socket = desktop.file_identity(endpoint.lstat()) if os.path.lexists(endpoint) else None
    if current_socket != socket_identity:
        raise RuntimeError("Tray fixture cleanup socket identity changed")
    endpoint_inactive(endpoint)
    current_socket = desktop.file_identity(endpoint.lstat()) if os.path.lexists(endpoint) else None
    if current_socket != socket_identity:
        raise RuntimeError("Tray fixture cleanup socket identity changed")
    endpoint.unlink(missing_ok=True)
    pid_file.unlink()


def runtime(app, helper, facts):
    isolated_ci()
    endpoint_inactive(unix.IPC_PATH)
    endpoint_inactive(URL_SOCKET)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "ch.mixel.remote" or info.get("CFBundleExecutable") != "Mixel-Remote":
        raise RuntimeError("Tray fixture bundle identity differs from Mixel Remote")
    facts["criticalHashes"] = {name: hashlib.sha256((app / name).read_bytes()).hexdigest() for name in CRITICAL}
    root = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library/Logs/Mixel-Remote"
    baseline = log_snapshot(root)
    server_pid = gui_pid = None
    server_launch = gui_launch = None
    phase = "tray-launch"
    try:
        launched = desktop.helper_request(helper, "tray", str(app))
        phase = "tray-launch-identity"
        server_pid = launched.get("pid")
        if type(server_pid) is not int or server_pid <= 0 or process_path(server_pid) != app / "Contents/MacOS/Mixel-Remote":
            raise RuntimeError("Tray launch returned a different executable")
        facts["trayPid"] = server_pid
        server_launch = launch_identity(helper, app, server_pid)
        if launched.get("launchTime") != server_launch:
            raise RuntimeError("Tray launch identity changed before verification")
        phase = "unarmed-tray-readiness"
        wait("Unarmed tray incoming service", lambda: health(server_pid, app, False), server_pid)
        print("PASS: exact packaged Mac tray starts with kernel-owned incoming service, confirmed relay and no attended guard or remote session", flush=True)
        phase = "os-url-activation"
        activated = desktop.helper_request(helper, "launch", str(app), unix.URI, "warm")
        if activated.get("pid") != server_pid or activated.get("launchTime") != server_launch or launch_identity(helper, app, server_pid) != server_launch:
            raise RuntimeError("OS URL activation bypassed the existing tray process")
        phase = "tray-opened-dispatch"
        wait("Actual Tao tray Opened dispatch", lambda: new_tray_marker(root, baseline), server_pid)
        facts["actualTrayDispatchObserved"] = True
        phase = "customer-gui-owner"
        gui_pid = wait("Distinct customer GUI", lambda: gui_owner(app, server_pid), server_pid)
        facts["guiPid"] = gui_pid
        phase = "customer-gui-launch-identity"
        gui_launch = launch_identity(helper, app, gui_pid)
        phase = "customer-gui-visible"
        wait("Visible customer GUI", lambda: desktop.probe(helper, gui_pid, app).get("visible"), server_pid)
        phase = "attended-service-readiness"
        wait("Tray service attended readiness", lambda: health(server_pid, app, True), server_pid)
        facts["visibleGuiAndAttendedV2"] = True
        print("PASS: actual OS URL reaches the packaged Tao tray, opens a distinct visible customer GUI and confirms attended-runtime-v2 on the original incoming service", flush=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        facts["runtimeFailure"] = {"phase": phase, "reason": desktop.safe_error(error)}
        print("FAIL: tray runtime (" + phase + "): " + facts["runtimeFailure"]["reason"],
              file=sys.stderr, flush=True)
        raise
    finally:
        if server_pid is not None:
            # Refuse all process cleanup when this disposable fixture has a
            # connection, a replaced incoming owner or a different binary.
            if not no_connections(server_pid, app):
                raise RuntimeError("Tray fixture cleanup refused an authenticated connection")
            if gui_pid is None:
                try:
                    gui_pid = gui_owner(app, server_pid)
                    gui_launch = launch_identity(helper, app, gui_pid)
                except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
                    pass
            if gui_pid is not None:
                terminate_owned(helper, app, gui_pid, gui_launch, server_pid)
            terminate_owned(helper, app, server_pid, server_launch, server_pid)
            facts["ownedProcessesStopped"] = True
            for endpoint, pid in ((URL_SOCKET, gui_pid), (unix.IPC_PATH, server_pid)):
                cleanup_endpoint(endpoint, pid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    args = parser.parse_args()
    facts = {"observedUtc": datetime.now(timezone.utc).isoformat(), "syntheticInvite": True,
             "customerAcceptTested": False, "remoteDesktopConnected": False,
             "actualTrayDispatchObserved": False, "visibleGuiAndAttendedV2": False,
             "ownedProcessesStopped": False}
    result = 1
    output_owned = False
    try:
        isolated_ci()
        app = args.app.resolve(strict=True)
        args.proofs = args.proofs.absolute()
        if args.proofs.parent.resolve() != args.proofs.parent:
            raise RuntimeError("Tray proof parent is not a canonical directory")
        args.proofs.mkdir(parents=False, exist_ok=False)
        output_owned = True
        with tempfile.TemporaryDirectory(prefix="mixel-macos-tray-fixture-") as temporary:
            desktop.SWIFT = tray_swift(desktop.SWIFT)
            helper = desktop.compile_helper(Path(temporary))
            runtime(app, helper, facts)
        facts["passed"] = True
        result = 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        facts["passed"] = False
        facts["failure"] = desktop.safe_error(error)
        print("FAIL: " + facts["failure"], file=sys.stderr)
    finally:
        if output_owned:
            proof = args.proofs / "tray-runtime-safe-facts.json"
            proof.write_text(json.dumps(facts, indent=2) + "\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
