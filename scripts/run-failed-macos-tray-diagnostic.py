#!/usr/bin/env python3
"""Run the frozen tray fixture with additive, content-free privacy observations."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import types
import sys

MAX_OBSERVATIONS = 64
PRIVACY_CATEGORIES = {"synthetic-invite", "synthetic-api-key", "support-uri-prefix",
                      "invite-shape", "bearer-pattern", "credential-assignment"}


def privacy_categories(desktop, data):
    categories = []
    if desktop.unix.TOKEN.encode() in data or desktop.WARM_TOKEN.encode() in data:
        categories.append("synthetic-invite")
    if desktop.unix.API_KEY.encode() in data:
        categories.append("synthetic-api-key")
    if b"mixel-remote://support/?invite=" in data:
        categories.append("support-uri-prefix")
    if re.search(rb"(?i)inv_[0-9a-f-]{36}", data):
        categories.append("invite-shape")
    if re.search(rb"(?i)bearer\s+\S+", data):
        categories.append("bearer-pattern")
    if re.search(rb"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization)[\"']?\s*[=:]\s*[\"']?[^\s\"',}]+", data):
        categories.append("credential-assignment")
    return categories


def observe_privacy(desktop):
    original = desktop.privacy_issue
    facts = {"schema": 1, "originalResultPreserved": True, "matchedObservations": [],
             "observationLimitReached": False}
    seen = set()
    def wrapped(data):
        result = original(data)
        if result:
            value = {"categories": privacy_categories(desktop, data),
                     "freshBytesSha256": hashlib.sha256(data).hexdigest(), "freshBytes": len(data)}
            identity = (value["freshBytesSha256"], value["freshBytes"], tuple(value["categories"]))
            if identity not in seen:
                if len(facts["matchedObservations"]) < MAX_OBSERVATIONS:
                    facts["matchedObservations"].append(value)
                    seen.add(identity)
                else:
                    facts["observationLimitReached"] = True
        return result
    desktop.privacy_issue = wrapped
    return facts


def finite_positive(value):
    if type(value) not in (int, float):
        return False
    try:
        return value > 0 and math.isfinite(value)
    except OverflowError:
        return False


def observe_launchservices(desktop):
    original = desktop.helper_request
    facts = {"schema": 1, "originalResultPreserved": True, "launchObservations": [],
             "observationLimitReached": False}
    def wrapped(helper, action, *args, **kwargs):
        result = original(helper, action, *args, **kwargs)
        if action in ("tray", "launch") and isinstance(result, dict):
            pid, launch = result.get("pid"), result.get("launchTime")
            if type(pid) is int and 0 < pid < 2**31 and finite_positive(launch):
                if len(facts["launchObservations"]) < 4:
                    facts["launchObservations"].append({"action": action, "pid": pid, "launchTime": launch})
                else:
                    facts["observationLimitReached"] = True
        return result
    desktop.helper_request = wrapped
    return facts



GUI_WINDOW_SECONDS = 14
MAX_GUI_OBSERVATIONS = 64
GUI_REFUSALS = {"isolated-context", "noncanonical-app", "gui-owner-changed",
                "authenticated-connection", "ownership-unavailable", "observation-operation-failed",
                "server-identity-changed", "service-health-unavailable"}


def probe_summary(result, app):
    """Allowlisted state only; the original response object is never modified."""
    response = result if isinstance(result, dict) else {}
    launch = response.get("launchTime")
    if launch is None:
        launch_state = "missing"
    elif type(launch) in (int, float) and launch >= 0 and finite_positive(launch + 1):
        launch_state = "finite"
    else:
        launch_state = "invalid"
        launch = None
    try:
        bundle_match = isinstance(response.get("bundlePath"), str) and Path(response["bundlePath"]).resolve() == app.resolve()
    except (OSError, ValueError, RuntimeError):
        bundle_match = False
    return {**{field: response.get(field) if type(response.get(field)) is bool else None
               for field in ("running", "visible", "hidden")},
            "launchTime": launch, "launchTimeState": launch_state, "bundleMatch": bundle_match}


def bounded_gui_probe(helper, pid, app, timeout):
    # The same exact compiled Swift probe, with a deadline only for additive
    # observations. Original desktop.probe/helper_request calls remain untouched.
    result = subprocess.run([str(helper), "probe", str(pid)], capture_output=True,
                            text=True, encoding="utf-8", timeout=timeout)
    if len(result.stdout.encode()) + len(result.stderr.encode()) > 4096:
        raise RuntimeError("GUI observation response exceeded its bound")
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("GUI observation response was invalid") from None
    if result.returncode or not isinstance(value, dict) or "error" in value:
        raise RuntimeError("GUI observation probe failed")
    # A missing NSRunningApplication reports an empty bundle path. Record
    # its bundleMatch=false without authorizing or replacing the original
    # desktop.probe bundle refusal. Kernel/executable ownership was required.
    return value



class DeadlineSocket:
    """Only shorten additive observation I/O; original socket guards are intact."""
    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline
        self.timeout = 2

    def arm(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("GUI observation deadline elapsed")
        self.connection.settimeout(min(self.timeout, remaining))

    def settimeout(self, value):
        self.timeout = value
        self.arm()

    def connect(self, address):
        self.arm()
        return self.connection.connect(address)

    def sendall(self, data):
        self.arm()
        return self.connection.sendall(data)

    def recv(self, size):
        self.arm()
        return self.connection.recv(size)

    def getsockopt(self, *args):
        return self.connection.getsockopt(*args)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        return self.connection.__exit__(*args)


def bounded_function(function, deadline, replacements=None):
    # Reuse the exact original code object, defaults and closure. No module
    # globals or original callable are mutated; only this private copy receives
    # the read-only deadline socket and already-bound original guard functions.
    namespace = dict(function.__globals__)
    socket_module = namespace.get("socket")
    if socket_module is not None:
        socket_factory = socket_module.socket
        namespace["socket"] = types.SimpleNamespace(**{
            **vars(socket_module),
            "socket": lambda *args, **kwargs: DeadlineSocket(socket_factory(*args, **kwargs), deadline)})
    namespace.update(replacements or {})
    copied = types.FunctionType(function.__code__, namespace, function.__name__,
                                function.__defaults__, function.__closure__)
    copied.__kwdefaults__ = function.__kwdefaults__
    return copied


def bounded_read_only_guards(tray, original_owner, deadline):
    replacements = {}
    if hasattr(tray, "query"):
        replacements["query"] = bounded_function(tray.query, deadline)
    connections = bounded_function(tray.no_connections, deadline, replacements)
    replacements["no_connections"] = connections
    health = bounded_function(tray.health, deadline, replacements) if hasattr(tray, "health") else None
    return bounded_function(original_owner, deadline), connections, health


def observe_gui_identity(tray, launch_facts=None):
    """Observe original GUI probes and a refused identity without authorizing it."""
    desktop = tray.desktop
    original_probe = desktop.probe
    original_owner = tray.gui_owner
    original_identity = tray.launch_identity
    context = {}
    facts = {"schema": 1, "originalResultPreserved": True, "probeObservations": [], "serviceObservations": [],
             "observationLimitReached": False,
             "postFailureWindow": {"attempted": False, "deadlineSeconds": GUI_WINDOW_SECONDS,
                                   "elapsedSeconds": 0, "refusalCategory": None}}

    def record(pid, app, result, origin, elapsed=0, status="response"):
        if len(facts["probeObservations"]) >= MAX_GUI_OBSERVATIONS:
            facts["observationLimitReached"] = True
            return
        facts["probeObservations"].append({"pid": pid, "origin": origin, "status": status,
            "observedUnixTime": time.time(), "elapsedSeconds": elapsed,
            **probe_summary(result, app)})

    def owner(app, server_pid):
        pid = original_owner(app, server_pid)
        context.update(app=app, server_pid=server_pid, gui_pid=pid)
        return pid

    def probe(helper, pid, app):
        try:
            result = original_probe(helper, pid, app)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            if pid == context.get("gui_pid"):
                record(pid, app, None, "fixture", status="probe-unavailable")
            raise
        if pid == context.get("gui_pid"):
            record(pid, app, result, "fixture")
        return result


    def observe_service(helper, app, pid, deadline, elapsed, owned_owner, owned_health):
        if len(facts["serviceObservations"]) >= MAX_GUI_OBSERVATIONS or deadline - time.monotonic() < 10:
            return
        server_pid = context["server_pid"]
        launches = launch_facts.get("launchObservations", []) if isinstance(launch_facts, dict) else []
        retained = next((row.get("launchTime") for row in launches
                         if row.get("action") == "tray" and row.get("pid") == server_pid), None)
        observation = {"pid": server_pid, "observedUnixTime": time.time(),
                       "elapsedSeconds": elapsed, "retainedServerLaunchTime": retained,
                       "serverLaunchMatch": False, "attendedServiceReady": None,
                       "status": "server-identity-unavailable"}
        try:
            if not finite_positive(retained):
                observation["retainedServerLaunchTime"] = None
                return
            # Reserved worst-case budget: kernel URL owner <=1s, Swift probe
            # <=1s, original health's four exact-owner IPC reads <=8s.
            tray.isolated_ci()
            if owned_owner(app, server_pid) != pid:
                observation["status"] = "ownership-unavailable"
                facts["postFailureWindow"]["refusalCategory"] = "gui-owner-changed"
                return
            state = bounded_gui_probe(helper, server_pid, app, min(1, deadline - time.monotonic()))
            summary = probe_summary(state, app)
            if summary["running"] is not True or not summary["bundleMatch"] or summary["launchTime"] != retained:
                facts["postFailureWindow"]["refusalCategory"] = "server-identity-changed"
                return
            observation["serverLaunchMatch"] = True
            ready = owned_health(server_pid, app, True)
            if type(ready) is not bool:
                observation["status"] = "health-unavailable"
                facts["postFailureWindow"]["refusalCategory"] = "service-health-unavailable"
                return
            observation["attendedServiceReady"] = ready
            observation["status"] = "response"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            observation["status"] = "health-unavailable"
            facts["postFailureWindow"]["refusalCategory"] = "service-health-unavailable"
        finally:
            facts["serviceObservations"].append(observation)

    def post_failure(helper, app, pid):
        window = facts["postFailureWindow"]
        window["attempted"] = True
        start = time.monotonic()
        deadline = start + GUI_WINDOW_SECONDS
        try:
            owned_owner, owned_connections, owned_health = bounded_read_only_guards(tray, original_owner, deadline)
            while deadline - time.monotonic() >= 4:
                try:
                    tray.isolated_ci()
                except RuntimeError:
                    window["refusalCategory"] = "isolated-context"
                    break
                if app.absolute() != app.resolve(strict=True):
                    window["refusalCategory"] = "noncanonical-app"
                    break
                try:
                    if owned_owner(app, context["server_pid"]) != pid:
                        window["refusalCategory"] = "gui-owner-changed"
                        break
                    if not owned_connections(context["server_pid"], app):
                        window["refusalCategory"] = "authenticated-connection"
                        break
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    window["refusalCategory"] = "ownership-unavailable"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    result = bounded_gui_probe(helper, pid, app, min(1, remaining))
                    record(pid, app, result, "post-failure", time.monotonic() - start)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    record(pid, app, None, "post-failure", time.monotonic() - start,
                           status="probe-unavailable")
                observe_service(helper, app, pid, deadline, time.monotonic() - start, owned_owner, owned_health)
                if window["refusalCategory"] is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(0.5, remaining))
        except Exception:
            # Additive measurement cannot replace or turn the primary refusal
            # into success. No termination or connection operation occurs here.
            window["refusalCategory"] = "observation-operation-failed"
        finally:
            window["elapsedSeconds"] = time.monotonic() - start

    def identity(helper, app, pid):
        try:
            return original_identity(helper, app, pid)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            if (pid == context.get("gui_pid") and app == context.get("app")
                    and str(error) == "Tray fixture process has no retained launch identity"
                    and not facts["postFailureWindow"]["attempted"]):
                post_failure(helper, app, pid)
            raise

    desktop.probe = probe
    tray.gui_owner = owner
    tray.launch_identity = identity
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    args = parser.parse_args(argv)
    args.proofs = args.proofs.absolute()
    try:
        parent = args.proofs.parent.lstat()
        fresh = (args.proofs.parent.resolve(strict=True) == args.proofs.parent
                 and stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                 and not os.path.lexists(args.proofs))
    except OSError:
        fresh = False
    if not fresh:
        raise RuntimeError("Privacy proof destination must be new with an owned canonical parent")
    source = Path(__file__).resolve().parent / "test-support-launch-macos-tray.py"
    spec = importlib.util.spec_from_file_location("mixel_frozen_tray_diagnostic", source)
    tray = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tray)
    facts = observe_privacy(tray.desktop)
    launch_facts = observe_launchservices(tray.desktop)
    gui_facts = observe_gui_identity(tray, launch_facts)
    original_argv = sys.argv
    try:
        sys.argv = [str(source), "--app", str(args.app), "--proofs", str(args.proofs)]
        return tray.main()
    finally:
        sys.argv = original_argv
        original_proof = args.proofs / "tray-runtime-safe-facts.json"
        if args.proofs.exists() and os.path.lexists(original_proof):
            attributes = args.proofs.lstat()
            current_parent = args.proofs.parent.lstat()
            proof = original_proof.lstat()
            if (not stat.S_ISDIR(attributes.st_mode) or attributes.st_uid != os.getuid()
                    or (current_parent.st_dev, current_parent.st_ino) != (parent.st_dev, parent.st_ino)
                    or not stat.S_ISREG(proof.st_mode) or proof.st_uid != os.getuid() or proof.st_nlink != 1):
                raise RuntimeError("Privacy observation directory is not owned")
            for name, value in (("privacy-match-safe-facts.json", facts),
                                ("launch-observation-safe-facts.json", launch_facts),
                                ("gui-probe-safe-facts.json", gui_facts)):
                with (args.proofs / name).open("x", encoding="utf-8") as output:
                    os.chmod(output.name, 0o600)
                    json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
                    output.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
