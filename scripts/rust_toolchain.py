"""Select the installed native MSVC linker without trusting Git Bash PATH."""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Mapping, Sequence


def read_windows_version_info(executable: Path) -> dict[str, str]:
    """Read native PE identity without locale-dependent compiler console text."""
    if os.name != "nt":
        raise RuntimeError("Native Windows version metadata requires Windows")
    import ctypes
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                                     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    handle = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(executable), ctypes.byref(handle))
    if not 0 < size <= 1048576:
        raise RuntimeError("Installed linker has absent or oversized native version metadata")
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(executable), 0, size, data):
        raise RuntimeError("Installed linker native version metadata could not be read")
    pointer = ctypes.c_void_p(); length = wintypes.UINT()
    if not version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)) or not pointer.value or length.value < 4:
        raise RuntimeError("Installed linker native version translation is absent")
    translation = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD))
    prefix = f"\\StringFileInfo\\{translation[0]:04x}{translation[1]:04x}\\"
    identity = {}
    for name in ("CompanyName", "OriginalFilename"):
        if not version.VerQueryValueW(data, prefix + name, ctypes.byref(pointer), ctypes.byref(length)) or not pointer.value or not 1 < length.value <= 256:
            raise RuntimeError("Installed linker native version identity is absent or oversized")
        value = ctypes.wstring_at(pointer, length.value - 1)
        if any(ord(character) < 32 for character in value):
            raise RuntimeError("Installed linker native version identity is invalid")
        identity[name] = value
    return identity


def verify_msvc_linker(linker: Path) -> None:
    # cl/link share the developer environment's explicit x64 tool directory.
    # Both are real PE files; then qualify the actual linker rather than a
    # same-named coreutils executable appearing earlier on PATH. Version
    # resources avoid locale, banner suppression and help exit-code differences.
    for executable in (linker, linker.with_name("cl.exe")):
        if not executable.is_file() or executable.is_symlink():
            raise RuntimeError("Installed x64 MSVC tool is absent or indirect")
        with executable.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise RuntimeError("Installed x64 MSVC tool is not a PE executable")
    identity = read_windows_version_info(linker)
    if identity.get("CompanyName") != "Microsoft Corporation" or identity.get("OriginalFilename", "").casefold() != "link.exe":
        raise RuntimeError("Installed linker is not the native Microsoft LINK executable; private metadata withheld")


def windows_environment_value(environment: Mapping[str, str], name: str) -> str:
    # Native os.environ ignores Windows key casing, but dict(os.environ) has
    # uppercase keys and loses that behavior. Keep the same semantics when
    # fixtures prepend a shadowing PATH, and reject conflicting synthetic aliases.
    values = [value for key, value in environment.items() if key.casefold() == name.casefold()]
    if values and any(value != values[0] for value in values):
        raise RuntimeError("Conflicting Windows environment aliases for " + name)
    return values[0] if values else ""


def find_msvc_linker(environment: Mapping[str, str], *, verifier=verify_msvc_linker) -> Path:
    directory = windows_environment_value(environment, "VCToolsInstallDir")
    if not directory or any(character in directory for character in "\r\n\x00"):
        raise RuntimeError("Activate the installed x64 MSVC developer environment before compiling Windows fixtures")
    tools = Path(directory)
    if not tools.is_absolute() or not tools.is_dir() or tools.is_symlink():
        raise RuntimeError("Installed MSVC tools directory is not an absolute native directory")
    linker = tools / "bin/Hostx64/x64/link.exe"
    # Resolve every directory component too, so an alias cannot move selection
    # outside the discovered toolchain while retaining the expected basename.
    if linker.resolve() != tools.resolve() / "bin/Hostx64/x64/link.exe":
        raise RuntimeError("Installed x64 MSVC linker path is indirect")
    configured = windows_environment_value(environment, "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER")
    if configured and Path(configured).resolve() != linker.resolve():
        raise RuntimeError("Cargo and direct Rust fixtures select different Windows linkers")
    verifier(linker)
    return linker


def rustc_command(command: Sequence[str], *, windows: bool | None = None,
                  environment: Mapping[str, str] | None = None, verifier=verify_msvc_linker) -> list[str]:
    """Keep compiler/flags intact, with an explicit native Windows linker."""
    result = list(command)
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return result
    if not result:
        raise RuntimeError("Rust compiler command is empty")
    linker = str(find_msvc_linker(os.environ if environment is None else environment, verifier=verifier))
    existing = []
    for index, argument in enumerate(result):
        if argument.startswith("-Clinker="):
            existing.append(argument.removeprefix("-Clinker="))
        elif argument == "-C" and index + 1 < len(result) and result[index + 1].startswith("linker="):
            existing.append(result[index + 1].removeprefix("linker="))
    if existing:
        if len(existing) != 1 or Path(existing[0]).resolve() != Path(linker).resolve():
            raise RuntimeError("Rust fixture linker override conflicts with installed x64 MSVC")
        return result
    return result + ["-C", "linker=" + linker]
