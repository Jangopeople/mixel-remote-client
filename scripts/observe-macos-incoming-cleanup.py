#!/usr/bin/env python3
"""Bounded, observational Mac incoming-cleanup adjunct; never cleanup authority.

Native provenance: Darwin SDK sys/proc_info.h PROC_UID_ONLY=4,
PROC_PIDTBSDINFO=3 and the 136-byte proc_bsdinfo layout; libproc.h
proc_listpids/proc_pidinfo/proc_pidpath; sys/un.h LOCAL_PEERPID=2;
getpeereid is also used by the exact producer's GUI IPC verifier. A connected
kernel peer is not proof of every holder of an inherited/shared listener FD.
The backend keyword is a source/mock seam. Production uses the gated backend.
"""
import ctypes
import errno
import math
import os
from pathlib import Path
import posixpath
import socket
import stat
import struct
import sys
import time

SCOPE = "macos-active-incoming-cleanup-observation-v1"
BUDGET = 1.5
OFFSETS = (0.0, 0.45, 0.9)
PID_BYTES = 16_384
PROC_UID_ONLY = 4
MAX_MEMBERS = 32
SOCKET_TIMEOUT = 0.2
MAX_PID = 0x7fffffff
MAX_UID = 0xffffffff
MAX_TIME = 0x1fffffffffffff
PROCESS_STATES = frozenset(("stable", "kernel_only", "missing", "unavailable", "malformed", "changed", "reused", "foreign_uid", "retained_mismatch", "deadline"))
PATH_STATES = frozenset(("main", "service", "other", "unavailable"))
PATH_OBSERVATIONS = frozenset(("available", "missing", "permission", "malformed", "unavailable"))
PEER_STATES = frozenset(("connected", "not_connected", "unavailable", "malformed", "changed", "deadline"))
REASONS = frozenset(("none", "absent", "refused", "timeout", "permission", "failed", "malformed", "changed", "deadline", "truncated", "member_limit", "identity_unavailable", "unsafe_socket"))


class _Deadline(Exception):
    pass


class _Malformed(Exception):
    pass


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _process(pid=None, state="unavailable"):
    return {"state": state, "pid": pid, "ppid": None, "uid": None, "euid": None,
            "ruid": None, "svuid": None, "status": None, "startSec": None,
            "startUsec": None, "executable": "unavailable", "pathState": "unavailable",
            "retainedBirthMatches": None}


def _peer(state="unavailable", reason="failed"):
    return {"state": state, "reason": reason, "pid": None,
            "credentialsState": "unavailable", "uid": None, "gid": None,
            "socketIdentitySameAsBefore": None,
            "process": _process()}


def _inventory(reason="failed", attempted=False):
    return {"state": "unavailable", "reason": reason, "attempted": attempted,
            "enumerated": None, "examined": 0, "omitted": 0,
            "mayBeIncomplete": True, "members": []}


def _sample(index, elapsed=0):
    return {"index": index, "elapsedMs": elapsed, "state": "skipped",
            "tray": _process(), "gui": _process(), "peer": _peer(),
            "inventory": _inventory()}


def _facts():
    return {"schema": 1, "scope": SCOPE, "outcome": "failed", "error": "unexpected",
            "elapsedMs": 0, "cleanupAuthority": False, "fdOwnershipEstablished": False,
            "recoveryEstablished": False, "samples": [_sample(i) for i in range(3)]}


