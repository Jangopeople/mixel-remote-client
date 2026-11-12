#!/usr/bin/env python3
"""Security update of the Quick Support 1.4.6 Cargo.lock (osv-scanner, 2026-11-12).

Same-major updates only, each one still building with the pinned Rust 1.75:
  rustls 0.23.28 -> 0.23.46, rustls-webpki 0.103.3 -> 0.103.16 (+ rustls-pki-types 1.16.0),
  tokio 1.44.2 -> 1.51.6 (+ mio, libc, socket2, tokio-macros), bytes 1.10.1 -> 1.11.1,
  crossbeam-channel 0.5.13 -> 0.5.15, crossbeam-epoch 0.9.18 -> 0.9.20,
  webpki-roots 1.0.4 -> 1.0.10 and 0.26.9 -> 0.26.11, anyhow 1.0.103, memmap2 0.9.11,
  tracing-subscriber 0.3.20, rand 0.8.6 / 0.9.3, zstd-sys 2.1.2,
  openssl 0.10.68 -> 0.10.78 + openssl-sys 0.9.114 (Linux only; 0.10.79+ needs Rust 1.80).
Left on purpose: time 0.3.47+ and zip 9 need a newer Rust than 1.75; idna 1.x, quick-xml,
ringbuf and zstd-safe are major updates of upstream dependencies.

The update is stored per package (scripts/security/cargo-lock-20261112.json), not as a text
diff, so it applies to the Windows tree (vendored path dependencies) and to the Mac/Linux tree
(git dependencies) alike: removed packages, added packages, and for changed packages the new
version, checksum and dependency list (a package's own `source` line is kept). Every package
it touches must be there exactly once (added ones must be absent); otherwise nothing is written.

Usage: patch-security-lock.py [--check] [--restore] <rustdesk-root>
       patch-security-lock.py --generate <old Cargo.lock> <new Cargo.lock>   (writes the JSON)
Line endings of the lock are kept.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SPEC = Path(__file__).resolve().parent / "security" / "cargo-lock-20261112.json"
MARK = "[[package]]\n"


def split(text: str) -> tuple[str, list[str], str]:
    """Header, package blocks (without the marker), trailer (a final [metadata] table)."""
    head, *blocks = text.split(MARK)
    trailer = ""
    if blocks:
        last = blocks[-1]
        cut = last.find("\n[")
        if cut >= 0:
            blocks[-1], trailer = last[:cut + 1], last[cut + 1:]
    return head, blocks, trailer


def fields(block: str) -> dict:
    out: dict = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("dependencies = ["):
            deps = []
            i += 1
            while lines[i] != "]":
                deps.append(lines[i].strip().rstrip(",").strip('"'))
                i += 1
            out["dependencies"] = deps
        elif " = " in line:
            name, value = line.split(" = ", 1)
            out[name] = value.strip('"')
        i += 1
    return out


def key(block: str) -> str:
    f = fields(block)
    return f"{f['name']} {f['version']}"


def render(target: dict, template: str) -> str:
    """The template block with the target's version, checksum and dependencies."""
    lines = template.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("version = "):
            out.append(f'version = "{target["version"]}"')
        elif line.startswith("checksum = "):
            pass
        elif line.startswith("dependencies = ["):
            while lines[i] != "]":
                i += 1
        else:
            out.append(line)
        i += 1
    while out and out[-1] == "":
        out.pop()
    if "checksum" in target:
        out.append(f'checksum = "{target["checksum"]}"')
    if target["dependencies"]:
        out.append("dependencies = [")
        out.extend(f' "{d}",' for d in target["dependencies"])
        out.append("]")
    return "\n".join(out) + "\n\n"


def direction(old: str, new: str) -> dict:
    _, a, _ = split(old)
    _, b, _ = split(new)
    ka = {key(x): x for x in a}
    kb = {key(x): x for x in b}
    order = [key(x) for x in b]
    changed: dict = {}
    for k, block in ka.items():
        if k in kb and kb[k] != block:
            f = fields(kb[k])
            changed[k] = {"version": f["version"], "dependencies": f.get("dependencies", [])}
            if "checksum" in f:
                changed[k]["checksum"] = f["checksum"]
    removed = sorted(k for k in ka if k not in kb)
    added = {}
    for k, block in kb.items():
        if k not in ka:
            position = order.index(k)
            added[k] = {"block": block, "after": order[position - 1] if position else None}
    return {"changed": changed, "removed": removed, "added": added}


def apply(text: str, spec: dict) -> str:
    head, blocks, trailer = split(text)
    keys = [key(b) for b in blocks]
    for k in list(spec["changed"]) + spec["removed"]:
        if keys.count(k) != 1:
            raise ValueError(f"expected {k} exactly once, found {keys.count(k)}")
    for k in spec["added"]:
        if k in keys:
            raise ValueError(f"{k} is already there")
    removed = set(spec["removed"])
    out: list[str] = []
    for block, k in zip(blocks, keys):
        if k in removed:
            continue
        out.append(render(spec["changed"][k], block) if k in spec["changed"] else block)
    pending = dict(spec["added"])
    while pending:
        progress = False
        for k, entry in list(pending.items()):
            current = [key(b) for b in out]
            if entry["after"] is None:
                out.insert(0, entry["block"])
            elif entry["after"] in current:
                out.insert(current.index(entry["after"]) + 1, entry["block"])
            else:
                continue
            del pending[k]
            progress = True
        if not progress:
            raise ValueError(f"no anchor for {sorted(pending)[:3]}")
    if trailer == "" and out:
        out[-1] = out[-1].rstrip("\n") + "\n"
    return head + "".join(MARK + b for b in out) + trailer


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--generate"] and len(arguments) == 3:
        old = Path(arguments[1]).read_text(encoding="utf-8").replace("\r\n", "\n")
        new = Path(arguments[2]).read_text(encoding="utf-8").replace("\r\n", "\n")
        spec = {"apply": direction(old, new), "restore": direction(new, old)}
        if apply(old, spec["apply"]) != new or apply(new, spec["restore"]) != old:
            print("REFUSED: generated update does not reproduce both locks")
            return 1
        SPEC.parent.mkdir(exist_ok=True)
        SPEC.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {SPEC.name}: {len(spec['apply']['changed'])} changed, "
              f"{len(spec['apply']['added'])} added, {len(spec['apply']['removed'])} removed")
        return 0
    flags = {value for value in arguments if value.startswith("--")}
    roots = [value for value in arguments if not value.startswith("--")]
    if len(roots) != 1 or flags - {"--check", "--restore"}:
        print(__doc__)
        return 2
    restore = "--restore" in flags
    lock = Path(roots[0]) / "Cargo.lock"
    raw = lock.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    forward, backward = (spec["restore"], spec["apply"]) if restore else (spec["apply"], spec["restore"])
    try:
        result = apply(text, forward)
    except ValueError as error:
        try:
            apply(text, backward)
        except ValueError:
            print(f"REFUSED: {error}")
            return 1
        print("nothing to restore" if restore else "already applied")
        return 0
    if "--check" in flags:
        print(f"check ok: {'restore' if restore else 'apply'} would change Cargo.lock")
        return 0
    lock.write_bytes((result.replace("\n", "\r\n") if crlf else result).encode("utf-8"))
    print(f"{'restored' if restore else 'patched'} Cargo.lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
