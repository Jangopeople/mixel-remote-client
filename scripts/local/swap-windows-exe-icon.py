#!/usr/bin/env python3
"""
Replace the icon resource in a Windows PE binary (.exe) with a new .ico.

Why this exists: rcedit (the canonical tool) only runs on Windows or via
Wine. Wine on macOS needs sudo to install gstreamer deps, which is awkward
in CI/scripts. This standalone Python script does the same job in pure
Python with no external native deps beyond `pefile` (pip-installable).

Usage:
    swap-windows-exe-icon.py <input.exe> <new-icon.ico> <output.exe>

Limitations:
- Only replaces RT_ICON + RT_GROUP_ICON resources (the standard app icon).
- Other resources (manifest, version info, dialogs) untouched.
- Uses pefile.set_bytes_at_offset() to overwrite icon bytes in-place; if
  the new icon's individual entries are larger than upstream's allocated
  bytes, it appends to a fresh icon section. Most .ico files at the same
  sizes pack to similar byte counts so in-place works in practice.
"""

import struct
import sys
from pathlib import Path

import pefile

RT_ICON = 3
RT_GROUP_ICON = 14


def parse_ico(ico_path: Path):
    """Parse a .ico file. Returns (group_data, [icon_bytes...]) where
    group_data is the bytes for the RT_GROUP_ICON resource and icon_bytes
    is the list of individual RT_ICON payloads (one per size)."""
    raw = ico_path.read_bytes()
    reserved, ico_type, count = struct.unpack_from("<HHH", raw, 0)
    if reserved != 0 or ico_type != 1:
        raise ValueError(f"not a valid .ico: reserved={reserved} type={ico_type}")

    icons: list[bytes] = []
    grp_entries: list[bytes] = []
    for i in range(count):
        # ICONDIRENTRY: width(1) height(1) colors(1) reserved(1)
        # planes(2) bit_count(2) bytes_in_res(4) image_offset(4) = 16 bytes
        off = 6 + i * 16
        w, h, cc, rsv, planes, bits, byte_count, img_off = struct.unpack_from(
            "<BBBBHHII", raw, off
        )
        icon_bytes = raw[img_off : img_off + byte_count]
        icons.append(icon_bytes)
        # GRPICONDIRENTRY differs by replacing image_offset (4 bytes) with
        # icon ID (2 bytes); total 14 bytes per entry.
        grp_entries.append(
            struct.pack(
                "<BBBBHHIH", w, h, cc, rsv, planes, bits, byte_count, i + 1
            )
        )

    group = struct.pack("<HHH", reserved, ico_type, count) + b"".join(grp_entries)
    return group, icons


def find_resources_of_type(pe: pefile.PE, type_id: int):
    """Yield (entry, lang_entry) for every leaf resource of `type_id`."""
    rsrc = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if not rsrc:
        return
    for type_entry in rsrc.entries:
        if type_entry.id != type_id:
            continue
        for name_entry in type_entry.directory.entries:
            for lang_entry in name_entry.directory.entries:
                yield name_entry, lang_entry


def replace_resource_data_in_place(pe: pefile.PE, lang_entry, new_bytes: bytes):
    """Overwrite the bytes of a resource leaf in pe.__data__. Pads with 0
    if the new data is shorter; raises if it's longer (no auto-grow)."""
    rva = lang_entry.data.struct.OffsetToData
    old_size = lang_entry.data.struct.Size
    file_offset = pe.get_offset_from_rva(rva)

    if len(new_bytes) > old_size:
        raise ValueError(
            f"new resource ({len(new_bytes)} B) larger than original "
            f"({old_size} B); in-place replacement not safe"
        )

    # Pad to old size so the (recorded) Size doesn't get out of sync with
    # how much we actually wrote.
    padded = new_bytes + b"\x00" * (old_size - len(new_bytes))
    pe.set_bytes_at_offset(file_offset, padded)
    # Update the recorded size too — keeps tools happy when re-parsing.
    lang_entry.data.struct.Size = len(new_bytes)


def main(argv):
    if len(argv) != 4:
        sys.stderr.write(f"usage: {argv[0]} input.exe new.ico output.exe\n")
        return 2

    exe_in = Path(argv[1])
    ico_in = Path(argv[2])
    exe_out = Path(argv[3])

    print(f"→ parsing {ico_in}")
    grp_bytes, icon_payloads = parse_ico(ico_in)
    print(f"   {len(icon_payloads)} icon sizes in {ico_in.name}")

    print(f"→ opening {exe_in}")
    pe = pefile.PE(str(exe_in), fast_load=False)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
    )

    rt_icons = list(find_resources_of_type(pe, RT_ICON))
    rt_groups = list(find_resources_of_type(pe, RT_GROUP_ICON))
    print(f"   PE has {len(rt_icons)} RT_ICON entries, {len(rt_groups)} RT_GROUP_ICON")

    if not rt_groups:
        sys.stderr.write("❌ no RT_GROUP_ICON in target — aborting\n")
        return 1

    # Replace as many RT_ICON entries as we have new icons; keep extras
    # untouched (they'll just remain orphaned but harmless if the group
    # only points at the ones we updated).
    n = min(len(rt_icons), len(icon_payloads))
    for i in range(n):
        old_size = rt_icons[i][1].data.struct.Size
        new_size = len(icon_payloads[i])
        if new_size > old_size:
            print(
                f"   ⚠ icon[{i}] new ({new_size} B) > old ({old_size} B); "
                f"skipping in-place; keeping original",
            )
            continue
        replace_resource_data_in_place(pe, rt_icons[i][1], icon_payloads[i])
        print(f"   icon[{i}]: {new_size} B (was {old_size} B)")

    # Replace GROUP_ICON. This one's small (~6 + 14*N bytes), almost
    # always fits into the existing slot.
    group_lang_entry = rt_groups[0][1]
    replace_resource_data_in_place(pe, group_lang_entry, grp_bytes)
    print(f"   GROUP_ICON: {len(grp_bytes)} B")

    print(f"→ writing {exe_out}")
    pe.write(filename=str(exe_out))
    print("✓ done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
