#!/usr/bin/env python3
"""Real isolated Linux peers: relay, consent, video/input/clipboard, files/recovery.

Never uses direct IP, personal desktops, existing containers, saved customer
passwords, or IPC authorization. IPC reads supply only independent assertions.
"""
import argparse
import base64
import csv
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
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
FILE = "mixel-proof.bin"


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


def decode_marker(samples, source_counter):
    """Decode the displayed fixture clock and reject stale/covered video."""
    if len(samples) != 20:
        raise RuntimeError("Decoded video marker has an invalid cell count")
    bits = ""
    for color in samples:
        if max(color) < 90:
            bits += "0"
        elif min(color) > 165:
            bits += "1"
        else:
            raise RuntimeError("Decoded video marker is obscured or ambiguous")
    if bits[:4] != "1010":
        raise RuntimeError("Decoded video marker synchronization is absent")
    counter = int(bits[4:], 2)
    # The half-second counter is unique over a nine-hour test window. At most
    # three seconds of capture/network/decoder lag are allowed, including wrap.
    age = ((source_counter - counter + 32768) & 65535) - 32768
    if not -2 <= age <= 6:
        raise RuntimeError("Decoded video retains an old or unrelated fixture frame")
    return counter


def verify_application_artifact(artifact_root, deb, run_id, source_commit, expected_sha256):
    """Bind the tested DEB to its collected Linux artifact and source run."""
    if not (run_id and re.fullmatch(r"[1-9][0-9]*", run_id)
            and source_commit and re.fullmatch(r"[0-9a-f]{40}", source_commit)
            and expected_sha256 and re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
        raise ValueError("Saved-password proof requires explicit run, full application source SHA and installer SHA256")
    root = artifact_root.resolve(strict=True)
    deb = deb.resolve(strict=True)
    if not deb.is_file() or not deb.is_relative_to(root):
        raise ValueError("Installer must reside under its collected artifact root")
    relative = deb.relative_to(root).as_posix()
    if not relative.startswith("artifacts/linux/") or deb.suffix != ".deb":
        raise ValueError("Installer path must belong to the collected Linux artifact")
    metadata_path, provenance_path = root / "run-metadata.json", root / "artifact-provenance.json"
    metadata, provenance = (json.loads(path.read_text()) for path in (metadata_path, provenance_path))
    url = "https://github.com/Jangopeople/mixel-remote-client/actions/runs/" + run_id
    assert str(metadata["id"]) == run_id and metadata["head_sha"] == source_commit, "Run metadata source/run differs from application"
    assert metadata["event"] == "workflow_dispatch" and metadata["html_url"] == url, "Unexpected application workflow metadata"
    assert provenance["repository"] == "Jangopeople/mixel-remote-client", "Unexpected collected repository"
    assert str(provenance["build_run_id"]) == run_id and provenance["build_url"] == url, "Collected artifact belongs to a different run"
    assert provenance["event"] == "workflow_dispatch", "Unexpected collected workflow event"
    for field in ("head_sha", "source_commit", "application_source_baseline"):
        assert provenance[field] == source_commit, "Collected application source differs: " + field
    artifact = provenance["collected_artifacts"]["linux"]
    assert type(artifact["artifact_id"]) is int and artifact["artifact_id"] > 0, "Invalid collected artifact ID"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["artifact_digest"]), "Invalid collected archive digest"
    assert artifact["files_sha256"][relative] == expected_sha256, "DEB differs from exact collected artifact mapping"
    assert hashlib.sha256(deb.read_bytes()).hexdigest() == expected_sha256, "Actual DEB bytes differ from collected mapping"
    return {"application_source_baseline": source_commit, "artifact_run_id": run_id,
            "artifact_sha256": expected_sha256, "artifact_relative_path": relative,
            "collected_artifact_id": artifact["artifact_id"], "collected_archive_digest": artifact["artifact_digest"],
            "artifact_workflow_url": url, "run_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            "artifact_provenance_sha256": hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
            "validation_scope": "exact Linux artifact; overall multi-platform run result is recorded independently",
            "application_run_status": metadata.get("status"), "application_run_conclusion": metadata.get("conclusion")}


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
        counters = re.search(r"^\[(\d+):(\d+)\] -A OUTPUT -p udp .*--dport 21116 .*?-j ACCEPT$", rules, re.M)
        assert counters and int(counters.group(1)) > 0, "No host UDP21116 registration packets observed"
        if label == "initial-registration":
            assert not any(":443" in line.split()[4] for line in established), "Host initial registration unexpectedly used HTTPS"
        assert self.online("host"), "Host native registration is not key-confirmed"
        (self.proofs / "host" / (label + "-udp-registration.txt")).write_text(rules + "\n" + sockets)
        return int(counters.group(1))

    def run(self, role, arguments, *, check=True, user=None, timeout=30):
        cmd = ["docker", "exec"]
        if user:
            cmd += ["--user", user]
        return command(cmd + [self.names[role]] + arguments, check=check, timeout=timeout)

    def gui(self, role, arguments, *, check=True):
        script = 'export DBUS_SESSION_BUS_ADDRESS="$(cat /proofs/dbus-address)"; ' + shlex.join(arguments)
        return self.run(role, ["bash", "-c", script], check=check)

    def launch(self, role, arguments):
        token = "launch-" + uuid.uuid4().hex[:12]
        pid_file = "/proofs/" + token + ".pid"
        result_file = "/proofs/" + token + "-exit.json"
        result_code = "import json,sys,time;from pathlib import Path;Path(" + repr(result_file) + ").write_text(json.dumps({'pid':int(sys.argv[1]),'wait_status':int(sys.argv[2]),'monotonic':time.monotonic()}))"
        child = (shlex.join([EXE, *arguments]) + " & app_pid=$!; printf '%s\\n' \"$app_pid\" > "
                 + shlex.quote(pid_file) + '; wait "$app_pid"; result=$?; '
                 + shlex.join(["python3", "-c", result_code]) + ' "$app_pid" "$result"')
        script = ('export DBUS_SESSION_BUS_ADDRESS="$(cat /proofs/dbus-address)"; nohup '
                  + shlex.join(["bash", "-c", child])
                  + ' >>/proofs/app-stdout.log 2>&1 </dev/null & '
                  + "for attempt in {1..50}; do [ -s " + shlex.quote(pid_file)
                  + " ] && break; sleep .1; done; cat " + shlex.quote(pid_file))
        return int(self.run(role, ["bash", "-c", script]).stdout.strip())

    def query(self, role, kind, content=None):
        code = ('import sys,json;sys.path.insert(0,"/payload");from ipc_probe import query;'
                'print(json.dumps(query(' + repr(kind) + ',' + repr(content) + ')))')
        return json.loads(self.run(role, ["python3", "-c", code]).stdout)

    def screenshot(self, role, name, timeout=30):
        self.run(role, ["python3", "-c", "from PIL import ImageGrab;ImageGrab.grab().save(" + repr("/proofs/" + name + ".png") + ")"], timeout=timeout)

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
        code = ("import sys,json,os;from pathlib import Path;sys.path.insert(0,'/payload');import ipc_probe;"
                + "ipc_probe.PUBLIC_KEY=" + repr(self.expected_pin) + ";"
                "state=ipc_probe.runtime_health(" + str(self.server[role]) + ");"
                "lease=Path('/tmp/mixel-remote-attended-v2-'+str(os.geteuid())+'/lease').stat();"
                "locks=[line.split() for line in Path('/proc/locks').read_text().splitlines()];"
                "assert any(len(row)>5 and row[1]=='FLOCK' and row[3]=='READ' and row[4]=="
                + repr(str(self.gui_pid[role]))
                + " and row[5].rsplit(':',1)[-1]==str(lease.st_ino) for row in locks),'Actual foreground PID lost its kernel attended lease';"
                "print(json.dumps(state))")
        state = json.loads(self.run(role, ["python3", "-c", code]).stdout)
        return state if state[0] > 0 and state[1] is True else None

    def fixture_state(self):
        code = "import json,time;from pathlib import Path;s=json.loads(Path('/proofs/fixture-state.json').read_text());assert time.monotonic()-s['monotonic']<3,'Synthetic desktop observer stopped';print(json.dumps(s))"
        state = json.loads(self.run("host", ["python3", "-c", code]).stdout)
        assert all(state["controls"][name]["visible"] for name in ("canvas", "entry", "button")), "Synthetic input controls are not visible"
        return state

    def process_snapshot(self, role, label, timeout=2, capture_logs=False):
        # Read only selected status fields. Never collect process argv, env,
        # configuration files, bearer values or unrelated desktop contents.
        code = r'''import json,time,shutil,subprocess,sys
from pathlib import Path
selected=('Name','State','PPid','VmRSS','VmHWM','Threads')
state={'monotonic':time.monotonic(),'processes':{},'memory':{}}
owned=set(PIDS)
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  if str((p/'exe').readlink())==EXE_PATH or (p/'comm').read_text().strip()=='mixel-remote':owned.add(int(p.name))
 except OSError:pass
for pid in sorted(owned):
 p=Path('/proc')/str(pid)
 try:
  fields=dict(line.split(':',1) for line in (p/'status').read_text().splitlines() if ':' in line)
  state['processes'][str(pid)]={key:fields[key].strip() for key in selected if key in fields}
 except OSError as error:state['processes'][str(pid)]={'error':str(error)}
for name in ('memory.events','memory.current','memory.max','memory.peak'):
 try:state['memory'][name]=(Path('/sys/fs/cgroup')/name).read_text().strip()
 except OSError as error:state['memory'][name]={'error':str(error)}
try:state['ipc_pid']=Path('/tmp/Mixel-Remote/ipc.pid').read_text().strip()
except OSError as error:state['ipc_pid']={'error':str(error)}
try:state['kernel_locks']=Path('/proc/locks').read_text().splitlines()
except OSError as error:state['kernel_locks']={'error':str(error)}
state['exit_statuses']=[]
for p in Path('/proofs').glob('launch-*-exit.json'):
 try:state['exit_statuses'].append(json.loads(p.read_text()))
 except (OSError,ValueError):pass
errors=[]
if CAPTURE_LOGS:
 try:shutil.copytree('/home/guest/.local/share/logs/Mixel-Remote','/proofs/native-logs',dirs_exist_ok=True)
 except OSError as error:errors.append(str(error))
 try:
  result=subprocess.run(['ss','-tnp'],capture_output=True,text=True,check=True)
  Path('/proofs/tcp-sockets.txt').write_text(result.stdout)
 except subprocess.SubprocessError as error:errors.append(str(error))
state['diagnostic_errors']=errors
Path(OUTPUT_PATH).write_text(json.dumps(state,indent=2)+'\n')
print(json.dumps(state))
if errors:sys.exit(1)
'''.replace("PIDS", repr(sorted({pid for pid in (self.server.get(role), self.gui_pid.get(role)) if pid}))).replace("EXE_PATH", repr(EXE)).replace("CAPTURE_LOGS", repr(capture_logs)).replace("OUTPUT_PATH", repr("/proofs/" + label + "-process-state.json"))
        snapshot = json.loads(self.run(role, ["python3", "-c", code], timeout=timeout).stdout)
        # The guest already wrote the mounted proof. Its UID differs from the
        # CI runner, so the runner must not reopen that file for writing.
        return snapshot

    def relay_addresses(self, role):
        if self.isolated_relay:
            return {self.isolated_relay["address"]}
        code = "import socket,json;print(json.dumps(sorted({item[4][0] for item in socket.getaddrinfo('rs.mixel.ch',443,socket.AF_INET,socket.SOCK_STREAM)})))"
        return set(json.loads(self.run(role, ["python3", "-c", code]).stdout))

    def other_peer_addresses(self, role):
        other = "controller" if role == "host" else "host"
        result = command(["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", self.names[other]])
        addresses = {details["IPAddress"] for details in json.loads(result.stdout).values() if details.get("IPAddress")}
        assert addresses, "Cannot independently identify the owned opposite peer"
        return addresses

    def app_tcp_evidence(self, role):
        """Count only exact relay endpoints owned by this extracted installer."""
        sockets = self.run(role, ["ss", "-tnp"]).stdout
        expected = self.relay_addresses(role)
        other = self.other_peer_addresses(role)
        verified = set()
        connections = {}
        for line in sockets.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[0] != "ESTAB":
                continue
            peer, _, port = fields[4].rpartition(":")
            try:
                address = ipaddress.ip_address(peer.strip("[]"))
                peer = str(address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address)
                port = int(port)
            except ValueError:
                continue
            assert peer not in other, "Direct established socket to the owned opposite peer bypasses HTTPS relay: " + fields[4]
            if peer not in expected:
                continue
            for owner in re.findall(r'\("mixel-remote",pid=(\d+),', line):
                pid = int(owner)
                if pid not in verified:
                    executable = self.run(role, ["readlink", "/proc/" + owner + "/exe"]).stdout.strip()
                    assert executable == EXE, "Socket PID is not the exact tested Mixel installer"
                    verified.add(pid)
                identity = (fields[3], fields[4])
                record = connections.setdefault(identity, {"pids": set(), "address": peer, "port": port,
                                                          "local_endpoint": fields[3], "peer_endpoint": fields[4]})
                record["pids"].add(pid)
        return sockets, [{**record, "pids": sorted(record["pids"])} for record in connections.values()]

    def active_https_proof(self, label=None):
        assert self.query("host", "VideoConnCount") == 1
        evidence = {}
        for role in self.names:
            sockets, connections = self.app_tcp_evidence(role)
            (self.proofs / role / ((label + "-" if label else "") + "active-encrypted-video-tcp.txt")).write_text(sockets)
            assert not any(21115 <= item["port"] <= 21119 for item in connections), "Blocked native relay connection established"
            tls = [item for item in connections if item["port"] == 443]
            assert len(tls) >= 2, "Actual Mixel registration plus relay did not establish two exact relay TLS443 sockets"
            assert any(self.server[role] in item["pids"] for item in tls), "Incoming registration process has no exact relay TLS443 socket"
            if role == "controller":
                assert any(self.server[role] not in item["pids"] for item in tls), "Outgoing session process has no separate exact relay TLS443 socket"
            evidence[role] = {"incoming_pid": self.server[role], "app_executable": EXE, "connections": connections}
        (self.proofs / ((label + "-" if label else "") + "https-transport-proof.json")).write_text(json.dumps(evidence, indent=2) + "\n")
        print("PASS: actual authenticated forced-relay video/input active with native21115-21119 blocked; both peers have TLS443 registration+session sockets", flush=True)

    def registration_tls_snapshot(self):
        # With no authenticated session, exactly one incoming TLS socket is
        # the registration lane. Guarded IPC is checked on both sides of the
        # kernel snapshot so a connecting socket cannot inherit cached readiness.
        if self.query("host", "VideoConnCount") != 0:
            return None
        before = self.health("host")
        if not before:
            return None
        sockets, connections = self.app_tcp_evidence("host")
        tls = [record for record in connections
               if record["port"] == 443 and self.server["host"] in record["pids"]]
        if len(tls) != 1:
            return None
        after = self.health("host")
        if not after or self.query("host", "VideoConnCount") != 0:
            return None
        return {"incoming_pid": self.server["host"], "guarded_ipc_before": before,
                "guarded_ipc_after": after, "authenticated_sessions": 0,
                "connections": tls, "tcp_snapshot": sockets,
                "observed_monotonic": time.monotonic()}

    def fresh_registration_tls(self, baseline):
        assert baseline["incoming_pid"] == self.server["host"], "Registration baseline belongs to another incoming process"
        current = self.registration_tls_snapshot()
        if not current:
            return None
        old = {(record["local_endpoint"], record["peer_endpoint"])
               for record in baseline["connections"]}
        record = current["connections"][0]
        if (record["local_endpoint"], record["peer_endpoint"]) in old:
            return None  # An old ESTAB socket and cached OnlineStatus are insufficient.
        return current

    def active_mixed_proof(self, label=None):
        assert self.query("host", "VideoConnCount") == 1
        def continued_registration():
            packets = self.native_udp_proof((label + "-" if label else "") + "active-encrypted-video")
            return packets if packets > self.initial_udp_packets else None
        # Consent can complete before the next native registration interval.
        # Require an actual later UDP packet instead of assuming it is due now.
        host_udp = until("actual host UDP registration after native TCP outage", continued_registration, timeout=45)
        assert self.query("host", "VideoConnCount") == 1, "Mixed session closed while waiting for actual UDP registration"
        evidence = {}
        for role in self.names:
            sockets, connections = self.app_tcp_evidence(role)
            (self.proofs / role / ((label + "-" if label else "") + "active-mixed-transport-tcp.txt")).write_text(sockets)
            if role == "host":
                assert not any(21115 <= item["port"] <= 21119 for item in connections), "Host blocked native TCP transport established"
                assert any(item["port"] == 443 and self.server[role] in item["pids"] for item in connections), "Actual host incoming process has no exact HTTPS443 relay socket"
            else:
                assert any(item["port"] == 443 and self.server[role] in item["pids"] for item in connections), "Controller incoming registration has no exact HTTPS443 socket"
                assert any(item["port"] in (21117, 443) and self.server[role] not in item["pids"] for item in connections), "Controller session has no separate verified native/HTTPS relay socket"
            evidence[role] = {"incoming_pid": self.server[role], "app_executable": EXE, "connections": connections}
        control_rules = self.run("controller", ["iptables-save", "-c"], user="root").stdout
        control = re.search(r"^\[(\d+):(\d+)\] -A OUTPUT -p tcp .*--dport 21116 .*?-j ACCEPT$", control_rules, re.M)
        assert control and int(control.group(1)) > 0, "Ordinary controller request did not exercise native TCP21116 control; mixed late-fallback gap not tested"
        (self.proofs / "controller" / ((label + "-" if label else "") + "ordinary-id-native-control-tcp.txt")).write_text(control_rules)
        (self.proofs / ((label + "-" if label else "") + "mixed-transport-proof.json")).write_text(json.dumps({
            "host_registration": "native UDP21116, key-confirmed before TCP block",
            "host_udp_packets": host_udp, "host_session": "HTTPS443; native TCP21115-19 blocked",
            "controller_registration": "HTTPS443; native UDP21115-19 blocked",
            "controller_native_tcp": "ordinary-ID native TCP21116 control observed",
            "controller_control_tcp_packets": int(control.group(1)), "request": "ordinary ID, no --relay",
            "socket_owners": evidence,
        }, indent=2) + "\n")
        print("PASS: mixed transport: host native UDP registration plus HTTPS443 session; controller HTTPS registration with native TCP available; ordinary ID without --relay", flush=True)

    def active_transport_proof(self, label):
        if self.blocked:
            self.active_https_proof(label)
        elif self.mixed_transports:
            self.active_mixed_proof(label)

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
        viewer = next(iter(self.windows("controller", "Remote Desktop.*Mixel-Remote$")), None)
        assert viewer, "Actual remote viewer is absent"
        self.activate("controller", viewer)
        before_capture = self.fixture_state()
        capture_started = time.monotonic()
        self.screenshot("controller", name)
        capture_finished = time.monotonic()
        # Bind the clock to the captured image before slow pixel analysis. A
        # pre-capture snapshot can reject a current frame as a future frame.
        state = self.fixture_state()
        if before_capture["controls"] != state["controls"]:
            raise RuntimeError("Host control geometry changed across image capture")
        code = r'''from PIL import Image
import json,sys,statistics
sys.path.insert(0,'/payload')
from session_probe import decode_marker
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
# A popup can cover only the top of one bar. Require the other two to agree
# on the common top; the clock itself still has to decode and be current.
top=statistics.median(rect[1] for rect in rects)
assert sum(abs(rect[1]-top)<=2 for rect in rects)>=2,'Remote RGB tops disagree'
rects[0]=(rects[0][0],round(top),rects[0][2])
marker=MARKER
canvas=CANVAS
x,y,width=rects[0]
samples=[]
for index in range(20):
 px=round(x+(marker['x']+index*marker['cell_width']+marker['cell_width']/2-canvas['x'])*width/300)
 py=round(y+(marker['y']+marker['height']/2-canvas['y'])*width/300)
 pixels=[im.getpixel((px+dx,py+dy)) for dx in (-1,0,1) for dy in (-1,0,1)]
 samples.append([statistics.median(pixel[channel] for pixel in pixels) for channel in range(3)])
counter=decode_marker(samples,marker['counter'])
print(json.dumps({'rectangle':rects[0],'decoded_counter':counter,'marker_samples':samples}))
'''.replace("NAME", name).replace("MARKER", repr(state["marker"])).replace("CANVAS", repr(state["controls"]["canvas"]))
        decoded = json.loads(self.run("controller", ["python3", "-c", code]).stdout)
        x, y, width = decoded["rectangle"]
        current = self.fixture_state()
        if current["controls"] != state["controls"]:
            raise RuntimeError("Host control geometry changed during decoded video observation")
        canvas = state["controls"]["canvas"]
        state["decoded_marker"] = decoded
        state["video_observation"] = {
            "source_before_capture": before_capture["marker"]["counter"],
            "source_after_capture": state["marker"]["counter"],
            "source_before_monotonic": before_capture["monotonic"],
            "source_after_monotonic": state["monotonic"],
            "capture_started_monotonic": capture_started,
            "capture_finished_monotonic": capture_finished,
        }
        return (lambda hx, hy: (x + (hx - canvas["x"]) * width / 300,
                               y + (hy - canvas["y"]) * width / 300)), state

    def fresh_video(self, label):
        observations = []
        def changing():
            mapping, state = self.video_map(label)
            counter = state["decoded_marker"]["decoded_counter"]
            observations.append({"source_counter": state["marker"]["counter"],
                                 "decoded_counter": counter, "rectangle": state["decoded_marker"]["rectangle"],
                                 "capture": state["video_observation"]})
            (self.proofs / (label + "-fresh-frames.json")).write_text(json.dumps(observations, indent=2) + "\n")
            if len(observations) >= 2 and 0 < ((counter - observations[0]["decoded_counter"]) & 65535) < 120:
                return mapping, state
            return None
        return until("changing current decoded video " + label, changing)

    def input(self):
        mapping, state = self.fresh_video("video-proof")
        print("PASS: actual remote video decodes the synthetic host RGB pattern and a changing current frame marker", flush=True)
        if self.flutter_input:
            self.gui("controller", ["xdotool", "mousemove", "550", "5"])
            time.sleep(.6)
            self.click("controller", 580, 10)
            self.click("controller", 550, 23)
            self.click("controller", 612, 167)
            mapping, state = self.video_map("flutter-input-mode")
        entry = state["controls"]["entry"]
        self.click("controller", *mapping(entry["x"] + entry["width"] / 2, entry["y"] + entry["height"] / 2))
        until("actual remote entry focus", lambda: self.fixture_state()["focus"].endswith(".!entry"))
        self.gui("controller", ["xdotool", "type", "--clearmodifiers", "--delay", "60", TEXT])
        until("independent actual remote keyboard text", lambda: self.fixture_state()["text"] == TEXT)
        print("PASS: independent host Tk observer confirms exact " + ("Flutter input2" if self.flutter_input else "default native input1") + " remote keyboard text", flush=True)
        attempts = []
        def button_callback():
            actual = json.loads((self.proofs / "host/input.json").read_text())
            if actual.get("text") == TEXT and actual.get("count", 0) >= 1:
                return actual
            mapping, observed = self.video_map("mouse-target")
            assert observed["text"] == TEXT, "Exact host keyboard text changed before remote mouse callback"
            button = observed["controls"]["button"]
            target = mapping(button["x"] + button["width"] / 2, button["y"] + button["height"] / 2)
            attempts.append({"host_geometry": observed["controls"], "controller_target": target,
                             "events_before": observed["events"]})
            (self.proofs / "mouse-target-proof.json").write_text(json.dumps(attempts, indent=2) + "\n")
            self.click("controller", *target)
            return None
        state = until("real remote keyboard and mouse callback", button_callback)
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

    @staticmethod
    def rendered_row(tsv, bounds, scale=3):
        """Require the exact visible synthetic filename; return its text center."""
        matches = []
        for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
            if row.get("text", "").strip() == FILE:
                matches.append((bounds[0] + (int(row["left"]) + int(row["width"]) / 2) / scale,
                                bounds[1] + (int(row["top"]) + int(row["height"]) / 2) / scale))
        assert len(matches) <= 1, "Rendered filename is ambiguous in the actual file pane"
        return matches[0] if matches else None

    def file_row(self, side, window, label, refresh=False):
        box = self.geometry("controller", window)
        # These bounds cover only the real file pane's first row, excluding
        # the transfer-complete job notification and the other pane.
        offset = 500 if side == "remote" else 0
        bounds = (box["X"] + offset + 20, box["Y"] + 238,
                  box["X"] + offset + 475, box["Y"] + 280)
        next_refresh = 0
        def observe():
            nonlocal next_refresh
            self.screenshot("controller", label)
            code = "from PIL import Image;im=Image.open('/proofs/" + label + ".png').crop(" + repr(bounds) + ");im.resize((im.width*3,im.height*3)).save('/proofs/" + label + "-row.png')"
            self.run("controller", ["python3", "-c", code])
            actual = self.run("controller", ["tesseract", "/proofs/" + label + "-row.png", "stdout", "--psm", "6", "tsv"]).stdout
            (self.proofs / "controller" / (label + "-rendered.tsv")).write_text(actual)
            point = self.rendered_row(actual, bounds)
            if point:
                return point
            if refresh and time.monotonic() >= next_refresh:
                # Actual visible refresh control, never a file-list IPC write.
                self.click("controller", box["X"] + (935 if side == "remote" else 449), box["Y"] + 140)
                next_refresh = time.monotonic() + 2
            return None
        return until("actual rendered " + side + " row " + FILE, observe)

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
        self.click("controller", *self.file_row("source", window, "file-upload-ready"))
        self.screenshot("controller", "file-upload-selected")
        self.click("controller", 424, 232)
        until("actual upload complete SHA256", lambda: self.run("host", ["sha256sum", remote + "/" + FILE], check=False).stdout.split()[:1] == [expected_hash])
        self.screenshot("controller", "file-upload-complete")
        self.path(130, roundtrip)
        self.click("controller", *self.file_row("remote", window, "file-download-ready", refresh=True))
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
        self.fresh_video("service-restart-video")
        self.active_transport_proof("service-restart")
        self.disconnect()

    def drop(self):
        self.stop_controller_gui()
        registration_before = None
        if self.blocked:
            registration_before = until("single key-confirmed incoming HTTPS registration socket at auth0", self.registration_tls_snapshot)
        self.connect("before-network-drop")
        if registration_before:
            _sockets, records = self.app_tcp_evidence("host")
            baseline = registration_before["connections"][0]
            assert any(record["local_endpoint"] == baseline["local_endpoint"]
                       and record["peer_endpoint"] == baseline["peer_endpoint"]
                       and self.server["host"] in record["pids"] for record in records), "Incoming registration changed before the actual network drop"
            registration_before["baseline_tuple_verified_immediately_before_drop"] = True
            (self.proofs / "before-network-drop-registration.json").write_text(json.dumps(registration_before, indent=2) + "\n")
        self.process_snapshot("host", "before-network-drop")
        command(["docker", "network", "disconnect", self.network, self.names["host"]])
        try:
            until("dropped transport disconnects authenticated session", lambda: self.query("host", "VideoConnCount") == 0, timeout=60)
            # End the outgoing session while the host is still offline. Its
            # automatic retry otherwise opens an unauthorized relay lane before
            # the independent auth0 registration snapshot can select one lane.
            self.stop_controller_gui()
        finally:
            command(["docker", "network", "connect", self.network, self.names["host"]])
        self.process_snapshot("host", "after-network-restored")
        # Require the same actual incoming process, attended kernel guard and
        # key-confirmed endpoint through several separate IPC observations.
        ready_since = None
        registration_after = None
        ready_tuple = None
        def recovered():
            nonlocal ready_since, registration_after, ready_tuple
            try:
                if registration_before:
                    registration_after = self.fresh_registration_tls(registration_before)
                    healthy = registration_after is not None
                else:
                    healthy = self.health("host")
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
                ready_since = None
                raise
            if healthy:
                if registration_after:
                    record = registration_after["connections"][0]
                    current_tuple = (record["local_endpoint"], record["peer_endpoint"])
                    if current_tuple != ready_tuple:
                        ready_since = None
                        ready_tuple = current_tuple
                if ready_since is None:
                    ready_since = time.monotonic()
                return time.monotonic() - ready_since >= 2
            ready_since = None
            return False
        until("same native PID and guarded IPC registered stably after actual network drop", recovered, timeout=90)
        if registration_after:
            (self.proofs / "network-recovered-registration.json").write_text(json.dumps({
                "baseline": registration_before, "fresh": registration_after,
                "same_incoming_pid": self.server["host"], "stable_seconds": time.monotonic() - ready_since,
                "fresh_registration_confirmed_before_first_controller_request": True,
            }, indent=2) + "\n")
        self.process_snapshot("host", "network-recovered")
        self.stop_controller_gui()
        self.connect("network-reconnect")
        self.fresh_video("network-reconnect-video")
        self.active_transport_proof("network-reconnect")
        self.disconnect()
        print("PASS: real network loss/recovery requires another actual customer Accept", flush=True)

    def capture(self):
        errors = []
        for role in self.created:
            operations = [lambda: self.screenshot(role, "final-desktop-state", timeout=2),
                          lambda: self.process_snapshot(role, "final", timeout=2, capture_logs=True)]
            if self.automatic_transport:
                operations.append(lambda: self.run(role, ["bash", "-c", "iptables-save -c >/proofs/native-port-block.txt"], user="root", timeout=2))
            for operation in operations:
                try:
                    operation()
                except Exception as error:
                    errors.append(role + " capture: " + str(error))
        return errors

    def cleanup(self, interrupted=False):
        errors = []
        if not interrupted:
            try:
                errors.extend(self.capture())
            except Exception as error:
                errors.append("Capture: " + str(error))
        for role in self.created:
            try:
                result = command(["docker", "rm", "-f", self.names[role]], check=False, timeout=4)
                if result.returncode:
                    errors.append(role + " container removal failed")
            except Exception as error:
                errors.append(role + " cleanup: " + str(error))
        try:
            result = command(["docker", "image", "rm", self.image], check=False, timeout=3)
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
        shutil.copyfile(Path(__file__), payload / "session_probe.py")
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


