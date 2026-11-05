#!/usr/bin/env python3
"""Guard the pinned branded Debian service lifecycle without hiding real errors.

Run after the ordinary Debian branding replacements. Only the exact complete
original or complete current three-script profile is accepted. The service unit,
legacy per-user cleanup, alias, dependency list and compiled app stay unchanged.
"""
import argparse
import hashlib
from pathlib import Path
import stat

ORIGINAL_SHA256 = {'preinst': '2a90debf3a6f2c65641be05ffc10aae729881407f3425acee395fc7cebf4f733',
 'prerm': '68fc285b98758214c950d90dafe13889f0c2e5a5701e99f7b7d9d0afd46a6519',
 'postinst': '275de2c0f82af8598efcf5919c5f6a0d87175a67e261a0b190cd6e90b79fc443'}
CURRENT_SHA256 = {'preinst': 'b4464d9b45e73e2fb29706007e9fe4c9eb7b78a70fdc23f561017539ebb4cba2',
 'prerm': 'afddb4b05e9c6008ed8a6a16c603cf6ba9ffa557f6492a16da76ab2c3abe854e',
 'postinst': 'b0e203772a832ec4cfcbd0e45259f2e873661350737acb29c2d8a60279d147c0'}
UNIT_SHA256 = 'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173'
REPLACEMENTS = {'preinst': [('set -e\n',
              'set -e\n'
              '\n'
              '# Only source-proven legacy Mixel overrides may be removed. Administrator\n'
              '# overrides, masks and foreign links retain both their file and enablement.\n'
              'mixel_classify_override() {\n'
              '    mixel_override=/etc/systemd/system/mixel-remote.service\n'
              '    mixel_override_state=absent\n'
              '    if [ -L "$mixel_override" ]; then\n'
              '        mixel_override_target=$(readlink -- "$mixel_override")\n'
              '        case "$mixel_override_target" in\n'
              '            '
              '/usr/lib/systemd/system/mixel-remote.service|/lib/systemd/system/mixel-remote.service)\n'
              '                if [ ! -e "$mixel_override_target" ] && [ ! -L '
              '"$mixel_override_target" ]; then\n'
              '                    mixel_override_state=owned\n'
              '                elif [ -f "$mixel_override_target" ] && [ ! -L '
              '"$mixel_override_target" ]; then\n'
              '                    mixel_override_sum=$(sha256sum -- "$mixel_override_target")\n'
              '                    case "${mixel_override_sum%% *}" in\n'
              '                        '
              'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173|5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410)\n'
              '                            mixel_override_state=owned ;;\n'
              '                        *) mixel_override_state=foreign ;;\n'
              '                    esac\n'
              '                else\n'
              '                    mixel_override_state=foreign\n'
              '                fi\n'
              '                ;;\n'
              '            *) mixel_override_state=foreign ;;\n'
              '        esac\n'
              '    elif [ -f "$mixel_override" ]; then\n'
              '        mixel_override_sum=$(sha256sum -- "$mixel_override")\n'
              '        case "${mixel_override_sum%% *}" in\n'
              '            '
              'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173|5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410)\n'
              '                mixel_override_state=owned ;;\n'
              '            *) mixel_override_state=foreign ;;\n'
              '        esac\n'
              '    elif [ -e "$mixel_override" ]; then\n'
              '        mixel_override_state=foreign\n'
              '    fi\n'
              '    if [ "$mixel_override_state" = foreign ]; then\n'
              "        printf '%s\\n' 'Preserving administrator Mixel service override and "
              "enablement.'\n"
              '    fi\n'
              '}\n'),
             ('\t\t\tservice mixel-remote stop || true',
              '\t\t\tmixel_classify_override\n'
              '\t\t\tif [ "$mixel_override_state" != foreign ] &&\n'
              '\t\t\t   [ -d /run/systemd/system ] && [ -z "${DPKG_ROOT:-}" ]; then\n'
              '\t\t\t    mixel_load_state=$(systemctl show --all mixel-remote.service '
              '--property=LoadState)\n'
              '\t\t\t    case "$mixel_load_state" in\n'
              '\t\t\t        LoadState=loaded|LoadState=masked)\n'
              '\t\t\t            deb-systemd-invoke stop mixel-remote.service\n'
              '\t\t\t            ;;\n'
              '\t\t\t        LoadState=not-found) ;;\n'
              "\t\t\t        *) printf 'Unexpected Mixel service load state: %s\\n' "
              '"$mixel_load_state" >&2; exit 1 ;;\n'
              '\t\t\t    esac\n'
              '\t\t\tfi')],
 'prerm': [('set -e\n',
            'set -e\n'
            '\n'
            '# Only source-proven legacy Mixel overrides may be removed. Administrator\n'
            '# overrides, masks and foreign links retain both their file and enablement.\n'
            'mixel_classify_override() {\n'
            '    mixel_override=/etc/systemd/system/mixel-remote.service\n'
            '    mixel_override_state=absent\n'
            '    if [ -L "$mixel_override" ]; then\n'
            '        mixel_override_target=$(readlink -- "$mixel_override")\n'
            '        case "$mixel_override_target" in\n'
            '            '
            '/usr/lib/systemd/system/mixel-remote.service|/lib/systemd/system/mixel-remote.service)\n'
            '                if [ ! -e "$mixel_override_target" ] && [ ! -L '
            '"$mixel_override_target" ]; then\n'
            '                    mixel_override_state=owned\n'
            '                elif [ -f "$mixel_override_target" ] && [ ! -L '
            '"$mixel_override_target" ]; then\n'
            '                    mixel_override_sum=$(sha256sum -- "$mixel_override_target")\n'
            '                    case "${mixel_override_sum%% *}" in\n'
            '                        '
            'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173|5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410)\n'
            '                            mixel_override_state=owned ;;\n'
            '                        *) mixel_override_state=foreign ;;\n'
            '                    esac\n'
            '                else\n'
            '                    mixel_override_state=foreign\n'
            '                fi\n'
            '                ;;\n'
            '            *) mixel_override_state=foreign ;;\n'
            '        esac\n'
            '    elif [ -f "$mixel_override" ]; then\n'
            '        mixel_override_sum=$(sha256sum -- "$mixel_override")\n'
            '        case "${mixel_override_sum%% *}" in\n'
            '            '
            'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173|5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410)\n'
            '                mixel_override_state=owned ;;\n'
            '            *) mixel_override_state=foreign ;;\n'
            '        esac\n'
            '    elif [ -e "$mixel_override" ]; then\n'
            '        mixel_override_state=foreign\n'
            '    fi\n'
            '    if [ "$mixel_override_state" = foreign ]; then\n'
            "        printf '%s\\n' 'Preserving administrator Mixel service override and "
            "enablement.'\n"
            '    fi\n'
            '}\n'),
           ('\t\t\tsystemctl stop mixel-remote || true\n'
            '\t\t\tsystemctl disable mixel-remote || true\n'
            '\t\t\trm /etc/systemd/system/mixel-remote.service '
            '/usr/lib/systemd/system/mixel-remote.service || true',
            '\t\t\tmixel_classify_override\n'
            '\t\t\tif [ "$mixel_override_state" != foreign ] &&\n'
            '\t\t\t   [ -d /run/systemd/system ] && [ -z "${DPKG_ROOT:-}" ]; then\n'
            '\t\t\t    mixel_load_state=$(systemctl show --all mixel-remote.service '
            '--property=LoadState)\n'
            '\t\t\t    case "$mixel_load_state" in\n'
            '\t\t\t        LoadState=loaded|LoadState=masked)\n'
            '\t\t\t            deb-systemd-invoke stop mixel-remote.service\n'
            '\t\t\t            systemctl disable mixel-remote\n'
            '\t\t\t            ;;\n'
            '\t\t\t        LoadState=not-found) ;;\n'
            "\t\t\t        *) printf 'Unexpected Mixel service load state: %s\\n' "
            '"$mixel_load_state" >&2; exit 1 ;;\n'
            '\t\t\t    esac\n'
            '\t\t\tfi\n'
            '\t\t\tif [ "$mixel_override_state" = owned ]; then\n'
            '\t\t\t\trm -f -- /etc/systemd/system/mixel-remote.service\n'
            '\t\t\tfi\n'
            '\t\t\tif [ "$mixel_override_state" != foreign ]; then\n'
            '\t\t\t\trm -f -- /usr/lib/systemd/system/mixel-remote.service\n'
            '\t\t\tfi')],
 'postinst': [('set -e\n',
               'set -e\n'
               '\n'
               '# Only source-proven legacy Mixel overrides may be removed. Administrator\n'
               '# overrides, masks and foreign links retain both their file and enablement.\n'
               'mixel_classify_override() {\n'
               '    mixel_override=/etc/systemd/system/mixel-remote.service\n'
               '    mixel_override_state=absent\n'
               '    if [ -L "$mixel_override" ]; then\n'
               '        mixel_override_target=$(readlink -- "$mixel_override")\n'
               '        case "$mixel_override_target" in\n'
               '            '
               '/usr/lib/systemd/system/mixel-remote.service|/lib/systemd/system/mixel-remote.service)\n'
               '                if [ ! -e "$mixel_override_target" ] && [ ! -L '
               '"$mixel_override_target" ]; then\n'
               '                    mixel_override_state=owned\n'
               '                elif [ -f "$mixel_override_target" ] && [ ! -L '
               '"$mixel_override_target" ]; then\n'
               '                    mixel_override_sum=$(sha256sum -- "$mixel_override_target")\n'
               '                    case "${mixel_override_sum%% *}" in\n'
               '                        '
               'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173|5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410)\n'
               '                            mixel_override_state=owned ;;\n'
               '                        *) mixel_override_state=foreign ;;\n'
               '                    esac\n'
               '                else\n'
               '                    mixel_override_state=foreign\n'
               '                fi\n'
               '                ;;\n'
               '            *) mixel_override_state=foreign ;;\n'
               '        esac\n'
               '    elif [ -f "$mixel_override" ]; then\n'
               '        mixel_override_sum=$(sha256sum -- "$mixel_override")\n'
               '        case "${mixel_override_sum%% *}" in\n'
               '            '
               'd5f9c6da03e3a87b77687d8282b6615c209aea3ea5bca6fbf9fe3df8074c2173|5f9370df93f15c416ed7c1cd2803387a28c3baaa90027ed8fbe447056aba2410)\n'
               '                mixel_override_state=owned ;;\n'
               '            *) mixel_override_state=foreign ;;\n'
               '        esac\n'
               '    elif [ -e "$mixel_override" ]; then\n'
               '        mixel_override_state=foreign\n'
               '    fi\n'
               '    if [ "$mixel_override_state" = foreign ]; then\n'
               "        printf '%s\\n' 'Preserving administrator Mixel service override and "
               "enablement.'\n"
               '    fi\n'
               '}\n'),
              ('\t\tif [ -e /etc/systemd/system/mixel-remote.service ]; then\n'
               '\t\t\trm -f /etc/systemd/system/mixel-remote.service '
               '/usr/lib/systemd/system/mixel-remote.service '
               '/usr/lib/systemd/user/mixel-remote.service >/dev/null  2>&1\n'
               '\t\tfi',
               '\t\tmixel_classify_override\n'
               '\t\tif [ "$mixel_override_state" != foreign ]; then\n'
               '\t\tif [ "$mixel_override_state" = owned ]; then\n'
               '\t\t\trm -f -- /etc/systemd/system/mixel-remote.service\n'
               '\t\tfi'),
              ('\t\tsystemctl daemon-reload\n'
               '\t\tsystemctl enable mixel-remote\n'
               '\t\tsystemctl start mixel-remote',
               '\t\tif [ "$mixel_override_state" != foreign ] &&\n'
               '\t\t   [ -d /run/systemd/system ] && [ -z "${DPKG_ROOT:-}" ]; then\n'
               '\t\t\tsystemctl daemon-reload\n'
               '\t\t\tsystemctl enable mixel-remote\n'
               '\t\t\tdeb-systemd-invoke start mixel-remote.service\n'
               '\t\tfi\n'
               '\t\tfi')]}


