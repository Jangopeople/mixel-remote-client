#!/usr/bin/env python3
"""Hide "Accept and Elevate" and "Elevate" in the Quick Support consent window.

Usage: patch-support-no-elevate.py <rustdesk/flutter directory>

Why (2026-11-10): starting the elevated helper is an input-generation epoch boundary
(src/server/mixel_receiver_input*.rs). The approved session is closed about 0.1 s after the click
("Input queue capacity or helper trust failed"), the controller is told nothing and reconnects in a
loop, and the helper's 10 s startup deadline includes the time the person needs for the UAC prompt.
Until the session survives that switch, attended Quick Support offers the plain Accept only.
See docs/QS_ELEVATION.md. Remove this patch together with that fix.

Not yet called by apply-branding.sh or the build drivers: release 1.4.6-support.noelevate.1 applied
it by hand to the pinned Flutter source before rebuilding data/app.so.
"""
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = pathlib.Path(sys.argv[1]) / "lib/desktop/pages/server_page.dart"
    text = path.read_bytes().decode("utf-8")
    if "kMixelOfferElevation" in text:
        print("already patched", path)
        return 0
    old = ("    final showElevation = canElevate &&\n        model.showElevation &&\n"
           "        client.type_() == ClientType.remote;\n")
    if text.count(old) != 2 and text.count(old.replace("\n", "\r\n")) == 2:
        old = old.replace("\n", "\r\n")
    if text.count(old) != 2:
        raise SystemExit("expected the showElevation expression twice, found %d" % text.count(old))
    nl = "\r\n" if "\r\n" in old else "\n"
    new = nl.join([
        "    // Mixel: elevation is not offered in attended Quick Support until the",
        "    // session survives the switch to the elevated helper.",
        "    final showElevation = kMixelOfferElevation &&",
        "        canElevate &&",
        "        model.showElevation &&",
        "        client.type_() == ClientType.remote;",
        "",
    ])
    marker = "class _CmHeader extends StatefulWidget"
    if text.count(marker) != 1:
        raise SystemExit("expected one _CmHeader class")
    text = text.replace(old, new).replace(marker, "const bool kMixelOfferElevation = false;" + nl + nl + marker)
    path.write_bytes(text.encode("utf-8"))
    print("patched", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
