#!/usr/bin/env python3
"""Real isolated Linux peers: relay, customer consent, video/input, files/recovery.

Never uses direct IP, personal desktops, existing containers, saved customer
passwords, or IPC authorization. IPC reads supply only independent assertions.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
EXE = "/usr/local/mixel-test/usr/share/mixel-remote/mixel-remote"
URI = "mixel-remote://support/?invite=inv_00000000-0000-0000-0000-000000000002&apikey=synthetic-invalid-public-key-000000000000"
TEXT = "mixel-remote-native-keyboard-proof"
FILE = "mixel-synthetic-transfer.bin"


def command(arguments, *, check=True, timeout=30):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{arguments[0]} failed ({result.returncode}): {result.stderr[-1500:]}")
    return result


def until(description, operation, timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = operation()
            if value:
                return value
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            last = error
        time.sleep(.4)
    raise RuntimeError(f"Timed out: {description}; {last or 'condition not met'}")


class Session:
    def __init__(self, proofs, deb, flutter_input, blocked):
        self.proofs = proofs
        self.deb = deb
        self.flutter_input = flutter_input
        self.blocked = blocked
        self.name = "mixel-session-" + uuid.uuid4().hex[:12]
        self.image = self.name + ":test"
        self.names = {role: self.name + "-" + role for role in ("host", "controller")}
        self.created = []
        self.server = {}
        self.gui_pid = {}
        self.ids = {}

    def run(self, role, arguments, *, check=True, user=None):
        cmd = ["docker", "exec"]
        if user:
            cmd += ["--user", user]
        return command(cmd + [self.names[role]] + arguments, check=check)

    def gui(self, role, arguments, *, check=True):
        script = 'export DBUS_SESSION_BUS_ADDRESS="$(cat /proofs/dbus-address)"; ' + shlex.join(arguments)
        return self.run(role, ["bash", "-c", script], check=check)

    def launch(self, role, arguments):
        script = ('export DBUS_SESSION_BUS_ADDRESS="$(cat /proofs/dbus-address)"; nohup '
                  + shlex.join([EXE, *arguments])
                  + ' >>/proofs/app-stdout.log 2>&1 </dev/null & echo $!')
        return int(self.run(role, ["bash", "-c", script]).stdout.strip())

    def query(self, role, kind, content=None):
        code = ('import sys,json;sys.path.insert(0,"/payload");from ipc_probe import query;'
                'print(json.dumps(query(' + repr(kind) + ',' + repr(content) + ')))')
        return json.loads(self.run(role, ["python3", "-c", code]).stdout)

    def screenshot(self, role, name):
        self.run(role, ["python3", "-c", "from PIL import ImageGrab;ImageGrab.grab().save(" + repr("/proofs/" + name + ".png") + ")"])

    def windows(self, role, pattern, visible=True):
        args = ["xdotool", "search"] + (["--onlyvisible"] if visible else []) + ["--name", pattern]
        result = self.gui(role, args, check=False)
        return result.stdout.split() if result.returncode == 0 else []

    def activate(self, role, window):
        self.gui(role, ["xdotool", "windowmap", window, "windowactivate", "--sync", window])

    def geometry(self, role, window):
        result = self.gui(role, ["xdotool", "getwindowgeometry", "--shell", window]).stdout
        return {key: int(value) for key, value in re.findall(r"^(X|Y|WIDTH|HEIGHT)=(-?\d+)$", result, re.M)}

    def click(self, role, x, y):
        self.gui(role, ["xdotool", "mousemove", str(round(x)), str(round(y)), "click", "1"])
        time.sleep(.3)

    def cm(self):
        pattern = "^" + self.ids["controller"] + ".*Mixel-Remote$"
        return until("real customer connection manager", lambda: next(iter(self.windows("host", pattern, False)), None))

    def accept(self, label):
        window = self.cm()
        self.activate("host", window)
        time.sleep(.7)
        self.screenshot("host", label + "-customer-accept")
        box = self.geometry("host", window)
        # Actual visible CM button; never send an Authorize IPC message.
        self.click("host", box["X"] + box["WIDTH"] * .27, box["Y"] + box["HEIGHT"] - 31)

    def disconnect(self):
        window = self.cm()
        self.activate("host", window)
        time.sleep(.4)
        box = self.geometry("host", window)
        self.click("host", box["X"] + box["WIDTH"] / 2, box["Y"] + box["HEIGHT"] - 31)
        until("actual customer Disconnect", lambda: self.query("host", "VideoConnCount") == 0)
        print("PASS: actual customer Disconnect returns authenticated Remote count to0", flush=True)

    def online(self, role):
        state = self.query(role, "OnlineStatus")
        return state if isinstance(state, list) and state[0] > 0 and state[1] is True else None

    def start_gui(self, role):
        self.gui_pid[role] = self.launch(role, [URI] if role == "host" else [])
        until(role + " actual main window", lambda: self.windows(role, "^Mixel-Remote$"))
        if role == "host":
            until("incoming v2 consent attestation", lambda: self.query(role, "Config", ["mixel-support-invite-attended", None]) == ["mixel-support-invite-attended", "attended-runtime-v2"])

    def stop_controller_gui(self):
        # Only processes inside this newly created, labeled peer container.
        code = r'''import os,signal
for name in os.listdir('/proc'):
 if not name.isdigit(): continue
 try:
  args=open('/proc/'+name+'/cmdline','rb').read().split(b'\0')
  if args and args[0].endswith(b'/mixel-remote') and b'--server' not in args:
   os.kill(int(name),signal.SIGTERM)
 except (OSError,ProcessLookupError): pass
'''
        self.run("controller", ["python3", "-c", code])
        time.sleep(.6)
        self.start_gui("controller")

    def connect(self, label):
        assert self.query("host", "VideoConnCount") == 0
        self.launch("controller", ["--connect", self.ids["host"], "--relay"])
        until("pending consent CM", self.cm)
        time.sleep(1)
        assert self.query("host", "VideoConnCount") == 0, "Password/recent session bypassed customer consent"
        self.screenshot("controller", label + "-before-accept")
        self.accept(label)
        until("explicit customer acceptance", lambda: self.query("host", "VideoConnCount") == 1)
        for window in self.windows("host", "^Mixel-Remote$"):
            self.gui("host", ["xdotool", "windowminimize", window])
        viewer = until("actual remote viewer", lambda: next(iter(self.windows("controller", "Remote Desktop.*Mixel-Remote$")), None))
        self.activate("controller", viewer)
        # Request fullscreen through the real window manager to make source and
        # destination screen coordinates deterministic; Flutter receives resize.
        self.gui("controller", ["wmctrl", "-ir", viewer, "-b", "add,fullscreen"])
        time.sleep(1)
        print(f"PASS: {label} waits for actual Accept (auth0) then authenticates (auth1)", flush=True)
        return viewer

    def video_map(self, name):
        self.screenshot("controller", name)
        code = r'''from PIL import Image
import json
im=Image.open('/proofs/NAME.png').convert('RGB')
rects=[]
for color in [(229,29,54),(19,183,108),(23,110,233)]:
 runs=[]
 for y in range(im.height):
  begin=None
  for x in range(im.width+1):
   match=x<im.width and max(abs(a-b) for a,b in zip(im.getpixel((x,y)),color))<20
   if match and begin is None:begin=x
   elif not match and begin is not None:
    if x-begin>=100:runs.append((x-begin,begin,y))
    begin=None
 assert runs,'Remote fixture color missing'
 width=max(r[0] for r in runs)
 wide=[r for r in runs if r[0]>=width*.9]
 rects.append((min(r[1] for r in wide),min(r[2] for r in wide),width))
assert abs(rects[1][0]-rects[0][0]-rects[0][2])<12
assert abs(rects[2][0]-rects[1][0]-rects[1][2])<12
print(json.dumps(rects[0]))
'''.replace("NAME", name)
        x, y, width = json.loads(self.run("controller", ["python3", "-c", code]).stdout)
        return lambda hx, hy: (x + (hx - 91) * width / 300, y + (hy - 128) * width / 300)

    def input(self):
        mapping = until("actual decoded video RGB fixture", lambda: self.video_map("video-proof"))
        print("PASS: actual remote video decodes the synthetic host RGB pattern", flush=True)
        if self.flutter_input:
            self.gui("controller", ["xdotool", "mousemove", "550", "5"])
            time.sleep(.6)
            self.click("controller", 580, 10)
            self.click("controller", 550, 23)
            self.click("controller", 612, 167)
            mapping = self.video_map("flutter-input-mode")
        self.click("controller", *mapping(250, 414))
        self.gui("controller", ["xdotool", "type", "--clearmodifiers", "--delay", "60", TEXT])
        self.click("controller", *mapping(541, 544))
        state = until("real remote keyboard and mouse callback", lambda: (value if value.get("text") == TEXT and value.get("count", 0) >= 1 else None) if (value := json.loads((self.proofs / "host/input.json").read_text())) else None)
        self.screenshot("controller", "keyboard-mouse-proof")
        print("PASS: exact remote keyboard text + mouse callback reached host using " + ("supported Flutter Input source2" if self.flutter_input else "default native Input source1"), flush=True)
        return state

    def path(self, x, path):
        self.click("controller", x, 180)
        self.gui("controller", ["xdotool", "key", "ctrl+a"])
        self.gui("controller", ["xdotool", "type", "--clearmodifiers", path])
        self.gui("controller", ["xdotool", "key", "Return"])
        time.sleep(.8)

    def files(self, expected_hash):
        self.stop_controller_gui()
        self.run("host", ["mkdir", "-p", "/home/guest/received"])
        self.run("controller", ["mkdir", "-p", "/home/guest/send-proof", "/home/guest/roundtrip"])
        self.run("controller", ["cp", "/payload/" + FILE, "/home/guest/send-proof/" + FILE])
        self.launch("controller", ["--file-transfer", self.ids["host"], "--relay"])
        self.accept("file-transfer")
        window = until("actual file manager", lambda: next(iter(self.windows("controller", "File Transfer.*Mixel-Remote$")), None))
        self.activate("controller", window)
        self.gui("controller", ["xdotool", "windowsize", window, "1300", "740", "windowmove", window, "0", "40"])
        time.sleep(1)
        self.path(230, "/home/guest/send-proof")
        self.path(870, "/home/guest/received")
        self.click("controller", 160, 294)
        self.click("controller", 424, 232)
        until("actual upload file", lambda: self.run("host", ["test", "-f", "/home/guest/received/" + FILE], check=False).returncode == 0)
        host_hash = self.run("host", ["sha256sum", "/home/guest/received/" + FILE]).stdout.split()[0]
        assert host_hash == expected_hash, "Transferred host bytes differ"
        self.path(230, "/home/guest/roundtrip")
        self.click("controller", 650, 294)
        self.click("controller", 575, 232)
        until("actual downloaded file", lambda: self.run("controller", ["test", "-f", "/home/guest/roundtrip/" + FILE], check=False).returncode == 0)
        received = self.run("controller", ["sha256sum", "/home/guest/roundtrip/" + FILE]).stdout.split()[0]
        assert received == expected_hash, "Round-trip bytes differ"
        self.screenshot("controller", "file-roundtrip-proof")
        print("PASS: actual encrypted relay file upload + download matches SHA256 " + expected_hash, flush=True)
        self.stop_controller_gui()

    def restart_server(self):
        self.run("host", ["kill", "-STOP", str(self.gui_pid["host"])])
        try:
            self.run("host", ["kill", "-KILL", str(self.server["host"])])
            self.server["host"] = self.launch("host", ["--server"])
            until("new native service IPC PID", lambda: self.run("host", ["cat", "/tmp/Mixel-Remote/ipc.pid"]).stdout.strip() == str(self.server["host"]))
            assert self.query("host", "Config", ["mixel-support-invite-attended", None]) == ["mixel-support-invite-attended", "attended-runtime-v2"], "Restarted service lost consent before heartbeat"
            assert self.query("host", "VideoConnCount") == 0
            print("PASS: fresh native incoming process keeps v2 consent while UI heartbeat is SIGSTOP paused", flush=True)
        finally:
            self.run("host", ["kill", "-CONT", str(self.gui_pid["host"])], check=False)
        until("native incoming process registered after restart", lambda: self.online("host"))
        self.connect("service-restart")
        self.video_map("service-restart-video")
        self.disconnect()

    def drop(self):
        self.stop_controller_gui()
        self.connect("before-network-drop")
        command(["docker", "network", "disconnect", "bridge", self.names["host"]])
        try:
            until("dropped transport disconnects authenticated session", lambda: self.query("host", "VideoConnCount") == 0, timeout=60)
        finally:
            command(["docker", "network", "connect", "bridge", self.names["host"]])
        until("rendezvous recovered after actual network drop", lambda: self.online("host"), timeout=90)
        self.stop_controller_gui()
        self.connect("network-reconnect")
        self.video_map("network-reconnect-video")
        self.disconnect()
        print("PASS: real network loss/recovery requires another actual customer Accept", flush=True)

    def capture(self):
        for role in self.created:
            self.run(role, ["bash", "-c", "cp -R /home/guest/.local/share/logs/Mixel-Remote /proofs/native-logs 2>/dev/null || true; ss -tnp >/proofs/tcp-sockets.txt"], check=False)
            if self.blocked:
                self.run(role, ["bash", "-c", "iptables-save -c >/proofs/native-port-block.txt"], user="root", check=False)

    def cleanup(self):
        self.capture()
        for role in self.created:
            command(["docker", "rm", "-f", self.names[role]], check=False)
        command(["docker", "image", "rm", self.image], check=False)

    def setup(self, temporary):
        context = temporary / "context"
        (context / "scripts/e2e").mkdir(parents=True)
        for name in ("linux-fixture.py", "start-linux-desktop.sh", "Dockerfile.linux"):
            shutil.copyfile(ROOT / "scripts/e2e" / name, context / "scripts/e2e" / name)
        build = command(["docker", "build", "--platform", "linux/amd64", "-f", str(context / "scripts/e2e/Dockerfile.linux"), "-t", self.image, str(context)], timeout=600)
        (self.proofs / "docker-build.log").write_text(build.stdout + build.stderr)
        payload = temporary / "payload"
        payload.mkdir()
        shutil.copyfile(self.deb, payload / "client.deb")
        shutil.copyfile(ROOT / "scripts/test-support-launch-linux.py", payload / "ipc_probe.py")
        content = b"MIXEL_SYNTHETIC_FILE_TRANSFER_PROOF\n" + bytes(range(256)) * 256
        (payload / FILE).write_bytes(content)
        expected_hash = hashlib.sha256(content).hexdigest()
        for role in self.names:
            proofs = self.proofs / role
            proofs.mkdir(mode=0o777)
            proofs.chmod(0o777)
            command(["docker", "run", "-d", "--platform", "linux/amd64", "--name", self.names[role], "--hostname", self.names[role], "--label", "com.mixel.test=" + self.name, "--cap-add", "NET_ADMIN", "--memory", "2g", "-v", str(proofs) + ":/proofs", "-v", str(payload) + ":/payload:ro", self.image])
            self.created.append(role)
            until(role + " synthetic desktop", lambda: (proofs / "fixture-ready").exists())
            self.run(role, ["bash", "-c", "mkdir -p /usr/local/mixel-test; dpkg-deb -x /payload/client.deb /usr/local/mixel-test; chown -R guest:guest /home/guest/.cache"], user="root")
            if self.blocked:
                for protocol in ("tcp", "udp"):
                    self.run(role, ["iptables", "-A", "OUTPUT", "-p", protocol, "--dport", "21115:21119", "-j", "REJECT"], user="root")
            self.server[role] = self.launch(role, ["--server"])
            until(role + " registered ID and verified relay key", lambda: self.online(role), timeout=90)
            self.ids[role] = self.query(role, "Config", ["id", None])[1]
            self.start_gui(role)
        self.gui("controller", ["bash", "-c", 'xdotool search --name "Mixel isolated customer desktop" windowminimize'])
        return expected_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--proofs", required=True, type=Path)
    parser.add_argument("--native-blocked", action="store_true", help="Block native relay ports in both owned peers; prove automatic HTTPS443 transport")
    parser.add_argument("--flutter-input", action="store_true", help="Separately prove supported Flutter Input source2 (default proves native source1)")
    parser.add_argument("--require-native", action="store_true", help="Reject amd64 emulation so native keyboard results are unambiguous")
    args = parser.parse_args()
    if args.require_native and platform.machine().lower() not in ("x86_64", "amd64"):
        raise SystemExit("Native amd64 host required for this proof")
    args.deb = args.deb.resolve(strict=True)
    args.proofs = args.proofs.resolve()
    args.proofs.mkdir(parents=True, exist_ok=True)
    if any(args.proofs.iterdir()):
        raise SystemExit("Proof output directory must be empty")
    manifest = {"artifact": args.deb.name, "sha256": hashlib.sha256(args.deb.read_bytes()).hexdigest(), "host_architecture": platform.machine(), "native_ports_blocked": args.native_blocked, "input_source": "flutter2" if args.flutter_input else "native1", "result": "failed"}
    session = Session(args.proofs, args.deb, args.flutter_input, args.native_blocked)
    with tempfile.TemporaryDirectory(prefix="mixel-real-session-") as temporary:
        try:
            digest = session.setup(Path(temporary))
            session.connect("initial")
            manifest["host_input"] = session.input()
            session.disconnect()
            session.files(digest)
            session.restart_server()
            session.drop()
            if args.native_blocked:
                for role in session.names:
                    sockets = session.run(role, ["ss", "-tn"]).stdout
                    assert ":443" in sockets, "HTTPS transport did not establish a TLS443 socket"
                    assert not re.search(r":2111[5-9]\s", sockets), "Blocked native relay connection still established"
                print("PASS: native21115-21119 blocked; real verified-key relay session survives on TLS443", flush=True)
            manifest["result"] = "passed"
            print("Result: real Linux consent/video/keyboard/mouse/file/restart/reconnect session passed", flush=True)
        finally:
            session.cleanup()
            (args.proofs / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