class PasswordSession(Session):
    """Prove the same valid password before and after a real attended handoff."""
    def firewall(self, role, after_registration=False):
        super().firewall(role, after_registration)
        if role == "host" and not after_registration:
            preferences = 'approve-mode = "password"\nverification-method = "use-permanent-password"\n'
            code = "from pathlib import Path;p=Path('/home/guest/.config/mixel-remote/Mixel-Remote2.toml');p.write_text(p.read_text()+" + repr(preferences) + ")"
            self.run(role, ["python3", "-c", code])

    def start_gui(self, role):
        self.gui_pid[role] = self.launch(role, [])
        until(role + " actual ordinary main window", lambda: self.windows(role, "^Mixel-Remote$"))

    def health(self, role):
        assert self.query(role, "Config", ["mixel-support-invite-attended", None]) == ["mixel-support-invite-attended", ""]
        assert self.run(role, ["cat", "/tmp/Mixel-Remote/ipc.pid"]).stdout.strip() == str(self.server[role])
        options = self.query(role, "Options")
        for key, value in {"custom-rendezvous-server": "rs.mixel.ch", "relay-server": "rs.mixel.ch", "key": self.expected_pin,
                           "approve-mode": "password", "verification-method": "use-permanent-password"}.items():
            assert options.get(key) == value
        return self.online(role)

    def saved_password_consent(self, manifest):
        password = "MixelSynthetic" + secrets.token_hex(12)
        result = self.run("host", [EXE, "--password", password], user="root")
        assert "Done!" in result.stdout, "Supported administrative permanent-password setter failed"
        until("synthetic permanent password stored in actual incoming server",
              lambda: self.query("host", "Config", ["permanent-password", None])[1] == password)
        saved = self.query("host", "Options")
        assert saved["approve-mode"] == "password" and saved["verification-method"] == "use-permanent-password"
        assert "mixel-support-invite-attended" not in saved
        manifest["password_set_via_supported_cli"] = True
        print("PASS: exact binary administrative --password setter stores a random synthetic permanent password", flush=True)
        # The positive control uses the real login path, without customer
        # Accept or any IPC authorization write.
        self.launch("controller", ["--connect", self.ids["host"], "--password", password])
        until("valid password baseline automatic authorization", lambda: self.query("host", "VideoConnCount") == 1)
        viewer = until("baseline real remote viewer", lambda: next(iter(self.windows("controller", "Remote Desktop.*Mixel-Remote$")), None))
        self.activate("controller", viewer)
        self.gui("controller", ["wmctrl", "-ir", viewer, "-b", "add,fullscreen"])
        self.fresh_video("valid-password-baseline-video")
        self.screenshot("host", "valid-password-baseline-authorized")
        manifest["valid_password_baseline_autoauthorized_without_accept"] = True
        print("PASS: the exact saved password automatically authorizes the ordinary app (auth1), with changing current video and no Accept click", flush=True)
        self.disconnect()
        self.stop_controller_gui()
        original_windows = set(self.windows("host", "^Mixel-Remote$", False))
        assert original_windows, "Ordinary host window disappeared before support handoff"
        handoff_pid = self.launch("host", [URI])
        def handoff_exit():
            statuses = self.process_snapshot("host", "warm-support-handoff")["exit_statuses"]
            return next((status for status in statuses if status["pid"] == handoff_pid), None)
        completed = until("warm URI sender exits after DBus acknowledgment", handoff_exit)
        assert completed["wait_status"] == 0, "Warm URI sender failed"
        assert set(self.windows("host", "^Mixel-Remote$", False)) == original_windows, "Warm URI replaced the original ordinary host window"
        until("original ordinary GUI owns attended kernel lease after sender exit", lambda: Session.health(self, "host"))
        manifest["warm_uri_handoff"] = {"sender_pid": handoff_pid, "sender_exit_status": 0, "original_gui_pid": self.gui_pid["host"],
                                        "original_window_ids": sorted(original_windows), "same_original_gui_kernel_lease_verified_after_sender_exit": True}
        print("PASS: warm support URI sender exits cleanly; the same original ordinary GUI/window owns the attended kernel lease", flush=True)
        until("actual warm support URI v2 guard", lambda: self.query("host", "Config", ["mixel-support-invite-attended", None]) == ["mixel-support-invite-attended", "attended-runtime-v2"])
        assert self.query("host", "Options") == saved, "Attended URI changed persistent access preferences"
        assert self.query("host", "Config", ["permanent-password", None])[1] == password
        self.launch("controller", ["--connect", self.ids["host"], "--password", password])
        self.pending_accept("valid-password-attended")
        started = time.monotonic()
        observations = []
        while time.monotonic() - started < 12:
            count = self.query("host", "VideoConnCount")
            assert count == 0, "Valid permanent password bypassed customer Accept"
            observations.append({"seconds": round(time.monotonic() - started, 3), "authenticated_remote_count": count})
            (self.proofs / "saved-password-auth0-observations.json").write_text(json.dumps(observations, indent=2) + "\n")
            time.sleep(.5)
        assert self.query("host", "VideoConnCount") == 0, "Valid password bypassed Accept after twelve seconds"
        observations.append({"seconds": round(time.monotonic() - started, 3), "authenticated_remote_count": 0})
        (self.proofs / "saved-password-auth0-observations.json").write_text(json.dumps(observations, indent=2) + "\n")
        self.screenshot("controller", "valid-password-attended-before-accept")
        self.pending_accept("valid-password-attended-after-wait")
        manifest["attended_auth0_observations"] = observations
        manifest["attended_unauthorized_duration_seconds"] = time.monotonic() - started
        print("PASS: the same proven valid password remains unauthorized for at least twelve seconds with visible blue customer Accept (auth0)", flush=True)
        self.connect("valid-password-attended", already_requested=True)
        self.fresh_video("valid-password-attended-accepted-video")
        self.active_https_proof("valid-password-attended")
        assert self.query("host", "Options") == saved, "Customer Accept changed saved access preferences"
        assert self.query("host", "Config", ["permanent-password", None])[1] == password
        manifest["actual_accept_then_authorized_video"] = True
        manifest["saved_preferences_and_password_preserved"] = True
        self.disconnect()
        manifest["actual_customer_disconnect_auth0"] = True
        print("PASS: actual customer Accept enables native encrypted current video; saved access choices and password remain unchanged", flush=True)


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
    parser.add_argument("--saved-password-consent", action="store_true", help="Native isolated proof: a proven valid permanent password cannot bypass real attended Accept")
    parser.add_argument("--artifact-root", type=Path, help="Collected root containing run metadata, provenance and the exact Linux DEB")
    parser.add_argument("--artifact-source-commit", help="Explicit full application source SHA, independent of updated QA source")
    parser.add_argument("--expected-sha256", help="Exact expected installer SHA256 from the collected Linux artifact")
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
    application_provenance = None
    provenance_requested = (args.artifact_root, args.artifact_source_commit, args.expected_sha256)
    if args.saved_password_consent:
        if not args.require_native or not args.native_blocked or isolated is None or args.mixed_transports or args.flutter_input:
            raise SystemExit("Saved-password proof requires native amd64, an isolated blocked-native relay and default input mode")
        if not all(provenance_requested):
            raise SystemExit("Saved-password proof requires exact collected artifact provenance")
    if any(provenance_requested):
        if not all(provenance_requested):
            raise SystemExit("All collected artifact provenance parameters are required together")
        application_provenance = verify_application_artifact(args.artifact_root, args.deb, args.artifact_run_id,
                                                            args.artifact_source_commit, args.expected_sha256)
    args.proofs = args.proofs.resolve()
    args.proofs.mkdir(parents=True, exist_ok=True)
    if any(args.proofs.iterdir()):
        raise SystemExit("Proof output directory must be empty")
    revision = command(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=False).stdout.strip()
    manifest = {"artifact": args.deb.name, "sha256": hashlib.sha256(args.deb.read_bytes()).hexdigest(), "host_architecture": platform.machine(), "native_ports_blocked": args.native_blocked, "input_source": "flutter2" if args.flutter_input else "native1", "result": "failed", "relay_environment": "isolated fixture (test CA and test public pin)" if isolated else "live Mixel relay", "forced_relay_request": "automatic transport selection (no --relay)" if args.native_blocked else "explicit --relay", "harness_revision": revision or "unavailable", "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "run_id": os.environ.get("GITHUB_RUN_ID"), "artifact_run_id": args.artifact_run_id or os.environ.get("GITHUB_RUN_ID")}
    if isolated:
        manifest["isolated_relay"] = {"network": isolated["network"], "private_address": isolated["address"], "public_pin_sha256": hashlib.sha256(isolated["pin"].encode()).hexdigest(), "ca_sha256": hashlib.sha256(isolated["ca"].read_bytes()).hexdigest()}
    manifest["mixed_transports"] = args.mixed_transports
    if application_provenance:
        manifest["application_artifact_provenance"] = application_provenance
        manifest["application_source_baseline"] = application_provenance["application_source_baseline"]
    qa_sources = ["scripts/test-support-session-linux.py", "scripts/test-support-launch-linux.py",
                  "scripts/test-support-session-https-linux.py", "scripts/e2e/linux-fixture.py",
                  "scripts/e2e/start-linux-desktop.sh", "scripts/e2e/Dockerfile.linux"]
    manifest["qa_source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in qa_sources}
    manifest["qa_tracked_changes"] = command(["git", "-C", str(ROOT), "status", "--porcelain", "--", *qa_sources], check=False).stdout.splitlines()
    if args.mixed_transports:
        manifest["forced_relay_request"] = "automatic mixed transport selection (no --relay)"
        manifest["transport_policy"] = {"host": "native UDP registration, TCP21115-19 blocked after initial registration", "controller": "UDP21115-19 blocked, native TCP available"}
    session_type = PasswordSession if args.saved_password_consent else Session
    session = session_type(args.proofs, args.deb, args.flutter_input, args.native_blocked, isolated, args.mixed_transports)
    if args.saved_password_consent:
        manifest.update({"kind": "native-valid-permanent-password-attended-consent-proof", "no_ipc_authorize": True,
                         "password": "random synthetic value retained only in temporary peer HOME",
                         "execution": "native Linux amd64", "production_mutations": False})
    with tempfile.TemporaryDirectory(prefix="mixel-real-session-") as temporary:
        try:
            digest = session.setup(Path(temporary))
            if args.saved_password_consent:
                session.saved_password_consent(manifest)
            else:
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
            print("Result: native valid-password attended consent session passed" if args.saved_password_consent else "Result: real Linux consent/video/keyboard/mouse/clipboard/file/restart/reconnect session passed", flush=True)
        finally:
            try:
                interrupted = isinstance(sys.exc_info()[1], (SystemExit, KeyboardInterrupt, subprocess.TimeoutExpired))
                cleanup_errors = session.cleanup(interrupted=interrupted)
                manifest["owned_resources_cleaned"] = not cleanup_errors
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
