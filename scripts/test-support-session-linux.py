#!/usr/bin/env python3
"""Real isolated Linux peers: relay, consent, video/input/clipboard, files/recovery.

Never uses direct IP, personal desktops, existing containers, saved customer
passwords, or IPC authorization. IPC reads supply only independent assertions.
"""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
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
    def __init__(self, proofs, deb, flutter_input, blocked, isolated_relay=None, mixed_transports=False):
        self.proofs = proofs
        self.deb = deb
        self.flutter_input = flutter_input
        self.blocked = blocked
        self.mixed_transports = mixed_transports
        self.automatic_transport = blocked or mixed_transports
        self.isolated_relay = isolated_relay
        self.expected_pin = isolated_relay["pin"] if isolated_relay else "OogSlDx9l+fgs0t6ihF3uTg9emyCv01m8cr4ullarRo="
        self.network = isolated_relay["network"] if isolated_relay else "bridge"
        self.name = "mixel-session-" + uuid.uuid4().hex[:12]
        self.image = self.name + ":test"
        self.names = {role: self.name + "-" + role for role in ("host", "controller")}
        self.created = []
        self.server = {}
        self.gui_pid = {}
        self.ids = {}
        self.initial_udp_packets = 0

    def firewall(self, role, after_registration=False):
        if self.mixed_transports:
            protocols = ("tcp",) if role == "host" and after_registration else (("udp",) if role == "controller" else ())
            if role == "host" and not after_registration:
                # Count real native registration independently before imposing
                # the partial TCP outage. NAT discovery must finish first.
                self.run(role, ["iptables", "-A", "OUTPUT", "-p", "udp", "--dport", "21116", "-j", "ACCEPT"], user="root")
        else:
            protocols = ("tcp", "udp") if self.blocked and not after_registration else ()
        for protocol in protocols:
            self.run(role, ["iptables", "-A", "OUTPUT", "-p", protocol, "--dport", "21115:21119", "-j", "REJECT"], user="root")

    def fixture_tcp(self, role):
        sockets = self.run(role, ["ss", "-tnp"]).stdout
        address = self.isolated_relay["address"]
        return sockets, [line for line in sockets.splitlines()
                         if line.startswith("ESTAB") and len(line.split()) >= 5
                         and line.split()[4].startswith(address + ":")]

    def native_udp_proof(self, label):
        sockets, established = self.fixture_tcp("host")
        rules = self.run("host", ["iptables-save", "-c"], user="root").stdout
        counters = re.search(r"^\[(\d+):(\d+)\] -A OUTPUT .*--dport 21116 .*?-j ACCEPT$", rules, re.M)
        assert counters and int(counters.group(1)) > 0, "No host UDP21116 registration packets observed"
        if label == "initial-registration":
            assert not any(":443" in line.split()[4] for line in established), "Host initial registration unexpectedly used HTTPS"
        assert self.online("host"), "Host native registration is not key-confirmed"
        (self.proofs / "host" / (label + "-udp-registration.txt")).write_text(rules + "\n" + sockets)
        return int(counters.group(1))

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

    def pending_accept(self, label):
        window = self.cm()
        self.activate("host", window)
        time.sleep(.7)
        box = self.geometry("host", window)
        # The real unauthorized CM renders blue Accept + white Cancel; an
        # authorized Remote/FileTransfer session renders red Disconnect.
        # This assertion covers FileTransfer too (VideoConnCount is Remote only).
        point = (box["X"] + 25, box["Y"] + box["HEIGHT"] - 31)
        def pending():
            code = "from PIL import ImageGrab;import json;print(json.dumps(ImageGrab.grab().getpixel(" + repr(point) + ")[:3]))"
            red, green, blue = json.loads(self.run("host", ["python3", "-c", code]).stdout)
            return red < 80 and green > 75 and blue > 180
        until(label + " actual unauthorized Accept UI", pending)
        self.screenshot("host", label + "-customer-accept")
        print("PASS: " + label + " real CM remains unauthorized with visible Accept before customer click", flush=True)
        return box

    def accept(self, label):
        box = self.pending_accept(label)
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

    def health(self, role):
        # Reuse independent smoke assertions for the attended host: real owner
        # PID, relay/key/options, native endpoint, ID, service key confirmation.
        code = ("import sys,json;sys.path.insert(0,'/payload');import ipc_probe;"
                + "ipc_probe.PUBLIC_KEY=" + repr(self.expected_pin) + ";"
                "print(json.dumps(ipc_probe.runtime_health("
                + str(self.server[role]) + ")))")
        state = json.loads(self.run(role, ["python3", "-c", code]).stdout)
        return state if state[0] > 0 and state[1] is True else None

    def active_https_proof(self):
        assert self.query("host", "VideoConnCount") == 1
        for role in self.names:
            sockets = self.run(role, ["ss", "-tnp"]).stdout
            (self.proofs / role / "active-encrypted-video-tcp.txt").write_text(sockets)
            established = [line for line in sockets.splitlines() if line.startswith("ESTAB")]
            assert not any(re.search(r":2111[5-9]\s", line) for line in established), "Blocked native relay connection established"
            assert sum(":443" in line for line in established) >= 2, "Active forced relay plus registration did not establish TLS443 sockets"
        print("PASS: actual authenticated forced-relay video/input active with native21115-21119 blocked; both peers have TLS443 registration+session sockets", flush=True)

    def active_mixed_proof(self):
        assert self.query("host", "VideoConnCount") == 1
        host_udp = self.native_udp_proof("active-encrypted-video")
        assert host_udp > self.initial_udp_packets, "Host UDP registration stopped after the native TCP outage"
        for role in self.names:
            sockets, established = self.fixture_tcp(role)
            (self.proofs / role / "active-mixed-transport-tcp.txt").write_text(sockets)
            if role == "host":
                assert not any(re.search(r":2111[5-9]$", line.split()[4]) for line in established), "Host blocked native TCP transport established"
                assert sum(line.split()[4].endswith(":443") for line in established) >= 1, "Host relay session did not use HTTPS443"
            else:
                assert sum(line.split()[4].endswith(":443") for line in established) >= 1, "Controller registration did not use HTTPS443"
        control_rules = self.run("controller", ["iptables-save", "-c"], user="root").stdout
        control = re.search(r"^\[(\d+):(\d+)\] -A OUTPUT .*--dport 21116 .*?-j ACCEPT$", control_rules, re.M)
        assert control and int(control.group(1)) > 0, "Ordinary controller request did not exercise native TCP21116 control; mixed late-fallback gap not tested"
        (self.proofs / "controller" / "ordinary-id-native-control-tcp.txt").write_text(control_rules)
        (self.proofs / "mixed-transport-proof.json").write_text(json.dumps({
            "host_registration": "native UDP21116, key-confirmed before TCP block",
            "host_udp_packets": host_udp, "host_session": "HTTPS443; native TCP21115-19 blocked",
            "controller_registration": "HTTPS443; native UDP21115-19 blocked",
            "controller_native_tcp": "ordinary-ID native TCP21116 control observed",
            "controller_control_tcp_packets": int(control.group(1)), "request": "ordinary ID, no --relay",
        }, indent=2) + "\n")
        print("PASS: mixed transport: host native UDP registration plus HTTPS443 session; controller HTTPS registration with native TCP available; ordinary ID without --relay", flush=True)

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

    def request(self, label):
        assert self.query("host", "VideoConnCount") == 0
        self.launch("controller", ["--connect", self.ids["host"]] + ([] if self.automatic_transport else ["--relay"]))
        until("pending consent CM", self.cm)
        time.sleep(1)
        assert self.query("host", "VideoConnCount") == 0, "Password/recent session bypassed customer consent"
        self.screenshot("controller", label + "-before-accept")
        self.pending_accept(label)

    def connect(self, label, already_requested=False):
        if not already_requested:
            self.request(label)
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
        # A delayed support-link foreground callback can run after Accept on a
        # slow desktop. Keep the synthetic fixture visible for pixel matching.
        for window in self.windows("host", "^Mixel-Remote$"):
            self.gui("host", ["xdotool", "windowminimize", window])
        for window in self.windows("host", "^Mixel isolated customer desktop$"):
            self.activate("host", window)
        # Foreground callbacks can restore the app after minimizing it. The
        # own synthetic fixture must be above it for an unoccluded RGB oracle.
        time.sleep(.3)
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
        self.screenshot("host", "host-keyboard-mouse-proof")
        # The independent callback can precede the next encoded video frame.
        # Let that frame settle so the shared viewer proof displays the click.
        time.sleep(.8)
        self.screenshot("controller", "keyboard-mouse-proof")
        print("PASS: exact remote keyboard text + mouse callback reached host using " + ("supported Flutter Input source2" if self.flutter_input else "default native Input source1"), flush=True)
        return state

    def clipboard(self):
        assert self.query("host", "VideoConnCount") == 1
        exchanges = []
        for source, destination in (("controller", "host"), ("host", "controller")):
            expected = "mixel-synthetic-clipboard-" + source + "-to-" + destination
            # xclip owns the real X11 CLIPBOARD selection. It forks into the
            # background; redirect its output so Docker pipes cannot stay open.
            script = (shlex.join(["printf", "%s", expected])
                      + " | xclip -selection clipboard -in >/dev/null 2>&1")
            self.gui(source, ["bash", "-c", script])
            actual = until(
                "actual " + source + " to " + destination + " clipboard",
                lambda: (value if value == expected else None)
                if (value := self.gui(destination, ["xclip", "-selection", "clipboard", "-out"], check=False).stdout) else None,
                timeout=30,
            )
            exchanges.append({"source": source, "destination": destination, "text": actual})
        (self.proofs / "clipboard-proof.json").write_text(json.dumps(exchanges, indent=2) + "\n")
        print("PASS: actual bidirectional remote clipboard matches exact synthetic text on both X11 desktops", flush=True)

    def path(self, x, path):
        # Click the breadcrumb divider (no child navigation handler), which
        # reliably opens the containing real editable location field.
        self.click("controller", x, 180)
        self.gui("controller", ["xdotool", "key", "ctrl+a"])
        self.gui("controller", ["xdotool", "type", "--clearmodifiers", path])
        self.gui("controller", ["xdotool", "key", "Return"])
        time.sleep(.8)

    def files(self, expected_hash):
        self.stop_controller_gui()
        # Keep synthetic paths short and distinct for unambiguous UI proof.
        source, remote, roundtrip = "/home/guest/s", "/home/guest/h", "/home/guest/r"
        self.run("host", ["mkdir", "-p", remote])
        self.run("controller", ["mkdir", "-p", source, roundtrip])
        self.run("controller", ["cp", "/payload/" + FILE, source + "/" + FILE])
        self.launch("controller", ["--file-transfer", self.ids["host"]] + ([] if self.automatic_transport else ["--relay"]))
        self.accept("file-transfer")
        window = until("actual file manager", lambda: next(iter(self.windows("controller", "File Transfer.*Mixel-Remote$")), None))
        self.activate("controller", window)
        self.gui("controller", ["xdotool", "windowsize", window, "1300", "740", "windowmove", window, "0", "40"])
        time.sleep(1)
        self.path(130, source)
        self.path(616, remote)
        self.click("controller", 160, 294)
        self.screenshot("controller", "file-upload-selected")
        self.click("controller", 424, 232)
        until("actual upload complete SHA256", lambda: self.run("host", ["sha256sum", remote + "/" + FILE], check=False).stdout.split()[:1] == [expected_hash])
        self.screenshot("controller", "file-upload-complete")
        self.path(130, roundtrip)
        self.click("controller", 650, 294)
        self.screenshot("controller", "file-download-selected")
        self.click("controller", 575, 232)
        until("actual downloaded complete SHA256", lambda: self.run("controller", ["sha256sum", roundtrip + "/" + FILE], check=False).stdout.split()[:1] == [expected_hash])
        self.screenshot("controller", "file-roundtrip-proof")
        (self.proofs / "file-roundtrip-proof.json").write_text(json.dumps({
            "filename": FILE, "sha256": expected_hash,
            "controller_source": source, "host_uploaded": remote,
            "controller_downloaded": roundtrip,
        }, indent=2) + "\n")
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
            until("native incoming process registered after restart", lambda: self.health("host"))
            # CM is its own process. Keep the foreground owner paused through
            # new login and visible unauthorized CM so no heartbeat can mask a
            # service restart which lost the kernel-owned consent requirement.
            self.request("service-restart-heartbeat-paused")
            assert self.query("host", "VideoConnCount") == 0
            print("PASS: fresh native incoming process requires real customer Accept for a new session while foreground UI heartbeat is SIGSTOP paused", flush=True)
        finally:
            self.run("host", ["kill", "-CONT", str(self.gui_pid["host"])], check=False)
        self.connect("service-restart", already_requested=True)
        self.video_map("service-restart-video")
        self.disconnect()

    def drop(self):
        self.stop_controller_gui()
        self.connect("before-network-drop")
        command(["docker", "network", "disconnect", self.network, self.names["host"]])
        try:
            until("dropped transport disconnects authenticated session", lambda: self.query("host", "VideoConnCount") == 0, timeout=60)
        finally:
            command(["docker", "network", "connect", self.network, self.names["host"]])
        until("rendezvous recovered after actual network drop", lambda: self.online("host"), timeout=90)
        self.stop_controller_gui()
        self.connect("network-reconnect")
        self.video_map("network-reconnect-video")
        self.disconnect()
        print("PASS: real network loss/recovery requires another actual customer Accept", flush=True)

    def capture(self):
        for role in self.created:
            self.screenshot(role, "final-desktop-state")
            self.run(role, ["bash", "-c", "cp -R /home/guest/.local/share/logs/Mixel-Remote /proofs/native-logs 2>/dev/null || true; ss -tnp >/proofs/tcp-sockets.txt"], check=False)
            if self.automatic_transport:
                self.run(role, ["bash", "-c", "iptables-save -c >/proofs/native-port-block.txt"], user="root", check=False)

    def cleanup(self):
        errors = []
        try:
            self.capture()
        except Exception as error:
            errors.append("Capture: " + str(error))
        for role in self.created:
            try:
                result = command(["docker", "rm", "-f", self.names[role]], check=False)
                if result.returncode:
                    errors.append(role + " container removal failed")
            except Exception as error:
                errors.append(role + " cleanup: " + str(error))
        try:
            result = command(["docker", "image", "rm", self.image], check=False)
            if result.returncode:
                errors.append("Owned test image removal failed")
        except Exception as error:
            errors.append("Image cleanup: " + str(error))
        return errors

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
        if self.isolated_relay:
            shutil.copyfile(self.isolated_relay["ca"], payload / "isolated-relay-ca.crt")
        content = b"MIXEL_SYNTHETIC_FILE_TRANSFER_PROOF\n" + bytes(range(256)) * 256
        (payload / FILE).write_bytes(content)
        expected_hash = hashlib.sha256(content).hexdigest()
        for role in self.names:
            proofs = self.proofs / role
            proofs.mkdir(mode=0o777)
            proofs.chmod(0o777)
            arguments = ["docker", "run", "-d", "--platform", "linux/amd64", "--name", self.names[role], "--hostname", self.names[role], "--label", "com.mixel.test=" + self.name, "--cap-add", "NET_ADMIN", "--memory", "2g", "-v", str(proofs) + ":/proofs", "-v", str(payload) + ":/payload:ro"]
            if self.isolated_relay:
                arguments += ["--network", self.network, "--add-host", "rs.mixel.ch:" + self.isolated_relay["address"]]
            # Enroll the unique owned name before Docker may create it. A
            # timeout/signal between creation and command return still cleans it.
            self.created.append(role)
            command(arguments + [self.image])
            until(role + " synthetic desktop", lambda: (proofs / "fixture-ready").exists())
            self.run(role, ["bash", "-c", "mkdir -p /usr/local/mixel-test; dpkg-deb -x /payload/client.deb /usr/local/mixel-test; chown -R guest:guest /home/guest/.cache"], user="root")
            if self.isolated_relay:
                self.run(role, ["bash", "-c", "cp /payload/isolated-relay-ca.crt /usr/local/share/ca-certificates/mixel-isolated-relay.crt; update-ca-certificates >/proofs/isolated-ca-install.log 2>&1"], user="root")
                config = '[options]\ncustom-rendezvous-server = "rs.mixel.ch"\nrelay-server = "rs.mixel.ch"\nkey = "' + self.expected_pin + '"\n'
                self.run(role, ["python3", "-c", "from pathlib import Path;p=Path('/home/guest/.config/mixel-remote');p.mkdir(parents=True,exist_ok=True);(p/'Mixel-Remote2.toml').write_text(" + repr(config) + ")"])
            self.firewall(role)
            self.server[role] = self.launch(role, ["--server"])
            until(role + " registered ID and verified relay key", lambda: self.online(role), timeout=90)
            self.ids[role] = self.query(role, "Config", ["id", None])[1]
            self.start_gui(role)
            if role == "host":
                until("independent host IPC/relay/pin/ID proof", lambda: self.health(role))
                if self.mixed_transports:
                    self.initial_udp_packets = self.native_udp_proof("initial-registration")
                    self.firewall(role, after_registration=True)
            else:
                assert self.run(role, ["cat", "/tmp/Mixel-Remote/ipc.pid"]).stdout.strip() == str(self.server[role])
                options = self.query(role, "Options")
                for key, value in {"custom-rendezvous-server": "rs.mixel.ch", "relay-server": "rs.mixel.ch", "key": self.expected_pin}.items():
                    assert options.get(key) == value, "Controller branded relay/pin changed"
                assert "mixel-support-invite-attended" not in options
        self.gui("controller", ["bash", "-c", 'xdotool search --name "Mixel isolated customer desktop" windowminimize'])
        if self.mixed_transports:
            # Install after service/GUI setup so this counter cannot be
            # satisfied by registration or a NAT discovery probe.
            self.run("controller", ["iptables", "-A", "OUTPUT", "-p", "tcp", "--dport", "21116", "-j", "ACCEPT"], user="root")
        return expected_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--proofs", required=True, type=Path)
    transports = parser.add_mutually_exclusive_group()
    transports.add_argument("--native-blocked", action="store_true", help="Block native relay ports in both owned peers; prove automatic HTTPS443 transport")
    transports.add_argument("--mixed-transports", action="store_true", help="Isolated fixture only: host UDP registration/native TCP blocked; controller UDP blocked/native TCP available")
    parser.add_argument("--flutter-input", action="store_true", help="Separately prove supported Flutter Input source2 (default proves native source1)")
    parser.add_argument("--require-native", action="store_true", help="Reject amd64 emulation so native keyboard results are unambiguous")
    parser.add_argument("--artifact-run-id", help="Source build run for the exact installer (independent of this harness run)")
    parser.add_argument("--isolated-relay-network", help="Explicit task-owned Docker network for a deployment-free relay fixture")
    parser.add_argument("--isolated-relay-address", help="Private fixture TLS router IPv4; mapped to rs.mixel.ch only in owned peers")
    parser.add_argument("--isolated-relay-ca", type=Path, help="Public fixture CA certificate; trusted only by owned peer containers")
    parser.add_argument("--isolated-relay-pin", type=Path, help="Public fixture Ed25519 relay pin; seeded only in owned peer HOME")
    args = parser.parse_args()
    isolated = None
    isolated_values = (args.isolated_relay_network, args.isolated_relay_address, args.isolated_relay_ca, args.isolated_relay_pin)
    if any(isolated_values):
        if not all(isolated_values) or not (args.native_blocked or args.mixed_transports):
            raise SystemExit("Isolated relay requires all four fixture parameters and an explicit blocked/mixed transport mode")
        address = ipaddress.ip_address(args.isolated_relay_address)
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified:
            raise SystemExit("Isolated relay must use its private Docker IPv4")
        ca = args.isolated_relay_ca.resolve(strict=True)
        if b"PRIVATE KEY" in ca.read_bytes() or b"BEGIN CERTIFICATE" not in ca.read_bytes():
            raise SystemExit("Isolated CA input must contain a public certificate only")
        pin = args.isolated_relay_pin.resolve(strict=True).read_text().strip()
        if len(base64.b64decode(pin, validate=True)) != 32:
            raise SystemExit("Isolated relay pin must be a public Ed25519 key")
        command(["docker", "network", "inspect", args.isolated_relay_network])
        isolated = {"network": args.isolated_relay_network, "address": str(address), "ca": ca, "pin": pin}
    if args.mixed_transports and isolated is None:
        raise SystemExit("Mixed transport tests require the explicit isolated fixture")
    if args.require_native and platform.machine().lower() not in ("x86_64", "amd64"):
        raise SystemExit("Native amd64 host required for this proof")
    args.deb = args.deb.resolve(strict=True)
    args.proofs = args.proofs.resolve()
    args.proofs.mkdir(parents=True, exist_ok=True)
    if any(args.proofs.iterdir()):
        raise SystemExit("Proof output directory must be empty")
    revision = command(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=False).stdout.strip()
    manifest = {"artifact": args.deb.name, "sha256": hashlib.sha256(args.deb.read_bytes()).hexdigest(), "host_architecture": platform.machine(), "native_ports_blocked": args.native_blocked, "input_source": "flutter2" if args.flutter_input else "native1", "result": "failed", "relay_environment": "isolated fixture (test CA and test public pin)" if isolated else "live Mixel relay", "forced_relay_request": "automatic transport selection (no --relay)" if args.native_blocked else "explicit --relay", "harness_revision": revision or "unavailable", "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "run_id": os.environ.get("GITHUB_RUN_ID"), "artifact_run_id": args.artifact_run_id or os.environ.get("GITHUB_RUN_ID")}
    if isolated:
        manifest["isolated_relay"] = {"network": isolated["network"], "private_address": isolated["address"], "public_pin_sha256": hashlib.sha256(isolated["pin"].encode()).hexdigest(), "ca_sha256": hashlib.sha256(isolated["ca"].read_bytes()).hexdigest()}
    manifest["mixed_transports"] = args.mixed_transports
    if args.mixed_transports:
        manifest["forced_relay_request"] = "automatic mixed transport selection (no --relay)"
        manifest["transport_policy"] = {"host": "native UDP registration, TCP21115-19 blocked after initial registration", "controller": "UDP21115-19 blocked, native TCP available"}
    session = Session(args.proofs, args.deb, args.flutter_input, args.native_blocked, isolated, args.mixed_transports)
    with tempfile.TemporaryDirectory(prefix="mixel-real-session-") as temporary:
        try:
            digest = session.setup(Path(temporary))
            session.connect("initial")
            if args.mixed_transports:
                session.active_mixed_proof()
            manifest["host_input"] = session.input()
            session.clipboard()
            if args.native_blocked:
                session.active_https_proof()
            session.disconnect()
            session.files(digest)
            session.restart_server()
            session.drop()
            manifest["result"] = "passed"
            print("Result: real Linux consent/video/keyboard/mouse/clipboard/file/restart/reconnect session passed", flush=True)
        finally:
            try:
                cleanup_errors = session.cleanup()
                if cleanup_errors:
                    manifest["cleanup_errors"] = cleanup_errors
                    manifest["result"] = "failed"
            finally:
                (args.proofs / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            if manifest.get("cleanup_errors"):
                raise RuntimeError("Owned test cleanup/capture failed: " + "; ".join(manifest["cleanup_errors"]))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    main()
