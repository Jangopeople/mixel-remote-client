"""Resolve SDK tools without forwarding Windows PATHEXT casing to Dart."""
from __future__ import annotations

import os
import shutil
from collections.abc import Mapping


def normalize_dart_executable(executable: str | None, *, windows: bool | None = None) -> str | None:
    if executable is None:
        return None
    if windows is None:
        windows = os.name == "nt"
    stem, suffix = os.path.splitext(executable)
    # shutil.which expands PATHEXT entries as written (typically .EXE).
    # Dart 3.5's analyzer recognizes only lowercase .exe and otherwise tries
    # spawning dart.EXE.exe. Windows paths are case-insensitive, so normalize
    # the executable suffix without changing case-sensitive Unix paths.
    if windows and suffix.lower() == ".exe":
        return stem + ".exe"
    return executable


def find_dart(environment: Mapping[str, str] | None = None, *, windows: bool | None = None,
              finder=shutil.which) -> str | None:
    if environment is None:
        environment = os.environ
    return normalize_dart_executable(environment.get("DART_BIN") or finder("dart"), windows=windows)