class _NativeBackend:
    """Only fixed-buffer readonly APIs and one bounded socket connection."""
    def __init__(self):
        self.library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.library.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
        self.library.proc_pidinfo.restype = ctypes.c_int
        self.library.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        self.library.proc_pidpath.restype = ctypes.c_int
        self.library.proc_listpids.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int)
        self.library.proc_listpids.restype = ctypes.c_int
        self.system = ctypes.CDLL(None, use_errno=True)
        self.system.getpeereid.argtypes = (ctypes.c_int, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32))
        self.system.getpeereid.restype = ctypes.c_int

    now = staticmethod(time.monotonic)
    wall = staticmethod(time.time)
    sleep = staticmethod(time.sleep)
    uid = staticmethod(os.getuid)

    def _buffer_call(self, method, size, *arguments):
        buffer = ctypes.create_string_buffer(size)
        ctypes.set_errno(0)
        count = method(*arguments, buffer, size)
        return count, buffer.raw, ctypes.get_errno()

    def bsd(self, pid):
        return self._buffer_call(self.library.proc_pidinfo, 136, pid, 3, 0)

    def path(self, pid):
        return self._buffer_call(self.library.proc_pidpath, 4096, pid)

    def pids(self, uid):
        # No sizing query, retry, growth, argv, AppKit or other process inventory.
        if not _integer(uid, 0, MAX_UID):
            raise ValueError("Invalid fixed UID enumeration")
        return self._buffer_call(self.library.proc_listpids, PID_BYTES, PROC_UID_ONLY, uid)

    def socket_identity(self, endpoint):
        attributes = endpoint.lstat()
        return (attributes.st_mode, attributes.st_uid, attributes.st_dev,
                attributes.st_ino, attributes.st_size, attributes.st_mtime_ns,
                attributes.st_ctime_ns)

    def connect(self, endpoint, timeout):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(timeout)
            connection.connect(str(endpoint))
            return connection
        except BaseException:
            connection.close()
            raise

    def credentials(self, connection):
        uid, gid = ctypes.c_uint32(MAX_UID), ctypes.c_uint32(MAX_UID)
        ctypes.set_errno(0)
        result = self.system.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid))
        if result != 0:
            raise OSError(ctypes.get_errno(), "Peer credentials unavailable")
        return uid.value, gid.value


class _Context:
    def __init__(self, backend, expected):
        self.backend = backend
        self.start = backend.now()
        if type(self.start) not in (int, float) or not math.isfinite(self.start) or self.start < 0:
            raise ValueError("Invalid observation clock")
        self.last = self.start
        self.deadline = self.start + BUDGET
        self.expected = expected
        self.uid = backend.uid()
        if not _integer(self.uid, 0, MAX_UID):
            raise ValueError("Invalid observation UID")

    def clock(self):
        now = self.backend.now()
        if type(now) not in (int, float) or not math.isfinite(now) or now < self.last:
            raise ValueError("Invalid observation clock")
        self.last = now
        return now

    def check(self):
        if self.clock() >= self.deadline:
            raise _Deadline()

    def call(self, operation, *arguments):
        self.check()
        result = operation(*arguments)
        self.check()
        return result

    def elapsed(self):
        return min(0x7fffffff, max(0, int((self.last - self.start) * 1000)))


def _record(ctx, pid):
    count, raw, result_errno = ctx.call(ctx.backend.bsd, pid)
    if not _integer(count, -1, 136) or type(raw) is not bytes or len(raw) != 136 or not _integer(result_errno, 0, 0x7fffffff):
        raise _Malformed()
    if count == 0 and result_errno == errno.ESRCH:
        return None
    if count != 136:
        if count in (-1, 0):
            raise OSError(result_errno, "Process record unavailable")
        raise _Malformed()
    if result_errno:
        raise _Malformed()
    fields = struct.unpack("=12I16s32s5Ii2Q", raw)
    sec, usec = fields[-2:]
    wall = ctx.call(ctx.backend.wall)
    if (not _integer(fields[3], 1, MAX_PID) or fields[3] != pid
            or not _integer(fields[4], 0, MAX_PID) or fields[1] not in (1, 2, 3, 4, 5)
            or not _integer(sec, 1, MAX_TIME) or type(wall) not in (int, float)
            or not math.isfinite(wall) or sec > wall + 5 or not _integer(usec, 0, 999_999)):
        raise _Malformed()
    return {"pid": pid, "ppid": fields[4], "uid": fields[5], "euid": fields[5],
            "ruid": fields[7], "svuid": fields[9], "status": fields[1],
            "startSec": sec, "startUsec": usec}


