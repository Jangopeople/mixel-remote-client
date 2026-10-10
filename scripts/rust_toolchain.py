"""Select the installed native MSVC linker without trusting Git Bash PATH."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from collections.abc import Mapping, Sequence


def verify_msvc_linker(linker: Path) -> None:
    # cl/link share the developer environment's explicit x64 tool directory.
    # Both are real PE files; then qualify the actual linker rather than a
    # same-named coreutils executable appearing earlier on PATH.
    for executable in (linker, linker.with_name("cl.exe")):
        if not executable.is_file() or executable.is_symlink():
            raise RuntimeError("Installed x64 MSVC tool is absent or indirect")
        with executable.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise RuntimeError("Installed x64 MSVC tool is not a PE executable")
    result = subprocess.run([str(linker), "/?"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=10)
    output = result.stdout + result.stderr
    if result.returncode != 0 or len(output) > 131072 or "Microsoft" not in output or "Incremental Linker" not in output:
        raise RuntimeError("Installed Microsoft linker failed native qualification; private output withheld")


def find_msvc_linker(environment: Mapping[str, str], *, verifier=verify_msvc_linker) -> Path:
    directory = environment.get("VCToolsInstallDir", "")
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
    configured = environment.get("CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER")
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
