#!/usr/bin/env python3
"""Prepare an isolated real application cargo check; compilation requires --run.

This is an early typecheck gate, not a replacement for complete platform builds,
Flutter analysis, signing, application execution or the existing release gates.
Only new, private output is written. The supplied branded application is copied,
including dependency Git metadata, before build.rs can rewrite src/version.rs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

SCHEMA = 1
VCPKG_COMMIT = "120deac3062162151622ca4860575a33844ba10b"
MATRIX = {
    "x86_64-unknown-linux-gnu": ("Linux", "1.75", "flutter", "x64-linux", None),
    "x86_64-pc-windows-msvc": ("Windows", "1.75", "flutter", "x64-windows-static", None),
    "aarch64-apple-darwin": ("Darwin", "1.81", "flutter,unix-file-copy-paste", "arm64-osx", "12.3"),
    "x86_64-apple-darwin": ("Darwin", "1.81", "flutter,unix-file-copy-paste", "x64-osx", "10.14"),
}
REQUIRED = (
    "Cargo.toml", "Cargo.lock", "build.rs", "src/lib.rs",
    "src/bridge_generated.rs", "src/bridge_generated.io.rs",
    "libs/hbb_common/Cargo.toml", "libs/enigo/Cargo.toml", "libs/scrap/Cargo.toml",
    "libs/scrap/build.rs",
)
# These are anchored application caches, not arbitrary directories named build or
# target. In particular libs/*/build source and .git/modules metadata are retained.
CACHES = {
    ".": {"build", "target", ".dart_tool", "__pycache__"},
    "flutter": {"build", ".dart_tool"},
    "flutter/macos/Flutter": {"ephemeral"},
    "flutter/windows/flutter": {"ephemeral"},
    "flutter/linux/flutter": {"ephemeral"},
}


class Refusal(RuntimeError):
    pass


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    # Every destination belongs to a just-reserved private output directory.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def ordinary_path(value, *, exists):
    if ".." in Path(value).parts:
        raise Refusal("parent traversal is not an explicit canonical path")
    path = Path(os.path.abspath(value))
    if any(part.casefold() == "worktrees" for part in path.parts):
        raise Refusal("worktrees paths are excluded")
    # Never follow a symlink/reparse-point ancestor into an unexpected output or
    # source. Callers on macOS should pass canonical /private/... temp paths.
    for ancestor in reversed((path, *path.parents)):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("indirect path component: " + str(ancestor))
        if ancestor != path and not stat.S_ISDIR(info.st_mode):
            raise Refusal("non-directory path ancestor")
    if exists and not path.is_dir():
        raise Refusal("expected an existing ordinary directory: " + str(path))
    if not exists and path.exists():
        raise Refusal("output must not already exist")
    return path


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def excluded(relative, name):
    return name.casefold() == "worktrees" or name in CACHES.get(relative, set())


def safe_destination(root, candidate, seen=()):
    """Resolve links/.. in filesystem order without touching excluded subtrees."""
    candidate = Path(candidate)
    if not candidate.is_absolute() or not inside(candidate, root):
        raise Refusal("source link escapes private copy")
    relative = candidate.relative_to(root)
    current = root
    for index, part in enumerate(relative.parts):
        if part == "..":
            if current == root:
                raise Refusal("source link escapes private copy")
            current = current.parent
            continue
        parent = current.relative_to(root).as_posix()
        if excluded(parent, part):
            raise Refusal("source link reaches an excluded subtree")
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            if current in seen or len(seen) >= 32:
                raise Refusal("source link cycle or excessive indirection")
            value = os.readlink(current)
            if Path(value).is_absolute():
                raise Refusal("absolute source symlink is not relocatable")
            remaining = relative.parts[index + 1:]
            return safe_destination(root, current.parent / value / Path(*remaining), (*seen, current))
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("source link reaches a reparse point")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise Refusal("source link traverses a non-directory")
    return current


def link_destination(root, path, target):
    if Path(target).is_absolute():
        raise Refusal("absolute source symlink is not relocatable: " + str(path.relative_to(root)))
    safe_destination(root, path.parent / target)
    return target


def git_pointer(root, path, raw):
    relative = path.relative_to(root).as_posix()
    if path.name == ".git":
        prefix = b"gitdir: "
        if not raw.startswith(prefix) or b"\n" in raw.rstrip(b"\r\n"):
            raise Refusal("invalid submodule Git pointer: " + relative)
        target = raw[len(prefix):].strip().decode("utf-8")
    elif path.name == "commondir" and ".git" in path.parts:
        target = raw.strip().decode("utf-8")
    elif path.name == "alternates" and ".git" in path.parts:
        if raw.strip():
            raise Refusal("external Git object alternates are not private")
        return
    else:
        return
    if Path(target).is_absolute():
        raise Refusal("absolute Git pointer is not relocatable: " + relative)
    candidate = safe_destination(root, path.parent / target)
    if not inside(candidate, root / ".git"):
        raise Refusal("Git pointer escapes copied ordinary checkout: " + relative)
    if any(part.casefold() == "worktrees" for part in candidate.parts):
        raise Refusal("Git worktree metadata is excluded")
    if not candidate.is_dir():
        raise Refusal("Git pointer is absent or indirect: " + relative)


def inventory(root):
    entries = []

    def visit(directory):
        relative = directory.relative_to(root).as_posix()
        # Decide exclusions by name before lstat/descent. A worktrees entry can be
        # a directory, symlink or inaccessible object: none of it is inspected.
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if excluded(relative, path.name):
                continue
            info = path.lstat()
            name = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                target = link_destination(root, path, os.readlink(path))
                entries.append({"path": name, "kind": "symlink", "target": target})
            elif getattr(info, "st_file_attributes", 0) & 0x400:
                raise Refusal("source contains a reparse point: " + name)
            elif stat.S_ISDIR(info.st_mode):
                entries.append({"path": name, "kind": "directory", "mode": mode})
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                digest = digest_file(path)
                after = path.lstat()
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode
                ):
                    raise Refusal("source changed while hashing: " + name)
                if path.name in (".git", "commondir", "alternates"):
                    git_pointer(root, path, path.read_bytes())
                entries.append({"path": name, "kind": "file", "mode": mode,
                                "size": info.st_size, "sha256": digest})
            else:
                raise Refusal("source contains a non-file object: " + name)

    visit(root)
    return entries


def binding(entries):
    raw = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def git_identity(root):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_OPTIONAL_LOCKS="0")
    result = {}
    for name, revision in (("head", "HEAD"), ("tree", "HEAD^{tree}")):
        run = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", revision],
                             env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             check=False)
        value = run.stdout.decode("ascii", errors="strict").strip()
        if run.returncode or len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise Refusal("source Git identity is unavailable; private diagnostics withheld")
        result[name] = value
    return result


def matrix(target, system=None, machine=None):
    system = platform.system() if system is None else system
    machine = platform.machine().casefold() if machine is None else machine.casefold()
    if target not in MATRIX or MATRIX[target][0] != system:
        raise Refusal("unsupported shipping host/target combination")
    if machine not in ({"x86_64", "amd64"} if system != "Darwin" else {"arm64", "aarch64", "x86_64"}):
        raise Refusal("unsupported shipping host architecture")
    host, toolchain, features, triplet, minimum = MATRIX[target]
    return {"host": host, "target": target, "toolchain": toolchain,
            "features": features, "triplet": triplet, "macosMinimum": minimum}


def command(config):
    return ["rustup", "run", config["toolchain"], "cargo", "check", "--locked", "--release",
            "--lib", "--features", config["features"], "--target", config["target"]]


def require_source(root):
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise Refusal("application source must be an ordinary Git checkout")
    for name in REQUIRED:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise Refusal("required real application/bridge input is absent: " + name)
        if not path.stat().st_size:
            raise Refusal("required application input is empty: " + name)


def owned_directory(path):
    path = ordinary_path(path, exists=True)
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise Refusal("local directory must belong to the current user without group/other write access")
    return path


def macos_local_admission(root, source, output, config, *, prepared):
    """Admit only an explicit Mac checkout-local workspace, never Windows effects."""
    if os.name != "posix" or platform.system() != "Darwin" or config["host"] != "Darwin":
        raise Refusal("local application typecheck admission requires native macOS")
    if not Path(root).is_absolute():
        raise Refusal("local run root must be an explicit absolute path")
    script = Path(__file__).absolute()
    owned_directory(script.parent)
    info = script.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or
            info.st_uid != os.getuid() or info.st_nlink != 1):
        raise Refusal("local admission requires this repository's ordinary current-user-owned driver")
    repo = owned_directory(script.parents[1])
    if ordinary_path(Path.cwd(), exists=True) != repo:
        raise Refusal("local application typecheck must start from this script's repository root")
    root = owned_directory(root)
    if root == repo or not inside(root, repo):
        raise Refusal("local run root must be inside this repository")
    relative = root.relative_to(repo)
    if relative.parts[0] != "_scratch":
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_OPTIONAL_LOCKS="0")
        ignored = subprocess.run(["git", "-C", str(repo), "-c", "core.excludesFile=" + os.devnull,
                                  "check-ignore", "--no-index", "--quiet", "--", relative.as_posix() + "/"],
                                 env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if ignored.returncode != 0:
            raise Refusal("local run root must be under _scratch or a Git-ignored build directory")
    for path in (root, *root.parents):
        owned_directory(path)
        if path == repo:
            break
    source = owned_directory(source)
    if not inside(source, repo):
        raise Refusal("local application source must be inside this repository")
    for path in source.parents:
        owned_directory(path)
        if path == repo:
            break
    output = ordinary_path(output, exists=prepared)
    if (output == root or not inside(output, root) or inside(root, source) or
            inside(output, source) or inside(source, output)):
        raise Refusal("local output must be separate from source and strictly inside its admitted root")
    if prepared and stat.S_IMODE(output.lstat().st_mode) != 0o700:
        raise Refusal("local output must retain its private preparation permissions")
    first = output if prepared else output.parent
    for path in (first, *first.parents):
        owned_directory(path)
        if path == root:
            break
    # The existing inventory prunes protected worktrees/caches before inspection.
    # Local input must additionally be owned and unaliased, including Git objects.
    for entry in inventory(source):
        info = (source / entry["path"]).lstat()
        if entry["kind"] == "symlink" or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refusal("local application source contains an indirect entry")
        if info.st_uid != os.getuid() or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise Refusal("local application source must be current-user owned without hardlinked files")
    return root


def require_windows_native_x64(process_machine, native_machine, pointer_bytes):
    # IsWow64Process2 reports UNKNOWN for a native (non-WOW64) process. AMD64
    # emulation on ARM64 and x86 WOW64 must not qualify a native x64 build host.
    if (process_machine, native_machine, pointer_bytes) != (0, 0x8664, 8):
        raise Refusal("local Windows admission requires actual native Windows x64")


class WindowsSecurityProbe:
    """Read the actual token SID and file owner/DACL; never alter security."""
    def __init__(self):
        if os.name != "nt" or platform.system() != "Windows":
            raise Refusal("local Windows admission requires actual native Windows x64")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.types = ctypes, wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer = ctypes.c_void_p
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.IsWow64Process2.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.WORD)]
        self.kernel.IsWow64Process2.restype = wintypes.BOOL
        process_machine, native_machine = wintypes.WORD(), wintypes.WORD()
        if not self.kernel.IsWow64Process2(self.kernel.GetCurrentProcess(), ctypes.byref(process_machine), ctypes.byref(native_machine)):
            raise Refusal("cannot query actual Windows process/native architecture")
        require_windows_native_x64(process_machine.value, native_machine.value, ctypes.sizeof(pointer))
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [pointer]
        self.kernel.LocalFree.restype = pointer
        self.security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.security.OpenProcessToken.restype = wintypes.BOOL
        self.security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.security.GetTokenInformation.restype = wintypes.BOOL
        self.security.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
        self.security.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.security.IsValidSid.argtypes = [pointer]
        self.security.IsValidSid.restype = wintypes.BOOL
        token = wintypes.HANDLE()
        if not self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise Refusal("cannot query the actual Windows process token")
        try:
            needed = wintypes.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            if ctypes.get_last_error() != 122 or not 0 < needed.value <= 65536:
                raise Refusal("actual Windows token identity size is invalid")
            raw = ctypes.create_string_buffer(needed.value)
            if not self.security.GetTokenInformation(token, 1, raw, needed, ctypes.byref(needed)):
                raise Refusal("cannot read the actual Windows token user")
            sid = ctypes.cast(raw, ctypes.POINTER(pointer))[0]
            self.user_sid = self.sid_text(sid)
        finally:
            if not self.kernel.CloseHandle(token):
                raise Refusal("Windows token query handle did not close")
        self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.kernel.GetDriveTypeW.restype = wintypes.UINT
        self.kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR, wintypes.DWORD]
        self.kernel.GetVolumeInformationW.restype = wintypes.BOOL
        self.volume_serials = {}
        self.security.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer), ctypes.POINTER(pointer)]
        self.security.GetSecurityInfo.restype = wintypes.DWORD
        self.security.IsValidAcl.argtypes = [pointer]
        self.security.IsValidAcl.restype = wintypes.BOOL
        self.security.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
        self.security.GetAce.restype = wintypes.BOOL

    def sid_text(self, sid):
        c, w = self.ctypes, self.types
        if not sid or not self.security.IsValidSid(sid):
            raise Refusal("invalid Windows security identifier")
        value = w.LPWSTR()
        if not self.security.ConvertSidToStringSidW(sid, c.byref(value)):
            raise Refusal("cannot read a Windows security identifier")
        try:
            return value.value
        finally:
            self.kernel.LocalFree(c.cast(value, c.c_void_p))

    def inspect(self, path):
        c, w = self.ctypes, self.types
        path = Path(path)
        if not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:\\", path.anchor):
            raise Refusal("local Windows source/output must use an ordinary local drive")
        if self.kernel.GetDriveTypeW(path.anchor) != 3:
            raise Refusal("local Windows source/output must use a fixed local drive")
        if path.anchor not in self.volume_serials:
            serial = w.DWORD()
            filesystem = c.create_unicode_buffer(64)
            if not self.kernel.GetVolumeInformationW(path.anchor, None, 0, c.byref(serial), None, None, filesystem, 64):
                raise Refusal("cannot read the actual local Windows volume identity")
            if filesystem.value != "NTFS":
                # The legacy 64-bit file index is a unique NTFS identity; ReFS
                # would require a separately reviewed 128-bit FileIdInfo path.
                raise Refusal("local Windows admission requires NTFS file identities and DACLs")
            self.volume_serials[path.anchor] = (serial.value, filesystem.value)
        # READ_CONTROL | FILE_READ_ATTRIBUTES, no write/delete/security-change
        # access. OPEN_REPARSE_POINT keeps the final component uninterpreted.
        handle = self.kernel.CreateFileW(str(path), 0x00020080, 3, None, 3, 0x02200000, None)
        if handle is None or handle == c.c_void_p(-1).value:
            raise Refusal("cannot read Windows file identity and security")
        descriptor = c.c_void_p()
        try:
            class FileTime(c.Structure):
                _fields_ = [("low", w.DWORD), ("high", w.DWORD)]
            class FileInfo(c.Structure):
                _fields_ = [("attributes", w.DWORD), ("created", FileTime), ("accessed", FileTime),
                    ("written", FileTime), ("volume", w.DWORD), ("size_high", w.DWORD),
                    ("size_low", w.DWORD), ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]
            self.kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, c.POINTER(FileInfo)]
            self.kernel.GetFileInformationByHandle.restype = w.BOOL
            info = FileInfo()
            if not self.kernel.GetFileInformationByHandle(handle, c.byref(info)):
                raise Refusal("cannot read Windows file attributes/link count")
            owner, dacl = c.c_void_p(), c.c_void_p()
            status = self.security.GetSecurityInfo(handle, 1, 0x00000005, c.byref(owner), None,
                                                   c.byref(dacl), None, c.byref(descriptor))
            if status or not owner.value or not dacl.value or not self.security.IsValidAcl(dacl):
                raise Refusal("Windows owner and non-null valid DACL are required")
            class Acl(c.Structure):
                _fields_ = [("revision", w.BYTE), ("reserved", w.BYTE), ("size", w.WORD),
                            ("count", w.WORD), ("reserved2", w.WORD)]
            acl = c.cast(dacl, c.POINTER(Acl)).contents
            if acl.count > 4096:
                raise Refusal("Windows DACL exceeds its finite bound")
            entries = []
            for index in range(acl.count):
                ace = c.c_void_p()
                if not self.security.GetAce(dacl, index, c.byref(ace)) or not ace.value:
                    raise Refusal("cannot enumerate Windows DACL entries")
                kind = c.c_ubyte.from_address(ace.value).value
                flags = c.c_ubyte.from_address(ace.value + 1).value
                size = c.c_ushort.from_address(ace.value + 2).value
                if kind not in (0, 1) or size < 16:
                    raise Refusal("unreviewed Windows DACL ACE type")
                mask = w.DWORD.from_address(ace.value + 4).value
                entries.append({"type": kind, "flags": flags, "mask": mask,
                                "sid": self.sid_text(c.c_void_p(ace.value + 8))})
            return {"ownerSid": self.sid_text(owner), "dacl": entries,
                    "attributes": info.attributes, "links": info.links,
                    "fileIndex": (info.index_high << 32) | info.index_low,
                    "volumeSerial": info.volume, "driveVolumeSerial": self.volume_serials[path.anchor][0],
                    "fileSystem": self.volume_serials[path.anchor][1],
                    "directory": bool(info.attributes & 0x10)}
        finally:
            if descriptor.value:
                self.kernel.LocalFree(descriptor)
            if not self.kernel.CloseHandle(handle):
                raise Refusal("Windows file-security query handle did not close")


def windows_private_entry(path, probe, *, directory):
    """Require real current-SID ownership and a private, ordinary NTFS object."""
    before = path.lstat()
    if (stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400 or
            (not directory and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1)) or
            (directory and not stat.S_ISDIR(before.st_mode))):
        raise Refusal("local Windows entry must be ordinary without links/reparse points")
    value = probe.inspect(path)
    if (value["ownerSid"] != probe.user_sid or value["attributes"] & 0x400 or
            value["directory"] != directory or (not directory and value["links"] != 1) or
            not value["fileIndex"] or value["fileIndex"] != before.st_ino or
            value["volumeSerial"] != value["driveVolumeSerial"] or value["fileSystem"] != "NTFS"):
        raise Refusal("local Windows entry must be owned by the actual token SID and unaliased")
    trusted = {probe.user_sid, "S-1-5-18", "S-1-5-32-544"}
    owner_allow = False
    if not value["dacl"]:
        raise Refusal("local Windows entry requires a non-empty private DACL")
    for ace in value["dacl"]:
        if ace["type"] not in (0, 1):
            raise Refusal("unreviewed private DACL ACE type")
        if ace["type"] == 1:
            continue
        if ace["sid"] == "S-1-3-0" and ace["flags"] & 0x08:
            continue  # Inherit-only CREATOR OWNER cannot grant another SID access.
        if ace["sid"] not in trusted:
            raise Refusal("local Windows DACL grants access beyond current owner, SYSTEM and Administrators")
        if ace["sid"] == probe.user_sid and not ace["flags"] & 0x08 and ace["mask"]:
            owner_allow = True
    if not owner_allow:
        raise Refusal("private Windows DACL must grant the actual owner direct access")
    after = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_mode, item.st_nlink)
    if identity(before) != identity(after) or value["fileIndex"] != after.st_ino:
        raise Refusal("local Windows entry changed while its security was read")


def windows_local_admission(root, source, output, config, *, prepared):
    """Explicit local compilation only: no account, desktop, service or policy effects."""
    if config != matrix("x86_64-pc-windows-msvc"):
        raise Refusal("local Windows admission requires the exact shipping x64 configuration")
    probe = WindowsSecurityProbe()
    if not Path(root).is_absolute():
        raise Refusal("local Windows run root must be an explicit absolute path")
    script = Path(__file__).absolute()
    repo = ordinary_path(script.parents[1], exists=True)
    if ordinary_path(Path.cwd(), exists=True) != repo:
        raise Refusal("local Windows typecheck must start from this script's repository root")
    windows_private_entry(script, probe, directory=False)
    windows_private_entry(script.parent, probe, directory=True)
    windows_private_entry(repo, probe, directory=True)
    root = ordinary_path(root, exists=True)
    source = ordinary_path(source, exists=True)
    output = ordinary_path(output, exists=prepared)
    if (root == repo or not inside(root, repo) or not inside(source, repo) or source == repo or
            output == root or not inside(output, root) or inside(root, source) or
            inside(output, source) or inside(source, output)):
        raise Refusal("local Windows source/output must be separate inside the owned repository/run root")
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0")
    for ignored_path in (root, output):
        relative = ignored_path.relative_to(repo).as_posix() + "/"
        ignored = subprocess.run(["git", "-C", str(repo), "-c", "core.excludesFile=" + os.devnull,
                                  "check-ignore", "--no-index", "--quiet", "--", relative],
                                 env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if ignored.returncode != 0:
            raise Refusal("local Windows run root and output must be Git-ignored")
    for first, stop in ((root, repo), (source, repo), (output if prepared else output.parent, root)):
        for path in (first, *first.parents):
            windows_private_entry(path, probe, directory=True)
            if path == stop:
                break
    for tree in ((source, output / "source") if prepared else (source,)):
        ordinary_path(tree, exists=True)
        windows_private_entry(tree, probe, directory=True)
        for entry in inventory(tree):
            if entry["kind"] == "symlink":
                raise Refusal("local Windows source contains an indirect entry")
            windows_private_entry(tree / entry["path"], probe, directory=entry["kind"] == "directory")
    return root


def local_admission(root, source, output, config, *, prepared):
    if platform.system() == "Windows":
        return windows_local_admission(root, source, output, config, prepared=prepared)
    return macos_local_admission(root, source, output, config, prepared=prepared)


def privacy_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("app_check_url_privacy", Path(__file__).with_name("patch-support-url-log-privacy.py"))
    privacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(privacy)
    return privacy


def verify_cargo_home(home, *, patched=False):
    privacy = privacy_module()
    privacy.validate_cargo_home(home)
    if patched:
        checkout = privacy.tao_checkout(home)
        path, _ = privacy.safe_file(checkout, privacy.SOURCES["tao"]["path"])
        current = path.read_bytes()
        if current != privacy.transform("tao", current):
            raise Refusal("local Cargo home must already contain the exact patched pinned Tao source")


def run_environment(config, source, output, environment, *, local_run_root=None):
    if local_run_root is not None:
        local_admission(local_run_root, source, output, config, prepared=True)
    elif (environment.get("GITHUB_ACTIONS") != "true" or
            environment.get("RUNNER_ENVIRONMENT") != "github-hosted" or
            environment.get("RUNNER_OS") != {"Darwin": "macOS", "Linux": "Linux", "Windows": "Windows"}[config["host"]]):
        raise Refusal("--run requires the matching GitHub-hosted CI runner")
    if environment.get("CARGO_BUILD_TARGET", config["target"]) != config["target"]:
        raise Refusal("CARGO_BUILD_TARGET conflicts with the explicit matrix target")
    # Rust wrappers/alternate compilers would turn the proposed whole-app check
    # into an unrelated executable. Shipping jobs do not use these overrides.
    for key in ("RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTDOC"):
        if environment.get(key):
            raise Refusal("unexpected compiler override: " + key)
    value = environment.get("VCPKG_ROOT", "")
    if not value:
        raise Refusal("shipping VCPKG_ROOT must already be installed")
    vcpkg = ordinary_path(value, exists=True)
    if inside(vcpkg, source) or inside(vcpkg, output):
        raise Refusal("vcpkg must be an external installed dependency")
    if git_identity(vcpkg)["head"] != VCPKG_COMMIT:
        raise Refusal("vcpkg revision differs from shipping pin")
    if not (vcpkg / "installed" / config["triplet"] / "lib").is_dir():
        raise Refusal("shipping vcpkg triplet libraries are absent")
    if config["host"] == "Darwin":
        if environment.get("MACOSX_DEPLOYMENT_TARGET") != config["macosMinimum"]:
            raise Refusal("macOS deployment target differs from shipping matrix")
        overlay = environment.get("VCPKG_OVERLAY_TRIPLETS", "")
        if not overlay or not ordinary_path(overlay, exists=True).joinpath(config["triplet"] + ".cmake").is_file():
            raise Refusal("shipping macOS overlay triplet is absent")
    if local_run_root is None:
        runner_temp = environment.get("RUNNER_TEMP", "")
        if not runner_temp or not inside(output, ordinary_path(runner_temp, exists=True)):
            raise Refusal("CI output must be private to RUNNER_TEMP")
    cargo_home = environment.get("CARGO_HOME", "")
    if config["host"] == "Darwin" and not cargo_home:
        raise Refusal("macOS requires its already-patched owned shipping CARGO_HOME")
    if cargo_home:
        home = ordinary_path(cargo_home, exists=True)
        if inside(home, source) or inside(home, output) or (local_run_root is not None and (
                inside(source, home) or inside(output, home))):
            raise Refusal("Cargo registry must be an external installed dependency")
        if config["host"] == "Darwin":
            verify_cargo_home(home, patched=local_run_root is not None)
    result = dict(environment)
    result["CARGO_TARGET_DIR"] = str(output / "target")
    result["CARGO_BUILD_TARGET"] = config["target"]
    if config["host"] == "Windows":
        # Reuse the existing strict installed Microsoft LINK/PE identity checks.
        from rust_toolchain import find_msvc_linker
        result["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"] = str(find_msvc_linker(result))
    # Preserve the shipping patched Cargo registry and native compiler/SDK setup.
    # Never print the inherited environment (it can contain signing credentials).
    write_json(output / "check.environment.json", {
        "vcpkgRoot": str(vcpkg), "vcpkgCommit": VCPKG_COMMIT,
        "triplet": config["triplet"], "cargoHome": cargo_home or "inherited Cargo default",
        "cargoTargetDir": result["CARGO_TARGET_DIR"], "target": result["CARGO_BUILD_TARGET"],
        "macosMinimum": config["macosMinimum"],
        "msvcLinker": result.get("CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"),
        "admission": ("local-windows" if config["host"] == "Windows" else "local-macos") if local_run_root is not None else "github-hosted",
        "localRunRoot": str(local_run_root) if local_run_root is not None else None,
    })
    return result


def capture(argv, cwd, environment, output, stem):
    plan = json.loads((output / "check.plan.json").read_text(encoding="utf-8"))
    selected = shutil.which(argv[0], path=environment.get("PATH"))
    write_json(output / (stem + ".command.json"), {
        "argv": argv, "cwd": str(cwd), "sourceInventorySha256": plan["sourceInventorySha256"],
        "driverSha256": plan["driverSha256"], "config": plan["config"],
        "resolvedExecutable": str(Path(selected).resolve(strict=True)) if selected else None,
        "executableSha256": digest_file(Path(selected)) if selected else None,
    })
    started = time.monotonic()
    result = {"status": "failed", "argv": argv, "cwd": str(cwd)}
    readers = []
    errors = []
    with (output / (stem + ".stdout.log")).open("xb") as stdout, (output / (stem + ".stderr.log")).open("xb") as stderr:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            result.update(error=str(error), elapsedSeconds=round(time.monotonic() - started, 3),
                          stdoutSha256=digest_file(output / (stem + ".stdout.log")),
                          stderrSha256=digest_file(output / (stem + ".stderr.log")))
            write_json(output / (stem + ".result.json"), result)
            raise Refusal(stem + " could not start; retained command/result are authoritative") from error

        def drain(pipe, destination, live):
            try:
                while block := pipe.read1(65536):
                    destination.write(block)
                    destination.flush()
                    try:
                        live.buffer.write(block)
                        live.flush()
                    except (AttributeError, BrokenPipeError, OSError):
                        pass
            except Exception as error:
                errors.append(type(error).__name__)
                # Popen retains this exact owned process handle; no PID search,
                # global process signal or retry of another compiler is used.
                process.kill()
            finally:
                pipe.close()

        for pipe, destination, live in ((process.stdout, stdout, sys.stdout), (process.stderr, stderr, sys.stderr)):
            thread = threading.Thread(target=drain, args=(pipe, destination, live), daemon=True)
            readers.append(thread)
            thread.start()
        code = process.wait()
        for thread in readers:
            thread.join()
        result.update(returncode=code, elapsedSeconds=round(time.monotonic() - started, 3),
                      status="passed" if code == 0 and not errors else "failed", captureErrors=errors)
    result.update(stdoutSha256=digest_file(output / (stem + ".stdout.log")),
                  stderrSha256=digest_file(output / (stem + ".stderr.log")))
    write_json(output / (stem + ".result.json"), result)
    if result["status"] != "passed":
        raise Refusal(stem + " failed; retained command/stdout/stderr are authoritative")
    return result


def prepare(source, output, target, *, local_run_root=None):
    source = ordinary_path(source, exists=True)
    output = ordinary_path(output, exists=False)
    if inside(output, source) or inside(source, output) or not output.parent.is_dir():
        raise Refusal("new output must have an existing parent and be separate from source")
    config = matrix(target)
    if local_run_root is not None:
        local_admission(local_run_root, source, output, config, prepared=False)
    output.mkdir(mode=0o700)
    if local_run_root is not None and config["host"] == "Windows":
        # NTFS ignores POSIX 0700 as a privacy proof. Inspect the newly created
        # output before writing evidence or copying any source; never chmod ACLs.
        windows_private_entry(output, WindowsSecurityProbe(), directory=True)
    # Reserve evidence before source validation/copying so even an absent bridge
    # or preparation refusal leaves an owned diagnostic artifact, never a retry.
    write_json(output / "prepare.attempt.json", {"schema": SCHEMA, "source": str(source),
               "output": str(output), "config": config, "argv": command(config),
               "driverSha256": digest_file(Path(__file__)), "compiled": False})
    try:
        require_source(source)
        before = inventory(source)
        identity = git_identity(source)
        write_json(output / "source.input.json", {"schema": SCHEMA, "root": str(source),
                   "git": identity, "inventorySha256": binding(before), "entries": before})
        write_json(output / "check.plan.json", {"schema": SCHEMA, "kind": "entire-application-rust-typecheck",
                   "config": config, "argv": command(config), "cwd": str(output / "source"),
                   "cargoTargetDir": str(output / "target"), "sourceInventorySha256": binding(before),
                   "vcpkgCommit": VCPKG_COMMIT, "driverSha256": digest_file(Path(__file__)),
                   "excludedPolicy": {"everywhere": ["worktrees (case insensitive)"], "anchored": {key: sorted(value) for key, value in CACHES.items()}},
                   "limitations": "Typecheck only; full application builds/signing/runtime and all release gates remain mandatory."})
        def ignored(directory, names):
            relative = Path(directory).relative_to(source).as_posix()
            return [name for name in names if excluded(relative, name)]
        shutil.copytree(source, output / "source", symlinks=True, ignore=ignored)
        copied = inventory(output / "source")
        if before != copied or before != inventory(source):
            raise Refusal("source/copy changed during preparation")
        if git_identity(output / "source") != identity:
            raise Refusal("private ordinary source Git identity changed")
        write_json(output / "prepare.result.json", {"status": "prepared", "compiled": False,
                   "sourceUnchanged": True, "copyInventorySha256": binding(copied)})
    except Exception as error:
        write_json(output / "prepare.result.json", {"status": "failed", "compiled": False,
                   "error": str(error)})
        raise
    return source, output, config, before


def execute(source, output, config, before, *, local_run_root=None):
    result = {"status": "failed", "compiled": False}
    try:
        environment = run_environment(config, source, output, os.environ, local_run_root=local_run_root)
        private = output / "source"
        # These identities are real installed shipping tools, not a substituted
        # fixture. They run only after explicit hosted or local admission.
        capture(["rustup", "run", config["toolchain"], "rustc", "-Vv"], private, environment, output, "rustc-identity")
        capture(["rustup", "run", config["toolchain"], "cargo", "-V"], private, environment, output, "cargo-identity")
        capture(command(config), private, environment, output, "cargo-check")
        result.update(status="passed", compiled=True)
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        try:
            after = inventory(source)
            unchanged = before == after
            result.update(sourceUnchanged=unchanged, sourceInventoryAfterSha256=binding(after))
            if not unchanged:
                result.update(status="failed", error="supplied application source changed during cargo check")
        except Exception as error:
            unchanged = False
            result.update(sourceUnchanged=False, status="failed", error=str(error))
        write_json(output / "check.result.json", result)
        if not unchanged:
            raise Refusal("supplied source immutability check failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="complete branded application with restored bridge")
    parser.add_argument("--output", type=Path, required=True, help="new private evidence/source/target directory")
    parser.add_argument("--target", required=True, choices=tuple(MATRIX))
    parser.add_argument("--run", action="store_true", help="run the real cargo check after hosted or explicit local admission")
    parser.add_argument("--local-run-root", type=Path,
                        help="admit a native Mac or Windows run under this repository's owned private ignored build root")
    args = parser.parse_args()
    if args.local_run_root is not None and not args.run:
        parser.error("--local-run-root requires --run")
    try:
        source, output, config, before = prepare(args.source, args.output, args.target, local_run_root=args.local_run_root)
        if args.run:
            execute(source, output, config, before, local_run_root=args.local_run_root)
            print("PASS: real entire-application cargo check; supplied source unchanged", flush=True)
        else:
            print("PREPARED: private application source and exact shipping cargo command; compiler not run", flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        print("FAIL: " + str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