def _path(ctx, pid):
    count, raw, result_errno = ctx.call(ctx.backend.path, pid)
    if not _integer(count, -1, 4096) or type(raw) is not bytes or len(raw) != 4096 or not _integer(result_errno, 0, 0x7fffffff):
        raise _Malformed()
    if count <= 0:
        raise OSError(result_errno, "Executable unavailable")
    end = raw.find(b"\0")
    if (count >= 4096 or result_errno or end <= 0 or end >= 4096
            or count not in (end, end + 1)):
        raise _Malformed()
    # The self-PID native ABI proof establishes nonzero unused buffer tail.
    # Only the bounded C string belongs to the path; tail bytes are never read
    # for identity, decoded, retained or serialized.
    image = raw[:end]
    if (not image.startswith(b"/") or any(value < 32 or value == 127 for value in image)
            or posixpath.normpath(image) != image or image.startswith(b"//")):
        raise _Malformed()
    image.decode("utf-8", errors="strict")
    return image


def _identity(record):
    return tuple(record[name] for name in ("pid", "ppid", "uid", "euid", "ruid", "svuid", "startSec", "startUsec"))


def _observed_path(ctx, pid):
    try:
        return _path(ctx, pid), "available"
    except _Deadline:
        raise
    except (_Malformed, UnicodeError, struct.error):
        return None, "malformed"
    except OSError as error:
        return None, "missing" if error.errno == errno.ESRCH else "permission" if error.errno in (errno.EACCES, errno.EPERM) else "unavailable"
    except Exception:
        return None, "unavailable"


def _observe_process(ctx, pid, retained=None):
    row = _process(pid)
    try:
        first = _record(ctx, pid)
        if first is None:
            if _record(ctx, pid) is None:
                row["state"] = "missing"
            else:
                row["state"] = "changed"
            return row
        image, path_state = _observed_path(ctx, pid)
        second = _record(ctx, pid)
        if second is None or _identity(first) != _identity(second):
            row["state"] = "changed"
            return row
        second_image, second_path_state = _observed_path(ctx, pid)
        if image != second_image or path_state != second_path_state:
            row["state"] = "changed"
            return row
        row.update(first)
        row["pathState"] = path_state
        same_uid = all(first[name] == ctx.uid for name in ("uid", "euid", "ruid", "svuid"))
        birth_matches = retained is not None and (retained[1] == pid and retained[2] == first["uid"]
                            and retained[4] == first["startSec"] and retained[5] == first["startUsec"]
                            and same_uid)
        if image is None:
            # A zombie can still have exact BSD birth while proc_pidpath returns
            # ESRCH. Preserve both reads; this match concerns kernel birth only,
            # and grants neither an executable identity nor cleanup authority.
            row["state"] = "kernel_only"
            if retained is not None:
                row["retainedBirthMatches"] = birth_matches
                if not birth_matches:
                    row["state"] = "reused"
            return row
        row["executable"] = next((name for name, path in ctx.expected.items() if image == path), "other")
        row["state"] = "stable" if same_uid else "foreign_uid"
        if retained is not None:
            matches = birth_matches and row["executable"] == "main"
            row["retainedBirthMatches"] = matches
            if not matches:
                row["state"] = "reused" if retained[2] != first["uid"] or retained[4:] != (first["startSec"], first["startUsec"]) else "retained_mismatch"
        return row
    except _Deadline:
        raise
    except (_Malformed, UnicodeError, struct.error):
        row["state"] = "malformed"
        return row
    except Exception:
        return row


def _reason(error):
    if isinstance(error, FileNotFoundError):
        return "absent"
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(error, PermissionError):
        return "permission"
    return "failed"


def _credentials(ctx, connection):
    try:
        result = ctx.call(ctx.backend.credentials, connection)
    except _Deadline:
        raise
    except Exception:
        return "unavailable", None, None
    if type(result) is not tuple or len(result) != 2 or any(not _integer(value, 0, MAX_UID) for value in result):
        return "malformed", None, None
    return "available", result[0], result[1]


