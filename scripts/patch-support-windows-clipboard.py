#!/usr/bin/env python3
"""Make pinned Windows clipboard resource cleanup belong to one context."""
import hashlib
import os
from pathlib import Path

SOURCE = "libs/clipboard/src/windows/wf_cliprdr.c"
SOURCE_SHA256 = "aeb846d47e3f74a7bb53f8d7800ac0b4ec4cc884b0ba8de763f8ad6009211501"
MARKER = "/* Mixel: only the owning context may release clipboard resources. */"

REPLACEMENTS = (
    ("\tHANDLE thread;\n\tHANDLE formatDataRespEvent;",
     "\tHANDLE thread;\n"
     "\t/* Mixel: publish shutdown before a late clipboard window can wait. */\n"
     "\tLONG stopping;\n\tHANDLE formatDataRespEvent;"),
    ('\tif (!(clipboard->data_obj_mutex = CreateMutex(NULL, FALSE, "data_obj_mutex")))',
     '\t/* Mixel: this data object is shared only by threads of this context. */\n'
     '\tif (!(clipboard->data_obj_mutex = CreateMutex(NULL, FALSE, NULL)))'),
    ("\tclipboard->context = cliprdr;\n\tclipboard->sync = FALSE;",
     "\t/* Mixel: a second context must not overwrite live native resources. */\n"
     "\tif (clipboard->context)\n\t\treturn FALSE;\n\n"
     "\tclipboard->context = cliprdr;\n\tclipboard->sync = FALSE;"),
    ("\tclipboard->copied = FALSE;\n\n\tif (clipboard->hUser32)",
     "\tclipboard->copied = FALSE;\n\tclipboard->stopping = FALSE;\n\n\tif (clipboard->hUser32)"),
    ("\tclipboard->copied = FALSE;\n\tcliprdr->Custom = NULL;",
     "\t" + MARKER + "\n"
     "\tif (clipboard->context != cliprdr)\n\t\treturn TRUE;\n\n"
     "\tclipboard->copied = FALSE;\n\tcliprdr->Custom = NULL;"),
    ("\tif (clipboard->hwnd)\n\t\tPostMessage(clipboard->hwnd, WM_QUIT, 0, 0);",
     "\t/* Mixel: the paired worker barrier publishes HWND before this read. */\n"
     "\tInterlockedExchange(&clipboard->stopping, TRUE);\n"
     "\tif (clipboard->hwnd)\n\t\tPostMessage(clipboard->hwnd, WM_QUIT, 0, 0);"),
    ("\twhile ((mcode = GetMessage(&msg, 0, 0, 0)) != 0)",
     "\t/* Mixel: a late-created window must not block after its owner stops. */\n"
     "\twhile (!InterlockedCompareExchange(&clipboard->stopping, FALSE, FALSE) &&\n"
     "\t\t   (mcode = GetMessage(&msg, 0, 0, 0)) != 0)"),
    ("\tOleUninitialize();\n\treturn 0;\n}\n\nstatic void clear_file_array",
     "\t/* Mixel: destroy listeners on the worker before releasing user32. */\n"
     "\tDestroyWindow(clipboard->hwnd);\n"
     "\tOleUninitialize();\n\treturn 0;\n}\n\nstatic void clear_file_array"),
    ("\tcase WM_DESTROY:\n\t\tif (clipboard->legacyApi)",
     "\tcase WM_DESTROY:\n"
     "\t\t/* Mixel: DestroyWindow also owns modern listener removal. */\n"
     "\t\tif (!clipboard->legacyApi)\n"
     "\t\t\tclipboard->RemoveClipboardFormatListener(hWnd);\n"
     "\t\tif (clipboard->legacyApi)"),
    ("\tclear_format_map(clipboard);\n\tfree(clipboard->format_mappings);\n\treturn TRUE;",
     "\tclear_format_map(clipboard);\n\tfree(clipboard->format_mappings);\n"
     "\tfree(clipboard->req_fdata);\n"
     "\tif (clipboard->hUser32)\n\t\tFreeLibrary(clipboard->hUser32);\n"
     "\t/* Mixel: error cleanup precedes Rust Box Drop; release the owner once. */\n"
     "\tmemset(clipboard, 0, sizeof(*clipboard));\n\treturn TRUE;"),
)


def patch(source: str) -> str:
    original = source
    if MARKER in source:
        for old, new in REPLACEMENTS:
            if original.count(new) != 1:
                raise RuntimeError("Windows clipboard cleanup patch changed")
            original = original.replace(new, old, 1)
        if hashlib.sha256(original.encode()).hexdigest() != SOURCE_SHA256:
            raise RuntimeError("Patched Windows clipboard source contains unrelated changes")
        return source
    if hashlib.sha256(original.encode()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Pinned Windows clipboard native source changed")
    for old, new in REPLACEMENTS:
        if source.count(old) != 1:
            raise RuntimeError("Pinned Windows clipboard ownership boundary changed")
        source = source.replace(old, new, 1)
    return source


def apply(repo: Path) -> None:
    target = repo / SOURCE
    expected = patch(target.read_text(encoding="utf-8"))
    target.write_text(expected, encoding="utf-8")
    print("   patched Windows clipboard ownership: failed init and Rust Drop release each resource once")


if __name__ == "__main__":
    apply(Path(os.environ.get("RDREPO", "./rustdesk")).resolve())