def logical(raw: bytes) -> tuple[bytes, bytes]:
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    value = raw.replace(b"\r\n", b"\n")
    if b"\r" in value or value.replace(b"\n", newline) != raw:
        raise RuntimeError("Mixed Debian maintainer newlines")
    return value, newline


def transform(scripts: dict[str, bytes], restore: bool = False) -> dict[str, bytes]:
    if set(scripts) != set(ORIGINAL_SHA256):
        raise RuntimeError("Pinned Debian maintainer file inventory changed")
    prepared = {}
    states = []
    for name, raw in scripts.items():
        value, newline = logical(raw)
        digest = hashlib.sha256(value).hexdigest()
        if digest == ORIGINAL_SHA256[name]:
            current = False
        elif digest == CURRENT_SHA256[name]:
            current = True
        else:
            raise RuntimeError("Pinned Debian maintainer bytes changed: " + name)
        states.append(current)
        pairs = REPLACEMENTS[name]
        text = value.decode("utf-8")
        if current != (not restore):
            for before, after in reversed(pairs) if restore else pairs:
                old, new = (after, before) if restore else (before, after)
                if text.count(old) != 1:
                    raise RuntimeError("Pinned Debian maintainer expression changed: " + name)
                text = text.replace(old, new)
        expected = ORIGINAL_SHA256[name] if restore else CURRENT_SHA256[name]
        result = text.encode("utf-8")
        if hashlib.sha256(result).hexdigest() != expected:
            raise RuntimeError("Debian maintainer transformation did not conserve bytes: " + name)
        prepared[name] = result.replace(b"\n", newline)
    if len(set(states)) != 1:
        raise RuntimeError("Partial Debian service lifecycle profile")
    return prepared


def apply(root: Path, restore: bool = False, check: bool = False) -> None:
    directory = root / "res" / "DEBIAN"
    if root.is_symlink() or (root / "res").is_symlink() or directory.is_symlink():
        raise RuntimeError("Debian maintainer directory must not be a symlink")
    unit = root / "res" / "mixel-remote.service"
    unit_info = unit.lstat()
    if (not stat.S_ISREG(unit_info.st_mode) or unit_info.st_nlink != 1
            or unit_info.st_size > 65536 or hashlib.sha256(logical(unit.read_bytes())[0]).hexdigest() != UNIT_SHA256):
        raise RuntimeError("Pinned Mixel systemd unit bytes changed")
    original = {}
    for name in ORIGINAL_SHA256:
        path = directory / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise RuntimeError("Debian maintainer must be a bounded unshared regular file: " + name)
        original[name] = path.read_bytes()
    changed = transform(original, restore)
    if not check:
        for name, value in changed.items():
            if original[name] != value:
                (directory / name).write_bytes(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    apply(args.root, args.restore, args.check)


if __name__ == "__main__":
    main()