def _observe_peer(ctx, callback):
    row = _peer()
    connection = None
    before = None
    endpoint = Path("/tmp/Mixel-Remote/ipc")
    try:
        candidate = ctx.call(ctx.backend.socket_identity, endpoint)
        if (type(candidate) is not tuple or len(candidate) != 7
                or any(type(value) is not int or value < 0 for value in candidate)
                or not stat.S_ISSOCK(candidate[0]) or candidate[1] != ctx.uid):
            row["reason"] = "unsafe_socket"
            return row
        before = candidate
        timeout = min(SOCKET_TIMEOUT, ctx.deadline - ctx.clock())
        if timeout <= 0:
            raise _Deadline()
        connection = ctx.call(ctx.backend.connect, endpoint, timeout)
        pid = ctx.call(callback, connection)
        if not _integer(pid, 1, MAX_PID):
            row.update(state="malformed", reason="malformed")
            return row
        row["pid"] = pid
        credentials, uid, gid = _credentials(ctx, connection)
        row.update(credentialsState=credentials, uid=uid, gid=gid)
        row["process"] = _observe_process(ctx, pid)
        again = ctx.call(callback, connection)
        again_credentials = _credentials(ctx, connection)
        if (not _integer(again, 1, MAX_PID) or again != pid
                or again_credentials != (credentials, uid, gid)):
            row.update(state="changed", reason="changed")
        elif (credentials == "available" and row["process"]["uid"] is not None
              and row["process"]["uid"] != uid):
            row.update(state="changed", reason="changed")
        elif row["process"]["state"] in ("stable", "foreign_uid") and credentials == "available":
            row.update(state="connected", reason="none")
        else:
            row.update(state="unavailable", reason="identity_unavailable")
        return row
    except _Deadline:
        raise
    except Exception as error:
        row.update(state="not_connected" if connection is None else "unavailable", reason=_reason(error))
        return row
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                row.update(state="unavailable", reason="failed")
        if before is not None:
            # Also compare after failed connect/peer reads. Deadline exhaustion
            # leaves an explicit unknown instead of granting further native work.
            try:
                after = ctx.call(ctx.backend.socket_identity, endpoint)
                if (type(after) is tuple and len(after) == 7
                        and all(type(value) is int and value >= 0 for value in after)):
                    row["socketIdentitySameAsBefore"] = after == before
                    if after != before and connection is not None:
                        row.update(state="changed", reason="changed")
                elif row["state"] == "connected":
                    row.update(state="unavailable", reason="identity_unavailable")
            except BaseException:
                if row["state"] == "connected":
                    row.update(state="unavailable", reason="identity_unavailable")


