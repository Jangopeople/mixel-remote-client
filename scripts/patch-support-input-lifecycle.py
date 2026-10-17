#!/usr/bin/env python3
"""Apply the reviewed desktop input lifetime layer after complete branding."""
import argparse
import contextlib
import hashlib
from pathlib import Path
import stat

SCRIPTS = Path(__file__).absolute().parent
ENGINE_NAME = "support-input-lifecycle-engine.py"
ENGINE_SHA = "984f784824f04b50969452bb4d2df861f4a962c1037ac45b14621a35c25f22c9"
PLAN_NAME = "support-input-lifecycle-plan.json"
PLAN_SHA = "2111e8c4877dcc59bfaab6ee21681ca13812ff4da42c2b71b6d1e9583a1e5f93"


def namespace():
    path = SCRIPTS / ENGINE_NAME
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= 100_000
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise RuntimeError("Unsafe desktop input source engine")
    raw = path.read_bytes()
    logical = raw.replace(b"\r\n", b"\n")
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    if (b"\r" in logical or logical.replace(b"\n", newline) != raw
            or hashlib.sha256(logical).hexdigest() != ENGINE_SHA):
        raise RuntimeError("Changed desktop input source engine")
    result = {"__name__": "mixel_input_lifecycle_engine", "__file__": str(path)}
    exec(compile(logical.decode("utf-8"), str(path), "exec"), result)
    return result


def source_plan(engine):
    return engine["load_plan"](SCRIPTS / PLAN_NAME, PLAN_SHA)


def has_layer(root):
    """Recognize only a complete, pinned final source profile."""
    engine = namespace()
    profile, _ = engine["inspect"](root, source_plan(engine))
    return profile == "final"


@contextlib.contextmanager
def previous(root):
    """Validate all owners and expose the exact prior source privately."""
    engine = namespace()
    with engine["previous"](root, source_plan(engine)) as stage:
        yield stage


def apply(root, restore=False, check=False):
    engine = namespace()
    return engine["apply"](root, source_plan(engine), check=check, restore=restore)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = apply(args.root, restore=args.restore, check=args.check)
    print("Desktop input lifetime verified: " + str(result))


if __name__ == "__main__":
    main()
