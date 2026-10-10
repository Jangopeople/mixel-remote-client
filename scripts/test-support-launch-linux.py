#!/usr/bin/env python3
"""Exercise the real Linux support app in an isolated X11/DBus desktop.

Uses synthetic invalid credentials only. IPC reads never arm the attended guard
or write preferences; the tested customer URI must do that through the real UI.
Requires dbus-run-session, xvfb-run, openbox, xdotool, xwininfo and xprop.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


TOKEN = "inv_00000000-0000-0000-0000-000000000002"
API_KEY = "synthetic-invalid-public-key-000000000000"
URI = f"mixel-remote://support/?invite={TOKEN}&apikey={API_KEY}"
PUBLIC_KEY = "OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo="
IPC_PATH = Path("/tmp/Mixel-Remote/ipc")
MAX_IPC_REPLY = 65_536


def frame(body: bytes) -> bytes:
    if len(body) > MAX_IPC_REPLY:
        raise ValueError("IPC request exceeds its bound")
    count = next(size for size in range(1, 5) if len(body) < 1 << (size * 8 - 2))
    return ((len(body) << 2) | (count - 1)).to_bytes(count, "little") + body


def read_exact(connection: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise RuntimeError("Incoming support IPC closed before its response")
        result.extend(chunk)
    return bytes(result)


def read_frame(connection: socket.socket) -> bytes:
    first = read_exact(connection, 1)
    count = (first[0] & 3) + 1
    size = int.from_bytes(first + read_exact(connection, count - 1), "little") >> 2
    if size > MAX_IPC_REPLY:
        raise RuntimeError("Incoming support IPC response exceeds its bound")
    return read_exact(connection, size)


def query(kind: str, content=None, endpoint: Path = IPC_PATH):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(endpoint))
        connection.sendall(frame(json.dumps({"t": kind, "c": content}, separators=(",", ":")).encode("utf-8")))
        response = json.loads(read_frame(connection))
    if not isinstance(response, dict) or response.get("t") != kind or "c" not in response:
        raise RuntimeError(f"Incoming support IPC returned an unexpected {kind} response")
    return response["c"]


def runtime_health(expected_pid: int) -> tuple[int, bool]:
    # Avoid accepting the guard of a different installed or unattended service.
    if int(IPC_PATH.with_suffix(".pid").read_text(encoding="utf-8").strip()) != expected_pid:
        raise RuntimeError("Incoming support IPC belongs to a different process")
    proof = query("Config", ["mixel-support-invite-attended", None])
    if proof != ["mixel-support-invite-attended", "attended-runtime-v2"]:
        raise RuntimeError("Actual incoming support guard did not confirm attended-runtime-v2")
    state = query("OnlineStatus")
    if not isinstance(state, list) or len(state) != 2 or type(state[0]) is not int or type(state[1]) is not bool:
        raise RuntimeError("Incoming support IPC returned an invalid online state")
    options = query("Options")
    if not isinstance(options, dict) or any(options.get(key) != expected for key, expected in {
        "custom-rendezvous-server": "rs.mixel.ch", "relay-server": "rs.mixel.ch", "key": PUBLIC_KEY,
    }.items()):
        raise RuntimeError("Actual incoming support server is not using the branded relay defaults")
    if "mixel-support-invite-attended" in options:
        raise RuntimeError("Attended support guard leaked into saved preferences")
    rendezvous = query("Config", ["rendezvous_server", None])
    if not isinstance(rendezvous, list) or len(rendezvous) != 2 or rendezvous[0] != "rendezvous_server" or not isinstance(rendezvous[1], str):
        raise RuntimeError("Incoming support IPC returned an invalid rendezvous server")
    if rendezvous[1].split(",", 1)[0] != "rs.mixel.ch:21116":
        raise RuntimeError("Actual incoming server rendezvous does not match rs.mixel.ch")
    device = query("Config", ["id", None])
    if not isinstance(device, list) or len(device) != 2 or device[0] != "id" or not isinstance(device[1], str) or not re.fullmatch(r"[a-zA-Z0-9-]{6,32}", device[1]):
        raise RuntimeError("Actual incoming server has no usable support device ID")
    return state[0], state[1]


def command(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=8)
    if check and result.returncode:
        # Never include the synthetic support URL or program output in errors.
        raise RuntimeError(f"Runtime desktop command failed: {Path(arguments[0]).name}")
    return result


def wait_for(description: str, operation, main: subprocess.Popen, timeout: float = 60):
    deadline = time.monotonic() + timeout
    last = "not ready"
    while time.monotonic() < deadline:
        if main.poll() is not None:
            raise RuntimeError(f"{description}: customer app exited with {main.returncode}")
        try:
            value = operation()
            if value:
                return value
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            last = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"{description}: {last}")


def windows(pid: int) -> list[str]:
    found = command(["xdotool", "search", "--pid", str(pid), "--name", "^Mixel-Remote$"], check=False)
    return found.stdout.splitlines() if found.returncode == 0 else []


def visible(window: str) -> bool:
    info = command(["xwininfo", "-id", window], check=False)
    state = command(["xprop", "-id", window, "_NET_WM_STATE"], check=False)
    return "Map State: IsViewable" in info.stdout and "_NET_WM_STATE_HIDDEN" not in state.stdout


def visible_main(pid: int):
    return next((window for window in windows(pid) if visible(window)), None)


def wait_health(main: subprocess.Popen, scenario: str) -> None:
    state, confirmed = wait_for(
        scenario + " incoming server",
        lambda: (result if result[0] > 0 and result[1] else None) if (result := runtime_health(main.pid)) else None,
        main,
    )
    print(f"PASS: {scenario} actual incoming IPC proves attended-runtime-v2, branded relay, registered ID, online state={state}, keyConfirmed={str(confirmed).lower()}", flush=True)


def stop(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def runtime(executable: Path) -> None:
    if IPC_PATH.exists() or IPC_PATH.with_suffix(".pid").exists():
        raise RuntimeError("A Mixel Remote IPC endpoint already exists; use a fresh isolated Linux runner")
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    log_root = home / ".local/share/logs/Mixel-Remote"
    offsets = {path: path.stat().st_size for path in log_root.rglob("*") if path.is_file()} if log_root.exists() else {}
    main = warm = desktop = None
    with tempfile.TemporaryDirectory(prefix="mixel-linux-runtime-") as temporary:
        stdout_path = Path(temporary) / "runtime-output.log"
        with stdout_path.open("wb") as output:
            try:
                desktop = subprocess.Popen(["openbox", "--sm-disable"], stdout=output, stderr=output, start_new_session=True)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    wm = command(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], check=False)
                    if "window id # 0x" in wm.stdout:
                        break
                    if desktop.poll() is not None:
                        raise RuntimeError("Headless test window manager exited")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Headless test window manager did not start")
                main = subprocess.Popen([str(executable), URI], cwd=executable.parent, stdout=output, stderr=output, start_new_session=True)
                wait_for("Cold support URI customer window", lambda: visible_main(main.pid), main)
                # Let startup's asynchronous URI handler finish; the old defect
                # created an initial window and then hid it during this phase.
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    if not visible_main(main.pid):
                        raise RuntimeError("Cold support URI hid the app after initialisation")
                    time.sleep(0.25)
                print("PASS: cold Linux support URI opens a stable visible customer window", flush=True)
                wait_health(main, "Cold support URI launch")
                window = visible_main(main.pid)
                command(["xdotool", "windowminimize", window])
                wait_for("Warm support URI minimisation setup", lambda: not visible(window), main, timeout=10)
                warm = subprocess.Popen([str(executable), URI], cwd=executable.parent, stdout=output, stderr=output, start_new_session=True)
                wait_for("Warm support URI customer window", lambda: visible_main(main.pid), main)
                warm.wait(timeout=15)
                if warm.returncode != 0:
                    raise RuntimeError("Warm support URI sender failed")
                print("PASS: warm Linux support URI restores the same customer window through branded DBus", flush=True)
                wait_health(main, "Warm support URI launch")
            finally:
                stop(warm)
                stop(main)
                stop(desktop)
                if main is not None:
                    try:
                        pid_file = IPC_PATH.with_suffix(".pid")
                        if int(pid_file.read_text(encoding="utf-8").strip()) == main.pid:
                            IPC_PATH.unlink(missing_ok=True)
                            pid_file.unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass

        sensitive = (TOKEN.encode(), API_KEY.encode(), b"mixel-remote://support/?invite=")
        if any(value in stdout_path.read_bytes() for value in sensitive):
            raise RuntimeError("Support invite bearer appeared in runtime output")
        for path in log_root.rglob("*") if log_root.exists() else []:
            if not path.is_file():
                continue
            with path.open("rb") as source:
                # Existing logs are unrelated to this synthetic test. If a log
                # rolled over, inspect its whole new file.
                old_offset = offsets.get(path, 0)
                if path.stat().st_size >= old_offset:
                    source.seek(old_offset)
                if any(value in source.read() for value in sensitive):
                    raise RuntimeError("Support invite bearer appeared in app logs")
        print("PASS: synthetic support invite bearer and API key absent from app logs and runtime output", flush=True)


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="mixel-linux-ipc-test-") as temporary:
        endpoint = Path(temporary) / "ipc"
        requests = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            listener.listen()
            def serve():
                for response in ({"t": "Config", "c": ["fixture", "attended-runtime-v2"]}, {"t": "Wrong", "c": None}):
                    with listener.accept()[0] as connection:
                        requests.append(json.loads(read_frame(connection)))
                        for byte in frame(json.dumps(response).encode()):
                            connection.sendall(bytes([byte]))
            worker = threading.Thread(target=serve)
            worker.start()
            assert query("Config", ["fixture", None], endpoint) == ["fixture", "attended-runtime-v2"]
            try:
                query("Config", ["fixture", None], endpoint)
                raise AssertionError("Mismatched IPC response passed")
            except RuntimeError:
                pass
            worker.join(timeout=3)
            assert not worker.is_alive()
            assert requests == [{"t": "Config", "c": ["fixture", None]}] * 2
    for length in (0, 63, 64, 16_383, 16_384, MAX_IPC_REPLY):
        sender, receiver = socket.socketpair()
        with sender, receiver:
            worker = threading.Thread(target=sender.sendall, args=(frame(b"x" * length),))
            worker.start()
            assert read_frame(receiver) == b"x" * length
            worker.join(timeout=3)
            assert not worker.is_alive()
    sender, receiver = socket.socketpair()
    with sender, receiver:
        sender.sendall(frame(b"incomplete")[:-1])
        sender.shutdown(socket.SHUT_WR)
        try:
            read_frame(receiver)
            raise AssertionError("Truncated IPC response passed")
        except RuntimeError:
            pass
    print("PASS: Linux smoke probe reads fragmented real-protocol Unix IPC and rejects mismatched/truncated replies")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--inside-session", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if sys.platform != "linux":
        parser.error("Actual support launch verification requires a Linux runner")
    if args.executable is None or not args.executable.is_file():
        parser.error("--executable must point to the built Linux bundle/mixel-remote")
    for tool in ("dbus-run-session", "xvfb-run", "openbox", "xdotool", "xwininfo", "xprop"):
        if not shutil.which(tool):
            parser.error(f"Required runtime smoke dependency missing: {tool}")
    if not args.inside_session:
        result = subprocess.run(["dbus-run-session", "--", "xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, str(Path(__file__).resolve()), "--executable", str(args.executable.resolve()), "--inside-session"])
        return result.returncode
    try:
        runtime(args.executable.resolve())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        # TimeoutExpired includes argv, which contains the synthetic bearer.
        detail = str(error).replace(URI, "[support URI]").replace(TOKEN, "[invite]").replace(API_KEY, "[API key]")
        print(f"FAIL: {detail}", file=sys.stderr)
        return 1
    print("PASS: actual Linux cold/warm attended-support runtime smoke", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