def _observe_inventory(ctx):
    row = _inventory(attempted=True)
    try:
        count, raw, result_errno = ctx.call(ctx.backend.pids, ctx.uid)
        if (not _integer(count, -1, PID_BYTES) or type(raw) is not bytes
                or len(raw) != PID_BYTES or not _integer(result_errno, 0, 0x7fffffff)):
            row["reason"] = "malformed"
            return row
        if count == PID_BYTES:
            row["reason"] = "truncated"
            return row
        if count <= 0:
            row["reason"] = "permission" if result_errno in (errno.EACCES, errno.EPERM) else "failed"
            return row
        if count % 4 or result_errno:
            row["reason"] = "malformed"
            return row
        pids = struct.unpack("=" + "i" * (count // 4), raw[:count])
        # Zero slots are not a proof of no processes. Reject duplicate/negative
        # IDs, and do not allow a malformed list to produce a false empty result.
        if any(pid < 0 or pid > MAX_PID for pid in pids):
            row["reason"] = "malformed"
            return row
        positive = [pid for pid in pids if pid]
        if not positive or len(set(positive)) != len(positive):
            row["reason"] = "malformed"
            return row
        row["enumerated"] = len(positive)
        row.update(state="complete", reason="none", mayBeIncomplete=False)
        for pid in positive:
            member = _observe_process(ctx, pid)
            row["examined"] += 1
            if member["state"] not in ("stable", "foreign_uid"):
                row.update(state="partial", reason="identity_unavailable", mayBeIncomplete=True)
                row["omitted"] += 1
            elif member["state"] == "stable" and member["executable"] in ("main", "service"):
                if len(row["members"]) == MAX_MEMBERS:
                    row.update(state="partial", reason="member_limit", mayBeIncomplete=True)
                    row["omitted"] += 1
                    break
                row["members"].append(member)
            else:
                row["omitted"] += 1
        if row["examined"] < len(positive):
            row.update(state="partial", mayBeIncomplete=True)
        return row
    except _Deadline:
        row.update(state="partial", reason="deadline", mayBeIncomplete=True)
        return row
    except Exception:
        return row


def _inputs(app, server_pid, server_birth, gui_pid, gui_birth, callback):
    app = Path(app)
    if not app.is_absolute() or app != app.resolve(strict=True) or not app.is_dir() or not callable(callback):
        raise ValueError("Invalid observation input")
    expected = {"main": os.fsencode(app / "Contents/MacOS/Mixel-Remote"),
                "service": os.fsencode(app / "Contents/MacOS/service")}
    for encoded in expected.values():
        member = Path(os.fsdecode(encoded))
        if member.resolve(strict=True) != member or not stat.S_ISREG(member.lstat().st_mode):
            raise ValueError("Invalid canonical observation executable")
    for pid, birth in ((server_pid, server_birth), (gui_pid, gui_birth)):
        if (not _integer(pid, 1, MAX_PID) or type(birth) is not tuple or len(birth) != 6
                or birth[0] != "darwin-gui-birth-v1" or birth[1] != pid
                or not _integer(birth[1], 1, MAX_PID) or not _integer(birth[2], 0, MAX_UID)
                or type(birth[3]) is not str or os.fsencode(birth[3]) != expected["main"]
                or not _integer(birth[4], 1, MAX_TIME) or not _integer(birth[5], 0, 999_999)):
            raise ValueError("Invalid retained observation identity")
    if server_pid == gui_pid:
        raise ValueError("Invalid retained observation identity")
    return expected


def observe(app, server_pid, server_birth, gui_pid, gui_birth, peer_pid_callback, *, backend=None):
    """Return three fixed diagnostic records. Never raise into original cleanup.

    Native calls start only before the 1.5-second deadline. A synchronous OS
    metadata call cannot be cancelled; an overrun is explicitly failed/partial,
    never accepted as complete. Socket calls have at most a .2-second timeout.
    """
    facts = _facts()
    ctx = None
    try:
        if backend is None:
            if (sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true"
                    or os.environ.get("RUNNER_OS") != "macOS"):
                facts["error"] = "gate"
                return facts
        try:
            expected = _inputs(app, server_pid, server_birth, gui_pid, gui_birth, peer_pid_callback)
        except Exception:
            facts["error"] = "input"
            return facts
        try:
            ctx = _Context(_NativeBackend() if backend is None else backend, expected)
        except Exception:
            facts["error"] = "backend"
            return facts
        facts.update(outcome="observed", error="none")
        for index, offset in enumerate(OFFSETS):
            sample = facts["samples"][index]
            try:
                delay = ctx.start + offset - ctx.clock()
                if delay > 0:
                    ctx.call(ctx.backend.sleep, min(delay, max(0, ctx.deadline - ctx.last)))
                ctx.check()
                sample.update(elapsedMs=ctx.elapsed(), state="complete")
                sample["tray"] = _observe_process(ctx, server_pid, server_birth)
                sample["gui"] = _observe_process(ctx, gui_pid, gui_birth)
                sample["peer"] = _observe_peer(ctx, peer_pid_callback)
                sample["inventory"] = _observe_inventory(ctx)
                if (sample["tray"]["state"] not in ("stable", "missing")
                        or sample["gui"]["state"] not in ("stable", "missing")
                        or sample["peer"]["state"] != "connected"
                        or sample["inventory"]["state"] != "complete"):
                    sample["state"] = "partial"
                    facts["outcome"] = "partial"
                ctx.check()
            except _Deadline:
                if sample["state"] != "skipped":
                    sample["state"] = "partial"
                if sample["inventory"]["attempted"] is False:
                    sample["inventory"] = _inventory("deadline")
                facts.update(outcome="failed", error="deadline")
                for remaining in facts["samples"][index + 1:]:
                    remaining["tray"]["state"] = remaining["gui"]["state"] = "deadline"
                    remaining["peer"] = _peer("deadline", "deadline")
                    remaining["inventory"] = _inventory("deadline")
                break
        facts["elapsedMs"] = ctx.elapsed()
        validate_facts(facts)
        return facts
    except BaseException:
        # Never serialize exceptions, raw native metadata, paths or bearer text.
        failed = _facts()
        failed["error"] = "unexpected"
        if ctx is not None:
            failed["elapsedMs"] = ctx.elapsed()
        return failed


def validate_facts(value):
    """Pure strict schema validation for the caller/provider receipt parser."""
    def refuse():
        raise ValueError("Unsafe Mac incoming observation facts")

    def exact(row, keys):
        if type(row) is not dict or set(row) != set(keys):
            refuse()

    def enum(item, allowed):
        if type(item) is not str or item not in allowed:
            refuse()

    def number(item, low, high, nullable=False):
        if (item is None and nullable) or _integer(item, low, high):
            return
        refuse()

    def process(row):
        exact(row, _process())
        enum(row["state"], PROCESS_STATES)
        enum(row["executable"], PATH_STATES)
        enum(row["pathState"], PATH_OBSERVATIONS)
        number(row["pid"], 1, MAX_PID, True)
        number(row["ppid"], 0, MAX_PID, True)
        for name in ("uid", "euid", "ruid", "svuid"):
            number(row[name], 0, MAX_UID, True)
        number(row["status"], 1, 5, True)
        number(row["startSec"], 1, MAX_TIME, True)
        number(row["startUsec"], 0, 999_999, True)
        if row["retainedBirthMatches"] is not None and type(row["retainedBirthMatches"]) is not bool:
            refuse()
        populated = ("pid", "ppid", "uid", "euid", "ruid", "svuid", "status", "startSec", "startUsec")
        if row["state"] in ("stable", "kernel_only", "foreign_uid", "reused", "retained_mismatch"):
            if any(row[name] is None for name in populated) or row["euid"] != row["uid"]:
                refuse()
            if (row["pathState"] == "available") != (row["executable"] != "unavailable"):
                refuse()
            if row["state"] in ("stable", "foreign_uid", "retained_mismatch") and row["pathState"] != "available":
                refuse()
            if row["state"] == "kernel_only" and row["pathState"] == "available":
                refuse()
        elif any(row[name] is not None for name in populated if name != "pid") or row["executable"] != "unavailable" or row["pathState"] != "unavailable" or row["retainedBirthMatches"] is not None:
            refuse()
        if row["state"] in ("reused", "retained_mismatch") and row["retainedBirthMatches"] is not False:
            refuse()
        if row["retainedBirthMatches"] is True and (row["state"] not in ("stable", "kernel_only") or (row["state"] == "stable" and row["executable"] != "main")):
            refuse()
        if row["state"] == "stable" or row["retainedBirthMatches"] is True:
            if not row["uid"] == row["euid"] == row["ruid"] == row["svuid"]:
                refuse()

    exact(value, _facts())
    if type(value["schema"]) is not int or value["schema"] != 1 or value["scope"] != SCOPE or type(value["scope"]) is not str:
        refuse()
    enum(value["outcome"], ("observed", "partial", "failed"))
    enum(value["error"], ("none", "gate", "input", "backend", "deadline", "unexpected"))
    number(value["elapsedMs"], 0, 0x7fffffff)
    if any(value[name] is not False for name in ("cleanupAuthority", "fdOwnershipEstablished", "recoveryEstablished")):
        refuse()
    if type(value["samples"]) is not list or len(value["samples"]) != 3:
        refuse()
    for index, sample in enumerate(value["samples"]):
        exact(sample, _sample(index))
        if type(sample["index"]) is not int or sample["index"] != index:
            refuse()
        number(sample["elapsedMs"], 0, 0x7fffffff)
        enum(sample["state"], ("complete", "partial", "skipped"))
        process(sample["tray"])
        process(sample["gui"])
        peer = sample["peer"]
        exact(peer, _peer())
        enum(peer["state"], PEER_STATES)
        enum(peer["reason"], REASONS)
        number(peer["pid"], 1, MAX_PID, True)
        enum(peer["credentialsState"], ("available", "unavailable", "malformed"))
        if peer["socketIdentitySameAsBefore"] is not None and type(peer["socketIdentitySameAsBefore"]) is not bool:
            refuse()
        number(peer["uid"], 0, MAX_UID, True)
        number(peer["gid"], 0, MAX_UID, True)
        if (peer["credentialsState"] == "available") != (peer["uid"] is not None and peer["gid"] is not None):
            refuse()
        if peer["credentialsState"] != "available" and (peer["uid"] is not None or peer["gid"] is not None):
            refuse()
        process(peer["process"])
        if peer["process"]["pid"] is not None and peer["process"]["pid"] != peer["pid"]:
            refuse()
        if peer["state"] == "connected" and (peer["pid"] is None or peer["reason"] != "none" or peer["process"]["state"] not in ("stable", "foreign_uid") or peer["credentialsState"] != "available" or peer["uid"] != peer["process"]["uid"] or peer["socketIdentitySameAsBefore"] is not True):
            refuse()
        inventory = sample["inventory"]
        exact(inventory, _inventory())
        enum(inventory["state"], ("complete", "partial", "unavailable"))
        enum(inventory["reason"], REASONS)
        if type(inventory["attempted"]) is not bool or type(inventory["mayBeIncomplete"]) is not bool:
            refuse()
        number(inventory["enumerated"], 1, 4095, True)
        number(inventory["examined"], 0, 4095)
        number(inventory["omitted"], 0, 4095)
        if type(inventory["members"]) is not list or len(inventory["members"]) > MAX_MEMBERS:
            refuse()
        member_pids = []
        for member in inventory["members"]:
            process(member)
            if member["state"] != "stable" or member["executable"] not in ("main", "service") or member["retainedBirthMatches"] is not None:
                refuse()
            if not member["uid"] == member["euid"] == member["ruid"] == member["svuid"]:
                refuse()
            member_pids.append(member["pid"])
        if len(set(member_pids)) != len(member_pids):
            refuse()
        if inventory["enumerated"] is not None and (inventory["examined"] > inventory["enumerated"] or inventory["omitted"] > inventory["examined"]):
            refuse()
        if len(member_pids) + inventory["omitted"] != inventory["examined"]:
            refuse()
        if inventory["state"] == "complete":
            if (not inventory["attempted"] or inventory["mayBeIncomplete"] or inventory["reason"] != "none"
                    or inventory["enumerated"] is None or inventory["examined"] != inventory["enumerated"]):
                refuse()
        elif inventory["mayBeIncomplete"] is not True or inventory["reason"] == "none":
            refuse()
        if not inventory["attempted"] and (inventory["enumerated"] is not None or inventory["examined"] or inventory["members"]):
            refuse()
    if value["outcome"] == "observed" and (value["error"] != "none" or any(sample["state"] != "complete" for sample in value["samples"])):
        refuse()
    if value["outcome"] == "partial" and value["error"] != "none":
        refuse()
    if value["outcome"] == "failed" and value["error"] == "none":
        refuse()
    return True
