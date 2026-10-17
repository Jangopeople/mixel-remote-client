#!/usr/bin/env python3
"""Give every Debian archive member root ownership without chowning staging files."""
import argparse
import ast
from pathlib import Path


def transform(raw: bytes) -> bytes:
    logical = raw.replace(b"\r\n", b"\n")
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    if b"\r" in logical or logical.replace(b"\n", newline) != raw:
        raise RuntimeError("Mixed Debian builder newlines")
    code = logical.decode("utf-8")
    tree = ast.parse(code)
    package = "mixel-remote.deb"
    commands = {
        "build_flutter_deb": "dpkg-deb -b tmpdeb " + package + ";",
        "build_deb_from_folder": "dpkg-deb -b tmpdeb " + package + ";",
        "main": "dpkg-deb -b tmpdeb " + package + "; /bin/rm -rf tmpdeb/",
    }
    extraction = "dpkg-deb -R " + package + " tmpdeb"
    states = []
    replacements = []
    for name, original in commands.items():
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(functions) != 1:
            raise RuntimeError("Pinned Debian assembly function changed: " + name)
        calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
                 and any(isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                         and "dpkg-deb" in arg.value and arg.value != extraction for arg in node.args)]
        if len(calls) != 1:
            raise RuntimeError("Pinned Debian assembly statement changed: " + name)
        call = calls[0]
        current = original.replace("dpkg-deb -b ", "dpkg-deb --root-owner-group -b ", 1)
        if not (isinstance(call.func, ast.Name) and call.func.id == "system2"
                and len(call.args) == 1 and not call.keywords
                and isinstance(call.args[0], ast.Constant) and call.args[0].value in (original, current)):
            raise RuntimeError("Pinned Debian assembly command changed: " + name)
        value = call.args[0].value
        if ast.get_source_segment(code, call) != "system2(" + repr(value) + ")":
            raise RuntimeError("Pinned Debian assembly expression changed: " + name)
        states.append(value == current)
        replacements.append(("system2(" + repr(original) + ")", "system2(" + repr(current) + ")"))
    # The one extraction command stays unchanged; no unreviewed additional
    # assembly call, dynamic command, or duplicate literal may enter the build.
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
              and isinstance(node.value, str) and "dpkg-deb" in node.value]
    expected = [commands[name].replace("dpkg-deb -b ", "dpkg-deb --root-owner-group -b ", 1)
                if states[index] else commands[name] for index, name in enumerate(commands)] + [extraction]
    if sorted(values) != sorted(expected) or code.count("dpkg-deb") != 4:
        raise RuntimeError("Pinned Debian command inventory changed")
    if len(set(states)) != 1:
        raise RuntimeError("Mixed Debian archive ownership state")
    if not states[0]:
        for original, current in set(replacements):
            expected_count = 2 if original.endswith("mixel-remote.deb;')") else 1
            if code.count(original) != expected_count or current in code:
                raise RuntimeError("Pinned Debian assembly literal changed")
            code = code.replace(original, current)
    return code.encode("utf-8").replace(b"\n", newline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_script", type=Path)
    args = parser.parse_args()
    original = args.build_script.read_bytes()
    current = transform(original)
    if current != original:
        args.build_script.write_bytes(current)


if __name__ == "__main__":
    main()
